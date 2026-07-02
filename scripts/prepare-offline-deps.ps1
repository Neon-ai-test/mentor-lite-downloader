param(
    [string]$PythonRuntimeDir = "",
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $root ".runtime"
$pythonExe = Join-Path $runtimeDir "python\python.exe"
$buildVenvDir = Join-Path $runtimeDir "build-venv"
$buildPythonExe = Join-Path $buildVenvDir "Scripts\python.exe"
$wheelDir = Join-Path $runtimeDir "wheels"
$browserDir = Join-Path $runtimeDir "playwright"
$pipTimeoutSeconds = if ($env:MENTOR_LITE_PIP_TIMEOUT_SECONDS) { [int]$env:MENTOR_LITE_PIP_TIMEOUT_SECONDS } else { 45 }
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

function Invoke-BuildPipWithIndexFallback([string]$Description, [string[]]$Arguments) {
    $errors = New-Object System.Collections.Generic.List[string]
    foreach ($indexUrl in $pipIndexUrls) {
        if (-not $indexUrl) { continue }
        Write-Step "$Description via $indexUrl"
        $env:PIP_INDEX_URL = $indexUrl
        & $buildPythonExe -m pip @Arguments --index-url $indexUrl --timeout $pipTimeoutSeconds --retries 2
        if ($LASTEXITCODE -eq 0) {
            Remove-Item Env:\PIP_INDEX_URL -ErrorAction SilentlyContinue
            return
        }
        $errors.Add("$indexUrl -> exit $LASTEXITCODE")
    }
    Remove-Item Env:\PIP_INDEX_URL -ErrorAction SilentlyContinue
    throw "$Description failed on all configured package indexes: $($errors -join ' | ')"
}

function Invoke-BuildPlaywrightInstallWithFallback {
    $errors = New-Object System.Collections.Generic.List[string]
    foreach ($host in $playwrightDownloadHosts) {
        if ($host) {
            Write-Step "Preparing Playwright Chromium via $host"
            $env:PLAYWRIGHT_DOWNLOAD_HOST = $host
        }
        else {
            Write-Step "Preparing Playwright Chromium via default Playwright CDN"
            Remove-Item Env:\PLAYWRIGHT_DOWNLOAD_HOST -ErrorAction SilentlyContinue
        }
        $env:PLAYWRIGHT_BROWSERS_PATH = $browserDir
        & $buildPythonExe -m playwright install chromium
        if ($LASTEXITCODE -eq 0) {
            Remove-Item Env:\PLAYWRIGHT_DOWNLOAD_HOST -ErrorAction SilentlyContinue
            return
        }
        $label = if ($host) { $host } else { "default" }
        $errors.Add("$label -> exit $LASTEXITCODE")
    }
    Remove-Item Env:\PLAYWRIGHT_DOWNLOAD_HOST -ErrorAction SilentlyContinue
    throw "Failed to install Playwright Chromium from all configured sources: $($errors -join ' | ')"
}

if (-not (Test-Path $pythonExe)) {
    $preparePythonScript = Join-Path $PSScriptRoot "prepare-python-runtime.ps1"
    if ($PythonRuntimeDir) {
        & $preparePythonScript -PythonRuntimeDir $PythonRuntimeDir
    }
    else {
        & $preparePythonScript
    }
    if ($LASTEXITCODE -ne 0) { throw "Python runtime preparation failed." }
}

New-Item -ItemType Directory -Force -Path $runtimeDir, $wheelDir, $browserDir | Out-Null

if (-not (Test-Path $buildPythonExe)) {
    Write-Step "Creating build environment under .runtime\build-venv"
    & $pythonExe -m venv $buildVenvDir
    if ($LASTEXITCODE -ne 0) { throw "Build venv creation failed." }
}

Write-Step "Preparing wheelhouse under .runtime\wheels"
Invoke-BuildPipWithIndexFallback "Upgrading build pip" @("install", "--disable-pip-version-check", "--upgrade", "pip")
Invoke-BuildPipWithIndexFallback "Downloading hatchling wheel" @("download", "--disable-pip-version-check", "--dest", $wheelDir, "hatchling>=1.25")
Invoke-BuildPipWithIndexFallback "Downloading project dependency wheels" @("download", "--disable-pip-version-check", "--dest", $wheelDir, $root)

if (-not $SkipBrowser) {
    Write-Step "Preparing Playwright Chromium under .runtime\playwright"
    & $buildPythonExe -m pip install --disable-pip-version-check --no-index --find-links $wheelDir playwright
    if ($LASTEXITCODE -ne 0) { throw "Failed to install bundled playwright wheel into build venv." }
    Invoke-BuildPlaywrightInstallWithFallback
}

Write-Host "[MENTOR-LITE] Offline dependencies are ready." -ForegroundColor Green
