@echo off
setlocal
cd /d "%~dp0"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install --upgrade pyinstaller
python -c "import cv2, numpy; print('OpenCV:', cv2.__version__); print('NumPy:', numpy.__version__)" || goto :error
python -m PyInstaller --noconfirm --clean --onefile --windowed --name RBloxTrex --icon RBloxTrex.ico --add-data "RBloxTrex.ico;." --collect-all cv2 --collect-all numpy --hidden-import=cv2 --hidden-import=cv2.cv2 --hidden-import=numpy src/main.py
if errorlevel 1 goto :error
echo.
echo Build complete: dist\RBloxTrex.exe
pause
exit /b 0
:error
echo.
echo Build failed. Check the error messages above.
pause
exit /b 1
