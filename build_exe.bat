@echo off
setlocal
cd /d "%~dp0"
py -3.11 -m pip install --upgrade pip
if errorlevel 1 exit /b 1
py -3.11 -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
py -3.11 -m PyInstaller --noconfirm --clean --onefile --windowed --name ResolShaders --icon ResolShaders.ico --add-data "ResolShaders.ico;." --collect-all cv2 --collect-all numpy --collect-all PySide6 --collect-all windows_capture --hidden-import win32gui --hidden-import win32process --hidden-import win32con --hidden-import psutil src\main.py
if errorlevel 1 exit /b 1
echo Built dist\ResolShaders.exe
endlocal
