@echo off
cd /d "%~dp0"
py -m pip install --upgrade pip
if errorlevel 1 goto failed
py -m pip uninstall -y opencv-python opencv-contrib-python opencv-python-headless
py -m pip install --no-cache-dir opencv-python-headless
if errorlevel 1 goto failed
py -m pip install --no-cache-dir PySide6 mss numpy pywin32 psutil pyinstaller
if errorlevel 1 goto failed
py -c "import cv2; print('OpenCV', cv2.__version__)"
if errorlevel 1 goto failed
py -m PyInstaller --noconfirm --clean --onefile --windowed --name RBloxTrex --icon RBloxTrex.ico --add-data "RBloxTrex_icon.png;." --collect-all cv2 --collect-submodules cv2 --collect-binaries cv2 --collect-all numpy --collect-all PySide6 --hidden-import=cv2 --hidden-import=cv2.cv2 --hidden-import=win32gui --hidden-import=win32process --hidden-import=win32con --hidden-import=psutil src/main.py
if errorlevel 1 goto failed
echo Build complete: dist\RBloxTrex.exe
pause
exit /b 0
:failed
echo Build failed. Read the error above.
pause
exit /b 1
