param(
    [string]$AppVersion = "1.0.0",
    [string]$SourceDir = "dist\Unshuffle",
    [string]$OutputDir = "dist\installer"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$sourcePath = Resolve-Path (Join-Path $repoRoot $SourceDir)
$outputPath = Join-Path $repoRoot $OutputDir
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null

$iscc = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
if ($null -eq $iscc) {
    throw "Inno Setup compiler ISCC.exe was not found on PATH."
}

& $iscc.Source `
    "/DAppVersion=$AppVersion" `
    "/DSourceDir=$sourcePath" `
    "/DOutputDir=$outputPath" `
    (Join-Path $repoRoot "packaging\windows\unshuffle.iss")

