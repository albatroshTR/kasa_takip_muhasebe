"""Çekirdek iş mantığı testleri.  Çalıştırma:  python -m tests.test_core"""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kasatakip.backup import BackupManager, validate_backup  # noqa: E402
from kasatakip.db import Database  # noqa: E402
from kasatakip.errors import AuthError, PermissionDenied, ServiceError  # noqa: E402
from kasatakip.paste import (Baglam, hazirla_cariler, hazirla_hareketler, infer_cari_mapping,  # noqa: E402
                             infer_hareket_mapping, match_odeme, parse_table_text, to_tsv)
from kasatakip.services import Service  # noqa: E402
from kasatakip.util import fmt_money, parse_amount, parse_date  # noqa: E402

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


def test_util():
    ok(parse_amount("1.250,50") == 125050, "tr para")
    ok(parse_amount("1,250.50") == 125050, "en para")
    ok(parse_amount("1250") == 125000, "tam")
    ok(parse_amount("1.250") == 125000, "binlik nokta")
    ok(parse_amount("12,5") == 1250, "ondalık virgül")
    ok(parse_amount("12.5") == 1250, "ondalık nokta")
    ok(parse_amount("1.250,00 ₺") == 125000, "sembol")
    ok(parse_amount("-75,30") == -7530, "negatif")
    ok(parse_amount("(100)") == -10000, "parantez")
    ok(parse_amount("abc") is None, "geçersiz")
    ok(fmt_money(125050) == "1.250,50", "biçim")
    ok(fmt_money(-5) == "-0,05", "negatif biçim")
    ok(fmt_money(1234567890) == "12.345.678,90", "büyük")
    ok(parse_date("28.10.2026") == dt.date(2026, 10, 28), "tarih1")
    ok(parse_date("2026-10-28") == dt.date(2026, 10, 28), "tarih2")
    ok(parse_date("28/10/26") == dt.date(2026, 10, 28), "tarih3")
    ok(parse_date("28 Ekim 2026") == dt.date(2026, 10, 28), "tarih4")
    ok(parse_date("5.1.2026") == dt.date(2026, 1, 5), "tarih5")


def make(tmp: Path):
    db = Database(tmp / "kasa.db")
    svc = Service(db)
    return db, svc


