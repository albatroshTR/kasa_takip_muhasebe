@echo off
rem Kasa Takip icin uygun Python'u bulur ve PY degiskenine yazar. Kullanim:  call tools\python_bul.bat
rem Uygun Python: 64-bit, 3.11 - 3.15 (tools\python_kontrol.py denetler). Once test edilen 3.13 denenir.
rem Cagiran betik proje klasorunde calismalidir. Yorumlayicilar "call" ile cagrilir: .bat/.cmd kisayollari
rem (or. pyenv-win) da calissin ve kontrol bu betige geri donsun.
set "PY="
for %%C in ("py -3.13" "py -3.12" "py -3.14" "py -3.11" "py -3.15" "py -3" "python") do if not defined PY call :dene %%C
if defined PY exit /b 0

echo.
echo Uygun Python bulunamadi.
call py -0p >nul 2>nul
if not errorlevel 1 (
    echo Bilgisayarda kayitli Python surumleri:
    call py -0p
)
echo Denenenler:
for %%C in ("py -3" "python") do call :goster %%C
echo.
echo Kasa Takip 64-bit Python 3.11 - 3.15 ister (onerilen 3.13). 32-bit Python'a PySide6 (Qt) kurulamaz.
echo Cozum: https://www.python.org/downloads/windows/ sayfasindan Python 3.13 icin
echo   "Windows installer (64-bit)" dosyasini indirip kurun; kurulumda "Add python.exe to PATH" kutusunu
echo   isaretleyin. Ya da komut satirinda:  winget install -e --id Python.Python.3.13
echo Sonra bu dosyayi tekrar calistirin.
exit /b 1

:dene
call %~1 tools\python_kontrol.py <nul >nul 2>nul
if not errorlevel 1 set "PY=%~1"
exit /b 0

:goster
call %~1 tools\python_kontrol.py <nul 2>nul >"%TEMP%\kasatakip_py.txt"
set "KT_BOYUT=0"
for %%F in ("%TEMP%\kasatakip_py.txt") do set "KT_BOYUT=%%~zF"
if "%KT_BOYUT%"=="0" echo   %~1: bulunamadi
if not "%KT_BOYUT%"=="0" type "%TEMP%\kasatakip_py.txt"
del "%TEMP%\kasatakip_py.txt" >nul 2>nul
set "KT_BOYUT="
exit /b 0
