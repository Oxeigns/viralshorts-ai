import asyncio
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from app.db import SessionLocal, init_db
from app.main import app
from app.services.pipeline import run_generation
from app.services.renderer import render_short


def test_ffmpeg_render_creates_mp4(tmp_path, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    settings.render_root = tmp_path
    result = render_short(9999, "Stop scrolling.", "This is a legal public-domain explainer about space photos.", "en")
    path = Path(result["path"])
    assert path.exists()
    assert path.stat().st_size > 2000
    assert result["width"] == 1080
    assert result["height"] == 1920


def test_health_and_generate_flow():
    async def run():
        await init_db()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json()["ok"] is True
            denied = await client.post("/api/generate")
            assert denied.status_code == 401
        async with SessionLocal() as session:
            video = await run_generation(session)
            assert video.id
            assert video.status in {"generated", "pending_approval", "failed"}
            if video.status in {"generated", "pending_approval"}:
                assert Path(video.file_path).exists()
                assert video.quality_score >= 70

    asyncio.run(run())
