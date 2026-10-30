"""Program sahibi (sahip hesabı) kuralları.  Çalıştırma:  python -m tests.test_sahip"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kasatakip import auth  # noqa: E402
from kasatakip.db import Database  # noqa: E402
from kasatakip.errors import PermissionDenied, ServiceError  # noqa: E402
from kasatakip.services import SAHIP_AYARI, Service  # noqa: E402

PASS = 0


def ok(cond, msg):
    global PASS
    if not cond:
        raise AssertionError(msg)
    PASS += 1


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc as e:
        return str(e)
    raise AssertionError(f"{fn.__name__} beklenen hatayı vermedi: {exc.__name__}")


def kur(path: Path):
    """Sahip 'admin' (1234), ikinci yönetici 'mudur' (5555), kullanıcı 'kasiyer' (6666)."""
    db = Database(path)
    svc = Service(db)
    svc.setup_first_admin("admin", "Sahip", "1234", "Test Kargo")
    ids = {"admin": svc.user["id"]}
    ids["mudur"] = svc.create_user("mudur", "Müdür", "5555", "yonetici")
    ids["kasiyer"] = svc.create_user("kasiyer", "Kasiyer", "6666", "kullanici")
    return db, svc, ids


def kullanici(svc, uid) -> dict:
    return dict(svc.db.q1("SELECT * FROM kullanicilar WHERE id=?", (uid,)))


def test_kurulum_sahibi(tmp: Path):
    db, svc, ids = kur(tmp / "kasa.kdb")
    ok(svc.setting(SAHIP_AYARI) == str(ids["admin"]), "kurulum yöneticisi ayara yazıldı")
    ok(svc.sahip_id() == ids["admin"] and svc.is_sahip, "kurulum yöneticisi sahip")
    rows = {r["kullanici_adi"]: r for r in svc.list_users()}
    ok(rows["admin"]["sahip"] and not rows["mudur"]["sahip"], "listede yalnız sahip işaretli")
    db.close()


def test_ayarsiz_veritabani(tmp: Path):
    db, svc, ids = kur(tmp / "kasa.kdb")
    db.x("DELETE FROM ayarlar WHERE anahtar=?", (SAHIP_AYARI,))
    svc.logout()
    ok(svc.sahip_id() == ids["admin"], "ayar yoksa en küçük id'li aktif yönetici sahip")
    ok(svc.setting(SAHIP_AYARI) == "", "oturum yokken ayara yazılmadı")
    db.x("UPDATE kullanicilar SET aktif=0 WHERE id=?", (ids["admin"],))
    ok(svc.sahip_id() == ids["mudur"], "pasif yönetici atlanır: sıradaki aktif yönetici sahip")
    db.x("UPDATE kullanicilar SET aktif=1 WHERE id=?", (ids["admin"],))
    svc.login("mudur", "5555")
    ok(svc.sahip_id() == ids["admin"] and not svc.is_sahip, "giriş yapan ikinci yönetici sahip değil")
    ok(svc.setting(SAHIP_AYARI) == str(ids["admin"]), "çözülen sahip yönetici oturumunda ayara yazıldı")
    db.close()


def test_ikinci_yonetici(tmp: Path):
    db, svc, ids = kur(tmp / "kasa.kdb")
    svc.logout()
    svc.login("mudur", "5555")
    sahip = ids["admin"]
    ok("sahib" in raises(PermissionDenied, svc.update_user, sahip, "Sahip", "yonetici", False), "sahibi pasif yapamaz")
    raises(PermissionDenied, svc.update_user, sahip, "Sahip", "kullanici", True)
    raises(PermissionDenied, svc.update_user, sahip, "Başka Ad", "yonetici", True)
    raises(PermissionDenied, svc.reset_password, sahip, "9999")
    u = kullanici(svc, sahip)
    ok(u["aktif"] == 1 and u["rol"] == "yonetici" and u["ad_soyad"] == "Sahip", "sahip hesabı değişmedi")
    ok(not u["sifre_degistirmeli"], "sahibin şifresi sıfırlanmadı")
    svc.reset_password(ids["kasiyer"], "7777")         # diğer kullanıcılar için yetki değişmedi
    svc.update_user(ids["kasiyer"], "Kasiyer 2", "kullanici", True)
    ok(kullanici(svc, ids["kasiyer"])["ad_soyad"] == "Kasiyer 2", "ikinci yönetici diğer hesabı düzenler")
    raises(PermissionDenied, svc.sahipligi_devret, ids["mudur"])
    db.close()


def test_sahip_kendini(tmp: Path):
    db, svc, ids = kur(tmp / "kasa.kdb")
    sahip = ids["admin"]
    ok("devredin" in raises(ServiceError, svc.update_user, sahip, "Sahip", "kullanici", True), "sahip kendini düşüremez")
    raises(ServiceError, svc.update_user, sahip, "Sahip", "yonetici", False)
    svc.update_user(sahip, "Sahip Bey", "yonetici", True)
    ok(kullanici(svc, sahip)["ad_soyad"] == "Sahip Bey", "sahip kendi adını değiştirir")
    svc.reset_password(sahip, "4321")
    svc.update_user(ids["mudur"], "Müdür", "yonetici", False)      # sahip başka yöneticiyi pasif yapabilir
    ok(not kullanici(svc, ids["mudur"])["aktif"], "sahip diğer yöneticiyi pasif yaptı")
    db.close()


def test_kurtarma_anahtari(tmp: Path):
    db, svc, ids = kur(tmp / "kasa.kdb")
    k1 = svc.regenerate_recovery_key()
    ok(len(k1) == 19, "sahip yeni kurtarma anahtarı oluşturur")
    svc.logout()
    svc.login("mudur", "5555")
    ok("sahibi" in raises(PermissionDenied, svc.regenerate_recovery_key), "ikinci yönetici anahtar oluşturamaz")
    svc.logout()
    svc.login("kasiyer", "6666")
    raises(PermissionDenied, svc.regenerate_recovery_key)
    svc.logout()
    # kurtarma anahtarıyla sıfırlama değişmedi: anahtarı bilen ikinci yöneticinin şifresini de sıfırlar
    k2 = svc.reset_with_recovery_key(k1, "mudur", "5556")
    ok(k2 and k2 != k1, "kurtarma anahtarıyla ikinci yöneticinin şifresi sıfırlandı")
    svc.login("mudur", "5556")
    ok(not svc.is_sahip, "sıfırlama sahipliği değiştirmedi")
    db.close()


def test_devir(tmp: Path):
    db, svc, ids = kur(tmp / "kasa.kdb")
    ok("aktif bir yönetici" in raises(ServiceError, svc.sahipligi_devret, ids["kasiyer"]), "kullanıcıya devredilmez")
    raises(ServiceError, svc.sahipligi_devret, ids["admin"])
    raises(ServiceError, svc.sahipligi_devret, 9999)
    svc.sahipligi_devret(ids["mudur"])
    ok(svc.sahip_id() == ids["mudur"] and not svc.is_sahip, "sahiplik devredildi")
    kayit = db.q1("SELECT * FROM islem_gecmisi ORDER BY id DESC LIMIT 1")
    ok(kayit["kayit_id"] == ids["mudur"] and "devredildi" in kayit["detay"] and kayit["kullanici_adi"] == "admin",
       "devir işlem geçmişinde")
    raises(PermissionDenied, svc.sahipligi_devret, ids["admin"])
    raises(PermissionDenied, svc.regenerate_recovery_key)
    svc.logout()
    svc.login("mudur", "5555")
    ok(svc.is_sahip, "yeni sahip giriş yaptı")
    svc.regenerate_recovery_key()
    svc.update_user(ids["admin"], "Eski Sahip", "kullanici", True)     # eski sahip artık sıradan hesap
    ok(kullanici(svc, ids["admin"])["rol"] == "kullanici", "yeni sahip eski sahibin rolünü değiştirebildi")
    raises(ServiceError, svc.update_user, ids["mudur"], "Müdür", "yonetici", False)
    db.close()


def test_yeniden_acma(tmp: Path):
    path = tmp / "kasa.kdb"
    db, svc, ids = kur(path)
    ok(db.sifreli, "şifreli veritabanı")
    db.close()
    db = Database(path)
    ok(db.kilitli, "yeniden açılınca kilitli")
    svc = Service(db)
    svc.login("mudur", "5555")
    ok(svc.sahip_id() == ids["admin"], "sahip yeniden açınca korunur")
    raises(PermissionDenied, svc.update_user, ids["admin"], "Sahip", "yonetici", False)
    raises(PermissionDenied, svc.reset_password, ids["admin"], "9999")
    raises(PermissionDenied, svc.regenerate_recovery_key)
    svc.logout()
    svc.login("admin", "1234")
    ok(svc.is_sahip, "sahip yeniden açınca sahip")
    raises(ServiceError, svc.update_user, ids["admin"], "Sahip", "yonetici", False)
    svc.sahipligi_devret(ids["mudur"])
    db.close()
    db = Database(path)
    svc = Service(db)
    svc.login("admin", "1234")
    ok(svc.sahip_id() == ids["mudur"] and not svc.is_sahip, "devir yeniden açınca korunur")
    raises(PermissionDenied, svc.reset_password, ids["mudur"], "9999")
    db.close()


def test_gecis_ikinci_yonetici(tmp: Path):
    """1.1.x düz veritabanı, önce ikinci yönetici girer: şifreye geçişte yalnız onun kilidi yazılır. Sahibin
    kilidi olmadığı için (yalnızca bu durumda) ikinci yönetici sahibin şifresini sıfırlayabilir."""
    from tests.test_sifreleme import _duz_kopya
    legacy, _ = _duz_kopya(tmp)
    con = sqlite3.connect(str(legacy))
    con.execute("INSERT INTO kullanicilar(kullanici_adi, ad_soyad, sifre_hash, rol, olusturma) "
                "VALUES('mudur','Müdür',?,'yonetici','2026-09-01 09:00:00')", (auth.hash_password("5555"),))
    con.commit()
    con.close()
    db = Database(legacy.with_suffix(".kdb"))
    svc = Service(db)
    svc.login("mudur", "5555")
    ok(db.sifreli, "geçiş yapıldı")
    sahip = db.scalar("SELECT id FROM kullanicilar WHERE kullanici_adi='admin'")
    ok(svc.sahip_id() == sahip and not svc.is_sahip, "eski veritabanında sahip en küçük id'li yönetici")
    ok("admin" in svc.kilitsiz_kullanicilar() and svc.sahip_kilitsiz(), "sahibin şifreli dosyada kilidi yok")
    raises(PermissionDenied, svc.update_user, sahip, "Sahip", "yonetici", False)
    svc.reset_password(sahip, "9999")
    ok(not svc.sahip_kilitsiz() and "admin" not in svc.kilitsiz_kullanicilar(), "sıfırlamayla sahibin kilidi yazıldı")
    kayit = db.q1("SELECT * FROM islem_gecmisi ORDER BY id DESC LIMIT 1")
    ok(kayit["kayit_id"] == sahip and "kilidi yoktu" in kayit["detay"], "istisnai sıfırlama işlem geçmişinde")
    raises(PermissionDenied, svc.reset_password, sahip, "8888")      # kilidi olunca kural yeniden geçerli
    path = db.path
    db.close()
    db = Database(path)
    svc = Service(db)
    svc.login("admin", "9999")
    ok(svc.is_sahip and svc.user["sifre_degistirmeli"], "sahip yeniden açınca yeni şifresiyle girer")
    db.close()


if __name__ == "__main__":
    for fn in (test_kurulum_sahibi, test_ayarsiz_veritabani, test_ikinci_yonetici, test_sahip_kendini,
               test_kurtarma_anahtari, test_devir, test_yeniden_acma, test_gecis_ikinci_yonetici):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            fn(Path(t))
    print(f"TAMAM: {PASS} kontrol geçti")
