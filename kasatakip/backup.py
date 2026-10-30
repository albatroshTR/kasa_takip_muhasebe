"""Otomatik/manuel yedekleme, yedek saklama düzeni ve geri yükleme.

Yedek dosyası adı: kasa_YYYY-AA-GG_SS-DD-ss_<tür>.kdb
  Otomatik türler : acilis, cikis, gunsonu
  Kalıcı türler   : manuel, geriyukleme (geri yükleme öncesi otomatik alınır)
Saklama düzeni (otomatik yedekler):
  - son 7 günün tüm yedekleri,
  - daha eskiler için her günün son yedeği (ayarlanabilir gün sayısına kadar),
  - ayrıca her ayın son hali 'Aylık' klasöründe süresiz tutulur.

Yedekler canlı veritabanıyla aynı veri anahtarı, kullanıcı kilitleri ve kurtarma kilidiyle şifrelenir
(sifreleme.py): bu kurulumda doğrudan, başka bilgisayarda yedeğin alındığı anda geçerli olan bir program şifresiyle
ya da kurtarma anahtarıyla açılır. 1.1.x'in düz .db yedekleri de geri yüklenebilir; programın bildiği
klasörlerdeki düz yedekler bir kez şifreli .kdb'ye çevrilip silinir.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import auth, sifreleme
from .db import GECIS_OZETI, SCHEMA_VERSION, Database, tablo_sayilari
from .errors import ServiceError
from .util import fmt_dt, fold, now_iso

log = logging.getLogger("kasatakip")

AUTO_KINDS = {"acilis", "cikis", "gunsonu"}
KIND_LABELS = {
    "acilis": "Oturum açılışı (otomatik)", "cikis": "Program kapanışı (otomatik)",
    "gunsonu": "Gün kapanışı (otomatik)", "manuel": "Manuel", "geriyukleme": "Geri yükleme öncesi",
    "aylik": "Aylık arşiv",
}
_NAME_RE = re.compile(r"^kasa_(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})_([a-z]+)\.k?db$")
_MONTH_RE = re.compile(r"^kasa_(\d{4})-(\d{2})\.k?db$")
EXTRA_SUBDIR = "KasaTakip Yedekleri"
# Bu bilgisayara ait ayarlar geri yüklemede korunur (eski yedeğin ayarları bunları ezmez).
PRESERVED_SETTINGS = ("ek_yedek_klasoru", "yedek_saklama_gun", "rapor_klasoru", "yazi_boyutu",
                      "pencere_geometri", "kurtarma_hash", "son_yedek", "son_kullanici", GECIS_OZETI)
YONETICI_GEREKLI = ("Bu yedekteki yönetici şifresi bu bilgisayardaki şifreden farklı. Geri yüklemeden sonra veriler "
                    "yedekteki şifrelerle açılacağı için, yedeğin alındığı tarihte geçerli olan bir yönetici "
                    "kullanıcı adını ve şifresini yazın. O şifreyi hatırlamıyorsanız kurtarma anahtarını yazın; geri "
                    "yüklemeden sonra giriş ekranındaki \"Şifremi unuttum\" ile bu anahtarla yeni yönetici şifresi "
                    "belirlersiniz.")


@dataclass
class BackupResult:
    path: Path
    extra_ok: bool | None   # None: ek klasör tanımlı değil
    extra_error: str = ""
    # yalnızca geri yüklemenin döndürdüğü öncesi yedeğinde: şifreler yedektekilere döndü mü; yönetici kilidi olmadan
    # (kurtarma anahtarıyla, "Şifremi unuttum" ile girilecek) mi yazıldı
    sifreler_degisti: bool = False
    kurtarma_ile: bool = False


def _ac(path: Path, anahtar=None, **giris) -> tuple[sqlite3.Connection, sifreleme.Anahtar | None]:
    """Yedeği (şifreli .kdb ya da 1.1.x düz .db) bellekte açar; diske düz kopya yazılmaz. anahtar: canlı
    veritabanının anahtarı (aynı kurulumun yedekleri doğrudan açılır); giris: kullanici (kullanıcı adı) + sifre ya
    da kurtarma_anahtari. Açılamıyorsa sifreleme.AnahtarGerekli / AnahtarHatali verir.
    Döner: (bağlantı, yedeğin anahtarı ve kilitleri; düz yedekte None)"""
    if giris.get("kullanici") is not None:
        giris["kullanici"] = fold(giris["kullanici"].strip())     # dosya kilitleri katlanmış adla tutulur
    blob = Path(path).read_bytes()
    if sifreleme.sifreli_mi(blob):
        veri, yedek_anahtar = sifreleme.coz(blob, anahtar, **giris)
    elif sifreleme.sqlite_mi(blob):
        veri, yedek_anahtar = blob, None
    else:
        raise sifreleme.BozukDosya("Bu dosya bir Kasa Takip yedeği değil.")
    del blob
    con = sqlite3.connect(":memory:")
    con.deserialize(veri)
    con.execute("PRAGMA temp_store = MEMORY")
    return con, yedek_anahtar


def _dogrula(con: sqlite3.Connection) -> tuple[bool, str, dict]:
    res = con.execute("PRAGMA integrity_check").fetchone()[0]
    if res != "ok":
        return False, f"Dosya bozuk görünüyor ({res}).", {}
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"kullanicilar", "hareketler", "odeme_tipleri", "kategoriler", "ayarlar"} <= tables:
        return False, "Bu dosya bir Kasa Takip yedeği değil.", {}
    ver = con.execute("PRAGMA user_version").fetchone()[0]
    if ver > SCHEMA_VERSION:
        return False, "Bu yedek programın daha yeni bir sürümüne ait.", {}
    if ver < 1:
        return False, "Bu dosyada Kasa Takip sürüm bilgisi yok; güvenle geri yüklenemez.", {}
    info = {
        "hareket": con.execute("SELECT COUNT(*) FROM hareketler").fetchone()[0],
        "cari": con.execute("SELECT COUNT(*) FROM cariler").fetchone()[0],
        "son_tarih": con.execute("SELECT MAX(tarih) FROM hareketler").fetchone()[0],
        "kullanici": con.execute("SELECT COUNT(*) FROM kullanicilar").fetchone()[0],
    }
    return True, "", info


def _sifre_ozetleri(con) -> dict:
    """Katlanmış kullanıcı adı -> [(kullanıcı adı, şifre özeti, rol, aktif), ...]. Dosya kilitleri katlanmış adla
    tutulur; adları yalnızca Türkçe harflerle ayrılan kullanıcılar (1.1.x'te açılabiliyordu) aynı listededir."""
    try:
        rows = con.execute("SELECT kullanici_adi, sifre_hash, rol, aktif FROM kullanicilar ORDER BY id").fetchall()
    except sqlite3.Error:
        return {}
    out: dict = {}
    for r in rows:
        out.setdefault(fold(r[0]), []).append((r[0], r[1], r[2], r[3]))
    return out


def _ozet_haritasi(kisiler: dict) -> dict:
    """Kullanıcı adı (tam) -> şifre özeti."""
    return {k[0]: k[1] for liste in kisiler.values() for k in liste}


def _kullanici(con, ad: str):
    """Yazılan adla kullanıcı (kullanıcı adı, şifre özeti, rol, aktif): girişteki gibi tam adla aranır (sütun NOCASE:
    yalnızca İngilizce harflerde büyük/küçük ayrımı yok)."""
    return con.execute("SELECT kullanici_adi, sifre_hash, rol, aktif FROM kullanicilar WHERE kullanici_adi=?",
                       ((ad or "").strip(),)).fetchone()


def validate_backup(path, anahtar=None, **giris) -> tuple[bool, str, dict]:
    """Yedek dosyasını bellekte açıp bütünlüğünü ve içeriğini kontrol eder. anahtar: canlı veritabanının anahtarı
    (db.anahtar); giris: başka kurulumun yedeği için kullanici (kullanıcı adı) + sifre ya da kurtarma_anahtari.
    Şifreli yedek bunlarla açılamıyorsa bilgi sözlüğünde anahtar_gerekli=True döner."""
    path = Path(path)
    if not path.exists():
        return False, "Dosya bulunamadı.", {}
    try:
        con, _ = _ac(path, anahtar, **giris)
    except sifreleme.AnahtarGerekli as e:
        return False, str(e), {"anahtar_gerekli": True}
    except sifreleme.AnahtarHatali as e:
        return False, str(e), {"anahtar_gerekli": True, "anahtar_hatali": True}
    except (sifreleme.SifrelemeHatasi, OSError) as e:
        return False, str(e), {}
    try:
        ok, msg, info = _dogrula(con)
        if ok:
            with open(path, "rb") as f:
                info["sifreli"] = sifreleme.sifreli_mi(f.read(len(sifreleme.MAGIC)))
        return ok, msg, info
    except sqlite3.DatabaseError as e:
        return False, f"Dosya okunamadı: {e}", {}
    finally:
        con.close()


class BackupManager:
    def __init__(self, db, service, backup_dir: Path):
        self.db = db
        self.svc = service
        self.backup_dir = Path(backup_dir)

    # ------------------------------------------------------------ ayarlar
    def extra_dir(self) -> Path | None:
        v = self.svc.setting("ek_yedek_klasoru", "").strip()
        return Path(v) if v else None

    def keep_days(self) -> int:
        try:
            return max(14, int(self.svc.setting("yedek_saklama_gun", "90")))
        except ValueError:
            return 90

    def last_backup_time(self) -> str:
        return self.svc.setting("son_yedek", "")

    # ------------------------------------------------------------ yedek al
    def backup(self, kind: str = "manuel", rotate: bool = True) -> BackupResult:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        # şifreye geçiş başarısız olup düz dosyayla çalışılıyorsa yedekler de eskisi gibi düz (.db) alınır
        ext = ".kdb" if self.db.sifreli else ".db"
        now = dt.datetime.now()
        target = self.backup_dir / f"kasa_{now:%Y-%m-%d_%H-%M-%S}_{kind}{ext}"
        while target.exists():
            now += dt.timedelta(seconds=1)
            target = self.backup_dir / f"kasa_{now:%Y-%m-%d_%H-%M-%S}_{kind}{ext}"
        try:
            if self.db.conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ServiceError("Yedek doğrulanamadı; lütfen tekrar deneyin.")
            veri = self.db.goruntu()
            blob = self.db.paketle(veri) if self.db.sifreli else veri
            del veri
            sifreleme.yaz(target, blob)
            monthly_dir = self.backup_dir / "Aylık"
            monthly_dir.mkdir(exist_ok=True)
            sifreleme.yaz(monthly_dir / f"kasa_{now:%Y-%m}{ext}", blob)
        except (sqlite3.Error, OSError) as e:
            raise ServiceError(f"Yedek alınamadı: {e}") from e

        extra_ok, extra_err = None, ""
        ex = self.extra_dir()
        if ex:
            try:
                d = ex / EXTRA_SUBDIR
                (d / "Aylık").mkdir(parents=True, exist_ok=True)
                sifreleme.yaz(d / target.name, blob)
                sifreleme.yaz(d / "Aylık" / f"kasa_{now:%Y-%m}{ext}", blob)
                if rotate:
                    self.rotate(d)
                extra_ok = True
            except OSError as e:
                extra_ok, extra_err = False, str(e)
        del blob

        if rotate:
            self.rotate(self.backup_dir)
        self.svc.set_setting("son_yedek", now_iso())
        detay = f"{KIND_LABELS.get(kind, kind)}: {target.name}"
        if extra_ok is False:
            detay += " (ek klasöre kopyalanamadı)"
        self.svc.log("YEDEK", "Yedek", None, detay)
        try:
            self.convert_legacy()            # ek klasör ilk kez erişilebilir olduysa eski düz yedekleri de çevirir
        except Exception:  # noqa: BLE001
            log.exception("Eski düz yedekler şifrelenemedi")
        return BackupResult(target, extra_ok, extra_err)

    def backup_if_old(self, kind: str, hours: float) -> BackupResult | None:
        last = self.last_backup_time()
        if last:
            try:
                age = dt.datetime.now() - dt.datetime.fromisoformat(last)
                if age < dt.timedelta(hours=hours):
                    return None
            except ValueError:
                pass
        return self.backup(kind)

    # ------------------------------------------------------------ eski düz yedekleri şifreleme
    def _legacy_folders(self) -> list[Path]:
        folders = [self.backup_dir, self.backup_dir / "Aylık"]
        ex = self.extra_dir()
        if ex:
            folders += [ex / EXTRA_SUBDIR, ex / EXTRA_SUBDIR / "Aylık"]
        return folders

    def convert_legacy(self) -> tuple[int, int]:
        """Yedek klasörü, Aylık klasörü ve ek kopya klasöründeki (ve onun Aylık klasöründeki) 1.1.x düz
        kasa_*.db yedeklerini aynı adla .kdb'ye çevirir. Şifreli kopya doğrulanmadan düz dosya silinmez.
        Yalnızca canlı veri şifreli ve kurtarma kilidi varken çalışır (yedek başka bilgisayarda da açılsın).
        Erişilemeyen klasör (takılı olmayan USB) atlanır; sonraki yedeklemede yeniden denenir.
        Döner: (çevrilen, çevrilemeyen)"""
        if not (self.db.sifreli and self.db.has_recovery()):
            return 0, 0
        done = failed = 0
        for folder in self._legacy_folders():
            try:
                files = sorted(folder.glob("kasa_*.db")) if folder.is_dir() else []
            except OSError:
                continue
            for f in files:
                if not (_NAME_RE.match(f.name) or _MONTH_RE.match(f.name)):
                    continue
                try:
                    self._convert_file(f)
                    done += 1
                except Exception:  # noqa: BLE001
                    failed += 1
                    log.exception("Düz yedek şifrelenemedi (dosya olduğu gibi bırakıldı): %s", f)
        if done:
            log.info("%d eski düz yedek şifrelendi", done)
        return done, failed

    def _convert_file(self, f: Path) -> None:
        target = f.with_suffix(".kdb")
        st = f.stat()
        if target.exists():
            # aynı adlı şifreli yedek zaten var (aynı anın yedeği ya da aynı ayın daha yeni arşivi)
            ok, msg, _ = validate_backup(target, self.db.anahtar)
            if not ok:
                raise RuntimeError(f"aynı adlı şifreli yedek doğrulanamadı: {msg}")
            sifreleme.sifirla_sil(f)
            return
        veri = f.read_bytes()
        if not veri[:len(sifreleme.SQLITE_BASLIK)].strip(b"\x00"):
            sifreleme.sifirla_sil(f)              # boş / başı sıfırlanmış dosya: silme yarıda kalmış, veri yok
            return
        if not sifreleme.sqlite_mi(veri):
            raise RuntimeError("düz SQLite dosyası değil")
        src = sqlite3.connect(":memory:")
        try:
            src.deserialize(veri)
            res = src.execute("PRAGMA integrity_check").fetchone()[0]
            if res != "ok":
                raise RuntimeError(f"bütünlük denetimi: {res}")
            # şifreli kopya canlı kurtarma kilidiyle kilitlenir: içindeki kurtarma özeti de o anahtarın olmalı, yoksa
            # başka bilgisayarda "Şifremi unuttum" kilidi açan anahtarı özet denetiminde reddederdi
            ozet = self.db.get_setting("kurtarma_hash") or ""
            if not src.execute("UPDATE ayarlar SET deger=? WHERE anahtar='kurtarma_hash'", (ozet,)).rowcount:
                src.execute("INSERT INTO ayarlar(anahtar, deger) VALUES('kurtarma_hash', ?)", (ozet,))
            src.commit()
            veri = src.serialize()
            counts = tablo_sayilari(src)
            eski = _sifre_ozetleri(src)
            meta = self.db._meta_oku(src)
        finally:
            src.close()
        # yalnızca şifresi bu yedekten beri değişmemiş kullanıcıların kilitleri taşınır: değişmişse canlı kilit bugünkü
        # şifreyle, yedekteki özet eski şifreyle olurdu ve yedek kimseyi içeri almazdı
        canli = _ozet_haritasi(_sifre_ozetleri(self.db.conn))
        kilitler = {ad: k for ad, k in self.db.anahtar.kullanicilar.items()
                    if any(canli.get(x[0]) == x[1] for x in eski.get(ad, []))}
        sifreleme.yaz(target, self.db.paketle(veri, kilitler, meta))
        try:
            chk, _ = _ac(target, self.db.anahtar)    # diskten, canlı veritabanının anahtarıyla
            try:
                same = chk.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and tablo_sayilari(chk) == counts
            finally:
                chk.close()
            if not same:
                raise RuntimeError("şifreli kopya düz dosyayla aynı değil")
            os.utime(target, (st.st_atime, st.st_mtime))     # aylık arşivlerin tarihi dosya zamanından okunur
        except Exception:
            target.unlink(missing_ok=True)
            raise
        sifreleme.sifirla_sil(f)

    # ------------------------------------------------------------ saklama düzeni
    def rotate(self, folder: Path) -> int:
        today = dt.date.today()
        keep = self.keep_days()
        by_day: dict = {}
        for f in folder.glob("kasa_*"):
            m = _NAME_RE.match(f.name)
            if not m or m.group(5) not in AUTO_KINDS:
                continue
            by_day.setdefault(dt.date.fromisoformat(m.group(1)), []).append(f)
        removed = 0
        for day, files in by_day.items():
            age = (today - day).days
            if age < 7:
                continue
            files.sort(key=lambda p: p.name)
            victims = files if age > keep else files[:-1]
            for f in victims:
                try:
                    if f.suffix.lower() == ".db":
                        sifreleme.sifirla_sil(f)    # 1.1.x'ten kalan düz (şifresiz) yedek: üzerine sıfır yazılır
                    else:
                        f.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    # ------------------------------------------------------------ liste
    def list_backups(self) -> list[dict]:
        out = []
        if self.backup_dir.exists():
            for f in self.backup_dir.glob("kasa_*"):
                m = _NAME_RE.match(f.name)
                if not m:
                    continue
                when = dt.datetime.fromisoformat(f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4)}")
                out.append(self._item(f, when, m.group(5)))
            mdir = self.backup_dir / "Aylık"
            if mdir.exists():
                for f in mdir.glob("kasa_*"):
                    m = _MONTH_RE.match(f.name)
                    if not m:
                        continue
                    when = dt.datetime.fromtimestamp(f.stat().st_mtime)
                    out.append(self._item(f, when, "aylik"))
        out.sort(key=lambda d: d["zaman"], reverse=True)
        return out

    @staticmethod
    def _item(f: Path, when: dt.datetime, kind: str) -> dict:
        size = f.stat().st_size
        return {"path": str(f), "dosya": f.name, "zaman": when.strftime("%Y-%m-%d %H:%M:%S"),
                "zaman_gosterim": fmt_dt(when.strftime("%Y-%m-%d %H:%M:%S")), "tur": kind,
                "tur_adi": KIND_LABELS.get(kind, kind), "boyut": size,
                "boyut_gosterim": f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 1048576:.1f} MB"}

    # ------------------------------------------------------------ geri yükle
    def _is_live_db(self, path: Path) -> bool:
        try:
            return path.resolve() == self.db.path.resolve() or os.path.samefile(path, self.db.path)
        except OSError:
            return False

    def _gecerli_kilitler(self, yedek_anahtar, kisiler: dict) -> tuple[dict, bool]:
        """Geri yüklemeden sonra kalacak kullanıcı kilitleri: yalnızca yedekteki aktif kullanıcıların, yedekteki şifre
        özetleriyle uyumlu kilitleri. Yedeğin kendi kilitleri (alındığı anda şifrelerle uyumluydu) ve aynı veri
        anahtarında, şifresi o zamandan beri değişmemiş kullanıcıların canlı kilitleri kalır. Şifresi değişmiş
        kullanıcının kilidi kalmaz: o oturumda girerse yazdığı şifreyle yenisi eklenir, yoksa yöneticiye bildirilir.
        Kilitler katlanmış adla tutulur: bir kilit, o katlanmış adlı aktif kullanıcılardan biri (tam adıyla)
        uyuyorsa kalır. Döner: (kilitler, aktif bir yöneticinin uyumlu kilidi var mı)"""
        canli = self.db.anahtar
        kilitler, yonetici = {}, False
        if yedek_anahtar is None or yedek_anahtar.kimlik == canli.kimlik:
            ozet = _ozet_haritasi(_sifre_ozetleri(self.db.conn))
            for ad, k in canli.kullanicilar.items():
                uyan = [x for x in kisiler.get(ad, []) if x[3] and ozet.get(x[0]) == x[1]]
                if uyan:
                    kilitler[ad] = k
                    yonetici = yonetici or any(x[2] == "yonetici" for x in uyan)
        if yedek_anahtar is not None:
            for ad, k in yedek_anahtar.kullanicilar.items():
                aktif = [x for x in kisiler.get(ad, []) if x[3]]
                if aktif:
                    kilitler[ad] = k
                    yonetici = yonetici or any(x[2] == "yonetici" for x in aktif)
        return kilitler, yonetici

    def _kurtarma_ile(self, giris: dict, baska: bool, yedek_anahtar) -> bool:
        """Yönetici kilidi kalmayacak geri yükleme kurtarma anahtarıyla yapılabilir mi? Geri yüklenen dosyada bir
        kurtarma kilidi kalmalı: aynı kurulumda yazılan anahtar canlı kurtarma özetiyle doğrulanır (canlı kurtarma
        kilidi kalır); başka kurulumda anahtar yedeğin kendi kurtarma kilidini açmış olmalı (_ac)."""
        yazilan = giris.get("kurtarma_anahtari")
        if not yazilan:
            return False
        if baska:
            return yedek_anahtar.kurtarma is not None
        if not self.db.has_recovery():
            return False
        stored = self.svc.setting("kurtarma_hash")
        if not stored or not auth.verify_password(auth.normalize_key(yazilan), stored):
            raise sifreleme.AnahtarHatali("Kurtarma anahtarı hatalı.")
        return True

    def restore(self, path, yonetici: tuple[str, str] | None = None, **giris) -> BackupResult:
        """Seçilen yedeği canlı veritabanına yükler.
        1) Seçilen dosya bellekte çözülür, doğrulanır ve bellekteki geçici bir kopyada sürümü yükseltilir.
        2) Mevcut durumun yedeği alınır (saklama düzeni çalıştırılmaz; seçilen dosya silinmez).
        3) Kopya canlı veritabanına aktarılır; bu bilgisayara ait ayarlar korunur. Kullanıcılar ve şifreleri
           yedektekidir; dosyada yalnızca bu şifre özetleriyle uyumlu kullanıcı kilitleri kalır (_gecerli_kilitler).
           Aynı kurulumun yedeğinde veri anahtarı ve kurtarma kilidi değişmez. Başka kurulumun yedeğinde canlı
           dosya o yedeğin veri anahtarıyla devam eder; yedeğin kurtarma kilidi ve kurtarma anahtarı özeti birbirine
           ait olduğu için birlikte alınır (yedekte kurtarma kilidi yoksa özet de düşer; sonraki yönetici girişinde
           yeni anahtar oluşturulur). Düz (1.1.x) yedek de şifreli olarak saklanır.
        Başka kurulumun şifreli yedeği için giris verilmelidir: o yedekteki bir kullanıcının adı (kullanici) ve
        şifresi (sifre) ya da kurtarma_anahtari.
        Geri yüklemeden sonra hiçbir aktif yöneticinin uyumlu kilidi kalmayacaksa (ör. düz yedekteki yönetici şifresi
        bugünkünden farklı) sifreleme.AnahtarGerekli verilir: yonetici=(kullanıcı adı, şifre) ile yedekteki bir
        yöneticinin şifresi verilmeli; şifre yedekteki özetle doğrulanır ve kilidi aynı yazmada eklenir. Böylece
        program yeniden girişten önce kapansa da dosya o şifreyle açılır. Yöneticinin o zamanki şifresi bilinmiyorsa
        yerine kurtarma_anahtari verilebilir (geri yüklenen dosyada kurtarma kilidi kalacaksa, _kurtarma_ile): dosya
        yönetici kilidi olmadan yazılır, yönetici giriş ekranındaki "Şifremi unuttum" ile bu anahtarla girer
        (reset_with_recovery_key yönetici kilidini yeniden kurar).
        Döner: geri yükleme öncesi alınan yedek; sifreler_degisti / kurtarma_ile bilgileriyle."""
        self.svc.require_admin()
        path = Path(path)
        if self._is_live_db(path):
            raise ServiceError("Seçilen dosya programın şu an kullandığı veritabanının kendisi; "
                               "geri yükleme için Yedekler klasöründen bir dosya seçin.")
        try:
            con, yedek_anahtar = _ac(path, self.db.anahtar, **giris)
        except OSError as e:
            raise ServiceError(f"Yedek dosyası okunamadı: {e}") from e
        try:
            ok, msg, _ = _dogrula(con)
            if not ok:
                raise ServiceError(msg)
            r = con.execute("SELECT deger FROM ayarlar WHERE anahtar='kurtarma_hash'").fetchone()
            yedek_hash = r[0] if r else ""
            kisiler = _sifre_ozetleri(con)
            yon = _kullanici(con, yonetici[0]) if yonetici is not None else None
            # girişte gösterilecek ad: yedeği açan (ya da doğrulanan) kullanıcı, yoksa yedeğin ilk aktif yöneticisi
            acan = yon or (_kullanici(con, giris["kullanici"]) if giris.get("kullanici") else None)
            ilk = con.execute("SELECT kullanici_adi FROM kullanicilar WHERE rol='yonetici' AND aktif=1 "
                              "ORDER BY id").fetchone()
            veri = con.serialize()
        except sqlite3.DatabaseError as e:
            raise ServiceError(f"Dosya okunamadı: {e}") from e
        finally:
            con.close()
        baska = self.db.sifreli and yedek_anahtar is not None and yedek_anahtar.kimlik != self.db.anahtar.kimlik
        kilitler, kurtarma_ile = {}, False
        if self.db.sifreli:
            kilitler, yonetici_kilidi = self._gecerli_kilitler(yedek_anahtar, kisiler)
            if yonetici is not None:
                if not (yon and yon[2] == "yonetici" and yon[3] and auth.verify_password(yonetici[1] or "", yon[1])):
                    raise sifreleme.AnahtarHatali(sifreleme.GIRIS_HATASI)
            elif not yonetici_kilidi:
                kurtarma_ile = self._kurtarma_ile(giris, baska, yedek_anahtar)
                if not kurtarma_ile:
                    raise sifreleme.AnahtarGerekli(YONETICI_GEREKLI)
        yedek_ozet = {x[0]: x[1] for liste in kisiler.values() for x in liste if x[3]}
        canli_ozet = _ozet_haritasi(_sifre_ozetleri(self.db.conn))
        sifreler_degisti = baska or any(canli_ozet.get(ad) != h for ad, h in yedek_ozet.items())
        son = acan[0] if acan and acan[3] else (ilk[0] if ilk else "")
        try:
            staged = Database.bellekte(veri)        # bellekteki kopya üzerinde sürüm yükseltmeyi dener
            veri = staged.goruntu()
            staged.close()
        except (sqlite3.Error, RuntimeError) as e:
            raise ServiceError(f"Yedek dosyası hazırlanamadı; mevcut verilere dokunulmadı: {e}") from e
        keep = {r["anahtar"]: r["deger"] for r in self.db.q(
            f"SELECT * FROM ayarlar WHERE anahtar IN ({','.join('?' * len(PRESERVED_SETTINGS))})",
            PRESERVED_SETTINGS)}
        user = self.svc.user
        pre = self.backup("geriyukleme", rotate=False)
        try:
            self.db.yukle(veri)
            self.db.migrate()
            with self.db.tx():                      # içerik, korunan ayarlar ve kilitler tek yazmada diske geçer
                for k, v in keep.items():
                    self.db.set_setting(k, v)
                if son and (baska or keep.get("son_kullanici") not in yedek_ozet):
                    # giriş ekranında yedekte olmayan (ör. başka kurulumun) kullanıcı adı önerilmesin
                    self.db.set_setting("son_kullanici", son)
                if self.db.sifreli:
                    kalsin = True
                    if baska:
                        # başka kurulumun yedeği: yedeğin kurtarma kilidi ve özeti birbirine aittir, birlikte geçer
                        yazilan = giris.get("kurtarma_anahtari")
                        ozet = auth.hash_password(auth.normalize_key(yazilan)) if yazilan else yedek_hash
                        kalsin = yedek_anahtar.kurtarma is not None and bool(ozet)
                        self.db.set_setting("kurtarma_hash", ozet if kalsin else "")
                    self.db.yedek_kilitleri(yedek_anahtar, kalsin, kilitler)
                    if yonetici is not None:
                        self.db.kullanici_kilidi(yon[0], yonetici[1])
        except sqlite3.Error as e:
            if self.db.sifreli:
                self.db._son_kayda_don()         # bellekteki içerik diske en son yazılan (değişmemiş) hale döner
            raise ServiceError(f"Geri yükleme başarısız; mevcut veriler değişmedi: {e}") from e
        self.svc.user = user
        self.svc.log("GERI_YUKLEME", "Yedek", None,
                     f"{path.name} geri yüklendi (önceki durum yedeği: {pre.path.name})"
                     + ("; yönetici kilidi yok, kurtarma anahtarıyla girilecek" if kurtarma_ile else ""))
        pre.sifreler_degisti, pre.kurtarma_ile = sifreler_degisti, kurtarma_ile
        return pre
