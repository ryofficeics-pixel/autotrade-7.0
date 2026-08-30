@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Missing .venv. Run setup.bat first. 1>&2
    exit /b 1
)

set "DURATION=3600"
if not "%~1"=="" set "DURATION=%~1"

".venv\Scripts\python.exe" -m autotrade capture --config config\paper.toml --duration %DURATION% --output data\gate-captures
