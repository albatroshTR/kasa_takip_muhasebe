"""Personel takibi testleri.  Çalıştırma:  python -m tests.test_personel"""
from __future__ import annotations

import datetime as dt
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kasatakip import db as dbmod  # noqa: E402
from kasatakip.db import Database  # noqa: E402
from kasatakip.errors import PermissionDenied, ServiceError  # noqa: E402
from kasatakip.services import Service, tr_sira  # noqa: E402
from kasatakip.util import MAX_AMOUNT, fold  # noqa: E402
from tests.ornek_personel import ILK_PERSONEL  # noqa: E402

PASS = 0
D15, D20, D31, N01 = dt.date(2026, 10, 15), dt.date(2026, 10, 20), dt.date(2026, 10, 31), dt.date(2026, 11, 1)


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


def make(tmp: Path, name: str = "kasa.db"):
    db = Database(tmp / name)
    svc = Service(db)
    svc.setup_first_admin("admin", "Yönetici", "1234", "Test")
    pts = {p["ad"]: p["id"] for p in svc.payment_types()}
    cats = {(c["tur"], c["ad"]): c["id"] for c in svc.categories()}
    return db, svc, pts, cats


def kasa_kaydi(svc, pid, islem, tarih, tutar, odeme, **k):
    return svc.add_personel_kaydi({"personel_id": pid, "islem": islem, "tarih": tarih, "tutar": tutar,
                                   "odeme_tipi_id": odeme, **k})


def test_upgrade(tmp: Path):
    """v1 veritabanı açılışta v2'ye yükseltilir; eski veri korunur, personel kategorileri eklenir."""
    path = tmp / "v1.db"
    c = sqlite3.connect(str(path))
    for stmt in dbmod._SCHEMA_V1:
        c.execute(stmt)
    for tur, names in (("GELIR", dbmod.DEFAULT_GELIR), ("GIDER", dbmod.DEFAULT_GIDER)):
        for i, ad in enumerate(names, 1):
            c.execute("INSERT INTO kategoriler(tur, ad, sira) VALUES(?,?,?)", (tur, ad, i))
    c.execute("INSERT INTO odeme_tipleri(ad, sira) VALUES('Nakit', 1)")
    c.execute("INSERT INTO cariler(kod, unvan, olusturma) VALUES('M0001', 'Eski Cari', '2026-01-01 10:00:00')")
    c.execute("INSERT INTO hareketler(tarih, tur, kategori_id, odeme_tipi_id, cari_id, tutar, olusturma) "
              "VALUES('2026-10-01', 'GIDER', (SELECT id FROM kategoriler WHERE ad='Maaş'), 1, 1, 12345, "
              "'2026-10-01 10:00:00')")
    c.execute("PRAGMA user_version = 1")
    c.commit()
    c.close()

    db = Database(path)
    ok(db.user_version() == 2, "şema sürümü 2")
    ok(db.scalar("SELECT COUNT(*) FROM hareketler") == 1 and db.scalar("SELECT tutar FROM hareketler") == 12345,
       "eski hareket korundu")
    ok(db.scalar("SELECT unvan FROM cariler") == "Eski Cari", "eski cari korundu")
    cols = {r["name"] for r in db.q("PRAGMA table_info(hareketler)")}
    ok({"personel_id", "personel_islem"} <= cols, "hareketlere personel sütunları eklendi")
    tables = {r[0] for r in db.q("SELECT name FROM sqlite_master WHERE type='table'")}
    ok({"personeller", "personel_fisleri"} <= tables, "personel tabloları oluştu")
    gider = [r["ad"] for r in db.q("SELECT ad FROM kategoriler WHERE tur='GIDER' ORDER BY sira")]
    gelir = [r["ad"] for r in db.q("SELECT ad FROM kategoriler WHERE tur='GELIR' ORDER BY sira")]
    for ad in ("Prim/İkramiye", "Fazla Mesai", "Yol/Yemek", "Tazminat/İzin Ücreti", "Personel Avansı",
               "Personele Borç"):
        ok(ad in gider, f"kategori eklendi: {ad}")
    ok(sum(1 for a in gider if fold(a) == fold("Maaş")) == 1, "Maaş ikinci kez eklenmedi")
    ok(gider[-1] == "Personele Borç" and gider[:len(dbmod.DEFAULT_GIDER)] == dbmod.DEFAULT_GIDER,
       "yeni kategoriler sona eklendi")
    ok("Personel Geri Ödemesi" in gelir and len(gelir) == len(dbmod.DEFAULT_GELIR) + 1, "gelir kategorisi eklendi")
    db.close()
    db = Database(path)          # ikinci açılışta tekrar yükseltme yapılmaz
    ok(db.scalar("SELECT COUNT(*) FROM kategoriler") == 16 + 7, "yeniden açılışta kategori çoğalmadı")
    db.close()


