#!/usr/bin/env bash
# comfy-bridge watchdog: verify the bridge is actually serving, restart it if not.
# Linux equivalent of windows/healthcheck-bridge.ps1.
#
# Covers death modes that systemd Restart=on-failure misses: a hung process, or a
# process that is alive and holding :8190 but not answering (broken config).
# Wire it to a 5-min timer:
#   linux/healthcheck-bridge.sh   (one-shot; safe to run anytime)
# Cron example (crontab -e):
#   */5 * * * * /home/ivan/projects/comfyui/comfy-bridge/linux/healthcheck-bridge.sh >/dev/null 2>&1
set -euo pipefail

URL="http://127.0.0.1:8190/comfy-bridge/gating"
UNIT=comfy-bridge

if curl -fsS -m 8 -o /dev/null "$URL"; then
  echo "[healthcheck] OK ($URL responding)"
  exit 0
fi

echo "[healthcheck] DOWN — $URL not responding. Restarting $UNIT ..." >&2
systemctl --user restart "$UNIT"
sleep 3
if curl -fsS -m 8 -o /dev/null "$URL"; then
  echo "[healthcheck] recovered after restart"
  exit 0
fi
echo "[healthcheck] STILL DOWN after restart — check: journalctl --user -u $UNIT -n 50" >&2
exit 1
