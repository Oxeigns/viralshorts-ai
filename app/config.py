from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_secret_key: str = Field(default="dev-only-change-me")
    app_base_url: str = "http://127.0.0.1:8000"
    frontend_url: str = ""
    database_url: str = "sqlite+aiosqlite:///./data/viralshorts.db"
    redis_url: str = "redis://localhost:6379/0"
    timezone: str = "Asia/Kolkata"
    daily_publish_target: int = 3
    publish_slots: str = "09:00,14:00,20:00"
    publish_time_1: str = "09:00"
    publish_time_2: str = "14:00"
    publish_time_3: str = "20:00"
    quality_threshold: int = 70
    default_language: str = "en"
    automation_paused: bool = False
    youtube_category_id: str = "24"
    publishing_mode: str = "approval"
    approval_required: bool = True
    worker_enabled: bool = True
    scheduler_enabled: bool = True
    log_level: str = "INFO"

    media_root: Path = Path("./data/media")
    render_root: Path = Path("./data/renders")
    object_storage_url: str = ""
    object_storage_bucket: str = ""
    storage_access_key: str = ""
    storage_secret_key: str = ""

    admin_username: str = "admin"
    admin_password: str = "change-this-password"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_admin_ids: str = ""
    telegram_notifications: bool = True

    pexels_api_key: str = ""
    pixabay_api_key: str = ""

    ollama_base_url: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""

    hackerai_api_key: str = ""
    hackerai_api_base_url: str = "https://api.hackerai.sh/v1"
    hackerai_model: str = "auto"

    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    elevenlabs_model_id: str = "eleven_multilingual_v2"
    elevenlabs_sts_model_id: str = "eleven_multilingual_sts_v2"
    default_voice: str = "Rachel"
    voice_language: str = "en"
    voice_stability: float = 0.5
    voice_similarity: float = 0.75

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://127.0.0.1:8000/api/integrations/youtube/callback"
    youtube_scopes: str = (
        "https://www.googleapis.com/auth/youtube.upload "
        "https://www.googleapis.com/auth/youtube.readonly "
        "https://www.googleapis.com/auth/yt-analytics.readonly"
    )

    @property
    def slots(self) -> list[str]:
        from_parts = [self.publish_time_1, self.publish_time_2, self.publish_time_3]
        if all(part.strip() for part in from_parts):
            return [part.strip() for part in from_parts]
        return [item.strip() for item in self.publish_slots.split(",") if item.strip()]

    def telegram_admins(self) -> set[str]:
        raw = self.telegram_admin_ids or self.telegram_chat_id
        return {item.strip() for item in raw.split(",") if item.strip()}

    @property
    def youtube_scope_list(self) -> list[str]:
        return [item for item in self.youtube_scopes.split() if item]

    def normalized_database_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql+asyncpg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = "postgresql+asyncpg://" + url[len("postgresql://"):]
        return url

    def ensure_dirs(self) -> None:
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.render_root.mkdir(parents=True, exist_ok=True)
        Path("./data").mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
