"""Ortak arayüz bileşenleri: tablo, kartlar, grafikler, tarih/para alanları, mesaj kutuları."""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import (QAbstractTableModel, QDate, QModelIndex, QPoint, QPointF, QRect, QRectF, QSize,
                            QSortFilterProxyModel, Qt, QTime, Signal)
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter, QDateEdit, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QLayout, QLineEdit, QMenu, QMessageBox, QPushButton, QSizePolicy, QStyle,
                               QStyledItemDelegate, QTableView, QTimeEdit, QToolTip, QVBoxLayout, QWidget,
                               QButtonGroup)

from ..paste import to_tsv
from ..util import (PERIOD_PRESETS, fmt_compact, fmt_date, fmt_dt, fmt_money, fold, parse_amount, period_range,
                    today)
from .icons import svg_icon
from .theme import C

RAW_ROLE = Qt.UserRole + 1
COPY_ROLE = Qt.UserRole + 2


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def set_prop(w: QWidget, name: str, value) -> None:
    w.setProperty(name, value)
    repolish(w)


# =========================================================================== tablo
@dataclass
class Col:
    key: str
    title: str
    kind: str = "text"          # text | money | smoney | balance | date | datetime | int | percent
    width: int | None = None
    stretch: bool = False
    align: str | None = None    # l | r | c
    color: Callable[[dict], str | None] | None = None
    fmt: Callable[[Any, dict], str] | None = None
    bold: bool = False
    bg: Callable[[dict], str | None] | None = None      # hücre arka plan rengi (ısı haritası tabloları)


class RowsModel(QAbstractTableModel):
    def __init__(self, columns: list[Col], parent=None):
        super().__init__(parent)
        self.columns = columns
        self.rows: list[dict] = []

    def set_rows(self, rows) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    @staticmethod
    def _align(col: Col):
        a = col.align or ("r" if col.kind in ("money", "smoney", "balance", "int", "percent")
                          else ("c" if col.kind in ("date",) else "l"))
        return {"l": Qt.AlignLeft, "r": Qt.AlignRight, "c": Qt.AlignHCenter}[a] | Qt.AlignVCenter

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and 0 <= section < len(self.columns):
            if role == Qt.DisplayRole:
                return self.columns[section].title
            if role == Qt.TextAlignmentRole:
                return self._align(self.columns[section])
        return None

    def display(self, row: dict, col: Col) -> str:
        v = row.get(col.key)
        if col.fmt:
            return col.fmt(v, row)
        if v is None or v == "":
            return ""
        k = col.kind
        if k in ("money", "smoney"):
            return fmt_money(v)
        if k == "balance":
            from ..util import fmt_balance
            return fmt_balance(v)
        if k == "date":
            return fmt_date(v)
        if k == "datetime":
            return fmt_dt(v)
        if k == "percent":
            return f"%{v * 100:.1f}".replace(".", ",")
        return str(v)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        col = self.columns[index.column()]
        if role == Qt.DisplayRole:
            return self.display(row, col)
        if role == RAW_ROLE:
            v = row.get(col.key)
            if col.kind in ("money", "smoney", "balance", "int", "percent"):
                return v if isinstance(v, (int, float)) else 0
            return fold(v) if isinstance(v, str) else ("" if v is None else str(v))
        if role == COPY_ROLE:
            return self.display(row, col)
        if role == Qt.TextAlignmentRole:
            return self._align(col)
        if role == Qt.ForegroundRole:
            if col.color:
                c = col.color(row)
                if c:
                    return QColor(c)
            if col.kind in ("smoney", "balance"):
                v = row.get(col.key) or 0
                if v < 0:
                    return QColor(C["gider"])
            if row.get("_muted"):
                return QColor(C["faint"])
        if role == Qt.BackgroundRole and col.bg:
            c = col.bg(row)
            if c:
                return QColor(c)
        if role == Qt.FontRole and (col.bold or row.get("_bold")):
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.ToolTipRole:
            text = self.display(row, col)
            return text if len(text) > 28 else None
        return None


