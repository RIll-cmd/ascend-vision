@echo off
setlocal
if not exist "%~dp0ascend-vision\run_live_camera.bat" (
    echo [ERROR] Vision launcher missing: "%~dp0ascend-vision\run_live_camera.bat"
    echo Restore the selected Ascend Vision checkout before retrying.
    pause
    exit /b 1
)
call "%~dp0ascend-vision\run_live_camera.bat" %*
exit /b %errorlevel%
