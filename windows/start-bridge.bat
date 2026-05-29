@echo off
REM (Re)start the comfy-bridge background service and RELOAD .env. Same as the workspace-root
REM start-bridge.bat. Double-click after editing .env (gateway / key / allowlist).
REM
REM Does the CORRECT restart: stop the scheduled task -> kill whatever still holds :8190
REM (Stop-ScheduledTask does NOT kill the child python) -> start the task again, which loads
REM the current .env. Just re-running start-bridge.ps1 does NOT reload: its health guard sees
REM the running service and exits "already healthy". The bridge runs hidden (no window) --
REM view live traffic with watch-bridge-log.bat.
title (re)start comfy-bridge  -  reload .env
powershell -NoProfile -ExecutionPolicy Bypass -Command "Stop-ScheduledTask -TaskName comfy-bridge -EA SilentlyContinue; Get-NetTCPConnection -LocalPort 8190 -State Listen -EA SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -EA SilentlyContinue }; Start-Sleep 2; Start-ScheduledTask -TaskName comfy-bridge; Start-Sleep 3; try { Write-Host ('bridge restarted OK, gating=' + (Invoke-RestMethod http://127.0.0.1:8190/comfy-bridge/gating -TimeoutSec 6).gating_enabled) -ForegroundColor Green } catch { Write-Host 'bridge not up yet - check watch-bridge-log.bat' -ForegroundColor Yellow }"
pause
