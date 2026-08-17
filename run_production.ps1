# Production startup script for Degen Predictions
# Run this to start the production server with gunicorn

$webappDir = "f:\Api_claude_projects\pro2\Degen_predictions\webapp"
$port = 5000
$workers = 4

Write-Host "Starting Degen Predictions production server..."
Write-Host "Listening on 0.0.0.0:$port with $workers workers"
Write-Host "Press Ctrl+C to stop"
Write-Host ""

Set-Location $webappDir

# Install gunicorn if not already installed
$gunicornCheck = pip list | findstr gunicorn
if (-not $gunicornCheck) {
    Write-Host "Installing gunicorn..."
    pip install gunicorn
}

# Start gunicorn server
gunicorn -w $workers -b 0.0.0.0:$port --timeout 120 --access-logfile - --error-logfile - app:app
