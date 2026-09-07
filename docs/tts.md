# Voice generation and recovery

The TTS implementation is synchronized with FrontierTechSN at `02d42ff` (2026-09-07), including its Pocket integration (`d4cb29c`), deployment optimization (`7067fd9`), previews (`321522e`), natural-pause fixes (`7514efd`, `8f29974`, `a86b4af`), and natural-speed policy (`26a8961`, `595f642`). Existing Video-Promotional pronunciation, chunk-boundary and accepted-job retry fixes remain in place.

## Select and preview

Select **Kyutai Pocket TTS — English 100M (remote CPU)** in New Task or Content Plan. Pocket supports English monologue and exposes 21 voices. Use the play button beside a voice to hear a sample. First play synthesizes a sample; later plays use `data/voice_previews/pocket-tts-en/`. Loading, playback, stop, errors and model changes are handled by one player per form. Pocket does not preload the entire catalog at startup.

Admin → System → Voice & TTS owns the following settings. Existing default model and voices are preserved; adding Pocket does not change saved task selections.

| Setting | Default | Purpose |
| --- | --- | --- |
| `POCKET_TTS_URL` | `http://10.60.11.3:8090` | Upstream HTTP service |
| `POCKET_TTS_API_KEY` | blank | Optional gateway `X-API-Key`; masked in Admin |
| `POCKET_TTS_MODEL_REVISION` | `adde0654090b1d54f6ee416ed10fd1b108069f13` | Deployment revision included in cache identity |
| `POCKET_TTS_CHUNK_WORDS` | 240 | External paragraph limit; split at complete sentences; 0 retains physical lines |
| `POCKET_TTS_REQUEST_TIMEOUT` | 900 seconds | Synchronous streaming WAV request ceiling |

The deployment files in [deploy/pocket-tts](../deploy/pocket-tts/README.md) are copied from the sibling project. Their host performance measurements describe that project's deployment investigation, not a new benchmark or deployment performed by this synchronization.

## Audio integrity and timing

- Pocket preserves physical script paragraphs and complete sentences, normalizes the upstream streaming WAV header, checks internal silence, and joins PCM without speech retiming. Ordinary internal silence remains bounded at 0.8 seconds; evidenced sentence boundaries may reach 1.2 seconds.
- Orpheus retains short, grammatical utterances, lossless source coverage, bounded retries, same-job recovery, and verified chunk reuse after renumbering. Video-Promotional's continuous network-outage retry window remains separate from synthesis timeout.
- Remote narration is checked against independent MLX Whisper transcripts for missing, extra, repeated or changed content. Exact acoustic spelling rules, same-waveform rechecks and bounded ASR adjudication are auditable beside each chunk. A slower verification copy never replaces delivery audio.
- The ASR judge uses this project's existing configured provider through `digester._resolve_provider` / `_chat`; synchronization does not introduce FrontierTechSN's primary/backup routing system.
- `tts_manifest.json` binds the source text, output WAV, model, per-part verification and natural `1.0x` pacing. Rendering rejects changed text/audio or an unproven pacing contract for every provider. The measured WAV drives scene duration; the existing visual outro remains separate.
- Legacy `ORPHEUS_TTS_SPEED_PERCENT` overrides are removed from the settings store and environment values no longer enable retiming. Existing audio without the new pacing proof must be regenerated before a new compose run. Completed videos remain available.
- The merged acoustic vocabulary uses new verifier versions (Orpheus 24, Pocket 6), so stale verification sidecars must be revalidated.

## Resume failed audio

A failed task with a saved script can use `POST /api/tasks/{id}/resume-tts`. The task detail page offers **Resume Audio** when a generated title exists and audio generation has not completed. This runs TTS and the usual post-audio path, preserving the title and thumbnail. The worker claims the TTS stage before consuming `.resume_tts`, so interruption cannot silently restart the full editorial pipeline.

## Validation

The synchronization was exercised against the existing Pocket service with two paragraphs and 49 spoken words: 17.92 seconds of audio, 49/49 exact ASR words, and complete source/audio manifest validation. A second run reused both chunks without modification (about 0.03 seconds).

The same WAV went through real MLX alignment, deterministic scene assembly, HyperFrames lint/inspect and 1080p MP4 rendering. Word alignment was 100%; narration remained 17.92 seconds with the template's existing 5-second visual outro. Decoded MP4 audio correlated with the original-speed WAV at 0.99993 after accounting for approximately 43 ms of codec delay. Visual planning used the deterministic scene kit and external visual review was disabled for this timing check.

Ego Lite playback checks covered model/voice selection, first-use preview, cached playback, playback time progression, stop, and model-switch cancellation in both New Task and Content Plan. Runtime evidence is kept under ignored `outputs/tts-sync-audit/`.

Run backend regressions with `source .venv/bin/activate && python -m pytest backend/tests -q`; run the frontend build with `cd frontend && npm run build`. The modified frontend files pass ESLint. Repository-wide ESLint still reports the pre-existing effect/state errors in `PromptsPanel.tsx` and the existing dependency warning in `SystemPanel.tsx`.
