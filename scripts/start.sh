#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/logs}"
RUN_ID="$(date '+%Y%m%d-%H%M%S')"
LOG_FILE="$LOG_DIR/start-$RUN_ID.log"
LATEST_LOG="$LOG_DIR/start-latest.log"
mkdir -p "$LOG_DIR"
ln -sf "$LOG_FILE" "$LATEST_LOG"

exec > >(while IFS= read -r line; do
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$line"
done | tee -a "$LOG_FILE") 2>&1

# Load nvm so that node/npm/npx are available
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"

if [ -z "${YTDLP_JS_RUNTIME:-}" ]; then
    if command -v node >/dev/null 2>&1; then
        export YTDLP_JS_RUNTIME="node:$(command -v node)"
    elif [ -x "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node" ]; then
        export YTDLP_JS_RUNTIME="node:$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
    fi
fi

if [ -z "${YTDLP_COOKIES:-}" ] && [ -z "${YTDLP_COOKIES_FROM_BROWSER:-}" ]; then
    export YTDLP_COOKIES_FROM_BROWSER="chrome"
fi

pids=()

cleanup() {
    for pid in "${pids[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait "${pids[@]}" 2>/dev/null || true
}

trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

echo "=== Starting Video-Promotional ==="
echo "Run log: $LOG_FILE"
echo "Latest log: $LATEST_LOG"
if [ -n "${YTDLP_JS_RUNTIME:-}" ]; then
    echo "yt-dlp JS runtime: ${YTDLP_JS_RUNTIME%%:*}"
fi
if [ -n "${YTDLP_COOKIES:-}" ]; then
    echo "yt-dlp cookies: file"
elif [ -n "${YTDLP_COOKIES_FROM_BROWSER:-}" ]; then
    echo "yt-dlp cookies: browser (${YTDLP_COOKIES_FROM_BROWSER%%:*})"
fi

# Backend
echo "Starting backend on http://localhost:8000 ..."
(
    source .venv/bin/activate
    # Watch only source code for reload. Without --reload-dir, uvicorn watches the
    # whole project root and reloads (and logs "1 change detected") every time the
    # worker writes to outputs/ or tasks.db — restarting the server mid-task.
    uvicorn backend.main:app --reload --reload-dir backend --host 0.0.0.0 --port 8000
) &
pids+=("$!")

# Frontend
echo "Starting frontend on http://localhost:5173 ..."
(
    cd frontend
    npm run dev
) &
pids+=("$!")

wait "${pids[@]}"
