@echo off
setlocal
cd /d "%~dp0"
echo Installing RBloxTrex build dependencies...
py -3.11 -m pip install --upgrade pip
if errorlevel 1 goto failed
py -3.11 -m pip install "PySide6>=6.6,<7" "mss>=9.0" "numpy>=1.26" "opencv-python>=4.9" "pywin32>=306" "psutil>=5.9" pyinstaller
if errorlevel 1 goto failed
py -3.11 -c "import cv2, numpy, mss, PySide6, win32gui, psutil; print('OpenCV:', cv2.__version__); print('NumPy:', numpy.__version__)"
if errorlevel 1 goto failed
echo Building RBloxTrex.exe...
py -3.11 -m PyInstaller --noconfirm --clean --onefile --windowed --name RBloxTrex --icon RBloxTrex.ico --add-data "RBloxTrex.ico;." --collect-all cv2 --collect-all numpy --collect-all PySide6 --hidden-import win32gui --hidden-import win32process --hidden-import win32con --hidden-import psutil src\main.py
if errorlevel 1 goto failed
if not exist "dist\RBloxTrex.exe" goto failed
echo.
echo SUCCESS: dist\RBloxTrex.exe
pause
exit /b 0
:failed
echo.
echo BUILD FAILED. Copy the error above and share it for troubleshooting.
pause
exit /b 1
