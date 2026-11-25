---
name: viralshorts-ai
description: Sets up and runs ViralShorts AI, a self-hosted FastAPI YouTube Shorts factory that writes scripts, renders original 9:16 videos with TTS and FFmpeg, quality-checks them, and uploads them to YouTube on a schedule, with Telegram approval and remote control. Use when the user asks to generate YouTube Shorts automatically, build a shorts pipeline, bulk-create vertical videos, or auto-upload videos to YouTube.
---

# ViralShorts AI

ViralShorts AI is a self-hosted web application that produces and publishes YouTube Shorts. It discovers topics from public RSS feeds, writes a script, draws original 9:16 graphics, generates a voiceover, renders an MP4 with FFmpeg, scores the result, queues it for a publish slot and uploads it through the official YouTube Data API v3. A Telegram bot provides approval and remote control.

This skill is a runbook: install the app, configure each integration, generate one Short, verify it, then enable scheduled publishing.

## Before you start

Confirm these points with the user before changing anything:

1. This app publishes to a real YouTube channel. Ask before connecting OAuth and before the first upload.
2. YouTube's default quota is 10,000 units per day and one upload costs about 1,600 units, so roughly 6 uploads succeed per day until the quota is raised. Do not promise a higher volume.
3. ElevenLabs is optional and paid. Without it the app uses eSpeak NG, which sounds robotic but works offline.
4. Hosting is the user's responsibility. A free Render web service sleeps and its disk is ephemeral, so production media needs a persistent disk or S3-compatible storage.
5. The app creates original graphics. It does not download other creators' videos, buy views, run engagement bots or guarantee monetization.

Never print, commit or paste secret values into chat. Write them to `.env` or the encrypted Configuration Center only, and keep `.env` out of version control.

## What is in this package

| Path | Purpose |
| --- | --- |
| `app/main.py` | FastAPI app, dashboard routes, JSON API, Telegram webhook |
| `app/services/pipeline.py` | Generation pipeline: idea, script, render, quality gate, queue |
| `app/services/renderer.py` | Pillow frames plus FFmpeg encode to 1080x1920 H.264/AAC |
| `app/services/tts.py` | ElevenLabs synthesis with eSpeak NG fallback |
| `app/services/youtube.py` | Google OAuth and resumable YouTube upload |
| `app/services/telegram_control.py` | Admin-only Telegram bot menu and approvals |
| `app/services/publisher.py` | Scheduler tick: generate ahead, publish due videos |
| `app/services/predeploy.py` | Live readiness checks and score |
| `app/config_schema.py` | Configuration Center field catalog |
| `.env.example` | Every supported environment variable with empty secrets |
| `render.yaml` | Render Blueprint (web, worker, Redis, Postgres, disks) |
| `Dockerfile` | Python 3.12 image with ffmpeg, eSpeak NG and DejaVu fonts |
| `tests/` | Pytest suite for the pipeline, OAuth, Telegram and scheduler |

`app.json` is a configuration schema, not a Render Blueprint. Deploy with `render.yaml` or Docker.

## Prerequisites

- Python 3.11 or newer (3.12 is tested)
- `ffmpeg`, `espeak-ng` and DejaVu fonts
- At least 1 GB RAM; rendering is CPU-heavy
- For scheduled publishing and Telegram: a public HTTPS URL (Render, VPS, or a tunnel)
- Accounts and credentials, all optional except where noted:
  - Google Cloud project with YouTube Data API v3 enabled and an OAuth web client (required for upload)
  - Telegram bot token from BotFather (recommended)
  - ElevenLabs API key (optional, better voices)
  - Hacker AI inference key (optional, better scripts)
  - S3-compatible bucket (optional, durable media storage)

## Setup

Run every command from the folder that contains this SKILL.md.

### Step 1: Create the Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.

### Step 2: Install the system binaries

Debian or Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg espeak-ng fonts-dejavu-core
```

macOS with Homebrew:

```bash
brew install ffmpeg espeak-ng
```

Verify both binaries before continuing:

```bash
ffmpeg -version
espeak-ng --version
```

### Step 3: Create the environment file

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Put the generated value in `APP_SECRET_KEY`. It encrypts stored API keys and signs dashboard sessions; changing it later makes stored secrets unreadable, so keep it stable.

Set these values first:

| Variable | Value |
| --- | --- |
| `APP_SECRET_KEY` | The generated random string |
| `ADMIN_USERNAME` | Dashboard login name |
| `ADMIN_PASSWORD` | Strong password, changed from the example |
| `APP_BASE_URL` | Public base URL, for example the deployed HTTPS origin |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/viralshorts.db` locally, Postgres in production |
| `TIMEZONE` | IANA zone such as `Asia/Kolkata` |
| `PUBLISHING_MODE` | `automatic`, `approval` or `manual` |

