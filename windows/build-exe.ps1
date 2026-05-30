<#
  build-exe.ps1 - Build a standalone comfy-bridge.exe (PyInstaller) and assemble a
  distributable folder for machines WITHOUT Python.

  Run:  powershell -ExecutionPolicy Bypass -File comfy-bridge\windows\build-exe.ps1

  Output: comfy-bridge\dist\comfy-bridge-dist\  containing
    comfy-bridge.exe        the bridge server (no Python needed on the target)
    .env.example            copy to .env and fill in gateway URL + key
    comfy-bridge-gating\    copy into ComfyUI\custom_nodes\ (stays .py; runs in ComfyUI's Python)
    README-fenfa.txt        usage notes (Chinese)

  NB: the exe bundles ONLY the bridge server. The gating custom_node must stay a .py inside
  ComfyUI (it loads in ComfyUI's interpreter); ComfyUI itself still needs its own Python+torch.

  This script is intentionally ASCII-only so Windows PowerShell 5.1 parses it reliably
  (UTF-8-no-BOM + multibyte content breaks here-strings under 5.1). User-facing Chinese text
  lives in windows\dist-README.txt and is copied verbatim.
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot          # repo root (parent of windows\)
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw "bridge venv not found at $py - run windows\bootstrap.ps1 first" }

Write-Host '[1/4] installing build deps (pyinstaller)...' -ForegroundColor Cyan
& $py -m pip install -e ".[build]" --quiet
if ($LASTEXITCODE -ne 0) { throw 'pip install .[build] failed' }

Write-Host '[2/4] running PyInstaller (comfy-bridge.spec)...' -ForegroundColor Cyan
& $py -m PyInstaller comfy-bridge.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
$exe = Join-Path $root 'dist\comfy-bridge.exe'
if (-not (Test-Path $exe)) { throw "build did not produce $exe" }

Write-Host '[3/4] assembling distribution folder...' -ForegroundColor Cyan
$out = Join-Path $root 'dist\comfy-bridge-dist'
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Force $out | Out-Null
Copy-Item $exe (Join-Path $out 'comfy-bridge.exe')
Copy-Item (Join-Path $root '.env.example') (Join-Path $out '.env.example')
Copy-Item (Join-Path $root 'custom_nodes\comfy-bridge-gating') (Join-Path $out 'comfy-bridge-gating') -Recurse
Copy-Item (Join-Path $PSScriptRoot 'dist-README.txt') (Join-Path $out 'README-fenfa.txt')
# strip any __pycache__ from the copied custom_node
Get-ChildItem (Join-Path $out 'comfy-bridge-gating') -Recurse -Directory -Filter '__pycache__' -EA SilentlyContinue |
  Remove-Item -Recurse -Force -EA SilentlyContinue

$exeSizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "[4/4] done. exe ${exeSizeMb}MB. distribution -> $out" -ForegroundColor Green
Get-ChildItem $out | Select-Object Name, @{n = 'SizeKB'; e = { [math]::Round($_.Length / 1KB) } } | Format-Table -AutoSize
