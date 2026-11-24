from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ContentIdea, MediaAsset
from app.services.ai_providers import CATEGORIES

logger = logging.getLogger(__name__)

SAFE_FEEDS = [
    ("NASA Breaking News", "https://www.nasa.gov/rss/dyn/breaking_news.rss", "science"),
    ("NASA Image of the Day", "https://www.nasa.gov/rss/dyn/lg_image_of_the_day.rss", "science"),
    ("Wikimedia Picture of the Day", "https://commons.wikimedia.org/w/api.php?action=featuredfeed&feed=potd&feedformat=rss", "history"),
    ("Internet Archive Blog", "https://blog.archive.org/feed/", "history"),
    ("The Verge", "https://www.theverge.com/rss/index.xml", "technology"),
    ("Hacker News Front", "https://hnrss.org/frontpage", "technology"),
    ("BBC Science", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "science"),
    ("NPR News", "https://feeds.npr.org/1001/rss.xml", "entertainment"),
]

INTERNAL_SEEDS = [
    ("Why cats always land on their feet", "animals", 0.82),
    ("A 20-second CPU explainer", "technology", 0.8),
    ("Public-domain space photos you can legally reuse", "science", 0.88),
    ("One life hack for packing a bag", "life_hacks", 0.7),
    ("A clean workplace joke that is original", "ai_comedy", 0.68),
    ("How a penalty kick really works", "sports", 0.66),
    ("A tiny history fact from 1919", "history", 0.72),
    ("Gaming loading screens and why they exist", "gaming", 0.64),
    ("A relatable friendship mix-up", "funny_memes", 0.7),
    ("A mystery that has a documented answer", "mystery", 0.69),
    ("An educational map fact", "educational", 0.73),
    ("What a viral trend actually means", "viral_trends", 0.65),
    ("A general entertainment hook about silence", "entertainment", 0.6),
]


def _score(popularity: float, recency: float, entertainment: float, license_ok: float, originality: float, safety: float) -> float:
    return round(
        0.22 * popularity
        + 0.18 * recency
        + 0.18 * entertainment
        + 0.18 * license_ok
        + 0.14 * originality
        + 0.10 * safety,
        3,
    )


def _host_ok(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and not any(bad in host for bad in ("tiktok", "instagram", "facebook.com", "fbcdn"))


async def discover_ideas(session: AsyncSession, limit: int = 20) -> list[ContentIdea]:
    created: list[ContentIdea] = []
    now = datetime.now(timezone.utc)
    for title, category, base in INTERNAL_SEEDS:
        existing = await session.scalar(select(ContentIdea).where(ContentIdea.title == title))
        if existing:
            continue
        idea = ContentIdea(
            title=title,
            category=category,
            source="internal",
            summary="Original seed topic. Visuals must still come from licensed or generated media.",
            popularity=base,
            recency=0.9,
            entertainment=0.75,
            license_availability=0.85,
            originality=0.9,
            safety=1.0,
            relevance_score=_score(base, 0.9, 0.75, 0.85, 0.9, 1.0),
            status="ready",
        )
        session.add(idea)
        created.append(idea)

    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers={"User-Agent": "ViralShortsAI/1.0"}) as client:
            for name, url, category in SAFE_FEEDS:
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue
                    parsed = feedparser.parse(resp.content)
                except Exception as exc:  # noqa: BLE001
                    logger.info("feed skipped %s: %s", name, exc)
                    continue
                for entry in parsed.entries[:4]:
                    title = (entry.get("title") or "").strip()
                    link = (entry.get("link") or "").strip()
                    if not title or not _host_ok(link):
                        continue
                    exists = await session.scalar(select(ContentIdea).where(ContentIdea.title == title))
                    if exists:
                        continue
                    published = entry.get("published_parsed")
                    recency = 0.5
                    if published:
                        recency = 0.9
                    idea = ContentIdea(
                        title=title[:400],
                        category=category,
                        source=name,
                        source_url=link,
                        summary=(entry.get("summary") or "")[:800],
                        popularity=0.7,
                        recency=recency,
                        entertainment=0.6,
                        license_availability=0.55,
                        originality=0.8,
                        safety=0.95,
                        relevance_score=_score(0.7, recency, 0.6, 0.55, 0.8, 0.95),
                        status="ready",
                    )
                    session.add(idea)
                    created.append(idea)
                    if len(created) >= limit:
                        break
                if len(created) >= limit:
                    break
    except Exception as exc:  # noqa: BLE001
        logger.info("discovery network limited: %s", exc)

    await session.commit()
    for idea in created:
        await session.refresh(idea)
    if not created:
        rows = (await session.scalars(select(ContentIdea).order_by(ContentIdea.relevance_score.desc()).limit(limit))).all()
        return list(rows)
    return created[:limit]


def license_record(
    source: str,
    url: str,
    creator: str,
    license_name: str,
    commercial: bool,
    attribution_required: bool,
    original_id: str,
    kind: str = "image",
) -> MediaAsset:
    return MediaAsset(
        source=source,
        source_url=url,
        creator=creator,
        license_name=license_name,
        commercial_use=commercial,
        attribution_required=attribution_required,
        attribution_text=f"{creator} / {license_name}" if creator else license_name,
        original_id=original_id,
        rights_status="verified" if license_name else "rejected",
        kind=kind,
    )


async def fetch_licensed_stills(session: AsyncSession, query: str) -> list[MediaAsset]:
    """Use official APIs only. Missing keys simply return no remote assets."""
    settings = get_settings()
    found: list[MediaAsset] = []
    headers = {"User-Agent": "ViralShortsAI/1.0"}
    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        if settings.pexels_api_key:
            try:
                resp = await client.get(
                    "https://api.pexels.com/v1/search",
                    params={"query": query, "per_page": 3, "orientation": "portrait"},
                    headers={"Authorization": settings.pexels_api_key},
                )
                if resp.status_code == 200:
                    for photo in resp.json().get("photos", []):
                        src = photo.get("src", {}).get("portrait") or photo.get("url")
                        if not src:
                            continue
                        asset = license_record(
                            "pexels",
                            src,
                            photo.get("photographer", "Pexels contributor"),
                            "Pexels License",
                            True,
                            True,
                            str(photo.get("id", "")),
                        )
                        session.add(asset)
                        found.append(asset)
            except Exception as exc:  # noqa: BLE001
                logger.info("pexels skipped: %s", exc)
        if settings.pixabay_api_key:
            try:
                resp = await client.get(
                    "https://pixabay.com/api/",
                    params={"key": settings.pixabay_api_key, "q": query, "image_type": "photo", "per_page": 3},
                )
                if resp.status_code == 200:
                    for hit in resp.json().get("hits", []):
                        src = hit.get("largeImageURL") or hit.get("pageURL")
                        if not src:
                            continue
                        asset = license_record(
                            "pixabay",
                            src,
                            hit.get("user", "Pixabay contributor"),
                            "Pixabay License",
                            True,
                            False,
                            str(hit.get("id", "")),
                        )
                        session.add(asset)
                        found.append(asset)
            except Exception as exc:  # noqa: BLE001
                logger.info("pixabay skipped: %s", exc)
    await session.commit()
    return found


def generated_asset_id(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


def active_categories() -> list[str]:
    return list(CATEGORIES)
