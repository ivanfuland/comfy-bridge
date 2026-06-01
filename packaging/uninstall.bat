@chcp 65001 >nul
@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "ROOT=%~1"
if "%ROOT%"=="" (
  echo 请把【ComfyUI 便携包根目录】拖到这里后回车：
  set /p "ROOT=> "
)
set "ROOT=%ROOT:"=%"
if "%ROOT%"=="" ( echo 未输入路径。& pause & exit /b 1 )

set "DEST=%ROOT%\ComfyUI\custom_nodes\comfy-bridge-gating"
if exist "%DEST%" ( rmdir /S /Q "%DEST%" & echo 已删除 gating 节点。) else ( echo 未发现 gating 节点，跳过。)

if exist "%ROOT%\run_nvidia_gpu_bridge.bat" ( del /Q "%ROOT%\run_nvidia_gpu_bridge.bat" & echo 已删除 run_nvidia_gpu_bridge.bat。) else ( echo 未发现兄弟启动器，跳过。)

echo 卸载完成。官方 run_nvidia_gpu.bat 未受影响。
pause