Add integration secrets only when the user supplies them: `ELEVENLABS_API_KEY`, `HACKERAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_IDS`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.

### Step 4: Start the app

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Confirm it is healthy:

```bash
curl -s http://127.0.0.1:8000/health
```

Expected response: `{"ok":true,"service":"viralshorts-ai"}`. Open `http://127.0.0.1:8000/login` and sign in with `ADMIN_USERNAME` and `ADMIN_PASSWORD`. On first boot the app creates the SQLite schema and the admin user.

### Step 5: Configure integrations

Open `/configuration`. The form has seven groups: AI, Voice, Telegram, YouTube, Publishing, Storage and Deploy. Secrets are write-only: after saving, the page shows only a last-four hint.

1. **AI**: paste a Hacker AI `sk-` inference key, or leave it empty to use local templates. `hsb_` tokens are for the local bridge and cannot write scripts.
2. **Voice**: paste an ElevenLabs key and choose a voice ID. Leave empty to use eSpeak NG.
3. **Telegram**: paste the bot token and comma-separated numeric admin user IDs.
4. **YouTube**: paste the Google OAuth client ID and secret.
5. **Publishing**: set daily target, three slot times, timezone, language and mode.
6. **Storage**: optional S3-compatible endpoint, bucket and keys.
7. **Deploy**: set `APP_BASE_URL` to the public HTTPS origin.

Save, then click **Validate Configuration**. This runs live probes and returns a score. The same report is available at `GET /api/predeploy` and is written to `data/predeploy-report.json`.

### Step 6: Connect YouTube

1. In Google Cloud Console, create or select a project.
2. Enable **YouTube Data API v3**.
3. Configure the OAuth consent screen. While the app is in testing, add the channel owner as a test user.
4. Create an OAuth client of type **Web application**.
5. Add the exact redirect URI: `{APP_BASE_URL}/api/integrations/youtube/callback`.
6. Save the client ID and client secret in `/integrations` or `/configuration`.
7. Sign in to the dashboard and open `/api/integrations/youtube/start`. Approve the Google consent screen. The callback stores encrypted tokens and redirects to `/integrations`.
8. Confirm the connected channel name appears and that the account is allowed to upload.

Scopes requested: `youtube.upload`, `youtube.readonly`, `yt-analytics.readonly`. The app never asks for a Google password.

### Step 7: Connect Telegram

1. Message BotFather, run `/newbot`, and copy the token.
2. Get the operator's numeric Telegram user ID. A username is not accepted.
3. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ADMIN_IDS` in `.env` or the Configuration Center.
4. Set `APP_BASE_URL` to the public HTTPS origin. The webhook is registered at boot when the URL is public.
5. If the bot is silent, call `POST /api/telegram/webhook/setup` while signed in, then check `GET /api/telegram/diagnose`.
6. Send `/start` to the bot. The menu includes Status, Generate, Videos, Approve next, Queue, Analytics, Auto-approve, Errors, Pause, Resume, Pre-Deploy and Help.

### Step 8: Generate one Short

From the dashboard, click **Generate**. From the API, authenticate first and queue a job:

```bash
curl -s -c cookies.txt -X POST http://127.0.0.1:8000/login \
  -d "username=$ADMIN_USERNAME" -d "password=$ADMIN_PASSWORD"
