"""Deneme (demo) verisi: programı gerçek veriye dokunmadan denemek için.

Gerçek veritabanına asla yazılmaz; yalnızca 'KasaTakip.exe --demo' ile açılan ayrı demo klasöründe kullanılır.
Giriş: kullanıcı adı 'admin', şifre 'admin'  (kasiyer / 1234 da vardır)
"""
from __future__ import annotations

import datetime as dt
import random

from .util import today

MUSTERILER = ["Konya Tekstil San. A.Ş.", "Meram Eczanesi", "Selçuklu Oto Yedek Parça", "Karatay Gıda Ltd. Şti.",
              "Mevlana Kitabevi", "Bosna Mobilya", "Anadolu Medikal", "Sille Seramik Atölyesi"]
TEDARIKCILER = ["Opet Akaryakıt - Selçuklu", "Yılmaz Nakliyat", "Konya Ofis Kırtasiye", "Anadolu Oto Servis",
                "Mehmet Kaya (Mülk Sahibi)"]


def populate(svc, days: int = 75, seed: int = 7) -> None:
    """Örnek veri üretir. Tüm yazımlar tek bir işlemde yapılır (hızlı)."""
    with svc.db.tx():
        _populate(svc, days, seed)


def _populate(svc, days: int, seed: int) -> None:
    rnd = random.Random(seed)
    if not svc.users_exist():
        svc.setup_first_admin("admin", "Demo Yönetici", "admin", "Örnek Firma - Merkez Şubesi")
        svc.save_firma({"adi": "Örnek Firma - Merkez Şubesi", "adres": "Örnek Mah. Örnek Cad. No:1 Merkez",
                        "telefon": "0332 000 00 00", "vergi": "Selçuk V.D. 1234567890"})
        svc.set_setting("son_kullanici", "admin")
    svc.user = dict(svc.db.q1("SELECT * FROM kullanicilar WHERE rol='yonetici' ORDER BY id LIMIT 1"))
    if not svc.db.q1("SELECT 1 FROM kullanicilar WHERE kullanici_adi='kasiyer'"):
        uid = svc.create_user("kasiyer", "Ayşe Yılmaz", "1234", "kullanici")
        svc.db.x("UPDATE kullanicilar SET sifre_degistirmeli=0 WHERE id=?", (uid,))

    cats = {(c["tur"], c["ad"]): c["id"] for c in svc.categories()}
    pts = {p["ad"]: p["id"] for p in svc.payment_types()}
    svc.update_payment_type(pts["Nakit"], "Nakit", 1_500_000)
    svc.update_payment_type(pts["Havale"], "Havale", 12_000_000)

    start = today() - dt.timedelta(days=days)
    mus = [svc.save_cari({"unvan": u, "tip": "MUSTERI", "telefon": f"0332 {rnd.randint(200, 699)} "
                                                                    f"{rnd.randint(10, 99)} {rnd.randint(10, 99)}"},
                         acilis={"tutar": rnd.choice([0, 0, 450_000, 1_200_000]), "taraf": "BORC",
                                 "tarih": start - dt.timedelta(days=1)})
           for u in MUSTERILER]
    ted = [svc.save_cari({"unvan": u, "tip": "TEDARIKCI"}) for u in TEDARIKCILER]

    def add(d, tur, kat=None, odeme=None, tutar=0, cari=None, aciklama="", hedef=None, saat=None):
        svc.add_movement({"tarih": d, "tur": tur, "kategori_id": cats.get((tur, kat)) if kat else None,
                          "odeme_tipi_id": pts.get(odeme), "hedef_odeme_tipi_id": pts.get(hedef), "cari_id": cari,
                          "tutar": int(tutar), "aciklama": aciklama,
                          "saat": saat or f"{rnd.randint(9, 18):02d}:{rnd.choice([0, 10, 20, 30, 40, 50]):02d}",
                          "belge_no": ""})

    def balance(cid):
        return svc.get_cari(cid)["bakiye"]

    def supplier_invoice_then_pay(d, cid, kat, odeme, tutar, fatura, odeme_aciklama):
        svc.add_movement({"tarih": d, "tur": "CARI_ALACAK", "cari_id": cid, "tutar": tutar, "aciklama": fatura})
        add(d, "GIDER", kat, odeme, tutar, cari=cid, aciklama=odeme_aciklama)

    prev_card = 0
    season = {1: 0.9, 2: 0.92, 7: 0.95, 8: 0.95, 11: 1.12, 12: 1.22}     # kargo yoğunluğu (aylık)
    for i in range(days + 1):
        d = start + dt.timedelta(days=i)
        if d.weekday() == 6:
            continue
        f = (0.85 + 0.3 * i / max(days, 1)) * season.get(d.month, 1.0)     # yavaş büyüme + mevsimsellik
        nakit = int(rnd.randint(450_000, 950_000) * f)
        kart = int(rnd.randint(250_000, 700_000) * f)
        add(d, "GELIR", "Kargo Geliri", "Nakit", nakit, aciklama="Gişe nakit tahsilatları")
        add(d, "GELIR", "Kargo Geliri", "Kart", kart, aciklama="POS tahsilatları")
        if prev_card:
            add(d, "VIRMAN", odeme="Kart", hedef="Havale", tutar=prev_card, aciklama="POS tutarı bankaya geçti")
        prev_card = kart
        if rnd.random() < 0.45:
            svc.add_movement({"tarih": d, "tur": "CARI_BORC", "cari_id": rnd.choice(mus),
                              "tutar": rnd.randint(300_000, 1_500_000), "aciklama": "Kargo gönderim bedeli (veresiye)"})
        if rnd.random() < 0.4:
            borclular = [c for c in mus if balance(c) > 100_000]
            if borclular:
                c = rnd.choice(borclular)
                odeme, kat = rnd.choice([("Havale", "Havale/EFT Tahsilatı"), ("Havale", "Havale/EFT Tahsilatı"),
                                         ("Nakit", "Nakit Tahsilat"), ("Çek", "Çek Tahsilatı")])
                tah = min(balance(c), max(100_000, int(balance(c) * rnd.uniform(0.3, 0.8))))
                add(d, "GELIR", kat, odeme, tah, cari=c,
                    aciklama="Vadeli çek alındı" if odeme == "Çek" else "Cari tahsilat")
        if rnd.random() < 0.5:
            add(d, "GIDER", "Yakıt", rnd.choice(["Nakit", "Havale"]), rnd.randint(80_000, 250_000),
                aciklama="Dağıtım aracı yakıtı")
        if rnd.random() < 0.2:
            add(d, "GIDER", "Servis/Ulaşım", "Nakit", rnd.randint(20_000, 90_000), aciklama="Personel servisi")
        if rnd.random() < 0.1:
            supplier_invoice_then_pay(d, ted[2], "Ofis/Kırtasiye", "Nakit", rnd.randint(15_000, 60_000),
                                      "Kırtasiye faturası", "Kırtasiye ödemesi")
        if rnd.random() < 0.07:
            supplier_invoice_then_pay(d, ted[3], "Bakım/Onarım", "Havale", rnd.randint(150_000, 600_000),
                                      "Servis faturası", "Araç bakımı ödemesi")
        add(d, "GIDER", "Merkeze Gönderilen", "Havale", rnd.randint(400_000, 900_000),
            aciklama="Günlük merkez transferi")
        if d.weekday() == 4:
            fatura = rnd.randint(400_000, 900_000)
            svc.add_movement({"tarih": d, "tur": "CARI_ALACAK", "cari_id": ted[1], "tutar": fatura,
                              "aciklama": "Haftalık nakliye faturası"})
            if balance(ted[1]) < 0:
                add(d, "GIDER", "Nakliye", "Havale", min(-balance(ted[1]), rnd.randint(300_000, 800_000)),
                    cari=ted[1], aciklama="Nakliye ödemesi")
        if i % 2 == 1:
            nakit_bal = svc.balances_at(d)[pts["Nakit"]]
            if nakit_bal > 2_000_000:
                add(d, "VIRMAN", odeme="Nakit", hedef="Havale", tutar=nakit_bal - 1_500_000,
                    aciklama="Nakit bankaya yatırıldı", saat="17:30")
        if d.day == 1:
            add(d, "GIDER", "Maaş", "Havale", 8_400_000, aciklama="Personel maaşları (3 kişi)")
        if d.day == 5:
            supplier_invoice_then_pay(d, ted[4], "Kira", "Havale", 3_500_000, "Aylık kira tahakkuku", "Dükkan kirası")
        if d.day == 26:
            add(d, "GIDER", "Vergi/Resmi Ödeme", "Havale", 2_200_000, aciklama="SGK ve vergi ödemeleri")

    # 2 gün öncesine kadar kasaları kapat (son günler açık kalsın)
    pt_nakit = pts["Nakit"]
    for i in range(days + 1):
        d = start + dt.timedelta(days=i)
        if d >= today() - dt.timedelta(days=1):
            break
        rep = svc.day_report(d)
        if not rep["hareketler"]:
            continue
        nak = [r for r in rep["odeme"] if r["id"] == pt_nakit][0]
        fark = rnd.choice([0, 0, 0, 0, -500, 1000, -2000])
        svc.close_day(d, {pt_nakit: nak["gun_sonu"] + fark}, "" if not fark else "Sayım farkı kontrol edildi")
