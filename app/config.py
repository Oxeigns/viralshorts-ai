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
    database_url: str = "sqlite+aiosqlite:///./data/viralshorts.db"
    redis_url: str = "redis://localhost:6379/0"
    timezone: str = "Asia/Kolkata"
    daily_publish_target: int = 3
    publish_slots: str = "09:00,14:00,20:00"
    quality_threshold: int = 70
    default_language: str = "en"
    automation_paused: bool = False

    media_root: Path = Path("./data/media")
    render_root: Path = Path("./data/renders")
    object_storage_url: str = ""
    object_storage_bucket: str = ""

    admin_username: str = "admin"
    admin_password: str = "change-this-password"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    pexels_api_key: str = ""
    pixabay_api_key: str = ""

    ollama_base_url: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""

    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    elevenlabs_model_id: str = "eleven_multilingual_v2"
    elevenlabs_sts_model_id: str = "eleven_multilingual_sts_v2"

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
        return [s.strip() for s in self.publish_slots.split(",") if s.strip()]

    @property
    def youtube_scope_list(self) -> list[str]:
        return [s for s in self.youtube_scopes.split() if s]

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
