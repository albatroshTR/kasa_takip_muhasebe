"""Arayüz duman testi: tüm ekranları ve pencereleri ekransız (offscreen) açıp görüntülerini kaydeder.
Çalıştırma:  python -m tests.gui_smoke <çıktı_klasörü>"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TMP = tempfile.mkdtemp(prefix="kasatakip_gui_")
os.environ["KASATAKIP_DATA"] = TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from kasatakip import demo  # noqa: E402
from kasatakip.backup import BackupManager  # noqa: E402
from kasatakip.db import Database  # noqa: E402
from kasatakip.paths import AppPaths  # noqa: E402
from kasatakip.services import Service  # noqa: E402
from kasatakip.util import today  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "shots")
OUT.mkdir(parents=True, exist_ok=True)


class DummyController:
    def logout(self, after_restore=False):
        print("logout çağrıldı", after_restore)

    def on_main_closing(self):
        pass


def shot(w, name):
    for _ in range(4):
        QApplication.processEvents()
    w.grab().save(str(OUT / f"{name}.png"))
    print("görüntü:", name)


def main():
    paths = AppPaths()
    paths.ensure()
    db = Database(paths.db_path)
    svc = Service(db)
    demo.populate(svc)
    svc.user = None
    svc.login("admin", "admin")

    from PySide6.QtCore import QLocale
    QLocale.setDefault(QLocale(QLocale.Turkish, QLocale.Turkey))
    app = QApplication([])
    app.setStyle("Fusion")
    from kasatakip.ui.context import AppContext, daily_pdf
    from kasatakip.ui.dialogs import (CariDialog, CloseDayDialog, HelpDialog, MovementDialog, RecoveryKeyDialog,
                                      UserDialog)
    from kasatakip.ui.login import LoginDialog
    from kasatakip.ui.main_window import MainWindow, apply_app_style
    from kasatakip.ui.paste_dialog import PasteImportDialog

    ctx = AppContext(svc=svc, db=db, paths=paths, backups=BackupManager(db, svc, paths.backup_dir))
    apply_app_style(app, "normal")
    ctx.backups.backup("acilis")
    win = MainWindow(ctx, DummyController())
    win.resize(1440, 900)
    win.show()
    for key in win.pages:
        win.navigate(key)
        if key == "cariler":
            win.pages[key].list.selectRow(0)
        shot(win, f"sayfa_{key}")

    # analiz sekmeleri ve raporlar
    win.navigate("analiz")
    ap = win.pages["analiz"]
    for i, t in enumerate(ap.tabs):
        ap.tabbar.setCurrentIndex(i)
        shot(win, f"analiz_{i}")
    win.navigate("raporlar")
    rp = win.pages["raporlar"]
    for key in ("kasa_defteri", "yaslandirma", "aylik"):
        rp.select_report(key)
        shot(win, f"rapor_{key}")

    # kasa: açık gün
    win.navigate("kasa")
    win.pages["kasa"].set_date(today())
    shot(win, "sayfa_kasa_bugun")

    # pencereler
    d = MovementDialog(ctx, win, "GELIR")
    d.show()
    shot(d, "pencere_gelir")
    d.seg.set_value("VIRMAN", emit=True)
    shot(d, "pencere_virman")
    d.seg.set_value("CARI_BORC", emit=True)
    shot(d, "pencere_cari_borc")
    d.close()
    m = svc.movements(start=today() - dt.timedelta(days=5), end=today(), tur="GIDER")[0]
    d = MovementDialog(ctx, win, movement=svc.get_movement(m["id"]))
    d.show()
    shot(d, "pencere_duzenle")
    d.close()
    d = CariDialog(ctx, win)
    d.show()
    shot(d, "pencere_yeni_cari")
    d.close()
    rep = svc.day_report(today())
    d = CloseDayDialog(ctx, win, rep)
    d.show()
    list(d.edits.values())[0][1].setText("1.000,00")
    shot(d, "pencere_gun_kapat")
    d.close()
    text = ("Tarih\tTür\tKategori\tÖdeme\tTutar\tAçıklama\n"
            f"{today():%d.%m.%Y}\tGelir\tKargo Geliri\tNakit\t1.250,50\tGişe\n"
            f"{today():%d.%m.%Y}\tGider\tYakıt\tNakit\t300\tMazot\n"
            f"{today():%d.%m.%Y}\tGider\tBilinmeyen\tNakit\t50\t\n")
    QGuiApplication.clipboard().setText(text)
    d = PasteImportDialog(ctx, win, "hareket", today(), text=QGuiApplication.clipboard().text())
    d.show()
    shot(d, "pencere_yapistir")
    d.close()
    d = UserDialog(ctx, win)
    d.show()
    shot(d, "pencere_kullanici")
    d.close()
    d = RecoveryKeyDialog(win, "ABCD-EFGH-JKLM-NPQR")
    d.show()
    shot(d, "pencere_kurtarma")
    d.chk.setChecked(True)
    d.close()
    d = HelpDialog(win)
    d.show()
    shot(d, "pencere_yardim")
    d.close()
    d = LoginDialog(ctx)
    d.show()
    shot(d, "pencere_giris")
    d.close()

    # PDF ve kopyalama
    p = daily_pdf(ctx, win, today() - dt.timedelta(days=2), open_after=False)
    print("pdf:", p)
    t = win.pages["hareketler"].table
    win.navigate("hareketler")
    t.selectRow(0)
    t.selectRow(1)
    n = t.copy_selection(headers=True)
    print("kopyalanan satır:", n, "| pano:", QGuiApplication.clipboard().text().splitlines()[:2])

    # büyük yazı
    win.apply_font_size("buyuk")
    win.navigate("kasa")
    shot(win, "sayfa_kasa_buyuk_yazi")
    win.apply_font_size("normal")

    # kullanıcı rolüyle menü
    svc.logout()
    svc.login("kasiyer", "1234")
    win2 = MainWindow(ctx, DummyController())
    win2.resize(1440, 900)
    win2.show()
    shot(win2, "kasiyer_ana_sayfa")
    print("kasiyer sayfaları:", list(win2.pages))
    QTimer.singleShot(0, app.quit)
    app.exec()
    print("TAMAM")


if __name__ == "__main__":
    main()
