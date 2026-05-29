' Launch a PowerShell script with NO visible window (window style 0 = hidden).
' wscript.exe is a windowless host; Run(..., 0, True) starts PowerShell hidden AND waits,
' so for a long-running service (the bridge) the scheduled-task action stays alive the whole
' time -> the task shows Running and its restart-on-failure still works, with no desktop
' window to accidentally close. For a one-shot (the watchdog) it just waits out the check.
'
' Usage:
'   wscript.exe run-hidden.vbs <script.ps1> [<logfile-for--LogFile>]
Set sh = CreateObject("WScript.Shell")
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & WScript.Arguments(0) & """"
If WScript.Arguments.Count > 1 Then
  cmd = cmd & " -LogFile """ & WScript.Arguments(1) & """"
End If
sh.Run cmd, 0, True
