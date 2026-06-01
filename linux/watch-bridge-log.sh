#!/usr/bin/env bash
# Live view of comfy-bridge traffic. The bridge runs as a systemd --user service;
# its stdout/stderr (uvicorn + BRIDGE_LOG_IO upstream call log) goes to the journal.
# This just follows it — closing/Ctrl+C does NOT affect the running service.
# Linux equivalent of windows/watch-bridge-log.bat.
set -euo pipefail
exec journalctl --user -u comfy-bridge -f -n "${1:-50}"
