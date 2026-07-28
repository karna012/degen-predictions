# Weekly crypto/stocks (investment_scoring) retrain, run on THIS machine
# (same reasoning as retrain_and_deploy.ps1 for MLB/NPB -- never on EC2's
# 1GB-RAM t3.micro). Refreshes price/fundamentals data (via investment_scoring's
# own on-disk cache -- see src/data_ingestion/cache.py -- so a same-day rerun
# is fast), retrains the full model, re-applies sentiment/logos, copies the
# output into the webapp, runs the test suite as a safety gate, and only
# deploys to EC2 if everything still passes.
#
# Scheduled via Windows Task Scheduler (see deploy/register_investment_retrain_task.ps1
# to install it) -- can also be run by hand any time:
#   powershell -File deploy/retrain_investment_scoring.ps1
#
# Weekly (not daily like MLB/NPB) because the 6-month forward-return horizon
# means day-to-day retraining wouldn't meaningfully change predictions, and
# yfinance/CoinGecko rate limits make a full 500+ ticker pull slow -- no
# reason to pay that cost more often than the target horizon calls for.

$ErrorActionPreference = "Stop"
$DegenRoot = Split-Path -Parent $PSScriptRoot
$InvestmentRoot = Join-Path (Split-Path -Parent $DegenRoot) "investment_scoring"
$LogDir = "$DegenRoot\deploy\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = "$LogDir\investment_retrain_$(Get-Date -Format 'yyyy-MM-dd_HHmmss').log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Output $line
    Add-Content -Path $LogFile -Value $line
}

$InvestmentPython = "$InvestmentRoot\venv\Scripts\python.exe"
$DegenPython = "$DegenRoot\venv_train\Scripts\python.exe"
$Bash = "C:\Program Files\Git\bin\bash.exe"

try {
    Log "==> Retraining investment_scoring (crypto/US stocks/India stocks)"
    Push-Location $InvestmentRoot
    & $InvestmentPython main.py 2>&1 | Tee-Object -FilePath $LogFile -Append
    if ($LASTEXITCODE -ne 0) { throw "main.py failed (exit $LASTEXITCODE)" }

    Log "==> Adding sentiment scores"
    & $InvestmentPython fetch_sentiment.py 2>&1 | Tee-Object -FilePath $LogFile -Append

    Log "==> Adding logos"
    & $InvestmentPython fetch_logos.py 2>&1 | Tee-Object -FilePath $LogFile -Append
    Pop-Location

    Log "==> Copying output into webapp"
    $Dest = "$DegenRoot\webapp\data\investment_scoring\ranked_assets.csv"
    New-Item -ItemType Directory -Force -Path (Split-Path $Dest) | Out-Null
    Copy-Item "$InvestmentRoot\output\ranked_assets.csv" $Dest -Force

    Log "==> Running test suite as a safety gate before deploying"
    Push-Location "$DegenRoot\webapp"
    & $DegenPython -m pytest tests\ -q 2>&1 | Tee-Object -FilePath $LogFile -Append
    $testsExitCode = $LASTEXITCODE
    Pop-Location

    if ($testsExitCode -ne 0) {
        Log "==> Tests FAILED (exit $testsExitCode) -- refusing to deploy a broken model. Check $LogFile"
        exit 1
    }

    Log "==> Tests passed -- deploying to EC2"
    Push-Location $DegenRoot
    & $Bash deploy/sync.sh data 2>&1 | Tee-Object -FilePath $LogFile -Append
    Pop-Location

    Log "==> Done"
}
catch {
    Log "==> FAILED: $_"
    exit 1
}