class _PinProxy(QSortFilterProxyModel):
    """Sıralama: '_total' işaretli satırlar (TOPLAM) artan ve azalan sıralamada hep en altta kalır."""

    def lessThan(self, left, right):
        rows = self.sourceModel().rows
        lp = left.row() < len(rows) and bool(rows[left.row()].get("_total"))
        rp = right.row() < len(rows) and bool(rows[right.row()].get("_total"))
        if lp or rp:
            asc = self.sortOrder() == Qt.AscendingOrder
            if lp and not rp:
                return not asc
            if rp and not lp:
                return asc
            return False
        return super().lessThan(left, right)


class _CellDelegate(QStyledItemDelegate):
    """Stil sayfası hücre arka planını (BackgroundRole) yok saydığı için arka planı kendimiz boyarız."""

    def paint(self, painter, option, index):
        bg = index.data(Qt.BackgroundRole)
        if bg is not None and not (option.state & QStyle.State_Selected):
            painter.fillRect(option.rect, bg)
        super().paint(painter, option, index)


class DataTable(QTableView):
    rowActivated = Signal(dict)
    deleteRequested = Signal()
    pasteRequested = Signal()
    selectionChangedRows = Signal()

    def __init__(self, columns: list[Col], parent=None, sortable: bool = True, multi: bool = True,
                 row_height: int = 32):
        super().__init__(parent)
        self.model_ = RowsModel(columns, self)
        self.proxy = _PinProxy(self)
        self.proxy.setSourceModel(self.model_)
        self.proxy.setSortRole(RAW_ROLE)
        self.setModel(self.proxy)
        self.setSortingEnabled(sortable)
        if sortable:
            self.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        self.setItemDelegate(_CellDelegate(self))
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection if multi else QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.ElideRight)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(row_height)
        hh = self.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setMinimumSectionSize(40)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.doubleClicked.connect(lambda ix: self._emit_activated())
        self.selectionModel().selectionChanged.connect(lambda *_: self.selectionChangedRows.emit())
        self._actions: list[tuple] = []
        self._apply_widths()

    # ------------------------------------------------------------- veri
    def set_rows(self, rows, keep_selection_key: str | None = "id") -> None:
        sel_keys = set()
        if keep_selection_key:
            sel_keys = {r.get(keep_selection_key) for r in self.selected_rows()}
        self.model_.set_rows(rows)
        if sel_keys and keep_selection_key:
            for i, r in enumerate(self.model_.rows):
                if r.get(keep_selection_key) in sel_keys:
                    ix = self.proxy.mapFromSource(self.model_.index(i, 0))
                    self.selectRow(ix.row())
                    break

    def rows(self) -> list[dict]:
        return list(self.model_.rows)

    def set_columns(self, columns: list[Col]) -> None:
        """Sütunları değiştirir (ör. dönem sayısına göre değişen özet tablolar)."""
        self.model_.beginResetModel()
        self.model_.columns = list(columns)
        self.model_.rows = []
        self.model_.endResetModel()
        self._apply_widths()

    def visible_rows(self) -> list[dict]:
        return [self.model_.rows[self.proxy.mapToSource(self.proxy.index(r, 0)).row()]
                for r in range(self.proxy.rowCount())]

    def _apply_widths(self) -> None:
        hh = self.horizontalHeader()
        for i, c in enumerate(self.model_.columns):
            if c.stretch:
                hh.setSectionResizeMode(i, QHeaderView.Stretch)
            else:
                hh.setSectionResizeMode(i, QHeaderView.Interactive)
                if c.width:
                    self.setColumnWidth(i, c.width)

    def selected_rows(self) -> list[dict]:
        sm = self.selectionModel()
        if sm is None:
            return []
        rows = sorted({self.proxy.mapToSource(ix).row() for ix in sm.selectedRows()})
        return [self.model_.rows[r] for r in rows if r < len(self.model_.rows)]

    def current_row(self) -> dict | None:
        sel = self.selected_rows()
        if sel:
            return sel[0]
        ix = self.currentIndex()
        if not ix.isValid():
            return None
        r = self.proxy.mapToSource(ix).row()
        return self.model_.rows[r] if r < len(self.model_.rows) else None

    def select_where(self, key: str, value) -> None:
        for i, r in enumerate(self.model_.rows):
            if r.get(key) == value:
                ix = self.proxy.mapFromSource(self.model_.index(i, 0))
                self.selectRow(ix.row())
                self.scrollTo(ix)
                return

    # ------------------------------------------------------------- olaylar
    def _emit_activated(self) -> None:
        r = self.current_row()
        if r is not None:
            self.rowActivated.emit(r)

    def keyPressEvent(self, e):
        ctrl = bool(e.modifiers() & Qt.ControlModifier)
        if ctrl and e.key() == Qt.Key_C:
            self.copy_selection(headers=bool(e.modifiers() & Qt.ShiftModifier))
            return
        if ctrl and e.key() == Qt.Key_V:
            self.pasteRequested.emit()
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._emit_activated()
            return
        if e.key() == Qt.Key_Delete:
            self.deleteRequested.emit()
            return
        super().keyPressEvent(e)

    # ------------------------------------------------------------- kopyalama
    def copy_selection(self, headers: bool = False, all_rows: bool = False) -> int:
        cols = [c for c in range(self.model_.columnCount()) if not self.isColumnHidden(c)]
        cols.sort(key=lambda c: self.horizontalHeader().visualIndex(c))
        if all_rows:
            prow = list(range(self.proxy.rowCount()))
        else:
            prow = sorted({ix.row() for ix in self.selectionModel().selectedRows()})
        if not prow:
            return 0
        lines = []
        if headers:
            lines.append([self.model_.columns[c].title for c in cols])
        for r in prow:
            lines.append([self.proxy.index(r, c).data(COPY_ROLE) or "" for c in cols])
        QGuiApplication.clipboard().setText(to_tsv(lines))
        return len(prow)

    def copy_cell(self, pos) -> None:
        ix = self.indexAt(pos)
        if ix.isValid():
            QGuiApplication.clipboard().setText(str(ix.data(COPY_ROLE) or ""))

    def add_action(self, text: str, callback, icon: str | None = None, needs_selection: bool = True,
                   shortcut: str = "", separator_before: bool = False) -> None:
        self._actions.append((text, callback, icon, needs_selection, shortcut, separator_before))

    def _menu(self, pos) -> None:
        m = QMenu(self)
        has_sel = bool(self.selected_rows())
        for text, cb, icon, needs, sc, sep in self._actions:
            if sep:
                m.addSeparator()
            a = QAction(text + (f"\t{sc}" if sc else ""), m)
            if icon:
                a.setIcon(svg_icon(icon))
            a.setEnabled(has_sel or not needs)
            a.triggered.connect(lambda _=False, f=cb: f())
            m.addAction(a)
        if self._actions:
            m.addSeparator()
        a = m.addAction(svg_icon("copy"), "Seçili satırları kopyala\tCtrl+C")
        a.setEnabled(has_sel)
        a.triggered.connect(lambda: self.copy_selection())
        a = m.addAction("Başlıklarla kopyala\tCtrl+Shift+C")
        a.setEnabled(has_sel)
        a.triggered.connect(lambda: self.copy_selection(headers=True))
        m.addAction("Bu hücreyi kopyala").triggered.connect(lambda: self.copy_cell(pos))
        m.addAction("Tüm tabloyu kopyala").triggered.connect(lambda: self.copy_selection(headers=True, all_rows=True))
        m.addSeparator()
        m.addAction("Tümünü seç\tCtrl+A").triggered.connect(self.selectAll)
        m.exec(self.viewport().mapToGlobal(pos))
        m.deleteLater()


