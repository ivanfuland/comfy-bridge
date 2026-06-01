#!/usr/bin/env bash
# restart-all.sh — ONE-COMMAND full (re)start of the stack (bridge :8190 + ComfyUI :8188),
# reloading comfy-bridge/.env. Linux counterpart of windows/restart-all.bat.
#
# Works as both START (nothing running yet) and RESTART (already running): `systemctl
# --user restart` starts an inactive unit too. Run after editing .env.
#
# Linux has none of the Windows pain: `systemctl --user restart` uses cgroups — it cleanly
# kills the whole process group and re-reads EnvironmentFile (.env). No "kill the grandchild
# still holding :8190" dance is needed; the restart reloads .env natively.
#
# Order matters: bridge FIRST (so its gating allowlist is fresh), THEN ComfyUI — the
# comfy-bridge-gating custom_node reads the bridge's gating endpoint and prunes nodes at
# load time, so ComfyUI must restart AFTER the bridge to pick up new
# BRIDGE_HIDDEN_NODE_CLASSES / BRIDGE_ALLOWED_* values.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRIDGE=comfy-bridge
COMFYUI=comfyui
HEALTH="http://127.0.0.1:8190/comfy-bridge/gating"

grn() { printf '\033[32m%s\033[0m\n' "$*"; }
ylw() { printf '\033[33m%s\033[0m\n' "$*"; }

# Pick up any edits to the bundled .service files (e.g. after `git pull`). Cheap + idempotent;
# a plain .env edit doesn't need it, but running it unconditionally keeps the one-command
# promise honest when the unit file itself changed.
systemctl --user daemon-reload || { ylw "daemon-reload failed — is the systemd --user instance running?"; exit 1; }

# Link + enable a unit from the repo if it is not installed yet (idempotent), so this script
# also works on a fresh clone. Returns non-zero only on a real install failure.
ensure_unit() {
  local name="$1"
  systemctl --user cat "$name" >/dev/null 2>&1 && return 0
  local src="$REPO/systemd/$name.service"
  [ -f "$src" ] || { ylw "      $name.service not installed and not in repo ($src)"; return 1; }
  mkdir -p "$HOME/.config/systemd/user" || return 1
  ln -sfn "$src" "$HOME/.config/systemd/user/$name.service" || return 1
  systemctl --user daemon-reload || return 1
  systemctl --user enable "$name" >/dev/null 2>&1 || true   # enable is best-effort; restart still starts it
  ylw "      ($name.service was not installed — linked from repo systemd/ + enabled)"
  return 0
}

# ---- 1) bridge ----
echo
echo "[1/3] Restarting bridge (:8190) + reloading .env ..."
ensure_unit "$BRIDGE" || { ylw "      Cannot install/enable $BRIDGE.service. Fix it, then re-run."; exit 1; }
systemctl --user restart "$BRIDGE" || { ylw "      restart $BRIDGE FAILED — check: journalctl --user -u $BRIDGE -n 50"; exit 1; }

# ---- 2) wait for health ----
echo "[2/3] Waiting for bridge health ($HEALTH) ..."
deadline=$((SECONDS + 30)); healthy=0
while [ "$SECONDS" -lt "$deadline" ]; do
  # Single source of truth: a 200 whose JSON actually has gating_enabled. No fragile grep.
  ge="$(curl -fsS -m 2 "$HEALTH" 2>/dev/null \
        | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["gating_enabled"])' 2>/dev/null)" || ge=""
  if [ -n "$ge" ]; then
    grn "      bridge OK, gating_enabled=$ge"
    healthy=1; break
  fi
  sleep 2
done
if [ "$healthy" -ne 1 ]; then
  ylw "      bridge NOT healthy after 30s — check: journalctl --user -u $BRIDGE -n 50"
  ylw "      Aborting: NOT restarting ComfyUI against an unhealthy bridge (would fail open to comfy.org billing)."
  exit 1
fi

# ---- 3) ComfyUI ----
echo "[3/3] Restarting ComfyUI (:8188, --comfy-api-base=:8190) ..."
if ensure_unit "$COMFYUI"; then
  # Guard against a pre-existing comfyui unit that lacks the billing-bypass flag.
  if ! systemctl --user cat "$COMFYUI" 2>/dev/null | grep -q -- '--comfy-api-base=http://127.0.0.1:8190'; then
    ylw "      WARNING: installed $COMFYUI.service has no --comfy-api-base=http://127.0.0.1:8190 flag."
    ylw "               ComfyUI api_node requests will hit comfy.org billing, NOT the bridge."
    ylw "               Fix ExecStart (see systemd/comfyui.service) then re-run."
  fi
  systemctl --user restart "$COMFYUI" || { ylw "      restart $COMFYUI FAILED — check: journalctl --user -u $COMFYUI -n 50"; exit 1; }
  grn "      ComfyUI restarted -> http://127.0.0.1:8188"
else
  ylw "      ComfyUI is not a systemd --user service here. Restart it yourself and KEEP the flag:"
  ylw "        --comfy-api-base=http://127.0.0.1:8190"
  ylw "      (or install the bundled unit: cp systemd/comfyui.service ~/.config/systemd/user/ && systemctl --user enable --now comfyui)"
fi

echo
grn "Done. Hard-refresh the browser:  Ctrl+Shift+R"
