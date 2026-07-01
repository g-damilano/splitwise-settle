@echo off
setlocal

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on PATH.
    echo Install Python 3.10 or newer, then run this file again.
    exit /b 1
)

echo Installing or updating build dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 goto fail

echo.
echo Rebuilding Splitwise Settle executable...
python -m PyInstaller --noconfirm --clean splitwise_settle.spec
if errorlevel 1 goto fail

echo.
echo Build complete:
echo %CD%\dist\splitwise_settle.exe
exit /b 0

:fail
echo.
echo Build failed.
exit /b 1
