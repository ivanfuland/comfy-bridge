@echo off
REM ============================================================
REM  restart-all.bat  --  ONE-CLICK full (re)start of the stack
REM  (bridge :8190  +  ComfyUI :8188), reloading comfy-bridge\.env.
REM
REM  Works as both START (nothing running yet) and RESTART
REM  (something already running). Double-click after editing .env.
REM
REM  Safe order, every step kills stale procs before relaunching:
REM    1) Stop bridge task + kill whatever still holds :8190
REM       (Stop-ScheduledTask does NOT kill the child python),
REM       restart the task -> reloads .env, wait until healthy.
REM    2) Kill old ComfyUI holding :8188.
REM    3) Launch a fresh ComfyUI in THIS window (logs visible).
REM  Then hard-refresh the browser:  Ctrl+Shift+R
REM ============================================================
title restart-all  -  bridge + ComfyUI  (reload .env)

echo.
echo [1/3] Restarting bridge (:8190) + reloading .env ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Stop-ScheduledTask -TaskName comfy-bridge -EA SilentlyContinue; Get-NetTCPConnection -LocalPort 8190 -State Listen -EA SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -EA SilentlyContinue }; Start-Sleep 2; Start-ScheduledTask -TaskName comfy-bridge; $ok=$false; for($i=0;$i -lt 15;$i++){ try{ $g=(Invoke-RestMethod http://127.0.0.1:8190/comfy-bridge/gating -TimeoutSec 2).gating_enabled; $ok=$true; break }catch{}; Start-Sleep 2 }; if($ok){ Write-Host ('      bridge OK, gating=' + $g) -ForegroundColor Green } else { Write-Host '      bridge NOT up yet - check watch-bridge-log.bat' -ForegroundColor Yellow }"

echo.
echo [2/3] Stopping old ComfyUI (:8188) if running ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=Get-NetTCPConnection -LocalPort 8188 -State Listen -EA SilentlyContinue; if($p){ $p | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -EA SilentlyContinue }; Start-Sleep 1; Write-Host '      old ComfyUI killed' -ForegroundColor Green } else { Write-Host '      nothing on :8188 (fresh start)' -ForegroundColor Green }"

echo.
echo [3/3] Starting ComfyUI (:8188) in this window ...
echo       After it loads, HARD-REFRESH the browser:  Ctrl+Shift+R
echo.
cd /d "%~dp0..\..\ComfyUI"
call .venv\Scripts\activate.bat
python main.py --listen 127.0.0.1 --port 8188 --comfy-api-base=http://127.0.0.1:8190
pause
