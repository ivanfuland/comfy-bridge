@chcp 65001 >nul
@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".env" (
  echo [comfy-bridge] 未找到 .env。
  echo   请先把 .env.example 复制为 .env，并填入你的雷火网关 key。
  pause
  exit /b 1
)

rem 至少一个 active（非注释）*_API_KEY 有非空白值（Codex plan-review #7：
rem findstr 会把注释行/全空格值误判为有效，改用 PowerShell trim 检查）
powershell -NoProfile -Command "if (-not (Get-Content '.env' | Where-Object { $_ -notmatch '^\s*#' -and $_ -match '^\s*[A-Z_]+_API_KEY\s*=\s*\S' })) { exit 1 }"
if errorlevel 1 (
  echo [comfy-bridge] .env 里没有任何已填写的 *_API_KEY。
  echo   请打开 .env 填入你的雷火网关 key 后再启动。
  pause
  exit /b 1
)

if not exist "bridge\bridge.exe" (
  echo [comfy-bridge] 缺少 bridge\bridge.exe，套件可能未完整解压。
  pause
  exit /b 1
)

echo [comfy-bridge] starting on http://127.0.0.1:8190  （关闭本窗口即停止服务）
"%~dp0bridge\bridge.exe"
