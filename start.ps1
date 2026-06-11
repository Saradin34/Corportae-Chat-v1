#Requires -Version 5.1
<#
.SYNOPSIS
    Corporate Chat startup script for Windows + Docker Desktop.

.DESCRIPTION
    Handles docker-compose build + run, fixes krb5 file permissions,
    and verifies Kerberos keytab is usable for SSO.
#>
$ErrorActionPreference = "Stop"

Write-Host "=== Corporate Chat v2.0 Startup ===" -ForegroundColor Cyan

# --- Check prerequisites ---
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker not found in PATH. Install Docker Desktop first."
    exit 1
}

$hasKeytab = Test-Path ".\krb5.keytab"
$hasConf   = Test-Path ".\krb5.conf"

if (-not $hasKeytab) {
    Write-Warning "krb5.keytab NOT found in $(Get-Location)."
    Write-Warning "  SSO (Kerberos/NTLM Negotiate) will FAIL until you place the keytab here."
    Write-Warning "  Reverse-proxy SSO (via nginx/IIS) will still work."
}

# --- Stop old containers if running ---
Write-Host "`nStopping old containers..." -ForegroundColor DarkGray
docker compose down | Out-Null

# --- Build & start (force no-cache for backend to pick up new code) ---
Write-Host "Building and starting containers..." -ForegroundColor DarkGray

# If user has run setup before, backend image may be cached with old code.
# We force a rebuild of the backend image to ensure latest changes.
docker compose build --no-cache backend | Out-Null

docker compose up -d --build

# --- Backend health check ---
Start-Sleep -Seconds 3
$backendRunning = docker ps -q -f name=cc_backend
if (-not $backendRunning) {
    Write-Error "Backend container did not start. Check 'docker logs cc_backend'."
    exit 1
}

# --- Ensure krb5 files exist and have correct permissions ---
if ($hasKeytab) {
    Write-Host "`nChecking Kerberos keytab permissions..." -ForegroundColor Yellow

    # Check if file exists inside container
    $exists = docker exec cc_backend test -f /etc/krb5.keytab 2>$null; $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  -> Copying keytab via docker cp..." -ForegroundColor Yellow
        docker cp ".\krb5.keytab" cc_backend:/etc/krb5.keytab
    }

    # Fix permissions (MIT Kerberos requires 600)
    docker exec cc_backend chmod 600 /etc/krb5.keytab 2>$null | Out-Null
    Write-Host "  -> Fixed keytab permissions (chmod 600)." -ForegroundColor Green

    # Verify
    $perms = docker exec cc_backend ls -la /etc/krb5.keytab 2>$null
    Write-Host "  -> $perms" -ForegroundColor DarkGray

    # Check SPN in keytab
    Write-Host "  -> Checking keytab contents..." -ForegroundColor Yellow
    docker exec cc_backend klist -k /etc/krb5.keytab 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  -> Installing krb5-user to inspect keytab..." -ForegroundColor Yellow
        docker exec cc_backend sh -c "DEBIAN_FRONTEND=noninteractive apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq krb5-user" 2>$null | Out-Null
    }
    docker exec cc_backend klist -k /etc/krb5.keytab 2>$null | ForEach-Object {
        Write-Host "     $_" -ForegroundColor DarkGray
    }
}

if ($hasConf) {
    $confExists = docker exec cc_backend test -f /etc/krb5.conf 2>$null; $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  -> Copying krb5.conf via docker cp..." -ForegroundColor Yellow
        docker cp ".\krb5.conf" cc_backend:/etc/krb5.conf
    }
}

# --- Health check ---
Write-Host "`nWaiting for backend health check..." -ForegroundColor DarkGray
$healthy = $false
for ($i = 1; $i -le 30; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost/api/health" -UseBasicParsing -ErrorAction Stop -TimeoutSec 2
        if ($resp.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch { }
    Start-Sleep -Seconds 1
}

if (-not $healthy) {
    Write-Error "Backend is not responding. Run: docker logs -f cc_backend"
    exit 1
}

# --- Summary ---
Write-Host "`n=== Corporate Chat is READY ===" -ForegroundColor Green
Write-Host "  URL:       http://localhost" -ForegroundColor Green
Write-Host "  API docs:  http://localhost/api/docs" -ForegroundColor Green
Write-Host "  Admin:     http://localhost (login admin / Admin12345!)" -ForegroundColor Green

if ($hasKeytab) {
    Write-Host "  SSO:       Kerberos keytab configured (/etc/krb5.keytab)" -ForegroundColor Green
} else {
    Write-Host "  SSO:       No keytab found. Only reverse-proxy or LDAP login works." -ForegroundColor Yellow
}

Write-Host "`nPress any key to stop the containers, or close this window to keep them running." -ForegroundColor Cyan
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
Write-Host "Shutting down..." -ForegroundColor DarkGray
docker compose down
