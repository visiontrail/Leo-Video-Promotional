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
# requirements.txt installs the vendored Claude Agent SDK (./claude-agent-sdk-python).
pip install -r requirements.txt -q
echo "  Python dependencies installed (incl. Claude Agent SDK)."

# The Agent SDK spawns the `claude` CLI. It ships bundled with the PyPI wheel,
# but the vendored source doesn't bundle it — so ensure one is on PATH.
if command -v claude >/dev/null 2>&1; then
    echo "  Claude CLI found: $(claude --version 2>/dev/null | head -1)"
else
    echo "  Claude CLI not found. Install it (needed only for AI_BACKEND=agent_sdk):"
    echo "    npm install -g @anthropic-ai/claude-code"
    echo "    # or: curl -fsSL https://claude.ai/install.sh | bash"
fi

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
echo "Run ./scripts/start.sh to start the app, then open http://localhost:8100"
