@echo off
setlocal
set ROOT=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\start.ps1" %*
if errorlevel 1 (
  echo.
  echo [MENTOR-LITE] Startup failed. Please send a screenshot of the error above.
  echo [MENTOR-LITE] Common causes: missing bundled Python, blocked network during first install, or port 8765 already in use.
  echo.
  pause
)
