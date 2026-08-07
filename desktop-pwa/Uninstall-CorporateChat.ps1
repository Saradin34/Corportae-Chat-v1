<#
  Removes Corporate Chat shortcuts created by Install-CorporateChat.ps1.
  Does not remove Edge or browser user data.
#>

param(
    [string]$AppName = "Corporate Chat",
    [switch]$AllUsers
)

$ErrorActionPreference = "SilentlyContinue"

$paths = @()

if ($AllUsers) {
    $paths += Join-Path ([Environment]::GetFolderPath("CommonDesktopDirectory")) "$AppName.lnk"
    $paths += Join-Path (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\$AppName") "$AppName.lnk"
    $paths += Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\$AppName"
} else {
    $paths += Join-Path ([Environment]::GetFolderPath("Desktop")) "$AppName.lnk"
    $paths += Join-Path (Join-Path ([Environment]::GetFolderPath("Programs")) $AppName) "$AppName.lnk"
    $paths += Join-Path ([Environment]::GetFolderPath("Programs")) $AppName
}

foreach ($p in $paths) {
    if (Test-Path $p) {
        Remove-Item $p -Force -Recurse
        Write-Host "Removed: $p"
    }
}

Write-Host "Done."
