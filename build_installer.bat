@echo off
cd /d "%~dp0"
python -m pip install --upgrade -r requirements-build.txt
rem exe со сведениями о программе; та же сборка, что на GitHub Actions
python tools\build_exe.py
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
