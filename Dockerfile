# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 — build the React/Vite frontend into frontend/dist
# ---------------------------------------------------------------------------
FROM node:20-bookworm-slim AS frontend-build
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ---------------------------------------------------------------------------
# Stage 2 — Python runtime that serves the API + built frontend and drives the
# Claude Agent SDK. Node is present for the `claude` CLI (spawned by the SDK),
# HyperFrames, and the yt-dlp JS runtime; ffmpeg for audio/video composition.
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NODE_MAJOR=20 \
    # SDK-spawned CLI: skip the version self-check and non-essential traffic.
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

WORKDIR /app

# System deps: node (for the claude CLI + HyperFrames + yt-dlp), ffmpeg, curl.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg ffmpeg git \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

# Python deps. Copy the vendored Claude Agent SDK first so the
# `./claude-agent-sdk-python` path requirement in requirements.txt resolves.
COPY claude-agent-sdk-python/ /app/claude-agent-sdk-python/
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Application code + the prebuilt frontend.
COPY backend/ /app/backend/
COPY hyperframe/ /app/hyperframe/
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

# Runtime data lives on mounted volumes (see docker-compose.yml).
RUN mkdir -p /app/data /app/outputs /app/uploads

ENV OUTPUTS_DIR=/app/outputs \
    UPLOADS_DIR=/app/uploads \
    DB_PATH=/app/data/tasks.db \
    HYPERFRAME_DIR=/app/hyperframe

EXPOSE 8100

HEALTHCHECK --interval=15s --timeout=5s --retries=6 \
    CMD curl -fsS http://127.0.0.1:8100/api/health >/dev/null || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8100"]
