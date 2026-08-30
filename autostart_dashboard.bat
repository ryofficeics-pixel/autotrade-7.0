@echo off
setlocal
cd /d "%~dp0"

set "DASHBOARD_URL=http://127.0.0.1:8767/"
set "STATE_URL=http://127.0.0.1:8767/api/state"
set "OPEN_BROWSER=1"
set "START_WATCHDOG=1"

if /i "%~1"=="--quiet" set "OPEN_BROWSER="
if /i "%~1"=="--recover" (
    set "OPEN_BROWSER="
    set "START_WATCHDOG="
)

if not exist ".venv\Scripts\python.exe" (
    echo Missing .venv. Run setup.bat first. 1>&2
    exit /b 1
)

call "%~dp0start_tradingview_debug.bat" || echo TradingView research is unavailable; continuing PAPER dashboard startup. 1>&2

call :healthy
if errorlevel 1 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~dp0.venv\Scripts\python.exe' -ArgumentList '-m','autotrade','dashboard','--config','config\paper.toml' -WorkingDirectory '%~dp0' -WindowStyle Hidden"

    for /l %%I in (1,1,30) do (
        call :healthy
        if not errorlevel 1 goto ready
        >nul 2>&1 ping 127.0.0.1 -n 2
    )

    echo Dashboard did not become healthy at %STATE_URL%. 1>&2
    exit /b 1
)

:ready
if defined START_WATCHDOG powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~dp0health_check.bat' -WorkingDirectory '%~dp0' -WindowStyle Hidden"
if defined OPEN_BROWSER start "" "%DASHBOARD_URL%"
exit /b 0

:healthy
powershell -NoProfile -Command "try { $s=Invoke-RestMethod -Uri '%STATE_URL%' -TimeoutSec 2; if ($s.mode -eq 'PAPER' -and $s.engine.status -eq 'SIMULATION_READY' -and $s.engine.risk_engine_enabled -eq $true -and $s.data.status -eq 'LIVE') { exit 0 } } catch {}; exit 1" >nul 2>&1
exit /b %errorlevel%
