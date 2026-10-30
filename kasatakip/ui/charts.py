"""Analiz grafikleri (QPainter ile çizilir, ek kütüphane gerekmez).

TrendChart      sütun + çizgi (karma) grafik: gruplu/yığılmış sütun, çizgi, alan; negatif değer destekli
DonutChart      halka (dağılım) grafiği, sağda lejant
Gauge           yarım daire gösterge (oran / yüzde)
Sparkline       KPI kartlarındaki küçük trend çizgisi
TrendKpi        değer + değişim rozeti + küçük trend çizgisi olan gösterge kartı
CalendarHeatmap takvim ısı haritası (günlük gelir/gider/net)
HCompareBars    yatay karşılaştırma çubukları (bu dönem / önceki dönem)
"""
from __future__ import annotations

import datetime as dt
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolTip, QVBoxLayout, QWidget

from ..report_model import OTHER_COLOR, SERIES_COLORS
from ..util import TR_DAYS, TR_DAYS_SHORT, TR_MONTHS_SHORT, fmt_compact, fmt_date, fmt_money, fmt_percent, to_date
from .icons import svg_icon
from .theme import C

PALETTE = SERIES_COLORS
GRID = "#E8ECF2"
ZERO = "#B9C3D3"
EMPTY_TEXT = "Bu dönemde kayıt yok"


def palette(i: int) -> str:
    return PALETTE[i % len(PALETTE)]


def fmt_int(v) -> str:
    return f"{int(v or 0):,}".replace(",", ".")


def fmt_value(v, money: bool = True) -> str:
    if v is None:
        return "—"
    return f"{fmt_money(v)} ₺" if money else fmt_int(v)


def _nice_step(raw: float) -> float:
    if raw <= 0:
        return 1
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def nice_scale(lo: float, hi: float, money: bool = True, ticks: int = 4):
    """Eksen için yuvarlak alt/üst sınır ve adım (0 her zaman dahil)."""
    lo, hi = min(lo, 0), max(hi, 0)
    if lo == hi:
        hi = 100_000 if money else 4
    unit = 100 if money else 1          # kuruş -> TL adımları
    step = _nice_step((hi - lo) / unit / ticks) * unit
    if not money:
        step = max(1, step)
    lo = math.floor(lo / step) * step
    hi = math.ceil(hi / step) * step
    return lo, hi, step


def _blend(c1: str, c2: str, t: float) -> QColor:
    a, b = QColor(c1), QColor(c2)
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def _with_alpha(c: str, alpha: int) -> QColor:
    q = QColor(c)
    q.setAlpha(alpha)
    return q


