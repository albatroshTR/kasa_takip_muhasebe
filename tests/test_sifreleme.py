"""Şifreli saklama testleri (canlı veritabanı, kilitler, yedekler, geçiş).  Çalıştırma:  python -m tests.test_sifreleme"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import struct
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kasatakip import auth, sifreleme  # noqa: E402
from kasatakip import db as dbmod  # noqa: E402
from kasatakip.backup import BackupManager, validate_backup  # noqa: E402
from kasatakip.db import GECIS_OZETI, KILITLI, Database, VeritabaniKilitli, tablo_sayilari  # noqa: E402
from kasatakip.errors import AuthError, ServiceError  # noqa: E402
from kasatakip.services import Service  # noqa: E402

PASS = 0
KEY = "ABCD-EFGH-JKLM-NPQR"
GIZLI = "Gizli Müşteri Zeynep Ticaret"
HATALI_GIRIS = "Kullanıcı adı veya şifre hatalı."


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


def kur(path: Path, recovery: bool = True, kullanici: str = "admin", sifre: str = "1234"):
    """Kurulmuş veritabanı. recovery=False: 1.1.x'ten gelen kurulum gibi kurtarma kilidi yok (girişten sonraki
    bir kerelik adım henüz yapılmamış)."""
    db = Database(path)
    svc = Service(db)
    key = svc.setup_first_admin(kullanici, "Yönetici", sifre, "Test Kargo")
    if not recovery:
        db.kurtarma_kilidini_kaldir()
    return db, svc, key


def ac(path: Path, kullanici: str = "admin", sifre: str = "1234") -> Database:
    """Yeniden açma yardımcısı: var olan şifreli dosya kilitli açılır, şifreyle açılır."""
    db = Database(path)
    db.ac(kullanici, sifre)
    return db


def baslik(path: Path) -> dict:
    return sifreleme.baslik_dosyadan(path)


def hareket(svc, tutar, aciklama=""):
    return svc.add_movement({"tarih": dt.date(2026, 10, 1), "tur": "GELIR", "tutar": tutar,
                             "kategori_id": svc.categories("GELIR")[0]["id"],
                             "odeme_tipi_id": svc.payment_types()[0]["id"], "aciklama": aciklama})


def test_kap():
    a = sifreleme.yeni_anahtar()
    a.kullanicilar["admin"] = sifreleme.sifre_sar(a, "1234")
    veri = b"SQLite format 3\x00" + os.urandom(5000)
    meta = {"son_kullanici": "admin", "firma_adi": "Test Kargo", "kurtarma_hash": "gizli özet", "yazi_boyutu": ""}
    blob = sifreleme.paketle(veri, a, meta)
    ok(sifreleme.sifreli_mi(blob) and not sifreleme.sqlite_mi(blob), "kap başlığı")
    ok(veri not in blob and veri[16:200] not in blob, "kapta düz veri yok")
    ok(sifreleme.paketle(veri, a, meta) != blob, "her yazmada yeni nonce")
    h = sifreleme.baslik_oku(blob)[0]
    ok("dpapi" not in h and "gecici" not in h and set(h["kullanicilar"]) == {"admin"}, "başlıkta DPAPI yok, kullanıcı kilidi var")
    ok(h["meta"] == {"son_kullanici": "admin", "firma_adi": "Test Kargo"}, "başlıkta yalnız giriş ekranı ayarları")
    ok(a.dek not in blob and sifreleme._b64(a.dek).encode() not in blob, "veri anahtarı başlıkta açıkta değil")
    raises(sifreleme.AnahtarGerekli, sifreleme.coz, blob)
    ok(raises(sifreleme.AnahtarHatali, sifreleme.coz, blob, kullanici="admin", sifre="yanlis") == HATALI_GIRIS,
       "yanlış şifre: girişle aynı metin")
    ok(raises(sifreleme.AnahtarHatali, sifreleme.coz, blob, kullanici="yok", sifre="1234") == HATALI_GIRIS,
       "olmayan kullanıcı: aynı metin")
    back, a2 = sifreleme.coz(blob, kullanici="admin", sifre="1234")
    ok(back == veri and a2.dek == a.dek and set(a2.kullanicilar) == {"admin"}, "şifreyle gidiş-dönüş")
    back, _ = sifreleme.coz(blob, a)
    ok(back == veri, "aynı kimlikli anahtarla doğrudan açılır")
    # kurtarma anahtarı kilidi
    raises(sifreleme.AnahtarHatali, sifreleme.coz, blob, kurtarma_anahtari=KEY)        # kilidi yok
    a.kurtarma = sifreleme.kurtarma_sar(a, KEY)
    blob = sifreleme.paketle(veri, a)
    raises(sifreleme.AnahtarHatali, sifreleme.coz, blob, kurtarma_anahtari="ABCD-EFGH-JKLM-NPQX")
    raises(sifreleme.AnahtarHatali, sifreleme.coz, blob, kurtarma_anahtari="")
    back, _ = sifreleme.coz(blob, kurtarma_anahtari="abcd efgh jklm npqr")
    ok(back == veri, "kurtarma anahtarıyla gidiş-dönüş (yazım biçimi önemsiz)")
    # kilit, saklanan şifre özetinden türetilmez: özeti bilen dosyayı açamaz
    from kasatakip import auth
    ozet = auth.hash_password("1234")
    raises(sifreleme.AnahtarHatali, sifreleme.coz, blob, kullanici="admin", sifre=ozet)
    # geçici kilit: yalnızca kullanıcı kilidi yokken yazılır
    g = sifreleme.yeni_anahtar(gecici=True)
    gb = sifreleme.paketle(veri, g)
    ok("gecici" in sifreleme.baslik_oku(gb)[0] and sifreleme.coz(gb)[0] == veri, "geçici kilitli dosya şifresiz açılır")
    g.kullanicilar["admin"] = sifreleme.sifre_sar(g, "1234")
    gb = sifreleme.paketle(veri, g)
    ok("gecici" not in sifreleme.baslik_oku(gb)[0] and sifreleme._b64(g.dek).encode() not in gb,
       "kullanıcı kilidi olan dosyaya geçici kilit yazılmaz")
    raises(sifreleme.AnahtarGerekli, sifreleme.coz, gb)
    # bozulmuş / değiştirilmiş dosya
    bad = bytearray(sifreleme.paketle(veri, a))
    good = bytes(bad)
    bad[-30] ^= 1
    raises(sifreleme.BozukDosya, sifreleme.coz, bytes(bad), a)
    n = struct.unpack(">I", good[12:16])[0]
    head = json.loads(good[16:16 + n])
    head["anahtar_id"] = head["anahtar_id"][::-1]
    hb = json.dumps(head, separators=(",", ":")).encode()
    raises(sifreleme.SifrelemeHatasi, sifreleme.coz, good[:12] + struct.pack(">I", len(hb)) + hb + good[16 + n:],
           kullanici="admin", sifre="1234")
    head = json.loads(good[16:16 + n])
    head["ek"] = 1                              # başlık AAD: alan eklemek de doğrulamayı bozar
    hb = json.dumps(head, separators=(",", ":")).encode()
    raises(sifreleme.BozukDosya, sifreleme.coz, good[:12] + struct.pack(">I", len(hb)) + hb + good[16 + n:], a)
    head = json.loads(good[16:16 + n])
    head["meta"] = {"firma_adi": "Sahte Firma"}  # giriş ekranı bilgisi de değiştirilemez
    hb = json.dumps(head, separators=(",", ":")).encode()
    raises(sifreleme.BozukDosya, sifreleme.coz, good[:12] + struct.pack(">I", len(hb)) + hb + good[16 + n:],
           kullanici="admin", sifre="1234")
    raises(sifreleme.BozukDosya, sifreleme.coz, good[:40])
    raises(sifreleme.BozukDosya, sifreleme.coz, b"SQLite format 3\x00abc")


def test_canli(tmp: Path):
    path = tmp / "kasa.db"                      # testlerin kullandığı ad: yoksa şifreli yeni veritabanı açılır
    db, svc, key = kur(path)
    svc.save_cari({"unvan": GIZLI, "tip": "MUSTERI"})
    hareket(svc, 123456, "Kasadan gizli açıklama")
    raw = path.read_bytes()
    ok(not raw.startswith(b"SQLite format 3"), "canlı dosyada SQLite başlığı yok")
    for s in (GIZLI, "Kasadan gizli açıklama", "hareketler", "kurtarma_hash", "pbkdf2"):
        ok(s.encode("utf-8") not in raw, f"canlı dosyada düz metin yok: {s}")
    ok(sorted(p.name for p in tmp.iterdir()) == ["kasa.db"], f"yanında geçici / günlük dosya yok {list(tmp.iterdir())}")
    h = baslik(path)
    ok(set(h["kullanicilar"]) == {"admin"} and "kurtarma" in h and "gecici" not in h and "dpapi" not in h,
       "başlıkta yönetici kilidi ve kurtarma kilidi var")
    ok(h["meta"] == {"son_kullanici": "admin", "firma_adi": "Test Kargo"}, f"başlıkta giriş ekranı bilgisi {h['meta']}")
    ok(db.has_recovery(), "kurtarma kilidi var")
    db.close()
    # kilitli açılış: giriş ekranı bilgileri okunur, veri okunamaz
    db = Database(path)
    svc = Service(db)
    ok(db.kilitli and db.conn is None, "yeniden açılınca kilitli")
    ok(svc.setting("firma_adi") == "Test Kargo" and svc.setting("son_kullanici") == "admin", "kilitliyken meta okunur")
    ok(svc.setting("yazi_boyutu", "normal") == "normal" and svc.setting("kurtarma_hash", "yok") == "yok",
       "kilitliyken diğer ayarlar varsayılan")
    ok(svc.users_exist(), "kilitliyken kullanıcı var (kilitlerden)")
    ok(raises(VeritabaniKilitli, svc.movements) == KILITLI, "kilitliyken sorgu açık bir hata verir")
    raises(VeritabaniKilitli, db.set_setting, "x", "1")
    raises(VeritabaniKilitli, db.goruntu)
    raises(ServiceError, svc.setup_first_admin, "biri", "", "1234")
    ok(raises(sifreleme.AnahtarHatali, db.ac, "admin", "yanlis") == HATALI_GIRIS and db.kilitli,
       "yanlış şifre açmadı (girişle aynı metin)")
    ok(raises(sifreleme.AnahtarHatali, db.ac, "baskasi", "1234") == HATALI_GIRIS, "olmayan kullanıcı açmadı")
    db.ac("ADMIN", "1234")                        # kullanıcı adı büyük/küçük harf duyarsız
    ok(not db.kilitli, "doğru şifre açtı")
    ok(svc.caris()[0]["unvan"] == GIZLI and svc.movements()[0]["tutar"] == 123456, "yeniden açınca veri aynı")
    ok(db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1, "foreign_keys açık")
    ok(db.scalar("SELECT tr_fold('ŞİŞLİ')") == "sisli", "tr_fold işlevi")
    # işlem dışındaki tek komutluk yazma da hemen diske geçer
    db.set_setting("deneme", "x1")
    ok(ac(path).get_setting("deneme") == "x1", "autocommit yazma kaydedildi")
    # giriş ekranı ayarları değişince başlık da aynı yazmada değişir
    db.set_setting("yazi_boyutu", "buyuk")
    ok(Database(path).get_setting("yazi_boyutu") == "buyuk", "başlıktaki yazı boyutu güncellendi")
    # işlem içinde verilen kurtarma anahtarı işlem geri alınırsa uygulanmaz
    old = db.anahtar.kurtarma
    try:
        with db.tx():
            db.set_recovery_key("WXYZ-WXYZ-WXYZ-WXYZ")
            raise ValueError("iptal")
    except ValueError:
        pass
    ok(db.anahtar.kurtarma is old and db.has_recovery(), "geri alınan işlemde kurtarma kilidi değişmedi")
    with db.tx():
        db.set_setting("deneme", "x2")
        db.set_recovery_key("WXYZ-WXYZ-WXYZ-WXYZ")
    db.close()
    raises(sifreleme.AnahtarHatali, Database(path).ac_kurtarma, key)
    db = Database(path)
    db.ac_kurtarma("WXYZ-WXYZ-WXYZ-WXYZ")
    ok(db.get_setting("deneme") == "x2", "kilit ve veri aynı yazmada")
    db.close()
    # şema değişikliği (ör. sürüm yükseltmesinin CREATE TABLE adımı) satır değişmese de kaydedilir
    db = ac(path)
    with db.tx():
        db.x("CREATE TABLE ek_tablo(x)")
    db.close()
    ok(ac(path).scalar("SELECT COUNT(*) FROM sqlite_master WHERE name='ek_tablo'") == 1, "DDL kaydedildi")
    # programın daha yeni sürümünün veritabanı: anlaşılır hata, kilitli kalır
    db = ac(path)
    db.x("PRAGMA user_version = 99")
    db.close()
    db = Database(path)
    ok("daha yeni" in raises(ServiceError, db.ac, "admin", "1234") and db.kilitli, "yeni sürüm hatası gösterildi")


def test_gecici_kilit(tmp: Path):
    path = tmp / "kasa.kdb"
    db = Database(path)
    svc = Service(db)
    h = baslik(path)
    ok("gecici" in h and "kullanicilar" not in h, "yepyeni veritabanı geçici kilitle yazıldı")
    ok(not db.kilitli and not svc.users_exist(), "yepyeni veritabanı açık, kullanıcı yok")
    d2 = Database(path)                            # kurulum yarım kalmış: yeniden açılınca da açık
    ok(not d2.kilitli and not Service(d2).users_exist(), "kullanıcısız dosya yeniden açılınca kurulum yapılabilir")
    d2.close()
    eski = db.anahtar
    # ilk kullanıcıyı ekleyen işlem geri alınırsa geçici kilit kalır
    try:
        with db.tx():
            db.kullanici_kilidi("admin", "1234")
            raise ValueError("iptal")
    except ValueError:
        pass
    ok("gecici" in baslik(path) and db.anahtar is eski, "geri alınan işlemde geçici kilit kaldı")
    key = svc.setup_first_admin("admin", "Yönetici", "1234", "Test Kargo")
    h = baslik(path)
    ok("gecici" not in h and set(h["kullanicilar"]) == {"admin"} and "kurtarma" in h,
       "geçici kilit ilk kullanıcı kilidiyle aynı yazmada kalktı")
    ok(db.anahtar.kimlik != eski.kimlik and db.anahtar.dek != eski.dek, "veri anahtarı yenilendi")
    raises(sifreleme.BozukDosya, sifreleme.oku, path, sifreleme.Anahtar(eski.dek, db.anahtar.kimlik))
    raw = path.read_bytes()
    ok(sifreleme._b64(db.anahtar.dek).encode() not in raw, "yeni veri anahtarı dosyada açıkta değil")
    svc.create_user("kasiyer", "Ayşe", "1111", "kullanici")
    ok("gecici" not in baslik(path), "kullanıcı kilidi varken geçici kilit hiç yazılmaz")
    db.close()
    d = Database(path)
    d.ac_kurtarma(key)
    ok(not d.kilitli, "kurulumdaki kurtarma anahtarı yeni veri anahtarını açıyor")
    d.close()


def test_kullanici_kilitleri(tmp: Path):
    path = tmp / "kasa.kdb"
    db, svc, key = kur(path)
    hareket(svc, 100)
    uid = svc.create_user("kasiyer", "Ayşe Yılmaz", "1111", "kullanici")
    ok(set(baslik(path)["kullanicilar"]) == {"admin", "kasiyer"}, "yeni kullanıcının kilidi eklendi")
    ok(not ac(path, "kasiyer", "1111").kilitli, "yeni kullanıcının ilk şifresi açıyor")
    raises(ServiceError, svc.create_user, "KASİYER", "", "1111", "kullanici")      # katlanmış ad aynı
    raises(ServiceError, svc.create_user, "Kasıyer", "", "1111", "kullanici")
    # işlem geri alınırsa kilit de eklenmez
    try:
        with db.tx():
            svc.create_user("gecici", "", "1111", "kullanici")
            raise ValueError("iptal")
    except ValueError:
        pass
    ok("gecici" not in baslik(path)["kullanicilar"] and "gecici" not in db.kilit_adlari(), "geri alınan kullanıcının kilidi yok")
    # iç içe işlem: iç blok geri alınınca onun kilidi de düşer
    with db.tx():
        try:
            with db.tx():
                db.kullanici_kilidi("ic", "1111")
                raise ValueError("iç hata")
        except ValueError:
            pass
        db.set_setting("deneme", "1")
    ok("ic" not in baslik(path)["kullanicilar"], "iç bloğun kilidi geri alındı")
    once = path.read_bytes()
    # yönetici şifreyi sıfırlayınca eski şifre sonraki dosyaları açamaz
    svc.reset_password(uid, "2222")
    raises(sifreleme.AnahtarHatali, ac, path, "kasiyer", "1111")
    ok(not ac(path, "kasiyer", "2222").kilitli, "sıfırlanan şifre açıyor")
    ok(sifreleme.coz(once, kullanici="kasiyer", sifre="1111")[0], "değişiklikten önceki kopya eski şifreyle açılır")
    # kendi şifresini değiştirme
    svc.change_own_password("1234", "5678")
    raises(sifreleme.AnahtarHatali, ac, path, "admin", "1234")
    ok(not ac(path, "admin", "5678").kilitli, "değiştirilen şifre açıyor")
    # kilit yazılamazsa şifre de değişmez (aynı yazma)
    real = sifreleme.yaz
    sifreleme.yaz = lambda p, v: (_ for _ in ()).throw(OSError("disk dolu"))
    try:
        raises(sifreleme.KayitHatasi, svc.change_own_password, "5678", "9999")
    finally:
        sifreleme.yaz = real
    ok(not ac(path, "admin", "5678").kilitli and svc.login("admin", "5678"), "yazılamayan şifre değişikliği geri alındı")
    db.close()
    # kilitli veritabanında giriş: açma girişin parçası, başarısız açma hatalı giriş sayılır
    db = Database(path)
    svc = Service(db)
    for _ in range(Service.MAX_FAILS - 1):
        ok(raises(AuthError, svc.login, "admin", "yanlis") == HATALI_GIRIS and db.kilitli, "hatalı giriş, kilit açılmadı")
    raises(AuthError, svc.login, "admin", "yanlis")
    ok("saniye" in raises(AuthError, svc.login, "admin", "5678"), "5 hatalı denemede bekleme")
    svc._locked_until = 0
    svc.login("kasiyer", "2222")                    # yönetici olmayan kullanıcı da kendi şifresiyle açar
    ok(not db.kilitli and svc.user["kullanici_adi"] == "kasiyer", "kullanıcı girişi veritabanını açtı")
    ok(any(a["islem"] == "GIRIS" for a in svc.audit(dt.date(2000, 1, 1), dt.date(2099, 1, 1))), "giriş kaydedildi")
    db.close()
    # pasif kullanıcı: giriş yapamaz, kilidi kalkar (şifresi sonraki dosyaları açmaz)
    db = ac(path, "admin", "5678")
    svc = Service(db)
    svc.login("admin", "5678")
    svc.update_user(uid, "Ayşe Yılmaz", "kullanici", False)
    ok("kasiyer" not in baslik(path)["kullanicilar"], "pasif yapılan kullanıcının kilidi kalktı")
    ok("pasif" in raises(AuthError, svc.login, "kasiyer", "2222"), "pasif kullanıcı giremedi")
    svc.reset_password(uid, "3333")
    ok("kasiyer" not in baslik(path)["kullanicilar"], "pasif kullanıcının şifresi sıfırlanınca kilit eklenmedi")
    svc.update_user(uid, "Ayşe Yılmaz", "kullanici", True)
    ok(svc.kilitsiz_kullanicilar() == ["kasiyer"], "yeniden aktif yapılan kullanıcı yöneticiye bildirildi")
    db.close()
    db = Database(path)
    s = Service(db)
    msg = raises(AuthError, s.login, "kasiyer", "2222")
    ok("kilidi yok" in msg and "sıfırla" in msg and db.kilitli, "pasif yapılan kullanıcının eski şifresi dosyayı açmadı; "
                                                                "kilidi olmadığı ve yöneticinin sıfırlaması gerektiği yazıldı")
    for _ in range(Service.MAX_FAILS + 1):
        raises(AuthError, s.login, "kasiyer", "3333")
    ok(s._fails == 0 and s._locked_until == 0, "kilidi olmayan kullanıcının denemesi hatalı giriş sayılmadı")
    ok(raises(AuthError, s.login, "admin", "yanlis") == HATALI_GIRIS and s._fails == 1,
       "kilidi olan kullanıcının yanlış şifresi eskisi gibi hatalı giriş")
    db.close()
    # kilit yalnızca eski şifrenin kilididir; veri anahtarı aynı kaldığından eski kopya + eski şifre anahtarı verir
    _, eski_anahtar = sifreleme.coz(once, kullanici="kasiyer", sifre="1111")
    ok(sifreleme.oku(path, eski_anahtar)[0], "eski kopyadan alınan veri anahtarı sonraki dosyayı açıyor (bilinen sınır)")


def test_kurtarma_ile_acilis(tmp: Path):
    path = tmp / "kasa.kdb"
    db, svc, key = kur(path)
    hareket(svc, 4242)
    db.close()
    db = Database(path)
    raises(sifreleme.AnahtarHatali, db.ac_kurtarma, "ZZZZ-ZZZZ-ZZZZ-ZZZZ")
    svc = Service(db)
    ok(raises(AuthError, svc.reset_with_recovery_key, "ZZZZ-ZZZZ-ZZZZ-ZZZZ", "admin", "yeni1") ==
       "Kurtarma anahtarı hatalı." and db.kilitli, "yanlış kurtarma anahtarı açmadı")
    new_key = svc.reset_with_recovery_key(key, "admin", "yeni1")
    ok(not db.kilitli and svc.movements()[0]["tutar"] == 4242, "şifremi unuttum: kurtarma anahtarı veriyi açtı")
    svc.login("admin", "yeni1")
    db.close()
    raises(sifreleme.AnahtarHatali, ac, path, "admin", "1234")
    ok(not ac(path, "admin", "yeni1").kilitli, "yeni şifre açıyor, eski açmıyor")
    raises(sifreleme.AnahtarHatali, Database(path).ac_kurtarma, key)
    d = Database(path)
    d.ac_kurtarma(new_key)
    ok(d.scalar("SELECT COUNT(*) FROM hareketler") == 1 and d.has_recovery(), "yeni kurtarma anahtarı açıyor")
    # hiç kullanıcı kilidi olmayan dosya (ör. önceki deneme sürümü): giriş "Şifremi unuttum"a yönlendirir
    d.kullanici_kilidi_sil("admin")
    d.close()
    d = Database(path)
    s = Service(d)
    ok(s.users_exist() and "Şifremi unuttum" in raises(AuthError, s.login, "admin", "yeni1") and d.kilitli,
       "kullanıcı kilidi olmayan dosyada giriş kurtarma anahtarına yönlendirdi")
    ok(s._fails == 1, "yönlendirme de hatalı deneme sayıldı")
    s.reset_with_recovery_key(new_key, "admin", "yeni2")
    ok(set(baslik(path)["kullanicilar"]) == {"admin"}, "kurtarma anahtarıyla sıfırlanan yöneticinin kilidi eklendi")
    d.close()


def test_ayni_katlanmis_ad(tmp: Path):
    """1.1.x'te açılabilen, adları yalnızca Türkçe harflerle ayrılan kullanıcılar aynı kilidi paylaşır."""
    from kasatakip import auth
    path = tmp / "kasa.kdb"
    db, svc, _ = kur(path)
    with db.tx():                                  # 1.1.x'te create_user bunu engellemiyordu
        for ad in ("Şule", "sule"):
            db.x("INSERT INTO kullanicilar(kullanici_adi, sifre_hash, rol, olusturma) VALUES(?,?,'kullanici','x')",
                 (ad, auth.hash_password(ad + "1")))
    ok(svc.kilit_paylasanlar() == ["sule", "Şule"] and svc.kilitsiz_kullanicilar() == ["sule", "Şule"],
       "aynı katlanmış adlı kullanıcılar bildirildi")
    sule = db.q1("SELECT id FROM kullanicilar WHERE kullanici_adi='sule'")["id"]
    Sule = db.q1("SELECT id FROM kullanicilar WHERE kullanici_adi='Şule'")["id"]
    ok("pasif" in raises(ServiceError, svc.reset_password, sule, "4444") and "sule" not in db.kilit_adlari(),
       "kilidi paylaşan kullanıcının şifresi sıfırlanmadı, kilit yazılmadı")
    svc.logout()
    svc.login("Şule", "Şule1")
    ok("sule" not in db.kilit_adlari(), "girişte de paylaşılan kilit yazılmadı")
    raises(ServiceError, svc.change_own_password, "Şule1", "Yeni1")
    ok(auth.verify_password("Şule1", db.scalar("SELECT sifre_hash FROM kullanicilar WHERE id=?", (Sule,))),
       "kilidi paylaşan kullanıcı kendi şifresini de değiştiremedi")
    svc.logout()
    svc.login("admin", "1234")
    ok(svc.kilitsiz_kullanicilar() == ["sule", "Şule"], "ikisi bildirilmeye devam ediyor")
    svc.update_user(sule, "", "kullanici", False)
    ok(svc.kilitsiz_kullanicilar() == ["Şule"], "biri pasif yapılınca kalan, kilidi olmadığı için bildirildi")
    svc.reset_password(Sule, "Şule2")
    ok("sule" in baslik(path)["kullanicilar"] and svc.kilitsiz_kullanicilar() == [],
       "pasif yapıldıktan sonra kalan kullanıcının şifresi sıfırlandı; kilit onun")
    db.close()
    d = Database(path)
    Service(d).login("Şule", "Şule2")
    ok(not d.kilitli, "aktif kalan kullanıcı kendi şifresiyle açıyor")
    d.close()

    # geçişten sonraki durum: yönetici 'Şule', kullanıcı 'sule'; ortak kilit yöneticinin şifresiyle
    path = tmp / "iki.kdb"
    db, svc, key = kur(path, kullanici="Şule", sifre="yonet1")
    db.x("INSERT INTO kullanicilar(kullanici_adi, sifre_hash, rol, olusturma) VALUES('sule', ?, 'kullanici', 'x')",
         (auth.hash_password("kull22"),))
    sule = db.scalar("SELECT id FROM kullanicilar WHERE kullanici_adi='sule'")
    svc.logout()
    svc.login("sule", "kull22")                    # aynı oturumda (veritabanı açık) kullanıcı girer
    ok("pasif" in raises(ServiceError, svc.change_own_password, "kull22", "yeni33"),
       "benzer adlı kullanıcı kendi şifresini değiştirip yöneticinin kilidini bozamadı")
    svc.logout()
    svc.login("Şule", "yonet1")
    raises(ServiceError, svc.reset_password, sule, "gecici44")
    bm = BackupManager(db, svc, tmp / "yedek")
    y = bm.backup("manuel").path
    hareket(svc, 55)
    bm.restore(y)                                  # yönetici bilgisi istenmez: kilit Şule'nin şifresiyle uyumlu
    ok(len(svc.movements()) == 0, "benzer adlar varken aynı kurulumun yedeği doğrudan geri yüklendi")
    bm.restore(y, yonetici=("Şule", "yonet1"))
    raises(sifreleme.AnahtarHatali, bm.restore, y, yonetici=("sule", "kull22"))     # tam adla bakılır: yönetici değil
    db.close()
    ok(not ac(path, "Şule", "yonet1").kilitli, "yöneticinin kilidi bozulmadı")
    # şifresini unutan yönetici kurtarma anahtarıyla girer: kilidi paylaşan kullanıcı pasif yapılır, kilit yöneticinin
    d = Database(path)
    s = Service(d)
    s.reset_with_recovery_key(key, "Şule", "yonet2")
    ok("sule" in d.uyari and not d.scalar("SELECT aktif FROM kullanicilar WHERE id=?", (sule,))
       and s.kilitsiz_kullanicilar() == [], "kurtarmada benzer adlı kullanıcı pasif yapıldı, yönetici uyarılacak")
    d.close()
    ok(not ac(path, "Şule", "yonet2").kilitli, "yönetici yeni şifresiyle açıyor")


