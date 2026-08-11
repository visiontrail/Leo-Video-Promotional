import asyncio
import json
import shutil
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse
from backend import database as db
from backend.models import (
    TaskCreate,
    TaskConfig,
    TaskListResponse,
    TaskResponse,
    SourceType,
    TaskStatus,
    ScriptUpdate,
    FootageAcquireRequest,
    TaskSchedule,
    normalize_schedule,
)
# Imported as a module, not by name: the Admin console rebinds these paths
# at runtime, and `config` is shadowed by a local TaskConfig below.
from backend import config as app_config
from backend.pipeline.footage import read_manifest
from backend.worker import is_task_logging_active, pipeline_log_file, subscribe_task_logs, unsubscribe_task_logs

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("", response_model=TaskResponse)
async def create_task(
    source_type: str = Form(...),
    source_url: str | None = Form(None),
    config_json: str = Form("{}"),
    scheduled_at: str | None = Form(None),
    file: UploadFile | None = File(None),
):
    st = SourceType(source_type)
    import json
    config = TaskConfig(**json.loads(config_json))

    try:
        start_at = normalize_schedule(scheduled_at)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    upload_path = None
    if file and st in (SourceType.EPUB, SourceType.PDF):
        upload_dir = app_config.UPLOADS_DIR / f"{st.value}"
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / file.filename
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)
        upload_path = str(dest)

    task = await db.create_task(
        source_type=st.value,
        source_url=source_url,
        config=config,
        upload_path=upload_path,
        scheduled_at=start_at,
    )
    return task


@router.post("/{task_id}/schedule", response_model=TaskResponse)
async def reschedule_task(task_id: str, body: TaskSchedule):
    """Move a queued task's start time, or clear it (null) to start now."""
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.status != TaskStatus.QUEUED:
        raise HTTPException(409, "Only a queued task's start time can be changed")

    try:
        start_at = normalize_schedule(body.scheduled_at)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    await db.update_task(task_id, scheduled_at=start_at)
    return await db.get_task(task_id)


@router.get("", response_model=TaskListResponse)
async def list_tasks():
    tasks = await db.list_tasks()
    return TaskListResponse(tasks=tasks)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@router.get("/{task_id}/footage")
async def get_task_footage(task_id: str):
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    out_dir = Path(task.output_dir) if task.output_dir else app_config.OUTPUTS_DIR / task_id
    manifest = read_manifest(out_dir)
    if manifest is not None:
        return manifest
    provider_id = task.config.footage_provider
    uses_web = provider_id in {"hybrid", "opencli_web"}
    provider = {
        "hybrid": "Hybrid: Wikimedia Commons + YouTube",
        "opencli_web": "YouTube",
    }.get(provider_id, "Wikimedia Commons")
    return {
        "task_id": task_id,
        "status": "not_started",
        "provider": provider,
        "provider_id": provider_id,
        "license_policy": "review_required" if uses_web else "open_only",
        "license_allowlist": (
            [] if provider_id == "opencli_web"
            else ["Public Domain", "CC0", "CC BY", "CC BY-SA"]
        ),
        "rights_review_required": uses_web,
        "publication_blockers": (
            ["Review reuse rights for every YouTube clip before publication"]
            if uses_web else []
        ),
        "requested_clip_count": task.config.footage_clip_count,
        "planner": "",
        "queries": [],
        "clips": [],
        "errors": [],
    }


@router.get("/{task_id}/footage/{clip_id}/file")
async def get_task_footage_file(task_id: str, clip_id: str):
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    out_dir = Path(task.output_dir) if task.output_dir else app_config.OUTPUTS_DIR / task_id
    manifest = read_manifest(out_dir)
    if manifest is None:
        raise HTTPException(404, "Footage manifest not available")
    clip = next((item for item in manifest.get("clips", []) if item.get("id") == clip_id), None)
    if clip is None:
        raise HTTPException(404, "Footage clip not found")

    path = (out_dir / str(clip.get("local_path") or "")).resolve()
    out_dir_resolved = out_dir.resolve()
    if path != out_dir_resolved and out_dir_resolved not in path.parents:
        raise HTTPException(400, "Invalid footage path")
    if not path.is_file():
        raise HTTPException(404, "Footage file missing")
    return FileResponse(
        path,
        media_type=str(clip.get("mime_type") or "video/webm"),
        filename=path.name,
    )


@router.post("/{task_id}/footage/acquire", response_model=TaskResponse)
async def acquire_task_footage(task_id: str, body: FootageAcquireRequest):
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.status not in (TaskStatus.AWAITING_REVIEW, TaskStatus.COMPLETE, TaskStatus.FAILED):
        raise HTTPException(409, "Task must be paused, complete, or failed before footage can be retried")
    if not task.script_path or not Path(task.script_path).exists():
        raise HTTPException(400, "No script available for footage planning")

    out_dir = Path(task.output_dir) if task.output_dir else app_config.OUTPUTS_DIR / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    marker = {
        "resume_status": task.status.value,
        "queries": body.queries,
    }
    (out_dir / ".footage").write_text(json.dumps(marker), encoding="utf-8")
    await db.update_task(task_id, status=TaskStatus.QUEUED.value, error_message=None)
    return await db.get_task(task_id)


