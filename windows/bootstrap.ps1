# comfy-bridge one-shot Windows installer (idempotent; safe to re-run).
#
#   git clone https://github.com/ivanfuland/comfy-bridge.git
#   powershell -ExecutionPolicy Bypass -File comfy-bridge\windows\bootstrap.ps1
#
# Orchestrates the whole workspace from (almost) zero:
#   prereq check -> install ComfyUI -> wire start-comfyui.bat -> bridge venv + tests
#   -> .env (prompts for gateway+key if absent) -> symlink custom_node
#   -> register scheduled task + watchdog -> start bridge -> doctor.
# Each step skips work already done, so re-running just heals what's missing.
#
# Workspace layout (this script sits at <workspace>\comfy-bridge\windows\):
#   <workspace>\.venv\           comfy-cli host venv
#   <workspace>\ComfyUI\         ComfyUI + its OWN .venv (torch lives there)
#   <workspace>\comfy-bridge\    this repo + its OWN .venv
#   <workspace>\start-comfyui.bat
param(
  [string]$Workspace,
  [string]$Gateway = "",  # LLM gateway base URL (origin root, no /v1); prompted if empty
  [string]$PyVer = "3.12"
)

# Continue (NOT Stop): this script drives native tools (uv/comfy/pip/pytest) that write to
# stderr routinely; under Win-PS 5.1, EAP=Stop promotes any native stderr to a terminating
# NativeCommandError and would abort mid-install. We gate each step with explicit
# Test-Path / $LASTEXITCODE / Die() instead.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$BridgeDir = Split-Path -Parent $PSScriptRoot
if (-not $Workspace) { $Workspace = Split-Path -Parent $BridgeDir }
$ComfyDir   = Join-Path $Workspace "ComfyUI"
$TopVenvPy  = Join-Path $Workspace ".venv\Scripts\python.exe"
$TopComfy   = Join-Path $Workspace ".venv\Scripts\comfy.exe"
$ComfyPy    = Join-Path $ComfyDir ".venv\Scripts\python.exe"
$BridgePy   = Join-Path $BridgeDir ".venv\Scripts\python.exe"
$Bat        = Join-Path $Workspace "start-comfyui.bat"