def test_yarida_kesilen_yazma(tmp: Path):
    path = tmp / "kasa.kdb"
    db, svc, _ = kur(path)
    hareket(svc, 100)
    before = path.read_bytes()
    real_fsync = os.fsync

    def kesinti(fd):
        raise OSError("elektrik kesildi (deneme)")
    os.fsync = kesinti
    try:
        raises(sifreleme.KayitHatasi, hareket, svc, 200)
    finally:
        os.fsync = real_fsync
    ok(path.read_bytes() == before, "eski dosya olduğu gibi duruyor")
    ok(not list(tmp.glob("*.yazim")), "yarım geçici dosya silindi")
    ok([m["tutar"] for m in svc.movements()] == [100], "kaydedilemeyen hareket bellekte de yok")
    ok(not db.conn.in_transaction, "bağlantı kullanılabilir")
    # yer değiştirme adımında hata
    real = sifreleme._yerine_koy
    sifreleme._yerine_koy = lambda a, b: (_ for _ in ()).throw(OSError("taşıma hatası"))
    try:
        raises(sifreleme.KayitHatasi, hareket, svc, 300)
    finally:
        sifreleme._yerine_koy = real
    ok(path.read_bytes() == before and not list(tmp.glob("*.yazim")), "taşıma hatasında eski dosya sağlam")
    hareket(svc, 400)
    # yazma başarısız ve disk de okunamıyor (ör. veri klasörüne erişim gitti): geri alma yine gerçekten yapılır
    real_yaz, real_oku = sifreleme.yaz, sifreleme.oku
    sifreleme.yaz = lambda p, v: (_ for _ in ()).throw(OSError("disk yok"))
    sifreleme.oku = lambda *a, **k: (_ for _ in ()).throw(OSError("disk yok"))
    try:
        raises(sifreleme.KayitHatasi, hareket, svc, 500)
    finally:
        sifreleme.yaz, sifreleme.oku = real_yaz, real_oku
    ok(sorted(m["tutar"] for m in svc.movements()) == [100, 400] and not db._kirli,
       "disk okunamasa da kaydedilemeyen hareket bellekten geri alındı")
    hareket(svc, 600)
    db.close()
    ok(sorted(m["tutar"] for m in Service(ac(path)).movements()) == [100, 400, 600],
       "sonraki kayıt çalışıyor, geri alınan hareket sonradan diske yazılmadı")


