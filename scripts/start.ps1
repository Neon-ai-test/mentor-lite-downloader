param(
    [switch]$NoOpen,
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $root ".runtime"
$bundledPythonExe = Join-Path $runtimeDir "python\python.exe"
$bundledPythonDir = Split-Path -Parent $bundledPythonExe
$venvDir = Join-Path $runtimeDir "venv"
$pythonExe = Join-Path $venvDir "Scripts\python.exe"
$wheelDir = Join-Path $runtimeDir "wheels"
$pipCache = Join-Path $runtimeDir "pip-cache"
$browserDir = Join-Path $runtimeDir "playwright"
$installerDir = Join-Path $runtimeDir "installers"
$logDir = Join-Path $runtimeDir "logs"
$pidFile = Join-Path $runtimeDir "server.pid"
$outLog = Join-Path $logDir "server.stdout.log"
$errLog = Join-Path $logDir "server.stderr.log"
$recordedPythonExe = $null
$pythonBootstrapVersion = if ($env:MENTOR_LITE_PYTHON_VERSION) { $env:MENTOR_LITE_PYTHON_VERSION } else { "3.12.13" }
$downloadTimeoutSeconds = if ($env:MENTOR_LITE_DOWNLOAD_TIMEOUT_SECONDS) { [int]$env:MENTOR_LITE_DOWNLOAD_TIMEOUT_SECONDS } else { 90 }
$pipTimeoutSeconds = if ($env:MENTOR_LITE_PIP_TIMEOUT_SECONDS) { [int]$env:MENTOR_LITE_PIP_TIMEOUT_SECONDS } else { 45 }
$pythonInstallerUrls = if ($env:MENTOR_LITE_PYTHON_INSTALLER_URLS) {
    $env:MENTOR_LITE_PYTHON_INSTALLER_URLS -split "[;,\r\n]+" | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() }
}
elseif ($env:MENTOR_LITE_PYTHON_INSTALLER_URL) {
    @($env:MENTOR_LITE_PYTHON_INSTALLER_URL)
}
else {
    @(
        "https://www.python.org/ftp/python/$pythonBootstrapVersion/python-$pythonBootstrapVersion-amd64.exe",
        "https://npmmirror.com/mirrors/python/$pythonBootstrapVersion/python-$pythonBootstrapVersion-amd64.exe",
        "https://registry.npmmirror.com/-/binary/python/$pythonBootstrapVersion/python-$pythonBootstrapVersion-amd64.exe",
        "https://mirrors.huaweicloud.com/python/$pythonBootstrapVersion/python-$pythonBootstrapVersion-amd64.exe",
        "https://mirrors.tuna.tsinghua.edu.cn/python/$pythonBootstrapVersion/python-$pythonBootstrapVersion-amd64.exe"
    )
}
$pythonInstallerPath = Join-Path $installerDir "python-$pythonBootstrapVersion-amd64.exe"
$pipIndexUrls = if ($env:MENTOR_LITE_PIP_INDEX_URLS) {
    $env:MENTOR_LITE_PIP_INDEX_URLS -split "[;,\r\n]+" | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() }
}
elseif ($env:PIP_INDEX_URL) {
    @($env:PIP_INDEX_URL)
}
else {
    @(
        "https://pypi.org/simple",
        "https://pypi.tuna.tsinghua.edu.cn/simple",
        "https://mirrors.aliyun.com/pypi/simple",
        "https://mirrors.cloud.tencent.com/pypi/simple",
        "https://repo.huaweicloud.com/repository/pypi/simple",
        "https://mirrors.ustc.edu.cn/pypi/simple"
    )
}
$playwrightDownloadHosts = if ($env:MENTOR_LITE_PLAYWRIGHT_DOWNLOAD_HOSTS) {
    $env:MENTOR_LITE_PLAYWRIGHT_DOWNLOAD_HOSTS -split "[;,\r\n]+" | ForEach-Object { $_.Trim() }
}
elseif ($env:PLAYWRIGHT_DOWNLOAD_HOST) {
    @($env:PLAYWRIGHT_DOWNLOAD_HOST)
}
else {
    @(
        "",
        "https://npmmirror.com/mirrors/playwright",
        "https://registry.npmmirror.com/-/binary/playwright"
    )
}

function Write-Step([string]$Message) {
    Write-Host "[MENTOR-LITE] $Message" -ForegroundColor Cyan
}

