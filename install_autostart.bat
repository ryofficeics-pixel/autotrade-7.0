@echo off
setlocal

set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTUP_FILE=%STARTUP_DIR%\Autotrade 7 Dashboard.bat"

if not exist "%STARTUP_DIR%" (
    echo Windows Startup folder was not found. 1>&2
    exit /b 1
)

>"%STARTUP_FILE%" (
    echo @echo off
    echo call "%~dp0autostart_dashboard.bat"
)

echo Installed "%STARTUP_FILE%"
exit /b 0
