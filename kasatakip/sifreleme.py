"""Diskte şifreli saklama: canlı veritabanı ve yedekler için şifreli kap (.kdb) biçimi.

Kap = MAGIC + 4 bayt başlık uzunluğu + JSON başlık + AES-256-GCM şifreli SQLite görüntüsü.
Başlığın tamamı (MAGIC ve uzunluk dahil) AES-GCM'e ek doğrulama verisi (AAD) olarak verilir; başlıkta
yapılan her değişiklik de dosyayı açılamaz yapar.

Başlık alanları:
  surum         biçim sürümü (1)
  anahtar_id    veri anahtarının kimliği (anahtarın kendisi değil)
  nonce         bu yazmaya ait rastgele 12 bayt (her yazmada yenisi)
  kullanicilar  her program kullanıcısı için bir kilit: veri anahtarı, o kullanıcının şifresinden scrypt ile (yeni
                rastgele tuzla) türetilen anahtarla AES-GCM kilitli. Sözlük anahtarı: katlanmış kullanıcı adı (fold)
  kurtarma      isteğe bağlı: veri anahtarı, kurtarma anahtarından aynı yolla türetilen anahtarla kilitli
  gecici        yalnızca yepyeni (henüz kullanıcısı olmayan) veritabanında: veri anahtarının kendisi. İlk kurulum
                ekranı ve kullanıcı açmadan veritabanı oluşturan testler için dosya bu sürede okunabilir kalır.
                İlk kullanıcı kilidini ekleyen yazmada kaldırılır (veri anahtarı da yenilenir, db.py); kullanıcı
                kilidi olan bir dosyaya hiçbir zaman yazılmaz (paketle bunu zorlar).
  meta          giriş ekranının kilit açılmadan gerektirdiği ayarlar: son_kullanici, firma_adi, yazi_boyutu.
                Bunların ve kullanıcı adlarının dışında hiçbir veri şifresiz değildir.

Açılış: programdaki herhangi bir kullanıcının şifresi ya da kurtarma anahtarı dosyayı açar. Windows hesabına
bağlı koruma (DPAPI) kullanılmaz: Windows hesabına girebilen ya da diski eline geçiren biri program şifresini
bilmeden veriyi açamaz. Kilitler hiçbir zaman veritabanındaki şifre özetlerinden türetilmez (özetler 1.1.x düz
yedeklerinde açıkta durur). Önceki deneme sürümünün başlığındaki "dpapi" alanı okunmaz, yeniden yazılmaz.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .auth import normalize_key
from .errors import ServiceError

MAGIC = b"KASATAKIPKDB"
SURUM = 1
SQLITE_BASLIK = b"SQLite format 3\x00"
_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}  # ~32 MB bellek, ~0,1 sn: kurtarma anahtarını tahminle denemeyi yavaşlatır
_SCRYPT_SIFRE = {"n": 2 ** 16, "r": 8, "p": 1}  # ~64 MB, ~0,25 sn: kullanıcı şifreleri kısa olabilir, daha yavaş
META_ALANLARI = ("son_kullanici", "firma_adi", "yazi_boyutu")
GIRIS_HATASI = "Kullanıcı adı veya şifre hatalı."      # yanlış şifre / kullanıcı: başarısız girişle aynı metin


class SifrelemeHatasi(ServiceError):
    """Şifreli dosya açılamadı / yazılamadı."""


class AnahtarGerekli(SifrelemeHatasi):
    """Dosyayı açmak için bir kullanıcı şifresi ya da kurtarma anahtarı gerekir."""


class AnahtarHatali(SifrelemeHatasi):
    pass


class BozukDosya(SifrelemeHatasi):
    pass


class KayitHatasi(SifrelemeHatasi):
    """Değişiklik diske yazılamadı (işlem geri alındı)."""


@dataclass
class Anahtar:
    dek: bytes                                          # 256 bit veri anahtarı (yalnızca bellekte)
    kimlik: str
    kullanicilar: dict = field(default_factory=dict)    # katlanmış kullanıcı adı -> şifre kilidi
    kurtarma: dict | None = None
    gecici: bool = False                                # yepyeni veritabanı: henüz kullanıcı kilidi yok

    def kopya(self) -> "Anahtar":
        return Anahtar(self.dek, self.kimlik, dict(self.kullanicilar), self.kurtarma, self.gecici)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"), validate=True)


# ---------------------------------------------------------------- anahtarlar ve kilitler
def yeni_anahtar(gecici: bool = False) -> Anahtar:
    return Anahtar(AESGCM.generate_key(bit_length=256), secrets.token_hex(8), gecici=gecici)


def _kek(sir: bytes, tuz: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(sir, salt=tuz, n=n, r=r, p=p, maxmem=256 * 1024 * 1024, dklen=32)


def _sar(anahtar: Anahtar, sir: bytes, ayar: dict) -> dict:
    """Veri anahtarını sırdan (şifre / kurtarma anahtarı) türetilen anahtara kilitler (her seferinde yeni tuz ve
    nonce)."""
    tuz, nonce = os.urandom(16), os.urandom(12)
    sifreli = AESGCM(_kek(sir, tuz, **ayar)).encrypt(nonce, anahtar.dek, anahtar.kimlik.encode("ascii"))
    return {"kdf": "scrypt", **ayar, "tuz": _b64(tuz), "nonce": _b64(nonce), "sifreli": _b64(sifreli)}


def _kilit_ac(kilit: dict, sir: bytes, kimlik: str, hatali: str) -> bytes:
    try:
        if kilit.get("kdf") != "scrypt":
            raise ValueError(kilit.get("kdf"))
        kek = _kek(sir, _b64d(kilit["tuz"]), int(kilit["n"]), int(kilit["r"]), int(kilit["p"]))
        return AESGCM(kek).decrypt(_b64d(kilit["nonce"]), _b64d(kilit["sifreli"]), kimlik.encode("ascii"))
    except InvalidTag:
        raise AnahtarHatali(hatali) from None
    except (KeyError, ValueError, TypeError, AttributeError) as e:
        raise BozukDosya(f"Dosyanın kilidi okunamadı ({e}).") from e


def _kurtarma_siri(kurtarma_anahtari: str) -> bytes:
    key = normalize_key(kurtarma_anahtari)
    if not key:
        raise AnahtarHatali("Kurtarma anahtarını yazın.")
    return key.encode("utf-8")


def kurtarma_sar(anahtar: Anahtar, kurtarma_anahtari: str) -> dict:
    """Veri anahtarını kurtarma anahtarına kilitler (yazım biçimi önemsiz: tire, boşluk, küçük harf)."""
    return _sar(anahtar, _kurtarma_siri(kurtarma_anahtari), _SCRYPT)


def kurtarma_ac(kilit: dict, kurtarma_anahtari: str, kimlik: str) -> bytes:
    return _kilit_ac(kilit, _kurtarma_siri(kurtarma_anahtari), kimlik, "Kurtarma anahtarı hatalı.")


def sifre_sar(anahtar: Anahtar, sifre: str) -> dict:
    """Veri anahtarını bir kullanıcının şifresine kilitler (kurtarma kilidiyle aynı yol, daha yavaş türetme)."""
    return _sar(anahtar, (sifre or "").encode("utf-8"), _SCRYPT_SIFRE)


def sifre_ac(kilit: dict, sifre: str, kimlik: str) -> bytes:
    return _kilit_ac(kilit, (sifre or "").encode("utf-8"), kimlik, GIRIS_HATASI)


# ---------------------------------------------------------------- Windows dosya taşıma (ctypes)
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    _kernel32.MoveFileExW.restype = wintypes.BOOL


# ---------------------------------------------------------------- kap biçimi
def sifreli_mi(veri: bytes) -> bool:
    return veri[:len(MAGIC)] == MAGIC


def sqlite_mi(veri: bytes) -> bool:
    return veri[:len(SQLITE_BASLIK)] == SQLITE_BASLIK


def paketle(veri: bytes, anahtar: Anahtar, meta: dict | None = None) -> bytes:
    """Düz SQLite görüntüsünü şifreli kaba çevirir (her çağrıda yeni nonce). Geçici kilit yalnızca hiç kullanıcı
    kilidi yokken yazılır."""
    nonce = os.urandom(12)
    baslik = {"surum": SURUM, "anahtar_id": anahtar.kimlik, "nonce": _b64(nonce)}
    if anahtar.kullanicilar:
        baslik["kullanicilar"] = anahtar.kullanicilar
    elif anahtar.gecici:
        baslik["gecici"] = _b64(anahtar.dek)
    if anahtar.kurtarma:
        baslik["kurtarma"] = anahtar.kurtarma
    meta = {k: str(meta[k]) for k in META_ALANLARI if meta and meta.get(k)}
    if meta:
        baslik["meta"] = meta
    hb = json.dumps(baslik, separators=(",", ":")).encode("utf-8")
    on = MAGIC + struct.pack(">I", len(hb)) + hb
    return on + AESGCM(anahtar.dek).encrypt(nonce, veri, on)


def _baslik_coz(hb: bytes) -> dict:
    try:
        baslik = json.loads(hb.decode("utf-8"))
        if not isinstance(baslik, dict):
            raise ValueError("başlık")
    except (ValueError, UnicodeDecodeError) as e:
        raise BozukDosya(f"Dosya bozuk veya eksik ({e}).") from e
    if baslik.get("surum") != SURUM:
        raise BozukDosya("Bu dosya programın daha yeni bir sürümüyle oluşturulmuş. Lütfen programı güncelleyin.")
    return baslik


def baslik_oku(blob: bytes) -> tuple[dict, bytes, int]:
    """(başlık, AAD olarak kullanılan ön kısım, şifreli verinin başladığı konum)"""
    if not sifreli_mi(blob):
        raise BozukDosya("Bu dosya bir Kasa Takip şifreli dosyası değil.")
    try:
        n = struct.unpack(">I", blob[len(MAGIC):len(MAGIC) + 4])[0]
        bas = len(MAGIC) + 4 + n
        if n > 1_000_000 or len(blob) < bas + 16:
            raise ValueError("kısa dosya")
    except (struct.error, ValueError) as e:
        raise BozukDosya(f"Dosya bozuk veya eksik ({e}).") from e
    return _baslik_coz(blob[len(MAGIC) + 4:bas]), blob[:bas], bas


def baslik_dosyadan(path) -> dict:
    """Dosyanın yalnızca başlığını okur, şifreli veriye dokunmaz (kilitli veritabanının giriş ekranı bilgileri ve
    kilitleri için)."""
    with open(path, "rb") as f:
        on = f.read(len(MAGIC) + 4)
        if len(on) < len(MAGIC) + 4 or not sifreli_mi(on):
            raise BozukDosya("Bu dosya bir Kasa Takip şifreli dosyası değil.")
        n = struct.unpack(">I", on[len(MAGIC):])[0]
        hb = f.read(n) if n <= 1_000_000 else b""
        if len(hb) != n or len(f.read(16)) < 16:
            raise BozukDosya("Dosya bozuk veya eksik (kısa dosya).")
    return _baslik_coz(hb)


def kilitler(baslik: dict) -> Anahtar:
    """Başlıktaki kilitler, veri anahtarı olmadan (dek boş): hangi kullanıcıların kilidi var, kurtarma kilidi var mı?"""
    k, r = baslik.get("kullanicilar"), baslik.get("kurtarma")
    return Anahtar(b"", str(baslik.get("anahtar_id", "")), dict(k) if isinstance(k, dict) else {},
                   r if isinstance(r, dict) else None, "gecici" in baslik)


def coz(blob: bytes, anahtar: Anahtar | None = None, *, kullanici: str | None = None, sifre: str | None = None,
        kurtarma_anahtari: str | None = None) -> tuple[bytes, Anahtar]:
    """Kabı çözer: (düz SQLite görüntüsü, dosyanın anahtarı ve kilitleri). Sıra: verilen anahtar (aynı kimlikse)
    -> geçici kilit (yepyeni veritabanı) -> kullanıcı adı + şifre -> kurtarma anahtarı. kullanici katlanmış (fold)
    kullanıcı adıdır. Hiçbiri verilmemişse AnahtarGerekli, yanlışsa AnahtarHatali verilir."""
    baslik, on, bas = baslik_oku(blob)
    try:
        kimlik, nonce = str(baslik["anahtar_id"]), _b64d(baslik["nonce"])
    except (KeyError, ValueError) as e:
        raise BozukDosya(f"Dosya başlığı eksik ({e}).") from e
    bilgi = kilitler(baslik)
    if anahtar is not None and anahtar.kimlik == kimlik:
        dek = anahtar.dek
    elif bilgi.gecici:
        try:
            dek = _b64d(baslik["gecici"])
        except (ValueError, AttributeError) as e:
            raise BozukDosya(f"Dosya başlığı eksik ({e}).") from e
    elif kullanici is not None:
        kilit = bilgi.kullanicilar.get(kullanici)
        if not kullanici or not isinstance(kilit, dict):
            raise AnahtarHatali(GIRIS_HATASI)
        dek = sifre_ac(kilit, sifre or "", kimlik)
    elif kurtarma_anahtari is not None:
        if bilgi.kurtarma is None:
            raise AnahtarHatali("Bu dosyada kurtarma anahtarı kilidi yok; bir kullanıcı şifresiyle açılabilir.")
        dek = kurtarma_ac(bilgi.kurtarma, kurtarma_anahtari, kimlik)
    else:
        raise AnahtarGerekli("Bu dosya şifreli. Açmak için dosyadaki bir kullanıcının adı ve şifresi ya da "
                             "kurtarma anahtarı gerekir.")
    try:
        veri = AESGCM(dek).decrypt(nonce, blob[bas:], on)
    except InvalidTag:
        raise BozukDosya("Dosya bozuk veya değiştirilmiş; şifre doğrulaması başarısız.") from None
    except ValueError as e:
        raise BozukDosya(f"Dosya bozuk ({e}).") from e
    bilgi.dek = dek
    return veri, bilgi


# ---------------------------------------------------------------- dosya işlemleri
def _yerine_koy(tmp: Path, hedef: Path) -> None:
    """tmp dosyasını tek adımda hedefin yerine koyar. Windows'ta MOVEFILE_WRITE_THROUGH ile taşıma diske
    yazılmadan dönülmez; virüs tarayıcısı / dizin oluşturucu dosyayı kısa süre tutarsa birkaç kez denenir."""
    for deneme in range(10):
        try:
            if sys.platform == "win32":
                if not _kernel32.MoveFileExW(str(tmp), str(hedef), 0x1 | 0x8):   # REPLACE_EXISTING | WRITE_THROUGH
                    raise ctypes.WinError(ctypes.get_last_error())
            else:
                os.replace(tmp, hedef)
                fd = os.open(hedef.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            return
        except PermissionError:
            if deneme == 9:
                raise
            time.sleep(0.1)


def yaz(path, veri: bytes) -> None:
    """Güvenli yazma: aynı klasörde geçici dosyaya yazar, diske zorlar (fsync) ve tek adımda eskisinin yerine
    koyar. Yazma yarıda kesilirse (hata, elektrik kesintisi) eski dosya olduğu gibi kalır."""
    path = Path(path)
    tmp = path.with_name(path.name + ".yazim")
    try:
        with open(tmp, "wb") as f:
            f.write(veri)
            f.flush()
            os.fsync(f.fileno())
        _yerine_koy(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def oku(path, anahtar: Anahtar | None = None, **giris) -> tuple[bytes, Anahtar]:
    return coz(Path(path).read_bytes(), anahtar, **giris)


def dosya_oku(path) -> bytes:
    """Şifreli dosyanın tamamını okur (girişte). Virüs tarayıcısı / yedekleme ya da eşitleme programı dosyayı kısa süre
    paylaşımsız tutarsa yazmadaki gibi birkaç kez denenir; okunamazsa giriş pencerelerinin gösterdiği SifrelemeHatasi
    verilir."""
    for deneme in range(10):
        try:
            return Path(path).read_bytes()
        except PermissionError as e:
            hata = e
            if deneme < 9:
                time.sleep(0.1)
        except OSError as e:
            hata = e
            break
    raise SifrelemeHatasi("Veri dosyası okunamadı. Başka bir program (ör. virüs tarayıcısı, yedekleme ya da eşitleme "
                          f"programı) dosyayı kullanıyor olabilir; biraz bekleyip tekrar deneyin.\n({hata})")


def sifirla_sil(path) -> None:
    """Düz (şifresiz) dosyanın üzerine sıfır yazar, diske zorlar ve siler. SSD'de tam silme garantisi yoktur.
    Sıfırlamadan sonra dosya boşaltılır: silme yarıda kalırsa (dosya tutuluyor, elektrik kesildi) geride yalnızca
    boş ya da başı sıfırlanmış bir dosya kalır; açılışta bu durum tanınıp dosya silinir."""
    path = Path(path)
    kalan = path.stat().st_size
    blok = bytes(1 << 20)
    with open(path, "r+b") as f:
        while kalan > 0:
            n = min(kalan, len(blok))
            f.write(blok[:n])
            kalan -= n
        f.flush()
        os.fsync(f.fileno())
        f.truncate(0)
        f.flush()
        os.fsync(f.fileno())
    path.unlink()
