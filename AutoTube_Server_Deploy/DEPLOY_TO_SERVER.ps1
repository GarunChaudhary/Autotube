# AutoTube AI - copy the bundle in this folder to your Linux server and install / update it.
# Start it by double-clicking DEPLOY_TO_SERVER.bat (same folder).
#
# First install : unpacks everything (app + your settings, database and YouTube login) and runs the installer.
# Update        : if AutoTube is already on the server, only the app code is replaced. The server's own
#                 database, settings (.env) and YouTube login are kept, so nothing made there is lost.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$bundle = Join-Path $here "autotube_server_bundle.tar.gz"
if (-not (Test-Path $bundle)) { Write-Host "autotube_server_bundle.tar.gz is missing from $here" -ForegroundColor Red; exit 1 }

Write-Host "`n=== AutoTube -> server ===" -ForegroundColor Cyan
$ip   = Read-Host "Server IP address or hostname"
$user = Read-Host "SSH user name (press Enter for 'ubuntu')"
if (-not $user) { $user = "ubuntu" }
$key  = Read-Host "Path to your SSH private key file (press Enter if you log in with a password)"
$key  = $key.Trim('"')

$sshArgs = @()
if ($key) {
    if (-not (Test-Path $key)) { Write-Host "Key file not found: $key" -ForegroundColor Red; exit 1 }
    $sshArgs += @("-i", $key)
}
$sshArgs += @("-o", "StrictHostKeyChecking=accept-new")
$target = "$user@$ip"

Write-Host "`nChecking the server..." -ForegroundColor Cyan
$existing = & ssh @sshArgs $target "test -f ~/AutoTube/data/autotube.db && echo EXISTS || echo NEW"
if ($LASTEXITCODE -ne 0) { Write-Host "Could not connect with SSH. Check the IP, user and key." -ForegroundColor Red; exit 1 }
$update = ($existing -match "EXISTS")
if ($update) {
    Write-Host "AutoTube is already installed there -> UPDATE (server database, settings and YouTube login are kept)." -ForegroundColor Yellow
} else {
    Write-Host "No AutoTube on the server yet -> FIRST INSTALL." -ForegroundColor Yellow
}
$ok = Read-Host "Continue? (y/n)"
if ($ok -ne "y") { exit 0 }

Write-Host "`nUploading the bundle..." -ForegroundColor Cyan
& scp @sshArgs $bundle "${target}:~/autotube_server_bundle.tar.gz"
if ($LASTEXITCODE -ne 0) { Write-Host "Upload failed." -ForegroundColor Red; exit 1 }

if ($update) {
    $remote = "cd ~ && tar xzf autotube_server_bundle.tar.gz --exclude='AutoTube/data' --exclude='AutoTube/.env' " +
              "&& rm autotube_server_bundle.tar.gz && cd AutoTube && bash deploy/install_server.sh"
} else {
    $remote = "cd ~ && tar xzf autotube_server_bundle.tar.gz && rm autotube_server_bundle.tar.gz " +
              "&& cd AutoTube && bash deploy/install_server.sh"
}
Write-Host "`nInstalling on the server (first install takes 30-40 minutes, an update a few minutes)..." -ForegroundColor Cyan
Write-Host "If it shows a Tailscale login link, open it and log in with the same account as on your PC/phone.`n"
& ssh -t @sshArgs $target $remote
if ($LASTEXITCODE -ne 0) {
    Write-Host "`nThe installer reported a problem (see the messages above)." -ForegroundColor Red
    Write-Host "Server log:  ssh $target  then  journalctl -u autotube -n 80"
    exit 1
}

Write-Host "`nDone. Open http://autotube on any device signed in to your Tailscale account." -ForegroundColor Green
if (-not $update) {
    Write-Host "IMPORTANT: now stop AutoTube on this PC (STOP_AUTOTUBE.bat) so videos are not uploaded twice." -ForegroundColor Yellow
}