function Invoke-DownloadWithFallback([string[]]$Urls, [string]$Destination, [string]$Label) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    if (Test-Path $Destination) {
        Remove-Item -LiteralPath $Destination -Force
    }
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    }
    catch {}

    $errors = New-Object System.Collections.Generic.List[string]
    foreach ($url in $Urls) {
        if (-not $url) { continue }
        Write-Step "Downloading $Label from $url"
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $Destination -TimeoutSec $downloadTimeoutSeconds
            if ((Test-Path $Destination) -and ((Get-Item $Destination).Length -gt 0)) {
                return $url
            }
            $errors.Add("$url -> empty download")
        }
        catch {
            $errors.Add("$url -> $($_.Exception.Message)")
            if (Test-Path $Destination) {
                Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
            }
        }
    }
    throw "Failed to download $Label from all configured sources: $($errors -join ' | ')"
}

function Invoke-PipWithIndexFallback([string]$Description, [string[]]$Arguments) {
    $errors = New-Object System.Collections.Generic.List[string]
    foreach ($indexUrl in $pipIndexUrls) {
        if (-not $indexUrl) { continue }
        Write-Step "$Description via $indexUrl"
        & $pythonExe -m pip @Arguments --index-url $indexUrl --timeout $pipTimeoutSeconds --retries 2
        if ($LASTEXITCODE -eq 0) { return }
        $errors.Add("$indexUrl -> exit $LASTEXITCODE")
    }
    throw "$Description failed on all configured package indexes: $($errors -join ' | ')"
}

function Invoke-PlaywrightInstallWithFallback {
    $errors = New-Object System.Collections.Generic.List[string]
    foreach ($host in $playwrightDownloadHosts) {
        if ($host) {
            Write-Step "Installing Playwright Chromium via $host"
            $env:PLAYWRIGHT_DOWNLOAD_HOST = $host
        }
        else {
            Write-Step "Installing Playwright Chromium via default Playwright CDN"
            Remove-Item Env:\PLAYWRIGHT_DOWNLOAD_HOST -ErrorAction SilentlyContinue
        }
        & $pythonExe -m playwright install chromium
        if ($LASTEXITCODE -eq 0) {
            Remove-Item Env:\PLAYWRIGHT_DOWNLOAD_HOST -ErrorAction SilentlyContinue
            return
        }
        $label = if ($host) { $host } else { "default" }
        $errors.Add("$label -> exit $LASTEXITCODE")
    }
    Remove-Item Env:\PLAYWRIGHT_DOWNLOAD_HOST -ErrorAction SilentlyContinue
    throw "Playwright Chromium installation failed on all configured sources: $($errors -join ' | ')"
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

function New-BootstrapCandidate([string]$Label, [string]$Exe, [string[]]$Args = @()) {
    return [pscustomobject]@{
        Label = $Label
        Exe = $Exe
        Args = $Args
    }
}

function Get-BootstrapPythonCandidates {
    $candidates = @()
    if (Test-Path $bundledPythonExe) {
        $candidates += New-BootstrapCandidate "bundled Python" $bundledPythonExe
    }
    if ($recordedPythonExe -and (Test-Path $recordedPythonExe)) {
        $candidates += New-BootstrapCandidate "recorded Python" $recordedPythonExe
    }
    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $candidates += New-BootstrapCandidate "py launcher" $pyLauncher.Source @("-3")
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        $candidates += New-BootstrapCandidate "system python" $python.Source
    }
    return $candidates
}

