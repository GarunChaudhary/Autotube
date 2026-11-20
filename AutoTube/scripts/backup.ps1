# Backs up the database, settings, encrypted token and logs to data\backups\autotube-backup-<timestamp>.zip
# Rendered videos are NOT included by default (large). Use -IncludeVideos to add them.
param([switch]$IncludeVideos)
$Root = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$tmp = Join-Path $env:TEMP "autotube-backup-$stamp"
New-Item -ItemType Directory -Force $tmp | Out-Null
$py = Join-Path $Root ".venv\Scripts\python.exe"
# consistent SQLite snapshot even while the app is running
& $py -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()" `
  (Join-Path $Root "data\autotube.db") (Join-Path $tmp "autotube.db")
Copy-Item (Join-Path $Root ".env") $tmp -ErrorAction SilentlyContinue
Copy-Item (Join-Path $Root "data\secrets") (Join-Path $tmp "secrets") -Recurse -ErrorAction SilentlyContinue
Copy-Item (Join-Path $Root "data\logs") (Join-Path $tmp "logs") -Recurse -ErrorAction SilentlyContinue
if ($IncludeVideos) { Copy-Item (Join-Path $Root "data\videos") (Join-Path $tmp "videos") -Recurse }
$dest = Join-Path $Root "data\backups\autotube-backup-$stamp.zip"
Compress-Archive -Path "$tmp\*" -DestinationPath $dest
Remove-Item $tmp -Recurse -Force
Write-Host "Backup written: $dest"
Write-Host "NOTE: the YouTube token is encrypted with a key in Windows Credential Manager on THIS PC; on a new PC just reconnect YouTube."