def test_crud(tmp: Path):
    db, svc, pts, cats = make(tmp)
    a = svc.save_personel({"ad_soyad": "Ali Çelik", "gorev": "Şoför", "telefon": "0555", "iban": "TR00",
                           "ise_giris": "01.09.2026", "notlar": "not"})
    b = svc.save_personel({"ad_soyad": "Veli Kaya"})
    ok(svc.get_personel(a)["kod"] == "P0001" and svc.get_personel(b)["kod"] == "P0002", "personel kodları")
    ok(svc.get_personel(a)["ise_giris"] == "2026-09-01", "işe giriş tarihi")
    raises(ServiceError, svc.save_personel, {"ad_soyad": "ALİ CELİK"})
    raises(ServiceError, svc.save_personel, {"ad_soyad": "  "})
    raises(ServiceError, svc.save_personel, {"ad_soyad": "X", "ise_giris": "bozuk"})
    raises(ServiceError, svc.save_personel, {"ad_soyad": "veli kaya"}, a)
    svc.save_personel({"ad_soyad": "Ali Çelik", "gorev": "Kurye"}, a)
    p = svc.get_personel(a)
    ok(p["gorev"] == "Kurye" and p["kod"] == "P0001", "güncelleme kodu korur")
    c = svc.save_personel({"ad_soyad": "Devirli"}, devreden={"tutar": 150000, "yon": "ALACAK", "tarih": D15})
    ok(svc.get_personel(c)["bakiye"] == -150000 and svc.get_personel(c)["durum"] == "Personele borçluyuz",
       "devreden alacak")
    d = svc.save_personel({"ad_soyad": "Avanslı"}, devreden={"tutar": 20000, "yon": "BORC", "tarih": D15})
    ok(svc.get_personel(d)["bakiye"] == 20000 and svc.get_personel(d)["durum"] == "Personel bize borçlu",
       "devreden borç")
    svc.delete_personel(b)
    ok(all(r["id"] != b for r in svc.personel_listesi(include_inactive=True)), "kaydı olmayan personel silindi")
    msg = raises(ServiceError, svc.delete_personel, c)
    ok("pasif" in msg, "kaydı olan personel silinemez")
    svc.set_personel_active(c, False)
    ok(all(r["id"] != c for r in svc.personel_listesi()), "pasif personel listede yok")
    ok(any(r["id"] == c for r in svc.personel_listesi(include_inactive=True)), "pasifler gösterilince var")
    raises(ServiceError, kasa_kaydi, svc, c, "AVANS", D15, 100, pts["Nakit"])
    ok([r["ad_soyad"] for r in svc.personel_listesi(search="celik")] == ["Ali Çelik"], "Türkçe duyarsız arama")
    ok(any(a_["konu"] == "Personel" and "eklendi" in a_["detay"] for a_ in svc.audit(dt.date(2000, 1, 1),
                                                                                    dt.date(2099, 1, 1))),
       "işlem geçmişi")
    sheets = {s["name"]: s for s in svc.export_tables()}
    ok("Personel" in sheets and len(sheets["Personel"]["rows"]) == 3, "Excel'de Personel sayfası (yönetici)")
    db.close()


