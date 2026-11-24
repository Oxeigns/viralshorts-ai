from __future__ import annotations

import logging
import shutil
import subprocess
import wave
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

VOICES = {
    "en": "en-us",
    "hi": "hi",
    "hinglish": "en-us",
}


def synthesize(text: str, dest: Path, language: str = "en", speed: int = 165) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    wav_path = dest.with_suffix(".wav")
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
    """Generate a silent-ish placeholder wav so rendering never publishes broken audio silently."""
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


def estimate_duration(text: str, wpm: int = 150) -> float:
    words = max(1, len(text.split()))
    return max(8.0, min(45.0, words / wpm * 60 + 1.5))
