from __future__ import annotations

import asyncio
import logging
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
    AppLog,
    AuditLog,
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
from app.services.config_store import get_setting, public_snapshot, save_form
from app.services.discovery import active_categories, discover_ideas
from app.services.jobs import enqueue_generation, failed_jobs, job_view, latest_jobs, reap_stale
from app.services.predeploy import run_predeploy
from app.services.publisher import tick
from app.services.scheduler import get_or_create_settings
from app.services.telegram_control import auto_approve_enabled
from app.services.secrets import (
    ELEVENLABS,
    ELEVENLABS_VOICE,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    HACKERAI,
    PUBLIC_BASE_URL,
    TELEGRAM_TOKEN,
    bootstrap_env_secrets,
    get_secret,
    put_secret,
    secret_status,
)
from app.services.telegram_control import diagnose as telegram_diagnose
from app.services.telegram_control import handle_update, set_webhook
from app.services.tts import list_voices, probe_account, restyle_clip
from app.services.youtube import (
    authorization_url,
    callback_uri,
    exchange_code,
    normalize_base_url,
    oauth_settings,
    store_tokens,
    youtube_ready,
)

logger = logging.getLogger(__name__)
BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
scheduler = AsyncIOScheduler()
SESSION_COOKIE = "vs_session"


async def _init_db_with_retry(attempts: int = 10, delay: float = 3.0) -> None:
    """A managed database can refuse connections for a few seconds after a restart.

    Crashing the lifespan here would leave the service unreachable behind a 502, so
    boot waits instead of failing immediately.
    """
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            await init_db()
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            logger.warning("database not ready (attempt %s/%s): %s", attempt, attempts, exc)
            await asyncio.sleep(delay)
    if last is not None:
        raise last


@asynccontextmanager
async def lifespan(_: FastAPI):
    await _init_db_with_retry()
    async with SessionLocal() as session:
        await _ensure_admin(session)
        await get_or_create_settings(session)
        await bootstrap_env_secrets(session)
    cfg = get_settings()
    # Register the Telegram webhook at boot so the bot answers without a manual step.
    try:
        async with SessionLocal() as session:
            base = normalize_base_url(await get_secret(session, PUBLIC_BASE_URL))
            if base and "127.0.0.1" not in base and "localhost" not in base:
                info = await set_webhook(session, base)
                logger.info("telegram webhook registered: %s", info.get("ok"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram webhook setup skipped: %s", exc)
    if cfg.scheduler_enabled and not scheduler.running:
        scheduler.add_job(
            _scheduled_tick,
            "interval",
            minutes=10,
            id="pipeline-tick",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
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
    hacker = await secret_status(session, HACKERAI)
    telegram = await secret_status(session, TELEGRAM_TOKEN)
    yt_ready = await youtube_ready(session)
    providers = {
        "youtube": yt_ready and bool(channel and channel.connected),
        "elevenlabs": eleven["configured"],
        "hackerai": hacker["configured"],
        "pexels": bool(cfg.pexels_api_key),
        "pixabay": bool(cfg.pixabay_api_key),
        "ollama": bool(cfg.ollama_base_url),
        "openai": bool(cfg.openai_api_key),
        "telegram": telegram["configured"],
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
        "hackerai": hacker,
        "telegram": telegram,
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
    await reap_stale(session)
    totals = {
        "generated": await session.scalar(select(func.count(GeneratedVideo.id))) or 0,
        "published": await session.scalar(select(func.count(GeneratedVideo.id)).where(GeneratedVideo.status == "published")) or 0,
        "failed": await session.scalar(select(func.count(GeneratedVideo.id)).where(GeneratedVideo.status == "failed")) or 0,
        "jobs_failed": await session.scalar(select(func.count(Job.id)).where(Job.status == "failed")) or 0,
        "views": await session.scalar(select(func.coalesce(func.sum(AnalyticsSnapshot.views), 0))) or 0,
        "subscribers": await session.scalar(select(func.coalesce(func.max(AnalyticsSnapshot.subscribers), 0))) or 0,
    }
    recent = (await session.scalars(select(GeneratedVideo).order_by(GeneratedVideo.created_at.desc()).limit(8))).all()
    jobs = await latest_jobs(session, limit=6)
    return templates.TemplateResponse(
        request,
        "overview.html",
        await _ctx(request, session, totals=totals, recent=recent, jobs=jobs, auto_approve=await auto_approve_enabled(session)),
    )


@app.get("/errors", response_class=HTMLResponse)
async def errors_page(request: Request, session: SessionDep):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=303)
    jobs = await failed_jobs(session, limit=40)
    videos = (
        await session.scalars(
            select(GeneratedVideo).where(GeneratedVideo.status == "failed").order_by(GeneratedVideo.id.desc()).limit(30)
        )
    ).all()
    return templates.TemplateResponse(request, "errors.html", await _ctx(request, session, jobs=jobs, failed_videos=videos))


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


@app.get("/configuration", response_class=HTMLResponse)
async def configuration_page(request: Request, session: SessionDep, saved: int | None = None):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=303)
    snapshot = await public_snapshot(session)
    return templates.TemplateResponse(
        request,
        "configuration.html",
        await _ctx(request, session, snapshot=snapshot, saved=bool(saved)),
    )


@app.post("/configuration")
async def configuration_save(request: Request, session: SessionDep):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=303)
    form = dict(await request.form())
    await save_form(session, {str(k): str(v) for k, v in form.items()})
    return RedirectResponse("/configuration?saved=1", status_code=303)


@app.get("/predeploy", response_class=HTMLResponse)
async def predeploy_page(request: Request, session: SessionDep):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=303)
    report = await run_predeploy(session)
    return templates.TemplateResponse(request, "predeploy.html", await _ctx(request, session, report=report))


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, session: SessionDep):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=303)
    logs = (await session.scalars(select(AppLog).order_by(AppLog.created_at.desc()).limit(50))).all()
    audits = (await session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(50))).all()
    return templates.TemplateResponse(request, "logs.html", await _ctx(request, session, logs=logs, audits=audits))


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
    """Queue a render and return at once.

    Rendering takes longer than a managed proxy keeps a request open, so doing the
    work inline used to surface as 502. Poll /api/jobs/{id} instead.
    """
    require_user(request)
    job = await enqueue_generation(session, source="dashboard")
    return {"job_id": job.id, "status": job.status, "poll": f"/api/jobs/{job.id}"}


