# Video-Promotional

A local agent that turns a **YouTube video, EPUB, or PDF** into a short **podcast-style video**.
The primary output is a **solo talk-show monologue** (a single host talking to the audience);
a two-host dialogue format is also available.

Pipeline: **source extract → AI digest → script → ChatGPT Web viral thumbnail → rights-ledgered
footage scout → optional web-generated collage B-roll → selectable TTS → storyboard → art direction
→ agent-authored scenes → HyperFrames video render**.

## Architecture

- **Backend** — FastAPI (Python 3.11). Serves the JSON API under `/api/*`, static job output
  under `/outputs/*`, and the built frontend for everything else.
- **Frontend** — React + Vite. Built to `frontend/dist/` and served by the backend.
- **AI (digest + script)** — the **Claude Agent SDK** by default (`AI_BACKEND=agent_sdk`); the
  legacy direct OpenAI-compatible HTTP client is still available (`AI_BACKEND=http`). See below.
- **TTS** — local VibeVoice (1.5B high quality / 0.5B fast solo) or authenticated
  remote Orpheus English Q4, with model-specific voice catalogs and async job polling.
- **Video** — HyperFrames HTML→MP4 renderer (`hyperframe/`), invoked as a subprocess.

### AI backend: Claude Agent SDK

The digestion and scriptwriting calls run through the [Claude Agent SDK](https://platform.claude.com/docs/en/agent-sdk/python)
(vendored under `claude-agent-sdk-python/`, samples under `claude-agent-sdk-demos/` — both git
submodules, see `.gitmodules`). The SDK spawns the `claude` CLI in-process and talks the
**Anthropic protocol** to whatever gateway `ANTHROPIC_BASE_URL` points at, so the existing
provider registry (endpoint / key / model) keeps working — the endpoint host is simply
reinterpreted as an Anthropic base URL.

All of it is configured in **Admin -> System** (groups *AI Engine* and *Claude Agent SDK*):

- `AI_BACKEND=agent_sdk` (default) — Claude Agent SDK. Requires the `claude` CLI on `PATH`
  (install with `npm install -g @anthropic-ai/claude-code`).
- `AI_BACKEND=http` — the original OpenAI-compatible HTTP client (no CLI needed).
- `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_MODEL` — leave blank to derive from
  `AI_ENDPOINT` / `AI_API_KEY` / `AI_MODEL`; set explicitly for gateways whose Anthropic route
  is on a sub-path (e.g. `https://api.deepseek.com/anthropic`).

The transport lives in `backend/pipeline/agent.py`; `backend/pipeline/digester.py` dispatches
between the two backends. All chunking, JSON parsing, and CJK-repair logic is backend-agnostic.

### Video composition: storyboard → art direction → agent crews

The compose stage does not render one caption line per script line. It builds a **storyboard**,
gets **art direction** for it, and then has **Claude Agent SDK crews author the actual scenes**.

| Module | Role |
| --- | --- |
| `pipeline/storyboard.py` | Cuts the script into timed scenes against the audio (silence map, else word count). Pure arithmetic — no model. |
| `pipeline/visual_plan.py` | Art director. Assigns each scene a layout archetype, on-screen copy, accent colour and motif. JSON in / JSON out, so any provider works. Falls back to direction derived from the narration. |
| `pipeline/scene_kit.py` | Deterministic renderer for nine archetypes (`title`, `statement`, `topic`, `contrast`, `list`, `stat`, `quote`, `footage`, `outro`) with seeded SVG motif artwork. Produces the drafts and the fallback. |
| `pipeline/director.py` | Claude Agent SDK crews. Each is given a slice of the storyboard and rewrites those scene files with bespoke layout and artwork. Several run concurrently. |
| `pipeline/assembler.py` | Generates `index.html` — the spine that mounts scenes on the audio timeline — and runs `hyperframes lint`. |

**Why the split.** The spine is generated, never written by a model, so a creative failure cannot
desynchronise picture from sound or leave a gap in the timeline. Every file a crew writes is
checked against the HyperFrames runtime contract (`director.validate_scene_html`) before it
ships; anything that fails is reverted to its deterministic draft. A crew that dies costs plain
scenes, never a blank video.

**Checked against HyperFrames' own tooling.** After assembly the pipeline runs
`hyperframes lint` (contract errors — blocking) and `hyperframes inspect` (layout errors —
overlapping text, content spilling out of frame). Inspect findings are mapped back to the scenes
that caused them, by selector or by timestamp, and handed to a repair agent; anything still
failing afterwards is reverted rather than shipped.

Per-task artifacts land in the output directory: `storyboard.json`, `visual_plan.json`,
`director_report.json`, `compositions/scene-*.html`, and the generated `index.html`.

**Cost.** Art direction is one model call per 12 scenes. Authoring is one agent per 6 scenes,
three at a time. On a 12-minute episode (~42 scenes) expect the compose stage to spend most of
its wall clock in the crews; the HyperFrames render itself is roughly a third of real time. Set
**Agent direction** off (Admin -> System -> Video Direction) for a fast deterministic render.

Tuning (Admin -> System -> Video Direction):

- **Agent direction** off — skip the crews and render the deterministic scenes.
- **Max directed scenes** `N` — hand only the first N scenes to agents (`0` = all).
- The task's `video_template` (`podcast` / `kinetic` / `swiss` / `minimal` / `shanshui`) selects the theme. `shanshui` adds the rice-paper, layered ink-green terrain and ochre route-line language used by the account artwork.

This runs as a **single-port production service**: one backend process serves both the API and
the UI, so there is only **one URL to open**.

## Prerequisites

- **Python 3.11** (setup uses `~/.pyenv/versions/3.11.9`)
- **Node.js** via `nvm` (for the frontend build and HyperFrames CLI)
- **VibeVoice** installed under the *VibeVoice install root* (default `/Volumes/TP-1TB/AIWork`) —
  Admin -> System -> Voice & TTS
- An **AI provider gateway** (Admin -> System -> AI Engine, or the Models tab) — OpenAI-compatible
  endpoint that also exposes the Anthropic protocol (used by the default Claude Agent SDK backend)
- The **`claude` CLI** on `PATH` for the `agent_sdk` backend (`npm install -g @anthropic-ai/claude-code`);
  not needed if you switch the backend to `http`
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

### Configuration lives in Admin → System

Every AI, TTS, render, direction, footage and path setting (`AI_ENDPOINT`, `AI_MODEL`,
`AIWORK_ROOT`, `TTS_DEVICE`, `RENDER_FPS`, `RENDER_QUALITY`, …) is edited in the app:
**Admin → System**. Saves go to `data/settings.json` and apply to the next pipeline stage
immediately — no restart, except the two fields flagged `restart` in the UI (`OUTPUTS_DIR`,
`DB_PATH`), which are bound at startup.

`.env` is now only the **seed**: it supplies the defaults a fresh machine starts from, and
"Reset to default" in the UI restores them. See `.env.example` for the full list, and
`backend/settings_store.py` for the registry that drives the form.

### Launch-only environment variables

These are read by `scripts/start.sh` before the app exists, so they stay in the environment:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `8100` | Port the app listens on |
| `HOST` | `0.0.0.0` | Bind address |
| `SKIP_FRONTEND_BUILD` | `0` | Set to `1` to reuse the existing `frontend/dist` (faster restarts) |
| `LOG_DIR` | `logs/` | Where `start-*.log` is written (`start-latest.log` symlinks the latest) |

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

## Video orientation and paper-collage B-roll

Each new task has one final-video orientation: **16:9 landscape** (default) or **9:16 portrait**.
That choice drives public-footage search, generated collage stills, Gemini videos, scene dimensions,
caption safe areas, and the final HyperFrames render; the pipeline does not generate in one ratio and
cross-crop to the other.

The optional **Paper-collage B-roll** switch defaults to off and, when enabled, defaults to four
five-second clips. The task's Agent provider selects narration beats and designs visual metaphors,
ChatGPT Web generates the completed stills, and Gemini Web **Create Video** assembles each scene from
an empty paper field. Both web stages run through the signed-in project-local OpenCLI session, with
no `GEMINI_API_KEY` or `google-genai` dependency and no intermediate approval pauses. Choosing
**Paper collage** as the opening style always generates at least the opening clip, even when the
general collage switch is off. Successful clips are mounted full-bleed in HyperFrames; failed items
leave the deterministic scene intact and are recorded in `collage_broll/manifest.json`.

Browser timeouts have safe defaults of 420 seconds for ChatGPT stills and 1800 seconds for Gemini
video generation. They are editable under **Admin → System → Paper-collage B-roll** as
`COLLAGE_CHATGPT_TIMEOUT` and `COLLAGE_GEMINI_TIMEOUT`.

## Footage scout: Commons + YouTube

New tasks can enable **Public Footage** and choose a source strategy:

- **Hybrid** (default) fills from Wikimedia Commons first, then uses YouTube.
- **Wikimedia only** keeps the original explicit-open-license path.
- **YouTube only** discovers and downloads clips through the project `yt-dlp`.

After scriptwriting, the configured AI provider produces concrete B-roll queries. For YouTube
candidates the scout asks the signed-in Gemini web app, through OpenCLI, to select a coherent
interval for the matching narration excerpt. If OpenCLI's initial wait expires, the scout polls
the same conversation for the late assistant JSON before recording a failure and falling back.
FFmpeg then trims, crops, removes source audio, normalizes to H.264/YUV420p, and writes three
evidence frames. The resulting `footage/manifest.json` records the source URL, creator, exact
trim, analyzer result, script excerpt, dimensions, duration, byte size, SHA-256, and local path.

The scout requires no stock-media API key. Its Wikimedia branch only accepts files with explicit
Public Domain, CC0, CC BY, or CC BY-SA metadata. The task detail page previews downloaded files
and supports retrying the footage stage without re-running extraction, scriptwriting, or TTS.

Platform downloadability is **not** treated as permission to republish. Every YouTube
entry is marked `review_required`, and the manifest/UI includes a publication blocker until a
human verifies the creator's reuse terms. Prefer creator-provided downloads, Creative Commons
uploads, and short transformative excerpts; comply with platform terms and local law.

### Script-driven viral thumbnail

After the final narration script is saved—and before any TTS model is loaded—the pipeline asks
the configured AI provider to turn that exact script into one focused cover-art prompt. The
project-local OpenCLI `chatgpt image` adapter sends it to the signed-in ChatGPT web app, exports
the generated image, and saves everything under the task output directory:

```text
thumbnail/prompt.txt
thumbnail/images/chatgpt_<timestamp>.png
thumbnail/manifest.json
```

The task API exposes the primary image as `/api/tasks/<id>/thumbnail` and the final prompt as
`/api/tasks/<id>/thumbnail/prompt`; the task detail page shows both alongside the final cut. New
tasks enable thumbnail generation by default and can switch it off in Production setup.

The master formula is [backend/prompts/thumbnail.txt](backend/prompts/thumbnail.txt). It is also
editable live in **Admin → Prompts → Viral Thumbnail Formula**, so composition rules can be tuned
without a restart or code change. ChatGPT image generation has its own timeout in **Admin →
System → Viral Thumbnail**.

### Project-local OpenCLI setup

`./scripts/setup.sh` installs the pinned OpenCLI runtime under `tools/opencli/`. It does not run a
global npm install and does not write to `~/.claude`. The six upstream OpenCLI skills plus this
project's `video-web-footage` workflow live only under `.claude/skills/`, which is the project
skill path used by the Claude Agent SDK.

Install or enable the official **OpenCLI Browser Bridge** Chrome extension once, keep Chrome
open with the desired Gemini and ChatGPT sessions signed in, then verify the project wrapper:

```bash
./scripts/opencli.sh doctor
./scripts/opencli.sh gemini status -f json
./scripts/opencli.sh chatgpt status -f json
```

No command in the pipeline invokes a global `opencli`; the SDK process prepends this repository's
wrapper/runtime paths to `PATH`. See `docs/OPENCLI_MEDIA_LOOP.md` for the architecture, operating
limits, validation recipe, and product roadmap.
