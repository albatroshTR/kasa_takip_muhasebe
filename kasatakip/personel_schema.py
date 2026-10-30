"""Şema sürüm 2: personel takibi.

    personeller        personel kartları (kod P0001…, ad soyad, iletişim, görev)
    hareketler         personel_id + personel_islem (ODEME | AVANS | BORC | GERI_ODEME): kasayı etkileyen kayıtlar
    personel_fisleri   kasa dışı kayıtlar (HAK_EDIS | DEVREDEN_BORC | DEVREDEN_ALACAK); kasa bakiyelerini,
                       analizi ve cari raporları etkilemez
"""
from __future__ import annotations

from .util import fold

PERSONEL_GIDER = ["Maaş", "Prim/İkramiye", "Fazla Mesai", "Yol/Yemek", "Tazminat/İzin Ücreti", "Personel Avansı",
                  "Personele Borç"]
PERSONEL_GELIR = ["Personel Geri Ödemesi"]

_SCHEMA_V2 = [
    """CREATE TABLE personeller(
        id        INTEGER PRIMARY KEY,
        kod       TEXT UNIQUE,
        ad_soyad  TEXT NOT NULL,
        telefon   TEXT NOT NULL DEFAULT '',
        iban      TEXT NOT NULL DEFAULT '',
        gorev     TEXT NOT NULL DEFAULT '',
        ise_giris TEXT NOT NULL DEFAULT '',
        notlar    TEXT NOT NULL DEFAULT '',
        aktif     INTEGER NOT NULL DEFAULT 1,
        olusturma TEXT NOT NULL)""",
    "ALTER TABLE hareketler ADD COLUMN personel_id INTEGER REFERENCES personeller(id)",
    "ALTER TABLE hareketler ADD COLUMN personel_islem TEXT",
    "CREATE INDEX ix_hareket_personel ON hareketler(personel_id)",
    """CREATE TABLE personel_fisleri(
        id               INTEGER PRIMARY KEY,
        personel_id      INTEGER NOT NULL REFERENCES personeller(id),
        tarih            TEXT NOT NULL,
        islem            TEXT NOT NULL CHECK(islem IN ('HAK_EDIS','DEVREDEN_BORC','DEVREDEN_ALACAK')),
        kategori_id      INTEGER REFERENCES kategoriler(id),
        tutar            INTEGER NOT NULL CHECK(tutar > 0),
        aciklama         TEXT NOT NULL DEFAULT '',
        bagli_hareket_id INTEGER,
        olusturan_id     INTEGER,
        olusturma        TEXT NOT NULL,
        guncelleyen_id   INTEGER,
        guncelleme       TEXT)""",
    "CREATE INDEX ix_personel_fis ON personel_fisleri(personel_id)",
]


def upgrade(db) -> None:
    """Sürüm 1 -> 2. Çağıranın işlemi (db.tx) içinde çalışır; hata olursa yükseltmenin tamamı geri alınır."""
    for stmt in _SCHEMA_V2:
        db.x(stmt)
    # personel kalemleri ve sabit personel kategorileri; aynı adlı kategori varsa (ör. 'Maaş') yenisi açılmaz
    for tur, names in (("GIDER", PERSONEL_GIDER), ("GELIR", PERSONEL_GELIR)):
        mevcut = {fold(r["ad"]) for r in db.q("SELECT ad FROM kategoriler WHERE tur=?", (tur,))}
        sira = db.scalar("SELECT COALESCE(MAX(sira), 0) FROM kategoriler WHERE tur=?", (tur,), 0)
        for ad in names:
            if fold(ad) not in mevcut:
                sira += 1
                db.x("INSERT INTO kategoriler(tur, ad, sira) VALUES(?,?,?)", (tur, ad, sira))
                mevcut.add(fold(ad))
