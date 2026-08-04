import json
import aiosqlite
from datetime import datetime, timezone
from backend import config
from backend.models import (
    AccountAutomationCreate,
    AccountAutomationResponse,
    AccountRunResponse,
    AccountRunStatus,
    ProviderResponse,
    TaskConfig,
    TaskResponse,
    TaskStatus,
    new_account_id,
    new_task_id,
)

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
    scheduled_at TEXT,
    output_dir TEXT,
    script_path TEXT,
    audio_path TEXT,
    video_path TEXT,
    thumbnail_path TEXT,
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

CREATE TABLE IF NOT EXISTS account_automations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    name TEXT NOT NULL,
    feature_type TEXT NOT NULL,
    platform TEXT NOT NULL,
    account_handle TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    schedule_time TEXT NOT NULL,
    timezone TEXT NOT NULL,
    prompt_template TEXT NOT NULL,
    executor TEXT NOT NULL,
    opencode_model TEXT NOT NULL,
    next_run_at TEXT,
    last_run_at TEXT
);

CREATE TABLE IF NOT EXISTS account_runs (
    id TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL,
    automation_name TEXT NOT NULL,
    account_handle TEXT NOT NULL,
    platform TEXT NOT NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    scheduled_for TEXT,
    event_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    title TEXT,
    post_text TEXT,
    image_path TEXT,
    chatgpt_conversation_url TEXT,
    post_url TEXT,
    external_post_id TEXT,
    executor TEXT NOT NULL,
    content_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    log_text TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (automation_id) REFERENCES account_automations(id)
);

CREATE INDEX IF NOT EXISTS idx_account_automations_due
    ON account_automations(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_account_runs_queue
    ON account_runs(status, created_at);
"""


def _mask_key(api_key: str | None) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:4]}...{api_key[-4:]}"


async def get_db() -> aiosqlite.Connection:
    # config.DB_PATH is read per connection, not bound at import: the Admin
    # console can move the database (a restart re-runs init_db against it).
    db = await aiosqlite.connect(str(config.DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def _migrate_tasks(db: aiosqlite.Connection):
    """Add columns introduced after a database was first created.

    CREATE TABLE IF NOT EXISTS leaves an existing tasks table untouched, so
    columns added later have to be patched in explicitly.
    """
    rows = await db.execute_fetchall("PRAGMA table_info(tasks)")
    existing = {row["name"] for row in rows}
    if "scheduled_at" not in existing:
        await db.execute("ALTER TABLE tasks ADD COLUMN scheduled_at TEXT")
        await db.commit()
    if "thumbnail_path" not in existing:
        await db.execute("ALTER TABLE tasks ADD COLUMN thumbnail_path TEXT")
        await db.commit()


async def init_db():
    db = await get_db()
    await db.executescript(SCHEMA)
    await db.commit()
    await _migrate_tasks(db)
    # Seed the default provider from the AI engine settings if none exist yet.
    rows = await db.execute_fetchall("SELECT COUNT(*) AS c FROM providers")
    if rows[0]["c"] == 0 and config.AI_ENDPOINT:
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO providers (name, endpoint, api_key, model, is_default, created_at)
               VALUES (?, ?, ?, ?, 1, ?)""",
            ("Default (settings)", config.AI_ENDPOINT, config.AI_API_KEY, config.AI_MODEL, now),
        )
        await db.commit()
    automation_count = await db.execute_fetchall(
        "SELECT COUNT(*) AS c FROM account_automations"
    )
    await db.close()
    if automation_count[0]["c"] == 0:
        await create_account_automation(AccountAutomationCreate())


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
        scheduled_at=row["scheduled_at"],
        output_dir=row["output_dir"],
        script_path=row["script_path"],
        audio_path=row["audio_path"],
        video_path=row["video_path"],
        thumbnail_path=row["thumbnail_path"],
        duration_seconds=row["duration_seconds"],
    )


async def create_task(
    source_type: str,
    source_url: str | None,
    config: TaskConfig,
    upload_path: str | None = None,
    scheduled_at: str | None = None,
) -> TaskResponse:
    task_id = new_task_id()
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()
    effective_url = source_url or upload_path
    await db.execute(
        """INSERT INTO tasks (id, created_at, updated_at, source_type, source_url, status, config_json, scheduled_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (task_id, now, now, source_type, effective_url, TaskStatus.QUEUED.value, config.model_dump_json(), scheduled_at),
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
        TaskStatus.SOURCING.value,
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
    """Return the oldest queued task that is due to start.

    A task with a future ``scheduled_at`` stays invisible to the worker until
    that moment passes, which is how a run is parked in an idle window. Both
    columns hold UTC ISO-8601 timestamps, so the text comparison is chronological.
    """
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT * FROM tasks
           WHERE status = ? AND (scheduled_at IS NULL OR scheduled_at <= ?)
           ORDER BY COALESCE(scheduled_at, created_at) ASC LIMIT 1""",
        (TaskStatus.QUEUED.value, now),
    )
    await db.close()
    return _row_to_response(rows[0]) if rows else None


