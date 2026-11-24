from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GeneratedVideo, Job, PublishQueue, utcnow
from app.services.memory import over_budget, rss_mb
from app.services.notify import notify
from app.services.pipeline import run_generation
from app.services.scheduler import as_utc, get_or_create_settings
from app.services.youtube import ReconnectRequired, upload_video, youtube_ready

# Renders per tick. The scheduler runs every 10 minutes, so three per tick fills a
# 24/day target in about 80 minutes without holding the event loop for long.
GENERATE_BATCH = 3


async def tick(session: AsyncSession) -> dict:
    settings = await get_or_create_settings(session)
    result = {"generated": 0, "published": 0, "paused": settings.paused, "errors": []}
    if settings.paused:
        return result

    # Keep enough finished videos to cover today's remaining slots without building a
    # huge backlog when the target is high (24/day). Batch the renders so no single
    # tick holds the event loop for long.
    backlog_target = max(1, settings.daily_target)
    ready = (await session.scalars(select(GeneratedVideo).where(GeneratedVideo.status == "generated"))).all()
    pending = await session.scalar(
        select(func.count(GeneratedVideo.id)).where(GeneratedVideo.status.in_(("pending_approval", "processing")))
    ) or 0
    queued = await session.scalar(
        select(func.count(PublishQueue.id)).where(PublishQueue.status == "scheduled")
    ) or 0
    have = len(ready) + pending + queued
    to_make = min(GENERATE_BATCH, max(0, backlog_target - have))
    for _ in range(to_make):
        if over_budget():
            result["errors"].append(f"generation paused: rss {rss_mb()}MB over budget")
            await notify(
                f"Generation paused: this instance is using {rss_mb():.0f} MB. "
                "Run generation on the worker service or raise RENDER_MEMORY_BUDGET_MB."
            )
            break
        try:
            await run_generation(session, settings.language)
            result["generated"] += 1
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(str(exc))
            await notify(f"Scheduler generate failed: {exc}")
            break

    # Compare in Python: SQLite stores timestamps naive, so a SQL comparison between
    # a stored naive value and an aware bind is a string comparison that misorders.
    now_utc = datetime.now(timezone.utc)
    scheduled = (
        await session.scalars(select(PublishQueue).where(PublishQueue.status == "scheduled"))
    ).all()
    due = [item for item in scheduled if as_utc(item.scheduled_for) is not None and as_utc(item.scheduled_for) <= now_utc]
    for item in due:
        video = await session.get(GeneratedVideo, item.video_id)
        if video is None or video.status not in {"generated", "scheduled"}:
            item.status = "skipped"
            continue
        if video.quality_score < settings.quality_threshold:
            item.status = "blocked"
            item.last_error = "quality threshold"
            continue
        if not await youtube_ready(session):
            item.last_error = "youtube oauth not configured"
            item.attempts += 1
            continue
        job = Job(kind="publish", status="running", detail=f"video {video.id}")
        session.add(job)
        try:
            await upload_video(session, video)
            item.status = "published"
            result["published"] += 1
            job.status = "done"
        except ReconnectRequired:
            item.status = "blocked"
            item.last_error = "youtube reconnect required"
            job.status = "failed"
            job.detail = "reconnect required"
            result["errors"].append("youtube reconnect required")
            job.finished_at = utcnow()
            await session.commit()
            continue
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if "quotaexceeded" in text.lower() or "uploadlimitexceeded" in text.lower():
                # Daily quota is a hard stop for today, not a transient error. Retrying
                # would burn the remaining attempts and pause the whole automation.
                item.status = "blocked"
                item.last_error = "youtube quota exceeded — remaining slots resume tomorrow"
                job.status = "failed"
                job.detail = item.last_error
                result["errors"].append(item.last_error)
                job.finished_at = utcnow()
                await session.commit()
                continue
            item.attempts += 1
            item.last_error = text
            job.status = "failed"
            job.detail = text
            result["errors"].append(text)
            await notify(f"Publish failed: {text}")
            if item.attempts >= 5:
                item.status = "failed"
                settings.paused = True
                await notify("Automation paused after repeated publish failures.")
        job.finished_at = utcnow()
        await session.commit()
    return result
