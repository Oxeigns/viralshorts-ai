from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal, get_session, init_db
from app.models import (
    AnalyticsSnapshot,
    Channel,
    ContentIdea,
    GeneratedVideo,
    Job,
    MediaAsset,
    PublishQueue,
    UploadHistory,
    User,
)
from app.security import hash_password, new_csrf, read_session, sign_session, verify_password
from app.services.discovery import active_categories, discover_ideas
from app.services.pipeline import run_generation
from app.services.publisher import tick
from app.services.scheduler import get_or_create_settings
from app.services.secrets import (
    ELEVENLABS,
    ELEVENLABS_VOICE,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    PUBLIC_BASE_URL,
    bootstrap_env_secrets,
    get_secret,
    put_secret,
    secret_status,
)
from app.services.tts import list_voices, probe_account, restyle_clip
from app.services.youtube import (
    authorization_url,
    callback_uri,
    exchange_code,
    oauth_settings,
    store_tokens,
    youtube_ready,
)

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
scheduler = AsyncIOScheduler()
SESSION_COOKIE = "vs_session"


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    async with SessionLocal() as session:
        await _ensure_admin(session)
        await get_or_create_settings(session)
        await bootstrap_env_secrets(session)
    if not scheduler.running:
        scheduler.add_job(_scheduled_tick, "interval", minutes=10, id="pipeline-tick", replace_existing=True)
        scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="VIRALSHORTS AI", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _scheduled_tick() -> None:
    async with SessionLocal() as session:
        await tick(session)


async def _ensure_admin(session: AsyncSession) -> None:
    settings = get_settings()
    user = await session.scalar(select(User).where(User.username == settings.admin_username))
    if user is None:
        session.add(User(username=settings.admin_username, password_hash=hash_password(settings.admin_password)))
        await session.commit()


def _current_user(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return read_session(token)


def require_user(request: Request) -> dict:
    user = _current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    return user


async def _ctx(request: Request, session: AsyncSession, **extra):
    settings = await get_or_create_settings(session)
    channel = await session.scalar(select(Channel).limit(1))
    cfg = get_settings()
    eleven = await secret_status(session, ELEVENLABS)
    google_id = await secret_status(session, GOOGLE_CLIENT_ID)
    google_secret = await secret_status(session, GOOGLE_CLIENT_SECRET)
    yt_ready = await youtube_ready(session)
    providers = {
        "youtube": yt_ready and bool(channel and channel.connected),
        "elevenlabs": eleven["configured"],
        "pexels": bool(cfg.pexels_api_key),
        "pixabay": bool(cfg.pixabay_api_key),
        "ollama": bool(cfg.ollama_base_url),
        "openai": bool(cfg.openai_api_key),
        "telegram": bool(cfg.telegram_bot_token and cfg.telegram_chat_id),
    }
    data = {
        "request": request,
        "user": _current_user(request),
        "settings": settings,
        "channel": channel,
        "providers": providers,
        "youtube_ready": yt_ready,
        "elevenlabs": eleven,
        "google_id": google_id,
        "google_secret": google_secret,
        "redirect_uri": callback_uri(await get_secret(session, PUBLIC_BASE_URL)),
        "voice_id": await get_secret(session, ELEVENLABS_VOICE),
        "categories": active_categories(),
    }
    data.update(extra)
    return data


@app.get("/health")
async def health():
    return {"ok": True, "service": "viralshorts-ai"}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, session: SessionDep):
    return templates.TemplateResponse(request, "login.html", await _ctx(request, session))


@app.post("/login")
async def login(request: Request, session: SessionDep, username: str = Form(...), password: str = Form(...)):
    user = await session.scalar(select(User).where(User.username == username))
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            await _ctx(request, session, error="Invalid username or password"),
            status_code=401,
        )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(SESSION_COOKIE, sign_session({"uid": user.id, "username": user.username}), httponly=True, samesite="lax")
    return response