def fit_table_height(table: QTableView, rows: int, max_rows: int | None = None, hscroll: bool = False) -> None:
    """Tablo yüksekliğini satır sayısına göre ayarlar. hscroll=True: yatay kaydırma çubuğuna da yer bırakır."""
    n = max(1, rows if max_rows is None else min(rows, max_rows))
    h = table.horizontalHeader().sizeHint().height() + n * table.verticalHeader().defaultSectionSize()
    if hscroll:
        h += 15
    table.setFixedHeight(h + 2 * table.frameWidth() + 2)


# =========================================================================== akış düzeni
class FlowLayout(QLayout):
    """Öğeleri yan yana dizer; satır dolunca alt satıra geçer (parametre alanları için)."""

    def __init__(self, parent=None, hspacing: int = 14, vspacing: int = 8):
        super().__init__(parent)
        self._items = []
        self._h, self._v = hspacing, vspacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do(self, rect, test: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            if it.widget() is not None and it.widget().isHidden():
                continue
            hint = it.sizeHint()
            if x + hint.width() > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + self._v
                line_h = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._h
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


# =========================================================================== kartlar
class Card(QFrame):
    def __init__(self, title: str = "", parent=None, margins=(16, 14, 16, 14), spacing: int = 10):
        super().__init__(parent)
        self.setProperty("card", True)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(*margins)
        self.lay.setSpacing(spacing)
        self.header = None
        if title:
            self.header = QHBoxLayout()
            self.title_label = QLabel(title)
            self.title_label.setObjectName("SectionTitle")
            self.header.addWidget(self.title_label)
            self.header.addStretch(1)
            self.lay.addLayout(self.header)

    def add(self, w, stretch: int = 0):
        if isinstance(w, QWidget):
            self.lay.addWidget(w, stretch)
        else:
            self.lay.addLayout(w, stretch)
        return w


class KpiCard(QFrame):
    def __init__(self, title: str, icon: str = "info", color: str = C["primary"], soft: str = C["primary_soft"],
                 parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        self.setMinimumWidth(170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(10)
        ic = QLabel()
        ic.setFixedSize(34, 34)
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet(f"background: {soft}; border-radius: 9px;")
        ic.setPixmap(svg_icon(icon, color, 18).pixmap(18, 18))
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
        self.sub = QLabel("")
        self.sub.setProperty("small", True)
        self.sub.setWordWrap(True)
        lay.addWidget(self.sub)

    def set(self, value: str, sub: str = "", value_color: str | None = None, sub_color: str | None = None) -> None:
        self.value.setText(value)
        self.value.setStyleSheet(f"color: {value_color};" if value_color else "")
        self.sub.setText(sub)
        self.sub.setStyleSheet(f"color: {sub_color}; font-weight: 600;" if sub_color else "")


class Banner(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        ic = QLabel()
        ic.setPixmap(svg_icon("alert", C["warn_text"], 18).pixmap(18, 18))
        lay.addWidget(ic)
        self.text = QLabel()
        self.text.setWordWrap(True)
        lay.addWidget(self.text, 1)
        self.button = QPushButton()
        self.button.hide()
        self._callback = None
        self.button.clicked.connect(self._clicked)
        lay.addWidget(self.button)
        self.hide()

    def _clicked(self) -> None:
        if self._callback:
            self._callback()

    def show_message(self, text: str, button_text: str = "", callback=None) -> None:
        self.text.setText(text)
        self._callback = callback
        if button_text and callback:
            self.button.setText(button_text)
            self.button.show()
        else:
            self.button.hide()
        self.show()


def caption(text: str) -> QLabel:
    lb = QLabel(text.upper())
    lb.setObjectName("CardCaption")
    return lb


def muted(text: str = "", small: bool = False, wrap: bool = True) -> QLabel:
    lb = QLabel(text)
    lb.setProperty("small" if small else "muted", True)
    lb.setWordWrap(wrap)
    return lb


def hline() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {C['border']};")
    return f


def button(text: str, icon: str | None = None, kind: str | None = None, tip: str = "", callback=None,
           icon_color: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if icon:
        color = icon_color or ("#FFFFFF" if kind in ("primary", "gelir", "gider", "virman", "cari") else
                               (C["gider"] if kind == "danger" else "#334155"))
        b.setIcon(svg_icon(icon, color, 16))
        b.setIconSize(QSize(16, 16))
    if tip:
        b.setToolTip(tip)
    if callback:
        b.clicked.connect(lambda _=False: callback())
    b.setCursor(Qt.PointingHandCursor)
    return b


class Segmented(QWidget):
    """Yan yana seçilebilir düğmeler (Gelir | Gider | ...)."""
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], tones: dict | None = None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        n = len(options)
        for i, (key, text) in enumerate(options):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setProperty("seg", True)
            b.setProperty("tone", (tones or {}).get(key, "plain"))
            b.setProperty("pos", "only" if n == 1 else ("first" if i == 0 else ("last" if i == n - 1 else "mid")))
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.TabFocus)
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))

    def value(self) -> str | None:
        for k, b in self.buttons.items():
            if b.isChecked():
                return k
        return None

    def set_value(self, key: str, emit: bool = False) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)
            if emit:
                self.changed.emit(key)

    def set_enabled_keys(self, keys) -> None:
        for k, b in self.buttons.items():
            b.setVisible(k in keys)


