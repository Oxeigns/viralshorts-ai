from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

CATEGORIES = [
    "funny_memes",
    "viral_trends",
    "animals",
    "gaming",
    "interesting_facts",
    "technology",
    "educational",
    "life_hacks",
    "mystery",
    "ai_comedy",
    "history",
    "science",
    "sports",
    "entertainment",
]


@dataclass
class ScriptDraft:
    hook: str
    body: str
    full_text: str
    titles: list[str]
    description: str
    hashtags: list[str]
    keywords: list[str]
    pinned_comment: str
    provider: str


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def local_script(topic: str, category: str, language: str = "en") -> ScriptDraft:
    lang = language.lower()
    if lang.startswith("hi"):
        hook = f"Ruko — {topic} ke baare mein yeh 20 second mein samajh aa jayega."
        body = (
            f"{topic} ek simple idea hai, lekin log isko complicate kar dete hain. "
            "Pehla point: sirf woh claim karo jo verify ho. "
            "Doosra: ek practical example lo. "
            "Teesra: agla step chhota rakho taaki koi turant try kar sake."
        )
        titles = [
            f"{topic}: 20 second fact",
            f"{topic} ko aise samjho",
            f"Aaj ka short: {topic}",
        ]
        description = f"Original explainer on {topic}. Sources are public-domain or licensed media only."
        hashtags = ["#shorts", "#facts", "#hindi", "#learn"]
        keywords = [topic, "facts", "hindi shorts"]
        pinned = "Kaunsa topic next chahiye? Comment karo."
    elif lang.startswith("hing"):
        hook = f"Wait — {topic} is simpler than it looks."
        body = (
            f"{topic} sounds big, but the useful version is tiny. "
            "First, ignore the hype. Second, keep one example. "
            "Third, try one small action today."
        )
        titles = [f"{topic} in 20s", f"Quick take: {topic}", f"{topic} without the fluff"]
        description = f"Hinglish short about {topic}. Original narration, licensed visuals."
        hashtags = ["#shorts", "#hinglish", "#facts"]
        keywords = [topic, "hinglish", "shorts"]
        pinned = "Next topic? Drop it below."
    else:
        hook = f"Stop scrolling. {topic} in twenty seconds, no fluff."
        body = (
            f"{topic} is usually oversold. Here is the useful version: "
            "one verified idea, one concrete example, and one action you can try today. "
            "If a claim cannot be checked, we skip it."
        )
        titles = [
            f"{topic} in 20 seconds",
            f"The useful version of {topic}",
            f"A 20-second {category.replace('_', ' ')} short",
        ]
        description = (
            f"Original short about {topic}. Visuals are public-domain or openly licensed. "
            "This is not financial, medical, or legal advice."
        )
        hashtags = ["#shorts", "#facts", f"#{category.replace('_', '')}"]
        keywords = [topic, category.replace("_", " "), "short explainer"]
        pinned = "Want the next topic? Comment one idea."

    full = f"{hook} {body}"
    return ScriptDraft(
        hook=_clean(hook),
        body=_clean(body),
        full_text=_clean(full),
        titles=titles,
        description=description,
        hashtags=hashtags,
        keywords=keywords,
        pinned_comment=pinned,
        provider="local",
    )


async def optional_remote_script(topic: str, category: str, language: str) -> ScriptDraft | None:
    settings = get_settings()
    prompt = (
        "Write a truthful 70-word YouTube Short script. No fabricated facts. "
        f"Topic: {topic}. Category: {category}. Language: {language}. "
        "Return JSON with keys hook, body, titles (3), description, hashtags, keywords, pinned_comment."
    )
    if settings.ollama_base_url:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    settings.ollama_base_url.rstrip("/") + "/api/generate",
                    json={"model": "llama3.2", "prompt": prompt, "stream": False},
                )
            if resp.status_code == 200:
                text = resp.json().get("response", "")
                parsed = _parse_json_blob(text)
                if parsed:
                    parsed.provider = "ollama"
                    return parsed
        except Exception as exc:  # noqa: BLE001
            logger.info("ollama unavailable: %s", exc)
    if settings.openai_api_key:
        try:
            headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
            base = settings.openai_base_url or "https://api.openai.com/v1"
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    base.rstrip("/") + "/chat/completions",
                    headers=headers,
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"]
                parsed = _parse_json_blob(text)
                if parsed:
                    parsed.provider = "openai"
                    return parsed
        except Exception as exc:  # noqa: BLE001
            logger.info("openai unavailable: %s", exc)
    return None


def _parse_json_blob(text: str) -> ScriptDraft | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    hook = str(data.get("hook") or "").strip()
    body = str(data.get("body") or "").strip()
    if not hook or not body:
        return None
    titles = [str(t) for t in (data.get("titles") or [])][:3]
    while len(titles) < 3:
        titles.append(hook[:70])
    return ScriptDraft(
        hook=hook,
        body=body,
        full_text=f"{hook} {body}",
        titles=titles,
        description=str(data.get("description") or body),
        hashtags=list(data.get("hashtags") or ["#shorts"]),
        keywords=list(data.get("keywords") or []),
        pinned_comment=str(data.get("pinned_comment") or ""),
        provider="remote",
    )


async def draft_script(topic: str, category: str, language: str = "en") -> ScriptDraft:
    remote = await optional_remote_script(topic, category, language)
    return remote or local_script(topic, category, language)
