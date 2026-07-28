@echo off
REM Regenerates upcoming-fixture predictions for all 5 leagues.
REM Run manually or via the "DegenPredictions Daily Refresh" scheduled task.
cd /d "%~dp0\.."
"football_preds\Scripts\python.exe" scripts\predict_pl.py >> daily_predict.log 2>&1
echo ---- %date% %time% ---- >> daily_predict.log
