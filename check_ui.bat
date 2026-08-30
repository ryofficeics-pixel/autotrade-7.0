@echo off
setlocal
cd /d "%~dp0"

set "RUNTIME=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies"
set "NODE=%RUNTIME%\node\bin\node.exe"
set "NODE_PATH=%RUNTIME%\node\node_modules"
set "PLAYWRIGHT=%NODE_PATH%\playwright\cli.js"

if not exist "%NODE%" (
    echo Bundled Node.js runtime not found. 1>&2
    exit /b 1
)
if not exist "%PLAYWRIGHT%" (
    echo Bundled Playwright not found. 1>&2
    exit /b 1
)

set "PLAYWRIGHT_HTML_OPEN=never"
"%NODE%" "%PLAYWRIGHT%" test --config playwright.config.js

