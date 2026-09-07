import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from backend import config
from backend.database import update_task
from backend.models import TaskResponse, TaskStatus, ScriptFormat
from backend.pipeline.extractors.youtube import extract_youtube
from backend.pipeline.extractors.epub import extract_epub
from backend.pipeline.extractors.epub_curated import extract_epub_curated
from backend.pipeline.extractors.pdf import extract_pdf
from backend.pipeline.extractors.topic import extract_topic
from backend.pipeline.digester import summarize, generate_script
from backend.pipeline.tts import generate_tts
from backend.pipeline.composer import compose_video
from backend.pipeline.footage import acquire_footage
from backend.pipeline.thumbnail import generate_thumbnail
from backend.pipeline.title import generate_title

logger = logging.getLogger(__name__)

LogCallback = Callable[[str], None]

EXTRACTORS = {
    "youtube": extract_youtube,
    "epub": extract_epub,
    "pdf": extract_pdf,
}


def pipeline_log_path(task_dir: Path) -> Path:
    return task_dir / "logs" / "pipeline.log"


def append_pipeline_log(task_dir: Path, message: str):
    log_path = pipeline_log_path(task_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(message.rstrip() + "\n")


def format_pipeline_log(task_id: str, message: str, timestamp: datetime | None = None) -> str:
    event_time = timestamp or datetime.now().astimezone()
    return f"[{event_time.strftime('%Y%m%d-%H:%M:%S')}] [{task_id}] {message}"


def emit_pipeline_log(task_id: str, task_dir: Path, message: str, log: LogCallback | None = None):
    formatted = format_pipeline_log(task_id, message)
    append_pipeline_log(task_dir, formatted)
    logger.info(formatted)
    if log:
        log(formatted)


async def _acquire_task_footage(
    task: TaskResponse,
    task_dir: Path,
    task_log: LogCallback,
    *,
    title: str | None = None,
    supplied_queries: list[str] | None = None,
):
    script_path = Path(task.script_path or task_dir / "script.txt")
    return await acquire_footage(
        media_provider=task.config.footage_provider,
        task_id=task.id,
        task_dir=task_dir,
        title=title or task.generated_title or task.source_title or task.id,
        script_path=script_path,
        clip_count=task.config.footage_clip_count,
        orientation=task.config.video_orientation,
        license_policy=task.config.footage_license_policy,
        provider_id=task.config.provider_id,
        ai_endpoint=task.config.ai_endpoint,
        ai_model=task.config.ai_model,
        supplied_queries=supplied_queries,
        log=task_log,
    )


async def _generate_task_thumbnail(
    task: TaskResponse,
    task_dir: Path,
    task_log: LogCallback,
    *,
    title: str,
):
    script_path = Path(task.script_path or task_dir / "script.txt")
    artifact = await generate_thumbnail(
        task_id=task.id,
        task_dir=task_dir,
        title=title,
        script_path=script_path,
        provider_id=task.config.provider_id,
        ai_endpoint=task.config.ai_endpoint,
        ai_model=task.config.ai_model,
        log=task_log,
    )
    await update_task(task.id, thumbnail_path=artifact.image_path)
    task.thumbnail_path = artifact.image_path
    return artifact


async def _generate_task_title(
    task: TaskResponse,
    task_dir: Path,
    task_log: LogCallback,
    *,
    source_title: str,
    summary: dict | None = None,
):
    script_path = Path(task.script_path or task_dir / "script.txt")
    script = script_path.read_text(encoding="utf-8").strip()
    if not script:
        raise RuntimeError(f"Title source script is empty at {script_path}")
    artifact = await generate_title(
        task_id=task.id,
        task_dir=task_dir,
        source_title=source_title,
        summary=summary,
        script=script,
        provider_id=task.config.provider_id,
        ai_endpoint=task.config.ai_endpoint,
        ai_model=task.config.ai_model,
        log=task_log,
    )
    await update_task(task.id, generated_title=artifact.title)
    task.generated_title = artifact.title
    return artifact


async def _after_audio(
    task: TaskResponse,
    *,
    script_path: str,
    audio_path: str,
    task_log: LogCallback,
    log: LogCallback | None,
    prefix: str = "",
):
    """Either pause for audio review or, when the task opted out, continue
    straight into the compose stage."""
    if not task.config.auto_render:
        await update_task(task.id, status=TaskStatus.AWAITING_REVIEW.value)
        task_log(f"{prefix}Audio ready, awaiting review before video render")
        return

    task_log(f"{prefix}Audio ready, audio review skipped — rendering video now")
    # run_compose reads the paths off the task object, which still holds the
    # values from before this run wrote them.
    task.script_path = script_path
    task.audio_path = audio_path
    await run_compose(task, log=log)


async def run_pipeline(task: TaskResponse, log: LogCallback | None = None):
    task_dir = config.OUTPUTS_DIR / task.id
    task_dir.mkdir(parents=True, exist_ok=True)
    task_log = lambda message: emit_pipeline_log(task.id, task_dir, message, log)

    await update_task(task.id, output_dir=str(task_dir))

    ai_endpoint = task.config.ai_endpoint
    ai_model = task.config.ai_model
    provider_id = task.config.provider_id

    # Stage 1: Extract
    task_log(f"Stage 1: Extracting from {task.source_type}")
    await update_task(task.id, status=TaskStatus.EXTRACTING.value)

    if task.source_type == "topic":
        content = await extract_topic(task.source_title, task.source_url, log=task_log)
    elif task.source_type == "epub" and task.config.processing_mode == "curated_highlights":
        content = await extract_epub_curated(task.source_url, str(task_dir / "isla_reader"), log=task_log)
    elif task.source_type == "youtube":
        content = await extract_youtube(task.source_url, log=task_log)
    else:
        extractor = EXTRACTORS.get(task.source_type)
        if not extractor:
            raise ValueError(f"Unsupported source type: {task.source_type}")
        content = await extractor(task.source_url, log=task_log)
    await update_task(task.id, source_title=content.title)
    task_log(f"Extracted '{content.title}' ({len(content.text.split())} words)")

    (task_dir / "extracted.json").write_text(
        json.dumps({"title": content.title, "metadata": content.metadata, "text_length": len(content.text)}, indent=2)
    )

    # Stage 2: Digest
    task_log("Stage 2: Digesting content")
    await update_task(task.id, status=TaskStatus.DIGESTING.value)

    summary = await summarize(content, ai_endpoint, ai_model, provider_id, log=task_log)
    (task_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    script = await generate_script(
        summary,
        task.config.target_duration_minutes,
        script_format=task.config.script_format.value,
        ai_endpoint=ai_endpoint,
        ai_model=ai_model,
        provider_id=provider_id,
        closing_remarks=task.config.closing_remarks,
        log=task_log,
    )
    script_path = str(task_dir / "script.txt")
    Path(script_path).write_text(script)
    await update_task(task.id, script_path=script_path)
    task.script_path = script_path
    task_log(f"Script saved to {script_path}")

    # Stage 3: a dedicated Agent SDK session turns the completed narration into
    # the publication title. It is persisted separately from the extracted
    # source title and becomes the title consumed by every downstream stage.
    task_log("Stage 3: Generating publication title with independent Agent")
    await update_task(task.id, status=TaskStatus.TITLING.value)
    title_artifact = await _generate_task_title(
        task,
        task_dir,
        task_log,
        source_title=content.title,
        summary=summary,
    )
    publication_title = title_artifact.title

    # Stage 4: use the final narration (the exact TTS input) to derive one
    # cover-art prompt, then generate/download the image through the signed-in
    # ChatGPT web session. Cover art is an enhancement, so a web/provider
    # outage is recorded in thumbnail/manifest.json but never discards a valid
    # script or forces a costly TTS retry.
    if task.config.thumbnail_enabled:
        task_log("Stage 4: Generating script-driven viral thumbnail")
        try:
            await _generate_task_thumbnail(
                task,
                task_dir,
                task_log,
                title=publication_title,
            )
        except Exception as exc:
            task_log(f"Thumbnail generation could not complete; continuing without cover art: {exc}")

    # Stage 5: AI-planned public B-roll. Footage is a production enhancement,
    # not a reason to lose an otherwise valid narration, so provider/network
    # failures are logged and the audio pipeline continues.
    if task.config.footage_enabled:
        task_log("Stage 5: Scouting open-license public footage")
        await update_task(task.id, status=TaskStatus.SOURCING.value)
        try:
            await _acquire_task_footage(
                task,
                task_dir,
                task_log,
                title=publication_title,
            )
        except Exception as exc:
            task_log(f"Public footage scout could not complete; continuing without B-roll: {exc}")

    # Stage 6: TTS
    task_log(f"Stage 6: Generating TTS audio with {task.config.tts_model}")
    await update_task(task.id, status=TaskStatus.TTS.value)

    voices = [task.config.voice_1]
    if task.config.script_format == ScriptFormat.DIALOGUE:
        voices.append(task.config.voice_2)

    audio_dir = str(task_dir / "audio")
    audio_path = await generate_tts(script_path, audio_dir, voices, task.config.tts_model, log=task_log)
    await update_task(task.id, audio_path=audio_path)

    # Pause for audio review before the (expensive) video composition. The user
    # previews the audio and triggers the compose stage via the render endpoint,
    # unless the task was created with the review step turned off.
    await _after_audio(
        task,
        script_path=script_path,
        audio_path=audio_path,
        task_log=task_log,
        log=log,
    )


async def run_regenerate(task: TaskResponse, log: LogCallback | None = None):
    """Re-run only the TTS and compose stages from an existing (possibly
    edited) script, skipping extraction and digestion."""
    task_dir = config.OUTPUTS_DIR / task.id
    task_dir.mkdir(parents=True, exist_ok=True)
    task_log = lambda message: emit_pipeline_log(task.id, task_dir, message, log)

    script_path = task.script_path or str(task_dir / "script.txt")
    if not Path(script_path).exists():
        raise FileNotFoundError(f"No script to regenerate from at {script_path}")

    summary_path = task_dir / "summary.json"
    summary = None
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            task_log("Regenerate: Existing summary is unreadable; title Agent will use the script")

    task_log("Regenerate: Updating publication title with independent Agent")
    await update_task(task.id, status=TaskStatus.TITLING.value, error_message=None)
    try:
        title_artifact = await _generate_task_title(
            task,
            task_dir,
            task_log,
            source_title=task.source_title or task.id,
            summary=summary,
        )
        title = title_artifact.title
    except Exception as exc:
        title = task.generated_title or task.source_title or task.id
        task_log(
            "Regenerate: Title update failed; retaining prior title "
            f"'{title}': {exc}"
        )

    # Keep cover art aligned with an edited script. This still precedes the TTS
    # subprocess, so a thumbnail failure consumes no model-synthesis memory.
    if task.config.thumbnail_enabled:
        task_log("Regenerate: Updating viral thumbnail from edited script")
        try:
            await _generate_task_thumbnail(
                task,
                task_dir,
                task_log,
                title=title,
            )
        except Exception as exc:
            task_log(f"Regenerate: Thumbnail update failed; retaining prior cover: {exc}")

    # Stage 6: TTS
    task_log(f"Regenerate: Generating TTS audio with {task.config.tts_model}")
    await update_task(task.id, status=TaskStatus.TTS.value, error_message=None)

    voices = [task.config.voice_1]
    if task.config.script_format == ScriptFormat.DIALOGUE:
        voices.append(task.config.voice_2)

    audio_dir = str(task_dir / "audio")
    audio_path = await generate_tts(script_path, audio_dir, voices, task.config.tts_model, log=task_log)
    await update_task(task.id, audio_path=audio_path)

    # Pause for audio review, same as the full pipeline.
    await _after_audio(
        task,
        script_path=script_path,
        audio_path=audio_path,
        task_log=task_log,
        log=log,
        prefix="Regenerate: ",
    )


async def run_footage_acquisition(
    task: TaskResponse,
    *,
    resume_status: str,
    supplied_queries: list[str] | None = None,
    log: LogCallback | None = None,
):
    """Run or retry only the public-footage scout from an existing script."""
    task_dir = config.OUTPUTS_DIR / task.id
    task_dir.mkdir(parents=True, exist_ok=True)
    task_log = lambda message: emit_pipeline_log(task.id, task_dir, message, log)

    script_path = Path(task.script_path or task_dir / "script.txt")
    if not script_path.exists():
        raise FileNotFoundError(f"No script to scout from at {script_path}")

    task_log("Footage retry: planning and acquiring open-license B-roll")
    await update_task(task.id, status=TaskStatus.SOURCING.value, error_message=None)
    try:
        await _acquire_task_footage(
            task,
            task_dir,
            task_log,
            supplied_queries=supplied_queries,
        )
    finally:
        await update_task(task.id, status=resume_status)


async def run_compose(task: TaskResponse, log: LogCallback | None = None):
    """Resume from the compose stage using an already-generated script and
    audio. Triggered after the user has reviewed the audio preview."""
    task_dir = config.OUTPUTS_DIR / task.id
    task_dir.mkdir(parents=True, exist_ok=True)
    task_log = lambda message: emit_pipeline_log(task.id, task_dir, message, log)

    script_path = task.script_path or str(task_dir / "script.txt")
    audio_path = task.audio_path
    if not audio_path or not Path(audio_path).exists():
        raise FileNotFoundError(f"No audio to compose from for task {task.id}")
    if not Path(script_path).exists():
        raise FileNotFoundError(f"No script to compose from at {script_path}")

    title = task.generated_title or task.source_title or task.id

    task_log(f"Render: Composing video with {task.config.video_template} template")
    await update_task(task.id, status=TaskStatus.COMPOSING.value, error_message=None)

    video_path = await compose_video(
        script_path=script_path,
        audio_path=audio_path,
        output_dir=str(task_dir),
        title=title,
        include_character=task.config.include_character,
        captions_enabled=task.config.captions_enabled,
        video_template=task.config.video_template,
        video_orientation=task.config.video_orientation,
        opening_style=task.config.opening_style,
        collage_broll_enabled=task.config.collage_broll_enabled,
        collage_broll_count=task.config.collage_broll_count,
        is_monologue=task.config.script_format == ScriptFormat.MONOLOGUE,
        # The compose stage now runs its own AI calls (art direction, then the
        # Claude Agent SDK authoring crews), so it needs the same provider the
        # digest stage used.
        ai_endpoint=task.config.ai_endpoint,
        ai_model=task.config.ai_model,
        provider_id=task.config.provider_id,
        tts_model=task.config.tts_model,
        log=task_log,
    )
    await update_task(task.id, video_path=video_path, status=TaskStatus.COMPLETE.value)

    task_log(f"Render complete: {video_path}")


async def run_tts_resume(task: TaskResponse, log: LogCallback | None = None):
    """Resume only TTS and its normal post-audio path from a saved script.

    Unlike ``run_regenerate``, this recovery path deliberately preserves the
    existing publication title and thumbnail. It is intended for a failed TTS
    run whose upstream editorial artifacts are already complete.
    """
    task_dir = Path(task.output_dir) if task.output_dir else config.OUTPUTS_DIR / task.id
    task_dir.mkdir(parents=True, exist_ok=True)
    task_log = lambda message: emit_pipeline_log(task.id, task_dir, message, log)

    script_path = task.script_path or str(task_dir / "script.txt")
    if not Path(script_path).is_file():
        raise FileNotFoundError(f"No script to resume TTS from at {script_path}")

    task_log(f"TTS resume: Generating audio with {task.config.tts_model} from the saved script")
    await update_task(task.id, status=TaskStatus.TTS.value, error_message=None)

    voices = [task.config.voice_1]
    if task.config.script_format == ScriptFormat.DIALOGUE:
        voices.append(task.config.voice_2)

    audio_dir = str(task_dir / "audio")
    audio_path = await generate_tts(
        script_path,
        audio_dir,
        voices,
        task.config.tts_model,
        log=task_log,
    )
    await update_task(task.id, audio_path=audio_path)

    await _after_audio(
        task,
        script_path=script_path,
        audio_path=audio_path,
        task_log=task_log,
        log=log,
        prefix="TTS resume: ",
    )
