@echo off
setlocal
cd /d "%~dp0"

if /i "%~1"=="--once" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0health_check.ps1" -Once
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0health_check.ps1"
)
exit /b %errorlevel%
