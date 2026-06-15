## 1. Real-Data Validation (Phase 2)

- [ ] 1.1 Test YouTube pipeline end-to-end with a real YouTube video URL (with English subtitles), verify MP4 output is playable
- [ ] 1.2 Test EPUB pipeline end-to-end with a real EPUB file uploaded via the web UI, verify MP4 output
- [ ] 1.3 Test PDF pipeline end-to-end with a real PDF file uploaded via the web UI, verify MP4 output
- [x] 1.4 Refine `backend/prompts/summarize.txt` based on real summarization output quality
- [x] 1.5 Refine `backend/prompts/scriptwrite.txt` to produce more natural 2-speaker dialogue (use `2p_goat.txt` as few-shot reference)
- [ ] 1.6 Fix any pipeline bugs discovered during real-data testing (extractor parsing, TTS invocation, HyperFrame rendering)

## 2. Video Templates (Phase 3)

- [x] 2.1 Create `backend/templates/podcast_kinetic.html` — kinetic text style with animated text reveals using GSAP or CSS animations
- [x] 2.2 Create `backend/templates/podcast_swiss.html` — clean Swiss grid typography layout
- [x] 2.3 Create `backend/templates/podcast_minimal.html` — minimal/clean style with large centered text
- [x] 2.4 Add template selection field to `TaskConfig` model and `TaskForm.tsx` UI
- [x] 2.5 Update `backend/pipeline/composer.py` to select template based on task config
- [x] 2.6 Add title card (first 5s) with fade-in animation to all templates
- [x] 2.7 Add outro card (last 5s) with fade-out animation to all templates
- [x] 2.8 Add smooth fade transitions (0.3-0.5s) between speaker segments in all templates

## 3. Lottie Character Overlay (Phase 3)

- [x] 3.1 Source or create a default Lottie animation file and save to `assets/lottie/podcast_host.json`
- [x] 3.2 Update `backend/templates/podcast.html` to use HyperFrame's Lottie adapter pattern (`window.__hfLottie` registration) instead of `<lottie-player>` autoplay
- [x] 3.3 Apply the same Lottie integration pattern to all other video templates
- [ ] 3.4 Test Lottie rendering with `npx hyperframes render` to verify deterministic frame-by-frame animation

## 4. Script Editing (Phase 4)

- [x] 4.1 Add `PUT /api/tasks/{id}/script` endpoint to `backend/routers/tasks.py` that accepts plain text body and writes to the task's script file
- [x] 4.2 Add `POST /api/tasks/{id}/regenerate` endpoint that re-runs TTS and compose stages from the existing script (skip extract + digest)
- [x] 4.3 Add script editor textarea component to `TaskDetail.tsx` that fetches and displays the script when available
- [x] 4.4 Add "Save Script" button that calls the PUT endpoint
- [x] 4.5 Add "Re-generate Audio" button that calls the regenerate endpoint and updates task status

## 5. Audio Preview (Phase 4)

- [x] 5.1 Add HTML `<audio>` player to `TaskDetail.tsx` that appears after TTS stage completes, sourcing from `/api/tasks/{id}/audio`
- [x] 5.2 Modify pipeline to pause after TTS stage (add `awaiting_review` status) so user can preview audio before video render
- [x] 5.3 Add "Render Video" button that resumes the pipeline from composing stage
- [x] 5.4 Update `TaskStatus` enum and pipeline stages display to include `awaiting_review` state

## 6. AI Provider Management (Phase 4)

- [x] 6.1 Create `providers` table in SQLite schema (id, name, endpoint, api_key, model, is_default, created_at)
- [x] 6.2 Add migration logic in `backend/database.py` to create the providers table, seed with the default provider from `.env`
- [x] 6.3 Create `backend/routers/providers.py` with CRUD endpoints: GET /api/providers, POST /api/providers, PUT /api/providers/{id}, DELETE /api/providers/{id}
- [x] 6.4 Update `backend/pipeline/digester.py` to load provider config from database by provider ID instead of only from `.env`
- [x] 6.5 Add AI provider dropdown to `TaskForm.tsx` that fetches from `/api/providers`
- [x] 6.6 Create settings page component (`SettingsPage.tsx`) with provider management UI (list, add, edit, delete, set default)
- [x] 6.7 Add route for settings page in `App.tsx`

## 7. Isla-Reader Integration (Phase 4)

- [x] 7.1 Create `backend/pipeline/extractors/epub_curated.py` that invokes `generate-promotion.sh --epub <path> --style none` as subprocess and parses `selected.stage2.json`
- [x] 7.2 Add fallback logic: if Swift CLI is not available, fall back to standard ebooklib extraction with a warning
- [x] 7.3 Add processing mode field to `TaskConfig` (values: `full_text`, `curated_highlights`)
- [x] 7.4 Update `backend/pipeline/orchestrator.py` to choose extractor based on processing mode
- [x] 7.5 Update `backend/pipeline/digester.py` to handle curated highlights input (pre-selected talking points instead of raw text)
- [x] 7.6 Add processing mode selector to EPUB tab in `TaskForm.tsx` (radio: "Full Text" / "Curated Highlights")

## 8. Log Streaming (Phase 4)

- [x] 8.1 Install `sse-starlette` package and add to `requirements.txt`
- [x] 8.2 Add pipeline log file writing to `backend/pipeline/orchestrator.py` — write each stage's log to `<output_dir>/logs/pipeline.log`
- [x] 8.3 Create an asyncio Queue-based log broadcast system in `backend/worker.py` for active tasks
- [x] 8.4 Add `GET /api/tasks/{id}/logs/stream` SSE endpoint in `backend/routers/tasks.py` that streams from the queue (active) or reads from log file (completed)
- [x] 8.5 Create `LogPanel.tsx` component with collapsible log viewer that auto-scrolls
- [x] 8.6 Integrate `LogPanel` into `TaskDetail.tsx` with EventSource for live streaming during processing

## 9. TTS Model Selection (Phase 4)

- [x] 9.1 Add a TTS model registry to `backend/config.py` mapping each model key to its env script, project dir, inference script, and speaker flag — `vibevoice-1.5b` → (`env_vibevoice_1.5b.sh`, `VibeVoice-1.5B`, `demo/inference_from_file.py`, `--speaker_names`); `vibevoice-0.5b` → (`env_vibevoice.sh`, `VibeVoice`, `demo/realtime_model_inference_from_file.py`, `--speaker_name`)
- [x] 9.2 Update `backend/pipeline/tts.py` so `generate_tts(..., tts_model=...)` resolves invocation params from the registry and uses the correct speaker flag; raise a clear error on unknown model keys; default to `vibevoice-1.5b`
- [x] 9.3 Add a `tts_model` field to the `TaskConfig` model (default `vibevoice-1.5b`) and thread it through `backend/pipeline/orchestrator.py` to the TTS stage
- [x] 9.4 Add a TTS model dropdown to `TaskForm.tsx` — options "1.5B (high quality)" and "0.5B (fast draft)", defaulting to 1.5B
- [ ] 9.5 Verify both models run end-to-end (1.5B and 0.5B) and produce playable WAV output via the existing output-detection logic
