@echo off
REM Production startup script for Degen Predictions using Waitress
REM Compatible with Windows

setlocal enabledelayedexpansion

set WEBAPP_DIR=f:\Api_claude_projects\pro2\Degen_predictions\webapp
set VENV_DIR=f:\Api_claude_projects\pro2\Degen_predictions\venv_train
set PYTHON_EXE=%VENV_DIR%\Scripts\python.exe

echo.
echo ======================================================================
echo Degen Predictions - Production Server (Waitress)
echo ======================================================================
echo.

if not exist "%PYTHON_EXE%" (
    echo Error: Python executable not found at:
    echo %PYTHON_EXE%
    echo.
    echo Please check the paths in this script.
    pause
    exit /b 1
)

cd /d "%WEBAPP_DIR%"

echo Starting server on http://0.0.0.0:5000
echo Press Ctrl+C to stop
echo.

REM Run the production server
"%PYTHON_EXE%" serve.py

pause