@app.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/", response_class=HTMLResponse)
async def overview(request: Request, session: SessionDep):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=303)
    totals = {
        "generated": await session.scalar(select(func.count(GeneratedVideo.id))) or 0,
        "published": await session.scalar(select(func.count(GeneratedVideo.id)).where(GeneratedVideo.status == "published")) or 0,
        "failed": await session.scalar(select(func.count(GeneratedVideo.id)).where(GeneratedVideo.status == "failed")) or 0,
        "jobs_failed": await session.scalar(select(func.count(Job.id)).where(Job.status == "failed")) or 0,
        "views": await session.scalar(select(func.coalesce(func.sum(AnalyticsSnapshot.views), 0))) or 0,
        "subscribers": await session.scalar(select(func.coalesce(func.max(AnalyticsSnapshot.subscribers), 0))) or 0,
    }
    recent = (await session.scalars(select(GeneratedVideo).order_by(GeneratedVideo.created_at.desc()).limit(8))).all()
    return templates.TemplateResponse(request, "overview.html", await _ctx(request, session, totals=totals, recent=recent))


@app.get("/videos", response_class=HTMLResponse)
async def videos(request: Request, session: SessionDep, status: str | None = None):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=303)
    stmt = select(GeneratedVideo).order_by(GeneratedVideo.created_at.desc())
    if status:
        stmt = stmt.where(GeneratedVideo.status == status)
    rows = (await session.scalars(stmt.limit(50))).all()
    return templates.TemplateResponse(request, "videos.html", await _ctx(request, session, videos=rows, filter_status=status or "all"))


@app.get("/discovery", response_class=HTMLResponse)
async def discovery_page(request: Request, session: SessionDep):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=303)
    ideas = (await session.scalars(select(ContentIdea).order_by(ContentIdea.relevance_score.desc()).limit(40))).all()
    assets = (await session.scalars(select(MediaAsset).order_by(MediaAsset.downloaded_at.desc()).limit(20))).all()
    return templates.TemplateResponse(request, "discovery.html", await _ctx(request, session, ideas=ideas, assets=assets))


@app.get("/automation", response_class=HTMLResponse)
async def automation_page(request: Request, session: SessionDep):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=303)
    queue = (await session.scalars(select(PublishQueue).order_by(PublishQueue.scheduled_for.asc()).limit(20))).all()
    return templates.TemplateResponse(request, "automation.html", await _ctx(request, session, queue=queue))


@app.post("/automation")
async def update_automation(
    request: Request,
    session: SessionDep,
    daily_target: int = Form(...),
    timezone: str = Form(...),
    slots: str = Form(...),
    language: str = Form(...),
    quality_threshold: int = Form(...),
    paused: str | None = Form(None),
):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=303)
    settings = await get_or_create_settings(session)
    settings.daily_target = max(1, min(daily_target, 6))
    settings.timezone = timezone
    settings.slots = slots
    settings.language = language
    settings.quality_threshold = max(40, min(quality_threshold, 95))
    settings.paused = paused == "on"
    await session.commit()
    return RedirectResponse("/automation", status_code=303)


@app.get("/integrations", response_class=HTMLResponse)
async def integrations(request: Request, session: SessionDep):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "integrations.html", await _ctx(request, session, oauth_state=new_csrf()))


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, session: SessionDep):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=303)
    snaps = (await session.scalars(select(AnalyticsSnapshot).order_by(AnalyticsSnapshot.captured_at.desc()).limit(30))).all()
    history = (await session.scalars(select(UploadHistory).order_by(UploadHistory.uploaded_at.desc()).limit(20))).all()
    return templates.TemplateResponse(request, "analytics.html", await _ctx(request, session, snaps=snaps, history=history))


@app.post("/api/discover")
async def api_discover(request: Request, session: SessionDep):
    require_user(request)
    ideas = await discover_ideas(session)
    return {"count": len(ideas), "ideas": [{"id": i.id, "title": i.title, "score": i.relevance_score} for i in ideas]}


@app.post("/api/generate")
async def api_generate(request: Request, session: SessionDep):
    require_user(request)
    video = await run_generation(session)
    return {
        "id": video.id,
        "status": video.status,
        "title": video.title,
        "quality": video.quality_score,
        "path": video.file_path,
        "fail_reason": video.fail_reason,
    }


@app.post("/api/tick")
async def api_tick(request: Request, session: SessionDep):
    require_user(request)
    return await tick(session)