# =========================================================================== karma grafik
class TrendChart(QWidget):
    """series: [{'name', 'color', 'values', 'kind': 'bar'|'line'|'area', 'dashed': bool}]"""

    def __init__(self, parent=None, height: int = 260, money: bool = True, stacked: bool = False,
                 legend: bool = True, net_tip: bool = False):
        super().__init__(parent)
        self.setMinimumHeight(height)
        self.setMouseTracking(True)
        self.money, self.stacked, self.legend, self.net_tip = money, stacked, legend, net_tip
        self.labels: list[str] = []
        self.titles: list[str] = []
        self.series: list[dict] = []
        self.empty_text = EMPTY_TEXT
        self._hover = -1
        self._geo = None

    def set_data(self, labels, series, titles=None) -> None:
        self.labels = list(labels)
        self.titles = list(titles or labels)
        self.series = [dict(s) for s in series]
        for s in self.series:
            s.setdefault("kind", "bar")
            s["values"] = list(s.get("values") or [])
        self._hover = -1
        self.update()

    @staticmethod
    def _v(s, i):
        vals = s["values"]
        return vals[i] if i < len(vals) else None

    def _axis_text(self, v) -> str:
        return fmt_compact(v) if self.money else fmt_int(v)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = QFontMetrics(self.font())
        r = QRectF(self.rect()).adjusted(4, 4, -6, -4)
        top = r.top()
        if self.legend and self.series:
            x, y = r.left() + 4, top + 2
            for s in self.series:
                w = 22 + fm.horizontalAdvance(s["name"]) + 16
                if x + w > r.right() and x > r.left() + 4:
                    x, y = r.left() + 4, y + fm.height() + 6
                col = QColor(s["color"])
                cy = y + fm.ascent() / 2 + 1
                if s["kind"] == "bar":
                    p.setPen(Qt.NoPen)
                    p.setBrush(col)
                    p.drawRoundedRect(QRectF(x, cy - 5.5, 11, 11), 3, 3)
                else:
                    pen = QPen(col, 2.2)
                    if s.get("dashed"):
                        pen.setStyle(Qt.DashLine)
                    p.setPen(pen)
                    p.drawLine(QPointF(x - 2, cy), QPointF(x + 14, cy))
                p.setPen(QColor(C["muted"]))
                p.drawText(QPointF(x + 18, y + fm.ascent()), s["name"])
                x += w
            top = y + fm.height() + 10
        n = len(self.labels)
        bars = [s for s in self.series if s["kind"] == "bar"]
        lines = [s for s in self.series if s["kind"] in ("line", "area")]
        vals: list[float] = []
        if self.stacked and bars:
            for i in range(n):
                vs = [self._v(s, i) or 0 for s in bars]
                vals += [sum(v for v in vs if v > 0), sum(v for v in vs if v < 0)]
        else:
            for s in bars:
                vals += [v for v in s["values"] if v is not None]
        for s in lines:
            vals += [v for v in s["values"] if v is not None]
        lo, hi, step = nice_scale(min(vals + [0]), max(vals + [0]), self.money)
        axis_w = max(fm.horizontalAdvance(self._axis_text(lo)), fm.horizontalAdvance(self._axis_text(hi))) + 10
        plot = QRectF(r.left() + axis_w, top, r.width() - axis_w - 4, r.bottom() - top - fm.height() - 8)
        if plot.height() < 20 or plot.width() < 20:
            return

        def ypos(v):
            return plot.bottom() - (v - lo) / (hi - lo) * plot.height()

        ticks = int(round((hi - lo) / step))
        for i in range(ticks + 1):
            v = lo + step * i
            y = ypos(v)
            p.setPen(QPen(QColor(ZERO if abs(v) < 1e-9 else GRID), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(C["faint"]))
            p.drawText(QRectF(r.left(), y - 8, axis_w - 6, 16), Qt.AlignRight | Qt.AlignVCenter, self._axis_text(v))
        if not n or not any(v for v in vals):
            p.setPen(QColor(C["faint"]))
            p.drawText(plot, Qt.AlignCenter, self.empty_text)
            self._geo = (plot, plot.width() / max(n, 1)) if n else None
            return
        gw = plot.width() / n
        self._geo = (plot, gw)
        if 0 <= self._hover < n:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#EEF3FC"))
            p.drawRect(QRectF(plot.left() + gw * self._hover, plot.top(), gw, plot.height()))
        y0 = ypos(0)
        if bars:
            if self.stacked:
                bw = max(2.0, min(36.0, gw * 0.64))
                for i in range(n):
                    x0 = plot.left() + gw * i + (gw - bw) / 2
                    pos = neg = 0.0
                    for s in bars:
                        v = self._v(s, i) or 0
                        if not v:
                            continue
                        if v > 0:
                            a, b = ypos(pos + v), ypos(pos)
                            pos += v
                        else:
                            a, b = ypos(neg), ypos(neg + v)
                            neg += v
                        p.setPen(Qt.NoPen)
                        p.setBrush(QColor(s["color"]))
                        p.drawRect(QRectF(x0, a, bw, max(0.8, b - a - (0.8 if bw > 6 else 0))))
            else:
                nb = len(bars)
                bw = max(2.0, min(26.0, gw * 0.74 / nb))
                for i in range(n):
                    gx = plot.left() + gw * i + (gw - bw * nb) / 2
                    for j, s in enumerate(bars):
                        v = self._v(s, i)
                        if not v:
                            continue
                        y1 = ypos(v)
                        rect = QRectF(gx + j * bw, min(y0, y1), bw - (1 if bw > 4 else 0), max(1.0, abs(y1 - y0)))
                        path = QPainterPath()
                        rad = min(3.0, bw / 3)
                        path.addRoundedRect(rect, rad, rad)
                        p.setPen(Qt.NoPen)
                        p.setBrush(QColor(s["color"]))
                        p.drawPath(path)
        for s in lines:
            segs, cur = [], []
            for i in range(n):
                v = self._v(s, i)
                if v is None:
                    if cur:
                        segs.append(cur)
                    cur = []
                    continue
                cur.append(QPointF(plot.left() + gw * i + gw / 2, ypos(v)))
            if cur:
                segs.append(cur)
            col = QColor(s["color"])
            for pts in segs:
                if s["kind"] == "area" and len(pts) > 1:
                    path = QPainterPath(QPointF(pts[0].x(), y0))
                    for pt in pts:
                        path.lineTo(pt)
                    path.lineTo(QPointF(pts[-1].x(), y0))
                    path.closeSubpath()
                    p.setPen(Qt.NoPen)
                    p.setBrush(_with_alpha(s["color"], 46))
                    p.drawPath(path)
                pen = QPen(col, 2.2)
                pen.setJoinStyle(Qt.RoundJoin)
                pen.setCapStyle(Qt.RoundCap)
                if s.get("dashed"):
                    pen.setStyle(Qt.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                if len(pts) > 1:
                    path = QPainterPath(pts[0])
                    for pt in pts[1:]:
                        path.lineTo(pt)
                    p.drawPath(path)
                if n <= 45 or len(pts) == 1:
                    p.setPen(QPen(col, 1.8))
                    p.setBrush(QColor("white"))
                    for pt in pts:
                        p.drawEllipse(pt, 2.8, 2.8)
            if 0 <= self._hover < n and self._v(s, self._hover) is not None:
                pt = QPointF(plot.left() + gw * self._hover + gw / 2, ypos(self._v(s, self._hover)))
                p.setPen(QPen(QColor("white"), 2))
                p.setBrush(col)
                p.drawEllipse(pt, 4.5, 4.5)
        p.setPen(QColor(C["muted"]))
        lw = max(fm.horizontalAdvance(lab) for lab in self.labels) + 12
        every = max(1, math.ceil(lw / gw))
        for i, lab in enumerate(self.labels):
            if i % every:
                continue
            cx = plot.left() + gw * i + gw / 2
            p.drawText(QRectF(cx - lw / 2, plot.bottom() + 4, lw, fm.height() + 2), Qt.AlignHCenter | Qt.AlignTop, lab)

    def mouseMoveEvent(self, e):
        if not self._geo or not self.labels:
            return
        plot, gw = self._geo
        x = e.position().x()
        i = int((x - plot.left()) // gw) if plot.left() <= x <= plot.right() else -1
        if i != self._hover:
            self._hover = i
            self.update()
        if 0 <= i < len(self.labels):
            lines = [f"<b>{self.titles[i] if i < len(self.titles) else self.labels[i]}</b>"]
            for s in self.series:
                v = self._v(s, i)
                lines.append(f"<span style='color:{s['color']}'>■</span> {s['name']}: {fmt_value(v, self.money)}")
            if self.net_tip and len(self.series) >= 2:
                a, b = self._v(self.series[0], i) or 0, self._v(self.series[1], i) or 0
                lines.append(f"Fark (net): {fmt_value(a - b, self.money)}")
            QToolTip.showText(e.globalPosition().toPoint(), "<br>".join(lines), self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()


# =========================================================================== halka grafik
class DonutChart(QWidget):
    def __init__(self, parent=None, height: int = 220, money: bool = True, max_slices: int = 7,
                 center_title: str = "Toplam"):
        super().__init__(parent)
        self.setMinimumHeight(height)
        self.setMouseTracking(True)
        self.money, self.max_slices, self.center_title = money, max_slices, center_title
        self.items: list[tuple[str, float, str]] = []
        self.total = 0
        self._hover = -1
        self._geo = None
        self.empty_text = EMPTY_TEXT

    def set_items(self, items, colors: list[str] | None = None) -> None:
        rows = [(str(a), b) for a, b in items if b and b > 0]
        rows.sort(key=lambda x: x[1], reverse=True)
        if len(rows) > self.max_slices:
            keep = rows[:self.max_slices - 1]
            rest = sum(v for _, v in rows[self.max_slices - 1:])
            rows = keep + [(f"Diğer ({len(rows) - len(keep)} kalem)", rest)]
        out = []
        for i, (lab, v) in enumerate(rows):
            col = (colors[i] if colors and i < len(colors) else
                   (OTHER_COLOR if lab.startswith("Diğer (") else palette(i)))
            out.append((lab, v, col))
        self.items = out
        self.total = sum(v for _, v, _ in out)
        self._hover = -1
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = QFontMetrics(self.font())
        w, h = self.width(), self.height()
        if not self.items:
            p.setPen(QColor(C["faint"]))
            p.drawText(self.rect(), Qt.AlignCenter, self.empty_text)
            self._geo = None
            return
        d = max(60.0, min(h - 16.0, w * 0.34))
        cx, cy = 8 + d / 2 + 4, h / 2
        outer = QRectF(cx - d / 2, cy - d / 2, d, d)
        thick = d * 0.23
        inner = outer.adjusted(thick, thick, -thick, -thick)
        start = 90.0
        spans = []
        for i, (lab, v, col) in enumerate(self.items):
            sweep = -360.0 * v / self.total
            o = outer.adjusted(-4, -4, 4, 4) if i == self._hover else outer
            path = QPainterPath()
            path.arcMoveTo(o, start)
            path.arcTo(o, start, sweep)
            path.arcTo(inner, start + sweep, -sweep)
            path.closeSubpath()
            p.setPen(QPen(QColor("white"), 1.5))
            p.setBrush(QColor(col))
            p.drawPath(path)
            spans.append((start, sweep))
            start += sweep
        # orta yazı
        bf = QFont(self.font())
        bf.setBold(True)
        bf.setPointSizeF(self.font().pointSizeF() + 2.5)
        p.setFont(bf)
        p.setPen(QColor(C["text"]))
        val = fmt_compact(self.total) if self.money else fmt_int(self.total)
        if 0 <= self._hover < len(self.items):
            val = fmt_percent(self.items[self._hover][1] / self.total)
        p.drawText(QRectF(inner.left(), cy - fm.height() - 1, inner.width(), fm.height() + 6),
                   Qt.AlignHCenter | Qt.AlignBottom, val)
        p.setFont(self.font())
        p.setPen(QColor(C["muted"]))
        sub = self.items[self._hover][0] if 0 <= self._hover < len(self.items) else self.center_title
        p.drawText(QRectF(inner.left() + 4, cy + 3, inner.width() - 8, fm.height() + 2), Qt.AlignHCenter | Qt.AlignTop,
                   fm.elidedText(sub, Qt.ElideRight, int(inner.width() - 8)))
        # lejant
        lx = outer.right() + 18
        lw = w - lx - 6
        rh = min(24.0, max(fm.height() + 3.0, (h - 8) / max(1, len(self.items))))
        y = cy - rh * len(self.items) / 2
        val_w = max(fm.horizontalAdvance(fmt_value(v, self.money)) for _, v, _ in self.items) + 4
        pct_w = fm.horizontalAdvance("%100,0") + 8
        show_val = lw > val_w + pct_w + 90
        legend_rows = []
        for i, (lab, v, col) in enumerate(self.items):
            row = QRectF(lx, y + i * rh, lw, rh)
            legend_rows.append(row)
            if i == self._hover:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#F1F5FB"))
                p.drawRoundedRect(row.adjusted(-6, 0, 0, 0), 5, 5)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(col))
            p.drawRoundedRect(QRectF(lx, row.center().y() - 5, 10, 10), 3, 3)
            label_w = lw - 18 - pct_w - (val_w + 6 if show_val else 0)
            p.setPen(QColor(C["text"]))
            p.drawText(QRectF(lx + 16, row.top(), label_w, rh), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(lab, Qt.ElideRight, int(max(10, label_w))))
            p.setPen(QColor(C["muted"]))
            px = lx + 16 + label_w
            p.drawText(QRectF(px, row.top(), pct_w, rh), Qt.AlignVCenter | Qt.AlignRight,
                       fmt_percent(v / self.total))
            if show_val:
                p.setPen(QColor(C["text"]))
                p.drawText(QRectF(px + pct_w, row.top(), val_w + 6, rh), Qt.AlignVCenter | Qt.AlignRight,
                           fmt_value(v, self.money))
        self._geo = (QPointF(cx, cy), d / 2, d / 2 - thick, spans, legend_rows)

    def _hit(self, pos) -> int:
        if not self._geo:
            return -1
        c, ro, ri, spans, rows = self._geo
        for i, row in enumerate(rows):
            if row.adjusted(-6, 0, 0, 0).contains(pos):
                return i
        dx, dy = pos.x() - c.x(), pos.y() - c.y()
        dist = math.hypot(dx, dy)
        if not (ri - 2 <= dist <= ro + 5):
            return -1
        ang = math.degrees(math.atan2(-dy, dx))           # saat yönünün tersine, 0 = sağ
        cw = (90 - ang) % 360                              # saat yönünde, 0 = üst
        acc = 0.0
        for i, (_, sweep) in enumerate(spans):
            acc += -sweep
            if cw <= acc + 1e-9:
                return i
        return len(spans) - 1

    def mouseMoveEvent(self, e):
        i = self._hit(e.position())
        if i != self._hover:
            self._hover = i
            self.update()
        if 0 <= i < len(self.items):
            lab, v, col = self.items[i]
            QToolTip.showText(e.globalPosition().toPoint(),
                              f"<b>{lab}</b><br>{fmt_value(v, self.money)} · {fmt_percent(v / self.total)}", self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()


# =========================================================================== gösterge
class Gauge(QWidget):
    """Yarım daire gösterge. good_high=True: yüksek değer iyi (ör. kapanış oranı)."""

    def __init__(self, title: str, parent=None, good_high: bool = True, green: float = 0.9, amber: float = 0.7,
                 height: int = 168, tip: str = ""):
        super().__init__(parent)
        self.title, self.good_high, self.green, self.amber = title, good_high, green, amber
        self.setMinimumHeight(height)
        self.setMinimumWidth(150)
        self.ratio = None
        self.text = "—"
        self.sub = ""
        if tip:
            self.setToolTip(tip)

    def set_value(self, ratio, text: str | None = None, sub: str = "") -> None:
        self.ratio = ratio
        self.text = text if text is not None else (fmt_percent(ratio) if ratio is not None else "—")
        self.sub = sub
        self.update()

    def color(self) -> str:
        r = self.ratio
        if r is None:
            return C["faint"]
        if self.good_high:
            return C["gelir"] if r >= self.green else (C["virman"] if r >= self.amber else C["gider"])
        return C["gelir"] if r <= self.green else (C["virman"] if r <= self.amber else C["gider"])

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = QFontMetrics(self.font())
        w, h = self.width(), self.height()
        tf = QFont(self.font())
        tf.setBold(True)
        p.setFont(tf)
        p.setPen(QColor(C["muted"]))
        p.drawText(QRectF(4, 2, w - 8, fm.height() + 2), Qt.AlignHCenter | Qt.AlignTop,
                   QFontMetrics(tf).elidedText(self.title, Qt.ElideRight, w - 8))
        p.setFont(self.font())
        title_h = fm.height() + 8
        sub_h = fm.height() + 2 if self.sub else 0
        label_h = fm.height() + 2                      # yayın uçlarındaki 0 / %100 yazıları
        avail_h = h - title_h - label_h - sub_h - 4
        rad = max(20.0, min(w * 0.34, avail_h / 1.11))
        thick = max(8.0, rad * 0.22)
        cx = w / 2
        cy = title_h + thick / 2 + rad
        rect = QRectF(cx - rad, cy - rad, 2 * rad, 2 * rad)
        pen = QPen(QColor("#E9EDF4"), thick)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 180 * 16, -180 * 16)
        if self.ratio is not None and self.ratio > 0:
            span = -180 * min(1.0, self.ratio)
            pen = QPen(QColor(self.color()), thick)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(rect, 180 * 16, int(span * 16))
        vf = QFont(self.font())
        vf.setBold(True)
        vf.setPointSizeF(self.font().pointSizeF() + 5)
        p.setFont(vf)
        p.setPen(QColor(self.color() if self.ratio is not None else C["faint"]))
        vfm = QFontMetrics(vf)
        p.drawText(QRectF(cx - rad, cy - vfm.height() - 1, 2 * rad, vfm.height() + 2), Qt.AlignHCenter | Qt.AlignBottom,
                   self.text)
        p.setFont(self.font())
        p.setPen(QColor(C["faint"]))
        p.drawText(QRectF(cx - rad - thick / 2 - 20, cy + 2, 40, fm.height()), Qt.AlignHCenter, "0")
        p.drawText(QRectF(cx + rad + thick / 2 - 26, cy + 2, 52, fm.height()), Qt.AlignHCenter, "%100")
        if self.sub:
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(4, cy + label_h, w - 8, fm.height() + 2), Qt.AlignHCenter | Qt.AlignTop,
                       fm.elidedText(self.sub, Qt.ElideRight, w - 8))


# =========================================================================== küçük trend
class Sparkline(QWidget):
    def __init__(self, parent=None, height: int = 34):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.values: list[float] = []
        self.color = C["primary"]

    def set_values(self, values, color: str | None = None) -> None:
        self.values = [v for v in (values or []) if v is not None]
        if color:
            self.color = color
        self.update()

    def paintEvent(self, ev):
        if len(self.values) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 4, -5, -3)
        lo, hi = min(self.values + [0]), max(self.values + [0])
        if hi == lo:
            hi = lo + 1
        n = len(self.values)

        def pt(i, v):
            return QPointF(r.left() + r.width() * i / (n - 1), r.bottom() - (v - lo) / (hi - lo) * r.height())

        pts = [pt(i, v) for i, v in enumerate(self.values)]
        y0 = r.bottom() - (0 - lo) / (hi - lo) * r.height()
        path = QPainterPath(QPointF(pts[0].x(), y0))
        for q in pts:
            path.lineTo(q)
        path.lineTo(QPointF(pts[-1].x(), y0))
        path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(_with_alpha(self.color, 38))
        p.drawPath(path)
        if lo < 0 < hi:
            p.setPen(QPen(QColor(ZERO), 1, Qt.DotLine))
            p.drawLine(QPointF(r.left(), y0), QPointF(r.right(), y0))
        line = QPainterPath(pts[0])
        for q in pts[1:]:
            line.lineTo(q)
        pen = QPen(QColor(self.color), 1.8)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(line)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(pts[-1], 2.8, 2.8)


class TrendKpi(QFrame):
    """Gösterge kartı: başlık, büyük değer, değişim rozeti, karşılaştırma metni ve küçük trend çizgisi."""

    def __init__(self, title: str, icon: str = "info", color: str = C["primary"], soft: str = C["primary_soft"],
                 parent=None, spark: bool = True):
        super().__init__(parent)
        self.setProperty("card", True)
        self.setMinimumWidth(190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.color = color
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 10)
        lay.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(10)
        ic = QLabel()
        ic.setFixedSize(32, 32)
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet(f"background: {soft}; border-radius: 9px;")
        ic.setPixmap(svg_icon(icon, color, 17).pixmap(17, 17))
        t = QLabel(title)
        t.setObjectName("KpiTitle")
        t.setWordWrap(True)
        top.addWidget(ic)
        top.addWidget(t, 1)
        lay.addLayout(top)
        self.value = QLabel("—")
        self.value.setObjectName("KpiValue")
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.value)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.badge = QLabel("")
        self.badge.setProperty("small", True)
        row.addWidget(self.badge)
        self.sub = QLabel("")
        self.sub.setProperty("small", True)
        self.sub.setWordWrap(True)
        row.addWidget(self.sub, 1)
        lay.addLayout(row)
        self.spark = Sparkline() if spark else None
        if self.spark:
            lay.addWidget(self.spark)

    def set(self, value: str, delta=None, good_up: bool = True, sub: str = "", spark=None,
            value_color: str | None = None, spark_color: str | None = None, pp: bool = False) -> None:
        """delta: oran olarak değişim (0,12 = %12). pp=True ise puan farkı olarak gösterilir."""
        self.value.setText(value)
        self.value.setStyleSheet(f"color: {value_color};" if value_color else "")
        if delta is None:
            self.badge.setText("")
            self.badge.setStyleSheet("")
            self.badge.hide()
        else:
            up = delta >= 0
            good = (up == good_up) if abs(delta) >= 0.0005 else None
            fg = C["muted"] if good is None else (C["gelir"] if good else C["gider"])
            bg = "#EEF1F6" if good is None else (C["gelir_soft"] if good else C["gider_soft"])
            arrow = "■" if good is None else ("▲" if up else "▼")
            amount = f"{abs(delta) * 100:.1f} puan".replace(".", ",") if pp else fmt_percent(abs(delta))
            self.badge.setText(f"{arrow} {amount}")
            self.badge.setStyleSheet(f"color: {fg}; background: {bg}; border-radius: 8px; padding: 1px 7px; "
                                     "font-weight: 700;")
            self.badge.show()
        self.sub.setText(sub)
        if self.spark is not None:
            self.spark.set_values(spark or [], spark_color or self.color)


