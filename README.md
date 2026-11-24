# VIRALSHORTS AI

Autonomous, free-first YouTube Shorts production system.

It discovers public topics from official RSS feeds and original seed ideas, writes a short script locally (Ollama / OpenAI optional), generates original 1080×1920 graphics, narrates with ElevenLabs when configured (otherwise eSpeak NG), renders H.264/AAC with FFmpeg, scores the result, queues three daily IST slots, and uploads only through the official YouTube Data API after Google OAuth.

This is a real FastAPI application with a dark dashboard, SQLite/Postgres, a scheduler, and a Docker/Render layout. It is **not** a promise of unlimited free hosting, YPP approval, or copyright immunity.

## What is free vs paid

| Piece | Default | Notes |
|---|---|---|
| App, dashboard, scheduler | Free / open source | FastAPI, SQLAlchemy, APScheduler |
| Script writer | Free local templates | Optional Ollama or OpenAI-compatible API |
| Voice | Free eSpeak NG | ElevenLabs if `ELEVENLABS_API_KEY` is set (encrypted at rest). Falls back to eSpeak, then a tone |
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
    tts.py                ElevenLabs TTS + speech-to-speech, eSpeak fallback
    secrets.py            Fernet-encrypted API keys (hint only in UI/reports)
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
# optionally set ELEVENLABS_API_KEY (never commit .env)
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

A GitHub Actions workflow is in `docs/github-actions-ci.yml`. Copy it to `.github/workflows/ci.yml` after granting the `workflow` scope (this account's Actions are also currently billing-locked).

## ElevenLabs (encrypted)

Do **not** commit the API key. On deploy, set `ELEVENLABS_API_KEY` as a Render environment variable (or a GitHub Actions secret). At boot the app encrypts it with Fernet keyed from `APP_SECRET_KEY` and stores only ciphertext. Status APIs and the dashboard show `…last4` only.

Used for:

- narration on every generated Short
- `/api/voice/restyle` speech-to-speech on an existing clip

If the key is missing or ElevenLabs errors, eSpeak NG is used.

You can also paste the key on **Integrations** after login. That path encrypts immediately and never writes plaintext to git.

## Google OAuth / YouTube (easy path after deploy)

1. Deploy the app so you have a public URL (`APP_BASE_URL`).
2. Open [YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com) and enable it.
3. Create an OAuth **Web client**. Authorized redirect = `{APP_BASE_URL}/api/integrations/youtube/callback` (the Integrations page shows the exact URI).
4. In the dashboard open **Integrations**, paste Client ID + Client Secret (they are encrypted the same way as ElevenLabs), then click **Connect YouTube** and approve your channel.
5. Confirm the channel is eligible for API uploads. New projects are often in restricted/unverified mode.

You can also set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` as Render env vars. The UI never asks for a Google password. Tokens are encrypted with `APP_SECRET_KEY`.

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
- `ELEVENLABS_API_KEY` for real voices
- `GOOGLE_*` if you want publishing
- `OBJECT_STORAGE_URL` if you outgrow the disk

Blueprint create from this repo. There is no live Render URL from this environment. After you deploy, the URL is whatever Render assigns.

Then:

1. Set `APP_BASE_URL` to that URL.
2. Copy the redirect URI from Integrations into Google Cloud.
3. Click **Connect YouTube**.

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
| Voice sounds robotic | Expected with eSpeak; set encrypted ElevenLabs key on Integrations |
| ElevenLabs 401 `payment_issue` | Key is valid but the ElevenLabs invoice is unpaid; app falls back to eSpeak until billing is current |
| Queue never publishes | Connect YouTube; confirm the job is not paused; wait for the IST slot or POST `/api/tick` |
| OAuth error | Redirect URI must match exactly; confirm state cookie |
| Render files vanish | Attach a disk or object storage |
| Actions CI red | This GitHub account currently locks Actions on billing; tests still run locally |

## License

Use at your own risk. You are responsible for licenses, YouTube policy, and any API quotas.
