#!/usr/bin/env bash
# Install comfy-bridge as a systemd --user service that starts on boot/login.
# Linux equivalent of windows/install-task-scheduler.ps1.
#
# Symlinks the repo's unit into ~/.config/systemd/user/ (so edits to
# systemd/comfy-bridge.service take effect after `daemon-reload`), enables it,
# and turns on lingering so it runs without an active login session.
#
# Usage (NOT as root):  linux/install-systemd.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT=comfy-bridge.service
SRC="$REPO/systemd/$UNIT"
DEST_DIR="$HOME/.config/systemd/user"
DEST="$DEST_DIR/$UNIT"

[ -f "$SRC" ] || { echo "[install] missing unit: $SRC" >&2; exit 1; }

mkdir -p "$DEST_DIR"
ln -sfn "$SRC" "$DEST"
echo "[install] linked $DEST -> $SRC"

systemctl --user daemon-reload
systemctl --user enable --now "$UNIT"
echo "[install] enabled + started $UNIT"

# Lingering: run the user service without an active session (survives logout / on boot).
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo no)" = "yes" ]; then
  echo "[install] lingering already enabled"
else
  echo "[install] enabling lingering (may prompt for sudo) ..."
  sudo loginctl enable-linger "$USER" && echo "[install] lingering enabled" \
    || echo "[install] WARN: could not enable lingering — run: sudo loginctl enable-linger $USER"
fi

echo "[install] done. Health check: linux/doctor.sh"
