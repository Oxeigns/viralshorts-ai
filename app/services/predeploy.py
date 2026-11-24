from __future__ import annotations

import asyncio
import json
import shutil
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Channel, OAuthCredential
from app.services.config_store import get_setting
from app.services.secrets import (
    ELEVENLABS,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    HACKERAI,
    TELEGRAM_TOKEN,
    get_secret,
)
from app.services.tts import probe_account
from app.services.youtube import callback_uri

WEIGHTS = {
    "ai": 15,
    "voice": 10,
    "telegram": 15,
    "youtube": 25,
    "publishing": 10,
    "storage": 10,
    "deploy": 15,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ok(name: str, message: str, extra: dict | None = None) -> dict:
    row = {"name": name, "status": "pass", "message": message}
    if extra:
        row.update(extra)
    return row


def _fail(name: str, message: str, extra: dict | None = None) -> dict:
    row = {"name": name, "status": "fail", "message": message}
    if extra:
        row.update(extra)
    return row


def _warn(name: str, message: str, extra: dict | None = None) -> dict:
    row = {"name": name, "status": "warn", "message": message}
    if extra:
        row.update(extra)
    return row


async def _check_hackerai(session: AsyncSession) -> dict:
    key = await get_secret(session, HACKERAI)
    base = (await get_setting(session, "hackerai_api_base_url")).rstrip("/") or "https://api.hackerai.sh/v1"
    model = (await get_setting(session, "hackerai_model")) or "auto"
    if not key:
        return {"group": "ai", "ready": False, "checks": [_fail("hackerai_key", "Hacker AI API key is not set.")]}
    # /v1/models answers 200 with no credentials at all, so only a completion with a
    # known answer can prove the key performs real inference.
    from app.services.ai_providers import probe_hackerai

    probe = await probe_hackerai(key, base, model)
    if probe.get("ok"):
        await _set_provider_status(session, "ok")
        return {
            "group": "ai",
            "ready": True,
            "checks": [_ok("hackerai_chat", f"inference verified · {probe.get('reason')} · model={model}")],
        }
    await _set_provider_status(session, "error")
    message = str(probe.get("reason") or "rejected")
    if probe.get("sample"):
        message = f"{message} · sample: {probe['sample'][:80]}"
    return {"group": "ai", "ready": False, "checks": [_fail("hackerai_chat", message)]}


async def _set_provider_status(session: AsyncSession, value: str) -> None:
    from app.services.config_store import put_setting

    await put_setting(session, "hackerai_provider_status", value)


async def _check_voice(session: AsyncSession) -> dict:
    key = await get_secret(session, ELEVENLABS)
    if not key:
        return {
            "group": "voice",
            "ready": True,
            "checks": [_warn("elevenlabs", "No ElevenLabs key. eSpeak NG fallback will narrate.")],
        }
    probe = await asyncio.to_thread(probe_account, key)
    if probe.get("ok"):
        return {
            "group": "voice",
            "ready": True,
            "checks": [_ok("elevenlabs", f"ElevenLabs /v1/user ok · {probe.get('tier') or 'account'} · {probe.get('voices', 0)} voices")],
        }
    detail = probe.get("detail") or "rejected"
    if "payment" in str(detail).lower():
        return {
            "group": "voice",
            "ready": True,
            "checks": [_warn("elevenlabs", f"Key accepted but billing blocks TTS: {detail}")],
        }
    return {"group": "voice", "ready": False, "checks": [_fail("elevenlabs", str(detail))]}


async def _check_telegram(session: AsyncSession) -> dict:
    token = await get_secret(session, TELEGRAM_TOKEN)
    admins = (await get_setting(session, "telegram_admin_ids")).strip()
    checks = []
    ready = True
    if not token:
        checks.append(_fail("telegram_token", "Telegram bot token is missing."))
        ready = False
    else:
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            body = resp.json() if resp.content else {}
            if resp.status_code == 200 and body.get("ok"):
                username = ((body.get("result") or {}).get("username")) or "bot"
                checks.append(_ok("telegram_token", f"getMe ok · @{username}"))
            elif resp.status_code == 429:
                checks.append(_warn("telegram_token", "getMe rate-limited (429). Token format accepted, retry later."))
            else:
                checks.append(_fail("telegram_token", f"getMe → {resp.status_code} {str(body)[:160]}"))
                ready = False
        except Exception as exc:  # noqa: BLE001
            checks.append(_fail("telegram_token", f"Telegram unreachable: {exc}"))
            ready = False
    if not admins:
        checks.append(_fail("telegram_admin", "No authorized Telegram admin ID."))
        ready = False
    else:
        checks.append(_ok("telegram_admin", f"Admin IDs configured ({len(admins.split(','))})."))
    return {"group": "telegram", "ready": ready, "checks": checks}


async def _check_youtube(session: AsyncSession) -> dict:
    client_id = await get_secret(session, GOOGLE_CLIENT_ID)
    client_secret = await get_secret(session, GOOGLE_CLIENT_SECRET)
    channel = await session.scalar(select(Channel).limit(1))
    cred = await session.scalar(select(OAuthCredential).limit(1))
    redirect = callback_uri(await get_setting(session, "app_base_url"))
    checks = []
    ready = True
    if not client_id or not client_secret:
        checks.append(_fail("google_oauth", "Google Client ID/Secret missing. Save them in Configuration Center."))
        ready = False
    else:
        checks.append(_ok("google_oauth", "Google OAuth client saved (encrypted)."))
    if not (channel and channel.connected and cred and cred.encrypted_refresh_token):
        checks.append(_fail("youtube_channel", f"Channel not connected. Redirect URI: {redirect}"))
        ready = False
    else:
        checks.append(_ok("youtube_channel", f"Connected: {channel.title}"))
    return {"group": "youtube", "ready": ready, "checks": checks}


async def _check_publishing(session: AsyncSession) -> dict:
    daily = await get_setting(session, "daily_target")
    tz = await get_setting(session, "timezone")
    mode = await get_setting(session, "publishing_mode")
    slots = [await get_setting(session, f"publish_time_{i}") for i in (1, 2, 3)]
    checks = []
    ready = True
    if not daily.isdigit() or not (1 <= int(daily) <= 48):
        checks.append(_fail("daily_target", "Daily upload limit must be 1–48."))
    elif int(daily) > 6:
        # A deliberate high target is a configuration choice, not a broken setup. It
        # stays a warning so readiness does not fail, but the quota reality is stated.
        checks.append(
            _warn(
                "daily_target",
                f"{daily} uploads/day requested. Default YouTube quota allows about 6 "
                "uploads/day (10,000 units, ~1,600 per upload); extra slots will be "
                "blocked with a quota message until the quota is raised.",
            )
        )
    else:
        checks.append(_ok("daily_target", f"{daily} Shorts / day"))
    if not tz:
        checks.append(_fail("timezone", "Timezone missing."))
        ready = False
    else:
        checks.append(_ok("timezone", tz))
    if not all(slots):
        checks.append(_fail("slots", "All three publishing times are required."))
        ready = False
    else:
        checks.append(_ok("slots", ", ".join(slots)))
    if mode not in {"automatic", "approval", "manual"}:
        checks.append(_fail("mode", "Publishing mode must be automatic, approval, or manual."))
        ready = False
    else:
        checks.append(_ok("mode", mode))
    return {"group": "publishing", "ready": ready, "checks": checks}


async def _check_storage(session: AsyncSession) -> dict:
    cfg = get_settings()
    url = await get_setting(session, "database_url") or cfg.database_url
    redis = await get_setting(session, "redis_url")
    bucket = await get_setting(session, "object_storage_bucket")
    checks = []
    ready = True
    if not url:
        checks.append(_fail("database", "DATABASE_URL is empty."))
        ready = False
    elif url.startswith("sqlite"):
        checks.append(_warn("database", "SQLite is fine for local/dev. Production should use PostgreSQL."))
    else:
        checks.append(_ok("database", "Database URL present."))
    if redis:
        parsed = urlparse(redis)
        host = parsed.hostname or "localhost"
        port = parsed.port or 6379
        sock = socket.socket()
        sock.settimeout(1.5)
        try:
            sock.connect((host, port))
            checks.append(_ok("redis", f"Redis reachable at {host}:{port}"))
        except OSError:
            checks.append(_warn("redis", f"Redis URL set but {host}:{port} is not reachable. APScheduler still works."))
        finally:
            sock.close()
    else:
        checks.append(_warn("redis", "Redis optional. In-process scheduler is active."))
    if bucket:
        checks.append(_ok("object_storage", f"Bucket {bucket} configured."))
    else:
        checks.append(_warn("object_storage", "No object storage. MP4s stay on local/Render disk."))
    return {"group": "storage", "ready": ready, "checks": checks}


async def _check_project_files() -> dict:
    root = Path(__file__).resolve().parents[2]
    required = [
        "Dockerfile",
        "requirements.txt",
        "package.json",
        ".env.example",
        "app.json",
        "render.yaml",
        "README.md",
        "alembic.ini",
        "app/main.py",
        "app/celery_app.py",
        "app/worker.py",
        "scripts/deploy.sh",
    ]
    checks = []
    ready = True
    for name in required:
        path = root / name
        if path.exists():
            checks.append(_ok(f"file:{name}", str(path)))
        else:
            checks.append(_fail(f"file:{name}", f"Required file missing: {name}"))
            ready = False
    return {"group": "files", "ready": ready, "checks": checks}


async def _check_python_runtime() -> dict:
    import importlib
    import sys

    checks = []
    ready = True
    version = sys.version_info
    if version < (3, 11):
        checks.append(_fail("python", f"Python {version.major}.{version.minor} is too old. Need 3.11+."))
        ready = False
    else:
        checks.append(_ok("python", f"Python {version.major}.{version.minor}.{version.micro}"))
    for module in ("fastapi", "sqlalchemy", "httpx", "jinja2", "cryptography", "alembic"):
        try:
            importlib.import_module(module)
            checks.append(_ok(f"dep:{module}", f"{module} importable"))
        except Exception as exc:  # noqa: BLE001
            checks.append(_fail(f"dep:{module}", f"{module} import failed: {exc}"))
            ready = False
    return {"group": "runtime", "ready": ready, "checks": checks}


async def _check_deploy(session: AsyncSession) -> dict:
    cfg = get_settings()
    base = (await get_setting(session, "app_base_url")) or cfg.app_base_url
    checks = []
    ready = True
    if not shutil.which("ffmpeg"):
        checks.append(_fail("ffmpeg", "ffmpeg is not on PATH."))
        ready = False
    else:
        checks.append(_ok("ffmpeg", shutil.which("ffmpeg") or "ffmpeg"))
    if not shutil.which("espeak-ng") and not shutil.which("espeak"):
        checks.append(_warn("espeak", "eSpeak NG missing. ElevenLabs becomes the only voice path."))
    else:
        checks.append(_ok("espeak", "eSpeak NG available as fallback."))
    if cfg.app_secret_key in {"", "dev-only-change-me"}:
        checks.append(_fail("secret_key", "APP_SECRET_KEY is still the development default."))
        ready = False
    else:
        checks.append(_ok("secret_key", "APP_SECRET_KEY is set."))
    if not base or "127.0.0.1" in base:
        checks.append(_warn("public_url", f"APP_BASE_URL is {base}. Set the Render URL before YouTube OAuth."))
    else:
        checks.append(_ok("public_url", base))
    return {"group": "deploy", "ready": ready, "checks": checks}


async def run_predeploy(session: AsyncSession) -> dict:
    groups = [
        await _check_hackerai(session),
        await _check_voice(session),
        await _check_telegram(session),
        await _check_youtube(session),
        await _check_publishing(session),
        await _check_storage(session),
        await _check_deploy(session),
    ]
    extras = [await _check_project_files(), await _check_python_runtime()]
    score = 0
    blockers: list[str] = []
    warnings: list[str] = []
    checks: list[dict] = []
    for group in groups:
        weight = WEIGHTS[group["group"]]
        if group["ready"]:
            score += weight
        else:
            blockers.extend(item["message"] for item in group["checks"] if item["status"] == "fail")
        warnings.extend(item["message"] for item in group["checks"] if item["status"] == "warn")
        for item in group["checks"]:
            checks.append({"group": group["group"], **item})
    for extra in extras:
        if not extra["ready"]:
            blockers.extend(item["message"] for item in extra["checks"] if item["status"] == "fail")
        warnings.extend(item["message"] for item in extra["checks"] if item["status"] == "warn")
        for item in extra["checks"]:
            checks.append({"group": extra["group"], **item})
    required = {"telegram", "youtube", "publishing", "storage", "deploy", "files", "runtime"}
    required_fail = any((g["group"] in required and not g["ready"]) for g in groups + extras)
    ready = (not required_fail) and score >= 85
    report = {
        "generated_at": _now(),
        "score": score,
        "ready": ready,
        "groups": groups,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
    }
    dest = Path("data/predeploy-report.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
