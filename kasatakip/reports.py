"""Raporlar bölümü: rapor tanımları (parametreler) ve rapor içeriğinin hazırlanması.

Her rapor report_model bloklarından oluşur; aynı içerik ekranda önizlenir, PDF'e (reports_pdf.render_report)
ve Excel'e (excel.export_report) aktarılır. Günlük kasa ve cari ekstresinin PDF'leri imza alanlı özel
düzenlerini korur.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import excel, reports_pdf
from .analytics import AGING_BUCKETS, COMPARE_MODES, GROUP_NAMES, GROUPS, Analytics, change, ratio, shift_year
from .errors import ServiceError
from .report_model import KV, SERIES_COLORS, Chart, Report, Row, Table, Text, V
from .util import (ALL_TIME_END, ALL_TIME_START, TR_DAYS, auto_group, fmt_date, fmt_dt, iter_group_keys,
                   period_range, to_date, today)

GELIR_C, GIDER_C, NET_C, MUTED_C = "#15803D", "#B91C1C", "#2563EB", "#94A3B8"
AGING_COLORS = ["#15803D", "#65A30D", "#D97706", "#EA580C", "#B91C1C"]

TUR_CHOICES = [("Tüm türler", None), ("Gelir", "GELIR"), ("Gider", "GIDER"), ("Gelir + Gider", "GG"),
               ("Virman", "VIRMAN"), ("Cari fişleri", "CARI")]
CARI_TIP_CHOICES = [("Tüm cariler", None), ("Müşteriler", "MUSTERI"), ("Tedarikçiler", "TEDARIKCI")]
KAT_TUR_CHOICES = [("Gider kategorileri", "GIDER"), ("Gelir kategorileri", "GELIR"), ("Gelir ve gider", None)]
AGING_SIDES = [("Alacaklarımız (bize borçlu cariler)", "alacak"), ("Borçlarımız (borçlu olduğumuz cariler)", "borc")]


@dataclass
class Param:
    key: str
    kind: str          # date | period | cari | cari_opt | personel | odeme | kategori | choice | year | check | user | text
    label: str
    default: Any = None
    choices: list | None = None
    required: bool = False


@dataclass
class ReportDef:
    key: str
    group: str
    title: str
    description: str
    icon: str
    params: list = field(default_factory=list)
    build: Callable | None = None
    pdf: Callable | None = None        # (path, svc, values, firma, kullanici) -> Path
    admin: bool = False


# --------------------------------------------------------------------------- yardımcılar
def _tone(tur: str):
    return {"GELIR": "GELIR", "GIDER": "GIDER", "VIRMAN": "VIRMAN"}.get(tur, "CARI")


def _dates(v: dict, key: str = "donem") -> tuple[dt.date, dt.date]:
    s, e = v[key]
    s, e = to_date(s), to_date(e)
    if s > e:
        s, e = e, s
    return s, e


def _all_time(v: dict, key: str = "donem") -> bool:
    """'Tüm Zamanlar' seçili mi (dönem 2000 – 2099 sınırlarını kapsıyor mu)?"""
    s, e = _dates(v, key)
    return s <= ALL_TIME_START and e >= ALL_TIME_END


def _span(svc, v: dict, key: str = "donem", audit: bool = False) -> tuple[dt.date, dt.date]:
    """Rapor dönemi; 'Tüm Zamanlar' kayıtların bulunduğu aralığa çekilir (bkz. Service.span)."""
    return svc.span(*_dates(v, key), audit=audit)


def _plines(s, e, *extra, tum: bool = False) -> list[str]:
    return ([f"Dönem: {fmt_date(s)} – {fmt_date(e)}" + (" (tüm kayıtlar)" if tum else "")]
            + [x for x in extra if x])


def _compare_line(ps, pe) -> str:
    return f"Karşılaştırma: {fmt_date(ps)} – {fmt_date(pe)}" if ps else ""


def _pivot_groups(s: dt.date, e: dt.date, group: str | None, max_cols: int = 13):
    """Özet (pivot) tablolar için gruplama: sütun sayısı sınırı aşılırsa daha kaba gruplama seçilir;
    aylık gruplama birden fazla yıla yayılıyorsa yıl yıl ayrı tablolar üretilir. [(başlangıç, bitiş, grup)]"""
    g = group or auto_group(s, e)
    order = ["day", "week", "month"]
    while g != "month" and len(list(iter_group_keys(s, e, g))) > max_cols:
        g = order[order.index(g) + 1]
    if g == "month" and len(list(iter_group_keys(s, e, g))) > max_cols:
        out = []
        for y in range(s.year, e.year + 1):
            out.append((max(s, dt.date(y, 1, 1)), min(e, dt.date(y, 12, 31)), "month"))
        return out
    return [(s, e, g)]


def _pivot_tables(an: Analytics, s, e, tur: str, group, label: str) -> list:
    blocks = []
    for ps_, pe_, g in _pivot_groups(s, e, group):
        pv = an.category_pivot(ps_, pe_, tur, g)
        grand = sum(r["toplam"] for r in pv["rows"])
        suffix = ""
        if g == "month" and s.year != e.year:
            suffix = f" ({ps_.year})" if ps_.year == pe_.year else f" ({ps_.year}–{pe_.year})"
        note = ("Haftalık sütun başlıkları haftanın başlangıç gününü gösterir (ilk sütun dönem başından başlar)."
                if g == "week" else "")
        blocks.append(Table(
            f"{label} Kategorileri × {GROUP_NAMES[g].replace('lık', '').replace('lük', '')}{suffix}",
            ["Kategori"] + pv["labels"] + ["Toplam", "Pay"],
            ["text"] + ["money"] * len(pv["labels"]) + ["money", "percent"],
            [[r["ad"]] + [x or None for x in r["values"]] + [r["toplam"], ratio(r["toplam"], grand)]
             for r in pv["rows"]],
            ["TOPLAM"] + [x or None for x in pv["totals"]] + [grand, 1.0 if grand else None],
            widths=[30] + [12] * len(pv["labels"]) + [15, 9], font=7.6, note=note,
            sheet=f"{label} × Dönem{suffix}"))
    return blocks


def _cat_compare_table(rows: list[dict], label: str, has_prev: bool) -> Table:
    headers = ["Kategori", "Adet", "Toplam", "Pay", "Ortalama"]
    types = ["text", "int", "money", "percent", "money"]
    widths = [34, 9, 19, 10, 17]
    if has_prev:
        headers += ["Karşılaştırma", "Fark", "Değişim"]
        types += ["money", "dmoney", "dpercent"]
        widths += [19, 18, 13]
    body = []
    for r in rows:
        row = [r["ad"], r["adet"] or None, r["toplam"], r["pay"], r["ort"] or None]
        if has_prev:
            row += [r["onceki"], r["fark"], r["degisim"]]
        body.append(row)
    tot = sum(r["toplam"] for r in rows)
    total = ["TOPLAM", sum(r["adet"] for r in rows), tot, 1.0 if tot else None,
             round(tot / sum(r["adet"] for r in rows)) if sum(r["adet"] for r in rows) else None]
    if has_prev:
        pt = sum(r["onceki"] or 0 for r in rows)
        total += [pt, tot - pt, change(tot, pt)]
    return Table(f"{label} Kategorileri", headers, types, body, total, widths=widths, sheet=f"{label} Kategorileri")


# --------------------------------------------------------------------------- KASA
def build_gunluk_kasa(svc, v: dict) -> Report:
    d = to_date(v.get("tarih")) or today()
    rep = svc.day_report(d)
    k = rep.get("kapanis")
    if k:
        durum = f"Kapalı · {fmt_dt(k['kapanis_zamani'])}" + (f" · {k['kapatan']}" if k.get("kapatan") else "")
    elif rep["kilitli"]:
        durum = "Kilitli (sonraki bir gün kapatıldı)"
    else:
        durum = "Açık (gün kapatılmadı)"
    blocks: list = [KV("Günlük Özet", [
        ("Toplam Gelir", rep["gelir"], "money"), ("Toplam Gider", rep["gider"], "money"),
        ("Net (Gelir − Gider)", rep["net"], "smoney"), ("Toplam İşlem", rep["gelir_adet"] + rep["gider_adet"], "int"),
        ("Gelir İşlem Sayısı", rep["gelir_adet"], "int"), ("Gider İşlem Sayısı", rep["gider_adet"], "int"),
        ("Ortalama Gelir", rep["ort_gelir"], "money"), ("Ortalama Gider", rep["ort_gider"], "money")])]
    od = rep["odeme"]
    has_count = any(r["sayilan"] is not None for r in od)
    headers = ["Ödeme Tipi", "Devir", "Gelir", "Gider", "Virman", "Gün Sonu"]
    types = ["text", "money", "money", "money", "dmoney", "smoney"]
    widths = [30, 22, 21, 21, 19, 23]
    if has_count:
        headers += ["Sayılan", "Fark"]
        types += ["money", "dmoney"]
        widths += [22, 18]
    rows = []
    for r in od:
        row = [r["ad"], r["devir"], r["gelir"] or None, r["gider"] or None, r["virman_net"] or None, r["gun_sonu"]]
        if has_count:
            row += [r["sayilan"], r["fark"]]
        rows.append(row)
    total = ["TOPLAM", sum(r["devir"] for r in od), rep["gelir"], rep["gider"],
             sum(r["virman_net"] for r in od) or None, sum(r["gun_sonu"] for r in od)]
    if has_count:
        counted = [r for r in od if r["sayilan"] is not None]
        total += [sum(r["sayilan"] for r in counted), sum(r["fark"] for r in counted)]
    blocks.append(Table("Ödeme Tipi Kırılımı", headers, types, rows, total, widths=widths))

    def cat_table(title, items, tot):
        return Table(title, ["Kategori", "Adet", "Toplam"], ["text", "int", "money"],
                     [[x["ad"], x["adet"] or None, x["toplam"] or None] for x in items],
                     ["TOPLAM", sum(x["adet"] for x in items), tot], widths=[56, 14, 30], empty="Kayıt yok")

    blocks.append(Row([cat_table("Gelir Kategorileri", rep["kat_gelir"], rep["gelir"]),
                       cat_table("Gider Kategorileri", rep["kat_gider"], rep["gider"])]))
    hs = rep["hareketler"]
    blocks.append(Table(
        "Hareket Dökümü", ["No", "Saat", "Tür", "Kategori", "Ödeme", "Cari", "Açıklama", "Tutar"],
        ["int", "text", "text", "text", "text", "text", "text", "money"],
        [[i, m["saat"], m["tur_adi"], m["kategori_gosterim"], m["odeme_gosterim"], m["cari"], m["aciklama"],
          m["tutar"]] for i, m in enumerate(hs, 1)],
        widths=[7, 10, 16, 27, 22, 30, 46, 22], tones=[_tone(m["tur"]) for m in hs], tone_cols=[2, 7],
        keys=[m["id"] for m in hs], key_kind="hareket", empty="Bu tarihte kayıt bulunmuyor.",
        note=("Cari Borç / Cari Alacak fişleri kasa bakiyelerini etkilemez; yalnızca cari hesaplara işlenir."
              if rep["cari_fis_adet"] else "")))
    if k and k.get("aciklama"):
        blocks.append(Text("Kapanış Notu", [("info", k["aciklama"])]))
    return Report("gunluk_kasa", "Günlük Kasa Raporu", [fmt_date(d, weekday=True), f"Durum: {durum}"], blocks,
                  f"Kasa_{d.isoformat()}", "Günlük Kasa", signatures=["Teslim Eden", "Teslim Alan", "Kontrol Eden"])


def _pdf_gunluk(path, svc, v, firma, kullanici):
    d = to_date(v.get("tarih")) or today()
    return reports_pdf.daily_kasa_pdf(path, svc.day_report(d), firma, kullanici,
                                      imza=svc.setting("pdf_imza", "1") == "1")


def build_kasa_defteri(svc, v: dict) -> Report:
    s, e = _span(svc, v)
    pid = v.get("odeme")
    daily = bool(v.get("gun_toplam", True))
    pt_name = next((p["ad"] for p in svc.payment_types(include_inactive=True) if p["id"] == pid), None)
    before = svc.balances_before(s)
    devir = before.get(pid, 0) if pid else sum(before.values())
    moves = svc.movements(start=s, end=e, tur=["GELIR", "GIDER", "VIRMAN"], odeme_tipi_id=pid)
    rows, tones, keys = [], [], []
    st = {"bal": devir, "tin": 0, "tout": 0, "day": None, "din": 0, "dout": 0}

    def add(row, tone=None, key=None):
        rows.append(row)
        tones.append(tone)
        keys.append(key)

    def flush():
        if daily and st["day"] is not None:
            add(["", "", "", "", "", "", f"{fmt_date(st['day'])} günü toplamı", st["din"] or None,
                 st["dout"] or None, st["bal"]], "sub")

    add([s.isoformat(), "", "Devir", "Dönem başı bakiyesi", pt_name or "Tüm ödeme tipleri", "", "", None, None,
         devir], "bold")
    for m in moves:
        if m["tarih"] != st["day"]:
            flush()
            st.update(day=m["tarih"], din=0, dout=0)
        gi = ci = None
        if m["tur"] == "GELIR":
            gi = m["tutar"]
        elif m["tur"] == "GIDER":
            ci = m["tutar"]
        elif pid:
            if m["hedef_odeme_tipi_id"] == pid:
                gi = m["tutar"]
            if m["odeme_tipi_id"] == pid:
                ci = m["tutar"]
        st["bal"] += (gi or 0) - (ci or 0)
        for k_, val in (("tin", gi), ("tout", ci), ("din", gi), ("dout", ci)):
            st[k_] += val or 0
        add([m["tarih"], m["saat"], m["tur_adi"], m["kategori_gosterim"], m["odeme_gosterim"], m["cari"],
             m["aciklama"], gi, ci, st["bal"]], _tone(m["tur"]), m["id"])
    flush()
    blocks = [KV("Özet", [
        ("Ödeme Tipi", pt_name or "Tümü (toplam kasa)", "text"), ("Dönem Başı Bakiye", devir, "smoney"),
        ("Toplam Giriş", st["tin"], "money"), ("Toplam Çıkış", st["tout"], "money"),
        ("Dönem Sonu Bakiye", st["bal"], "smoney"), ("Net Değişim", st["bal"] - devir, "dmoney"),
        ("Kayıt Sayısı", len(moves), "int")]),
        Table("Kasa Hareketleri", ["Tarih", "Saat", "Tür", "Kategori / İşlem", "Ödeme", "Cari", "Açıklama", "Giriş",
                                   "Çıkış", "Bakiye"],
              ["date", "text", "text", "text", "text", "text", "text", "money", "money", "smoney"], rows,
              ["", "", "", "DÖNEM TOPLAMI", "", "", "", st["tin"], st["tout"], st["bal"]],
              widths=[17, 10, 13, 26, 22, 30, 50, 20, 20, 22], tones=tones, tone_cols=[2, 7, 8], keys=keys,
              key_kind="hareket", sheet="Kasa Defteri",
              note=("" if pid else "Virmanlar ödeme tipleri arasındaki aktarımdır; toplam kasa bakiyesini "
                                   "değiştirmez (giriş / çıkış sütunları boş bırakılır)."))]
    return Report("kasa_defteri", "Kasa Defteri", _plines(s, e, f"Ödeme tipi: {pt_name or 'Tümü'}", tum=_all_time(v)),
                  blocks,
                  f"Kasa_Defteri_{(pt_name or 'Tumu')}_{s.isoformat()}_{e.isoformat()}", "Kasa Raporları",
                  landscape=True)


def build_kapanislar(svc, v: dict) -> Report:
    an = Analytics(svc)
    s, e = _span(svc, v)
    cs = an.closing_status(s, e)
    cd = an.count_differences(s, e)
    rows = an.closing_rows(s, e)
    blocks: list = [KV("Özet", [
        ("Kapanması Gereken Gün", cs["gerekli"], "int"), ("Kapatılan Gün", cs["kapali"], "int"),
        ("Kapatılmamış (açık) Gün", len(cs["acik"]), "int"),
        ("Kapatılmadan Kilitlenen Gün", cs["kilitli"], "int"), ("Kapanış Oranı", cs["oran"], "percent"),
        ("Sayım Yapılan Gün", cd["sayim_gun"], "int"), ("Sayım Farkı Çıkan Gün", cd["farkli_gun"], "int"),
        ("Toplam Fazla", cd["fazla"], "money"), ("Toplam Eksik", -cd["eksik"], "money"),
        ("Net Sayım Farkı", cd["net"], "dmoney"), ("Sayım Doğruluğu", cd["dogruluk"], "percent")])]
    blocks.append(Table(
        "Kapatılan Günler", ["Tarih", "Gün", "Gelir", "Gider", "Net", "Kasa Toplamı", "Sayım Farkı", "Kapatan",
                             "Kapanış Zamanı", "Not"],
        ["date", "text", "money", "money", "smoney", "smoney", "dmoney", "text", "datetime", "text"],
        [[r["tarih"], r["gun"], r["gelir"] or None, r["gider"] or None, r["net"], r["sistem"],
          r["fark"] if r["sayim"] else None, r["kapatan"] or "", r["kapanis_zamani"], r["aciklama"]] for r in rows],
        ["TOPLAM", f"{len(rows)} gün", sum(r["gelir"] for r in rows), sum(r["gider"] for r in rows),
         sum(r["net"] for r in rows), "", sum(r["fark"] or 0 for r in rows), "", "", ""],
        widths=[17, 15, 20, 20, 20, 22, 18, 16, 24, 40], keys=[r["tarih"] for r in rows], key_kind="gun",
        tones=["GIDER" if r["fark"] else None for r in rows], tone_cols=[6],
        empty="Bu dönemde kapatılmış gün yok."))
    blocks.append(Table(
        "Sayım Farkları", ["Tarih", "Ödeme Tipi", "Sistem Bakiyesi", "Sayılan", "Fark", "Kapatan", "Not"],
        ["date", "text", "smoney", "money", "dmoney", "text", "text"],
        [[r["tarih"], r["odeme"], r["sistem"], r["sayilan"], r["fark"], r["kapatan"] or "", r["aciklama"]]
         for r in cd["satirlar"]],
        ["TOPLAM", f"{cd['farkli_gun']} gün", "", "", cd["net"], "", ""] if cd["satirlar"] else None,
        widths=[17, 22, 22, 22, 18, 16, 50], keys=[r["tarih"] for r in cd["satirlar"]], key_kind="gun",
        empty="Bu dönemde sayım farkı yok."))
    daily = an.daily_totals(s, e) if (cs["acik"] or cs["kilitli"]) else {}

    def day_rows(days):
        return [[d, TR_DAYS[to_date(d).weekday()], daily.get(d, {}).get("gelir") or None,
                 daily.get(d, {}).get("gider") or None, daily.get(d, {}).get("net", 0)] for d in days]

    if cs["acik"]:
        blocks.append(Table(
            "Kapatılmamış Günler", ["Tarih", "Gün", "Gelir", "Gider", "Net"],
            ["date", "text", "money", "money", "smoney"], day_rows(cs["acik"]),
            widths=[20, 20, 20, 20, 20], keys=list(cs["acik"]), key_kind="gun",
            note="Bu günlerin kasası henüz kapatılmamış. Günlük Kasa ekranından 'Günü Kapat' ile kapatabilirsiniz."))
    if cs["kilitli"]:
        blocks.append(Table(
            "Kapatılmadan Kilitlenen Günler", ["Tarih", "Gün", "Gelir", "Gider", "Net"],
            ["date", "text", "money", "money", "smoney"], day_rows(cs["kilitli_gunler"]),
            widths=[20, 20, 20, 20, 20], keys=list(cs["kilitli_gunler"]), key_kind="gun",
            note="Bu günler kapatılmadı; daha sonraki bir gün kapatıldığı için kilitlendi. Kasa sayımı ve kapanış "
                 "kaydı yoktur. Gerekirse yönetici 'Günü Aç' ile açıp sırayla kapatabilir."))
    return Report("kapanislar", "Gün Sonu Kapanış Raporu", _plines(s, e, tum=_all_time(v)), blocks,
                  f"Kapanislar_{s.isoformat()}_{e.isoformat()}", "Kasa Raporları", landscape=True)


def build_odeme_bakiye(svc, v: dict) -> Report:
    an = Analytics(svc)
    s, e = an.effective_range(*_dates(v))
    pt = an.payment_table(s, e)
    bh = an.balance_history(s, e)
    tdv, tson = sum(r["devir"] for r in pt), sum(r["son"] for r in pt)
    blocks: list = [KV("Özet", [("Dönem Başı Toplam", tdv, "smoney"), ("Dönem Sonu Toplam", tson, "smoney"),
                                ("Değişim", tson - tdv, "dmoney"), ("Ödeme Tipi Sayısı", len(pt), "int")] +
                       [(f"{r['ad']} (dönem sonu)", r["son"], "smoney") for r in pt])]
    blocks.append(Table(
        "Ödeme Tipi Özeti", ["Ödeme Tipi", "Dönem Başı", "Gelir", "Gider", "Virman Giriş", "Virman Çıkış",
                             "Dönem Sonu", "Değişim"],
        ["text", "smoney", "money", "money", "money", "money", "smoney", "dmoney"],
        [[r["ad"], r["devir"], r["gelir"] or None, r["gider"] or None, r["virman_giris"] or None,
          r["virman_cikis"] or None, r["son"], r["degisim"]] for r in pt],
        ["TOPLAM", tdv, sum(r["gelir"] for r in pt), sum(r["gider"] for r in pt),
         sum(r["virman_giris"] for r in pt), sum(r["virman_cikis"] for r in pt), tson, tson - tdv],
        widths=[26, 20, 19, 19, 19, 19, 20, 18]))
    blocks.append(Chart("Bakiye Seyri", "line", bh["labels"],
                        [(sr["ad"], SERIES_COLORS[i % len(SERIES_COLORS)], sr["values"])
                         for i, sr in enumerate(bh["series"])], titles=bh["titles"]))
    names = [sr["ad"] for sr in bh["series"]]
    gname = {"day": "Gün Sonu", "week": "Hafta Sonu", "month": "Ay Sonu"}[bh["group"]]
    blocks.append(Table(
        f"{gname} Bakiyeleri", ["Tarih"] + names + ["Toplam"], ["date"] + ["smoney"] * (len(names) + 1),
        [[d.isoformat()] + [sr["values"][i] for sr in bh["series"]] + [bh["total"][i]]
         for i, d in enumerate(bh["dates"])][::-1],
        widths=[20] + [20] * len(names) + [22], keys=[d.isoformat() for d in bh["dates"]][::-1], key_kind="gun",
        sheet="Bakiyeler"))
    return Report("odeme_bakiye", "Ödeme Tipi Bakiye Raporu", _plines(s, e, tum=_all_time(v)), blocks,
                  f"Odeme_Tipi_Bakiyeleri_{s.isoformat()}_{e.isoformat()}", "Kasa Raporları",
                  landscape=len(names) > 5)


# --------------------------------------------------------------------------- GELİR - GİDER
def build_donem_analiz(svc, v: dict) -> Report:
    an = Analytics(svc)
    s, e = an.effective_range(*_dates(v))
    mode = v.get("karsilastirma", "prev")
    ps, pe = an.compare_range(s, e, mode)
    cur = an.kpis(s, e)
    prev = an.kpis(ps, pe) if ps else None
    group, seri = an.series(s, e, v.get("grup"))
    has_prev = bool(prev)

    def kpi_row(label, key, kind):
        a = cur[key]
        b = prev[key] if prev else None
        if kind == "percent":
            ch = V((a - b) if (a is not None and b is not None) else None, "dpp")
        else:
            ch = V(change(a, b) if b is not None else None, "dpercent")
        row = [label, V(a, kind)]
        if has_prev:
            row += [V(b, kind), ch]
        return row

    spec = [("Toplam Gelir", "gelir", "money"), ("Toplam Gider", "gider", "money"),
            ("Net (Gelir − Gider)", "net", "smoney"), ("Kâr Marjı", "marj", "percent"),
            ("Gelir İşlem Sayısı", "gelir_adet", "int"), ("Gider İşlem Sayısı", "gider_adet", "int"),
            ("Faaliyet Günü", "gun_sayisi", "int"), ("Günlük Ortalama Gelir", "gunluk_gelir", "money"),
            ("Günlük Ortalama Gider", "gunluk_gider", "money"), ("Ortalama Gelir (işlem başına)", "ort_gelir", "money"),
            ("Ortalama Gider (işlem başına)", "ort_gider", "money"),
            ("Veresiye (Cari Borç Fişleri)", "cari_borc", "money"), ("Cari Tahsilat", "cari_tahsilat", "money"),
            ("Tahsilat Oranı", "tahsilat_orani", "percent"), ("Virman Toplamı", "virman", "money")]
    headers = ["Gösterge", "Bu Dönem"] + (["Karşılaştırma", "Değişim"] if has_prev else [])
    blocks: list = [Table("Dönem Özeti", headers, ["text", "money", "money", "dpercent"][:len(headers)],
                          [kpi_row(*x) for x in spec], widths=[40, 22, 22, 16][:len(headers)], sheet="Özet Tablo")]
    blocks.append(Text("Öne Çıkanlar", [(i["tone"], i["text"]) for i in an.insights(s, e, ps, pe, cur, prev)]))
    cs = an.closing_status(s, e)
    cd = an.count_differences(s, e)
    blocks.append(KV("Göstergeler", [
        ("Kâr Marjı", cur["marj"], "percent"), ("Gider / Gelir Oranı", cur["gider_orani"], "percent"),
        ("Tahsilat Oranı", cur["tahsilat_orani"], "percent"), ("Gün Kapanış Oranı", cs["oran"], "percent"),
        ("Sayım Doğruluğu", cd["dogruluk"], "percent"), ("Net Sayım Farkı", cd["net"], "dmoney"),
        ("Kapatılmamış Gün", len(cs["acik"]), "int"), ("Faaliyet Günü", cur["gun_sayisi"], "int")]))
    if seri:
        blocks.append(Chart(f"Gelir – Gider ({GROUP_NAMES[group]})", "bar", [r["etiket"] for r in seri],
                            [("Gelir", GELIR_C, [r["gelir"] for r in seri]),
                             ("Gider", GIDER_C, [r["gider"] for r in seri])], titles=[r["baslik"] for r in seri]))
        blocks.append(Chart("Kümülatif Net (birikimli gelir − gider)", "line", [r["etiket"] for r in seri],
                            [("Kümülatif net", NET_C, [r["kumulatif"] for r in seri])], height=48,
                            titles=[r["baslik"] for r in seri]))
    kg = an.category_compare(s, e, ps, pe, "GELIR")
    kc = an.category_compare(s, e, ps, pe, "GIDER")
    blocks.append(Row([
        Chart("Gelir Dağılımı", "donut", [r["ad"] for r in kg if r["toplam"]],
              [("Gelir", None, [r["toplam"] for r in kg if r["toplam"]])], height=52),
        Chart("Gider Dağılımı", "donut", [r["ad"] for r in kc if r["toplam"]],
              [("Gider", None, [r["toplam"] for r in kc if r["toplam"]])], height=52)]))
    blocks.append(_cat_compare_table(kc, "Gider", has_prev))
    blocks.append(_cat_compare_table(kg, "Gelir", has_prev))
    pt = an.payment_table(s, e)
    blocks.append(Table(
        "Ödeme Tipleri", ["Ödeme Tipi", "Dönem Başı", "Gelir", "Gider", "Virman (net)", "Dönem Sonu"],
        ["text", "smoney", "money", "money", "dmoney", "smoney"],
        [[r["ad"], r["devir"], r["gelir"] or None, r["gider"] or None, r["virman_net"] or None, r["son"]] for r in pt],
        ["TOPLAM", sum(r["devir"] for r in pt), sum(r["gelir"] for r in pt), sum(r["gider"] for r in pt), None,
         sum(r["son"] for r in pt)], widths=[28, 20, 20, 20, 18, 20]))
    tm, tt = svc.top_caris(s, e, "GELIR", 10), svc.top_caris(s, e, "GIDER", 10)
    if tm or tt:
        def top(title, items):
            return Table(title, ["Cari", "Adet", "Tutar"], ["text", "int", "money"],
                         [[x["unvan"], x["adet"], x["toplam"]] for x in items], widths=[58, 12, 30],
                         keys=[x["id"] for x in items], key_kind="cari", empty="Kayıt yok")
        blocks.append(Row([top("En Çok Tahsilat Yapılan Cariler", tm), top("En Çok Ödeme Yapılan Cariler", tt)]))
    wk = an.weekday_stats(s, e)
    blocks.append(Table(
        "Haftanın Günlerine Göre", ["Gün", "Faaliyet Günü", "Toplam Gelir", "Toplam Gider", "Ort. Gelir / Gün",
                                    "Ort. Gider / Gün", "Ort. Net / Gün"],
        ["text", "int", "money", "money", "money", "money", "smoney"],
        [[w["ad"], w["aktif_gun"] or None, w["gelir"] or None, w["gider"] or None, w["ort_gelir"] or None,
          w["ort_gider"] or None, w["ort_net"] if w["aktif_gun"] else None] for w in wk],
        widths=[18, 13, 19, 19, 17, 17, 17], sheet="Haftanın Günleri"))
    blocks.append(Table(
        f"{GROUP_NAMES[group]} Döküm", ["Dönem", "Gelir", "Gider", "Net", "Kümülatif Net", "Kâr Marjı", "İşlem"],
        ["text", "money", "money", "smoney", "smoney", "percent", "int"],
        [[r["baslik"], r["gelir"] or None, r["gider"] or None, r["net"], r["kumulatif"], r["marj"],
          r["adet"] or None] for r in seri],
        ["TOPLAM", cur["gelir"], cur["gider"], cur["net"], "", cur["marj"], cur["adet"]],
        widths=[34, 19, 19, 19, 19, 12, 10], sheet="Dönem Dökümü"))
    lines = _plines(s, e, _compare_line(ps, pe), tum=_all_time(v))
    return Report("donem_analiz", "Dönem Analiz Raporu", lines, blocks,
                  f"Donem_Raporu_{s.isoformat()}_{e.isoformat()}", "Dönem Raporları",
                  meta={"start": s, "end": e, "prev_start": ps, "prev_end": pe, "group": group})


def build_hareket_dokumu(svc, v: dict) -> Report:
    s, e = _span(svc, v)
    t = v.get("tur")
    tur = {"GG": ["GELIR", "GIDER"], "CARI": ["CARI_BORC", "CARI_ALACAK"]}.get(t, t)
    kid, pid, cid = v.get("kategori"), v.get("odeme"), v.get("cari")
    search = (v.get("arama") or "").strip()
    rows = svc.movements(start=s, end=e, tur=tur, kategori_id=kid, odeme_tipi_id=pid, cari_id=cid, search=search)
    filt = []
    if t:
        filt.append("Tür: " + dict((b, a) for a, b in TUR_CHOICES)[t])
    if kid:
        filt.append("Kategori: " + next((c["ad"] for c in svc.categories() if c["id"] == kid), "?"))
    if pid:
        filt.append("Ödeme: " + next((p["ad"] for p in svc.payment_types() if p["id"] == pid), "?"))
    if cid:
        try:
            filt.append("Cari: " + svc.get_cari(cid)["unvan"])
        except ServiceError:
            pass
    if search:
        filt.append(f"Arama: “{search}”")
    g = sum(r["tutar"] for r in rows if r["tur"] == "GELIR")
    c = sum(r["tutar"] for r in rows if r["tur"] == "GIDER")
    o = sum(r["tutar"] for r in rows if r["tur"] not in ("GELIR", "GIDER"))
    blocks = [KV("Özet", [("Kayıt Sayısı", len(rows), "int"), ("Toplam Gelir", g, "money"),
                          ("Toplam Gider", c, "money"), ("Net (Gelir − Gider)", g - c, "smoney"),
                          ("Virman Toplamı", sum(r["tutar"] for r in rows if r["tur"] == "VIRMAN"), "money"),
                          ("Cari Fişleri Toplamı", sum(r["tutar"] for r in rows if r["tur"] in ("CARI_BORC",
                                                                                                 "CARI_ALACAK")),
                           "money")]),
              Table("Hareketler", ["Tarih", "Saat", "Tür", "Kategori", "Ödeme", "Cari", "Açıklama", "Belge No",
                                   "Gelir", "Gider", "Virman / Fiş"],
                    ["date", "text", "text", "text", "text", "text", "text", "text", "money", "money", "money"],
                    [[r["tarih"], r["saat"], r["tur_adi"], r["kategori_gosterim"], r["odeme_gosterim"], r["cari"],
                      r["aciklama"], r["belge_no"], r["tutar"] if r["tur"] == "GELIR" else None,
                      r["tutar"] if r["tur"] == "GIDER" else None,
                      r["tutar"] if r["tur"] not in ("GELIR", "GIDER") else None] for r in rows],
                    ["TOPLAM", "", f"{len(rows)} kayıt", "", "", "", "", "", g, c, o],
                    widths=[17, 10, 15, 26, 22, 30, 50, 14, 19, 19, 19], tones=[_tone(r["tur"]) for r in rows],
                    tone_cols=[2, 8, 9, 10], keys=[r["id"] for r in rows], key_kind="hareket", sheet="Hareketler")]
    return Report("hareket_dokumu", "Hareket Dökümü", _plines(s, e, " · ".join(filt), tum=_all_time(v)), blocks,
                  f"Hareket_Dokumu_{s.isoformat()}_{e.isoformat()}", "Gelir-Gider Raporları", landscape=True)


def build_kategori(svc, v: dict) -> Report:
    an = Analytics(svc)
    s, e = an.effective_range(*_dates(v))
    ps, pe = an.compare_range(s, e, v.get("karsilastirma", "prev"))
    turler = {"GIDER": ["GIDER"], "GELIR": ["GELIR"]}.get(v.get("tur"), ["GIDER", "GELIR"])
    blocks: list = []
    for t in turler:
        label = "Gider" if t == "GIDER" else "Gelir"
        cc = an.category_compare(s, e, ps, pe, t)
        blocks.append(Chart(f"{label} Dağılımı", "donut", [r["ad"] for r in cc if r["toplam"]],
                            [(label, None, [r["toplam"] for r in cc if r["toplam"]])], height=55))
        blocks.append(_cat_compare_table(cc, label, bool(ps)))
        blocks += _pivot_tables(an, s, e, t, v.get("grup"), label)
    return Report("kategori", "Kategori Raporu", _plines(s, e, _compare_line(ps, pe), tum=_all_time(v)), blocks,
                  f"Kategori_Raporu_{s.isoformat()}_{e.isoformat()}", "Gelir-Gider Raporları", landscape=True)


def build_aylik(svc, v: dict) -> Report:
    an = Analytics(svc)
    y = int(v.get("yil") or today().year)
    mc = an.monthly_compare(y)
    rows, cur, prev = mc["rows"], mc["cur"], mc["prev"]
    ys, ye = mc["ytd"]
    kis = " (yılbaşından bugüne)" if mc["kismi"] else ""
    eksik = mc["prev_eksik"]
    blocks: list = [KV(f"{y} Özeti{kis}", [
        ("Toplam Gelir", cur["gelir"], "money"), ("Toplam Gider", cur["gider"], "money"),
        ("Net", cur["net"], "smoney"), ("Kâr Marjı", ratio(cur["net"], cur["gelir"]), "percent"),
        (f"{y - 1} Aynı Dönem Gelir", prev["gelir"], "money"), (f"{y - 1} Aynı Dönem Gider", prev["gider"], "money"),
        (f"{y - 1} Aynı Dönem Net", prev["net"], "smoney"),
        ("Gelir Değişimi", None if eksik else change(cur["gelir"], prev["gelir"]), "dpercent")])]
    if eksik:
        blocks.append(Text("Not", [("warn", f"Kayıtlar {fmt_date(mc['ilk_kayit'])} tarihinde başlıyor; {y - 1} yılının "
                                            "verisi eksik olduğundan yıllık değişim oranı hesaplanmadı. Aylık "
                                            "karşılaştırmalar yalnızca verisi olan aylar için anlamlıdır.")]))
    labels = [r["kisa"] for r in rows]
    blocks.append(Chart(f"{y} Aylık Gelir – Gider", "bar", labels,
                        [("Gelir", GELIR_C, [r["gelir"] for r in rows]), ("Gider", GIDER_C, [r["gider"] for r in rows])],
                        titles=[f"{r['ad']} {y}" for r in rows]))
    blocks.append(Chart(f"Aylık Net: {y} / {y - 1}", "line", labels,
                        [(f"{y} net", NET_C, [None if r["gelecek"] else r["net"] for r in rows]),
                         (f"{y - 1} net", MUTED_C, [r["o_net"] for r in rows])], height=50,
                        titles=[r["ad"] for r in rows]))
    body = []
    for r in rows:
        body.append([r["ad"] + (" (devam ediyor)" if r["devam"] else ""), r["gelir"] or None, r["gider"] or None,
                     None if r["gelecek"] else r["net"], None if r["gelecek"] else r["kumulatif"],
                     r["o_gelir"] or None, r["o_gider"] or None, r["o_net"] if (r["o_gelir"] or r["o_gider"]) else None,
                     r["d_gelir"], r["d_gider"]])
    tg, tc = sum(r["gelir"] for r in rows), sum(r["gider"] for r in rows)
    og, oc = sum(r["o_gelir"] for r in rows), sum(r["o_gider"] for r in rows)
    blocks.append(Table(
        "Aylık Döküm", ["Ay", "Gelir", "Gider", "Net", "Kümülatif Net", f"{y - 1} Gelir",
                                        f"{y - 1} Gider", f"{y - 1} Net", "Gelir Değişim", "Gider Değişim"],
        ["text", "money", "money", "smoney", "smoney", "money", "money", "smoney", "dpercent", "dpercent"], body,
        ["TOPLAM", tg, tc, tg - tc, "", og, oc, og - oc] +
        ([change(tg, og), change(tc, oc)] if not (mc["kismi"] or eksik) else [None, None]),
        widths=[26, 19, 19, 19, 19, 19, 19, 19, 14, 14], sheet="Aylık Tablo",
        note=(f"Değişim sütunları ayı {y - 1} yılının aynı ayıyla karşılaştırır. Yıl tamamlanmadığı için toplam "
              f"satırında değişim verilmez (yıl özeti, {y - 1} yılının aynı dönemiyle karşılaştırır); devam eden ayın "
              "karşılaştırması da eksik görünebilir.") if mc["kismi"] else
        f"Değişim sütunları her ayı {y - 1} yılının aynı ayıyla karşılaştırır."))
    for t, label in (("GIDER", "Gider"), ("GELIR", "Gelir")):
        blocks += _pivot_tables(an, dt.date(y, 1, 1), dt.date(y, 12, 31), t, "month", label)
    return Report("aylik", f"{y} Aylık Gelir – Gider Tablosu",
                  [f"Yıl: {y}", f"Karşılaştırma: {y - 1}" +
                   (f" (özet: {fmt_date(shift_year(ys, -1))} – {fmt_date(shift_year(ye, -1))})" if mc["kismi"]
                    else "")],
                  blocks, f"Aylik_Gelir_Gider_{y}", "Gelir-Gider Raporları", landscape=True)


# --------------------------------------------------------------------------- CARİ
def _cari_required(v):
    cid = v.get("cari")
    if not cid:
        raise ServiceError("Rapor için bir cari seçin.")
    return cid


def build_cari_ekstre(svc, v: dict) -> Report:
    cid = _cari_required(v)
    c = svc.get_cari(cid)
    s, e = _span(svc, v)
    e = min(e, max(today(), s))
    st = svc.cari_statement(cid, s, e)
    rows = [[s.isoformat(), "Devir", "", "Önceki dönemden devreden bakiye", None, None, st["devir"]]]
    rows += [[m["tarih"], m["islem"], m["belge_no"], m["aciklama"], m["borc"] or None, m["alacak"] or None,
              m["bakiye"]] for m in st["satirlar"]]
    blocks = [KV("Cari Bilgileri", [
        ("Cari Kodu", c["kod"] or "-", "text"), ("Cari Tipi", c["tip_adi"], "text"),
        ("Ünvan", c["unvan"], "text"), ("Telefon", c.get("telefon") or "-", "text"),
        ("Devir", st["devir"], "balance"), ("Dönem Borç", st["toplam_borc"], "money"),
        ("Dönem Alacak", st["toplam_alacak"], "money"), ("Dönem Sonu Bakiye", st["bakiye"], "balance")]),
        Table("Hesap Hareketleri", ["Tarih", "İşlem", "Belge No", "Açıklama", "Borç", "Alacak", "Bakiye"],
              ["date", "text", "text", "text", "money", "money", "balance"], rows,
              ["", "Dönem Toplamı", "", "", st["toplam_borc"], st["toplam_alacak"], st["bakiye"]],
              widths=[20, 28, 18, 52, 21, 21, 24], tones=["bold"] + [_tone(m["tur"]) for m in st["satirlar"]],
              tone_cols=[1], keys=[None] + [m["id"] for m in st["satirlar"]], key_kind="hareket", sheet="Ekstre",
              note="Bakiye: (B) cari bize borçlu, (A) biz cariye borçluyuz.")]
    return Report("cari_ekstre", "Cari Hesap Ekstresi", _plines(s, e, c["unvan"], tum=_all_time(v)), blocks,
                  f"Ekstre_{c['unvan']}_{e.isoformat()}", "Cari Ekstreler",
                  signatures=[svc.firma().get("adi") or "Firma Yetkilisi", c["unvan"]])


def _pdf_ekstre(path, svc, v, firma, kullanici):
    cid = _cari_required(v)
    c = svc.get_cari(cid)
    s, e = _span(svc, v)
    e = min(e, max(today(), s))
    return reports_pdf.cari_ekstre_pdf(path, c, svc.cari_statement(cid, s, e), s, e, firma, kullanici)


def _bal_tone(b):
    return "GELIR" if b > 0 else ("GIDER" if b < 0 else "muted")


def build_cari_bakiye(svc, v: dict) -> Report:
    an = Analytics(svc)
    d = to_date(v.get("tarih")) or today()
    tip = v.get("tip")
    rows = an.cari_balances(d, tip, bool(v.get("sadece_bakiyeli", True)))
    alacak = sum(r["bakiye"] for r in rows if r["bakiye"] > 0)
    borc = -sum(r["bakiye"] for r in rows if r["bakiye"] < 0)
    tip_txt = dict((b, a) for a, b in CARI_TIP_CHOICES).get(tip, "Tüm cariler")
    blocks = [KV("Özet", [("Cari Sayısı", len(rows), "int"),
                          ("Alacaklarımız (borçlu cariler)", alacak, "money"),
                          ("Borçlarımız (alacaklı cariler)", borc, "money"), ("Net Cari Pozisyon", alacak - borc,
                                                                               "smoney"),
                          ("Borçlu Cari Sayısı", sum(1 for r in rows if r["bakiye"] > 0), "int"),
                          ("Alacaklı Cari Sayısı", sum(1 for r in rows if r["bakiye"] < 0), "int")]),
              Table("Cari Bakiyeleri", ["Kod", "Ünvan", "Tip", "Telefon", "Toplam Borç", "Toplam Alacak", "Bakiye",
                                        "Durum", "Son Hareket"],
                    ["text", "text", "text", "text", "money", "money", "balance", "text", "date"],
                    [[r["kod"], r["unvan"], r["tip_adi"], r["telefon"], r["borc_toplam"] or None,
                      r["alacak_toplam"] or None, r["bakiye"],
                      "Borçlu" if r["bakiye"] > 0 else ("Alacaklı" if r["bakiye"] < 0 else "Kapalı"), r["son_hareket"]]
                     for r in rows],
                    ["", f"{len(rows)} cari", "", "", sum(r["borc_toplam"] for r in rows),
                     sum(r["alacak_toplam"] for r in rows), sum(r["bakiye"] for r in rows), "", None],
                    widths=[13, 48, 20, 22, 20, 20, 23, 13, 17], tones=[_bal_tone(r["bakiye"]) for r in rows],
                    tone_cols=[6, 7], keys=[r["id"] for r in rows], key_kind="cari", font=8.0)]
    return Report("cari_bakiye", "Cari Bakiye Listesi", [f"Tarih itibarıyla: {fmt_date(d)}", tip_txt], blocks,
                  f"Cari_Bakiye_Listesi_{d.isoformat()}", "Cari Raporları")


def build_cari_mizan(svc, v: dict) -> Report:
    an = Analytics(svc)
    s, e = _span(svc, v)
    tip = v.get("tip")
    rows = an.cari_activity(s, e, tip)
    tip_txt = dict((b, a) for a, b in CARI_TIP_CHOICES).get(tip, "Tüm cariler")
    blocks = [KV("Özet", [("Cari Sayısı", len(rows), "int"), ("Toplam Devir", sum(r["devir"] for r in rows), "balance"),
                          ("Dönem Borç", sum(r["borc"] for r in rows), "money"),
                          ("Dönem Alacak", sum(r["alacak"] for r in rows), "money"),
                          ("Dönem Sonu Bakiye", sum(r["bakiye"] for r in rows), "balance"),
                          ("Dönem Tahsilatı", sum(r["tahsilat"] for r in rows), "money"),
                          ("Dönem Veresiyesi", sum(r["veresiye"] for r in rows), "money"),
                          ("Tedarikçilere Ödeme", sum(r["odeme"] for r in rows), "money")]),
              Table("Cari Hesap Mizanı", ["Kod", "Ünvan", "Tip", "Devir", "Dönem Borç", "Dönem Alacak", "Bakiye",
                                          "İşlem", "Son Hareket"],
                    ["text", "text", "text", "balance", "money", "money", "balance", "int", "date"],
                    [[r["kod"], r["unvan"], r["tip_adi"], r["devir"], r["borc"] or None, r["alacak"] or None,
                      r["bakiye"], r["adet"] or None, r["son_hareket"]] for r in rows],
                    ["", f"{len(rows)} cari", "", sum(r["devir"] for r in rows), sum(r["borc"] for r in rows),
                     sum(r["alacak"] for r in rows), sum(r["bakiye"] for r in rows), sum(r["adet"] for r in rows), None],
                    widths=[12, 46, 18, 22, 20, 20, 22, 9, 17], tones=[_bal_tone(r["bakiye"]) for r in rows],
                    tone_cols=[6], keys=[r["id"] for r in rows], key_kind="cari", font=8.0,
                    note="Borç: carinin borcuna işlenenler (veresiye, cariye ödeme) · Alacak: carinin alacağına "
                         "işlenenler (tahsilat, tedarikçi faturası) · (B) bize borçlu, (A) biz borçluyuz.")]
    return Report("cari_mizan", "Cari Hesap Mizanı", _plines(s, e, tip_txt, tum=_all_time(v)), blocks,
                  f"Cari_Mizan_{s.isoformat()}_{e.isoformat()}", "Cari Raporları")


def build_yaslandirma(svc, v: dict) -> Report:
    an = Analytics(svc)
    d = to_date(v.get("tarih")) or today()
    side = v.get("taraf") or "alacak"
    ag = an.cari_aging(d, side)
    rows = ag["satirlar"]
    title = "Alacak Yaşlandırma Raporu" if side == "alacak" else "Borç Yaşlandırma Raporu"
    son = "Son Tahsilat" if side == "alacak" else "Son Ödeme"
    labels = ag["etiketler"]
    blocks: list = [KV("Özet", [("Toplam " + ("Alacak" if side == "alacak" else "Borç"), ag["toplam"], "money"),
                                ("Cari Sayısı", len(rows), "int"),
                                ("90 Günden Eski", ag["vadesi_gecmis_90"], "money"),
                                ("90 Günden Eski Payı", ratio(ag["vadesi_gecmis_90"], ag["toplam"]), "percent")] +
                       [(lab, val, "money") for lab, val in zip(labels, ag["dilim_toplam"])])]
    blocks.append(Chart("Yaş Dilimlerine Göre Dağılım", "hbar", labels, [("Tutar", AGING_COLORS, ag["dilim_toplam"])],
                        height=40))
    blocks.append(Table(
        "Cari Bazında Yaşlandırma", ["Kod", "Ünvan", "Telefon", "Bakiye"] + labels + ["En Eski Açık Kalem",
                                                                                 "Gecikme (gün)", son],
        ["text", "text", "text", "money"] + ["money"] * len(labels) + ["date", "int", "date"],
        [[r["kod"], r["unvan"], r["telefon"], r["bakiye"]] + [x or None for x in r["dilimler"]] +
         [r["en_eski"], r["gecikme"], r["son_odeme"]] for r in rows],
        ["", f"{len(rows)} cari", "", ag["toplam"]] + [x or None for x in ag["dilim_toplam"]] + [None, None, None],
        widths=[11, 40, 20, 19] + [16] * len(labels) + [17, 12, 17], keys=[r["id"] for r in rows], key_kind="cari",
        font=7.8, sheet="Yaşlandırma",
        note="Yaşlandırma FIFO yöntemiyle hesaplanır: ödemeler önce en eski kalemleri kapatır; açık kalan bakiye en "
             "yeni kalemlerden oluşur ve kalemin tarihinden itibaren geçen gün sayısına göre dilimlere ayrılır."))
    return Report("yaslandirma", title, [f"Tarih itibarıyla: {fmt_date(d)}",
                                         "Bize borçlu cariler" if side == "alacak" else "Borçlu olduğumuz cariler"],
                  blocks, f"{'Alacak' if side == 'alacak' else 'Borc'}_Yaslandirma_{d.isoformat()}", "Cari Raporları",
                  landscape=True)


# --------------------------------------------------------------------------- PERSONEL
def build_personel_ekstre(svc, v: dict) -> Report:
    """Seçilen personelin hesap dökümü (cari ekstresi gibi); personel seçilmezse tüm personelin dönem özeti."""
    svc.require_admin()
    s, e = _span(svc, v)
    pid = v.get("personel")
    if not pid:
        rows = []
        for p in svc.personel_listesi(include_inactive=True):
            st = svc.personel_ekstre(p["id"], s, e)
            if st["devir"] or st["satirlar"] or p["aktif"]:
                rows.append([p["kod"], p["ad_soyad"], p["gorev"], st["devir"], st["toplam_borc"] or None,
                             st["toplam_alacak"] or None, st["bakiye"]])
        blocks = [Table("Personel Hesapları", ["Kod", "Ad Soyad", "Görev", "Devir", "Dönem Borç", "Dönem Alacak",
                                               "Bakiye"],
                        ["text", "text", "text", "balance", "money", "money", "balance"], rows,
                        ["", f"{len(rows)} personel", "", sum(r[3] for r in rows), sum(r[4] or 0 for r in rows),
                         sum(r[5] or 0 for r in rows), sum(r[6] for r in rows)],
                        widths=[13, 46, 26, 22, 21, 21, 23], tones=[_bal_tone(r[6]) for r in rows], tone_cols=[6],
                        empty="Personel kaydı yok.",
                        note="Bakiye: (B) personel bize borçlu, (A) personele borçluyuz. Tek bir personelin dökümü "
                             "için personel seçin.")]
        return Report("personel_ekstre", "Personel Hesap Dökümü", _plines(s, e, "Tüm personel", tum=_all_time(v)),
                      blocks, f"Personel_Dokumu_{s.isoformat()}_{e.isoformat()}", "Yönetim Raporları")
    p = svc.get_personel(pid)
    st = svc.personel_ekstre(pid, s, e)
    rows = [[s.isoformat(), "Devir", "", "", "Önceki dönemden devreden bakiye", None, None, st["devir"]]]
    rows += [[m["tarih"], m["islem"], m["kalem"], m["odeme"], m["aciklama"], m["borc"] or None, m["alacak"] or None,
              m["bakiye"]] for m in st["satirlar"]]
    blocks = [KV("Personel Bilgileri", [
        ("Personel Kodu", p["kod"] or "-", "text"), ("Ad Soyad", p["ad_soyad"], "text"),
        ("Görev", p["gorev"] or "-", "text"), ("Telefon", p["telefon"] or "-", "text"),
        ("Devir", st["devir"], "balance"), ("Dönem Borç", st["toplam_borc"], "money"),
        ("Dönem Alacak", st["toplam_alacak"], "money"), ("Dönem Sonu Bakiye", st["bakiye"], "balance")]),
        Table("Hesap Hareketleri", ["Tarih", "İşlem", "Kalem", "Ödeme Tipi", "Açıklama", "Borç", "Alacak", "Bakiye"],
              ["date", "text", "text", "text", "text", "money", "money", "balance"], rows,
              ["", "Dönem Toplamı", "", "", "", st["toplam_borc"], st["toplam_alacak"], st["bakiye"]],
              widths=[20, 24, 26, 20, 46, 21, 21, 24], tones=["bold"] + [_tone(m["tur"]) for m in st["satirlar"]],
              tone_cols=[1], keys=[None] + [m["id"] if m["kaynak"] == "hareket" else None for m in st["satirlar"]],
              key_kind="hareket", sheet="Hesap Dökümü",
              note="Bakiye: (B) personel bize borçlu, (A) personele borçluyuz. Kasa dışı: hak ediş ve devreden "
                   "bakiye kayıtları kasayı etkilemez.")]
    return Report("personel_ekstre", "Personel Hesap Dökümü", _plines(s, e, p["ad_soyad"], tum=_all_time(v)), blocks,
                  f"Personel_{p['ad_soyad']}_{e.isoformat()}", "Yönetim Raporları",
                  signatures=[svc.firma().get("adi") or "Firma Yetkilisi", p["ad_soyad"]])


# --------------------------------------------------------------------------- YÖNETİM
def build_islem_gecmisi(svc, v: dict) -> Report:
    svc.require_admin()
    s, e = _span(svc, v, audit=True)
    user = v.get("kullanici")
    rows = svc.audit(s, e, user, limit=20000)
    cnt: dict = {}
    for r in rows:
        cnt[r["islem"]] = cnt.get(r["islem"], 0) + 1
    tones = {"SILME": "GIDER", "GIRIS_HATASI": "GIDER", "GERI_YUKLEME": "VIRMAN", "ACMA": "VIRMAN",
             "EKLEME": "GELIR", "KAPANIS": "CARI"}
    blocks = [KV("Özet", [("Kayıt Sayısı", len(rows), "int"),
                          ("Kullanıcı Sayısı", len({r["kullanici_adi"] for r in rows if r["kullanici_adi"]}), "int"),
                          ("Ekleme", cnt.get("EKLEME", 0), "int"), ("Güncelleme", cnt.get("GUNCELLEME", 0), "int"),
                          ("Silme", cnt.get("SILME", 0), "int"), ("Gün Kapanışı", cnt.get("KAPANIS", 0), "int"),
                          ("Gün Açma", cnt.get("ACMA", 0), "int"), ("Hatalı Giriş", cnt.get("GIRIS_HATASI", 0), "int")]),
              Table("İşlem Kayıtları", ["Zaman", "Kullanıcı", "İşlem", "Konu", "Kayıt No", "Ayrıntı"],
                    ["datetime", "text", "text", "text", "int", "text"],
                    [[r["zaman"], r["kullanici_adi"], r["islem_adi"], r["konu"], r["kayit_id"], r["detay"]]
                     for r in rows], widths=[24, 18, 18, 14, 11, 120], tones=[tones.get(r["islem"]) for r in rows],
                    tone_cols=[2], font=7.8, sheet="İşlem Geçmişi",
                    note="En fazla 20.000 kayıt listelenir." if len(rows) >= 20000 else "")]
    return Report("islem_gecmisi", "İşlem Geçmişi Raporu",
                  _plines(s, e, f"Kullanıcı: {user}" if user else "Tüm kullanıcılar", tum=_all_time(v)), blocks,
                  f"Islem_Gecmisi_{s.isoformat()}_{e.isoformat()}", "Yönetim Raporları", landscape=True)


# --------------------------------------------------------------------------- tanımlar
def _period(default="Bu Ay"):
    return Param("donem", "period", "Dönem", default)


REPORTS: list[ReportDef] = [
    ReportDef("gunluk_kasa", "Kasa Raporları", "Günlük Kasa Raporu",
              "Seçilen günün ödeme tipi bakiyeleri (devir, gelir, gider, virman, gün sonu, sayım farkı), kategori "
              "toplamları ve tüm hareketleri. İmza alanlıdır; gün sonunda yazdırılıp dosyalanır.",
              "wallet", [Param("tarih", "date", "Tarih")], build_gunluk_kasa, pdf=_pdf_gunluk),
    ReportDef("kasa_defteri", "Kasa Raporları", "Kasa Defteri",
              "Dönemdeki tüm kasa hareketleri tarih sırasıyla: giriş, çıkış ve yürüyen bakiye; isteğe bağlı gün "
              "toplamları. Ödeme tipi seçilirse yalnız o kasanın (ör. Nakit) defteri çıkar.",
              "list", [_period(), Param("odeme", "odeme", "Ödeme tipi"),
                       Param("gun_toplam", "check", "Gün toplamlarını göster", True)], build_kasa_defteri),
    ReportDef("kapanislar", "Kasa Raporları", "Kapanışlar ve Sayım Farkları",
              "Kapatılan günler, kapatan kullanıcı ve zaman, gün toplamları, kasa sayım farkları (fazla / eksik) "
              "ve henüz kapatılmamış günler.", "lock", [_period()], build_kapanislar),
    ReportDef("odeme_bakiye", "Kasa Raporları", "Ödeme Tipi Bakiye Raporu",
              "Her ödeme tipinin (Nakit, Kart, Havale, Çek…) dönem başı ve sonu bakiyesi, hareket özeti, bakiye "
              "seyri grafiği ve gün sonu bakiye tablosu.", "scale", [_period()], build_odeme_bakiye),
    ReportDef("donem_analiz", "Gelir – Gider Raporları", "Dönem Analiz Raporu",
              "Kapsamlı yönetim raporu: göstergeler ve karşılaştırma, otomatik yorumlar, gelir-gider grafikleri, "
              "kategori dağılımları, ödeme tipleri, en çok çalışılan cariler, haftanın günleri ve dönem dökümü.",
              "chart", [_period(), Param("grup", "choice", "Gruplama", None, GROUPS),
                        Param("karsilastirma", "choice", "Karşılaştırma", "prev", COMPARE_MODES)],
              build_donem_analiz),
    ReportDef("hareket_dokumu", "Gelir – Gider Raporları", "Hareket Dökümü",
              "Filtrelenmiş hareket listesi (tür, kategori, ödeme tipi, cari, açıklama araması) ve gelir / gider "
              "toplamları.", "receipt",
              [_period(), Param("tur", "choice", "Tür", None, TUR_CHOICES), Param("kategori", "kategori", "Kategori"),
               Param("odeme", "odeme", "Ödeme tipi"), Param("cari", "cari_opt", "Cari"),
               Param("arama", "text", "Açıklamada ara")], build_hareket_dokumu),
    ReportDef("kategori", "Gelir – Gider Raporları", "Kategori Raporu",
              "Kategorilerin toplamı, payı, ortalaması ve karşılaştırma dönemine göre değişimi; dağılım grafiği ve "
              "kategori × dönem (gün/hafta/ay) özet tablosu.", "tags",
              [_period(), Param("tur", "choice", "Kapsam", "GIDER", KAT_TUR_CHOICES),
               Param("grup", "choice", "Tablo gruplaması", None, GROUPS),
               Param("karsilastirma", "choice", "Karşılaştırma", "prev", COMPARE_MODES)], build_kategori),
    ReportDef("aylik", "Gelir – Gider Raporları", "Aylık Gelir – Gider Tablosu",
              "Seçilen yılın 12 aylık gelir, gider, net ve kümülatif tablosu; önceki yılın aynı aylarıyla "
              "karşılaştırma ve kategori × ay tabloları.", "calendar", [Param("yil", "year", "Yıl")], build_aylik),
    ReportDef("cari_ekstre", "Cari Raporları", "Cari Hesap Ekstresi",
              "Seçilen carinin dönem başı devri, tüm borç / alacak hareketleri ve yürüyen bakiyesi. İmza alanlıdır; "
              "cariye mutabakat için gönderilebilir.", "users",
              [Param("cari", "cari", "Cari", required=True), _period("Bu Yıl")], build_cari_ekstre, pdf=_pdf_ekstre),
    ReportDef("cari_bakiye", "Cari Raporları", "Cari Bakiye Listesi",
              "Seçilen tarih itibarıyla tüm carilerin toplam borç, alacak ve bakiyeleri; alacaklarımız ve "
              "borçlarımız toplamı.", "users",
              [Param("tarih", "date", "Tarih itibarıyla"), Param("tip", "choice", "Cari tipi", None, CARI_TIP_CHOICES),
               Param("sadece_bakiyeli", "check", "Sadece bakiyesi olanlar", True)], build_cari_bakiye),
    ReportDef("cari_mizan", "Cari Raporları", "Cari Hesap Mizanı",
              "Her cari için dönem başı devir, dönem içi borç ve alacak toplamları, dönem sonu bakiye ve işlem "
              "sayısı.", "scale", [_period(), Param("tip", "choice", "Cari tipi", None, CARI_TIP_CHOICES)],
              build_cari_mizan),
    ReportDef("yaslandirma", "Cari Raporları", "Alacak / Borç Yaşlandırma",
              "Açık bakiyelerin kaç gündür beklediği: 0–30, 31–60, 61–90, 91–180 ve 180 gün üzeri dilimler; en eski "
              "açık kalem ve son tahsilat tarihi.", "history",
              [Param("tarih", "date", "Tarih itibarıyla"), Param("taraf", "choice", "Kapsam", "alacak", AGING_SIDES)],
              build_yaslandirma),
    ReportDef("personel_ekstre", "Personel Raporları", "Personel Hesap Dökümü",
              "Seçilen personelin dönem başı devri, hak ediş, ödeme, avans, borç ve geri ödemeleri ile yürüyen "
              "bakiyesi. Personel seçilmezse tüm personelin dönem özeti. Yalnızca yöneticiler görebilir.", "user",
              [Param("personel", "personel", "Personel"), _period("Bu Yıl")], build_personel_ekstre, admin=True),
    ReportDef("islem_gecmisi", "Yönetim Raporları", "İşlem Geçmişi Raporu",
              "Kim, ne zaman, neyi ekledi / değiştirdi / sildi; girişler, gün kapanışları, yedekler. Yalnızca "
              "yöneticiler görebilir.", "shield",
              [_period("Son 30 Gün"), Param("kullanici", "user", "Kullanıcı")], build_islem_gecmisi, admin=True),
]
REPORT_BY_KEY = {r.key: r for r in REPORTS}


def available(svc) -> list[ReportDef]:
    return [r for r in REPORTS if not r.admin or svc.is_admin]


def default_values(rd: ReportDef, svc=None) -> dict:
    """Parametrelerin varsayılan değerleri (testler ve ilk açılış için)."""
    out: dict = {}
    for p in rd.params:
        if p.kind == "period":
            out[p.key] = period_range(p.default or "Bu Ay")
        elif p.kind == "date":
            out[p.key] = today()
        elif p.kind == "year":
            out[p.key] = today().year
        elif p.kind == "cari" and svc is not None:
            cs = svc.caris()
            out[p.key] = cs[0]["id"] if cs else None
        else:
            out[p.key] = p.default
    return out


def build(key: str, svc, values: dict) -> Report:
    return REPORT_BY_KEY[key].build(svc, values)


def write_pdf(key: str, svc, values: dict, path, firma: dict, kullanici: str = "") -> Path:
    rd = REPORT_BY_KEY[key]
    if rd.pdf:
        return rd.pdf(path, svc, values, firma, kullanici)
    return reports_pdf.render_report(path, rd.build(svc, values), firma, kullanici)


def write_excel(report: Report, path) -> Path:
    return excel.export_report(path, report)


__all__ = ["REPORTS", "REPORT_BY_KEY", "ReportDef", "Param", "available", "default_values", "build", "write_pdf",
           "write_excel", "AGING_BUCKETS"]
