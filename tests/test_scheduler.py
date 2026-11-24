import asyncio
from datetime import datetime, timezone

from app.db import SessionLocal, init_db
from app.services.scheduler import get_or_create_settings, next_open_slot


def test_next_slot_is_timezone_aware():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            settings = await get_or_create_settings(session)
            assert settings.timezone == "Asia/Kolkata"
            slot = await next_open_slot(session)
            assert slot.tzinfo is not None
            assert slot > datetime.now(timezone.utc)

    asyncio.run(run())
