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
$LogFile = Join-Path $BridgeDir "logs\bridge.log"
$TaskName = "comfy-bridge"
$WatchdogName = "comfy-bridge-watchdog"

if (-not (Test-Path $LaunchScript)) {
  Write-Error "Launch script not found: $LaunchScript"
  exit 1
}

$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

# ── Bridge service task ──────────────────────────────────────────────────────
# -LogFile routes uvicorn output to a rotated logfile (hidden task has no console).
# ExecutionTimeLimit 0 = NO limit: the default New-ScheduledTaskSettingsSet bakes in
# PT72H, which would force-kill this long-running service every 3 days.
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$LaunchScript`" -LogFile `"$LogFile`""
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
$wdAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$HealthScript`""
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
