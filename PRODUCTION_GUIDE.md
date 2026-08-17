# Degen Predictions - Production Deployment Guide

## Quick Start

### Option 1: Windows Batch File (Recommended for Windows)
```batch
run_production.bat
```

### Option 2: Windows PowerShell
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.\run_production.ps1
```

### Option 3: Manual Command
```bash
gunicorn -w 4 -b 0.0.0.0:5000 wsgi:app
```

Or with config file:
```bash
gunicorn -c gunicorn_config.py wsgi:app
```

## What's Been Set Up

✅ **gunicorn** - Production WSGI server
✅ **wsgi.py** - Entry point for gunicorn
✅ **gunicorn_config.py** - Production configuration
✅ **run_production.ps1** - PowerShell launcher
✅ **run_production.bat** - Batch file launcher
✅ **requirements.txt** - Updated with gunicorn

## Server Details

- **Address**: 0.0.0.0:5000 (all network interfaces)
- **Workers**: Auto-detected based on CPU cores (usually 3-4)
- **Timeout**: 120 seconds
- **Max Requests**: 1000 per worker (auto-restart)

## Logs

Logs are created in the `logs/` directory:
- `gunicorn_access.log` - HTTP request logs
- `gunicorn_error.log` - Server errors

## Stopping the Server

Press `Ctrl+C` in the terminal where the server is running.

## Production Notes

1. **Deploy Behind Reverse Proxy** - Use nginx or Apache as a reverse proxy for better security and performance
2. **SSL/TLS** - Use nginx/Apache to handle HTTPS
3. **Process Manager** - Use systemd (Linux) or NSSM (Windows) to keep server running on restart
4. **Environment Variables** - Create a `.env` file for sensitive config
5. **Monitoring** - Monitor logs for errors and performance issues

## Restart on Reboot (Windows)

To run automatically on system restart, use **Task Scheduler** or **NSSM**:

### Using NSSM (Non-Sucking Service Manager)
```bash
nssm install DegenPredictions "C:\Python314\python.exe" "-m gunicorn -c gunicorn_config.py wsgi:app"
nssm start DegenPredictions
```

## Testing

After starting the server:
```bash
Invoke-WebRequest http://localhost:5000/ -UseBasicParsing
```

Should return HTTP 200 OK.
