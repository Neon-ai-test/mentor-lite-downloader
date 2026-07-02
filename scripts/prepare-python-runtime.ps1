param(
    [string]$PythonRuntimeDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $root ".runtime"
$targetDir = Join-Path $runtimeDir "python"

function Write-Step([string]$Message) {
    Write-Host "[MENTOR-LITE] $Message" -ForegroundColor Cyan
}

function Get-PythonRuntimeInfo([string]$Exe, [string[]]$PrefixArgs = @()) {
    try {
        $output = & $Exe @PrefixArgs -c "import sys, venv; print(sys.base_prefix); print(sys.version_info.major); print(sys.version_info.minor)"
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
        $exe = Join-Path $resolved "python.exe"
        if (-not (Test-Path $exe)) {
            throw "PythonRuntimeDir does not contain python.exe: $resolved"
        }
        $info = Get-PythonRuntimeInfo $exe
        if (-not $info) {
            throw "PythonRuntimeDir is not a usable Python 3.11+ runtime: $resolved"
        }
        return $resolved
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

    throw "No Python 3.11+ runtime found. Pass -PythonRuntimeDir with a full Python directory."
}

$sourceDir = Resolve-PythonRuntimeDir
$resolvedRoot = (Resolve-Path $root).Path
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
if (Test-Path $targetDir) {
    $resolvedTarget = (Resolve-Path $targetDir).Path
    if (-not $resolvedTarget.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a directory outside the tool root: $resolvedTarget"
    }
    Remove-Item -LiteralPath $targetDir -Recurse -Force
}

Write-Step "Copying Python runtime from $sourceDir"
Copy-Item -Path $sourceDir -Destination $targetDir -Recurse -Force

$targetPython = Join-Path $targetDir "python.exe"
& $targetPython -c "import sys, venv; print(sys.version)"
if ($LASTEXITCODE -ne 0) {
    throw "Bundled Python runtime validation failed: $targetPython"
}

Write-Host "[MENTOR-LITE] Bundled Python runtime is ready: $targetPython" -ForegroundColor Green
