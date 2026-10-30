"""SQLite veritabanı: bağlantı, şema, sürüm yükseltme ve ayarlar.

Veritabanı diskte yalnızca şifreli durur (sifreleme.py, .kdb kabı). Program açıkken veri bellekteki bir SQLite
bağlantısında çalışır; her tamamlanan yazma işleminden sonra bellekteki görüntü şifrelenip güvenli biçimde
(geçici dosya + fsync + tek adımda yer değiştirme) diske yazılır. Diske düz geçici dosya veya günlük (journal)
dosyası yazılmaz.

Şifreli dosyayı program şifreleri açar: her kullanıcının kendi kilidi (şifresinden türetilen) ve kurtarma anahtarı
kilidi dosya başlığındadır. Var olan dosya açılışta kilitli kalır; giriş ekranında yazılan şifreyle açılır."""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import sifreleme
from .errors import ServiceError
from .util import fold

log = logging.getLogger("kasatakip")

SCHEMA_VERSION = 2
GECIS_OZETI = "sifreye_gecis_ozet"     # ayar: şifreye çevrilen düz kasa.db dosyasının SHA-256 özeti
KILITLI = "Veritabanı kilitli; önce giriş yapın."


class VeritabaniKilitli(ServiceError):
    """Veritabanı kilitli (henüz şifreyle açılmadı)."""

DEFAULT_GELIR = ["Kargo Geliri", "Nakit Tahsilat", "Kart Tahsilatı", "Çek Tahsilatı",
                 "Havale/EFT Tahsilatı", "Diğer"]
DEFAULT_GIDER = ["Maaş", "Yakıt", "Nakliye", "Kira", "Servis/Ulaşım", "Merkeze Gönderilen",
                 "Vergi/Resmi Ödeme", "Ofis/Kırtasiye", "Bakım/Onarım", "Diğer"]
DEFAULT_ODEME = ["Nakit", "Kart", "Havale", "Çek"]

_SCHEMA_V1 = [
    """CREATE TABLE ayarlar(
        anahtar TEXT PRIMARY KEY,
        deger   TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE kullanicilar(
        id                 INTEGER PRIMARY KEY,
        kullanici_adi      TEXT NOT NULL UNIQUE COLLATE NOCASE,
        ad_soyad           TEXT NOT NULL DEFAULT '',
        sifre_hash         TEXT NOT NULL,
        rol                TEXT NOT NULL CHECK(rol IN ('yonetici','kullanici')),
        aktif              INTEGER NOT NULL DEFAULT 1,
        sifre_degistirmeli INTEGER NOT NULL DEFAULT 0,
        olusturma          TEXT NOT NULL,
        son_giris          TEXT)""",
    """CREATE TABLE kategoriler(
        id    INTEGER PRIMARY KEY,
        tur   TEXT NOT NULL CHECK(tur IN ('GELIR','GIDER')),
        ad    TEXT NOT NULL,
        aktif INTEGER NOT NULL DEFAULT 1,
        sira  INTEGER NOT NULL DEFAULT 0,
        UNIQUE(tur, ad))""",
    """CREATE TABLE odeme_tipleri(
        id            INTEGER PRIMARY KEY,
        ad            TEXT NOT NULL UNIQUE,
        acilis_bakiye INTEGER NOT NULL DEFAULT 0,
        aktif         INTEGER NOT NULL DEFAULT 1,
        sira          INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE cariler(
        id            INTEGER PRIMARY KEY,
        kod           TEXT UNIQUE,
        unvan         TEXT NOT NULL,
        tip           TEXT NOT NULL DEFAULT 'MUSTERI' CHECK(tip IN ('MUSTERI','TEDARIKCI','HER_IKISI')),
        yetkili       TEXT NOT NULL DEFAULT '',
        telefon       TEXT NOT NULL DEFAULT '',
        eposta        TEXT NOT NULL DEFAULT '',
        vergi_dairesi TEXT NOT NULL DEFAULT '',
        vergi_no      TEXT NOT NULL DEFAULT '',
        adres         TEXT NOT NULL DEFAULT '',
        notlar        TEXT NOT NULL DEFAULT '',
        aktif         INTEGER NOT NULL DEFAULT 1,
        olusturma     TEXT NOT NULL)""",
    """CREATE TABLE hareketler(
        id                  INTEGER PRIMARY KEY,
        tarih               TEXT NOT NULL,
        saat                TEXT NOT NULL DEFAULT '',
        tur                 TEXT NOT NULL CHECK(tur IN ('GELIR','GIDER','VIRMAN','CARI_BORC','CARI_ALACAK')),
        kategori_id         INTEGER REFERENCES kategoriler(id),
        odeme_tipi_id       INTEGER REFERENCES odeme_tipleri(id),
        hedef_odeme_tipi_id INTEGER REFERENCES odeme_tipleri(id),
        cari_id             INTEGER REFERENCES cariler(id),
        tutar               INTEGER NOT NULL CHECK(tutar > 0),
        aciklama            TEXT NOT NULL DEFAULT '',
        belge_no            TEXT NOT NULL DEFAULT '',
        olusturan_id        INTEGER,
        olusturma           TEXT NOT NULL,
        guncelleyen_id      INTEGER,
        guncelleme          TEXT)""",
    "CREATE INDEX ix_hareket_tarih ON hareketler(tarih)",
    "CREATE INDEX ix_hareket_cari ON hareketler(cari_id)",
    "CREATE INDEX ix_hareket_kategori ON hareketler(kategori_id)",
    "CREATE INDEX ix_hareket_odeme ON hareketler(odeme_tipi_id)",
    """CREATE TABLE gun_kapanis(
        tarih          TEXT PRIMARY KEY,
        kapatan_id     INTEGER,
        kapanis_zamani TEXT NOT NULL,
        aciklama       TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE gun_kapanis_detay(
        tarih         TEXT NOT NULL REFERENCES gun_kapanis(tarih) ON DELETE CASCADE,
        odeme_tipi_id INTEGER NOT NULL,
        odeme_tipi_ad TEXT NOT NULL,
        devir         INTEGER NOT NULL,
        giris         INTEGER NOT NULL,
        cikis         INTEGER NOT NULL,
        sistem_bakiye INTEGER NOT NULL,
        sayilan       INTEGER,
        PRIMARY KEY(tarih, odeme_tipi_id))""",
    """CREATE TABLE islem_gecmisi(
        id            INTEGER PRIMARY KEY,
        zaman         TEXT NOT NULL,
        kullanici_id  INTEGER,
        kullanici_adi TEXT NOT NULL DEFAULT '',
        islem         TEXT NOT NULL,
        konu          TEXT NOT NULL,
        kayit_id      INTEGER,
        detay         TEXT NOT NULL DEFAULT '')""",
    "CREATE INDEX ix_gecmis_zaman ON islem_gecmisi(zaman)",
]


