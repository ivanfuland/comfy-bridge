#!/usr/bin/env bash
# (Re)start the comfy-bridge service and RELOAD .env. Run after editing .env
# (gateway / key / allowlist). Linux equivalent of windows/start-bridge.bat.
#
# The bridge runs as a systemd --user service (see systemd/comfy-bridge.service),
# which reads .env via EnvironmentFile on every (re)start. `restart` is the correct
# way to pick up .env changes — do NOT just `kill` the process.
set -euo pipefail

UNIT=comfy-bridge

if ! systemctl --user cat "$UNIT" >/dev/null 2>&1; then
  echo "[start-bridge] $UNIT not installed. Run linux/install-systemd.sh first." >&2
  exit 1
fi

echo "[start-bridge] restarting $UNIT (reloads .env) ..."
systemctl --user restart "$UNIT"
sleep 1
systemctl --user --no-pager --lines=0 status "$UNIT" | head -5 || true
echo
echo "[start-bridge] tail live log:  linux/watch-bridge-log.sh"
