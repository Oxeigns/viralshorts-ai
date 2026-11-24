"""Telegram remote control.

Inline buttons are the primary surface. Every action is a callback so the chat
stays one editable message instead of a growing wall of text.

Video review has its own screen: a paged list of videos where each row carries
Approve, Reject and Watch buttons, so a specific video can be judged instead of
only "approve the oldest one".

Only numeric ids in TELEGRAM_ADMIN_IDS may drive the bot.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AnalyticsSnapshot, AuditLog, GeneratedVideo, Job, PublishQueue, UploadHistory
from app.services.config_store import get_setting, put_setting
from app.services.jobs import enqueue_generation, failed_jobs
from app.services.predeploy import run_predeploy
from app.services.publisher import tick
from app.services.scheduler import get_or_create_settings, next_open_slot
from app.services.secrets import TELEGRAM_TOKEN, get_secret
from app.services.youtube import youtube_ready

logger = logging.getLogger(__name__)

API = "https://api.telegram.org"
VIDEO_PAGE_SIZE = 4

MODE_TITLES = {
    "pending": "Waiting for approval",
    "queued": "Approved and queued",
    "all": "Recent videos",
    "failed": "Failed videos",
}


def _is_admin(user_id: str, configured: str) -> bool:
    allowed = {item.strip() for item in (configured or "").split(",") if item.strip()}
    fallback = get_settings().telegram_admins()
    return bool(user_id) and (user_id in allowed or user_id in fallback)


async def _token(session: AsyncSession) -> str:
    return await get_secret(session, TELEGRAM_TOKEN)


async def _call(session: AsyncSession, method: str, payload: dict[str, Any]) -> dict:
    token = await _token(session)
    if not token:
        return {"ok": False, "error": "telegram token missing"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{API}/bot{token}/{method}", json=payload)
        return resp.json() if resp.content else {"ok": False, "status": resp.status_code}
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram %s failed: %s", method, exc)
        return {"ok": False, "error": str(exc)}


async def send_message(session: AsyncSession, chat_id: str | int, text: str, markup: dict | None = None) -> dict:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:3900],
        "disable_web_page_preview": True,
    }
    if markup:
        payload["reply_markup"] = markup
    return await _call(session, "sendMessage", payload)


async def edit_message(session: AsyncSession, chat_id: str | int, message_id: int, text: str, markup: dict | None = None) -> dict:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text[:3900],
        "disable_web_page_preview": True,
    }
    if markup:
        payload["reply_markup"] = markup
    return await _call(session, "editMessageText", payload)


async def answer_callback(session: AsyncSession, callback_id: str, text: str = "") -> dict:
    return await _call(session, "answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190]})


async def send_video_file(
    session: AsyncSession, chat_id: str | int, path: str, caption: str, markup: dict | None = None
) -> dict:
    """Upload the local MP4 to Telegram so the video can be judged without the dashboard."""
    token = await _token(session)
    if not token:
        return {"ok": False, "error": "telegram token missing"}
    file = Path(path) if path else None
    if file is None or not file.exists():
        return {"ok": False, "error": "video file is missing on disk"}
    data = {"chat_id": str(chat_id), "caption": caption[:1000], "supports_streaming": "true"}
    if markup:
        data["reply_markup"] = json.dumps(markup)
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(
                f"{API}/bot{token}/sendVideo",
                data=data,
                files={"video": (file.name, file.read_bytes(), "video/mp4")},
            )
        return resp.json() if resp.content else {"ok": False, "status": resp.status_code}
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram sendVideo failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def main_keyboard() -> dict:
    """Inline keyboard. Two columns, grouped by what the operator does next."""
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Status", "callback_data": "status"},
                {"text": "🚀 Generate", "callback_data": "generate"},
            ],
            [
                {"text": "🎬 Videos", "callback_data": "ls:pending:0"},
                {"text": "✅ Approve next", "callback_data": "approve"},
            ],
            [
                {"text": "📤 Queue", "callback_data": "queue"},
                {"text": "📈 Analytics", "callback_data": "analytics"},
            ],
            [
                {"text": "🤖 Auto-approve", "callback_data": "autoapprove"},
                {"text": "⚠️ Errors", "callback_data": "errors"},
            ],
            [
                {"text": "⏸ Pause", "callback_data": "pause"},
                {"text": "▶️ Resume", "callback_data": "resume"},
            ],
            [
                {"text": "🔍 Pre-deploy", "callback_data": "predeploy"},
                {"text": "❓ Help", "callback_data": "help"},
            ],
        ]
    }


def back_keyboard() -> dict:
    return {"inline_keyboard": [[{"text": "« Back", "callback_data": "status"}]]}


async def audit(session: AsyncSession, actor: str, action: str, detail: str = "") -> None:
    session.add(AuditLog(actor=actor, action=action, detail=detail[:500]))
    await session.commit()


async def auto_approve_enabled(session: AsyncSession) -> bool:
    raw = (await get_setting(session, "auto_approve") or "").strip().lower()
    if raw in {"true", "1", "yes", "on"}:
        return True
    if raw in {"false", "0", "no", "off"}:
        return False
    mode = (await get_setting(session, "publishing_mode") or "approval").lower()
    return mode == "automatic"


def _short(text: str, limit: int = 60) -> str:
    value = (text or "").strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _video_block(video: GeneratedVideo) -> str:
    score = int(video.quality_score or 0)
    seconds = int(video.duration_sec or 0)
    lines = [
        f"<b>#{video.id}</b> · {video.status} · quality {score} · {seconds}s",
        f"<b>{_short(video.title, 70)}</b>",
    ]
    if video.hashtags:
        lines.append(f"<i>{_short(video.hashtags, 90)}</i>")
    if video.status == "failed" and video.fail_reason:
        lines.append(f"reason: {_short(video.fail_reason, 100)}")
    return "\n".join(lines)


async def _video_page(
    session: AsyncSession, mode: str, page: int
) -> tuple[list[GeneratedVideo], int, int, int]:
    stmt = select(GeneratedVideo)
    if mode == "pending":
        stmt = stmt.where(GeneratedVideo.status == "pending_approval")
    elif mode == "queued":
        stmt = stmt.where(GeneratedVideo.status == "generated")
    elif mode == "failed":
        stmt = stmt.where(GeneratedVideo.status == "failed")
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pages = max(1, (total + VIDEO_PAGE_SIZE - 1) // VIDEO_PAGE_SIZE)
    page = max(0, min(int(page), pages - 1))
    rows = (
        await session.scalars(
            stmt.order_by(GeneratedVideo.id.desc())
            .offset(page * VIDEO_PAGE_SIZE)
            .limit(VIDEO_PAGE_SIZE)
        )
    ).all()
    return list(rows), total, pages, page


def _video_keyboard(rows: list[GeneratedVideo], mode: str, page: int, pages: int, total: int) -> dict:
    keyboard: list[list[dict]] = []
    for video in rows:
        can_decide = video.status not in {"published", "rejected"}
        if can_decide:
            keyboard.append(
                [
                    {"text": f"✅ Approve #{video.id}", "callback_data": f"ap:{video.id}"},
                    {"text": f"❌ Reject #{video.id}", "callback_data": f"rj:{video.id}"},
                ]
            )
        keyboard.append([{"text": f"▶️ Watch #{video.id}", "callback_data": f"wt:{video.id}"}])

    nav: list[dict] = []
    if page > 0:
        nav.append({"text": "« Prev", "callback_data": f"ls:{mode}:{page - 1}"})
    nav.append({"text": f"{page + 1}/{pages} · {total}", "callback_data": "noop"})
    if page < pages - 1:
        nav.append({"text": "Next »", "callback_data": f"ls:{mode}:{page + 1}"})
    keyboard.append(nav)
    keyboard.append(
        [
            {"text": "⏳ Pending", "callback_data": "ls:pending:0"},
            {"text": "📄 All", "callback_data": "ls:all:0"},
        ]
    )
    keyboard.append(
        [
            {"text": "🚀 Generate", "callback_data": "generate"},
            {"text": "« Menu", "callback_data": "status"},
        ]
    )
    return {"inline_keyboard": keyboard}


async def videos_view(session: AsyncSession, mode: str = "pending", page: int = 0) -> tuple[str, dict]:
    rows, total, pages, page = await _video_page(session, mode, page)
    header = f"<b>{MODE_TITLES.get(mode, 'Videos')}</b> · {total} total · page {page + 1}/{pages}"
    if not rows:
        body = "Nothing here." + (" Tap Generate to make one." if mode == "pending" else "")
    else:
        body = "\n\n".join(_video_block(video) for video in rows)
    return f"{header}\n\n{body}", _video_keyboard(rows, mode, page, pages, total)


async def _slot_label(session: AsyncSession, when: datetime | None) -> str:
    if when is None:
        return "the next slot"
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo(await get_setting(session, "timezone") or "Asia/Kolkata")
        return when.astimezone(tz).strftime("%d %b %H:%M %Z")
    except Exception:  # noqa: BLE001
        return when.strftime("%d %b %H:%M UTC")


async def approve_one(session: AsyncSession, video_id: int) -> str:
    """Approve one specific video and queue it for the next slot."""
    video = await session.get(GeneratedVideo, video_id)
    if video is None:
        return f"Video #{video_id} does not exist."
    if video.status == "published":
        return f"<b>#{video.id}</b> is already published."
    if video.status == "processing":
        return f"<b>#{video.id}</b> is still rendering. Try again in a moment."

    queue = await session.scalar(select(PublishQueue).where(PublishQueue.video_id == video.id))
    if queue is None:
        queue = PublishQueue(video_id=video.id, scheduled_for=await next_open_slot(session), status="scheduled")
        session.add(queue)
    elif queue.status in {"skipped", "blocked", "rejected"}:
        queue.status = "scheduled"
        queue.scheduled_for = await next_open_slot(session)
    video.status = "generated"
    await session.commit()
    await session.refresh(queue)
    when = await _slot_label(session, queue.scheduled_for)
    return f"Approved <b>#{video.id}</b> — {_short(video.title, 50)}\nQueued for {when}."


async def reject_one(session: AsyncSession, video_id: int) -> str:
    """Reject one video so it is never published."""
    video = await session.get(GeneratedVideo, video_id)
    if video is None:
        return f"Video #{video_id} does not exist."
    if video.status == "published":
        return f"<b>#{video.id}</b> is already published — delete it on YouTube instead."
    video.status = "rejected"
    queue = await session.scalar(select(PublishQueue).where(PublishQueue.video_id == video.id))
    if queue is not None and queue.status == "scheduled":
        queue.status = "skipped"
    await session.commit()
    return f"Rejected <b>#{video.id}</b> — {_short(video.title, 50)}\nIt will not be published."


async def _approve_next_legacy(session: AsyncSession) -> str:
    video = await session.scalar(
        select(GeneratedVideo)
        .where(GeneratedVideo.status == "pending_approval")
        .order_by(GeneratedVideo.created_at.asc())
    )
    if video is None:
        return "No video is waiting for approval."
    return await approve_one(session, video.id)


async def status_text(session: AsyncSession) -> str:
    settings = await get_or_create_settings(session)
    generated = await session.scalar(select(func.count(GeneratedVideo.id))) or 0
    published = await session.scalar(select(func.count(UploadHistory.id))) or 0
    failed = await session.scalar(select(func.count(GeneratedVideo.id)).where(GeneratedVideo.status == "failed")) or 0
    queued = await session.scalar(select(func.count(PublishQueue.id)).where(PublishQueue.status == "scheduled")) or 0
    pending = await session.scalar(
        select(func.count(GeneratedVideo.id)).where(GeneratedVideo.status == "pending_approval")
    ) or 0
    running = await session.scalar(select(func.count(Job.id)).where(Job.status.in_(("queued", "running")))) or 0
    yt = await youtube_ready(session)
    auto = await auto_approve_enabled(session)
    return (
        "<b>VIRALSHORTS</b>\n"
        f"automation: <b>{'paused' if settings.paused else 'running'}</b>\n"
        f"auto-approve: <b>{'on' if auto else 'off'}</b>\n"
        f"schedule: {settings.daily_target}/day · {settings.timezone} · {', '.join(settings.slots)}\n"
        f"videos: generated {generated} · published {published} · failed {failed}\n"
        f"waiting for you: <b>{pending}</b>\n"
        f"queue: {queued} scheduled · jobs running {running}\n"
        f"youtube: {'connected' if yt else '<b>not connected</b>'}"
    )


async def config_text(session: AsyncSession) -> str:
    return (
        "<b>Configuration</b>\n"
        f"Hacker AI model: {(await get_setting(session, 'hackerai_model')) or 'auto'}\n"
        f"Voice language: {(await get_setting(session, 'voice_language')) or 'en'}\n"
        f"Publishing mode: {(await get_setting(session, 'publishing_mode')) or 'approval'}\n"
        "Edit everything at /configuration. Keys stay encrypted and are never printed here."
    )


async def analytics_text(session: AsyncSession) -> str:
    views = await session.scalar(select(func.coalesce(func.sum(AnalyticsSnapshot.views), 0))) or 0
    likes = await session.scalar(select(func.coalesce(func.sum(AnalyticsSnapshot.likes), 0))) or 0
    latest = await session.scalar(select(AnalyticsSnapshot).order_by(AnalyticsSnapshot.captured_at.desc()))
    extra = ""
    if latest:
        extra = f"\nlatest: {latest.youtube_video_id or '-'} · views={latest.views} · likes={latest.likes}"
    return f"<b>Analytics</b>\nviews={views} likes={likes}{extra}\nOnly stored snapshots; nothing is invented."


async def queue_text(session: AsyncSession) -> str:
    rows = (await session.scalars(select(PublishQueue).order_by(PublishQueue.scheduled_for.asc()).limit(8))).all()
    if not rows:
        return "<b>Queue</b>\nEmpty. Generate a video first."
    lines = ["<b>Queue</b>"]
    for item in rows:
        lines.append(f"#{item.video_id} {item.status} @ {item.scheduled_for:%d %b %H:%M} · tries {item.attempts}")
    return "\n".join(lines)


async def errors_text(session: AsyncSession) -> str:
    jobs = await failed_jobs(session, limit=8)
    videos = (
        await session.scalars(
            select(GeneratedVideo).where(GeneratedVideo.status == "failed").order_by(GeneratedVideo.id.desc()).limit(5)
        )
    ).all()
    lines = ["<b>Recent errors</b>"]
    if not jobs and not videos:
        lines.append("Nothing failed. Clean.")
    for job in jobs:
        lines.append(f"job {job.id}: {(job.detail or '')[:140]}")
    for video in videos:
        lines.append(f"video {video.id}: {(video.fail_reason or '')[:140]}")
    return "\n".join(lines)


async def toggle_auto_approve(session: AsyncSession) -> str:
    current = await auto_approve_enabled(session)
    new_value = not current
    await put_setting(session, "auto_approve", "true" if new_value else "false")
    mode = (await get_setting(session, "publishing_mode") or "approval").lower()
    if new_value and mode != "automatic":
        await put_setting(session, "publishing_mode", "automatic")
    if not new_value and mode == "automatic":
        await put_setting(session, "publishing_mode", "approval")
    from app.services.config_store import apply_runtime_settings

    await apply_runtime_settings(session)
    return f"Auto-approve is now <b>{'ON' if new_value else 'OFF'}</b>.\n" + (
        "New videos queue for the next slot by themselves."
        if new_value
        else "New videos wait for you in Videos."
    )


async def predeploy_text(session: AsyncSession) -> str:
    report = await run_predeploy(session)
    blockers = "\n".join(f"• {item}" for item in report["blockers"][:6]) or "none"
    return (
        f"<b>Readiness {report['score']}/100</b> · ready={report['ready']}\n"
        f"blockers:\n{blockers}"
    )


HELP_TEXT = (
    "<b>VIRALSHORTS remote control</b>\n"
    "🎬 Videos — every video with Approve / Reject / Watch buttons.\n"
    "🚀 Generate — queues a render and replies at once, so the chat never times out.\n"
    "🤖 Auto-approve — decide whether finished videos wait for you."
)


async def handle_text(session: AsyncSession, user_id: str, chat_id: str, text: str) -> tuple[str, dict]:
    """Return (reply, keyboard). All long work is queued, never awaited inline."""
    command = (text or "").strip()
    lowered = command.lower()

    if command in {"📊 Status", "/status"} or lowered in {"status", "start", "/start"}:
        return await status_text(session), main_keyboard()
    if lowered in {"videos", "/videos", "/pending", "🎬 videos", "pending"}:
        return await videos_view(session, "pending", 0)
    if command in {"🚀 Generate Video", "/generate"} or lowered.startswith("generate"):
        job = await enqueue_generation(session, source=f"telegram:{user_id}")
        await audit(session, user_id, "generate", f"job {job.id}")
        return (
            f"Generation started as <b>job {job.id}</b>.\n"
            "Rendering runs in the background. Tap Videos in ~1 minute to review it.",
            main_keyboard(),
        )
    if command in {"⚙️ Config", "/config"} or lowered == "config":
        return await config_text(session), back_keyboard()
    if command in {"📤 Publish Queue", "/queue"} or lowered == "queue":
        return await queue_text(session), main_keyboard()
    if command in {"✅ Approve Video", "/approve"} or lowered.startswith("approve"):
        result = await _approve_next_legacy(session)
        await audit(session, user_id, "approve", result)
        return result, main_keyboard()
    if command in {"📈 Analytics", "/analytics"} or lowered == "analytics":
        return await analytics_text(session), main_keyboard()
    if command in {"⏸ Pause Automation", "/pause"} or lowered == "pause":
        settings = await get_or_create_settings(session)
        settings.paused = True
        await session.commit()
        await audit(session, user_id, "pause")
        return "Automation <b>paused</b>.", main_keyboard()
    if command in {"▶️ Resume Automation", "/resume"} or lowered == "resume":
        settings = await get_or_create_settings(session)
        settings.paused = False
        await session.commit()
        await audit(session, user_id, "resume")
        return "Automation <b>resumed</b>.", main_keyboard()
    if command in {"🔍 Pre-Deploy Check", "/predeploy"} or "pre-deploy" in lowered or lowered == "predeploy":
        return await predeploy_text(session), main_keyboard()
    if command in {"🧪 Test Integrations", "/test"} or lowered == "test":
        result = await tick(session)
        return (
            f"Tick done: generated={result['generated']} published={result['published']} "
            f"errors={result['errors'] or 'none'}",
            main_keyboard(),
        )
    if lowered in {"errors", "/errors", "⚠️ errors"}:
        return await errors_text(session), main_keyboard()
    if lowered in {"help", "/help", "❓ help"}:
        return HELP_TEXT, main_keyboard()
    return (
        "Unknown command. Use the buttons, or /videos /generate /queue /approve /pause /resume /errors.",
        main_keyboard(),
    )


async def handle_callback(session: AsyncSession, callback: dict) -> dict:
    callback_id = str(callback.get("id") or "")
    data = str(callback.get("data") or "")
    message = callback.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id") or "")
    message_id = message.get("message_id")
    user_id = str((callback.get("from") or {}).get("id") or "")
    admins = await get_setting(session, "telegram_admin_ids")
    if not _is_admin(user_id, admins):
        await answer_callback(session, callback_id, "Not authorized")
        return {"ok": False, "unauthorized": True}

    parts = data.split(":")
    op = parts[0]

    # Video list navigation and per-video decisions.
    if op == "ls":
        mode = parts[1] if len(parts) > 1 and parts[1] in MODE_TITLES else "pending"
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await answer_callback(session, callback_id)
        text, markup = await videos_view(session, mode, page)
        if message_id:
            await edit_message(session, chat_id, int(message_id), text, markup)
        else:
            await send_message(session, chat_id, text, markup)
        return {"ok": True, "data": data}

    if op == "noop":
        await answer_callback(session, callback_id)
        return {"ok": True, "data": data}

    if op in {"ap", "rj"} and len(parts) > 1 and parts[1].isdigit():
        video_id = int(parts[1])
        # Acknowledge first: Telegram drops callbacks that are not answered quickly.
        await answer_callback(session, callback_id, "Approved" if op == "ap" else "Rejected")
        result = await approve_one(session, video_id) if op == "ap" else await reject_one(session, video_id)
        await audit(session, user_id, "approve" if op == "ap" else "reject", result)
        text, markup = await videos_view(session, "pending", 0)
        combined = f"{result}\n\n{text}"
        if message_id:
            await edit_message(session, chat_id, int(message_id), combined, markup)
        else:
            await send_message(session, chat_id, combined, markup)
        return {"ok": True, "data": data, "result": result}

    if op == "wt" and len(parts) > 1 and parts[1].isdigit():
        video_id = int(parts[1])
        video = await session.get(GeneratedVideo, video_id)
        if video is None:
            await answer_callback(session, callback_id, "Not found")
            return {"ok": False, "data": data}
        await answer_callback(session, callback_id, "Sending video…")
        if not video.file_path or not Path(video.file_path).exists():
            await send_message(
                session,
                chat_id,
                f"<b>#{video.id}</b> has no file on disk any more "
                "(the instance likely restarted on ephemeral storage).\n"
                f"{_video_block(video)}",
                back_keyboard(),
            )
            return {"ok": False, "data": data, "reason": "file missing"}
        caption = f"#{video.id} · {video.status} · quality {int(video.quality_score or 0)}\n{video.title}"
        markup = {
            "inline_keyboard": [
                [
                    {"text": f"✅ Approve #{video.id}", "callback_data": f"ap:{video.id}"},
                    {"text": f"❌ Reject #{video.id}", "callback_data": f"rj:{video.id}"},
                ],
                [{"text": "« Back to videos", "callback_data": "ls:pending:0"}],
            ]
        }
        sent = await send_video_file(session, chat_id, video.file_path, caption, markup)
        if not sent.get("ok"):
            await send_message(session, chat_id, f"Could not send the video: {sent.get('error') or sent.get('description')}")
        return {"ok": bool(sent.get("ok")), "data": data}

    if data == "generate":
        job = await enqueue_generation(session, source=f"telegram:{user_id}")
        await audit(session, user_id, "generate", f"job {job.id}")
        text = f"Generation queued as <b>job {job.id}</b>.\nRendering runs in the background — tap Videos in a minute."
        await answer_callback(session, callback_id, "Queued")
    elif data == "autoapprove":
        text = await toggle_auto_approve(session)
        await audit(session, user_id, "auto_approve", text[:120])
        await answer_callback(session, callback_id, "Updated")
    elif data == "errors":
        text = await errors_text(session)
        await answer_callback(session, callback_id, "Errors")
    elif data == "queue":
        text = await queue_text(session)
        await answer_callback(session, callback_id)
    elif data == "approve":
        text = await _approve_next_legacy(session)
        await audit(session, user_id, "approve", text[:120])
        await answer_callback(session, callback_id, "Approved")
    elif data == "analytics":
        text = await analytics_text(session)
        await answer_callback(session, callback_id)
    elif data == "pause":
        settings = await get_or_create_settings(session)
        settings.paused = True
        await session.commit()
        await audit(session, user_id, "pause")
        text = "Automation <b>paused</b>."
        await answer_callback(session, callback_id, "Paused")
    elif data == "resume":
        settings = await get_or_create_settings(session)
        settings.paused = False
        await session.commit()
        await audit(session, user_id, "resume")
        text = "Automation <b>resumed</b>."
        await answer_callback(session, callback_id, "Resumed")
    elif data == "predeploy":
        text = await predeploy_text(session)
        await answer_callback(session, callback_id, "Checked")
    elif data == "help":
        text = HELP_TEXT
        await answer_callback(session, callback_id)
    else:
        text = await status_text(session)
        await answer_callback(session, callback_id)

    if chat_id and message_id:
        await edit_message(session, chat_id, int(message_id), text, main_keyboard())
    elif chat_id:
        await send_message(session, chat_id, text, main_keyboard())
    return {"ok": True, "data": data}


async def handle_update(session: AsyncSession, update: dict) -> dict:
    if update.get("callback_query"):
        return await handle_callback(session, update["callback_query"])

    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    chat_id = str(chat.get("id") or "")
    user_id = str(user.get("id") or "")
    text = message.get("text") or ""
    if not chat_id:
        return {"ok": False, "error": "no chat"}
    admins = await get_setting(session, "telegram_admin_ids")
    if not _is_admin(user_id, admins):
        await send_message(
            session,
            chat_id,
            f"Unauthorized. Your Telegram user id is <code>{user_id}</code>. "
            "Add it to TELEGRAM_ADMIN_IDS.",
        )
        return {"ok": False, "unauthorized": True, "user_id": user_id}
    reply, markup = await handle_text(session, user_id, chat_id, text)
    await send_message(session, chat_id, reply, markup)
    return {"ok": True, "reply": reply[:200]}


async def webhook_info(session: AsyncSession) -> dict:
    return await _call(session, "getWebhookInfo", {})


async def bot_identity(session: AsyncSession) -> dict:
    return await _call(session, "getMe", {})


async def set_webhook(session: AsyncSession, public_url: str) -> dict:
    hook = public_url.rstrip("/") + "/api/telegram/webhook"
    result = await _call(
        session,
        "setWebhook",
        {"url": hook, "drop_pending_updates": True, "allowed_updates": ["message", "callback_query"]},
    )
    result["webhook_url"] = hook
    return result


async def diagnose(session: AsyncSession, public_url: str) -> dict:
    """One call that explains why the bot is silent."""
    token = await _token(session)
    admins = await get_setting(session, "telegram_admin_ids")
    expected = public_url.rstrip("/") + "/api/telegram/webhook"
    out: dict = {
        "token_saved": bool(token),
        "admin_ids": admins,
        "expected_webhook": expected,
    }
    if not token:
        out["problem"] = "No bot token stored. Add it in Configuration Center."
        return out
    me = await bot_identity(session)
    out["bot_username"] = ((me.get("result") or {}).get("username")) if me.get("ok") else None
    out["get_me_error"] = None if me.get("ok") else me
    info = await webhook_info(session)
    result = info.get("result") or {}
    out["current_webhook"] = result.get("url") or ""
    out["pending_updates"] = result.get("pending_update_count")
    out["last_error"] = result.get("last_error_message")
    if not out["current_webhook"]:
        out["problem"] = "Webhook is not set. Telegram cannot reach this app."
    elif out["current_webhook"] != expected:
        out["problem"] = "Webhook points somewhere else."
    elif out["last_error"]:
        out["problem"] = "Telegram reported a delivery error."
    elif not admins:
        out["problem"] = "TELEGRAM_ADMIN_IDS is empty, so every command is rejected."
    else:
        out["problem"] = None
    return out