def test_yetki(tmp: Path):
    db, svc, pts, cats = make(tmp)
    pid = svc.save_personel({"ad_soyad": "Ali Çelik"})
    mid = kasa_kaydi(svc, pid, "ODEME", D15, 100000, pts["Nakit"], aciklama="Ekim maaşı", belge_no="D-1")["hareket_id"]
    normal = svc.add_movement({"tarih": D15, "tur": "GIDER", "kategori_id": cats[("GIDER", "Yakıt")],
                               "odeme_tipi_id": pts["Nakit"], "tutar": 5000, "aciklama": "Mazot"})
    adm = svc.get_movement(mid)
    ok(adm["cari"] == "Ali Çelik" and adm["aciklama"] == "Ekim maaşı" and adm["personel_islem"] == "ODEME",
       "yönetici personel adını görür")
    ok([m["id"] for m in svc.movements(search="celik")] == [mid], "yönetici personel adıyla arar")
    svc.create_user("kasiyer", "Kasiyer", "1234", "kullanici")
    svc.logout()
    svc.login("kasiyer", "1234")
    for fn, args in ((svc.personel_listesi, ()), (svc.get_personel, (pid,)), (svc.save_personel, ({"ad_soyad": "X"},)),
                     (svc.set_personel_active, (pid, False)), (svc.delete_personel, (pid,)),
                     (svc.personel_ekstre, (pid, D15, D31)),
                     (svc.add_personel_kaydi, ({"personel_id": pid, "islem": "HAK_EDIS", "tarih": D15,
                                                "tutar": 100},)),
                     (svc.personel_toplu_kaydet, (D15, None, pts["Nakit"], "", []))):
        raises(PermissionDenied, fn, *args)
    m = [x for x in svc.movements() if x["id"] == mid][0]
    ok(m["cari"] == "Personel ödemesi" and m["aciklama"] == "" and m["belge_no"] == "" and not m["personel"],
       "kullanıcı rolünde personel bilgisi gizli")
    g = svc.get_movement(mid)
    ok(g["cari"] == "Personel ödemesi" and g["aciklama"] == "", "get_movement da gizli")
    ok(m["tutar"] == 100000 and m["kategori"] == "Maaş", "tutar ve kategori görünür")
    ok(svc.movements(search="celik") == [] and svc.movements(search="Ekim maaşı") == []
       and svc.movements(search="D-1") == [], "kullanıcı aramasında personel bilgisi eşleşmez")
    ok([x["id"] for x in svc.movements(search="mazot")] == [normal], "normal arama çalışır")
    upd = {"tarih": D15, "tur": "GIDER", "kategori_id": cats[("GIDER", "Maaş")], "odeme_tipi_id": pts["Nakit"],
           "tutar": 1}
    raises(PermissionDenied, svc.update_movement, mid, upd)
    raises(PermissionDenied, svc.delete_movement, mid)
    raises(PermissionDenied, svc.delete_movements, [normal, mid])
    ok(len(svc.movements()) == 2, "toplu silme geri alındı")
    svc.update_movement(normal, {**upd, "kategori_id": cats[("GIDER", "Yakıt")], "tutar": 6000})
    ok(svc.get_movement(normal)["tutar"] == 6000, "kullanıcı normal kaydı değiştirebilir")
    ok("Personel" not in {s["name"] for s in svc.export_tables()}, "kullanıcının Excel'inde Personel sayfası yok")
    svc.logout()
    svc.login("admin", "1234")
    svc.update_movement(mid, {**upd, "tutar": 120000, "aciklama": "düzeltme"})
    g = svc.get_movement(mid)
    ok(g["personel_id"] == pid and g["personel_islem"] == "ODEME", "güncelleme personel bağlantısını korur")
    ok(svc.get_personel(pid)["bakiye"] == 120000, "güncellenen tutar bakiyeye yansır")
    db.close()


