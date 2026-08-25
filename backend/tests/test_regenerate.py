import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend import config
from backend.models import TaskConfig, TaskResponse
from backend.pipeline import orchestrator


class RegeneratePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_title_failure_retains_prior_title_and_continues_to_tts(self):
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory) / "regenerate-title-fallback"
            task_dir.mkdir()
            script_path = task_dir / "script.txt"
            script_path.write_text("A finished narration script.", encoding="utf-8")
            task = TaskResponse(
                id=task_dir.name,
                created_at="2026-08-25T00:00:00+00:00",
                updated_at="2026-08-25T00:00:00+00:00",
                source_type="youtube",
                source_title="Original Source Title",
                generated_title="Existing Publication Title",
                status="failed",
                script_path=str(script_path),
                config=TaskConfig(
                    thumbnail_enabled=False,
                    footage_enabled=False,
                    auto_render=False,
                ),
            )
            logs: list[str] = []
            generated_audio = str(task_dir / "audio" / "narration.wav")

            with (
                patch.object(config, "OUTPUTS_DIR", Path(directory)),
                patch.object(orchestrator, "update_task", AsyncMock()),
                patch.object(
                    orchestrator,
                    "_generate_task_title",
                    AsyncMock(side_effect=RuntimeError("provider unavailable")),
                ),
                patch.object(
                    orchestrator,
                    "generate_tts",
                    AsyncMock(return_value=generated_audio),
                ) as generate_tts,
                patch.object(orchestrator, "_after_audio", AsyncMock()),
                patch.object(
                    orchestrator,
                    "emit_pipeline_log",
                    side_effect=lambda task_id, output_dir, message, log: logs.append(message),
                ),
            ):
                await orchestrator.run_regenerate(task)

            generate_tts.assert_awaited_once()
            self.assertTrue(
                any(
                    "retaining prior title 'Existing Publication Title'" in message
                    for message in logs
                )
            )


if __name__ == "__main__":
    unittest.main()
