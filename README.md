# VIRALSHORTS AI

Autonomous YouTube Shorts factory: research → script → original 9:16 graphics → voice → FFmpeg render → quality gate → IST publish slots → official YouTube Data API upload. Remote control is a Telegram bot. Secrets are Fernet-encrypted.

This is a real FastAPI app with a dashboard, SQLite/Postgres, APScheduler plus optional Celery/Redis, Alembic, Docker, and a Render Blueprint. It is **not** a promise of unlimited free hosting, YPP approval, or copyright immunity.

## What is free vs paid

| Piece | Default | Notes |
|---|---|---|
| App, dashboard, scheduler | Free / open source | FastAPI, SQLAlchemy, APScheduler |
| Script writer | Local templates | Hacker AI first if `HACKERAI_API_KEY` is set, then Ollama / OpenAI |
| Voice | eSpeak NG | ElevenLabs if configured (encrypted). Unpaid invoice → eSpeak |
| Video render | FFmpeg + Pillow | Needs CPU |
| Media | Original generated graphics | Optional official Pexels / Pixabay keys |
| Database | SQLite locally | Render Postgres is a billed/free-tier service |
| Queue | In-process APScheduler | Redis + Celery are optional (`REDIS_URL`) |
| YouTube | Official OAuth + Data API | Your Google Cloud project and quota |
| Hosting | You provide it | Render free web services sleep; attach a disk or object storage |

Do not put production videos on ephemeral Render disk.

## Architecture

```
app/
  main.py                 FastAPI + dashboard + webhook
  config_schema.py        Configuration Center field catalog
  celery_app.py           Optional Celery worker tasks
  services/
    config_store.py       Encrypted settings + public snapshot
    predeploy.py          Weighted readiness score + JSON report
    telegram_control.py   Admin-only remote control
    ai_providers.py       Hacker AI → Ollama → OpenAI → local
    tts.py                ElevenLabs + eSpeak fallback
    secrets.py            Fernet vault (hint only in UI)
    storage.py            Optional S3/R2 upload
    renderer.py           1080×1920 H.264/AAC
    publisher.py          Generate-ahead + publish tick
    youtube.py            OAuth + resumable upload
  worker.py               Render worker loop
alembic/                  Schema migrations
app.json                  Config schema (NOT a Render Blueprint)
render.yaml               Render Blueprint
```

Default daily target is **24** and the default mode is `automatic` — no approval step.
Slots derive from the target: 3 or fewer keeps the configured times (09:00, 14:00,
20:00 IST); a higher target divides the day evenly, so 24 means one Short every hour
and 12 means every two hours. A day never accepts more than its target, so extra
videos roll into tomorrow. Modes: `automatic`, `approval`, `manual`.

**Quota reality:** YouTube's default quota is 10,000 units/day and one upload costs
about 1,600, so roughly 6 uploads succeed per day until the quota is raised. Slots
beyond that are marked `blocked` with a quota message and automation keeps running;
the Pre-Deploy checker warns when the target exceeds 6.

## App Configuration Center

After login open **Config** (`/configuration`).

- Seven groups: AI, Voice, Telegram, YouTube, Publishing, Storage, Deploy
- Each field explains what it is and where to get the value
- Secrets stay blank in the browser; only a last-4 hint is shown
- Actions: **Save Encrypted Configuration**, **Validate Configuration**, **Run Pre-Deployment Checker**
- `app.json` is the same schema with empty secret placeholders. Deploy with `render.yaml`, not by treating `app.json` as a Blueprint.

## Pre-deployment checker

`/predeploy` and `GET /api/predeploy` (login required) run live checks:

- Hacker AI `GET {base}/models`
- ElevenLabs `/v1/user` (billing issues are warnings, not fake passes)
- Telegram `getMe` + admin allowlist
- Google OAuth + connected channel
- Publish slots / mode
- Database, Redis reachability, object storage
- ffmpeg, eSpeak, `APP_SECRET_KEY`, public URL
- Required project files and importable Python deps

Score weights: YouTube 25, Telegram 15, AI 15, Deploy 15, Voice/Publishing/Storage 10. **READY** only when required groups pass. A JSON copy is written to `data/predeploy-report.json` (gitignored).

## Telegram remote control

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ADMIN_IDS` (comma-separated numeric user ids). The webhook is registered at boot; `POST /api/telegram/webhook/setup` re-registers it manually.

Menu: **Status, Generate, Videos, Approve next, Queue, Analytics, Auto-approve, Errors, Pause, Resume, Pre-Deploy, Help**. Unauthorized users are rejected. Keys are never echoed.

The **Videos** screen lists every video with paging and per-video buttons:

- **✅ Approve #id** — queues that video for the next slot
- **❌ Reject #id** — marks it rejected so it is never published
- **▶️ Watch #id** — uploads the MP4 into the chat so you can judge it before deciding

`GET /api/telegram/diagnose` reports why a silent bot is silent: missing token, unset
or mismatched webhook, Telegram delivery errors, or an empty admin list.

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo apt-get install -y ffmpeg espeak-ng fonts-dejavu-core   # Debian/Ubuntu
cp .env.example .env
# edit ADMIN_PASSWORD and APP_SECRET_KEY
# optionally set ELEVENLABS_API_KEY and HACKERAI_API_KEY (never commit .env)
uvicorn app.main:app --reload --port 8000
```

