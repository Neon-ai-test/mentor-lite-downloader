param(
    [string]$OutputDir = "release"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$releaseRoot = Join-Path $root $OutputDir
$staging = Join-Path $releaseRoot "mentor-lite-downloader"
$zip = Join-Path $releaseRoot "mentor-lite-downloader.zip"

if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Force -Path $staging | Out-Null

$include = @(
    ".gitignore",
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

if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $zip
Write-Host "[MENTOR-LITE] Release package: $zip" -ForegroundColor Green
