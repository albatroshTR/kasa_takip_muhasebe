"""Rapor önizleme: report_model bloklarını ekranda kart, tablo ve grafik olarak gösterir."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..report_model import KV, Chart, Report, Row, Table, Text, format_cell, is_negative, unwrap
from .charts import DonutChart, TrendChart
from .theme import C
from .widgets import Card, Col, DataTable, HBarList, fit_table_height, muted

TONE_COLORS = {"GELIR": C["gelir"], "GIDER": C["gider"], "VIRMAN": C["virman"], "CARI": C["cari"],
               "muted": C["faint"]}
TEXT_TONE = {"good": C["gelir"], "bad": C["gider"], "warn": C["virman"], "info": C["primary"]}
_KIND_MAP = {"money": "money", "smoney": "smoney", "dmoney": "smoney", "balance": "balance", "date": "date",
             "datetime": "datetime", "int": "int", "percent": "percent", "dpercent": "smoney", "dpp": "smoney",
             "num": "int", "text": "text"}
MAX_TABLE_H = 460
STRETCH_LIMIT = 820


def insight_label(tone: str, text: str) -> QLabel:
    c = TEXT_TONE.get(tone, C["primary"])
    lb = QLabel(f"<span style='color:{c}; font-size:13pt;'>●</span>&nbsp; {text}")
    lb.setWordWrap(True)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


def kv_grid(items, cols: int = 4) -> QGridLayout:
    g = QGridLayout()
    g.setHorizontalSpacing(18)
    g.setVerticalSpacing(10)
    for i, (lab, val, kind) in enumerate(items):
        box = QVBoxLayout()
        box.setSpacing(1)
        t = muted(lab, small=True)
        box.addWidget(t)
        v = QLabel(format_cell(val, kind) if val is not None else "—")
        v.setStyleSheet("font-weight: 700; font-size: 12pt;" +
                        (f" color: {C['gider']};" if is_negative(val, kind) else ""))
        v.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(v)
        g.addLayout(box, i // cols, i % cols)
    for c in range(cols):
        g.setColumnStretch(c, 1)
    return g


def table_widget(b: Table, on_activate=None) -> DataTable:
    """report_model.Table -> DataTable (sıralama kapalı; toplam satırı en altta kalır)."""
    n = len(b.headers)
    sample = b.rows[:200]
    est = []
    for i, (h, t) in enumerate(zip(b.headers, b.types)):
        lens = [len(format_cell(r[i], t)) for r in sample if i < len(r)]
        if b.total and i < len(b.total):
            lens.append(len(format_cell(b.total[i], t)))
        chars = max([len(h)] + lens)
        est.append(min(max(chars * 7.4 + 26, 58), 330 if t == "text" else 150))
    text_cols = [i for i, t in enumerate(b.types) if t == "text"]
    # dar tablolarda en uzun metin sütunu genişler; geniş tablolarda sütunlar sıkışmasın, yatay kaydırma olsun
    stretch = max(text_cols, key=lambda i: est[i]) if text_cols and sum(est) <= STRETCH_LIMIT else None
    cols = []
    for i, (h, t) in enumerate(zip(b.headers, b.types)):
        tone_ok = b.tone_cols is None or i in b.tone_cols
        cols.append(Col(
            f"c{i}", h, _KIND_MAP.get(t, "text"), width=int(est[i]), stretch=(i == stretch),
            fmt=lambda v, r, i=i, t=t: format_cell(r.get(f"v{i}"), t),
            color=(lambda r, ok=tone_ok: TONE_COLORS.get(r.get("_tone")) if ok else None),
            bg=lambda r: "#EEF2F8" if r.get("_total") else ("#F5F7FB" if r.get("_sub") else None)))
    table = DataTable(cols, sortable=False)
    rows = []
    for ri, row in enumerate(b.rows):
        d = {"_key": b.keys[ri] if b.keys and ri < len(b.keys) else None}
        for i in range(n):
            v = row[i] if i < len(row) else None
            d[f"v{i}"] = v
            d[f"c{i}"] = unwrap(v)[0]
        tone = b.tones[ri] if b.tones and ri < len(b.tones) else None
        if tone in ("bold", "sub"):
            d["_bold"] = True
            d["_sub"] = tone == "sub"
        elif tone:
            d["_tone"] = tone
        rows.append(d)
    if b.total and rows:
        d = {"_bold": True, "_total": True, "_key": None}
        for i in range(n):
            v = b.total[i] if i < len(b.total) else None
            d[f"v{i}"] = v
            d[f"c{i}"] = unwrap(v)[0]
        rows.append(d)
    table.set_rows(rows, keep_selection_key=None)
    if len(rows) <= 20:
        fit_table_height(table, len(rows))
    else:
        table.setFixedHeight(MAX_TABLE_H)
    if on_activate and b.key_kind:
        def activate(r, kind=b.key_kind):
            key = r.get("_key")
            if key is not None:
                # Çift tıklama işleyicisi bitmeden pencere açılmasın: açılan pencerede yapılan değişiklik önizlemeyi
                # yeniler ve bu tabloyu siler; işleyici silinmiş tabloya dönerse program çöker.
                QTimer.singleShot(0, lambda: on_activate(kind, key))
        table.rowActivated.connect(activate)
    return table


def chart_widget(b: Chart) -> QWidget:
    if b.kind == "donut":
        w = DonutChart(height=max(200, int(b.height * 3.6)), money=b.money)
        vals = list(b.series[0][2]) if b.series else []
        given = b.series[0][1] if b.series and isinstance(b.series[0][1], list) else None
        w.set_items(list(zip(b.labels, vals)), given)
        return w
    if b.kind == "hbar":
        w = HBarList()
        vals = list(b.series[0][2]) if b.series else []
        given = b.series[0][1] if b.series and isinstance(b.series[0][1], list) else None
        w.set_items([(lab, v or 0, given[i] if given and i < len(given) else C["primary"])
                     for i, (lab, v) in enumerate(zip(b.labels, vals))])
        return w
    w = TrendChart(height=max(220, int(b.height * 4.2)), money=b.money, stacked=b.kind == "stacked",
                   net_tip=b.kind == "bar" and len(b.series) == 2)
    kind = "line" if b.kind == "line" else "bar"
    w.set_data(b.labels, [{"name": n, "color": c if isinstance(c, str) else C["primary"], "values": vals,
                           "kind": kind} for n, c, vals in b.series], b.titles)
    return w


class ReportView(QWidget):
    """Bir raporun ekrandaki önizlemesi. activated(tür, anahtar): tabloda çift tıklanan satır."""
    activated = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(12)
        self.report: Report | None = None

    def clear(self) -> None:
        while self.lay.count():
            it = self.lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()                   # deleteLater beklerken eski önizleme görünmesin
                w.deleteLater()
            elif it.layout() is not None:
                _delete_layout(it.layout())

    def show_message(self, text: str) -> None:
        self.clear()
        self.report = None
        c = Card()
        lb = muted(text)
        lb.setAlignment(Qt.AlignCenter)
        lb.setMinimumHeight(120)
        c.add(lb)
        self.lay.addWidget(c)
        self.lay.addStretch(1)

    def show_report(self, rep: Report) -> None:
        self.clear()
        self.report = rep
        for b in rep.blocks:
            self.lay.addWidget(self._block(b))
        self.lay.addStretch(1)

    def _emit(self, kind, key) -> None:
        self.activated.emit(kind, key)

    def _block(self, b) -> QWidget:
        if isinstance(b, Row):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(14)
            for sub in b.blocks:
                h.addWidget(self._block(sub), 1)
            return w
        card = Card(b.title)
        if isinstance(b, KV):
            card.add(kv_grid(b.items, 4))
        elif isinstance(b, Text):
            for tone, text in b.items:
                card.add(insight_label(tone, text))
            if not b.items:
                card.add(muted("—"))
        elif isinstance(b, Chart):
            card.add(chart_widget(b))
            card.lay.addStretch(1)
        elif isinstance(b, Table):
            if b.rows:
                info = muted(f"{len(b.rows)} satır", small=True, wrap=False)
                if b.key_kind:
                    info.setToolTip("Satıra çift tıklayarak kaydı / cariyi / günü açabilirsiniz")
                card.header.addWidget(info)
                card.add(table_widget(b, self._emit))
            else:
                card.add(muted(b.empty))
            if b.note:
                card.add(muted(b.note, small=True))
        return card


def _delete_layout(lay) -> None:
    while lay.count():
        it = lay.takeAt(0)
        if it.widget() is not None:
            it.widget().deleteLater()
        elif it.layout() is not None:
            _delete_layout(it.layout())
