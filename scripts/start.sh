#!/bin/bash
set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

# Load nvm so that node/npm/npx are available
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"

trap 'kill 0' EXIT

echo "=== Starting Video-Promotional ==="

# Backend
echo "Starting backend on http://localhost:8000 ..."
source .venv/bin/activate
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000 &

# Frontend
echo "Starting frontend on http://localhost:5173 ..."
cd frontend && npm run dev &

wait
