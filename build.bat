@echo off
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller
python -m PyInstaller --onefile --noconsole --uac-admin --name ZapretManager zapret_manager.py
if errorlevel 1 (
  echo.
  echo BUILD FAILED - copy the error text above and send it
) else (
  echo.
  echo Done: dist\ZapretManager.exe
)
pause