# =========================================================================== takvim ısı haritası
class CalendarHeatmap(QWidget):
    MAX_DAYS = 371

    def __init__(self, parent=None, money: bool = True):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(232)
        self.money = money
        self.start = self.end = None
        self.values: dict = {}
        self.color = C["gelir"]
        self.neg_color = C["gider"]
        self.diverging = False
        self.label = "Gelir"
        self._geo = None

    def set_data(self, start, end, values: dict, color: str | None = None, diverging: bool = False,
                 label: str = "Gelir", money: bool = True) -> None:
        s, e = to_date(start), to_date(end)
        if (e - s).days >= self.MAX_DAYS:
            s = e - dt.timedelta(days=self.MAX_DAYS - 1)
        self.start, self.end = s, e
        self.values = dict(values)
        self.color = color or C["gelir"]
        self.diverging, self.label, self.money = diverging, label, money
        self.update()

    def truncated(self, start) -> bool:
        return self.start is not None and to_date(start) < self.start

    def _cell_color(self, v, vmax):
        if not v:
            return QColor("#EDF0F5")
        t = min(1.0, abs(v) / vmax) if vmax else 1.0
        level = 0.28 + 0.72 * (math.ceil(t * 4) / 4)
        base = self.neg_color if (self.diverging and v < 0) else self.color
        return _blend("#FFFFFF", base, level)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = QFontMetrics(self.font())
        if not self.start:
            return
        first = self.start - dt.timedelta(days=self.start.weekday())
        weeks = (self.end - first).days // 7 + 1
        left = max(fm.horizontalAdvance(x) for x in TR_DAYS_SHORT) + 10
        top = fm.height() + 6
        legend_h = fm.height() + 12
        by_w = (self.width() - left - 8) / max(weeks, 1)
        by_h = (self.height() - top - legend_h - 4) / 7
        ch = max(7.0, min(26.0, by_h))
        cw = min(by_w, ch * 3.2)                 # kısa dönemlerde hücreler yatay genişler
        if cw < ch:
            ch = cw
        cw, ch = max(cw, 6.0), max(ch, 6.0)
        gap = 2.0 if ch >= 12 else 1.0
        vals = [v for v in self.values.values() if v]
        vmax = max((abs(v) for v in vals), default=0)
        p.setPen(QColor(C["faint"]))
        for row, name in enumerate(TR_DAYS_SHORT):
            if ch >= 13 or row in (0, 2, 4, 6):
                p.drawText(QRectF(0, top + row * ch, left - 6, ch), Qt.AlignRight | Qt.AlignVCenter, name)
        last_month = None
        for wk in range(weeks):
            wd = first + dt.timedelta(days=7 * wk)
            ref = max(wd, self.start)
            if ref.month != last_month:
                p.setPen(QColor(C["muted"]))
                p.drawText(QPointF(left + wk * cw, fm.ascent() + 1),
                           f"{TR_MONTHS_SHORT[ref.month - 1]}" + (f" {ref.year}" if ref.month == 1 or wk == 0 else ""))
                last_month = ref.month
        small = QFont(self.font())
        small.setPointSizeF(max(6.5, self.font().pointSizeF() - 2))
        show_day = cw >= 30 and ch >= 16
        d = self.start
        while d <= self.end:
            wk = (d - first).days // 7
            row = d.weekday()
            rect = QRectF(left + wk * cw, top + row * ch, cw - gap, ch - gap)
            v = self.values.get(d.isoformat())
            col = self._cell_color(v, vmax)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(rect, 2.5, 2.5)
            if show_day:
                p.setFont(small)
                dark = col.lightness() < 150
                p.setPen(QColor("white") if dark else QColor("#475569"))
                p.drawText(rect.adjusted(4, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft, str(d.day))
                p.setFont(self.font())
            d += dt.timedelta(days=1)
        # lejant
        ly = top + 7 * ch + 8
        p.setPen(QColor(C["faint"]))
        x = left
        if self.diverging:
            p.drawText(QPointF(x, ly + fm.ascent()), "Zarar")
            x += fm.horizontalAdvance("Zarar") + 6
            for t in (1.0, 0.5):
                p.setPen(Qt.NoPen)
                p.setBrush(_blend("#FFFFFF", self.neg_color, 0.28 + 0.72 * t))
                p.drawRoundedRect(QRectF(x, ly + 1, 12, 12), 2.5, 2.5)
                x += 15
        else:
            p.drawText(QPointF(x, ly + fm.ascent()), "Az")
            x += fm.horizontalAdvance("Az") + 6
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#EDF0F5"))
        p.drawRoundedRect(QRectF(x, ly + 1, 12, 12), 2.5, 2.5)
        x += 15
        for t in (0.25, 0.5, 0.75, 1.0):
            p.setBrush(_blend("#FFFFFF", self.color, 0.28 + 0.72 * t))
            p.drawRoundedRect(QRectF(x, ly + 1, 12, 12), 2.5, 2.5)
            x += 15
        p.setPen(QColor(C["faint"]))
        p.drawText(QPointF(x + 4, ly + fm.ascent()), "Kâr" if self.diverging else "Çok")
        self._geo = (first, left, top, cw, ch)

    def mouseMoveEvent(self, e):
        if not self._geo:
            return
        first, left, top, cw, ch = self._geo
        x, y = e.position().x(), e.position().y()
        if x < left or y < top or y > top + 7 * ch:
            QToolTip.hideText()
            return
        wk, row = int((x - left) // cw), int((y - top) // ch)
        d = first + dt.timedelta(days=wk * 7 + row)
        if not (self.start <= d <= self.end):
            QToolTip.hideText()
            return
        v = self.values.get(d.isoformat())
        txt = f"<b>{fmt_date(d)} {TR_DAYS[d.weekday()]}</b><br>{self.label}: "
        txt += fmt_value(v, self.money) if v else "kayıt yok"
        QToolTip.showText(e.globalPosition().toPoint(), txt, self)


# =========================================================================== yatay karşılaştırma
class HCompareBars(QWidget):
    """items: [(etiket, bu_dönem, önceki_dönem | None)]"""

    def __init__(self, parent=None, color: str = C["primary"], money: bool = True, good_up: bool = True):
        super().__init__(parent)
        self.items: list = []
        self.color, self.money, self.good_up = color, money, good_up
        self.names = ("Bu dönem", "Karşılaştırma")
        self.row_h = 34
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)

    def set_items(self, items, color: str | None = None, good_up: bool | None = None,
                  names: tuple[str, str] | None = None) -> None:
        self.items = list(items)
        if color:
            self.color = color
        if good_up is not None:
            self.good_up = good_up
        if names:
            self.names = names
        has_prev = any(x[2] is not None for x in self.items)
        self.setFixedHeight(max(1, len(self.items)) * self.row_h + (26 if has_prev else 6))
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = QFontMetrics(self.font())
        w = self.width()
        if not self.items:
            p.setPen(QColor(C["faint"]))
            p.drawText(self.rect(), Qt.AlignCenter, EMPTY_TEXT)
            return
        has_prev = any(x[2] is not None for x in self.items)
        y0 = 0
        if has_prev:
            x = 0
            for name, col in ((self.names[0], self.color), (self.names[1], "#C3CBD8")):
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(col))
                p.drawRoundedRect(QRectF(x, 5, 11, 11), 3, 3)
                p.setPen(QColor(C["muted"]))
                p.drawText(QPointF(x + 16, 5 + fm.ascent() - 1), name)
                x += 34 + fm.horizontalAdvance(name)
            y0 = 22
        vmax = max([max(a or 0, b or 0) for _, a, b in self.items] + [1])
        label_w = int(w * 0.30)
        val_w = fm.horizontalAdvance("000.000.000,00") + 10
        chg_w = fm.horizontalAdvance("▲ %000,0") + 10 if has_prev else 0
        bar_x = label_w + 8
        bar_w = max(30, w - bar_x - val_w - chg_w - 8)
        for i, (lab, cur, prev) in enumerate(self.items):
            y = y0 + i * self.row_h
            p.setPen(QColor(C["text"]))
            p.drawText(QRectF(0, y, label_w, self.row_h), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(lab, Qt.ElideRight, label_w))
            cy = y + self.row_h / 2
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#EEF1F6"))
            p.drawRoundedRect(QRectF(bar_x, cy - 8, bar_w, 9), 4, 4)
            if cur:
                p.setBrush(QColor(self.color))
                p.drawRoundedRect(QRectF(bar_x, cy - 8, max(4.0, bar_w * cur / vmax), 9), 4, 4)
            if prev:
                p.setBrush(QColor("#C3CBD8"))
                p.drawRoundedRect(QRectF(bar_x, cy + 3, max(3.0, bar_w * prev / vmax), 5), 2.5, 2.5)
            p.setPen(QColor(C["text"]))
            p.drawText(QRectF(bar_x + bar_w + 6, y, val_w, self.row_h), Qt.AlignVCenter | Qt.AlignRight,
                       fmt_money(cur or 0) if self.money else fmt_int(cur))
            if has_prev:
                if prev:
                    ch = ((cur or 0) - prev) / prev
                    up = ch >= 0
                    good = up == self.good_up
                    p.setPen(QColor(C["gelir"] if good else C["gider"]))
                    txt = f"{'▲' if up else '▼'} {fmt_percent(abs(ch))}" if abs(ch) < 10 else ("▲ çok" if up else "▼")
                elif cur:
                    p.setPen(QColor(C["muted"]))
                    txt = "yeni"
                else:
                    p.setPen(QColor(C["muted"]))
                    txt = ""
                p.drawText(QRectF(w - chg_w, y, chg_w, self.row_h), Qt.AlignVCenter | Qt.AlignRight, txt)

    def mouseMoveEvent(self, e):
        has_prev = any(x[2] is not None for x in self.items)
        y0 = 22 if has_prev else 0
        i = int((e.position().y() - y0) // self.row_h)
        if 0 <= i < len(self.items):
            lab, cur, prev = self.items[i]
            txt = f"<b>{lab}</b><br>{self.names[0]}: {fmt_value(cur or 0, self.money)}"
            if prev is not None:
                txt += f"<br>{self.names[1]}: {fmt_value(prev, self.money)}"
            QToolTip.showText(e.globalPosition().toPoint(), txt, self)
        else:
            QToolTip.hideText()


__all__ = ["TrendChart", "DonutChart", "Gauge", "Sparkline", "TrendKpi", "CalendarHeatmap", "HCompareBars",
           "PALETTE", "OTHER_COLOR", "palette", "fmt_value", "fmt_int", "nice_scale"]
