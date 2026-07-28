# Registers the daily ML retrain (deploy/retrain_and_deploy.ps1) as a
# Windows Task Scheduler job -- run once, as your normal user (no admin
# required for a per-user scheduled task):
#
#   powershell -File deploy/register_retrain_task.ps1
#
# Runs every day at 03:00 local time (low-traffic window). This machine
# must be on and unlocked at that time for it to fire -- StartWhenAvailable
# below means a missed run (PC off) fires as soon as it's back on instead
# of silently skipping the day. Change $Trigger to adjust cadence. To
# remove: Unregister-ScheduledTask -TaskName "DegenPredictions-DailyRetrain"
# -Confirm:$false

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ScriptPath = "$RepoRoot\deploy\retrain_and_deploy.ps1"

# Clean up the old weekly task name if it exists from a prior registration.
Unregister-ScheduledTask -TaskName "DegenPredictions-WeeklyRetrain" -Confirm:$false -ErrorAction SilentlyContinue

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
$Trigger = New-ScheduledTaskTrigger -Daily -At 3am
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName "DegenPredictions-DailyRetrain" `
    -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Daily MLB/NPB ML ensemble retrain + deploy to EC2 (see deploy/retrain_and_deploy.ps1)" `
    -Force

Write-Output "Registered. Next run:"
Get-ScheduledTask -TaskName "DegenPredictions-DailyRetrain" | Get-ScheduledTaskInfo | Select-Object NextRunTime
