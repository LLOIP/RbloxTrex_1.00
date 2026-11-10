@echo off
setlocal
cd /d "%~dp0"
echo Installing RBloxTrex build dependencies...
py -3.11 -m pip install --upgrade pip
if errorlevel 1 goto failed
py -3.11 -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto failed
echo Building RBloxTrex.exe...
py -3.11 -m PyInstaller --noconfirm --clean --onefile --windowed --name RBloxTrex --icon RBloxTrex.ico --add-data "RBloxTrex.ico;." src\main.py
if errorlevel 1 goto failed
echo.
echo SUCCESS: dist\RBloxTrex.exe
pause
exit /b 0
:failed
echo.
echo BUILD FAILED. Copy the error above and share it for troubleshooting.
pause
exit /b 1
