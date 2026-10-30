"""Uygulamanın başlatılması: günlükler, tek örnek kilidi, giriş akışı, ana pencere."""
from __future__ import annotations

import logging
import sys
import time
import traceback
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QEventLoop, QLibraryInfo, QLocale, QLockFile, QObject, Qt, QTimer, QTranslator
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QSplashScreen

from . import APP_NAME, APP_TITLE, __version__, netguard, sifreleme
from .backup import BackupManager
from .db import Database
from .paths import AppPaths, resource_path
from .personel_listesi import ILK_PERSONEL
from .services import Service

log = logging.getLogger("kasatakip")

SIFRELEME_KURTARMA_METNI = ("Verileriniz artık şifreli saklanıyor ve program şifreleriyle açılıyor. Tüm şifreler "
                            "unutulursa verileri ve bundan sonraki yedekleri (başka bir bilgisayarda da) yalnızca bu yeni "
                            "kurtarma anahtarı açabilir; programın önceki sürümündeki kurtarma anahtarı şifreli verileri "
                            "açmaz. Not edip güvenli bir yerde saklayın.")
KILITSIZ_METNI = ("Şu kullanıcıların şifreli verileri açan kilidi henüz yok: {adlar}.\n\nProgram yeniden açıldığında "
                  "bu kullanıcılar kendi şifreleriyle giriş yapamaz. Lütfen şifrelerini Kullanıcılar ekranından "
                  "sıfırlayın; ilk girişlerinde yeni şifre belirlerler.")
PAYLASAN_METNI = ("Şu kullanıcıların adları yalnızca Türkçe harflerle ayrılıyor ve şifreli dosyada aynı kilidi "
                  "paylaşıyor: {adlar}. Yalnızca biri verileri açabilir ve bu kullanıcıların şifresi sıfırlanamaz, "
                  "değiştirilemez. Önce Kullanıcılar ekranından birini pasif yapın; kilit kalan kullanıcının olur. Sonra "
                  "kalan kullanıcının şifresini bir kez sıfırlayın.")


