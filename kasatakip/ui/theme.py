"""Renkler ve uygulama genel stil sayfası (QSS)."""
from __future__ import annotations

from ..paths import resource_path

C = {
    "navy": "#1E2A44", "navy_hover": "#28385B", "navy_active": "#34497A", "nav_text": "#C3CCDF",
    "nav_muted": "#7F8BA6", "accent": "#5B9BFF",
    "bg": "#F1F4F9", "card": "#FFFFFF", "border": "#E2E7F0", "border_strong": "#C8D0DD",
    "text": "#1E293B", "muted": "#64748B", "faint": "#94A3B8",
    "primary": "#2563EB", "primary_dark": "#1D4ED8", "primary_soft": "#E6EEFE",
    "gelir": "#15803D", "gelir_dark": "#116632", "gelir_soft": "#E6F5EA",
    "gider": "#B91C1C", "gider_dark": "#991818", "gider_soft": "#FCEBEB",
    "virman": "#B45309", "virman_dark": "#92400E", "virman_soft": "#FEF3C7",
    "cari": "#6D28D9", "cari_dark": "#5B21B6", "cari_soft": "#F1EAFE",
    "warn_bg": "#FFF7E6", "warn_border": "#F5D58F", "warn_text": "#8A5A00",
    "row_alt": "#F8FAFD", "select": "#DCE8FF",
}

TUR_RENK = {"GELIR": C["gelir"], "GIDER": C["gider"], "VIRMAN": C["virman"],
            "CARI_BORC": C["cari"], "CARI_ALACAK": C["cari"]}

FONT_SIZES = {"normal": 10.0, "buyuk": 11.5, "cok_buyuk": 13.0}
FONT_SIZE_NAMES = {"normal": "Normal", "buyuk": "Büyük", "cok_buyuk": "Çok büyük"}


def _url(name: str) -> str:
    return resource_path("assets", "icons", name).as_posix()


def acik_palet():
    """Programın açık renk paleti. Windows koyu temadayken Qt koyu palet (beyaz yazı) verir; stil sayfası zeminleri
    açık renge boyadığı için, yazı rengi belirtilmeyen yerlerde (tablo satırları, onay kutuları, yardım metni…)
    beyaz zemine beyaz yazı çıkar. Palet her zaman bu açık palete sabitlenir."""
    from PySide6.QtGui import QColor, QPalette
    pal = QPalette(QColor("#FFFFFF"), QColor(C["bg"]))          # Button, Window; ışık/gölge tonları bunlardan türer
    for rol, renk in ((QPalette.Window, C["bg"]), (QPalette.WindowText, C["text"]), (QPalette.Base, "#FFFFFF"),
                      (QPalette.AlternateBase, C["row_alt"]), (QPalette.Text, C["text"]), (QPalette.Button, "#FFFFFF"),
                      (QPalette.ButtonText, C["text"]), (QPalette.ToolTipBase, "#FFFFFF"),
                      (QPalette.ToolTipText, C["text"]), (QPalette.PlaceholderText, C["faint"]),
                      (QPalette.Highlight, C["select"]), (QPalette.HighlightedText, C["text"]),
                      (QPalette.Link, C["primary"]), (QPalette.BrightText, "#FFFFFF")):
        pal.setColor(rol, QColor(renk))
    for rol in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, rol, QColor(C["faint"]))
    return pal


