<#
  Lightweight Corporate Chat desktop builder based on .NET WinForms + WebView2.
  No Electron. No Rust. No Visual Studio Build Tools required.

  If a .NET SDK is not installed, this script downloads a local .NET 8 SDK into:
    desktop-webview2\.dotnet

  Examples:
    powershell -ExecutionPolicy Bypass -File .\Build-Desktop.ps1
    powershell -ExecutionPolicy Bypass -File .\Build-Desktop.ps1 -SelfContained
#>
param(
    [switch]$SelfContained,
    [string]$Runtime = "win-x64"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Project = Join-Path $Root "CorporateChatWebView2\CorporateChatWebView2.csproj"
$Out = Join-Path $Root "dist\CorporateChat-WebView2"

function Test-DotNetSdk($dotnetExe) {
    try {
        $sdks = & $dotnetExe --list-sdks 2>$null
        return ($LASTEXITCODE -eq 0 -and $sdks -and ($sdks | Select-String -Pattern "^8\." -Quiet))
    } catch {
        return $false
    }
}

function Download-File($Url, $OutFile) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try {
        Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
        return
    } catch {
        Write-Host "Invoke-WebRequest failed, trying curl.exe..." -ForegroundColor Yellow
    }
    & curl.exe -L $Url -o $OutFile
    if ($LASTEXITCODE -ne 0) { throw "Failed to download $Url" }
}

function Ensure-DotNetSdk {
    $localDotnet = Join-Path $Root ".dotnet\dotnet.exe"
    if ((Test-Path $localDotnet) -and (Test-DotNetSdk $localDotnet)) {
        return $localDotnet
    }

    $cmd = Get-Command dotnet -ErrorAction SilentlyContinue
    if ($cmd -and (Test-DotNetSdk $cmd.Source)) {
        return $cmd.Source
    }

    Write-Host "No .NET 8 SDK found. Downloading local .NET SDK into .dotnet ..." -ForegroundColor Yellow
    $dotnetDir = Join-Path $Root ".dotnet"
    New-Item -ItemType Directory -Path $dotnetDir -Force | Out-Null

    $installer = Join-Path $Root "dotnet-install.ps1"
    if (!(Test-Path $installer)) {
        Download-File "https://dot.net/v1/dotnet-install.ps1" $installer
    }

    powershell -NoProfile -ExecutionPolicy Bypass -File $installer -InstallDir $dotnetDir -Channel 8.0 -Architecture x64
    if ($LASTEXITCODE -ne 0) { throw "dotnet-install.ps1 failed" }

    if (!(Test-DotNetSdk $localDotnet)) {
        throw "Local .NET SDK installation failed or SDK 8.x was not found in $dotnetDir"
    }

    return $localDotnet
}

$dotnet = Ensure-DotNetSdk

Write-Host "Using dotnet:" -ForegroundColor Green
Write-Host "  $dotnet"
& $dotnet --info

if (Test-Path $Out) { Remove-Item $Out -Recurse -Force }
New-Item -ItemType Directory -Path $Out -Force | Out-Null

$self = if ($SelfContained) { "true" } else { "false" }
Write-Host ""
Write-Host "Building Corporate Chat WebView2 desktop..." -ForegroundColor Cyan
Write-Host "SelfContained: $self"

Write-Host ""
Write-Host "Restoring packages..." -ForegroundColor Cyan
& $dotnet restore "$Project"
if ($LASTEXITCODE -ne 0) { throw "dotnet restore failed" }

Write-Host ""
Write-Host "Publishing app..." -ForegroundColor Cyan
& $dotnet publish "$Project" `
    -c Release `
    -r "$Runtime" `
    --self-contained:$self `
    -p:PublishSingleFile=true `
    -p:IncludeNativeLibrariesForSelfExtract=true `
    -o "$Out"
if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed" }

$exe = Join-Path $Out "CorporateChat.exe"
if (!(Test-Path $exe)) {
    throw "Build finished but EXE was not found: $exe"
}

$zipDir = Join-Path $Root "dist"
New-Item -ItemType Directory -Path $zipDir -Force | Out-Null
$zip = Join-Path $zipDir "CorporateChat-WebView2-$Runtime.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $Out "*") -DestinationPath $zip -Force

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "EXE: $exe"
Write-Host "ZIP: $zip"
