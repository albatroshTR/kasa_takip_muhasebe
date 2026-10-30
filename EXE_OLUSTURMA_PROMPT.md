Bu klasör "Kasa Takip" adlı Windows masaüstü programının kaynak kodu (Python, PySide6, reportlab, openpyxl).
Görevin: bu kaynaktan dağıtılabilir EXE'yi ve Inno Setup varsa tek dosyalık kurulum programını oluşturmak, sonra
doğrulamak. Komutları VS Code terminalinde (PowerShell), proje kök klasöründe çalıştır. Adımları sırayla uygula;
bir adımın çıktısını kontrol etmeden sonrakine geçme.

## Dosyalar
- main.py (giriş noktası), kasatakip\ (program kodu), tests\ (testler), requirements.txt (paketler)
- KasaTakip.spec: PyInstaller ayarı (klasör halinde çıktı, konsolsuz, simge ve sürüm bilgisi dahil)
- version_info.txt: EXE sürüm bilgisi · kurulum.iss: Inno Setup kurulum betiği
- exe_olustur.bat: aynı işi yapan çift tıklamalık betik. Sonunda tuşa basılmasını beklediği (pause) için onu
  çalıştırma; adımları aşağıdaki gibi tek tek uygula.

## Kurallar
- Kaynak dosyaları değiştirme (kasatakip\, tests\, main.py, KasaTakip.spec, kurulum.iss, version_info.txt,
  requirements.txt). Bir hata kod değişikliği gerektiriyorsa dur; nedenini ve önerdiğin değişikliği yaz, onayımı bekle.
- Gerçek verilere dokunma: %LOCALAPPDATA%\KasaTakip ve Belgeler\KasaTakip (veritabanı, yedekler, raporlar).
  Programın kendi testi (--selftest) geçici bir klasör kullanır.
- Silip yeniden oluşturabileceğin klasörler yalnızca: .venv, build, dist, Kurulum.
- Sanal ortamı activate etme; her komutu .\.venv\Scripts\python.exe ile çalıştır.
- Bir şey kurmadan (Python, Inno Setup) ve çalışan bir programı kapatmadan önce bana sor. Antivirüsü veya SSL
  doğrulamasını kapatma.

## Adımlar
1. Ön kontrol
   - Test-Path .\KasaTakip.spec ve Test-Path .\main.py ile kök klasörde olduğunu doğrula.
   - kasatakip\__init__.py içindeki __version__ değerini oku. version_info.txt (filevers ve FileVersion) ile
     kurulum.iss (#define AppVersion) aynı sürümü göstermiyorsa raporla, düzeltme.
   - Klasör OneDrive altındaysa bunu not et (derlemede dosya kilidi hatalarına yol açabilir).
   - Get-Process KasaTakip -ErrorAction SilentlyContinue: program açıksa kapatmamı iste.
2. Python
   - py -0p ile kurulu sürümleri listele (py yoksa: python --version).
   - Python 64-bit ve 3.11 – 3.15 olmalı; 32-bit Python'a PySide6 kurulamaz. Tercih sırası 3.13, 3.12, 3.14, 3.11,
     3.15 (program 3.13 ile test edildi). Her adayı şu komutla denetle; satır "uygun" ile bitmeli:
     py -3.13 tools\python_kontrol.py
   - Uygun Python yoksa dur; 64-bit Python 3.13 kurmamı öner (python.org'da "Windows installer (64-bit)" ya da
     onayımla winget install -e --id Python.Python.3.13).
3. Sanal ortam ve paketler
   - Seçtiğin Python ile: py -3.13 tools\ortam.py --derleme
     Bu betik .venv'i gerekirse silip yeniden kurar, requirements.txt ve PyInstaller'ı yükler, paketlerin
     açıldığını doğrular; sonunda "Sanal ortam hazır." yazar (ortam zaten hazırsa hiçbir şey yazmadan biter).
   - İlk kurulum birkaç dakika sürebilir. Hata verirse betiğin yazdığı nedenleri ve pip ayarlarını bana göster;
     pip ayarlarını veya proxy'yi kendi başına değiştirme.
4. Testler
   - .\.venv\Scripts\python.exe -m tests.test_core
   - Son satır "TAMAM: … kontrol geçti" olmalı. Hata varsa EXE'ye geçme, hatayı raporla.
5. EXE
   - .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean KasaTakip.spec
   - Çıktı dist\KasaTakip\ klasörüdür; KasaTakip.exe bu klasördeki diğer dosyalarla birlikte çalışır.
   - "Erişim engellendi" / WinError 5 hatasında: program açık mı, OneDrive veya antivirüs dosyayı tutuyor mu bak;
     build ve dist klasörlerini silip bir kez daha dene.
6. EXE doğrulama
   - Programın kendi testini çalıştır (pencere açmadan biter; en fazla 3 dakika bekle):
     $p = Start-Process .\dist\KasaTakip\KasaTakip.exe -ArgumentList '--selftest' -WorkingDirectory $PWD -PassThru
     $null = $p.Handle
     if ($p.WaitForExit(180000)) { "Çıkış kodu: $($p.ExitCode)" } else { $p.Kill(); "ZAMAN AŞIMI" }
   - Çıkış kodu 0 olmalı. Get-Content .\selftest_sonuc.txt -Encoding UTF8 ile sonucu göster; ilk satır
     "SELFTEST OK" olmalı.
   - (Get-Item .\dist\KasaTakip\KasaTakip.exe).VersionInfo.FileVersion değeri __version__ ile aynı olmalı.
7. Kurulum dosyası (Inno Setup 6.3 veya 7)
   - ISCC.exe'yi ara:
     Get-ChildItem "$env:ProgramFiles\Inno Setup*\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup*\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup*\ISCC.exe" -ErrorAction SilentlyContinue
     (bulunamazsa Get-Command ISCC.exe -ErrorAction SilentlyContinue)
   - Bulursan en yeni sürümle derle: & "<ISCC.exe yolu>" kurulum.iss
     Çıktı: Kurulum\KasaTakip_Kurulum_<sürüm>.exe
   - Bulamazsan Inno Setup kurmak isteyip istemediğimi sor. Onaylarsam winget install -e --id JRSoftware.InnoSetup.7
     ile kur ve bu adımı tekrarla. Kurulum dosyası olmadan da dist\KasaTakip klasörü ZIP'lenip taşınabilir.
   - "x64compatible" ile ilgili bir hata Inno Setup'ın 6.3'ten eski olduğunu gösterir; güncellemeyi öner.
8. Rapor (Türkçe, kısa)
   - Kullanılan Python ve paket sürümleri (PySide6, reportlab, openpyxl, pyinstaller)
   - Test sonucu satırı
   - EXE yolu, FileVersion ve dist\KasaTakip klasörünün boyutu (MB)
   - Selftest çıkış kodu ve selftest_sonuc.txt özeti
   - Kurulum dosyasının yolu ve boyutu (ya da neden oluşturulmadığı)
   - Kaynak dosyalarda değişiklik yapmadığının teyidi
   - Hatırlatma: program imzasız olduğu için ilk açılışta Windows SmartScreen uyarısı çıkabilir
     ("Ek bilgi → Yine de çalıştır").
