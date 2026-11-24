from __future__ import annotations

import logging
import shutil
import subprocess
import wave
from pathlib import Path

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

ELEVEN_BASE = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"  # Rachel

VOICES = {
    "en": "en-us",
    "hi": "hi",
    "hinglish": "en-us",
}


def elevenlabs_ready(api_key: str | None = None) -> bool:
    return bool((api_key or get_settings().elevenlabs_api_key or "").strip())


def synthesize(
    text: str,
    dest: Path,
    language: str = "en",
    speed: int = 165,
    api_key: str | None = None,
    voice_id: str | None = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    wav_path = dest.with_suffix(".wav")
    key = (api_key or get_settings().elevenlabs_api_key or "").strip()
    voice = (voice_id or get_settings().elevenlabs_voice_id or DEFAULT_VOICE).strip()
    if key:
        try:
            return _elevenlabs_tts(text, wav_path, key, voice)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ElevenLabs TTS failed, falling back to eSpeak: %s", exc)
    return _espeak(text, wav_path, language, speed)


def restyle_clip(
    source: Path,
    dest: Path,
    api_key: str | None = None,
    voice_id: str | None = None,
) -> Path:
    """Speech-to-speech / voice changer for an existing clip."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    key = (api_key or get_settings().elevenlabs_api_key or "").strip()
    if not key:
        raise RuntimeError("ElevenLabs is not configured")
    if not source.exists():
        raise RuntimeError("source audio not found")
    audio_in = _extract_audio(source)
    voice = (voice_id or get_settings().elevenlabs_voice_id or DEFAULT_VOICE).strip()
    model = get_settings().elevenlabs_sts_model_id
    headers = {"xi-api-key": key, "Accept": "audio/mpeg"}
    data = {"model_id": model}
    endpoints = [
        f"{ELEVEN_BASE}/speech-to-speech/{voice}",
        f"{ELEVEN_BASE}/voice-changer/{voice}",
    ]
    last_error = "voice restyle failed"
    with httpx.Client(timeout=90) as client:
        for url in endpoints:
            with audio_in.open("rb") as fh:
                resp = client.post(url, headers=headers, data=data, files={"audio": (audio_in.name, fh, "audio/wav")})
            if resp.status_code < 400 and resp.content:
                return _write_audio(resp.content, dest.with_suffix(".wav"), resp.headers.get("content-type", ""))
            last_error = f"{resp.status_code} {resp.text[:180]}"
    raise RuntimeError(last_error)


def list_voices(api_key: str | None = None) -> list[dict]:
    key = (api_key or get_settings().elevenlabs_api_key or "").strip()
    if not key:
        return []
    with httpx.Client(timeout=20) as client:
        resp = client.get(f"{ELEVEN_BASE}/voices", headers={"xi-api-key": key})
    if resp.status_code >= 400:
        logger.warning("ElevenLabs voices failed: %s", resp.status_code)
        return []
    voices = []
    for item in resp.json().get("voices") or []:
        voices.append(
            {
                "voice_id": item.get("voice_id", ""),
                "name": item.get("name", ""),
                "category": item.get("category", ""),
            }
        )
    return voices


def probe_account(api_key: str) -> dict:
    """Validate a key without returning it. Used by the dashboard."""
    key = (api_key or "").strip()
    if not key:
        return {"ok": False, "error": "missing key"}
    with httpx.Client(timeout=20) as client:
        resp = client.get(f"{ELEVEN_BASE}/user", headers={"xi-api-key": key})
    if resp.status_code >= 400:
        return {"ok": False, "error": f"ElevenLabs rejected the key ({resp.status_code})"}
    body = resp.json()
    subscription = body.get("subscription") or {}
    return {
        "ok": True,
        "tier": subscription.get("tier") or body.get("first_name") or "active",
        "character_count": subscription.get("character_count"),
        "character_limit": subscription.get("character_limit"),
    }


def estimate_duration(text: str, wpm: int = 150) -> float:
    words = max(1, len(text.split()))
    return max(8.0, min(45.0, words / wpm * 60 + 1.5))


def _elevenlabs_tts(text: str, wav_path: Path, api_key: str, voice_id: str) -> Path:
    settings = get_settings()
    payload = {
        "text": text[:4500],
        "model_id": settings.elevenlabs_model_id,
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.75},
    }
    headers = {"xi-api-key": api_key, "Accept": "audio/mpeg", "Content-Type": "application/json"}
    with httpx.Client(timeout=90) as client:
        resp = client.post(f"{ELEVEN_BASE}/text-to-speech/{voice_id}", headers=headers, json=payload)
    if resp.status_code >= 400:
        raise RuntimeError(f"ElevenLabs TTS {resp.status_code}: {resp.text[:180]}")
    if not resp.content:
        raise RuntimeError("ElevenLabs returned empty audio")
    return _write_audio(resp.content, wav_path, resp.headers.get("content-type", ""))


def _extract_audio(source: Path) -> Path:
    suffix = source.suffix.lower()
    if suffix == ".wav" and source.stat().st_size > 44:
        return source
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not installed")
    dest = source.with_name(source.stem + ".extracted.wav")
    cmd = [ffmpeg, "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "22050", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    if not dest.exists() or dest.stat().st_size < 44:
        raise RuntimeError("could not extract audio from source clip")
    return dest


def _write_audio(content: bytes, wav_path: Path, content_type: str) -> Path:
    if "wav" in content_type or content[:4] == b"RIFF":
        wav_path.write_bytes(content)
        return wav_path
    src = wav_path.with_suffix(".mp3")
    src.write_bytes(content)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        cmd = [ffmpeg, "-y", "-i", str(src), "-ac", "1", "-ar", "22050", str(wav_path)]
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        if wav_path.exists() and wav_path.stat().st_size > 44:
            return wav_path
    raise RuntimeError("could not decode ElevenLabs audio to wav")


def _espeak(text: str, wav_path: Path, language: str, speed: int) -> Path:
    voice = VOICES.get(language, "en-us")
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    if espeak:
        cmd = [espeak, "-v", voice, "-s", str(speed), "-w", str(wav_path), text]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            if wav_path.exists() and wav_path.stat().st_size > 44:
                return wav_path
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("espeak failed, using fallback tone: %s", exc)
    return _fallback_tone(wav_path, duration=max(6, min(20, len(text.split()) // 2)))


def _fallback_tone(path: Path, duration: int) -> Path:
    """Generate a placeholder wav so rendering never publishes broken audio silently."""
    import math
    import struct

    rate = 22050
    frames = rate * duration
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        for i in range(frames):
            value = int(4000 * math.sin(2 * math.pi * 220 * (i / rate)))
            wf.writeframes(struct.pack("<h", value))
    return path
