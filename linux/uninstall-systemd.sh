#!/usr/bin/env bash
# Remove the comfy-bridge systemd --user service (created by install-systemd.sh).
# Linux equivalent of windows/uninstall-task-scheduler.ps1.
# Does NOT touch lingering (other user services may rely on it) or .env.
set -euo pipefail

UNIT=comfy-bridge.service
DEST="$HOME/.config/systemd/user/$UNIT"

systemctl --user disable --now "$UNIT" 2>/dev/null && echo "[uninstall] disabled + stopped $UNIT" \
  || echo "[uninstall] $UNIT was not active"

if [ -L "$DEST" ] || [ -f "$DEST" ]; then
  rm -f "$DEST"
  echo "[uninstall] removed $DEST"
fi

systemctl --user daemon-reload
echo "[uninstall] done."
