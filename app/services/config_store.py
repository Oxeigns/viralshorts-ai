from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.config_schema import GROUPS, all_fields
from app.models import AppSetting, Channel, OAuthCredential
from app.services.secrets import (
    ELEVENLABS,
    ELEVENLABS_VOICE,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    HACKERAI,
    PUBLIC_BASE_URL,
    STORAGE_ACCESS,
    STORAGE_SECRET,
    TELEGRAM_TOKEN,
    bootstrap_env_secrets,
    get_secret,
    put_secret,
    secret_status,
)
from app.services.scheduler import get_or_create_settings
from app.services.youtube import callback_uri, youtube_ready

SECRET_KEYS = {
    "hackerai_api_key": HACKERAI,
    "elevenlabs_api_key": ELEVENLABS,
    "google_client_secret": GOOGLE_CLIENT_SECRET,
    "telegram_bot_token": TELEGRAM_TOKEN,
    "storage_access_key": STORAGE_ACCESS,
    "storage_secret_key": STORAGE_SECRET,
    "database_url": "database_url",
}

SETTING_ENV = {item["key"]: item.get("env") or "" for item in all_fields()}


def _settings_fallback(key: str) -> str:
    cfg = get_settings()
    mapping = {
        "hackerai_api_key": cfg.hackerai_api_key,
        "hackerai_api_base_url": cfg.hackerai_api_base_url,
        "hackerai_model": cfg.hackerai_model,
        "elevenlabs_api_key": cfg.elevenlabs_api_key,
        "elevenlabs_voice_id": cfg.elevenlabs_voice_id,
        "default_voice": cfg.default_voice,
        "voice_language": cfg.voice_language,
        "voice_stability": str(cfg.voice_stability),
        "voice_similarity": str(cfg.voice_similarity),
        "telegram_bot_token": cfg.telegram_bot_token,
        "telegram_admin_ids": cfg.telegram_admin_ids or cfg.telegram_chat_id,
        "telegram_notifications": "true" if cfg.telegram_notifications else "false",
        "google_client_id": cfg.google_client_id,
        "google_client_secret": cfg.google_client_secret,
        "daily_target": str(cfg.daily_publish_target),
        "publish_time_1": cfg.publish_time_1,
        "publish_time_2": cfg.publish_time_2,
        "publish_time_3": cfg.publish_time_3,
        "timezone": cfg.timezone,
        "default_language": cfg.default_language,
        "video_category": cfg.youtube_category_id,
        "publishing_mode": cfg.publishing_mode,
        "approval_required": "true" if cfg.approval_required else "false",
        "database_url": cfg.database_url,
        "redis_url": cfg.redis_url,
        "object_storage_url": cfg.object_storage_url,
        "storage_access_key": cfg.storage_access_key,
        "storage_secret_key": cfg.storage_secret_key,
        "object_storage_bucket": cfg.object_storage_bucket,
        "app_env": cfg.app_env,
        "frontend_url": cfg.frontend_url or cfg.app_base_url,
        "app_base_url": cfg.app_base_url,
        "worker_enabled": "true" if cfg.worker_enabled else "false",
        "scheduler_enabled": "true" if cfg.scheduler_enabled else "false",
        "log_level": cfg.log_level,
    }
    return str(mapping.get(key) or "").strip()


async def get_setting(session: AsyncSession, name: str) -> str:
    if name in SECRET_KEYS:
        return await get_secret(session, SECRET_KEYS[name])
    row = await session.scalar(select(AppSetting).where(AppSetting.name == name))
    if row and row.value:
        return row.value
    return _settings_fallback(name)


async def put_setting(session: AsyncSession, name: str, value: str) -> None:
    value = (value or "").strip()
    if name in SECRET_KEYS:
        if value:
            await put_secret(session, SECRET_KEYS[name], value)
        return
    row = await session.scalar(select(AppSetting).where(AppSetting.name == name))
    if row is None:
        row = AppSetting(name=name, value=value)
        session.add(row)
    else:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)
    await session.commit()


