# Weekly ML ensemble retrain, run on THIS machine (never on the EC2 box --
# a prior retrain attempt on EC2's 1GB-RAM t3.micro exhausted memory and
# took the live site down). Refreshes data, rebuilds Elo + training
# datasets, retrains both MLB and NPB ensembles, runs the full test suite
# as a safety gate, and only deploys to EC2 if everything still passes.
#
# Scheduled via Windows Task Scheduler (see deploy/register_retrain_task.ps1
# to install it) -- can also be run by hand any time:
#   powershell -File deploy/retrain_and_deploy.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Python = "$RepoRoot\venv_train\Scripts\python.exe"
$Bash = "C:\Program Files\Git\bin\bash.exe"
$LogDir = "$RepoRoot\deploy\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = "$LogDir\retrain_$(Get-Date -Format 'yyyy-MM-dd_HHmmss').log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Output $line
    Add-Content -Path $LogFile -Value $line
}

try {
    Log "==> Refreshing MLB data + Elo"
    & $Python scripts\mlb\download_current_season_espn.py 2>&1 | Tee-Object -FilePath $LogFile -Append
    & $Python scripts\mlb\build_elo.py 2>&1 | Out-Null
    & $Python scripts\mlb\build_training_dataset.py 2>&1 | Tee-Object -FilePath $LogFile -Append

    Log "==> Training MLB ensemble"
    & $Python scripts\mlb\train_ml_models.py 2>&1 | Tee-Object -FilePath $LogFile -Append

    Log "==> Refreshing NPB data + Elo"
    & $Python scripts\npb\download_npb_data.py 2>&1 | Tee-Object -FilePath $LogFile -Append
    & $Python scripts\npb\build_elo.py 2>&1 | Out-Null
    & $Python scripts\npb\build_training_dataset.py 2>&1 | Tee-Object -FilePath $LogFile -Append

    Log "==> Training NPB ensemble"
    & $Python scripts\npb\train_ml_models.py 2>&1 | Tee-Object -FilePath $LogFile -Append

    Log "==> Running test suite as a safety gate before deploying"
    Push-Location webapp
    & $Python -m pytest tests\ -q 2>&1 | Tee-Object -FilePath $LogFile -Append
    $testsExitCode = $LASTEXITCODE
    Pop-Location

    if ($testsExitCode -ne 0) {
        Log "==> Tests FAILED (exit $testsExitCode) -- refusing to deploy a broken model. Check $LogFile"
        exit 1
    }

    Log "==> Tests passed -- deploying to EC2"
    & $Bash deploy/sync.sh data 2>&1 | Tee-Object -FilePath $LogFile -Append

    Log "==> Done"
}
catch {
    Log "==> FAILED: $_"
    exit 1
}
