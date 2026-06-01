#!/usr/bin/env bash
# Live view of comfy-bridge traffic. The bridge runs as a systemd --user service; its
# uvicorn stdout + BRIDGE_LOG_IO upstream-call log (every `→` request / `←` response) go
# to the journal. This just follows it — Ctrl+C / closing does NOT affect the service.
# Linux counterpart of windows/watch-bridge-log.bat.
#
# Usage:  linux/watch-bridge-log.sh [N]   # N = lines of history to show first (default 50)
set -euo pipefail
exec journalctl --user -u comfy-bridge -f -n "${1:-50}"
