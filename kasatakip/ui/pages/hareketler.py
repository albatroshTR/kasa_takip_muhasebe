"""Hareketler: tüm kayıtlar, filtreleme, düzenleme, çoğaltma, toplu yapıştırma ve Excel'e aktarım."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

from ...services import TUR_ADI
from ...util import fmt_money, today
from ..context import delete_movements, duplicate_movement, edit_movement, export_excel, new_movement, paste_import
from ..theme import C, TUR_RENK
from ..widgets import Card, Col, DataTable, PeriodSelector, SearchCombo, button, combo, combo_select
from .base import Page


class HareketlerPage(Page):
    title = "Hareketler"
    subtitle = "Tüm gelir, gider, virman ve cari fişleri — filtreleyin, düzenleyin, Excel'e aktarın"

    def __init__(self, ctx):
        super().__init__(ctx)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        fc = Card(margins=(14, 10, 14, 10), spacing=8)
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        self.period = PeriodSelector("Bu Ay")
        self.tur = combo([("Tüm türler", None), ("Gelir", "GELIR"), ("Gider", "GIDER"), ("Gelir + Gider", "GG"),
                          ("Virman", "VIRMAN"), ("Cari fişleri", "CARI")])
        self.kat = combo([("Tüm kategoriler", None)])
        self.kat.setMinimumWidth(170)
        self.odeme = combo([("Tüm ödeme tipleri", None)])
        self.cari = SearchCombo(placeholder="Tüm cariler")
        self.cari.setMinimumWidth(260)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Ara: açıklama, belge no, cari, tutar…")
        self.search.setClearButtonEnabled(True)
        for w in (self.period, self.tur, self.kat, self.odeme):
            r1.addWidget(w)
        r1.addStretch(1)
        fc.add(r1)
        r2 = QHBoxLayout()
        r2.setSpacing(8)
        r2.addWidget(self.cari)
        r2.addWidget(self.search, 1)
        r2.addWidget(button("Filtreleri Temizle", "refresh", callback=self._clear_filters))
        fc.add(r2)
        root.addWidget(fc)

        tb = QHBoxLayout()
        tb.setSpacing(8)
        tb.addWidget(button("Gelir", "plus", "gelir", "F2", lambda: new_movement(self.ctx, self, "GELIR")))
        tb.addWidget(button("Gider", "minus", "gider", "F3", lambda: new_movement(self.ctx, self, "GIDER")))
        tb.addWidget(button("Virman", "swap", "virman", "F4", lambda: new_movement(self.ctx, self, "VIRMAN")))
        tb.addWidget(button("Cari Fişi", "receipt", "cari", callback=lambda: new_movement(self.ctx, self, "CARI_BORC")))
        tb.addSpacing(12)
        self.b_edit = button("Düzenle", "edit", callback=self._edit)
        self.b_dup = button("Çoğalt", "copy", tip="Seçili kaydın kopyasını bugüne oluştur", callback=self._dup)
        self.b_del = button("Sil", "trash", "danger", "Delete", self._delete)
        for b in (self.b_edit, self.b_dup, self.b_del):
            tb.addWidget(b)
        tb.addStretch(1)
        tb.addWidget(button("Excel'den Yapıştır", "paste", tip="Ctrl+V", callback=lambda: self._paste(False)))
        tb.addWidget(button("Excel'e Aktar", "excel", callback=self._export))
        root.addLayout(tb)

        self.table = DataTable([
            Col("tarih", "Tarih", "date", 104),
            Col("saat", "Saat", width=66, align="c"),
            Col("tur_adi", "Tür", width=92, color=lambda r: TUR_RENK.get(r["tur"])),
            Col("kategori_gosterim", "Kategori", width=160),
            Col("odeme_gosterim", "Ödeme", width=130),
            Col("cari", "Cari", width=170),
            Col("tutar", "Tutar", "money", 112, bold=True, color=lambda r: TUR_RENK.get(r["tur"])),
            Col("aciklama", "Açıklama", stretch=True),
            Col("belge_no", "Belge No", width=90),
            Col("olusturan", "Kaydeden", width=90),
        ])
        self.table.rowActivated.connect(lambda m: edit_movement(self.ctx, self, m))
        self.table.deleteRequested.connect(self._delete)
        self.table.pasteRequested.connect(lambda: self._paste(True))
        self.table.selectionChangedRows.connect(self._sel_changed)
        self.table.add_action("Düzenle", self._edit, "edit", shortcut="Enter")
        self.table.add_action("Kopyasını oluştur (çoğalt)", self._dup, "copy")
        self.table.add_action("Sil", self._delete, "trash", shortcut="Delete")
        self.table.add_action("Excel'den yapıştır…", lambda: self._paste(False), "paste", needs_selection=False,
                              separator_before=True)
        root.addWidget(self.table, 1)
        self.footer = QLabel()
        self.footer.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.footer)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self.safe_refresh)
        self.period.changed.connect(self.safe_refresh)
        self.tur.currentIndexChanged.connect(self._tur_changed)
        for w in (self.kat, self.odeme):
            w.currentIndexChanged.connect(self.safe_refresh)
        self.cari.currentIndexChanged.connect(self.safe_refresh)
        self.cari.lineEdit().editingFinished.connect(self.safe_refresh)
        self.search.textChanged.connect(lambda _: self._timer.start())
        self._loading = False
        self._sel_changed()

    def default_date(self):
        return today()

    def on_show(self) -> None:
        self._fill_filters()
        super().on_show()

    def _on_bus(self, topic: str) -> None:
        if topic in ("tanim", "cari") and self.isVisible():
            self._fill_filters()
        super()._on_bus(topic)

    # ------------------------------------------------------------ filtreler
    def _fill_filters(self) -> None:
        self._loading = True
        cur_k, cur_o = self.kat.currentData(), self.odeme.currentData()
        self.kat.clear()
        self.kat.addItem("Tüm kategoriler", None)
        t = self.tur.currentData()
        for k in self.svc.categories():
            if t in ("GELIR", "GIDER") and k["tur"] != t:
                continue
            self.kat.addItem(f"{k['ad']} ({TUR_ADI[k['tur']]})" if t not in ("GELIR", "GIDER") else k["ad"], k["id"])
        combo_select(self.kat, cur_k)
        self.odeme.clear()
        self.odeme.addItem("Tüm ödeme tipleri", None)
        for p in self.svc.payment_types():
            self.odeme.addItem(p["ad"], p["id"])
        combo_select(self.odeme, cur_o)
        self.cari.set_items([(c["unvan"], c["id"]) for c in self.svc.caris(include_inactive=True)], none_text="")
        self._loading = False

    def _tur_changed(self) -> None:
        self._fill_filters()
        self.safe_refresh()

    def _clear_filters(self) -> None:
        self._loading = True
        combo_select(self.period.preset, "Bu Ay")
        self.tur.setCurrentIndex(0)
        self.kat.setCurrentIndex(0)
        self.odeme.setCurrentIndex(0)
        self.cari.set_current_id(None)
        self.search.clear()
        self._loading = False
        self.safe_refresh()

    # ------------------------------------------------------------ yenile
    def refresh(self) -> None:
        if self._loading:
            return
        if self.kat.count() <= 1 or self.odeme.count() <= 1:
            self._fill_filters()
        s, e = self.period.range()
        t = self.tur.currentData()
        tur = {"GG": ["GELIR", "GIDER"], "CARI": ["CARI_BORC", "CARI_ALACAK"]}.get(t, t)
        cid = self.cari.current_id()
        if cid is SearchCombo.INVALID:
            cid = -1
        rows = self.svc.movements(start=s, end=e, tur=tur, kategori_id=self.kat.currentData(),
                                  odeme_tipi_id=self.odeme.currentData(), cari_id=cid,
                                  search=self.search.text().strip(), desc=True)
        self.table.set_rows(rows)
        g = sum(r["tutar"] for r in rows if r["tur"] == "GELIR")
        c = sum(r["tutar"] for r in rows if r["tur"] == "GIDER")
        v = sum(r["tutar"] for r in rows if r["tur"] == "VIRMAN")
        cf = sum(1 for r in rows if r["tur"] in ("CARI_BORC", "CARI_ALACAK"))
        net_c = C["gider"] if g - c < 0 else C["text"]
        self.footer.setText(
            f"<span style='color:{C['muted']}'>{len(rows)} kayıt</span> &nbsp;·&nbsp; "
            f"Gelir <b style='color:{C['gelir']}'>{fmt_money(g)}</b> &nbsp;·&nbsp; "
            f"Gider <b style='color:{C['gider']}'>{fmt_money(c)}</b> &nbsp;·&nbsp; "
            f"Net <b style='color:{net_c}'>{fmt_money(g - c)} ₺</b> &nbsp;·&nbsp; "
            f"<span style='color:{C['muted']}'>Virman {fmt_money(v)} · {cf} cari fişi</span>")
        self._sel_changed()

    def _sel_changed(self) -> None:
        sel = self.table.selected_rows()
        self.b_edit.setEnabled(len(sel) == 1)
        self.b_dup.setEnabled(len(sel) == 1)
        self.b_del.setEnabled(bool(sel))
        if len(sel) > 1:
            g = sum(r["isaretli"] for r in sel)
            self.ctx.status(f"{len(sel)} kayıt seçili · toplam tutar {fmt_money(sum(r['tutar'] for r in sel))} ₺ · "
                            f"net (gelir − gider) {fmt_money(g)} ₺", 0)

    # ------------------------------------------------------------ işlemler
    def _edit(self) -> None:
        r = self.table.current_row()
        if r:
            edit_movement(self.ctx, self, r)

    def _dup(self) -> None:
        r = self.table.current_row()
        if r:
            duplicate_movement(self.ctx, self, r, today())

    def _delete(self) -> None:
        delete_movements(self.ctx, self, self.table.selected_rows())

    def _paste(self, from_clipboard: bool) -> None:
        paste_import(self.ctx, self, "hareket", default_date=today(), from_clipboard=from_clipboard)

    def _export(self) -> None:
        rows = self.table.visible_rows()
        s, e = self.svc.span(*self.period.range())
        export_excel(self.ctx, self, f"Hareketler_{s.isoformat()}_{e.isoformat()}.xlsx", [{
            "name": "Hareketler", "title": f"Hareketler · {self.period.label()}",
            "headers": ["Tarih", "Saat", "Tür", "Kategori", "Ödeme", "Cari", "Tutar", "Gelir", "Gider", "Açıklama",
                        "Belge No", "Kaydeden"],
            "types": ["date", "text", "text", "text", "text", "text", "money", "money", "money", "text", "text",
                      "text"],
            "rows": [[r["tarih"], r["saat"], r["tur_adi"], r["kategori_gosterim"], r["odeme_gosterim"], r["cari"],
                      r["tutar"], r["tutar"] if r["tur"] == "GELIR" else None,
                      r["tutar"] if r["tur"] == "GIDER" else None, r["aciklama"], r["belge_no"], r["olusturan"] or ""]
                     for r in rows],
            "totals": ["TOPLAM", "", "", "", "", "", None, sum(r["tutar"] for r in rows if r["tur"] == "GELIR"),
                       sum(r["tutar"] for r in rows if r["tur"] == "GIDER"), "", "", ""],
        }])
