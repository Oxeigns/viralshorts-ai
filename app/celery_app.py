from __future__ import annotations

from celery import Celery

from app.config import get_settings

settings = get_settings()
celery = Celery("viralshorts", broker=settings.redis_url, backend=settings.redis_url)
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone=settings.timezone,
    enable_utc=True,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
)


@celery.task(name="viralshorts.generate")
def generate_task(language: str = "en") -> dict:
    import asyncio

    from app.db import SessionLocal, init_db
    from app.services.pipeline import run_generation

    async def _run() -> dict:
        await init_db()
        async with SessionLocal() as session:
            video = await run_generation(session, language)
            return {"id": video.id, "status": video.status, "title": video.title}

    return asyncio.run(_run())


@celery.task(name="viralshorts.publish_tick")
def publish_tick_task() -> dict:
    import asyncio

    from app.db import SessionLocal, init_db
    from app.services.publisher import tick

    async def _run() -> dict:
        await init_db()
        async with SessionLocal() as session:
            return await tick(session)

    return asyncio.run(_run())
