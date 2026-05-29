# comfy-bridge Windows launcher (PowerShell)
#
# Usage:
#   Right-click -> Run with PowerShell                       (foreground, for debugging)
#   powershell -ExecutionPolicy Bypass -File windows\start-bridge.ps1
#   powershell ... -File windows\start-bridge.ps1 -LogFile <path>   (Task Scheduler: log to file)
#
# -LogFile: when set (the scheduled-task path), uvicorn output is rotated + written to that
# file (Task Scheduler runs hidden, so there's no console otherwise). When unset (manual run)
# output stays on the console for live debugging.
#
# Assumes: layout follows README - venv at .venv\, .env present, deps installed.
# CWD is set to the bridge dir so Python's load_dotenv() picks up .env and the
# default asset-cache dir resolves to <bridge_dir>\asset-cache.
param([string]$LogFile)

$ErrorActionPreference = "Stop"
$BridgeDir = Split-Path -Parent $PSScriptRoot
Set-Location $BridgeDir

if (-not (Test-Path ".env")) {
  Write-Host "[comfy-bridge] .env not found at $BridgeDir\.env" -ForegroundColor Red
  Write-Host "  Copy .env.example to .env and fill in your provider keys (OPENAI_API_KEY etc.)"
  Read-Host "Press Enter to exit"
  exit 1
}

$Python = Join-Path $BridgeDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
  Write-Host "[comfy-bridge] venv not found at $BridgeDir\.venv" -ForegroundColor Red
  Write-Host "  Run setup:"
  Write-Host "    uv venv --python 3.12 .venv"
  Write-Host "    .venv\Scripts\python -m pip install -e ."
  Read-Host "Press Enter to exit"
  exit 1
}

# Read BRIDGE_HOST/PORT from .env so this launcher matches user's config.
# Python's load_dotenv() handles the rest; this is only for the CLI args to uvicorn.
$bridgeHost = "127.0.0.1"
$bridgePort = "8190"
Get-Content ".env" | ForEach-Object {
  if ($_ -match '^\s*BRIDGE_HOST\s*=\s*([^\s#]+)') { $bridgeHost = $Matches[1] }
  if ($_ -match '^\s*BRIDGE_PORT\s*=\s*([^\s#]+)') { $bridgePort = $Matches[1] }
}

Write-Host "[comfy-bridge] starting on http://${bridgeHost}:${bridgePort}" -ForegroundColor Green

# uvicorn logs to STDERR. Under $ErrorActionPreference='Stop', a native command's stderr
# is promoted to a terminating NativeCommandError -- which would abort us the instant
# uvicorn prints its first startup line, before it ever binds the port. Drop to Continue
# for the launch so stderr is just text.
$ErrorActionPreference = "Continue"

if ($LogFile) {
  # rotate at startup: keep the previous run as .1 (cheap 2-file rotation, no unbounded growth)
  $LogDir = Split-Path -Parent $LogFile
  if ($LogDir) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }
  if (Test-Path $LogFile) {
    $prev = "$LogFile.1"
    if (Test-Path $prev) { Remove-Item $prev -Force -ErrorAction SilentlyContinue }
    Move-Item $LogFile $prev -Force -ErrorAction SilentlyContinue
  }
  "[comfy-bridge] $(Get-Date -Format o) starting on http://${bridgeHost}:${bridgePort}" | Out-File -FilePath $LogFile -Encoding utf8
  # 2>&1 merges uvicorn's stderr logs into the pipeline; Out-File -Encoding utf8 keeps the
  # log readable (Win-PS 5.1 Tee-Object has no -Encoding and would write UTF-16). The hidden
  # scheduled task has no console anyway -- the file is the only sink that matters here.
  & $Python -m uvicorn app.main:app --host $bridgeHost --port $bridgePort 2>&1 | Out-File -FilePath $LogFile -Append -Encoding utf8
} else {
  & $Python -m uvicorn app.main:app --host $bridgeHost --port $bridgePort
}
