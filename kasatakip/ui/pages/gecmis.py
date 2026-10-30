"""İşlem Geçmişi: kim, ne zaman, neyi ekledi/değiştirdi/sildi (yalnız yönetici)."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

from ...util import today
from ..context import export_excel
from ..theme import C
from ..widgets import Card, Col, DataTable, PeriodSelector, button, combo, combo_select
from .base import Page

_COLORS = {"SILME": C["gider"], "GIRIS_HATASI": C["gider"], "GERI_YUKLEME": C["virman"], "ACMA": C["virman"],
           "EKLEME": C["gelir"], "KAPANIS": C["primary"]}


class GecmisPage(Page):
    title = "İşlem Geçmişi"
    subtitle = "Tüm eklemeler, değişiklikler, silmeler, girişler, kapanışlar ve yedekler"

    def __init__(self, ctx):
        super().__init__(ctx)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        fc = Card(margins=(14, 10, 14, 10))
        r = QHBoxLayout()
        self.period = PeriodSelector("Son 30 Gün")
        self.user = combo([("Tüm kullanıcılar", None)])
        self.topic = combo([("Tüm konular", None)])
        self.search = QLineEdit()
        self.search.setPlaceholderText("Ayrıntılarda ara…")
        self.search.setClearButtonEnabled(True)
        for w in (self.period, self.user, self.topic):
            r.addWidget(w)
        r.addWidget(self.search, 1)
        r.addWidget(button("Excel'e Aktar", "excel", callback=self._export))
        fc.add(r)
        root.addWidget(fc)
        self.table = DataTable([
            Col("zaman", "Zaman", "datetime", 140),
            Col("kullanici_adi", "Kullanıcı", width=110),
            Col("islem_adi", "İşlem", width=120, color=lambda r: _COLORS.get(r["islem"])),
            Col("konu", "Konu", width=100),
            Col("detay", "Ayrıntı", stretch=True),
        ])
        root.addWidget(self.table, 1)
        self.footer = QLabel()
        root.addWidget(self.footer)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self.safe_refresh)
        self.period.changed.connect(self.safe_refresh)
        self.user.currentIndexChanged.connect(self.safe_refresh)
        self.topic.currentIndexChanged.connect(self.safe_refresh)
        self.search.textChanged.connect(lambda _: self._timer.start())
        self._filling = False

    def _fill(self) -> None:
        self._filling = True
        users, topics = self.svc.audit_filters()
        cu, ct = self.user.currentData(), self.topic.currentData()
        self.user.clear()
        self.user.addItem("Tüm kullanıcılar", None)
        for u in users:
            self.user.addItem(u, u)
        combo_select(self.user, cu)
        self.topic.clear()
        self.topic.addItem("Tüm konular", None)
        for t in topics:
            self.topic.addItem(t, t)
        combo_select(self.topic, ct)
        self._filling = False

    def refresh(self) -> None:
        if self._filling:
            return
        self._fill()
        s, e = self.period.range()
        rows = self.svc.audit(s, e, self.user.currentData(), self.topic.currentData(), self.search.text().strip())
        self.table.set_rows(rows)
        self.footer.setText(f"<span style='color:{C['muted']}'>{len(rows)} kayıt"
                            f"{' (en yeni 5000 kayıt gösteriliyor)' if len(rows) >= 5000 else ''}</span>")

    def _export(self) -> None:
        rows = self.table.visible_rows()
        export_excel(self.ctx, self, f"Islem_Gecmisi_{today().isoformat()}.xlsx", [{
            "name": "İşlem Geçmişi", "title": f"İşlem Geçmişi · {self.period.label()}",
            "headers": ["Zaman", "Kullanıcı", "İşlem", "Konu", "Kayıt No", "Ayrıntı"],
            "types": ["text", "text", "text", "text", "int", "text"],
            "rows": [[r["zaman"], r["kullanici_adi"], r["islem_adi"], r["konu"], r["kayit_id"], r["detay"]]
                     for r in rows]}])
