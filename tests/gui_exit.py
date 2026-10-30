"""Kapanış ve pencere ömrü testi: gerçek main() akışında pencereler açılıp kapatılır, program kapanır;
alt süreç çıkış kodu 0 olmalı (kapanışta çökme yok). Rapor önizlemesinden kayıt açıp içinden yeni cari
eklemek de denenir (silinen tabloya dönüş çökmesi).  Çalıştırma:  python -m tests.gui_exit"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHILD = r'''
import os, sys, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["KASATAKIP_DATA"] = tempfile.mkdtemp(prefix="kt_exit_")
sys.path.insert(0, ROOT)
mode = sys.argv[1]
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog
from kasatakip import app as appmod
from kasatakip.db import Database
from kasatakip.paths import AppPaths
from kasatakip.services import Service
paths = AppPaths(); paths.ensure()
db = Database(paths.db_path); svc = Service(db)
svc.setup_first_admin("admin", "Yönetici", "1234", "Firma")
cat = [c for c in svc.categories("GELIR")][0]["id"]
for i in range(1, 6):
    import datetime as dt
    svc.add_movement({"tarih": dt.date.today() - dt.timedelta(days=i), "tur": "GELIR", "kategori_id": cat,
                      "odeme_tipi_id": svc.payment_types()[0]["id"], "tutar": 10000 * i})
db.close()
from kasatakip.ui import dialogs, login
import kasatakip.ui.widgets as widgets
import kasatakip.ui.context as context
context.open_path = lambda p: None
widgets._box = lambda parent, icon, title, text, buttons: buttons[0][2]
REAL = QDialog.exec
def fake_exec(self):
    if isinstance(self, login.LoginDialog):
        self.user.setText("admin"); self.pw.setText("1234"); self._login()
        return self.result()
    if isinstance(self, dialogs.RecoveryKeyDialog):      # şifreye geçişte bir kez gösterilen yeni anahtar
        self.chk.setChecked(True)
        QTimer.singleShot(30, self.accept)
        return REAL(self)
    if isinstance(self, dialogs.CariDialog):
        def save():
            self.unvan.setText("Yeni Cari " + str(id(self))); self._save()
        QTimer.singleShot(30, save)
        return REAL(self)
    if isinstance(self, dialogs.MovementDialog):
        if "cari" in mode:
            QTimer.singleShot(30, lambda: self._new_cari())
        def save():
            self.tutar.setText("100")
            self._save(False)
        QTimer.singleShot(250, save)
        QTimer.singleShot(500, self.reject)        # kaydedilemezse de pencere kapansın
        return REAL(self)
    QTimer.singleShot(30, self.reject)
    return REAL(self)
QDialog.exec = fake_exec
def scenario():
    main = [x for x in QApplication.topLevelWidgets() if type(x).__name__ == "MainWindow"][0]
    if mode == "hareket_cari":
        main._new("GELIR")
    elif mode == "pencereler":
        dialogs.UserDialog(main.ctx, main).exec()
        dialogs.CariDialog(main.ctx, main).exec()
        main._help()
    elif mode == "rapor_cari":
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from kasatakip.ui.widgets import DataTable
        main.navigate("raporlar")
        rp = main.pages["raporlar"]
        rp.select_report("hareket_dokumu")
        QApplication.processEvents()
        t = [x for x in rp.view.findChildren(DataTable) if x.isVisible()][-1]
        rect = t.visualRect(t.model().index(0, 0))
        QTest.mouseClick(t.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
        QTest.mouseDClick(t.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
        for _ in range(30):
            QApplication.processEvents()
            QTest.qWait(20)
    QTimer.singleShot(150, main.close)
_orig = appmod.AppController.start
def _start(self):
    ok = _orig(self)
    QTimer.singleShot(200, scenario)
    return ok
appmod.AppController.start = _start
sys.exit(appmod.main(["KasaTakip"]))
'''.replace("ROOT", repr(str(ROOT)))


def main() -> None:
    checks = 0
    for mode in ("bos", "hareket_cari", "pencereler", "rapor_cari"):
        for rep in range(3):
            r = subprocess.run([sys.executable, "-c", CHILD, mode], capture_output=True, text=True, timeout=120,
                               env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
            if r.returncode != 0:
                print(r.stdout[-2000:], r.stderr[-4000:])
                raise AssertionError(f"{mode}: program kapanışta hata verdi (çıkış kodu {r.returncode})")
            checks += 1
        print(f"  ✓ {mode}: 3 çalıştırmada temiz kapanış")
    print(f"\nTAMAM: {checks} kontrol geçti")


if __name__ == "__main__":
    main()
