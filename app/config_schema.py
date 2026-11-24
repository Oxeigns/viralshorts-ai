"""Single source of truth for the App Configuration Center.

Placeholder values only. Real credentials never belong in this file or app.json.
"""

from __future__ import annotations

from typing import Any

Field = dict[str, Any]

GROUPS: list[dict[str, Any]] = [
    {
        "id": "ai",
        "title": "AI Configuration",
        "help": "Hacker AI is an OpenAI-compatible chat API. The inference key starts with sk- and is emailed after purchase. An hsb_ token is the local bridge (npx @hackerai/local) and cannot write scripts. Docs: https://hackerai.sh/how-to-use-api",
        "fields": [
            {
                "key": "hackerai_api_key",
                "label": "Hacker AI API Key",
                "secret": True,
                "required": False,
                "placeholder": "sk-your-key-here",
                "env": "HACKERAI_API_KEY",
                "obtain": "Get an sk- key at https://hackerai.sh/pricing (emailed). hsb_ tokens are for the local bridge, not inference. Validate asks 2+2 and rejects endpoints that answer with prose.",
            },
            {
                "key": "hackerai_api_base_url",
                "label": "Hacker AI API Base URL",
                "secret": False,
                "required": False,
                "placeholder": "https://api.hackerai.sh/v1",
                "default": "https://api.hackerai.sh/v1",
                "env": "HACKERAI_API_BASE_URL",
                "obtain": "Official base URL from https://hackerai.sh/how-to-use-api",
            },
            {
                "key": "hackerai_model",
                "label": "Hacker AI Model",
                "secret": False,
                "required": False,
                "placeholder": "auto",
                "default": "auto",
                "env": "HACKERAI_MODEL",
                "obtain": "Use auto unless you need a specific model listed at https://api.hackerai.sh/v1/models",
            },
            {
                "key": "hackerai_provider_status",
                "label": "Hacker AI Provider Status",
                "secret": False,
                "required": False,
                "readonly": True,
                "placeholder": "unchecked",
                "env": "",
                "obtain": "Filled by Validate Configuration. Never edited by hand.",
            },
        ],
    },
    {
        "id": "voice",
        "title": "Voice Configuration",
        "help": "ElevenLabs official API. Stored encrypted. Unpaid invoices make TTS fall back to eSpeak NG.",
        "fields": [
            {
                "key": "elevenlabs_api_key",
                "label": "ElevenLabs API Key",
                "secret": True,
                "required": False,
                "placeholder": "sk_…",
                "env": "ELEVENLABS_API_KEY",
                "obtain": "https://elevenlabs.io → Profile → API keys",
            },
            {
                "key": "elevenlabs_voice_id",
                "label": "ElevenLabs Voice ID",
                "secret": False,
                "required": False,
                "placeholder": "21m00Tcm4TlvDq8ikWAM",
                "default": "21m00Tcm4TlvDq8ikWAM",
                "env": "ELEVENLABS_VOICE_ID",
                "obtain": "Voice Lab in ElevenLabs, or Integrations → list voices after the key is saved.",
            },
            {
                "key": "default_voice",
                "label": "Default Voice",
                "secret": False,
                "required": False,
                "placeholder": "Rachel",
                "default": "Rachel",
                "env": "DEFAULT_VOICE",
                "obtain": "Friendly name only; Voice ID is what the API uses.",
            },
            {
                "key": "voice_language",
                "label": "Voice Language",
                "secret": False,
                "required": False,
                "placeholder": "en",
                "default": "en",
                "env": "VOICE_LANGUAGE",
                "obtain": "en, hi, or hinglish. eSpeak uses this when ElevenLabs is unavailable.",
            },
            {
                "key": "voice_stability",
                "label": "Voice Stability",
                "secret": False,
                "required": False,
                "placeholder": "0.5",
                "default": "0.5",
                "env": "VOICE_STABILITY",
                "obtain": "0.0–1.0 ElevenLabs voice_settings.stability",
            },
            {
                "key": "voice_similarity",
                "label": "Voice Similarity",
                "secret": False,
                "required": False,
                "placeholder": "0.75",
                "default": "0.75",
                "env": "VOICE_SIMILARITY",
                "obtain": "0.0–1.0 ElevenLabs voice_settings.similarity_boost",
            },
        ],
    },
    {
        "id": "telegram",
        "title": "Telegram Configuration",
        "help": "Telegram is the primary remote-control surface. Only listed admin IDs can drive the bot.",
        "fields": [
            {
                "key": "telegram_bot_token",
                "label": "Telegram Bot Token",
                "secret": True,
                "required": False,
                "placeholder": "123456:ABC…",
                "env": "TELEGRAM_BOT_TOKEN",
                "obtain": "Talk to @BotFather → /newbot. Never paste the token into git.",
            },
            {
                "key": "telegram_admin_ids",
                "label": "Authorized Telegram Admin ID",
                "secret": False,
                "required": False,
                "placeholder": "123456789",
                "env": "TELEGRAM_ADMIN_IDS",
                "obtain": "Message @userinfobot. Comma-separate multiple admins.",
            },
            {
                "key": "telegram_notifications",
                "label": "Telegram Notifications Enabled",
                "secret": False,
                "required": False,
                "placeholder": "true",
                "default": "true",
                "env": "TELEGRAM_NOTIFICATIONS",
                "obtain": "true or false. Controls publish/fail notices.",
            },
        ],
    },
    {
        "id": "youtube",
        "title": "YouTube Configuration",
        "help": "Official YouTube Data API v3 + OAuth 2.0. The app never asks for a Google password.",
        "fields": [
            {
                "key": "google_client_id",
                "label": "Google Client ID",
                "secret": False,
                "required": False,
                "placeholder": "….apps.googleusercontent.com",
                "env": "GOOGLE_CLIENT_ID",
                "obtain": "Google Cloud → APIs & Services → Credentials → OAuth Web client.",
            },
            {
                "key": "google_client_secret",
                "label": "Google Client Secret",
                "secret": True,
                "required": False,
                "placeholder": "GOCSPX-…",
                "env": "GOOGLE_CLIENT_SECRET",
                "obtain": "Same OAuth Web client. Stored encrypted.",
            },
            {
                "key": "youtube_refresh_token",
                "label": "YouTube OAuth Refresh Token",
                "secret": True,
                "required": False,
                "readonly": True,
                "placeholder": "set via Connect YouTube",
                "env": "",
                "obtain": "Created automatically by Connect YouTube. Do not paste tokens into git.",
            },
            {
                "key": "youtube_channel_status",
                "label": "Connected Channel Status",
                "secret": False,
                "required": False,
                "readonly": True,
                "placeholder": "not connected",
                "env": "",
                "obtain": "Filled after a successful OAuth callback.",
            },
        ],
    },
    {
        "id": "publishing",
        "title": "Publishing Configuration",
        "help": "Default factory cadence is 3 Shorts per day at 09:00, 14:00, 20:00 IST.",
        "fields": [
            {
                "key": "daily_target",
                "label": "Daily Upload Limit",
                "secret": False,
                "required": True,
                "placeholder": "24",
                "default": "24",
                "env": "DAILY_PUBLISH_TARGET",
                "obtain": "Integer 1–48. Above 3 the day is divided into even slots (24 = hourly). Default YouTube quota still allows only about 6 successful uploads/day until it is raised.",
            },
            {
                "key": "publish_time_1",
                "label": "Publishing Time 1",
                "secret": False,
                "required": True,
                "placeholder": "09:00",
                "default": "09:00",
                "env": "PUBLISH_TIME_1",
                "obtain": "24-hour HH:MM in the configured timezone.",
            },
            {
                "key": "publish_time_2",
                "label": "Publishing Time 2",
                "secret": False,
                "required": True,
                "placeholder": "14:00",
                "default": "14:00",
                "env": "PUBLISH_TIME_2",
                "obtain": "24-hour HH:MM in the configured timezone.",
            },
            {
                "key": "publish_time_3",
                "label": "Publishing Time 3",
                "secret": False,
                "required": True,
                "placeholder": "20:00",
                "default": "20:00",
                "env": "PUBLISH_TIME_3",
                "obtain": "24-hour HH:MM in the configured timezone.",
            },
            {
                "key": "timezone",
                "label": "Time Zone",
                "secret": False,
                "required": True,
                "placeholder": "Asia/Kolkata",
                "default": "Asia/Kolkata",
                "env": "TIMEZONE",
                "obtain": "IANA name, e.g. Asia/Kolkata.",
            },
            {
                "key": "default_language",
                "label": "Default Video Language",
                "secret": False,
                "required": True,
                "placeholder": "en",
                "default": "en",
                "env": "DEFAULT_LANGUAGE",
                "obtain": "en, hi, hinglish, or another language the script writer supports.",
            },
            {
                "key": "video_category",
                "label": "Default Video Category",
                "secret": False,
                "required": False,
                "placeholder": "24",
                "default": "24",
                "env": "YOUTUBE_CATEGORY_ID",
                "obtain": "YouTube category ID. 24 = Entertainment.",
            },
            {
                "key": "publishing_mode",
                "label": "Publishing Mode",
                "secret": False,
                "required": True,
                "placeholder": "automatic",
                "default": "automatic",
                "env": "PUBLISHING_MODE",
                "obtain": "automatic, approval, or manual.",
            },
            {
                "key": "approval_required",
                "label": "Approval Required",
                "secret": False,
                "required": False,
                "placeholder": "false",
                "default": "false",
                "env": "APPROVAL_REQUIRED",
                "obtain": "false (default) never waits for approval. true forces pending_approval as an extra gate.",
            },
            {
                "key": "auto_approve",
                "label": "Auto-approve New Videos",
                "secret": False,
                "required": False,
                "placeholder": "true",
                "default": "true",
                "env": "AUTO_APPROVE",
                "obtain": "true queues every video that passes the quality gate into the next slot without asking. false keeps them in pending_approval.",
            },
        ],
    },
    {
        "id": "storage",
        "title": "Storage Configuration",
        "help": "Do not keep production MP4s on ephemeral Render disk. Attach a disk or S3-compatible bucket.",
        "fields": [
            {
                "key": "database_url",
                "label": "PostgreSQL Database URL",
                "secret": True,
                "required": False,
                "placeholder": "postgresql://user:pass@host:5432/viralshorts",
                "env": "DATABASE_URL",
                "obtain": "Render Postgres connection string, or local sqlite+aiosqlite:///./data/viralshorts.db",
            },
            {
                "key": "redis_url",
                "label": "Redis URL",
                "secret": False,
                "required": False,
                "placeholder": "redis://localhost:6379/0",
                "default": "redis://localhost:6379/0",
                "env": "REDIS_URL",
                "obtain": "Optional broker for Celery. APScheduler still runs without Redis.",
            },
            {
                "key": "object_storage_url",
                "label": "S3-Compatible Storage Endpoint",
                "secret": False,
                "required": False,
                "placeholder": "https://s3.amazonaws.com",
                "env": "OBJECT_STORAGE_URL",
                "obtain": "AWS, Cloudflare R2, or any S3-compatible endpoint.",
            },
            {
                "key": "storage_access_key",
                "label": "Storage Access Key",
                "secret": True,
                "required": False,
                "placeholder": "AKIA…",
                "env": "STORAGE_ACCESS_KEY",
                "obtain": "Access key for the bucket. Leave blank to stay on local disk.",
            },
            {
                "key": "storage_secret_key",
                "label": "Storage Secret Key",
                "secret": True,
                "required": False,
                "placeholder": "secret",
                "env": "STORAGE_SECRET_KEY",
                "obtain": "Secret key for the bucket.",
            },
            {
                "key": "object_storage_bucket",
                "label": "Storage Bucket Name",
                "secret": False,
                "required": False,
                "placeholder": "viralshorts-media",
                "env": "OBJECT_STORAGE_BUCKET",
                "obtain": "Bucket that already exists and is writable.",
            },
        ],
    },
    {
        "id": "deploy",
        "title": "Deployment Configuration",
        "help": "Maps onto render.yaml. app.json is not a Render Blueprint.",
        "fields": [
            {
                "key": "app_env",
                "label": "Application Environment",
                "secret": False,
                "required": True,
                "placeholder": "production",
                "default": "development",
                "env": "APP_ENV",
                "obtain": "development or production.",
            },
            {
                "key": "frontend_url",
                "label": "Frontend URL",
                "secret": False,
                "required": False,
                "placeholder": "https://your-app.onrender.com",
                "env": "FRONTEND_URL",
                "obtain": "Public URL users open. Same as the Render web service for this app.",
            },
            {
                "key": "app_base_url",
                "label": "Backend URL",
                "secret": False,
                "required": True,
                "placeholder": "https://your-app.onrender.com",
                "env": "APP_BASE_URL",
                "obtain": "Public origin used for OAuth redirects and Telegram webhooks.",
            },
            {
                "key": "worker_enabled",
                "label": "Worker Enabled",
                "secret": False,
                "required": False,
                "placeholder": "true",
                "default": "true",
                "env": "WORKER_ENABLED",
                "obtain": "true starts generate/publish ticks on the worker service.",
            },
            {
                "key": "scheduler_enabled",
                "label": "Scheduler Enabled",
                "secret": False,
                "required": False,
                "placeholder": "true",
                "default": "true",
                "env": "SCHEDULER_ENABLED",
                "obtain": "true keeps the in-process APScheduler tick.",
            },
            {
                "key": "log_level",
                "label": "Logging Level",
                "secret": False,
                "required": False,
                "placeholder": "INFO",
                "default": "INFO",
                "env": "LOG_LEVEL",
                "obtain": "DEBUG, INFO, WARNING, or ERROR.",
            },
        ],
    },
]