async def apply_runtime_settings(session: AsyncSession) -> None:
    settings = await get_or_create_settings(session)
    daily = await get_setting(session, "daily_target")
    if daily.isdigit():
        settings.daily_target = max(1, min(int(daily), 6))
    settings.timezone = (await get_setting(session, "timezone")) or settings.timezone
    t1 = await get_setting(session, "publish_time_1")
    t2 = await get_setting(session, "publish_time_2")
    t3 = await get_setting(session, "publish_time_3")
    slots = ",".join(item for item in (t1, t2, t3) if item)
    if slots:
        settings.slots = slots
    settings.language = (await get_setting(session, "default_language")) or settings.language
    mode = (await get_setting(session, "publishing_mode")).lower() or "approval"
    settings.publishing_mode = mode if mode in {"automatic", "approval", "manual"} else "approval"
    settings.approval_required = (await get_setting(session, "approval_required")).lower() != "false"
    settings.video_category = (await get_setting(session, "video_category")) or "24"
    await session.commit()


async def save_form(session: AsyncSession, form: dict[str, str]) -> list[str]:
    saved: list[str] = []
    for item in all_fields():
        if item.get("readonly"):
            continue
        key = item["key"]
        if key not in form:
            continue
        value = (form.get(key) or "").strip()
        if item.get("secret") and not value:
            continue
        await put_setting(session, key, value)
        if key == "elevenlabs_voice_id" and value:
            await put_secret(session, ELEVENLABS_VOICE, value)
        if key == "google_client_id" and value:
            await put_secret(session, GOOGLE_CLIENT_ID, value)
        if key == "app_base_url" and value:
            await put_secret(session, PUBLIC_BASE_URL, value.rstrip("/"))
        saved.append(key)
    await apply_runtime_settings(session)
    return saved


async def public_snapshot(session: AsyncSession) -> dict:
    await bootstrap_env_secrets(session)
    channel = await session.scalar(select(Channel).limit(1))
    cred = await session.scalar(select(OAuthCredential).limit(1))
    groups = []
    configured = missing = invalid = 0
    for group in GROUPS:
        fields = []
        for item in group["fields"]:
            key = item["key"]
            status = "missing"
            hint = ""
            value = ""
            if key == "hackerai_provider_status":
                value = await get_setting(session, key) or "unchecked"
                status = "configured" if value not in {"", "unchecked", "error"} else "missing"
            elif key == "youtube_channel_status":
                value = channel.title if channel and channel.connected else "not connected"
                status = "configured" if channel and channel.connected else "missing"
            elif key == "youtube_refresh_token":
                status = "configured" if cred and cred.encrypted_refresh_token else "missing"
                hint = "encrypted" if status == "configured" else ""
            elif item.get("secret"):
                vault = SECRET_KEYS.get(key)
                if vault:
                    info = await secret_status(session, vault)
                    status = "configured" if info["configured"] else "missing"
                    hint = info["hint"]
                else:
                    raw = await get_setting(session, key)
                    status = "configured" if raw else "missing"
                    hint = "••••" if raw else ""
            else:
                value = await get_setting(session, key)
                if value:
                    status = "configured"
                elif item.get("required"):
                    status = "missing"
                else:
                    status = "optional"
            if status == "configured":
                configured += 1
            elif status == "invalid":
                invalid += 1
            elif item.get("required") or status == "missing":
                if item.get("required") and status == "missing":
                    missing += 1
            fields.append(
                {
                    "key": key,
                    "label": item["label"],
                    "secret": bool(item.get("secret")),
                    "required": bool(item.get("required")),
                    "readonly": bool(item.get("readonly")),
                    "placeholder": item.get("placeholder", ""),
                    "obtain": item.get("obtain", ""),
                    "env": item.get("env", ""),
                    "status": status,
                    "hint": hint,
                    "value": "" if item.get("secret") else value,
                }
            )
        groups.append({"id": group["id"], "title": group["title"], "help": group["help"], "fields": fields})
    return {
        "groups": groups,
        "counts": {"configured": configured, "missing": missing, "invalid": invalid},
        "youtube_ready": await youtube_ready(session),
        "redirect_uri": callback_uri(await get_secret(session, PUBLIC_BASE_URL)),
        "channel": channel.title if channel and channel.connected else "",
    }
