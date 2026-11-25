import asyncio
import json
import re
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config_schema import GROUPS, placeholder_app_json
from app.db import SessionLocal, init_db
from app.main import _ensure_admin, app
from app.models import EncryptedSecret
from app.services.config_store import public_snapshot, save_form
from app.services.predeploy import run_predeploy
from app.services.secrets import secret_status
from app.services.telegram_control import handle_text, main_keyboard

TEST_SECRET = "test_config_center_key"


def test_app_json_has_no_live_secrets():
    raw = Path("app.json").read_text(encoding="utf-8")
    assert not re.search(r"sk_[a-f0-9]{40,}", raw)
    assert not re.search(r"GOCSPX-[A-Za-z0-9_-]{10,}", raw)
    assert not re.search(r"hsb_[a-f0-9]{20,}", raw)
    data = json.loads(raw)
    assert data["env"]["ELEVENLABS_API_KEY"].get("value") in {None, ""}
    assert "HACKERAI_API_KEY" in data["env"]
    assert placeholder_app_json()["config"]["elevenlabs_api_key"] == ""


def test_config_groups_cover_required_sections():
    ids = {group["id"] for group in GROUPS}
    assert {"ai", "voice", "telegram", "youtube", "publishing", "storage", "deploy"} <= ids
    keys = {field["key"] for group in GROUPS for field in group["fields"]}
    assert "auto_approve" in keys


def test_encrypted_config_save_and_status_api():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            await save_form(
                session,
                {
                    "daily_target": "3",
                    "publish_time_1": "09:00",
                    "publish_time_2": "14:00",
                    "publish_time_3": "20:00",
                    "timezone": "Asia/Kolkata",
                    "default_language": "en",
                    "publishing_mode": "approval",
                    "hackerai_api_base_url": "https://api.hackerai.sh/v1",
                },
            )
            from app.services.secrets import put_secret

            await put_secret(session, TEST_SECRET, "sk_test_config_center_not_a_real_key_aaaa")
            row = await session.scalar(select(EncryptedSecret).where(EncryptedSecret.name == TEST_SECRET))
            assert row is not None
            assert "sk_test" not in row.ciphertext
            assert row.hint.endswith("aaaa")
            assert (await secret_status(session, TEST_SECRET))["source"] == "encrypted"
            snap = await public_snapshot(session)
            blob = json.dumps(snap)
            assert "sk_test_config_center" not in blob
            eleven = next(f for g in snap["groups"] if g["id"] == "voice" for f in g["fields"] if f["key"] == "elevenlabs_api_key")
            assert eleven["value"] == ""
            assert eleven["secret"] is True
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            status = await client.get("/api/status")
            assert status.status_code == 200
            assert "elevenlabs_hint" in status.json()
            assert (await client.get("/api/config")).status_code == 401
            assert (await client.get("/api/predeploy")).status_code == 401
            assert (await client.get("/api/jobs")).status_code == 401
            assert (await client.get("/api/telegram/diagnose")).status_code == 401
            assert (await client.post("/api/generate")).status_code == 401

    asyncio.run(run())


def test_predeploy_writes_report():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            report = await run_predeploy(session)
        assert 0 <= report["score"] <= 100
        assert Path("data/predeploy-report.json").exists()
        saved = json.loads(Path("data/predeploy-report.json").read_text(encoding="utf-8"))
        assert saved["score"] == report["score"]
        names = {item["name"] for item in report["checks"]}
        assert "ffmpeg" in names
        assert "file:app.json" in names
        # /v1/models is unauthenticated, so it must not be the credential check.
        assert "hackerai_chat" in names or "hackerai_key" in names

    asyncio.run(run())


def test_telegram_inline_menu_and_commands():
    rows = main_keyboard()["inline_keyboard"]
    datas = {button["callback_data"] for row in rows for button in row}
    assert {"status", "generate", "queue", "approve", "autoapprove", "errors", "predeploy"} <= datas
    labels = [button["text"] for row in rows for button in row]
    assert any("Status" in label for label in labels)

    async def run():
        await init_db()
        async with SessionLocal() as session:
            text, markup = await handle_text(session, "1", "1", "/status")
            assert "VIRALSHORTS" in text
            assert "inline_keyboard" in markup
            generated, _ = await handle_text(session, "1", "1", "/generate")
            # Must answer immediately with a job id, never wait for the render.
            assert "job" in generated.lower()
            assert "started" in generated.lower() or "queued" in generated.lower()

    asyncio.run(run())


def test_generate_returns_immediately():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            await _ensure_admin(session)
        transport = ASGITransport(app=app)
        from app.config import get_settings

        cfg = get_settings()
        async with AsyncClient(transport=transport, base_url="http://test", timeout=30) as client:
            await client.post("/login", data={"username": cfg.admin_username, "password": cfg.admin_password})
            resp = await client.post("/api/generate")
            assert resp.status_code == 200
            body = resp.json()
            assert "job_id" in body
            assert body["status"] in {"queued", "running"}
            poll = await client.get(f"/api/jobs/{body['job_id']}")
            assert poll.status_code == 200
            assert poll.json()["kind"] == "generate"
            for path in ["/errors", "/"]:
                page = await client.get(path)
                assert page.status_code == 200

    asyncio.run(run())
