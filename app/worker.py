from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.services.publisher import tick
from app.services.secrets import bootstrap_env_secrets

logging.basicConfig(level=get_settings().log_level.upper())
logger = logging.getLogger("viralshorts.worker")
INTERVAL_SECONDS = 600


async def _once() -> dict:
    async with SessionLocal() as session:
        return await tick(session)


async def main() -> None:
    cfg = get_settings()
    if not cfg.worker_enabled:
        logger.info("WORKER_ENABLED is false; worker exiting")
        return
    await init_db()
    async with SessionLocal() as session:
        await bootstrap_env_secrets(session)
    logger.info("worker started interval=%ss timezone=%s", INTERVAL_SECONDS, cfg.timezone)
    while True:
        try:
            result = await _once()
            logger.info("tick %s", result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("tick failed: %s", exc)
        await asyncio.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