# =========================================================================== giriş alanları
class MoneyEdit(QLineEdit):
    """Tutar alanı. '1.250,50', '1250.5', '₺1.250' gibi girişleri kabul eder."""

    def __init__(self, parent=None, big: bool = False, allow_negative: bool = False):
        super().__init__(parent)
        self.allow_negative = allow_negative
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setPlaceholderText("0,00")
        self.setProperty("bigmoney" if big else "money", True)
        self.textChanged.connect(lambda _: self.set_invalid(False))

    def value(self):
        t = self.text().strip()
        if not t:
            return None
        v = parse_amount(t)
        if v is None:
            return None
        return v if self.allow_negative else abs(v)

    def is_valid(self) -> bool:
        return not self.text().strip() or parse_amount(self.text()) is not None

    def set_value(self, kurus) -> None:
        self.setText(fmt_money(kurus) if kurus not in (None, "") else "")

    def set_invalid(self, flag: bool) -> None:
        if bool(self.property("invalid")) != flag:
            set_prop(self, "invalid", flag)

    def focusOutEvent(self, e):
        v = self.value()
        if v is not None:
            self.setText(fmt_money(v))
        elif self.text().strip():
            self.set_invalid(True)
        super().focusOutEvent(e)


def date_edit(d: dt.date | None = None) -> QDateEdit:
    w = QDateEdit()
    w.setCalendarPopup(True)
    w.setDisplayFormat("dd.MM.yyyy")
    w.setDate(qdate(d or today()))
    w.setMinimumWidth(130)
    return w