def _setup_logging(paths: AppPaths) -> None:
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(paths.log_dir / "kasatakip.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


def _install_excepthook() -> None:
    def hook(exc_type, exc, tb):
        log.error("Beklenmeyen hata:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        try:
            mb = QMessageBox()
            mb.setIcon(QMessageBox.Warning)
            mb.setWindowTitle("Beklenmeyen hata")
            mb.setText(f"Beklenmeyen bir hata oluştu:\n\n{exc}\n\nAyrıntılar günlük (log) dosyasına yazıldı. "
                       "Verileriniz etkilenmedi; işleme devam edebilirsiniz.")
            mb.addButton("Tamam", QMessageBox.AcceptRole)
            mb.exec()
        except Exception:  # noqa: BLE001
            pass
    sys.excepthook = hook


class AppController(QObject):
    def __init__(self, app: QApplication, ctx):
        super().__init__()
        self.app, self.ctx, self.svc = app, ctx, ctx.svc
        self.main = None

    def start(self) -> bool:
        from .ui.dialogs import PasswordDialog
        from .ui.login import LoginDialog, SetupDialog
        from .ui.main_window import MainWindow, apply_app_style
        apply_app_style(self.app, self.svc.setting("yazi_boyutu", "normal"))
        while True:
            if not self.svc.users_exist():
                if SetupDialog(self.ctx).exec() != QDialog.Accepted:
                    return False
            elif LoginDialog(self.ctx).exec() != QDialog.Accepted:
                return False
            if self.svc.user and self.svc.user.get("sifre_degistirmeli"):
                if not PasswordDialog(self.ctx, None, "force").exec():
                    self.svc.logout()
                    continue
            break
        self._giris_sonrasi()
        apply_app_style(self.app, self.svc.setting("yazi_boyutu", "normal"))
        try:
            self.ctx.backups.backup_if_old("acilis", hours=6)
        except Exception:  # noqa: BLE001
            log.exception("Açılış yedeği alınamadı")
        self.main = MainWindow(self.ctx, self)
        self.main.show()
        return True

    def _mesaj(self, icon, text: str) -> None:
        mb = QMessageBox(icon, APP_TITLE, text)
        mb.addButton("Tamam", QMessageBox.AcceptRole)
        mb.exec()

    def _giris_sonrasi(self) -> None:
        """Başarılı girişten sonra, veritabanı açıkken yapılan adımlar (kilitli veritabanı girişte açılır)."""
        db = self.ctx.db
        if db.uyari:              # ör. şifreye geçiş yapılamadı, farklı içerikli düz kasa.db kenara alındı
            self._mesaj(QMessageBox.Warning, db.uyari)
            db.uyari = ""
        if ILK_PERSONEL and not self.ctx.paths.demo:
            try:
                self.svc.ilk_personel_yukle(ILK_PERSONEL)
            except Exception:  # noqa: BLE001
                log.exception("İlk personel listesi yüklenemedi")
        if self.svc.is_admin and not db.has_recovery():
            self._sifreleme_kurtarma()
        if self.svc.is_admin:
            eksik = self.svc.kilitsiz_kullanicilar()
            if eksik:
                # kilidi paylaşanların şifresi sıfırlanamaz (services.py): onlar ayrı metinle, önce pasif yapılmak üzere
                paylasan = [ad for ad in self.svc.kilit_paylasanlar() if ad in eksik]
                tek = [ad for ad in eksik if ad not in paylasan]
                metin = [KILITSIZ_METNI.format(adlar=", ".join(tek))] if tek else []
                if paylasan:
                    metin.append(PAYLASAN_METNI.format(adlar=", ".join(paylasan)))
                self._mesaj(QMessageBox.Information, "\n\n".join(metin))
        try:
            self.ctx.backups.convert_legacy()
        except Exception:  # noqa: BLE001
            log.exception("Eski düz yedekler şifrelenemedi")

    def _sifreleme_kurtarma(self) -> None:
        """Şifreye geçişin (ya da kurtarma kilidi olmayan başka kurulumun yedeği geri yüklendikten sonra) bir
        kerelik adımı: veri anahtarı yeni bir kurtarma anahtarına kilitlenir (eski anahtarın özetinden kilit
        türetilemez), yeni anahtar bir kez gösterilir. Yeni anahtarın özeti ve veri
        anahtarının kilidi tek işlemde, aynı yazmada kaydedilir: biri başarısız olursa ikisi de geri alınır, eski
        anahtar geçerli kalır ve adım bir sonraki yönetici girişinde yeniden denenir."""
        from .ui.dialogs import RecoveryKeyDialog
        db = self.ctx.db
        try:
            with db.tx():
                key = self.svc.regenerate_recovery_key()
                if not db.has_recovery():           # services.py kilidi zaten bağladıysa yeniden bağlanmaz
                    db.set_recovery_key(key)
        except Exception:  # noqa: BLE001
            log.exception("Kurtarma anahtarı şifrelemeye bağlanamadı (eski anahtar geçerli; sonraki girişte "
                          "yeniden denenecek)")
            return
        RecoveryKeyDialog(None, key, SIFRELEME_KURTARMA_METNI).exec()

    def _exit_backup(self) -> None:
        try:
            self.ctx.backups.backup("cikis")
        except Exception:  # noqa: BLE001
            log.exception("Çıkış yedeği alınamadı")

    def on_main_closing(self) -> None:
        """Pencere kapatılırken (program kapanışı)."""
        if self.main:
            self.main.save_geometry()
        if self.svc.user:
            self._exit_backup()
            self.svc.logout()

    def logout(self, after_restore: bool = False) -> None:
        win = self.main
        if win is None:
            return
        win.save_geometry()
        if not after_restore:
            self._exit_backup()
        self.svc.logout()
        win._logging_out = True
        win.close()
        win.deleteLater()
        self.ctx.main = None
        self.main = None
        if not self.start():
            self.app.quit()


def selftest(argv) -> int:
    """Paketlenmiş programın kendini doğrulaması: geçici klasörde örnek veri, PDF, Excel, yedek ve
    tüm ekranlar. Başarılıysa 0 döner. Kullanım: KasaTakip.exe --selftest"""
    import shutil
    import tempfile
    import os
    tmp = tempfile.mkdtemp(prefix="kasatakip_selftest_")
    os.environ["KASATAKIP_DATA"] = tmp
    result = []
    code = 1
    app = None
    try:
        paths = AppPaths()
        paths.ensure()
        app = QApplication.instance() or QApplication(argv)
        app.setStyle("Fusion")
        from . import demo as demo_data
        from . import excel, reports_pdf
        from .backup import validate_backup
        from .ui.context import AppContext
        from .ui.main_window import MainWindow, apply_app_style
        apply_app_style(app, "normal")
        db = Database(paths.db_path)
        svc = Service(db)
        demo_data.populate(svc, days=20)
        result.append(f"hareket: {len(svc.movements())}")
        rep = svc.day_report(svc.data_range()[1])
        pdf = reports_pdf.daily_kasa_pdf(paths.default_report_dir / "selftest.pdf", rep, svc.firma(), "test")
        assert pdf.stat().st_size > 2000, "PDF boş"
        result.append(f"pdf: {pdf.stat().st_size} bayt, yazi tipi {reports_pdf._fonts()[0]}")
        xl = excel.export_sheets(paths.default_report_dir / "selftest.xlsx", svc.export_tables())
        assert xl.stat().st_size > 2000, "Excel boş"
        result.append("excel: tamam")
        bm = BackupManager(db, svc, paths.backup_dir)
        b = bm.backup("manuel")
        assert validate_backup(b.path, db.anahtar)[0], "yedek doğrulanamadı"
        result.append("yedek: tamam")
        with open(paths.db_path, "rb") as f:
            assert not f.read(16).startswith(b"SQLite format 3"), "canlı veritabanı şifresiz"
        assert b.path.suffix == ".kdb" and validate_backup(b.path, db.anahtar)[2].get("sifreli"), "yedek şifresiz"
        result.append("sifreleme: tamam")
        db2 = Database(paths.db_path)            # yeniden açılan canlı dosya kilitli; yalnızca program şifresi açar
        try:
            assert db2.kilitli and db2.get_setting("son_kullanici") == "admin", "yeniden açılan dosya kilitli değil"
            try:
                db2.ac("admin", "yanlis")
                raise AssertionError("yanlış şifre veritabanını açtı")
            except sifreleme.AnahtarHatali:
                pass
            assert db2.kilitli, "yanlış şifreden sonra kilit açık"
            db2.ac("admin", "admin")
            assert not db2.kilitli and db2.scalar("SELECT COUNT(*) FROM hareketler") == len(svc.movements()), \
                "doğru şifreyle açılan veri farklı"
        finally:
            db2.close()
        result.append("kilit: tamam (yanlış şifre açmıyor, doğru şifre açıyor)")
        from . import reports as reps
        for rd in reps.REPORTS:
            v = reps.default_values(rd, svc)
            rp = reps.write_pdf(rd.key, svc, v, paths.default_report_dir / f"st_{rd.key}.pdf", svc.firma(), "test")
            xp = reps.write_excel(reps.build(rd.key, svc, v), paths.default_report_dir / f"st_{rd.key}.xlsx")
            assert rp.stat().st_size > 2000 and xp.stat().st_size > 2000, f"rapor boş: {rd.key}"
        result.append(f"raporlar (PDF + Excel): {len(reps.REPORTS)}")

        class _Ctrl:
            def logout(self, after_restore=False):
                pass

            def on_main_closing(self):
                pass

        ctx = AppContext(svc=svc, db=db, paths=paths, backups=bm)
        win = MainWindow(ctx, _Ctrl())
        for key in list(win.pages):
            win.navigate(key)
        ap = win.pages["analiz"]
        win.navigate("analiz")
        for i in range(len(ap.tabs)):
            ap.tabbar.setCurrentIndex(i)
        assert not any(t.dirty for t in ap.tabs), "analiz sekmeleri yüklenemedi"
        win.apply_font_size("buyuk")
        win.apply_font_size("normal")
        assert resource_path("assets", "icons", "chevron-down.svg").exists(), "simgeler eksik"
        result.append(f"ekranlar: {len(win.pages)} · analiz sekmeleri: {len(ap.tabs)}")
        tr_ok = QTranslator().load(QLocale(QLocale.Turkish), "qtbase", "_",
                                   QLibraryInfo.path(QLibraryInfo.TranslationsPath))
        result.append(f"qt turkce ceviri: {'var' if tr_ok else 'yok'}")
        import socket
        try:
            socket.create_connection(("1.1.1.1", 53), timeout=1).close()
            raise AssertionError("ağ erişimi kapatılamadı")
        except netguard.AgErisimiKapali:
            result.append("ag erisimi: kapali")
        win._logging_out = True
        win.close()
        db.close()
        code = 0
    except Exception as e:  # noqa: BLE001
        result.append("HATA: " + "".join(traceback.format_exception(type(e), e, e.__traceback__)))
    text = ("SELFTEST " + ("OK" if code == 0 else "HATA") + "\n" + "\n".join(result))
    print(text)
    try:
        from pathlib import Path
        (Path.cwd() / "selftest_sonuc.txt").write_text(text, encoding="utf-8")
    except OSError:
        pass
    shutil.rmtree(tmp, ignore_errors=True)
    return code


def _acilis_ekrani(app: QApplication):
    """Açılış ekranını gösterir; (ekran, başlangıç zamanı) döner."""
    from .ui.brand import splash_pixmap
    sp = QSplashScreen(splash_pixmap(__version__), Qt.WindowStaysOnTopHint)
    sp.show()
    sp.showMessage("Yükleniyor…", Qt.AlignBottom | Qt.AlignHCenter, QColor("#6B7694"))
    app.processEvents()
    return sp, time.monotonic()


def _acilis_ekrani_kapat(sp, t0: float, en_az: float = 1.6) -> None:
    """Ekranı en az `en_az` saniye açık tutup kapatır."""
    kalan = int((en_az - (time.monotonic() - t0)) * 1000)
    if kalan > 0:
        loop = QEventLoop()
        QTimer.singleShot(kalan, loop.quit)
        loop.exec()
    sp.close()


def main(argv=None) -> int:
    netguard.install()
    argv = list(sys.argv if argv is None else argv)
    if "--selftest" in argv:
        return selftest(argv)
    demo = "--demo" in argv
    paths = AppPaths(demo=demo)
    paths.ensure()
    _setup_logging(paths)
    log.info("%s %s başlatılıyor (demo=%s)", APP_TITLE, __version__, demo)

    QLocale.setDefault(QLocale(QLocale.Turkish, QLocale.Turkey))
    app = QApplication(argv)
    app.setApplicationName(APP_NAME + ("Demo" if demo else ""))
    app.setApplicationDisplayName(APP_TITLE)
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(resource_path("assets", "app.png"))))
    tr = QTranslator(app)
    if tr.load(QLocale(QLocale.Turkish), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(tr)
    _install_excepthook()

    lock = QLockFile(str(paths.data_dir / "kasatakip.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(200):
        mb = QMessageBox(QMessageBox.Information, APP_TITLE,
                         "Kasa Takip zaten açık. Görev çubuğundaki pencereyi kullanın.")
        mb.addButton("Tamam", QMessageBox.AcceptRole)
        mb.exec()
        return 0

    splash, t0 = _acilis_ekrani(app)
    try:
        db = Database(paths.db_path)        # var olan şifreli dosya kilitli açılır; girişte şifreyle açılır
    except Exception as e:  # noqa: BLE001
        log.exception("Veritabanı açılamadı")
        _acilis_ekrani_kapat(splash, t0, 0)
        mb = QMessageBox(QMessageBox.Critical, APP_TITLE,
                         f"Veritabanı açılamadı:\n{paths.db_path}\n\n{e}\n\nYedekleme klasöründeki son yedeği "
                         "kullanmak için destek alın.")
        mb.addButton("Tamam", QMessageBox.AcceptRole)
        mb.exec()
        return 1
    svc = Service(db)
    if demo and not svc.users_exist():
        from . import demo as demo_data
        demo_data.populate(svc, days=420)          # ~14 ay: yıllık karşılaştırma da dolu görünsün
        svc.set_setting("son_kullanici", "admin")
        svc.user = None

    from .ui.context import AppContext
    backups = BackupManager(db, svc, paths.backup_dir)
    ctx = AppContext(svc=svc, db=db, paths=paths, backups=backups)
    ctrl = AppController(app, ctx)
    _acilis_ekrani_kapat(splash, t0)
    if not ctrl.start():
        db.close()
        return 0
    app.setQuitOnLastWindowClosed(True)
    code = app.exec()
    db.close()
    lock.unlock()
    log.info("Program kapandı")
    return code
