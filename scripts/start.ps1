param(
    [switch]$NoOpen,
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $root ".runtime"
$venvDir = Join-Path $runtimeDir "venv"
$pythonExe = Join-Path $venvDir "Scripts\python.exe"
$pipCache = Join-Path $runtimeDir "pip-cache"
$browserDir = Join-Path $runtimeDir "playwright"
$logDir = Join-Path $runtimeDir "logs"
$pidFile = Join-Path $runtimeDir "server.pid"
$outLog = Join-Path $logDir "server.stdout.log"
$errLog = Join-Path $logDir "server.stderr.log"
$recordedPythonExe = $null

function Write-Step([string]$Message) {
    Write-Host "[MENTOR-LITE] $Message" -ForegroundColor Cyan
}

function Test-Server([int]$TargetPort) {
    return $null -ne (Get-ServerHealth $TargetPort)
}

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

function Stop-ExistingServer([int]$TargetPort) {
    $stopped = $false
    if (Test-Path $pidFile) {
        $pidValue = Get-Content $pidFile -ErrorAction SilentlyContinue
        if ($pidValue) {
            $process = Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
            if ($process) {
                Write-Step "Stopping existing local server process $pidValue"
                Stop-Process -Id $process.Id -Force
                $stopped = $true
            }
        }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }
    if (-not $stopped) {
        $portPid = Get-PortProcessId $TargetPort
        if ($portPid) {
            $process = Get-Process -Id $portPid -ErrorAction SilentlyContinue
            if ($process) {
                Write-Step "Stopping existing local server on port $TargetPort, process $portPid"
                Stop-Process -Id $process.Id -Force
                $stopped = $true
            }
        }
    }
    if ($stopped) {
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Date) -lt $deadline) {
            if (-not (Test-Server $TargetPort)) { break }
            Start-Sleep -Milliseconds 300
        }
    }
}

function Get-RecordedVenvPython {
    $configPath = Join-Path $venvDir "pyvenv.cfg"
    if (-not (Test-Path $configPath)) { return $null }
    foreach ($line in Get-Content $configPath -ErrorAction SilentlyContinue) {
        if ($line -match "^\s*executable\s*=\s*(.+?)\s*$") {
            $candidate = $Matches[1]
            if (Test-Path $candidate) { return $candidate }
        }
    }
    return $null
}

function New-LocalPythonEnvironment {
    Write-Step "Creating local Python environment under .runtime\venv"
    if ($recordedPythonExe -and (Test-Path $recordedPythonExe)) {
        try {
            & $recordedPythonExe -m venv $venvDir
            if ($LASTEXITCODE -eq 0) { return }
        }
        catch {
            Write-Step "Recorded Python path is not executable, falling back to PATH lookup"
        }
    }
    $python = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($python) {
        & $python.Source -3 -m venv $venvDir
    }
    else {
        $python = Get-Command python.exe -ErrorAction Stop
        & $python.Source -m venv $venvDir
    }
    if ($LASTEXITCODE -ne 0) { throw "Python venv creation failed." }
}

function Test-LocalPythonEnvironment {
    if (-not (Test-Path $pythonExe)) { return $false }
    & $pythonExe --version *> $null
    return ($LASTEXITCODE -eq 0)
}

New-Item -ItemType Directory -Force -Path $runtimeDir, $pipCache, $browserDir, $logDir | Out-Null
$env:PIP_CACHE_DIR = $pipCache
$env:PLAYWRIGHT_BROWSERS_PATH = $browserDir
$env:MENTOR_LITE_ROOT = $root

$recordedPythonExe = Get-RecordedVenvPython
if (-not (Test-LocalPythonEnvironment)) {
    if (Test-Path $venvDir) {
        Write-Step "Recreating invalid local Python environment"
        Remove-Item -LiteralPath $venvDir -Recurse -Force
    }
    New-LocalPythonEnvironment
}

Write-Step "Installing tool dependencies into local .runtime\venv"
& $pythonExe -m pip install --disable-pip-version-check --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $pythonExe -m pip install --disable-pip-version-check -e $root
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

Write-Step "Ensuring local Playwright Chromium is installed"
& $pythonExe -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "Playwright Chromium installation failed." }

$health = Get-ServerHealth $Port
if ($health) {
    $serverRoot = ""
    if ($health.root) {
        try { $serverRoot = (Resolve-Path ([string]$health.root)).Path } catch { $serverRoot = [string]$health.root }
    }
    $localRoot = (Resolve-Path $root).Path
    if ($serverRoot -and $serverRoot -ne $localRoot) {
        throw "Port $Port is already used by another MENTOR Lite root: $serverRoot"
    }
    Stop-ExistingServer $Port
}

Write-Step "Starting local server on port $Port"
$process = Start-Process -FilePath $pythonExe `
    -ArgumentList @("-m", "mentor_lite.api", "--host", "127.0.0.1", "--port", "$Port") `
    -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog
Set-Content -Path $pidFile -Value $process.Id

$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    if (Test-Server $Port) { break }
    Start-Sleep -Milliseconds 800
}
if (-not (Test-Server $Port)) {
    if (Test-Path $errLog) { Get-Content $errLog -Tail 60 }
    throw "Server did not become ready. See $errLog"
}

$url = "http://127.0.0.1:$Port"
Write-Host "[MENTOR-LITE] Ready: $url" -ForegroundColor Green
if (-not $NoOpen) {
    Start-Process $url
}
