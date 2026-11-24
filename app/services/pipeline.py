from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ContentIdea, GeneratedVideo, PublishQueue, Script
from app.services.ai_providers import draft_script, write_srt
from app.services.discovery import discover_ideas, generated_asset_id, license_record
from app.services.notify import notify
from app.services.quality import evaluate
from app.services.renderer import render_short
from app.services.scheduler import get_or_create_settings, next_open_slot
from app.services.config_store import get_setting
from app.services.secrets import ELEVENLABS, ELEVENLABS_VOICE, HACKERAI, get_secret
from app.services.storage import upload_file

logger = logging.getLogger(__name__)


async def run_generation(session: AsyncSession, language: str | None = None) -> GeneratedVideo:
    settings = get_settings()
    language = language or settings.default_language

    ideas = await discover_ideas(session)
    idea = next((i for i in ideas if i.status in {"new", "ready"}), None)
    if idea is None:
        idea = await session.scalar(select(ContentIdea).order_by(ContentIdea.created_at.desc()))
    if idea is None:
        raise RuntimeError("no content ideas available")

    hacker_key = await get_secret(session, HACKERAI)
    hacker_base = await get_setting(session, "hackerai_api_base_url")
    hacker_model = await get_setting(session, "hackerai_model")
    draft = await draft_script(
        idea.title,
        idea.category,
        language,
        hackerai_key=hacker_key,
        hackerai_base=hacker_base,
        hackerai_model=hacker_model,
    )
    script = Script(
        idea_id=idea.id,
        language=language,
        hook=draft.hook,
        body=draft.body,
        full_text=draft.full_text,
        provider=draft.provider,
    )
    session.add(script)
    await session.flush()

    asset = license_record(
        "generated",
        f"local://graphics/{generated_asset_id(idea.title)}",
        "VIRALSHORTS AI",
        "Original work",
        True,
        False,
        generated_asset_id(idea.title),
        kind="graphic",
    )
    asset.rights_status = "verified"
    session.add(asset)

    video = GeneratedVideo(
        idea_id=idea.id,
        script_id=script.id,
        status="processing",
        language=language,
        title=draft.titles[0][:100],
        title_suggestions=" | ".join(draft.titles),
        description=draft.description,
        hashtags=" ".join(draft.hashtags),
        keywords=", ".join(draft.keywords),
        pinned_comment=draft.pinned_comment,
        category=idea.category,
    )
    session.add(video)
    await session.commit()
    await session.refresh(video)

    try:
        voice_key = await get_secret(session, ELEVENLABS)
        voice_id = (await get_secret(session, ELEVENLABS_VOICE)) or None
        # render_short runs ffmpeg and eSpeak through subprocess; keep the event loop free.
        rendered = await asyncio.to_thread(
            render_short,
            video.id,
            draft.hook,
            draft.body,
            language=language,
            api_key=voice_key or None,
            voice_id=voice_id,
        )
        quality = await asyncio.to_thread(
            evaluate, rendered["path"], rendered["duration"], True, draft.full_text, draft.hook
        )
        # Sidecar subtitles from the same caption lines that are burned on screen.
        srt_path = await asyncio.to_thread(
            write_srt, draft.captions, Path(rendered["path"]).with_suffix(".srt")
        )
        logger.info("captions written: %s", srt_path)
        stored = await upload_file(session, rendered["path"])
        video.file_path = stored
        video.duration_sec = rendered["duration"]
        video.width = rendered["width"]
        video.height = rendered["height"]
        video.quality_score = quality["score"]
        video.quality_notes = quality["notes"]
        video.voice_used = voice_id or settings.default_voice
        sched = await get_or_create_settings(session)
        auto = (await get_setting(session, "auto_approve") or "").strip().lower()
        auto_on = auto in {"true", "1", "yes", "on"} or (
            auto not in {"false", "0", "no", "off"} and sched.publishing_mode == "automatic"
        )
        if quality["passed"]:
            if auto_on:
                video.status = "generated"
                slot = await next_open_slot(session)
                session.add(PublishQueue(video_id=video.id, scheduled_for=slot, status="scheduled"))
            else:
                video.status = "pending_approval"
        else:
            video.status = "failed"
            video.fail_reason = quality["notes"]
            await notify(f"Generation rejected: {video.title} ({quality['notes']})")
    except Exception as exc:  # noqa: BLE001
        video.status = "failed"
        video.fail_reason = str(exc)
        await notify(f"Generation error: {exc}")

    idea.status = "used"
    await session.commit()
    await session.refresh(video)
    return video
