"""Ana pencere: sol menü + sayfalar."""
from __future__ import annotations

import logging

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QFont, QGuiApplication, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_TITLE
from ..paths import resource_path
from ..services import ROL_ADI
from ..util import fmt_date, today, to_date
from .context import new_movement
from .dialogs import HelpDialog, PasswordDialog
from .icons import svg_icon
from .pages.analiz import AnalizPage
from .pages.ayarlar import AyarlarPage
from .pages.cariler import CarilerPage
from .pages.dashboard import DashboardPage
from .pages.gecmis import GecmisPage
from .pages.hareketler import HareketlerPage
from .pages.kasa import KasaPage
from .pages.kullanicilar import KullanicilarPage
from .pages.personel import PersonelPage
from .pages.raporlar import RaporlarPage
from .pages.tanimlar import TanimlarPage
from .pages.yedekleme import YedeklemePage
from .theme import C, FONT_SIZES, acik_palet, stylesheet

log = logging.getLogger("kasatakip")

NAV = [
    ("GENEL", None, None, None, False),
    ("dashboard", "Ana Sayfa", "home", DashboardPage, False),
    ("kasa", "Günlük Kasa", "wallet", KasaPage, False),
    ("hareketler", "Hareketler", "list", HareketlerPage, False),
    ("cariler", "Cariler", "users", CarilerPage, False),
    ("personel", "Personel", "user", PersonelPage, True),
    ("analiz", "Analiz", "chart", AnalizPage, False),
    ("raporlar", "Raporlar", "report", RaporlarPage, False),
    ("YÖNETİM", None, None, None, False),
    ("tanimlar", "Tanımlar", "tags", TanimlarPage, True),
    ("kullanicilar", "Kullanıcılar", "shield", KullanicilarPage, True),
    ("yedekleme", "Yedekleme", "database", YedeklemePage, False),
    ("gecmis", "İşlem Geçmişi", "history", GecmisPage, True),
    ("ayarlar", "Ayarlar", "settings", AyarlarPage, False),
]


def apply_app_style(app: QApplication, size_key: str) -> None:
    pt = FONT_SIZES.get(size_key, FONT_SIZES["normal"])
    f = QFont()
    f.setFamilies(["Segoe UI", "Noto Sans", "DejaVu Sans", "Arial"])
    f.setPointSizeF(pt)
    app.setFont(f)
    # Program açık temayla tasarlandı: Windows koyu temadayken de açık renk şeması ve palet kullanılır
    hints = QGuiApplication.styleHints()
    if hasattr(hints, "setColorScheme"):            # Qt 6.8+
        hints.setColorScheme(Qt.ColorScheme.Light)
    app.setPalette(acik_palet())
    app.setStyleSheet(stylesheet(pt))


