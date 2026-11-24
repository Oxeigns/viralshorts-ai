"""Telegram remote-control surface. Only listed admin IDs may drive the bot."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AuditLog, GeneratedVideo, Job, PublishQueue, UploadHistory
from app.services.config_store import get_setting
from app.services.pipeline import run_generation
from app.services.predeploy import run_predeploy
from app.services.publisher import tick
from app.services.scheduler import get_or_create_settings, next_open_slot
from app.services.secrets import TELEGRAM_TOKEN, get_secret
from app.services.youtube import youtube_ready

logger = logging.getLogger(__name__)

API = "https://api.telegram.org"

MENU = [
    ["📊 Status", "⚙️ Config"],
    ["🚀 Generate Video", "📤 Publish Queue"],
    ["✅ Approve Video", "📈 Analytics"],
    ["⏸ Pause Automation", "▶️ Resume Automation"],
    ["🔍 Pre-Deploy Check", "🧪 Test Integrations"],
]


def _is_admin(user_id: str, configured: str) -> bool:
    allowed = {item.strip() for item in configured.split(",") if item.strip()}
    fallback = get_settings().telegram_admins()
    return user_id in allowed or user_id in fallback


async def _token(session: AsyncSession) -> str:
    return await get_secret(session, TELEGRAM_TOKEN)


async def send_message(session: AsyncSession, chat_id: str | int, text: str, reply_markup: dict | None = None) -> dict:
    token = await _token(session)
    if not token:
        return {"ok": False, "error": "telegram token missing"}
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:3900]}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{API}/bot{token}/sendMessage", json=payload)
    return resp.json() if resp.content else {"ok": False, "status": resp.status_code}


def menu_keyboard() -> dict:
    return {
        "keyboard": [[{"text": label} for label in row] for row in MENU],
        "resize_keyboard": True,
    }


async def audit(session: AsyncSession, actor: str, action: str, detail: str = "") -> None:
    session.add(AuditLog(actor=actor, action=action, detail=detail[:500]))
    await session.commit()


async def status_text(session: AsyncSession) -> str:
    settings = await get_or_create_settings(session)
    generated = await session.scalar(select(func.count(GeneratedVideo.id))) or 0
    published = await session.scalar(select(func.count(UploadHistory.id))) or 0
    failed = await session.scalar(select(func.count(GeneratedVideo.id)).where(GeneratedVideo.status == "failed")) or 0
    queued = await session.scalar(select(func.count(PublishQueue.id)).where(PublishQueue.status == "scheduled")) or 0
    pending = await session.scalar(select(func.count(GeneratedVideo.id)).where(GeneratedVideo.status == "pending_approval")) or 0
    yt = await youtube_ready(session)
    return (
        "VIRALSHORTS status\n"
        f"automation: {'paused' if settings.paused else 'running'}\n"
        f"mode: {getattr(settings, 'publishing_mode', 'approval')} · {settings.daily_target}/day · {settings.timezone}\n"
        f"slots: {settings.slots}\n"
        f"generated={generated} published={published} failed={failed}\n"
        f"queue={queued} pending_approval={pending}\n"
        f"youtube={'connected' if yt else 'not ready'}"
    )


async def config_text(session: AsyncSession) -> str:
    return (
        "Configuration Center lives at /configuration\n"
        f"Hacker AI model: {(await get_setting(session, 'hackerai_model')) or 'auto'}\n"
        f"Voice language: {(await get_setting(session, 'voice_language')) or 'en'}\n"
        f"Publish mode: {(await get_setting(session, 'publishing_mode')) or 'approval'}\n"
        "Secrets are encrypted. This chat never prints API keys."
    )


async def analytics_text(session: AsyncSession) -> str:
    from app.models import AnalyticsSnapshot

    views = await session.scalar(select(func.coalesce(func.sum(AnalyticsSnapshot.views), 0))) or 0
    likes = await session.scalar(select(func.coalesce(func.sum(AnalyticsSnapshot.likes), 0))) or 0
    latest = await session.scalar(select(AnalyticsSnapshot).order_by(AnalyticsSnapshot.captured_at.desc()))
    extra = ""
    if latest:
        extra = f"\nlatest snapshot: {latest.youtube_video_id or '-'} views={latest.views} likes={latest.likes}"
    return f"Analytics (stored snapshots only)\nviews={views} likes={likes}{extra}"


async def queue_text(session: AsyncSession) -> str:
    rows = (await session.scalars(select(PublishQueue).order_by(PublishQueue.scheduled_for.asc()).limit(8))).all()
    if not rows:
        return "Publish queue is empty."
    lines = ["Publish queue"]
    for item in rows:
        lines.append(f"#{item.video_id} {item.status} @ {item.scheduled_for} attempts={item.attempts}")
    return "\n".join(lines)


async def approve_next(session: AsyncSession) -> str:
    video = await session.scalar(
        select(GeneratedVideo)
        .where(GeneratedVideo.status == "pending_approval")
        .order_by(GeneratedVideo.created_at.asc())
    )
    if video is None:
        return "No video is waiting for approval."
    existing = await session.scalar(select(PublishQueue).where(PublishQueue.video_id == video.id))
    video.status = "generated"
    if existing is None:
        session.add(PublishQueue(video_id=video.id, scheduled_for=await next_open_slot(session), status="scheduled"))
    elif existing.status in {"skipped", "blocked"}:
        existing.status = "scheduled"
        existing.scheduled_for = await next_open_slot(session)
    await session.commit()
    return f"Approved video #{video.id} · {video.title}"


async def handle_text(session: AsyncSession, user_id: str, chat_id: str, text: str) -> str:
    command = (text or "").strip()
    lowered = command.lower()
    if command in {"📊 Status", "/status"} or lowered == "status":
        return await status_text(session)
    if command in {"⚙️ Config", "/config"} or lowered == "config":
        return await config_text(session)
    if command in {"🚀 Generate Video", "/generate"} or lowered.startswith("generate"):
        video = await run_generation(session)
        await audit(session, user_id, "generate", f"video {video.id} {video.status}")
        return f"Generated #{video.id} status={video.status} quality={video.quality_score} {video.title}"
    if command in {"📤 Publish Queue", "/queue"} or lowered == "queue":
        return await queue_text(session)
    if command in {"✅ Approve Video", "/approve"} or lowered.startswith("approve"):
        result = await approve_next(session)
        await audit(session, user_id, "approve", result)
        return result
    if command in {"📈 Analytics", "/analytics"} or lowered == "analytics":
        return await analytics_text(session)
    if command in {"⏸ Pause Automation", "/pause"} or lowered == "pause":
        settings = await get_or_create_settings(session)
        settings.paused = True
        await session.commit()
        await audit(session, user_id, "pause")
        return "Automation paused."
    if command in {"▶️ Resume Automation", "/resume"} or lowered == "resume":
        settings = await get_or_create_settings(session)
        settings.paused = False
        await session.commit()
        await audit(session, user_id, "resume")
        return "Automation resumed."
    if command in {"🔍 Pre-Deploy Check", "/predeploy"} or "pre-deploy" in lowered or lowered == "predeploy":
        report = await run_predeploy(session)
        blockers = "\n".join(f"- {item}" for item in report["blockers"][:6]) or "none"
        return f"Readiness {report['score']}/100 ready={report['ready']}\nblockers:\n{blockers}"
    if command in {"🧪 Test Integrations", "/test"} or lowered.startswith("test"):
        result = await tick(session)
        jobs = await session.scalar(select(func.count(Job.id))) or 0
        return f"Tick complete generated={result['generated']} published={result['published']} jobs={jobs} errors={result['errors']}"
    if lowered in {"/start", "/help", "help", "menu"}:
        return "VIRALSHORTS remote control. Use the menu. Keys are never echoed."
    return "Unknown command. Use the menu or /status /generate /queue /approve /pause /resume /predeploy."


async def handle_update(session: AsyncSession, update: dict) -> dict:
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
        await send_message(session, chat_id, "Unauthorized. Your Telegram user id is not in TELEGRAM_ADMIN_IDS.")
        return {"ok": False, "unauthorized": True, "user_id": user_id}
    reply = await handle_text(session, user_id, chat_id, text)
    await send_message(session, chat_id, reply, reply_markup=menu_keyboard())
    return {"ok": True, "reply": reply}


async def set_webhook(session: AsyncSession, public_url: str) -> dict:
    token = await _token(session)
    if not token:
        return {"ok": False, "error": "telegram token missing"}
    hook = public_url.rstrip("/") + "/api/telegram/webhook"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{API}/bot{token}/setWebhook", json={"url": hook, "drop_pending_updates": False})
    return resp.json() if resp.content else {"ok": False, "status": resp.status_code}
