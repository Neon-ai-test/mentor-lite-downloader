@echo off
setlocal
set ROOT=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\stop.ps1" %*
