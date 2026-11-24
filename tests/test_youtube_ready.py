"""youtube_ready must not claim publishing works without a connected channel."""

import asyncio

from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.models import Channel, OAuthCredential
from app.services.secrets import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, put_secret
from app.services.youtube import youtube_ready


async def _clear(session):
    await session.execute(delete(OAuthCredential))
    await session.execute(delete(Channel))
    await session.commit()


def test_not_ready_without_credentials():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            await _clear(session)
            assert await youtube_ready(session) is False

    asyncio.run(run())


def test_not_ready_with_credentials_but_no_channel():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            await _clear(session)
            await put_secret(session, GOOGLE_CLIENT_ID, "test.apps.googleusercontent.com")
            await put_secret(session, GOOGLE_CLIENT_SECRET, "GOCSPX-test")
            assert await youtube_ready(session) is False

    asyncio.run(run())


def test_ready_only_after_channel_and_token():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            await _clear(session)
            await put_secret(session, GOOGLE_CLIENT_ID, "test.apps.googleusercontent.com")
            await put_secret(session, GOOGLE_CLIENT_SECRET, "GOCSPX-test")
            channel = Channel(youtube_channel_id="UC_test", title="Test Channel", connected=True)
            session.add(channel)
            await session.commit()
            await session.refresh(channel)
            session.add(OAuthCredential(channel_id=channel.id, encrypted_refresh_token="ciphertext"))
            await session.commit()
            assert await youtube_ready(session) is True

    asyncio.run(run())
