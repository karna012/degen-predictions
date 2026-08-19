# Weekly full retrain on THIS machine (never on the small EC2 box).
# Covers: soccer (all leagues), MLB, NPB, NBA, NHL, cricket, tennis, crypto + stocks.
# Slim model stack: calibrated XGBoost (+ logistic/ridge), LSTM only if it
# clearly beats tabular IC. Then pytest gate + promote-if-better + sync.
#
# Schedule: Sunday 03:00 via deploy/register_retrain_task.ps1
# Manual:   powershell -File deploy/retrain_and_deploy.ps1
#
# EC2 still runs the cheap settle/feedback loop every 3h (no model train).

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Python = "$RepoRoot\venv_train\Scripts\python.exe"
$Bash = "C:\Program Files\Git\bin\bash.exe"
$InvestmentRoot = Join-Path (Split-Path -Parent $RepoRoot) "investment_scoring"
$InvestmentPython = "$InvestmentRoot\venv\Scripts\python.exe"
$LogDir = "$RepoRoot\deploy\logs"
$ScorePath = "$RepoRoot\data\feedback\last_promoted_model_score.json"
$Ec2Host = "ubuntu@52.201.204.110"
$Key = "$RepoRoot\deploy\degen-predictions-key.pem"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path "$RepoRoot\data\feedback" | Out-Null
$LogFile = "$LogDir\retrain_$(Get-Date -Format 'yyyy-MM-dd_HHmmss').log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Output $line
    Add-Content -Path $LogFile -Value $line
}

function Run-Py {
    param(
        [string]$Exe,
        [string[]]$PyArgs,
        [string]$WorkDir = $RepoRoot
    )
    Push-Location $WorkDir
    try {
        & $Exe @PyArgs 2>&1 | Tee-Object -FilePath $LogFile -Append
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed (exit $LASTEXITCODE): $Exe $($PyArgs -join ' ')"
        }
    } finally {
        Pop-Location
    }
}

function Get-EnsembleHoldoutAcc([string]$JoblibPath) {
    if (-not (Test-Path $JoblibPath)) { return $null }
    $py = @"
import joblib
m = joblib.load(r'$JoblibPath')
acc = None
if isinstance(m, dict):
    acc = m.get('holdout_accuracy', m.get('ensemble_holdout_accuracy', m.get('test_accuracy')))
print('' if acc is None else acc)
"@
    $out = & $Python -c $py 2>$null
    if (-not $out) { return $null }
    try { return [double]$out.Trim() } catch { return $null }
}

