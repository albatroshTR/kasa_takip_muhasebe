"""İş kuralları: kullanıcılar, tanımlar, cariler, hareketler, günlük kasa ve analiz.

Hareket türleri
    GELIR        Kasaya giriş (kategori + ödeme tipi). Cari seçilirse carinin ALACAĞINA işlenir (tahsilat).
    GIDER        Kasadan çıkış (kategori + ödeme tipi). Cari seçilirse carinin BORCUNA işlenir (ödeme).
    VIRMAN       Bir ödeme tipinden diğerine aktarım (ör. Nakit -> Havale/banka). Gelir/gider değildir.
    CARI_BORC    Kasayı etkilemeyen borç fişi (ör. müşteriye veresiye satış).
    CARI_ALACAK  Kasayı etkilemeyen alacak fişi (ör. tedarikçiden veresiye alış).

Cari bakiye = Σ borç − Σ alacak;  pozitif -> cari bize borçlu (B), negatif -> biz cariye borçluyuz (A).
Her ödeme tipinin kendi bakiyesi vardır ve günden güne devreder.

Personel (yalnız yönetici) cari gibi çalışır: ödeme, avans, borç verme (GIDER) personeli borçlandırır; geri ödeme
(GELIR) ve kasa dışı hak ediş fişi alacaklandırır. Kasa kayıtları hareketler'de personel_id ile, kasa dışı fişler
personel_fisleri tablosunda tutulur. Personel bakiyesi: pozitif -> personel bize borçlu (B), negatif -> personele
borçluyuz (A).
"""
from __future__ import annotations

import datetime as dt
import time

from . import auth, sifreleme
from .db import Database
from .errors import AuthError, PermissionDenied, ServiceError
from .util import (ALL_TIME_END, ALL_TIME_START, MAX_AMOUNT, auto_group, balance_word, clean_text, date_in_range,
                   fmt_date, fmt_money, fold, group_key, group_label, group_title, iso, iter_group_keys, month_end,
                   month_start, now_iso, now_time, parse_amount, parse_time, to_date, today, tr_lower)

__all__ = ["Service", "ServiceError", "PermissionDenied", "AuthError"]

GELIR, GIDER, VIRMAN, CARI_BORC, CARI_ALACAK = "GELIR", "GIDER", "VIRMAN", "CARI_BORC", "CARI_ALACAK"
KASA_TURLERI = (GELIR, GIDER, VIRMAN)
TUM_TURLER = (GELIR, GIDER, VIRMAN, CARI_BORC, CARI_ALACAK)
TUR_ADI = {GELIR: "Gelir", GIDER: "Gider", VIRMAN: "Virman", CARI_BORC: "Cari Borç", CARI_ALACAK: "Cari Alacak"}
CARI_TIP_ADI = {"MUSTERI": "Müşteri", "TEDARIKCI": "Tedarikçi", "HER_IKISI": "Müşteri + Tedarikçi"}
ROL_ADI = {"yonetici": "Yönetici", "kullanici": "Kullanıcı"}
SAHIP_AYARI = "sahip_kullanici_id"       # program sahibinin kullanıcı id'si (ayarlar; geri yüklemede yedekten gelir)
ISLEM_ADI = {
    "EKLEME": "Ekleme", "GUNCELLEME": "Güncelleme", "SILME": "Silme", "GIRIS": "Giriş",
    "GIRIS_HATASI": "Hatalı giriş", "CIKIS": "Çıkış", "KAPANIS": "Gün kapanışı", "ACMA": "Gün açma",
    "YEDEK": "Yedek", "GERI_YUKLEME": "Geri yükleme", "KURULUM": "Kurulum", "SIFRE": "Şifre",
    "AYAR": "Ayar", "AKTARIM": "Toplu aktarım",
}
_ZERO = {"gelir": 0, "gider": 0, "virman_cikis": 0, "virman_giris": 0}
ACILIS_ACIKLAMA = "Açılış bakiyesi"      # cari açılış fişlerinin açıklaması (analizde veresiye sayılmaz)
KILIT_YOK_METNI = ("Bu veri dosyası henüz bir kullanıcı şifresine bağlı değil; giriş ekranındaki \"Şifremi unuttum\" "
                   "ile kurtarma anahtarını kullanın.")
KULLANICI_KILIDI_YOK = ("'{ad}' kullanıcısının şifreli verileri açan kilidi yok (ör. 1.1.x'ten gelen, pasif yapılmış "
                        "ya da yeniden aktif yapılan kullanıcı). Önce bir yöneticinin giriş yapıp Kullanıcılar ekranından "
                        "bu kullanıcının şifresini sıfırlaması gerekir. Kullanıcı adını doğru yazdığınızdan emin olun; "
                        "yöneticiyseniz giriş ekranındaki \"Şifremi unuttum\" ile kurtarma anahtarını kullanın.")
BENZER_AD_METNI = ("'{ad}' kullanıcısının adı '{diger}' kullanıcısından yalnızca Türkçe harflerle ayrılıyor; şifreli "
                   "dosyada ikisi aynı kilidi kullanır ve bu şifre değişikliği diğerinin kilidini bozardı. Önce yönetici, "
                   "Kullanıcılar ekranından birini pasif yapmalı: kilit kalan kullanıcının olur, sonra onun şifresi "
                   "değiştirilebilir.")
BENZER_PASIF_METNI = ("Şu kullanıcıların adları sizinkinden yalnızca Türkçe harflerle ayrıldığı ve şifreli dosyada aynı "
                      "kilidi kullandığı için kurtarma anahtarıyla şifre sıfırlanırken pasif yapıldı: {adlar}. "
                      "Gerekirse bunlardan birine farklı bir adla yeni kullanıcı açın.")

# personel işlemleri: kasa kayıtları (hareketler.personel_islem) ve kasa dışı fişler (personel_fisleri.islem)
PERSONEL_ISLEM_ADI = {
    "HAK_EDIS": "Hak Ediş", "ODEME": "Ödeme", "AVANS": "Avans", "BORC": "Borç Verme", "GERI_ODEME": "Geri Ödeme",
    "DEVREDEN_BORC": "Devreden Borç", "DEVREDEN_ALACAK": "Devreden Alacak",
}
PERSONEL_KASA = {"ODEME": (GIDER, None), "AVANS": (GIDER, "Personel Avansı"), "BORC": (GIDER, "Personele Borç"),
                 "GERI_ODEME": (GELIR, "Personel Geri Ödemesi")}      # işlem -> (hareket türü, sabit kategori)
PERSONEL_FISLERI = ("HAK_EDIS", "DEVREDEN_BORC", "DEVREDEN_ALACAK")
MAAS = "Maaş"                             # varsayılan personel kalemi (gider kategorisi)
MASKE = "Personel ödemesi"                # kullanıcı rolünde personel adının yerine
_TR_ALFABE = " abcçdefgğhıijklmnoöprsştuüvyz"


def tr_sira(s) -> list[int]:
    """Türk alfabesine göre sıralama anahtarı (fold Ş'yi S yapar; sıralamada kullanılamaz)."""
    return [_TR_ALFABE.find(ch) if ch in _TR_ALFABE else 100 + ord(ch) for ch in tr_lower(s)]


def personel_durum(bakiye) -> str:
    b = int(bakiye or 0)
    return "Personel bize borçlu" if b > 0 else ("Personele borçluyuz" if b < 0 else "Bakiye yok")


def _d(row):
    return dict(row) if row is not None else None


def cari_islem_adi(m: dict) -> str:
    if m["tur"] == GELIR:
        return f"Tahsilat ({m.get('odeme') or ''})"
    if m["tur"] == GIDER:
        return f"Ödeme ({m.get('odeme') or ''})"
    if m["tur"] == CARI_BORC:
        return "Borç Fişi"
    if m["tur"] == CARI_ALACAK:
        return "Alacak Fişi"
    return TUR_ADI.get(m["tur"], m["tur"])


