@echo off
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller pywebview pystray pillow
rem keys.txt (ключи vless://, по одному на строку) вшивается в exe как ключи по умолчанию
set "KEYS="
if exist keys.txt set "KEYS=--add-data keys.txt;."
python -m PyInstaller --noconfirm --onefile --noconsole --uac-admin --add-data "ui.html;." --add-data "icon.png;." --add-data "icon.ico;." --icon icon.ico --hidden-import pystray._win32 --exclude-module numpy --exclude-module tkinter %KEYS% --name NapsterVPN zapret_manager.py
if errorlevel 1 goto fail

set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 not found. Install it: winget install JRSoftware.InnoSetup
  goto fail
)
"%ISCC%" installer.iss
if errorlevel 1 goto fail
if not exist "%USERPROFILE%\Documents\vpn" mkdir "%USERPROFILE%\Documents\vpn"
copy /Y "installer\NapsterVPN-Setup.exe" "%USERPROFILE%\Documents\vpn\" >nul
echo Saved to %USERPROFILE%\Documents\vpn

echo.
echo Done: installer\NapsterVPN-Setup.exe
pause
exit /b 0

:fail
echo.
echo BUILD FAILED - copy the error text above and send it
pause
exit /b 1
