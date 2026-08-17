#!/usr/bin/env pwsh
# Production startup script for Degen Predictions using Waitress
# For Windows PowerShell

$webappDir = "f:\Api_claude_projects\pro2\Degen_predictions\webapp"
$venvDir = "f:\Api_claude_projects\pro2\Degen_predictions\venv_train"
$pythonExe = "$venvDir\Scripts\python.exe"

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "Degen Predictions - Production Server (Waitress)" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $pythonExe)) {
    Write-Host "Error: Python executable not found at:" -ForegroundColor Red
    Write-Host $pythonExe -ForegroundColor Red
    Write-Host ""
    Write-Host "Please check the paths in this script." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

Set-Location $webappDir

Write-Host "Starting server on http://0.0.0.0:5000" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""

# Run the production server
& $pythonExe serve.py