curl -s -b cookies.txt -X POST http://127.0.0.1:8000/api/generate
```

The response contains a `job_id` and a poll URL. Generation runs in the background because rendering outlives a typical proxy timeout:

```bash
curl -s -b cookies.txt http://127.0.0.1:8000/api/jobs/1
```

The finished video appears on `/videos`. Download it from `/media/{video_id}` and check that it is 1080x1920 with audio before approving.

### Step 9: Approve and publish

- In `approval` mode a generated video waits as `pending_approval`.
- Approve it from the dashboard or with `POST /api/videos/{id}/approve`.
- In Telegram, **Videos** lists each render with **Approve**, **Reject** and **Watch** buttons. **Watch** sends the MP4 into the chat so the user can judge it first.
- Force a publish tick with `POST /api/tick`, or wait for the scheduler, which runs every 10 minutes.
- A successful upload stores the YouTube video ID and URL in `upload_history` and sends a Telegram notice.

Do not report success until a YouTube video ID or URL is present. A queued item is not an upload.

### Step 10: Enable scheduled operation

The scheduler generates ahead of each slot and publishes due videos. Settings:

- `DAILY_PUBLISH_TARGET`: 1 to 48. The default is 24, but the quota caveat above applies.
- Three configured times are used when the target is three or fewer. Above that, the day is divided evenly, so 24 means hourly and 12 means every two hours.
- A day never accepts more than its target; overflow rolls to the next day.
- `PUBLISHING_MODE`: `automatic` publishes without review, `approval` holds each video, `manual` only publishes on request.
- `QUALITY_THRESHOLD`: default 70 of 100. Videos below it are marked failed or blocked instead of published.
- `AUTOMATION_PAUSED`: set true to stop generation and publishing without stopping the web app.

For production, run the web service and the render worker separately (`python -m app.worker`) so FFmpeg does not block the event loop. Celery with Redis is optional for the same reason.

## API quick reference

| Method and path | Purpose |
| --- | --- |
| `GET /health` | Liveness probe |
| `POST /login` | Dashboard session cookie |
| `POST /api/discover` | Fetch and score RSS ideas |
| `POST /api/generate` | Queue one generation job |
| `GET /api/jobs/{id}` | Poll job status |
| `POST /api/videos/{id}/approve` | Queue a video for the next slot |
| `POST /api/tick` | Run generate-and-publish now |
| `GET /api/status` | Automation and integration status |
| `GET /api/predeploy` | Readiness report and score |
| `POST /api/telegram/webhook/setup` | Re-register the Telegram webhook |
| `GET /api/telegram/diagnose` | Explain a silent bot |
| `GET /api/integrations/youtube/start` | Begin Google OAuth |
| `GET /media/{id}` | Download a rendered MP4 |
| `GET /media/{id}/captions` | Download the SRT sidecar |

All routes except `/health`, `/login` and the Telegram webhook require the dashboard session cookie.

## Docker and Render

Docker:

```bash
docker build -t viralshorts-ai .
docker run --env-file .env -p 8000:8000 -v "$PWD/data:/app/data" viralshorts-ai
```

Render: create a Blueprint from `render.yaml`. It defines a Docker web service, a render worker, an optional Celery worker, Redis, Postgres and persistent disks. Set at least `ADMIN_PASSWORD`, `APP_BASE_URL`, `ELEVENLABS_API_KEY`, `HACKERAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_IDS`, `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. Do not treat `app.json` as a Render manifest.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Network hosts contacted

The app contacts these hosts at runtime, and only when the matching feature is configured:

| Host | Used for |
| --- | --- |
| `api.hackerai.sh` | Script, caption and metadata generation |
| `api.openai.com` | Optional OpenAI-compatible fallback |
| User-set Ollama URL | Optional local model fallback |
| `api.elevenlabs.io` | Voiceover synthesis and account probe |
| `api.telegram.org` | Bot control, approvals and notices |
| `accounts.google.com` | Google OAuth consent |
| `oauth2.googleapis.com` | OAuth token exchange and refresh |
| `www.googleapis.com` | YouTube Data API upload and channel lookup |
| `s3.amazonaws.com` or user-set S3 endpoint | Optional object storage |
| `www.nasa.gov`, `commons.wikimedia.org`, `blog.archive.org`, `www.theverge.com`, `hnrss.org`, `feeds.bbci.co.uk`, `feeds.npr.org` | Public RSS topic discovery |
| `api.pexels.com`, `pixabay.com` | Optional stock media keys present in configuration |

No analytics, telemetry or third-party tracking endpoints are called.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Login fails | `ADMIN_USERNAME` and `ADMIN_PASSWORD`, and that `.env` is in the working directory |
| `ffmpeg is not installed` | Run `ffmpeg -version`; install ffmpeg |
| Voice is robotic | Expected with eSpeak NG; add a working ElevenLabs key |
| ElevenLabs returns `payment_required` | The key is valid but billing is not; the app falls back to eSpeak |
| YouTube upload says reconnect required | Re-run `/api/integrations/youtube/start`; refresh token was revoked or expired |
| Uploads stop after about six | Daily YouTube quota reached; slots are marked blocked until tomorrow or a quota increase |
| Queue never publishes | YouTube not connected, automation paused, or no due slot; `POST /api/tick` to test |
| Telegram bot silent | Check token, public `APP_BASE_URL`, webhook status and numeric admin IDs |
| 502 or OOM on a 512 MB instance | Set `FFMPEG_THREADS=1`, lower `RENDER_MEMORY_BUDGET_MB`, run rendering on the worker |
| Render files vanish after restart | Ephemeral disk; attach a disk or configure S3-compatible storage |
| OAuth redirect error | The redirect URI must match `{APP_BASE_URL}/api/integrations/youtube/callback` exactly |

## Reporting back

When the setup is done, tell the user exactly what is configured, what is still missing and whether a video was actually uploaded. Distinguish a rendered local file from a published YouTube video. Never claim an upload, quota increase or monetization result that was not observed.
