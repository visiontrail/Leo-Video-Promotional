import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend import config, database
from backend.models import (
    ContentPlanItemCreate,
    ContentPlanStatus,
    ContentSeriesCreate,
    PublicationStatus,
    TaskConfig,
    TaskStatus,
)
from backend.pipeline.extractors.topic import extract_topic
from backend.publishing import run_auto_publish_pipeline


class ContentPlanningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            config, "DB_PATH", Path(self.directory.name) / "planning.db"
        )
        self.db_patch.start()
        await database.init_db()

    async def asyncTearDown(self):
        self.db_patch.stop()
        self.directory.cleanup()

    async def test_scheduled_plan_materializes_linked_video_task(self):
        series = await database.create_content_series(
            ContentSeriesCreate(
                name="Borderlands",
                theme="Countries and identity",
                description="How borders shape daily life.",
            )
        )
        item = await database.create_content_plan_item(
            ContentPlanItemCreate(
                series_id=series.id,
                title="The border that crosses a library",
                brief="Explain the history, the people who use it, and what the border reveals.",
                episode_number=3,
                generation_at="2030-04-10T01:00:00Z",
                publish_at="2030-04-11T09:00:00Z",
                platform="YouTube",
            )
        )

        self.assertEqual(item.status, ContentPlanStatus.SCHEDULED)
        self.assertIsNotNone(item.task_id)
        task = await database.get_task(item.task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task.source_type.value, "topic")
        self.assertEqual(task.scheduled_at, "2030-04-10T01:00:00+00:00")
        self.assertEqual(task.planned_publish_at, "2030-04-11T09:00:00+00:00")
        self.assertEqual(task.origin_type, "content_plan")
        self.assertEqual(task.origin_id, item.id)
        self.assertEqual(
            task.origin_label,
            "Borderlands · EP 03 · The border that crosses a library",
        )

    async def test_rescheduling_planned_task_moves_editorial_clock_atomically(self):
        item = await database.create_content_plan_item(
            ContentPlanItemCreate(
                title="A capital built from scratch",
                brief="Compare the political goals and lived results of planned capital cities.",
                generation_at="2030-05-01T01:00:00Z",
                publish_at="2030-05-03T01:00:00Z",
            )
        )

        await database.reschedule_task(item.task_id, "2030-05-02T02:30:00+00:00")

        task = await database.get_task(item.task_id)
        refreshed = await database.get_content_plan_item(item.id)
        self.assertEqual(task.scheduled_at, "2030-05-02T02:30:00+00:00")
        self.assertEqual(refreshed.generation_at, task.scheduled_at)

    async def test_due_plan_is_prioritized_and_future_plan_stays_parked(self):
        manual = await database.create_task(
            "youtube", "https://example.com/manual", config=TaskConfig()
        )
        due_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        item = await database.create_content_plan_item(
            ContentPlanItemCreate(
                title="A scheduled priority",
                brief="Verify that a due editorial plan runs ahead of an older manual queue item.",
                generation_at=due_at,
                publish_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            )
        )

        selected = await database.get_next_queued_task()
        self.assertEqual(selected.id, item.task_id)
        self.assertNotEqual(selected.id, manual.id)

        await database.update_task(item.task_id, status=TaskStatus.EXTRACTING.value)
        await database.update_task(manual.id, status=TaskStatus.COMPLETE.value)
        future = await database.create_content_plan_item(
            ContentPlanItemCreate(
                title="A parked future plan",
                brief="Verify that the worker cannot claim this plan before its release time.",
                generation_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                publish_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            )
        )
        self.assertIsNotNone(future.task_id)
        self.assertIsNone(await database.get_next_queued_task())

    async def test_finished_plan_requires_approval_before_manual_publication(self):
        item = await database.create_content_plan_item(
            ContentPlanItemCreate(
                title="The country with two calendars",
                brief="Build a sourced explainer about calendar systems and civic life.",
                generation_at="2030-06-01T01:00:00Z",
                publish_at="2030-06-02T01:00:00Z",
            )
        )
        await database.update_task(item.task_id, status=TaskStatus.COMPLETE.value)

        review = await database.get_content_plan_item(item.id)
        self.assertEqual(review.status, ContentPlanStatus.REVIEW)
        self.assertEqual(review.publication_status, PublicationStatus.AWAITING_REVIEW)
        with self.assertRaisesRegex(ValueError, "approval"):
            await database.record_manual_publication(item.id, "https://example.com/watch")

        approved = await database.approve_content_plan_item(item.id)
        self.assertEqual(approved.status, ContentPlanStatus.READY)
        self.assertEqual(approved.publication_status, PublicationStatus.APPROVED)
        published = await database.record_manual_publication(
            item.id, "https://example.com/watch"
        )
        self.assertEqual(published.status, ContentPlanStatus.PUBLISHED)
        self.assertEqual(published.publication_status, PublicationStatus.PUBLISHED)
        self.assertEqual(published.publication_url, "https://example.com/watch")

    async def test_auto_publication_is_blocked_by_default_and_without_adapter(self):
        item = await database.create_content_plan_item(
            ContentPlanItemCreate(
                title="Why this island changed names",
                brief="Trace the naming history without flattening competing local perspectives.",
                generation_at="2030-07-01T01:00:00Z",
                publish_at="2030-07-02T01:00:00Z",
                platform="YouTube",
                auto_publish_requested=True,
            )
        )
        await database.update_task(item.task_id, status=TaskStatus.COMPLETE.value)
        task = await database.get_task(item.task_id)

        with patch.object(config, "VIDEO_AUTO_PUBLISH_ENABLED", False):
            result = await run_auto_publish_pipeline(task)
        self.assertEqual(result.action, "awaiting_review")
        self.assertIn("disabled", result.reason)

        with patch.object(config, "VIDEO_AUTO_PUBLISH_ENABLED", True):
            result = await run_auto_publish_pipeline(task)
        self.assertEqual(result.action, "awaiting_review")
        self.assertIn("No automatic publisher", result.reason)

    async def test_topic_extractor_preserves_editorial_brief(self):
        content = await extract_topic(
            "The map that created a country",
            "Explain which map mattered, who commissioned it, and how later borders diverged.",
        )

        self.assertEqual(content.source_type, "topic")
        self.assertEqual(content.title, "The map that created a country")
        self.assertTrue(content.metadata["research_required"])
        self.assertIn("later borders", content.text)


if __name__ == "__main__":
    unittest.main()
