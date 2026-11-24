"""24/day autonomous scheduling: derived slots, batched generation, no approvals."""

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from app.db import SessionLocal, init_db
from app.models import ContentIdea, GeneratedVideo, PublishQueue, SchedulerSettings
from app.services.publisher import GENERATE_BATCH, tick
from app.services.scheduler import (
    MAX_DAILY_TARGET,
    build_slots,
    get_or_create_settings,
    next_open_slot,
    slots_remaining_today,
)


def _tz() -> ZoneInfo:
    return ZoneInfo("Asia/Kolkata")


def test_build_slots_24_is_hourly():
    slots = build_slots("09:00,14:00,20:00", 24)
    assert len(slots) == 24
    hours = sorted(h for h, _ in slots)
    assert hours == list(range(24))


def test_build_slots_12_is_every_two_hours():
    slots = build_slots("09:00,14:00,20:00", 12)
    assert len(slots) == 12
    minutes = sorted(h * 60 + m for h, m in slots)
    assert minutes[0] == 0
    assert minutes[1] - minutes[0] == 120


def test_small_targets_keep_configured_times():
    slots = build_slots("09:00,14:00,20:00", 3)
    assert slots == [(9, 0), (14, 0), (20, 0)]
    assert build_slots("09:00,14:00,20:00", 1) == [(9, 0)]


def test_targets_are_clamped():
    assert len(build_slots("09:00", 999)) == MAX_DAILY_TARGET
    assert build_slots("09:00", 0) == [(9, 0)]


def test_next_open_slot_stacks_no_more_than_target_per_day():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            await session.execute(delete(PublishQueue))
            await session.execute(delete(GeneratedVideo))
            await session.execute(delete(SchedulerSettings))
            await session.commit()
            settings = await get_or_create_settings(session)
            settings.daily_target = 24
            settings.timezone = "Asia/Kolkata"
            await session.commit()

            tz = _tz()
            today = datetime.now(tz).date()
            first = await next_open_slot(session)
            # next_open_slot returns UTC; compare calendar days in the app timezone.
            assert first.astimezone(tz).date() == today  # first free slot is today

            # Fill every remaining slot today; the next call must move to tomorrow.
            from app.services.scheduler import slot_stamps_for_day

            stamps = [s for s in slot_stamps_for_day(settings.slots, 24, datetime.now(tz), tz) if s > datetime.now(tz)]
            for index, stamp in enumerate(stamps, start=1):
                # Store UTC (the production convention) and keep ids unique for dummies.
                session.add(PublishQueue(video_id=-index, scheduled_for=stamp.astimezone(timezone.utc), status="scheduled"))
            await session.commit()
            second = await next_open_slot(session)
            assert second.astimezone(tz).date() > today

    asyncio.run(run())


def test_slots_remaining_today_counts_free_future_slots():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            await session.execute(delete(PublishQueue))
            await session.execute(delete(GeneratedVideo))
            await session.execute(delete(SchedulerSettings))
            await session.commit()
            settings = await get_or_create_settings(session)
            settings.daily_target = 4
            settings.timezone = "Asia/Kolkata"
            await session.commit()

            remaining = await slots_remaining_today(session)
            assert remaining <= 4

    asyncio.run(run())


