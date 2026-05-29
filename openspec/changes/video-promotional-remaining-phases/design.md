## Context

Phase 1 MVP is complete with the following architecture:
- **Backend**: FastAPI (Python 3.11) at `backend/` with SQLite task queue, single-worker processing
- **Frontend**: React + Vite at `frontend/` with TaskForm, TaskList, TaskDetail components
- **Pipeline**: YouTube/EPUB/PDF extractors → AI digester (2-pass) → VibeVoice TTS subprocess → HyperFrame HTML composition + render
- **TTS**: VibeVoice 1.5B at `/Volumes/TP-1TB/AIWork`, invoked as subprocess with `env_vibevoice_1.5b.sh`
- **Video**: HyperFrame at `hyperframe/`, single `podcast.html` Jinja2 template with basic dark theme
- **AI**: OpenAI-compatible API via `oneapi.yhroot.com` endpoint, configurable in `.env`

The system is structurally complete but untested with real data. Remaining work spans 3 phases: validation, visual polish, and advanced UX features.

## Goals / Non-Goals

**Goals:**
- Validate the full pipeline with real YouTube videos, EPUB books, and PDF files
- Refine AI prompts so generated podcast scripts sound natural and engaging
- Provide multiple visually polished video templates beyond the basic dark theme
- Integrate Lottie character animation in the HyperFrame composition
- Allow users to review/edit scripts before TTS and preview audio before video render
- Support multiple AI providers configurable from the UI
- Stream pipeline logs to the frontend in real time
- Integrate Isla-Reader curated highlights as an alternative EPUB processing mode

**Non-Goals:**
- Cloud deployment or multi-user support — this remains a local single-user tool
- Live video editing or timeline editing — HyperFrame handles composition programmatically
- Custom voice training or voice cloning — use VibeVoice's existing 9 presets
- YouTube upload automation — user downloads the MP4 and uploads manually

## Decisions

### 1. Video template system: Jinja2 template per style
**Choice**: Each video style is a separate Jinja2 HTML file in `backend/templates/` (e.g., `podcast_kinetic.html`, `podcast_swiss.html`). The composer selects the template based on task config.
**Why over single parameterized template**: Each style has fundamentally different HTML structure, animations, and CSS. Separate files are easier to maintain and test independently.

### 2. Lottie integration: lottie-web player in HyperFrame HTML
**Choice**: Embed `<lottie-player>` (or `@lottiefiles/dotlottie-web`) in the composition HTML, registered on `window.__hfLottie` for deterministic seek-driven rendering per HyperFrame's Lottie adapter pattern.
**Why over GIF/video overlay**: Lottie is vector-based, resolution-independent, and HyperFrame has native Lottie support with deterministic frame rendering.

### 3. Script editing: Save-to-file + re-trigger TTS
**Choice**: Add a `PUT /api/tasks/{id}/script` endpoint that writes the edited script to disk. Frontend shows a textarea with the generated script. User edits, saves, then clicks "Re-generate Audio" which re-runs TTS and compose stages.
**Why over inline editing with auto-save**: Explicit save+regenerate gives the user clear control over when expensive TTS inference runs.

### 4. AI provider management: SQLite config table
**Choice**: Store AI providers in a `providers` table in the same SQLite database. Each provider has endpoint, api_key, model, and is_default. The settings UI allows CRUD operations.
**Why over .env-only**: Multiple providers need persistence and UI management. A config table is simple and consistent with the existing SQLite pattern.

### 5. Log streaming: Server-Sent Events (SSE)
**Choice**: Use `sse-starlette` package. Add `GET /api/tasks/{id}/logs/stream` endpoint that yields pipeline log lines as SSE events. The worker writes logs to both file and an asyncio Queue that SSE consumers read from.
**Why over WebSocket**: SSE is simpler (one-way, auto-reconnect), sufficient for log streaming, and doesn't require a WebSocket library.

### 6. Isla-Reader integration: subprocess to Swift CLI
**Choice**: Call `generate-promotion.sh --epub <path> --style none` as subprocess to get `selected.stage2.json` with curated highlights. Parse the JSON and feed it to the digester as pre-curated talking points.
**Why over reimplementing in Python**: The Swift pipeline is production-tested with sophisticated AI curation. Reusing it avoids duplicating complex logic.

## Risks / Trade-offs

- **[TTS generation time on MPS]** → VibeVoice 1.5B on Apple Silicon MPS may take 5-15 min for a 10-min podcast. Mitigation: show clear progress in UI, allow shorter durations, consider the 0.5B realtime model as a "draft" option.
- **[AI prompt quality]** → Generated podcast scripts may sound unnatural initially. Mitigation: iterate on prompts with real content, store prompts as external files for easy tuning.
- **[HyperFrame Lottie rendering]** → Lottie animations need seek-driven registration (`window.__hfLottie`) which differs from autoplay. Mitigation: follow HyperFrame's documented Lottie adapter pattern, test with simple animations first.
- **[Isla-Reader Swift CLI dependency]** → Requires Xcode/Swift toolchain installed. Mitigation: make it optional — fallback to Python ebooklib extraction if Swift CLI is unavailable.
- **[Audio-text sync accuracy]** → Silence detection is approximate. Mitigation: tune silence threshold and minimum duration parameters; accept that overlay timing is "good enough" for podcast format where exact lip-sync isn't needed.
