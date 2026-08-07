# Applies Kerberos-only SSO Docker dependency fixes to the local project copy.
# Run from the project root:  .\fix_kerberos_sso_patch.ps1

$ErrorActionPreference = 'Stop'

$dockerfile = Join-Path $PSScriptRoot 'backend\Dockerfile'
$req = Join-Path $PSScriptRoot 'backend\requirements.txt'

if (!(Test-Path $dockerfile)) { throw "Dockerfile not found: $dockerfile" }
if (!(Test-Path $req)) { throw "requirements.txt not found: $req" }

$d = Get-Content $dockerfile -Raw

# Ensure DEBIAN_FRONTEND is present in ENV block.
if ($d -notmatch 'DEBIAN_FRONTEND=noninteractive') {
    $d = $d -replace 'PIP_NO_CACHE_DIR=1', 'PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive'
}

# Ensure Kerberos tools and build deps are installed.
$d = $d -replace 'curl libkrb5-dev(\s*\\)', 'curl libkrb5-dev krb5-user gcc python3-dev$1'

Set-Content $dockerfile $d -Encoding UTF8

$r = Get-Content $req -Raw
$r = $r -replace 'pyspnego==0\.11\.1', 'pyspnego[kerberos]==0.11.1'
if ($r -notmatch 'pyspnego\[kerberos\]==0\.11\.1') {
    $r = $r.TrimEnd() + "`npyspnego[kerberos]==0.11.1`n"
}
Set-Content $req $r -Encoding UTF8

Write-Host 'Patch applied. Verify:' -ForegroundColor Green
Write-Host '  findstr /C:"krb5-user" backend\Dockerfile'
Write-Host '  findstr /C:"pyspnego[kerberos]" backend\requirements.txt'
Write-Host 'Then rebuild:' -ForegroundColor Yellow
Write-Host '  docker compose build --no-cache backend'
Write-Host '  docker compose up -d backend nginx'
