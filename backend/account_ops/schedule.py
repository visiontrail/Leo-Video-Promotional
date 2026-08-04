from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def next_daily_run(
    schedule_time: str,
    timezone_name: str,
    *,
    after: datetime | None = None,
) -> str:
    """Return the first daily wall-clock occurrence strictly after ``after``."""
    reference = after or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    zone = ZoneInfo(timezone_name)
    local_reference = reference.astimezone(zone)
    hour, minute = (int(part) for part in schedule_time.split(":", maxsplit=1))
    candidate = datetime.combine(
        local_reference.date(),
        time(hour=hour, minute=minute),
        tzinfo=zone,
    )
    if candidate <= local_reference:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc).isoformat()


def local_event_date(timezone_name: str, *, at: datetime | None = None) -> str:
    reference = at or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference.astimezone(ZoneInfo(timezone_name)).date().isoformat()
