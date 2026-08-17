# Daily Top-3 digest + optional auto-post (Windows)
# Register with Task Scheduler or run manually:
#   powershell -File deploy/daily_distribute.ps1
#   powershell -File deploy/daily_distribute.ps1 -DryRun

param(
    [switch]$DryRun,
    [switch]$ForceRepost,
    [switch]$UseCache
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$envFile = Join-Path $Root "deploy\production.env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { return }
        $parts = $line.Split("=", 2)
        $k = $parts[0].Trim()
        $v = $parts[1].Trim().Trim('"').Trim("'")
        if ($k -and -not [Environment]::GetEnvironmentVariable($k)) {
            [Environment]::SetEnvironmentVariable($k, $v, "Process")
        }
    }
}

$argsList = @()
if ($DryRun) { $argsList += "--dry-run" }
if ($ForceRepost) { $argsList += "--force-repost" }
if ($UseCache) { $argsList += "--use-cache" }
$argsList += "--print-share-text"

$logDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("daily_distribute_{0:yyyyMMdd}.log" -f (Get-Date).ToUniversalTime())

Write-Host "==> Daily distribute $(Get-Date -Format o)"
Push-Location (Join-Path $Root "webapp")
try {
    python daily_digest.py @argsList 2>&1 | Tee-Object -FilePath $log -Append
} finally {
    Pop-Location
}
