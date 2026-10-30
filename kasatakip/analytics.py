"""Analiz hesapları (arayüzden bağımsız).

Göstergeler, dönem karşılaştırması, kategori/ödeme tipi dağılımları, kasa bakiye seyri, gün kapanışı ve
sayım farkları, cari yaşlandırma (FIFO), cari mizan, gün/saat desenleri, yıllık karşılaştırma ve
otomatik yorumlar. Analiz ekranı ve Raporlar aynı hesapları kullanır.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from .services import ACILIS_ACIKLAMA
from .util import (TR_DAYS, TR_MONTHS, TR_MONTHS_SHORT, auto_group, fmt_date, fmt_money, fmt_percent, fold, group_key,
                   group_label, group_title, iso, iter_group_keys, previous_period, to_date, today)

GELIR, GIDER, VIRMAN, CARI_BORC, CARI_ALACAK = "GELIR", "GIDER", "VIRMAN", "CARI_BORC", "CARI_ALACAK"
CARI_TIP_ADI = {"MUSTERI": "Müşteri", "TEDARIKCI": "Tedarikçi", "HER_IKISI": "Müşteri + Tedarikçi"}

COMPARE_MODES = [("Önceki eşit dönem", "prev"), ("Geçen yılın aynı dönemi", "yoy"), ("Karşılaştırma yok", None)]
GROUPS = [("Otomatik", None), ("Günlük", "day"), ("Haftalık", "week"), ("Aylık", "month")]
GROUP_NAMES = {"day": "Günlük", "week": "Haftalık", "month": "Aylık"}

AGING_BUCKETS = [(0, 30, "0–30 gün"), (31, 60, "31–60 gün"), (61, 90, "61–90 gün"), (91, 180, "91–180 gün"),
                 (181, None, "180+ gün")]
_DEBIT = (CARI_BORC, GIDER)        # carinin borcuna işlenenler
_CREDIT = (CARI_ALACAK, GELIR)     # carinin alacağına işlenenler


def shift_year(d: dt.date, n: int) -> dt.date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:                      # 29 Şubat
        return d.replace(year=d.year + n, day=28)


def ratio(a, b):
    return (a / b) if b else None


def change(a, b):
    return ((a - b) / abs(b)) if b else None


def bucket_text(key: str, group: str, s: dt.date, e: dt.date) -> tuple[str, str]:
    """Grafik etiketi ve uzun başlık. Haftalık gruplarda ilk/son hafta dönem sınırına göre kısaltılır."""
    if group != "week":
        return group_label(key, group), group_title(key, group)
    ws = to_date(key)
    a, b = max(ws, s), min(ws + dt.timedelta(days=6), e)
    return a.strftime("%d.%m"), f"{fmt_date(a)} – {fmt_date(b)}"


def _bucket_index(age_days: int) -> int:
    for i, (lo, hi, _) in enumerate(AGING_BUCKETS):
        if hi is None or age_days <= hi:
            return i
    return len(AGING_BUCKETS) - 1


class Analytics:
    def __init__(self, svc):
        self.svc = svc
        self.db = svc.db

    # ================================================================ dönem
    def effective_range(self, start, end) -> tuple[dt.date, dt.date]:
        """Seçilen dönemi verinin bulunduğu aralığa daraltır ('Tüm Zamanlar' 2000 yılından başlamasın)."""
        s, e = to_date(start), to_date(end)
        first, last = self.svc.data_range()
        upper = max(today(), last) if last else today()
        if e > upper:
            e = upper
        if first is None:
            s = max(s, e)
        elif s < first:
            s = first
        if s > e:
            s = e
        return s, e

    @staticmethod
    def compare_range(s: dt.date, e: dt.date, mode: str | None):
        if mode == "prev":
            return previous_period(s, e)
        if mode == "yoy":
            return shift_year(s, -1), shift_year(e, -1)
        return None, None

    # ================================================================ göstergeler
    def kpis(self, s, e) -> dict:
        """Dönem göstergeleri. Karşılaştırma için aynı fonksiyon önceki dönemle de çağrılır."""
        d = self.svc.summary(s, e)
        r = self.db.q1("""
            SELECT COALESCE(SUM(CASE WHEN tur='GELIR' AND cari_id IS NOT NULL THEN tutar END), 0) AS cari_tahsilat,
                   COALESCE(SUM(CASE WHEN tur='GIDER' AND cari_id IS NOT NULL THEN tutar END), 0) AS cari_odeme,
                   COALESCE(SUM(CASE WHEN tur='CARI_BORC' AND aciklama = ? THEN tutar END), 0) AS acilis_borc
            FROM hareketler WHERE tarih BETWEEN ? AND ?""", (ACILIS_ACIKLAMA, iso(s), iso(e)))
        d.update(dict(r))
        # cari açılış bakiyesi fişleri dönemin veresiyesi değildir
        d["cari_borc"] = max(0, d["cari_borc"] - d["acilis_borc"])
        d["marj"] = ratio(d["net"], d["gelir"])
        d["gider_orani"] = ratio(d["gider"], d["gelir"])
        d["tahsilat_orani"] = ratio(d["cari_tahsilat"], d["cari_borc"])
        return d

    # ================================================================ zaman serisi
    def series(self, s, e, group: str | None = None):
        """(grup, satırlar): her dönem için gelir, gider, net, kümülatif net ve işlem sayıları."""
        s, e = to_date(s), to_date(e)
        group = group or auto_group(s, e)
        data: dict = {}
        for r in self.db.q("SELECT tarih, tur, SUM(tutar) AS t, COUNT(*) AS n FROM hareketler "
                           "WHERE tur IN ('GELIR','GIDER') AND tarih BETWEEN ? AND ? GROUP BY tarih, tur",
                           (s.isoformat(), e.isoformat())):
            slot = data.setdefault(group_key(to_date(r["tarih"]), group), [0, 0, 0, 0])
            if r["tur"] == GELIR:
                slot[0] += r["t"]
                slot[2] += r["n"]
            else:
                slot[1] += r["t"]
                slot[3] += r["n"]
        rows, cum = [], 0
        for k in iter_group_keys(s, e, group):
            g, c, ga, ca = data.get(k, (0, 0, 0, 0))
            cum += g - c
            etiket, baslik = bucket_text(k, group, s, e)
            rows.append({"key": k, "etiket": etiket, "baslik": baslik,
                         "gelir": g, "gider": c, "net": g - c, "kumulatif": cum,
                         "gelir_adet": ga, "gider_adet": ca, "adet": ga + ca,
                         "marj": ratio(g - c, g)})
        return group, rows

    # ================================================================ kategoriler
    def category_compare(self, s, e, ps, pe, tur: str) -> list[dict]:
        cur = {r["id"]: r for r in self.svc.category_totals(s, e, tur)}
        prev = {r["id"]: r for r in self.svc.category_totals(ps, pe, tur)} if ps else {}
        total = sum(r["toplam"] for r in cur.values())
        out = []
        for cid in list(cur) + [k for k in prev if k not in cur]:
            c, p = cur.get(cid), prev.get(cid)
            toplam = c["toplam"] if c else 0
            adet = c["adet"] if c else 0
            onceki = p["toplam"] if p else 0
            out.append({"id": cid, "ad": (c or p)["ad"], "adet": adet, "toplam": toplam,
                        "pay": ratio(toplam, total) or 0.0, "ort": round(toplam / adet) if adet else 0,
                        "onceki": onceki if ps else None, "fark": (toplam - onceki) if ps else None,
                        "degisim": change(toplam, onceki) if ps else None})
        out.sort(key=lambda r: (r["toplam"], r["onceki"] or 0), reverse=True)
        return out

    def category_pivot(self, s, e, tur: str, group: str | None = None) -> dict:
        """Kategori × dönem tablosu."""
        s, e = to_date(s), to_date(e)
        group = group or auto_group(s, e)
        keys = list(iter_group_keys(s, e, group))
        idx = {k: i for i, k in enumerate(keys)}
        names = {c["id"]: c["ad"] for c in self.svc.categories(tur)}
        vals: dict = defaultdict(lambda: [0] * len(keys))
        for r in self.db.q("SELECT kategori_id, tarih, SUM(tutar) AS t FROM hareketler WHERE tur=? AND "
                           "tarih BETWEEN ? AND ? GROUP BY kategori_id, tarih", (tur, s.isoformat(), e.isoformat())):
            i = idx.get(group_key(to_date(r["tarih"]), group))
            if i is not None:
                vals[r["kategori_id"]][i] += r["t"]
        rows = [{"id": cid, "ad": names.get(cid, "?"), "values": v, "toplam": sum(v)} for cid, v in vals.items()]
        rows.sort(key=lambda r: r["toplam"], reverse=True)
        texts = [bucket_text(k, group, s, e) for k in keys]
        return {"group": group, "keys": keys, "labels": [t[0] for t in texts],
                "titles": [t[1] for t in texts], "rows": rows,
                "totals": [sum(r["values"][i] for r in rows) for i in range(len(keys))]}

    def category_stack(self, s, e, tur: str, group: str | None = None, top: int = 6) -> dict:
        """Yığılmış grafik için: en büyük 'top' kategori + Diğer."""
        pv = self.category_pivot(s, e, tur, group)
        rows = pv["rows"]
        main, rest = rows[:top], rows[top:]
        series = [(r["ad"], r["values"]) for r in main]
        if rest:
            series.append(("Diğer (toplu)", [sum(r["values"][i] for r in rest) for i in range(len(pv["keys"]))]))
        return {**pv, "series": series}

    # ================================================================ ödeme tipleri / kasa
    def payment_table(self, s, e) -> list[dict]:
        before = self.svc.balances_before(s)
        after = self.svc.balances_at(e)
        flows = {r["id"]: r for r in self.svc.payment_totals(s, e)}
        out = []
        for pt in self.svc.payment_types(include_inactive=True):
            f = flows.get(pt["id"]) or {"gelir": 0, "gider": 0, "virman_giris": 0, "virman_cikis": 0}
            dv, son = before.get(pt["id"], 0), after.get(pt["id"], 0)
            if not pt["aktif"] and not dv and not son and not any(f[k] for k in ("gelir", "gider", "virman_giris",
                                                                                   "virman_cikis")):
                continue
            out.append({"id": pt["id"], "ad": pt["ad"], "devir": dv, "gelir": f["gelir"], "gider": f["gider"],
                        "virman_giris": f["virman_giris"], "virman_cikis": f["virman_cikis"],
                        "virman_net": f["virman_giris"] - f["virman_cikis"], "son": son, "degisim": son - dv})
        return out

    def balance_history(self, s, e) -> dict:
        """Ödeme tiplerinin gün sonu bakiye seyri. Uzun dönemlerde hafta/ay sonu bakiyeleri alınır."""
        s, e = to_date(s), to_date(e)
        days = (e - s).days + 1
        group = "day" if days <= 120 else ("week" if days <= 730 else "month")
        pts = [p for p in self.svc.payment_types(include_inactive=True)]
        bal = dict(self.svc.balances_before(s))
        deltas: dict = defaultdict(dict)
        for r in self.db.q("""
                SELECT tarih, pt, SUM(d) AS d FROM (
                    SELECT tarih, odeme_tipi_id AS pt, CASE WHEN tur='GELIR' THEN tutar ELSE -tutar END AS d
                    FROM hareketler WHERE tur IN ('GELIR','GIDER','VIRMAN') AND tarih BETWEEN ? AND ?
                    UNION ALL
                    SELECT tarih, hedef_odeme_tipi_id, tutar FROM hareketler
                    WHERE tur='VIRMAN' AND tarih BETWEEN ? AND ?)
                GROUP BY tarih, pt""", (s.isoformat(), e.isoformat()) * 2):
            deltas[r["tarih"]][r["pt"]] = r["d"]
        samples: list[tuple[dt.date, dict]] = []
        d = s
        while d <= e:
            for pid, v in deltas.get(d.isoformat(), {}).items():
                bal[pid] = bal.get(pid, 0) + v
            nxt = d + dt.timedelta(days=1)
            if group == "day" or nxt > e or group_key(nxt, group) != group_key(d, group):
                samples.append((d, dict(bal)))
            d = nxt
        series = []
        for p in pts:
            vals = [b.get(p["id"], 0) for _, b in samples]
            if not p["aktif"] and not any(vals):
                continue
            series.append({"id": p["id"], "ad": p["ad"], "values": vals})
        labels = [x.strftime("%d.%m") if group != "month" else f"{TR_MONTHS_SHORT[x.month - 1]} {str(x.year)[2:]}"
                  for x, _ in samples]
        titles = [f"{fmt_date(x, weekday=True)} gün sonu" for x, _ in samples]
        total = [sum(sr["values"][i] for sr in series) for i in range(len(samples))]
        return {"group": group, "dates": [x for x, _ in samples], "labels": labels, "titles": titles,
                "series": series, "total": total}

    def payment_channels(self, s, e) -> dict:
        rows = self.svc.payment_totals(s, e)
        return {"gelir": [(r["ad"], r["gelir"]) for r in rows if r["gelir"]],
                "gider": [(r["ad"], r["gider"]) for r in rows if r["gider"]]}

    # ================================================================ kapanış ve sayım
    def closing_status(self, s, e) -> dict:
        """Kapanması gereken günler (kasa hareketi olan, bugünden önceki günler) ve durumları."""
        s, e = to_date(s), to_date(e)
        upto = min(e, today() - dt.timedelta(days=1))
        active = [r["tarih"] for r in self.db.q(
            "SELECT DISTINCT tarih FROM hareketler WHERE tur IN ('GELIR','GIDER','VIRMAN') AND tarih BETWEEN ? AND ? "
            "ORDER BY tarih", (s.isoformat(), upto.isoformat()))] if upto >= s else []
        closed = {r["tarih"] for r in self.db.q("SELECT tarih FROM gun_kapanis WHERE tarih BETWEEN ? AND ?",
                                                 (s.isoformat(), e.isoformat()))}
        lock = self.svc.lock_date() or ""
        kapali = [d for d in active if d in closed]
        kilitli = [d for d in active if d not in closed and d <= lock]      # sonraki gün kapatılınca kilitlenen,
        acik = [d for d in active if d > lock]                              # ama kendisi kapatılmamış (sayımsız)
        return {"gerekli": len(active), "kapali": len(kapali), "kilitli": len(kilitli), "kilitli_gunler": kilitli,
                "acik": acik, "kapanan_toplam": len(closed), "oran": ratio(len(kapali), len(active))}

    def closing_rows(self, s, e) -> list[dict]:
        """Kapatılan günler: gün toplamları, kasa (sistem) toplamı, sayım farkı, kapatan."""
        daily = self.daily_totals(s, e)
        rows = []
        for r in self.db.q("""
                SELECT g.tarih, g.kapanis_zamani, g.aciklama, u.kullanici_adi AS kapatan,
                       (SELECT COALESCE(SUM(sistem_bakiye), 0) FROM gun_kapanis_detay d WHERE d.tarih = g.tarih)
                           AS sistem,
                       (SELECT SUM(sayilan - sistem_bakiye) FROM gun_kapanis_detay d
                        WHERE d.tarih = g.tarih AND sayilan IS NOT NULL) AS fark,
                       (SELECT COUNT(*) FROM gun_kapanis_detay d WHERE d.tarih = g.tarih AND sayilan IS NOT NULL)
                           AS sayim
                FROM gun_kapanis g LEFT JOIN kullanicilar u ON u.id = g.kapatan_id
                WHERE g.tarih BETWEEN ? AND ? ORDER BY g.tarih""", (iso(s), iso(e))):
            d = dict(r)
            t = daily.get(d["tarih"], {})
            d.update({"gelir": t.get("gelir", 0), "gider": t.get("gider", 0), "net": t.get("net", 0),
                      "gun": TR_DAYS[to_date(d["tarih"]).weekday()]})
            rows.append(d)
        return rows

    def count_differences(self, s, e) -> dict:
        rows = [dict(r) for r in self.db.q("""
            SELECT d.tarih, d.odeme_tipi_ad AS odeme, d.sistem_bakiye AS sistem, d.sayilan,
                   d.sayilan - d.sistem_bakiye AS fark, g.aciklama, u.kullanici_adi AS kapatan
            FROM gun_kapanis_detay d JOIN gun_kapanis g ON g.tarih = d.tarih
            LEFT JOIN kullanicilar u ON u.id = g.kapatan_id
            WHERE d.tarih BETWEEN ? AND ? AND d.sayilan IS NOT NULL
            ORDER BY d.tarih DESC, d.odeme_tipi_id""", (iso(s), iso(e)))]
        sayim_gun = {r["tarih"] for r in rows}
        farkli = [r for r in rows if r["fark"]]
        farkli_gun = {r["tarih"] for r in farkli}
        return {"satirlar": farkli, "tum": rows, "sayim_gun": len(sayim_gun), "farkli_gun": len(farkli_gun),
                "fazla": sum(r["fark"] for r in farkli if r["fark"] > 0),
                "eksik": sum(r["fark"] for r in farkli if r["fark"] < 0),
                "net": sum(r["fark"] for r in farkli),
                "dogruluk": ratio(len(sayim_gun) - len(farkli_gun), len(sayim_gun))}

    # ================================================================ cariler
    def _cari_info(self) -> dict:
        return {c["id"]: c for c in self.svc.caris(include_inactive=True)}

    def cari_aging(self, as_of=None, side: str = "alacak", tip: str | None = None) -> dict:
        """Yaşlandırma (FIFO): ödemeler önce en eski borcu kapatır; açık kalan tutar en yeni borç
        kalemlerinden oluşur ve kalemlerin yaşına göre dilimlere ayrılır.
        side='alacak': bize borçlu cariler (alacaklarımız) · side='borc': borçlu olduğumuz cariler."""
        as_of = to_date(as_of) or today()
        info = self._cari_info()
        moves: dict = defaultdict(list)
        for r in self.db.q("SELECT cari_id, tarih, tur, tutar FROM hareketler WHERE cari_id IS NOT NULL AND tarih <= ? "
                           "ORDER BY cari_id, tarih, id", (as_of.isoformat(),)):
            moves[r["cari_id"]].append((r["tarih"], r["tur"], r["tutar"]))
        out = []
        totals = [0] * len(AGING_BUCKETS)
        for cid, ms in moves.items():
            c = info.get(cid)
            if c is None:
                continue
            if tip == "MUSTERI" and c["tip"] not in ("MUSTERI", "HER_IKISI"):
                continue
            if tip == "TEDARIKCI" and c["tip"] not in ("TEDARIKCI", "HER_IKISI"):
                continue
            borc = sum(t for _, tur, t in ms if tur in _DEBIT)
            alacak = sum(t for _, tur, t in ms if tur in _CREDIT)
            bakiye = borc - alacak
            if side == "alacak" and bakiye <= 0 or side == "borc" and bakiye >= 0:
                continue
            open_side = _DEBIT if side == "alacak" else _CREDIT
            pay_side = _CREDIT if side == "alacak" else _DEBIT
            kalan = abs(bakiye)
            buckets = [0] * len(AGING_BUCKETS)
            oldest = None
            for tarih, tur, t in reversed(ms):
                if kalan <= 0:
                    break
                if tur not in open_side:
                    continue
                part = min(kalan, t)
                kalan -= part
                age = (as_of - to_date(tarih)).days
                buckets[_bucket_index(max(0, age))] += part
                oldest = tarih
            if kalan > 0:                       # kayıt dışı devir (olmamalı) -> en eski dilim
                buckets[-1] += kalan
            for i, v in enumerate(buckets):
                totals[i] += v
            last_pay = max((tarih for tarih, tur, _ in ms if tur in pay_side), default=None)
            out.append({"id": cid, "kod": c["kod"], "unvan": c["unvan"], "tip_adi": c["tip_adi"],
                        "telefon": c.get("telefon") or "", "bakiye": abs(bakiye), "dilimler": buckets,
                        "en_eski": oldest, "gecikme": (as_of - to_date(oldest)).days if oldest else 0,
                        "son_odeme": last_pay, "aktif": c["aktif"]})
        out.sort(key=lambda r: r["bakiye"], reverse=True)
        total = sum(totals)
        return {"as_of": as_of, "side": side, "satirlar": out, "dilim_toplam": totals, "toplam": total,
                "dilim_pay": [ratio(v, total) or 0.0 for v in totals], "etiketler": [b[2] for b in AGING_BUCKETS],
                "vadesi_gecmis_90": sum(totals[3:])}

    def cari_activity(self, s, e, tip: str | None = None, include_empty: bool = False) -> list[dict]:
        """Cari mizan: dönem başı devir, dönem borç/alacak ve dönem sonu bakiye."""
        s, e = iso(s), iso(e)
        rows = self.db.q("""
            SELECT c.id, c.kod, c.unvan, c.tip, c.aktif, c.telefon,
              COALESCE(SUM(CASE WHEN h.tarih < ? AND h.tur IN ('CARI_BORC','GIDER') THEN h.tutar END), 0) AS d_borc,
              COALESCE(SUM(CASE WHEN h.tarih < ? AND h.tur IN ('CARI_ALACAK','GELIR') THEN h.tutar END), 0) AS d_alacak,
              COALESCE(SUM(CASE WHEN h.tarih >= ? AND h.tur IN ('CARI_BORC','GIDER') THEN h.tutar END), 0) AS borc,
              COALESCE(SUM(CASE WHEN h.tarih >= ? AND h.tur IN ('CARI_ALACAK','GELIR') THEN h.tutar END), 0) AS alacak,
              COALESCE(SUM(CASE WHEN h.tarih >= ? AND h.tur='CARI_BORC' AND h.aciklama <> ? THEN h.tutar END), 0)
                  AS veresiye,
              COALESCE(SUM(CASE WHEN h.tarih >= ? AND h.tur='GELIR' THEN h.tutar END), 0) AS tahsilat,
              COALESCE(SUM(CASE WHEN h.tarih >= ? AND h.tur='CARI_ALACAK' THEN h.tutar END), 0) AS fatura,
              COALESCE(SUM(CASE WHEN h.tarih >= ? AND h.tur='GIDER' THEN h.tutar END), 0) AS odeme,
              COUNT(CASE WHEN h.tarih >= ? THEN 1 END) AS adet,
              MAX(h.tarih) AS son_hareket
            FROM cariler c LEFT JOIN hareketler h ON h.cari_id = c.id AND h.tarih <= ?
            GROUP BY c.id""", (s, s, s, s, s, ACILIS_ACIKLAMA, s, s, s, s, e))
        out = []
        for r in rows:
            d = dict(r)
            if tip == "MUSTERI" and d["tip"] not in ("MUSTERI", "HER_IKISI"):
                continue
            if tip == "TEDARIKCI" and d["tip"] not in ("TEDARIKCI", "HER_IKISI"):
                continue
            d["devir"] = d["d_borc"] - d["d_alacak"]
            d["bakiye"] = d["devir"] + d["borc"] - d["alacak"]
            d["tip_adi"] = CARI_TIP_ADI.get(d["tip"], d["tip"])
            if not include_empty and not d["adet"] and not d["devir"]:
                continue
            out.append(d)
        out.sort(key=lambda r: fold(r["unvan"]))
        return out

    def cari_balances(self, as_of, tip: str | None = None, only_balance: bool = False) -> list[dict]:
        """Belirli bir tarih itibarıyla cari bakiyeleri."""
        d = to_date(as_of)
        rows = self.cari_activity(d, d, tip=tip, include_empty=True)
        out = []
        for r in rows:
            r["borc_toplam"] = r["d_borc"] + r["borc"]
            r["alacak_toplam"] = r["d_alacak"] + r["alacak"]
            if only_balance and not r["bakiye"]:
                continue
            if not r["aktif"] and not r["bakiye"] and not r["borc_toplam"]:
                continue
            out.append(r)
        return out

    # ================================================================ gün / saat desenleri
    def daily_totals(self, s, e) -> dict:
        out = {}
        for r in self.db.q("""
                SELECT tarih, SUM(CASE WHEN tur='GELIR' THEN tutar ELSE 0 END) AS gelir,
                       SUM(CASE WHEN tur='GIDER' THEN tutar ELSE 0 END) AS gider, COUNT(*) AS adet
                FROM hareketler WHERE tur IN ('GELIR','GIDER') AND tarih BETWEEN ? AND ? GROUP BY tarih""",
                           (iso(s), iso(e))):
            out[r["tarih"]] = {"tarih": r["tarih"], "gelir": r["gelir"], "gider": r["gider"],
                               "net": r["gelir"] - r["gider"], "adet": r["adet"]}
        return out

    def weekday_stats(self, s, e, daily: dict | None = None) -> list[dict]:
        daily = daily if daily is not None else self.daily_totals(s, e)
        acc = [{"gun": i, "ad": TR_DAYS[i], "aktif_gun": 0, "gelir": 0, "gider": 0, "adet": 0} for i in range(7)]
        for d in daily.values():
            w = acc[to_date(d["tarih"]).weekday()]
            w["aktif_gun"] += 1
            w["gelir"] += d["gelir"]
            w["gider"] += d["gider"]
            w["adet"] += d["adet"]
        for w in acc:
            n = w["aktif_gun"]
            w["ort_gelir"] = round(w["gelir"] / n) if n else 0
            w["ort_gider"] = round(w["gider"] / n) if n else 0
            w["ort_net"] = w["ort_gelir"] - w["ort_gider"]
        return acc

    def hourly_stats(self, s, e) -> list[dict]:
        acc = {h: {"saat": h, "gelir": 0, "gider": 0, "gelir_adet": 0, "gider_adet": 0} for h in range(24)}
        for r in self.db.q("""
                SELECT CAST(substr(saat, 1, 2) AS INTEGER) AS h, tur, COUNT(*) AS n, SUM(tutar) AS t
                FROM hareketler WHERE tur IN ('GELIR','GIDER') AND tarih BETWEEN ? AND ? AND length(saat) >= 4
                GROUP BY h, tur""", (iso(s), iso(e))):
            if r["h"] is None or not 0 <= r["h"] <= 23:
                continue
            a = acc[r["h"]]
            if r["tur"] == GELIR:
                a["gelir"] += r["t"]
                a["gelir_adet"] += r["n"]
            else:
                a["gider"] += r["t"]
                a["gider_adet"] += r["n"]
        used = [h for h, a in acc.items() if a["gelir_adet"] or a["gider_adet"]]
        if not used:
            return []
        lo, hi = min(min(used), 8), max(max(used), 18)
        return [{**acc[h], "etiket": f"{h:02d}:00", "adet": acc[h]["gelir_adet"] + acc[h]["gider_adet"]}
                for h in range(lo, hi + 1)]

    @staticmethod
    def top_days(daily: dict, key: str = "gelir", n: int = 10) -> list[dict]:
        rows = [d for d in daily.values() if d[key]]
        rows.sort(key=lambda d: d[key], reverse=True)
        return [{**d, "gun": TR_DAYS[to_date(d["tarih"]).weekday()]} for d in rows[:n]]

    # ================================================================ yıllık
    def first_kasa_date(self):
        """İlk gelir/gider kaydının tarihi (cari açılış fişleri gibi geriye tarihli fişler hariç)."""
        return to_date(self.db.scalar("SELECT MIN(tarih) FROM hareketler WHERE tur IN ('GELIR','GIDER')"))

    def years(self) -> list[int]:
        ys = [int(r[0]) for r in self.db.q("SELECT DISTINCT substr(tarih, 1, 4) FROM hareketler "
                                            "WHERE tur IN ('GELIR','GIDER') ORDER BY 1 DESC") if r[0]]
        if today().year not in ys:
            ys.insert(0, today().year)
        return sorted(set(ys), reverse=True)

    def monthly_compare(self, year: int) -> dict:
        m = {}
        for r in self.db.q("""
                SELECT substr(tarih, 1, 7) AS ym, SUM(CASE WHEN tur='GELIR' THEN tutar ELSE 0 END) AS g,
                       SUM(CASE WHEN tur='GIDER' THEN tutar ELSE 0 END) AS c, COUNT(*) AS n
                FROM hareketler WHERE tur IN ('GELIR','GIDER') AND tarih BETWEEN ? AND ? GROUP BY ym""",
                           (f"{year - 1}-01-01", f"{year}-12-31")):
            m[r["ym"]] = (r["g"], r["c"], r["n"])
        t = today()
        rows = []
        cum = 0
        for i in range(1, 13):
            g, c, n = m.get(f"{year}-{i:02d}", (0, 0, 0))
            og, oc, _ = m.get(f"{year - 1}-{i:02d}", (0, 0, 0))
            cum += g - c
            future = (year, i) > (t.year, t.month)
            rows.append({"ay": i, "ad": TR_MONTHS[i - 1], "kisa": TR_MONTHS_SHORT[i - 1], "gelir": g, "gider": c,
                         "net": g - c, "adet": n, "kumulatif": cum, "o_gelir": og, "o_gider": oc, "o_net": og - oc,
                         "d_gelir": None if future else change(g, og), "d_gider": None if future else change(c, oc),
                         "devam": year == t.year and i == t.month, "gelecek": future})
        # yılbaşından bugüne karşılaştırma (yıl bitmediyse geçen yılın aynı günlerine göre)
        if year == t.year:
            ytd_s, ytd_e = dt.date(year, 1, 1), t
        else:
            ytd_s, ytd_e = dt.date(year, 1, 1), dt.date(year, 12, 31)
        cur = self.svc.summary(ytd_s, ytd_e)
        prev = self.svc.summary(shift_year(ytd_s, -1), shift_year(ytd_e, -1))
        first = self.first_kasa_date()
        # önceki yılın verisi karşılaştırma döneminin başından sonra başlıyorsa karşılaştırma eksiktir
        prev_eksik = bool(first and prev["adet"] and first > shift_year(ytd_s, -1))
        return {"year": year, "rows": rows, "ytd": (ytd_s, ytd_e), "cur": cur, "prev": prev,
                "kismi": year == t.year, "prev_eksik": prev_eksik, "ilk_kayit": first}

    # ================================================================ yorumlar
    def insights(self, s, e, ps=None, pe=None, cur: dict | None = None, prev: dict | None = None) -> list[dict]:
        """Dönem için kısa, otomatik yorumlar. tone: good | bad | warn | info"""
        out: list[dict] = []

        def add(tone, text):
            out.append({"tone": tone, "text": text})

        cur = cur or self.kpis(s, e)
        if not cur["adet"]:
            add("info", "Seçilen dönemde gelir veya gider kaydı yok.")
        else:
            net = cur["net"]
            txt = f"Dönem net sonucu {fmt_money(net, True, plus=True)}"
            if cur["marj"] is not None:
                txt += f" (kâr marjı {fmt_percent(cur['marj'])})"
            add("good" if net >= 0 else "bad", txt + ".")
            if ps and prev is None:
                prev = self.kpis(ps, pe)
            if prev and prev["adet"]:
                for key, label, good_up in (("gelir", "Gelir", True), ("gider", "Gider", False)):
                    ch = change(cur[key], prev[key])
                    if ch is None:
                        continue
                    if abs(ch) < 0.005:
                        add("info", f"{label} karşılaştırma dönemiyle aynı seviyede.")
                        continue
                    up = ch > 0
                    fark = cur[key] - prev[key]
                    add("good" if up == good_up else "bad",
                        f"{label} karşılaştırma dönemine göre {fmt_percent(abs(ch))} "
                        f"{'arttı' if up else 'azaldı'} ({fmt_money(fark, True, plus=True)}).")
            giders = self.svc.category_totals(s, e, GIDER)
            if giders:
                g0 = giders[0]
                add("info", f"En büyük gider kalemi: {g0['ad']} — {fmt_money(g0['toplam'], True)} "
                            f"(gider payı {fmt_percent(g0['pay'])}).")
            if ps:
                cc = self.category_compare(s, e, ps, pe, GIDER)
                rising = [r for r in cc if r["onceki"] and r["degisim"] is not None and r["degisim"] > 0.15
                          and r["fark"] >= max(50_000, cur["gider"] * 0.02)]
                if rising:
                    r = max(rising, key=lambda x: x["fark"])
                    add("warn", f"En çok artan gider: {r['ad']} ({fmt_percent(r['degisim'])} artış, "
                                f"{fmt_money(r['fark'], True, plus=True)}).")
            daily = self.daily_totals(s, e)
            best = self.top_days(daily, "gelir", 1)
            if best:
                b = best[0]
                add("info", f"En yüksek gelirli gün: {fmt_date(b['tarih'])} {b['gun']} — "
                            f"{fmt_money(b['gelir'], True)}.")
            worst = self.top_days(daily, "gider", 1)
            if worst:
                w = worst[0]
                add("info", f"En yüksek giderli gün: {fmt_date(w['tarih'])} {w['gun']} — "
                            f"{fmt_money(w['gider'], True)}.")
            wk = [w for w in self.weekday_stats(s, e, daily) if w["aktif_gun"]]
            if len(wk) >= 3:
                top = max(wk, key=lambda w: w["ort_gelir"])
                low = min(wk, key=lambda w: w["ort_gelir"])
                add("info", f"En yoğun gün {top['ad']} (ortalama {fmt_money(top['ort_gelir'], True)} gelir); "
                            f"en sakin gün {low['ad']} ({fmt_money(low['ort_gelir'], True)}).")
            ch = self.payment_channels(s, e)["gelir"]
            tot = sum(v for _, v in ch)
            if tot and len(ch) > 1:
                parts = sorted(ch, key=lambda x: x[1], reverse=True)[:3]
                add("info", "Gelirin ödeme tipine dağılımı: " +
                    ", ".join(f"{ad} {fmt_percent(v / tot, 0)}" for ad, v in parts) + ".")
        if cur["cari_borc"]:
            o = cur["tahsilat_orani"] or 0
            add("good" if o >= 0.9 else ("warn" if o >= 0.6 else "bad"),
                f"Dönemde {fmt_money(cur['cari_borc'], True)} veresiye yazıldı, carilerden "
                f"{fmt_money(cur['cari_tahsilat'], True)} tahsil edildi (tahsilat oranı {fmt_percent(o, 0)}).")
        cs = self.closing_status(s, e)
        if cs["acik"]:
            add("warn", f"Kasası kapatılmamış {len(cs['acik'])} gün var (en eskisi {fmt_date(cs['acik'][0])}).")
        if cs["kilitli"]:
            add("warn", f"{cs['kilitli']} gün kapatılmadan kilitlenmiş (sonraki bir gün kapatıldığı için); bu günlerde "
                        f"kasa sayımı yapılmadı (ilki {fmt_date(cs['kilitli_gunler'][0])}).")
        if cs["gerekli"] and not cs["acik"] and not cs["kilitli"]:
            add("good", "Dönemdeki tüm günlerin kasası kapatılmış.")
        cd = self.count_differences(s, e)
        if cd["farkli_gun"]:
            add("warn", f"{cd['farkli_gun']} günde kasa sayım farkı çıktı (fazla {fmt_money(cd['fazla'])}, "
                        f"eksik {fmt_money(-cd['eksik'])}; net {fmt_money(cd['net'], True, plus=True)}).")
        elif cd["sayim_gun"]:
            add("good", f"Sayım yapılan {cd['sayim_gun']} günün hiçbirinde kasa farkı çıkmadı.")
        ag = self.cari_aging(min(to_date(e), today()), "alacak")
        if ag["toplam"]:
            old = ag["vadesi_gecmis_90"]
            if old:
                add("warn", f"Müşteri alacakları {fmt_money(ag['toplam'], True)}; 90 günden eski kısım: "
                            f"{fmt_money(old, True)}.")
            else:
                add("info", f"Müşteri alacakları {fmt_money(ag['toplam'], True)}; 90 günden eski alacak yok.")
        for r in self.payment_table(s, e):
            if r["son"] < 0:
                add("bad", f"{r['ad']} bakiyesi dönem sonunda eksiye düşmüş ({fmt_money(r['son'], True)}); "
                           "kayıtları kontrol edin.")
        return out
