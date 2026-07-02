@echo off
setlocal
set ROOT=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\start.ps1" %*
if errorlevel 1 (
  echo.
  echo [MENTOR-LITE] Startup failed. Please send a screenshot of the error above.
  echo [MENTOR-LITE] Bootstrap log: %ROOT%.runtime\logs\bootstrap.log
  echo [MENTOR-LITE] Common causes: blocked first-run downloads, antivirus/proxy restrictions, or port 8765 already in use.
  echo.
  pause
)
