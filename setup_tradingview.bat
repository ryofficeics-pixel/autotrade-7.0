@echo off
setlocal
cd /d "%~dp0"

set "TV_PIN=c05b8f5755ed8e64ea242de88ddbf46aa24d56a4"
set "TV_VENDOR=%CD%\vendor\tradingview-mcp"
set "TV_DEPENDENCIES=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies"
set "TV_NODE_DIR=%TV_DEPENDENCIES%\node\bin"
set "TV_PNPM_DIR=%TV_DEPENDENCIES%\bin\fallback"
set "TV_PNPM=%TV_PNPM_DIR%\pnpm.cmd"

if not exist "%TV_VENDOR%\.git" (
    git clone --no-checkout https://github.com/tradesdontlie/tradingview-mcp.git "%TV_VENDOR%" || exit /b 1
    git -C "%TV_VENDOR%" checkout --detach "%TV_PIN%" || exit /b 1
)

for /f "usebackq delims=" %%H in (`git -C "%TV_VENDOR%" rev-parse HEAD`) do set "TV_ACTUAL=%%H"
if /i not "%TV_ACTUAL%"=="%TV_PIN%" (
    echo TradingView MCP checkout does not match the tested pin. 1>&2
    echo Expected %TV_PIN%, found %TV_ACTUAL%. 1>&2
    exit /b 1
)
if not exist "%TV_NODE_DIR%\node.exe" (
    echo Bundled Node.js runtime not found. 1>&2
    exit /b 1
)
if not exist "%TV_PNPM%" (
    echo Bundled pnpm launcher not found. 1>&2
    exit /b 1
)

set "PATH=%TV_NODE_DIR%;%TV_PNPM_DIR%;%PATH%"
pushd "%TV_VENDOR%"
call "%TV_PNPM%" dlx npm@11.6.1 ci --ignore-scripts || (popd & exit /b 1)
"%TV_NODE_DIR%\node.exe" src\cli\index.js --help >nul || (popd & exit /b 1)
popd

echo TradingView MCP %TV_PIN% is installed but remains disabled.
