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

function Write-Step([string]$Message) {
    Write-Host "[MENTOR-LITE] $Message" -ForegroundColor Cyan
}

function Test-Server([int]$TargetPort) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$TargetPort/api/health" -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

New-Item -ItemType Directory -Force -Path $runtimeDir, $pipCache, $browserDir, $logDir | Out-Null
$env:PIP_CACHE_DIR = $pipCache
$env:PLAYWRIGHT_BROWSERS_PATH = $browserDir
$env:MENTOR_LITE_ROOT = $root

if (-not (Test-Path $pythonExe)) {
    Write-Step "Creating local Python environment under .runtime\venv"
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

Write-Step "Installing tool dependencies into local .runtime\venv"
& $pythonExe -m pip install --disable-pip-version-check --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $pythonExe -m pip install --disable-pip-version-check -e $root
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

Write-Step "Ensuring local Playwright Chromium is installed"
& $pythonExe -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "Playwright Chromium installation failed." }

if (Test-Server $Port) {
    Write-Host "[MENTOR-LITE] Server already running: http://127.0.0.1:$Port" -ForegroundColor Green
}
else {
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
}

$url = "http://127.0.0.1:$Port"
Write-Host "[MENTOR-LITE] Ready: $url" -ForegroundColor Green
if (-not $NoOpen) {
    Start-Process $url
}
