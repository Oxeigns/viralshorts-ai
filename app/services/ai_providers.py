from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

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
    captions: list[str] = field(default_factory=list)


PROMPT = """You write original YouTube Shorts scripts. Never invent facts, statistics,
quotes, or dates. If a claim cannot be sourced, rewrite it as an opinion.

Topic: {topic}
Category: {category}
Language: {language}

Rules:
- hook: max 12 words, spoken first, no "in this video".
- body: 45-60 words, one idea, one concrete example, one action.
- captions: split the whole spoken script into 5-8 short caption lines of at most
  7 words each, in order. These are burned on screen and exported as subtitles.
- titles: exactly 3, each under 70 characters.
- description: 2 lines ending with the hashtags.
- hashtags: 5 items, each starting with #.
- keywords: 6 search terms.
- pinned_comment: one question for viewers.

Return ONLY JSON with keys: hook, body, captions (array), titles (array of 3),
description, hashtags (array), keywords (array), pinned_comment.
"""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _split_captions(text: str, size: int = 6) -> list[str]:
    words = text.split()
    return [" ".join(words[i : i + size]) for i in range(0, len(words), size)] or [text]


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
        hashtags = ["#shorts", "#facts", "#hindi", "#learn", "#education"]
        keywords = [topic, "facts", "hindi shorts", "explainer", "learn", "education"]
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
        hashtags = ["#shorts", "#hinglish", "#facts", "#learn", "#india"]
        keywords = [topic, "hinglish", "shorts", "explainer", "facts", "india"]
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
        hashtags = ["#shorts", "#facts", f"#{category.replace('_', '')}", "#learn", "#explained"]
        keywords = [topic, category.replace("_", " "), "short explainer", "facts", "learn", "explained"]
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
        captions=_split_captions(_clean(full)),
    )


def _as_list(value, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        if items:
            return items
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in re.split(r"[,\n]", value) if part.strip()]
    return fallback


def _parse_json_blob(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _draft_from_json(data: dict, provider: str) -> ScriptDraft | None:
    hook = str(data.get("hook") or "").strip()
    body = str(data.get("body") or "").strip()
    if not hook or not body:
        return None
    full = f"{hook} {body}"
    titles = _as_list(data.get("titles"), [hook[:70]])[:3]
    while len(titles) < 3:
        titles.append(hook[:70])
    captions = _as_list(data.get("captions"), _split_captions(full))
    return ScriptDraft(
        hook=_clean(hook),
        body=_clean(body),
        full_text=_clean(full),
        titles=titles,
        description=str(data.get("description") or body).strip(),
        hashtags=_as_list(data.get("hashtags"), ["#shorts", "#facts"]),
        keywords=_as_list(data.get("keywords"), []),
        pinned_comment=str(data.get("pinned_comment") or "").strip(),
        provider=provider,
        captions=[_clean(item) for item in captions][:8],
    )


async def _post_chat(base: str, key: str, model: str, messages: list[dict], temperature: float, force_json: bool) -> tuple[int, str]:
    """One chat completion. Returns (status, assistant text or error body)."""
    url = (base or "https://api.hackerai.sh/v1").rstrip("/") + "/chat/completions"
    payload: dict = {"model": model or "auto", "messages": messages, "temperature": temperature}
    if force_json:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, headers=headers, json=payload)
    if resp.status_code == 400 and force_json:
        # Some compatible gateways reject response_format. Retry plain.
        return await _post_chat(base, key, model, messages, temperature, False)
    if resp.status_code >= 400:
        return resp.status_code, resp.text[:300]
    try:
        return resp.status_code, resp.json()["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        return 502, resp.text[:300]


async def probe_hackerai(key: str, base: str = "", model: str = "") -> dict:
    """Prove the key actually performs inference.

    A key that is merely accepted is not enough: some gateways answer 200 with
    canned prose for any credential, which would silently replace real scripts
    with one-line quotes. Ask a question with a single unambiguous answer.
    """
    if not key:
        return {"ok": False, "reason": "no key configured"}
    messages = [{"role": "user", "content": "What is 2+2? Reply with the digit only, no words."}]
    try:
        status, text = await _post_chat(base, key, model, messages, 0.0, False)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"unreachable: {exc}"}
    if status >= 400:
        return {"ok": False, "reason": f"HTTP {status}: {text[:160]}"}
    answer = (text or "").strip()
    if "4" not in answer or len(answer) > 24:
        return {
            "ok": False,
            "reason": "endpoint replied with unrelated text instead of an answer — key is not valid for inference",
            "sample": answer[:120],
        }
    return {"ok": True, "reason": "answered 2+2=4", "sample": answer[:40]}