def _duz_kopya(tmp: Path, kasiyer: bool = False) -> tuple[Path, dict]:
    """1.1.x düz kasa.db oluşturur, 1.1.4'ün oluşturduğu gibi şema sürüm 1 (db._SCHEMA_V1 + varsayılan tanımlar +
    user_version 1): yönetici admin / 1234, isteğe bağlı kasiyer / 1111, bir cari ve 5 hareket."""
    legacy = tmp / "veri" / "kasa.db"
    legacy.parent.mkdir()
    con = sqlite3.connect(str(legacy))
    for stmt in dbmod._SCHEMA_V1:
        con.execute(stmt)
    for tur, names in (("GELIR", dbmod.DEFAULT_GELIR), ("GIDER", dbmod.DEFAULT_GIDER)):
        for i, ad in enumerate(names, 1):
            con.execute("INSERT INTO kategoriler(tur, ad, sira) VALUES(?, ?, ?)", (tur, ad, i))
    for i, ad in enumerate(dbmod.DEFAULT_ODEME, 1):
        con.execute("INSERT INTO odeme_tipleri(ad, sira) VALUES(?, ?)", (ad, i))
    zaman = "2026-09-01 09:00:00"
    kisiler = [("admin", "Yönetici", "1234", "yonetici")]
    if kasiyer:
        kisiler.append(("kasiyer", "Ayşe Yılmaz", "1111", "kullanici"))
    for ad, ad_soyad, sifre, rol in kisiler:
        con.execute("INSERT INTO kullanicilar(kullanici_adi, ad_soyad, sifre_hash, rol, olusturma) VALUES(?,?,?,?,?)",
                    (ad, ad_soyad, auth.hash_password(sifre), rol, zaman))
    for k, v in (("firma_adi", "Test Kargo"), ("son_kullanici", "admin"),
                 ("kurtarma_hash", auth.hash_password(auth.normalize_key("WXYZ-WXYZ-WXYZ-WXYZ")))):
        con.execute("INSERT INTO ayarlar(anahtar, deger) VALUES(?, ?)", (k, v))
    con.execute("INSERT INTO cariler(kod, unvan, tip, olusturma) VALUES('M0001', ?, 'MUSTERI', ?)", (GIZLI, zaman))
    for i in range(1, 6):
        con.execute("INSERT INTO hareketler(tarih, saat, tur, kategori_id, odeme_tipi_id, tutar, olusturan_id, "
                    "olusturma) VALUES('2026-10-01', '10:00', 'GELIR', 1, 1, ?, 1, ?)", (i * 1000, zaman))
    con.execute("INSERT INTO islem_gecmisi(zaman, kullanici_id, kullanici_adi, islem, konu, detay) "
                "VALUES(?, 1, 'admin', 'KURULUM', 'Sistem', 'İlk kurulum yapıldı')", (zaman,))
    con.execute("PRAGMA user_version = 1")
    con.commit()
    counts = tablo_sayilari(con)
    con.close()
    return legacy, counts