def test_ornek_ay(tmp: Path):
    db, svc, pts, cats = make(tmp)
    nakit = pts["Nakit"]
    svc.update_payment_type(nakit, "Nakit", 10_000_000)
    pid = svc.save_personel({"ad_soyad": "Ali Çelik"})
    kasa_kaydi(svc, pid, "AVANS", D15, 500_000, nakit)
    p = svc.get_personel(pid)
    ok(p["bakiye"] == 500_000 and p["durum"] == "Personel bize borçlu", "avans: B 5.000")
    ok(svc.balances_at(D15)[nakit] == 9_500_000, "avans kasadan düştü")
    before = svc.balances_at(D31)[nakit]
    r = svc.add_personel_kaydi({"personel_id": pid, "islem": "HAK_EDIS", "tarih": D31, "tutar": 2_500_000})
    ok(r["hareket_id"] is None and r["fis_id"], "hak ediş fiş olarak kaydedildi")
    p = svc.get_personel(pid)
    ok(p["bakiye"] == -2_000_000 and p["durum"] == "Personele borçluyuz", "hak ediş: A 20.000")
    ok(svc.balances_at(D31)[nakit] == before, "hak ediş kasa bakiyesini etkilemez")
    rep = svc.day_report(D31)
    ok(rep["gider"] == 0 and rep["hareketler"] == [], "hak ediş günlük kasada yok")
    ok(db.scalar("SELECT kategori_id FROM personel_fisleri") == cats[("GIDER", "Maaş")], "varsayılan kalem Maaş")
    kasa_kaydi(svc, pid, "ODEME", N01, 2_000_000, nakit, kategori_id=cats[("GIDER", "Maaş")])
    p = svc.get_personel(pid)
    ok(p["bakiye"] == 0 and p["durum"] == "Bakiye yok", "ödeme: 0")
    ok(svc.balances_at(N01)[nakit] == 10_000_000 - 2_500_000, "kasadan toplam 25.000 çıktı")
    rep = svc.day_report(N01)
    ok(rep["gider"] == 2_000_000 and rep["hareketler"][0]["cari"] == "Ali Çelik", "ödeme günlük kasada")
    ekim = {r["ad"]: r["toplam"] for r in svc.category_totals(dt.date(2026, 10, 1), D31, "GIDER")}
    ok(ekim == {"Personel Avansı": 500_000}, f"analiz nakit esaslı: hak ediş gider sayılmaz {ekim}")
    ok(svc.summary(dt.date(2026, 10, 1), dt.date(2026, 11, 30))["gider"] == 2_500_000, "gider iki kez sayılmaz")
    ok(svc.caris(include_inactive=True) == [], "cari hesaplar etkilenmez")

    # hesap dökümü: devir ve yürüyen bakiye
    st = svc.personel_ekstre(pid, dt.date(2026, 10, 16), dt.date(2026, 11, 30))
    ok(st["devir"] == 500_000, "döküm devri")
    ok([(r["kaynak"], r["islem"], r["bakiye"]) for r in st["satirlar"]] ==
       [("fis", "Hak Ediş", -2_000_000), ("hareket", "Ödeme", 0)], f"yürüyen bakiye {st['satirlar']}")
    ok(st["toplam_borc"] == 2_000_000 and st["toplam_alacak"] == 2_500_000 and st["bakiye"] == 0, "döküm toplamları")
    ok(st["satirlar"][0]["odeme"] == "Kasa dışı" and st["satirlar"][1]["odeme"] == "Nakit"
       and st["satirlar"][0]["kalem"] == "Maaş", "döküm sütunları")
    st = svc.personel_ekstre(pid, dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    ok(st["devir"] == 0 and [r["bakiye"] for r in st["satirlar"]] == [500_000, -2_000_000, 0], "tüm yıl dökümü")
    lst = svc.personel_listesi()[0]
    ok(lst["son_kayit"] == N01.isoformat() and lst["bakiye"] == 0, "listede son kayıt")

    # geri ödeme ve borç verme
    kasa_kaydi(svc, pid, "BORC", N01, 300_000, nakit)
    kasa_kaydi(svc, pid, "GERI_ODEME", N01, 100_000, nakit)
    ok(svc.get_personel(pid)["bakiye"] == 200_000, "borç verme ve geri ödeme")
    kats = {m["personel_islem"]: m["kategori"] for m in svc.movements(personel_id=pid)}
    ok(kats["BORC"] == "Personele Borç" and kats["GERI_ODEME"] == "Personel Geri Ödemesi"
       and kats["AVANS"] == "Personel Avansı", "sabit kategoriler")
    raises(ServiceError, kasa_kaydi, svc, pid, "ODEME", N01, 100, None)          # ödeme tipi zorunlu

    # rapor
    from kasatakip import reports
    from kasatakip.report_model import Table
    rep = reports.build("personel_ekstre", svc, {"personel": pid, "donem": (dt.date(2026, 10, 1), D20)})
    tb = [b for b in rep.blocks if isinstance(b, Table)][0]
    ok(tb.rows[0][1] == "Devir" and tb.rows[1][1] == "Avans" and tb.total[-1] == 500_000
       and rep.subdir == "Yönetim Raporları", "rapor dökümü")
    pdf = reports.write_pdf("personel_ekstre", svc, {"personel": pid, "donem": (D15, N01)}, tmp / "p.pdf",
                            svc.firma(), "test")
    ok(pdf.stat().st_size > 2000, "rapor PDF")
    rep = reports.build("personel_ekstre", svc, {"personel": None, "donem": (D15, N01)})
    ok(rep.blocks[0].rows[0][1] == "Ali Çelik", "personel seçilmezse tüm personel özeti")
    # ileri tarihli fiş: 'Tüm Zamanlar' dökümü ve rapor kart bakiyesiyle aynı
    from kasatakip.util import ALL_TIME_END, ALL_TIME_START
    ileri = dt.date(2027, 3, 31)
    svc.add_personel_kaydi({"personel_id": pid, "islem": "HAK_EDIS", "tarih": ileri, "tutar": 50_000})
    s_, e_ = svc.span(ALL_TIME_START, ALL_TIME_END)
    ok(e_ == ileri, "tüm zamanlar aralığı ileri tarihli fişi kapsar")
    kart = svc.get_personel(pid)["bakiye"]
    ok(svc.personel_ekstre(pid, s_, e_)["bakiye"] == kart, "döküm bakiyesi kart bakiyesiyle aynı")
    rep = reports.build("personel_ekstre", svc, {"personel": pid, "donem": (ALL_TIME_START, ALL_TIME_END)})
    tb = [b for b in rep.blocks if isinstance(b, Table)][0]
    ok(tb.total[-1] == kart and tb.rows[-1][0] == ileri.isoformat(), "rapor ileri tarihli fişi içerir")
    db.close()


def test_dogrudan_ve_fis(tmp: Path):
    db, svc, pts, cats = make(tmp)
    pid = svc.save_personel({"ad_soyad": "Ali Çelik"})
    prim = cats[("GIDER", "Prim/İkramiye")]
    r = kasa_kaydi(svc, pid, "ODEME", D20, 750_000, pts["Havale"], kategori_id=prim, hak_edis_de=True)
    ok(svc.get_personel(pid)["bakiye"] == 0, "doğrudan ödeme hesap bakiyesini değiştirmez")
    f = svc.get_personel_fisi(r["fis_id"])
    ok(f["bagli_hareket_id"] == r["hareket_id"] and f["kategori_id"] == prim and f["tutar"] == 750_000
       and f["tarih"] == D20.isoformat() and f["islem"] == "HAK_EDIS", "bağlı hak ediş fişi")
    # ödeme düzenlenince bağlı hak ediş de güncellenir; bağlı fiş tek başına değiştirilemez
    upd = {"tarih": D31, "tur": "GIDER", "kategori_id": cats[("GIDER", "Maaş")], "odeme_tipi_id": pts["Havale"],
           "tutar": 600_000}
    svc.update_movement(r["hareket_id"], upd)
    f = svc.get_personel_fisi(r["fis_id"])
    ok(f["tutar"] == 600_000 and f["tarih"] == D31.isoformat() and f["kategori"] == "Maaş"
       and svc.get_personel(pid)["bakiye"] == 0, "ödeme düzenlenince bağlı hak ediş güncellendi")
    ok("ödemeyi" in raises(ServiceError, svc.update_personel_fisi, r["fis_id"],
                           {"islem": "HAK_EDIS", "tarih": D31, "tutar": 1}), "bağlı fiş ayrıca düzenlenmez")
    raises(ServiceError, svc.delete_personel_fisi, r["fis_id"])
    for degisik in ({**upd, "tur": "GELIR", "kategori_id": cats[("GELIR", "Diğer")]},
                    {"tarih": D31, "tur": "VIRMAN", "odeme_tipi_id": pts["Havale"], "hedef_odeme_tipi_id": pts["Nakit"],
                     "tutar": 600_000}):
        ok("türü değiştirilemez" in raises(ServiceError, svc.update_movement, r["hareket_id"], degisik),
           "personel kaydının türü değişmez")
    ok(svc.get_movement(r["hareket_id"])["tur"] == "GIDER", "tür korunmuş")
    svc.delete_movement(r["hareket_id"])
    ok(db.scalar("SELECT COUNT(*) FROM personel_fisleri") == 0, "ödeme silinince bağlı hak ediş silindi")
    ok(svc.get_personel(pid)["bakiye"] == 0, "silme sonrası bakiye")
    ok(any("Fiş silindi" in a["detay"] for a in svc.audit(dt.date(2000, 1, 1), dt.date(2099, 1, 1))),
       "bağlı fiş silme işlem geçmişinde")

    # fiş düzenleme / silme
    fid = svc.add_personel_kaydi({"personel_id": pid, "islem": "HAK_EDIS", "tarih": D20, "tutar": 1_000_000,
                                  "aciklama": "Ekim"})["fis_id"]
    svc.update_personel_fisi(fid, {"islem": "HAK_EDIS", "tarih": D31, "tutar": 1_200_000, "kategori_id": prim,
                                   "aciklama": "Ekim prim"})
    f = svc.get_personel_fisi(fid)
    ok(f["tutar"] == 1_200_000 and f["tarih"] == D31.isoformat() and f["kategori"] == "Prim/İkramiye",
       "fiş güncellendi")
    ok(svc.get_personel(pid)["bakiye"] == -1_200_000, "güncel fiş bakiyesi")
    ok("kayıtta kullanılıyor" in raises(ServiceError, svc.delete_category, prim), "fişte kullanılan kalem silinmez")
    svc.update_personel_fisi(fid, {"islem": "DEVREDEN_BORC", "tarih": D31, "tutar": 1_200_000})
    ok(svc.get_personel_fisi(fid)["kategori_id"] is None and svc.get_personel(pid)["bakiye"] == 1_200_000,
       "fiş türü değişti, kalem temizlendi")
    raises(ServiceError, svc.update_personel_fisi, fid, {"islem": "ODEME", "tarih": D31, "tutar": 1})
    svc.delete_personel_fisi(fid)
    raises(ServiceError, svc.get_personel_fisi, fid)
    ok(svc.get_personel(pid)["bakiye"] == 0, "fiş silindi")
    raises(ServiceError, svc.add_personel_kaydi, {"personel_id": pid, "islem": "HAK_EDIS", "tarih": D20, "tutar": 0})
    raises(ServiceError, svc.add_personel_kaydi, {"personel_id": pid, "islem": "HAK_EDIS", "tarih": D20, "tutar": 5,
                                                  "kategori_id": cats[("GELIR", "Diğer")]})
    raises(ServiceError, svc.add_personel_kaydi, {"personel_id": None, "islem": "HAK_EDIS", "tarih": D20, "tutar": 5})
    raises(ServiceError, svc.add_personel_kaydi, {"personel_id": pid, "islem": "YOK", "tarih": D20, "tutar": 5})
    cnt = db.scalar("SELECT COUNT(*) FROM hareketler")
    raises(ServiceError, kasa_kaydi, svc, pid, "ODEME", D20, 0, pts["Nakit"], hak_edis_de=True)
    ok(db.scalar("SELECT COUNT(*) FROM hareketler") == cnt, "hatalı kayıt geri alındı")
    db.close()


def test_gun_kilidi(tmp: Path):
    db, svc, pts, cats = make(tmp)
    pid = svc.save_personel({"ad_soyad": "Ali Çelik"})
    avans = kasa_kaydi(svc, pid, "AVANS", D15, 100_000, pts["Nakit"])["hareket_id"]
    svc.close_day(D20)
    msg = raises(ServiceError, kasa_kaydi, svc, pid, "AVANS", D20, 50_000, pts["Nakit"])
    ok("kapatılmış" in msg, "kapalı güne personel kasa kaydı girilemez")
    raises(ServiceError, kasa_kaydi, svc, pid, "ODEME", D15, 50_000, pts["Nakit"], hak_edis_de=True)
    ok(db.scalar("SELECT COUNT(*) FROM personel_fisleri") == 0, "kilitte bağlı hak ediş de oluşmadı")
    raises(ServiceError, svc.delete_movement, avans)
    fid = svc.add_personel_kaydi({"personel_id": pid, "islem": "HAK_EDIS", "tarih": D15, "tutar": 300_000})["fis_id"]
    ok(fid and svc.get_personel(pid)["bakiye"] == -200_000, "kapalı güne hak ediş fişi girilir")
    svc.update_personel_fisi(fid, {"islem": "HAK_EDIS", "tarih": D20, "tutar": 250_000})
    ok(svc.get_personel(pid)["bakiye"] == -150_000, "kapalı günde fiş düzenlenir")
    svc.delete_personel_fisi(fid)
    ok(svc.get_personel(pid)["bakiye"] == 100_000, "kapalı günde fiş silinir")
    db.close()


def test_toplu(tmp: Path):
    db, svc, pts, cats = make(tmp)
    a = svc.save_personel({"ad_soyad": "Ali Çelik"})
    b = svc.save_personel({"ad_soyad": "Veli Kaya"})
    c = svc.save_personel({"ad_soyad": "Pasif Kişi"})
    svc.set_personel_active(c, False)
    kasa_kaydi(svc, a, "AVANS", D15, 500_000, pts["Nakit"])
    maas = cats[("GIDER", "Maaş")]

    def counts():
        return db.scalar("SELECT COUNT(*) FROM hareketler"), db.scalar("SELECT COUNT(*) FROM personel_fisleri")

    n0 = counts()
    raises(ServiceError, svc.personel_toplu_kaydet, D31, maas, pts["Nakit"], "Ekim 2026 maaşı",
           [{"personel_id": a, "hak_edis": 2_500_000, "odenen": 2_000_000},
            {"personel_id": c, "hak_edis": 1_000_000, "odenen": 0}])
    ok(counts() == n0, "toplu kayıt: hata olunca hiçbiri kaydedilmez (pasif personel)")
    msg = raises(ServiceError, svc.personel_toplu_kaydet, D31, maas, pts["Nakit"], "",
                 [{"personel_id": a, "hak_edis": 2_500_000, "odenen": 2_000_000},
                  {"personel_id": b, "hak_edis": 1_000_000, "odenen": MAX_AMOUNT + 1}])
    ok(counts() == n0 and "Veli Kaya" in msg, "toplu kayıt: hatalı satır adıyla bildirilir, hepsi geri alınır")
    svc.close_day(D20)
    raises(ServiceError, svc.personel_toplu_kaydet, D20, maas, pts["Nakit"], "",
           [{"personel_id": b, "hak_edis": 1_000_000, "odenen": 1_000_000}])
    ok(counts() == n0, "toplu kayıt: kapalı günde ödeme olunca hak ediş de geri alındı")
    r = svc.personel_toplu_kaydet(D31, maas, pts["Nakit"], "Ekim 2026 maaşı",
                                  [{"personel_id": a, "hak_edis": 2_500_000, "odenen": 2_000_000},
                                   {"personel_id": b, "hak_edis": 1_000_000, "odenen": 0},
                                   {"personel_id": c, "hak_edis": 0, "odenen": 0}])
    ok(r == {"kisi": 2, "hak_edis": 2, "odeme": 1}, f"toplu kayıt sayıları {r}")
    ok(svc.get_personel(a)["bakiye"] == 0 and svc.get_personel(b)["bakiye"] == -1_000_000, "toplu kayıt bakiyeleri")
    ok(db.scalar("SELECT COUNT(*) FROM personel_fisleri WHERE bagli_hareket_id IS NOT NULL") == 0,
       "toplu ödemeler hak edişe bağlanmaz")
    m = svc.movements(start=D31, end=D31)
    ok(len(m) == 1 and m[0]["aciklama"] == "Ekim 2026 maaşı" and m[0]["kategori"] == "Maaş", "toplu ödeme defterde")
    db.close()


def test_ilk_yukleme(tmp: Path):
    ok(len(ILK_PERSONEL) == 41 and len({fold(x) for x in ILK_PERSONEL}) == 41, "ilk liste 41 farklı isim")
    db = Database(tmp / "ilk.db")
    svc = Service(db)
    ok(svc.user is None, "oturum açılmadan")
    ok(svc.ilk_personel_yukle(ILK_PERSONEL) == 41, "ilk yüklemede 41 personel")
    ok(svc.ilk_personel_yukle(ILK_PERSONEL) == 0, "ikinci yüklemede 0")
    rows = db.q("SELECT kod, ad_soyad FROM personeller ORDER BY kod")
    ok(rows[0]["kod"] == "P0001" and rows[0]["ad_soyad"] == "ADA TEST" and rows[-1]["kod"] == "P0041"
       and rows[-1]["ad_soyad"] == "ZEYNEP TEST", "kodlar alfabetik sırayla")
    adlar = [r["ad_soyad"] for r in rows]
    ok(adlar == sorted(ILK_PERSONEL, key=tr_sira), "kodlar Türk alfabesi sırasıyla")
    ok(adlar.index("SELMA TEST") < adlar.index("ŞEVVAL TEST") < adlar.index("TUNA TEST"), "Ş, S'den sonra gelir")
    ok(any(a["konu"] == "Personel" and "41" in a["detay"] for a in svc.audit(dt.date(2000, 1, 1),
                                                                          dt.date(2099, 1, 1))),
       "ilk yükleme işlem geçmişinde")
    db.close()
    # önceden kayıtlı isim tekrar eklenmez
    db, svc, pts, cats = make(tmp, "ilk2.db")
    svc.save_personel({"ad_soyad": "Ada Test"})
    ok(svc.ilk_personel_yukle(ILK_PERSONEL) == 40, "kayıtlı isim atlandı")
    ok(db.scalar("SELECT COUNT(*) FROM personeller") == 41, "toplam 41 personel")
    db.close()


if __name__ == "__main__":
    for fn in (test_upgrade, test_crud, test_yetki, test_ornek_ay, test_dogrudan_ve_fis, test_gun_kilidi, test_toplu,
               test_ilk_yukleme):
        # Windows'ta açık kalan bir dosya geçici klasörün silinmesini engellerse test başarısız sayılmasın
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            fn(Path(t))
    print(f"TAMAM: {PASS} kontrol geçti")