def all_fields() -> list[Field]:
    fields: list[Field] = []
    for group in GROUPS:
        fields.extend(group["fields"])
    return fields


def field_by_key(key: str) -> Field | None:
    for item in all_fields():
        if item["key"] == key:
            return item
    return None


def placeholder_app_json() -> dict[str, Any]:
    values: dict[str, Any] = {}
    for item in all_fields():
        if item.get("readonly"):
            continue
        if item.get("secret"):
            values[item["key"]] = ""
        else:
            values[item["key"]] = item.get("default") or item.get("placeholder") or ""
    return {
        "name": "viralshorts-ai",
        "description": "VIRALSHORTS AI — production YouTube Shorts factory",
        "repository": "https://github.com/Oxeigns/viralshorts-ai",
        "website": "https://github.com/Oxeigns/viralshorts-ai",
        "success_url": "/",
        "env": {
            item["env"]: {
                "description": item["label"],
                "required": bool(item.get("required")),
                "value": "" if item.get("secret") else (item.get("default") or ""),
            }
            for item in all_fields()
            if item.get("env")
        },
        "formation": {"web": {"quantity": 1}, "worker": {"quantity": 1}},
        "addons": ["postgresql"],
        "config": values,
        "note": "app.json is a configuration schema, not a Render Blueprint. Use render.yaml for Render.",
    }