def _row_to_account_automation(row: aiosqlite.Row) -> AccountAutomationResponse:
    return AccountAutomationResponse(
        id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        name=row["name"],
        feature_type=row["feature_type"],
        platform=row["platform"],
        account_handle=row["account_handle"],
        enabled=bool(row["enabled"]),
        schedule_time=row["schedule_time"],
        timezone=row["timezone"],
        prompt_template=row["prompt_template"],
        executor=row["executor"],
        opencode_model=row["opencode_model"],
        next_run_at=row["next_run_at"],
        last_run_at=row["last_run_at"],
    )


def _row_to_account_run(row: aiosqlite.Row) -> AccountRunResponse:
    try:
        content = json.loads(row["content_json"] or "{}")
    except json.JSONDecodeError:
        content = {}
    return AccountRunResponse(
        id=row["id"],
        automation_id=row["automation_id"],
        automation_name=row["automation_name"],
        account_handle=row["account_handle"],
        platform=row["platform"],
        trigger=row["trigger"],
        status=row["status"],
        scheduled_for=row["scheduled_for"],
        event_date=row["event_date"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        title=row["title"],
        post_text=row["post_text"],
        image_path=row["image_path"],
        chatgpt_conversation_url=row["chatgpt_conversation_url"],
        post_url=row["post_url"],
        external_post_id=row["external_post_id"],
        executor=row["executor"],
        content=content if isinstance(content, dict) else {},
        error_message=row["error_message"],
        log_text=row["log_text"],
    )


async def create_account_automation(
    automation: AccountAutomationCreate,
) -> AccountAutomationResponse:
    from backend.account_ops.schedule import next_daily_run

    automation_id = new_account_id("auto")
    now = datetime.now(timezone.utc).isoformat()
    next_run_at = (
        next_daily_run(automation.schedule_time, automation.timezone)
        if automation.enabled
        else None
    )
    db = await get_db()
    await db.execute(
        """INSERT INTO account_automations (
               id, created_at, updated_at, name, feature_type, platform,
               account_handle, enabled, schedule_time, timezone, prompt_template,
               executor, opencode_model, next_run_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            automation_id,
            now,
            now,
            automation.name,
            automation.feature_type,
            automation.platform,
            automation.account_handle,
            int(automation.enabled),
            automation.schedule_time,
            automation.timezone,
            automation.prompt_template,
            automation.executor.value,
            automation.opencode_model,
            next_run_at,
        ),
    )
    await db.commit()
    rows = await db.execute_fetchall(
        "SELECT * FROM account_automations WHERE id = ?", (automation_id,)
    )
    await db.close()
    return _row_to_account_automation(rows[0])


async def list_account_automations() -> list[AccountAutomationResponse]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM account_automations ORDER BY created_at ASC"
    )
    await db.close()
    return [_row_to_account_automation(row) for row in rows]


async def get_account_automation(
    automation_id: str,
) -> AccountAutomationResponse | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM account_automations WHERE id = ?", (automation_id,)
    )
    await db.close()
    return _row_to_account_automation(rows[0]) if rows else None


async def update_account_automation(
    automation_id: str, values: dict[str, object]
) -> AccountAutomationResponse | None:
    from backend.account_ops.schedule import next_daily_run

    current = await get_account_automation(automation_id)
    if current is None:
        return None
    allowed = {
        "name",
        "account_handle",
        "enabled",
        "schedule_time",
        "timezone",
        "prompt_template",
        "executor",
        "opencode_model",
    }
    fields = {key: value for key, value in values.items() if key in allowed}
    if not fields:
        return current
    schedule_changed = bool({"enabled", "schedule_time", "timezone"} & fields.keys())
    if schedule_changed:
        enabled = bool(fields.get("enabled", current.enabled))
        schedule_time = str(fields.get("schedule_time", current.schedule_time))
        timezone_name = str(fields.get("timezone", current.timezone))
        fields["next_run_at"] = (
            next_daily_run(schedule_time, timezone_name) if enabled else None
        )
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    sets: list[str] = []
    parameters: list[object] = []
    for key, value in fields.items():
        if key == "enabled":
            value = int(bool(value))
        if hasattr(value, "value"):
            value = value.value
        sets.append(f"{key} = ?")
        parameters.append(value)
    parameters.append(automation_id)
    db = await get_db()
    await db.execute(
        f"UPDATE account_automations SET {', '.join(sets)} WHERE id = ?",
        parameters,
    )
    await db.commit()
    rows = await db.execute_fetchall(
        "SELECT * FROM account_automations WHERE id = ?", (automation_id,)
    )
    await db.close()
    return _row_to_account_automation(rows[0])


async def create_account_run(
    automation: AccountAutomationResponse,
    *,
    trigger: str,
    scheduled_for: str | None = None,
    event_date: str | None = None,
    connection: aiosqlite.Connection | None = None,
) -> AccountRunResponse:
    from backend.account_ops.schedule import local_event_date

    run_id = new_account_id("acct")
    now = datetime.now(timezone.utc).isoformat()
    own_connection = connection is None
    db = connection or await get_db()
    await db.execute(
        """INSERT INTO account_runs (
               id, automation_id, automation_name, account_handle, platform,
               trigger, status, scheduled_for, event_date, created_at, executor
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            automation.id,
            automation.name,
            automation.account_handle,
            automation.platform,
            trigger,
            AccountRunStatus.QUEUED.value,
            scheduled_for,
            event_date or local_event_date(automation.timezone),
            now,
            automation.executor.value,
        ),
    )
    if own_connection:
        await db.commit()
    rows = await db.execute_fetchall("SELECT * FROM account_runs WHERE id = ?", (run_id,))
    if own_connection:
        await db.close()
    return _row_to_account_run(rows[0])


async def enqueue_due_account_runs() -> int:
    """Atomically enqueue each due automation once and advance its schedule."""
    from backend.account_ops.schedule import local_event_date, next_daily_run

    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    db = await get_db()
    await db.execute("BEGIN IMMEDIATE")
    rows = await db.execute_fetchall(
        """SELECT * FROM account_automations
           WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
           ORDER BY next_run_at ASC""",
        (now,),
    )
    for row in rows:
        automation = _row_to_account_automation(row)
        await create_account_run(
            automation,
            trigger="scheduled",
            scheduled_for=automation.next_run_at,
            event_date=local_event_date(automation.timezone, at=now_dt),
            connection=db,
        )
        await db.execute(
            """UPDATE account_automations
               SET last_run_at = ?, next_run_at = ?, updated_at = ? WHERE id = ?""",
            (
                automation.next_run_at,
                next_daily_run(
                    automation.schedule_time,
                    automation.timezone,
                    after=now_dt,
                ),
                now,
                automation.id,
            ),
        )
    await db.commit()
    await db.close()
    return len(rows)


async def claim_next_account_run() -> AccountRunResponse | None:
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()
    await db.execute("BEGIN IMMEDIATE")
    rows = await db.execute_fetchall(
        """SELECT * FROM account_runs WHERE status = ?
           ORDER BY created_at ASC LIMIT 1""",
        (AccountRunStatus.QUEUED.value,),
    )
    if not rows:
        await db.commit()
        await db.close()
        return None
    run_id = rows[0]["id"]
    await db.execute(
        "UPDATE account_runs SET status = ?, started_at = ? WHERE id = ? AND status = ?",
        (
            AccountRunStatus.PLANNING.value,
            now,
            run_id,
            AccountRunStatus.QUEUED.value,
        ),
    )
    await db.commit()
    claimed = await db.execute_fetchall("SELECT * FROM account_runs WHERE id = ?", (run_id,))
    await db.close()
    return _row_to_account_run(claimed[0])


async def list_account_runs(limit: int = 200) -> list[AccountRunResponse]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM account_runs ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    await db.close()
    return [_row_to_account_run(row) for row in rows]


async def get_account_run(run_id: str) -> AccountRunResponse | None:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM account_runs WHERE id = ?", (run_id,))
    await db.close()
    return _row_to_account_run(rows[0]) if rows else None


async def update_account_run(run_id: str, **values: object) -> None:
    allowed = {
        "status",
        "started_at",
        "completed_at",
        "title",
        "post_text",
        "image_path",
        "chatgpt_conversation_url",
        "post_url",
        "external_post_id",
        "content_json",
        "error_message",
        "log_text",
    }
    fields = {key: value for key, value in values.items() if key in allowed}
    if not fields:
        return
    sets: list[str] = []
    parameters: list[object] = []
    for key, value in fields.items():
        if hasattr(value, "value"):
            value = value.value
        sets.append(f"{key} = ?")
        parameters.append(value)
    parameters.append(run_id)
    db = await get_db()
    await db.execute(
        f"UPDATE account_runs SET {', '.join(sets)} WHERE id = ?", parameters
    )
    await db.commit()
    await db.close()


async def append_account_run_log(run_id: str, line: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE account_runs SET log_text = log_text || ? WHERE id = ?",
        (line.rstrip() + "\n", run_id),
    )
    await db.commit()
    await db.close()


async def reset_orphaned_account_runs() -> int:
    statuses = (
        AccountRunStatus.PLANNING.value,
        AccountRunStatus.GENERATING_IMAGE.value,
        AccountRunStatus.PUBLISHING.value,
    )
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()
    placeholders = ", ".join("?" for _ in statuses)
    cursor = await db.execute(
        f"""UPDATE account_runs
            SET status = ?, completed_at = ?, error_message = ?,
                log_text = log_text || ?
            WHERE status IN ({placeholders})""",
        (
            AccountRunStatus.FAILED.value,
            now,
            "Interrupted by a server restart.",
            "Interrupted by a server restart.\n",
            *statuses,
        ),
    )
    await db.commit()
    await db.close()
    return cursor.rowcount
