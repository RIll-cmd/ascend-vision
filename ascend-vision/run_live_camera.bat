@echo off
setlocal
cd /d "%~dp0"
title Phone Watch - Live Camera (Focus Mode)
echo ========================================================
echo Starting Phone Watch Live Camera Preview in FOCUS Mode...
echo Press Space in the camera window to toggle mode
echo Press Ctrl+Alt+F anywhere to toggle mode
echo Press 'q' or 'Esc' in the camera window to exit
echo ========================================================
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment .venv not found in %CD%!
    echo Please ensure dependencies are installed.
    pause
    exit /b 1
)

echo.
echo Starting the selected Vision checkout. Initialization stages follow below.
echo If model or camera startup stalls, use the Desktop launcher's recovery chat.
echo.

".venv\Scripts\python.exe" -u main.py --focus --config "%~dp0config.yaml" %*
set "VISION_EXIT_CODE=%ERRORLEVEL%"

if errorlevel 1 (
    echo.
    echo Application exited with an error code.
)
pause
exit /b %VISION_EXIT_CODE%
