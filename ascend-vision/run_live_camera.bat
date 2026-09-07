@echo off
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
echo [1/3] Loading PyTorch, YOLOv8, MediaPipe and Style-Bert-VITS2...
echo [2/3] Accessing default camera (Index 0)...
echo [3/3] The preview window will appear shortly. Please wait...
echo.

".venv\Scripts\python.exe" main.py --focus

if errorlevel 1 (
    echo.
    echo Application exited with an error code.
)
pause