@app.get("/api/status")
async def api_status(session: SessionDep):
    settings = await get_or_create_settings(session)
    eleven = await secret_status(session, ELEVENLABS)
    return {
        "paused": settings.paused,
        "daily_target": settings.daily_target,
        "timezone": settings.timezone,
        "generated": await session.scalar(select(func.count(GeneratedVideo.id))) or 0,
        "published": await session.scalar(select(func.count(UploadHistory.id))) or 0,
        "youtube_configured": await youtube_ready(session),
        "elevenlabs_configured": eleven["configured"],
        "elevenlabs_hint": eleven["hint"],
    }


@app.get("/api/integrations/youtube/start")
async def youtube_start(request: Request, session: SessionDep):
    require_user(request)
    creds = await oauth_settings(session)
    if not creds["client_id"] or not creds["client_secret"]:
        raise HTTPException(400, "Save Google Client ID and Client Secret first")
    state = new_csrf()
    response = RedirectResponse(authorization_url(state, creds["client_id"], creds["redirect_uri"]), status_code=303)
    response.set_cookie("oauth_state", state, httponly=True, samesite="lax", max_age=600)
    return response


@app.get("/api/integrations/youtube/callback")
async def youtube_callback(request: Request, session: SessionDep, code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        raise HTTPException(400, error)
    if not code or not state or state != request.cookies.get("oauth_state"):
        raise HTTPException(400, "invalid OAuth state")
    tokens = await exchange_code(code, session)
    await store_tokens(session, tokens)
    return RedirectResponse("/integrations", status_code=303)


@app.post("/api/integrations/elevenlabs")
async def save_elevenlabs(
    request: Request,
    session: SessionDep,
    api_key: str = Form(""),
    voice_id: str = Form(""),
):
    require_user(request)
    if api_key.strip():
        probe = probe_account(api_key.strip())
        if not probe.get("ok"):
            return RedirectResponse("/integrations?voice_error=1", status_code=303)
        await put_secret(session, ELEVENLABS, api_key.strip())
    if voice_id.strip():
        await put_secret(session, ELEVENLABS_VOICE, voice_id.strip())
    return RedirectResponse("/integrations?voice=saved", status_code=303)


@app.post("/api/integrations/google")
async def save_google(
    request: Request,
    session: SessionDep,
    client_id: str = Form(""),
    client_secret: str = Form(""),
    public_base_url: str = Form(""),
):
    require_user(request)
    if client_id.strip():
        await put_secret(session, GOOGLE_CLIENT_ID, client_id.strip())
    if client_secret.strip():
        await put_secret(session, GOOGLE_CLIENT_SECRET, client_secret.strip())
    if public_base_url.strip():
        await put_secret(session, PUBLIC_BASE_URL, public_base_url.strip().rstrip("/"))
    return RedirectResponse("/integrations?google=saved", status_code=303)


@app.get("/api/integrations/elevenlabs/voices")
async def elevenlabs_voices(request: Request, session: SessionDep):
    require_user(request)
    return {"voices": list_voices(await get_secret(session, ELEVENLABS))}


@app.post("/api/voice/restyle")
async def voice_restyle(request: Request, session: SessionDep, video_id: int = Form(...), voice_id: str = Form("")):
    require_user(request)
    video = await session.get(GeneratedVideo, video_id)
    if not video or not video.file_path or not Path(video.file_path).exists():
        raise HTTPException(404, "video not found")
    key = await get_secret(session, ELEVENLABS)
    if not key:
        raise HTTPException(400, "ElevenLabs is not configured")
    dest = get_settings().render_root / f"voice-{video.id}.wav"
    restyle_clip(Path(video.file_path), dest, api_key=key, voice_id=voice_id or None)
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return {"ok": True, "path": str(dest)}
    return RedirectResponse("/videos?voice=restyled", status_code=303)


@app.get("/media/{video_id}")
async def media(video_id: int, request: Request, session: SessionDep):
    require_user(request)
    video = await session.get(GeneratedVideo, video_id)
    if not video or not video.file_path or not Path(video.file_path).exists():
        raise HTTPException(404, "video not found")
    return FileResponse(video.file_path, media_type="video/mp4")
