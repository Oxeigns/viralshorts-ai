from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(get_settings().normalized_database_url(), future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

_SQLITE_PATCHES = (
    ("generated_videos", "voice_used", "TEXT DEFAULT ''"),
    ("generated_videos", "category", "TEXT DEFAULT ''"),
    ("scheduler_settings", "publishing_mode", "TEXT DEFAULT 'approval'"),
    ("scheduler_settings", "approval_required", "BOOLEAN DEFAULT 1"),
    ("scheduler_settings", "video_category", "TEXT DEFAULT '24'"),
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def _patch_sqlite() -> None:
    url = get_settings().normalized_database_url()
    if not url.startswith("sqlite"):
        return
    async with engine.begin() as conn:
        for table, column, decl in _SQLITE_PATCHES:
            rows = await conn.execute(text(f"PRAGMA table_info({table})"))
            names = {row[1] for row in rows}
            if names and column not in names:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {decl}"))


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _patch_sqlite()
