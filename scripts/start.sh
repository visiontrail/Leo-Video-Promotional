#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

# Single-port production service: the backend serves the API and the built
# frontend on one port. Override with PORT/HOST if needed.
PORT="${PORT:-8100}"
HOST="${HOST:-0.0.0.0}"

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

# Build the frontend so the backend serves the current UI. Set
# SKIP_FRONTEND_BUILD=1 to reuse the existing frontend/dist (faster restarts).
if [ "${SKIP_FRONTEND_BUILD:-0}" = "1" ]; then
    echo "Skipping frontend build (SKIP_FRONTEND_BUILD=1); reusing frontend/dist"
else
    echo "Building frontend (npm run build) ..."
    (
        cd frontend
        if [ ! -d node_modules ]; then
            echo "Installing frontend dependencies (npm install) ..."
            npm install
        fi
        npm run build
    )
    echo "Frontend build complete -> frontend/dist"
fi

# Single process on purpose: the app runs an in-process background worker and
# keeps in-memory log subscriptions, so it must NOT be scaled with --workers,
# and --reload is a dev-only feature we don't want in a production service.
echo "Starting server on http://localhost:$PORT (API + frontend) ..."
(
    source .venv/bin/activate
    uvicorn backend.main:app --host "$HOST" --port "$PORT"
) &
pids+=("$!")

wait "${pids[@]}"
