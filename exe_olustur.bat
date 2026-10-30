@echo off
setlocal
cd /d "%~dp0"
title Kasa Takip - EXE olusturma
echo ==========================================================
echo   Kasa Takip - EXE ve kurulum dosyasi olusturma
echo ==========================================================
echo.

echo [1/5] Uygun Python araniyor...
call tools\python_bul.bat
if errorlevel 1 goto hata
if not defined PY goto hata
echo Kullanilan Python: %PY%

echo [2/5] Sanal ortam ve gerekli paketler hazirlaniyor...
call %PY% tools\ortam.py --derleme
if errorlevel 1 goto hata

echo [3/5] Testler calistiriliyor...
".venv\Scripts\python.exe" -m tests.test_core
if errorlevel 1 goto hata

echo [4/5] EXE olusturuluyor (birkac dakika surebilir)...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean KasaTakip.spec
if errorlevel 1 goto hata
echo.
echo EXE kendini test ediyor...
start "" /wait "dist\KasaTakip\KasaTakip.exe" --selftest
if errorlevel 1 (
    echo HATA: Olusturulan EXE kendi testini gecemedi. Ayrinti: selftest_sonuc.txt
    goto hata
)
echo EXE testi basarili.
echo EXE hazir:  dist\KasaTakip\KasaTakip.exe
echo.

echo [5/5] Kurulum dosyasi (Inno Setup) araniyor...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
rem Inno Setup 7 ve diger surumler ("Inno Setup ..." ile baslayan klasorler) ya da PATH uzerindeki ISCC.exe
if not defined ISCC for /f "delims=" %%D in ('dir /b /ad "%ProgramFiles%\Inno Setup*" 2^>nul') do if exist "%ProgramFiles%\%%D\ISCC.exe" set "ISCC=%ProgramFiles%\%%D\ISCC.exe"
if not defined ISCC for /f "delims=" %%D in ('dir /b /ad "%ProgramFiles(x86)%\Inno Setup*" 2^>nul') do if exist "%ProgramFiles(x86)%\%%D\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\%%D\ISCC.exe"
if not defined ISCC for /f "delims=" %%D in ('dir /b /ad "%LOCALAPPDATA%\Programs\Inno Setup*" 2^>nul') do if exist "%LOCALAPPDATA%\Programs\%%D\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\%%D\ISCC.exe"
if not defined ISCC for /f "delims=" %%I in ('where ISCC.exe 2^>nul') do set "ISCC=%%I"
if not defined ISCC goto noinno
echo Derleyici: "%ISCC%"
"%ISCC%" kurulum.iss
if errorlevel 1 goto hata
echo.
echo Kurulum dosyasi hazir: Kurulum klasorunde, KasaTakip_Kurulum_[surum].exe
echo Bu tek dosyayi diger bilgisayara kopyalayip calistirmaniz yeterli.
goto bitti

:noinno
echo Inno Setup bulunamadi; kurulum dosyasi olusturulmadi.
echo Istege bagli: https://jrsoftware.org/isdl.php adresinden Inno Setup (6.3 veya 7) kurup bu dosyayi tekrar calistirin.
echo Kurulum dosyasi olmadan da olur: dist\KasaTakip klasorunu ZIP'leyip diger bilgisayara tasiyin.

:bitti
echo.
echo Islem tamamlandi.
pause
exit /b 0

:hata
echo.
echo HATA: Islem tamamlanamadi. Yukaridaki mesajlari kontrol edin.
pause
exit /b 1
