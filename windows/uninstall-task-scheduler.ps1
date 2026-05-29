# Remove the comfy-bridge Task Scheduler entries (created by install-task-scheduler.ps1).
$ErrorActionPreference = "Stop"

foreach ($TaskName in @("comfy-bridge", "comfy-bridge-watchdog")) {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[comfy-bridge] removed task: $TaskName" -ForegroundColor Green
  } else {
    Write-Host "[comfy-bridge] no task to remove: $TaskName" -ForegroundColor Yellow
  }
}
