#!/usr/bin/env bash
# comfy-bridge one-shot Linux installer (idempotent; safe to re-run).
# Linux equivalent of windows/bootstrap.ps1.
#
#   git clone https://github.com/ivanfuland/comfy-bridge.git ~/projects/comfyui/comfy-bridge
#   ~/projects/comfyui/comfy-bridge/linux/bootstrap.sh
#
# Creates the venv, installs deps, seeds .env, installs+starts the systemd --user
# service, and runs doctor. Does NOT overwrite an existing .env.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# 1. Python
command -v python3 >/dev/null || { echo "[bootstrap] python3 not found" >&2; exit 1; }
PYVER=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
echo "[bootstrap] python $PYVER"

# 2. venv + deps
if [ ! -x .venv/bin/python ]; then
  echo "[bootstrap] creating .venv ..."
  python3 -m venv .venv
fi
echo "[bootstrap] installing deps (editable + dev) ..."
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[dev]"

# 3. .env
if [ ! -f .env ]; then
  cp .env.example .env
  echo "[bootstrap] created .env from .env.example — EDIT IT (gateway URLs + keys) then re-run linux/start-bridge.sh"
else
  echo "[bootstrap] .env already exists — left untouched"
fi

# 4. systemd service
"$REPO/linux/install-systemd.sh"

# 5. health
echo
"$REPO/linux/doctor.sh" || true
echo
echo "[bootstrap] done. Edit .env if you haven't, then: linux/start-bridge.sh"
