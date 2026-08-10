param(
    [string]$AppVersion = "1.1.0",
    [string]$SourceDir = "dist\Unshuffle",
    [string]$OutputDir = "dist\installer"
)

$ErrorActionPreference = "Stop"

$versionParts = @($AppVersion.Split("."))
while ($versionParts.Count -lt 4) {
    $versionParts += "0"
}
$appVersionInfo = ($versionParts[0..3] -join ".")

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
    "/DAppVersionInfo=$appVersionInfo" `
    "/DSourceDir=$sourcePath" `
    "/DOutputDir=$outputPath" `
    (Join-Path $repoRoot "packaging\windows\unshuffle.iss")

