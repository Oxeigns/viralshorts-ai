# VIRALSHORTS AI

Autonomous, free-first YouTube Shorts production system.

It discovers public topics from official RSS feeds and original seed ideas, writes a short script locally (Ollama / OpenAI optional), generates original 1080×1920 graphics, narrates with eSpeak NG, renders H.264/AAC with FFmpeg, scores the result, queues three daily IST slots, and uploads only through the official YouTube Data API after Google OAuth.

This is a real FastAPI application with a dark dashboard, SQLite/Postgres, a scheduler, and a Docker/Render layout. It is **not** a promise of unlimited free hosting, YPP approval, or copyright immunity.

## What is free vs paid

| Piece | Default | Notes |
|---|---|---|
| App, dashboard, scheduler | Free / open source | FastAPI, SQLAlchemy, APScheduler |
| Script writer | Free local templates | Optional Ollama or OpenAI-compatible API |
| Voice | Free eSpeak NG | Optional paid TTS can be added later; missing TTS falls back to a tone instead of a silent publish |
| Video render | Free FFmpeg + Pillow | Needs CPU time |
| Media | Original generated graphics | Optional official Pexels / Pixabay keys |
| Database | SQLite locally | Render Postgres is a paid/free-tier service with limits |
| Queue | In-process scheduler | Redis is optional and unused unless you add a broker |
| YouTube | Official OAuth + Data API | Requires your Google Cloud project and quota |
| Hosting | You provide it | Render free web services sleep; video render needs a persistent disk or object storage |

Do not put production videos on ephemeral Render disk without a disk or object store.

## Architecture

```
app/
  main.py                 FastAPI + dashboard
  models.py               SQLAlchemy models
  services/
    discovery.py          RSS + seed ideas + license records
    ai_providers.py       Local / Ollama / OpenAI scripts
    tts.py                eSpeak NG + fallback
    renderer.py           9:16 FFmpeg shorts
    quality.py            Automated checks
    youtube.py            OAuth + resumable upload
    scheduler.py          Asia/Kolkata slots
    publisher.py          Generate-ahead + publish tick
    notify.py             Optional Telegram
  worker.py               Render worker loop
```

Default publish times: **09:00, 14:00, 20:00 IST**. Daily target is 3 and can be changed in Automation.

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo apt-get install -y ffmpeg espeak-ng fonts-dejavu-core   # Debian/Ubuntu
cp .env.example .env
# edit ADMIN_PASSWORD and APP_SECRET_KEY
uvicorn app.main:app --reload --port 8000
```

Open http://127.0.0.1:8000 and sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

Generate a Short from Overview. The file is stored under `data/renders/` and listed in Videos.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

Tests compile the app, score scripts, compute IST slots, hit `/health`, and render a real MP4 with FFmpeg.

## Google OAuth / YouTube

1. Create a Google Cloud project.
2. Enable **YouTube Data API v3**. Analytics scope is requested only if you also enable YouTube Analytics API.
3. Configure OAuth consent and a Web client.
4. Authorized redirect: `{APP_BASE_URL}/api/integrations/youtube/callback`
5. Put `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI` in the environment.
6. In the dashboard open Integrations → Connect YouTube.
7. Confirm the channel is eligible for API uploads. New projects are often in restricted/unverified mode.

Tokens are encrypted with `APP_SECRET_KEY` before they are stored. The UI never asks for a Google password.

## Render

`render.yaml` defines:

- Docker web service (`/health` check)
- Docker worker (`python -m app.worker`)
- Postgres
- Persistent disks for media/renders

Free-tier web services sleep, starter disks and Postgres are not unlimited, and a small instance should **not** run large local LLMs. Use the built-in script writer on Render.

Set at least:

- `ADMIN_PASSWORD`
- `APP_SECRET_KEY` (auto-generated is fine)
- `APP_BASE_URL`
- `GOOGLE_*` if you want publishing
- `OBJECT_STORAGE_URL` if you outgrow the disk

Blueprint create from this repo, or:

```bash
# after first deploy, run a generate from the dashboard
```

There is no live Render URL from this environment. After you deploy, the URL is whatever Render assigns.

## Telegram (optional)

`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` send publish / failure / pause notices. Leave blank to disable.

## What it will not do

- Download YouTube, TikTok, Instagram, or other creators’ videos
- Buy views or run engagement bots
- Guarantee monetization
- Invent trending numbers
- Publish unverified remote media
- Stay online 24/7 on a sleeping free web dyno

## Troubleshooting

| Symptom | Check |
|---|---|
| Login fails | `ADMIN_USERNAME` / `ADMIN_PASSWORD` in `.env` |
| Generate fails with ffmpeg | `ffmpeg -version` and install the package |
| Voice sounds robotic | Expected with eSpeak; optional TTS is not required |
| Queue never publishes | Connect YouTube; confirm the job is not paused; wait for the IST slot or POST `/api/tick` |
| OAuth error | Redirect URI must match exactly; confirm state cookie |
| Render files vanish | Attach a disk or object storage |
| Actions CI red | This GitHub account currently locks Actions on billing; tests still run locally |

## License

Use at your own risk. You are responsible for licenses, YouTube policy, and any API quotas.
