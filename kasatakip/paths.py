"""Veri, yedek ve rapor klasörlerinin yerleri."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_NAME


def resource_path(*parts: str) -> Path:
    """Program dosyalarının (ikon, yazı tipi) yolu; exe içinden de çalışır."""
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else Path(__file__).resolve().parent.parent
    return root.joinpath(*parts)


def documents_dir() -> Path:
    """Windows'ta 'Belgeler' klasörü (OneDrive yönlendirmesi dahil)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            # CSIDL_PERSONAL = 5 (Belgeler)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return Path(buf.value)
        except Exception:
            pass
    d = Path.home() / "Documents"
    return d if d.exists() else Path.home()


class AppPaths:
    """
    Canlı veritabanı yerel uygulama klasöründe (senkronize edilmeyen) şifreli olarak durur:
        %LOCALAPPDATA%\\KasaTakip\\veri\\kasa.kdb
    (1.1.x'in düz kasa.db dosyası ilk açılışta şifreli dosyaya çevrilip silinir; bkz. db.Database)
    Yedekler ve raporlar kullanıcının kolayca bulacağı yerdedir:
        Belgeler\\KasaTakip\\Yedekler   ve   Belgeler\\KasaTakip\\Raporlar
    KASATAKIP_DATA ortam değişkeni verilirse hepsi o klasörün altına alınır.
    """

    def __init__(self, demo: bool = False):
        self.demo = demo
        override = os.environ.get("KASATAKIP_DATA")
        if override:
            root = Path(override)
            self.data_dir = root / "veri"
            self.docs_dir = root
        else:
            if sys.platform == "win32":
                local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
            else:
                local = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
            name = APP_NAME + ("Demo" if demo else "")
            self.data_dir = local / name / "veri"
            self.docs_dir = documents_dir() / (f"{APP_NAME} Demo" if demo else APP_NAME)
        self.db_path = self.data_dir / "kasa.kdb"
        self.log_dir = self.data_dir.parent / "log"
        self.backup_dir = self.docs_dir / "Yedekler"
        self.default_report_dir = self.docs_dir / "Raporlar"

    def ensure(self) -> None:
        for d in (self.data_dir, self.log_dir, self.backup_dir, self.default_report_dir):
            d.mkdir(parents=True, exist_ok=True)
