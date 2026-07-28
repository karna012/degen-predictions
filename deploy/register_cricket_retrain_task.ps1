# Registers the event-driven cricket retrain (scripts/cricket/check_and_retrain_cricket.py)
# as a Windows Task Scheduler job -- run once, as your normal user:
#
#   powershell -File deploy/register_cricket_retrain_task.ps1
#
# Runs every 45 minutes (Hundred/MLC play far fewer matches per day than a
# soccer league's full slate, so a slightly longer interval than the 30-min
# soccer job is a reasonable fit for the seasonal match cadence). Like the
# soccer job, the script itself checks whether any new completed match data
# actually showed up since the last retrain and skips (near-instantly) if
# not, so this cadence just means "react to newly finished matches within
# ~45 minutes" without wasting compute on empty checks.
# To remove: Unregister-ScheduledTask -TaskName "DegenPredictions-CricketRetrain" -Confirm:$false

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = "$RepoRoot\venv_train\Scripts\python.exe"
$ScriptPath = "$RepoRoot\scripts\cricket\check_and_retrain_cricket.py"

$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$ScriptPath`"" -WorkingDirectory "$RepoRoot\scripts\cricket"
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 45) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "DegenPredictions-CricketRetrain" `
    -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Checks every 45 min for new Hundred/MLC results and retrains+deploys only if found (see scripts/cricket/check_and_retrain_cricket.py)" `
    -Force

Write-Output "Registered. Next run:"
Get-ScheduledTask -TaskName "DegenPredictions-CricketRetrain" | Get-ScheduledTaskInfo | Select-Object NextRunTime
