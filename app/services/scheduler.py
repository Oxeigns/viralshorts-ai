from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import PublishQueue, SchedulerSettings

MAX_DAILY_TARGET = 48


def as_utc(value: datetime | None) -> datetime | None:
    """Normalize a stored timestamp to UTC.

    SQLite drops the timezone offset on write, so values come back naive even though
    they were written UTC-aware. Treating naive values as UTC keeps comparisons
    correct; aware values pass through unchanged.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def get_or_create_settings(session: AsyncSession) -> SchedulerSettings:
    row = await session.scalar(select(SchedulerSettings).limit(1))
    if row:
        return row
    cfg = get_settings()
    row = SchedulerSettings(
        daily_target=cfg.daily_publish_target,
        timezone=cfg.timezone,
        slots=",".join(cfg.slots),
        language=cfg.default_language,
        paused=cfg.automation_paused,
        quality_threshold=cfg.quality_threshold,
        publishing_mode=cfg.publishing_mode,
        approval_required=cfg.approval_required,
        video_category=cfg.youtube_category_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def _parse_slots(raw: str) -> list[tuple[int, int]]:
    out = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        try:
            h, m = item.split(":", 1)
            out.append((int(h), int(m)))
        except ValueError:
            continue
    return out or [(9, 0), (14, 0), (20, 0)]


def build_slots(configured: str, target: int) -> list[tuple[int, int]]:
    """Slots that can actually deliver ``target`` uploads a day.

    Three fixed times can never satisfy a target of 24, so when the target exceeds
    the number of configured times the day is divided evenly instead. 24 becomes an
    hourly schedule; 12 becomes every two hours.
    """
    target = max(1, min(int(target or 1), MAX_DAILY_TARGET))
    slots = _parse_slots(configured)
    if target <= len(slots):
        return sorted(slots)[:target]
    step = max(1, round((24 * 60) / target))
    spread = [((index * step) // 60 % 24, (index * step) % 60) for index in range(target)]
    return spread


def slot_stamps_for_day(configured: str, target: int, day: datetime, tz: ZoneInfo) -> list[datetime]:
    return [day.astimezone(tz).replace(hour=h, minute=m, second=0, microsecond=0) for h, m in build_slots(configured, target)]


async def next_open_slot(session: AsyncSession) -> datetime:
    """First future slot that keeps the day within ``daily_target``.

    A day is full once ``daily_target`` queue rows already sit on it, so the search
    moves to the next day rather than stacking more uploads than YouTube allows.
    """
    cfg = await get_or_create_settings(session)
    tz = ZoneInfo(cfg.timezone or "Asia/Kolkata")
    now = datetime.now(tz)
    target = max(1, min(int(cfg.daily_target or 1), MAX_DAILY_TARGET))
    existing = (await session.scalars(select(PublishQueue))).all()
    taken = {
        as_utc(q.scheduled_for).astimezone(tz).replace(second=0, microsecond=0)
        for q in existing
        if q.status in {"scheduled", "published", "blocked", "skipped", "failed"} and q.scheduled_for is not None
    }

    for day in range(0, 21):
        base = (now + timedelta(days=day)).replace(hour=0, minute=0, second=0, microsecond=0)
        stamps = slot_stamps_for_day(cfg.slots, target, base, tz)
        used = sum(1 for stamp in stamps if stamp in taken)
        if used >= target:
            continue
        for stamp in stamps:
            if stamp > now and stamp not in taken:
                return stamp.astimezone(timezone.utc)
    return (now + timedelta(hours=2)).astimezone(timezone.utc)


async def slots_remaining_today(session: AsyncSession) -> int:
    """How many uploads today's schedule can still accept."""
    cfg = await get_or_create_settings(session)
    tz = ZoneInfo(cfg.timezone or "Asia/Kolkata")
    now = datetime.now(tz)
    target = max(1, min(int(cfg.daily_target or 1), MAX_DAILY_TARGET))
    stamps = slot_stamps_for_day(cfg.slots, target, now, tz)
    existing = (await session.scalars(select(PublishQueue))).all()
    taken = {
        as_utc(q.scheduled_for).astimezone(tz).replace(second=0, microsecond=0)
        for q in existing
        if q.status in {"scheduled", "published", "blocked", "skipped", "failed"} and q.scheduled_for is not None
    }
    return sum(1 for stamp in stamps if stamp > now and stamp not in taken)
