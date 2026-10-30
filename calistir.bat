@echo off
setlocal
cd /d "%~dp0"
rem Kaynak koddan calistirir (EXE olusturmadan denemek icin). Ilk calistirmada gerekli paketleri kurar.
call tools\python_bul.bat
if errorlevel 1 goto hata
if not defined PY goto hata
call %PY% tools\ortam.py
if errorlevel 1 goto hata
start "" ".venv\Scripts\pythonw.exe" main.py %*
exit /b 0

:hata
echo.
echo Program baslatilamadi. Yukaridaki mesajlari kontrol edin.
pause
exit /b 1
