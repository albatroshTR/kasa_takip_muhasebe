"""Geçiş beklerken ve kilitliyken kurtarma anahtarıyla şifre sıfırlama testleri.  Çalıştırma:  python -m tests.test_gecis"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("KASATAKIP_DATA", tempfile.mkdtemp(prefix="kt_gecis_"))   # gerçek veriye dokunulmaz
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kasatakip import auth  # noqa: E402
from kasatakip.db import Database  # noqa: E402
from kasatakip.errors import AuthError  # noqa: E402
from kasatakip.services import Service  # noqa: E402
from tests import test_sifreleme as ts  # noqa: E402

PASS = 0
ESKI_ANAHTAR = "WXYZ-WXYZ-WXYZ-WXYZ"        # _duz_kopya'nın 1.1.x kurtarma anahtarı


def ok(cond, msg):
    global PASS
    if not cond:
        raise AssertionError(msg)
    PASS += 1


raises = ts.raises


def test_yanlis_anahtar_gecis(tmp: Path):
    legacy, _ = ts._duz_kopya(tmp)
    raw = legacy.read_bytes()
    db = Database(legacy.with_suffix(".kdb"))
    ok(db.gecis_bekliyor, "geçiş bekliyor")
    svc = Service(db)
    ok(raises(AuthError, svc.reset_with_recovery_key, "ZZZZ-ZZZZ-ZZZZ-ZZZZ", "admin", "yeni1")
       == "Kurtarma anahtarı hatalı.", "yanlış anahtar reddedildi")
    ok(legacy.read_bytes() == raw and not legacy.with_name("kasa.db-journal").exists(),
       "yanlış anahtar denemesi düz dosyaya yazılmadı")
    ok(db.gecis_bekliyor and not db.sifreli, "geçiş hâlâ bekliyor")
    db.close()
    ok(legacy.read_bytes() == raw, "kapatınca da düz dosya aynı")


def test_dogru_anahtar_gecis(tmp: Path):
    legacy, _ = ts._duz_kopya(tmp)
    db = Database(legacy.with_suffix(".kdb"))
    svc = Service(db)
    eski_hash = svc.setting("kurtarma_hash")
    ok(svc.reset_with_recovery_key(ESKI_ANAHTAR, "admin", "yeni1") is None, "geçiş beklerken yeni anahtar yok")
    ok(svc.setting("kurtarma_hash") == eski_hash, "saklı kurtarma özeti değişmedi (ikinci anahtar üretilmedi)")
    ok(db.gecis_bekliyor and not db.sifreli, "sıfırlama geçişi yapmadı")
    raises(AuthError, svc.login, "admin", "1234")
    svc.login("admin", "yeni1")
    ok(db.sifreli and not db.gecis_bekliyor, "yeni şifreyle giriş geçişi yaptı")
    ok(not db.has_recovery(), "geçişten hemen sonra kurtarma kilidi yok (app.py tek anahtarı gösterir)")
    ok(auth.verify_password(auth.normalize_key(ESKI_ANAHTAR), svc.setting("kurtarma_hash")),
       "sıfırlama kendi anahtarını yazmadı")
    yeni = svc.regenerate_recovery_key()          # app.py'nin girişten sonraki adımı
    ok(db.has_recovery(), "app.py adımından sonra kurtarma kilidi var")
    path = db.path
    db.close()
    ok(not ts.ac(path, "admin", "yeni1").kilitli, "yeni şifre şifreli dosyayı açıyor")
    d = Database(path)
    d.ac_kurtarma(yeni)
    ok(not d.kilitli, "app.py'nin gösterdiği anahtar açıyor")
    d.close()


def test_kilitli_ozet_uyusmuyor(tmp: Path):
    path = tmp / "kasa.kdb"
    db, svc, key = ts.kur(path)
    svc.set_setting("kurtarma_hash", auth.hash_password(auth.normalize_key("ZZZZ-ZZZZ-ZZZZ-ZZZZ")))
    db.close()
    db = Database(path)
    ok(db.kilitli, "kilitli açıldı")
    svc = Service(db)
    raises(AuthError, svc.reset_with_recovery_key, "QQQQ-QQQQ-QQQQ-QQQQ", "admin", "yeni1")
    ok(db.kilitli, "yanlış anahtar açmadı")
    new_key = svc.reset_with_recovery_key(key, "admin", "yeni1")
    ok(new_key and not db.kilitli, "özet uyuşmasa da kurtarma kilidini açan anahtar sıfırladı")
    ok(auth.verify_password(auth.normalize_key(new_key), svc.setting("kurtarma_hash")), "özet yeni anahtarın")
    db.close()
    d = ts.ac(path, "admin", "yeni1")
    ok(not d.kilitli, "yeniden açınca yeni şifre açıyor")
    Service(d).login("admin", "yeni1")
    d.close()
    d = Database(path)
    d.ac_kurtarma(new_key)
    ok(not d.kilitli, "yeni kurtarma anahtarı açıyor")
    d.close()


if __name__ == "__main__":
    import logging
    logging.getLogger("kasatakip").addHandler(logging.NullHandler())
    logging.getLogger("kasatakip").propagate = False
    for fn in (test_yanlis_anahtar_gecis, test_dogru_anahtar_gecis, test_kilitli_ozet_uyusmuyor):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            fn(Path(t))
    print(f"TAMAM: {PASS} kontrol geçti")
