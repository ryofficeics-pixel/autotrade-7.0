@echo off
setlocal
cd /d "%~dp0"

set "ORIGIN=http://127.0.0.1:8767"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { Invoke-RestMethod -Method Post -Uri '%ORIGIN%/api/control/shutdown' -Headers @{ Origin = '%ORIGIN%' } -ContentType 'application/json' -Body '{}' -TimeoutSec 10 | Out-Null } catch { if ($_.Exception.Response.StatusCode.value__ -eq 404) { exit 2 }; exit 1 }"
if errorlevel 2 (
    echo Dashboard does not support graceful shutdown. 1>&2
    exit /b 1
)
if errorlevel 1 (
    echo Dashboard is not running or did not accept graceful shutdown.
    exit /b 0
)

for /l %%I in (1,1,20) do (
    powershell -NoProfile -Command "if (-not (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 8767 -State Listen -ErrorAction SilentlyContinue)) { exit 0 }; exit 1" >nul 2>&1
    if not errorlevel 1 (
        echo Autotrade PAPER dashboard stopped gracefully.
        exit /b 0
    )
    >nul 2>&1 ping 127.0.0.1 -n 2
)

echo Graceful shutdown timed out; no process was force-terminated. 1>&2
exit /b 1
