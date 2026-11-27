#!/bin/zsh
# Starts Tessera on http://127.0.0.1:5173 (first run sets up a Python venv).
set -e
cd "$(dirname "$0")"

if ! command -v ffmpeg >/dev/null; then
  echo "ffmpeg is required for videos. Install it with:  brew install ffmpeg"
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up Python environment…"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
fi
.venv/bin/pip install -q -r requirements.txt

exec .venv/bin/python server.py
