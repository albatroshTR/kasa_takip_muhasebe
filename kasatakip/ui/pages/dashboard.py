"""Ana Sayfa: günün ve ayın özeti, bakiyeler, grafik, son hareketler."""
from __future__ import annotations

import datetime as dt

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...util import fmt_date, fmt_money, month_start, today
from ..context import daily_pdf, edit_movement, new_movement
from ..theme import C, TUR_RENK
from ..widgets import BarChart, Banner, Card, Col, DataTable, HBarList, KpiCard, button, caption, muted
from .base import Page, scroll_body


class DashboardPage(Page):
    title = "Ana Sayfa"

    def __init__(self, ctx):
        super().__init__(ctx)
        lay = scroll_body(self)
        self.banner = Banner()
        lay.addWidget(self.banner)

        kpis = QHBoxLayout()
        kpis.setSpacing(14)
        self.k_in = KpiCard("Bugün Gelir", "in", C["gelir"], C["gelir_soft"])
        self.k_out = KpiCard("Bugün Gider", "out", C["gider"], C["gider_soft"])
        self.k_month = KpiCard("Bu Ay Net (Gelir − Gider)", "chart", C["primary"], C["primary_soft"])
        self.k_cari = KpiCard("Cari Durum", "users", C["cari"], C["cari_soft"])
        for k in (self.k_in, self.k_out, self.k_month, self.k_cari):
            kpis.addWidget(k)
        lay.addLayout(kpis)

        self.bal_card = Card("Ödeme Tipi Bakiyeleri")
        self.bal_card.header.addWidget(muted("Bugün itibarıyla · her ödeme tipi ayrı devreder", small=True,
                                             wrap=False))
        self.bal_grid = QGridLayout()
        self.bal_grid.setHorizontalSpacing(12)
        self.bal_card.add(self.bal_grid)
        lay.addWidget(self.bal_card)

        mid = QHBoxLayout()
        mid.setSpacing(14)
        self.chart_card = Card("Son 30 Gün")
        self.chart = BarChart(height=250)
        self.chart_card.add(self.chart)
        mid.addWidget(self.chart_card, 3)
        self.cat_card = Card("Bu Ay Gider Dağılımı")
        self.cats = HBarList(color=C["gider"])
        self.cat_card.add(self.cats)
        self.cat_card.lay.addStretch(1)
        mid.addWidget(self.cat_card, 2)
        lay.addLayout(mid)

        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        rec = Card("Son Hareketler")
        self.recent = DataTable([
            Col("tarih", "Tarih", "date", 104), Col("saat", "Saat", width=56),
            Col("tur_adi", "Tür", width=90, color=lambda r: TUR_RENK.get(r["tur"])),
            Col("kategori_gosterim", "Kategori", width=150), Col("odeme_gosterim", "Ödeme", width=120),
            Col("cari", "Cari", width=150), Col("tutar", "Tutar", "money", 110, bold=True,
                                                color=lambda r: TUR_RENK.get(r["tur"])),
            Col("aciklama", "Açıklama", stretch=True)], sortable=False)
        self.recent.setMinimumHeight(330)
        self.recent.rowActivated.connect(lambda m: edit_movement(self.ctx, self, m))
        rec.add(self.recent)
        bottom.addWidget(rec, 3)

        quick = Card("Hızlı İşlemler")
        for text, icon, kind, cb in (
                ("Yeni Gelir  (F2)", "in", "gelir", lambda: new_movement(self.ctx, self, "GELIR")),
                ("Yeni Gider  (F3)", "out", "gider", lambda: new_movement(self.ctx, self, "GIDER")),
                ("Virman  (F4)", "swap", "virman", lambda: new_movement(self.ctx, self, "VIRMAN")),
                ("Cari Borç / Alacak Fişi", "receipt", "cari", lambda: new_movement(self.ctx, self, "CARI_BORC")),
                ("Bugünün Kasası", "wallet", None, lambda: self.ctx.main.navigate("kasa")),
                ("Bugünün Kasa Raporu (PDF)", "pdf", None, lambda: daily_pdf(self.ctx, self, today())),
                ("Raporlar", "report", None, lambda: self.ctx.main.navigate("raporlar"))):
            b = button(text, icon, kind, callback=cb)
            b.setMinimumHeight(38)
            b.setStyleSheet("text-align: left; padding-left: 14px;")
            quick.add(b)
        quick.lay.addStretch(1)
        self.last_backup = muted("", small=True)
        quick.add(self.last_backup)
        bottom.addWidget(quick, 1)
        lay.addLayout(bottom)
        lay.addStretch(1)

    def page_subtitle(self) -> str:
        return "Günün ve ayın özeti, bakiyeler ve son hareketler"

    def refresh(self) -> None:
        svc = self.svc
        t = today()
        rep = svc.day_report(t)
        self.k_in.set(fmt_money(rep["gelir"], True), f"{rep['gelir_adet']} işlem", C["gelir"])
        self.k_out.set(fmt_money(rep["gider"], True), f"{rep['gider_adet']} işlem", C["gider"])

        ms = month_start(t)
        cur = svc.summary(ms, t)
        prev_start = (ms - dt.timedelta(days=1)).replace(day=1)
        prev_end = min(prev_start + dt.timedelta(days=(t - ms).days), ms - dt.timedelta(days=1))
        prev = svc.summary(prev_start, prev_end)
        sub = f"Gelir {fmt_money(cur['gelir'])} · Gider {fmt_money(cur['gider'])}"
        if prev["adet"]:
            sub += f"\nGeçen ay aynı dönem: {fmt_money(prev['net'])} ₺"
        self.k_month.set(fmt_money(cur["net"], True), sub, C["gider"] if cur["net"] < 0 else None)
        ct = svc.cari_totals()
        self.k_cari.set(fmt_money(ct["alacaklarimiz"], True),
                        f"Alacaklarımız ({ct['borclu_sayisi']} cari)\nBorçlarımız: {fmt_money(ct['borclarimiz'])} ₺ "
                        f"({ct['alacakli_sayisi']} cari)")

        # bakiyeler
        while self.bal_grid.count():
            w = self.bal_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, r in enumerate(rep["odeme"]):
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(12, 8, 12, 8)
            box.setStyleSheet(f"background: {C['bg']}; border-radius: 8px;")
            v.addWidget(caption(r["ad"]))
            val = QLabel(fmt_money(r["gun_sonu"], True))
            val.setObjectName("BigValue")
            if r["gun_sonu"] < 0:
                val.setStyleSheet(f"color: {C['gider']};")
            v.addWidget(val)
            ch = r["gun_sonu"] - r["devir"]
            v.addWidget(muted(f"Bugün {'+' if ch >= 0 else ''}{fmt_money(ch)}", small=True))
            self.bal_grid.addWidget(box, 0, i)

        # grafik
        _, seri = svc.series(t - dt.timedelta(days=29), t, "day")
        self.chart.set_data([r["etiket"] for r in seri],
                            [("Gelir", C["gelir"], [r["gelir"] for r in seri]),
                             ("Gider", C["gider"], [r["gider"] for r in seri])],
                            [r["baslik"] for r in seri])
        cats = svc.category_totals(ms, t, "GIDER")[:7]
        self.cats.set_items([(c["ad"], c["toplam"]) for c in cats], C["gider"])
        self.recent.set_rows(svc.movements(limit=15, desc=True))

        # uyarılar
        unclosed = svc.unclosed_days()
        if unclosed:
            days = ", ".join(fmt_date(d) for d in unclosed[-5:])
            more = f" (+{len(unclosed) - 5} gün daha)" if len(unclosed) > 5 else ""
            first = unclosed[0]
            self.banner.show_message(f"Kasası kapatılmamış günler var: {days}{more}. Gün sonu kapanışı yapmayı "
                                     "unutmayın.", "Kasaya Git",
                                     lambda: self.ctx.main.open_kasa(first))
        else:
            self.banner.hide()
        lb = self.ctx.backups.last_backup_time()
        from ...util import fmt_dt
        self.last_backup.setText(f"Son yedek: {fmt_dt(lb)}" if lb else "Henüz yedek alınmadı")