function Section($n) { Write-Host "`n=== $n ===" -ForegroundColor Cyan }
function Info($m)    { Write-Host "  $m" }
function Good($m)    { Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn($m)    { Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Die($m)     { Write-Host "  [stop] $m" -ForegroundColor Red; exit 1 }

# --- 0. prerequisites ---------------------------------------------------------
Section "0/8 prerequisites"
if (-not (Get-Command uv  -ErrorAction SilentlyContinue)) { Die "uv missing - install from https://docs.astral.sh/uv/ then re-run" }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Die "git missing - install Git for Windows then re-run" }
Good "uv + git present"
# Python 3.12: uv can fetch it if absent
uv python find $PyVer *>$null
if ($LASTEXITCODE -ne 0) { Info "fetching Python $PyVer via uv..."; uv python install $PyVer | Out-Null }
Good "Python $PyVer available"
# Developer Mode (needed for symlink; copy fallback otherwise)
$devmode = $false
try {
  $k = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock" -ErrorAction Stop
  if ($k.AllowDevelopmentWithoutDevLicense -eq 1) { $devmode = $true }
} catch {}
if ($devmode) { Good "Developer Mode on (symlink ok)" } else { Warn "Developer Mode off - will COPY custom_node instead of symlink (manual re-copy on upgrade)" }
# GPU (informational)
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { Good "nvidia-smi present" } else { Warn "no nvidia-smi - ComfyUI engine needs an NVIDIA GPU" }

# --- 1. ComfyUI (comfy-cli + uv venv) ----------------------------------------
Section "1/8 ComfyUI"
$torchOk = $false
if (Test-Path $ComfyPy) { & $ComfyPy -c "import torch" 2>$null; if ($LASTEXITCODE -eq 0) { $torchOk = $true } }
if ($torchOk) {
  Good "ComfyUI already installed (torch in ComfyUI\.venv) - skipping"
} else {
  if (-not (Test-Path $TopVenvPy)) {
    Info "creating host venv + comfy-cli (uv venv has no pip by default -> add it first)"
    uv venv --python $PyVer (Join-Path $Workspace ".venv") | Out-Null
    uv pip install --python $TopVenvPy pip | Out-Null
    uv pip install --python $TopVenvPy comfy-cli | Out-Null
  }
  Info "installing ComfyUI (downloads ~2.6GB torch+cu128, several minutes)..."
  & $TopComfy --skip-prompt --workspace $ComfyDir install --nvidia --cuda-version 12.8 --version latest --fast-deps
  if (-not (Test-Path $ComfyPy)) { Die "ComfyUI install did not produce $ComfyPy" }
  Good "ComfyUI installed"
}

# --- 2. start-comfyui.bat -----------------------------------------------------
Section "2/8 start-comfyui.bat"
if (Test-Path $Bat) {
  Good "start-comfyui.bat exists - leaving as-is"
} else {
  $batBody = @"
@echo off
REM Only sanctioned way to launch ComfyUI: always carries --comfy-api-base (the ONLY thing
REM keeping api_node requests off comfy.org billing) and pre-flights the bridge.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "`$ok=`$false; for(`$i=0;`$i -lt 15;`$i++){ try{ Invoke-RestMethod http://127.0.0.1:8190/comfy-bridge/gating -TimeoutSec 2 ^| Out-Null; `$ok=`$true; break }catch{}; if(`$i -eq 0){ try{ Start-ScheduledTask -TaskName comfy-bridge }catch{} }; Start-Sleep -Seconds 2 }; if(`$ok){ Write-Host '[preflight] bridge ready' -ForegroundColor Green }else{ Write-Host '[preflight] bridge NOT ready - gating may fail-open (credits still safe via --comfy-api-base)' -ForegroundColor Yellow }"
cd /d $ComfyDir
call .venv\Scripts\activate.bat
python main.py --listen 127.0.0.1 --port 8188 --comfy-api-base=http://127.0.0.1:8190
pause
"@
  Set-Content -Path $Bat -Value $batBody -Encoding ASCII
  Good "wrote $Bat"
}

# --- 3. bridge venv + deps + tests -------------------------------------------
Section "3/8 bridge venv"
$depsOk = $false
if (Test-Path $BridgePy) { & $BridgePy -c "import fastapi,pytest" 2>$null; if ($LASTEXITCODE -eq 0) { $depsOk = $true } }
if ($depsOk) {
  Good "bridge venv + deps already present - skipping"
} else {
  if (-not (Test-Path $BridgePy)) {
    uv venv --python $PyVer (Join-Path $BridgeDir ".venv") | Out-Null
    uv pip install --python $BridgePy pip | Out-Null   # same no-pip-by-default gotcha
  }
  Push-Location $BridgeDir
  & $BridgePy -m pip install -e ".[dev]"   # [dev] = pytest etc.
  Pop-Location
  Good "bridge deps installed"
}
Info "running tests..."
Push-Location $BridgeDir
& $BridgePy -m pytest tests -q
$testRc = $LASTEXITCODE
Pop-Location
if ($testRc -eq 0) { Good "pytest passed" } else { Warn "pytest reported failures (rc=$testRc) - check output above" }

# --- 4. .env ------------------------------------------------------------------
Section "4/8 .env"
$envf = Join-Path $BridgeDir ".env"
if (Test-Path $envf) {
  Good ".env exists - preserving (not overwriting secrets)"
} else {
  Info "no .env - let's create one."
  $g = Read-Host "  LLM gateway base URL (origin root, NO /v1; e.g. https://your-gateway.example.com)"
  if (-not $g) { $g = $Gateway }
  if (-not $g) { Die ".env not created: gateway base URL is required" }
  $key = Read-Host "  API key (one key, used for all 4 vendors)"
  if (-not $key) { Die ".env not created: API key is required" }
  $allow = "ClaudeNode,OpenAIChatNode,OpenAIGPTImage1,OpenAIGPTImageNodeV2,GeminiNode,GeminiImageNode,GeminiImage2Node,GeminiNanoBanana2,GeminiNanoBanana2V2,TripoImageToModelNode,TripoMultiviewToModelNode,TripoTextToModelNode,TripoTextureNode,TripoRefineNode,TripoRigNode,TripoRetargetNode,TripoConversionNode"
  $lines = @(
    "BRIDGE_HOST=127.0.0.1"
    "BRIDGE_PORT=8190"
    "BRIDGE_GATING=on"
    "BRIDGE_CORS_ORIGINS=http://127.0.0.1:8188,http://localhost:8188"
    "BRIDGE_ALLOWED_NODE_CLASSES=$allow"
    "BRIDGE_HIDDEN_NODE_CLASSES=OpenAIDalle2,OpenAIDalle3"
    "OPENAI_BASE_URL=$g"
    "OPENAI_API_KEY=$key"
    "ANTHROPIC_BASE_URL=$g"
    "ANTHROPIC_API_KEY=$key"
    "ANTHROPIC_VERSION=2023-06-01"
    "GEMINI_BASE_URL=$g"
    "GEMINI_API_KEY=$key"
    "TRIPO_BASE_URL=$g"
    "TRIPO_API_KEY=$key"
  )
  Set-Content -Path $envf -Value $lines -Encoding ASCII
  Good "wrote .env (gateway=$g)"
}
# lock down permissions regardless
try { icacls $envf /inheritance:r /grant:r "$($env:USERNAME):(R,W)" *>$null; Good ".env permissions locked to current user" } catch { Warn "icacls failed (non-fatal)" }

# --- 5. symlink custom_node ---------------------------------------------------
Section "5/8 custom_node"
$dst = Join-Path $ComfyDir "custom_nodes\comfy-bridge-gating"
$src = Join-Path $BridgeDir "custom_nodes\comfy-bridge-gating"
if (Test-Path (Join-Path $dst "__init__.py")) {
  Good "custom_node already wired - skipping"
} else {
  $linked = $false
  if ($devmode) {
    try { New-Item -ItemType SymbolicLink -Path $dst -Target $src -ErrorAction Stop | Out-Null; $linked = $true; Good "symlinked custom_node" } catch { Warn "symlink failed: $($_.Exception.Message)" }
  }
  if (-not $linked) { Copy-Item -Recurse -Force $src $dst; Warn "COPIED custom_node (re-copy after bridge upgrades)" }
}

# --- 6. scheduled task + watchdog --------------------------------------------
Section "6/8 scheduled task"
if (Get-ScheduledTask -TaskName comfy-bridge -ErrorAction SilentlyContinue) {
  Good "task 'comfy-bridge' already registered - skipping (re-run install-task-scheduler.ps1 to refresh)"
} else {
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "install-task-scheduler.ps1")
}

