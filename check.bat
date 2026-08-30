@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Missing .venv. Run setup.bat first. 1>&2
    exit /b 1
)

".venv\Scripts\python.exe" -m unittest discover -s tests -v || exit /b 1
".venv\Scripts\python.exe" -m ruff check . || exit /b 1
".venv\Scripts\python.exe" -m mypy autotrade tests || exit /b 1

