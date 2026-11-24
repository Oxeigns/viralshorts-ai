"""Per-video review from Telegram: list, approve one, reject one, page.

Telegram caps callback_data at 64 bytes and drops callbacks that are not answered
quickly, so the keyboard must stay compact and every id must round-trip.
"""

import asyncio

from sqlalchemy import delete, select

from app.db import SessionLocal, init_db
from app.models import GeneratedVideo, PublishQueue
from app.services.config_store import put_setting
from app.services.telegram_control import (
    MODE_TITLES,
    VIDEO_PAGE_SIZE,
    _video_keyboard,
    approve_one,
    main_keyboard,
    reject_one,
    videos_view,
)


async def _make_video(session, status="pending_approval", title="Test video") -> GeneratedVideo:
    video = GeneratedVideo(
        status=status,
        title=title,
        hashtags="#shorts #test",
        quality_score=100.0,
        duration_sec=17.0,
        file_path="",
    )
    session.add(video)
    await session.commit()
    await session.refresh(video)
    return video


def _datas(markup) -> list[str]:
    return [button["callback_data"] for row in markup["inline_keyboard"] for button in row]


def test_main_keyboard_has_videos_entry():
    datas = _datas(main_keyboard())
    assert "ls:pending:0" in datas
    assert "generate" in datas
    assert "approve" in datas


def test_callback_data_fits_telegram_limit():
    video = GeneratedVideo(id=999999, status="pending_approval", title="x", hashtags="")
    markup = _video_keyboard([video], "pending", 0, 1, 1)
    for data in _datas(markup):
        assert len(data.encode()) <= 64, data


def test_pending_list_shows_approve_and_reject():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            await session.execute(delete(PublishQueue))
            await session.execute(delete(GeneratedVideo))
            await session.commit()
            video = await _make_video(session)
            text, markup = await videos_view(session, "pending", 0)
            assert f"#{video.id}" in text
            assert "Waiting for approval" in text
            datas = _datas(markup)
            assert f"ap:{video.id}" in datas
            assert f"rj:{video.id}" in datas
            assert f"wt:{video.id}" in datas

    asyncio.run(run())


def test_approve_one_queues_only_that_video():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            await session.execute(delete(PublishQueue))
            await session.execute(delete(GeneratedVideo))
            await session.commit()
            first = await _make_video(session, title="first")
            second = await _make_video(session, title="second")

            message = await approve_one(session, second.id)
            assert "Approved" in message
            await session.refresh(second)
            await session.refresh(first)
            assert second.status == "generated"
            assert first.status == "pending_approval"

            queue = (await session.scalars(select(PublishQueue))).all()
            assert [item.video_id for item in queue] == [second.id]
            assert queue[0].status == "scheduled"
            assert queue[0].scheduled_for is not None

    asyncio.run(run())


def test_reject_one_marks_rejected_and_skips_queue():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            await session.execute(delete(PublishQueue))
            await session.execute(delete(GeneratedVideo))
            await session.commit()
            video = await _make_video(session)
            await approve_one(session, video.id)
            await session.refresh(video)
            assert video.status == "generated"

            message = await reject_one(session, video.id)
            assert "Rejected" in message
            await session.refresh(video)
            assert video.status == "rejected"
            queue = await session.scalar(select(PublishQueue).where(PublishQueue.video_id == video.id))
            assert queue.status == "skipped"

    asyncio.run(run())


def test_approve_is_idempotent_and_reports_missing():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            await session.execute(delete(PublishQueue))
            await session.execute(delete(GeneratedVideo))
            await session.commit()
            video = await _make_video(session)
            await approve_one(session, video.id)
            again = await approve_one(session, video.id)
            assert "Approved" in again
            queue = (await session.scalars(select(PublishQueue))).all()
            assert len(queue) == 1
            assert "does not exist" in await approve_one(session, 999999)

    asyncio.run(run())


def test_pages_are_bounded():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            await session.execute(delete(PublishQueue))
            await session.execute(delete(GeneratedVideo))
            await session.commit()
            for index in range(VIDEO_PAGE_SIZE + 2):
                await _make_video(session, title=f"video {index}")
            text, markup = await videos_view(session, "pending", 0)
            assert text.count("<b>#") == VIDEO_PAGE_SIZE
            assert "Next »" in str(markup)
            text2, markup2 = await videos_view(session, "pending", 1)
            assert "« Prev" in str(markup2)
            assert text2 != text

    asyncio.run(run())


def test_modes_cover_pending_and_all():
    assert {"pending", "queued", "all", "failed"} <= set(MODE_TITLES)


def test_callback_approve_edits_message(monkeypatch):
    """The button flow must answer the callback and then repaint the list."""
    from app.services import telegram_control as tc

    calls = []

    async def fake_call(session, method, payload):
        calls.append((method, payload))
        return {"ok": True}

    monkeypatch.setattr(tc, "_call", fake_call)

    async def run():
        await init_db()
        async with SessionLocal() as session:
            await session.execute(delete(PublishQueue))
            await session.execute(delete(GeneratedVideo))
            await session.commit()
            await put_setting(session, "telegram_admin_ids", "1")
            video = await _make_video(session)
            result = await tc.handle_callback(
                session,
                {
                    "id": "cb1",
                    "data": f"ap:{video.id}",
                    "from": {"id": "1"},
                    "message": {"chat": {"id": "1"}, "message_id": 42},
                },
            )
            assert result["ok"] is True
            methods = [name for name, _ in calls]
            assert "answerCallbackQuery" in methods
            assert "editMessageText" in methods
            edit = next(payload for name, payload in calls if name == "editMessageText")
            assert "Approved" in edit["text"]
            await session.refresh(video)
            assert video.status == "generated"

    asyncio.run(run())


def test_callback_rejects_unknown_admin(monkeypatch):
    from app.services import telegram_control as tc

    calls = []

    async def fake_call(session, method, payload):
        calls.append(method)
        return {"ok": True}

    monkeypatch.setattr(tc, "_call", fake_call)

    async def run():
        await init_db()
        async with SessionLocal() as session:
            await put_setting(session, "telegram_admin_ids", "1")
            result = await tc.handle_callback(
                session,
                {"id": "cb2", "data": "generate", "from": {"id": "424242"}, "message": {"chat": {"id": "1"}, "message_id": 7}},
            )
            assert result.get("unauthorized") is True
            assert "generate" not in calls

    asyncio.run(run())
