$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $root ".runtime"
$pidFile = Join-Path $runtimeDir "server.pid"

if (Test-Path $pidFile) {
    $pidValue = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($pidValue) {
        $process = Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
        if ($process) {
            Write-Host "[MENTOR-LITE] Stopping server process $pidValue..." -ForegroundColor Yellow
            Stop-Process -Id $process.Id -Force
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

Write-Host "[MENTOR-LITE] Stopped." -ForegroundColor Green