def _surum(path: Path) -> int:
    """Düz SQLite dosyasının user_version değeri, dosyayı açmadan (başlıkta 60. bayt)."""
    return struct.unpack(">I", path.read_bytes()[60:64])[0]


def test_gecis(tmp: Path):
    legacy, counts = _duz_kopya(tmp, kasiyer=True)
    kdb = legacy.with_suffix(".kdb")
    raw = legacy.read_bytes()
    db = Database(kdb)
    ok(not db.sifreli and db.gecis_bekliyor and db.path == legacy and not db.kilitli and not kdb.exists(),
       "güncellemeden sonra düz dosyayla açıldı, geçiş ilk yönetici girişini bekliyor")
    ok(legacy.read_bytes() == raw and _surum(legacy) == 1 and db.user_version() == 1,
       "açılışta düz dosyaya dokunulmadı, sürümü yükseltilmedi")
    ok(db.has_recovery(), "düz dosyada kurtarma adımı sorulmaz")
    svc = Service(db)
    ok(svc.users_exist() and svc.setting("firma_adi") == "Test Kargo", "düz dosyada giriş ekranı eskisi gibi")
    raises(AuthError, svc.login, "admin", "yanlis")
    raises(AuthError, svc.login, "kimse", "1234")
    ok(legacy.read_bytes() == raw and not legacy.with_name("kasa.db-journal").exists(),
       "hatalı girişler düz dosyaya yazılmadı (1.1.x ile yeniden açılabilir)")
    # yönetici olmayan giriş geçişi başlatmaz; program düz dosyayla çalışacağı için sürüm o girişte yükseltilir
    svc.login("kasiyer", "1111")
    ok(not db.sifreli and db.gecis_bekliyor and not kdb.exists(), "kullanıcı girişi geçiş yapmadı")
    ok(_surum(legacy) == dbmod.SCHEMA_VERSION and db.scalar("SELECT COUNT(*) FROM personeller") == 0,
       "kullanıcı girişinde düz dosyanın sürümü yükseltildi")
    hareket(svc, 7777)                             # düz dosyayla çalışılırken yeni kayıt
    bm = BackupManager(db, svc, tmp / "yedek")
    ok(bm.backup("manuel").path.suffix == ".db", "düz dosyayla çalışırken yedek de düz")
    svc.logout()
    # başarısız geçiş: giriş yapılır, düz dosya olduğu gibi kalır, onunla çalışılır
    real_oku = sifreleme.oku
    sifreleme.oku = lambda *a, **k: (_ for _ in ()).throw(sifreleme.BozukDosya("doğrulama hatası (deneme)"))
    try:
        svc.login("admin", "1234")
    finally:
        sifreleme.oku = real_oku
    ok(svc.is_admin and not db.sifreli and db.uyari and db.path == legacy and db.gecis_bekliyor,
       "geçiş başarısız: giriş yapıldı, düz dosyayla devam, uyarı var")
    ok(not kdb.exists() and sifreleme.sqlite_mi(legacy.read_bytes()), "yarım şifreli dosya yok, düz dosya duruyor")
    svc.logout()
    db.close()
    con = sqlite3.connect(str(legacy))
    counts = tablo_sayilari(con)
    con.close()
    migrated = legacy.read_bytes()
    # sonraki yönetici girişinde yeniden denenir ve başarır; düz dosya ancak şifreli kopya doğrulanınca silinir
    db = Database(kdb)
    svc = Service(db)
    gorulen = []

    def izle(*a, **k):
        gorulen.append(legacy.exists())
        return real_oku(*a, **k)
    sifreleme.oku = izle
    try:
        svc.login("admin", "1234")
    finally:
        sifreleme.oku = real_oku
    ok(gorulen == [True], "doğrulama yapılırken düz dosya henüz silinmemişti")
    ok(db.sifreli and not db.gecis_bekliyor and not db.uyari and not legacy.exists() and db.path == kdb,
       "yönetici girişinde geçiş tamam")
    h = baslik(kdb)
    ok(set(h["kullanicilar"]) == {"admin"} and "kurtarma" not in h and "gecici" not in h,
       "şifreli dosya yöneticinin şifresiyle kilitli")
    ok(not db.has_recovery(), "kurtarma kilidi girişten sonraki adımda kurulur")
    ok(tablo_sayilari(db.conn) == {**counts, "ayarlar": counts["ayarlar"] + 1,
                                   "islem_gecmisi": counts["islem_gecmisi"] + 1} and counts["hareketler"] == 6,
       "tüm tablolar korunmuş (ayarlara geçiş özeti, geçmişe giriş kaydı eklendi)")
    ok(db.get_setting(GECIS_OZETI) == hashlib.sha256(migrated).hexdigest(), "çevrilen düz dosyanın özeti saklandı")
    ok(svc.caris()[0]["unvan"] == GIZLI, "cari korunmuş")
    ok(not kdb.read_bytes().startswith(b"SQLite") and GIZLI.encode() not in kdb.read_bytes(), "yeni dosya şifreli")
    ok(sorted(p.name for p in legacy.parent.iterdir()) == ["kasa.kdb"], "düz dosya silindi")
    ok(svc.kilitsiz_kullanicilar() == ["kasiyer"], "kilidi olmayan kullanıcı bildirildi")
    db.close()
    db = Database(kdb)
    ok(db.kilitli, "geçişten sonra açılışta kilitli")
    raises(AuthError, Service(db).login, "kasiyer", "1111")       # kilidi yok: sıfırlanana kadar açamaz
    db.ac("admin", "1234")
    svc = Service(db)
    svc.login("admin", "1234")
    svc.reset_password(svc.db.q1("SELECT id FROM kullanicilar WHERE kullanici_adi='kasiyer'")["id"], "2222")
    ok(not svc.kilitsiz_kullanicilar() and not ac(kdb, "kasiyer", "2222").kilitli, "şifresi sıfırlanan kullanıcı açıyor")
    hareket(svc, 8888)                             # şifreli veri, kalan düz dosyadan farklılaşır
    db.close()
    # silme hiç yapılamamışsa (çevrilen dosyanın aynısı kalmış, şifreliden yeni) kilit açılınca silinir
    legacy.write_bytes(migrated)
    kdb_once = kdb.read_bytes()
    db = Database(kdb)
    ok(db.kilitli and legacy.exists() and db.sifreli, "kilitliyken kalan düz dosyaya dokunulmadı, ona dönülmedi")
    db.ac("admin", "1234")
    ok(not legacy.exists() and not db.uyari and db.scalar("SELECT COUNT(*) FROM hareketler") == 7,
       "çevrilen düz dosyanın kalan aynısı silindi, şifreli veri kullanıldı")
    db.close()
    ok(kdb.read_bytes() == kdb_once, "şifreli dosyaya dokunulmadı")
    # silme yarıda kalmış: sıfırlanmış / boşaltılmış dosya şifreliden yeni
    for kalan in (bytes(8192), b"", bytes(100) + raw[100:]):
        legacy.write_bytes(kalan)
        db = ac(kdb)
        ok(db.sifreli and not legacy.exists() and not db.uyari and kdb.read_bytes() == kdb_once
           and db.scalar("SELECT COUNT(*) FROM hareketler") == 7,
           f"yarım silinmiş düz dosya ({len(kalan)} bayt) silindi, şifreli veri korundu")
        db.close()
    # sifirla_sil silemezse (dosya başka programca tutuluyor) geride yalnızca boş dosya kalır
    legacy.write_bytes(raw)
    real_unlink = Path.unlink
    Path.unlink = lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("dosya kullanımda"))
    try:
        raises(PermissionError, sifreleme.sifirla_sil, legacy)
    finally:
        Path.unlink = real_unlink
    ok(legacy.exists() and legacy.stat().st_size == 0, "silinemeyen düz dosya boşaltıldı")
    ac(kdb).close()
    ok(not legacy.exists() and kdb.read_bytes() == kdb_once, "boş kalan dosya sonraki açılışta silindi")
    # şifreliden yeni ama farklı içerikli düz dosya (ör. eski sürüm, elle kopya): ikisi de silinmez
    legacy.write_bytes(raw)
    db = ac(kdb)
    aside = [p for p in legacy.parent.iterdir() if p.name.startswith("kasa.db.eski-")]
    ok(db.sifreli and db.uyari and not legacy.exists() and len(aside) == 1 and aside[0].read_bytes() == raw,
       "farklı düz dosya kenara alındı, kullanıcı uyarıldı")
    ok(kdb.read_bytes() == kdb_once and db.scalar("SELECT COUNT(*) FROM hareketler") == 7,
       "şifreli veritabanı korundu (üzerine yazılmadı, silinmedi)")
    db.close()
    aside[0].unlink()
    # bozuk düz dosya: geçiş yapılmaz, dosya korunur
    kdb.unlink()
    legacy.write_bytes(raw[:4096] + b"\x00" * 1000)
    raw2 = legacy.read_bytes()
    try:
        db = Database(kdb)
        Service(db).login("admin", "1234")
        db.close()
    except Exception:  # noqa: BLE001
        pass
    ok(legacy.read_bytes() == raw2 and not kdb.exists(), "bozuk düz dosya şifrelenmedi, silinmedi")