def time_edit(text: str = "") -> QTimeEdit:
    w = QTimeEdit()
    w.setDisplayFormat("HH:mm")
    if text:
        h, m = text.split(":")
        w.setTime(QTime(int(h), int(m)))
    else:
        w.setTime(QTime.currentTime())
    w.setMinimumWidth(80)
    return w


def qdate(d: dt.date) -> QDate:
    return QDate(d.year, d.month, d.day)


def pydate(q: QDate) -> dt.date:
    return dt.date(q.year(), q.month(), q.day())


class SearchCombo(QComboBox):
    """Yazarak arama yapılabilen açılır liste (ör. cari seçimi)."""
    INVALID = object()

    def __init__(self, parent=None, placeholder: str = ""):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.setMaxVisibleItems(18)
        comp = self.completer()
        comp.setFilterMode(Qt.MatchContains)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setCompletionMode(QCompleter.PopupCompletion)
        if placeholder:
            self.lineEdit().setPlaceholderText(placeholder)
        self.none_text = ""

    def set_items(self, items: list[tuple[str, Any]], none_text: str | None = None) -> None:
        cur = self.current_id()
        self.blockSignals(True)
        self.clear()
        self.none_text = none_text or ""
        if none_text is not None:
            self.addItem(none_text, None)
        for text, data in items:
            self.addItem(text, data)
        self.blockSignals(False)
        if cur is not self.INVALID:
            self.set_current_id(cur)

    def current_id(self):
        text = self.currentText().strip()
        if not text or text == self.none_text:
            return None
        key = fold(text)
        for i in range(self.count()):
            if fold(self.itemText(i)) == key:
                return self.itemData(i)
        return self.INVALID

    def set_current_id(self, data) -> None:
        idx = self.findData(data)
        if idx < 0 and data is None:
            idx = 0 if self.count() and self.itemData(0) is None else -1
        if idx >= 0:
            self.setCurrentIndex(idx)
        else:
            self.setEditText("")