# --- 7. start bridge ----------------------------------------------------------
Section "7/8 start bridge"
$up = $false
try { Invoke-RestMethod http://127.0.0.1:8190/comfy-bridge/gating -TimeoutSec 3 | Out-Null; $up = $true } catch {}
if ($up) { Good "bridge already running" }
else {
  Start-ScheduledTask -TaskName comfy-bridge
  for ($i = 0; $i -lt 15; $i++) { try { Invoke-RestMethod http://127.0.0.1:8190/comfy-bridge/gating -TimeoutSec 2 | Out-Null; $up = $true; break } catch { Start-Sleep -Seconds 1 } }
  if ($up) { Good "bridge started" } else { Warn "bridge did not come up in 15s - check logs\bridge.log" }
}

# --- 8. doctor ----------------------------------------------------------------
Section "8/8 doctor"
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "doctor.ps1") -Workspace $Workspace

Write-Host "`nBootstrap done. Next:" -ForegroundColor Cyan
Write-Host "  - Launch ComfyUI:  double-click $Bat   (then open http://127.0.0.1:8188)"
Write-Host "  - If you just enabled the custom_node, restart ComfyUI once so the menu prune applies."
Write-Host "  - Re-check anytime: powershell -File comfy-bridge\windows\doctor.ps1"
