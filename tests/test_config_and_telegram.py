import asyncio
import json
import re
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config_schema import GROUPS, placeholder_app_json
from app.db import SessionLocal, init_db
from app.main import app
from app.models import EncryptedSecret
from app.services.config_store import public_snapshot, save_form
from app.services.predeploy import run_predeploy
from app.services.secrets import ELEVENLABS, secret_status
from app.services.telegram_control import handle_text, menu_keyboard

TEST_SECRET = "test_config_center_key"


def test_app_json_has_no_live_secrets():
    raw = Path("app.json").read_text(encoding="utf-8")
    assert not re.search(r"sk_[a-f0-9]{40,}", raw)
    assert not re.search(r"GOCSPX-[A-Za-z0-9_-]{10,}", raw)
    data = json.loads(raw)
    assert data["env"]["ELEVENLABS_API_KEY"].get("value") in {None, ""}
    assert "HACKERAI_API_KEY" in data["env"]
    schema = placeholder_app_json()
    assert schema["config"]["elevenlabs_api_key"] == ""


def test_config_groups_cover_required_sections():
    ids = {group["id"] for group in GROUPS}
    assert {"ai", "voice", "telegram", "youtube", "publishing", "storage", "deploy"} <= ids


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
            # Use a dedicated secret name so the real provider key is never overwritten.
            from app.services.secrets import put_secret

            await put_secret(session, TEST_SECRET, "sk_test_config_center_not_a_real_key_aaaa")
            row = await session.scalar(select(EncryptedSecret).where(EncryptedSecret.name == TEST_SECRET))
            assert row is not None
            assert "sk_test" not in row.ciphertext
            assert row.hint.endswith("aaaa")
            status = await secret_status(session, TEST_SECRET)
            assert status["source"] == "encrypted"
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
            body = status.json()
            assert "elevenlabs_hint" in body
            assert "sk_" not in str(body.get("elevenlabs_hint"))
            denied = await client.get("/api/config")
            assert denied.status_code == 401
            denied_pre = await client.get("/api/predeploy")
            assert denied_pre.status_code == 401

    asyncio.run(run())


def test_predeploy_writes_report():
    async def run():
        await init_db()
        async with SessionLocal() as session:
            report = await run_predeploy(session)
        assert "score" in report
        assert 0 <= report["score"] <= 100
        assert Path("data/predeploy-report.json").exists()
        saved = json.loads(Path("data/predeploy-report.json").read_text(encoding="utf-8"))
        assert saved["score"] == report["score"]
        assert any(item["name"] == "ffmpeg" for item in report["checks"])
        assert any(item["name"] == "file:app.json" for item in report["checks"])
        assert report["ready"] is False or not report["blockers"]

    asyncio.run(run())


def test_telegram_menu_and_unauthorized_commands():
    labels = [button["text"] for row in menu_keyboard()["keyboard"] for button in row]
    assert any("Status" in label for label in labels)
    assert any("Pre-Deploy" in label for label in labels)

    async def run():
        await init_db()
        async with SessionLocal() as session:
            text = await handle_text(session, "1", "1", "/status")
            assert "VIRALSHORTS status" in text
            cfg = await handle_text(session, "1", "1", "/config")
            assert "encrypted" in cfg.lower() or "Configuration" in cfg

    asyncio.run(run())