def combo(items: list[tuple[str, Any]] | None = None) -> QComboBox:
    cb = QComboBox()
    cb.setMaxVisibleItems(20)
    for text, data in items or []:
        cb.addItem(text, data)
    return cb


def combo_select(cb: QComboBox, data) -> None:
    i = cb.findData(data)
    if i >= 0:
        cb.setCurrentIndex(i)


class DateNav(QWidget):
    changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.prev = button("", "left", tip="Önceki gün (Ctrl+←)")
        self.next = button("", "right", tip="Sonraki gün (Ctrl+→)")
        self.edit = date_edit()
        self.edit.setDisplayFormat("dd.MM.yyyy  ddd")
        self.edit.setMinimumWidth(170)
        self.today_btn = button("Bugün", "calendar", tip="Bugüne git")
        self.day = QLabel()
        self.day.setObjectName("DateDay")
        for w in (self.prev, self.edit, self.next, self.today_btn):
            lay.addWidget(w)
        self.prev.clicked.connect(lambda: self.shift(-1))
        self.next.clicked.connect(lambda: self.shift(1))
        self.today_btn.clicked.connect(lambda: self.set_date(today()))
        self.edit.dateChanged.connect(self._changed)
        self._changed()

    def date(self) -> dt.date:
        return pydate(self.edit.date())

    def set_date(self, d: dt.date) -> None:
        self.edit.setDate(qdate(d))

    def shift(self, n: int) -> None:
        self.set_date(self.date() + dt.timedelta(days=n))

    def _changed(self, *_):
        d = self.date()
        self.today_btn.setEnabled(d != today())
        self.changed.emit(d)


