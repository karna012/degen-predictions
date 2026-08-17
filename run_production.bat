@echo off
REM Production startup script for Degen Predictions
REM Run this to start the production server with gunicorn

setlocal enabledelayedexpansion

set WEBAPP_DIR=f:\Api_claude_projects\pro2\Degen_predictions\webapp
set PORT=5000
set WORKERS=4

echo.
echo Starting Degen Predictions production server...
echo Listening on 0.0.0.0:%PORT% with %WORKERS% workers
echo Press Ctrl+C to stop
echo.

cd /d "%WEBAPP_DIR%"

REM Check if gunicorn is installed
pip list | findstr gunicorn >nul
if errorlevel 1 (
    echo Installing gunicorn...
    pip install gunicorn
)

REM Start gunicorn server
gunicorn -w %WORKERS% -b 0.0.0.0:%PORT% --timeout 120 --access-logfile - --error-logfile - app:app

pause
