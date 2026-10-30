"""Tanımlar: gelir/gider kategorileri ve ödeme tipleri (yalnız yönetici)."""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from ...errors import ServiceError
from ...util import fmt_money
from ..dialogs import PaymentTypeDialog, TextDialog
from ..theme import C
from ..widgets import Card, Col, DataTable, ask, button, muted, warn
from .base import Page


def _status_color(r):
    return C["gelir"] if r.get("aktif") else C["faint"]


class _CategoryPanel(Card):
    def __init__(self, page, tur: str, title: str):
        super().__init__(title)
        self.page, self.svc, self.tur = page, page.svc, tur
        self.table = DataTable([Col("ad", "Kategori", stretch=True), Col("kullanim", "Kayıt", "int", 64),
                                Col("durum", "Durum", width=70, color=_status_color)], sortable=False, multi=False)
        self.table.rowActivated.connect(lambda r: self._rename())
        self.add(self.table, 1)
        r1 = QHBoxLayout()
        r1.addWidget(button("Ekle", "plus", "primary", callback=self._add))
        r1.addWidget(button("Adını Değiştir", "edit", callback=self._rename))
        r1.addStretch(1)
        r1.addWidget(button("", "up", tip="Yukarı taşı", callback=lambda: self._move(-1)))
        r1.addWidget(button("", "down", tip="Aşağı taşı", callback=lambda: self._move(1)))
        self.add(r1)
        r2 = QHBoxLayout()
        r2.addWidget(button("Aktif / Pasif", "eye", callback=self._toggle))
        r2.addWidget(button("Sil", "trash", "danger", callback=self._delete))
        r2.addStretch(1)
        self.add(r2)

    def refresh(self) -> None:
        self.table.set_rows(self.svc.categories(self.tur))

    def _cur(self):
        return self.table.current_row()

    def _run(self, fn, *a) -> bool:
        try:
            fn(*a)
        except ServiceError as e:
            warn(self, "İşlem yapılamadı", str(e))
            return False
        self.page.ctx.bus.changed.emit("tanim")
        return True

    def _add(self) -> None:
        dlg = TextDialog(self, "Yeni Kategori", "Kategori adı")
        if dlg.exec() and dlg.value():
            self._run(self.svc.add_category, self.tur, dlg.value())

    def _rename(self) -> None:
        r = self._cur()
        if not r:
            return
        dlg = TextDialog(self, "Kategori Adı", "Yeni ad", r["ad"],
                         "Ad değişikliği geçmiş kayıtlarda da görünür (kayıtlar kategoriye bağlıdır).")
        if dlg.exec() and dlg.value() and dlg.value() != r["ad"]:
            self._run(self.svc.rename_category, r["id"], dlg.value())

    def _move(self, delta: int) -> None:
        r = self._cur()
        if r and self._run(self.svc.move_category, r["id"], delta):
            self.table.select_where("id", r["id"])

    def _toggle(self) -> None:
        r = self._cur()
        if r:
            self._run(self.svc.set_category_active, r["id"], not r["aktif"])

    def _delete(self) -> None:
        r = self._cur()
        if not r:
            return
        if r["kullanim"]:
            if ask(self, "Silinemez", f"'{r['ad']}' {r['kullanim']} kayıtta kullanılıyor; silinemez.\n\n"
                                      "Pasif yapılsın mı? (Yeni kayıtlarda görünmez, eski kayıtlar korunur.)",
                   yes="Pasif Yap"):
                self._run(self.svc.set_category_active, r["id"], False)
            return
        if ask(self, "Kategori Sil", f"'{r['ad']}' kategorisi silinsin mi?", yes="Sil", danger=True):
            self._run(self.svc.delete_category, r["id"])


class _PaymentPanel(Card):
    def __init__(self, page):
        super().__init__("Ödeme Tipleri")
        self.page, self.svc = page, page.svc
        self.add(muted("Her ödeme tipinin ayrı bakiyesi vardır ve günden güne devreder. Açılış bakiyesi, programa "
                       "başladığınız andaki tutardır.", small=True))
        self.table = DataTable([Col("ad", "Ödeme Tipi", stretch=True),
                                Col("acilis_bakiye", "Açılış", "smoney", 105),
                                Col("kullanim", "Kayıt", "int", 56),
                                Col("durum", "Durum", width=70, color=_status_color)], sortable=False, multi=False)
        self.table.rowActivated.connect(lambda r: self._edit())
        self.add(self.table, 1)
        r1 = QHBoxLayout()
        r1.addWidget(button("Ekle", "plus", "primary", callback=self._add))
        r1.addWidget(button("Düzenle", "edit", callback=self._edit))
        r1.addStretch(1)
        r1.addWidget(button("", "up", tip="Yukarı taşı", callback=lambda: self._move(-1)))
        r1.addWidget(button("", "down", tip="Aşağı taşı", callback=lambda: self._move(1)))
        self.add(r1)
        r2 = QHBoxLayout()
        r2.addWidget(button("Aktif / Pasif", "eye", callback=self._toggle))
        r2.addWidget(button("Sil", "trash", "danger", callback=self._delete))
        r2.addStretch(1)
        self.add(r2)

    def refresh(self) -> None:
        self.table.set_rows(self.svc.payment_types())

    def _run(self, fn, *a) -> bool:
        try:
            fn(*a)
        except ServiceError as e:
            warn(self, "İşlem yapılamadı", str(e))
            return False
        self.page.ctx.bus.changed.emit("tanim")
        return True

    def _add(self) -> None:
        PaymentTypeDialog(self.page.ctx, self).exec()

    def _edit(self) -> None:
        r = self.table.current_row()
        if r:
            PaymentTypeDialog(self.page.ctx, self, r).exec()

    def _move(self, delta: int) -> None:
        r = self.table.current_row()
        if r and self._run(self.svc.move_payment_type, r["id"], delta):
            self.table.select_where("id", r["id"])

    def _toggle(self) -> None:
        r = self.table.current_row()
        if r:
            self._run(self.svc.set_payment_type_active, r["id"], not r["aktif"])

    def _delete(self) -> None:
        r = self.table.current_row()
        if not r:
            return
        if r["kullanim"] or r["acilis_bakiye"]:
            if ask(self, "Silinemez", f"'{r['ad']}' kullanılıyor veya açılış bakiyesi var ({fmt_money(r['acilis_bakiye'])})."
                                      "\n\nPasif yapılsın mı?", yes="Pasif Yap"):
                self._run(self.svc.set_payment_type_active, r["id"], False)
            return
        if ask(self, "Ödeme Tipi Sil", f"'{r['ad']}' silinsin mi?", yes="Sil", danger=True):
            self._run(self.svc.delete_payment_type, r["id"])


class TanimlarPage(Page):
    title = "Tanımlar"
    subtitle = "Gelir/gider kategorileri ve ödeme tipleri · Kullanılan tanımlar silinmez, pasif yapılır"

    def __init__(self, ctx):
        super().__init__(ctx)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.setSpacing(14)
        self.gelir = _CategoryPanel(self, "GELIR", "Gelir Kategorileri")
        self.gider = _CategoryPanel(self, "GIDER", "Gider Kategorileri")
        self.pay = _PaymentPanel(self)
        for w in (self.gelir, self.gider, self.pay):
            row.addWidget(w, 1)
        root.addLayout(row)

    def refresh(self) -> None:
        for p in (self.gelir, self.gider, self.pay):
            p.refresh()
