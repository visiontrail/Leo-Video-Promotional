#!/bin/bash
set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

# Load nvm so that node/npm/npx are available
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"

echo "=== Video-Promotional Setup ==="

# 1. Python venv
echo "[1/4] Creating Python virtual environment..."
if [ ! -d .venv ]; then
    ~/.pyenv/versions/3.11.9/bin/python3.11 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "  Python dependencies installed."

# 2. Frontend
echo "[2/4] Installing frontend dependencies..."
cd frontend
npm install --silent
cd "$PROJECT_ROOT"
echo "  Frontend dependencies installed."

# 3. HyperFrame
echo "[3/4] Initializing HyperFrame project..."
if [ ! -d hyperframe ]; then
    npx hyperframes init hyperframe --example blank
fi
echo "  HyperFrame initialized."

# 4. Output directories
echo "[4/4] Creating output directories..."
mkdir -p outputs assets/lottie

echo ""
echo "=== Setup complete ==="
echo "Run ./scripts/start.sh to start the servers."
