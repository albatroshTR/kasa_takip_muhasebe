"""Açılış ekranı görseli."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap

from .. import APP_TITLE
from ..paths import resource_path


def splash_pixmap(version: str) -> QPixmap:
    """Açılış ekranı görseli: uygulama simgesi, program adı ve sürüm."""
    w, h, dpr = 480, 260, 2
    pm = QPixmap(w * dpr, h * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(QColor("#FFFFFF"))
    p = QPainter(pm)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
    p.fillRect(QRectF(0, 0, w, 6), QColor("#1E2A44"))
    icon = QPixmap(str(resource_path("assets", "app.png")))
    if not icon.isNull():
        icon = icon.scaled(96 * dpr, 96 * dpr, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        icon.setDevicePixelRatio(dpr)
        p.drawPixmap(QPointF((w - 96) / 2, 44), icon)
    f = QFont("Segoe UI", 18)
    f.setWeight(QFont.DemiBold)
    p.setFont(f)
    p.setPen(QColor("#1E2A44"))
    p.drawText(QRectF(0, 158, w, 36), Qt.AlignCenter, APP_TITLE)
    f.setPointSize(9)
    f.setWeight(QFont.Normal)
    p.setFont(f)
    p.setPen(QColor("#6B7694"))
    p.drawText(QRectF(0, 196, w, 20), Qt.AlignCenter, f"Sürüm {version}")
    p.setPen(QPen(QColor("#D5DBE8"), 1))
    p.drawRect(QRectF(0.5, 0.5, w - 1, h - 1))
    p.end()
    return pm
