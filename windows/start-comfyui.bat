@echo off
REM Launch ComfyUI WITH the bridge. Assumes the standard layout created by bootstrap.ps1:
REM   <workspace>\ComfyUI\        and  <workspace>\comfy-bridge\windows\ (this file)
REM so ComfyUI is at ..\..\ComfyUI relative to here. Paths are relative -> no hardcoded drive.
REM
REM Always carries --comfy-api-base (the ONLY thing keeping api_node requests off comfy.org
REM billing). Pre-flights the bridge so the gating prune doesn't fail-open on a startup race.
REM comfy-cli built ComfyUI its own venv (ComfyUI\.venv, torch lives there).

REM --- Pre-flight: wait for bridge (:8190) healthy; kick its scheduled task if down ---
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ok=$false; for($i=0;$i -lt 15;$i++){ try{ Invoke-RestMethod http://127.0.0.1:8190/comfy-bridge/gating -TimeoutSec 2 ^| Out-Null; $ok=$true; break }catch{}; if($i -eq 0){ try{ Start-ScheduledTask -TaskName comfy-bridge }catch{} }; Start-Sleep -Seconds 2 }; if($ok){ Write-Host '[preflight] bridge ready' -ForegroundColor Green }else{ Write-Host '[preflight] bridge NOT ready - menu gating may fail-open (credits still safe via --comfy-api-base)' -ForegroundColor Yellow }"

cd /d "%~dp0..\..\ComfyUI"
call .venv\Scripts\activate.bat
python main.py --listen 127.0.0.1 --port 8188 --comfy-api-base=http://127.0.0.1:8190
pause