async def _hackerai(prompt: str, key: str, base: str, model: str) -> ScriptDraft | None:
    if not key:
        return None
    messages = [
        {"role": "system", "content": "You reply with a single JSON object and nothing else. No prose, no markdown. Never invent facts."},
        {"role": "user", "content": prompt},
    ]
    candidates = [model or "auto"]
    for fallback in ("kimi-k2.5", "glm-5.3-flash"):
        if fallback not in candidates:
            candidates.append(fallback)
    for candidate in candidates:
        try:
            status, text = await _post_chat(base, key, candidate, messages, 0.8, True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("hackerai %s failed: %s", candidate, exc)
            continue
        if status >= 400:
            logger.warning("hackerai %s: HTTP %s %s", candidate, status, text[:160])
            continue
        data = _parse_json_blob(text)
        if not data:
            logger.warning("hackerai %s returned non-JSON: %s", candidate, (text or "")[:160])
            continue
        draft = _draft_from_json(data, "hackerai")
        if draft:
            return draft
    return None


async def _ollama(prompt: str, base: str) -> ScriptDraft | None:
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                base.rstrip("/") + "/api/generate",
                json={"model": "llama3.2", "prompt": prompt, "stream": False, "format": "json"},
            )
        if resp.status_code != 200:
            return None
        data = _parse_json_blob(resp.json().get("response", ""))
        return _draft_from_json(data, "ollama") if data else None
    except Exception as exc:  # noqa: BLE001
        logger.info("ollama unavailable: %s", exc)
        return None


async def _openai(prompt: str, key: str, base: str) -> ScriptDraft | None:
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                (base or "https://api.openai.com/v1").rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]},
            )
        if resp.status_code != 200:
            return None
        data = _parse_json_blob(resp.json()["choices"][0]["message"]["content"])
        return _draft_from_json(data, "openai") if data else None
    except Exception as exc:  # noqa: BLE001
        logger.info("openai unavailable: %s", exc)
        return None


async def draft_script(
    topic: str,
    category: str,
    language: str = "en",
    hackerai_key: str = "",
    hackerai_base: str = "",
    hackerai_model: str = "",
) -> ScriptDraft:
    """Hacker AI first, then local fallbacks. Never raises on provider failure."""
    settings = get_settings()
    prompt = PROMPT.format(topic=topic, category=category, language=language)

    draft = await _hackerai(
        prompt,
        (hackerai_key or settings.hackerai_api_key).strip(),
        hackerai_base or settings.hackerai_api_base_url,
        hackerai_model or settings.hackerai_model,
    )
    if draft:
        return draft
    if settings.ollama_base_url:
        draft = await _ollama(prompt, settings.ollama_base_url)
        if draft:
            return draft
    if settings.openai_api_key:
        draft = await _openai(prompt, settings.openai_api_key, settings.openai_base_url)
        if draft:
            return draft
    return local_script(topic, category, language)


def write_srt(captions: list[str], dest, seconds_per_line: float | None = None) -> object:
    """Write a sidecar .srt so YouTube can show real subtitles."""
    from pathlib import Path

    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = captions or [" "]
    per = seconds_per_line or max(1.6, 18.0 / max(1, len(lines)))
    blocks = []
    for index, text in enumerate(lines, start=1):
        start = (index - 1) * per
        end = index * per
        blocks.append(
            f"{index}\n{_stamp(start)} --> {_stamp(end)}\n{text}\n"
        )
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def _stamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"
