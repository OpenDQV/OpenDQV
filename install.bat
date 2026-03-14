@echo off
setlocal

echo Checking Python version...
python -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" 2>nul
if errorlevel 1 (
    echo.
    echo ERROR: Python 3.11 or higher is required.
    echo Your current version:
    python --version
    echo.
    echo Please download Python 3.11+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo Then re-run this script.
    exit /b 1
)

echo Python version OK.
echo Creating virtual environment...
python -m venv .venv
call .venv\Scripts\activate

echo Installing dependencies...
pip install -r requirements.txt

echo Starting onboarding wizard...
python -m cli onboard