def test_flow(tmp: Path):
    db, svc = make(tmp)
    ok(not svc.users_exist(), "ilk kurulum öncesi")
    key = svc.setup_first_admin("admin", "Yönetici", "1234", "Test Kargo")
    ok(len(key) == 19, "kurtarma anahtarı")
    svc.logout()
    raises(AuthError, svc.login, "admin", "yanlis")
    svc.login("ADMIN", "1234")   # büyük/küçük harf duyarsız kullanıcı adı
    ok(svc.is_admin, "yönetici")

    cats = {(c["tur"], c["ad"]): c["id"] for c in svc.categories()}
    pts = {p["ad"]: p["id"] for p in svc.payment_types()}
    ok(len(cats) == 23 and len(pts) == 4, "varsayılan tanımlar")
    svc.update_payment_type(pts["Nakit"], "Nakit", 100000)      # 1.000,00 açılış
    svc.update_payment_type(pts["Havale"], "Havale", 500000)

    d1, d2 = dt.date(2026, 10, 1), dt.date(2026, 10, 2)
    m1 = svc.add_movement({"tarih": d1, "tur": "GELIR", "kategori_id": cats[("GELIR", "Kargo Geliri")],
                           "odeme_tipi_id": pts["Nakit"], "tutar": 250000, "aciklama": "Gişe"})
    svc.add_movement({"tarih": d1, "tur": "GELIR", "kategori_id": cats[("GELIR", "Kargo Geliri")],
                      "odeme_tipi_id": pts["Kart"], "tutar": 120000})
    svc.add_movement({"tarih": d1, "tur": "GIDER", "kategori_id": cats[("GIDER", "Yakıt")],
                      "odeme_tipi_id": pts["Nakit"], "tutar": 30000})
    svc.add_movement({"tarih": d1, "tur": "VIRMAN", "odeme_tipi_id": pts["Nakit"],
                      "hedef_odeme_tipi_id": pts["Havale"], "tutar": 200000, "aciklama": "Bankaya yatan"})
    raises(ServiceError, svc.add_movement, {"tarih": d1, "tur": "GIDER", "kategori_id": cats[("GELIR", "Diğer")],
                                            "odeme_tipi_id": pts["Nakit"], "tutar": 100})
    raises(ServiceError, svc.add_movement, {"tarih": d1, "tur": "VIRMAN", "odeme_tipi_id": pts["Nakit"],
                                            "hedef_odeme_tipi_id": pts["Nakit"], "tutar": 100})

    rep = svc.day_report(d1)
    by = {r["ad"]: r for r in rep["odeme"]}
    ok(by["Nakit"]["devir"] == 100000, "nakit devir")
    ok(by["Nakit"]["gun_sonu"] == 100000 + 250000 - 30000 - 200000, "nakit gün sonu")
    ok(by["Havale"]["gun_sonu"] == 500000 + 200000, "havale gün sonu")
    ok(by["Kart"]["gun_sonu"] == 120000, "kart gün sonu")
    ok(rep["gelir"] == 370000 and rep["gider"] == 30000, "gün toplamları")
    ok(rep["ort_gelir"] == 185000, "ortalama gelir")

    # cari
    musteri = svc.save_cari({"unvan": "Konya Tekstil A.Ş.", "tip": "MUSTERI"},
                            acilis={"tutar": 50000, "taraf": "BORC", "tarih": dt.date(2026, 9, 30)})
    ted = svc.save_cari({"unvan": "Opet Akaryakıt", "tip": "TEDARIKCI"})
    raises(ServiceError, svc.save_cari, {"unvan": "konya tekstil a.ş."})
    ok(svc.get_cari(musteri)["kod"] == "M0001", "cari kodu")
    svc.add_movement({"tarih": d2, "tur": "CARI_BORC", "cari_id": musteri, "tutar": 300000,
                      "aciklama": "Ekim kargo bedeli"})
    svc.add_movement({"tarih": d2, "tur": "GELIR", "kategori_id": cats[("GELIR", "Havale/EFT Tahsilatı")],
                      "odeme_tipi_id": pts["Havale"], "cari_id": musteri, "tutar": 200000})
    svc.add_movement({"tarih": d2, "tur": "CARI_ALACAK", "cari_id": ted, "tutar": 80000, "aciklama": "Yakıt faturası"})
    svc.add_movement({"tarih": d2, "tur": "GIDER", "kategori_id": cats[("GIDER", "Yakıt")],
                      "odeme_tipi_id": pts["Havale"], "cari_id": ted, "tutar": 50000})
    c1, c2 = svc.get_cari(musteri), svc.get_cari(ted)
    ok(c1["bakiye"] == 50000 + 300000 - 200000, "müşteri bakiye")
    ok(c2["bakiye"] == -80000 + 50000, "tedarikçi bakiye")
    st = svc.cari_statement(musteri, dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    ok(st["devir"] == 50000 and st["bakiye"] == 150000 and len(st["satirlar"]) == 2, "ekstre")
    tot = svc.cari_totals()
    ok(tot["alacaklarimiz"] == 150000 and tot["borclarimiz"] == 30000, "cari toplamlar")
    raises(ServiceError, svc.delete_cari, musteri)

    # kapanış ve kilit
    svc.close_day(d1, {pts["Nakit"]: 119500}, "Sayımda 5 TL eksik")
    rep = svc.day_report(d1)
    nak = [r for r in rep["odeme"] if r["ad"] == "Nakit"][0]
    ok(rep["kapali"] and nak["fark"] == -500, "sayım farkı")
    raises(ServiceError, svc.update_movement, m1, {"tarih": d1, "tur": "GELIR",
                                                   "kategori_id": cats[("GELIR", "Kargo Geliri")],
                                                   "odeme_tipi_id": pts["Nakit"], "tutar": 1})
    raises(ServiceError, svc.delete_movement, m1)
    raises(ServiceError, svc.add_movement, {"tarih": d1, "tur": "GELIR", "kategori_id": cats[("GELIR", "Diğer")],
                                            "odeme_tipi_id": pts["Nakit"], "tutar": 100})
    # cari fişleri kilitten etkilenmez
    svc.add_movement({"tarih": d1, "tur": "CARI_BORC", "cari_id": musteri, "tutar": 100})
    ok(svc.day_report(d2)["odeme"][0]["devir"] == 120000, "kapanış sonrası devir")

    # kullanıcı rolleri
    svc.create_user("kasiyer", "Ayşe", "1111", "kullanici")
    svc.logout()
    svc.login("kasiyer", "1111")
    ok(svc.user["sifre_degistirmeli"] == 1, "ilk girişte şifre değişmeli")
    svc.change_own_password("", "2222", check_old=False)
    raises(PermissionDenied, svc.add_category, "GELIR", "Yeni")
    raises(PermissionDenied, svc.reopen_from, d1)
    svc.close_day(d2)
    raises(ServiceError, svc.close_day, d1)
    svc.logout()
    svc.login("admin", "1234")
    days = svc.reopen_from(d1)
    ok(days == ["2026-10-01", "2026-10-02"], "yeniden açma")
    svc.update_movement(m1, {"tarih": d1, "tur": "GELIR", "kategori_id": cats[("GELIR", "Kargo Geliri")],
                             "odeme_tipi_id": pts["Nakit"], "tutar": 260000, "aciklama": "Düzeltildi"})
    ok(svc.get_movement(m1)["tutar"] == 260000, "güncelleme")
    raises(ServiceError, svc.update_user, svc.user["id"], "x", "kullanici", True)

    # tanımlar
    cid = svc.add_category("GIDER", "Temizlik")
    raises(ServiceError, svc.add_category, "GIDER", "temizlik")
    svc.move_category(cid, -1)
    svc.delete_category(cid)
    raises(ServiceError, svc.delete_category, cats[("GIDER", "Yakıt")])

    # analiz
    s = svc.summary(dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    ok(s["gelir"] == 260000 + 120000 + 200000 and s["gider"] == 30000 + 50000, "dönem özeti")
    grp, ser = svc.series(dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    ok(grp == "day" and ser[0]["gelir"] == 380000, "seri")
    ok(len(svc.category_totals(dt.date(2026, 10, 1), dt.date(2026, 10, 31), "GELIR")) == 2, "kategori toplam")

    # kurtarma anahtarı
    svc.logout()
    raises(AuthError, svc.reset_with_recovery_key, "AAAA-BBBB-CCCC-DDDD", "admin", "9999")
    new_key = svc.reset_with_recovery_key(key.lower().replace("-", " "), "admin", "9999")
    ok(new_key != key, "yeni anahtar")
    svc.login("admin", "9999")

    # yedek ve geri yükleme
    bm = BackupManager(db, svc, tmp / "yedek")
    res = bm.backup("manuel")
    ok(res.path.exists() and validate_backup(res.path, db.anahtar)[0], "yedek")
    count_before = len(svc.movements())
    svc.add_movement({"tarih": d2, "tur": "GELIR", "kategori_id": cats[("GELIR", "Diğer")],
                      "odeme_tipi_id": pts["Nakit"], "tutar": 999})
    ok(len(svc.movements()) == count_before + 1, "yedek sonrası kayıt")
    bm.restore(res.path)
    ok(len(svc.movements()) == count_before, "geri yükleme")
    ok(any(b["tur"] == "geriyukleme" for b in bm.list_backups()), "geri yükleme öncesi yedek")

    # saklama düzeni
    old = tmp / "yedek" / "kasa_2026-01-05_10-00-00_cikis.kdb"
    old2 = tmp / "yedek" / "kasa_2026-01-05_18-00-00_cikis.kdb"
    man = tmp / "yedek" / "kasa_2020-01-05_18-00-00_manuel.kdb"
    for f in (old, old2, man):
        f.write_bytes(res.path.read_bytes())
    bm.rotate(tmp / "yedek")
    ok(man.exists(), "manuel yedek korunur")
    ok(not old.exists() or not old2.exists(), "eski otomatik yedek temizlenir")
    db.close()


def test_paste(tmp: Path):
    db, svc = make(tmp)
    svc.setup_first_admin("admin", "Y", "1234")
    svc.save_cari({"unvan": "Meram Eczanesi", "tip": "MUSTERI"})
    ctx = Baglam.from_service(svc)
    text = ("Tarih\tTür\tKategori\tÖdeme\tTutar\tAçıklama\tCari\n"
            "01.10.2026\tGelir\tKargo Geliri\tNakit\t1.250,50\tGişe\t\n"
            "01.10.2026\tGider\tYakit\tKredi Kartı\t300\tAkaryakıt\t\n"
            "02.10.2026\tGelir\tHavale/EFT Tahsilatı\tHavale\t5000\tTahsilat\tMeram Eczanesi\n"
            "xx\tGelir\tKargo Geliri\tNakit\t10\t\t\n"
            "02.10.2026\tGider\tYeni Kalem\tNakit\t10\t\t\n")
    rows = parse_table_text(text)
    mapping, header = infer_hareket_mapping(rows, ctx)
    ok(header and mapping == ["tarih", "tur", "kategori", "odeme", "tutar", "aciklama", "cari"],
       f"başlık eşleştirme {mapping}")
    sec ={"baslik_var": header, "varsayilan_tarih": dt.date(2026, 10, 3), "varsayilan_tur": "GELIR",
           "varsayilan_odeme_id": None, "kategori_olustur": False, "cari_olustur": False}
    res = hazirla_hareketler(rows, mapping, ctx, sec)
    ok([r.gecerli for r in res] == [True, True, True, False, False], f"satır doğrulama {[r.hata for r in res]}")
    ok(res[0].veri["tutar"] == 125050 and res[2].veri["cari_id"], "dönüştürme")
    sec["kategori_olustur"] = True
    res = hazirla_hareketler(rows, mapping, ctx, sec)
    ok(res[4].gecerli and res[4].yeni_kategori == ("GIDER", "Yeni Kalem"), "yeni kategori")
    n = svc.import_movements([{"veri": r.veri, "yeni_kategori": r.yeni_kategori, "yeni_cari": r.yeni_cari}
                              for r in res if r.gecerli])
    ok(n == 4 and len(svc.movements()) == 4, "toplu kayıt")

    # başlıksız, gelir/gider sütunlu kasa defteri
    text2 = "03.10.2026\tGişe tahsilatı\t1500\t\n03.10.2026\tMazot\t\t750,25\n"
    rows2 = parse_table_text(text2)
    ctx = Baglam.from_service(svc)
    mapping2, header2 = infer_hareket_mapping(rows2, ctx)
    ok(not header2 and mapping2 == ["tarih", "aciklama", "gelir_tutar", "gider_tutar"], f"içerikten tahmin {mapping2}")
    sec2 = {"baslik_var": False, "varsayilan_tarih": dt.date(2026, 10, 3), "varsayilan_tur": "GELIR",
            "varsayilan_odeme_id": ctx.odemeler["nakit"][0], "kategori_olustur": False, "cari_olustur": False}
    res2 = hazirla_hareketler(rows2, mapping2, ctx, sec2)
    ok(res2[0].veri["tur"] == "GELIR" and res2[1].veri["tur"] == "GIDER" and res2[1].veri["tutar"] == 75025,
       "gelir/gider sütunları")

    # cariler
    text3 = "Ünvan\tTelefon\tBakiye\nKaratay Gıda\t0332 111 22 33\t1.500,00\nMeram Eczanesi\t\t\nYılmaz Nakliyat\t\t-200\n"
    rows3 = parse_table_text(text3)
    m3, h3 = infer_cari_mapping(rows3)
    ok(h3 and m3 == ["unvan", "telefon", "bakiye"], "cari başlık")
    res3 = hazirla_cariler(rows3, m3, Baglam.from_service(svc), {"baslik_var": True, "varsayilan_tip": "MUSTERI",
                                                                    "acilis_tarihi": dt.date(2026, 9, 30)})
    ok([r.gecerli for r in res3] == [True, False, True], "cari doğrulama")
    svc.import_caris([{"veri": r.veri, "acilis": r.acilis} for r in res3 if r.gecerli])
    ok({c["unvan"]: c["bakiye"] for c in svc.caris()}["Yılmaz Nakliyat"] == -20000, "cari açılış")
    ok(to_tsv([["a", "b\tc"], ["1", "2"]]) == "a\tb c\r\n1\t2", "tsv")
    db.close()


def test_review_regressions(tmp: Path):
    """Bağımsız incelemede bulunan sorunların regresyon testleri."""
    import shutil
    import sqlite3
    from kasatakip import excel, reports_pdf
    from kasatakip.util import today as _today

    db, svc = make(tmp)
    svc.setup_first_admin("admin", "Y", "1234", "Test")
    pts = {p["ad"]: p["id"] for p in svc.payment_types()}
    cats = {(c["tur"], c["ad"]): c["id"] for c in svc.categories()}
    gelir = lambda d, t, **k: svc.add_movement({"tarih": d, "tur": "GELIR", "tutar": t,  # noqa: E731
                                                 "kategori_id": cats[("GELIR", "Kargo Geliri")],
                                                 "odeme_tipi_id": pts["Nakit"], **k})
    d0 = dt.date(2026, 9, 1)

    # 1) kayıt diske yazılamayınca işlem geri alınmalı, bağlantı kilitli kalmamalı
    #    (veritabanı bellekte çalışır; diske yazma şifreli kabın güvenli yazılmasıdır)
    import logging
    from kasatakip import sifreleme
    real_yaz = sifreleme.yaz

    def bozuk_yaz(*a, **k):
        raise OSError("disk dolu (deneme)")
    sifreleme.yaz = bozuk_yaz
    logging.getLogger("kasatakip").disabled = True        # beklenen hata kaydı ekrana basılmasın
    try:
        gelir(d0, 100)
        failed = False
    except sifreleme.KayitHatasi:
        failed = True
    finally:
        sifreleme.yaz = real_yaz
        logging.getLogger("kasatakip").disabled = False
    ok(failed and not db.conn.in_transaction, "commit hatası geri alındı")
    ok(len(svc.movements()) == 0, "yazılamayan kayıt bellekte de geri alındı")
    gelir(d0, 200)
    ok(len(svc.movements()) == 1, "commit hatası sonrası yeni kayıt çalışıyor")

    # iç içe işlem: iç blok hatası yalnız kendi işini geri alır
    try:
        with db.tx():
            gelir(d0, 300)
            try:
                with db.tx():
                    gelir(d0, 400)
                    raise ValueError("iç hata")
            except ValueError:
                pass
    except Exception:
        pass
    ok(sorted(m["tutar"] for m in svc.movements()) == [200, 300], "savepoint ile kısmi geri alma")

    # 2) canlı dosya şifreli; başka bir program onu SQLite olarak açıp kilitleyemez
    locker = sqlite3.connect(str(db.path))
    raises(sqlite3.DatabaseError, locker.execute, "BEGIN EXCLUSIVE")
    locker.close()
    ok(not list(tmp.glob("*-journal")) and not list(tmp.glob("*.yazim")), "diskte günlük / geçici dosya yok")

    # 3) aylık arşivin geri yüklenmesi gerçekten geri yükler
    bm = BackupManager(db, svc, tmp / "yedek")
    bm.backup("manuel")
    n_backup = len(svc.movements())
    gelir(d0, 500)
    gelir(d0, 600)
    aylik = [b for b in bm.list_backups() if b["tur"] == "aylik"][0]
    bm.restore(aylik["path"])
    ok(len(svc.movements()) == n_backup, "aylık arşiv geri yüklendi")

    # 4) saklama düzeni geri yüklenen dosyayı silmez; makine ayarları korunur
    src = bm.list_backups()[0]["path"]
    old_day = (_today() - dt.timedelta(days=10)).isoformat()
    f1 = tmp / "yedek" / f"kasa_{old_day}_09-00-00_cikis.kdb"
    f2 = tmp / "yedek" / f"kasa_{old_day}_18-00-00_cikis.kdb"
    shutil.copy2(src, f1)
    shutil.copy2(src, f2)
    svc.set_setting("ek_yedek_klasoru", str(tmp / "usb"))
    svc.set_setting("yedek_saklama_gun", "365")
    bm.restore(f1)
    ok(f1.exists(), "geri yüklenen eski yedek silinmedi")
    ok(svc.setting("ek_yedek_klasoru") == str(tmp / "usb") and svc.setting("yedek_saklama_gun") == "365",
       "geri yüklemede makine ayarları korundu")
    raises(ServiceError, bm.restore, db.path)                      # canlı veritabanı seçilemez
    v0 = tmp / "v0.kdb"                                            # yedekler şifreli: bellekte değiştirip yeniden şifrele
    c0 = sqlite3.connect(":memory:")
    c0.deserialize(sifreleme.oku(src, db.anahtar)[0])
    c0.execute("PRAGMA user_version = 0")
    v0.write_bytes(db.paketle(c0.serialize()))
    c0.close()
    ok(not validate_backup(v0, db.anahtar)[0], "sürüm bilgisi olmayan dosya reddedildi")

    # 5) tutar ayrıştırma katı
    for bad in ("1,250", "1.000.50", "1,250,50", "1.250.00", "1.2.3", "10,999", "0,005", "120.01.001", "12a"):
        ok(parse_amount(bad) is None, f"belirsiz/bozuk tutar reddedildi: {bad}")
    for good, val in (("1.250", 125000), ("1.250,5", 125050), ("1,250.50", 125050), ("1.000.000", 100000000),
                      ("1,000,000", 100000000), ("12,5", 1250), ("1250", 125000), ("-75,30", -7530)):
        ok(parse_amount(good) == val, f"geçerli tutar: {good}")

    # 6) yapıştırma: muhasebe biçimi '-' ve işaretler
    ctx = Baglam.from_service(svc)
    base = {"baslik_var": True, "varsayilan_tarih": dt.date(2026, 10, 3), "varsayilan_tur": "GELIR",
            "varsayilan_odeme_id": pts["Nakit"], "kategori_olustur": False, "cari_olustur": False}
    rows = parse_table_text("Tarih\tAçıklama\tGelir\tGider\n01.10.2026\tGişe\t1.500,00\t-\n"
                            "\tGişe 2\t2.000,00\t-\n01.10.2026\tMazot\t-\t350,25\n02.10.2026\tİade\t-200\t-\n")
    m, h = infer_hareket_mapping(rows, ctx)
    ok(m == ["tarih", "aciklama", "gelir_tutar", "gider_tutar"], f"tire'li sütunlar tutar kaldı {m}")
    res = hazirla_hareketler(rows, m, ctx, base)
    ok([r.veri.get("tur") for r in res[:3]] == ["GELIR", "GELIR", "GIDER"], "tür doğru")
    ok(res[1].veri["tarih"] == "2026-10-01", "boş tarih üst satırdan alındı")
    ok(not res[3].gecerli, "gelir sütununda eksi tutar reddedildi")
    rows = parse_table_text("Tür\tTutar\nGelir\t-200\n")
    res = hazirla_hareketler(rows, ["tur", "tutar"], ctx, base)
    ok(not res[0].gecerli, "tür+eksi tutar reddedildi")
    rows = parse_table_text("Tarih\tTutar\n01.10.2026\t500\n01.10.2026\t-120\n")
    res = hazirla_hareketler(rows, ["tarih", "tutar"], ctx, dict(base, varsayilan_tur="GIDER"))
    ok([r.veri["tur"] for r in res] == ["GELIR", "GIDER"], "işaretli tutar sütunu")
    rows = parse_table_text("Tarih\tTutar\n28.10.2206\t500\n")
    ok(not hazirla_hareketler(rows, ["tarih", "tutar"], ctx, base)[0].gecerli, "aralık dışı tarih reddedildi")
    ok(parse_date("31.12") <= _today(), "yılsız tarih gelecekte değil")

    # 10) hücre başında tırnak sonraki satırları yutmaz
    rows = parse_table_text('01.10.2026\t"\t100\n01.10.2026\t"Acil gönderi\t200\n01.10.2026\tx\t300\n'
                            '01.10.2026\t"\t400\n')
    ok(len(rows) == 4 and rows[1][1] == '"Acil gönderi', "tırnaklı hücreler")
    rows = parse_table_text('A\t"çok\nsatırlı"\tB\nC\tD\tE\n')
    ok(rows == [["A", "çok\nsatırlı", "B"], ["C", "D", "E"]], "Excel çok satırlı hücre")

    # 13) pasif 'Diğer' ve belirsiz ödeme takma adı
    svc.set_category_active(cats[("GIDER", "Diğer")], False)
    ctx = Baglam.from_service(svc)
    res = hazirla_hareketler(parse_table_text("Tutar\n100\n"), ["tutar"], ctx, dict(base, varsayilan_tur="GIDER"))
    ok(not res[0].gecerli, "pasif Diğer önizlemede yakalandı")
    svc.add_payment_type("Kart Ziraat")
    svc.add_payment_type("Kart Akbank")
    ok(match_odeme("POS", Baglam.from_service(svc)) is None, "belirsiz takma ad reddedildi")

    # 7) cari yapıştırma: bakiye iki kez sayılmaz, kod bakiye sanılmaz
    rows = parse_table_text("Kod\tÜnvan\tBorç\tAlacak\tBakiye (+B / −A)\nX1\tA Ltd\t1.000,00\t\t1.000,00\n"
                            "X2\tB Ltd\t\t800,00\t-800,00\n")
    m, h = infer_cari_mapping(rows)
    res = hazirla_cariler(rows, m, Baglam.from_service(svc), {"baslik_var": h, "varsayilan_tip": "MUSTERI",
                                                                 "acilis_tarihi": d0})
    ok(res[0].acilis["tutar"] == 100000 and res[1].acilis == {"tutar": 80000, "taraf": "ALACAK", "tarih": d0},
       "cari açılış bakiyesi tek sayıldı")
    m, h = infer_cari_mapping(parse_table_text("1001\tKaratay Gıda\n1002\tMeram Ecza\n"))
    ok("bakiye" not in m and m[:2] == ["kod", "unvan"], f"başlıksız listede kod {m}")
    ok(infer_cari_mapping(parse_table_text("Ünvan\tTip\nA\tMüşteri + Tedarikçi\n"))[0] == ["unvan", "tip"]
       and hazirla_cariler(parse_table_text("Ünvan\tTip\nZ A.Ş.\tMüşteri + Tedarikçi\n"), ["unvan", "tip"],
                           Baglam.from_service(svc), {"baslik_var": True})[0].veri["tip"] == "HER_IKISI",
       "her ikisi tipi")

    # 8) açılış bakiyesi değişikliği kapalı günü değiştirmez
    dk = dt.date(2026, 9, 2)
    gelir(dk, 1000)
    rep = svc.day_report(dk)
    nak = [r for r in rep["odeme"] if r["ad"] == "Nakit"][0]
    svc.close_day(dk, {pts["Nakit"]: nak["gun_sonu"]})
    svc.update_payment_type(pts["Nakit"], "Nakit", 999900)
    after = [r for r in svc.day_report(dk)["odeme"] if r["ad"] == "Nakit"][0]
    ok(after["devir"] == nak["devir"] and after["gun_sonu"] == nak["gun_sonu"] and after["fark"] == 0,
       "kapalı gün açılış değişikliğinden etkilenmedi")
    ok(svc.day_report(dk + dt.timedelta(days=1))["odeme"][0]["devir"] == nak["gun_sonu"] + 999900,
       "açık günlerde yeni açılış bakiyesi geçerli")

    # 14) uzun sayı aramada çökmez
    ok(svc.movements(search="1234567890123456789012") == [], "uzun sayı araması")
    raises(ServiceError, gelir, dt.date(1998, 1, 1), 100)

    # 15) çok kategorili günlük PDF ve Excel formül koruması
    for i in range(50):
        svc.add_category("GELIR", f"Kategori {i:02d}")
    pdf = reports_pdf.daily_kasa_pdf(tmp / "cok.pdf", svc.day_report(dt.date(2026, 9, 5)), svc.firma(), "t")
    ok(pdf.stat().st_size > 3000, "50+ kategoriyle günlük PDF")
    from openpyxl import load_workbook
    xp = excel.export_sheets(tmp / "f.xlsx", [{"name": "T", "headers": ["A"], "rows": [["=SUM(A1:A2)"]]}])
    cell = load_workbook(xp).active.cell(2, 1)
    ok(cell.data_type == "s" and cell.value == "=SUM(A1:A2)", "Excel'de '=' metin kaldı")
    db.close()


def test_analytics(tmp: Path):
    """Analiz hesapları ve Raporlar bölümü."""
    from kasatakip import reports
    from kasatakip.analytics import Analytics, shift_year
    from kasatakip.report_model import Table, flatten, format_cell
    from kasatakip.util import fmt_delta_percent, fmt_percent, today

    ok(fmt_percent(-0.05) == "-%5,0" and fmt_percent(0.125) == "%12,5" and fmt_percent(-0.0001) == "%0,0",
       "yüzde biçimi")
    ok(fmt_delta_percent(0.123) == "+%12,3" and fmt_delta_percent(-0.5) == "-%50,0", "değişim biçimi")
    ok(shift_year(dt.date(2024, 2, 29), -1) == dt.date(2023, 2, 28), "29 Şubat yıl kaydırma")

    db = Database(tmp / "an.db")
    svc = Service(db)
    an = Analytics(svc)
    # boş veritabanında hiçbir hesap hata vermemeli
    for rd in reports.REPORTS:
        if rd.key != "cari_ekstre":
            reports.build(rd.key, svc, reports.default_values(rd, svc)) if not rd.admin else None
    ok(an.insights(today(), today())[0]["tone"] == "info", "boş veritabanında yorum")

    svc.setup_first_admin("admin", "Yönetici", "1234", "Firma")
    pts = {p["ad"]: p["id"] for p in svc.payment_types()}
    cats = {(c["tur"], c["ad"]): c["id"] for c in svc.categories()}
    t = today()

    def mov(days_ago, tur, tutar, cari=None, kat=None, odeme="Nakit", saat="10:00", hedef=None):
        return svc.add_movement({"tarih": t - dt.timedelta(days=days_ago), "tur": tur, "tutar": tutar,
                                 "cari_id": cari, "kategori_id": cats.get((tur, kat)) if kat else None,
                                 "odeme_tipi_id": pts.get(odeme) if tur in ("GELIR", "GIDER", "VIRMAN") else None,
                                 "hedef_odeme_tipi_id": pts.get(hedef), "saat": saat})

    mus = svc.save_cari({"unvan": "Müşteri A", "tip": "MUSTERI"})
    ted = svc.save_cari({"unvan": "Tedarikçi B", "tip": "TEDARIKCI"})
    mov(100, "CARI_BORC", 100000, mus)
    mov(70, "GELIR", 60000, mus, "Nakit Tahsilat")
    mov(40, "CARI_BORC", 50000, mus)
    mov(10, "CARI_BORC", 30000, mus)
    mov(20, "CARI_ALACAK", 80000, ted)
    mov(5, "GIDER", 30000, ted, "Nakliye", "Havale")
    for i in range(1, 15):
        mov(i, "GELIR", 10000 * i, kat="Kargo Geliri", saat=f"{8 + i % 10:02d}:15")
        mov(i, "GIDER", 2000 * i, kat="Yakıt")
    mov(3, "VIRMAN", 15000, odeme="Nakit", hedef="Havale")

    # yaşlandırma (FIFO): 1.000 borç, 600 tahsilat -> en eski kalemden 400 açık kalır
    ag = an.cari_aging(t, "alacak")
    r = ag["satirlar"][0]
    ok(r["bakiye"] == 120000 and r["dilimler"] == [30000, 50000, 0, 40000, 0], f"alacak yaşlandırma {r['dilimler']}")
    ok(r["gecikme"] == 100 and r["son_odeme"] == (t - dt.timedelta(days=70)).isoformat(), "en eski kalem / son tahsilat")
    bo = an.cari_aging(t, "borc")
    ok(bo["toplam"] == 50000 and bo["satirlar"][0]["dilimler"][0] == 50000, "borç yaşlandırma")
    tot = svc.cari_totals()
    ok(ag["toplam"] == tot["alacaklarimiz"] and bo["toplam"] == tot["borclarimiz"], "yaşlandırma = cari toplamları")
    # geçmiş tarih itibarıyla: 50 gün önce bakiye 400; kalem o tarihte 50 günlüktü
    ok(an.cari_aging(t - dt.timedelta(days=50), "alacak")["satirlar"][0]["dilimler"] == [0, 40000, 0, 0, 0],
       "geçmiş tarih itibarıyla yaşlandırma")

    # cari mizan
    s, e = t - dt.timedelta(days=50), t
    m = {x["unvan"]: x for x in an.cari_activity(s, e)}
    ok(m["Müşteri A"]["devir"] == 40000 and m["Müşteri A"]["borc"] == 80000 and m["Müşteri A"]["bakiye"] == 120000,
       "cari mizan devir/borç/bakiye")
    ok(m["Tedarikçi B"]["bakiye"] == -50000 and m["Tedarikçi B"]["odeme"] == 30000, "tedarikçi mizan")
    cb = {x["unvan"]: x for x in an.cari_balances(t - dt.timedelta(days=50))}
    ok(cb["Müşteri A"]["bakiye"] == 40000 and "Tedarikçi B" in cb, "tarih itibarıyla cari bakiye")

    # ödeme tipleri ve bakiye seyri
    pt = {x["ad"]: x for x in an.payment_table(s, e)}
    at = svc.balances_at(e)
    ok(all(pt[a]["son"] == at[pts[a]] for a in pt), "ödeme tipi dönem sonu = gün sonu bakiyesi")
    ok(pt["Nakit"]["virman_cikis"] == 15000 and pt["Havale"]["virman_giris"] == 15000, "virman giriş/çıkış")
    bh = an.balance_history(s, e)
    ok(all(sr["values"][-1] == at[sr["id"]] for sr in bh["series"]) and bh["group"] == "day", "bakiye seyri son değer")
    ok(an.balance_history(t - dt.timedelta(days=400), t)["group"] == "week", "uzun dönemde haftalık bakiye")

    # özet, seri, kategoriler
    k = an.kpis(s, e)
    ok(k["gelir"] == sum(10000 * i for i in range(1, 15)) and k["gider"] == sum(2000 * i for i in range(1, 15)) + 30000,
       "dönem göstergeleri")
    ok(k["tahsilat_orani"] == 0 and k["cari_borc"] == 80000, "tahsilat oranı (dönemde tahsilat yok)")
    wed = t - dt.timedelta(days=(t.weekday() - 2) % 7 + 14)
    grp, ser = an.series(wed, t, "week")
    ok(ser[0]["etiket"] == wed.strftime("%d.%m") and ser[0]["baslik"].startswith(fmt_date_(wed)), "hafta etiketi dönemden")
    ok(sum(r["gelir"] for r in ser) == an.kpis(wed, t)["gelir"], "seri toplamı = özet")
    cc = an.category_compare(s, e, *an.compare_range(s, e, "prev"), "GELIR")
    ok(abs(sum(r["pay"] for r in cc) - 1) < 1e-9, "kategori payları toplamı %100")
    pv = an.category_pivot(s, e, "GIDER", "week")
    ok(sum(pv["totals"]) == k["gider"], "kategori × dönem toplamı")
    ok(len(an.hourly_stats(s, e)) >= 10 and sum(w["aktif_gun"] for w in an.weekday_stats(s, e)) == 14,
       "saat ve gün desenleri")
    mc = an.monthly_compare(t.year)
    ok(len(mc["rows"]) == 12 and all(r["d_gelir"] is None for r in mc["rows"] if r["gelecek"]), "aylık karşılaştırma")

    # kapanış ve sayım farkı
    d = t - dt.timedelta(days=14)
    rep = svc.day_report(d)
    nak = [x for x in rep["odeme"] if x["ad"] == "Nakit"][0]
    svc.close_day(d, {pts["Nakit"]: nak["gun_sonu"] - 2500})
    cd = an.count_differences(s, e)
    ok(cd["farkli_gun"] == 1 and cd["net"] == -2500 and cd["eksik"] == -2500, "sayım farkı")
    cs = an.closing_status(s, e)
    ok(cs["kapali"] == 1 and len(cs["acik"]) == 13, f"kapanış durumu {cs['kapali']} {len(cs['acik'])}")
    ok(any(i["tone"] == "warn" for i in an.insights(s, e)), "yorumlarda uyarı")

    # tüm raporlar: içerik + PDF + Excel
    for rd in reports.REPORTS:
        v = reports.default_values(rd, svc)
        if rd.key == "cari_ekstre":
            v["cari"] = mus
        rep = reports.build(rd.key, svc, v)
        ok(rep.blocks and rep.title, f"rapor içeriği: {rd.key}")
        for b in flatten(rep.blocks):
            if isinstance(b, Table):
                ok(all(len(r) == len(b.headers) for r in b.rows) and (b.total is None or len(b.total) ==
                                                                        len(b.headers)) and len(b.types) ==
                   len(b.headers), f"{rd.key}/{b.title}: sütun sayıları tutarlı")
        pdf = reports.write_pdf(rd.key, svc, v, tmp / f"{rd.key}.pdf", svc.firma(), "test")
        xl = reports.write_excel(rep, tmp / f"{rd.key}.xlsx")
        ok(pdf.stat().st_size > 2500 and xl.stat().st_size > 3000, f"rapor dosyaları: {rd.key}")
    defter = reports.build("kasa_defteri", svc, {"donem": (s, e), "odeme": pts["Nakit"], "gun_toplam": False})
    tb = [b for b in defter.blocks if isinstance(b, Table)][0]
    ok(tb.rows[-1][-1] == svc.balances_at(e)[pts["Nakit"]], "kasa defteri yürüyen bakiye = gün sonu bakiyesi")
    ek = reports.build("cari_ekstre", svc, {"cari": mus, "donem": (s, e)})
    ok([b for b in ek.blocks if isinstance(b, Table)][0].total[-1] == 120000, "rapor ekstre bakiyesi")
    raises(ServiceError, reports.build, "cari_ekstre", svc, {"cari": None, "donem": (s, e)})
    ok(format_cell(-1500, "smoney") == "-15,00" and format_cell(0.25, "dpercent") == "+%25,0", "hücre biçimleri")
    svc.logout()
    svc.login("admin", "1234")
    uid = svc.create_user("kasiyer", "Kasiyer", "1234", "kullanici")
    svc.logout()
    svc.login("kasiyer", "1234")
    ok("islem_gecmisi" not in [r.key for r in reports.available(svc)], "kullanıcı yönetim raporunu görmez")
    raises(PermissionDenied, reports.build, "islem_gecmisi", svc, {"donem": (s, e), "kullanici": None})
    ok(uid > 0, "kullanıcı")
    db.close()


def test_review2(tmp: Path):
    """İkinci bağımsız incelemede bulunan hataların regresyon testleri."""
    from kasatakip import reports, reports_pdf
    from kasatakip.analytics import Analytics
    from kasatakip.report_model import Chart, Report, Table, flatten
    from kasatakip.util import today
    from openpyxl import load_workbook

    db = Database(tmp / "r2.db")
    svc = Service(db)
    svc.setup_first_admin("admin", "Yönetici", "1234", "Firma")
    an = Analytics(svc)
    pts = {p["ad"]: p["id"] for p in svc.payment_types()}
    kat = svc.categories("GELIR")[0]["id"]
    t = today()

    def gelir(d, tutar, cari=None):
        return svc.add_movement({"tarih": d, "tur": "GELIR", "kategori_id": kat, "odeme_tipi_id": pts["Nakit"],
                                 "tutar": tutar, "cari_id": cari})

    # açılış bakiyesi fişi dönemin veresiyesi sayılmaz
    c = svc.save_cari({"unvan": "Açılışlı Müşteri", "tip": "MUSTERI"},
                      acilis={"tutar": 10_000_000, "taraf": "BORC", "tarih": t})
    gelir(t, 1_000_000, c)
    k = an.kpis(t, t)
    ok(k["cari_borc"] == 0 and k["tahsilat_orani"] is None and k["acilis_borc"] == 10_000_000,
       "açılış fişi veresiye sayılmadı")
    ok(not any("veresiye yazıldı" in i["text"] for i in an.insights(t, t)), "açılış fişi yorumda veresiye değil")
    ok(an.cari_activity(t, t)[0]["veresiye"] == 0, "mizan veresiyesi açılış hariç")

    # (önceki yıl verisi, gün kapanışından önce girilir; kapanış önceki günleri kilitler)
    y = t.year
    svc.save_cari({"unvan": "Eski Açılış", "tip": "MUSTERI"},
                  acilis={"tutar": 50_000, "taraf": "BORC", "tarih": dt.date(y - 1, 1, 1)})
    gelir(dt.date(y - 1, 6, 1), 5000)

    # kilitlenen ama kapatılmayan günler kapalı sayılmaz
    for i in range(2, 12):
        gelir(t - dt.timedelta(days=i), 1000)
    svc.close_day(t - dt.timedelta(days=2))
    cs = an.closing_status(t - dt.timedelta(days=11), t)
    ok(cs["kapali"] == 1 and cs["kilitli"] == 9 and abs(cs["oran"] - 0.1) < 1e-9, "kilitli günler ayrı sayıldı")
    txt = " ".join(i["text"] for i in an.insights(t - dt.timedelta(days=11), t))
    ok("kapatılmadan kilitlenmiş" in txt and "tüm günlerin kasası kapatılmış" not in txt, "kilitli gün uyarısı")
    rep = reports.build("kapanislar", svc, {"donem": (t - dt.timedelta(days=11), t)})
    ok(any(isinstance(b, Table) and b.title == "Kapatılmadan Kilitlenen Günler" and len(b.rows) == 9
           for b in rep.blocks), "kapanış raporunda kilitli günler")

    # önceki yıl eksikliği yalnız gelir/gider kayıtlarına göre; geriye tarihli açılış fişi yanıltmaz
    mc = an.monthly_compare(y)
    ok(mc["prev_eksik"] and mc["ilk_kayit"] <= dt.date(y - 1, 6, 1), "önceki yıl eksik tespit edildi")
    ay = reports.build("aylik", svc, {"yil": y})
    kv = [b for b in ay.blocks if b.__class__.__name__ == "KV"][0]
    ok(dict((a, v) for a, v, _ in kv.items)["Gelir Değişimi"] is None, "eksik yılda değişim verilmez")
    tb = [b for b in ay.blocks if isinstance(b, Table) and b.title == "Aylık Döküm"][0]
    ok(tb.total[-1] is None and tb.total[-2] is None, "kısmi yılda toplam satırı değişimi boş")

    # gelecek yıl: tüm değerleri boş çizgi serisi PDF'i bozmaz
    nxt = reports.build("aylik", svc, {"yil": y + 1})
    p = reports_pdf.render_report(tmp / "gelecek.pdf", nxt, svc.firma(), "t")
    ok(p.stat().st_size > 2000, "gelecek yıl aylık rapor PDF")
    r = Report("x", "Deneme", [], [Chart("Boş", "line", ["a", "b"], [("A", "#000000", [None, None])])], "x")
    ok(reports_pdf.render_report(tmp / "bos_cizgi.pdf", r, svc.firma()).stat().st_size > 1000, "boş çizgi grafiği")

    # sayfadan uzun tek hücre ve çok satırlı tablo
    long_t = Table("Uzun", ["A", "B"], ["text", "money"], [["x" * 9000, 100]] + [[f"satır {i}", i] for i in range(700)])
    p = reports_pdf.render_report(tmp / "uzun.pdf", Report("u", "Uzun", [], [long_t], "u"), svc.firma())
    ok(p.stat().st_size > 10000, "çok uzun hücre ve 700 satır PDF")

    # Excel: kullanıcı metni formül olarak yazılmaz (başlık, etiket, sütun başlığı)
    evil = svc.save_cari({"unvan": "=HYPERLINK(\"http://x\",\"Tıkla\")", "tip": "MUSTERI"})
    svc.add_payment_type("=1+1")
    for key, v in (("cari_ekstre", {"cari": evil, "donem": (t - dt.timedelta(days=30), t)}),
                   ("odeme_bakiye", {"donem": (t - dt.timedelta(days=30), t)})):
        xp = reports.write_excel(reports.build(key, svc, v), tmp / f"{key}.xlsx")
        wb = load_workbook(xp)
        formulas = [c.coordinate for ws in wb for row in ws.iter_rows() for c in row if c.data_type == "f"]
        ok(not formulas, f"Excel formül enjeksiyonu yok: {key} {formulas[:3]}")
    # boş tabloda toplam satırı yazılmaz
    rep = reports.build("kapanislar", svc, {"donem": (t + dt.timedelta(days=200), t + dt.timedelta(days=201))})
    xp = reports.write_excel(rep, tmp / "bos.xlsx")
    ws = load_workbook(xp)["Kapatılan Günler"]
    ok(not any(c.value == "TOPLAM" for row in ws.iter_rows() for c in row), "boş tabloda TOPLAM yok")

    # özet tablo başlığı iki yıla yayılınca doğru
    from kasatakip.reports import _pivot_tables
    pv = _pivot_tables(an, dt.date(2025, 11, 1), dt.date(2026, 9, 30), "GIDER", "month", "Gider")
    ok(len(pv) == 1 and pv[0].title.endswith("(2025–2026)"), f"özet tablo başlığı {pv[0].title}")
    ok(all(isinstance(b, Table) for b in flatten(pv)), "özet tablo")
    db.close()


def test_all_time(tmp: Path):
    """'Tüm Zamanlar' seçilen raporlarda başlık, devir satırı ve dosya adı 2000 – 2099 göstermemeli."""
    from kasatakip import reports
    from kasatakip.util import period_range, today
    from openpyxl import load_workbook

    db = Database(tmp / "tz.db")
    svc = Service(db)
    svc.setup_first_admin("admin", "Yönetici", "1234", "Firma")
    t = today()
    ok(svc.span(*period_range("Tüm Zamanlar")) == (t, t), "boş veritabanında tüm zamanlar = bugün")
    pts = {p["ad"]: p["id"] for p in svc.payment_types()}
    kat = svc.categories("GELIR")[0]["id"]
    d0, d1 = t - dt.timedelta(days=40), t - dt.timedelta(days=3)
    c = svc.save_cari({"unvan": "Tüm Zaman Müşterisi", "tip": "MUSTERI"})
    for d in (d0, d1):
        svc.add_movement({"tarih": d, "tur": "GELIR", "kategori_id": kat, "odeme_tipi_id": pts["Nakit"],
                          "tutar": 100_000, "cari_id": c})
    ok(svc.span(*period_range("Tüm Zamanlar")) == (d0, t), "tüm zamanlar = ilk kayıt – bugün")
    ok(svc.span(*period_range("Bu Ay")) == period_range("Bu Ay"), "elle / hazır dönemlere dokunulmaz")
    ok(svc.span(dt.date(2026, 1, 5), dt.date(2099, 12, 31)) == (dt.date(2026, 1, 5), t), "yalnız açık uç daralır")
    for rd in reports.available(svc):
        if not any(p.kind == "period" for p in rd.params):
            continue
        v = reports.default_values(rd, svc)
        for p in rd.params:
            if p.kind == "period":
                v[p.key] = period_range("Tüm Zamanlar")
            if p.kind == "cari" and v.get(p.key) is None:
                v[p.key] = c
        rep = reports.build(rd.key, svc, v)
        text = " ".join([rep.filename] + rep.lines)
        ok("2000" not in text and "2099" not in text, f"{rd.key}: tüm zamanlar başlığı {text}")
        ok(rep.lines[0].endswith("(tüm kayıtlar)"), f"{rd.key}: tüm kayıtlar notu")
    v = reports.default_values(reports.REPORT_BY_KEY["kasa_defteri"], svc)
    v["donem"] = period_range("Tüm Zamanlar")
    ws = load_workbook(reports.write_excel(reports.build("kasa_defteri", svc, v), tmp / "kd.xlsx"))["Kasa Defteri"]
    devir = next(r for r in ws.iter_rows(values_only=True) if r[2] == "Devir")
    ok(devir[0].date() == d0, f"kasa defteri devir tarihi {devir[0]}")
    db.close()


def test_version_consistency():
    """Sürüm numarası EXE bilgisi, kurulum betiği ve README'de aynı olmalı; derleme betiği ve README sabit
    sürüm numaralı kurulum dosyası adı içermemeli (yeni sürümde eskimesin)."""
    import re
    from kasatakip import __version__ as ver
    root = Path(__file__).resolve().parent.parent
    vi = (root / "version_info.txt").read_text(encoding="utf-8")
    iss = (root / "kurulum.iss").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    bat = (root / "exe_olustur.bat").read_text(encoding="utf-8", errors="replace")
    parts = ver.split(".")
    tup = "(" + ", ".join(parts + ["0"] * (4 - len(parts))) + ")"
    ok(f"filevers={tup}" in vi and f"prodvers={tup}" in vi, f"version_info.txt sayısal sürüm {tup}")
    ok(vi.count(f"'{ver}'") == 2, "version_info.txt FileVersion / ProductVersion")
    ok(f'#define AppVersion "{ver}"' in iss, "kurulum.iss AppVersion")
    ok(f"- **{ver}**" in changelog, "CHANGELOG sürüm notu")
    ok(not re.search(r"KasaTakip_Kurulum_\d", bat + readme), "betik ve README'de sabit sürümlü kurulum adı yok")
    ok("Inno Setup*" in bat, "derleme betiği Inno Setup 7 klasörünü de arıyor")


def test_kurulum_araclari():
    """Python denetimi, sanal ortam yardımcısı ve .bat dosyalarının biçimi (Windows'ta cmd için ASCII + CRLF)."""
    import importlib.util
    import subprocess
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(root / "tools" / "python_kontrol.py")], capture_output=True, text=True)
    ok(r.returncode == 0 and r.stdout.rstrip().endswith("->  uygun"), f"bu Python uygun görülmeli: {r.stdout.strip()}")
    spec = importlib.util.spec_from_file_location("kt_ortam", root / "tools" / "ortam.py")
    ortam = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ortam)
    ok(ortam.maskele("index-url='https://ali:s1fre@nexus.firma.local/simple'") ==
       "index-url='https://***@nexus.firma.local/simple'", "pip adresindeki şifre gizlenir")
    ok(ortam.maskele("https://pypi.org/simple") == "https://pypi.org/simple", "şifresiz adres değişmez")
    ok("PyInstaller" in ortam.moduller(True) and "PyInstaller" not in ortam.moduller(False), "derleme modülleri")
    bats = sorted(root.glob("*.bat")) + sorted((root / "tools").glob("*.bat"))
    ok(len(bats) >= 4, "bat dosyaları bulundu")
    for b in bats:
        raw = b.read_bytes()
        ok(raw.isascii(), f"{b.name}: yalnızca ASCII olmalı")
        ok(b"\n" not in raw.replace(b"\r\n", b""), f"{b.name}: satır sonları CRLF olmalı")
    req = (root / "requirements.txt").read_text(encoding="utf-8")
    ok(all(p in req for p in ("PySide6", "reportlab", "openpyxl")) and "<" in req, "requirements üst sınırlı")


