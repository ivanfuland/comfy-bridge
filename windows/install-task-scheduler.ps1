# Register comfy-bridge as a Task Scheduler entry that runs on user login.
# Equivalent of `systemctl --user enable` on Linux.
#
# Usage (open PowerShell, NOT elevated, from repo root):
#   powershell -ExecutionPolicy Bypass -File windows\install-task-scheduler.ps1
#
# After install:
#   - Bridge starts automatically when you log in
#   - Manual control: Task Scheduler GUI -> Task Scheduler Library -> "comfy-bridge"
#   - Uninstall: powershell -File windows\uninstall-task-scheduler.ps1

$ErrorActionPreference = "Stop"
$BridgeDir = Split-Path -Parent $PSScriptRoot
$LaunchScript = Join-Path $BridgeDir "windows\start-bridge.ps1"
$HealthScript = Join-Path $BridgeDir "windows\healthcheck-bridge.ps1"
$HiddenVbs = Join-Path $BridgeDir "windows\run-hidden.vbs"
$LogFile = Join-Path $BridgeDir "logs\bridge.log"
$TaskName = "comfy-bridge"
$WatchdogName = "comfy-bridge-watchdog"

if (-not (Test-Path $LaunchScript)) {
  Write-Error "Launch script not found: $LaunchScript"
  exit 1
}

$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

# ── Bridge service task ──────────────────────────────────────────────────────
# Launch via wscript + run-hidden.vbs so there is NO desktop window to accidentally close
# (-WindowStyle Hidden is not honored on some boxes and leaves a visible window = the bridge,
# which a user can close and kill the service). The vbs runs PowerShell hidden AND waits, so
# the task stays Running and restart-on-failure still works. -LogFile -> rotated logfile (Tee
# also echoes to console, which is harmless when hidden). ExecutionTimeLimit 0 = NO limit
# (default New-ScheduledTaskSettingsSet bakes in PT72H = force-kill every 3 days).
$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$HiddenVbs`" `"$LaunchScript`" `"$LogFile`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "[comfy-bridge] removed existing task entry" -ForegroundColor Yellow
}
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "comfy-bridge: FastAPI proxy for ComfyUI api_nodes" | Out-Null

# ── Watchdog task (every 5 min) ──────────────────────────────────────────────
# Probes the HTTP endpoint and restarts the bridge on death/hang/stale-port. The bridge
# task's own restart-on-failure only covers clean crash-exit; this covers the rest.
# Launch via wscript + run-hidden.vbs so the 5-min health check never flashes a console
# window on the desktop (powershell.exe -WindowStyle Hidden still briefly flashes on some
# interactive-logon setups; wscript Run(...,0) is truly windowless).
$wdAction = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$HiddenVbs`" `"$HealthScript`""
# -Once + RepetitionInterval with no duration => repeats indefinitely; +1min so it doesn't
# race the bridge's own logon start.
$wdTrigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 5)
$wdSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $WatchdogName -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $WatchdogName -Confirm:$false
}
Register-ScheduledTask -TaskName $WatchdogName -Action $wdAction -Trigger $wdTrigger -Settings $wdSettings -Principal $Principal -Description "comfy-bridge watchdog: health-check + auto-restart every 5 min" | Out-Null

Write-Host "[comfy-bridge] task + watchdog registered. Bridge starts on login, watchdog checks every 5 min." -ForegroundColor Green
Write-Host "  Start now:    Start-ScheduledTask -TaskName comfy-bridge"
Write-Host "  Status:       Get-ScheduledTask -TaskName comfy-bridge,comfy-bridge-watchdog"
Write-Host "  Logs:         $LogFile"
Write-Host "  Uninstall:    powershell -File windows\uninstall-task-scheduler.ps1"
