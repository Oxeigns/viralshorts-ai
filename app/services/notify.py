from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.db import SessionLocal
from app.services.secrets import TELEGRAM_TOKEN, get_secret

logger = logging.getLogger(__name__)


async def notify(message: str) -> None:
    settings = get_settings()
    if not settings.telegram_notifications:
        return
    async with SessionLocal() as session:
        token = await get_secret(session, TELEGRAM_TOKEN)
        chat = settings.telegram_chat_id or next(iter(settings.telegram_admins()), "")
        if not token or not chat:
            logger.info("notify skipped: %s", message)
            return
        async with httpx.AsyncClient(timeout=20) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": message[:3900]},
            )
