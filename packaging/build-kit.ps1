# One-click LOCAL build of the comfy-bridge portable kit zip.
# Mirrors .github/workflows/release.yml, but runs on this machine. ASCII-only on purpose
# (Windows PowerShell 5.1 mis-parses BOM-less .ps1 with non-ASCII on GBK locales).
#
# Usage (from anywhere):
#   powershell -ExecutionPolicy Bypass -File packaging\build-kit.ps1
#   powershell -ExecutionPolicy Bypass -File packaging\build-kit.ps1 -Version v0.1.0-rc5 -OutDir D:\out
#   ... -SkipSmoke          (skip the frozen-exe smoke test)
#   ... -SmokePort 8202     (smoke binds this port; default 8199 to avoid the prod bridge on 8190)
param(
  [string]$Version,
  [string]$OutDir,
  [int]$SmokePort = 8199,
  [switch]$SkipSmoke
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot          # packaging\ -> repo root
$Py   = Join-Path $Repo '.venv\Scripts\python.exe'
$Pyi  = Join-Path $Repo '.venv\Scripts\pyinstaller.exe'
Set-Location $Repo

if (-not (Test-Path $Py)) { throw "venv python not found at $Py - create the dev venv first" }

# default version from pyproject.toml
if (-not $Version) {
  $m = Select-String -Path (Join-Path $Repo 'pyproject.toml') -Pattern '^\s*version\s*=\s*"([^"]+)"' | Select-Object -First 1
  if (-not $m) { throw "could not read version from pyproject.toml" }
  $Version = "v$($m.Matches[0].Groups[1].Value)"
}
if (-not $OutDir) { $OutDir = $Repo }
$kitName = "comfy-bridge-kit-$Version"

Write-Host "[build-kit] $kitName  (out: $OutDir)" -ForegroundColor Cyan

# 1) ensure pyinstaller (dev venv already has the app + deps)
Write-Host "[1/5] checking pyinstaller ..."
& $Py -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
if ($LASTEXITCODE -ne 0) {
  Write-Host "      installing pyinstaller (pinned) ..."
  & $Py -m pip install pyinstaller -c packaging\constraints-build.txt
}

# 2) build the exe.
# PyInstaller logs INFO to STDERR; under $ErrorActionPreference='Stop' a native command's
# stderr is promoted to a terminating NativeCommandError. Drop to Continue for the call and
# gate on $LASTEXITCODE + the artifact instead.
Write-Host "[2/5] building exe (pyinstaller bridge.spec) ..."
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $Pyi bridge.spec --noconfirm 2>&1 | Select-Object -Last 1 | Out-Host
$pyiExit = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($pyiExit -ne 0 -or -not (Test-Path 'dist\bridge\bridge.exe')) { throw "pyinstaller build failed (exit $pyiExit)" }

# 3) assemble the kit folder
Write-Host "[3/5] assembling kit ..."
$stageRoot = Join-Path $Repo 'dist\_kitstage'
if (Test-Path $stageRoot) { Remove-Item -LiteralPath $stageRoot -Recurse -Force }
$stage = Join-Path $stageRoot $kitName
New-Item -ItemType Directory -Force $stage | Out-Null
Copy-Item "$Repo\dist\bridge" "$stage\bridge" -Recurse -Force
Copy-Item "$Repo\custom_nodes\comfy-bridge-gating" "$stage\comfy-bridge-gating" -Recurse -Force
foreach ($d in (Get-ChildItem "$stage\comfy-bridge-gating" -Recurse -Directory -Filter '__pycache__')) {
  Remove-Item -LiteralPath $d.FullName -Recurse -Force
}
Copy-Item "$Repo\packaging\.env.example.kit" "$stage\.env.example" -Force
foreach ($f in 'install.bat','_patch_launcher.ps1','start-bridge.bat','uninstall.bat') {
  Copy-Item "$Repo\packaging\$f" "$stage\" -Force
}
# the recipient readme has a non-ASCII name; reference it by extension so this script stays ASCII-only
Get-ChildItem -LiteralPath "$Repo\packaging" -Filter '*.txt' | ForEach-Object { Copy-Item -LiteralPath $_.FullName "$stage\" -Force }

# 4) frozen-exe smoke (REAL kit layout: .env at root, exe in bridge\ -> validates walk-up)
if (-not $SkipSmoke) {
  Write-Host "[4/5] smoke test on :$SmokePort (prod bridge on 8190 is left alone) ..."
  $smoke = Join-Path $Repo 'dist\_smoke'
  if (Test-Path $smoke) { Remove-Item -LiteralPath $smoke -Recurse -Force }
  New-Item -ItemType Directory -Force $smoke | Out-Null
  Copy-Item "$stage\bridge" "$smoke\bridge" -Recurse -Force
  Set-Content "$smoke\.env" -Value "BRIDGE_HOST=127.0.0.1`r`nBRIDGE_PORT=$SmokePort" -Encoding ascii
  $p = Start-Process "$smoke\bridge\bridge.exe" -PassThru -WindowStyle Hidden
  try {
    $ok = $false
    foreach ($i in 1..15) {
      Start-Sleep 2
      try { if ((Invoke-RestMethod "http://127.0.0.1:$SmokePort/comfy-bridge/gating" -TimeoutSec 3).gating_enabled) { $ok = $true; break } } catch {}
    }
    if (-not $ok) { throw "smoke failed: gating unhealthy (missing dep, or walk-up did not find root .env)" }
    Write-Host "      smoke OK (frozen exe healthy, walk-up found root .env)" -ForegroundColor Green
  } finally {
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep 1
    Remove-Item -LiteralPath $smoke -Recurse -Force -ErrorAction SilentlyContinue
  }
} else {
  Write-Host "[4/5] smoke skipped (-SkipSmoke)"
}

# 5) zip + verify structure / ASCII bats
Write-Host "[5/5] zipping + verifying ..."
$zip = Join-Path $OutDir "$kitName.zip"
Compress-Archive -Path (Get-ChildItem -LiteralPath $stage).FullName -DestinationPath $zip -Force
$vt = Join-Path $Repo 'dist\_ziptest'
if (Test-Path $vt) { Remove-Item -LiteralPath $vt -Recurse -Force }
Expand-Archive -Path $zip -DestinationPath $vt -Force
$need = @(
  'bridge\bridge.exe','comfy-bridge-gating\__init__.py','comfy-bridge-gating\web\comfy-bridge-gating.js',
  '.env.example','install.bat','_patch_launcher.ps1','start-bridge.bat','uninstall.bat'
)
foreach ($f in $need) { if (-not (Test-Path -LiteralPath (Join-Path $vt $f))) { throw "zip missing: $f" } }
if (-not (Get-ChildItem -LiteralPath $vt -Filter '*.txt')) { throw "zip missing the recipient readme (.txt)" }
foreach ($b in 'install.bat','start-bridge.bat','uninstall.bat') {
  $hi = ([IO.File]::ReadAllBytes((Join-Path $vt $b)) | Where-Object { $_ -gt 127 }).Count
  if ($hi -ne 0) { throw "$b is not pure ASCII ($hi non-ASCII bytes)" }
}
Remove-Item -LiteralPath $vt -Recurse -Force
$z = Get-Item $zip
Write-Host ("[build-kit] DONE -> {0}  ({1:N1} MB)" -f $z.FullName, ($z.Length / 1MB)) -ForegroundColor Green
