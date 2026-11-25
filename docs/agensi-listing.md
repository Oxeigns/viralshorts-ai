# Agensi listing copy (resubmission)

Paste these values into the existing listing. Do not add external links to the listing fields.

## Name (47 characters)

Automatic YouTube Shorts Generator and Uploader

## Summary (272 characters)

Self-hosted FastAPI app that turns public RSS topics into original 9:16 YouTube Shorts: script, on-screen graphics, TTS voiceover, FFmpeg render and a quality gate, then scheduled uploads through the official YouTube Data API. Telegram bot for approval and remote control.

## Description (Markdown)

ViralShorts AI is a self-hosted FastAPI application that runs a complete YouTube Shorts pipeline on your own machine or server. It does not generate READMEs or API documentation. It creates vertical videos and uploads them.

**What it does**

1. Discovers topics from public RSS feeds: NASA, Wikimedia, Internet Archive, The Verge, Hacker News, BBC and NPR.
2. Writes a script and metadata. Hacker AI is tried first, then Ollama, OpenAI, then built-in templates. Every AI reply must be structured JSON with a hook, body, captions, titles, hashtags, keywords and description, or it is discarded.
3. Draws original 9:16 graphics with Pillow and synthesizes a voiceover with ElevenLabs. If ElevenLabs is missing or its invoice is unpaid, eSpeak NG is used instead.
4. Renders a 1080x1920 H.264/AAC MP4 with FFmpeg, burns in captions and writes an SRT sidecar.
5. Scores the result 0-100 with a quality gate: duration, resolution, audio stream, text length and caption checks. Videos below the threshold (70 by default) do not publish.
6. Queues the video for a publish slot. Slots derive from the daily target and timezone, and a day never exceeds its target.
7. Uploads through the official YouTube Data API v3 with OAuth and a resumable upload, then sends a Telegram notice with the video URL.
8. Provides a web dashboard and an admin-only Telegram bot with Status, Generate, Videos, Approve, Reject, Watch, Queue, Analytics, Auto-approve, Errors, Pause, Resume, Pre-Deploy and Help.

**How it is configured**

Python 3.11 or newer, `pip install -r requirements.txt`, FFmpeg, eSpeak NG and DejaVu fonts. Copy `.env.example` to `.env`, set `ADMIN_PASSWORD` and a stable `APP_SECRET_KEY`, then run `uvicorn app.main:app`. Configure the integrations in the dashboard's Configuration Center; secrets are stored Fernet-encrypted and only a last-four hint is shown. Docker and a Render Blueprint are included.

**Services it connects to**

- ElevenLabs at `api.elevenlabs.io` for paid voiceovers. Optional; eSpeak NG is the offline fallback.
- Google at `accounts.google.com`, `oauth2.googleapis.com` and `www.googleapis.com` for OAuth and YouTube Data API v3 uploads. A Google Cloud project with the YouTube Data API enabled is required for publishing.
- Telegram at `api.telegram.org` for the control bot, approvals and notices. Optional.
- Hacker AI at `api.hackerai.sh` for scripts and metadata. Optional.
- OpenAI at `api.openai.com`, or a user-supplied Ollama URL, as optional script fallbacks.
- NASA, Wikimedia, Internet Archive, The Verge, Hacker News, BBC and NPR RSS feeds for topic discovery.
- An S3-compatible bucket (Amazon S3, Cloudflare R2, Backblaze B2 or MinIO) for durable media storage. Optional.
- Pexels at `api.pexels.com` and Pixabay at `pixabay.com` appear in the optional stock-media configuration.

**What you need**

- Python 3.11+, FFmpeg, eSpeak NG and DejaVu fonts.
- A Google Cloud project, an OAuth web client and a YouTube channel that can accept API uploads.
- A public HTTPS URL for the OAuth callback and Telegram webhook when deploying.
- Optional paid keys for ElevenLabs and Hacker AI.
- Hosting that you provide. The app does not include hosting or a YouTube quota increase.

**Where it stops**

- YouTube's default quota allows roughly 6 uploads per day. Higher targets wait or are marked blocked until the quota is raised.
- Free hosting that sleeps, or an ephemeral disk, will lose in-progress media.
- It does not download other creators' videos, buy views, run engagement bots, invent trend numbers or guarantee monetization.
- Voices are robotic without ElevenLabs. Script quality depends on the configured model.
- It creates original graphics; it does not license third-party footage.

## See it in action (real local run, approval mode)

