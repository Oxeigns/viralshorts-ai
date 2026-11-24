from __future__ import annotations

from pathlib import Path

from app.config import get_settings


def evaluate(video_path: str, duration: float, license_ok: bool, script_text: str, hook: str) -> dict:
    notes: list[str] = []
    score = 100
    path = Path(video_path)
    if not path.exists() or path.stat().st_size < 2000:
        return {"score": 0, "passed": False, "notes": "missing or empty render"}
    if not license_ok:
        score -= 50
        notes.append("unverified license")
    if duration < 8 or duration > 180:
        score -= 25
        notes.append(f"duration {duration:.1f}s outside 8-180")
    if 20 <= duration <= 45:
        score += 0
    elif duration > 45:
        score -= 5
        notes.append("longer than preferred 45s window")
    if len(hook.split()) < 3:
        score -= 15
        notes.append("weak hook")
    words = script_text.split()
    if len(words) < 20:
        score -= 10
        notes.append("script too short")
    if len(set(words)) < max(8, len(words) * 0.4):
        score -= 15
        notes.append("repetitive wording")
    banned = ("guaranteed money", "fake news", "miracle cure", "buy followers")
    lower = script_text.lower()
    if any(term in lower for term in banned):
        score -= 40
        notes.append("policy language")
    score = max(0, min(100, score))
    threshold = get_settings().quality_threshold
    return {
        "score": score,
        "passed": score >= threshold and license_ok,
        "notes": "; ".join(notes) or "ok",
    }