def test_pipeline_auto_queues_without_approval():
    """The pipeline must queue passing videos itself when auto mode is on."""

    async def run():
        from app.services import pipeline as pipeline_module

        draft = None
        from app.services.ai_providers import local_script

        draft = local_script("auto queue test", "facts", "en")

        async def fake_discover(session_):
            idea = ContentIdea(title="auto queue test", category="facts", status="new")
            session_.add(idea)
            await session_.commit()
            await session_.refresh(idea)
            return [idea]

        async def fake_draft(*args, **kwargs):
            return draft

        async def fake_to_thread(func, *args, **kwargs):
            name = getattr(func, "__name__", "")
            if name == "render_short":
                return {"path": "/tmp/auto-queue-test.mp4", "duration": 15.0, "width": 1080, "height": 1920}
            if name == "evaluate":
                return {"score": 100.0, "passed": True, "notes": ""}
            return kwargs.get("dest") or args[-1]

        async def fake_upload(session_, path):
            return str(path)

        await init_db()
        async with SessionLocal() as session:
            await session.execute(delete(PublishQueue))
            await session.execute(delete(GeneratedVideo))
            await session.execute(delete(SchedulerSettings))
            await session.commit()
            settings = await get_or_create_settings(session)
            settings.publishing_mode = "automatic"
            settings.paused = False
            await session.commit()
            from app.services.config_store import put_setting

            await put_setting(session, "auto_approve", "true")

            originals = (
                pipeline_module.discover_ideas,
                pipeline_module.draft_script,
                pipeline_module.asyncio.to_thread,
                pipeline_module.upload_file,
            )
            pipeline_module.discover_ideas = fake_discover
            pipeline_module.draft_script = fake_draft
            pipeline_module.asyncio.to_thread = fake_to_thread
            pipeline_module.upload_file = fake_upload
            try:
                video = await pipeline_module.run_generation(session, "en")
            finally:
                (
                    pipeline_module.discover_ideas,
                    pipeline_module.draft_script,
                    pipeline_module.asyncio.to_thread,
                    pipeline_module.upload_file,
                ) = originals

            assert video.status == "generated"  # never pending_approval
            queue = await session.scalar(select(PublishQueue).where(PublishQueue.video_id == video.id))
            assert queue is not None and queue.status == "scheduled"

    asyncio.run(run())


def test_tick_generates_in_batches_up_to_backlog_target():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            await session.execute(delete(PublishQueue))
            await session.execute(delete(GeneratedVideo))
            await session.execute(delete(SchedulerSettings))
            await session.commit()
            settings = await get_or_create_settings(session)
            settings.daily_target = 24
            settings.paused = False
            await session.commit()

            made = {"n": 0}

            async def fake_generate(session_, language):
                made["n"] += 1
                video = GeneratedVideo(
                    status="generated",
                    title=f"auto {made['n']}",
                    hashtags="#shorts",
                    quality_score=100.0,
                    duration_sec=15.0,
                    file_path="",
                )
                session_.add(video)
                await session_.commit()
                await session_.refresh(video)
                return video

            from app.services import publisher

            original = publisher.run_generation
            publisher.run_generation = fake_generate
            try:
                result = await tick(session)
            finally:
                publisher.run_generation = original

            assert result["generated"] == GENERATE_BATCH  # 3 per tick, not 24 at once
            assert made["n"] == GENERATE_BATCH

    asyncio.run(run())


def test_quota_error_blocks_without_pausing():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            await session.execute(delete(PublishQueue))
            await session.execute(delete(GeneratedVideo))
            await session.execute(delete(SchedulerSettings))
            await session.commit()
            settings = await get_or_create_settings(session)
            settings.paused = False
            settings.publishing_mode = "automatic"
            await session.commit()

            video = GeneratedVideo(
                status="generated", title="q", hashtags="", quality_score=100.0, duration_sec=15.0, file_path=""
            )
            session.add(video)
            await session.commit()
            await session.refresh(video)
            item = PublishQueue(video_id=video.id, scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=1))
            session.add(item)
            await session.commit()
            await session.refresh(item)

            from app.services import publisher

            async def fake_ready(session_):
                return True

            async def fake_upload(session_, video_):
                raise RuntimeError('{"error": {"errors": [{"reason": "quotaExceeded"}]}}')

            original_ready, original_upload = publisher.youtube_ready, publisher.upload_video
            publisher.youtube_ready, publisher.upload_video = fake_ready, fake_upload
            try:
                result = await tick(session)
            finally:
                publisher.youtube_ready, publisher.upload_video = original_ready, original_upload

            await session.refresh(item)
            assert item.status == "blocked"
            assert "quota" in (item.last_error or "")
            await session.refresh(settings)
            assert settings.paused is False  # automation keeps running
            assert result["errors"]

    asyncio.run(run())