function Test-BootstrapPythonCandidate($Candidate) {
    try {
        & $Candidate.Exe @($Candidate.Args) -c "import sys, venv; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Assert-PathInsideTool([string]$TargetPath) {
    $toolRoot = (Resolve-Path $root).Path
    $resolvedTarget = $TargetPath
    if (Test-Path $TargetPath) {
        $resolvedTarget = (Resolve-Path $TargetPath).Path
    }
    else {
        $resolvedTarget = [System.IO.Path]::GetFullPath($TargetPath)
    }
    if (-not $resolvedTarget.StartsWith($toolRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the tool root: $resolvedTarget"
    }
}

function Install-BundledPythonFromInternet {
    Write-Step "Bundled Python was not found; downloading Python $pythonBootstrapVersion"
    New-Item -ItemType Directory -Force -Path $installerDir | Out-Null
    if ((Test-Path $pythonInstallerPath) -and ((Get-Item $pythonInstallerPath).Length -lt 1048576)) {
        Remove-Item -LiteralPath $pythonInstallerPath -Force
    }
    if (-not (Test-Path $pythonInstallerPath)) {
        Invoke-DownloadWithFallback $pythonInstallerUrls $pythonInstallerPath "Python $pythonBootstrapVersion installer" | Out-Null
    }

    if (Test-Path $bundledPythonDir) {
        Assert-PathInsideTool $bundledPythonDir
        Remove-Item -LiteralPath $bundledPythonDir -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $bundledPythonDir | Out-Null

    Write-Step "Installing Python into .runtime\python"
    $arguments = @(
        "/quiet",
        "InstallAllUsers=0",
        "TargetDir=$bundledPythonDir",
        "Include_pip=1",
        "Include_launcher=0",
        "PrependPath=0",
        "Include_test=0",
        "Shortcuts=0",
        "AssociateFiles=0"
    )
    $process = Start-Process -FilePath $pythonInstallerPath -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "Python installer failed with exit code $($process.ExitCode)."
    }
    & $bundledPythonExe --version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Bundled Python validation failed after installation."
    }
}

function New-LocalPythonEnvironment {
    Write-Step "Creating local Python environment under .runtime\venv"
    foreach ($candidate in Get-BootstrapPythonCandidates) {
        if (-not (Test-BootstrapPythonCandidate $candidate)) {
            Write-Step "Skipping unusable $($candidate.Label)"
            continue
        }
        if (Test-Path $venvDir) {
            Assert-PathInsideTool $venvDir
            Remove-Item -LiteralPath $venvDir -Recurse -Force
        }
        Write-Step "Trying venv creation with $($candidate.Label)"
        & $candidate.Exe @($candidate.Args) -m venv $venvDir
        if ($LASTEXITCODE -eq 0 -and (Test-Path $pythonExe)) {
            return
        }
        Write-Step "Venv creation failed with $($candidate.Label); trying next Python source"
        if (Test-Path $venvDir) {
            Assert-PathInsideTool $venvDir
            Remove-Item -LiteralPath $venvDir -Recurse -Force
        }
    }

    Install-BundledPythonFromInternet
    $downloaded = New-BootstrapCandidate "downloaded bundled Python" $bundledPythonExe
    if (-not (Test-BootstrapPythonCandidate $downloaded)) {
        throw "Downloaded bundled Python is not usable."
    }
    if (Test-Path $venvDir) {
        Assert-PathInsideTool $venvDir
        Remove-Item -LiteralPath $venvDir -Recurse -Force
    }
    Write-Step "Trying venv creation with downloaded bundled Python"
    & $downloaded.Exe -m venv $venvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $pythonExe)) {
        throw "Python venv creation failed after trying all Python sources."
    }
}

function Test-LocalPythonEnvironment {
    if (-not (Test-Path $pythonExe)) { return $false }
    & $pythonExe --version *> $null
    return ($LASTEXITCODE -eq 0)
}

function Test-Wheelhouse {
    if (-not (Test-Path $wheelDir)) { return $false }
    $wheel = Get-ChildItem -Path $wheelDir -Filter "*.whl" -File -ErrorAction SilentlyContinue | Select-Object -First 1
    return $null -ne $wheel
}

function Test-PlaywrightChromium {
    if (-not (Test-Path $browserDir)) { return $false }
    $chrome = Get-ChildItem -Path $browserDir -Recurse -Filter "chrome.exe" -File -ErrorAction SilentlyContinue | Select-Object -First 1
    return $null -ne $chrome
}

New-Item -ItemType Directory -Force -Path $runtimeDir, $pipCache, $browserDir, $installerDir, $logDir | Out-Null
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
if (Test-Wheelhouse) {
    Write-Step "Using bundled Python wheels from .runtime\wheels"
    & $pythonExe -m pip install --disable-pip-version-check --no-index --find-links $wheelDir hatchling
    if ($LASTEXITCODE -ne 0) { throw "Offline hatchling installation failed." }
    & $pythonExe -m pip install --disable-pip-version-check --no-index --find-links $wheelDir --no-build-isolation $root
    if ($LASTEXITCODE -ne 0) { throw "Offline dependency installation failed." }
}
else {
    Write-Step "No bundled wheelhouse found; installing dependencies from Python package index"
    Invoke-PipWithIndexFallback "Upgrading pip" @("install", "--disable-pip-version-check", "--upgrade", "pip")
    Invoke-PipWithIndexFallback "Installing MENTOR Lite dependencies" @("install", "--disable-pip-version-check", "-e", $root)
}

if (Test-PlaywrightChromium) {
    Write-Step "Using bundled Playwright Chromium from .runtime\playwright"
}
else {
    Write-Step "Ensuring local Playwright Chromium is installed"
    Invoke-PlaywrightInstallWithFallback
}

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
