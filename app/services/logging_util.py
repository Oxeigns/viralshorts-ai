from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppLog


async def log_event(session: AsyncSession, message: str, level: str = "info") -> None:
    session.add(AppLog(level=level, message=message))
    await session.commit()
