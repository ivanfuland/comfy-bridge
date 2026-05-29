@echo off
REM Live view of comfy-bridge traffic. The bridge runs as a HIDDEN service (no window of its
REM own); this just tails its log file. Double-click to open; close anytime (does NOT affect
REM the bridge). Do NOT run start-bridge to "watch" -- that restarts the service.
title comfy-bridge log (live)  -  close this window to stop watching
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content '%~dp0..\logs\bridge.log' -Wait -Tail 40"
