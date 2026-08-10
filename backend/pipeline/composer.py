"""Compose the narration into a rendered video.

The stage runs in five steps:

1. **Storyboard** — cut the script into timed scenes against the audio.
2. **Direction** — an art director model decides what each scene shows.
3. **Authoring** — Claude Agent SDK crews write the HyperFrames scene files,
   starting from deterministic drafts and falling back to them scene-by-scene.
4. **Assembly** — a generated spine mounts the scenes on the audio timeline.
5. **Render** — the HyperFrames CLI captures the composition to MP4.

Steps 2 and 3 are the only places a model is involved, and neither can produce a
blank video: the spine is generated, every authored scene is gated against the
HyperFrames runtime contract, and lint failures revert the offending scene to
its deterministic draft before the render starts.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

from backend import config
from backend.pipeline import (
    av_sync,
    assembler,
    director,
    footage,
    multimodal_review,
    scene_kit,
    storyboard as sb,
    visual_plan,
)
from backend.pipeline.process_logging import run_capture_logged, stream_subprocess

logger = logging.getLogger(__name__)
LogCallback = Callable[[str], None]

# FFmpeg silence analysis is a quick pass; the HyperFrames render is the long
# stage and is streamed live, so this ceiling only guards a genuine hang.
SILENCE_TIMEOUT = 120

# Per-frame wall-clock ceiling used to derive the render timeout from the frame
# count (duration x fps). Scene-based compositions capture far faster than the
# old 158-clip single-page layout, but the ceiling stays generous: it must not
# kill a slow-but-live render, only a genuinely wedged one.
RENDER_SECONDS_PER_FRAME = 1.5
RENDER_TIMEOUT_FLOOR = 900  # never below 15 min, regardless of how short the clip is
# The real hang detector: HyperFrames reports capture progress continuously, so
# going silent for this long means it is wedged rather than merely slow. Warmup
# (Chrome launch, first-frame compile) is the longest legitimate quiet stretch.
RENDER_STALL_TIMEOUT = 600


def _detect_silence_boundaries(wav_path: str, log: LogCallback | None = None) -> list[float]:
    command = [
        "ffmpeg", "-i", wav_path,
        # VibeVoice inserts sub-second pauses inside sentences and longer pauses
        # between script paragraphs/turns. Treat only the latter as caption
        # boundaries; otherwise early sentence pauses consume segment slots and
        # leave the final caption on-screen for most of the episode.
        "-af", "silencedetect=noise=-30dB:d=1.2",
        "-f", "null", "-",
    ]
    # NB: do NOT pass the task `log` callback here. silencedetect emits hundreds
    # of stderr lines; fanning each one out per-line to pipeline.log (reopened
    # every line) + the SSE stream + the root logger floods the log pipe and is
    # what produced the "--- Logging error ---" spam. The full output still goes
    # to the module logger as a single record (start.sh log) for debugging, and
    # compose_video emits a concise one-line boundary summary to the task log.
    result = run_capture_logged(
        name="FFmpeg silence detect",
        command=command,
        logger=logger,
        log=None,
        timeout=SILENCE_TIMEOUT,
    )
    boundaries = []
    for line in result.stderr.splitlines():
        match = re.search(r"silence_end: ([\d.]+)", line)
        if match:
            boundaries.append(float(match.group(1)))
    return boundaries


def _project_relative(path: str | Path, project_dir: Path) -> str:
    """Path of ``path`` relative to the render project dir, POSIX-style.

    HyperFrames sandboxes asset access to the project directory, so every
    referenced file must live inside it and be referenced relatively. Falls back
    to an absolute path for assets outside the project (which HyperFrames will
    flag as missing) rather than raising.
    """
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(project_dir.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _build_render_command(project_dir: Path, video_path: Path) -> list[str]:
    """Build the render command for the current HyperFrames CLI (v0.6.x).

    The render entry is the project directory (which must contain
    ``index.html``) and dimensions come from ``--resolution``. Prefer the
    locally-installed, version-pinned binary so a render never triggers an
    on-demand ``npx`` install; fall back to a *pinned* npx invocation only if the
    local install is missing.
    """
    local_bin = config.HYPERFRAME_DIR / "node_modules" / ".bin" / "hyperframes"
    if local_bin.exists():
        base = [str(local_bin)]
    else:
        base = ["npx", "--yes", f"hyperframes@{config.HYPERFRAMES_VERSION}"]
    return base + [
        "render", str(project_dir),
        "--output", str(video_path),
        "--resolution", config.RENDER_RESOLUTION,
        "--fps", str(config.RENDER_FPS),
        "--quality", config.RENDER_QUALITY,
        "-w", str(config.RENDER_WORKERS),
    ]


def _mount_list(board: dict) -> list[dict]:
    """Every mount needed to cover the timeline with no gaps.

    The first narrated scene begins at zero; there is no separate title-card
    mount before the subject starts.
    """
    mounts = [
        {"id": scene["id"], "start": scene["start"], "duration": scene["duration"]}
        for scene in board["scenes"]
    ]
    mounts.append(
        {
            "id": visual_plan.OUTRO_SCENE_ID,
            "start": float(board["outro_start"]),
            "duration": float(board["outro_duration"]),
        }
    )
    return mounts


def _kit_plans(
    plans: list[dict], mounts: list[dict], theme: scene_kit.Theme
) -> list[scene_kit.ScenePlan]:
    duration_by_id = {mount["id"]: float(mount["duration"]) for mount in mounts}
    return [
        scene_kit.ScenePlan.from_dict(
            plan,
            duration=duration_by_id.get(plan["id"], 6.0),
            scene_id=plan["id"],
            theme=theme,
        )
        for plan in plans
    ]


async def compose_video(
    script_path: str,
    audio_path: str,
    output_dir: str,
    title: str = "Podcast Episode",
    include_character: bool = False,
    captions_enabled: bool = True,
    video_template: str = "podcast",
    is_monologue: bool = False,
    ai_endpoint: str | None = None,
    ai_model: str | None = None,
    provider_id: int | None = None,
    log: LogCallback | None = None,
) -> str:
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    # Mirror to the task log (pipeline.log + LogPanel) when available, else the
    # module logger (start.sh log). Prefer the callback to avoid double-logging.
    emit = lambda message: log(message) if log else logger.info(message)

    # --- 1. Storyboard -----------------------------------------------------
    audio_duration = sb.get_audio_duration(audio_path)
    word_transcript, transcription = await av_sync.ensure_word_transcript(
        audio_path,
        output_dir_path,
        required=config.AV_SYNC_REQUIRED,
        log=emit,
    )
    boundaries = _detect_silence_boundaries(audio_path, log)
    emit(f"Audio duration: {audio_duration:.1f}s; {len(boundaries)} silence boundaries detected")

    summary_path = output_dir_path / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else None

    board = sb.build_storyboard(
        script_path=script_path,
        audio_duration=audio_duration,
        title=title,
        silence_boundaries=boundaries,
        word_transcript=word_transcript,
        minimum_word_coverage=config.AV_SYNC_MIN_WORD_COVERAGE_PERCENT / 100,
        maximum_boundary_uncertainty=config.AV_SYNC_MAX_BOUNDARY_UNCERTAINTY_MS / 1000,
        is_monologue=is_monologue,
        summary=summary,
        log=emit,
    )
    board["alignment"]["transcription_backend"] = transcription.get("backend", "unavailable")
    sb.write_storyboard(output_dir_path, board)
    if config.AV_SYNC_REQUIRED and not board["alignment"].get("passed"):
        reasons = "; ".join(board["alignment"].get("failure_reasons") or ["unknown failure"])
        raise RuntimeError(f"A/V sync quality gate rejected the storyboard: {reasons}")
    emit(
        "A/V sync timing: "
        f"{float(board['alignment'].get('word_coverage') or 0):.1%} word coverage; "
        f"max boundary uncertainty "
        f"{float(board['alignment'].get('max_boundary_uncertainty_seconds') or 0):.2f}s"
    )
    emit(f"Composition {board['total_duration']:.1f}s over {board['scene_count']} scenes")

    # --- 2. Direction ------------------------------------------------------
    plans = await visual_plan.plan_scene_visuals(
        board,
        ai_endpoint=ai_endpoint,
        ai_model=ai_model,
        provider_id=provider_id,
        log=emit,
    )

    manifest = footage.read_manifest(output_dir_path)
    attached = visual_plan.attach_footage(plans, board, manifest, output_dir_path)
    if attached:
        emit(f"Footage: {attached} manifest clip(s) placed as full-bleed scenes")

    scene_plans = list(plans)
    visual_grounding = visual_plan.visual_grounding_report(scene_plans, board)
    quality_report = {
        "passed": bool(board["alignment"].get("passed")) and visual_grounding["passed"],
        "timing": board["alignment"],
        "visual_grounding": visual_grounding,
        "multimodal": {"status": "pending", "passed": False},
    }
    (output_dir_path / "av_sync_report.json").write_text(
        json.dumps(quality_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if not visual_grounding["passed"]:
        failed = [scene["id"] for scene in visual_grounding["scenes"] if not scene["grounded"]]
        raise RuntimeError(
            "A/V sync quality gate rejected ungrounded visual plans: " + ", ".join(failed)
        )
    emit(
        f"A/V sync semantics: {visual_grounding['grounded_scenes']}/"
        f"{visual_grounding['scene_count']} scene plans grounded in their narration"
    )

    plans = scene_plans + [visual_plan.outro_plan(board)]
    (output_dir_path / "visual_plan.json").write_text(
        json.dumps(plans, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # --- 3. Authoring ------------------------------------------------------
    mounts = _mount_list(board)
    theme = scene_kit.resolve_theme(video_template)
    kit_plans = _kit_plans(plans, mounts, theme)

    assembler.vendor_assets(output_dir_path, include_lottie=include_character)
    character_src = assembler.stage_character(output_dir_path) if include_character else None
    assembler.write_scene_files(output_dir_path, kit_plans)
    emit(f"Wrote {len(kit_plans)} deterministic scene draft(s)")

    # The crews talk to the same provider as the digest stage, so they need its
    # resolved credentials — not just the task's overrides.
    from backend.pipeline.digester import _resolve_provider

    try:
        endpoint, model, api_key = await _resolve_provider(provider_id, ai_endpoint, ai_model)
    except Exception as exc:  # noqa: BLE001 - the deterministic scenes still stand
        emit(f"Provider lookup failed ({exc}); rendering the deterministic scenes")
        endpoint, model, api_key = None, None, None

    if config.DIRECTOR_ENABLED and scene_plans and model:
        budget = scene_plans[: config.DIRECTOR_MAX_SCENES] if config.DIRECTOR_MAX_SCENES else scene_plans
        try:
            outcome = await director.direct_scenes(
                output_dir_path,
                board,
                budget,
                kit_plans,
                model=model,
                endpoint=endpoint,
                api_key=api_key,
                log=emit,
            )
            (output_dir_path / "director_report.json").write_text(
                json.dumps(
                    {
                        "authored": outcome.authored,
                        "rejected": outcome.rejected,
                        "failures": outcome.failures,
                        "agents_run": outcome.agents_run,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001 - the deterministic scenes still stand
            emit(f"Director unavailable ({exc}); rendering the deterministic scenes")
            outcome = director.DirectorOutcome()
    else:
        emit("Director agents disabled; rendering the deterministic scenes")
        outcome = director.DirectorOutcome()

    # --- 4. Assembly -------------------------------------------------------
    audio_src = _project_relative(audio_path, output_dir_path)
    spine = assembler.build_spine(
        board,
        audio_src=audio_src,
        mounts=mounts,
        theme=theme,
        character_src=character_src,
        captions_enabled=captions_enabled,
    )
    composition_path = assembler.write_spine(output_dir_path, spine)
    emit(f"Composition written to {composition_path}")

    clean, lint_output = assembler.lint_project(output_dir_path, emit)
    if not clean and outcome.authored:
        # Only agent-authored files can be wrong here — the spine and the kit are
        # generated. Revert exactly the scenes lint named, then re-check.
        blamed = director.scenes_named_in(lint_output, outcome.authored)
        if blamed:
            director.revert_scenes(output_dir_path, blamed, kit_plans)
            emit(f"Lint: reverted {len(blamed)} agent scene(s) to the deterministic draft")
            clean, lint_output = assembler.lint_project(output_dir_path, emit)
    if not clean:
        # Last resort: every scene goes back to the known-good renderer rather
        # than shipping a composition the runtime may refuse to drive.
        director.revert_scenes(output_dir_path, [plan.id for plan in kit_plans], kit_plans)
        emit("Lint still failing; reverted every scene to the deterministic kit")
        outcome.authored.clear()
        clean, lint_output = assembler.lint_project(output_dir_path, emit)
        if not clean:
            raise RuntimeError(f"Composition failed HyperFrames lint:\n{lint_output[-1200:]}")

    # Layout check. Overlapping text and content spilling out of a card do not
    # stop a render — they just make it look broken — so this loop is advisory:
    # give the agents HyperFrames' own findings, and revert anything still
    # failing afterwards rather than blocking the video.
    if config.INSPECT_ENABLED:
        layout_ok, findings = assembler.inspect_project(output_dir_path, emit)
        blamed = director.scenes_named_in_findings(findings, outcome.authored, mounts)
        if not layout_ok and blamed:
            try:
                await director.repair_scenes(
                    output_dir_path,
                    blamed,
                    findings,
                    model=model,
                    endpoint=endpoint,
                    api_key=api_key,
                    log=emit,
                )
            except Exception as exc:  # noqa: BLE001 - layout polish is never fatal
                emit(f"Director repair pass unavailable ({exc})")
            clean, lint_output = assembler.lint_project(output_dir_path, emit)
            if not clean:
                director.revert_scenes(output_dir_path, blamed, kit_plans)
                emit("Repair broke lint; reverted those scenes to the deterministic draft")
            else:
                layout_ok, findings = assembler.inspect_project(output_dir_path, emit)
                still_bad = director.scenes_named_in_findings(findings, blamed, mounts)
                if still_bad:
                    director.revert_scenes(output_dir_path, still_bad, kit_plans)
                    emit(
                        f"Layout still failing for {len(still_bad)} scene(s); "
                        "reverted them to the deterministic draft"
                    )

    # --- 5. Render ---------------------------------------------------------
    video_path = output_dir_path / "video.mp4"
    total_frames = max(1, round(float(board["total_duration"]) * config.RENDER_FPS))
    render_timeout = max(RENDER_TIMEOUT_FLOOR, int(300 + total_frames * RENDER_SECONDS_PER_FRAME))
    emit(
        f"Rendering ~{total_frames} frames "
        f"({board['total_duration']:.0f}s @ {config.RENDER_FPS}fps, {config.RENDER_QUALITY}, "
        f"{config.RENDER_WORKERS} worker(s)); render timeout {render_timeout}s, "
        f"stall timeout {RENDER_STALL_TIMEOUT}s"
    )

    render_command = _build_render_command(output_dir_path, video_path)
    returncode, output = await stream_subprocess(
        name="HyperFrames render",
        command=render_command,
        logger=logger,
        log=log,
        cwd=config.HYPERFRAME_DIR,
        timeout=render_timeout,
        stall_timeout=RENDER_STALL_TIMEOUT,
    )

    if returncode != 0:
        raise RuntimeError(f"Video render failed (exit {returncode}): {output[-500:]}")

    if not video_path.exists():
        raise RuntimeError("Video render produced no output file")

    emit(f"Video rendered: {video_path} ({video_path.stat().st_size / 1024 / 1024:.1f} MB)")
    if config.AV_SYNC_GEMINI_REVIEW_ENABLED:
        gemini_review = await multimodal_review.review_video(
            video_path,
            board,
            output_dir_path,
            log=emit,
        )
    else:
        gemini_review = {
            "status": "disabled",
            "passed": False,
            "analyzer": "gemini-web-via-opencli",
            "scenes": [],
        }
    quality_report["multimodal"] = gemini_review
    quality_report["passed"] = (
        bool(board["alignment"].get("passed"))
        and visual_grounding["passed"]
        and (
            gemini_review["passed"]
            or not config.AV_SYNC_GEMINI_REVIEW_REQUIRED
        )
    )
    (output_dir_path / "av_sync_report.json").write_text(
        json.dumps(quality_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if (
        config.AV_SYNC_GEMINI_REVIEW_ENABLED
        and config.AV_SYNC_GEMINI_REVIEW_REQUIRED
        and not gemini_review["passed"]
    ):
        failed = ", ".join(gemini_review.get("failed_scene_ids") or [])
        errors = "; ".join(gemini_review.get("errors") or [])
        detail = failed or errors or "review thresholds were not met"
        raise RuntimeError(f"Gemini A/V match gate rejected the rendered video: {detail}")
    if gemini_review["passed"]:
        emit(
            "Gemini A/V match: "
            f"{gemini_review['reviewed_scenes']}/{gemini_review['scene_count']} scenes passed; "
            f"average {gemini_review['average_score']:.1f}/100"
        )
    return str(video_path)
