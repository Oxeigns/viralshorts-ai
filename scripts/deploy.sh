#!/usr/bin/env bash
# Prepare VIRALSHORTS AI for GitHub + Render. Does not claim Render is live.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== validate files =="
for f in Dockerfile requirements.txt package.json .env.example app.json render.yaml README.md alembic.ini app/main.py; do
  test -f "$f" || { echo "missing $f"; exit 1; }
done

echo "== python compile =="
python3 -m compileall -q app tests

echo "== tests =="
if [[ -x .venv/bin/pytest ]]; then
  .venv/bin/python -m pytest -q --tb=short
elif command -v pytest >/dev/null 2>&1; then
  pytest -q --tb=short
else
  echo "pytest not installed; run: pip install -r requirements-dev.txt"
  exit 1
fi

echo "== git status =="
git status --short

echo
echo "Manual Render path:"
echo "1. Push this repo to GitHub (never commit .env)."
echo "2. Render Dashboard → New → Blueprint → select this repo (render.yaml)."
echo "3. Set ADMIN_PASSWORD, APP_BASE_URL, ELEVENLABS_API_KEY, HACKERAI_API_KEY,"
echo "   TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_IDS, GOOGLE_CLIENT_ID/SECRET."
echo "4. After the web service is green, open /health then /configuration."
echo "5. Connect YouTube, then POST /api/telegram/webhook/setup."
echo "Do not treat this script as a completed Render deploy."
