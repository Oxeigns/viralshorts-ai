from __future__ import annotations

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


async def notify(text: str) -> bool:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.info("telegram skipped: %s", text)
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                json={"chat_id": settings.telegram_chat_id, "text": text[:3500]},
            )
        return resp.status_code == 200
    except httpx.HTTPError as exc:
        logger.warning("telegram notify failed: %s", exc)
        return False