def test_gecis_v1(tmp: Path):
    """1.1.x dosyası (şema 1) yönetici geçişine kadar bayt bayt aynı kalır; sürüm bellekteki kopyada yükseltilir ve
    personel bölümü geçişten sonra çalışır."""
    from tests.ornek_personel import ILK_PERSONEL
    legacy, _ = _duz_kopya(tmp, kasiyer=True)
    kdb = legacy.with_suffix(".kdb")
    raw = legacy.read_bytes()
    for _ in range(2):                             # açılış + iptal edilen (yanlış şifreli) giriş
        db = Database(kdb)
        svc = Service(db)
        ok(svc.users_exist() and svc.setting("son_kullanici") == "admin" and db.gecis_bekliyor, "giriş ekranı bilgileri")
        raises(AuthError, svc.login, "admin", "0000")
        db.close()
    ok(legacy.read_bytes() == raw and sorted(p.name for p in legacy.parent.iterdir()) == ["kasa.db"],
       "açılış ve iptal edilen giriş düz dosyayı değiştirmedi, günlük dosyası yazılmadı")
    db = Database(kdb)
    svc = Service(db)
    svc.login("admin", "1234")
    ok(db.sifreli and not legacy.exists() and kdb.exists() and db.user_version() == dbmod.SCHEMA_VERSION,
       "yönetici girişinde geçiş; sürüm şifreli kopyada yükseltildi")
    ok(db.get_setting(GECIS_OZETI) == hashlib.sha256(raw).hexdigest(), "çevrilen (şema 1) dosyanın özeti saklandı")
    yuksek = Database.bellekte(raw)
    beklenen = tablo_sayilari(yuksek.conn)
    yuksek.close()
    ok(tablo_sayilari(db.conn) == {**beklenen, "ayarlar": beklenen["ayarlar"] + 1,
                                   "islem_gecmisi": beklenen["islem_gecmisi"] + 1},
       "şifreli kopya, düz dosyanın yükseltilmiş haliyle aynı (geçiş özeti ve giriş kaydı eklendi)")
    ok({"personeller", "personel_fisleri"} <= {r[0] for r in db.q("SELECT name FROM sqlite_master WHERE type='table'")},
       "personel tabloları var")
    ok(svc.ilk_personel_yukle(ILK_PERSONEL) == 41 and svc.ilk_personel_yukle(ILK_PERSONEL) == 0,
       "41 kişilik liste bir kez yüklendi")
    kategori = db.scalar("SELECT COUNT(*) FROM kategoriler")
    db.close()
    db = ac(kdb)
    ok(db.scalar("SELECT COUNT(*) FROM personeller") == 41 and Service(db).ilk_personel_yukle(ILK_PERSONEL) == 0
       and db.scalar("SELECT COUNT(*) FROM kategoriler") == kategori, "yeniden açılışta personel ve kategori çoğalmadı")
    db.close()
    # geçiş başarısız: program düz dosyayla çalışacağı için sürüm dosyanın kendisinde yükseltilir
    g = tmp / "b"
    g.mkdir()
    legacy, _ = _duz_kopya(g)
    db = Database(legacy.with_suffix(".kdb"))
    svc = Service(db)
    real_oku = sifreleme.oku
    sifreleme.oku = lambda *a, **k: (_ for _ in ()).throw(sifreleme.BozukDosya("doğrulama hatası (deneme)"))
    try:
        svc.login("admin", "1234")
    finally:
        sifreleme.oku = real_oku
    ok(not db.sifreli and db.uyari and _surum(legacy) == dbmod.SCHEMA_VERSION
       and svc.ilk_personel_yukle(ILK_PERSONEL) == 41, "başarısız geçişte düz dosya yükseltildi, personel çalışıyor")
    db.close()


def test_duz_kdb(tmp: Path):
    """kasa.kdb'nin kendisi düz SQLite (ör. 1.1.x yedeği bu adla kopyalanmış): şifresiz çalışılmaz."""
    legacy, _ = _duz_kopya(tmp)
    kdb = legacy.with_suffix(".kdb")
    raw = legacy.read_bytes()
    legacy.replace(kdb)
    db = Database(kdb)
    ok(not kdb.exists() and legacy.read_bytes() == raw and db.gecis_bekliyor and db.path == legacy and not db.sifreli,
       "düz kasa.kdb, kasa.db adına taşındı; geçiş yönetici girişini bekliyor")
    Service(db).login("admin", "1234")
    blob = kdb.read_bytes()
    ok(db.sifreli and not legacy.exists() and not blob.startswith(b"SQLite") and GIZLI.encode() not in blob,
       "yönetici girişinde şifrelendi")
    db.close()
    ok(Database(kdb).kilitli, "sonraki açılışta kilitli")
    # yanında kasa.db de varsa hangisinin güncel olduğu bilinemez: açılmaz, iki dosyaya da dokunulmaz
    g = tmp / "iki"
    g.mkdir()
    legacy, _ = _duz_kopya(g)
    kdb = legacy.with_suffix(".kdb")
    kdb.write_bytes(raw)
    once = legacy.read_bytes()
    msg = raises(sifreleme.SifrelemeHatasi, Database, kdb)
    ok("şifreli değil" in msg and kdb.read_bytes() == raw and legacy.read_bytes() == once,
       "düz kasa.kdb ve kasa.db birlikteyken program şifresiz açılmadı, dosyalara dokunulmadı")


def test_okuma_hatasi(tmp: Path):
    """Girişte şifreli dosya okunamıyorsa (virüs tarayıcısı / eşitleme programı tutuyor) birkaç kez denenir, sonra
    giriş pencerelerinin gösterdiği hata verilir; hatalı giriş sayılmaz."""
    path = tmp / "kasa.kdb"
    db, svc, key = kur(path)
    db.close()
    real = Path.read_bytes
    denemeler = []

    def tutuluyor(self):
        if self.name == "kasa.kdb" and len(denemeler) < tutuluyor.kac:
            denemeler.append(1)
            raise PermissionError(13, "Erişim engellendi (deneme)")
        return real(self)
    tutuluyor.kac = 99
    Path.read_bytes = tutuluyor
    try:
        d = Database(path)
        s = Service(d)
        msg = raises(sifreleme.SifrelemeHatasi, s.login, "admin", "1234")
        ok("okunamadı" in msg and d.kilitli and s._fails == 0 and len(denemeler) == 10,
           "okunamayan dosya: birkaç deneme, açık hata, hatalı giriş sayılmadı")
        raises(sifreleme.SifrelemeHatasi, s.reset_with_recovery_key, key, "admin", "5678")
        denemeler.clear()
        tutuluyor.kac = 3                           # dosya kısa süre tutuldu
        s.login("admin", "1234")
    finally:
        Path.read_bytes = real
    ok(not d.kilitli and len(denemeler) == 3, "kısa süre tutulan dosya yeniden denemede açıldı")
    d.close()


def test_eski_yedek_kurtarma(tmp: Path):
    """1.1.x'te şifre değişmeden önce alınmış yedek: dönüştürülünce yalnızca kurtarma anahtarı açar (kullanıcı kilidi
    yok). Bu kurulumda da başka bilgisayarda da kurtarma anahtarıyla geri yüklenir; yönetici sonra "Şifremi unuttum"
    ile girer."""
    legacy, counts = _duz_kopya(tmp)
    yd = tmp / "yedek"
    (yd / "Aylık").mkdir(parents=True)
    arsiv = yd / "Aylık" / "kasa_2026-01.db"
    c = sqlite3.connect(":memory:")
    c.deserialize(legacy.read_bytes())
    c.execute("UPDATE kullanicilar SET sifre_hash=? WHERE kullanici_adi='admin'", (auth.hash_password("0000"),))
    c.commit()
    arsiv.write_bytes(c.serialize())               # o tarihte yöneticinin şifresi 0000'dı
    c.close()
    db = Database(legacy.with_suffix(".kdb"))
    svc = Service(db)
    svc.login("admin", "1234")                     # geçiş, ardından girişten sonraki kurtarma adımı
    key = svc.regenerate_recovery_key()
    bm = BackupManager(db, svc, yd)
    ok(bm.convert_legacy() == (1, 0), "arşiv dönüştürüldü")
    k = arsiv.with_suffix(".kdb")
    ok("kullanicilar" not in baslik(k) and "kurtarma" in baslik(k), "arşivde kullanıcı kilidi yok, kurtarma kilidi var")
    hareket(svc, 999)
    once = db.path.read_bytes()
    raises(sifreleme.AnahtarGerekli, bm.restore, k)
    raises(sifreleme.AnahtarHatali, bm.restore, k, yonetici=("admin", "1234"))
    ok(raises(sifreleme.AnahtarHatali, bm.restore, k, kurtarma_anahtari="ZZZZ-ZZZZ-ZZZZ-ZZZZ") == "Kurtarma anahtarı hatalı."
       and db.path.read_bytes() == once, "yanlış kurtarma anahtarı reddedildi, verilere dokunulmadı")
    pre = bm.restore(k, kurtarma_anahtari=key)
    h = baslik(db.path)
    ok(pre.kurtarma_ile and pre.sifreler_degisti and len(svc.movements()) == counts["hareketler"],
       "bu kurulumda kurtarma anahtarıyla geri yüklendi")
    ok("admin" not in h.get("kullanicilar", {}) and "kurtarma" in h and db.has_recovery()
       and auth.verify_password(auth.normalize_key(key), svc.setting("kurtarma_hash")),
       "yönetici kilidi yok; canlı kurtarma kilidi ve özeti kaldı")
    svc.logout()
    db.close()
    d = Database(db.path)
    s = Service(d)
    ok("Şifremi unuttum" in raises(AuthError, s.login, "admin", "1234") and d.kilitli,
       "yeniden açılışta giriş kurtarma anahtarına yönlendirdi")
    s.reset_with_recovery_key(key, "admin", "5678")
    s.login("admin", "5678")
    ok(len(s.movements()) == counts["hareketler"] and "admin" in baslik(d.path)["kullanicilar"],
       "Şifremi unuttum ile yönetici kilidi yeniden kuruldu")
    d.close()
    ok(not ac(d.path, "admin", "5678").kilitli, "yeni şifre açıyor")

    # başka bilgisayar: arşivi yalnızca kurtarma anahtarı açar; kilitsiz geri yüklenir, giriş adı yedeğin yöneticisi
    dbB, svcB, _ = kur(tmp / "b" / "kasa.kdb", kullanici="patron", sifre="4321")
    bmB = BackupManager(dbB, svcB, tmp / "b" / "yedek")
    ok(validate_backup(k, dbB.anahtar)[2].get("anahtar_gerekli"), "başka kurulumda anahtar istendi")
    raises(sifreleme.AnahtarHatali, bmB.restore, k, kullanici="admin", sifre="0000")
    pre = bmB.restore(k, kurtarma_anahtari=key)
    ok(pre.kurtarma_ile and dbB.has_recovery() and svcB.setting("son_kullanici") == "admin"
       and auth.verify_password(auth.normalize_key(key), svcB.setting("kurtarma_hash"))
       and not baslik(dbB.path).get("kullanicilar"), "başka kurulumda kurtarma anahtarıyla geri yüklendi")
    dbB.close()
    d = Database(dbB.path)
    s = Service(d)
    ok(d.kilitli and d.get_setting("son_kullanici") == "admin", "giriş ekranı yedekteki yöneticiyi öneriyor")
    s.reset_with_recovery_key(key, "admin", "8080")
    s.login("admin", "8080")
    ok(len(s.movements()) == counts["hareketler"], "başka bilgisayarda Şifremi unuttum ile girildi")
    d.close()


