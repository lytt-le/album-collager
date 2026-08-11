@echo off
REM Run AlbumCollage from source (installs dependencies on first run).
setlocal

cd /d "%~dp0"

if not exist ".venv" (
    echo First run - creating virtual environment...
    python -m venv .venv || goto :fail
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip >nul
    python -m pip install -r requirements.txt || goto :fail
) else (
    call .venv\Scripts\activate.bat
)

python main.py
goto :eof

:fail
echo.
echo Setup failed. Make sure Python 3.10+ is installed and on your PATH.
pause
exit /b 1