class MainWindow(QMainWindow):
    def __init__(self, ctx, controller):
        super().__init__()
        self.ctx, self.svc, self.controller = ctx, ctx.svc, controller
        ctx.main = self
        self._logging_out = False
        self.setWindowIcon(QIcon(str(resource_path("assets", "app.png"))))
        self.setMinimumSize(1100, 680)
        self.resize(1400, 880)

        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_sidebar())
        content = QWidget()
        content.setObjectName("Content")
        cv = QVBoxLayout(content)
        cv.setContentsMargins(26, 16, 26, 10)
        cv.setSpacing(12)
        head = QHBoxLayout()
        tv = QVBoxLayout()
        tv.setSpacing(0)
        self.title = QLabel()
        self.title.setObjectName("PageTitle")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("PageSubtitle")
        tv.addWidget(self.title)
        tv.addWidget(self.subtitle)
        head.addLayout(tv, 1)
        self.today_chip = QLabel()
        self.today_chip.setProperty("chip", True)
        head.addWidget(self.today_chip, 0, Qt.AlignTop)
        cv.addLayout(head)
        self.stack = QStackedWidget()
        cv.addWidget(self.stack, 1)
        h.addWidget(content, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Hazır", 3000)

        self.pages = {}
        for key, text, icon, cls, admin in NAV:
            if cls is None or (admin and not self.svc.is_admin):
                continue
            page = cls(ctx)
            self.pages[key] = page
            self.stack.addWidget(page)

        for seq, fn in (("F1", self._help), ("F2", lambda: self._new("GELIR")), ("F3", lambda: self._new("GIDER")),
                        ("F4", lambda: self._new("VIRMAN")), ("F5", self._refresh_current)):
            QShortcut(QKeySequence(seq), self, activated=fn)
        keys = [k for k, *_ in NAV if k in self.pages]
        for i, key in enumerate(keys[:9], 1):
            QShortcut(QKeySequence(f"Ctrl+{i}"), self, activated=lambda k=key: self.navigate(k))
        self.update_brand()
        geo = self.svc.setting("pencere_geometri", "")
        if geo:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
            except Exception:  # noqa: BLE001
                pass
        self.navigate("dashboard")

    # ------------------------------------------------------------ sol menü
    def _build_sidebar(self) -> QFrame:
        sb = QFrame()
        sb.setObjectName("Sidebar")
        sb.setFixedWidth(236)
        v = QVBoxLayout(sb)
        v.setContentsMargins(14, 18, 14, 14)
        v.setSpacing(3)
        brand = QHBoxLayout()
        logo = QLabel()
        pm = QPixmap(str(resource_path("assets", "app.png")))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(38, 38, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand.addWidget(logo)
        bt = QVBoxLayout()
        bt.setSpacing(0)
        name = QLabel(APP_TITLE)
        name.setObjectName("Brand")
        self.brand_sub = QLabel()
        self.brand_sub.setObjectName("BrandSub")
        self.brand_sub.setWordWrap(True)
        bt.addWidget(name)
        bt.addWidget(self.brand_sub)
        brand.addLayout(bt, 1)
        v.addLayout(brand)
        v.addSpacing(12)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons = {}
        for key, text, icon, cls, admin in NAV:
            if cls is None:
                g = QLabel(key)
                g.setObjectName("NavGroup")
                v.addWidget(g)
                continue
            if admin and not self.svc.is_admin:
                continue
            b = QPushButton("  " + text)
            b.setProperty("nav", True)
            b.setCheckable(True)
            b.setIcon(svg_icon(icon, C["nav_text"], 18))
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.navigate(k))
            self.nav_group.addButton(b)
            self.nav_buttons[key] = (b, icon)
            v.addWidget(b)
        v.addStretch(1)
        ub = QFrame()
        ub.setObjectName("UserBox")
        uv = QVBoxLayout(ub)
        uv.setContentsMargins(12, 10, 12, 10)
        uv.setSpacing(2)
        u = self.svc.user or {}
        un = QLabel(u.get("ad_soyad") or u.get("kullanici_adi", ""))
        un.setObjectName("UserName")
        ur = QLabel(f"{u.get('kullanici_adi', '')} · {ROL_ADI.get(u.get('rol'), '')}")
        ur.setObjectName("UserRole")
        uv.addWidget(un)
        uv.addWidget(ur)
        v.addWidget(ub)
        v.addSpacing(6)
        for text, icon, fn in (("Şifre Değiştir", "key", self._change_password), ("Yardım  (F1)", "help", self._help),
                               ("Çıkış Yap", "logout", lambda: self.logout())):
            b = QPushButton("  " + text)
            b.setProperty("side", True)
            b.setIcon(svg_icon(icon, C["nav_text"], 16))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            v.addWidget(b)
        return sb

    def update_brand(self) -> None:
        firma = self.svc.setting("firma_adi") or "Firma adı tanımlanmamış"
        demo = " · DEMO" if self.ctx.paths.demo else ""
        self.brand_sub.setText(firma + demo)
        self.setWindowTitle(f"{APP_TITLE} — {firma}{demo}")

    # ------------------------------------------------------------ gezinme
    def navigate(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        for k, (b, icon) in self.nav_buttons.items():
            b.setChecked(k == key)
            b.setIcon(svg_icon(icon, "#FFFFFF" if k == key else C["nav_text"], 18))
        self.stack.setCurrentWidget(page)
        self.title.setText(page.title)
        self.subtitle.setText(page.page_subtitle())
        self.today_chip.setText(fmt_date(today(), weekday=True))
        page.on_show()

    def current_page(self):
        return self.stack.currentWidget()

    def open_kasa(self, d) -> None:
        self.navigate("kasa")
        self.pages["kasa"].set_date(to_date(d))

    def show_cari(self, cid) -> None:
        self.navigate("cariler")
        self.pages["cariler"].show_cari(cid)

    def _refresh_current(self) -> None:
        p = self.current_page()
        if p:
            p.safe_refresh()
            self.subtitle.setText(p.page_subtitle())
            self.ctx.status("Yenilendi", 2000)

    def _new(self, tur: str) -> None:
        p = self.current_page()
        d = p.default_date() if p else None
        new_movement(self.ctx, self, tur, {"tarih": d} if d else None)

    def _help(self) -> None:
        HelpDialog(self, self.ctx).exec()

    def _change_password(self) -> None:
        if PasswordDialog(self.ctx, self, "change").exec():
            self.ctx.status("Şifreniz değiştirildi.")

    def apply_font_size(self, key: str) -> None:
        apply_app_style(QApplication.instance(), key)

    # ------------------------------------------------------------ çıkış
    def logout(self, after_restore: bool = False) -> None:
        self.controller.logout(after_restore)

    def save_geometry(self) -> None:
        try:
            self.svc.set_setting("pencere_geometri", bytes(self.saveGeometry().toBase64()).decode())
        except Exception:  # noqa: BLE001
            pass

    def closeEvent(self, e):
        if not self._logging_out:
            self.controller.on_main_closing()
        e.accept()
