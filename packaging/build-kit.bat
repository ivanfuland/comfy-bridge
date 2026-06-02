@echo off
rem Double-click to build the portable kit zip locally.
rem Pass-through args, e.g.:  build-kit.bat -Version v0.1.0-rc5 -SkipSmoke
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-kit.ps1" %*
echo.
pause
