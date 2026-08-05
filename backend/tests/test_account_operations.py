import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image

from backend import config, database
from backend.account_ops.opencode import OpenCodeError, _assistant_text
from backend.account_ops.orchestrator import (
    _content_object,
    _prepare_publish_image,
    _publish,
    _render_prompt,
    _verify_account,
)
from backend.account_ops.schedule import next_daily_run
from backend.models import AccountRunStatus
from backend.pipeline.opencli import OpenCLIError, OpenCLIResult


class AccountScheduleTests(unittest.TestCase):
    def test_daily_wall_clock_is_converted_across_timezones(self):
        after = datetime(2026, 8, 4, 0, 30, tzinfo=timezone.utc)

        self.assertEqual(
            next_daily_run("09:00", "Asia/Singapore", after=after),
            "2026-08-04T01:00:00+00:00",
        )

    def test_passed_wall_clock_moves_to_the_next_day(self):
        after = datetime(2026, 8, 4, 2, 0, tzinfo=timezone.utc)

        self.assertEqual(
            next_daily_run("09:00", "Asia/Singapore", after=after),
            "2026-08-05T01:00:00+00:00",
        )


class AccountDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup)
        self.db_path = Path(self.temp.name) / "account-operations.db"
        self.db_patch = patch.object(config, "DB_PATH", self.db_path)
        self.db_patch.start()
        await database.init_db()

    async def _cleanup(self):
        self.db_patch.stop()
        self.temp.cleanup()

    async def test_init_seeds_the_quiet_atlas_commission(self):
        automations = await database.list_account_automations()

        self.assertEqual(len(automations), 1)
        self.assertEqual(automations[0].account_handle, "AQuietAtlas")
        self.assertTrue(automations[0].enabled)
        self.assertIsNotNone(automations[0].next_run_at)

    async def test_prompt_edit_preserves_the_existing_schedule(self):
        automation = (await database.list_account_automations())[0]

        updated = await database.update_account_automation(
            automation.id,
            {"prompt_template": automation.prompt_template + "\nPrefer primary sources."},
        )

        self.assertEqual(updated.next_run_at, automation.next_run_at)

    async def test_manual_run_is_claimed_once_with_an_account_snapshot(self):
        automation = (await database.list_account_automations())[0]
        created = await database.create_account_run(automation, trigger="manual")

        claimed = await database.claim_next_account_run()
        second = await database.claim_next_account_run()

        self.assertEqual(claimed.id, created.id)
        self.assertEqual(claimed.status, AccountRunStatus.PLANNING)
        self.assertEqual(claimed.account_handle, "AQuietAtlas")
        self.assertIsNone(second)

    async def test_due_schedule_is_enqueued_only_once(self):
        automation = (await database.list_account_automations())[0]
        db = await database.get_db()
        await db.execute(
            "UPDATE account_automations SET next_run_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00+00:00", automation.id),
        )
        await db.commit()
        await db.close()

        first = await database.enqueue_due_account_runs()
        second = await database.enqueue_due_account_runs()
        runs = await database.list_account_runs()

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].trigger, "scheduled")


class AccountOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    def test_prompt_variables_render_without_touching_json_language(self):
        rendered = _render_prompt(
            "On {month_name} {day}, return JSON for {date} in {year}.",
            "2026-08-04",
        )

        self.assertEqual(
            rendered,
            "On August 4, return JSON for 2026-08-04 in 2026.",
        )

    def test_content_contract_accepts_a_sourced_post(self):
        content = _content_object(
            {
                "title": "A consequential event",
                "year": 1914,
                "event_summary": "A verified account of what happened.",
                "historical_reflection": "Institutions turn choices into consequences.",
                "post_text": "In 1914, a consequential event altered institutions and public life. History rarely repeats cleanly; it preserves the cost of choices long after their authors are gone.",
                "image_prompt": "A period-authentic documentary scene without text.",
                "source_notes": ["Archive A", "Museum B"],
            }
        )

        self.assertEqual(content["year"], 1914)

    def test_content_contract_rejects_an_oversized_post(self):
        with self.assertRaisesRegex(OpenCLIError, "maximum is 280"):
            _content_object(
                {
                    "title": "Event",
                    "year": 1900,
                    "event_summary": "Summary",
                    "historical_reflection": "Reflection",
                    "post_text": "x" * 281,
                    "image_prompt": "Scene",
                    "source_notes": ["A", "B"],
                }
            )

    def test_publish_image_is_compacted_for_browser_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGB", (1800, 1200), (36, 48, 58)).save(source)

            output = _prepare_publish_image(source, root)

            self.assertEqual(output.suffix, ".jpg")
            self.assertLess(output.stat().st_size, source.stat().st_size)
            with Image.open(output) as image:
                self.assertLessEqual(image.width, 1400)
                self.assertLessEqual(image.height, 1400)

    async def test_publish_guard_rejects_the_wrong_browser_account(self):
        result = OpenCLIResult(
            args=("twitter", "whoami"),
            returncode=0,
            stdout='{"logged_in":true,"username":"SomeoneElse"}',
            stderr="",
        )
        with patch(
            "backend.account_ops.orchestrator.run_opencli",
            AsyncMock(return_value=result),
        ):
            with self.assertRaisesRegex(OpenCLIError, "Refusing to publish"):
                await _verify_account("AQuietAtlas")

    async def test_a_publish_that_reached_x_before_dying_is_not_reported_failed(self):
        post_text = "On 5 August 1858 the first transatlantic cable carried its first message."
        timeline = OpenCLIResult(
            args=("twitter", "tweets"),
            returncode=0,
            stdout=(
                '[{"id":"1234","text":"On 5 August 1858 the first transatlantic cable '
                'carried its first message.","url":"https://x.com/AQuietAtlas/status/1234"}]'
            ),
            stderr="",
        )

        async def opencli(args, **kwargs):
            if args[1] == "post":
                raise OpenCLIError("CDP command Runtime.evaluate timed out after 115s")
            return timeline

        with patch("backend.account_ops.orchestrator.run_opencli", opencli):
            url, post_id, raw = await _publish(post_text, Path("image.jpg"), "AQuietAtlas")

        self.assertEqual(url, "https://x.com/AQuietAtlas/status/1234")
        self.assertEqual(post_id, "1234")
        self.assertIn("recovered_from_error", raw)

    async def test_a_publish_absent_from_the_timeline_is_declared_safe_to_retry(self):
        empty = OpenCLIResult(
            args=("twitter", "tweets"), returncode=0, stdout="[]", stderr=""
        )

        async def opencli(args, **kwargs):
            if args[1] == "post":
                raise OpenCLIError("CDP command Runtime.evaluate timed out after 115s")
            return empty

        with patch("backend.account_ops.orchestrator.run_opencli", opencli):
            with self.assertRaisesRegex(OpenCLIError, "safe to retry"):
                await _publish("Nothing went out.", Path("image.jpg"), "AQuietAtlas")

    async def test_an_unreadable_timeline_leaves_the_publication_state_unknown(self):
        async def opencli(args, **kwargs):
            raise OpenCLIError(f"OpenCLI twitter {args[1]} failed with exit 1")

        with patch("backend.account_ops.orchestrator.run_opencli", opencli):
            with self.assertRaisesRegex(OpenCLIError, "UNKNOWN"):
                await _publish("Ambiguous.", Path("image.jpg"), "AQuietAtlas")


class OpenCodeOutputTests(unittest.TestCase):
    def test_json_event_stream_recovers_assistant_text_and_session(self):
        stdout = "\n".join(
            [
                '{"type":"step_start","sessionID":"ses_123","part":{}}',
                '{"type":"text","sessionID":"ses_123","part":{"text":"{\\"ok\\":true}"}}',
            ]
        )

        session_id, text = _assistant_text(stdout)

        self.assertEqual(session_id, "ses_123")
        self.assertEqual(text, '{"ok":true}')

    def test_empty_event_stream_is_an_error(self):
        with self.assertRaises(OpenCodeError):
            _assistant_text('{"type":"step_finish","sessionID":"ses_123"}')


if __name__ == "__main__":
    unittest.main()