class PeriodSelector(QWidget):
    changed = Signal()

    def __init__(self, default: str = "Bu Ay", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.preset = combo([(p, p) for p in PERIOD_PRESETS] + [("Özel aralık", "Özel aralık")])
        self.preset.setMinimumWidth(130)
        self.start = date_edit()
        self.end = date_edit()
        dash = QLabel("–")
        for w in (self.preset, self.start, dash, self.end):
            lay.addWidget(w)
        self._block = False
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.start.dateChanged.connect(self._dates_changed)
        self.end.dateChanged.connect(self._dates_changed)
        combo_select(self.preset, default)
        self._preset_changed()

    def _preset_changed(self, *_):
        name = self.preset.currentData()
        if name != "Özel aralık":
            s, e = period_range(name)
            self._block = True
            self.start.setDate(qdate(s))
            self.end.setDate(qdate(e))
            self._block = False
        self.changed.emit()

    def _dates_changed(self, *_):
        if self._block:
            return
        if self.preset.currentData() != "Özel aralık":
            self.preset.blockSignals(True)
            combo_select(self.preset, "Özel aralık")
            self.preset.blockSignals(False)
        if self.start.date() > self.end.date():
            self._block = True
            self.end.setDate(self.start.date())
            self._block = False
        self.changed.emit()

    def range(self) -> tuple[dt.date, dt.date]:
        return pydate(self.start.date()), pydate(self.end.date())

    def label(self) -> str:
        s, e = self.range()
        name = self.preset.currentData()
        base = f"{fmt_date(s)} – {fmt_date(e)}" if name != "Tüm Zamanlar" else "Tüm zamanlar"
        return base


# =========================================================================== grafikler
def _nice_step(vmax: float, ticks: int = 4) -> float:
    if vmax <= 0:
        return 1
    raw = vmax / ticks
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


class BarChart(QWidget):
    """Gruplu sütun grafiği (Gelir / Gider)."""

    def __init__(self, parent=None, height: int = 240):
        super().__init__(parent)
        self.setMinimumHeight(height)
        self.setMouseTracking(True)
        self.labels: list[str] = []
        self.titles: list[str] = []
        self.series: list[tuple[str, str, list[int]]] = []
        self._hover = -1
        self._geo = None

    def set_data(self, labels, series, titles=None) -> None:
        self.labels = list(labels)
        self.titles = list(titles or labels)
        self.series = list(series)
        self._hover = -1
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = QFontMetrics(self.font())
        r = self.rect().adjusted(4, 4, -6, -4)
        # lejant
        x = r.left() + 4
        for name, color, _ in self.series:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(x, r.top() + 4, 11, 11), 3, 3)
            p.setPen(QColor(C["muted"]))
            p.drawText(QPointF(x + 16, r.top() + 14), name)
            x += 26 + fm.horizontalAdvance(name)
        n = len(self.labels)
        vmax = max([max(v) if v else 0 for _, _, v in self.series] + [0])
        step = _nice_step(vmax / 100) * 100 if vmax else 100000
        top_val = step * math.ceil(vmax / step) if vmax else step * 4
        ticks = int(round(top_val / step))
        axis_w = max(fm.horizontalAdvance(fmt_compact(top_val)), fm.horizontalAdvance("0")) + 10
        plot = QRectF(r.left() + axis_w, r.top() + 26, r.width() - axis_w - 4, r.height() - 26 - fm.height() - 8)
        # ızgara
        for i in range(ticks + 1):
            y = plot.bottom() - plot.height() * i / max(ticks, 1)
            p.setPen(QPen(QColor("#E8ECF2"), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(C["faint"]))
            p.drawText(QRectF(r.left(), y - 8, axis_w - 6, 16), Qt.AlignRight | Qt.AlignVCenter,
                       fmt_compact(step * i))
        if not n:
            p.setPen(QColor(C["faint"]))
            p.drawText(plot, Qt.AlignCenter, "Bu dönemde kayıt yok")
            self._geo = None
            return
        gw = plot.width() / n
        ns = max(len(self.series), 1)
        bw = max(2.0, min(26.0, gw * 0.72 / ns))
        self._geo = (plot, gw)
        if 0 <= self._hover < n:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#EEF3FC"))
            p.drawRect(QRectF(plot.left() + gw * self._hover, plot.top(), gw, plot.height()))
        for i in range(n):
            gx = plot.left() + gw * i + (gw - bw * ns) / 2
            for j, (_, color, vals) in enumerate(self.series):
                v = vals[i] if i < len(vals) else 0
                if v <= 0:
                    continue
                h = plot.height() * v / top_val
                rect = QRectF(gx + j * bw, plot.bottom() - h, bw - (1 if bw > 4 else 0), h)
                path = QPainterPath()
                rad = min(3.0, bw / 3)
                path.addRoundedRect(rect, rad, rad)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(color))
                p.drawPath(path)
        # x etiketleri
        p.setPen(QColor(C["muted"]))
        lw = max(fm.horizontalAdvance(l) for l in self.labels) + 10
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
            lines = [f"<b>{self.titles[i]}</b>"]
            for name, color, vals in self.series:
                v = vals[i] if i < len(vals) else 0
                lines.append(f"<span style='color:{color}'>■</span> {name}: {fmt_money(v)} ₺")
            if len(self.series) == 2:
                net = self.series[0][2][i] - self.series[1][2][i]
                lines.append(f"Net: {fmt_money(net)} ₺")
            QToolTip.showText(e.globalPosition().toPoint(), "<br>".join(lines), self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()


class HBarList(QWidget):
    """Yatay çubuklarla oran listesi (kategori dağılımı vb.)."""

    def __init__(self, parent=None, color: str = C["primary"]):
        super().__init__(parent)
        self.items: list[tuple[str, int]] = []
        self.color = color
        self.row_h = 30
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_items(self, items: list[tuple], color: str | None = None) -> None:
        """items: (etiket, değer) veya (etiket, değer, renk)"""
        self.items = [tuple(x) for x in items]
        if color:
            self.color = color
        self.setFixedHeight(max(1, len(self.items)) * self.row_h + 6)
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = QFontMetrics(self.font())
        w = self.width()
        if not self.items:
            p.setPen(QColor(C["faint"]))
            p.drawText(self.rect(), Qt.AlignCenter, "Kayıt yok")
            return
        total = sum(it[1] for it in self.items) or 1
        vmax = max(it[1] for it in self.items) or 1
        label_w = int(w * 0.36)
        val_w = fm.horizontalAdvance("000.000.000,00") + 8
        bar_x = label_w + 8
        bar_w = max(20, w - bar_x - val_w - 52)
        for i, it in enumerate(self.items):
            lab, v = it[0], it[1]
            color = it[2] if len(it) > 2 and it[2] else self.color
            y = 3 + i * self.row_h
            p.setPen(QColor(C["text"]))
            p.drawText(QRectF(0, y, label_w, self.row_h), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(lab, Qt.ElideRight, label_w))
            track = QRectF(bar_x, y + self.row_h / 2 - 5, bar_w, 10)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#EEF1F6"))
            p.drawRoundedRect(track, 5, 5)
            p.setBrush(QColor(color))
            if v > 0:
                p.drawRoundedRect(QRectF(track.left(), track.top(), max(4.0, bar_w * v / vmax), 10), 5, 5)
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(bar_x + bar_w + 6, y, 46, self.row_h), Qt.AlignVCenter | Qt.AlignLeft,
                       f"%{v * 100 / total:.0f}")
            p.setPen(QColor(C["text"]))
            p.drawText(QRectF(w - val_w, y, val_w, self.row_h), Qt.AlignVCenter | Qt.AlignRight, fmt_money(v))


