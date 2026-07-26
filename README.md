# Video-Promotional

A local agent that turns a **YouTube video, EPUB, or PDF** into a short **podcast-style video**.
The primary output is a **solo talk-show monologue** (a single host talking to the audience);
a two-host dialogue format is also available.

Pipeline: **source extract → AI digest → script → VibeVoice TTS → HyperFrames video render**.

## Architecture

- **Backend** — FastAPI (Python 3.11). Serves the JSON API under `/api/*`, static job output
  under `/outputs/*`, and the built frontend for everything else.
- **Frontend** — React + Vite. Built to `frontend/dist/` and served by the backend.
- **AI (digest + script)** — the **Claude Agent SDK** by default (`AI_BACKEND=agent_sdk`); the
  legacy direct OpenAI-compatible HTTP client is still available (`AI_BACKEND=http`). See below.
- **TTS** — VibeVoice (1.5B high quality / 0.5B fast solo) invoked as a subprocess.
- **Video** — HyperFrames HTML→MP4 renderer (`hyperframe/`), invoked as a subprocess.

### AI backend: Claude Agent SDK

The digestion and scriptwriting calls run through the [Claude Agent SDK](https://platform.claude.com/docs/en/agent-sdk/python)
(vendored under `claude-agent-sdk-python/`, samples under `claude-agent-sdk-demos/` — both git
submodules, see `.gitmodules`). The SDK spawns the `claude` CLI in-process and talks the
**Anthropic protocol** to whatever gateway `ANTHROPIC_BASE_URL` points at, so the existing
provider registry (endpoint / key / model) keeps working — the endpoint host is simply
reinterpreted as an Anthropic base URL.

- `AI_BACKEND=agent_sdk` (default) — Claude Agent SDK. Requires the `claude` CLI on `PATH`
  (`npm install -g @anthropic-ai/claude-code`), which is installed automatically in the Docker image.
- `AI_BACKEND=http` — the original OpenAI-compatible HTTP client (no CLI needed).
- `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_MODEL` — leave blank to derive from
  `AI_ENDPOINT` / `AI_API_KEY` / `AI_MODEL`; set explicitly for gateways whose Anthropic route
  is on a sub-path (e.g. `https://api.deepseek.com/anthropic`).

The transport lives in `backend/pipeline/agent.py`; `backend/pipeline/digester.py` dispatches
between the two backends. All chunking, JSON parsing, and CJK-repair logic is backend-agnostic.

## Docker deployment

A container image (`Dockerfile` + `docker-compose.yml`) bundles the API, the built frontend, the
Claude Agent SDK **and** the `claude` CLI, plus Node and ffmpeg. Runtime data (rendered videos,
uploads, tasks DB) persists in named volumes.

```bash
cp .env.example .env      # then fill in AI_API_KEY (the scripts auto-create .env if missing)
./scripts/docker_start.sh # build + start; prints the endpoints
./scripts/docker_logs.sh  # follow logs
./scripts/docker_down.sh  # stop (volumes preserved)
```

App: **http://localhost:8100** (override with `APP_PORT`).

> **TTS / render caveat.** VibeVoice runs on Apple **MPS** and HyperFrames renders via headless
> Chrome — both are host-hardware bound and are **not** exercised inside the Linux container.
> The container fully covers extraction, AI digest/scriptwriting (Agent SDK), and the API/UI; run
> `./scripts/start.sh` natively on the Mac host when you need the VibeVoice/render stages.

This runs as a **single-port production service**: one backend process serves both the API and
the UI, so there is only **one URL to open**.

## Prerequisites

- **Python 3.11** (setup uses `~/.pyenv/versions/3.11.9`)
- **Node.js** via `nvm` (for the frontend build and HyperFrames CLI)
- **VibeVoice** installed under `AIWORK_ROOT` (default `/Volumes/TP-1TB/AIWork`) — see `backend/config.py`
- An **AI provider gateway** (configured in `.env`) — OpenAI-compatible endpoint that also exposes
  the Anthropic protocol (used by the default Claude Agent SDK backend)
- The **`claude` CLI** on `PATH` for `AI_BACKEND=agent_sdk` (`npm install -g @anthropic-ai/claude-code`);
  not needed if you set `AI_BACKEND=http`
- **Git submodules** for the vendored SDK — after a fresh clone run
  `git submodule update --init --recursive`

## Setup (once)

```bash
./scripts/setup.sh
```

Creates the Python venv and installs dependencies, installs frontend deps, initializes the
HyperFrames project, and creates output directories.

## Run

```bash
./scripts/start.sh
```

`start.sh` **builds the frontend** (`npm run build`) and then starts the backend, which serves
the API and the freshly built UI on a single port.

Open: **http://localhost:8100** (new task form at **http://localhost:8100/new**)

> First load after a rebuild: hard-refresh once (`Cmd+Shift+R`) if the browser shows a cached page.

### Useful environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `8100` | Port the app listens on |
| `HOST` | `0.0.0.0` | Bind address |
| `SKIP_FRONTEND_BUILD` | `0` | Set to `1` to reuse the existing `frontend/dist` (faster restarts) |
| `LOG_DIR` | `logs/` | Where `start-*.log` is written (`start-latest.log` symlinks the latest) |

AI, TTS, and render settings (`AI_ENDPOINT`, `AI_MODEL`, `AIWORK_ROOT`, `TTS_DEVICE`,
`RENDER_FPS`, `RENDER_QUALITY`, …) live in `.env` / `backend/config.py`.

Stop the app with `Ctrl+C` (it terminates the backend cleanly).

## Optional: live-reload dev mode

The single-port service does **not** hot-reload. For active development with instant
frontend/backend reload, run the two dev servers manually instead of `start.sh`:

```bash
# Terminal 1 — backend with auto-reload
source .venv/bin/activate
uvicorn backend.main:app --reload --reload-dir backend --host 0.0.0.0 --port 8100

# Terminal 2 — Vite dev server (proxies /api and /outputs to :8100)
cd frontend && npm run dev
```

Then open the Vite URL it prints (http://localhost:5173). Use this only for development;
for normal runs use `./scripts/start.sh` on port 8100.

## Choosing the format

On the New Task form, **Format** is the first choice:

- **Solo Talk-Show** (default) — one host, one voice. Works with either TTS model
  (the 0.5B model is solo-only).
- **Two-Host Dialogue** — host + co-host, two voices. Requires the **1.5B** model.

The number of voice pickers follows the format automatically.