@app.get("/api/jobs/{job_id}")
async def api_job(job_id: int, request: Request, session: SessionDep):
    require_user(request)
    view = await job_view(session, job_id)
    if view is None:
        raise HTTPException(404, "job not found")
    return view


@app.get("/api/jobs")
async def api_jobs(request: Request, session: SessionDep):
    require_user(request)
    await reap_stale(session)
    rows = await latest_jobs(session, limit=15)
    return {
        "jobs": [
            {
                "id": row.id,
                "status": row.status,
                "detail": row.detail,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
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
        "hackerai_configured": (await secret_status(session, HACKERAI))["configured"],
        "telegram_configured": (await secret_status(session, TELEGRAM_TOKEN))["configured"],
    }


@app.get("/api/config")
async def api_config(request: Request, session: SessionDep):
    require_user(request)
    return await public_snapshot(session)


@app.post("/api/config")
async def api_config_save(request: Request, session: SessionDep):
    require_user(request)
    payload = await request.json()
    saved = await save_form(session, {str(k): str(v) for k, v in payload.items()})
    return {"ok": True, "saved": saved}


@app.get("/api/config/validate")
async def api_config_validate(request: Request, session: SessionDep):
    require_user(request)
    report = await run_predeploy(session)
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/predeploy", status_code=303)
    return report


@app.get("/api/predeploy")
async def api_predeploy(request: Request, session: SessionDep):
    require_user(request)
    return await run_predeploy(session)


@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request, session: SessionDep):
    payload = await request.json()
    return await handle_update(session, payload)


@app.post("/api/telegram/webhook/setup")
async def telegram_webhook_setup(request: Request, session: SessionDep):
    require_user(request)
    base = normalize_base_url(await get_secret(session, PUBLIC_BASE_URL))
    if not base:
        base = normalize_base_url((await get_setting(session, "app_base_url")) or get_settings().app_base_url)
    if not base:
        raise HTTPException(400, "Set the public app URL first")
    return await set_webhook(session, base)


@app.get("/api/telegram/diagnose")
async def telegram_diagnose_api(request: Request, session: SessionDep):
    require_user(request)
    base = normalize_base_url(await get_secret(session, PUBLIC_BASE_URL))
    if not base:
        base = normalize_base_url((await get_setting(session, "app_base_url")) or get_settings().app_base_url)
    return await telegram_diagnose(session, base)


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
        probe = await asyncio.to_thread(probe_account, api_key.strip())
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
        await put_secret(session, PUBLIC_BASE_URL, normalize_base_url(public_base_url))
    return RedirectResponse("/integrations?google=saved", status_code=303)


@app.get("/api/integrations/elevenlabs/voices")
async def elevenlabs_voices(request: Request, session: SessionDep):
    require_user(request)
    key = await get_secret(session, ELEVENLABS)
    return {"voices": await asyncio.to_thread(list_voices, key)}


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
    # restyle_clip shells out to ffmpeg; never block the event loop from a request.
    await asyncio.to_thread(restyle_clip, Path(video.file_path), dest, key, voice_id or None)
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return {"ok": True, "path": str(dest)}
    return RedirectResponse("/videos?voice=restyled", status_code=303)


@app.post("/api/videos/{video_id}/approve")
async def approve_video(video_id: int, request: Request, session: SessionDep):
    require_user(request)
    from app.services.scheduler import next_open_slot

    video = await session.get(GeneratedVideo, video_id)
    if video is None:
        raise HTTPException(404, "video not found")
    video.status = "generated"
    session.add(PublishQueue(video_id=video.id, scheduled_for=await next_open_slot(session), status="scheduled"))
    await session.commit()
    return {"ok": True, "id": video.id, "status": video.status}


@app.get("/media/{video_id}")
async def media(video_id: int, request: Request, session: SessionDep):
    require_user(request)
    video = await session.get(GeneratedVideo, video_id)
    if not video or not video.file_path or not Path(video.file_path).exists():
        raise HTTPException(404, "video not found")
    return FileResponse(video.file_path, media_type="video/mp4")


@app.get("/media/{video_id}/captions")
async def media_captions(video_id: int, request: Request, session: SessionDep):
    require_user(request)
    video = await session.get(GeneratedVideo, video_id)
    if not video or not video.file_path:
        raise HTTPException(404, "video not found")
    srt = Path(video.file_path).with_suffix(".srt")
    if not srt.exists():
        raise HTTPException(404, "captions not generated for this video")
    return FileResponse(srt, media_type="application/x-subrip", filename=f"short-{video_id}.srt")
