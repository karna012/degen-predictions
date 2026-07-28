# Registers the event-driven soccer retrain (scripts/check_and_retrain_soccer.py)
# as a Windows Task Scheduler job -- run once, as your normal user:
#
#   powershell -File deploy/register_soccer_retrain_task.ps1
#
# Runs every 30 minutes. Unlike the MLB/NPB daily retrain, this doesn't
# blindly retrain on every fire -- the script itself checks whether any
# new completed match results actually showed up since the last retrain,
# and skips (near-instantly) if not. So a 30-min cadence just means "react
# to newly finished matches within ~30 minutes" without wasting compute on
# every check, since most checks find nothing new and exit immediately.
# To remove: Unregister-ScheduledTask -TaskName "DegenPredictions-SoccerRetrain" -Confirm:$false

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = "$RepoRoot\venv_train\Scripts\python.exe"
$ScriptPath = "$RepoRoot\scripts\check_and_retrain_soccer.py"

$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$ScriptPath`"" -WorkingDirectory "$RepoRoot\scripts"
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "DegenPredictions-SoccerRetrain" `
    -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Checks every 30 min for new soccer results and retrains+deploys only if found (see scripts/check_and_retrain_soccer.py)" `
    -Force

Write-Output "Registered. Next run:"
Get-ScheduledTask -TaskName "DegenPredictions-SoccerRetrain" | Get-ScheduledTaskInfo | Select-Object NextRunTime
