## Why

Phase 1 MVP of the Video-Promotional podcast agent is complete: project scaffolding, FastAPI backend, React frontend, pipeline stages (YouTube extractor, AI digester, TTS wrapper, HyperFrame composer), and web UI with task management are all implemented. However, the system has not been tested end-to-end with real data, the EPUB/PDF extractors need real-file validation, video output quality needs polish (better templates, Lottie character, accurate audio-text sync), and several UX features (script editing, audio preview, AI provider management) are missing.

## What Changes

**Already completed (Phase 1):**
- Project scaffolding: FastAPI + Vite/React + HyperFrame init + setup/start scripts
- SQLite task store + single-worker background queue (`backend/database.py`, `backend/worker.py`)
- YouTube extractor via yt-dlp (`backend/pipeline/extractors/youtube.py`)
- EPUB extractor via ebooklib (`backend/pipeline/extractors/epub.py`)
- PDF extractor via pdfplumber (`backend/pipeline/extractors/pdf.py`)
- AI content digester with 2-pass summarize+script (`backend/pipeline/digester.py`)
- VibeVoice TTS subprocess wrapper (`backend/pipeline/tts.py`)
- Basic HyperFrame composition template (`backend/templates/podcast.html`)
- Video composer with silence detection (`backend/pipeline/composer.py`)
- Pipeline orchestrator (`backend/pipeline/orchestrator.py`)
- Web UI: TaskForm, TaskList, TaskDetail components
- REST API: tasks CRUD, file download, settings endpoints
- Git repository initialized

**Remaining work (Phases 2-4):**
- Real-data end-to-end testing (YouTube URL → finished MP4)
- EPUB/PDF source validation with real files and prompt refinement
- Multiple video templates with improved visual design
- Silence-detection-based segment timing accuracy improvements
- Lottie animated character overlay integration
- Title card and outro card with transitions
- Script editing in UI before TTS generation
- Audio preview before video render
- AI provider management UI (add/switch/configure providers)
- Isla-Reader full pipeline integration for curated EPUB highlights
- Real-time log streaming via SSE

## Capabilities

### New Capabilities
- `real-data-validation`: End-to-end testing with real YouTube URLs, EPUB files, and PDFs to validate the full pipeline and refine AI prompts
- `video-templates`: Multiple HyperFrame video templates (kinetic text, Swiss grid, minimal) with improved visual design, title/outro cards, and transitions
- `lottie-character`: Lottie animated character overlay in video corner with proper HyperFrame integration
- `script-editing`: In-browser script review and editing before TTS generation, with save and re-generate capabilities
- `audio-preview`: Audio playback in UI after TTS generation, before committing to video render
- `ai-provider-management`: UI for managing multiple AI providers (add/edit/switch endpoints, models, API keys)
- `isla-reader-integration`: Integration with Isla-Reader Promotion-Agent for curated book highlights as podcast source material
- `log-streaming`: Real-time pipeline log streaming to the frontend via Server-Sent Events (SSE)

### Modified Capabilities

## Impact

- `backend/pipeline/composer.py` — major changes for multiple templates and Lottie integration
- `backend/templates/` — new template files for each video style
- `backend/pipeline/digester.py` — prompt refinement based on real output quality
- `backend/routers/tasks.py` — new endpoints for script editing, audio preview, SSE logs
- `backend/routers/settings.py` — expanded AI provider management
- `frontend/src/components/` — new UI components for script editor, audio player, provider config
- `assets/lottie/` — Lottie animation JSON files
- Dependencies: may need additional packages for SSE (sse-starlette)
