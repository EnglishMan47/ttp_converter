@echo off
rem Manual install of the web version (Windows, no Docker). Run: setup.bat
rem Prerequisite checks only, no install:  setup.bat check
setlocal
set MIN_FREE_GB=5
set CHECKONLY=%~1

echo === Checking prerequisites ===

where git >nul 2>&1
if errorlevel 1 (
    echo WARNING: git is not installed ^(needed only to clone the repository^).
    echo          Install it from https://git-scm.com/download/win
)

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.10+ from python.org
    echo        with the "Add Python to PATH" option, then run again.
    if /i not "%CHECKONLY%"=="check" pause
    exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.10+ is required. Found:
    python --version
    if /i not "%CHECKONLY%"=="check" pause
    exit /b 1
)

python -c "import venv, ensurepip" >nul 2>&1
if errorlevel 1 (
    echo ERROR: the Python venv module is missing or broken.
    echo        Reinstall Python from python.org.
    if /i not "%CHECKONLY%"=="check" pause
    exit /b 1
)

for /f %%g in ('powershell -NoProfile -Command "[int][math]::Floor((Get-Item -LiteralPath .).PSDrive.Free/1GB)"') do set FREE_GB=%%g
if %FREE_GB% LSS 2 (
    echo ERROR: only %FREE_GB% GB free on this drive; at least 2 GB is required.
    if /i not "%CHECKONLY%"=="check" pause
    exit /b 1
)
if %FREE_GB% LSS %MIN_FREE_GB% (
    echo WARNING: only %FREE_GB% GB free; %MIN_FREE_GB%+ GB recommended.
)

where docker >nul 2>&1
if errorlevel 1 (
    echo INFO: Docker is not installed - only this manual install is available.
    echo       For the container deployment install Docker Desktop.
) else (
    echo INFO: Docker found - the container deployment is also available:
    echo       docker compose up -d --build
)

echo All checks passed.
if /i "%CHECKONLY%"=="check" exit /b 0

echo === Creating virtual environment ===
python -m venv venv
call venv\Scripts\activate.bat
echo === Installing packages ===
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo === Done ===
echo Run the web interface:
echo     venv\Scripts\activate.bat
echo     streamlit run app\web.py
echo.
echo NOTE: to enable the neural engine on Windows run:
echo     powershell -ExecutionPolicy Bypass -File scripts\setup_neural_windows.ps1
pause
