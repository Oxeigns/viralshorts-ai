"""Background tick loop for Render worker service."""

from __future__ import annotations

import asyncio
import logging
import time

from app.db import SessionLocal, init_db
from app.services.publisher import tick
from app.services.secrets import bootstrap_env_secrets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("viralshorts.worker")


async def loop() -> None:
    await init_db()
    async with SessionLocal() as session:
        await bootstrap_env_secrets(session)
    while True:
        try:
            async with SessionLocal() as session:
                result = await tick(session)
            logger.info("tick %s", result)
        except Exception:
            logger.exception("tick failed")
        await asyncio.sleep(600)


if __name__ == "__main__":
    time.sleep(2)
    asyncio.run(loop())
