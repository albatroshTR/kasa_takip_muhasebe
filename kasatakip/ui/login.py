"""Giriş, ilk kurulum ve şifre kurtarma pencereleri."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from .. import APP_COPYRIGHT, APP_TITLE, __version__
from ..errors import ServiceError
from ..paths import resource_path
from .dialogs import BaseDialog, RecoveryKeyDialog
from .icons import svg_icon
from .theme import C
from .widgets import button, info, muted

# yeni kurtarma anahtarı oluşturulunca (Şifremi unuttum, Ayarlar): eski anahtarın kilidi yalnızca bundan sonraki
# dosyalardan kalkar; önceden alınmış yedeklerde durur
YENI_ANAHTAR_NOTU = ("Bundan sonra canlı veriler ve yeni yedekler bu yeni anahtarla açılır. Daha önce alınmış yedekler "
                     "başka bir bilgisayarda hâlâ eski anahtarla açılır; eski anahtarı da saklayın.")


def _brand_panel(subtitle: str) -> QFrame:
    left = QFrame()
    left.setObjectName("Sidebar")
    left.setFixedWidth(300)
    lv = QVBoxLayout(left)
    lv.setContentsMargins(30, 36, 30, 24)
    lv.setSpacing(10)
    logo = QLabel()
    pm = QPixmap(str(resource_path("assets", "app.png")))
    if not pm.isNull():
        logo.setPixmap(pm.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    lv.addWidget(logo)
    lv.addSpacing(6)
    name = QLabel(APP_TITLE)
    name.setObjectName("Brand")
    name.setStyleSheet("font-size: 22pt;")
    lv.addWidget(name)
    tag = QLabel(subtitle)
    tag.setObjectName("BrandSub")
    tag.setWordWrap(True)
    lv.addWidget(tag)
    lv.addStretch(1)
    for icon, text in (("wallet", "Günlük kasa ve gün sonu kapanışı"), ("users", "Müşteri ve tedarikçi carileri"),
                       ("chart", "Geçmişe dönük analiz ve PDF raporlar"), ("database", "Otomatik yedekleme")):
        row = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(svg_icon(icon, C["nav_text"], 18).pixmap(18, 18))
        tx = QLabel(text)
        tx.setObjectName("BrandSub")
        row.addWidget(ic)
        row.addWidget(tx, 1)
        lv.addLayout(row)
    lv.addSpacing(16)
    ver = QLabel(f"Sürüm {__version__}")
    ver.setObjectName("BrandSub")
    lv.addWidget(ver)
    hak = QLabel(APP_COPYRIGHT)
    hak.setObjectName("BrandSub")
    hak.setWordWrap(True)
    lv.addWidget(hak)
    return left


def _password_field(placeholder: str = "") -> QLineEdit:
    e = QLineEdit()
    e.setEchoMode(QLineEdit.Password)
    e.setPlaceholderText(placeholder)
    act = QAction(svg_icon("eye", C["muted"], 16), "Şifreyi göster", e)
    act.setCheckable(True)
    act.toggled.connect(lambda on: e.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
    e.addAction(act, QLineEdit.TrailingPosition)
    return e


class _TwoPane(QDialog):
    def __init__(self, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(APP_TITLE)
        self.setFixedSize(820, 520)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(_brand_panel("Günlük kasa, cari hesap ve gelir-gider takibi"))
        right = QWidget()
        self.form = QVBoxLayout(right)
        self.form.setContentsMargins(44, 40, 44, 30)
        self.form.setSpacing(8)
        t = QLabel(title)
        t.setObjectName("PageTitle")
        self.form.addWidget(t)
        self.form.addWidget(muted(subtitle))
        self.form.addSpacing(10)
        h.addWidget(right, 1)
        self.err = QLabel()
        self.err.setObjectName("ErrorText")
        self.err.setWordWrap(True)
        self.err.hide()

    def exec(self) -> int:
        try:
            return super().exec()
        finally:
            self.deleteLater()

    def field(self, label: str, w):
        lb = QLabel(label)
        lb.setStyleSheet("font-weight: 600;")
        self.form.addWidget(lb)
        self.form.addWidget(w)
        self.form.addSpacing(4)
        return w

    def error(self, text: str) -> None:
        self.err.setText(text)
        self.err.show()


class LoginDialog(_TwoPane):
    def __init__(self, ctx, parent=None):
        self.ctx, self.svc = ctx, ctx.svc
        firma = self.svc.setting("firma_adi")
        super().__init__("Giriş Yap", f"{firma} — devam etmek için bilgilerinizi girin." if firma
                         else "Devam etmek için bilgilerinizi girin.", parent)
        self.user = self.field("Kullanıcı adı", QLineEdit())
        self.pw = self.field("Şifre", _password_field())
        self.form.addWidget(self.err)
        self.form.addSpacing(6)
        self.ok = button("Giriş Yap", "logout", "primary", callback=self._login)
        self.ok.setMinimumHeight(40)
        self.ok.setDefault(True)
        self.form.addWidget(self.ok)
        forgot = button("Şifremi unuttum", kind="link", callback=self._forgot)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(forgot)
        self.form.addLayout(row)
        self.form.addStretch(1)
        if ctx.paths.demo:
            self.form.addWidget(muted("DEMO MODU — kullanıcı: admin, şifre: admin. Bu veriler gerçek verinizden "
                                      "ayrıdır.", small=True))
        last = self.svc.setting("son_kullanici")
        self.user.setText(last)
        (self.pw if last else self.user).setFocus()

    def _login(self) -> None:
        try:
            self.svc.login(self.user.text(), self.pw.text())
        except ServiceError as e:
            self.error(str(e))
            self.pw.clear()
            self.pw.setFocus()
            return
        self.accept()

    def _forgot(self) -> None:
        RecoveryDialog(self.ctx, self, self.user.text()).exec()


class SetupDialog(_TwoPane):
    """İlk çalıştırmada yönetici hesabını açar."""

    def __init__(self, ctx, parent=None):
        self.ctx, self.svc = ctx, ctx.svc
        super().__init__("Hoş geldiniz", "İlk kurulum: firma adını ve yönetici hesabını belirleyin. Diğer "
                                         "kullanıcıları sonra ekleyebilirsiniz.", parent)
        self.setFixedSize(820, 600)
        self.firma = self.field("Firma / şube adı (raporlarda görünür)", QLineEdit())
        self.firma.setPlaceholderText("Örn: Örnek Firma Merkez Şubesi")
        self.full = self.field("Yönetici adı soyadı", QLineEdit())
        row = QHBoxLayout()
        self.user = QLineEdit("yonetici")
        self.pw = _password_field("en az 4 karakter")
        self.pw2 = _password_field("tekrar")
        for lab, w in (("Kullanıcı adı", self.user), ("Şifre", self.pw), ("Şifre (tekrar)", self.pw2)):
            col = QVBoxLayout()
            lb = QLabel(lab)
            lb.setStyleSheet("font-weight: 600;")
            col.addWidget(lb)
            col.addWidget(w)
            row.addLayout(col)
        self.form.addLayout(row)
        self.form.addWidget(muted("Bu ilk yönetici programın sahibi olur (sahiplik sonra devredilebilir).", small=True))
        self.form.addWidget(self.err)
        self.form.addSpacing(8)
        ok = button("Kurulumu Tamamla", "check", "primary", callback=self._ok)
        ok.setMinimumHeight(40)
        ok.setDefault(True)
        self.form.addWidget(ok)
        self.form.addStretch(1)
        self.firma.setFocus()

    def _ok(self) -> None:
        if self.pw.text() != self.pw2.text():
            self.error("Şifreler aynı değil.")
            return
        try:
            key = self.svc.setup_first_admin(self.user.text(), self.full.text(), self.pw.text(), self.firma.text())
        except ServiceError as e:
            self.error(str(e))
            return
        RecoveryKeyDialog(self, key).exec()
        self.accept()


class RecoveryDialog(BaseDialog):
    def __init__(self, ctx, parent=None, username: str = ""):
        self.ctx, self.svc = ctx, ctx.svc
        super().__init__(parent, "Şifremi Unuttum",
                         "Kurtarma anahtarıyla yönetici şifresini sıfırlayabilirsiniz; şifreli veriler de bu "
                         "anahtarla açılır. Diğer kullanıcıların şifresini yönetici, Kullanıcılar ekranından "
                         "sıfırlar.", 520)
        self.key = QLineEdit()
        self.key.setPlaceholderText("XXXX-XXXX-XXXX-XXXX")
        self.user = QLineEdit(username)
        self.pw1 = _password_field()
        self.pw2 = _password_field()
        self.add_row("Kurtarma anahtarı", self.key)
        self.add_row("Yönetici kullanıcı adı", self.user)
        self.add_row("Yeni şifre", self.pw1)
        self.add_row("Yeni şifre (tekrar)", self.pw2)
        self.buttons.addStretch(1)
        self.buttons.addWidget(button("İptal", callback=self.reject))
        b = button("Şifreyi Sıfırla", "key", "primary", callback=self._ok)
        b.setDefault(True)
        self.buttons.addWidget(b)
        self.finish_layout()
        self.key.setFocus()

    def _ok(self) -> None:
        if self.pw1.text() != self.pw2.text():
            self.show_error("Şifreler aynı değil.")
            return
        try:
            new_key = self.svc.reset_with_recovery_key(self.key.text(), self.user.text(), self.pw1.text())
        except ServiceError as e:
            self.show_error(str(e))
            return
        if new_key is None:            # geçiş bekliyor: yeni anahtar şifrelemeden sonraki ilk girişte gösterilir
            info(self, "Şifremi Unuttum", "Şifre sıfırlandı. Şimdi yeni şifrenizle giriş yapın; verileriniz "
                                          "şifrelendikten sonra yeni kurtarma anahtarınız bir kez gösterilecek.")
        else:
            RecoveryKeyDialog(self, new_key, f"Şifre sıfırlandı; yeni anahtarınızı not edin. {YENI_ANAHTAR_NOTU} "
                                             "Şimdi yeni şifrenizle giriş yapabilirsiniz.").exec()
        self.accept()


class KilitAcmaDialog(BaseDialog):
    """Başka kurulumun şifreli dosyasını (yedeğini) açmak için o dosyadaki bir kullanıcının adını ve şifresini ya da
    kurtarma anahtarını sorar. dene(giris) ServiceError verirse (ör. şifre hatalı) pencere açık kalır ve hata
    gösterilir; giris {"kullanici": ..., "sifre": ...} ya da {"kurtarma_anahtari": ...} olur.
    kurtarma=False: yalnızca kullanıcı adı ve şifre sorulur."""

    def __init__(self, parent, metin: str, dene, ok_text: str = "Aç", kurtarma: bool = True):
        super().__init__(parent, "Yedeğin Şifresi Gerekli", metin, 520)
        self.dene = dene
        self.user = QLineEdit()
        self.pw = _password_field()
        self.key = QLineEdit()
        self.key.setPlaceholderText("XXXX-XXXX-XXXX-XXXX (şifre yerine)")
        self.add_row("Kullanıcı adı", self.user)
        self.add_row("Şifre", self.pw)
        if kurtarma:
            self.add_row("veya kurtarma anahtarı", self.key)
        self.buttons.addStretch(1)
        self.buttons.addWidget(button("İptal", callback=self.reject))
        b = button(ok_text, "key", "primary", callback=self._ok)
        b.setDefault(True)
        self.buttons.addWidget(b)
        self.finish_layout()
        self.user.setFocus()

    def giris(self) -> dict:
        if self.key.text().strip():
            return {"kurtarma_anahtari": self.key.text()}
        return {"kullanici": self.user.text(), "sifre": self.pw.text()}

    def _ok(self) -> None:
        try:
            self.dene(self.giris())
        except ServiceError as e:
            self.show_error(str(e))
            self.pw.clear()
            (self.key if self.key.text().strip() else self.pw).setFocus()
            return
        self.accept()
