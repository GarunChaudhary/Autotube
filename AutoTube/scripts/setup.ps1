# AutoTube AI - dependency installer. No administrator rights required.
# Installs (per-user, under %LOCALAPPDATA%\AutoTube): Python 3.12, FFmpeg, Ollama + model, Piper voices,
# then the Python virtualenv and the dashboard build. Safe to re-run: existing installs are skipped.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = "Tls12"
$Root = Split-Path -Parent $PSScriptRoot
$Tools = Join-Path $env:LOCALAPPDATA "AutoTube"
$Dl = Join-Path $env:TEMP "autotube_dl"
New-Item -ItemType Directory -Force $Tools, $Dl | Out-Null
function Step($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }

# ---------- Python ----------
Step "Python"
$py = (Get-Command python -ErrorAction SilentlyContinue | ? { $_.Source -notlike "*WindowsApps*" }).Source
$pyPkg = Join-Path $Tools "python_pkg\tools\python.exe"
if (Test-Path $pyPkg) { $py = $pyPkg }
if (-not $py) {
  Write-Host "Installing Python 3.12 (official python.org NuGet package, no admin needed)..."
  Invoke-WebRequest "https://www.nuget.org/api/v2/package/python/3.12.10" -OutFile "$Dl\python.zip" -UseBasicParsing
  Expand-Archive "$Dl\python.zip" -DestinationPath (Join-Path $Tools "python_pkg") -Force
  $py = $pyPkg
}
& $py --version

# ---------- FFmpeg ----------
Step "FFmpeg"
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
$ffLocal = Get-ChildItem (Join-Path $Tools "ffmpeg") -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | Select -First 1
if (-not $ff -and -not $ffLocal) {
  Write-Host "Downloading portable FFmpeg (BtbN GPL build)..."
  Invoke-WebRequest "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" -OutFile "$Dl\ffmpeg.zip" -UseBasicParsing
  Expand-Archive "$Dl\ffmpeg.zip" -DestinationPath (Join-Path $Tools "ffmpeg") -Force
  Remove-Item "$Dl\ffmpeg.zip"
}
Write-Host "FFmpeg OK"

# ---------- Ollama ----------
Step "Ollama (local AI)"
$ollama = Join-Path $Tools "ollama\ollama.exe"
if (-not (Get-Command ollama -ErrorAction SilentlyContinue) -and -not (Test-Path $ollama)) {
  $rel = Invoke-RestMethod "https://api.github.com/repos/ollama/ollama/releases/latest" -UseBasicParsing
  $asset = $rel.assets | ? { $_.name -eq "ollama-windows-amd64.zip" } | Select -First 1
  Write-Host "Downloading Ollama $($rel.tag_name) (~1.6 GB, GPU libraries removed afterwards if no NVIDIA/AMD GPU)..."
  Invoke-WebRequest $asset.browser_download_url -OutFile "$Dl\ollama.zip" -UseBasicParsing
  Expand-Archive "$Dl\ollama.zip" -DestinationPath (Join-Path $Tools "ollama") -Force
  Remove-Item "$Dl\ollama.zip"
  $gpu = (Get-CimInstance Win32_VideoController).Name -join " "
  if ($gpu -notmatch "NVIDIA|AMD|Radeon") {
    Get-ChildItem (Join-Path $Tools "ollama\lib\ollama") -Directory | ? { $_.Name -match "cuda|rocm" } | Remove-Item -Recurse -Force
  }
}
if (Get-Command ollama -ErrorAction SilentlyContinue) { $ollama = (Get-Command ollama).Source }
try { Invoke-WebRequest http://127.0.0.1:11434/api/tags -UseBasicParsing -TimeoutSec 2 | Out-Null }
catch { Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden; Start-Sleep 5 }
$model = "qwen2.5:3b"
$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) { $m = Select-String -Path $envFile -Pattern "^OLLAMA_MODEL=(.+)$"; if ($m) { $model = $m.Matches[0].Groups[1].Value.Trim() } }
Write-Host "Pulling model $model (~2 GB, fact-checking / ideas)..."
& $ollama pull $model
$writer = "qwen2.5:7b"
if (Test-Path $envFile) { $m = Select-String -Path $envFile -Pattern "^OLLAMA_MODEL_WRITER=(.+)$"; if ($m) { $writer = $m.Matches[0].Groups[1].Value.Trim() } }
Write-Host "Pulling writer model $writer (~4.7 GB, scripts / SEO)..."
& $ollama pull $writer

# ---------- Python env ----------
Step "Python packages"
Set-Location $Root
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $py -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip -q
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# ---------- Voices ----------
Step "Piper voices"
New-Item -ItemType Directory -Force "data\voices" | Out-Null
if (-not (Test-Path "data\voices\en_US-lessac-medium.onnx")) {
  & .\.venv\Scripts\python.exe -m piper.download_voices --data-dir data\voices en_US-lessac-medium en_US-ryan-high
}
New-Item -ItemType Directory -Force "data\voices\kokoro" | Out-Null
$kb = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
foreach ($f in "kokoro-v1.0.onnx", "voices-v1.0.bin") {
  if (-not (Test-Path "data\voices\kokoro\$f")) {
    Write-Host "Downloading Kokoro narration model: $f"
    Invoke-WebRequest "$kb/$f" -OutFile "data\voices\kokoro\$f" -UseBasicParsing
  }
}

# ---------- Database ----------
Step "Database migrations"
& .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'backend'); from app import db; print('applied', db.migrate())"

# ---------- Dashboard ----------
Step "Dashboard (Next.js)"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  Write-Warning "Node.js/npm not found. Install Node.js LTS from https://nodejs.org (per-user zip works without admin)."
} else {
  Push-Location frontend
  $env:NEXT_TELEMETRY_DISABLED = "1"
  npm install --no-audit --no-fund
  npm run build
  Pop-Location
}
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
Write-Host "`nSetup complete. Run START_AUTOTUBE.bat" -ForegroundColor Green
