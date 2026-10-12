@echo off
setlocal
cd /d "%~dp0"
title Ascend Vision - Setup
echo ========================================================
echo  Ascend Vision setup (one time, about 10 minutes)
echo ========================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating the Python environment...
    py -3.11 -m venv .venv
    if errorlevel 1 (
        echo.
        echo Python 3.11 was not found. Install it from https://www.python.org/downloads/release/python-3119/
        echo ^(tick "Add python.exe to PATH"^), then run Setup.bat again.
        pause
        exit /b 1
    )
) else (
    echo [1/4] Python environment already exists.
)

echo [2/4] Installing Vision ^(this is the long step^)...
".venv\Scripts\python.exe" -m pip install --quiet -r requirements-lock.txt
if errorlevel 1 goto :failed

echo [3/4] Downloading the camera models...
".venv\Scripts\python.exe" main.py --download-model
if errorlevel 1 goto :failed

echo [4/4] Preparing the Vision window...
if exist "fairy-ui\dist\index.html" (
    echo     Already built.
) else (
    where npm >nul 2>nul
    if errorlevel 1 (
        echo.
        echo Node.js is needed once to build the Vision window.
        echo Install the LTS version from https://nodejs.org then run Setup.bat again.
        pause
        exit /b 1
    )
    pushd fairy-ui
    call npm install --no-audit --no-fund
    if errorlevel 1 ( popd & goto :failed )
    call npm run build
    if errorlevel 1 ( popd & goto :failed )
    popd
)

".venv\Scripts\python.exe" setup_wizard.py
echo.
echo All done. Double-click "Start Vision.bat" to begin.
pause
exit /b 0

:failed
echo.
echo Setup stopped because a step failed. The message above says why.
echo Fix it and run Setup.bat again; finished steps are skipped.
pause
exit /b 1
