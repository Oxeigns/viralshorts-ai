"""Background generation jobs.

Rendering a Short takes far longer than a managed proxy allows a single HTTP
request to live, so ``POST /api/generate`` used to hang until Render answered 502.
Generation now runs in a background task and the caller polls a job row.

Jobs also run under their own database session: the request session is closed as
soon as the response is sent.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Job, utcnow

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=15)
_tasks: set[asyncio.Task] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def enqueue_generation(session: AsyncSession, source: str = "web", language: str | None = None) -> Job:
    """Create the job row and start it in the background. Returns immediately."""
    job = Job(kind="generate", status="queued", detail=f"queued by {source}")
    session.add(job)
    await session.commit()
    await session.refresh(job)
    task = asyncio.create_task(_run(job.id, language))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job


async def _run(job_id: int, language: str | None) -> None:
    from app.services.pipeline import run_generation

    try:
        async with SessionLocal() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            job.status = "running"
            job.detail = "rendering"
            await session.commit()

            video = await run_generation(session, language)
            job = await session.get(Job, job_id)
            if job is not None:
                job.status = "failed" if video.status == "failed" else "done"
                job.detail = f"video {video.id} {video.status}" + (f": {video.fail_reason}" if video.fail_reason else "")
                job.finished_at = utcnow()
                await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("generation job %s failed", job_id)
        try:
            async with SessionLocal() as session:
                job = await session.get(Job, job_id)
                if job is not None:
                    job.status = "failed"
                    job.detail = str(exc)[:400]
                    job.finished_at = utcnow()
                    await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("could not record failure for job %s", job_id)


async def job_view(session: AsyncSession, job_id: int) -> dict | None:
    job = await session.get(Job, job_id)
    if job is None:
        return None
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "detail": job.detail,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


async def latest_jobs(session: AsyncSession, kind: str = "generate", limit: int = 20) -> list[Job]:
    rows = await session.scalars(
        select(Job).where(Job.kind == kind).order_by(Job.id.desc()).limit(limit)
    )
    return list(rows)


async def failed_jobs(session: AsyncSession, limit: int = 40) -> list[Job]:
    rows = await session.scalars(
        select(Job).where(Job.status == "failed").order_by(Job.id.desc()).limit(limit)
    )
    return list(rows)


async def reap_stale(session: AsyncSession) -> int:
    """Mark jobs that never finished as failed so the UI cannot show a stuck spinner."""
    cutoff = _now() - STALE_AFTER
    rows = await session.scalars(select(Job).where(Job.status.in_(("queued", "running"))))
    reaped = 0
    for job in rows:
        created = job.created_at
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created < cutoff:
            job.status = "failed"
            job.detail = "timed out before finishing (instance likely restarted mid-render)"
            job.finished_at = utcnow()
            reaped += 1
    if reaped:
        await session.commit()
    return reaped
