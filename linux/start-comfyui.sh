#!/usr/bin/env bash
# (Re)start ComfyUI together with the bridge. Linux equivalent of windows/start-comfyui.bat.
#
# On this workstation ComfyUI itself runs as a systemd --user service (comfyui.service,
# launched with --comfy-api-base=http://127.0.0.1:8190 so its api_nodes go through the
# bridge). This restarts the bridge first, then ComfyUI, so ComfyUI comes up against a
# fresh bridge. If comfyui.service is not installed, falls back to a foreground launch.
set -euo pipefail

systemctl --user restart comfy-bridge 2>/dev/null && echo "[start-comfyui] bridge restarted" \
  || echo "[start-comfyui] WARN: comfy-bridge service not found (run linux/install-systemd.sh)"

if systemctl --user cat comfyui >/dev/null 2>&1; then
  systemctl --user restart comfyui
  sleep 1
  systemctl --user --no-pager --lines=0 status comfyui | head -5 || true
  echo "[start-comfyui] ComfyUI restarted -> http://127.0.0.1:8188"
else
  COMFY="$HOME/projects/comfyui/ComfyUI"
  echo "[start-comfyui] comfyui.service not installed; launching foreground from $COMFY"
  cd "$COMFY"
  exec "$HOME/projects/comfyui/.venv/bin/python" main.py \
    --listen 127.0.0.1 --port 8188 --comfy-api-base=http://127.0.0.1:8190
fi