Open http://127.0.0.1:8000 and sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

Optional Celery (needs Redis):

```bash
celery -A app.celery_app:celery worker --loglevel=info
```

Schema migrations (Postgres or a clean SQLite file):

```bash
alembic upgrade head
```

Local SQLite also creates tables via `init_db()` on boot.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

A GitHub Actions workflow is in `docs/github-actions-ci.yml`. Copy it to `.github/workflows/ci.yml` after granting the `workflow` scope (this account's Actions are currently billing-locked).

`scripts/deploy.sh` compiles the app, runs pytest, and prints the manual Render path. It does **not** mean Render is live.

## Hacker AI

Official docs: https://hackerai.sh/how-to-use-api

- Base URL: `https://api.hackerai.sh/v1`
- Auth: `Authorization: Bearer sk-…`
- Recommended model: `auto`
- Used for scripts, titles, descriptions, **hashtags, captions and keywords**. Falls back to local templates if the key is missing or the reply is not usable.

### Two different tokens, do not mix them

| Token | What it is | Works for scripts? |
|---|---|---|
| `sk-…` | Inference API key, emailed after purchase | **Yes** |
| `hsb_…` | Token for `npx @hackerai/local --token …`, the bridge that lets HackerAI drive a machine | **No** |

`hsb_` is not an inference credential. Pointing `HACKERAI_API_KEY` at it does not produce
scripts. The app now asks the model `2+2` during **Validate Configuration** and refuses
keys whose reply is not an answer: a gateway that answers `200` with unrelated prose is
reported as *not valid for inference* instead of silently filling videos with nonsense.
Both `/v1/models` and unauthenticated requests can return `200`, so neither is used as proof.

Every AI reply must be a JSON object with `hook`, `body`, `captions`, `titles`, `hashtags`,
`keywords` and `description`. Anything else is discarded and the local writer takes over, so
a bad provider can never write a one-line quote into your video.

## ElevenLabs (encrypted)

Do **not** commit the API key. Set `ELEVENLABS_API_KEY` as a Render env var or paste it in Configuration Center. At boot the app encrypts it with Fernet keyed from `APP_SECRET_KEY`. Status APIs show `…last4` only.

If the key is valid but ElevenLabs returns `payment_required` / `payment_issue`, TTS falls back to eSpeak NG until the invoice is paid.

## Google OAuth / YouTube

1. Deploy so you have a public URL (`APP_BASE_URL`).
2. Enable [YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com).
3. Create an OAuth **Web client**. Redirect = `{APP_BASE_URL}/api/integrations/youtube/callback`.
4. Paste Client ID + Secret on **Integrations** or Config, then **Connect YouTube**.
5. Confirm the channel can accept API uploads.

Tokens are encrypted. The UI never asks for a Google password.

## Render

`render.yaml` defines:

- Docker web service (`/health`)
- Docker worker (`python -m app.worker`)
- Optional Celery worker (`celery -A app.celery_app:celery worker`)
- Redis
- Postgres
- Persistent disks for media/renders

There is no live Render URL from this environment. After you create a Blueprint from this repo, set at least:

- `ADMIN_PASSWORD`
- `APP_BASE_URL` (the assigned `https://….onrender.com`)
- `ELEVENLABS_API_KEY` for real voices
- `HACKERAI_API_KEY` for remote scripts
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_ADMIN_IDS`
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`

Then open `/configuration` → Validate → Connect YouTube → webhook setup.

Do not claim deploy is complete until Render shows the web service as live and `/health` returns `{"ok":true}`.

## What it will not do

- Download YouTube, TikTok, Instagram, or other creators’ videos
- Buy views or run engagement bots
- Guarantee monetization
- Invent trending numbers
- Publish unverified remote media
- Stay online 24/7 on a sleeping free web dyno
- Treat `app.json` as a native Render manifest

## Troubleshooting

| Symptom | Check |
|---|---|
| Login fails | `ADMIN_USERNAME` / `ADMIN_PASSWORD` in `.env` |
| Generate fails with ffmpeg | `ffmpeg -version` |
| Voice sounds robotic | Expected with eSpeak; pay ElevenLabs invoice or set a working key |
| ElevenLabs 401 `payment_issue` | Key is valid; billing is not. App falls back to eSpeak |
| Queue never publishes | Connect YouTube; not paused; wait for IST slot or POST `/api/tick` |
| OAuth error | Redirect URI must match exactly |
| Render files vanish | Attach a disk or object storage |
| 502 after login / random 502 | Container was OOM-killed or the event loop was blocked. `FFMPEG_THREADS=1` and `RENDER_MEMORY_BUDGET_MB` keep a 512 MB instance inside its limit; put rendering on the worker for production |
| Actions CI red | GitHub account currently locks Actions on billing |
| Pre-deploy not 100 | Required groups failed; read blockers, do not treat warnings as passes |

## License

Use at your own risk. You are responsible for licenses, YouTube policy, and API quotas.
