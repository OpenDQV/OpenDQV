#!/usr/bin/env bash
set -e

echo "Checking Python version..."
if ! python3 -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
    echo ""
    echo "ERROR: Python 3.11 or higher is required."
    echo "Your current version: $(python3 --version 2>&1)"
    echo ""
    echo "macOS:  brew install python@3.11"
    echo "Linux:  sudo apt install python3.11  (or use pyenv)"
    echo ""
    exit 1
fi

echo "Python version OK."
echo "Creating virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

echo "Installing dependencies..."
pip install -r requirements.txt

echo "Starting onboarding wizard..."
python -m cli onboard
