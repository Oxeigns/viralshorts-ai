"""A key that is merely accepted is not a working key.

api.hackerai.sh answers 200 with a canned motivational quote even when no
credential is sent, so probing with /v1/models or with "say ok" reports success
for a useless key. The probe must require a real answer.
"""

import asyncio

from app.services import ai_providers
from app.services.ai_providers import probe_hackerai

QUOTE = "The sun will rise and we will try again."


def _fake_post(status, text):
    async def _inner(base, key, model, messages, temperature, force_json):
        return status, text

    return _inner


def test_quote_response_is_rejected(monkeypatch):
    monkeypatch.setattr(ai_providers, "_post_chat", _fake_post(200, QUOTE))
    result = asyncio.run(probe_hackerai("hsb_example", "https://api.hackerai.sh/v1", "auto"))
    assert result["ok"] is False
    assert "not valid for inference" in result["reason"]
    assert "sample" in result


def test_correct_answer_is_accepted(monkeypatch):
    monkeypatch.setattr(ai_providers, "_post_chat", _fake_post(200, "4"))
    result = asyncio.run(probe_hackerai("sk_example", "https://api.hackerai.sh/v1", "auto"))
    assert result["ok"] is True


def test_http_error_is_rejected(monkeypatch):
    monkeypatch.setattr(ai_providers, "_post_chat", _fake_post(401, '{"error":"invalid key"}'))
    result = asyncio.run(probe_hackerai("sk_bad", "https://api.hackerai.sh/v1", "auto"))
    assert result["ok"] is False
    assert "401" in result["reason"]


def test_missing_key_is_rejected():
    result = asyncio.run(probe_hackerai("", "https://api.hackerai.sh/v1", "auto"))
    assert result["ok"] is False


def test_prose_instead_of_json_falls_back_to_local(monkeypatch):
    monkeypatch.setattr(ai_providers, "_post_chat", _fake_post(200, QUOTE))
    draft = asyncio.run(
        ai_providers.draft_script(
            "topic", "facts", "en", hackerai_key="hsb_example", hackerai_base="https://api.hackerai.sh/v1"
        )
    )
    assert draft.provider == "local"
    assert draft.captions