def stylesheet(base: float = 10.0) -> str:
    s = base
    c = C
    down, up, check = _url("chevron-down.svg"), _url("chevron-up.svg"), _url("check.svg")
    return f"""
* {{ font-size: {s}pt; }}
QMainWindow, QDialog {{ background: {c['bg']}; }}
QWidget#Content, QWidget#PageBody {{ background: {c['bg']}; }}
QLabel {{ color: {c['text']}; background: transparent; }}
QLabel[muted="true"] {{ color: {c['muted']}; }}
QLabel[small="true"] {{ font-size: {s - 1}pt; color: {c['muted']}; }}
QLabel#PageTitle {{ font-size: {s + 7}pt; font-weight: 700; color: {c['text']}; }}
QLabel#PageSubtitle {{ color: {c['muted']}; }}
QLabel#SectionTitle {{ font-size: {s + 1.5}pt; font-weight: 700; color: {c['text']}; }}
QLabel#CardCaption {{ font-size: {s - 1}pt; font-weight: 700; color: {c['muted']}; letter-spacing: 0.5px; }}
QLabel#KpiValue {{ font-size: {s + 8}pt; font-weight: 700; color: {c['text']}; }}
QLabel#KpiTitle {{ color: {c['muted']}; font-weight: 600; }}
QLabel#BigValue {{ font-size: {s + 5}pt; font-weight: 700; }}
QLabel#ErrorText {{ color: {c['gider']}; font-weight: 600; }}
QLabel#OkText {{ color: {c['gelir']}; font-weight: 600; }}
QLabel#HintText {{ color: {c['muted']}; font-size: {s - 0.5}pt; }}
QLabel#DateDay {{ color: {c['muted']}; font-weight: 600; }}

/* ---------- sol panel ---------- */
QFrame#Sidebar {{ background: {c['navy']}; border: none; }}
QLabel#Brand {{ color: white; font-size: {s + 4}pt; font-weight: 800; }}
QLabel#BrandSub {{ color: {c['nav_text']}; font-size: {s - 1}pt; }}
QLabel#NavGroup {{ color: {c['nav_muted']}; font-size: {s - 1.5}pt; font-weight: 700; letter-spacing: 1px;
                   padding: 10px 14px 4px 14px; }}
QPushButton[nav="true"] {{ background: transparent; color: {c['nav_text']}; text-align: left; border: none;
                           padding: 9px 12px; border-radius: 8px; font-weight: 500; }}
QPushButton[nav="true"]:hover {{ background: {c['navy_hover']}; color: white; }}
QPushButton[nav="true"]:checked {{ background: {c['navy_active']}; color: white; font-weight: 700; }}
QFrame#UserBox {{ background: {c['navy_hover']}; border-radius: 10px; }}
QLabel#UserName {{ color: white; font-weight: 700; }}
QLabel#UserRole {{ color: {c['nav_text']}; font-size: {s - 1}pt; }}
QPushButton[side="true"] {{ background: transparent; color: {c['nav_text']}; border: 1px solid {c['navy_active']};
                            border-radius: 7px; padding: 6px 8px; text-align: left; }}
QPushButton[side="true"]:hover {{ background: {c['navy_active']}; color: white; }}

/* ---------- kartlar ---------- */
QFrame[card="true"] {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QFrame[card="true"] QLabel {{ background: transparent; }}
QFrame#Banner {{ background: {c['warn_bg']}; border: 1px solid {c['warn_border']}; border-radius: 8px; }}
QFrame#Banner QLabel {{ color: {c['warn_text']}; }}
QFrame#LockBar {{ background: #EEF2F8; border: 1px solid {c['border_strong']}; border-radius: 8px; }}

/* ---------- düğmeler ---------- */
QPushButton {{ background: white; border: 1px solid {c['border_strong']}; border-radius: 7px; padding: 6px 12px;
               color: {c['text']}; }}
QPushButton:hover {{ background: #F5F7FB; border-color: #AEB9CB; }}
QPushButton:pressed {{ background: #EBEFF6; }}
QPushButton:disabled {{ color: {c['faint']}; background: #F4F6F9; border-color: {c['border']}; }}
QPushButton:focus {{ border-color: {c['primary']}; }}
QPushButton[kind="primary"] {{ background: {c['primary']}; border-color: {c['primary']}; color: white; font-weight: 700; }}
QPushButton[kind="primary"]:hover {{ background: {c['primary_dark']}; }}
QPushButton[kind="gelir"] {{ background: {c['gelir']}; border-color: {c['gelir']}; color: white; font-weight: 700; }}
QPushButton[kind="gelir"]:hover {{ background: {c['gelir_dark']}; }}
QPushButton[kind="gider"] {{ background: {c['gider']}; border-color: {c['gider']}; color: white; font-weight: 700; }}
QPushButton[kind="gider"]:hover {{ background: {c['gider_dark']}; }}
QPushButton[kind="virman"] {{ background: {c['virman']}; border-color: {c['virman']}; color: white; font-weight: 700; }}
QPushButton[kind="virman"]:hover {{ background: {c['virman_dark']}; }}
QPushButton[kind="cari"] {{ background: {c['cari']}; border-color: {c['cari']}; color: white; font-weight: 700; }}
QPushButton[kind="cari"]:hover {{ background: {c['cari_dark']}; }}
QPushButton[kind="primary"]:disabled, QPushButton[kind="gelir"]:disabled, QPushButton[kind="gider"]:disabled,
QPushButton[kind="virman"]:disabled, QPushButton[kind="cari"]:disabled {{ background: #CBD3DF; border-color: #CBD3DF;
                                                                          color: #F4F6F9; }}
QPushButton[kind="danger"] {{ color: {c['gider']}; }}
QPushButton[kind="danger"]:hover {{ background: {c['gider_soft']}; border-color: #E9B3B3; }}
QPushButton[kind="link"] {{ background: transparent; border: none; color: {c['primary']}; padding: 2px 4px; }}
QPushButton[kind="link"]:hover {{ text-decoration: underline; }}
QPushButton[seg="true"] {{ background: white; color: {c['muted']}; border: 1px solid {c['border_strong']};
                           border-radius: 0px; padding: 6px 14px; font-weight: 600; }}
QPushButton[seg="true"][pos="first"] {{ border-top-left-radius: 7px; border-bottom-left-radius: 7px; }}
QPushButton[seg="true"][pos="last"] {{ border-top-right-radius: 7px; border-bottom-right-radius: 7px; }}
QPushButton[seg="true"][pos="only"] {{ border-radius: 7px; }}
QPushButton[seg="true"]:checked {{ color: white; }}
QPushButton[seg="true"][tone="GELIR"]:checked {{ background: {c['gelir']}; border-color: {c['gelir']}; }}
QPushButton[seg="true"][tone="GIDER"]:checked {{ background: {c['gider']}; border-color: {c['gider']}; }}
QPushButton[seg="true"][tone="VIRMAN"]:checked {{ background: {c['virman']}; border-color: {c['virman']}; }}
QPushButton[seg="true"][tone="CARI_BORC"]:checked, QPushButton[seg="true"][tone="CARI_ALACAK"]:checked,
QPushButton[seg="true"][tone="cari"]:checked {{ background: {c['cari']}; border-color: {c['cari']}; }}
QPushButton[seg="true"][tone="plain"]:checked {{ background: {c['navy']}; border-color: {c['navy']}; }}
QToolButton {{ background: white; border: 1px solid {c['border_strong']}; border-radius: 7px; padding: 5px; }}
QToolButton:hover {{ background: #F5F7FB; }}

/* ---------- giriş alanları ---------- */
QLineEdit, QComboBox, QDateEdit, QTimeEdit, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: white; border: 1px solid {c['border_strong']}; border-radius: 6px; padding: 5px 8px;
    color: {c['text']}; selection-background-color: {c['select']}; selection-color: {c['text']}; }}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QTimeEdit:focus, QSpinBox:focus, QPlainTextEdit:focus,
QTextEdit:focus {{ border: 1px solid {c['primary']}; }}
QLineEdit:disabled, QComboBox:disabled, QDateEdit:disabled, QTimeEdit:disabled, QPlainTextEdit:disabled {{
    background: #F4F6F9; color: {c['faint']}; }}
QLineEdit[money="true"] {{ font-weight: 700; }}
QLineEdit[bigmoney="true"] {{ font-weight: 700; font-size: {s + 4}pt; padding: 6px 10px; }}
QLineEdit[invalid="true"], QComboBox[invalid="true"] {{ border: 1px solid {c['gider']}; background: #FFF7F7; }}
QComboBox {{ padding-right: 22px; }}
QComboBox::drop-down, QDateEdit::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right;
    width: 22px; border: none; }}
QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url({down}); width: 11px; height: 11px; }}
QComboBox QAbstractItemView {{ background: white; border: 1px solid {c['border_strong']}; outline: none;
    selection-background-color: {c['primary_soft']}; selection-color: {c['text']}; padding: 2px; }}
QSpinBox::up-button, QTimeEdit::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 18px;
    border: none; }}
QSpinBox::down-button, QTimeEdit::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right;
    width: 18px; border: none; }}
QSpinBox::up-arrow, QTimeEdit::up-arrow {{ image: url({up}); width: 9px; height: 9px; }}
QSpinBox::down-arrow, QTimeEdit::down-arrow {{ image: url({down}); width: 9px; height: 9px; }}
QCheckBox, QRadioButton {{ spacing: 7px; color: {c['text']}; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {c['border_strong']}; border-radius: 4px;
    background: white; }}
QCheckBox::indicator:checked {{ background: {c['primary']}; border-color: {c['primary']}; image: url({check}); }}
QCheckBox::indicator:disabled {{ background: #EEF1F5; }}

/* ---------- tablolar ---------- */
QTableView, QTableWidget {{ background: white; border: 1px solid {c['border']}; border-radius: 8px;
    gridline-color: #EDF0F5; alternate-background-color: {c['row_alt']}; selection-background-color: {c['select']};
    selection-color: {c['text']}; outline: none; }}
QTableView::item {{ padding: 0px 6px; border: none; }}
QTableView::item:selected {{ background: {c['select']}; color: {c['text']}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: #F6F8FB; color: {c['muted']}; font-weight: 700; border: none;
    border-bottom: 1px solid {c['border']}; border-right: 1px solid #EDF0F5; padding: 6px 8px; }}
QTableCornerButton::section {{ background: #F6F8FB; border: none; }}

/* ---------- diğer ---------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #C7CFDC; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #AAB5C6; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #C7CFDC; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: transparent; }}
QStatusBar {{ background: white; color: {c['muted']}; border-top: 1px solid {c['border']}; }}
QStatusBar QLabel {{ color: {c['muted']}; }}
QToolTip {{ background: {c['navy']}; color: white; border: none; padding: 6px 8px; border-radius: 4px; }}
QMenu {{ background: white; border: 1px solid {c['border_strong']}; padding: 4px; border-radius: 6px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 4px; color: {c['text']}; }}
QMenu::item:selected {{ background: {c['primary_soft']}; }}
QMenu::item:disabled {{ color: {c['faint']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 6px; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 8px; background: white; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 8px 16px; border: none;
    border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:selected {{ color: {c['primary']}; border-bottom: 2px solid {c['primary']}; }}
QGroupBox {{ border: 1px solid {c['border']}; border-radius: 8px; margin-top: 14px; padding: 12px 10px 10px 10px;
    background: white; font-weight: 700; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {c['muted']}; }}
QLabel[badge="acik"] {{ background: {c['gelir_soft']}; color: {c['gelir']}; border-radius: 11px; padding: 3px 12px;
    font-weight: 700; }}
QLabel[badge="kapali"] {{ background: #E5EAF3; color: {c['navy']}; border-radius: 11px; padding: 3px 12px;
    font-weight: 700; }}
QLabel[badge="kilitli"] {{ background: {c['virman_soft']}; color: {c['virman_dark']}; border-radius: 11px;
    padding: 3px 12px; font-weight: 700; }}
QLabel[chip="true"] {{ background: {c['primary_soft']}; color: {c['primary_dark']}; border-radius: 10px;
    padding: 2px 10px; font-weight: 600; }}
QTextBrowser {{ background: white; border: 1px solid {c['border']}; border-radius: 8px; padding: 8px; }}
"""
