@echo off
setlocal
cd /d "%~dp0"

set "TV_NODE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
set "TV_CLI=%CD%\vendor\tradingview-mcp\src\cli\index.js"
if not exist "%TV_NODE%" (
    echo Bundled Node.js runtime not found. Run setup_tradingview.bat. 1>&2
    exit /b 1
)
if not exist "%TV_CLI%" (
    echo TradingView MCP not found. Run setup_tradingview.bat. 1>&2
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$base = Join-Path $env:LOCALAPPDATA 'tradingview-mcp'; $listeners = @(Get-NetTCPConnection -LocalPort 9222 -State Listen -ErrorAction SilentlyContinue); $bad = @($listeners | Where-Object LocalAddress -NotIn '127.0.0.1','::1'); if ($bad.Count) { Write-Error 'CDP port 9222 is not loopback-only. Close TradingView and do not enable the integration.'; exit 1 }; if (-not $listeners.Count) { $exe = Get-ChildItem -LiteralPath $base -Filter TradingView.exe -File -Recurse -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName; if (-not $exe) { Write-Error 'Verified local TradingView Desktop copy not found. Reinstall the documented Desktop prerequisite.'; exit 1 }; Start-Process -FilePath $exe -ArgumentList '--remote-debugging-port=9222'; $deadline = (Get-Date).AddSeconds(30); do { Start-Sleep -Milliseconds 500; $listeners = @(Get-NetTCPConnection -LocalPort 9222 -State Listen -ErrorAction SilentlyContinue) } until ($listeners.Count -or (Get-Date) -ge $deadline) }; $bad = @($listeners | Where-Object LocalAddress -NotIn '127.0.0.1','::1'); if ($bad.Count -or -not $listeners.Count) { Write-Error 'TradingView CDP did not become available on loopback.'; exit 1 }" || exit /b 1
echo TradingView CDP is listening on loopback only.
