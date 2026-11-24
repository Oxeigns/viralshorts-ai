import re
from pathlib import Path

from app.security import decrypt_secret, encrypt_secret, secret_hint
from app.services.youtube import authorization_url, callback_uri

LIVE_KEY_RE = re.compile(r"sk_[a-f0-9]{40,}")


def test_secret_roundtrip_never_stores_plaintext():
    raw = "sk_example_not_a_real_key_zzzz"
    blob = encrypt_secret(raw)
    assert raw not in blob
    assert decrypt_secret(blob) == raw
    assert secret_hint(raw) == "…zzzz"
    assert "sk_example" not in secret_hint(raw)


def test_youtube_callback_follows_public_base():
    uri = callback_uri("https://viralshorts-ai.onrender.com")
    assert uri == "https://viralshorts-ai.onrender.com/api/integrations/youtube/callback"
    url = authorization_url("abc123", "cid.apps.googleusercontent.com", uri)
    assert "client_id=cid.apps.googleusercontent.com" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url


def test_repo_does_not_contain_live_elevenlabs_key():
    root = Path(__file__).resolve().parents[1]
    skip = {".git", ".venv", "__pycache__", "data", ".env"}
    for path in root.rglob("*"):
        if any(part in skip for part in path.parts):
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        assert not LIVE_KEY_RE.search(text), f"plaintext ElevenLabs key leaked in {path}"
