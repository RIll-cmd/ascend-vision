@echo off
setlocal
cd /d "%~dp0"
title Ascend Vision
if not exist ".venv\Scripts\python.exe" (
    echo Vision is not set up yet. Double-click Setup.bat first.
    pause
    exit /b 1
)
echo Starting Ascend Vision. Your browser will open the Vision window.
echo Close this window, or use "Quit Vision" in the tray, to stop.
echo.
".venv\Scripts\python.exe" -u main.py %*
if errorlevel 1 (
    echo.
    echo Vision stopped with an error. The message above says why.
    pause
)
