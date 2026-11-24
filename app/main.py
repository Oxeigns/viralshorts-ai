from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
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
    SchedulerSettings,
    UploadHistory,
    User,
)
from app.security import hash_password, new_csrf, read_session, sign_session, verify_password
from app.services.discovery import active_categories, discover_ideas
from app.services.pipeline import run_generation
from app.services.publisher import tick
from app.services.scheduler import get_or_create_settings
from app.services.youtube import authorization_url, exchange_code, store_tokens, youtube_configured

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
    providers = {
        "youtube": youtube_configured() and bool(channel and channel.connected),
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
        "youtube_ready": youtube_configured(),
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
    return {
        "paused": settings.paused,
        "daily_target": settings.daily_target,
        "timezone": settings.timezone,
        "generated": await session.scalar(select(func.count(GeneratedVideo.id))) or 0,
        "published": await session.scalar(select(func.count(UploadHistory.id))) or 0,
        "youtube_configured": youtube_configured(),
    }


@app.get("/api/integrations/youtube/start")
async def youtube_start(request: Request):
    require_user(request)
    if not youtube_configured():
        raise HTTPException(400, "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET first")
    state = new_csrf()
    response = RedirectResponse(authorization_url(state), status_code=303)
    response.set_cookie("oauth_state", state, httponly=True, samesite="lax", max_age=600)
    return response


@app.get("/api/integrations/youtube/callback")
async def youtube_callback(request: Request, session: SessionDep, code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        raise HTTPException(400, error)
    if not code or not state or state != request.cookies.get("oauth_state"):
        raise HTTPException(400, "invalid OAuth state")
    tokens = await exchange_code(code)
    await store_tokens(session, tokens)
    return RedirectResponse("/integrations", status_code=303)


@app.get("/media/{video_id}")
async def media(video_id: int, request: Request, session: SessionDep):
    require_user(request)
    video = await session.get(GeneratedVideo, video_id)
    if not video or not video.file_path or not Path(video.file_path).exists():
        raise HTTPException(404, "video not found")
    return FileResponse(video.file_path, media_type="video/mp4")
