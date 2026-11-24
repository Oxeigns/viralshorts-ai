from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import EncryptedSecret
from app.security import decrypt_secret, encrypt_secret, secret_hint

ELEVENLABS = "elevenlabs_api_key"
ELEVENLABS_VOICE = "elevenlabs_voice_id"
GOOGLE_CLIENT_ID = "google_client_id"
GOOGLE_CLIENT_SECRET = "google_client_secret"
PUBLIC_BASE_URL = "public_base_url"
HACKERAI = "hackerai_api_key"
TELEGRAM_TOKEN = "telegram_bot_token"
STORAGE_ACCESS = "storage_access_key"
STORAGE_SECRET = "storage_secret_key"


def _env_fallback(name: str) -> str:
    settings = get_settings()
    mapping = {
        ELEVENLABS: settings.elevenlabs_api_key or os.environ.get("ELEVENLABS_API_KEY", ""),
        ELEVENLABS_VOICE: settings.elevenlabs_voice_id,
        GOOGLE_CLIENT_ID: settings.google_client_id,
        GOOGLE_CLIENT_SECRET: settings.google_client_secret,
        PUBLIC_BASE_URL: settings.app_base_url,
        HACKERAI: settings.hackerai_api_key or os.environ.get("HACKERAI_API_KEY", ""),
        TELEGRAM_TOKEN: settings.telegram_bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        STORAGE_ACCESS: settings.storage_access_key,
        STORAGE_SECRET: settings.storage_secret_key,
        "database_url": settings.database_url,
    }
    return (mapping.get(name) or "").strip()


async def get_secret(session: AsyncSession, name: str) -> str:
    row = await session.scalar(select(EncryptedSecret).where(EncryptedSecret.name == name))
    if row and row.ciphertext:
        try:
            return decrypt_secret(row.ciphertext)
        except Exception:
            pass
    return _env_fallback(name)


async def put_secret(session: AsyncSession, name: str, value: str) -> EncryptedSecret:
    value = (value or "").strip()
    row = await session.scalar(select(EncryptedSecret).where(EncryptedSecret.name == name))
    if row is None:
        row = EncryptedSecret(name=name, ciphertext="", hint="")
        session.add(row)
    if value:
        row.ciphertext = encrypt_secret(value)
        row.hint = secret_hint(value)
    else:
        row.ciphertext = ""
        row.hint = ""
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(row)
    return row


async def secret_status(session: AsyncSession, name: str) -> dict:
    row = await session.scalar(select(EncryptedSecret).where(EncryptedSecret.name == name))
    env_value = _env_fallback(name)
    configured = bool((row and row.ciphertext) or env_value)
    hint = ""
    source = "missing"
    if row and row.ciphertext:
        hint = row.hint
        source = "encrypted"
    elif env_value:
        hint = secret_hint(env_value)
        source = "environment"
    return {"name": name, "configured": configured, "hint": hint, "source": source}


async def bootstrap_env_secrets(session: AsyncSession) -> None:
    """Persist env-provided secrets as ciphertext so reports and APIs never see plaintext."""
    pairs = {
        ELEVENLABS: _env_fallback(ELEVENLABS),
        GOOGLE_CLIENT_ID: _env_fallback(GOOGLE_CLIENT_ID),
        GOOGLE_CLIENT_SECRET: _env_fallback(GOOGLE_CLIENT_SECRET),
        HACKERAI: _env_fallback(HACKERAI),
        TELEGRAM_TOKEN: _env_fallback(TELEGRAM_TOKEN),
    }
    for name, value in pairs.items():
        if not value:
            continue
        existing = await session.scalar(select(EncryptedSecret).where(EncryptedSecret.name == name))
        if existing and existing.ciphertext:
            continue
        await put_secret(session, name, value)
