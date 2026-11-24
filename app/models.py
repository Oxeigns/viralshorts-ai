from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    youtube_channel_id: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    thumbnail_url: Mapped[str] = mapped_column(String(500), default="")
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    oauth: Mapped["OAuthCredential | None"] = relationship(back_populates="channel", uselist=False)


class OAuthCredential(Base):
    __tablename__ = "oauth_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    encrypted_refresh_token: Mapped[str] = mapped_column(Text)
    encrypted_access_token: Mapped[str] = mapped_column(Text, default="")
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[str] = mapped_column(Text, default="")

    channel: Mapped[Channel] = relationship(back_populates="oauth")


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(80))
    source_url: Mapped[str] = mapped_column(String(1000))
    creator: Mapped[str] = mapped_column(String(255), default="")
    license_name: Mapped[str] = mapped_column(String(255), default="")
    commercial_use: Mapped[bool] = mapped_column(Boolean, default=False)
    attribution_required: Mapped[bool] = mapped_column(Boolean, default=True)
    attribution_text: Mapped[str] = mapped_column(Text, default="")
    original_id: Mapped[str] = mapped_column(String(255), default="")
    rights_status: Mapped[str] = mapped_column(String(40), default="unverified")
    local_path: Mapped[str] = mapped_column(String(1000), default="")
    kind: Mapped[str] = mapped_column(String(40), default="image")
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContentIdea(Base):
    __tablename__ = "content_ideas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(400))
    category: Mapped[str] = mapped_column(String(80))
    language: Mapped[str] = mapped_column(String(16), default="en")
    source: Mapped[str] = mapped_column(String(80), default="internal")
    source_url: Mapped[str] = mapped_column(String(1000), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    relevance_score: Mapped[float] = mapped_column(Float, default=0)
    popularity: Mapped[float] = mapped_column(Float, default=0)
    recency: Mapped[float] = mapped_column(Float, default=0)
    entertainment: Mapped[float] = mapped_column(Float, default=0)
    license_availability: Mapped[float] = mapped_column(Float, default=0)
    originality: Mapped[float] = mapped_column(Float, default=0)
    safety: Mapped[float] = mapped_column(Float, default=1)
    status: Mapped[str] = mapped_column(String(40), default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    scripts: Mapped[list["Script"]] = relationship(back_populates="idea")


class Script(Base):
    __tablename__ = "scripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("content_ideas.id"))
    language: Mapped[str] = mapped_column(String(16), default="en")
    hook: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    full_text: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(40), default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    idea: Mapped[ContentIdea] = relationship(back_populates="scripts")


class GeneratedVideo(Base):
    __tablename__ = "generated_videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idea_id: Mapped[int | None] = mapped_column(ForeignKey("content_ideas.id"), nullable=True)
    script_id: Mapped[int | None] = mapped_column(ForeignKey("scripts.id"), nullable=True)
    file_path: Mapped[str] = mapped_column(String(1000), default="")
    duration_sec: Mapped[float] = mapped_column(Float, default=0)
    width: Mapped[int] = mapped_column(Integer, default=1080)
    height: Mapped[int] = mapped_column(Integer, default=1920)
    status: Mapped[str] = mapped_column(String(40), default="processing")
    quality_score: Mapped[float] = mapped_column(Float, default=0)
    quality_notes: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    title_suggestions: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[str] = mapped_column(Text, default="")
    pinned_comment: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(16), default="en")
    fail_reason: Mapped[str] = mapped_column(Text, default="")
    voice_used: Mapped[str] = mapped_column(String(80), default="")
    category: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PublishQueue(Base):
    __tablename__ = "publish_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("generated_videos.id"), unique=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), default="scheduled")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")


class UploadHistory(Base):
    __tablename__ = "upload_history"
    __table_args__ = (UniqueConstraint("youtube_video_id", name="uq_youtube_video"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("generated_videos.id"))
    youtube_video_id: Mapped[str] = mapped_column(String(40), default="")
    youtube_url: Mapped[str] = mapped_column(String(255), default="")
    visibility: Mapped[str] = mapped_column(String(20), default="public")
    status: Mapped[str] = mapped_column(String(40), default="uploaded")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    youtube_video_id: Mapped[str] = mapped_column(String(40), default="")
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    subscribers: Mapped[int] = mapped_column(Integer, default=0)
    average_view_duration: Mapped[float] = mapped_column(Float, default=0)
    category: Mapped[str] = mapped_column(String(80), default="")
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SchedulerSettings(Base):
    __tablename__ = "scheduler_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    daily_target: Mapped[int] = mapped_column(Integer, default=3)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    slots: Mapped[str] = mapped_column(String(120), default="09:00,14:00,20:00")
    language: Mapped[str] = mapped_column(String(16), default="en")
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    quality_threshold: Mapped[int] = mapped_column(Integer, default=70)
    publishing_mode: Mapped[str] = mapped_column(String(20), default="approval")
    approval_required: Mapped[bool] = mapped_column(Boolean, default=True)
    video_category: Mapped[str] = mapped_column(String(16), default="24")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="queued")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AppLog(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EncryptedSecret(Base):
    __tablename__ = "encrypted_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    ciphertext: Mapped[str] = mapped_column(Text, default="")
    hint: Mapped[str] = mapped_column(String(16), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(80), default="system")
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
