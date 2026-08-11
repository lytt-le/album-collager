@echo off
REM Build AlbumCollage.exe (single file, no console window).
setlocal

cd /d "%~dp0"

if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv || goto :fail
)

call .venv\Scripts\activate.bat || goto :fail

echo Installing dependencies...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || goto :fail
python -m pip install pyinstaller || goto :fail

echo Building executable...
pyinstaller --noconfirm --clean ^
    --name AlbumCollage ^
    --onefile ^
    --windowed ^
    --collect-submodules PyQt6 ^
    --exclude-module PyQt6.QtWebEngineCore ^
    --exclude-module PyQt6.QtWebEngineWidgets ^
    --exclude-module PyQt6.QtBluetooth ^
    --exclude-module PyQt6.QtQuick3D ^
    --exclude-module PyQt6.Qt3DCore ^
    --exclude-module tkinter ^
    --exclude-module matplotlib ^
    main.py || goto :fail

echo.
echo Done. Executable: %CD%\dist\AlbumCollage.exe
goto :eof

:fail
echo.
echo BUILD FAILED. See the messages above.
exit /b 1
