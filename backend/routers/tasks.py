import shutil
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from backend import database as db
from backend.models import TaskCreate, TaskConfig, TaskListResponse, TaskResponse, SourceType
from backend.config import UPLOADS_DIR, OUTPUTS_DIR

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("", response_model=TaskResponse)
async def create_task(
    source_type: str = Form(...),
    source_url: str | None = Form(None),
    config_json: str = Form("{}"),
    file: UploadFile | None = File(None),
):
    st = SourceType(source_type)
    import json
    config = TaskConfig(**json.loads(config_json))

    upload_path = None
    if file and st in (SourceType.EPUB, SourceType.PDF):
        upload_dir = UPLOADS_DIR / f"{st.value}"
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
    )
    return task


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


@router.get("/{task_id}/video")
async def download_video(task_id: str):
    task = await db.get_task(task_id)
    if not task or not task.video_path:
        raise HTTPException(404, "Video not available")
    path = Path(task.video_path)
    if not path.exists():
        raise HTTPException(404, "Video file missing")
    return FileResponse(path, media_type="video/mp4", filename=f"{task_id}.mp4")


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
