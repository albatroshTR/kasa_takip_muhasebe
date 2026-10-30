# -*- coding: utf-8 -*-
"""Bu Python yorumlayıcısı Kasa Takip için uygun mu?  Uygunsa 0, değilse 1 döner; tek satır açıklama yazar.

Koşullar: 64-bit, Python 3.11 – 3.15, standart (GIL'li) sürüm. PySide6 (Qt) Windows'ta yalnızca 64-bit Python
için paket yayımlar; 32-bit Python'da kurulum "No matching distribution found for PySide6" hatası verir.
Bilgisayardaki eski Python'larda da çalışabilmesi için yalnızca eski sözdizimi kullanılır ve çıktı ASCII'dir.
Kullanım:  py -3.13 tools\\python_kontrol.py
"""
import struct
import sys
import sysconfig

ENAZ, ENCOK = (3, 11), (3, 15)

surum = tuple(sys.version_info[:2])
bit = struct.calcsize("P") * 8
nedenler = []
if bit != 64:
    nedenler.append("%d-bit (64-bit gerekli)" % bit)
if surum < ENAZ or surum > ENCOK:
    nedenler.append("Python %d.%d desteklenmiyor (3.11 - 3.15 gerekli)" % surum)
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    nedenler.append("serbest is parcacikli (free-threaded) surum")
durum = "uygun" if not nedenler else "uygun degil: " + ", ".join(nedenler)
print("%s  (Python %s, %d-bit)  ->  %s" % (sys.executable, sys.version.split()[0], bit, durum))
sys.exit(1 if nedenler else 0)
