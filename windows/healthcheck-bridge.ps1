# comfy-bridge watchdog: verify the bridge is actually serving, restart it if not.
#
# Registered by install-task-scheduler.ps1 as task "comfy-bridge-watchdog", run every
# 5 min. Covers death modes the bridge task's restart-on-failure misses: manual kill,
# hang, or a stale grandchild process holding :8190 with broken config (a port that is
# merely LISTENing is NOT proof of health -- so we probe the HTTP endpoint, not the port).
#
# Restart only after 3 consecutive failed probes (~10s) so a momentarily busy bridge
# (mid-request, slow GC) is never killed by a single timed-out probe.
$ErrorActionPreference = "Stop"
$GatingUrl = "http://127.0.0.1:8190/comfy-bridge/gating"
$TaskName = "comfy-bridge"

$healthy = $false
for ($i = 0; $i -lt 3; $i++) {
  try {
    Invoke-RestMethod $GatingUrl -TimeoutSec 5 | Out-Null   # throws on non-2xx / no-connect
    $healthy = $true
    break
  } catch {
    Start-Sleep -Seconds 3
  }
}

if ($healthy) { exit 0 }

Write-Host "[watchdog] bridge unhealthy after 3 probes - restarting" -ForegroundColor Yellow
# kill any stale process squatting on the port (Stop-ScheduledTask won't kill grandchildren)
try {
  Get-NetTCPConnection -LocalPort 8190 -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
} catch {}
Start-Sleep -Seconds 1
Start-ScheduledTask -TaskName $TaskName
