#!/usr/bin/env bash
# One-time setup: creates the venv and installs all dependencies.
# Run this on first deploy and after any requirements.txt change.
# Does NOT start the server — use the systemd service or start.sh for that.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "[setup] Creating virtual environment..."
  python3 -m venv .venv
else
  echo "[setup] Virtual environment already exists, updating packages..."
fi

echo "[setup] Installing dependencies..."
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

echo "[setup] Done. .venv is ready."
echo "        To start (dev):        ./start.sh"
echo "        To start (production): systemctl start xddsearch"
