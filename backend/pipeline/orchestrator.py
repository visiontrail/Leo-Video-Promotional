import json
import logging
from pathlib import Path
from backend import config
from backend.database import update_task
from backend.models import TaskResponse, TaskStatus
from backend.pipeline.extractors.youtube import extract_youtube
from backend.pipeline.extractors.epub import extract_epub
from backend.pipeline.extractors.pdf import extract_pdf
from backend.pipeline.digester import summarize, generate_script
from backend.pipeline.tts import generate_tts
from backend.pipeline.composer import compose_video

logger = logging.getLogger(__name__)

EXTRACTORS = {
    "youtube": extract_youtube,
    "epub": extract_epub,
    "pdf": extract_pdf,
}


async def run_pipeline(task: TaskResponse):
    task_dir = config.OUTPUTS_DIR / task.id
    task_dir.mkdir(parents=True, exist_ok=True)

    await update_task(task.id, output_dir=str(task_dir))

    ai_endpoint = task.config.ai_endpoint
    ai_model = task.config.ai_model
    provider_id = task.config.provider_id

    # Stage 1: Extract
    logger.info(f"[{task.id}] Stage 1: Extracting from {task.source_type}")
    await update_task(task.id, status=TaskStatus.EXTRACTING.value)

    extractor = EXTRACTORS.get(task.source_type)
    if not extractor:
        raise ValueError(f"Unsupported source type: {task.source_type}")

    content = await extractor(task.source_url)
    await update_task(task.id, source_title=content.title)

    (task_dir / "extracted.json").write_text(
        json.dumps({"title": content.title, "metadata": content.metadata, "text_length": len(content.text)}, indent=2)
    )

    # Stage 2: Digest
    logger.info(f"[{task.id}] Stage 2: Digesting content")
    await update_task(task.id, status=TaskStatus.DIGESTING.value)

    summary = await summarize(content, ai_endpoint, ai_model, provider_id)
    (task_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    script = await generate_script(summary, task.config.target_duration_minutes, ai_endpoint, ai_model, provider_id)
    script_path = str(task_dir / "script.txt")
    Path(script_path).write_text(script)
    await update_task(task.id, script_path=script_path)
    logger.info(f"[{task.id}] Script generated: {len(script.split())} words")

    # Stage 3: TTS
    logger.info(f"[{task.id}] Stage 3: Generating TTS audio")
    await update_task(task.id, status=TaskStatus.TTS.value)

    voices = [task.config.voice_1]
    if task.config.speaker_count >= 2:
        voices.append(task.config.voice_2)

    audio_dir = str(task_dir / "audio")
    audio_path = await generate_tts(script_path, audio_dir, voices)
    await update_task(task.id, audio_path=audio_path)

    # Stage 4: Compose video
    logger.info(f"[{task.id}] Stage 4: Composing video")
    await update_task(task.id, status=TaskStatus.COMPOSING.value)

    video_path = await compose_video(
        script_path=script_path,
        audio_path=audio_path,
        output_dir=str(task_dir),
        title=content.title,
        include_character=task.config.include_character,
    )
    await update_task(task.id, video_path=video_path, status=TaskStatus.COMPLETE.value)

    logger.info(f"[{task.id}] Pipeline complete: {video_path}")
