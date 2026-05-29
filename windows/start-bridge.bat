@echo off
REM Thin wrapper around start-bridge.ps1 - double-click to launch.
REM Uses -ExecutionPolicy Bypass so users don't have to fiddle with PS policy.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-bridge.ps1"
