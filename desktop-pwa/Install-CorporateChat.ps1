<#
  Corporate Chat PWA/Edge-app installer.
  Creates shortcuts that open https://chat.kupava.by in a separate Microsoft Edge app window.
  Does not require Rust, Visual Studio, Tauri or Electron.
#>

param(
    [string]$AppName = "Corporate Chat",
    [string]$Url = "https://chat.kupava.by",
    [switch]$AllUsers,
    [switch]$NoDesktopShortcut,
    [switch]$NoStartMenuShortcut,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"

function Find-Edge {
    $candidates = @(
        "$env:ProgramFiles(x86)\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe"
    )
    foreach ($p in $candidates) {
        if ($p -and (Test-Path $p)) { return $p }
    }

    $cmd = Get-Command msedge.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    throw "Microsoft Edge was not found. Install Edge or check msedge.exe path."
}

function New-Shortcut {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$TargetPath,
        [Parameter(Mandatory=$true)][string]$Arguments,
        [string]$WorkingDirectory,
        [string]$IconLocation,
        [string]$Description
    )

    $dir = Split-Path -Parent $Path
    if (!(Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    $wsh = New-Object -ComObject WScript.Shell
    $shortcut = $wsh.CreateShortcut($Path)
    $shortcut.TargetPath = $TargetPath
    $shortcut.Arguments = $Arguments
    if ($WorkingDirectory) { $shortcut.WorkingDirectory = $WorkingDirectory }
    if ($IconLocation) { $shortcut.IconLocation = $IconLocation }
    if ($Description) { $shortcut.Description = $Description }
    $shortcut.Save()
}

$edge = Find-Edge
$edgeDir = Split-Path -Parent $edge

# --app opens a standalone Edge app window. We do NOT use a separate user-data-dir
# so SSO/Kerberos, cookies, certificates and permissions work like normal Edge.
$args = "--app=$Url"
$desc = "Open $AppName ($Url)"
$icon = "$edge,0"

if ($AllUsers) {
    $desktop = [Environment]::GetFolderPath("CommonDesktopDirectory")
    $startMenu = Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs"
} else {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $startMenu = [Environment]::GetFolderPath("Programs")
}

$created = @()

if (!$NoDesktopShortcut) {
    $desktopLnk = Join-Path $desktop "$AppName.lnk"
    New-Shortcut -Path $desktopLnk -TargetPath $edge -Arguments $args -WorkingDirectory $edgeDir -IconLocation $icon -Description $desc
    $created += $desktopLnk
}

if (!$NoStartMenuShortcut) {
    $startDir = Join-Path $startMenu $AppName
    $startLnk = Join-Path $startDir "$AppName.lnk"
    New-Shortcut -Path $startLnk -TargetPath $edge -Arguments $args -WorkingDirectory $edgeDir -IconLocation $icon -Description $desc
    $created += $startLnk
}

Write-Host "Done. Created shortcuts:" -ForegroundColor Green
$created | ForEach-Object { Write-Host "  $_" }
Write-Host ""
Write-Host "The app opens in a separate Edge window:" -ForegroundColor Cyan
Write-Host "  $Url"

if (!$NoLaunch) {
    Start-Process -FilePath $edge -ArgumentList $args
}
