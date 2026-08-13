"""Human-gated video publication pipeline.

The dispatcher and its safety gates are production code; platform adapters are
intentionally absent in the first phase. Adding an adapter later cannot publish
anything until both the global master switch and the individual plan opt-in are
enabled.
"""

from dataclasses import dataclass
from typing import Protocol

from backend import config, database
from backend.models import TaskResponse


@dataclass(frozen=True)
class PublicationResult:
    action: str
    reason: str
    publication_url: str | None = None


class VideoPublisher(Protocol):
    async def publish(self, task: TaskResponse, *, title: str, platform: str) -> str:
        """Publish a finished video and return its canonical URL."""


PUBLISHERS: dict[str, VideoPublisher] = {}


async def run_auto_publish_pipeline(task: TaskResponse) -> PublicationResult:
    """Evaluate and, only when fully authorized, dispatch automatic publication."""
    if task.origin_type != "content_plan" or not task.origin_id:
        return PublicationResult("not_applicable", "Task did not originate from content planning")
    item = await database.get_content_plan_item(task.origin_id)
    if item is None:
        return PublicationResult("blocked", "Originating plan no longer exists")
    if not config.VIDEO_AUTO_PUBLISH_ENABLED:
        return PublicationResult("awaiting_review", "Global automatic publication is disabled")
    if not item.auto_publish_requested:
        return PublicationResult("awaiting_review", "This plan requires manual publication")
    publisher = PUBLISHERS.get(item.platform.strip().lower())
    if publisher is None:
        return PublicationResult(
            "awaiting_review",
            f"No automatic publisher is configured for {item.platform}",
        )
    try:
        url = await publisher.publish(task, title=item.title, platform=item.platform)
    except Exception as exc:
        # Publication failure must never rewrite a valid finished render as a
        # failed generation task. Keep it in the human review queue.
        return PublicationResult("failed", f"Automatic publisher failed: {exc}")
    await database.approve_content_plan_item(item.id)
    await database.record_manual_publication(item.id, url)
    return PublicationResult("published", "Automatic publication completed", url)
