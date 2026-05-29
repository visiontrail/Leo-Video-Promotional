import asyncio
import logging
import traceback
from backend.database import get_next_queued_task, update_task
from backend.models import TaskStatus
from backend.pipeline.orchestrator import run_pipeline

logger = logging.getLogger("worker")

_worker_task: asyncio.Task | None = None


async def _worker_loop():
    logger.info("Background worker started")
    while True:
        try:
            task = await get_next_queued_task()
            if task:
                logger.info(f"Processing task {task.id} ({task.source_type})")
                try:
                    await run_pipeline(task)
                except Exception as e:
                    logger.error(f"Task {task.id} failed: {e}\n{traceback.format_exc()}")
                    await update_task(task.id, status=TaskStatus.FAILED.value, error_message=str(e))
            else:
                await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"Worker error: {e}\n{traceback.format_exc()}")
            await asyncio.sleep(5)


def start_worker():
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker_loop())
        logger.info("Worker task created")
