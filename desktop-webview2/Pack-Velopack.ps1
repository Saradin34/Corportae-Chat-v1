<#
  Build and package Corporate Chat WebView2 with Velopack.

  First release:
    powershell -ExecutionPolicy Bypass -File .\Pack-Velopack.ps1 -Version 2.0.0

  Next release must use a higher version, for example:
    powershell -ExecutionPolicy Bypass -File .\Pack-Velopack.ps1 -Version 2.0.1
#>
param(
    [Parameter(Mandatory=$true)][string]$Version,
    [string]$Runtime = "win-x64",
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dotnet = Join-Path $Root ".dotnet\dotnet.exe"
if (!(Test-Path $Dotnet)) { $Dotnet = "dotnet" }
$PublishDir = Join-Path $Root "dist\CorporateChat-WebView2"
$ReleasesDir = Join-Path $Root "releases"
$Project = Join-Path $Root "CorporateChatWebView2\CorporateChatWebView2.csproj"
$Icon = Join-Path $Root "CorporateChatWebView2\icon.ico"

if (!$NoBuild) {
    powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "Build-Desktop.ps1") -SelfContained -Runtime $Runtime
}

if (!(Test-Path (Join-Path $PublishDir "CorporateChat.exe"))) {
    throw "CorporateChat.exe not found in $PublishDir"
}

# Local dotnet tool manifest keeps vpk inside this folder, no global install required.
if (!(Test-Path (Join-Path $Root ".config\dotnet-tools.json"))) {
    & $Dotnet new tool-manifest --force --output $Root
}

$tools = & $Dotnet tool list --tool-manifest (Join-Path $Root ".config\dotnet-tools.json")
if ($tools -notmatch "\bvpk\b") {
    & $Dotnet tool install vpk --tool-manifest (Join-Path $Root ".config\dotnet-tools.json")
}

if (!(Test-Path $ReleasesDir)) { New-Item -ItemType Directory -Path $ReleasesDir -Force | Out-Null }

Write-Host "Packing Velopack release $Version..." -ForegroundColor Cyan
& $Dotnet tool run vpk pack `
    -u CorporateChat `
    -v $Version `
    -p $PublishDir `
    -e CorporateChat.exe `
    -o $ReleasesDir `
    --icon $Icon

if ($LASTEXITCODE -ne 0) { throw "vpk pack failed" }

Write-Host ""
Write-Host "Done. Release files:" -ForegroundColor Green
Get-ChildItem $ReleasesDir | Sort-Object LastWriteTime | Format-Table Name, Length, LastWriteTime
Write-Host ""
Write-Host "Upload ALL files from:" -ForegroundColor Yellow
Write-Host "  $ReleasesDir"
Write-Host "to server folder:"
Write-Host "  ~/Corportae-Chat-v1/desktop-updates"
