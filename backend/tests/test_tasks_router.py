from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from backend.models import TaskStatus
from backend.routers.tasks import render_task


class RenderTaskRecoveryTests(IsolatedAsyncioTestCase):
    async def test_render_task_recovers_failed_task_with_existing_audio(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            audio_path = output_dir / "narration.wav"
            audio_path.write_bytes(b"RIFF")
            task = SimpleNamespace(
                id="failed-task",
                status=TaskStatus.FAILED,
                audio_path=str(audio_path),
                output_dir=str(output_dir),
            )
            queued_task = SimpleNamespace(id=task.id, status=TaskStatus.QUEUED)

            with (
                patch(
                    "backend.routers.tasks.db.get_task",
                    AsyncMock(side_effect=[task, queued_task]),
                ),
                patch("backend.routers.tasks.db.update_task", AsyncMock()) as update_task,
            ):
                result = await render_task(task.id)

            self.assertIs(result, queued_task)
            self.assertTrue((output_dir / ".render").is_file())
            update_task.assert_awaited_once_with(
                task.id,
                status=TaskStatus.QUEUED.value,
                error_message=None,
            )

    async def test_render_task_still_rejects_nonrecoverable_status(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            audio_path = output_dir / "narration.wav"
            audio_path.write_bytes(b"RIFF")
            task = SimpleNamespace(
                id="complete-task",
                status=TaskStatus.COMPLETE,
                audio_path=str(audio_path),
                output_dir=str(output_dir),
            )

            with patch("backend.routers.tasks.db.get_task", AsyncMock(return_value=task)):
                with self.assertRaisesRegex(
                    HTTPException,
                    "not awaiting review or eligible",
                ) as raised:
                    await render_task(task.id)

            self.assertEqual(raised.exception.status_code, 409)
            self.assertFalse((output_dir / ".render").exists())
