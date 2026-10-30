"""Ekranların ortak temeli."""
from __future__ import annotations

import logging

from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

log = logging.getLogger("kasatakip")


class Page(QWidget):
    title = ""
    subtitle = ""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.svc = ctx.svc
        self.setObjectName("PageBody")
        ctx.bus.changed.connect(self._on_bus)

    def _on_bus(self, topic: str) -> None:
        if self.isVisible():
            self.safe_refresh()

    def on_show(self) -> None:
        self.safe_refresh()

    def safe_refresh(self) -> None:
        try:
            self.refresh()
        except Exception as e:  # noqa: BLE001
            log.exception("Ekran yenilenemedi: %s", self.title)
            self.ctx.status(f"Ekran yenilenirken hata: {e}")

    def refresh(self) -> None:
        pass

    def default_date(self):
        return None

    def page_subtitle(self) -> str:
        return self.subtitle


def scroll_body(page: QWidget, spacing: int = 14) -> QVBoxLayout:
    """Sayfaya dikey kaydırılabilir bir gövde ekler ve onun düzenini döndürür."""
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    inner = QWidget()
    inner.setObjectName("PageBody")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(0, 0, 8, 8)
    lay.setSpacing(spacing)
    sa.setWidget(inner)
    outer.addWidget(sa)
    return lay
