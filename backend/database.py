import json
import aiosqlite
from datetime import datetime, timezone
from backend.config import DB_PATH
from backend.models import TaskConfig, TaskResponse, TaskStatus, new_task_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_url TEXT,
    source_title TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    error_message TEXT,
    config_json TEXT NOT NULL,
    output_dir TEXT,
    script_path TEXT,
    audio_path TEXT,
    video_path TEXT,
    duration_seconds REAL
);
"""


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def init_db():
    db = await get_db()
    await db.executescript(SCHEMA)
    await db.close()


def _row_to_response(row: aiosqlite.Row) -> TaskResponse:
    return TaskResponse(
        id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        source_type=row["source_type"],
        source_url=row["source_url"],
        source_title=row["source_title"],
        status=row["status"],
        error_message=row["error_message"],
        config=TaskConfig(**json.loads(row["config_json"])),
        output_dir=row["output_dir"],
        script_path=row["script_path"],
        audio_path=row["audio_path"],
        video_path=row["video_path"],
        duration_seconds=row["duration_seconds"],
    )


async def create_task(source_type: str, source_url: str | None, config: TaskConfig, upload_path: str | None = None) -> TaskResponse:
    task_id = new_task_id()
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()
    effective_url = source_url or upload_path
    await db.execute(
        """INSERT INTO tasks (id, created_at, updated_at, source_type, source_url, status, config_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (task_id, now, now, source_type, effective_url, TaskStatus.QUEUED.value, config.model_dump_json()),
    )
    await db.commit()
    row = await db.execute_fetchall("SELECT * FROM tasks WHERE id = ?", (task_id,))
    await db.close()
    return _row_to_response(row[0])


async def get_task(task_id: str) -> TaskResponse | None:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM tasks WHERE id = ?", (task_id,))
    await db.close()
    return _row_to_response(rows[0]) if rows else None


async def list_tasks() -> list[TaskResponse]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM tasks ORDER BY created_at DESC")
    await db.close()
    return [_row_to_response(r) for r in rows]


async def update_task(task_id: str, **kwargs):
    sets = []
    vals = []
    for k, v in kwargs.items():
        sets.append(f"{k} = ?")
        vals.append(v)
    sets.append("updated_at = ?")
    vals.append(datetime.now(timezone.utc).isoformat())
    vals.append(task_id)
    db = await get_db()
    await db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", vals)
    await db.commit()
    await db.close()


async def delete_task(task_id: str):
    db = await get_db()
    await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    await db.commit()
    await db.close()


async def get_next_queued_task() -> TaskResponse | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM tasks WHERE status = ? ORDER BY created_at ASC LIMIT 1",
        (TaskStatus.QUEUED.value,),
    )
    await db.close()
    return _row_to_response(rows[0]) if rows else None
