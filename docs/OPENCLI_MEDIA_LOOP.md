# OpenCLI web-footage loop

## Product contract

The footage loop turns a narration script into attributable, editable B-roll without expanding
Claude Code's global state. OpenCLI is pinned in `tools/opencli/package.json`; its upstream skills
and the project workflow are stored in `.claude/skills`. The backend executes only
`scripts/opencli.sh` and the project virtualenv's `yt-dlp`.

```text
narration script
  -> query plan
  -> Wikimedia search -----------------------> explicit-open-license candidate
  -> OpenCLI + signed-in Chrome -> Bilibili --> rights-review candidate
  -> project yt-dlp ------------> YouTube ----> rights-review candidate
  -> Gemini Web link analysis (YouTube) ------> trim JSON or recorded fallback
  -> FFprobe + FFmpeg ------------------------> silent normalized clip + evidence frames
  -> footage/manifest.json -------------------> visual plan -> HyperFrames -> final MP4
```

OpenCLI is used where a real signed-in browser session matters. YouTube discovery/download stays
on `yt-dlp` because upstream OpenCLI 1.8.6 has a Bilibili adapter but no equivalent YouTube
adapter. This is deliberate capability routing, not a second global dependency.

## Setup and isolation

1. Run `./scripts/setup.sh` or `npm install --prefix tools/opencli --no-audit --no-fund`.
2. Install the official OpenCLI Browser Bridge Chrome extension and keep Chrome running.
3. Sign in to Bilibili and Gemini as needed.
4. Run `./scripts/opencli.sh doctor`.
5. In the New Task form select Hybrid or Web Platforms.

Project isolation can be audited with:

```bash
./scripts/opencli.sh --version
find .claude/skills -maxdepth 2 -name SKILL.md -print
```

No workflow step writes to `~/.claude`, installs `opencli` globally, or changes global Claude
Code settings. Browser login/extension state is intentionally user-owned Chrome state.

## Manifest and failure semantics

Every retained clip has a canonical source link, creator, provider, source duration, exact trim,
script excerpt, analyzer/status/reason, normalized media facts, SHA-256, and three evidence frame
paths. Manifest writes are atomic. Raw downloads are deleted only after the normalized derivative and
evidence frames succeed; failed raw files are retained for diagnosis.

Gemini's web product can usually inspect public YouTube links, but it cannot reliably open
Bilibili links and may time out or return no response. These are normal external limitations.
They do not become fabricated model judgments: the manifest records a low-confidence
`deterministic-safe-offset` result and the rest of the pipeline continues.

Downloading is not a rights grant. Wikimedia clips pass an explicit license allowlist. Web clips
are always `rights_status=review_required` and add a publication blocker. The editor must review
the original page and reuse terms before publishing.

## TTS-free validation

For a light test, use a text script and a short silent timing WAV rather than generating local
TTS. Acquire one Bilibili and one YouTube clip, inspect `footage/manifest.json`, then compose at
draft quality. Verify the final file with:

```bash
ffprobe -v error -show_entries format=duration:stream=codec_name,width,height,pix_fmt \
  -of json outputs/<task-id>/video.mp4
```

The checked development fixture `outputs/opencli-e2e/` is intentionally ignored by Git.

## Next product bets

1. **Shot quality ranker.** Score motion continuity, sharpness, occlusion, subtitles, faces, logos,
   and watermarks from evidence frames; let Gemini explain only the top candidates.
2. **Visual continuity pass.** Match color temperature, camera direction, subject scale, and motion
   between neighboring shots; generate per-clip LUT/crop instructions for FFmpeg.
3. **Script-to-shot memory.** Store successful query/shot/trim pairs as a local searchable ledger.
   Similar future narration can reuse proven sources without repeating browser work.
4. **Beat-aware editing.** Derive energy and cut points from the final audio waveform, then align
   B-roll entrances and transitions without regenerating narration.
5. **Evidence-first multimodal review.** Add a project OpenCLI adapter that uploads the three local
   frames to Gemini when link access fails. This is preferable to pretending Bilibili URLs were
   seen; upstream's generic upload command currently needs a real file input exposed by the page.
6. **Rights inbox.** Present all web candidates in one approval queue with source snapshot, creator
   terms, intended use, and accept/replace controls; only approved clips can clear publication.
7. **Self-healing web adapters.** Run the project copies of `opencli-browser-sitemap` and
   `opencli-autofix` against changed sites, then require a recorded smoke test before adopting a
   repaired action.
8. **Automatic reframing.** Detect subject/saliency across sampled frames and animate a safe crop
   for portrait exports rather than using a fixed center crop.

The first two bets most directly improve perceived video quality; the rights inbox is the most
important release-safety investment. Frame upload is the highest-value OpenCLI extension because
it closes the current Bilibili/Gemini visibility gap while keeping claims auditable.
