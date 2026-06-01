param(
  [Parameter(Mandatory)][string]$Src,
  [Parameter(Mandatory)][string]$Dst
)
$ErrorActionPreference = 'Stop'
$flag = '--comfy-api-base=http://127.0.0.1:8190'

$lines = @(Get-Content -LiteralPath $Src)
$match = $lines | Select-String -Pattern 'python.*main\.py' | Select-Object -First 1
if (-not $match) { Write-Error 'official launcher: no "python ... main.py" line found'; exit 3 }
$idx = $match.LineNumber - 1
$launch = $lines[$idx]

if ($launch -match 'comfy-api-base') {
  # 幂等：官方行已含 flag（极少见），原样复制
  Set-Content -LiteralPath $Dst -Value $lines -Encoding Default
}
elseif ($launch -match '%\*') {
  # 首选 wrap-via-call：官方行透传 %* → 不复制启动行，包装调用（spec §8.1 首选）
  $name = Split-Path -Leaf $Src
  $wrap = @('@echo off', ('call "%~dp0' + $name + '" ' + $flag + ' %*'))
  Set-Content -LiteralPath $Dst -Value $wrap -Encoding Default
}
else {
  # 回退 full-replicate：复制全文，仅 main.py 行尾插 flag（保留所有前置 set/%~dp0/pause）
  $out = for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($i -eq $idx) { $lines[$i].TrimEnd() + ' ' + $flag } else { $lines[$i] }
  }
  Set-Content -LiteralPath $Dst -Value $out -Encoding Default
}