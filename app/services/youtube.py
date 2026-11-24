from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Channel, GeneratedVideo, OAuthCredential, PublishQueue, UploadHistory
from app.security import decrypt_secret, encrypt_secret
from app.services.notify import notify
from app.services.secrets import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, PUBLIC_BASE_URL, get_secret

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"


def youtube_configured() -> bool:
    settings = get_settings()
    return bool(settings.google_client_id and settings.google_client_secret)


def callback_uri(base_url: str | None = None) -> str:
    settings = get_settings()
    origin = (base_url or settings.app_base_url).rstrip("/")
    configured = (settings.google_redirect_uri or "").strip()
    if configured and not configured.endswith("/api/integrations/youtube/callback"):
        return configured
    if configured.startswith("http://127.0.0.1") and origin and "127.0.0.1" not in origin:
        return f"{origin}/api/integrations/youtube/callback"
    if configured:
        return configured
    return f"{origin}/api/integrations/youtube/callback"


async def youtube_ready(session: AsyncSession) -> bool:
    creds = await oauth_settings(session)
    return bool(creds["client_id"] and creds["client_secret"])


async def oauth_settings(session: AsyncSession) -> dict[str, str]:
    return {
        "client_id": await get_secret(session, GOOGLE_CLIENT_ID),
        "client_secret": await get_secret(session, GOOGLE_CLIENT_SECRET),
        "redirect_uri": callback_uri(await get_secret(session, PUBLIC_BASE_URL)),
    }


def authorization_url(state: str, client_id: str | None = None, redirect_uri: str | None = None) -> str:
    settings = get_settings()
    params = {
        "client_id": client_id or settings.google_client_id,
        "redirect_uri": redirect_uri or callback_uri(),
        "response_type": "code",
        "scope": " ".join(settings.youtube_scope_list),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return AUTH_URL + "?" + urlencode(params)


async def exchange_code(code: str, session: AsyncSession | None = None) -> dict[str, Any]:
    if session is not None:
        creds = await oauth_settings(session)
    else:
        creds = {
            "client_id": get_settings().google_client_id,
            "client_secret": get_settings().google_client_secret,
            "redirect_uri": callback_uri(),
        }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "redirect_uri": creds["redirect_uri"],
                "grant_type": "authorization_code",
            },
        )
    resp.raise_for_status()
    return resp.json()


async def refresh_access_token(refresh_token: str, session: AsyncSession | None = None) -> dict[str, Any]:
    if session is not None:
        creds = await oauth_settings(session)
    else:
        creds = {
            "client_id": get_settings().google_client_id,
            "client_secret": get_settings().google_client_secret,
        }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    resp.raise_for_status()
    return resp.json()


async def store_tokens(session: AsyncSession, token: dict[str, Any]) -> Channel:
    access = token.get("access_token", "")
    refresh = token.get("refresh_token", "")
    expiry = datetime.now(timezone.utc) + timedelta(seconds=int(token.get("expires_in") or 3500))
    channel_id = "unknown"
    title = "YouTube channel"
    thumb = ""
    if access:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{YOUTUBE_API}/channels",
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {access}"},
            )
        if resp.status_code == 200:
            items = resp.json().get("items") or []
            if items:
                channel_id = items[0]["id"]
                snippet = items[0].get("snippet") or {}
                title = snippet.get("title") or title
                thumb = ((snippet.get("thumbnails") or {}).get("default") or {}).get("url") or ""

    existing = await session.scalar(select(Channel).where(Channel.youtube_channel_id == channel_id))
    if existing is None:
        existing = Channel(youtube_channel_id=channel_id, title=title, thumbnail_url=thumb, connected=True)
        session.add(existing)
        await session.flush()
    else:
        existing.title = title
        existing.thumbnail_url = thumb
        existing.connected = True

    cred = await session.scalar(select(OAuthCredential).where(OAuthCredential.channel_id == existing.id))
    if cred is None:
        cred = OAuthCredential(channel_id=existing.id, encrypted_refresh_token="", encrypted_access_token="")
        session.add(cred)
    if refresh:
        cred.encrypted_refresh_token = encrypt_secret(refresh)
    if access:
        cred.encrypted_access_token = encrypt_secret(access)
    cred.token_expiry = expiry
    cred.scopes = " ".join(get_settings().youtube_scope_list)
    await session.commit()
    await session.refresh(existing)
    return existing


async def upload_video(session: AsyncSession, video: GeneratedVideo) -> UploadHistory:
    already = await session.scalar(select(UploadHistory).where(UploadHistory.video_id == video.id))
    if already:
        return already
    cred = await session.scalar(select(OAuthCredential))
    if cred is None or not await youtube_ready(session):
        raise RuntimeError("YouTube OAuth is not connected")
    if not video.file_path or not Path(video.file_path).exists():
        raise RuntimeError("video file missing")

    refresh = decrypt_secret(cred.encrypted_refresh_token) if cred.encrypted_refresh_token else ""
    access = decrypt_secret(cred.encrypted_access_token) if cred.encrypted_access_token else ""
    if cred.token_expiry and cred.token_expiry < datetime.now(timezone.utc) and refresh:
        fresh = await refresh_access_token(refresh, session)
        access = fresh.get("access_token", access)
        cred.encrypted_access_token = encrypt_secret(access)
        cred.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=int(fresh.get("expires_in") or 3500))
        await session.commit()

    metadata = {
        "snippet": {
            "title": video.title[:100],
            "description": video.description,
            "tags": [t.strip("# ") for t in video.hashtags.split() if t.strip()][:10],
            "categoryId": "24",
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    headers = {"Authorization": f"Bearer {access}", "Content-Type": "application/json; charset=UTF-8"}
    params = {"uploadType": "resumable", "part": "snippet,status"}
    async with httpx.AsyncClient(timeout=60) as client:
        init = await client.post(
            "https://www.googleapis.com/upload/youtube/v3/videos",
            params=params,
            headers=headers,
            content=json.dumps(metadata),
        )
        if init.status_code >= 400:
            raise RuntimeError(f"YouTube init failed: {init.status_code} {init.text[:200]}")
        upload_url = init.headers.get("Location")
        if not upload_url:
            raise RuntimeError("YouTube did not return an upload URL")
        data = Path(video.file_path).read_bytes()
        put = await client.put(
            upload_url,
            headers={"Authorization": f"Bearer {access}", "Content-Type": "video/mp4"},
            content=data,
        )
        if put.status_code >= 400:
            raise RuntimeError(f"YouTube upload failed: {put.status_code} {put.text[:200]}")
        body = put.json() if put.content else {}
    yt_id = body.get("id", "")
    url = f"https://youtu.be/{yt_id}" if yt_id else ""
    history = UploadHistory(
        video_id=video.id,
        youtube_video_id=yt_id,
        youtube_url=url,
        visibility="public",
        status="uploaded",
    )
    session.add(history)
    video.status = "published"
    queue = await session.scalar(select(PublishQueue).where(PublishQueue.video_id == video.id))
    if queue:
        queue.status = "published"
    await session.commit()
    await notify(f"Published: {video.title} {url}")
    return history