class Service:
    MAX_FAILS = 5
    LOCK_SECONDS = 30

    def __init__(self, db: Database):
        self.db = db
        self.user: dict | None = None
        self._fails = 0
        self._locked_until = 0.0

    # ================================================================ oturum
    def users_exist(self) -> bool:
        if self.db.kilitli:
            # kilitli dosya kurulmuştur: kullanıcı kilitlerinden (önceki deneme biçiminde yalnız kurtarma kilidi)
            return bool(self.db.kilit_adlari()) or self.db.has_recovery()
        return self.db.scalar("SELECT COUNT(*) FROM kullanicilar", default=0) > 0

    @property
    def is_admin(self) -> bool:
        return bool(self.user) and self.user.get("rol") == "yonetici"

    def require_admin(self) -> None:
        if not self.is_admin:
            raise PermissionDenied("Bu işlem için yönetici yetkisi gerekir.")

    def require_login(self) -> None:
        if not self.user:
            raise AuthError("Oturum açık değil.")

    def _uid(self):
        return self.user["id"] if self.user else None

    def _refresh_user(self) -> None:
        if self.user:
            self.user = _d(self.db.q1("SELECT * FROM kullanicilar WHERE id=?", (self.user["id"],)))

    def setup_first_admin(self, kullanici_adi: str, ad_soyad: str, sifre: str, firma_adi: str = "") -> str:
        """İlk kurulum: yönetici hesabını açar, kurtarma anahtarını döndürür."""
        if self.users_exist():
            raise ServiceError("İlk kurulum daha önce yapılmış.")
        name = self._check_username(kullanici_adi)
        auth.check_new_password(sifre)
        self.db.duz_yukselt()           # kullanıcısı olmayan 1.1.x düz dosyası: program onunla çalışacak
        key = auth.new_recovery_key()
        with self.db.tx():
            cur = self.db.x(
                "INSERT INTO kullanicilar(kullanici_adi, ad_soyad, sifre_hash, rol, olusturma) VALUES(?,?,?,?,?)",
                (name, clean_text(ad_soyad, 80), auth.hash_password(sifre), "yonetici", now_iso()))
            uid = cur.lastrowid
            self.db.set_setting("firma_adi", clean_text(firma_adi, 120))
            self.db.set_setting("son_kullanici", name)
            self.db.set_setting(SAHIP_AYARI, uid)           # ilk yönetici programın sahibidir
            self.db.set_setting("kurtarma_hash", auth.hash_password(auth.normalize_key(key)))
            self.db.set_recovery_key(key)
            self.db.kullanici_kilidi(name, sifre)       # geçici kilit bu yazmada kalkar (db.py)
            self.user = _d(self.db.q1("SELECT * FROM kullanicilar WHERE id=?", (uid,)))
            self.log("KURULUM", "Sistem", uid, "İlk kurulum yapıldı; yönetici hesabı açıldı")
        return key

    def login(self, kullanici_adi: str, sifre: str) -> dict:
        """Kilitli veritabanı önce yazılan kullanıcı adı ve şifreyle açılır (açılamazsa hatalı giriş sayılır).
        1.1.x düz veritabanı yöneticinin ilk başarılı girişinde, yazdığı şifreyle şifreli dosyaya çevrilir."""
        now_m = time.monotonic()
        if now_m < self._locked_until:
            kalan = int(self._locked_until - now_m) + 1
            raise AuthError(f"Çok fazla hatalı deneme yapıldı. {kalan} saniye sonra tekrar deneyin.")
        name = (kullanici_adi or "").strip()
        if self.db.kilitli:
            try:
                self.db.ac(name, sifre or "")
            except sifreleme.AnahtarHatali:
                # kilitliyken işlem geçmişine yazılamaz. Hiç kullanıcı kilidi olmayan dosya (ör. önceki deneme
                # sürümünün dosyası) yalnızca kurtarma anahtarıyla açılır: kullanıcı oraya yönlendirilir
                adlar = self.db.kilit_adlari()
                if name and adlar and fold(name) not in adlar:
                    # bu adın kilidi yok: şifre denenmedi, hatalı deneme sayılmaz (kilit adları başlıkta zaten açık)
                    raise AuthError(KULLANICI_KILIDI_YOK.format(ad=name)) from None
                self._giris_hatasi(name, now_m, kayit=False, mesaj="" if adlar else KILIT_YOK_METNI)
        row = self.db.q1("SELECT * FROM kullanicilar WHERE kullanici_adi=?", (name,))
        if row is None or not auth.verify_password(sifre or "", row["sifre_hash"]):
            # geçiş beklerken düz kasa.db'ye yazılmaz: yönetici geçişine kadar 1.1.x ile yeniden açılabilir kalır
            self._giris_hatasi(name, now_m, kayit=not self.db.gecis_bekliyor)
        if not row["aktif"]:
            raise AuthError("Bu kullanıcı pasif durumda. Lütfen yöneticiye başvurun.")
        self._fails = 0
        if self.db.gecis_bekliyor:
            if row["rol"] == "yonetici":
                self.db.sifreye_gec(row["kullanici_adi"], sifre)    # başarısızsa düz dosyayla devam (db.uyari)
            # yönetici olmayan kullanıcı girdi ya da geçiş başarısız: program düz dosyayla çalışacağı için sürüm
            # dosyanın kendisinde yükseltilir (geçiş başarılıysa yükseltme bellekteki kopyada yapılmıştır)
            self.db.duz_yukselt()
        with self.db.tx():
            ad = row["kullanici_adi"]
            if self.db.sifreli and not self.db.kullanici_kilidi_var(ad) and not self._benzer_adlar(ad, row["id"]):
                # kilidi olmayan kullanıcı (ör. 1.1.x'ten gelen, geri yüklenen yedekte şifresi farklı): yazılan şifre.
                # Adı başka bir aktif kullanıcıyla aynı kilide düşüyorsa yazılmaz (yönetici birini pasif yapmalı)
                self.db.kullanici_kilidi(ad, sifre)
            self.db.x("UPDATE kullanicilar SET son_giris=? WHERE id=?", (now_iso(), row["id"]))
            self.user = _d(self.db.q1("SELECT * FROM kullanicilar WHERE id=?", (row["id"],)))
            self.db.set_setting("son_kullanici", self.user["kullanici_adi"])
            self.log("GIRIS", "Oturum", self.user["id"], "Oturum açıldı")
        return self.user

    def _giris_hatasi(self, name: str, now_m: float, kayit: bool = True, mesaj: str = "") -> None:
        self._fails += 1
        if kayit:
            self._log_raw("GIRIS_HATASI", "Oturum", None, name, f"Hatalı giriş denemesi ({name or 'boş'})")
        if self._fails >= self.MAX_FAILS:
            self._fails = 0
            self._locked_until = now_m + self.LOCK_SECONDS
        raise AuthError(mesaj or sifreleme.GIRIS_HATASI)

    def _benzer_adlar(self, ad: str, uid) -> list[str]:
        """Adı yalnızca Türkçe harflerle ayrılan (katlanmış adı aynı) diğer aktif kullanıcılar: şifreli dosyada bu
        kullanıcıyla aynı kilidi paylaşırlar."""
        rows = self.db.q("SELECT kullanici_adi FROM kullanicilar WHERE aktif=1 AND id<>?", (uid,))
        return [r["kullanici_adi"] for r in rows if fold(r["kullanici_adi"]) == fold(ad)]

    def _benzer_ad_engeli(self, row) -> None:
        """Kilidi başka bir aktif kullanıcıyla paylaşılan kullanıcının kilidi yazılmaz (diğerinin girişini bozardı)."""
        benzer = self._benzer_adlar(row["kullanici_adi"], row["id"]) if self.db.sifreli else []
        if benzer:
            raise ServiceError(BENZER_AD_METNI.format(ad=row["kullanici_adi"], diger=", ".join(benzer)))

    def _aktif_adlar(self) -> list[str]:
        return [r["kullanici_adi"] for r in self.db.q("SELECT kullanici_adi FROM kullanicilar WHERE aktif=1 "
                                                       "ORDER BY kullanici_adi COLLATE NOCASE")]

    def kilit_paylasanlar(self) -> list[str]:
        """Adları yalnızca Türkçe harflerle ayrılan aktif kullanıcılar (ör. 'Şule' ve 'sule'; 1.1.x'te açılabiliyordu).
        Dosya kilitleri katlanmış adla tutulduğu için aynı kilidi paylaşırlar: yalnızca biri veriyi açabilir."""
        adlar = self._aktif_adlar()
        sayi: dict = {}
        for ad in adlar:
            sayi[fold(ad)] = sayi.get(fold(ad), 0) + 1
        return [ad for ad in adlar if sayi[fold(ad)] > 1]

    def kilitsiz_kullanicilar(self) -> list[str]:
        """Şifreli dosyada kendi kilidi olmayan aktif kullanıcılar (ör. 1.1.x'ten gelen, kilidi başka bir kullanıcıyla
        paylaşan): program yeniden açıldığında kendi şifreleriyle veriyi açamazlar; yönetici şifrelerini sıfırlamalı
        (kilit paylaşanlarda birini pasif yapmalı)."""
        if not self.db.sifreli or self.db.kilitli:
            return []
        adlar, paylasan = self.db.kilit_adlari(), set(self.kilit_paylasanlar())
        return [ad for ad in self._aktif_adlar() if fold(ad) not in adlar or ad in paylasan]

    def logout(self) -> None:
        if self.user:
            self.log("CIKIS", "Oturum", self.user["id"], "Oturum kapatıldı")
        self.user = None

    # ================================================================ işlem geçmişi
    def _log_raw(self, islem, konu, kayit_id, kullanici_adi, detay) -> None:
        self.db.x("INSERT INTO islem_gecmisi(zaman, kullanici_id, kullanici_adi, islem, konu, kayit_id, detay) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (now_iso(), self._uid(), kullanici_adi or "", islem, konu, kayit_id, detay or ""))

    def log(self, islem: str, konu: str, kayit_id=None, detay: str = "") -> None:
        self._log_raw(islem, konu, kayit_id, self.user["kullanici_adi"] if self.user else "", detay)

    def audit(self, start, end, kullanici: str | None = None, konu: str | None = None, search: str = "",
              limit: int = 5000) -> list[dict]:
        sql = "SELECT * FROM islem_gecmisi WHERE zaman >= ? AND zaman <= ?"
        p: list = [iso(start) + " 00:00:00", iso(end) + " 23:59:59"]
        if kullanici:
            sql += " AND kullanici_adi = ?"
            p.append(kullanici)
        if konu:
            sql += " AND konu = ?"
            p.append(konu)
        if search:
            sql += " AND tr_fold(detay) LIKE ?"
            p.append(f"%{fold(search)}%")
        sql += " ORDER BY id DESC LIMIT ?"
        p.append(limit)
        rows = []
        for r in self.db.q(sql, p):
            d = dict(r)
            d["islem_adi"] = ISLEM_ADI.get(d["islem"], d["islem"])
            rows.append(d)
        return rows

    def audit_filters(self) -> tuple[list[str], list[str]]:
        users = [r[0] for r in self.db.q("SELECT DISTINCT kullanici_adi FROM islem_gecmisi WHERE kullanici_adi<>'' "
                                         "ORDER BY kullanici_adi")]
        topics = [r[0] for r in self.db.q("SELECT DISTINCT konu FROM islem_gecmisi ORDER BY konu")]
        return users, topics

    # ================================================================ ayarlar
    def setting(self, key: str, default: str = "") -> str:
        return self.db.get_setting(key, default)

    def set_setting(self, key: str, value) -> None:
        self.db.set_setting(key, value)

    FIRMA_ALANLARI = ("adi", "adres", "telefon", "vergi")

    def firma(self) -> dict:
        return {k: self.setting(f"firma_{k}") for k in self.FIRMA_ALANLARI}

    def save_firma(self, data: dict) -> None:
        self.require_admin()
        with self.db.tx():
            for k in self.FIRMA_ALANLARI:
                self.set_setting(f"firma_{k}", clean_text(data.get(k), 200))
            self.log("AYAR", "Ayarlar", None, "Firma bilgileri güncellendi")

    # ================================================================ kullanıcılar
    @staticmethod
    def _check_username(name: str) -> str:
        name = (name or "").strip()
        if not name:
            raise ServiceError("Kullanıcı adı boş olamaz.")
        if any(ch.isspace() for ch in name):
            raise ServiceError("Kullanıcı adında boşluk olamaz.")
        if len(name) > 30:
            raise ServiceError("Kullanıcı adı en fazla 30 karakter olabilir.")
        return name

    def list_users(self) -> list[dict]:
        rows, sahip = [], self.sahip_id()
        for r in self.db.q("SELECT id, kullanici_adi, ad_soyad, rol, aktif, son_giris, olusturma, sifre_degistirmeli "
                           "FROM kullanicilar ORDER BY kullanici_adi COLLATE NOCASE"):
            d = dict(r)
            d["rol_adi"] = ROL_ADI.get(d["rol"], d["rol"])
            d["durum"] = "Aktif" if d["aktif"] else "Pasif"
            d["sahip"] = d["id"] == sahip
            rows.append(d)
        return rows

    def sahip_id(self) -> int | None:
        """Program sahibinin kullanıcı id'si. Ayar boşsa (eski veritabanı) ya da kayıtlı kullanıcı artık aktif bir
        yönetici değilse sahip, id'si en küçük aktif yöneticidir; oturum açmış bir yönetici varken ayara yazılır."""
        sahip = self.setting(SAHIP_AYARI)
        if sahip.isdigit() and self.db.scalar(
                "SELECT COUNT(*) FROM kullanicilar WHERE id=? AND rol='yonetici' AND aktif=1", (int(sahip),), 0):
            return int(sahip)
        uid = self.db.scalar("SELECT MIN(id) FROM kullanicilar WHERE rol='yonetici' AND aktif=1")
        if uid is not None and self.is_admin:
            self.set_setting(SAHIP_AYARI, uid)
        return uid

    @property
    def is_sahip(self) -> bool:
        return self.is_admin and self.sahip_id() == self._uid()

    def sahip_kilitsiz(self) -> bool:
        """Şifreli dosyada program sahibinin kilidi yok mu (sahip kendi şifresiyle veriyi açamaz)?"""
        sahip = self.sahip_id()
        if sahip is None or not self.db.sifreli or self.db.kilitli:
            return False
        ad = self.db.scalar("SELECT kullanici_adi FROM kullanicilar WHERE id=?", (sahip,))
        return fold(ad) not in self.db.kilit_adlari()

    def require_sahip(self, mesaj: str = "Bu işlemi yalnızca program sahibi yapabilir.") -> None:
        self.require_admin()
        if not self.is_sahip:
            raise PermissionDenied(mesaj)

    def sahipligi_devret(self, uid: int) -> None:
        """Program sahipliğini başka bir aktif yöneticiye devreder (yalnız sahip)."""
        self.require_sahip("Sahipliği yalnızca program sahibi devredebilir.")
        row = self.db.q1("SELECT * FROM kullanicilar WHERE id=?", (uid,))
        if not row:
            raise ServiceError("Kullanıcı bulunamadı.")
        if uid == self._uid():
            raise ServiceError("Program sahibi zaten sizsiniz.")
        if row["rol"] != "yonetici" or not row["aktif"]:
            raise ServiceError("Sahiplik yalnızca aktif bir yöneticiye devredilebilir.")
        with self.db.tx():
            self.set_setting(SAHIP_AYARI, uid)
            self.log("GUNCELLEME", "Kullanıcı", uid,
                     f"Program sahipliği {self.user['kullanici_adi']} kullanıcısından {row['kullanici_adi']} "
                     "kullanıcısına devredildi")

    def create_user(self, kullanici_adi: str, ad_soyad: str, sifre: str, rol: str) -> int:
        self.require_admin()
        name = self._check_username(kullanici_adi)
        if rol not in ROL_ADI:
            raise ServiceError("Geçersiz rol.")
        auth.check_new_password(sifre)
        # dosya kilitleri katlanmış adla tutulur: 'Şule' ile 'sule' aynı kilidi paylaşamaz
        if any(fold(r["kullanici_adi"]) == fold(name) for r in self.db.q("SELECT kullanici_adi FROM kullanicilar")):
            raise ServiceError("Bu kullanıcı adı zaten kullanılıyor.")
        with self.db.tx():
            cur = self.db.x(
                "INSERT INTO kullanicilar(kullanici_adi, ad_soyad, sifre_hash, rol, sifre_degistirmeli, olusturma) "
                "VALUES(?,?,?,?,1,?)",
                (name, clean_text(ad_soyad, 80), auth.hash_password(sifre), rol, now_iso()))
            self.db.kullanici_kilidi(name, sifre)
            self.log("EKLEME", "Kullanıcı", cur.lastrowid, f"{name} ({ROL_ADI[rol]}) eklendi")
        return cur.lastrowid

    def _active_admins_except(self, uid) -> int:
        return self.db.scalar("SELECT COUNT(*) FROM kullanicilar WHERE rol='yonetici' AND aktif=1 AND id<>?",
                              (uid,), 0)

    def update_user(self, uid: int, ad_soyad: str, rol: str, aktif: bool) -> None:
        self.require_admin()
        row = self.db.q1("SELECT * FROM kullanicilar WHERE id=?", (uid,))
        if not row:
            raise ServiceError("Kullanıcı bulunamadı.")
        if rol not in ROL_ADI:
            raise ServiceError("Geçersiz rol.")
        sahip = self.sahip_id()
        if uid == sahip and uid != self._uid():
            raise PermissionDenied("Program sahibinin hesabını yalnızca sahibin kendisi değiştirebilir.")
        if uid == sahip and (not aktif or rol != "yonetici"):
            raise ServiceError("Program sahibinin hesabı pasif yapılamaz, yönetici yetkisi kaldırılamaz. Gerekirse "
                               "önce sahipliği başka bir yöneticiye devredin.")
        if uid == self._uid() and (not aktif or rol != "yonetici"):
            raise ServiceError("Kendi yönetici yetkinizi kaldıramaz, kendinizi pasif yapamazsınız.")
        if row["rol"] == "yonetici" and row["aktif"] and (rol != "yonetici" or not aktif) \
                and self._active_admins_except(uid) == 0:
            raise ServiceError("En az bir aktif yönetici kalmalıdır.")
        paylasan = row["kullanici_adi"] in self.kilit_paylasanlar()
        with self.db.tx():
            self.db.x("UPDATE kullanicilar SET ad_soyad=?, rol=?, aktif=? WHERE id=?",
                      (clean_text(ad_soyad, 80), rol, 1 if aktif else 0, uid))
            if row["aktif"] and not aktif and not paylasan:
                # pasif kullanıcının şifresi bundan sonra yazılan dosyaları açmaz (yeniden aktif yapılırsa yönetici
                # şifresini sıfırlar). Kilidi başka bir aktif kullanıcıyla paylaşılıyorsa (aynı katlanmış ad) o
                # kullanıcının olabilir; silinmez
                self.db.kullanici_kilidi_sil(row["kullanici_adi"])
            self.log("GUNCELLEME", "Kullanıcı", uid,
                     f"{row['kullanici_adi']}: rol {ROL_ADI[rol]}, {'aktif' if aktif else 'pasif'}")
        self._refresh_user()

    def reset_password(self, uid: int, yeni: str) -> None:
        self.require_admin()
        row = self.db.q1("SELECT * FROM kullanicilar WHERE id=?", (uid,))
        if not row:
            raise ServiceError("Kullanıcı bulunamadı.")
        sahip_kilitsiz = False
        if uid == self.sahip_id() and uid != self._uid():
            # istisna: şifreli dosyada sahibin kilidi yoksa (ör. 1.1.x'ten geçişte önce başka yönetici girdi) sahip
            # kendi şifresiyle giremez; o durumda başka bir yönetici sahibin şifresini sıfırlayabilir
            if not self.sahip_kilitsiz():
                raise PermissionDenied("Program sahibinin şifresini yalnızca sahibin kendisi değiştirebilir.")
            sahip_kilitsiz = True
        auth.check_new_password(yeni)
        if row["aktif"]:
            self._benzer_ad_engeli(row)
        must = 0 if uid == self._uid() else 1
        with self.db.tx():
            self.db.x("UPDATE kullanicilar SET sifre_hash=?, sifre_degistirmeli=? WHERE id=?",
                      (auth.hash_password(yeni), must, uid))
            if row["aktif"]:        # eski şifrenin kilidi sonraki dosyalarda yok; pasif kullanıcıya kilit eklenmez
                self.db.kullanici_kilidi(row["kullanici_adi"], yeni)
            self.log("SIFRE", "Kullanıcı", uid, f"{row['kullanici_adi']} kullanıcısının şifresi sıfırlandı"
                     + (" (program sahibinin şifreli dosyada kilidi yoktu)" if sahip_kilitsiz else ""))

    def change_own_password(self, eski: str, yeni: str, check_old: bool = True) -> None:
        self.require_login()
        row = self.db.q1("SELECT * FROM kullanicilar WHERE id=?", (self._uid(),))
        if check_old and not auth.verify_password(eski or "", row["sifre_hash"]):
            raise ServiceError("Mevcut şifre hatalı.")
        auth.check_new_password(yeni)
        if auth.verify_password(yeni, row["sifre_hash"]):
            raise ServiceError("Yeni şifre eskisiyle aynı olamaz.")
        self._benzer_ad_engeli(row)
        with self.db.tx():
            self.db.x("UPDATE kullanicilar SET sifre_hash=?, sifre_degistirmeli=0 WHERE id=?",
                      (auth.hash_password(yeni), self._uid()))
            self.db.kullanici_kilidi(row["kullanici_adi"], yeni)
            self.log("SIFRE", "Kullanıcı", self._uid(), "Kullanıcı kendi şifresini değiştirdi")
        self._refresh_user()

    def reset_with_recovery_key(self, key: str, kullanici_adi: str, yeni: str) -> str | None:
        """Yönetici şifresini kurtarma anahtarıyla sıfırlar; yeni bir kurtarma anahtarı döndürür. Kilitli
        veritabanı önce kurtarma anahtarıyla açılır; yöneticinin dosya kilidi yeni şifreyle değişir.
        Geçiş beklerken (1.1.x düz dosya) yeni anahtar oluşturulmaz, None döner: geçişten sonraki ilk yönetici
        girişinde tek geçerli yeni anahtar oluşturulup gösterilir."""
        acildi = False
        if self.db.kilitli:
            try:
                self.db.ac_kurtarma(key)
            except sifreleme.AnahtarHatali as e:
                raise AuthError(str(e)) from None
            acildi = True          # anahtar kriptografiyle doğrulandı; saklı özet uyuşmasa da (ör. eski sürümün
            # dönüştürdüğü yedek) reddedilmez, özet aşağıda yeni anahtarınkiyle değişir
        gecis = self.db.gecis_bekliyor
        stored = self.setting("kurtarma_hash")
        if not acildi and (not stored or not auth.verify_password(auth.normalize_key(key), stored)):
            if not gecis:          # geçiş beklerken düz kasa.db'ye yazılmaz (girişteki gibi)
                self._log_raw("GIRIS_HATASI", "Oturum", None, kullanici_adi, "Hatalı kurtarma anahtarı denemesi")
            raise AuthError("Kurtarma anahtarı hatalı.")
        row = self.db.q1("SELECT * FROM kullanicilar WHERE kullanici_adi=?", ((kullanici_adi or "").strip(),))
        if not row or row["rol"] != "yonetici":
            raise AuthError("Kurtarma anahtarı yalnızca bir yönetici hesabının şifresini sıfırlar. "
                            "Yönetici kullanıcı adını yazın.")
        auth.check_new_password(yeni)
        # yöneticinin kilidi başka bir aktif kullanıcıyla paylaşılıyorsa (adları yalnızca Türkçe harflerle ayrılıyor)
        # o kullanıcı pasif yapılır: kilit yöneticinin olur. Reddedilseydi şifresini unutan yönetici hiç giremezdi
        benzer = self._benzer_adlar(row["kullanici_adi"], row["id"]) if self.db.sifreli else []
        new_key = None if gecis else auth.new_recovery_key()
        with self.db.tx():
            self.db.x("UPDATE kullanicilar SET sifre_hash=?, aktif=1, sifre_degistirmeli=0 WHERE id=?",
                      (auth.hash_password(yeni), row["id"]))
            for ad in benzer:
                self.db.x("UPDATE kullanicilar SET aktif=0 WHERE kullanici_adi=?", (ad,))
                self._log_raw("GUNCELLEME", "Kullanıcı", None, row["kullanici_adi"],
                              f"{ad}: pasif (adı yöneticinin adıyla aynı kilidi kullanıyor)")
            if new_key:
                self.set_setting("kurtarma_hash", auth.hash_password(auth.normalize_key(new_key)))
                self.db.set_recovery_key(new_key)
            self.db.kullanici_kilidi(row["kullanici_adi"], yeni)
            self._log_raw("SIFRE", "Kullanıcı", row["id"], row["kullanici_adi"],
                          "Kurtarma anahtarıyla yönetici şifresi sıfırlandı")
        if benzer:
            self.db.uyari = BENZER_PASIF_METNI.format(adlar=", ".join(benzer))     # girişten sonra gösterilir
        return new_key

    def regenerate_recovery_key(self) -> str:
        self.require_admin()
        if self.db.has_recovery():
            # kurtarma kilidi henüz yoksa (şifreye geçişten sonraki bir kerelik adım, app.py) her yönetici yapabilir
            self.require_sahip("Yeni kurtarma anahtarını yalnızca program sahibi oluşturabilir.")
        key = auth.new_recovery_key()
        with self.db.tx():
            self.set_setting("kurtarma_hash", auth.hash_password(auth.normalize_key(key)))
            self.db.set_recovery_key(key)
            self.log("AYAR", "Ayarlar", None, "Yeni kurtarma anahtarı oluşturuldu")
        return key

    # ================================================================ tanımlar
    @staticmethod
    def _clean_def_name(ad: str, existing: list[str], label: str) -> str:
        ad = clean_text(ad)
        if not ad:
            raise ServiceError(f"{label} adı boş olamaz.")
        if len(ad) > 60:
            raise ServiceError(f"{label} adı en fazla 60 karakter olabilir.")
        if fold(ad) in {fold(x) for x in existing}:
            raise ServiceError(f"'{ad}' adında bir {tr_lower(label)} zaten var.")
        return ad

    def _move(self, table: str, rid: int, delta: int, where: str = "", params=()) -> None:
        rows = self.db.q(f"SELECT id FROM {table} WHERE 1=1 {where} ORDER BY sira, id", params)
        ids = [r["id"] for r in rows]
        if rid not in ids:
            return
        i = ids.index(rid)
        j = i + delta
        if j < 0 or j >= len(ids):
            return
        ids[i], ids[j] = ids[j], ids[i]
        with self.db.tx():
            for n, x in enumerate(ids, 1):
                self.db.x(f"UPDATE {table} SET sira=? WHERE id=?", (n, x))

    # --- kategoriler
    def categories(self, tur: str | None = None, include_inactive: bool = True) -> list[dict]:
        sql = ("SELECT k.*, (SELECT COUNT(*) FROM hareketler h WHERE h.kategori_id=k.id) AS kullanim "
               "FROM kategoriler k WHERE 1=1")
        p: list = []
        if tur:
            sql += " AND k.tur=?"
            p.append(tur)
        if not include_inactive:
            sql += " AND k.aktif=1"
        sql += " ORDER BY k.tur, k.sira, k.id"
        rows = []
        for r in self.db.q(sql, p):
            d = dict(r)
            d["durum"] = "Aktif" if d["aktif"] else "Pasif"
            rows.append(d)
        return rows

    def _category(self, cid) -> dict:
        r = self.db.q1("SELECT * FROM kategoriler WHERE id=?", (cid,))
        if not r:
            raise ServiceError("Kategori bulunamadı.")
        return dict(r)

    def add_category(self, tur: str, ad: str) -> int:
        self.require_admin()
        if tur not in (GELIR, GIDER):
            raise ServiceError("Geçersiz kategori türü.")
        names = [r["ad"] for r in self.db.q("SELECT ad FROM kategoriler WHERE tur=?", (tur,))]
        ad = self._clean_def_name(ad, names, "Kategori")
        sira = self.db.scalar("SELECT COALESCE(MAX(sira), 0) + 1 FROM kategoriler WHERE tur=?", (tur,), 1)
        with self.db.tx():
            cur = self.db.x("INSERT INTO kategoriler(tur, ad, sira) VALUES(?,?,?)", (tur, ad, sira))
            self.log("EKLEME", "Kategori", cur.lastrowid, f"{TUR_ADI[tur]} kategorisi eklendi: {ad}")
        return cur.lastrowid

    def rename_category(self, cid: int, ad: str) -> None:
        self.require_admin()
        k = self._category(cid)
        names = [r["ad"] for r in self.db.q("SELECT ad FROM kategoriler WHERE tur=? AND id<>?", (k["tur"], cid))]
        ad = self._clean_def_name(ad, names, "Kategori")
        with self.db.tx():
            self.db.x("UPDATE kategoriler SET ad=? WHERE id=?", (ad, cid))
            self.log("GUNCELLEME", "Kategori", cid, f"'{k['ad']}' -> '{ad}'")

    def set_category_active(self, cid: int, aktif: bool) -> None:
        self.require_admin()
        k = self._category(cid)
        with self.db.tx():
            self.db.x("UPDATE kategoriler SET aktif=? WHERE id=?", (1 if aktif else 0, cid))
            self.log("GUNCELLEME", "Kategori", cid, f"{k['ad']} {'aktif' if aktif else 'pasif'} yapıldı")

    def move_category(self, cid: int, delta: int) -> None:
        self.require_admin()
        k = self._category(cid)
        self._move("kategoriler", cid, delta, "AND tur=?", (k["tur"],))

    def delete_category(self, cid: int) -> None:
        self.require_admin()
        k = self._category(cid)
        n = self.db.scalar("SELECT (SELECT COUNT(*) FROM hareketler WHERE kategori_id=?) + "
                           "(SELECT COUNT(*) FROM personel_fisleri WHERE kategori_id=?)", (cid, cid), 0)
        if n:
            raise ServiceError(f"'{k['ad']}' kategorisi {n} kayıtta kullanılıyor. Silmek yerine pasif yapabilirsiniz.")
        with self.db.tx():
            self.db.x("DELETE FROM kategoriler WHERE id=?", (cid,))
            self.log("SILME", "Kategori", cid, f"{TUR_ADI[k['tur']]} kategorisi silindi: {k['ad']}")

    # --- ödeme tipleri
    def payment_types(self, include_inactive: bool = True) -> list[dict]:
        sql = ("SELECT o.*, (SELECT COUNT(*) FROM hareketler h WHERE h.odeme_tipi_id=o.id "
               "OR h.hedef_odeme_tipi_id=o.id) AS kullanim FROM odeme_tipleri o")
        if not include_inactive:
            sql += " WHERE o.aktif=1"
        sql += " ORDER BY o.sira, o.id"
        rows = []
        for r in self.db.q(sql):
            d = dict(r)
            d["durum"] = "Aktif" if d["aktif"] else "Pasif"
            rows.append(d)
        return rows

    def _payment_type(self, pid) -> dict:
        r = self.db.q1("SELECT * FROM odeme_tipleri WHERE id=?", (pid,))
        if not r:
            raise ServiceError("Ödeme tipi bulunamadı.")
        return dict(r)

    def add_payment_type(self, ad: str, acilis: int = 0) -> int:
        self.require_admin()
        names = [r["ad"] for r in self.db.q("SELECT ad FROM odeme_tipleri")]
        ad = self._clean_def_name(ad, names, "Ödeme tipi")
        sira = self.db.scalar("SELECT COALESCE(MAX(sira), 0) + 1 FROM odeme_tipleri", (), 1)
        with self.db.tx():
            cur = self.db.x("INSERT INTO odeme_tipleri(ad, acilis_bakiye, sira) VALUES(?,?,?)",
                            (ad, int(acilis or 0), sira))
            self.log("EKLEME", "Ödeme Tipi", cur.lastrowid,
                     f"Ödeme tipi eklendi: {ad} (açılış {fmt_money(acilis or 0)})")
        return cur.lastrowid

    def update_payment_type(self, pid: int, ad: str, acilis: int) -> None:
        self.require_admin()
        o = self._payment_type(pid)
        names = [r["ad"] for r in self.db.q("SELECT ad FROM odeme_tipleri WHERE id<>?", (pid,))]
        ad = self._clean_def_name(ad, names, "Ödeme tipi")
        with self.db.tx():
            self.db.x("UPDATE odeme_tipleri SET ad=?, acilis_bakiye=? WHERE id=?", (ad, int(acilis or 0), pid))
            self.log("GUNCELLEME", "Ödeme Tipi", pid,
                     f"{o['ad']} -> {ad}; açılış bakiyesi {fmt_money(o['acilis_bakiye'])} -> {fmt_money(acilis or 0)}")

    def set_payment_type_active(self, pid: int, aktif: bool) -> None:
        self.require_admin()
        o = self._payment_type(pid)
        with self.db.tx():
            self.db.x("UPDATE odeme_tipleri SET aktif=? WHERE id=?", (1 if aktif else 0, pid))
            self.log("GUNCELLEME", "Ödeme Tipi", pid, f"{o['ad']} {'aktif' if aktif else 'pasif'} yapıldı")

    def move_payment_type(self, pid: int, delta: int) -> None:
        self.require_admin()
        self._payment_type(pid)
        self._move("odeme_tipleri", pid, delta)

    def delete_payment_type(self, pid: int) -> None:
        self.require_admin()
        o = self._payment_type(pid)
        n = self.db.scalar("SELECT COUNT(*) FROM hareketler WHERE odeme_tipi_id=? OR hedef_odeme_tipi_id=?",
                           (pid, pid), 0)
        if n:
            raise ServiceError(f"'{o['ad']}' {n} kayıtta kullanılıyor. Silmek yerine pasif yapabilirsiniz.")
        if o["acilis_bakiye"]:
            raise ServiceError("Açılış bakiyesi olan ödeme tipi silinemez. Önce açılış bakiyesini sıfırlayın.")
        with self.db.tx():
            self.db.x("DELETE FROM odeme_tipleri WHERE id=?", (pid,))
            self.log("SILME", "Ödeme Tipi", pid, f"Ödeme tipi silindi: {o['ad']}")

    # ================================================================ cariler
    CARI_ALANLARI = ("kod", "unvan", "tip", "yetkili", "telefon", "eposta", "vergi_dairesi", "vergi_no",
                     "adres", "notlar")

    _CARI_SQL = """
        SELECT c.*,
               COALESCE(SUM(CASE WHEN h.tur IN ('CARI_BORC','GIDER') THEN h.tutar END), 0)   AS borc,
               COALESCE(SUM(CASE WHEN h.tur IN ('CARI_ALACAK','GELIR') THEN h.tutar END), 0) AS alacak,
               MAX(h.tarih) AS son_hareket,
               COUNT(h.id)  AS hareket_sayisi
        FROM cariler c LEFT JOIN hareketler h ON h.cari_id = c.id
    """

    @staticmethod
    def _cari_decorate(r) -> dict:
        d = dict(r)
        d["bakiye"] = d["borc"] - d["alacak"]
        d["tip_adi"] = CARI_TIP_ADI.get(d["tip"], d["tip"])
        d["durum"] = balance_word(d["bakiye"])
        d["aktif_durum"] = "Aktif" if d["aktif"] else "Pasif"
        return d

    def caris(self, search: str = "", tip: str | None = None, include_inactive: bool = False,
              only_balance: bool = False) -> list[dict]:
        sql = self._CARI_SQL + " WHERE 1=1"
        p: list = []
        if not include_inactive:
            sql += " AND c.aktif=1"
        if tip == "MUSTERI":
            sql += " AND c.tip IN ('MUSTERI','HER_IKISI')"
        elif tip == "TEDARIKCI":
            sql += " AND c.tip IN ('TEDARIKCI','HER_IKISI')"
        if search:
            f = f"%{fold(search)}%"
            sql += (" AND (tr_fold(c.unvan) LIKE ? OR tr_fold(c.kod) LIKE ? OR tr_fold(c.telefon) LIKE ?"
                    " OR tr_fold(c.yetkili) LIKE ? OR tr_fold(c.vergi_no) LIKE ?)")
            p += [f] * 5
        sql += " GROUP BY c.id"
        rows = [self._cari_decorate(r) for r in self.db.q(sql, p)]
        if only_balance:
            rows = [r for r in rows if r["bakiye"] != 0]
        rows.sort(key=lambda r: fold(r["unvan"]))
        return rows

    def get_cari(self, cid: int) -> dict:
        r = self.db.q1(self._CARI_SQL + " WHERE c.id=? GROUP BY c.id", (cid,))
        if not r:
            raise ServiceError("Cari bulunamadı.")
        return self._cari_decorate(r)

    def next_cari_code(self, tip: str = "MUSTERI") -> str:
        prefix = {"MUSTERI": "M", "TEDARIKCI": "T", "HER_IKISI": "C"}.get(tip, "M")
        mx = 0
        for r in self.db.q("SELECT kod FROM cariler WHERE kod LIKE ?", (prefix + "%",)):
            k = r["kod"] or ""
            if k[1:].isdigit():
                mx = max(mx, int(k[1:]))
        return f"{prefix}{mx + 1:04d}"

    def save_cari(self, data: dict, cid: int | None = None, acilis: dict | None = None) -> int:
        """Yeni cari ekler (cid=None) veya mevcut cariyi günceller.
        acilis: {'tutar': kuruş, 'taraf': 'BORC'|'ALACAK', 'tarih': date} -> açılış bakiyesi fişi."""
        self.require_login()
        d = {}
        for k in self.CARI_ALANLARI:
            v = data.get(k)
            d[k] = str(v or "").strip()[:500] if k in ("adres", "notlar") else clean_text(v, 150)
        if not d["unvan"]:
            raise ServiceError("Cari ünvanı boş olamaz.")
        if d["tip"] not in CARI_TIP_ADI:
            d["tip"] = "MUSTERI"
        old = self.get_cari(cid) if cid else None
        for r in self.db.q("SELECT id, unvan FROM cariler WHERE id<>?", (cid or -1,)):
            if fold(r["unvan"]) == fold(d["unvan"]):
                raise ServiceError(f"'{d['unvan']}' ünvanlı bir cari zaten kayıtlı.")
        if not d["kod"]:
            d["kod"] = (old["kod"] if old and old["kod"] else self.next_cari_code(d["tip"]))
        if self.db.q1("SELECT 1 FROM cariler WHERE kod=? AND id<>?", (d["kod"], cid or -1)):
            raise ServiceError(f"'{d['kod']}' cari kodu başka bir caride kullanılıyor.")
        with self.db.tx():
            if old is None:
                cur = self.db.x(
                    "INSERT INTO cariler(kod, unvan, tip, yetkili, telefon, eposta, vergi_dairesi, vergi_no, "
                    "adres, notlar, olusturma) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(d[k] for k in self.CARI_ALANLARI) + (now_iso(),))
                cid = cur.lastrowid
                self.log("EKLEME", "Cari", cid, f"{d['unvan']} ({CARI_TIP_ADI[d['tip']]}) eklendi")
                if acilis and acilis.get("tutar"):
                    self.add_movement({
                        "tarih": acilis.get("tarih") or today(),
                        "tur": CARI_BORC if acilis.get("taraf") == "BORC" else CARI_ALACAK,
                        "cari_id": cid, "tutar": abs(int(acilis["tutar"])), "aciklama": ACILIS_ACIKLAMA,
                    })
            else:
                sets = ", ".join(f"{k}=?" for k in self.CARI_ALANLARI)
                self.db.x(f"UPDATE cariler SET {sets} WHERE id=?", tuple(d[k] for k in self.CARI_ALANLARI) + (cid,))
                self.log("GUNCELLEME", "Cari", cid, f"{d['unvan']} bilgileri güncellendi")
        return cid

    def set_cari_active(self, cid: int, aktif: bool) -> None:
        self.require_login()
        c = self.get_cari(cid)
        with self.db.tx():
            self.db.x("UPDATE cariler SET aktif=? WHERE id=?", (1 if aktif else 0, cid))
            self.log("GUNCELLEME", "Cari", cid, f"{c['unvan']} {'aktif' if aktif else 'pasif'} yapıldı")

    def delete_cari(self, cid: int) -> None:
        self.require_admin()
        c = self.get_cari(cid)
        if c["hareket_sayisi"]:
            raise ServiceError(f"'{c['unvan']}' carisinin {c['hareket_sayisi']} hareketi var. "
                               "Silmek yerine pasif yapabilirsiniz.")
        with self.db.tx():
            self.db.x("DELETE FROM cariler WHERE id=?", (cid,))
            self.log("SILME", "Cari", cid, f"Cari silindi: {c['unvan']}")

    def cari_statement(self, cid: int, start, end) -> dict:
        s, e = iso(start), iso(end)
        devir = self.db.scalar(
            "SELECT COALESCE(SUM(CASE WHEN tur IN ('CARI_BORC','GIDER') THEN tutar ELSE -tutar END), 0) "
            "FROM hareketler WHERE cari_id=? AND tarih < ?", (cid, s), 0)
        bal = devir
        tb = ta = 0
        rows = []
        for m in self.movements(start=s, end=e, cari_id=cid):
            borc = m["tutar"] if m["tur"] in (CARI_BORC, GIDER) else 0
            alacak = m["tutar"] if m["tur"] in (CARI_ALACAK, GELIR) else 0
            bal += borc - alacak
            tb += borc
            ta += alacak
            rows.append({**m, "borc": borc, "alacak": alacak, "bakiye": bal, "islem": cari_islem_adi(m)})
        return {"devir": devir, "satirlar": rows, "toplam_borc": tb, "toplam_alacak": ta, "bakiye": bal}

    def cari_totals(self) -> dict:
        rows = self.caris(include_inactive=True)
        pos = [r["bakiye"] for r in rows if r["bakiye"] > 0]
        neg = [r["bakiye"] for r in rows if r["bakiye"] < 0]
        return {"alacaklarimiz": sum(pos), "borclarimiz": -sum(neg), "borclu_sayisi": len(pos),
                "alacakli_sayisi": len(neg), "cari_sayisi": len(rows)}

    def import_caris(self, items: list[dict]) -> int:
        """items: {'veri': {...cari alanları}, 'acilis': {...} | None}"""
        self.require_login()
        with self.db.tx():
            for it in items:
                self.save_cari(it["veri"], acilis=it.get("acilis"))
            self.log("AKTARIM", "Cari", None, f"{len(items)} cari yapıştırma ile eklendi")
        return len(items)

    # ================================================================ hareketler
    _MOV_SQL = """
        SELECT h.*, k.ad AS kategori, o.ad AS odeme, o2.ad AS hedef_odeme, c.unvan AS cari, c.kod AS cari_kod,
               u.kullanici_adi AS olusturan, u2.kullanici_adi AS guncelleyen, pr.ad_soyad AS personel
        FROM hareketler h
        LEFT JOIN kategoriler   k  ON k.id  = h.kategori_id
        LEFT JOIN odeme_tipleri o  ON o.id  = h.odeme_tipi_id
        LEFT JOIN odeme_tipleri o2 ON o2.id = h.hedef_odeme_tipi_id
        LEFT JOIN cariler       c  ON c.id  = h.cari_id
        LEFT JOIN kullanicilar  u  ON u.id  = h.olusturan_id
        LEFT JOIN kullanicilar  u2 ON u2.id = h.guncelleyen_id
        LEFT JOIN personeller   pr ON pr.id = h.personel_id
    """

    def _decorate(self, r) -> dict:
        d = dict(r)
        t = d["tur"]
        d["tur_adi"] = TUR_ADI.get(t, t)
        if t == VIRMAN:
            d["odeme_gosterim"] = f"{d['odeme']} → {d['hedef_odeme']}"
            d["kategori_gosterim"] = "Virman"
        elif t in (CARI_BORC, CARI_ALACAK):
            d["odeme_gosterim"] = "Kasa dışı"
            d["kategori_gosterim"] = "Borç fişi" if t == CARI_BORC else "Alacak fişi"
        else:
            d["odeme_gosterim"] = d["odeme"] or ""
            d["kategori_gosterim"] = d["kategori"] or ""
        d["isaretli"] = d["tutar"] if t == GELIR else (-d["tutar"] if t == GIDER else 0)
        d["cari"] = d.get("cari") or ""
        if d.get("personel_id"):
            # personel kaydı: yönetici personel adını görür; kullanıcı rolünde ad, açıklama ve belge no gizlenir
            if self.is_admin:
                d["cari"] = d["cari"] or d.get("personel") or ""
            else:
                d["cari"] = d["cari"] or MASKE
                d["personel"] = ""
                d["aciklama"] = d["belge_no"] = ""
        return d

    def movements(self, start=None, end=None, tur=None, kategori_id=None, odeme_tipi_id=None, cari_id=None,
                  search: str = "", limit: int | None = None, desc: bool = False, personel_id=None) -> list[dict]:
        sql = self._MOV_SQL + " WHERE 1=1"
        p: list = []
        if start:
            sql += " AND h.tarih >= ?"
            p.append(iso(start))
        if end:
            sql += " AND h.tarih <= ?"
            p.append(iso(end))
        if tur:
            turler = list(tur) if isinstance(tur, (list, tuple, set)) else [tur]
            sql += f" AND h.tur IN ({','.join('?' * len(turler))})"
            p += turler
        if kategori_id:
            sql += " AND h.kategori_id=?"
            p.append(kategori_id)
        if odeme_tipi_id:
            sql += " AND (h.odeme_tipi_id=? OR h.hedef_odeme_tipi_id=?)"
            p += [odeme_tipi_id, odeme_tipi_id]
        if cari_id:
            sql += " AND h.cari_id=?"
            p.append(cari_id)
        if personel_id:
            sql += " AND h.personel_id=?"
            p.append(personel_id)
        if search:
            f = f"%{fold(search)}%"
            if self.is_admin:
                cond = ("tr_fold(h.aciklama) LIKE ? OR tr_fold(h.belge_no) LIKE ? OR tr_fold(c.unvan) LIKE ? "
                        "OR tr_fold(k.ad) LIKE ? OR tr_fold(pr.ad_soyad) LIKE ?")
                p += [f] * 5
            else:
                # kullanıcı rolünde personel kayıtlarının gizli alanları aramada eşleşmez
                cond = ("(h.personel_id IS NULL AND (tr_fold(h.aciklama) LIKE ? OR tr_fold(h.belge_no) LIKE ?)) "
                        "OR tr_fold(c.unvan) LIKE ? OR tr_fold(k.ad) LIKE ?")
                p += [f] * 4
            amount = parse_amount(search)
            if amount and abs(amount) <= MAX_AMOUNT:
                cond += " OR h.tutar = ?"
                p.append(abs(amount))
            sql += f" AND ({cond})"
        order = "DESC" if desc else "ASC"
        sql += f" ORDER BY h.tarih {order}, h.saat {order}, h.id {order}"
        if limit:
            sql += " LIMIT ?"
            p.append(int(limit))
        return [self._decorate(r) for r in self.db.q(sql, p)]

    def get_movement(self, mid: int) -> dict:
        r = self.db.q1(self._MOV_SQL + " WHERE h.id=?", (mid,))
        if not r:
            raise ServiceError("Kayıt bulunamadı. Başka bir ekranda silinmiş olabilir.")
        return self._decorate(r)

    @staticmethod
    def describe(m: dict) -> str:
        parts = [m["tur_adi"], fmt_date(m["tarih"])]
        if m["tur"] in (GELIR, GIDER):
            parts.append(f"{m.get('kategori') or ''} / {m.get('odeme') or ''}")
        elif m["tur"] == VIRMAN:
            parts.append(m.get("odeme_gosterim") or "")
        if m.get("cari"):
            parts.append(m["cari"])
        parts.append(fmt_money(m["tutar"], symbol=True))
        if m.get("aciklama"):
            parts.append(m["aciklama"])
        return " · ".join(p for p in parts if p)

    # --- kilit
    def lock_date(self) -> str | None:
        return self.db.scalar("SELECT MAX(tarih) FROM gun_kapanis")

    def is_locked(self, tarih) -> bool:
        lk = self.lock_date()
        return bool(lk) and iso(tarih) <= lk

    def _check_lock(self, tur: str, tarih) -> None:
        if tur in KASA_TURLERI and self.is_locked(tarih):
            raise ServiceError(f"{fmt_date(tarih)} tarihli kasa kapatılmış; bu tarihteki kasa kayıtları "
                               "değiştirilemez. Gerekirse yönetici 'Günü Aç' ile günü yeniden açabilir.")

    # --- doğrulama
    def _valid_pt(self, pid, keep: set) -> int:
        if not pid:
            raise ServiceError("Ödeme tipi seçin.")
        r = self.db.q1("SELECT * FROM odeme_tipleri WHERE id=?", (pid,))
        if not r:
            raise ServiceError("Ödeme tipi bulunamadı.")
        if not r["aktif"] and pid not in keep:
            raise ServiceError(f"'{r['ad']}' ödeme tipi pasif durumda.")
        return int(pid)

    def _valid_cari(self, cid, keep) -> int:
        r = self.db.q1("SELECT * FROM cariler WHERE id=?", (cid,))
        if not r:
            raise ServiceError("Cari bulunamadı.")
        if not r["aktif"] and cid != keep:
            raise ServiceError(f"'{r['unvan']}' carisi pasif durumda.")
        return int(cid)

    def _normalize(self, data: dict, existing: dict | None = None) -> dict:
        tur = data.get("tur")
        if tur not in TUM_TURLER:
            raise ServiceError("Geçersiz kayıt türü.")
        d = to_date(data.get("tarih"))
        if not d:
            raise ServiceError("Geçerli bir tarih girin.")
        if not date_in_range(d):
            raise ServiceError(f"Tarih ({fmt_date(d)}) geçersiz: 2000 yılından önce veya bir yıldan daha ileri olamaz.")
        tutar = data.get("tutar")
        if tutar is None or isinstance(tutar, bool):
            raise ServiceError("Tutar girin.")
        tutar = int(tutar)
        if tutar <= 0:
            raise ServiceError("Tutar sıfırdan büyük olmalı.")
        if tutar > MAX_AMOUNT:
            raise ServiceError("Tutar çok büyük; lütfen kontrol edin.")
        out = {"tur": tur, "tarih": d.isoformat(), "saat": parse_time(data.get("saat")), "tutar": tutar,
               "aciklama": clean_text(data.get("aciklama"), 500), "belge_no": clean_text(data.get("belge_no"), 60),
               "kategori_id": None, "odeme_tipi_id": None, "hedef_odeme_tipi_id": None, "cari_id": None}
        ex = existing or {}
        keep_pts = {ex.get("odeme_tipi_id"), ex.get("hedef_odeme_tipi_id")} - {None}
        if tur in (GELIR, GIDER):
            kid = data.get("kategori_id")
            k = self.db.q1("SELECT * FROM kategoriler WHERE id=?", (kid,)) if kid else None
            if not k:
                raise ServiceError("Kategori seçin.")
            if k["tur"] != tur:
                raise ServiceError(f"'{k['ad']}' bir {tr_lower(TUR_ADI[k['tur']])} kategorisi; "
                                   f"{tr_lower(TUR_ADI[tur])} kaydında kullanılamaz.")
            if not k["aktif"] and kid != ex.get("kategori_id"):
                raise ServiceError(f"'{k['ad']}' kategorisi pasif durumda.")
            out["kategori_id"] = int(kid)
            out["odeme_tipi_id"] = self._valid_pt(data.get("odeme_tipi_id"), keep_pts)
            if data.get("cari_id"):
                out["cari_id"] = self._valid_cari(data["cari_id"], ex.get("cari_id"))
        elif tur == VIRMAN:
            a = self._valid_pt(data.get("odeme_tipi_id"), keep_pts)
            b = self._valid_pt(data.get("hedef_odeme_tipi_id"), keep_pts)
            if a == b:
                raise ServiceError("Virmanda çıkan ve giren ödeme tipi farklı olmalı.")
            out["odeme_tipi_id"], out["hedef_odeme_tipi_id"] = a, b
        else:
            if not data.get("cari_id"):
                raise ServiceError("Cari seçin.")
            out["cari_id"] = self._valid_cari(data["cari_id"], ex.get("cari_id"))
        return out

    # --- ekle / güncelle / sil
    def add_movement(self, data: dict) -> int:
        self.require_login()
        m = self._normalize(data)
        self._check_lock(m["tur"], m["tarih"])
        if not m["saat"] and m["tarih"] == today().isoformat():
            m["saat"] = now_time()
        with self.db.tx():
            cur = self.db.x(
                "INSERT INTO hareketler(tarih, saat, tur, kategori_id, odeme_tipi_id, hedef_odeme_tipi_id, cari_id, "
                "tutar, aciklama, belge_no, olusturan_id, olusturma) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (m["tarih"], m["saat"], m["tur"], m["kategori_id"], m["odeme_tipi_id"], m["hedef_odeme_tipi_id"],
                 m["cari_id"], m["tutar"], m["aciklama"], m["belge_no"], self._uid(), now_iso()))
            mid = cur.lastrowid
            self.log("EKLEME", "Hareket", mid, self.describe(self.get_movement(mid)))
        return mid

    def _check_personel_movement(self, m: dict) -> None:
        if m.get("personel_id") and not self.is_admin:
            raise PermissionDenied("Personel kayıtlarını yalnızca yönetici değiştirebilir veya silebilir.")

    def update_movement(self, mid: int, data: dict) -> None:
        """personel_id / personel_islem bu güncellemede değişmez (UPDATE onlara dokunmaz)."""
        self.require_login()
        old = self.get_movement(mid)
        self._check_personel_movement(old)
        self._check_lock(old["tur"], old["tarih"])
        m = self._normalize(data, existing=old)
        if old.get("personel_id") and (m["tur"] != PERSONEL_KASA[old["personel_islem"]][0] or m["cari_id"]):
            raise ServiceError("Personel kaydının türü değiştirilemez; kaydı silip Personel ekranından yeniden girin.")
        self._check_lock(m["tur"], m["tarih"])
        with self.db.tx():
            self.db.x(
                "UPDATE hareketler SET tarih=?, saat=?, tur=?, kategori_id=?, odeme_tipi_id=?, hedef_odeme_tipi_id=?, "
                "cari_id=?, tutar=?, aciklama=?, belge_no=?, guncelleyen_id=?, guncelleme=? WHERE id=?",
                (m["tarih"], m["saat"], m["tur"], m["kategori_id"], m["odeme_tipi_id"], m["hedef_odeme_tipi_id"],
                 m["cari_id"], m["tutar"], m["aciklama"], m["belge_no"], self._uid(), now_iso(), mid))
            self.log("GUNCELLEME", "Hareket", mid,
                     f"Önce: {self.describe(old)}  →  Sonra: {self.describe(self.get_movement(mid))}")
            if old.get("personel_id"):
                # doğrudan ödemeye bağlı hak ediş ödemeyle birlikte güncellenir; hesap bakiyesi değişmez
                for f in self.db.q("SELECT id FROM personel_fisleri WHERE bagli_hareket_id=?", (mid,)):
                    fo = self._fis(f["id"])
                    self.db.x("UPDATE personel_fisleri SET tarih=?, tutar=?, kategori_id=?, guncelleyen_id=?, "
                              "guncelleme=? WHERE id=?",
                              (m["tarih"], m["tutar"], m["kategori_id"], self._uid(), now_iso(), f["id"]))
                    self.log("GUNCELLEME", "Personel", f["id"], f"Fiş önce: {self.describe_fis(fo)}  →  "
                                                               f"Sonra: {self.describe_fis(self._fis(f['id']))}")

    def delete_movement(self, mid: int) -> None:
        self.require_login()
        old = self.get_movement(mid)
        self._check_personel_movement(old)
        self._check_lock(old["tur"], old["tarih"])
        with self.db.tx():
            # doğrudan ödemede ödemeye bağlı oluşturulan hak ediş fişi de silinir
            for f in self.db.q("SELECT id FROM personel_fisleri WHERE bagli_hareket_id=?", (mid,)):
                self._delete_fis(f["id"])
            self.db.x("DELETE FROM hareketler WHERE id=?", (mid,))
            self.log("SILME", "Hareket", mid, self.describe(old))

    def delete_movements(self, ids: list[int]) -> int:
        with self.db.tx():
            for mid in ids:
                self.delete_movement(mid)
        return len(ids)

    def import_movements(self, items: list[dict]) -> int:
        """Yapıştırılan satırları tek işlemde kaydeder.
        items: {'veri': hareket verisi, 'yeni_kategori': (tur, ad) | None, 'yeni_cari': ünvan | None}"""
        self.require_login()
        if any(it.get("yeni_kategori") for it in items):
            self.require_admin()
        with self.db.tx():
            cat_cache: dict = {}
            cari_cache: dict = {}
            for it in items:
                veri = dict(it["veri"])
                yk = it.get("yeni_kategori")
                if yk:
                    key = (yk[0], fold(yk[1]))
                    if key not in cat_cache:
                        found = [c for c in self.categories(yk[0]) if fold(c["ad"]) == key[1]]
                        cat_cache[key] = found[0]["id"] if found else self.add_category(yk[0], yk[1])
                    veri["kategori_id"] = cat_cache[key]
                yc = it.get("yeni_cari")
                if yc:
                    key = fold(yc)
                    if key not in cari_cache:
                        found = [c for c in self.caris(include_inactive=True) if fold(c["unvan"]) == key]
                        tip = "MUSTERI" if veri.get("tur") == GELIR else "TEDARIKCI"
                        cari_cache[key] = found[0]["id"] if found else self.save_cari({"unvan": yc, "tip": tip})
                    veri["cari_id"] = cari_cache[key]
                self.add_movement(veri)
            self.log("AKTARIM", "Hareket", None, f"{len(items)} hareket yapıştırma ile eklendi")
        return len(items)

    # ================================================================ personel (yalnız yönetici)
    PERSONEL_ALANLARI = ("ad_soyad", "telefon", "iban", "gorev", "ise_giris", "notlar")

    _PERSONEL_SQL = """
        SELECT p.*,
               COALESCE((SELECT SUM(h.tutar) FROM hareketler h WHERE h.personel_id = p.id AND h.tur = 'GIDER'), 0)
             + COALESCE((SELECT SUM(f.tutar) FROM personel_fisleri f
                         WHERE f.personel_id = p.id AND f.islem = 'DEVREDEN_BORC'), 0) AS borc,
               COALESCE((SELECT SUM(h.tutar) FROM hareketler h WHERE h.personel_id = p.id AND h.tur = 'GELIR'), 0)
             + COALESCE((SELECT SUM(f.tutar) FROM personel_fisleri f
                         WHERE f.personel_id = p.id AND f.islem IN ('HAK_EDIS','DEVREDEN_ALACAK')), 0) AS alacak,
               (SELECT MAX(h.tarih) FROM hareketler h WHERE h.personel_id = p.id)     AS son_hareket,
               (SELECT MAX(f.tarih) FROM personel_fisleri f WHERE f.personel_id = p.id) AS son_fis,
               (SELECT COUNT(*) FROM hareketler h WHERE h.personel_id = p.id)        AS hareket_sayisi,
               (SELECT COUNT(*) FROM personel_fisleri f WHERE f.personel_id = p.id)  AS fis_sayisi,
               COALESCE((SELECT SUM(h.tutar) FROM hareketler h WHERE h.personel_id = p.id AND h.tur = 'GIDER'
                         AND h.tarih BETWEEN ? AND ?), 0) AS bu_ay_odenen
        FROM personeller p
    """

    _FIS_SQL = """
        SELECT f.*, k.ad AS kategori, pr.ad_soyad AS personel, u.kullanici_adi AS olusturan
        FROM personel_fisleri f
        LEFT JOIN kategoriler  k  ON k.id  = f.kategori_id
        LEFT JOIN personeller  pr ON pr.id = f.personel_id
        LEFT JOIN kullanicilar u  ON u.id  = f.olusturan_id
    """

    @staticmethod
    def _month_params() -> list:
        t = today()
        return [month_start(t).isoformat(), month_end(t).isoformat()]

    @staticmethod
    def _personel_decorate(r) -> dict:
        d = dict(r)
        d["bakiye"] = d["borc"] - d["alacak"]
        d["durum"] = personel_durum(d["bakiye"])
        d["son_kayit"] = max(d["son_hareket"] or "", d["son_fis"] or "") or None
        d["aktif_durum"] = "Aktif" if d["aktif"] else "Pasif"
        return d

    def personel_listesi(self, search: str = "", include_inactive: bool = False) -> list[dict]:
        self.require_admin()
        sql = self._PERSONEL_SQL + " WHERE 1=1"
        p: list = self._month_params()
        if not include_inactive:
            sql += " AND p.aktif=1"
        if search:
            f = f"%{fold(search)}%"
            sql += (" AND (tr_fold(p.ad_soyad) LIKE ? OR tr_fold(p.kod) LIKE ? OR tr_fold(p.telefon) LIKE ?"
                    " OR tr_fold(p.gorev) LIKE ?)")
            p += [f] * 4
        rows = [self._personel_decorate(r) for r in self.db.q(sql, p)]
        rows.sort(key=lambda r: fold(r["ad_soyad"]))
        return rows

    def get_personel(self, pid: int) -> dict:
        self.require_admin()
        r = self.db.q1(self._PERSONEL_SQL + " WHERE p.id=?", self._month_params() + [pid])
        if not r:
            raise ServiceError("Personel bulunamadı.")
        return self._personel_decorate(r)

    def next_personel_code(self) -> str:
        mx = 0
        for r in self.db.q("SELECT kod FROM personeller WHERE kod LIKE 'P%'"):
            k = r["kod"] or ""
            if k[1:].isdigit():
                mx = max(mx, int(k[1:]))
        return f"P{mx + 1:04d}"

    def _insert_personel(self, d: dict) -> int:
        cur = self.db.x("INSERT INTO personeller(kod, ad_soyad, telefon, iban, gorev, ise_giris, notlar, olusturma) "
                        "VALUES(?,?,?,?,?,?,?,?)", (self.next_personel_code(),) +
                        tuple(d.get(k, "") for k in self.PERSONEL_ALANLARI) + (now_iso(),))
        return cur.lastrowid

    def save_personel(self, data: dict, pid: int | None = None, devreden: dict | None = None) -> int:
        """Yeni personel ekler (pid=None) veya mevcut personeli günceller.
        devreden: {'tutar': kuruş, 'yon': 'BORC'|'ALACAK', 'tarih': date} -> yeni personelde devreden bakiye fişi."""
        self.require_admin()
        d = {}
        for k in self.PERSONEL_ALANLARI:
            v = data.get(k)
            d[k] = str(v or "").strip()[:500] if k == "notlar" else clean_text(v, 150)
        if not d["ad_soyad"]:
            raise ServiceError("Ad soyad boş olamaz.")
        if d["ise_giris"]:
            g = to_date(data.get("ise_giris"))
            if not g:
                raise ServiceError("İşe giriş tarihi anlaşılamadı. Örnek: 01.09.2026")
            d["ise_giris"] = g.isoformat()
        old = self.get_personel(pid) if pid else None
        for r in self.db.q("SELECT id, ad_soyad FROM personeller WHERE id<>?", (pid or -1,)):
            if fold(r["ad_soyad"]) == fold(d["ad_soyad"]):
                raise ServiceError(f"'{d['ad_soyad']}' adlı bir personel zaten kayıtlı.")
        with self.db.tx():
            if old is None:
                pid = self._insert_personel(d)
                self.log("EKLEME", "Personel", pid, f"{d['ad_soyad']} eklendi")
                if devreden and devreden.get("tutar"):
                    islem = "DEVREDEN_BORC" if devreden.get("yon") == "BORC" else "DEVREDEN_ALACAK"
                    self._insert_fis(self._valid_personel(pid), islem, devreden.get("tarih") or today(),
                                     abs(int(devreden["tutar"])), None, "Devreden bakiye")
            else:
                sets = ", ".join(f"{k}=?" for k in self.PERSONEL_ALANLARI)
                self.db.x(f"UPDATE personeller SET {sets} WHERE id=?",
                          tuple(d[k] for k in self.PERSONEL_ALANLARI) + (pid,))
                self.log("GUNCELLEME", "Personel", pid, f"{d['ad_soyad']} bilgileri güncellendi")
        return pid

    def set_personel_active(self, pid: int, aktif: bool) -> None:
        self.require_admin()
        p = self.get_personel(pid)
        with self.db.tx():
            self.db.x("UPDATE personeller SET aktif=? WHERE id=?", (1 if aktif else 0, pid))
            self.log("GUNCELLEME", "Personel", pid, f"{p['ad_soyad']} {'aktif' if aktif else 'pasif'} yapıldı")

    def delete_personel(self, pid: int) -> None:
        self.require_admin()
        p = self.get_personel(pid)
        n = p["hareket_sayisi"] + p["fis_sayisi"]
        if n:
            raise ServiceError(f"'{p['ad_soyad']}' personelinin {n} kaydı var. Silmek yerine pasif yapabilirsiniz.")
        with self.db.tx():
            self.db.x("DELETE FROM personeller WHERE id=?", (pid,))
            self.log("SILME", "Personel", pid, f"Personel silindi: {p['ad_soyad']}")

    # --- doğrulama
    def _valid_personel(self, pid, keep=None) -> dict:
        r = self.db.q1("SELECT * FROM personeller WHERE id=?", (pid,)) if pid else None
        if not r:
            raise ServiceError("Personel seçin.")
        if not r["aktif"] and pid != keep:
            raise ServiceError(f"'{r['ad_soyad']}' pasif durumda.")
        return dict(r)

    def _personel_kalem(self, kid, keep=None) -> int:
        """Hak ediş / ödeme kalemi: bir gider kategorisi; seçilmezse 'Maaş'."""
        if not kid:
            found = [k for k in self.categories(GIDER) if fold(k["ad"]) == fold(MAAS)]
            if not found:
                raise ServiceError("Kalem seçin.")
            kid = found[0]["id"]
        k = self.db.q1("SELECT * FROM kategoriler WHERE id=?", (kid,))
        if not k or k["tur"] != GIDER:
            raise ServiceError("Kalem olarak bir gider kategorisi seçin.")
        if not k["aktif"] and kid != keep:
            raise ServiceError(f"'{k['ad']}' kategorisi pasif durumda.")
        return int(kid)

    def _sabit_kategori(self, tur: str, ad: str) -> int:
        for k in self.categories(tur):
            if fold(k["ad"]) == fold(ad):
                return k["id"]
        raise ServiceError(f"'{ad}' kategorisi bulunamadı. Tanımlar ekranından bu adla bir kategori ekleyin.")

    @staticmethod
    def _fis_tarih_tutar(tarih, tutar) -> tuple[str, int]:
        d = to_date(tarih)
        if not d:
            raise ServiceError("Geçerli bir tarih girin.")
        if not date_in_range(d):
            raise ServiceError(f"Tarih ({fmt_date(d)}) geçersiz: 2000 yılından önce veya bir yıldan daha ileri olamaz.")
        if tutar is None or isinstance(tutar, bool):
            raise ServiceError("Tutar girin.")
        tutar = int(tutar)
        if tutar <= 0:
            raise ServiceError("Tutar sıfırdan büyük olmalı.")
        if tutar > MAX_AMOUNT:
            raise ServiceError("Tutar çok büyük; lütfen kontrol edin.")
        return d.isoformat(), tutar

    # --- kasa dışı fişler (hak ediş, devreden); gün kilidinden etkilenmez
    @staticmethod
    def describe_fis(f: dict) -> str:
        parts = [PERSONEL_ISLEM_ADI.get(f["islem"], f["islem"]), fmt_date(f["tarih"]), f.get("personel") or "",
                 f.get("kategori") or "", fmt_money(f["tutar"], symbol=True), f.get("aciklama") or ""]
        return " · ".join(p for p in parts if p)

    def _fis(self, fid) -> dict:
        r = self.db.q1(self._FIS_SQL + " WHERE f.id=?", (fid,))
        if not r:
            raise ServiceError("Kayıt bulunamadı. Başka bir ekranda silinmiş olabilir.")
        return dict(r)

    def get_personel_fisi(self, fid: int) -> dict:
        self.require_admin()
        return self._fis(fid)

    def _insert_fis(self, p: dict, islem: str, tarih, tutar, kid, aciklama: str = "", bagli=None) -> int:
        if islem not in PERSONEL_FISLERI:
            raise ServiceError("Geçersiz personel işlemi.")
        d, tutar = self._fis_tarih_tutar(tarih, tutar)
        cur = self.db.x("INSERT INTO personel_fisleri(personel_id, tarih, islem, kategori_id, tutar, aciklama, "
                        "bagli_hareket_id, olusturan_id, olusturma) VALUES(?,?,?,?,?,?,?,?,?)",
                        (p["id"], d, islem, kid, tutar, clean_text(aciklama, 500), bagli, self._uid(), now_iso()))
        fid = cur.lastrowid
        self.log("EKLEME", "Personel", fid, "Fiş: " + self.describe_fis(self._fis(fid)))
        return fid

    def _delete_fis(self, fid: int) -> None:
        f = self._fis(fid)
        self.db.x("DELETE FROM personel_fisleri WHERE id=?", (fid,))
        self.log("SILME", "Personel", fid, "Fiş silindi: " + self.describe_fis(f))

    @staticmethod
    def _check_bagli_fis(f: dict) -> None:
        if f.get("bagli_hareket_id"):
            raise ServiceError("Bu hak ediş bir ödemeye bağlı (doğrudan ödeme); ödemeyi düzenleyin veya silin.")

    def update_personel_fisi(self, fid: int, data: dict) -> None:
        self.require_admin()
        old = self.get_personel_fisi(fid)
        self._check_bagli_fis(old)
        islem = data.get("islem") or old["islem"]
        if islem not in PERSONEL_FISLERI:
            raise ServiceError("Kasa dışı bir kayıt, kasa kaydına çevrilemez.")
        p = self._valid_personel(data.get("personel_id") or old["personel_id"], keep=old["personel_id"])
        d, tutar = self._fis_tarih_tutar(data.get("tarih"), data.get("tutar"))
        kid = self._personel_kalem(data.get("kategori_id"), keep=old["kategori_id"]) if islem == "HAK_EDIS" else None
        with self.db.tx():
            self.db.x("UPDATE personel_fisleri SET personel_id=?, tarih=?, islem=?, kategori_id=?, tutar=?, aciklama=?, "
                      "guncelleyen_id=?, guncelleme=? WHERE id=?",
                      (p["id"], d, islem, kid, tutar, clean_text(data.get("aciklama"), 500), self._uid(), now_iso(),
                       fid))
            self.log("GUNCELLEME", "Personel", fid,
                     f"Fiş önce: {self.describe_fis(old)}  →  Sonra: {self.describe_fis(self._fis(fid))}")

    def delete_personel_fisi(self, fid: int) -> None:
        self.require_admin()
        self._check_bagli_fis(self.get_personel_fisi(fid))
        with self.db.tx():
            self._delete_fis(fid)

    # --- kayıt girişi
    def add_personel_kaydi(self, data: dict) -> dict:
        """data: personel_id, islem, tarih, tutar, kategori_id (kalem; HAK_EDIS / ODEME), odeme_tipi_id (kasa
        işlemleri), aciklama, belge_no, saat, hak_edis_de (ODEME: aynı tutarda bağlı hak ediş de oluşturur).
        {'hareket_id', 'fis_id'} döndürür."""
        self.require_admin()
        p = self._valid_personel(data.get("personel_id"))
        with self.db.tx():
            return self._personel_kaydi(p, data)

    def _personel_kaydi(self, p: dict, data: dict) -> dict:
        islem = data.get("islem")
        if islem in PERSONEL_FISLERI:
            kid = self._personel_kalem(data.get("kategori_id")) if islem == "HAK_EDIS" else None
            fid = self._insert_fis(p, islem, data.get("tarih"), data.get("tutar"), kid, data.get("aciklama"))
            return {"hareket_id": None, "fis_id": fid}
        if islem not in PERSONEL_KASA:
            raise ServiceError("Geçersiz personel işlemi.")
        tur, kat_adi = PERSONEL_KASA[islem]
        kid = self._personel_kalem(data.get("kategori_id")) if islem == "ODEME" else self._sabit_kategori(tur, kat_adi)
        # kasa kaydı normal hareket yolundan geçer (doğrulama, gün kilidi, işlem geçmişi)
        mid = self.add_movement({"tarih": data.get("tarih"), "saat": data.get("saat"), "tur": tur, "kategori_id": kid,
                                 "odeme_tipi_id": data.get("odeme_tipi_id"), "tutar": data.get("tutar"),
                                 "aciklama": data.get("aciklama"), "belge_no": data.get("belge_no")})
        self.db.x("UPDATE hareketler SET personel_id=?, personel_islem=? WHERE id=?", (p["id"], islem, mid))
        m = self.get_movement(mid)
        self.log("EKLEME", "Personel", p["id"], f"{p['ad_soyad']}: {PERSONEL_ISLEM_ADI[islem]} "
                                                f"{fmt_money(m['tutar'], symbol=True)} (kayıt no {mid})")
        fid = None
        if islem == "ODEME" and data.get("hak_edis_de"):
            # doğrudan ödeme: aynı kalem, tarih ve tutarda hak ediş; hesap bakiyesi değişmez
            fid = self._insert_fis(p, "HAK_EDIS", m["tarih"], m["tutar"], kid, m["aciklama"], bagli=mid)
        return {"hareket_id": mid, "fis_id": fid}

    def personel_toplu_kaydet(self, tarih, kategori_id, odeme_tipi_id, aciklama: str, satirlar: list[dict]) -> dict:
        """Maaş günü: her satır için hak ediş fişi (hak_edis > 0) ve ödeme (odenen > 0). Tek işlemdir; bir satır
        hatalıysa hiçbiri kaydedilmez."""
        self.require_admin()
        n_hak = n_ode = 0
        kisiler = set()
        with self.db.tx():
            for s in satirlar:
                hak, ode = int(s.get("hak_edis") or 0), int(s.get("odenen") or 0)
                if hak <= 0 and ode <= 0:
                    continue
                p = self._valid_personel(s.get("personel_id"))
                try:
                    if hak > 0:
                        self._personel_kaydi(p, {"islem": "HAK_EDIS", "tarih": tarih, "tutar": hak,
                                                 "kategori_id": kategori_id, "aciklama": aciklama})
                        n_hak += 1
                    if ode > 0:
                        self._personel_kaydi(p, {"islem": "ODEME", "tarih": tarih, "tutar": ode,
                                                 "kategori_id": kategori_id, "odeme_tipi_id": odeme_tipi_id,
                                                 "aciklama": aciklama})
                        n_ode += 1
                except ServiceError as e:
                    raise ServiceError(f"{p['ad_soyad']}: {e}") from e
                kisiler.add(p["id"])
            self.log("AKTARIM", "Personel", None,
                     f"Toplu kayıt ({fmt_date(tarih)}): {len(kisiler)} kişi, {n_hak} hak ediş, {n_ode} ödeme")
        return {"kisi": len(kisiler), "hak_edis": n_hak, "odeme": n_ode}

    # --- hesap dökümü
    def personel_ekstre(self, pid: int, start, end) -> dict:
        self.require_admin()
        s, e = iso(start), iso(end)
        devir = self.db.scalar(
            "SELECT COALESCE(SUM(CASE WHEN tur='GIDER' THEN tutar ELSE -tutar END), 0) FROM hareketler "
            "WHERE personel_id=? AND tur IN ('GELIR','GIDER') AND tarih < ?", (pid, s), 0)
        devir += self.db.scalar(
            "SELECT COALESCE(SUM(CASE WHEN islem='DEVREDEN_BORC' THEN tutar ELSE -tutar END), 0) FROM personel_fisleri "
            "WHERE personel_id=? AND tarih < ?", (pid, s), 0)
        rows = []
        for m in self.movements(start=s, end=e, personel_id=pid):
            rows.append({"kaynak": "hareket", "id": m["id"], "tarih": m["tarih"], "saat": m["saat"] or "",
                         "tur": m["tur"], "islem": PERSONEL_ISLEM_ADI.get(m["personel_islem"], m["tur_adi"]),
                         "kalem": m["kategori"] or "", "odeme": m["odeme_gosterim"],
                         "borc": m["tutar"] if m["tur"] == GIDER else 0,
                         "alacak": m["tutar"] if m["tur"] == GELIR else 0,
                         "aciklama": m["aciklama"], "belge_no": m["belge_no"], "olusturan": m["olusturan"] or ""})
        for f in self.db.q(self._FIS_SQL + " WHERE f.personel_id=? AND f.tarih BETWEEN ? AND ?", (pid, s, e)):
            borc = f["islem"] == "DEVREDEN_BORC"
            # fişin saati yoktur; aynı gün içinde giriş zamanına göre sıralanır
            rows.append({"kaynak": "fis", "id": f["id"], "tarih": f["tarih"], "saat": f["olusturma"][11:16],
                         "tur": CARI_BORC if borc else CARI_ALACAK, "islem": PERSONEL_ISLEM_ADI[f["islem"]],
                         "kalem": f["kategori"] or "", "odeme": "Kasa dışı",
                         "borc": f["tutar"] if borc else 0, "alacak": 0 if borc else f["tutar"],
                         "aciklama": f["aciklama"], "belge_no": "", "olusturan": f["olusturan"] or ""})
        rows.sort(key=lambda r: (r["tarih"], r["saat"], r["id"]))
        bal, tb, ta = devir, 0, 0
        for r in rows:
            bal += r["borc"] - r["alacak"]
            tb += r["borc"]
            ta += r["alacak"]
            r["bakiye"] = bal
        return {"devir": devir, "satirlar": rows, "toplam_borc": tb, "toplam_alacak": ta, "bakiye": bal}

    # --- ilk yükleme
    def ilk_personel_yukle(self, isimler: list[str]) -> int:
        """Gerçek veritabanında bir kez çalışır (girişten hemen sonra, veritabanı açıkken): kayıtlı olmayan isimleri
        alfabetik sırayla kod vererek ekler. Sonradan silinen veya pasif yapılan personel tekrar eklenmez."""
        if self.setting("personel_ilk_yukleme"):
            return 0
        with self.db.tx():
            mevcut = {fold(r["ad_soyad"]) for r in self.db.q("SELECT ad_soyad FROM personeller")}
            yeni = []
            for ad in sorted((clean_text(x, 150) for x in isimler), key=tr_sira):
                if ad and fold(ad) not in mevcut:
                    mevcut.add(fold(ad))
                    yeni.append(ad)
            for ad in yeni:
                self._insert_personel({"ad_soyad": ad})
            self.set_setting("personel_ilk_yukleme", now_iso())
            self.log("EKLEME", "Personel", None, f"İlk personel listesi yüklendi: {len(yeni)} personel")
        return len(yeni)

    # ================================================================ günlük kasa
    def _pt_flows(self, cond: str, params) -> dict:
        sql = f"""
            SELECT pt, SUM(g) AS gelir, SUM(c) AS gider, SUM(vc) AS virman_cikis, SUM(vg) AS virman_giris FROM (
                SELECT odeme_tipi_id AS pt,
                       CASE WHEN tur='GELIR'  THEN tutar ELSE 0 END AS g,
                       CASE WHEN tur='GIDER'  THEN tutar ELSE 0 END AS c,
                       CASE WHEN tur='VIRMAN' THEN tutar ELSE 0 END AS vc,
                       0 AS vg
                FROM hareketler WHERE tur IN ('GELIR','GIDER','VIRMAN') AND {cond}
                UNION ALL
                SELECT hedef_odeme_tipi_id, 0, 0, 0, tutar FROM hareketler WHERE tur='VIRMAN' AND {cond}
            ) GROUP BY pt"""
        out = {}
        for r in self.db.q(sql, list(params) * 2):
            out[r["pt"]] = {"gelir": r["gelir"] or 0, "gider": r["gider"] or 0,
                            "virman_cikis": r["virman_cikis"] or 0, "virman_giris": r["virman_giris"] or 0}
        return out

    def _balances(self, cond: str, params) -> dict:
        flows = self._pt_flows(cond, params)
        out = {}
        for pt in self.db.q("SELECT id, acilis_bakiye FROM odeme_tipleri"):
            f = flows.get(pt["id"], _ZERO)
            out[pt["id"]] = pt["acilis_bakiye"] + f["gelir"] - f["gider"] - f["virman_cikis"] + f["virman_giris"]
        return out

    def balances_before(self, tarih) -> dict:
        """Verilen günün başındaki (devir) bakiyeler: {odeme_tipi_id: kuruş}"""
        return self._balances("tarih < ?", (iso(tarih),))

    def balances_at(self, tarih) -> dict:
        """Verilen günün sonundaki bakiyeler."""
        return self._balances("tarih <= ?", (iso(tarih),))

    def _category_rows(self, start, end, tur: str, include_all: bool) -> list[dict]:
        rows = self.db.q(
            "SELECT k.id, k.ad, k.aktif, COUNT(h.id) AS adet, COALESCE(SUM(h.tutar), 0) AS toplam "
            "FROM kategoriler k LEFT JOIN hareketler h ON h.kategori_id = k.id AND h.tarih BETWEEN ? AND ? "
            "WHERE k.tur=? GROUP BY k.id ORDER BY k.sira, k.id", (iso(start), iso(end), tur))
        out = [dict(r) for r in rows if r["adet"] or (include_all and r["aktif"])]
        total = sum(r["toplam"] for r in out)
        for r in out:
            r["pay"] = (r["toplam"] / total) if total else 0.0
        return out

    def day_report(self, tarih) -> dict:
        d = iso(tarih)
        devir = self.balances_before(d)
        flows = self._pt_flows("tarih = ?", (d,))
        kapanis = _d(self.db.q1("SELECT g.*, u.kullanici_adi AS kapatan FROM gun_kapanis g "
                                "LEFT JOIN kullanicilar u ON u.id = g.kapatan_id WHERE g.tarih=?", (d,)))
        snap = {}
        if kapanis:
            snap = {r["odeme_tipi_id"]: dict(r)
                    for r in self.db.q("SELECT * FROM gun_kapanis_detay WHERE tarih=?", (d,))}
        odeme = []
        for pt in self.payment_types(include_inactive=True):
            f = flows.get(pt["id"], _ZERO)
            if kapanis:
                # Kapatılmış gün: devir, kapanış anındaki kayıttan alınır; sonradan yapılan açılış
                # bakiyesi değişiklikleri veya sonradan eklenen ödeme tipleri kapalı günü değiştirmez.
                if pt["id"] not in snap:
                    if not any(f.values()):
                        continue
                    dv = devir.get(pt["id"], 0)
                else:
                    dv = snap[pt["id"]]["devir"]
            else:
                dv = devir.get(pt["id"], 0)
            son = dv + f["gelir"] - f["gider"] - f["virman_cikis"] + f["virman_giris"]
            if not kapanis and not pt["aktif"] and dv == 0 and son == 0 and not any(f.values()):
                continue
            say = snap[pt["id"]]["sayilan"] if pt["id"] in snap else None
            odeme.append({"id": pt["id"], "ad": pt["ad"], "devir": dv, **f,
                          "virman_net": f["virman_giris"] - f["virman_cikis"], "gun_sonu": son,
                          "sayilan": say, "fark": (say - son) if say is not None else None})
        hareketler = self.movements(start=d, end=d)
        gelir = sum(m["tutar"] for m in hareketler if m["tur"] == GELIR)
        gider = sum(m["tutar"] for m in hareketler if m["tur"] == GIDER)
        ga = sum(1 for m in hareketler if m["tur"] == GELIR)
        ca = sum(1 for m in hareketler if m["tur"] == GIDER)
        return {
            "tarih": d, "odeme": odeme, "hareketler": hareketler,
            "gelir": gelir, "gider": gider, "net": gelir - gider, "gelir_adet": ga, "gider_adet": ca,
            "ort_gelir": round(gelir / ga) if ga else 0, "ort_gider": round(gider / ca) if ca else 0,
            "virman_adet": sum(1 for m in hareketler if m["tur"] == VIRMAN),
            "cari_fis_adet": sum(1 for m in hareketler if m["tur"] in (CARI_BORC, CARI_ALACAK)),
            "kat_gelir": self._category_rows(d, d, GELIR, include_all=True),
            "kat_gider": self._category_rows(d, d, GIDER, include_all=True),
            "kapanis": kapanis, "kapali": kapanis is not None, "kilitli": self.is_locked(d),
            "son_kapanis": self.lock_date(),
        }

    def close_day(self, tarih, sayimlar: dict | None = None, aciklama: str = "") -> dict:
        self.require_login()
        d = iso(tarih)
        if d > today().isoformat():
            raise ServiceError("İleri tarihli bir gün kapatılamaz.")
        lk = self.lock_date()
        if lk and d <= lk:
            raise ServiceError(f"{fmt_date(d)} zaten kapalı (son kapanış: {fmt_date(lk)}).")
        rep = self.day_report(d)
        sayimlar = sayimlar or {}
        with self.db.tx():
            self.db.x("INSERT INTO gun_kapanis(tarih, kapatan_id, kapanis_zamani, aciklama) VALUES(?,?,?,?)",
                      (d, self._uid(), now_iso(), clean_text(aciklama, 1000)))
            notlar = []
            for r in rep["odeme"]:
                say = sayimlar.get(r["id"])
                self.db.x("INSERT INTO gun_kapanis_detay(tarih, odeme_tipi_id, odeme_tipi_ad, devir, giris, cikis, "
                          "sistem_bakiye, sayilan) VALUES(?,?,?,?,?,?,?,?)",
                          (d, r["id"], r["ad"], r["devir"], r["gelir"] + r["virman_giris"],
                           r["gider"] + r["virman_cikis"], r["gun_sonu"], say))
                txt = f"{r['ad']} {fmt_money(r['gun_sonu'])}"
                if say is not None and say != r["gun_sonu"]:
                    txt += f" (sayım farkı {fmt_money(say - r['gun_sonu'], plus=True)})"
                notlar.append(txt)
            self.log("KAPANIS", "Gün", None, f"{fmt_date(d)} kasası kapatıldı. " + "; ".join(notlar))
        return self.day_report(d)

    def closings_from(self, tarih) -> list[str]:
        return [r["tarih"] for r in self.db.q("SELECT tarih FROM gun_kapanis WHERE tarih >= ? ORDER BY tarih",
                                              (iso(tarih),))]

    def reopen_from(self, tarih) -> list[str]:
        """Bu tarihi ve sonrasındaki tüm kapanışları geri alır (yalnız yönetici)."""
        self.require_admin()
        days = self.closings_from(tarih)
        if not days:
            raise ServiceError("Bu tarihte veya sonrasında kapatılmış gün yok.")
        with self.db.tx():
            self.db.x("DELETE FROM gun_kapanis WHERE tarih >= ?", (iso(tarih),))
            self.log("ACMA", "Gün", None, "Yeniden açılan günler: " + ", ".join(fmt_date(x) for x in days))
        return days

    def unclosed_days(self, limit: int = 31) -> list[str]:
        lk = self.lock_date() or "0000-00-00"
        rows = self.db.q("SELECT DISTINCT tarih FROM hareketler WHERE tur IN ('GELIR','GIDER','VIRMAN') "
                         "AND tarih > ? AND tarih < ? ORDER BY tarih", (lk, today().isoformat()))
        return [r["tarih"] for r in rows][-limit:]

    def closing_list(self, limit: int = 400) -> list[dict]:
        rows = self.db.q("SELECT g.*, u.kullanici_adi AS kapatan FROM gun_kapanis g "
                         "LEFT JOIN kullanicilar u ON u.id = g.kapatan_id ORDER BY g.tarih DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # ================================================================ analiz
    def summary(self, start, end) -> dict:
        r = self.db.q1("""
            SELECT COALESCE(SUM(CASE WHEN tur='GELIR' THEN tutar END), 0) AS gelir,
                   COALESCE(SUM(CASE WHEN tur='GIDER' THEN tutar END), 0) AS gider,
                   COALESCE(SUM(CASE WHEN tur='GELIR' THEN 1 END), 0)     AS gelir_adet,
                   COALESCE(SUM(CASE WHEN tur='GIDER' THEN 1 END), 0)     AS gider_adet,
                   COALESCE(SUM(CASE WHEN tur='CARI_BORC' THEN tutar END), 0)   AS cari_borc,
                   COALESCE(SUM(CASE WHEN tur='CARI_ALACAK' THEN tutar END), 0) AS cari_alacak,
                   COALESCE(SUM(CASE WHEN tur='VIRMAN' THEN tutar END), 0)      AS virman,
                   COUNT(DISTINCT CASE WHEN tur IN ('GELIR','GIDER') THEN tarih END) AS gun_sayisi
            FROM hareketler WHERE tarih BETWEEN ? AND ?""", (iso(start), iso(end)))
        d = dict(r)
        d["net"] = d["gelir"] - d["gider"]
        d["adet"] = d["gelir_adet"] + d["gider_adet"]
        d["ort_gelir"] = round(d["gelir"] / d["gelir_adet"]) if d["gelir_adet"] else 0
        d["ort_gider"] = round(d["gider"] / d["gider_adet"]) if d["gider_adet"] else 0
        d["gunluk_gelir"] = round(d["gelir"] / d["gun_sayisi"]) if d["gun_sayisi"] else 0
        d["gunluk_gider"] = round(d["gider"] / d["gun_sayisi"]) if d["gun_sayisi"] else 0
        return d

    def category_totals(self, start, end, tur: str) -> list[dict]:
        rows = self._category_rows(start, end, tur, include_all=False)
        rows.sort(key=lambda r: r["toplam"], reverse=True)
        return rows

    def payment_totals(self, start, end) -> list[dict]:
        flows = self._pt_flows("tarih BETWEEN ? AND ?", (iso(start), iso(end)))
        out = []
        for pt in self.payment_types(include_inactive=True):
            f = flows.get(pt["id"], _ZERO)
            if not any(f.values()) and not pt["aktif"]:
                continue
            out.append({"id": pt["id"], "ad": pt["ad"], "gelir": f["gelir"], "gider": f["gider"],
                        "net": f["gelir"] - f["gider"], "virman_giris": f["virman_giris"],
                        "virman_cikis": f["virman_cikis"]})
        return out

    def data_range(self):
        r = self.db.q1("SELECT MIN(tarih), MAX(tarih) FROM hareketler")
        return (to_date(r[0]), to_date(r[1])) if r and r[0] else (None, None)

    def span(self, start, end, audit: bool = False) -> tuple[dt.date, dt.date]:
        """'Tüm Zamanlar' seçildiğinde dönemin açık uçlarını (2000 / 2099) kayıtların bulunduğu aralığa çeker;
        ekranda, raporda ve dosya adında 01.01.2000 – 31.12.2099 görünmez. Elle seçilen tarihlere dokunulmaz.
        audit=True: aralık işlem geçmişinden alınır."""
        s, e = to_date(start), to_date(end)
        if s > e:
            s, e = e, s
        open_s, open_e = s <= ALL_TIME_START, e >= ALL_TIME_END
        if not (open_s or open_e):
            return s, e
        if audit:
            r = self.db.q1("SELECT MIN(substr(zaman, 1, 10)), MAX(substr(zaman, 1, 10)) FROM islem_gecmisi")
        else:
            r = self.db.q1("SELECT MIN(t), MAX(t) FROM (SELECT tarih AS t FROM hareketler "
                           "UNION ALL SELECT tarih FROM gun_kapanis UNION ALL SELECT tarih FROM personel_fisleri)")
        first, last = (to_date(r[0]), to_date(r[1])) if r and r[0] else (None, None)
        if open_e:
            e = max(today(), last) if last else today()
        if open_s:
            s = min(first, e) if first else e
        return s, e

    def series(self, start, end, group: str | None = None):
        """Gelir/gider zaman serisi. (grup, satırlar) döndürür; boş dönemler 0 ile doldurulur."""
        s, e = to_date(start), to_date(end)
        first, last = self.data_range()
        if first and s < first:
            s = first
        upper = max(today(), last) if last else today()
        if e > upper:
            e = upper
        if s > e:
            return group or "day", []
        group = group or auto_group(s, e)
        data: dict = {}
        for r in self.db.q("SELECT tarih, tur, SUM(tutar) AS t FROM hareketler WHERE tur IN ('GELIR','GIDER') "
                           "AND tarih BETWEEN ? AND ? GROUP BY tarih, tur", (s.isoformat(), e.isoformat())):
            slot = data.setdefault(group_key(to_date(r["tarih"]), group), [0, 0])
            slot[0 if r["tur"] == GELIR else 1] += r["t"]
        rows = []
        cum = 0
        for k in iter_group_keys(s, e, group):
            g, c = data.get(k, (0, 0))
            cum += g - c
            rows.append({"key": k, "etiket": group_label(k, group), "baslik": group_title(k, group),
                         "gelir": g, "gider": c, "net": g - c, "kumulatif": cum})
        return group, rows

    def top_caris(self, start, end, tur: str, limit: int = 8) -> list[dict]:
        rows = self.db.q("SELECT c.id, c.unvan, COUNT(*) AS adet, SUM(h.tutar) AS toplam FROM hareketler h "
                         "JOIN cariler c ON c.id = h.cari_id WHERE h.tur=? AND h.tarih BETWEEN ? AND ? "
                         "GROUP BY c.id ORDER BY toplam DESC LIMIT ?", (tur, iso(start), iso(end), limit))
        return [dict(r) for r in rows]

    # ================================================================ dışa aktarım
    def export_tables(self) -> list[dict]:
        """Tüm verinin Excel'e aktarılacak tablo listesi."""
        mov = self.movements()
        caris = self.caris(include_inactive=True)
        guncel = self.balances_at(dt.date(2099, 12, 31))
        sheets = [
            {"name": "Hareketler", "headers": ["No", "Tarih", "Saat", "Tür", "Kategori", "Ödeme Tipi", "Cari",
                                               "Tutar", "Açıklama", "Belge No", "Kaydeden", "Kayıt Zamanı"],
             "types": ["int", "date", "text", "text", "text", "text", "text", "money", "text", "text", "text", "text"],
             "rows": [[m["id"], m["tarih"], m["saat"], m["tur_adi"], m["kategori_gosterim"], m["odeme_gosterim"],
                       m["cari"], m["tutar"], m["aciklama"], m["belge_no"], m["olusturan"] or "", m["olusturma"]]
                      for m in mov]},
            {"name": "Cariler", "headers": ["Kod", "Ünvan", "Tip", "Yetkili", "Telefon", "E-posta", "Vergi Dairesi",
                                            "Vergi No", "Adres", "Borç", "Alacak", "Bakiye", "Durum", "Aktif"],
             "types": ["text"] * 9 + ["money", "money", "money", "text", "text"],
             "rows": [[c["kod"], c["unvan"], c["tip_adi"], c["yetkili"], c["telefon"], c["eposta"],
                       c["vergi_dairesi"], c["vergi_no"], c["adres"], c["borc"], c["alacak"], c["bakiye"],
                       c["durum"], c["aktif_durum"]] for c in caris]},
            {"name": "Kategoriler", "headers": ["Tür", "Kategori", "Durum", "Kullanım"],
             "types": ["text", "text", "text", "int"],
             "rows": [[TUR_ADI[k["tur"]], k["ad"], k["durum"], k["kullanim"]] for k in self.categories()]},
            {"name": "Ödeme Tipleri", "headers": ["Ödeme Tipi", "Açılış Bakiyesi", "Güncel Bakiye", "Durum"],
             "types": ["text", "money", "money", "text"],
             "rows": [[o["ad"], o["acilis_bakiye"], guncel.get(o["id"], 0), o["durum"]]
                      for o in self.payment_types()]},
            {"name": "Gün Kapanışları", "headers": ["Tarih", "Kapatan", "Kapanış Zamanı", "Açıklama"],
             "types": ["date", "text", "text", "text"],
             "rows": [[g["tarih"], g["kapatan"] or "", g["kapanis_zamani"], g["aciklama"]]
                      for g in self.closing_list(100000)]},
        ]
        if self.is_admin:
            sheets.append({"name": "Personel", "headers": ["Kod", "Ad Soyad", "Görev", "Telefon", "IBAN", "Bakiye",
                                                           "Durum"],
                           "types": ["text"] * 5 + ["money", "text"],
                           "rows": [[p["kod"], p["ad_soyad"], p["gorev"], p["telefon"], p["iban"], p["bakiye"],
                                     p["durum"]] for p in self.personel_listesi(include_inactive=True)]})
        return sheets
