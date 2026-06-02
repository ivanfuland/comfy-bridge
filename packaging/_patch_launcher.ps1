param(
  [Parameter(Mandatory)][string]$Src,
  [Parameter(Mandatory)][string]$Dst
)
# ASCII-only on purpose: install.bat invokes this via `powershell` (Windows PowerShell 5.1),
# which decodes a BOM-less .ps1 using the system ANSI code page. Non-ASCII comments would be
# mis-decoded on non-English Windows and break parsing -- keep this file ASCII.
$ErrorActionPreference = 'Stop'
$flag = '--comfy-api-base=http://127.0.0.1:8190'

# Find the REAL launch line: contains `python ... main.py` AND is not a comment (rem / :: / #).
# Excluding comments avoids patching a line that merely mentions main.py, which would leave the
# actual launch line unpatched while findstr still "passes" (Codex M-2).
$lines = @(Get-Content -LiteralPath $Src)
$match = $lines |
  Select-String -Pattern 'python.*main\.py' |
  Where-Object { $_.Line.TrimStart() -notmatch '^(@?rem\b|::|#)' } |
  Select-Object -First 1
if (-not $match) { throw 'official launcher: no executable "python ... main.py" line found' }
$idx = $match.LineNumber - 1
$launch = $lines[$idx]

# -Encoding OEM matches cmd.exe's console code page (correct for a .bat; avoids GBK mojibake of
# a UTF-8 source bat on non-English Windows). ASCII content is unaffected.
if ($launch -match 'comfy-api-base') {
  # idempotent: official line already has the flag (rare) -> copy verbatim
  Set-Content -LiteralPath $Dst -Value $lines -Encoding OEM
}
elseif ($launch -match '%\*') {
  # preferred wrap-via-call: official line forwards %* -> wrap instead of copying it
  $name = Split-Path -Leaf $Src
  $wrap = @('@echo off', ('call "%~dp0' + $name + '" ' + $flag + ' %*'))
  Set-Content -LiteralPath $Dst -Value $wrap -Encoding OEM
}
else {
  # fallback full-replicate: copy whole file, append flag only to the main.py line.
  # A caret (^) line-continuation can't be safely appended to (Codex H-2) -> fail loudly so
  # install.bat tells the user to add the flag by hand.
  if ($launch.TrimEnd().EndsWith('^')) {
    throw 'launch line ends with a caret (^) continuation; cannot safely append the flag -- add --comfy-api-base to the python main.py line by hand.'
  }
  $out = for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($i -eq $idx) { $lines[$i].TrimEnd() + ' ' + $flag } else { $lines[$i] }
  }
  Set-Content -LiteralPath $Dst -Value $out -Encoding OEM
}

# Post-verify (Codex M-2): the flag must land on an ACTIVE (non-comment) line -- covers both the
# patched main.py line (full-replicate/idempotent) and the call-wrapper line (wrap-via-call).
$check = @(Get-Content -LiteralPath $Dst)
$flagLine = $check | Where-Object { $_ -match 'comfy-api-base' -and $_.TrimStart() -notmatch '^(@?rem\b|::|#)' }
if (-not $flagLine) { throw 'post-check failed: --comfy-api-base is not on an active (non-comment) line of the generated launcher.' }
