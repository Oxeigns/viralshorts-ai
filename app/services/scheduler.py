from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import PublishQueue, SchedulerSettings


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
    for item in raw.split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        h, m = item.split(":", 1)
        out.append((int(h), int(m)))
    return out or [(9, 0), (14, 0), (20, 0)]


async def next_open_slot(session: AsyncSession) -> datetime:
    cfg = await get_or_create_settings(session)
    tz = ZoneInfo(cfg.timezone or "Asia/Kolkata")
    now = datetime.now(tz)
    slots = _parse_slots(cfg.slots)
    existing = (await session.scalars(select(PublishQueue))).all()
    taken = {q.scheduled_for.astimezone(tz).replace(second=0, microsecond=0) for q in existing}

    for day in range(0, 14):
        base = (now + timedelta(days=day)).replace(second=0, microsecond=0)
        used_today = 0
        candidates = []
        for h, m in slots:
            stamp = base.replace(hour=h, minute=m)
            if stamp <= now:
                continue
            if stamp in taken:
                used_today += 1
                continue
            candidates.append(stamp)
        remaining = max(0, cfg.daily_target - used_today)
        if remaining and candidates:
            return candidates[0].astimezone(timezone.utc)
    return (now + timedelta(hours=2)).astimezone(timezone.utc)
