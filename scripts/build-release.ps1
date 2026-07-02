param(
    [string]$OutputDir = "release",
    [string]$PythonRuntimeDir = "",
    [switch]$BundleRuntime,
    [switch]$NoPythonRuntime,
    [switch]$AllowOnlineInstall
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$releaseRoot = Join-Path $root $OutputDir
$staging = Join-Path $releaseRoot "mentor-lite-downloader"
$zip = Join-Path $releaseRoot "mentor-lite-downloader.zip"

if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Force -Path $staging | Out-Null

function Write-Step([string]$Message) {
    Write-Host "[MENTOR-LITE] $Message" -ForegroundColor Cyan
}

function Get-PythonRuntimeInfo([string]$Exe, [string[]]$PrefixArgs = @()) {
    try {
        $output = & $Exe @PrefixArgs -c "import sys; print(sys.base_prefix); print(sys.version_info.major); print(sys.version_info.minor)"
        if ($LASTEXITCODE -ne 0 -or $output.Count -lt 3) { return $null }
        $major = [int]$output[1]
        $minor = [int]$output[2]
        if ($major -ne 3 -or $minor -lt 11) { return $null }
        return @{
            Root = [string]$output[0]
            Version = "$major.$minor"
        }
    }
    catch {
        return $null
    }
}

function Resolve-PythonRuntimeDir {
    if ($PythonRuntimeDir) {
        $resolved = (Resolve-Path $PythonRuntimeDir).Path
        if (-not (Test-Path (Join-Path $resolved "python.exe"))) {
            throw "PythonRuntimeDir does not contain python.exe: $resolved"
        }
        return $resolved
    }

    $existingBundled = Join-Path $root ".runtime\python"
    if (Test-Path (Join-Path $existingBundled "python.exe")) {
        return (Resolve-Path $existingBundled).Path
    }

    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $info = Get-PythonRuntimeInfo $pyLauncher.Source @("-3")
        if ($info) { return $info.Root }
    }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        $info = Get-PythonRuntimeInfo $python.Source
        if ($info) { return $info.Root }
    }

    throw "No Python 3.11+ runtime found for packaging. Install Python 3.11+ or pass -PythonRuntimeDir."
}

function Copy-RuntimeDirectory([string]$Name, [string]$RequiredFilePattern, [string]$MissingMessage) {
    $source = Join-Path $root ".runtime\$Name"
    if (-not (Test-Path $source)) {
        if ($AllowOnlineInstall) { return }
        throw $MissingMessage
    }
    $required = Get-ChildItem -Path $source -Recurse -File -Filter $RequiredFilePattern -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $required) {
        if ($AllowOnlineInstall) { return }
        throw $MissingMessage
    }
    $targetParent = Join-Path $staging ".runtime"
    $target = Join-Path $targetParent $Name
    New-Item -ItemType Directory -Force -Path $targetParent | Out-Null
    Write-Step "Bundling .runtime\$Name"
    Copy-Item -Path $source -Destination $target -Recurse -Force
}

$include = @(
    ".gitignore",
    "config",
    "pyproject.toml",
    "README.md",
    "start.cmd",
    "stop.cmd",
    "scripts",
    "src",
    "static"
)
foreach ($item in $include) {
    $source = Join-Path $root $item
    if (Test-Path $source) {
        Copy-Item -Path $source -Destination $staging -Recurse -Force
    }
}

if ($BundleRuntime) {
    if (-not $NoPythonRuntime) {
        $runtimeSource = Resolve-PythonRuntimeDir
        $runtimeTargetParent = Join-Path $staging ".runtime"
        $runtimeTarget = Join-Path $runtimeTargetParent "python"
        New-Item -ItemType Directory -Force -Path $runtimeTargetParent | Out-Null
        Write-Step "Bundling Python runtime from $runtimeSource"
        Copy-Item -Path $runtimeSource -Destination $runtimeTarget -Recurse -Force
    }

    Copy-RuntimeDirectory "wheels" "*.whl" "Missing .runtime\wheels. Run scripts\prepare-offline-deps.ps1 before building an offline release, or pass -AllowOnlineInstall."
    Copy-RuntimeDirectory "playwright" "chrome.exe" "Missing .runtime\playwright Chromium. Run scripts\prepare-offline-deps.ps1 before building an offline release, or pass -AllowOnlineInstall."
}
else {
    Write-Step "Building online bootstrap package; dependencies will be downloaded on first startup"
}

if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $zip
Write-Host "[MENTOR-LITE] Release package: $zip" -ForegroundColor Green
