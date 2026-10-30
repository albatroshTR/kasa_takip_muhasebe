"""Ayarlar: firma bilgileri, yazı boyutu, rapor klasörü, kurtarma anahtarı, program bilgisi."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit

from ... import __version__
from ...errors import ServiceError
from ..context import open_path
from ..dialogs import RecoveryKeyDialog
from ..login import YENI_ANAHTAR_NOTU
from ..theme import FONT_SIZE_NAMES
from ..widgets import Card, Segmented, ask, button, muted, warn
from .base import Page, scroll_body


class AyarlarPage(Page):
    title = "Ayarlar"
    subtitle = "Firma bilgileri, görünüm, rapor klasörü ve güvenlik"

    def __init__(self, ctx):
        super().__init__(ctx)
        lay = scroll_body(self)
        row = QHBoxLayout()
        row.setSpacing(14)

        fc = Card("Firma Bilgileri")
        fc.add(muted("Raporların (PDF) başlığında görünür.", small=True))
        g = QGridLayout()
        g.setVerticalSpacing(8)
        self.f = {}
        for i, (k, t, ph) in enumerate((("adi", "Firma / şube adı", "Örn: Örnek Firma Merkez Şubesi"),
                                        ("adres", "Adres", ""), ("telefon", "Telefon", ""),
                                        ("vergi", "Vergi dairesi / no", "Örn: Selçuk V.D. 1234567890"))):
            lb = QLabel(t)
            lb.setProperty("muted", True)
            e = QLineEdit()
            e.setPlaceholderText(ph)
            g.addWidget(lb, i, 0)
            g.addWidget(e, i, 1)
            self.f[k] = e
        g.setColumnStretch(1, 1)
        fc.add(g)
        br = QHBoxLayout()
        br.addStretch(1)
        self.b_firma = button("Kaydet", "check", "primary", callback=self._save_firma)
        br.addWidget(self.b_firma)
        fc.add(br)
        row.addWidget(fc, 3)

        vc = Card("Görünüm")
        vc.add(muted("Yazı boyutu (tüm ekranlar)"))
        self.font_sel = Segmented(list(FONT_SIZE_NAMES.items()))
        vc.add(self.font_sel)
        vc.add(muted("Büyük yazı tipi, ekranı yakından okumayı kolaylaştırır.", small=True))
        vc.lay.addStretch(1)
        row.addWidget(vc, 2)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(14)
        rc = Card("Raporlar")
        self.l_rep = QLabel()
        self.l_rep.setWordWrap(True)
        rc.add(muted("PDF raporların ve Excel dosyalarının kaydedildiği klasör"))
        rc.add(self.l_rep)
        rr = QHBoxLayout()
        rr.addWidget(button("Klasörü Aç", "folder", callback=lambda: open_path(self.ctx.report_dir())))
        self.b_rep = button("Değiştir…", "edit", callback=self._choose_report_dir)
        self.b_rep_def = button("Varsayılan", callback=self._default_report_dir)
        rr.addWidget(self.b_rep)
        rr.addWidget(self.b_rep_def)
        rr.addStretch(1)
        rc.add(rr)
        self.imza = QCheckBox("Günlük kasa raporunda imza alanları olsun (Teslim Eden / Teslim Alan / Kontrol Eden)")
        self.imza.toggled.connect(self._save_imza)
        rc.add(self.imza)
        row2.addWidget(rc, 3)

        sc = Card("Güvenlik")
        sc.add(muted("Kurtarma anahtarı, yönetici şifresi unutulduğunda yeni şifre belirlemeye yarar. Kaybettiyseniz "
                     "yenisini oluşturun; canlı veriler ve yeni yedekler artık yenisiyle açılır.", small=True))
        self.b_key = button("Yeni Kurtarma Anahtarı Oluştur", "key", callback=self._new_key)
        sc.add(self.b_key)
        sc.lay.addStretch(1)
        row2.addWidget(sc, 2)
        lay.addLayout(row2)

        ic = Card("Program Bilgisi")
        self.l_info = QLabel()
        self.l_info.setWordWrap(True)
        self.l_info.setTextInteractionFlags(self.l_info.textInteractionFlags())
        ic.add(self.l_info)
        ir = QHBoxLayout()
        ir.addWidget(button("Veri Klasörünü Aç", "folder", callback=lambda: open_path(self.ctx.paths.data_dir)))
        ir.addWidget(button("Günlük (log) Klasörü", "folder", callback=lambda: open_path(self.ctx.paths.log_dir)))
        ir.addStretch(1)
        ic.add(ir)
        lay.addWidget(ic)
        lay.addStretch(1)
        self.font_sel.changed.connect(self._font_changed)

    def refresh(self) -> None:
        admin = self.svc.is_admin
        firma = self.svc.firma()
        for k, e in self.f.items():
            e.setText(firma.get(k, ""))
            e.setEnabled(admin)
        self.b_firma.setEnabled(admin)
        self.font_sel.set_value(self.svc.setting("yazi_boyutu", "normal"))
        self.l_rep.setText(str(self.ctx.report_dir()))
        for w in (self.b_rep, self.b_rep_def, self.imza):
            w.setEnabled(admin)
        sahip = self.svc.is_sahip
        self.b_key.setEnabled(sahip)
        self.b_key.setToolTip("" if sahip else "Yeni kurtarma anahtarını yalnızca program sahibi oluşturabilir.")
        self.imza.blockSignals(True)
        self.imza.setChecked(self.svc.setting("pdf_imza", "1") == "1")
        self.imza.blockSignals(False)
        p = self.ctx.paths
        self.l_info.setText(
            f"<b>Kasa Takip</b> sürüm {__version__}<br>"
            f"Veritabanı: {p.db_path}<br>Yedekler: {self.ctx.backups.backup_dir}<br>"
            f"Raporlar: {self.ctx.report_dir()}<br>Günlük dosyaları: {p.log_dir}"
            + ("<br><b>DEMO MODU</b> — gerçek verilerden ayrı klasör" if p.demo else ""))

    def _save_firma(self) -> None:
        try:
            self.svc.save_firma({k: e.text() for k, e in self.f.items()})
        except ServiceError as e:
            warn(self, "Kaydedilemedi", str(e))
            return
        self.ctx.bus.changed.emit("ayar")
        self.ctx.main.update_brand()
        self.ctx.status("Firma bilgileri kaydedildi.")

    def _font_changed(self, key: str) -> None:
        self.svc.set_setting("yazi_boyutu", key)
        self.ctx.main.apply_font_size(key)

    def _choose_report_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Rapor klasörünü seçin", str(self.ctx.report_dir()))
        if d:
            self.svc.set_setting("rapor_klasoru", d)
            self.svc.log("AYAR", "Ayarlar", None, f"Rapor klasörü: {d}")
            self.refresh()

    def _default_report_dir(self) -> None:
        self.svc.set_setting("rapor_klasoru", "")
        self.refresh()

    def _save_imza(self, on: bool) -> None:
        self.svc.set_setting("pdf_imza", "1" if on else "0")

    def _new_key(self) -> None:
        if not ask(self, "Kurtarma Anahtarı", f"Yeni bir kurtarma anahtarı oluşturulsun mu?\n\n{YENI_ANAHTAR_NOTU}"):
            return
        try:
            key = self.svc.regenerate_recovery_key()
        except ServiceError as e:
            warn(self, "Oluşturulamadı", str(e))
            return
        RecoveryKeyDialog(self, key, f"Yeni kurtarma anahtarınız aşağıda. {YENI_ANAHTAR_NOTU}").exec()