def test_ui_static():
    """Arayüz sınıflarında Qt metotlarını gölgeleyen öznitelik olmamalı (ör. self.metric, self.font):
    Qt bu metotları çağırdığında program kilitlenir / bellek taşar."""
    import ast
    try:
        from PySide6.QtWidgets import QDialog, QFrame, QMainWindow, QWidget
    except ImportError:
        return
    names = set(dir(QWidget)) | set(dir(QFrame)) | set(dir(QDialog)) | set(dir(QMainWindow))
    root = Path(__file__).resolve().parent.parent / "kasatakip" / "ui"
    bad = []
    for p in root.rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
                    and isinstance(node.value, ast.Name) and node.value.id == "self" and node.attr in names):
                bad.append(f"{p.name}:{node.lineno} self.{node.attr}")
    ok(not bad, f"Qt metodunu gölgeleyen öznitelik: {bad}")


def test_hak_bilgisi():
    """Telif bilgisi programda, EXE bilgisinde, kurulumda ve LISANS.txt'de aynı kişiye ait olmalı."""
    from kasatakip import APP_AUTHOR, APP_COPYRIGHT, APP_WEBSITE
    root = Path(__file__).resolve().parent.parent
    vi = (root / "version_info.txt").read_text(encoding="utf-8")
    iss = (root / "kurulum.iss").read_text(encoding="utf-8")
    ok(APP_AUTHOR in APP_COPYRIGHT and APP_WEBSITE in APP_COPYRIGHT, "telif metni")
    ok(f"'CompanyName', '{APP_AUTHOR}'" in vi and f"'LegalCopyright', '{APP_COPYRIGHT}'" in vi, "EXE telif bilgisi")
    ok(f"AppPublisher={APP_AUTHOR}" in iss and APP_WEBSITE in iss, "kurulum yayıncı bilgisi")
    ok(APP_AUTHOR in (root / "LISANS.txt").read_text(encoding="utf-8"), "LISANS.txt")


