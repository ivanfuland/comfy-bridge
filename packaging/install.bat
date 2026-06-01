@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo   comfy-bridge : connect to your ComfyUI portable
echo ============================================================
echo.

rem ---- 1. locate the ComfyUI portable root ----
set "ROOT=%~1"
if "%ROOT%"=="" (
  echo Drag the ComfyUI portable ROOT folder (the one with run_nvidia_gpu.bat) here, then press Enter:
  set /p "ROOT=> "
)
rem strip surrounding quotes if any
set "ROOT=%ROOT:"=%"
if "%ROOT%"=="" ( echo No path entered.& pause & exit /b 1 )

rem ---- 2. structural checks ----
if not exist "%ROOT%\run_nvidia_gpu.bat" ( echo [ERROR] %ROOT%\run_nvidia_gpu.bat not found - is this the ComfyUI portable root?& pause & exit /b 1 )
if not exist "%ROOT%\ComfyUI\main.py"   ( echo [ERROR] %ROOT%\ComfyUI\main.py not found.& pause & exit /b 1 )
if not exist "%ROOT%\python_embeded\python.exe" ( echo [WARN] python_embeded not found - may not be a standard portable build; continue at your own risk.& pause )

rem ---- 2b. probe: does this ComfyUI accept --comfy-api-base ----
"%ROOT%\python_embeded\python.exe" -s "%ROOT%\ComfyUI\main.py" --help 2>nul | findstr /C:"comfy-api-base" >nul
if errorlevel 1 (
  echo [WARN] This ComfyUI's main.py --help does not list --comfy-api-base.
  echo   It may be too old or modified; bridge routing might not take effect. Continue anyway?
  pause
)

rem ---- 3. copy the gating custom_node ----
set "DEST=%ROOT%\ComfyUI\custom_nodes\comfy-bridge-gating"
echo [1/3] installing gating node -^> %DEST%
if not exist "comfy-bridge-gating\__init__.py" ( echo [ERROR] kit is missing comfy-bridge-gating - incomplete unzip.& pause & exit /b 1 )
robocopy "comfy-bridge-gating" "%DEST%" /MIR /NJH /NJS /NDL /NP >nul
if errorlevel 8 ( echo [ERROR] failed to copy gating node.& pause & exit /b 1 )

rem ---- 4. generate the sibling launcher (calls separate .ps1; official bat untouched) ----
set "SRC=%ROOT%\run_nvidia_gpu.bat"
set "DST=%ROOT%\run_nvidia_gpu_bridge.bat"
echo [2/3] generating launcher -^> %DST%
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_patch_launcher.ps1" -Src "%SRC%" -Dst "%DST%"
if errorlevel 1 ( echo [ERROR] launcher generation failed (official bat launch line may be non-standard; add --comfy-api-base by hand).& pause & exit /b 1 )
if not exist "%DST%" ( echo [ERROR] launcher was not generated.& pause & exit /b 1 )
findstr /C:"comfy-api-base" "%DST%" >nul || ( echo [ERROR] launcher does not contain --comfy-api-base.& pause & exit /b 1 )

rem ---- 5. prepare .env ----
echo [3/3] preparing config
if not exist ".env.example" ( echo [ERROR] kit is missing .env.example - incomplete unzip.& pause & exit /b 1 )
if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  if errorlevel 1 ( echo [ERROR] failed to create .env (is the folder read-only?).& pause & exit /b 1 )
  if not exist ".env" ( echo [ERROR] .env was not created.& pause & exit /b 1 )
  echo   .env created - remember to paste your gateway key into it.
)

echo.
echo ============================================================
echo   Done. Daily usage:
echo   1) double-click start-bridge.bat in this kit to start the bridge
echo   2) double-click %ROOT%\run_nvidia_gpu_bridge.bat to start ComfyUI
echo   (using the official run_nvidia_gpu.bat will NOT route through the bridge)
echo ============================================================
pause