@router.get("/{task_id}/video")
async def download_video(task_id: str):
    task = await db.get_task(task_id)
    if not task or not task.video_path:
        raise HTTPException(404, "Video not available")
    path = Path(task.video_path)
    if not path.exists():
        raise HTTPException(404, "Video file missing")
    return FileResponse(path, media_type="video/mp4", filename=f"{task_id}.mp4")


@router.get("/{task_id}/thumbnail")
async def download_thumbnail(task_id: str):
    task = await db.get_task(task_id)
    if not task or not task.thumbnail_path:
        raise HTTPException(404, "Thumbnail not available")
    path = Path(task.thumbnail_path)
    if not path.is_file():
        raise HTTPException(404, "Thumbnail file missing")
    media_type = {
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(path.suffix.lower(), "image/jpeg")
    return FileResponse(path, media_type=media_type, filename=f"{task_id}_thumbnail{path.suffix}")


@router.get("/{task_id}/thumbnail/prompt")
async def download_thumbnail_prompt(task_id: str):
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    out_dir = Path(task.output_dir) if task.output_dir else app_config.OUTPUTS_DIR / task_id
    path = out_dir / "thumbnail" / "prompt.txt"
    if not path.is_file():
        raise HTTPException(404, "Thumbnail prompt not available")
    return FileResponse(path, media_type="text/plain", filename=f"{task_id}_thumbnail_prompt.txt")


@router.get("/{task_id}/audio")
async def download_audio(task_id: str):
    task = await db.get_task(task_id)
    if not task or not task.audio_path:
        raise HTTPException(404, "Audio not available")
    path = Path(task.audio_path)
    if not path.exists():
        raise HTTPException(404, "Audio file missing")
    return FileResponse(path, media_type="audio/wav", filename=f"{task_id}.wav")


@router.get("/{task_id}/script")
async def get_script(task_id: str):
    task = await db.get_task(task_id)
    if not task or not task.script_path:
        raise HTTPException(404, "Script not available")
    path = Path(task.script_path)
    if not path.exists():
        raise HTTPException(404, "Script file missing")
    return FileResponse(path, media_type="text/plain", filename=f"{task_id}_script.txt")


@router.get("/{task_id}/logs/stream")
async def stream_task_logs(task_id: str):
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    async def event_stream():
        active = is_task_logging_active(task_id)
        queue = subscribe_task_logs(task_id) if active else None
        log_path = pipeline_log_file(task_id, task.output_dir)

        if log_path.exists():
            for line in log_path.read_text(encoding="utf-8").splitlines():
                yield {"event": "log", "data": line}

        if not active:
            refreshed = await db.get_task(task_id)
            status = refreshed.status.value if refreshed else task.status.value
            yield {"event": "status", "data": status}
            return

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
                    continue

                yield event
                if event.get("event") == "status":
                    return
        finally:
            if queue:
                unsubscribe_task_logs(task_id, queue)

    return EventSourceResponse(event_stream())


@router.put("/{task_id}/script")
async def update_script(task_id: str, body: ScriptUpdate):
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    out_dir = Path(task.output_dir) if task.output_dir else app_config.OUTPUTS_DIR / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = Path(task.script_path) if task.script_path else out_dir / "script.txt"
    path.write_text(body.content)

    if not task.script_path:
        await db.update_task(task_id, script_path=str(path))
    return {"ok": True}


@router.post("/{task_id}/regenerate", response_model=TaskResponse)
async def regenerate_task(task_id: str):
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.status in (
        TaskStatus.EXTRACTING,
        TaskStatus.DIGESTING,
        TaskStatus.TITLING,
        TaskStatus.SOURCING,
        TaskStatus.TTS,
        TaskStatus.COMPOSING,
    ):
        raise HTTPException(409, "Task is currently processing")
    if not task.script_path or not Path(task.script_path).exists():
        raise HTTPException(400, "No script available to regenerate from")

    out_dir = Path(task.output_dir) if task.output_dir else app_config.OUTPUTS_DIR / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ".regenerate").write_text("")

    await db.update_task(task_id, status=TaskStatus.QUEUED.value, error_message=None)
    return await db.get_task(task_id)


@router.post("/{task_id}/render", response_model=TaskResponse)
async def render_task(task_id: str):
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.status != TaskStatus.AWAITING_REVIEW:
        raise HTTPException(409, "Task is not awaiting review")
    if not task.audio_path or not Path(task.audio_path).exists():
        raise HTTPException(400, "No audio available to render from")

    out_dir = Path(task.output_dir) if task.output_dir else app_config.OUTPUTS_DIR / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ".render").write_text("")

    await db.update_task(task_id, status=TaskStatus.QUEUED.value, error_message=None)
    return await db.get_task(task_id)


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.output_dir:
        out = Path(task.output_dir)
        if out.exists():
            shutil.rmtree(out)
    await db.delete_task(task_id)
    return {"ok": True}
