# Registers the weekly FULL retrain as a Windows Task Scheduler job.
# Covers soccer (all leagues), MLB, NPB, cricket, crypto + stocks.
#
#   powershell -File deploy/register_retrain_task.ps1
#
# Sunday 03:00 local. StartWhenAvailable retries if the PC was off.
# To remove:
#   Unregister-ScheduledTask -TaskName "DegenPredictions-WeeklyRetrain" -Confirm:$false

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ScriptPath = "$RepoRoot\deploy\retrain_and_deploy.ps1"

Unregister-ScheduledTask -TaskName "DegenPredictions-DailyRetrain" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "DegenPredictions-WeeklyRetrain" -Confirm:$false -ErrorAction SilentlyContinue
# One weekly job replaces the old frequent sport-specific timers.
Unregister-ScheduledTask -TaskName "DegenPredictions-SoccerRetrain" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "DegenPredictions-CricketRetrain" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "DegenPredictions-InvestmentRetrain" -Confirm:$false -ErrorAction SilentlyContinue

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3am
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)

Register-ScheduledTask -TaskName "DegenPredictions-WeeklyRetrain" `
    -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Weekly full retrain: soccer + MLB + NPB + cricket + crypto/stocks, then promote-if-better deploy (deploy/retrain_and_deploy.ps1)" `
    -Force

Write-Output "Registered weekly FULL retrain (Sunday 03:00). Next run:"
Get-ScheduledTask -TaskName "DegenPredictions-WeeklyRetrain" | Get-ScheduledTaskInfo | Select-Object NextRunTime
Get-ScheduledTask -TaskName "DegenPredictions-*" | Select-Object TaskName, State | Format-Table -AutoSize
