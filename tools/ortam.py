"""Kasa Takip sanal ortamını (.venv) hazırlar ve doğrular.

calistir.bat ve exe_olustur.bat, uygun bulunan Python ile bu betiği çalıştırır:
    py -3.13 tools\\ortam.py             program paketleri (PySide6, reportlab, openpyxl, cryptography)
    py -3.13 tools\\ortam.py --derleme   + PyInstaller (EXE oluşturmak için)

- .venv sağlamsa (uygun Python ve paketler açılabiliyor) hiçbir şey yapmaz.
- .venv uygun olmayan, bozuk ya da kaldırılmış bir Python ile kurulmuşsa siler ve bu Python ile yeniden kurar.
- Eksik paketleri kurar ve yeniden doğrular. Hata olursa nedenini ve çözümünü yazar; çıkış kodu 1 olur.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
VENV = KOK / ".venv"
KONTROL = KOK / "tools" / "python_kontrol.py"
GEREKSINIM = KOK / "requirements.txt"
VC_REDIST = "https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def moduller(derleme: bool) -> list[str]:
    return (["PySide6.QtWidgets", "reportlab", "openpyxl", "cryptography.hazmat.primitives.ciphers.aead"]
            + (["PyInstaller"] if derleme else []))


def _run(args, sessiz: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in args], cwd=KOK, stdin=subprocess.DEVNULL, text=True,
                          capture_output=sessiz)


def durum(derleme: bool) -> str:
    """'yok' | 'uygunsuz' (Python uygun değil / bozuk) | 'eksik' (paket eksik) | 'tamam'"""
    py = venv_python()
    if not py.exists():
        return "yok"
    try:
        if _run([py, KONTROL], sessiz=True).returncode != 0:
            return "uygunsuz"
        r = _run([py, "-c", "import " + ", ".join(moduller(derleme))], sessiz=True)
    except OSError:
        return "uygunsuz"
    return "tamam" if r.returncode == 0 else "eksik"


def maskele(text: str) -> str:
    """Adreslerdeki kullanıcı adı / şifreyi gizler (https://ad:sifre@sunucu -> https://***@sunucu)."""
    return re.sub(r"(://)[^/@\s]+@", r"\1***@", text)


def pip_tanilama(py: Path) -> None:
    print()
    print("Paketler kurulamadı. Olası nedenler:")
    print("  - İnternet bağlantısı yok ya da bir vekil sunucu (proxy) / güvenlik duvarı pip'i engelliyor.")
    print("  - pip bir şirket içi paket deposuna (index) yönlendirilmiş ve PySide6 orada yok.")
    print("  - PySide6'nın bu Python sürümü için henüz paketi yok (Python 3.13 64-bit önerilir).")
    try:
        r = _run([py, "-m", "pip", "config", "list"], sessiz=True)
        ayarlar = [s for s in (r.stdout or "").splitlines() if s.strip()]
    except OSError:
        ayarlar = []
    degiskenler = [k for k in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_CONFIG_FILE", "HTTPS_PROXY",
                               "HTTP_PROXY") if os.environ.get(k)]
    if ayarlar or degiskenler:
        print("Bu bilgisayardaki pip ayarları:")
        for s in ayarlar:
            print("   ", maskele(s))
        for k in degiskenler:
            print(f"    {k} = {maskele(os.environ[k])}")
    else:
        print("pip için özel ayar yok (paketler https://pypi.org adresinden indirilir).")


def _sil(yol: Path) -> bool:
    try:
        shutil.rmtree(yol)
        return True
    except OSError as e:
        print(f"HATA: {yol.name} klasörü silinemedi ({e}).")
        print("Kasa Takip kaynak koddan açıksa kapatıp tekrar deneyin ya da .venv klasörünü elle silin.")
        return False


def main(argv: list[str]) -> int:
    derleme = "--derleme" in argv
    d = durum(derleme)
    if d == "tamam":
        return 0
    print(f"Python: {sys.executable} ({sys.version.split()[0]})")
    if d == "uygunsuz":
        print("Mevcut sanal ortam (.venv) uygun olmayan ya da artık bulunmayan bir Python ile kurulmuş; "
              "yeniden kuruluyor...")
        if not _sil(VENV):
            return 1
    elif d == "yok" and VENV.exists():           # yarım kalmış kurulum
        if not _sil(VENV):
            return 1
    if not venv_python().exists():
        print("Sanal ortam oluşturuluyor (.venv)...")
        r = _run([sys.executable, "-m", "venv", VENV])
        if r.returncode != 0 or not venv_python().exists():
            print("HATA: Sanal ortam oluşturulamadı.")
            return 1
    py = venv_python()
    print("Gerekli paketler kuruluyor (ilk seferde birkaç dakika sürebilir)...")
    _run([py, "-m", "pip", "install", "--upgrade", "pip"], sessiz=True)      # başarısız olsa da devam
    r = _run([py, "-m", "pip", "install", "-r", GEREKSINIM] + (["pyinstaller>=6,<7"] if derleme else []))
    if r.returncode != 0:
        pip_tanilama(py)
        return 1
    r = _run([py, "-c", "import " + ", ".join(moduller(derleme))], sessiz=True)
    if r.returncode != 0:
        print("HATA: Paketler kuruldu ama açılamadı:")
        print("\n".join((r.stderr or "").strip().splitlines()[-6:]))
        if "DLL load failed" in (r.stderr or ""):
            print(f"Microsoft Visual C++ Redistributable (x64) kurup tekrar deneyin: {VC_REDIST}")
        return 1
    print("Sanal ortam hazır.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
