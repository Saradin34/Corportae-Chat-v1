<#
  GPO Logon Script для пользователя.
  Разместите папку desktop-pwa в сетевой шаре, например:
    \\server\share\CorporateChatPWA\
  И укажите этот скрипт в:
    User Configuration → Windows Settings → Scripts → Logon
#>

$AppName = "Corporate Chat"
$Shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "$AppName.lnk"

# Если ярлык уже есть — ничего не делаем.
if (Test-Path $Shortcut) { exit 0 }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Installer = Join-Path $ScriptDir "Install-CorporateChat.ps1"

if (Test-Path $Installer) {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Installer -NoLaunch
}