def test_donusturulen_yedek_kurtarma_ozeti(tmp: Path):
    """1.1.x düz yedeğindeki kurtarma özeti eski anahtarın (K0): dönüştürülen kopya canlı kurtarma kilidiyle (K1)
    kilitlendiği için içindeki özet de K1'in olmalı. Yeni bilgisayarda kasa.kdb olarak kopyalanınca "Şifremi
    unuttum" K1'i kabul eder, K0'ı reddeder."""
    legacy, counts = _duz_kopya(tmp)                 # içindeki kurtarma_hash eski anahtarın: WXYZ-WXYZ-WXYZ-WXYZ
    k0 = "WXYZ-WXYZ-WXYZ-WXYZ"
    yd = tmp / "yedek"
    yd.mkdir()
    duz = yd / "kasa_2026-01-05_10-00-00_cikis.db"
    shutil.copyfile(legacy, duz)
    db = Database(legacy.with_suffix(".kdb"))
    svc = Service(db)
    svc.login("admin", "1234")                       # geçiş
    k1 = svc.regenerate_recovery_key()
    ok(BackupManager(db, svc, yd).convert_legacy() == (1, 0), "düz yedek dönüştürüldü")
    kdb = duz.with_suffix(".kdb")
    svc.logout()
    db.close()
    yeni = tmp / "yeni_pc" / "kasa.kdb"              # yeni bilgisayar: yedek kasa.kdb olarak kopyalandı
    yeni.parent.mkdir()
    shutil.copyfile(kdb, yeni)
    d = Database(yeni)
    s = Service(d)
    ok(d.kilitli, "kopyalanan yedek kilitli açıldı")
    ok(raises(AuthError, s.reset_with_recovery_key, k0, "admin", "yeni9") == "Kurtarma anahtarı hatalı.",
       "eski kurtarma anahtarı (K0) reddedildi")
    s.reset_with_recovery_key(k1, "admin", "yeni9")
    ok(len(s.movements()) == counts["hareketler"], "yeni kurtarma anahtarı (K1) ile Şifremi unuttum çalıştı")
    d.close()
    d = ac(yeni, "admin", "yeni9")
    s = Service(d)
    s.login("admin", "yeni9")
    ok(not d.kilitli and len(s.movements()) == counts["hareketler"], "yeniden açılışta yeni şifreyle girildi")
    d.close()


def test_yedekler(tmp: Path):
    db, svc, key = kur(tmp / "kasa.kdb")
    svc.save_cari({"unvan": GIZLI, "tip": "MUSTERI"})
    hareket(svc, 100)
    bm = BackupManager(db, svc, tmp / "yedek")
    svc.set_setting("ek_yedek_klasoru", str(tmp / "usb"))
    res = bm.backup("manuel")
    ok(res.path.suffix == ".kdb" and res.extra_ok, "yedek .kdb ve ek klasöre kopyalandı")
    files = [res.path, tmp / "yedek" / "Aylık" / f"kasa_{dt.date.today():%Y-%m}.kdb",
             tmp / "usb" / "KasaTakip Yedekleri" / res.path.name,
             tmp / "usb" / "KasaTakip Yedekleri" / "Aylık" / f"kasa_{dt.date.today():%Y-%m}.kdb"]
    for f in files:
        raw = f.read_bytes()
        ok(not raw.startswith(b"SQLite") and GIZLI.encode() not in raw, f"şifreli yedek: {f.name}")
        v = validate_backup(f, db.anahtar)
        ok(v[0] and v[2]["sifreli"] and v[2]["hareket"] == 1, f"yedek doğrulandı: {f.name}")
    h = baslik(res.path)
    ok(set(h["kullanicilar"]) == {"admin"} and "kurtarma" in h and h["meta"]["firma_adi"] == "Test Kargo",
       "yedek başlığı kilitleri ve giriş bilgilerini taşıyor")
    ok(validate_backup(res.path)[2].get("anahtar_gerekli"), "anahtarsız açılmıyor")
    ok(validate_backup(res.path, kullanici="Admin", sifre="1234")[0], "yedek program şifresiyle açılıyor")
    ok(validate_backup(res.path, kurtarma_anahtari=key)[0], "yedek kurtarma anahtarıyla açılıyor")
    v = validate_backup(res.path, kullanici="admin", sifre="yanlis")
    ok(not v[0] and v[2].get("anahtar_hatali") and v[1] == HATALI_GIRIS, "yanlış şifreyle açılmıyor")
    ok({b["tur"] for b in bm.list_backups()} == {"manuel", "aylik"}, "liste .kdb yedekleri görüyor")
    ok(not list((tmp / "yedek").rglob("*.yazim")), "yedek klasöründe geçici dosya yok")
    # aynı kurulumun yedeği doğrudan geri yüklenir; kilitler yedekteki şifrelerle uyumlu kalır
    hareket(svc, 200)
    svc.change_own_password("1234", "9999")
    pre = bm.restore(res.path)
    ok(len(svc.movements()) == 1 and pre.path.suffix == ".kdb", "şifreli yedek geri yüklendi")
    ok(not db.path.read_bytes().startswith(b"SQLite"), "geri yüklemeden sonra canlı dosya şifreli")
    raises(sifreleme.AnahtarHatali, ac, db.path, "admin", "9999")
    d = ac(db.path, "admin", "1234")
    ok(d.scalar("SELECT COUNT(*) FROM hareketler") == 1 and d.has_recovery(),
       "geri yükleme diske yazıldı; yedekteki şifre açıyor, kurtarma kilidi korundu")
    d.close()
    # yeni bilgisayar: yedek canlı kasa.kdb olarak kopyalanır, eski şifreyle girilir
    yeni = tmp / "yeni_pc" / "kasa.kdb"
    yeni.parent.mkdir()
    shutil.copyfile(res.path, yeni)
    d = Database(yeni)
    s = Service(d)
    ok(d.kilitli and s.users_exist() and s.setting("firma_adi") == "Test Kargo", "yeni bilgisayarda giriş ekranı")
    s.login("admin", "1234")
    ok(len(s.movements()) == 1 and s.caris()[0]["unvan"] == GIZLI, "yeni bilgisayarda eski şifreyle açıldı")
    d.close()
    d = Database(yeni)
    Service(d).reset_with_recovery_key(key, "admin", "4444")
    ok(not d.kilitli, "yeni bilgisayarda şifre unutulunca kurtarma anahtarı açtı")
    d.close()

    # başka kurulumun yedeği: o yedeğin şifresi ya da kurtarma anahtarı sorulur
    def diger_kurulum(ad: str, kurtarma: str | None = None, kasiyer: bool = False):
        d, s, k = kur(tmp / ad / "kasa.kdb", kullanici="patron", sifre="4321")
        if kurtarma:                               # aynı kurtarma anahtarıyla kurulmuş gibi
            with d.tx():
                d.set_recovery_key(kurtarma)
                s.set_setting("kurtarma_hash", svc.setting("kurtarma_hash"))
            k = kurtarma
        if kasiyer:
            s.create_user("kasa", "", "7777", "kullanici")
        for t in (11, 22, 33):
            hareket(s, t)
        b = BackupManager(d, s, tmp / ad / "yedek").backup("manuel").path
        kimlik = d.anahtar.kimlik
        d.close()
        return b, k, kimlik
    svc.login("admin", "1234")
    yerel_hash = svc.setting("kurtarma_hash")
    other, _, kimlik = diger_kurulum("ayni_anahtar", kurtarma=key)
    ok_, msg, info = validate_backup(other, db.anahtar)
    ok(not ok_ and info.get("anahtar_gerekli"), "başka kurulumun yedeği: şifre / anahtar istendi")
    raises(sifreleme.AnahtarGerekli, bm.restore, other)
    raises(sifreleme.AnahtarHatali, bm.restore, other, kullanici="admin", sifre="1234")
    ok(len(svc.movements()) == 1, "açılamayan yedek verilere dokunmadı")
    bm.restore(other, kullanici="patron", sifre="4321")
    ok(len(svc.movements()) == 3 and db.anahtar.kimlik == kimlik, "yedeğin şifresiyle geri yüklendi, onun anahtarı alındı")
    ok(db.has_recovery() and svc.setting("kurtarma_hash") == yerel_hash, "kurtarma anahtarı uyuşuyor: kilit kaldı")
    db.close()
    raises(sifreleme.AnahtarHatali, ac, db.path, "admin", "1234")
    d = Database(db.path)
    d.ac_kurtarma(key)
    ok(d.scalar("SELECT COUNT(*) FROM hareketler") == 3, "bu kurulumun kurtarma anahtarı açıyor")
    d.close()
    db = ac(db.path, "patron", "4321")
    svc = Service(db)
    svc.login("patron", "4321")
    bm = BackupManager(db, svc, tmp / "yedek")
    other2, key2, kimlik2 = diger_kurulum("baska")
    raises(sifreleme.AnahtarHatali, bm.restore, other2, kurtarma_anahtari=key)
    bm.restore(other2, kurtarma_anahtari=key2)
    h = baslik(db.path)
    ok(db.anahtar.kimlik == kimlik2 and set(h["kullanicilar"]) == {"patron"} and "kurtarma" in h,
       "kurtarma anahtarıyla geri yüklendi; yedeğin kilitleri ve kurtarma kilidi alındı")
    ok(db.has_recovery() and auth.verify_password(auth.normalize_key(key2), svc.setting("kurtarma_hash")),
       "kurtarma kilidi ve özeti birlikte yedeğin anahtarına ait")
    # yedeğin şifresini bilmeyen yönetici aynı oturumda da, yeniden açılışta da yedeğin kurtarma anahtarıyla girer
    key2b = svc.reset_with_recovery_key(key2, "patron", "8888")
    db.close()
    d = Database(db.path)
    s = Service(d)
    ok(d.kilitli and "kurtarma" in baslik(db.path), "yeniden açılışta kurtarma kilidi duruyor")
    s.reset_with_recovery_key(key2b, "patron", "8888")
    s.login("patron", "8888")
    ok(len(s.movements()) == 3, "kurtarma anahtarıyla geri yüklenen veri yeniden açılışta açıldı")
    # yedek, yönetici olmayan bir kullanıcının şifresiyle açıldı: yönetici sonra yedeğin kurtarma anahtarıyla girer
    other3, key3, kimlik3 = diger_kurulum("ucuncu", kasiyer=True)
    bm = BackupManager(d, s, tmp / "yedek")
    bm.restore(other3, kullanici="kasa", sifre="7777")
    ok(s.setting("son_kullanici") == "kasa", "giriş ekranı yedeği açan kullanıcıyı öneriyor")
    ok(d.anahtar.kimlik == kimlik3 and d.has_recovery()
       and auth.verify_password(auth.normalize_key(key3), s.setting("kurtarma_hash")),
       "kullanıcı şifresiyle açılan yedeğin kurtarma kilidi ve özeti kaldı")
    d.close()
    d = Database(db.path)
    Service(d).reset_with_recovery_key(key3, "patron", "5151")
    ok(not d.kilitli, "yönetici yedeğin kurtarma anahtarıyla girdi")
    d.close()
    ok(not ac(db.path, "kasa", "7777").kilitli, "geri yüklenen yedeğin şifresi açıyor")


