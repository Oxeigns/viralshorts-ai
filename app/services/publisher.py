from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GeneratedVideo, Job, PublishQueue, utcnow
from app.services.notify import notify
from app.services.pipeline import run_generation
from app.services.scheduler import get_or_create_settings
from app.services.youtube import upload_video, youtube_configured


async def tick(session: AsyncSession) -> dict:
    settings = await get_or_create_settings(session)
    result = {"generated": 0, "published": 0, "paused": settings.paused, "errors": []}
    if settings.paused:
        return result

    ready = (await session.scalars(select(GeneratedVideo).where(GeneratedVideo.status == "generated"))).all()
    if len(ready) < settings.daily_target:
        try:
            await run_generation(session, settings.language)
            result["generated"] += 1
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(str(exc))
            await notify(f"Scheduler generate failed: {exc}")

    due = (
        await session.scalars(
            select(PublishQueue).where(
                PublishQueue.status == "scheduled",
                PublishQueue.scheduled_for <= datetime.now(timezone.utc),
            )
        )
    ).all()
    for item in due:
        video = await session.get(GeneratedVideo, item.video_id)
        if video is None or video.status not in {"generated", "scheduled"}:
            item.status = "skipped"
            continue
        if video.quality_score < settings.quality_threshold:
            item.status = "blocked"
            item.last_error = "quality threshold"
            continue
        if not youtube_configured():
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
        except Exception as exc:  # noqa: BLE001
            item.attempts += 1
            item.last_error = str(exc)
            job.status = "failed"
            job.detail = str(exc)
            result["errors"].append(str(exc))
            await notify(f"Publish failed: {exc}")
            if item.attempts >= 5:
                item.status = "failed"
                settings.paused = True
                await notify("Automation paused after repeated publish failures.")
        job.finished_at = utcnow()
        await session.commit()
    return result
