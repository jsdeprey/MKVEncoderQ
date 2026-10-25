@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher "py" was not found.
  exit /b 1
)
py -m pip install --upgrade pyinstaller psutil tkinterdnd2
if errorlevel 1 exit /b 1
py -m PyInstaller --noconfirm --clean --onefile --windowed --collect-all tkinterdnd2 --name MKVEncoderQ MKVEncoderQ.py
if errorlevel 1 exit /b 1
py -m PyInstaller --noconfirm --clean --onefile --name MKVEncoderQ-CLI MKVEncoderQ_CLI.py
if errorlevel 1 exit /b 1
copy /Y USER_GUIDE.html dist\USER_GUIDE.html >nul
if errorlevel 1 exit /b 1
echo.
echo Build complete: dist\MKVEncoderQ.exe, dist\MKVEncoderQ-CLI.exe, and dist\USER_GUIDE.html
