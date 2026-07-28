# Registers the weekly crypto/stocks retrain (deploy/retrain_investment_scoring.ps1)
# as a Windows Task Scheduler job -- run once, as your normal user:
#
#   powershell -File deploy/register_investment_retrain_task.ps1
#
# Runs every Sunday at 4am local time (low-traffic window, staggered from
# the MLB/NPB 3am daily retrain so they don't compete for CPU/RAM at the
# same time). StartWhenAvailable means a missed run (PC off) fires as soon
# as it's back on, same as the other scheduled retrains -- fully unattended,
# no confirmation prompts of any kind.
# To remove: Unregister-ScheduledTask -TaskName "DegenPredictions-InvestmentRetrain" -Confirm:$false

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ScriptPath = "$RepoRoot\deploy\retrain_investment_scoring.ps1"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 4am
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)

Register-ScheduledTask -TaskName "DegenPredictions-InvestmentRetrain" `
    -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Weekly crypto/stocks (investment_scoring) retrain + deploy to EC2 (see deploy/retrain_investment_scoring.ps1)" `
    -Force

Write-Output "Registered. Next run:"
Get-ScheduledTask -TaskName "DegenPredictions-InvestmentRetrain" | Get-ScheduledTaskInfo | Select-Object NextRunTime
