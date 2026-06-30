param(
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $root ".runtime"
$pidFile = Join-Path $runtimeDir "server.pid"

function Get-ServerHealth([int]$TargetPort) {
    try {
        return Invoke-RestMethod -UseBasicParsing -Uri "http://127.0.0.1:$TargetPort/api/health" -TimeoutSec 2
    }
    catch {
        return $null
    }
}

function Get-PortProcessId([int]$TargetPort) {
    try {
        $connection = Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($connection) { return [int]$connection.OwningProcess }
    }
    catch {
        return $null
    }
    return $null
}

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

$health = Get-ServerHealth $Port
if ($health -and $health.root) {
    $serverRoot = ""
    try { $serverRoot = (Resolve-Path ([string]$health.root)).Path } catch { $serverRoot = [string]$health.root }
    $localRoot = (Resolve-Path $root).Path
    if ($serverRoot -eq $localRoot) {
        $portPid = Get-PortProcessId $Port
        if ($portPid) {
            Write-Host "[MENTOR-LITE] Stopping server process $portPid on port $Port..." -ForegroundColor Yellow
            Stop-Process -Id $portPid -Force
        }
    }
}

Write-Host "[MENTOR-LITE] Stopped." -ForegroundColor Green
