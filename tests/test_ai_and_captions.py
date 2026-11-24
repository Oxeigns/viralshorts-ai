"""Hacker AI wiring, caption export and the auto-approve switch."""

import asyncio
from pathlib import Path

from app.db import SessionLocal, init_db
from app.services import ai_providers
from app.services.ai_providers import PROMPT, _draft_from_json, _split_captions, local_script, write_srt


def test_local_script_always_has_captions_and_hashtags():
    draft = local_script("why sleep matters", "educational", "en")
    assert draft.provider == "local"
    assert draft.captions
    assert all(len(line.split()) <= 8 for line in draft.captions)
    assert len(draft.hashtags) >= 4
    assert all(tag.startswith("#") for tag in draft.hashtags)
    assert len(draft.titles) == 3


def test_prompt_asks_for_captions_and_hashtags():
    text = PROMPT.format(topic="x", category="y", language="en")
    for token in ("captions", "hashtags", "titles", "description"):
        assert token in text


def test_draft_from_json_fills_missing_fields():
    draft = _draft_from_json({"hook": "Stop scrolling now.", "body": "One idea. One example. One action."}, "hackerai")
    assert draft is not None
    assert draft.provider == "hackerai"
    assert draft.captions
    assert len(draft.titles) == 3
    assert draft.hashtags


def test_draft_from_json_rejects_empty():
    assert _draft_from_json({"hook": "", "body": ""}, "hackerai") is None


def test_caption_split_is_bounded():
    lines = _split_captions("a b c d e f g h i j k l m n", size=6)
    assert lines == ["a b c d e f", "g h i j k l", "m n"]


def test_write_srt_is_valid(tmp_path: Path):
    dest = tmp_path / "short.srt"
    write_srt(["first line", "second line"], dest)
    text = dest.read_text(encoding="utf-8")
    assert "1\n00:00:00,000 --> " in text
    assert "first line" in text
    assert "2\n" in text
    assert "second line" in text


def test_hackerai_is_first_provider_when_key_present(monkeypatch):
    calls = []

    async def fake_hackerai(prompt, key, base, model):
        calls.append((key, base, model))
        return _draft_from_json({"hook": "From the API.", "body": "Body from the API."}, "hackerai")

    monkeypatch.setattr(ai_providers, "_hackerai", fake_hackerai)
    draft = asyncio.run(
        ai_providers.draft_script(
            "topic", "facts", "en", hackerai_key="hsb_test", hackerai_base="https://api.hackerai.sh/v1", hackerai_model="auto"
        )
    )
    assert draft.provider == "hackerai"
    assert calls and calls[0][0] == "hsb_test"


def test_missing_key_falls_back_to_local(monkeypatch):
    async def none_provider(*args, **kwargs):
        return None

    monkeypatch.setattr(ai_providers, "_hackerai", none_provider)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    draft = asyncio.run(ai_providers.draft_script("topic", "facts", "en", hackerai_key=""))
    assert draft.provider == "local"


def test_auto_approve_roundtrip():
    async def run():
        from app.services.config_store import put_setting
        from app.services.telegram_control import auto_approve_enabled, toggle_auto_approve

        await init_db()
        async with SessionLocal() as session:
            await put_setting(session, "auto_approve", "false")
            assert await auto_approve_enabled(session) is False
            message = await toggle_auto_approve(session)
            assert "ON" in message
            assert await auto_approve_enabled(session) is True
            message = await toggle_auto_approve(session)
            assert "OFF" in message
            assert await auto_approve_enabled(session) is False

    asyncio.run(run())
