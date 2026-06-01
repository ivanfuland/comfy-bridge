@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "ROOT=%~1"
if "%ROOT%"=="" (
  echo Drag the ComfyUI portable ROOT folder here, then press Enter:
  set /p "ROOT=> "
)
set "ROOT=%ROOT:"=%"
if "%ROOT%"=="" ( echo No path entered.& pause & exit /b 1 )

set "DEST=%ROOT%\ComfyUI\custom_nodes\comfy-bridge-gating"
if exist "%DEST%" ( rmdir /S /Q "%DEST%" & echo Removed gating node.) else ( echo Gating node not found, skipping.)

if exist "%ROOT%\run_nvidia_gpu_bridge.bat" ( del /Q "%ROOT%\run_nvidia_gpu_bridge.bat" & echo Removed run_nvidia_gpu_bridge.bat.) else ( echo Sibling launcher not found, skipping.)

echo Uninstall complete. The official run_nvidia_gpu.bat was not touched.
pause
