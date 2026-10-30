"""Analiz: sekmeli gösterge paneli.

Genel Bakış · Trend · Kategoriler · Kasa ve Ödeme Tipleri · Cariler · Günler ve Saatler · Yıllık Karşılaştırma
Tüm sekmeler üstteki dönem / gruplama / karşılaştırma seçimini kullanır (Yıllık sekmesi kendi yıl seçimini).
Sekmeler yalnızca açıldıklarında hesaplanır.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QStackedWidget, QTabBar, QVBoxLayout, QWidget

from ... import reports
from ...analytics import AGING_BUCKETS, COMPARE_MODES, GROUP_NAMES, GROUPS, Analytics, change
from ...util import (TR_DAYS_SHORT, fmt_compact, fmt_date, fmt_delta_percent, fmt_money, fmt_percent, group_key,
                     to_date, today)
from ..charts import (CalendarHeatmap, DonutChart, Gauge, HCompareBars, TrendChart, TrendKpi, palette)
from ..context import export_report_excel, open_target, period_text, report_pdf
from ..report_view import insight_label
from ..theme import C
from ..widgets import (Card, Col, DataTable, HBarList, KpiCard, PeriodSelector, Segmented, button, combo,
                       combo_select, fit_table_height, muted)
from .base import Page, log, scroll_body

AGING_COLORS = ["#15803D", "#65A30D", "#D97706", "#EA580C", "#B91C1C"]


@dataclass
class Params:
    s: dt.date
    e: dt.date
    ps: dt.date | None
    pe: dt.date | None
    group: str | None
    mode: str | None

    @property
    def days(self) -> int:
        return (self.e - self.s).days + 1


def _heat(base: str, vmax_getter):
    """Isı haritası tablo hücresi rengi: değer büyüdükçe koyulaşır."""
    def f(row, key):
        v = row.get(key) or 0
        vmax = vmax_getter()
        if not v or not vmax:
            return None
        t = min(1.0, abs(v) / vmax)
        a, b = _rgb("#FFFFFF"), _rgb(base)
        k = 0.10 + 0.55 * t
        return "#%02X%02X%02X" % tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))
    return f


def _rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _money_text(v, symbol=True) -> str:
    return fmt_money(v, symbol) if v is not None else "—"


class AnalizTab(QWidget):
    name = ""

    def __init__(self, page: "AnalizPage"):
        super().__init__()
        self.page = page
        self.ctx = page.ctx
        self.svc = page.svc
        self.an: Analytics = page.an
        self.dirty = True
        self.lay = scroll_body(self)

    def load(self, p: Params) -> None:
        raise NotImplementedError

    def open(self, kind, key) -> None:
        if key is not None:
            # tablo olay işleyicisi bittikten sonra aç (açılan ekran/pencere tabloyu yenileyebilir)
            QTimer.singleShot(0, lambda: open_target(self.ctx, self, kind, key))


# =========================================================================== 1. Genel bakış
class OzetTab(AnalizTab):
    name = "Genel Bakış"

    def __init__(self, page):
        super().__init__(page)
        L = self.lay
        g = QGridLayout()
        g.setSpacing(14)
        self.k = {"gelir": TrendKpi("Toplam Gelir", "in", C["gelir"], C["gelir_soft"]),
                  "gider": TrendKpi("Toplam Gider", "out", C["gider"], C["gider_soft"]),
                  "net": TrendKpi("Net Sonuç (Gelir − Gider)", "scale", C["primary"], C["primary_soft"]),
                  "marj": TrendKpi("Kâr Marjı (net / gelir)", "chart", C["navy"], "#E5EAF3")}
        for i, w in enumerate(self.k.values()):
            g.addWidget(w, 0, i)
        self.k2 = {"gunluk_gelir": KpiCard("Günlük Ortalama Gelir", "in", C["gelir"], C["gelir_soft"]),
                   "gunluk_gider": KpiCard("Günlük Ortalama Gider", "out", C["gider"], C["gider_soft"]),
                   "adet": KpiCard("İşlem Sayısı", "list", C["navy"], "#E5EAF3"),
                   "gun": KpiCard("Faaliyet Günü", "calendar", C["virman"], C["virman_soft"])}
        for i, w in enumerate(self.k2.values()):
            g.addWidget(w, 1, i)
        L.addLayout(g)

        gc = Card("Göstergeler")
        gc.header.addWidget(muted("Yeşil: iyi · Turuncu: dikkat · Kırmızı: sorun · Ayrıntı için göstergenin "
                                  "üzerine gelin", small=True, wrap=False))
        h = QHBoxLayout()
        h.setSpacing(10)
        self.g_gider = Gauge("Gider / Gelir Oranı", good_high=False, green=0.80, amber=0.95,
                             tip="Gelirin yüzde kaçının gidere harcandığı. Düşük olması iyidir; %100'ün üstü "
                                 "dönemin zararla kapandığını gösterir.")
        self.g_tahsilat = Gauge("Tahsilat Oranı", green=0.90, amber=0.60,
                                tip="Dönemde carilere yazılan veresiyenin (borç fişi) ne kadarının tahsil edildiği.")
        self.g_kapanis = Gauge("Gün Kapanış Oranı", green=0.999, amber=0.90,
                               tip="Kasa hareketi olan günlerden (bugün hariç) kaçının kapatıldığı.")
        self.g_sayim = Gauge("Sayım Doğruluğu", green=0.95, amber=0.80,
                             tip="Gün sonu sayımı yapılan günlerin ne kadarında kasa farkı çıkmadığı.")
        for w in (self.g_gider, self.g_tahsilat, self.g_kapanis, self.g_sayim):
            h.addWidget(w, 1)
        gc.add(h)
        L.addWidget(gc)

        cc = Card("Gelir, Gider ve Net")
        self.c_group = muted("", small=True, wrap=False)
        cc.header.addWidget(self.c_group)
        self.chart = TrendChart(height=290, net_tip=True)
        cc.add(self.chart)
        L.addWidget(cc)

        row = QHBoxLayout()
        row.setSpacing(14)
        c1 = Card("Gelir Dağılımı (kategori)")
        self.d_gelir = DonutChart(height=230)
        c1.add(self.d_gelir)
        c2 = Card("Gider Dağılımı (kategori)")
        self.d_gider = DonutChart(height=230)
        c2.add(self.d_gider)
        row.addWidget(c1, 1)
        row.addWidget(c2, 1)
        L.addLayout(row)

        ic = Card("Öne Çıkanlar")
        ic.header.addWidget(muted("Dönem verilerinden otomatik çıkarılan yorumlar", small=True, wrap=False))
        self.ins = QVBoxLayout()
        self.ins.setSpacing(6)
        ic.add(self.ins)
        L.addWidget(ic)
        L.addStretch(1)

    def load(self, p: Params) -> None:
        an = self.an
        cur = an.kpis(p.s, p.e)
        prev = an.kpis(p.ps, p.pe) if p.ps else None
        group, seri = an.series(p.s, p.e, p.group)

        def delta(key):
            return change(cur[key], prev[key]) if prev and prev[key] else None

        def cmp_txt(key):
            if not prev:
                return ""
            return f"Karşılaştırma: {fmt_money(prev[key], True)}" if prev["adet"] else "Karşılaştırma döneminde kayıt yok"

        self.k["gelir"].set(fmt_money(cur["gelir"], True), delta("gelir"), True, cmp_txt("gelir"),
                            [r["gelir"] for r in seri], spark_color=C["gelir"])
        self.k["gider"].set(fmt_money(cur["gider"], True), delta("gider"), False, cmp_txt("gider"),
                            [r["gider"] for r in seri], spark_color=C["gider"])
        self.k["net"].set(fmt_money(cur["net"], True), delta("net"), True, cmp_txt("net"),
                          [r["kumulatif"] for r in seri], value_color=C["gider"] if cur["net"] < 0 else None)
        m, pm = cur["marj"], (prev["marj"] if prev else None)
        self.k["marj"].set(fmt_percent(m) if m is not None else "—",
                           (m - pm) if (m is not None and pm is not None) else None, True,
                           (f"Karşılaştırma: {fmt_percent(pm)}" if pm is not None else
                            ("Karşılaştırma döneminde gelir yok" if prev else "")),
                           [r["marj"] for r in seri], value_color=C["gider"] if (m or 0) < 0 else None, pp=True)
        self.k2["gunluk_gelir"].set(fmt_money(cur["gunluk_gelir"], True), "faaliyet günü başına")
        self.k2["gunluk_gider"].set(fmt_money(cur["gunluk_gider"], True), "faaliyet günü başına")
        self.k2["adet"].set(f"{cur['adet']}", f"{cur['gelir_adet']} gelir · {cur['gider_adet']} gider · "
                                              f"ort. gelir {fmt_money(cur['ort_gelir'])}")
        self.k2["gun"].set(f"{cur['gun_sayisi']}", f"{p.days} günlük dönemde kayıt olan gün")

        self.g_gider.set_value(cur["gider_orani"],
                               sub=f"{fmt_compact(cur['gider'])} / {fmt_compact(cur['gelir'])}" if cur["gelir"]
                               else "Dönemde gelir yok")
        self.g_tahsilat.set_value(cur["tahsilat_orani"],
                                  sub=(f"Tahsilat {fmt_compact(cur['cari_tahsilat'])} · veresiye "
                                       f"{fmt_compact(cur['cari_borc'])}") if cur["cari_borc"] else "Dönemde veresiye yok")
        cs = an.closing_status(p.s, p.e)
        self.g_kapanis.set_value(cs["oran"], sub=(f"{cs['kapali']} / {cs['gerekli']} gün kapatıldı" +
                                                  (f" · {len(cs['acik'])} açık" if cs["acik"] else "") +
                                                  (f" · {cs['kilitli']} kapanışsız" if cs["kilitli"] else ""))
                                 if cs["gerekli"] else "Kapanması gereken gün yok")
        cd = an.count_differences(p.s, p.e)
        self.g_sayim.set_value(cd["dogruluk"], sub=(f"{cd['farkli_gun']} günde fark · net "
                                                    f"{fmt_money(cd['net'], plus=True)}") if cd["sayim_gun"]
                               else "Sayım yapılmamış")

        self.c_group.setText(f"{GROUP_NAMES[group]} · üzerine gelince ayrıntı")
        self.chart.set_data([r["etiket"] for r in seri], [
            {"name": "Gelir", "color": C["gelir"], "values": [r["gelir"] for r in seri]},
            {"name": "Gider", "color": C["gider"], "values": [r["gider"] for r in seri]},
            {"name": "Net", "color": C["primary"], "values": [r["net"] for r in seri], "kind": "line"}],
            [r["baslik"] for r in seri])
        self.d_gelir.set_items([(r["ad"], r["toplam"]) for r in self.svc.category_totals(p.s, p.e, "GELIR")])
        self.d_gider.set_items([(r["ad"], r["toplam"]) for r in self.svc.category_totals(p.s, p.e, "GIDER")])
        while self.ins.count():
            w = self.ins.takeAt(0).widget()
            if w:
                w.deleteLater()
        for it in an.insights(p.s, p.e, p.ps, p.pe, cur, prev):
            self.ins.addWidget(insight_label(it["tone"], it["text"]))


# =========================================================================== 2. Trend
class TrendTab(AnalizTab):
    name = "Trend"

    def __init__(self, page):
        super().__init__(page)
        L = self.lay
        c = Card("Gelir ve Gider Trendi")
        self.t_note = muted("", small=True, wrap=False)
        c.header.addWidget(self.t_note)
        self.trend = TrendChart(height=300)
        c.add(self.trend)
        L.addWidget(c)
        c = Card("Kümülatif Net (birikimli gelir − gider)")
        c.header.addWidget(muted("Dönem başından itibaren biriken net sonuç", small=True, wrap=False))
        self.cum = TrendChart(height=220)
        c.add(self.cum)
        L.addWidget(c)
        c = Card("Kategori Dağılımının Seyri")
        self.st_tur = Segmented([("GIDER", "Gider"), ("GELIR", "Gelir")], {"GIDER": "GIDER", "GELIR": "GELIR"})
        self.st_tur.set_value("GIDER")
        self.st_tur.changed.connect(lambda _: self._stack())
        c.header.addWidget(self.st_tur)
        self.stack_chart = TrendChart(height=300, stacked=True)
        c.add(self.stack_chart)
        L.addWidget(c)
        c = Card("Dönem Dökümü")
        self.table = DataTable([
            Col("baslik", "Dönem", stretch=True), Col("gelir", "Gelir", "money", 120),
            Col("gider", "Gider", "money", 120), Col("net", "Net", "smoney", 120, bold=True),
            Col("kumulatif", "Kümülatif Net", "smoney", 130), Col("marj", "Kâr Marjı", "percent", 90),
            Col("d_gelir", "Gelir Değişimi", "smoney", 128, fmt=lambda v, r: fmt_delta_percent(v)),
            Col("adet", "İşlem", "int", 70)], sortable=False)
        c.add(self.table)
        L.addWidget(c)
        L.addStretch(1)
        self.p = None

    def load(self, p: Params) -> None:
        self.p = p
        group, seri = self.an.series(p.s, p.e, p.group)
        labels, titles = [r["etiket"] for r in seri], [r["baslik"] for r in seri]
        series = [{"name": "Gelir", "color": C["gelir"], "values": [r["gelir"] for r in seri], "kind": "line"},
                  {"name": "Gider", "color": C["gider"], "values": [r["gider"] for r in seri], "kind": "line"},
                  {"name": "Net", "color": C["primary"], "values": [r["net"] for r in seri], "kind": "area"}]
        if p.ps:
            # karşılaştırma dönemindeki günler, bu dönemin aynı konumdaki günlerine kaydırılıp aynı gruplara toplanır
            shift = (p.s - p.ps).days
            idx = {r["key"]: i for i, r in enumerate(seri)}
            vals = [0] * len(seri)
            for d, v in self.an.daily_totals(p.ps, p.pe).items():
                sd = to_date(d) + dt.timedelta(days=shift)
                i = idx.get(group_key(sd, group)) if p.s <= sd <= p.e else None
                if i is not None:
                    vals[i] += v["gelir"]
            if any(vals):
                series.append({"name": "Gelir (karşılaştırma)", "color": "#94A3B8", "values": vals, "kind": "line",
                               "dashed": True})
        self.t_note.setText(f"{GROUP_NAMES[group]} · kesikli çizgi: karşılaştırma dönemi" if len(series) > 3
                            else GROUP_NAMES[group])
        self.trend.set_data(labels, series, titles)
        self.cum.set_data(labels, [{"name": "Kümülatif net", "color": C["primary"],
                                    "values": [r["kumulatif"] for r in seri], "kind": "area"}], titles)
        rows, prev = [], None
        for r in seri:
            rows.append({**r, "d_gelir": change(r["gelir"], prev) if prev else None})
            prev = r["gelir"]
        self.table.set_rows(list(reversed(rows)), keep_selection_key=None)
        fit_table_height(self.table, len(rows), 18)
        self._stack()

    def _stack(self) -> None:
        p = self.p
        if not p:
            return
        st = self.an.category_stack(p.s, p.e, self.st_tur.value(), p.group)
        colors = [palette(i) for i in range(len(st["series"]))]
        if st["series"] and st["series"][-1][0].startswith("Diğer"):
            colors[-1] = "#A3AEC2"
        self.stack_chart.set_data(st["labels"], [{"name": n, "color": c, "values": v}
                                                 for (n, v), c in zip(st["series"], colors)], st["titles"])


# =========================================================================== 3. Kategoriler
class KategoriTab(AnalizTab):
    name = "Kategoriler"

    def __init__(self, page):
        super().__init__(page)
        L = self.lay
        top = QHBoxLayout()
        self.tur = Segmented([("GIDER", "Gider Kategorileri"), ("GELIR", "Gelir Kategorileri")],
                             {"GIDER": "GIDER", "GELIR": "GELIR"})
        self.tur.set_value("GIDER")
        self.tur.changed.connect(lambda _: self.load(self.p) if self.p else None)
        top.addWidget(self.tur)
        top.addStretch(1)
        L.addLayout(top)
        row = QHBoxLayout()
        row.setSpacing(14)
        c1 = Card("Dağılım")
        self.donut = DonutChart(height=250)
        c1.add(self.donut)
        c1.lay.addStretch(1)
        c2 = Card("Karşılaştırma")
        self.cmp_note = muted("", small=True, wrap=False)
        c2.header.addWidget(self.cmp_note)
        self.bars = HCompareBars()
        c2.add(self.bars)
        c2.lay.addStretch(1)
        row.addWidget(c1, 5)
        row.addWidget(c2, 6)
        L.addLayout(row)
        c = Card("Kategori Tablosu")
        self.table = DataTable([
            Col("ad", "Kategori", stretch=True), Col("adet", "Adet", "int", 60), Col("toplam", "Toplam", "money", 120,
                                                                                    bold=True),
            Col("pay", "Pay", "percent", 82), Col("ort", "Ortalama", "money", 110),
            Col("onceki", "Karşılaştırma", "money", 120), Col("fark", "Fark", "smoney", 115,
                                                              fmt=lambda v, r: fmt_money(v, plus=True) if v is not None
                                                              else ""),
            Col("degisim", "Değişim", "smoney", 90,
                fmt=lambda v, r: (fmt_delta_percent(v) if v is not None else
                                  ("yeni" if r.get("toplam") and r.get("onceki") == 0 else "")))], sortable=True)
        c.add(self.table)
        L.addWidget(c)
        c = Card("Kategori × Dönem Tablosu")
        self.pv_note = muted("", small=True, wrap=False)
        c.header.addWidget(self.pv_note)
        self.pivot = DataTable([Col("ad", "Kategori")], sortable=False)
        c.add(self.pivot)
        L.addWidget(c)
        L.addStretch(1)
        self.p = None
        self._pv_max = 0

    def load(self, p: Params) -> None:
        self.p = p
        t = self.tur.value()
        rows = self.an.category_compare(p.s, p.e, p.ps, p.pe, t)
        self.donut.set_items([(r["ad"], r["toplam"]) for r in rows])
        self.bars.set_items([(r["ad"], r["toplam"], r["onceki"]) for r in rows[:12]],
                            C["gider"] if t == "GIDER" else C["gelir"], good_up=(t == "GELIR"),
                            names=("Bu dönem", "Karşılaştırma dönemi"))
        self.cmp_note.setText("En büyük 12 kategori" if len(rows) > 12 else "")
        tot = sum(r["toplam"] for r in rows)
        trows = list(rows)
        if rows:
            pt = sum(r["onceki"] or 0 for r in rows) if p.ps else None
            trows.append({"ad": "TOPLAM", "adet": sum(r["adet"] for r in rows), "toplam": tot,
                          "pay": 1.0 if tot else None, "onceki": pt, "fark": (tot - pt) if p.ps else None,
                          "degisim": change(tot, pt) if p.ps else None, "_bold": True, "_total": True})
        self.table.setColumnHidden(5, not p.ps)
        self.table.setColumnHidden(6, not p.ps)
        self.table.setColumnHidden(7, not p.ps)
        self.table.set_rows(trows, keep_selection_key=None)
        fit_table_height(self.table, len(trows), 16)
        # özet tablo
        pv = self.an.category_pivot(p.s, p.e, t, p.group)
        keys = pv["keys"]
        self._pv_max = max([max(r["values"]) if r["values"] else 0 for r in pv["rows"]] + [0])
        heat = _heat(C["gider"] if t == "GIDER" else C["gelir"], lambda: self._pv_max)
        cols = [Col("ad", "Kategori", width=170)]
        for i, lab in enumerate(pv["labels"]):
            cols.append(Col(f"k{i}", lab, "money", 106, fmt=lambda v, r: fmt_money(v) if v else "",
                            bg=lambda r, k=f"k{i}": None if r.get("_bold") else heat(r, k)))
        cols.append(Col("toplam", "Toplam", "money", 115, bold=True))
        self.pivot.set_columns(cols)
        prow = []
        for r in pv["rows"]:
            d = {"ad": r["ad"], "toplam": r["toplam"]}
            d.update({f"k{i}": v for i, v in enumerate(r["values"])})
            prow.append(d)
        if prow:
            d = {"ad": "TOPLAM", "toplam": sum(pv["totals"]), "_bold": True, "_total": True}
            d.update({f"k{i}": v for i, v in enumerate(pv["totals"])})
            prow.append(d)
        self.pivot.set_rows(prow, keep_selection_key=None)
        fit_table_height(self.pivot, len(prow), 16, hscroll=170 + 106 * len(keys) + 115 > self.pivot.width() - 4)
        self.pv_note.setText(f"{GROUP_NAMES[pv['group']]} · {len(keys)} sütun · koyu hücreler daha yüksek tutar")


# =========================================================================== 4. Kasa ve ödeme tipleri
class KasaTab(AnalizTab):
    name = "Kasa ve Ödeme Tipleri"

    def __init__(self, page):
        super().__init__(page)
        L = self.lay
        g = QHBoxLayout()
        g.setSpacing(14)
        self.k_son = KpiCard("Dönem Sonu Toplam Kasa", "wallet", C["primary"], C["primary_soft"])
        self.k_bas = KpiCard("Dönem Başı Toplam Kasa", "history", C["navy"], "#E5EAF3")
        self.k_deg = KpiCard("Dönemdeki Değişim", "scale", C["gelir"], C["gelir_soft"])
        self.k_vir = KpiCard("Virman (ödeme tipleri arası)", "swap", C["virman"], C["virman_soft"])
        for w in (self.k_son, self.k_bas, self.k_deg, self.k_vir):
            g.addWidget(w)
        L.addLayout(g)
        c = Card("Ödeme Tipi Bakiye Seyri")
        self.bh_note = muted("", small=True, wrap=False)
        c.header.addWidget(self.bh_note)
        self.balance = TrendChart(height=280)
        c.add(self.balance)
        L.addWidget(c)
        c = Card("Ödeme Tipi Özeti")
        self.table = DataTable([
            Col("ad", "Ödeme Tipi", stretch=True), Col("devir", "Dönem Başı", "smoney", 120),
            Col("gelir", "Gelir", "money", 115), Col("gider", "Gider", "money", 115),
            Col("virman_giris", "Virman Giriş", "money", 110), Col("virman_cikis", "Virman Çıkış", "money", 110),
            Col("son", "Dönem Sonu", "smoney", 125, bold=True),
            Col("degisim", "Değişim", "smoney", 115, fmt=lambda v, r: fmt_money(v, plus=True))], sortable=False)
        c.add(self.table)
        L.addWidget(c)
        row = QHBoxLayout()
        row.setSpacing(14)
        c1 = Card("Gelirin Ödeme Tipine Dağılımı")
        self.d_in = DonutChart(height=210)
        c1.add(self.d_in)
        c2 = Card("Giderin Ödeme Tipine Dağılımı")
        self.d_out = DonutChart(height=210)
        c2.add(self.d_out)
        row.addWidget(c1, 1)
        row.addWidget(c2, 1)
        L.addLayout(row)
        c = Card("Gün Kapanışları ve Sayım Farkları")
        self.cl_info = QLabel()
        self.cl_info.setWordWrap(True)
        c.add(self.cl_info)
        self.b_open = button("İlk kapatılmamış güne git", "wallet", callback=self._goto_open)
        self.b_open.hide()
        c.header.addWidget(self.b_open)
        self.diffs = DataTable([
            Col("tarih", "Tarih", "date", 104), Col("odeme", "Ödeme Tipi", width=130),
            Col("sistem", "Sistem Bakiyesi", "smoney", 145), Col("sayilan", "Sayılan", "money", 120),
            Col("fark", "Fark", "smoney", 110, bold=True, fmt=lambda v, r: fmt_money(v, plus=True)),
            Col("kapatan", "Kapatan", width=100), Col("aciklama", "Not", stretch=True)], sortable=False)
        self.diffs.rowActivated.connect(lambda r: self.open("gun", r.get("tarih")))
        c.add(self.diffs)
        L.addWidget(c)
        L.addStretch(1)
        self._acik = []

    def load(self, p: Params) -> None:
        an = self.an
        pt = an.payment_table(p.s, p.e)
        bas, son = sum(r["devir"] for r in pt), sum(r["son"] for r in pt)
        self.k_son.set(fmt_money(son, True), " · ".join(f"{r['ad']} {fmt_compact(r['son'])}" for r in pt),
                       C["gider"] if son < 0 else None)
        self.k_bas.set(fmt_money(bas, True), f"{fmt_date(p.s)} günü başında")
        self.k_deg.set(fmt_money(son - bas, True, plus=True), "gelir − gider (virmanlar toplamı değiştirmez)",
                       C["gider"] if son - bas < 0 else C["gelir"])
        vir = sum(r["virman_giris"] for r in pt)
        self.k_vir.set(fmt_money(vir, True), "ör. nakit bankaya yatırma, POS tutarı hesaba geçişi")
        bh = an.balance_history(p.s, p.e)
        self.bh_note.setText({"day": "Gün sonu bakiyeleri", "week": "Hafta sonu bakiyeleri",
                              "month": "Ay sonu bakiyeleri"}[bh["group"]])
        self.balance.set_data(bh["labels"], [{"name": sr["ad"], "color": palette(i), "values": sr["values"],
                                              "kind": "line"} for i, sr in enumerate(bh["series"])], bh["titles"])
        rows = list(pt)
        if pt:
            rows.append({"ad": "TOPLAM", "devir": bas, "gelir": sum(r["gelir"] for r in pt),
                         "gider": sum(r["gider"] for r in pt), "virman_giris": vir,
                         "virman_cikis": sum(r["virman_cikis"] for r in pt), "son": son, "degisim": son - bas,
                         "_bold": True, "_total": True})
        self.table.set_rows(rows, keep_selection_key=None)
        fit_table_height(self.table, len(rows))
        ch = an.payment_channels(p.s, p.e)
        self.d_in.set_items(ch["gelir"])
        self.d_out.set_items(ch["gider"])
        cs = an.closing_status(p.s, p.e)
        cd = an.count_differences(p.s, p.e)
        self._acik = cs["acik"]
        parts = [f"Kapanması gereken <b>{cs['gerekli']}</b> gün", f"kapatılan <b>{cs['kapali']}</b>"]
        if cs["kilitli"]:
            parts.append(f"kapanışsız kilitlenen <b>{cs['kilitli']}</b>")
        parts.append(f"<span style='color:{C['gider'] if cs['acik'] else C['gelir']}'>kapatılmamış "
                     f"<b>{len(cs['acik'])}</b></span>")
        parts.append(f"sayım yapılan <b>{cd['sayim_gun']}</b> gün, farklı <b>{cd['farkli_gun']}</b> gün")
        parts.append(f"fazla <b>{fmt_money(cd['fazla'])}</b>, eksik <b>{fmt_money(-cd['eksik'])}</b>, net "
                     f"<b>{fmt_money(cd['net'], True, plus=True)}</b>")
        if cs["acik"]:
            parts.append("açık günler: " + ", ".join(fmt_date(d) for d in cs["acik"][:6]) +
                         (" …" if len(cs["acik"]) > 6 else ""))
        self.cl_info.setText(" · ".join(parts))
        self.b_open.setVisible(bool(cs["acik"]))
        self.diffs.set_rows(cd["satirlar"] or [{"odeme": "Bu dönemde sayım farkı yok", "_muted": True}],
                            keep_selection_key=None)
        fit_table_height(self.diffs, max(1, len(cd["satirlar"])), 12)

    def _goto_open(self) -> None:
        if self._acik:
            self.open("gun", self._acik[0])


# =========================================================================== 5. Cariler
class CariTab(AnalizTab):
    name = "Cariler"

    def __init__(self, page):
        super().__init__(page)
        L = self.lay
        g = QHBoxLayout()
        g.setSpacing(14)
        self.k_al = KpiCard("Alacaklarımız (bize borçlu cariler)", "in", C["gelir"], C["gelir_soft"])
        self.k_bo = KpiCard("Borçlarımız (borçlu olduğumuz cariler)", "out", C["gider"], C["gider_soft"])
        self.k_tah = KpiCard("Dönem Tahsilatı (carilerden)", "receipt", C["primary"], C["primary_soft"])
        self.k_ver = KpiCard("Dönem Veresiyesi (borç fişleri)", "users", C["cari"], C["cari_soft"])
        for w in (self.k_al, self.k_bo, self.k_tah, self.k_ver):
            g.addWidget(w)
        L.addLayout(g)
        top = QHBoxLayout()
        self.side = Segmented([("alacak", "Alacaklarımız"), ("borc", "Borçlarımız")])
        self.side.set_value("alacak")
        self.side.changed.connect(lambda _: self._aging())
        top.addWidget(self.side)
        self.as_of = muted("", small=True, wrap=False)
        top.addWidget(self.as_of)
        top.addStretch(1)
        L.addLayout(top)
        row = QHBoxLayout()
        row.setSpacing(14)
        c1 = Card("Yaşlandırma (açık bakiyenin yaşı)")
        self.buckets = HBarList()
        c1.add(self.buckets)
        self.bucket_note = muted("", small=True)
        c1.add(self.bucket_note)
        c1.lay.addStretch(1)
        c2 = Card("En Yüksek Bakiyeli Cariler")
        self.top = HBarList()
        c2.add(self.top)
        c2.lay.addStretch(1)
        row.addWidget(c1, 1)
        row.addWidget(c2, 1)
        L.addLayout(row)
        c = Card("Cari Bazında Yaşlandırma")
        c.header.addWidget(muted("Çift tıklayınca cari açılır", small=True, wrap=False))
        cols = [Col("kod", "Kod", width=64), Col("unvan", "Ünvan", stretch=True),
                Col("bakiye", "Bakiye", "money", 112, bold=True)]
        for i, (_, _, lab) in enumerate(AGING_BUCKETS):
            cols.append(Col(f"b{i}", lab, "money", 96, fmt=lambda v, r: fmt_money(v) if v else "",
                            color=lambda r, i=i: AGING_COLORS[i] if r.get(f"b{i}") else None))
        cols += [Col("en_eski", "En Eski", "date", 98), Col("gecikme", "Gün", "int", 50),
                 Col("son_odeme", "Son Tahsilat", "date", 106)]
        self.aging_t = DataTable(cols)
        self.aging_t.rowActivated.connect(lambda r: self.open("cari", r.get("id")))
        c.add(self.aging_t)
        L.addWidget(c)
        c = Card("Dönem Cari Hareketleri (Mizan)")
        c.header.addWidget(muted("Devir + dönem borç − dönem alacak = bakiye · (B) bize borçlu, (A) biz borçluyuz",
                                 small=True, wrap=False))
        self.mizan = DataTable([
            Col("kod", "Kod", width=70), Col("unvan", "Ünvan", stretch=True), Col("tip_adi", "Tip", width=90),
            Col("devir", "Devir", "balance", 140), Col("borc", "Dönem Borç", "money", 118),
            Col("alacak", "Dönem Alacak", "money", 124), Col("bakiye", "Bakiye", "balance", 140, bold=True),
            Col("adet", "İşlem", "int", 60), Col("son_hareket", "Son Hareket", "date", 104)])
        self.mizan.rowActivated.connect(lambda r: self.open("cari", r.get("id")))
        c.add(self.mizan)
        L.addWidget(c)
        L.addStretch(1)
        self.p = None

    def load(self, p: Params) -> None:
        self.p = p
        an = self.an
        d = min(p.e, today())
        al = an.cari_aging(d, "alacak")
        bo = an.cari_aging(d, "borc")
        self.k_al.set(fmt_money(al["toplam"], True), f"{len(al['satirlar'])} cari · 90 günden eski "
                                                     f"{fmt_money(al['vadesi_gecmis_90'])}", C["gelir"])
        self.k_bo.set(fmt_money(bo["toplam"], True), f"{len(bo['satirlar'])} cari", C["gider"])
        k = an.kpis(p.s, p.e)
        self.k_tah.set(fmt_money(k["cari_tahsilat"], True),
                       f"Tahsilat oranı {fmt_percent(k['tahsilat_orani'], 0)}" if k["tahsilat_orani"] is not None
                       else "Dönemde veresiye yok")
        self.k_ver.set(fmt_money(k["cari_borc"], True), f"Cariye ödemeler: {fmt_money(k['cari_odeme'])} ₺")
        self.as_of.setText(f"   {fmt_date(d)} itibarıyla")
        self._ag = {"alacak": al, "borc": bo}
        self._aging()
        rows = an.cari_activity(p.s, p.e)
        if rows:
            rows.append({"unvan": f"TOPLAM ({len(rows)} cari)", "devir": sum(r["devir"] for r in rows),
                         "borc": sum(r["borc"] for r in rows), "alacak": sum(r["alacak"] for r in rows),
                         "bakiye": sum(r["bakiye"] for r in rows), "adet": sum(r["adet"] for r in rows),
                         "_bold": True, "_total": True, "id": None})
        self.mizan.set_rows(rows or [{"unvan": "Dönemde cari hareketi yok", "_muted": True}])
        fit_table_height(self.mizan, max(1, len(rows)), 16)

    def _aging(self) -> None:
        if not getattr(self, "_ag", None):
            return
        side = self.side.value()
        ag = self._ag[side]
        self.buckets.set_items([(lab, v, AGING_COLORS[i]) for i, (lab, v) in
                                enumerate(zip(ag["etiketler"], ag["dilim_toplam"]))])
        self.bucket_note.setText("Ödemeler önce en eski kalemleri kapatır (FIFO). 90 günü geçen tutar: "
                                 f"{fmt_money(ag['vadesi_gecmis_90'], True)}")
        self.top.set_items([(r["unvan"], r["bakiye"], C["gelir"] if side == "alacak" else C["gider"])
                            for r in ag["satirlar"][:10]])
        self.aging_t.model_.columns[-1].title = "Son Tahsilat" if side == "alacak" else "Son Ödeme"
        rows = []
        for r in ag["satirlar"]:
            d = dict(r)
            d.update({f"b{i}": v for i, v in enumerate(r["dilimler"])})
            rows.append(d)
        if rows:
            t = {"unvan": f"TOPLAM ({len(rows)} cari)", "bakiye": ag["toplam"], "_bold": True, "_total": True,
                 "id": None}
            t.update({f"b{i}": v for i, v in enumerate(ag["dilim_toplam"])})
            rows.append(t)
        self.aging_t.set_rows(rows or [{"unvan": "Açık bakiye yok", "_muted": True}])
        fit_table_height(self.aging_t, max(1, len(rows)), 14)


# =========================================================================== 6. Günler ve saatler
class ZamanTab(AnalizTab):
    name = "Günler ve Saatler"

    def __init__(self, page):
        super().__init__(page)
        L = self.lay
        c = Card("Takvim")
        self.metric_sel = Segmented([("gelir", "Gelir"), ("gider", "Gider"), ("net", "Net"), ("adet", "İşlem sayısı")])
        self.metric_sel.set_value("gelir")
        self.metric_sel.changed.connect(lambda _: self._calendar())
        c.header.addWidget(self.metric_sel)
        self.cal_note = muted("", small=True)
        c.add(self.cal_note)
        self.cal = CalendarHeatmap()
        c.add(self.cal)
        L.addWidget(c)
        row = QHBoxLayout()
        row.setSpacing(14)
        c1 = Card("Haftanın Günleri")
        c1.header.addWidget(muted("Faaliyet günü başına ortalama", small=True, wrap=False))
        self.week = TrendChart(height=250, net_tip=True)
        c1.add(self.week)
        c2 = Card("Saatlere Göre İşlem Sayısı")
        c2.header.addWidget(muted("Kaydın saatine göre", small=True, wrap=False))
        self.hours = TrendChart(height=250, money=False)
        c2.add(self.hours)
        row.addWidget(c1, 1)
        row.addWidget(c2, 1)
        L.addLayout(row)
        c = Card("Haftanın Günlerine Göre Tablo")
        self.week_t = DataTable([
            Col("ad", "Gün", stretch=True), Col("aktif_gun", "Faaliyet Günü", "int", 128),
            Col("gelir", "Toplam Gelir", "money", 125), Col("gider", "Toplam Gider", "money", 125),
            Col("ort_gelir", "Ort. Gelir / Gün", "money", 135, bold=True),
            Col("ort_gider", "Ort. Gider / Gün", "money", 135), Col("ort_net", "Ort. Net / Gün", "smoney", 125),
            Col("adet", "İşlem", "int", 70)], sortable=False)
        c.add(self.week_t)
        L.addWidget(c)
        row = QHBoxLayout()
        row.setSpacing(14)
        self.top_in = DataTable([Col("tarih", "Tarih", "date", 104), Col("gun", "Gün", width=96),
                                 Col("gelir", "Gelir", "money", 120, bold=True, color=lambda r: C["gelir"]),
                                 Col("gider", "Gider", "money", 110), Col("adet", "İşlem", "int", 60, stretch=True)],
                                sortable=False)
        self.top_out = DataTable([Col("tarih", "Tarih", "date", 104), Col("gun", "Gün", width=96),
                                  Col("gider", "Gider", "money", 120, bold=True, color=lambda r: C["gider"]),
                                  Col("gelir", "Gelir", "money", 110), Col("adet", "İşlem", "int", 60, stretch=True)],
                                 sortable=False)
        for title, t in (("En Yüksek Gelirli 10 Gün", self.top_in), ("En Yüksek Giderli 10 Gün", self.top_out)):
            cc = Card(title)
            cc.header.addWidget(muted("Çift tıklayınca o günün kasası açılır", small=True, wrap=False))
            t.rowActivated.connect(lambda r: self.open("gun", r.get("tarih")))
            cc.add(t)
            row.addWidget(cc, 1)
        L.addLayout(row)
        L.addStretch(1)
        self.p = None
        self.daily = {}

    def load(self, p: Params) -> None:
        self.p = p
        an = self.an
        self.daily = an.daily_totals(p.s, p.e)
        self._calendar()
        wk = an.weekday_stats(p.s, p.e, self.daily)
        self.week.set_data([TR_DAYS_SHORT[w["gun"]] for w in wk],
                           [{"name": "Ort. gelir", "color": C["gelir"], "values": [w["ort_gelir"] for w in wk]},
                            {"name": "Ort. gider", "color": C["gider"], "values": [w["ort_gider"] for w in wk]}],
                           [f"{w['ad']} ({w['aktif_gun']} faaliyet günü)" for w in wk])
        self.week_t.set_rows(wk, keep_selection_key=None)
        fit_table_height(self.week_t, 7)
        hs = an.hourly_stats(p.s, p.e)
        self.hours.set_data([h["etiket"][:2] for h in hs],
                            [{"name": "Gelir kaydı", "color": C["gelir"], "values": [h["gelir_adet"] for h in hs]},
                             {"name": "Gider kaydı", "color": C["gider"], "values": [h["gider_adet"] for h in hs]}],
                            [f"{h['etiket']} – {h['saat']:02d}:59" for h in hs])
        if not hs:
            self.hours.empty_text = "Saat bilgisi olan kayıt yok"
        for t, key in ((self.top_in, "gelir"), (self.top_out, "gider")):
            rows = an.top_days(self.daily, key, 10)
            t.set_rows(rows or [{"gun": "Kayıt yok", "_muted": True}], keep_selection_key=None)
            fit_table_height(t, max(1, len(rows)), 10)

    def _calendar(self) -> None:
        p = self.p
        if not p:
            return
        m = self.metric_sel.value()
        vals = {d: v[m] for d, v in self.daily.items() if v[m]}
        color = {"gelir": C["gelir"], "gider": C["gider"], "net": C["gelir"], "adet": C["primary"]}[m]
        label = {"gelir": "Gelir", "gider": "Gider", "net": "Net", "adet": "İşlem sayısı"}[m]
        self.cal.set_data(p.s, p.e, vals, color, diverging=(m == "net"), label=label, money=(m != "adet"))
        note = "Her kare bir gün; renk koyulaştıkça tutar artar. Üzerine gelince ayrıntı görünür."
        if self.cal.truncated(p.s):
            note += " Dönem uzun olduğu için son 53 hafta gösteriliyor."
        self.cal_note.setText(note)


# =========================================================================== 7. Yıllık karşılaştırma
class YillikTab(AnalizTab):
    name = "Yıllık Karşılaştırma"

    def __init__(self, page):
        super().__init__(page)
        L = self.lay
        top = QHBoxLayout()
        lab = QLabel("Yıl")
        lab.setStyleSheet("font-weight: 700;")
        top.addWidget(lab)
        self.year = combo([])
        self.year.setMinimumWidth(90)
        self.year.currentIndexChanged.connect(lambda _: self._load_year())
        top.addWidget(self.year)
        top.addSpacing(10)
        self.metric_sel = Segmented([("gelir", "Gelir"), ("gider", "Gider"), ("net", "Net")],
                                {"gelir": "GELIR", "gider": "GIDER"})
        self.metric_sel.set_value("gelir")
        self.metric_sel.changed.connect(lambda _: self._chart())
        top.addWidget(self.metric_sel)
        top.addSpacing(10)
        top.addWidget(muted("Bu sekme üstteki dönem seçiminden bağımsızdır; yıl seçin.", small=True, wrap=False))
        top.addStretch(1)
        L.addLayout(top)
        g = QHBoxLayout()
        g.setSpacing(14)
        self.k = {"gelir": TrendKpi("Yıllık Gelir", "in", C["gelir"], C["gelir_soft"], spark=False),
                  "gider": TrendKpi("Yıllık Gider", "out", C["gider"], C["gider_soft"], spark=False),
                  "net": TrendKpi("Yıllık Net", "scale", C["primary"], C["primary_soft"], spark=False)}
        for w in self.k.values():
            g.addWidget(w)
        L.addLayout(g)
        c = Card("Aylara Göre: Seçilen Yıl / Önceki Yıl")
        self.chart = TrendChart(height=300)
        c.add(self.chart)
        L.addWidget(c)
        c = Card("Aylık Tablo")
        c.header.addWidget(muted("Değişim: aynı ayın önceki yıla göre farkı", small=True, wrap=False))
        self.table = DataTable([
            Col("ad", "Ay", stretch=True), Col("gelir", "Gelir", "money", 115), Col("gider", "Gider", "money", 115),
            Col("net", "Net", "smoney", 115, bold=True), Col("kumulatif", "Kümülatif Net", "smoney", 125),
            Col("o_gelir", "Önceki Yıl Gelir", "money", 138), Col("o_gider", "Önceki Yıl Gider", "money", 138),
            Col("d_gelir", "Gelir Değ.", "smoney", 96, fmt=lambda v, r: fmt_delta_percent(v)),
            Col("d_gider", "Gider Değ.", "smoney", 96, fmt=lambda v, r: fmt_delta_percent(v))],
            sortable=False)
        c.add(self.table)
        L.addWidget(c)
        L.addStretch(1)
        self.mc = None

    def load(self, p: Params) -> None:
        years = self.an.years()
        cur = self.year.currentData()
        self.year.blockSignals(True)
        self.year.clear()
        for y in years:
            self.year.addItem(str(y), y)
        combo_select(self.year, cur if cur in years else (p.e.year if p.e.year in years else years[0]))
        self.year.blockSignals(False)
        self._load_year()

    def _load_year(self) -> None:
        y = self.year.currentData()
        if not y:
            return
        self.mc = mc = self.an.monthly_compare(y)
        cur, prev = mc["cur"], mc["prev"]
        ys, ye = mc["ytd"]
        sub_base = (f"{y - 1} aynı dönem ({fmt_date(ys)[:5]} – {fmt_date(ye)[:5]}): " if mc["kismi"]
                    else f"{y - 1}: ")
        eksik = mc["prev_eksik"]
        for key, good_up in (("gelir", True), ("gider", False), ("net", True)):
            ch = change(cur[key], prev[key]) if prev[key] and not eksik else None
            sub = sub_base + (fmt_money(prev[key], True) if (prev["gelir"] or prev["gider"]) else "kayıt yok")
            if eksik:
                sub += f" · kayıtlar {fmt_date(mc['ilk_kayit'])} tarihinde başlıyor, karşılaştırma eksik"
            self.k[key].set(fmt_money(cur[key], True), ch, good_up, sub,
                            value_color=C["gider"] if key == "net" and cur[key] < 0 else None)
        rows = []
        for r in mc["rows"]:
            d = dict(r)
            if r["gelecek"]:
                d.update(gelir=None, gider=None, net=None, kumulatif=None)
            if r["devam"]:
                d["ad"] = r["ad"] + " (devam ediyor)"
            rows.append(d)
        tg, tc = sum(r["gelir"] for r in mc["rows"]), sum(r["gider"] for r in mc["rows"])
        og, oc = sum(r["o_gelir"] for r in mc["rows"]), sum(r["o_gider"] for r in mc["rows"])
        same = not mc["kismi"] and not eksik          # yıl tamamlandıysa toplamlar karşılaştırılabilir
        rows.append({"ad": "TOPLAM", "gelir": tg, "gider": tc, "net": tg - tc, "o_gelir": og, "o_gider": oc,
                     "d_gelir": change(tg, og) if same else None, "d_gider": change(tc, oc) if same else None,
                     "_bold": True, "_total": True})
        self.table.set_rows(rows, keep_selection_key=None)
        fit_table_height(self.table, len(rows))
        self._chart()

    def _chart(self) -> None:
        mc = self.mc
        if not mc:
            return
        m = self.metric_sel.value()
        y = mc["year"]
        color = {"gelir": C["gelir"], "gider": C["gider"], "net": C["primary"]}[m]
        cur = [None if r["gelecek"] else r[m] for r in mc["rows"]]
        prev = [r["o_" + m] for r in mc["rows"]]
        self.chart.set_data([r["kisa"] for r in mc["rows"]],
                            [{"name": str(y), "color": color, "values": cur},
                             {"name": str(y - 1), "color": "#B8C2D1", "values": prev}],
                            [r["ad"] for r in mc["rows"]])


# =========================================================================== sayfa
class AnalizPage(Page):
    title = "Analiz"
    subtitle = "Göstergeler, grafikler ve tablolarla geçmişe dönük analiz · karşılaştırmalı"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.an = Analytics(self.svc)
        self.p: Params | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        fc = Card(margins=(14, 10, 14, 8), spacing=4)
        r = QHBoxLayout()
        r.setSpacing(8)
        lab = QLabel("Dönem")
        lab.setStyleSheet("font-weight: 700;")
        r.addWidget(lab)
        self.period = PeriodSelector("Son 3 Ay")
        r.addWidget(self.period)
        r.addSpacing(6)
        r.addWidget(muted("Gruplama", wrap=False))
        self.group = combo(GROUPS)
        r.addWidget(self.group)
        r.addSpacing(6)
        r.addWidget(muted("Karşılaştır", wrap=False))
        self.compare = combo(COMPARE_MODES)
        r.addWidget(self.compare)
        r.addStretch(1)
        fc.add(r)
        self.info = muted("", small=True)
        fc.add(self.info)
        root.addWidget(fc)
        self.tabbar = QTabBar()
        self.tabbar.setDrawBase(False)
        self.tabbar.setExpanding(False)
        self.tabbar.setUsesScrollButtons(True)
        self.tabbar.setCursor(Qt.PointingHandCursor)
        tr = QHBoxLayout()
        tr.setSpacing(8)
        tr.addWidget(self.tabbar, 1)
        tr.addWidget(button("PDF Rapor", "pdf", "primary", "Seçili dönemin kapsamlı analiz raporu (PDF)", self._pdf))
        tr.addWidget(button("Excel", "excel", tip="Analiz tablolarını Excel'e aktar", callback=self._excel))
        root.addLayout(tr)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.tabs = [OzetTab(self), TrendTab(self), KategoriTab(self), KasaTab(self), CariTab(self), ZamanTab(self),
                     YillikTab(self)]
        for t in self.tabs:
            self.tabbar.addTab(t.name)
            self.stack.addWidget(t)
        self.tabbar.currentChanged.connect(self._tab_changed)
        self.period.changed.connect(self.safe_refresh)
        self.group.currentIndexChanged.connect(self.safe_refresh)
        self.compare.currentIndexChanged.connect(self.safe_refresh)

    def _tab_changed(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        self._load_current()

    def refresh(self) -> None:
        s, e = self.an.effective_range(*self.period.range())
        mode = self.compare.currentData()
        ps, pe = self.an.compare_range(s, e, mode)
        self.p = Params(s, e, ps, pe, self.group.currentData(), mode)
        txt = f"Analiz edilen dönem: <b>{period_text(s, e)}</b> ({self.p.days} gün)"
        txt += f"   ·   Karşılaştırma: {period_text(ps, pe)}" if ps else "   ·   Karşılaştırma yok"
        if self.period.range()[0] < s:
            txt += "   ·   <i>dönem, ilk kayıt tarihinden başlatıldı</i>"
        self.info.setText(txt)
        for t in self.tabs:
            t.dirty = True
        self._load_current()

    def _load_current(self) -> None:
        t = self.tabs[self.stack.currentIndex()]
        if t.dirty and self.p:
            t.dirty = False
            try:
                t.load(self.p)
            except Exception as ex:  # noqa: BLE001
                log.exception("Analiz sekmesi yüklenemedi: %s", t.name)
                self.ctx.status(f"Analiz hesaplanırken hata: {ex}")

    def current_tab(self) -> AnalizTab:
        return self.tabs[self.stack.currentIndex()]

    def show_tab(self, name: str) -> None:
        for i, t in enumerate(self.tabs):
            if t.name == name:
                self.tabbar.setCurrentIndex(i)

    def _values(self) -> dict:
        p = self.p
        return {"donem": (p.s, p.e), "grup": p.group, "karsilastirma": p.mode}

    def _pdf(self) -> None:
        if self.p:
            report_pdf(self.ctx, self, "donem_analiz", self._values())

    def _excel(self) -> None:
        if not self.p:
            return
        try:
            rep = reports.build("donem_analiz", self.svc, self._values())
        except Exception as e:  # noqa: BLE001
            log.exception("Analiz Excel")
            self.ctx.status(f"Excel hazırlanamadı: {e}")
            return
        export_report_excel(self.ctx, self, rep)

