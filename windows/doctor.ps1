# comfy-bridge doctor: one-shot health check of the whole Windows stack.
#   powershell -ExecutionPolicy Bypass -File windows\doctor.ps1
# Prints [PASS]/[WARN]/[FAIL] per component. Exit 1 if any FAIL. Safe to run anytime.
param([string]$Workspace)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$BridgeDir = Split-Path -Parent $PSScriptRoot
if (-not $Workspace) { $Workspace = Split-Path -Parent $BridgeDir }
$ComfyDir = Join-Path $Workspace "ComfyUI"

$script:fail = 0
$script:warn = 0
function Ok($m)   { Write-Host "[PASS] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[WARN] $m" -ForegroundColor Yellow; $script:warn++ }
function Bad($m)  { Write-Host "[FAIL] $m" -ForegroundColor Red;    $script:fail++ }

Write-Host "comfy-bridge doctor  (workspace: $Workspace)" -ForegroundColor Cyan
Write-Host ("-" * 60)

# --- prerequisites ---
if (Get-Command uv  -ErrorAction SilentlyContinue) { Ok "uv present" }  else { Bad "uv missing (https://docs.astral.sh/uv/)" }
if (Get-Command git -ErrorAction SilentlyContinue) { Ok "git present" } else { Bad "git missing" }

# --- ComfyUI venv + torch/CUDA ---
$cpy = Join-Path $ComfyDir ".venv\Scripts\python.exe"
if (Test-Path $cpy) {
  $o = & $cpy -c "import torch,sys; sys.stdout.write(torch.__version__+'|'+str(torch.cuda.is_available()))" 2>$null
  if ($o -like "*|True")  { Ok "ComfyUI venv torch+CUDA ok ($o)" }
  elseif ($o -like "*|*") { Warn "ComfyUI torch present but CUDA unavailable ($o)" }
  else                    { Bad "ComfyUI venv has no torch (re-run install)" }
} else { Bad "ComfyUI venv missing: $cpy" }

# --- bridge venv deps ---
$bpy = Join-Path $BridgeDir ".venv\Scripts\python.exe"
if (Test-Path $bpy) {
  & $bpy -c "import fastapi,uvicorn,httpx,pydantic,dotenv" 2>$null
  if ($LASTEXITCODE -eq 0) { Ok "bridge venv deps ok" } else { Bad "bridge venv missing deps (pip install -e .)" }
} else { Bad "bridge venv missing: $bpy" }

# --- .env ---
$envf = Join-Path $BridgeDir ".env"
if (Test-Path $envf) {
  if (Select-String -Path $envf -Pattern '^\s*OPENAI_API_KEY\s*=\s*\S' -Quiet) { Ok ".env present, OPENAI_API_KEY set" }
  else { Warn ".env present but OPENAI_API_KEY empty" }
} else { Bad ".env missing (copy .env.example, fill keys)" }

# --- custom_node wired into ComfyUI ---
if (Test-Path (Join-Path $ComfyDir "custom_nodes\comfy-bridge-gating\__init__.py")) { Ok "custom_node present in ComfyUI" }
else { Bad "custom_node missing (symlink/copy comfy-bridge-gating)" }

# --- ComfyUI launcher ---
if (Test-Path (Join-Path $Workspace "start-comfyui.bat")) { Ok "start-comfyui.bat present" }
else { Warn "start-comfyui.bat missing" }

# --- scheduled tasks ---
$t = Get-ScheduledTask -TaskName comfy-bridge -ErrorAction SilentlyContinue
if ($t) {
  $lim = $t.Settings.ExecutionTimeLimit
  if ($lim -eq "PT0S" -or [string]::IsNullOrEmpty($lim)) { Ok "bridge task registered, no 72h kill limit" }
  else { Warn "bridge task ExecutionTimeLimit=$lim (expected PT0S; will be force-killed)" }
} else { Bad "scheduled task 'comfy-bridge' not registered (windows\install-task-scheduler.ps1)" }
if (Get-ScheduledTask -TaskName comfy-bridge-watchdog -ErrorAction SilentlyContinue) { Ok "watchdog task registered" }
else { Warn "watchdog task not registered (re-run install-task-scheduler.ps1)" }

# --- bridge live (:8190) ---
$bridgeUp = $false
try {
  $g = Invoke-RestMethod http://127.0.0.1:8190/comfy-bridge/gating -TimeoutSec 5
  $bridgeUp = $true
  Ok ("bridge :8190 up (gating_enabled={0}, allowed={1}, hidden={2})" -f $g.gating_enabled, $g.allowed_node_classes.Count, $g.hidden_node_classes.Count)
} catch { Bad "bridge :8190 not responding (Start-ScheduledTask -TaskName comfy-bridge)" }

# A uv-created venv on Windows uses a trampoline python.exe that spawns the base python as
# a child, so ONE healthy bridge == TWO python processes. That is normal, not a duplicate.
# >2 means multiple bridge instances are fighting for :8190 (someone started it manually
# while the scheduled task is also running) -> the loser crash-loops via RestartCount.
if ($bridgeUp) {
  $nproc = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*uvicorn*app.main*" }).Count
  if ($nproc -le 2) { Ok "bridge process count = $nproc (1-2 is normal: uv venv trampoline + child)" }
  else { Warn "bridge process count = $nproc (>2 => duplicate instances fighting :8190; stop manual starts, keep only the scheduled task)" }
}

# --- ComfyUI live (:8188) + prune applied ---
try {
  $oi = Invoke-RestMethod http://127.0.0.1:8188/object_info -TimeoutSec 10
  $names = $oi.PSObject.Properties.Name
  if ($names -contains "ClaudeNode") { Ok "ComfyUI :8188 up (ClaudeNode present)" } else { Warn "ComfyUI up but ClaudeNode missing" }
  if ($names -contains "KlingTextToVideoNode") { Bad "gating prune NOT applied (KlingTextToVideoNode still listed) - restart ComfyUI after bridge is up" }
  else { Ok "gating prune applied (disallowed-vendor nodes gone)" }
} catch { Warn "ComfyUI :8188 not running - start via start-comfyui.bat (skipped menu checks)" }

# --- summary ---
Write-Host ("-" * 60)
if ($script:fail -gt 0) { Write-Host ("DOCTOR: {0} FAIL, {1} WARN" -f $script:fail, $script:warn) -ForegroundColor Red; exit 1 }
elseif ($script:warn -gt 0) { Write-Host ("DOCTOR: critical OK, {0} WARN" -f $script:warn) -ForegroundColor Yellow; exit 0 }
else { Write-Host "DOCTOR: all green" -ForegroundColor Green; exit 0 }
