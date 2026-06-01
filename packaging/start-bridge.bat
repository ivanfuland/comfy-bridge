@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".env" (
  echo [comfy-bridge] .env not found.
  echo   Copy .env.example to .env and put your gateway key in it first.
  pause
  exit /b 1
)

rem require at least one active (non-comment) *_API_KEY with a non-blank value
powershell -NoProfile -Command "if (-not (Get-Content '.env' | Where-Object { $_ -notmatch '^\s*#' -and $_ -match '^\s*[A-Z_]+_API_KEY\s*=\s*\S' })) { exit 1 }"
if errorlevel 1 (
  echo [comfy-bridge] No API key filled in .env yet.
  echo   Open .env and paste your gateway key into a *_API_KEY line, then retry.
  pause
  exit /b 1
)

if not exist "bridge\bridge.exe" (
  echo [comfy-bridge] bridge\bridge.exe is missing - the kit may be incompletely unzipped.
  pause
  exit /b 1
)

echo [comfy-bridge] starting on http://127.0.0.1:8190  (closing this window stops the service)
"%~dp0bridge\bridge.exe"