def test_duz_yedek_geri_yukleme(tmp: Path):
    legacy, _ = _duz_kopya(tmp, kasiyer=True)    # yedekte admin / 1234, kasiyer / 1111
    old = tmp / "eski" / "kasa_2025-12-01_10-00-00_manuel.db"
    old.parent.mkdir()
    legacy.replace(old)
    db, svc, key = kur(tmp / "canli.kdb", sifre="5555")
    bm = BackupManager(db, svc, tmp / "yedek")
    v = validate_backup(old)
    ok(v[0] and not v[2]["sifreli"] and v[2]["hareket"] == 5, "düz yedek doğrulandı")
    # yedekteki yönetici şifresi (1234) canlı kilitten (5555) farklı: yedekteki bir yöneticinin şifresi istenir
    once = db.path.read_bytes()
    ok("yönetici" in raises(sifreleme.AnahtarGerekli, bm.restore, old), "yedekteki yönetici şifresi istendi")
    ok(raises(sifreleme.AnahtarHatali, bm.restore, old, yonetici=("admin", "5555")) == HATALI_GIRIS,
       "bugünkü şifre yedekteki özetle doğrulanmadı")
    raises(sifreleme.AnahtarHatali, bm.restore, old, yonetici=("kasiyer", "1111"))       # yönetici değil
    ok(db.path.read_bytes() == once and len(svc.movements()) == 0 and not (tmp / "yedek").exists(),
       "reddedilen geri yükleme hiçbir şeye dokunmadı")
    bm.restore(old, yonetici=("Admin", "1234"))
    ok(len(svc.movements()) == 5 and svc.caris()[0]["unvan"] == GIZLI, "düz yedek geri yüklendi")
    raw = db.path.read_bytes()
    ok(not raw.startswith(b"SQLite") and GIZLI.encode() not in raw, "geri yüklenen düz yedek şifreli saklandı")
    ok(old.exists(), "seçilen dosyaya dokunulmadı")
    h = baslik(db.path)
    ok(set(h["kullanicilar"]) == {"admin"} and "kurtarma" in h, "eski şifrenin kilidi kalmadı; doğrulanan yöneticinin "
                                                                 "kilidi aynı yazmada eklendi, kurtarma kilidi duruyor")
    ok(svc.kilitsiz_kullanicilar() == ["kasiyer"], "kilidi olmayan kullanıcı bildirildi")
    # program yeniden girişten önce kapansa da dosya yedekteki şifreyle açılır
    db.close()
    raises(sifreleme.AnahtarHatali, ac, db.path, "admin", "5555")
    db = Database(db.path)
    svc = Service(db)
    svc.login("admin", "1234")
    ok(len(svc.movements()) == 5, "yeniden açılışta yedekteki şifreyle girildi")
    # şifresi değişmemiş kullanıcıların kilitleri kalır; şifre sorulmaz
    duz = tmp / "eski" / "kasa_2025-12-02_10-00-00_manuel.db"
    duz.write_bytes(db.goruntu())
    hareket(svc, 9999)
    bm = BackupManager(db, svc, tmp / "yedek")
    bm.restore(duz)
    ok(len(svc.movements()) == 5 and set(baslik(db.path)["kullanicilar"]) == {"admin"},
       "aynı şifreli düz yedek doğrudan geri yüklendi, kilit kaldı")
    db.close()
    ok(not ac(db.path, "admin", "1234").kilitli, "geri yüklemeden sonra şifre açıyor")
    d = Database(db.path)
    d.ac_kurtarma(key)
    ok(not d.kilitli, "kurtarma anahtarı açıyor")
    d.close()


def test_eski_yedek_donusturme(tmp: Path):
    legacy, counts = _duz_kopya(tmp)
    plain = legacy.read_bytes()
    yd = tmp / "yedek"
    (yd / "Aylık").mkdir(parents=True)
    a = yd / "kasa_2026-01-05_10-00-00_cikis.db"
    m = yd / "Aylık" / "kasa_2026-01.db"
    bad = yd / "kasa_2026-01-06_10-00-00_cikis.db"
    degisik = yd / "kasa_2025-06-01_10-00-00_manuel.db"     # yöneticinin şifresi o zaman farklıymış (0000)
    other = yd / "notlar.db"
    for f in (a, m, other):
        f.write_bytes(plain)
    bad.write_bytes(plain[:3000])
    c = sqlite3.connect(":memory:")
    c.deserialize(plain)
    duz_hash = c.execute("SELECT sifre_hash FROM kullanicilar WHERE kullanici_adi='admin'").fetchone()[0]
    c.execute("UPDATE kullanicilar SET sifre_hash=? WHERE kullanici_adi='admin'", (auth.hash_password("0000"),))
    c.commit()
    degisik.write_bytes(c.serialize())
    c.close()
    old_time = time.time() - 86400 * 200
    os.utime(m, (old_time, old_time))
    db, svc, key = kur(tmp / "canli.kdb", recovery=False)
    # canlı veri, düz yedeklerin alındığı veritabanıdır: yöneticinin şifresi (özeti) o zamandan beri aynı
    db.x("UPDATE kullanicilar SET sifre_hash=? WHERE kullanici_adi='admin'", (duz_hash,))
    bm = BackupManager(db, svc, yd)
    ok(bm.convert_legacy() == (0, 0) and a.exists(), "kurtarma kilidi yokken dönüştürme yapılmaz")
    db.set_recovery_key(key)
    usb = tmp / "usb"
    svc.set_setting("ek_yedek_klasoru", str(usb))      # henüz takılı değil
    done, failed = bm.convert_legacy()
    ok(done == 3 and failed == 1, f"dönüştürülen / dönüştürülemeyen ({done}, {failed})")
    for f in (a, m):
        k = f.with_suffix(".kdb")
        ok(not f.exists() and k.exists(), f"düz yedek silindi, şifrelisi var: {f.name}")
        v = validate_backup(k, db.anahtar)
        ok(v[0] and v[2]["sifreli"] and v[2]["hareket"] == counts["hareketler"], f"dönüştürülen doğrulandı: {k.name}")
        ok(validate_backup(k, kullanici="admin", sifre="1234")[0], f"dönüştürülen program şifresiyle açılıyor: {k.name}")
        ok(not k.read_bytes().startswith(b"SQLite") and GIZLI.encode() not in k.read_bytes(), f"şifreli: {k.name}")
    # şifresi o yedekten beri değişen kullanıcının bugünkü kilidi dönüştürülen yedeğe taşınmaz
    dk = degisik.with_suffix(".kdb")
    ok(not degisik.exists() and validate_backup(dk, db.anahtar)[0] and "kullanicilar" not in baslik(dk)
       and "kurtarma" in baslik(dk), "şifresi değişen yöneticinin kilidi taşınmadı, kurtarma kilidi var")
    ok(validate_backup(dk, kullanici="admin", sifre="1234")[1] == HATALI_GIRIS
       and validate_backup(dk, kurtarma_anahtari=key)[0], "bugünkü şifre açmıyor, kurtarma anahtarı açıyor")
    ok(abs(m.with_suffix(".kdb").stat().st_mtime - old_time) < 2, "aylık arşivin tarihi korundu")
    ok(bad.exists() and bad.read_bytes() == plain[:3000], "bozuk düz yedek silinmedi")
    ok(other.exists(), "programa ait olmayan dosyaya dokunulmadı")
    # ek klasör takılınca ilk yedeklemede onun düz yedekleri de çevrilir
    ud = usb / "KasaTakip Yedekleri"
    (ud / "Aylık").mkdir(parents=True)
    u1, u2 = ud / "kasa_2026-01-05_10-00-00_manuel.db", ud / "Aylık" / "kasa_2025-11.db"
    u1.write_bytes(plain)
    u2.write_bytes(plain)
    bm.backup("manuel")
    ok(not u1.exists() and not u2.exists() and validate_backup(u1.with_suffix(".kdb"), db.anahtar)[0]
       and validate_backup(u2.with_suffix(".kdb"), db.anahtar)[0], "ek klasördeki düz yedekler yedeklemede çevrildi")
    # aynı adlı şifreli yedek zaten varsa (bu ayın yeni arşivi) düz olanı silinir
    mm = yd / "Aylık" / f"kasa_{dt.date.today():%Y-%m}.db"
    mm.write_bytes(plain)
    bm.convert_legacy()
    ok(not mm.exists() and validate_backup(mm.with_suffix(".kdb"), db.anahtar)[2]["hareket"] == 0,
       "aynı ayın düz arşivi silindi")
    # şifreli kopya doğrulanamazsa düz dosya silinmez
    a.write_bytes(plain)
    a.with_suffix(".kdb").unlink(missing_ok=True)        # saklama düzeni eski otomatik yedeği silmiş olabilir
    real = sifreleme.yaz
    sifreleme.yaz = lambda p, v: real(p, v[:-10] + b"0" * 10)      # bozuk yazma
    try:
        ok(bm.convert_legacy()[1] >= 1, "bozuk yazma başarısız sayıldı")
    finally:
        sifreleme.yaz = real
    ok(a.exists() and a.read_bytes() == plain and not a.with_suffix(".kdb").exists(),
       "doğrulanmayan dönüştürmede düz dosya korundu")
    # dönüştürülen yedeğin geri yüklenmesi: yöneticinin o zamanki şifresi istenir, kilit onunla yazılır
    raises(sifreleme.AnahtarGerekli, bm.restore, dk)
    bm.restore(dk, yonetici=("admin", "0000"))
    db.close()
    raises(sifreleme.AnahtarHatali, ac, db.path, "admin", "1234")
    d = Database(db.path)
    Service(d).login("admin", "0000")
    ok(d.scalar("SELECT COUNT(*) FROM hareketler") == counts["hareketler"], "yedeğin şifresiyle yeniden açıldı")
    d.close()


