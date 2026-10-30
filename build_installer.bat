@echo off
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller pywebview pystray pillow certifi
rem публичная сборка: никаких ключей и подписок внутри — пользователь вставляет свои
if exist keys.txt (
  echo keys.txt must not be in the public build folder - remove it and run again
  goto fail
)
python -m PyInstaller --noconfirm --onefile --noconsole --uac-admin --add-data "ui.html;." --add-data "icon.png;." --add-data "icon.ico;." --icon icon.ico --hidden-import pystray._win32 --hidden-import certifi --exclude-module numpy --exclude-module tkinter --name HiddenPath zapret_manager.py
if errorlevel 1 goto fail

set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 not found. Install it: winget install JRSoftware.InnoSetup
  goto fail
)
"%ISCC%" installer.iss
if errorlevel 1 goto fail

echo.
echo Done: installer\HiddenPath-Setup.exe
pause
exit /b 0

:fail
echo.
echo BUILD FAILED - copy the error text above and send it
pause
exit /b 1