def tablo_sayilari(conn: sqlite3.Connection) -> dict:
    """Her tablonun satır sayısı (şifreye geçişte ve yedek dönüştürmede iki kopyayı karşılaştırmak için)."""
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}


class Database:
    """path bir .kdb dosyasıdır (uzantı zorunlu değil).
    - Dosya yoksa yeni şifreli veritabanı açık olarak oluşturulur; ilk kullanıcı kilidi eklenene kadar geçici
      kilitle okunabilir kalır (sifreleme.py, 'gecici').
    - Var olan şifreli dosyanın yalnızca başlığı okunur ve veritabanı KİLİTLİ kalır. Kilitliyken get_setting
      yalnızca giriş ekranının ayarlarını (başlıktaki meta) verir; diğer her sorgu VeritabaniKilitli hatası verir.
      ac(kullanıcı adı, şifre) ya da ac_kurtarma(anahtar) ile açılır, sonra sürüm yükseltme yapılır.
    - Yol .kdb ile bitiyor, şifreli dosya henüz yok ve yanında eski düz kasa.db (1.1.x) varsa, ilk yönetici
      girişine kadar düz dosyayla eskisi gibi çalışılır; geçiş o girişte yazılan şifreyle yapılır (sifreye_gec).
      Düz dosyanın sürümü açılışta yükseltilmez (bkz. duz_yukselt). kasa.kdb'nin kendisi düz SQLite ise (ör. 1.1.x
      yedeği bu adla kopyalanmış) kasa.db adını alır ve aynı yoldan şifrelenir (_duz_kdb).
    Var olan şifreli dosyanın üzerine hiçbir zaman otomatik yazılmaz, o dosya silinmez."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ilk_degerler()
        self._ac()
        if self.kilitli:
            return
        if self.gecis_bekliyor and 1 <= self.user_version() <= SCHEMA_VERSION:
            # 1.1.x düz dosyası açılışta yükseltilmez: yönetici geçişine kadar dosya bayt bayt aynı kalır (1.1.x ile
            # yeniden açılabilir, düz günlük dosyası yazılmaz). Yükseltme geçişte bellekteki kopyada yapılır; yönetici
            # olmayan bir kullanıcı önce girerse o girişte dosyanın kendisinde (duz_yukselt)
            return
        self.migrate()
        if self.sifreli and not self.path.exists():
            self._kalici_yaz(zorla=True)

    def _ilk_degerler(self) -> None:
        self._depth = 0
        self.sifreli = True          # False: eski düz dosyayla çalışılıyor (geçiş bekliyor ya da başarısız oldu)
        self.kilitli = False         # True: şifreli dosya henüz açılmadı (yalnızca başlık okundu)
        self.uyari = ""              # kullanıcıya bir kez gösterilecek uyarı (girişten sonra)
        self.anahtar: sifreleme.Anahtar | None = None
        self._kilit_bilgisi: sifreleme.Anahtar | None = None   # kilitliyken başlıktaki kilitler (veri anahtarı yok)
        self._meta: dict = {}                                   # kilitliyken başlıktaki giriş ekranı ayarları
        self._gecis_hedefi: Path | None = None                  # düz kasa.db'nin çevrileceği şifreli dosya
        self._kilit_bekleyen: list | None = None                # işlem sonunda verilerle birlikte yazılacak kilitler
        self._kirli = False
        self._son_durum = None
        self._son_goruntu: bytes | None = None   # diske en son başarıyla yazılan düz görüntü (yalnızca bellekte)
        self.conn: sqlite3.Connection | None = None

    @classmethod
    def bellekte(cls, veri: bytes) -> "Database":
        """Dosyaya bağlı olmayan geçici veritabanı; diske hiç yazılmaz (geri yüklemeden önce sürüm yükseltmeyi
        bellekte denemek için)."""
        db = cls.__new__(cls)
        db.path = None
        db._ilk_degerler()
        db.sifreli = False
        db.conn = db._bellek(veri)
        db.migrate()
        return db

    @staticmethod
    def connect(path) -> sqlite3.Connection:
        """Düz SQLite dosyasına doğrudan bağlantı (1.1.x dosyasıyla, şifreye geçişten önce kullanılır)."""
        conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.execute("PRAGMA synchronous = FULL")
        conn.create_function("tr_fold", 1, fold, deterministic=True)
        return conn

    @staticmethod
    def _ayarla(conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = MEMORY")     # günlük ve geçici tablolar diske yazılmaz
        conn.execute("PRAGMA temp_store = MEMORY")

    def _bellek(self, veri: bytes | None = None) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        if veri:
            conn.deserialize(veri)
        self._ayarla(conn)
        conn.create_function("tr_fold", 1, fold, deterministic=True)
        return conn

    def _eski_duz(self) -> Path | None:
        p = self._gecis_hedefi or self.path
        return p.with_suffix(".db") if p is not None and p.suffix.lower() == ".kdb" else None

    # ------------------------------------------------------------------ açılış ve kilit
    def _ac(self) -> None:
        eski = self._eski_duz()
        yok = not self.path.exists() or self.path.stat().st_size == 0
        if eski and eski.exists() and yok:
            # şifreli dosya yok: 1.1.x'ten güncelleme (ya da önceki geçiş başarısız oldu). Kilit ancak yazılan
            # şifreden üretilebildiği için ilk yönetici girişine kadar düz dosyayla eskisi gibi çalışılır.
            self.sifreli, self._gecis_hedefi, self.path = False, self.path, eski
            self.conn = self.connect(eski)
            return
        if yok:
            self.anahtar = sifreleme.yeni_anahtar(gecici=True)
            self.conn = self._bellek()
            self._son_goruntu = self.conn.serialize()
            return
        with open(self.path, "rb") as f:
            bas = f.read(len(sifreleme.SQLITE_BASLIK))
        if sifreleme.sqlite_mi(bas):
            if eski:
                self._duz_kdb(eski)          # şifreli olması gereken kasa.kdb düz SQLite (ör. 1.1.x yedeği kopyalanmış)
                return
            # .kdb olmayan yolla düz SQLite dosyasının kendisi verilmiş: eskisi gibi doğrudan dosyayla çalışılır
            self.sifreli = False
            self.conn = self.connect(self.path)
            return
        baslik = sifreleme.baslik_dosyadan(self.path)
        self._kilit_bilgisi = sifreleme.kilitler(baslik)
        meta = baslik.get("meta")
        self._meta = {k: v for k, v in meta.items() if isinstance(v, str)} if isinstance(meta, dict) else {}
        self.kilitli = True
        if self._kilit_bilgisi.gecici:
            self._kilidi_ac()           # henüz kullanıcısı olmayan veritabanı (kurulum yarım kalmış): geçici kilit

    def _duz_kdb(self, eski: Path) -> None:
        """kasa.kdb şifreli değil, düz bir SQLite dosyası (ör. 1.1.x'in .db yedeği bu adla kopyalanmış). Şifresiz
        çalışmaya devam edilmez: yanında kasa.db yoksa dosya tek adımda kasa.db adını alır ve 1.1.x dosyası gibi açılır
        (sonraki yönetici girişinde şifrelenir). kasa.db de varsa hangisinin güncel olduğu bilinemez: program açılmaz,
        iki dosyaya da dokunulmaz."""
        kdb, ne = self.path, ""
        if not eski.exists():
            gunluk = kdb.with_name(kdb.name + "-journal")       # yarım kalmış işlemin günlüğü dosyayla birlikte taşınır
            yeni_gunluk = eski.with_name(eski.name + "-journal")
            tasindi = False
            try:
                if gunluk.exists():
                    gunluk.rename(yeni_gunluk)
                    tasindi = True
                kdb.rename(eski)
            except OSError as e:
                log.exception("Düz kasa.kdb, kasa.db adına taşınamadı: %s", kdb)
                if tasindi:
                    try:
                        yeni_gunluk.rename(gunluk)
                    except OSError:
                        pass
                ne = f"Dosya kasa.db adına taşınamadı ({e})."
            else:
                log.warning("kasa.kdb düz SQLite dosyasıydı; kasa.db adına taşındı, yönetici girişinde şifrelenecek")
                self.sifreli, self._gecis_hedefi, self.path = False, kdb, eski
                self.conn = self.connect(eski)
                return
        else:
            ne = ("Yanında eski kasa.db dosyası da var; hangisinin güncel olduğu bilinemediği için iki dosyaya da "
                  "dokunulmadı.")
        self.uyari = ("Veri klasöründeki kasa.kdb şifreli değil (düz bir SQLite dosyası; ör. eski bir .db yedeği bu adla "
                      f"kopyalanmış). Program verileri şifresiz kullanmaz. {ne} Bir yedeği yüklemek istiyorsanız "
                      "kasa.kdb'yi veri klasöründen kaldırın, programı açıp yedeği Yedekleme → Geri Yükle ile seçin."
                      f"\n\n({kdb.parent})")
        raise sifreleme.SifrelemeHatasi(self.uyari)

    def ac(self, kullanici_adi: str, sifre: str) -> None:
        """Kilitli veritabanını bir kullanıcının şifresiyle açar. Yanlış kullanıcı / şifre: başarısız girişle aynı
        metinle sifreleme.AnahtarHatali."""
        if self.kilitli:
            self._kilidi_ac(kullanici=fold((kullanici_adi or "").strip()), sifre=sifre or "")

    def ac_kurtarma(self, anahtar: str) -> None:
        """Kilitli veritabanını kurtarma anahtarıyla açar (yanlışsa sifreleme.AnahtarHatali)."""
        if self.kilitli:
            self._kilidi_ac(kurtarma_anahtari=anahtar or "")

    def _kilidi_ac(self, **giris) -> None:
        blob = sifreleme.dosya_oku(self.path)       # okunamazsa (dosya başka programca tutuluyor) SifrelemeHatasi
        veri, anahtar = sifreleme.coz(blob, **giris)
        del blob
        self.conn, self.anahtar, self.kilitli = self._bellek(veri), anahtar, False
        self._son_goruntu = veri
        self._son_durum = self._durum()
        try:
            self.migrate()
        except (sqlite3.Error, RuntimeError) as e:
            # ör. programın daha yeni sürümünün veritabanı: kilitli hale dönülür, gerçek neden gösterilir
            log.exception("Veritabanı açıldı ama hazırlanamadı")
            self.conn.close()
            self.conn, self.anahtar, self.kilitli, self._son_goruntu = None, None, True, None
            raise ServiceError(f"Veritabanı açılamadı: {e}") from e
        self._kilit_bilgisi, self._meta = None, {}
        eski = self._eski_duz()
        if eski and eski.exists():
            self._kalan_duz(eski)

    def _acik(self) -> None:
        if self.kilitli:
            raise VeritabaniKilitli(KILITLI)

    def _kalan_duz(self, eski: Path) -> None:
        """Şifreli dosyanın yanında eski düz kasa.db de var. Yalnızca boş / başı sıfırlanmış (silme yarıda
        kalmış) ya da şifreye çevrilen dosyanın birebir aynısıysa (özet aynı) silinir. Başka bir düz dosya
        (ör. eski sürümle açılmış ya da elle kopyalanmış) silinmez, şifreli verinin üzerine de yazılmaz:
        kenara alınır ve kullanıcı uyarılır."""
        try:
            ham = eski.read_bytes()
        except OSError:
            log.exception("Eski düz veritabanı okunamadı: %s", eski)
            return
        try:
            ozet = self.get_setting(GECIS_OZETI)
        except sqlite3.Error:
            ozet = ""
        silinebilir = (not ham[:len(sifreleme.SQLITE_BASLIK)].strip(b"\x00")
                       or (ozet and hashlib.sha256(ham).hexdigest() == ozet))
        del ham
        if silinebilir:
            self._duz_sil(eski)
            return
        hedef = eski.with_name(f"{eski.name}.eski-{dt.datetime.now():%Y%m%d-%H%M%S}")
        try:
            eski.rename(hedef)
            gunluk = eski.with_name(eski.name + "-journal")
            if gunluk.exists():
                gunluk.rename(hedef.with_name(hedef.name + "-journal"))
            ne = f"Bu dosya silinmedi, aynı klasörde '{hedef.name}' adıyla kenara alındı."
        except OSError as e:
            log.exception("Eski düz veritabanı kenara alınamadı: %s", eski)
            ne = f"Bu dosya silinmedi; kenara da alınamadı ({e})."
        log.warning("Şifreli veritabanının yanında farklı içerikli düz kasa.db bulundu: %s", eski)
        self.uyari = ("Veri klasöründe eski (şifresiz) bir kasa.db dosyası bulundu ve içeriği şifreli verilerinizden "
                      f"farklı. Program şifreli verilerle çalışıyor. {ne} İçindeki kayıtlara ihtiyacınız yoksa "
                      f"dosyayı silebilirsiniz; varsa destek alın.\n\n({eski.parent})")

    # ------------------------------------------------------------------ şifreye geçiş (1.1.x düz kasa.db)
    @property
    def gecis_bekliyor(self) -> bool:
        """Düz kasa.db ile çalışılıyor ve ilk yönetici girişinde şifreli dosyaya çevrilecek."""
        return self._gecis_hedefi is not None

    def duz_yukselt(self) -> None:
        """Geçiş beklerken düz kasa.db'nin sürümünü dosyanın kendisinde yükseltir. Yalnızca program düz dosyayla
        çalışacaksa çağrılır (yönetici olmayan kullanıcı geçişten önce girdi, geçiş başarısız oldu ya da ilk kurulum
        düz dosyada yapılıyor); bundan sonra dosya 1.1.x ile açılamaz."""
        if not self.gecis_bekliyor or self.user_version() >= SCHEMA_VERSION:
            return
        try:
            self.migrate()
        except (sqlite3.Error, RuntimeError) as e:
            log.exception("Düz veritabanının sürümü yükseltilemedi")
            raise ServiceError(f"Veritabanı hazırlanamadı: {e}") from e

    def sifreye_gec(self, kullanici_adi: str, sifre: str) -> bool:
        """Eski düz kasa.db'yi, yöneticinin girişte yazdığı şifrenin kilidiyle şifreli dosyaya çevirir. Şifreli dosya
        diskten, bir sonraki açılıştaki gibi bu şifreyle yeniden açılıp karşılaştırılmadan düz dosyaya dokunulmaz.
        Herhangi bir adım başarısız olursa düz dosyayla eskisi gibi çalışılır (uyari dolar) ve bir sonraki yönetici
        girişinde yeniden denenir. Çevrilen düz dosyanın özeti şifreli veride saklanır: silme yarıda kalırsa
        sonraki açılışta kalan dosyanın aynı dosya olduğu anlaşılır (_kalan_duz). Kurtarma kilidi burada
        kurulmaz; girişten sonraki adım yeni kurtarma anahtarı oluşturup gösterir (app.py)."""
        hedef, eski = self._gecis_hedefi, self.path
        if hedef is None or self.conn.in_transaction:
            return False
        onceden = hedef.exists() and hedef.stat().st_size > 0   # (savunma: yalnız dosya yokken geçiş bekler)
        ad = fold((kullanici_adi or "").strip())
        try:
            if onceden:
                raise RuntimeError("şifreli dosya zaten var")
            src = sqlite3.connect(str(eski))        # yarım kalmış işlem günlüğü varsa SQLite onu geri alır
            try:
                res = src.execute("PRAGMA integrity_check").fetchone()[0]
                if res != "ok":
                    raise RuntimeError(f"düz veritabanı bütünlük denetimi: {res}")
                veri = src.serialize()
            finally:
                src.close()
            # sürüm yükseltmesi (1.1.x: şema 1 -> 2) bellekteki kopyada yapılır; düz dosyaya yazılmaz
            conn = Database.bellekte(veri).conn
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ayarlar'").fetchone():
                conn.execute("INSERT INTO ayarlar(anahtar, deger) VALUES(?, ?) ON CONFLICT(anahtar) DO UPDATE "
                             "SET deger=excluded.deger", (GECIS_OZETI, hashlib.sha256(eski.read_bytes()).hexdigest()))
            sayilar = tablo_sayilari(conn)
            veri = conn.serialize()
            meta = self._meta_oku(conn)
            conn.close()
            anahtar = sifreleme.yeni_anahtar()
            anahtar.kullanicilar[ad] = sifreleme.sifre_sar(anahtar, sifre)
            sifreleme.yaz(hedef, sifreleme.paketle(veri, anahtar, meta))
            veri2, _ = sifreleme.oku(hedef, kullanici=ad, sifre=sifre)   # şifreyle, sonraki açılıştaki gibi
            conn = self._bellek(veri2)
            res = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if res != "ok" or tablo_sayilari(conn) != sayilar:
                conn.close()
                raise RuntimeError(f"şifreli kopya düz dosyayla aynı değil ({res})")
        except Exception as e:  # noqa: BLE001
            log.exception("Şifreli veritabanına geçiş başarısız; düz dosyayla devam ediliyor")
            if not onceden:                          # yalnızca bu denemenin yazdığı yarım şifreli dosya silinir
                try:
                    hedef.unlink(missing_ok=True)
                except OSError:
                    pass
            self.uyari = ("Veritabanı şifreli biçime çevrilemedi; program şimdilik eski (şifresiz) dosyayla "
                          "çalışıyor ve bir sonraki yönetici girişinde yeniden deneyecek. Verileriniz "
                          f"etkilenmedi.\n\n({e})")
            return False
        try:
            self.conn.close()                        # düz dosya bağlantısı: silinebilmesi için kapatılır
        except sqlite3.Error:
            pass
        self.conn, self.anahtar, self.path, self.sifreli, self._gecis_hedefi = conn, anahtar, hedef, True, None
        self._son_goruntu = veri2
        self._son_durum = self._durum()
        log.info("Veritabanı şifreli biçime çevrildi: %s", self.path)
        self._duz_sil(eski)
        return True

    @staticmethod
    def _duz_sil(eski: Path) -> None:
        for f in (eski, eski.with_name(eski.name + "-journal")):
            try:
                if f.exists():
                    sifreleme.sifirla_sil(f)
            except OSError:
                log.exception("Eski düz veritabanı silinemedi: %s (sonraki açılışta yeniden denenecek)", f)

    # ------------------------------------------------------------------ kalıcı yazma
    def _durum(self) -> tuple:
        """Değişiklik algılama: satır değişiklikleri, şema sürümü (CREATE/ALTER) ve user_version."""
        c = self.conn
        return (c.total_changes, c.execute("PRAGMA schema_version").fetchone()[0],
                c.execute("PRAGMA user_version").fetchone()[0])

    def _meta_oku(self, conn: sqlite3.Connection | None = None) -> dict:
        """Başlığa şifresiz yazılan giriş ekranı ayarları (verilerle aynı yazmada güncellenir)."""
        alanlar = sifreleme.META_ALANLARI
        try:
            return {r[0]: r[1] for r in (conn or self.conn).execute(
                f"SELECT anahtar, deger FROM ayarlar WHERE anahtar IN ({','.join('?' * len(alanlar))})", alanlar)}
        except sqlite3.Error:
            return {}

    def _kalici_yaz(self, zorla: bool = False) -> None:
        """Tamamlanmış değişiklikleri şifreleyip diske yazar. Yazılamazsa bellekteki veri diske en son yazılan
        hale döndürülür (işlem gerçekten geri alınır) ve KayitHatasi verilir."""
        if not self.sifreli or self.conn.in_transaction:
            return
        durum = self._durum()
        if not (zorla or self._kirli or durum != self._son_durum):
            return
        try:
            veri = self.conn.serialize()
            sifreleme.yaz(self.path, sifreleme.paketle(veri, self.anahtar, self._meta_oku()))
        except Exception as e:  # noqa: BLE001
            log.exception("Veritabanı diske yazılamadı")
            self._son_kayda_don()
            raise sifreleme.KayitHatasi(f"Değişiklik diske kaydedilemedi; son işlem geri alındı.\n({e})") from e
        self._son_goruntu = veri
        self._son_durum, self._kirli = durum, False

    def _son_kayda_don(self) -> None:
        """Bellekteki veriyi diske en son başarıyla yazılmış hale döndürür. Görüntü bellekte tutulduğu için
        diske erişilemese de geri alma gerçekten yapılır."""
        self._safe_rollback()
        self.conn.deserialize(self._son_goruntu)
        self._ayarla(self.conn)
        self._son_durum, self._kirli = self._durum(), False

    def _islem_sonu(self) -> None:
        islemler, self._kilit_bekleyen = self._kilit_bekleyen, None
        if not islemler:
            self._kalici_yaz()
            return
        eski = self.anahtar
        try:
            yeni = self._kilitleri_uygula(islemler)
        except Exception as e:  # noqa: BLE001
            log.exception("Kilitler hazırlanamadı")
            self._son_kayda_don()
            raise sifreleme.KayitHatasi(f"Değişiklik diske kaydedilemedi; son işlem geri alındı.\n({e})") from e
        self.anahtar = yeni
        try:
            self._kalici_yaz(zorla=True)
        except BaseException:
            self.anahtar = eski
            raise

    # ------------------------------------------------------------------ kilitler (kullanıcılar, kurtarma)
    def _kilitleri_uygula(self, islemler: list) -> sifreleme.Anahtar:
        """Bekleyen kilit değişikliklerini uygulanmış yeni anahtar (yazma başarılı olursa geçerli olur).
        Şifreler ve kurtarma anahtarı yalnızca işlem bitene kadar bellekte bekler; kilitler burada türetilir."""
        a = self.anahtar.kopya()
        if a.gecici and any(i[0] == "kullanici" for i in islemler):
            # ilk kullanıcı kilidi: geçici kilit bu yazmada kalkar. Geçici dönemde diske yazılan dosyalar veri
            # anahtarını açıkça taşıdığından veri anahtarı da yenilenir (eski kurtarma kilidi yeni anahtarı açmaz,
            # düşer; aynı işlemde verilen kurtarma anahtarı aşağıda yeni anahtara kilitlenir).
            a = sifreleme.yeni_anahtar()
        for islem in islemler:
            tur = islem[0]
            if tur == "kurtarma":
                a.kurtarma = sifreleme.kurtarma_sar(a, islem[1]) if islem[1] else None
            elif tur == "kullanici":
                a.kullanicilar[islem[1]] = sifreleme.sifre_sar(a, islem[2])
            elif tur == "kullanici_sil":
                a.kullanicilar.pop(islem[1], None)
            elif tur == "yedek":
                # geri yükleme: kullanıcı kilitleri yedekteki şifre özetleriyle uyumlu olanlardır (backup.py seçer)
                y, kurtarma_kalsin, kilitler = islem[1], islem[2], islem[3]
                if y is not None and y.kimlik != a.kimlik:
                    # başka kurulumun yedeği: canlı dosya o yedeğin veri anahtarıyla devam eder
                    a = sifreleme.Anahtar(y.dek, y.kimlik, {}, y.kurtarma if kurtarma_kalsin else None,
                                          y.gecici and not kilitler)
                a.kullanicilar = dict(kilitler)
        return a

    def _kilit_islemi(self, islem: tuple) -> None:
        """Kilit değişikliği: bir işlem (tx) içinde çağrılırsa işlem başarıyla bittiğinde verilerle birlikte aynı
        yazmada kaydedilir; işlem geri alınırsa uygulanmaz. Düz dosyayla çalışılırken kilit yoktur."""
        self._acik()
        if not self.sifreli:
            return
        if self._kilit_bekleyen is None:
            self._kilit_bekleyen = []
        self._kilit_bekleyen.append(islem)
        if not self._depth:
            self._islem_sonu()

    def _kilitler(self) -> sifreleme.Anahtar | None:
        return self._kilit_bilgisi if self.kilitli else self.anahtar

    def kilit_adlari(self) -> set[str]:
        """Kilidi olan (dosyayı açabilen) kullanıcılar, katlanmış adlarıyla; bekleyen değişiklikler dahil."""
        a = self._kilitler()
        adlar = set(a.kullanicilar) if a else set()
        for islem in self._kilit_bekleyen or []:
            if islem[0] == "kullanici":
                adlar.add(islem[1])
            elif islem[0] == "kullanici_sil":
                adlar.discard(islem[1])
            elif islem[0] == "yedek":
                adlar = set(islem[3])
        return adlar

    def kullanici_kilidi_var(self, kullanici_adi: str) -> bool:
        return fold((kullanici_adi or "").strip()) in self.kilit_adlari()

    def kullanici_kilidi(self, kullanici_adi: str, sifre: str) -> None:
        """Kullanıcının kilidini (yoksa) ekler, varsa yeni şifreyle değiştirir: bundan sonra yazılan dosyalarda eski
        şifrenin kilidi yoktur. Veri anahtarı değişmez; eski şifreyle açılan eski bir kopya anahtarı verir ve o
        anahtar sonraki dosyaları da açar."""
        self._kilit_islemi(("kullanici", fold((kullanici_adi or "").strip()), sifre or ""))

    def kullanici_kilidi_sil(self, kullanici_adi: str) -> None:
        self._kilit_islemi(("kullanici_sil", fold((kullanici_adi or "").strip())))

    def yedek_kilitleri(self, anahtar: sifreleme.Anahtar | None, kurtarma_kalsin: bool, kilitler: dict) -> None:
        """Geri yüklemede kullanıcı kilitlerini verilenlerle (yedekteki şifre özetleriyle uyumlu olanlar) değiştirir.
        anahtar: geri yüklenen şifreli yedeğin anahtarı (düz yedekte None); başka kurulumunsa canlı dosya o veri
        anahtarıyla devam eder. kurtarma_kalsin=False: başka kurulumun yedeğinin kurtarma kilidi alınmaz (sonraki
        yönetici girişinde yeni kurtarma anahtarı oluşturulur)."""
        self._kilit_islemi(("yedek", anahtar, kurtarma_kalsin, dict(kilitler)))

    def has_recovery(self) -> bool:
        """Veri anahtarı kurtarma anahtarına kilitli mi (tüm şifreler unutulursa ya da başka bilgisayarda açılabilir
        mi)? Düz dosyayla çalışılırken kilit gerekmez; True döner (şifreye geçişten sonra yeniden sorulur)."""
        if not self.sifreli:
            return True
        a = self._kilitler()
        var = bool(a and a.kurtarma)
        for islem in self._kilit_bekleyen or []:
            if islem[0] == "kurtarma":
                var = bool(islem[1])
        return var

    def set_recovery_key(self, key: str) -> None:
        """Veri anahtarını verilen (güncel) kurtarma anahtarına yeniden kilitler; eski anahtarın kilidi silinir.
        Bir işlem (tx) içinde çağrılırsa işlem başarıyla bittiğinde verilerle birlikte aynı yazmada kaydedilir."""
        self._kilit_islemi(("kurtarma", key))

    def kurtarma_kilidini_kaldir(self) -> None:
        self._kilit_islemi(("kurtarma", None))

    def goruntu(self) -> bytes:
        """Bellekteki veritabanının düz görüntüsü (yalnızca bellekte; yedek için şifrelenir)."""
        self._acik()
        return self.conn.serialize()

    def paketle(self, veri: bytes, kilitler: dict | None = None, meta: dict | None = None) -> bytes:
        """Görüntüyü canlı dosyayla aynı veri anahtarı, kullanıcı kilitleri ve kurtarma kilidiyle şifreler
        (yedekler için: yedek, alındığı andaki bir program şifresiyle ya da kurtarma anahtarıyla açılır).
        kilitler / meta: canlı dosyanınkiler yerine taşınacak kullanıcı kilitleri ve giriş ekranı ayarları (eski düz
        yedek çevrilirken: yalnızca o yedekteki şifre özetleriyle uyumlu kilitler)."""
        self._acik()
        anahtar = self.anahtar
        if kilitler is not None:
            anahtar = anahtar.kopya()
            anahtar.kullanicilar = dict(kilitler)
        return sifreleme.paketle(veri, anahtar, self._meta_oku() if meta is None else meta)

    def yukle(self, veri: bytes) -> None:
        """Veritabanının içeriğini verilen düz görüntüyle değiştirir (geri yükleme). Diske, sonraki işlemin
        (tx) sonunda yazılır."""
        self._acik()
        self._safe_rollback()
        if self.sifreli:
            self.conn.deserialize(veri)
            self._ayarla(self.conn)
            self._kirli = True
            return
        src = self._bellek(veri)
        try:
            src.backup(self.conn)
        finally:
            src.close()

    def close(self) -> None:
        if self.conn is None:
            return
        try:
            if self.sifreli:
                self._kalici_yaz()          # kaydedilmemiş değişiklik kaldıysa son bir deneme
        except Exception:  # noqa: BLE001
            log.exception("Kapanışta veritabanı yazılamadı")
        try:
            self.conn.close()
        except Exception:
            pass
        self.conn = None

    # ------------------------------------------------------------------ sorgu
    @contextmanager
    def tx(self):
        """İç içe kullanılabilen işlem bloğu. Hata olursa o bloğun yaptığı her şey geri alınır
        (iç bloklar SAVEPOINT kullanır). COMMIT başarısız olursa işlem geri alınır ve bağlantı
        kullanılabilir halde kalır. En dıştaki blok bitince değişiklikler şifrelenip diske yazılır;
        yazılamazsa işlem geri alınmış olur (KayitHatasi)."""
        self._acik()
        depth = self._depth
        bekleyen = len(self._kilit_bekleyen or [])
        if depth == 0:
            if self.conn.in_transaction:
                self._safe_rollback()
            self.conn.execute("BEGIN IMMEDIATE")
        else:
            self.conn.execute(f"SAVEPOINT sp{depth}")
        self._depth = depth + 1
        try:
            yield self
        except BaseException:
            self._depth = depth
            if depth == 0:
                self._kilit_bekleyen = None
                self._safe_rollback()
            else:
                if self._kilit_bekleyen:
                    del self._kilit_bekleyen[bekleyen:]      # iç bloğun kilit değişiklikleri de geri alınır
                try:
                    self.conn.execute(f"ROLLBACK TO sp{depth}")
                    self.conn.execute(f"RELEASE sp{depth}")
                except sqlite3.Error:
                    pass
            raise
        self._depth = depth
        if depth == 0:
            try:
                self.conn.execute("COMMIT")
            except BaseException:
                self._kilit_bekleyen = None
                self._safe_rollback()
                raise
            self._islem_sonu()
        else:
            self.conn.execute(f"RELEASE sp{depth}")

    def _safe_rollback(self) -> None:
        try:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def x(self, sql, params=()):
        self._acik()
        cur = self.conn.execute(sql, params)
        if self._depth == 0 and self.sifreli:
            self._kalici_yaz()          # işlem dışındaki tek komutluk yazma (autocommit) hemen diske
        return cur

    def q(self, sql, params=()):
        self._acik()
        return self.conn.execute(sql, params).fetchall()

    def q1(self, sql, params=()):
        self._acik()
        return self.conn.execute(sql, params).fetchone()

    def scalar(self, sql, params=(), default=None):
        self._acik()
        r = self.conn.execute(sql, params).fetchone()
        return default if r is None or r[0] is None else r[0]

    # ------------------------------------------------------------------ şema
    def user_version(self) -> int:
        return int(self.scalar("PRAGMA user_version", default=0))

    def migrate(self) -> None:
        v = self.user_version()
        if v > SCHEMA_VERSION:
            raise RuntimeError("Bu veritabanı programın daha yeni bir sürümüyle oluşturulmuş. "
                               "Lütfen programı güncelleyin.")
        if v < 1:
            with self.tx():
                for stmt in _SCHEMA_V1:
                    self.x(stmt)
                self._seed_defaults()
                self.x("PRAGMA user_version = 1")
        if v < 2:
            from . import personel_schema
            with self.tx():
                personel_schema.upgrade(self)
                self.x("PRAGMA user_version = 2")

    def _seed_defaults(self) -> None:
        for i, ad in enumerate(DEFAULT_GELIR, 1):
            self.x("INSERT INTO kategoriler(tur, ad, sira) VALUES('GELIR', ?, ?)", (ad, i))
        for i, ad in enumerate(DEFAULT_GIDER, 1):
            self.x("INSERT INTO kategoriler(tur, ad, sira) VALUES('GIDER', ?, ?)", (ad, i))
        for i, ad in enumerate(DEFAULT_ODEME, 1):
            self.x("INSERT INTO odeme_tipleri(ad, sira) VALUES(?, ?)", (ad, i))

    # ------------------------------------------------------------------ ayarlar
    def get_setting(self, key: str, default: str = "") -> str:
        if self.kilitli:
            # kilitliyken yalnızca giriş ekranının ayarları başlıktan okunur (sifreleme.META_ALANLARI)
            v = self._meta.get(key) if key in sifreleme.META_ALANLARI else None
            return v or default
        v = self.scalar("SELECT deger FROM ayarlar WHERE anahtar=?", (key,))
        return default if v is None else v

    def set_setting(self, key: str, value) -> None:
        self.x("INSERT INTO ayarlar(anahtar, deger) VALUES(?, ?) "
               "ON CONFLICT(anahtar) DO UPDATE SET deger=excluded.deger", (key, "" if value is None else str(value)))
