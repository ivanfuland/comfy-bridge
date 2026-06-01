@chcp 65001 >nul
@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo   comfy-bridge 接入 ComfyUI 便携包
echo ============================================================
echo.

rem ---- 1. 定位 ComfyUI 便携包根目录 ----
set "ROOT=%~1"
if "%ROOT%"=="" (
  echo 请把【ComfyUI 便携包根目录】（含 run_nvidia_gpu.bat 那层）拖到本窗口后回车：
  set /p "ROOT=> "
)
rem 去掉可能的成对引号
set "ROOT=%ROOT:"=%"
if "%ROOT%"=="" ( echo 未输入路径。& pause & exit /b 1 )

rem ---- 2. 结构预检 ----
if not exist "%ROOT%\run_nvidia_gpu.bat" ( echo [错误] 找不到 %ROOT%\run_nvidia_gpu.bat，确认是 ComfyUI 便携包根目录。& pause & exit /b 1 )
if not exist "%ROOT%\ComfyUI\main.py"   ( echo [错误] 找不到 %ROOT%\ComfyUI\main.py。& pause & exit /b 1 )
if not exist "%ROOT%\python_embeded\python.exe" ( echo [警告] 未见 python_embeded，可能非标准便携包，继续需自行确认。& pause )

rem ---- 2b. 兼容探测：该 ComfyUI 是否认 --comfy-api-base（Codex plan-review #2）----
"%ROOT%\python_embeded\python.exe" -s "%ROOT%\ComfyUI\main.py" --help 2>nul | findstr /C:"comfy-api-base" >nul
if errorlevel 1 (
  echo [警告] 这份 ComfyUI 的 main.py --help 未列出 --comfy-api-base。
  echo   可能版本过旧/魔改，装上去 bridge 路由可能不生效。是否仍继续？
  pause
)

rem ---- 3. 拷 gating custom_node ----
set "DEST=%ROOT%\ComfyUI\custom_nodes\comfy-bridge-gating"
echo [1/3] 安装 gating 节点 -^> %DEST%
if not exist "comfy-bridge-gating\__init__.py" ( echo [错误] 套件缺少 comfy-bridge-gating，解压不完整。& pause & exit /b 1 )
robocopy "comfy-bridge-gating" "%DEST%" /MIR /NJH /NJS /NDL /NP >nul
if errorlevel 8 ( echo [错误] 复制 gating 节点失败。& pause & exit /b 1 )

rem ---- 4. 生成兄弟启动器（调独立 .ps1，路径作参数；不动官方 bat）----
set "SRC=%ROOT%\run_nvidia_gpu.bat"
set "DST=%ROOT%\run_nvidia_gpu_bridge.bat"
echo [2/3] 生成启动器 -^> %DST%
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_patch_launcher.ps1" -Src "%SRC%" -Dst "%DST%"
if errorlevel 1 ( echo [错误] 启动器生成失败（官方 bat 启动行可能非常规，请手动加 --comfy-api-base）。& pause & exit /b 1 )
if not exist "%DST%" ( echo [错误] 启动器未生成。& pause & exit /b 1 )
findstr /C:"comfy-api-base" "%DST%" >nul || ( echo [错误] 启动器未含 --comfy-api-base。& pause & exit /b 1 )

rem ---- 5. 准备 .env ----
echo [3/3] 准备配置文件
if not exist ".env.example" ( echo [错误] 套件缺少 .env.example，解压不完整。& pause & exit /b 1 )
if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  if errorlevel 1 ( echo [错误] 生成 .env 失败（目录是否只读？）。& pause & exit /b 1 )
  if not exist ".env" ( echo [错误] .env 未生成。& pause & exit /b 1 )
  echo   已生成 .env，请记得填入你的雷火网关 key。
)

echo.
echo ============================================================
echo   完成！日常用法：
echo   1) 双击本套件的 start-bridge.bat 启动 bridge
echo   2) 双击 %ROOT%\run_nvidia_gpu_bridge.bat 启动 ComfyUI
echo   （仍用官方 run_nvidia_gpu.bat 则不会接入 bridge）
echo ============================================================
pause
