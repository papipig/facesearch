#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "[setup] Creating virtual environment..."
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt -q
fi

exec .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
