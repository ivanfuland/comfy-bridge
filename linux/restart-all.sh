#!/usr/bin/env bash
# restart-all.sh — ONE-COMMAND full (re)start of the stack (bridge :8190 + ComfyUI :8188),
# reloading comfy-bridge/.env. Linux counterpart of windows/restart-all.bat.
#
# Works as both START (nothing running yet) and RESTART (already running): `systemctl
# --user restart` starts an inactive unit too. Run after editing .env.
#
# Linux has none of the Windows pain: `systemctl --user restart` uses cgroups — it cleanly
# kills the whole process group and re-reads EnvironmentFile (.env). No "kill the grandchild
# still holding :8190" dance is needed; the restart reloads .env natively. (Only edits to a
# .service file itself need `systemctl --user daemon-reload` first — a plain .env edit does not.)
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

# Link + enable a unit from the repo if it is not installed yet (idempotent), so this
# script also works on a fresh clone — not just as a restart.
ensure_unit() {
  local name="$1"
  systemctl --user cat "$name" >/dev/null 2>&1 && return 0
  local src="$REPO/systemd/$name.service"
  [ -f "$src" ] || { ylw "      $name.service not installed and not in repo ($src)"; return 1; }
  mkdir -p "$HOME/.config/systemd/user"
  ln -sfn "$src" "$HOME/.config/systemd/user/$name.service"
  systemctl --user daemon-reload
  systemctl --user enable "$name" >/dev/null 2>&1 || true
  ylw "      ($name.service was not installed — linked from repo systemd/ + enabled)"
}

# ---- 1) bridge ----
echo
echo "[1/3] Restarting bridge (:8190) + reloading .env ..."
if ! ensure_unit "$BRIDGE"; then
  ylw "      Cannot manage $BRIDGE.service. Install it, then re-run."
  exit 1
fi
systemctl --user restart "$BRIDGE"

# ---- 2) wait for health ----
echo "[2/3] Waiting for bridge health ($HEALTH) ..."
deadline=$((SECONDS + 30)); healthy=0
while [ "$SECONDS" -lt "$deadline" ]; do
  resp="$(curl -fsS -m 2 "$HEALTH" 2>/dev/null || true)"
  if printf '%s' "$resp" | grep -q '"gating_enabled"'; then
    ge="$(printf '%s' "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("gating_enabled"))' 2>/dev/null || echo '?')"
    grn "      bridge OK, gating_enabled=$ge"
    healthy=1; break
  fi
  sleep 2
done
if [ "$healthy" -ne 1 ]; then
  ylw "      bridge NOT healthy after 30s — check: journalctl --user -u $BRIDGE -n 50"
  exit 1
fi

# ---- 3) ComfyUI ----
echo "[3/3] Restarting ComfyUI (:8188, --comfy-api-base=:8190) ..."
if ensure_unit "$COMFYUI"; then
  systemctl --user restart "$COMFYUI"
  grn "      ComfyUI restarted -> http://127.0.0.1:8188"
else
  ylw "      ComfyUI is not a systemd --user service here. Restart it yourself and KEEP the flag:"
  ylw "        --comfy-api-base=http://127.0.0.1:8190"
  ylw "      (or install the bundled unit: cp systemd/comfyui.service ~/.config/systemd/user/ && systemctl --user enable --now comfyui)"
fi

echo
grn "Done. Hard-refresh the browser:  Ctrl+Shift+R"
