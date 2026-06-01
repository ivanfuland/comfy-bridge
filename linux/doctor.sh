#!/usr/bin/env bash
# comfy-bridge doctor: one-shot health check of the whole Linux stack.
# Linux equivalent of windows/doctor.ps1. Prints [PASS]/[WARN]/[FAIL] per component.
# Exit 1 if any FAIL. Safe to run anytime.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT=comfy-bridge.service
PORT=8190
HEALTH="http://127.0.0.1:$PORT/comfy-bridge/gating"
fails=0

pass() { printf '[PASS] %s\n' "$1"; }
warn() { printf '[WARN] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1"; fails=$((fails+1)); }

# 1. venv + uvicorn
if [ -x "$REPO/.venv/bin/uvicorn" ]; then pass "venv + uvicorn present"; else fail "missing $REPO/.venv/bin/uvicorn (run linux/bootstrap.sh)"; fi

# 2. .env
if [ -f "$REPO/.env" ]; then
  pass ".env present"
  grep -qE '^GEMINI_BASE_URL=.+' "$REPO/.env" 2>/dev/null && pass "GEMINI_BASE_URL set" || warn "GEMINI_BASE_URL empty (gemini -> default Google direct)"
else
  fail ".env missing (cp .env.example .env and fill keys)"
fi

# 3. unit installed + enabled
if systemctl --user cat "$UNIT" >/dev/null 2>&1; then
  pass "unit installed"
  [ "$(systemctl --user is-enabled "$UNIT" 2>/dev/null)" = enabled ] && pass "enabled (starts on boot)" || warn "not enabled (run linux/install-systemd.sh)"
else
  fail "unit not installed (run linux/install-systemd.sh)"
fi

# 4. lingering
[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = yes ] && pass "lingering on (runs without login)" || warn "lingering off (sudo loginctl enable-linger $USER)"

# 5. service active
[ "$(systemctl --user is-active "$UNIT" 2>/dev/null)" = active ] && pass "service active" || fail "service not active (linux/start-bridge.sh)"

# 6. port listening
if ss -tlnp 2>/dev/null | grep -q "127.0.0.1:$PORT "; then pass "listening on :$PORT"; else fail "nothing on :$PORT"; fi

# 7. health endpoint
if curl -fsS -m 8 -o /dev/null "$HEALTH"; then pass "health endpoint 200 ($HEALTH)"; else fail "health endpoint not responding ($HEALTH)"; fi

# 8. ComfyUI wired to bridge
if systemctl --user cat comfyui >/dev/null 2>&1; then
  if systemctl --user cat comfyui 2>/dev/null | grep -q -- "--comfy-api-base=http://127.0.0.1:$PORT"; then
    pass "ComfyUI points at bridge (:$PORT)"
  else
    warn "comfyui.service not using --comfy-api-base=http://127.0.0.1:$PORT"
  fi
  [ "$(systemctl --user is-active comfyui 2>/dev/null)" = active ] && pass "ComfyUI active" || warn "ComfyUI not active"
else
  warn "comfyui.service not installed (skip)"
fi

echo
if [ "$fails" -eq 0 ]; then echo "doctor: all critical checks PASS"; exit 0; else echo "doctor: $fails FAIL(s)"; exit 1; fi
