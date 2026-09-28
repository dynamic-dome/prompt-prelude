[CmdletBinding()]
param(
    [string]$PythonwExe,
    [int]$IntervalMinutes = 5,
    [switch]$Unregister
)

# Registers the user task "Prelude-Atlas-Keepalive": every N minutes a tiny
# /search against the Atlas daemon keeps the embedder warm (NOTES Befund 12,
# idea 8). Runs only while the user is logged on, no admin rights needed.
# Remove again:  powershell -ExecutionPolicy Bypass -File tools\register-keepalive-task.ps1 -Unregister
# Kept pure ASCII on purpose (PowerShell 5.1 reads BOM-less files as cp1252).

$ErrorActionPreference = "Stop"
$TaskName = "Prelude-Atlas-Keepalive"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed: $TaskName"
    return
}

$script = Join-Path $PSScriptRoot "daemon_keepalive.py"
if (-not (Test-Path -LiteralPath $script)) {
    throw "keepalive script missing: $script"
}
if (-not $PythonwExe) {
    $PythonwExe = (Get-Command pythonw -CommandType Application -ErrorAction Stop |
        Select-Object -First 1).Source
}

$action = New-ScheduledTaskAction -Execute $PythonwExe -Argument ('"' + $script + '"') `
    -WorkingDirectory (Split-Path $script -Parent)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
Write-Output ("registered: {0} state={1} every {2} min -> {3} {4}" -f `
    $TaskName, $task.State, $IntervalMinutes, $PythonwExe, $script)
