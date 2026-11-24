from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ContentIdea, GeneratedVideo, Job, MediaAsset, PublishQueue, Script, utcnow
from app.services.ai_providers import draft_script
from app.services.discovery import discover_ideas, generated_asset_id, license_record
from app.services.notify import notify
from app.services.quality import evaluate
from app.services.renderer import render_short
from app.services.scheduler import next_open_slot


async def run_generation(session: AsyncSession, language: str | None = None) -> GeneratedVideo:
    settings = get_settings()
    language = language or settings.default_language
    job = Job(kind="generate", status="running", detail="pipeline start")
    session.add(job)
    await session.commit()

    ideas = await discover_ideas(session)
    idea = next((i for i in ideas if i.status in {"new", "ready"}), None)
    if idea is None:
        idea = await session.scalar(select(ContentIdea).order_by(ContentIdea.created_at.desc()))
    if idea is None:
        raise RuntimeError("no content ideas available")

    draft = await draft_script(idea.title, idea.category, language)
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
    )
    session.add(video)
    await session.commit()
    await session.refresh(video)

    try:
        rendered = render_short(video.id, draft.hook, draft.body, language=language)
        quality = evaluate(rendered["path"], rendered["duration"], True, draft.full_text, draft.hook)
        video.file_path = rendered["path"]
        video.duration_sec = rendered["duration"]
        video.width = rendered["width"]
        video.height = rendered["height"]
        video.quality_score = quality["score"]
        video.quality_notes = quality["notes"]
        if quality["passed"]:
            video.status = "generated"
            slot = await next_open_slot(session)
            session.add(PublishQueue(video_id=video.id, scheduled_for=slot, status="scheduled"))
        else:
            video.status = "failed"
            video.fail_reason = quality["notes"]
            await notify(f"Generation rejected: {video.title} ({quality['notes']})")
    except Exception as exc:  # noqa: BLE001
        video.status = "failed"
        video.fail_reason = str(exc)
        await notify(f"Generation error: {exc}")

    idea.status = "used"
    job.status = "done" if video.status != "failed" else "failed"
    job.detail = video.fail_reason or video.status
    job.finished_at = utcnow()
    await session.commit()
    await session.refresh(video)
    return video