def test_saklama_duz_yedek(tmp: Path):
    db, svc, _ = kur(tmp / "kasa.kdb")
    bm = BackupManager(db, svc, tmp / "yedek")
    bm.backup_dir.mkdir()
    eski = (dt.date.today() - dt.timedelta(days=400)).isoformat()
    duz, sif = bm.backup_dir / f"kasa_{eski}_10-00-00_cikis.db", bm.backup_dir / f"kasa_{eski}_11-00-00_cikis.kdb"
    duz.write_bytes(db.goruntu())
    sif.write_bytes(b"x")
    silinen = []
    real = sifreleme.sifirla_sil
    sifreleme.sifirla_sil = lambda p: (silinen.append(Path(p).name), real(p))
    try:
        ok(bm.rotate(bm.backup_dir) == 2, "saklama süresi geçen iki yedek silindi")
    finally:
        sifreleme.sifirla_sil = real
    ok(silinen == [duz.name] and not duz.exists() and not sif.exists(),
       "saklama düzeni düz yedeğin üzerine sıfır yazarak sildi")
    db.close()


def test_uygulama_adimlari(tmp: Path):
    from kasatakip import app as app_mod
    from kasatakip import auth
    from tests.ornek_personel import ILK_PERSONEL
    from kasatakip.ui import dialogs

    app_mod.ILK_PERSONEL = ILK_PERSONEL            # uygulamadaki ilk liste boş; testte örnek liste yüklenir

    class Ctx:
        pass

    class Yollar:
        demo = False
    gosterilen, mesajlar = [], []

    class SahteAnahtarPenceresi:
        def __init__(self, parent, key, reason=""):
            gosterilen.append((key, reason))

        def exec(self):
            return 1
    db, svc, eski_key = kur(tmp / "kasa.kdb", recovery=False)
    svc.login("admin", "1234")
    ctx = Ctx()
    ctx.db, ctx.svc = db, svc
    ctrl = app_mod.AppController(None, ctx)
    eski_hash = svc.setting("kurtarma_hash")
    real_dlg, real_yaz, real_sar = dialogs.RecoveryKeyDialog, sifreleme.yaz, db.set_recovery_key
    dialogs.RecoveryKeyDialog = SahteAnahtarPenceresi
    try:
        # kayıt diske yazılamazsa: anahtar değişmez, gösterilmez (eski anahtar geçerli kalır)
        sifreleme.yaz = lambda p, v: (_ for _ in ()).throw(OSError("disk dolu"))
        try:
            ctrl._sifreleme_kurtarma()
        finally:
            sifreleme.yaz = real_yaz
        ok(not gosterilen and svc.setting("kurtarma_hash") == eski_hash and not db.has_recovery(),
           "yazma hatasında kurtarma anahtarı değişmedi")
        # kilit bağlanamazsa: yeni anahtarın özeti de geri alınır
        db.set_recovery_key = lambda key: (_ for _ in ()).throw(sifreleme.KayitHatasi("kilit hatası"))
        try:
            ctrl._sifreleme_kurtarma()
        finally:
            db.set_recovery_key = real_sar
        ok(not gosterilen and svc.setting("kurtarma_hash") == eski_hash, "kilit hatasında özet de geri alındı")
        ok(auth.verify_password(auth.normalize_key(eski_key), svc.setting("kurtarma_hash")), "eski anahtar geçerli")
        ctrl._sifreleme_kurtarma()
    finally:
        dialogs.RecoveryKeyDialog = real_dlg
    ok(len(gosterilen) == 1 and gosterilen[0][1] == app_mod.SIFRELEME_KURTARMA_METNI, "yeni anahtar bir kez gösterildi")
    ok("program şifreleriyle" in app_mod.SIFRELEME_KURTARMA_METNI and "yalnızca bu" in app_mod.SIFRELEME_KURTARMA_METNI,
       "metin: veriler program şifreleriyle açılır, şifreler unutulursa yalnız bu anahtar")
    yeni = gosterilen[0][0]
    ok(db.has_recovery() and auth.verify_password(auth.normalize_key(yeni), svc.setting("kurtarma_hash")),
       "yeni anahtarın özeti ve kilidi birlikte kaydedildi")
    db.close()
    d = Database(db.path)
    d.ac_kurtarma(yeni)                            # tüm şifreler unutulursa / başka bilgisayarda yeni anahtar açar
    ok(not d.kilitli, "yeni kurtarma anahtarı dosyayı açıyor")
    d.close()

    # personel listesi girişten sonra, veritabanı açıkken yüklenir
    db = Database(db.path)
    svc = Service(db)
    ctx.db, ctx.svc, ctx.paths, ctx.backups = db, svc, Yollar(), BackupManager(db, svc, tmp / "yedek")
    raises(VeritabaniKilitli, svc.ilk_personel_yukle, ILK_PERSONEL)
    svc.login("admin", "1234")
    ctrl = app_mod.AppController(None, ctx)
    ctrl._mesaj = lambda icon, text: mesajlar.append(text)
    ctrl._giris_sonrasi()
    ok(db.scalar("SELECT COUNT(*) FROM personeller") == len(ILK_PERSONEL) and not mesajlar and len(gosterilen) == 1,
       "girişten sonra personel listesi yüklendi (başka adım yok)")
    db.close()

    # 1.1.x düz veritabanı: yönetici olmayan giriş geçiş yapmaz; yönetici girişinden sonra kurtarma anahtarı bir
    # kez gösterilir, kilidi olmayan kullanıcılar bildirilir, eski düz yedekler çevrilir
    g = tmp / "g"
    g.mkdir()
    legacy, _ = _duz_kopya(g, kasiyer=True)
    kdb = legacy.with_suffix(".kdb")
    db = Database(kdb)
    svc = Service(db)
    Yollar.demo = True
    ctx.db, ctx.svc, ctx.paths, ctx.backups = db, svc, Yollar(), BackupManager(db, svc, g / "yedek")
    ctrl = app_mod.AppController(None, ctx)
    ctrl._mesaj = lambda icon, text: mesajlar.append(text)
    dialogs.RecoveryKeyDialog = SahteAnahtarPenceresi
    try:
        svc.login("kasiyer", "1111")
        ctrl._giris_sonrasi()
        duz_yedek = ctx.backups.backup("manuel").path
        ok(not kdb.exists() and not db.sifreli and len(gosterilen) == 1 and not mesajlar and duz_yedek.suffix == ".db",
           "kullanıcı girişinde geçiş, kurtarma adımı ve bildirim yok")
        svc.logout()
        svc.login("admin", "1234")
        ctrl._giris_sonrasi()
    finally:
        dialogs.RecoveryKeyDialog = real_dlg
    ok(db.sifreli and kdb.exists() and not legacy.exists(), "yönetici girişinde şifreye geçildi")
    ok(len(gosterilen) == 2 and gosterilen[1][1] == app_mod.SIFRELEME_KURTARMA_METNI and db.has_recovery(),
       "geçişten sonra yeni kurtarma anahtarı bir kez gösterildi")
    ok(len(mesajlar) == 1 and "kasiyer" in mesajlar[0] and "şifrelerini Kullanıcılar ekranından sıfırlayın" in mesajlar[0],
       f"kilidi olmayan kullanıcılar tek mesajda bildirildi: {mesajlar}")
    ok(not duz_yedek.exists() and validate_backup(duz_yedek.with_suffix(".kdb"), db.anahtar)[0],
       "eski düz yedek geçişten sonra şifrelendi")
    db.close()
    d = Database(kdb)
    d.ac_kurtarma(gosterilen[1][0])
    ok(not d.kilitli, "gösterilen kurtarma anahtarı şifreye çevrilen dosyayı açıyor")
    d.close()


def test_hiz(tmp: Path):
    db, svc, _ = kur(tmp / "kasa.kdb")
    with db.tx():
        db.x("CREATE TABLE dolgu(x BLOB)")
        for _ in range(20):
            db.x("INSERT INTO dolgu VALUES(?)", (os.urandom(1_000_000),))
    size = db.path.stat().st_size
    ok(size > 20_000_000, f"~20 MB veritabanı ({size} bayt)")
    times = []
    for i in range(3):
        t = time.perf_counter()
        hareket(svc, 100 + i)
        times.append(time.perf_counter() - t)
    print(f"  20 MB kayıt süresi: {min(times):.3f} sn (en kötü {max(times):.3f} sn)")
    ok(max(times) < 1.5, f"20 MB veritabanında kayıt 1,5 sn'den kısa ({max(times):.2f} sn)")
    t = time.perf_counter()
    db.close()
    ok(Database(tmp / "kasa.kdb").kilitli and time.perf_counter() - t < 1, "kilitli açılış yalnız başlığı okuyor")
    ac(tmp / "kasa.kdb").close()
    ok(time.perf_counter() - t < 3, "20 MB veritabanı şifreyle hızlı açılıyor")


if __name__ == "__main__":
    import logging
    logging.getLogger("kasatakip").addHandler(logging.NullHandler())
    logging.getLogger("kasatakip").propagate = False          # beklenen hata kayıtları ekrana basılmasın
    test_kap()
    for fn in (test_canli, test_gecici_kilit, test_kullanici_kilitleri, test_kurtarma_ile_acilis,
               test_ayni_katlanmis_ad, test_yarida_kesilen_yazma, test_gecis, test_gecis_v1, test_duz_kdb,
               test_okuma_hatasi, test_yedekler, test_duz_yedek_geri_yukleme, test_eski_yedek_donusturme,
               test_eski_yedek_kurtarma, test_donusturulen_yedek_kurtarma_ozeti, test_saklama_duz_yedek, test_uygulama_adimlari, test_hiz):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            fn(Path(t))
    print(f"TAMAM: {PASS} kontrol geçti")
