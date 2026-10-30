@echo off
setlocal
cd /d "%~dp0"
title Kasa Takip - sifirdan kurulum
echo ==========================================================
echo   Kasa Takip - SIFIRDAN KURULUM
echo ==========================================================
echo.
tasklist /FO CSV /NH 2>nul | findstr /I "KasaTakip" >nul
if not errorlevel 1 (
    echo Kasa Takip su anda acik. Once programi kapatin, sonra bu dosyayi tekrar calistirin.
    goto bitti
)
set "BELGELER="
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('MyDocuments')"`) do set "BELGELER=%%D"
if not defined BELGELER set "BELGELER=%USERPROFILE%\Documents"
echo Bu islem bu bilgisayardaki TUM Kasa Takip verilerini KALICI olarak siler:
echo   %LOCALAPPDATA%\KasaTakip        (veritabani ve log)
echo   %LOCALAPPDATA%\KasaTakipDemo    (deneme modu verisi)
echo   %BELGELER%\KasaTakip            (yedekler ve raporlar)
echo   %BELGELER%\KasaTakip Demo       (deneme modu yedekleri)
echo.
echo Program dosyasi (EXE) silinmez. Program sonraki acilista ilk kurulum ekraniyla baslar.
echo USB bellek / OneDrive gibi bir ek yedek klasoru tanimliysa oradaki "KasaTakip Yedekleri" klasorunu elle silin.
echo Silinen veriler geri getirilemez.
echo.
set "ONAY="
set /p ONAY=Silmek icin EVET yazip Enter'a basin: 
if /I not "%ONAY%"=="EVET" (
    echo Iptal edildi; hicbir sey silinmedi.
    goto bitti
)
call :sil "%LOCALAPPDATA%\KasaTakip"
call :sil "%LOCALAPPDATA%\KasaTakipDemo"
call :sil "%BELGELER%\KasaTakip"
call :sil "%BELGELER%\KasaTakip Demo"
echo.
echo Tamam. Simdi KasaTakip.exe'yi acin; ilk kurulum ekrani gelecek.
goto bitti

:sil
if exist "%~1" (
    rmdir /s /q "%~1"
    if exist "%~1" (echo SILINEMEDI: %~1) else (echo silindi: %~1)
) else (
    echo yok: %~1
)
exit /b 0

:bitti
echo.
pause
exit /b 0