# =========================================================================== mesajlar
def _box(parent, icon, title, text, buttons):
    mb = QMessageBox(parent)
    mb.setIcon(icon)
    mb.setWindowTitle(title)
    mb.setText(text)
    result = {}
    for label, role, key in buttons:
        b = mb.addButton(label, role)
        result[b] = key
    mb.exec()
    key = result.get(mb.clickedButton())
    mb.deleteLater()            # mesaj kutuları ebeveynin altında birikmesin
    return key


def info(parent, title: str, text: str) -> None:
    _box(parent, QMessageBox.Information, title, text, [("Tamam", QMessageBox.AcceptRole, True)])


def warn(parent, title: str, text: str) -> None:
    _box(parent, QMessageBox.Warning, title, text, [("Tamam", QMessageBox.AcceptRole, True)])


def error(parent, title: str, text: str) -> None:
    _box(parent, QMessageBox.Critical, title, text, [("Tamam", QMessageBox.AcceptRole, True)])


def ask(parent, title: str, text: str, yes: str = "Evet", no: str = "Vazgeç", danger: bool = False) -> bool:
    return bool(_box(parent, QMessageBox.Warning if danger else QMessageBox.Question, title, text,
                     [(yes, QMessageBox.AcceptRole, True), (no, QMessageBox.RejectRole, False)]))
