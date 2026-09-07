@echo off
cd /d "%~dp0"
title Phone Watch - Habit Dashboard
echo ========================================================
echo Starting Phone Watch Habit Dashboard at http://127.0.0.1:8765
echo Close this window to stop the dashboard.
echo ========================================================
.\.venv\Scripts\python.exe dashboard.py
pause
