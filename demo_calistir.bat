@echo off
rem Deneme modu: gercek verilerden ayri bir klasorde ornek verilerle acilir.  Giris: admin / admin
cd /d "%~dp0"
if exist "dist\KasaTakip\KasaTakip.exe" (
    start "" "dist\KasaTakip\KasaTakip.exe" --demo
) else (
    call calistir.bat --demo
)