try {
    Log "==> Weekly full retrain starting (soccer + baseball + NBA + NHL + cricket + tennis + crypto/stocks)"
    Log "==> Rule: only ship models that beat (or tie) the previous holdout -- never ship a regression"

    Log "==> Pulling EC2 feedback baseline (informational)"
    try {
        & scp -i $Key "${Ec2Host}:~/Degen_predictions/data/feedback/latest.json" `
            "$RepoRoot\data\feedback\ec2_latest.json" 2>&1 | Out-Null
    } catch {
        Log "    (no EC2 latest.json yet -- continuing)"
    }

    Log "==> Backing up current models (promote-if-better baseline)"
    Run-Py $Python @("scripts\promote_gate.py", "backup")

    # ---- Soccer (all configured leagues) ----
    Log "==> Soccer: download + features + train + predict"
    Run-Py $Python @("download_data.py") "$RepoRoot\scripts"
    Run-Py $Python @("build_features.py") "$RepoRoot\scripts"
    Run-Py $Python @("train_models.py") "$RepoRoot\scripts"
    Run-Py $Python @("predict_pl.py") "$RepoRoot\scripts"
    if (Test-Path "$RepoRoot\scripts\calibrate_confidence.py") {
        Log "==> Soccer: recalibrate confidence thresholds"
        Run-Py $Python @("calibrate_confidence.py") "$RepoRoot\scripts"
    }

    # ---- MLB ----
    Log "==> MLB: refresh + Elo + train"
    Run-Py $Python @("scripts\mlb\download_current_season_espn.py")
    & $Python scripts\mlb\build_elo.py 2>&1 | Out-Null
    Run-Py $Python @("scripts\mlb\build_training_dataset.py")
    Run-Py $Python @("scripts\mlb\train_ml_models.py")

    # ---- NPB ----
    Log "==> NPB: refresh + Elo + train"
    Run-Py $Python @("scripts\npb\download_npb_data.py")
    & $Python scripts\npb\build_elo.py 2>&1 | Out-Null
    Run-Py $Python @("scripts\npb\build_training_dataset.py")
    Run-Py $Python @("scripts\npb\train_ml_models.py")

    # ---- NBA ----
    Log "==> NBA: refresh + Elo + train"
    Run-Py $Python @("download_nba_data.py") "$RepoRoot\scripts\nba"
    & $Python scripts\nba\build_elo.py 2>&1 | Out-Null
    Run-Py $Python @("build_training_dataset.py") "$RepoRoot\scripts\nba"
    Run-Py $Python @("train_ml_models.py") "$RepoRoot\scripts\nba"

    # ---- NHL ----
    Log "==> NHL: refresh + Elo + train"
    Run-Py $Python @("download_nhl_data.py") "$RepoRoot\scripts\nhl"
    & $Python scripts\nhl\build_elo.py 2>&1 | Out-Null
    Run-Py $Python @("build_training_dataset.py") "$RepoRoot\scripts\nhl"
    Run-Py $Python @("train_ml_models.py") "$RepoRoot\scripts\nhl"

    if (Test-Path "$RepoRoot\scripts\calibrate_sports_confidence.py") {
        Log "==> NBA/NHL: recalibrate confidence thresholds"
        Run-Py $Python @("scripts\calibrate_sports_confidence.py", "nba", "nhl") "$RepoRoot"
    }

    # ---- Cricket (Hundred / MLC / IPL / BBL / CPL) ----
    Log "==> Cricket: download + Elo + train"
    Run-Py $Python @("download_data.py") "$RepoRoot\scripts\cricket"
    Run-Py $Python @("build_elo.py") "$RepoRoot\scripts\cricket"
    Run-Py $Python @("build_training_dataset.py") "$RepoRoot\scripts\cricket"
    Run-Py $Python @("train_ml_models.py") "$RepoRoot\scripts\cricket"

    # ---- Tennis (ATP men) ----
    Log "==> Tennis ATP: download + Elo + train"
    Run-Py $Python @("download_data.py") "$RepoRoot\scripts\tennis"
    Run-Py $Python @("build_elo.py") "$RepoRoot\scripts\tennis"
    Run-Py $Python @("build_training_dataset.py") "$RepoRoot\scripts\tennis"
    Run-Py $Python @("train_ml_models.py") "$RepoRoot\scripts\tennis"
    if (Test-Path "$RepoRoot\scripts\calibrate_sports_confidence.py") {
        Log "==> Tennis: recalibrate confidence thresholds"
        Run-Py $Python @("scripts\calibrate_sports_confidence.py", "tennis") "$RepoRoot"
    }

    # ---- Crypto + stocks ----
    if ((Test-Path $InvestmentPython) -and (Test-Path $InvestmentRoot)) {
        Log "==> Investment: multi-horizon stocks"
        Run-Py $InvestmentPython @("train_multi_horizon_stocks.py") $InvestmentRoot
        Log "==> Investment: multi-horizon crypto"
        Run-Py $InvestmentPython @("train_multi_horizon_crypto.py") $InvestmentRoot
        Log "==> Investment: sentiment + logos"
        Run-Py $InvestmentPython @("fetch_sentiment.py") $InvestmentRoot
        Run-Py $InvestmentPython @("fetch_logos.py") $InvestmentRoot

        $Dest = "$RepoRoot\data\investment_scoring"
        New-Item -ItemType Directory -Force -Path $Dest | Out-Null
        Copy-Item "$InvestmentRoot\output\ranked_assets.csv" $Dest -Force -ErrorAction SilentlyContinue
        Copy-Item "$InvestmentRoot\output\crypto_history_span.json" $Dest -Force -ErrorAction SilentlyContinue
        Copy-Item "$InvestmentRoot\output\crypto_horizon_model_metrics.json" $Dest -Force -ErrorAction SilentlyContinue
        Log "==> Investment outputs copied to data/investment_scoring"
    } else {
        Log "==> SKIP investment_scoring (venv or repo missing at $InvestmentRoot)"
    }

    Log "==> Promote-if-better: roll back any category that got worse"
    & $Python scripts\promote_gate.py apply 2>&1 | Tee-Object -FilePath $LogFile -Append
    $gateExit = $LASTEXITCODE
    if ($gateExit -ne 0) {
        Log "==> Promote gate blocked deploy (exit $gateExit) -- EC2 unchanged, worse models restored locally"
        exit 2
    }

    Log "==> Running test suite as a safety gate before deploying"
    Push-Location webapp
    & $Python -m pytest tests\ -q 2>&1 | Tee-Object -FilePath $LogFile -Append
    $testsExitCode = $LASTEXITCODE
    Pop-Location

    if ($testsExitCode -ne 0) {
        Log "==> Tests FAILED (exit $testsExitCode) -- refusing to deploy. Check $LogFile"
        exit 1
    }

    Log "==> Tests passed -- deploying data + model artifacts to EC2"
    & $Bash deploy/sync.sh data model 2>&1 | Tee-Object -FilePath $LogFile -Append

    $newAcc = Get-EnsembleHoldoutAcc "$RepoRoot\data\mlb\ml_ensemble_model.joblib"
    if ($null -ne $newAcc) {
        @{
            mlb_holdout_accuracy = $newAcc
            promoted_at = (Get-Date).ToUniversalTime().ToString("o")
            scope = "soccer+mlb+npb+nba+nhl+cricket+tennis+investment"
            promote_gate = "per-category holdout (no regressions shipped)"
        } | ConvertTo-Json | Set-Content -Path $ScorePath -Encoding utf8
        Log "==> Recorded promoted score $newAcc -> $ScorePath"
    }

    Log "==> Weekly full retrain done (only equal-or-better models shipped)"
}
catch {
    Log "==> FAILED: $_"
    exit 1
}
