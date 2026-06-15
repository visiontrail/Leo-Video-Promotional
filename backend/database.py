import json
import aiosqlite
from datetime import datetime, timezone
from backend import config
from backend.config import DB_PATH
from backend.models import TaskConfig, TaskResponse, TaskStatus, ProviderResponse, new_task_id

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

CREATE TABLE IF NOT EXISTS providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    api_key TEXT,
    model TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


def _mask_key(api_key: str | None) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:4]}...{api_key[-4:]}"


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def init_db():
    db = await get_db()
    await db.executescript(SCHEMA)
    await db.commit()
    # Seed the default provider from .env if no providers exist yet.
    rows = await db.execute_fetchall("SELECT COUNT(*) AS c FROM providers")
    if rows[0]["c"] == 0 and config.AI_ENDPOINT:
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO providers (name, endpoint, api_key, model, is_default, created_at)
               VALUES (?, ?, ?, ?, 1, ?)""",
            ("Default (.env)", config.AI_ENDPOINT, config.AI_API_KEY, config.AI_MODEL, now),
        )
        await db.commit()
    await db.close()


def _row_to_provider(row: aiosqlite.Row) -> ProviderResponse:
    return ProviderResponse(
        id=row["id"],
        name=row["name"],
        endpoint=row["endpoint"],
        api_key_masked=_mask_key(row["api_key"]),
        model=row["model"],
        is_default=bool(row["is_default"]),
        created_at=row["created_at"],
    )


async def list_providers() -> list[ProviderResponse]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM providers ORDER BY created_at ASC")
    await db.close()
    return [_row_to_provider(r) for r in rows]


async def get_provider(provider_id: int) -> ProviderResponse | None:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM providers WHERE id = ?", (provider_id,))
    await db.close()
    return _row_to_provider(rows[0]) if rows else None


async def get_provider_raw(provider_id: int | None) -> aiosqlite.Row | None:
    """Return the raw provider row (including unmasked api_key) by id, or the
    default provider when provider_id is None. Used by the pipeline."""
    db = await get_db()
    if provider_id is not None:
        rows = await db.execute_fetchall("SELECT * FROM providers WHERE id = ?", (provider_id,))
    else:
        rows = await db.execute_fetchall("SELECT * FROM providers WHERE is_default = 1 LIMIT 1")
    await db.close()
    return rows[0] if rows else None


async def create_provider(name: str, endpoint: str, api_key: str | None, model: str, is_default: bool) -> ProviderResponse:
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()
    if is_default:
        await db.execute("UPDATE providers SET is_default = 0")
    cursor = await db.execute(
        """INSERT INTO providers (name, endpoint, api_key, model, is_default, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, endpoint, api_key, model, 1 if is_default else 0, now),
    )
    await db.commit()
    rows = await db.execute_fetchall("SELECT * FROM providers WHERE id = ?", (cursor.lastrowid,))
    await db.close()
    return _row_to_provider(rows[0])


async def update_provider(provider_id: int, **kwargs) -> ProviderResponse | None:
    fields = {k: v for k, v in kwargs.items() if v is not None}
    db = await get_db()
    if fields.get("is_default"):
        await db.execute("UPDATE providers SET is_default = 0")
    if fields:
        sets = []
        vals = []
        for k, v in fields.items():
            if k == "is_default":
                v = 1 if v else 0
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(provider_id)
        await db.execute(f"UPDATE providers SET {', '.join(sets)} WHERE id = ?", vals)
        await db.commit()
    rows = await db.execute_fetchall("SELECT * FROM providers WHERE id = ?", (provider_id,))
    await db.close()
    return _row_to_provider(rows[0]) if rows else None


async def delete_provider(provider_id: int):
    db = await get_db()
    await db.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
    await db.commit()
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


async def reset_orphaned_tasks() -> int:
    """Mark tasks left mid-flight by a previous process as FAILED, returning the
    count reset.

    The worker only ever picks up QUEUED tasks, so a task interrupted while
    running — e.g. a uvicorn ``--reload`` restart (triggered by editing a
    backend file) that kills the worker and its render subprocess — would
    otherwise sit forever in COMPOSING/TTS/etc. with nothing driving it. This is
    exactly the "stuck in composing" symptom. AWAITING_REVIEW is a deliberate,
    stable pause (no process running) and is left untouched.
    """
    in_progress = (
        TaskStatus.EXTRACTING.value,
        TaskStatus.DIGESTING.value,
        TaskStatus.TTS.value,
        TaskStatus.COMPOSING.value,
    )
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()
    placeholders = ", ".join("?" for _ in in_progress)
    cursor = await db.execute(
        f"""UPDATE tasks SET status = ?, error_message = ?, updated_at = ?
            WHERE status IN ({placeholders})""",
        (TaskStatus.FAILED.value, "Interrupted by a server restart — please retry.", now, *in_progress),
    )
    await db.commit()
    await db.close()
    return cursor.rowcount


async def get_next_queued_task() -> TaskResponse | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM tasks WHERE status = ? ORDER BY created_at ASC LIMIT 1",
        (TaskStatus.QUEUED.value,),
    )
    await db.close()
    return _row_to_response(rows[0]) if rows else None