def test_ag_engeli():
    """Programın ağ erişimi kapalı: bağlantı ve ad çözümleme denemeleri hata verir, kodda ağ modülü yok, kurulum
    güvenlik duvarı kuralı ekler, EXE'ye Qt ağ modülü alınmaz. Soketleri kalıcı kapattığı için en son çalışır."""
    import ast
    import socket
    from kasatakip import netguard
    root = Path(__file__).resolve().parent.parent
    netguard.install()
    netguard.install()          # tekrar çağrılabilir
    ok(netguard.active(), "ağ engeli etkin")
    raises(netguard.AgErisimiKapali, socket.create_connection, ("1.1.1.1", 53), 1)
    raises(netguard.AgErisimiKapali, socket.getaddrinfo, "tahirekinalan.com", 443)
    s = socket.socket()
    try:
        raises(netguard.AgErisimiKapali, s.connect, ("1.1.1.1", 53))
    finally:
        s.close()
    yasak = ("urllib", "http", "requests", "httpx", "ftplib", "smtplib", "xmlrpc")
    bad = []
    for p in (root / "kasatakip").rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            bad += [f"{p.name}: {n}" for n in names
                    if n.split(".")[0] in yasak or "QtNetwork" in n or "WebEngine" in n]
    ok(not bad, f"kodda ağ modülü kullanılmamalı: {bad}")
    iss = (root / "kurulum.iss").read_text(encoding="utf-8")
    ok("advfirewall firewall add rule" in iss and "dir=out action=block" in iss, "kurulum güvenlik duvarı kuralı")
    for spec in ("KasaTakip.spec", "KasaTakip_tasinabilir.spec"):
        ok('"PySide6.QtNetwork"' in (root / spec).read_text(encoding="utf-8"), f"{spec}: Qt ağ modülü dışarıda")


def fmt_date_(d):
    from kasatakip.util import fmt_date
    return fmt_date(d)


if __name__ == "__main__":
    test_util()
    # Windows'ta açık kalan bir dosya geçici klasörün silinmesini engellerse test başarısız sayılmasın
    for fn in (test_flow, test_paste, test_review_regressions, test_analytics, test_review2, test_all_time):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            fn(Path(t))
    test_version_consistency()
    test_kurulum_araclari()
    test_ui_static()
    test_hak_bilgisi()
    test_ag_engeli()
    print(f"TAMAM: {PASS} kontrol geçti")
