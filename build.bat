@echo off
setlocal

cd /d "%~dp0"

echo [1/3] Checking Python...
python --version
if errorlevel 1 goto :error

echo [2/3] Installing build dependencies...
python -m pip install -e ".[dev]"
if errorlevel 1 goto :error

echo [3/3] Building single-file executable...
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name WatcherNT ^
  --icon icon.png ^
  --add-data "icon.png;." ^
  --paths src ^
  src\watchernt\app.py
if errorlevel 1 goto :error

echo.
echo Build completed: %CD%\dist\WatcherNT.exe
exit /b 0

:error
echo.
echo Build failed. Check the output above.
exit /b 1