```
POST /login                         -> 303 See Other, session cookie set
POST /api/generate                  -> {"job_id":1,"status":"queued","poll":"/api/jobs/1"}
GET  /api/jobs/1                    -> {"id":1,"kind":"generate","status":"done",
                                        "detail":"video 1 pending_approval",
                                        "created_at":"2026-11-25T16:51:40.564417",
                                        "finished_at":"2026-11-25T16:51:48.327110"}
ffprobe data/renders/short-1.mp4    -> video: h264 1080x1920, audio: aac,
                                        duration 19.190884 s, size 605632 bytes
POST /api/videos/1/approve          -> {"ok":true,"id":1,"status":"generated"}
publish_queue                       -> scheduled_for 2026-11-26 03:30:00 UTC, status scheduled
generated_videos                    -> quality_score 100.0, quality_notes "ok"
data/renders/short-1.srt            -> timed caption cues written
```

The run produced a 19.2-second 1080x1920 MP4 with a burned-in caption, an audio track and an SRT sidecar, scored 100/100, and was approved into the next 09:00 IST slot. No YouTube account was connected in this local run, so the upload step was not exercised. A deployed instance with Google OAuth performs the same queue-then-upload flow and records the YouTube video ID.

## Compatibility note

Requires Python 3.11+ and FFmpeg, eSpeak NG and DejaVu fonts. Scheduled publishing and the Telegram webhook need a public HTTPS origin.

## Categories

1. Image & Video (primary)
2. Social Media
3. Workflow Automation

## Tags (10)

youtube shorts, video generation, auto upload, ffmpeg, elevenlabs, telegram bot, youtube api, content automation, fastapi, faceless video

## Keywords (10)

youtube shorts generator, automatic video uploader, shorts automation, vertical video renderer, ai voiceover, text to video, youtube oauth upload, telegram approval bot, faceless channel, scheduled publishing

## Use cases (8)

- Generate and publish faceless YouTube Shorts on a schedule.
- Render 1080x1920 videos with voiceover and burned-in captions.
- Auto-upload finished videos to a YouTube channel with official OAuth.
- Review and approve renders from a Telegram bot before publishing.
- Run a local or self-hosted shorts pipeline with SQLite and FFmpeg.
- Batch-produce vertical videos from RSS-discovered topics.
- Add a quality gate that blocks low-scoring renders.
- Keep media in S3-compatible storage instead of an ephemeral disk.

## Permissions to tick

- Terminal / Shell: runs Python, pip, uvicorn, FFmpeg and eSpeak NG.
- Read Files: reads the app package, `.env`, frames and rendered media.
- Write Files: writes SQLite/Postgres data, frames, MP4, SRT and logs under `data/`.
- Network Access: calls the hosts below.
- Environment Variables: reads the documented variables in `.env.example` and the process environment.

Do not tick Browser: the skill does not automate a browser.

## Allowed hosts

```
api.hackerai.sh
api.openai.com
api.elevenlabs.io
api.telegram.org
accounts.google.com
oauth2.googleapis.com
www.googleapis.com
s3.amazonaws.com
www.nasa.gov
commons.wikimedia.org
blog.archive.org
www.theverge.com
hnrss.org
feeds.bbci.co.uk
feeds.npr.org
api.pexels.com
pixabay.com
```

User-supplied endpoints are not fixed hosts: an Ollama server URL, an OpenAI-compatible base URL and an S3-compatible endpoint (Cloudflare R2, Backblaze B2 or MinIO) are entered by the operator.

## Limitations

- Default YouTube quota allows about 6 uploads per day; the app marks extra slots as blocked.
- ElevenLabs is paid; without it the voice is eSpeak NG and sounds robotic.
- The free Render service sleeps and its disk is ephemeral; production needs a disk or object storage.
- No guarantee of YouTube monetization or policy approval.
- Rendering needs CPU; a 512 MB instance needs `FFMPEG_THREADS=1` and a render budget, or a separate worker.

## FAQ

**Does it generate READMEs or API documentation?**
No. It generates and uploads YouTube Shorts. The earlier listing text was wrong and has been replaced.

**Which services does it need?**
Google OAuth plus the YouTube Data API for uploads. ElevenLabs, Telegram, Hacker AI, OpenAI, Ollama and S3-compatible storage are optional.

**Does it use the official YouTube API?**
Yes. It uses OAuth 2.0 and the YouTube Data API v3 resumable upload endpoint. It does not scrape or download videos.

**Where are API keys stored?**
In a Fernet-encrypted database using `APP_SECRET_KEY`, or in environment variables. The dashboard shows only a last-four hint.

**Can I review videos before they publish?**
Yes. Use approval mode and approve or reject each render from the dashboard or the Telegram bot.

**Does it include hosting?**
No. Run it on your own machine, a VPS, Docker or Render.
