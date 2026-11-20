@echo off
REM ==========================================================
REM  AutoTube AI - one-click launcher
REM ==========================================================
setlocal
cd /d "%~dp0"
title AutoTube AI
REM "silent" = started by Windows at login (no browser, no pauses)
set "SILENT="
if /i "%~1"=="silent" set "SILENT=1"
if not exist "data\logs" mkdir "data\logs"

if not exist ".venv\Scripts\python.exe" (
  echo [AutoTube] First run: installing dependencies. This takes several minutes...
  powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup.ps1"
  if errorlevel 1 ( echo Setup failed. See messages above. & pause & exit /b 1 )
)

if not exist ".env" copy ".env.example" ".env" >nul

REM --- start Ollama (local AI) if it is not already running ---
powershell -NoProfile -Command "try { Invoke-WebRequest http://127.0.0.1:11434/api/tags -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  set "OLLAMA_EXE=%LOCALAPPDATA%\AutoTube\ollama\ollama.exe"
  if exist "%LOCALAPPDATA%\AutoTube\ollama\ollama.exe" (
    echo [AutoTube] Starting Ollama...
    start "" /min "%LOCALAPPDATA%\AutoTube\ollama\ollama.exe" serve
  ) else (
    where ollama >nul 2>nul && start "" /min ollama serve
  )
)

REM --- build dashboard if missing ---
if not exist "frontend\out\index.html" (
  echo [AutoTube] Building dashboard...
  pushd frontend
  call npm install --no-audit --no-fund && call npm run build
  popd
)

REM --- already running? just open the browser ---
powershell -NoProfile -Command "try { Invoke-WebRequest http://127.0.0.1:8000/api/ping -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if not errorlevel 1 (
  echo [AutoTube] Already running.
  if not defined SILENT call :open_dashboard
  exit /b 0
)

echo [AutoTube] Starting backend on http://localhost:8000 ...
start "AutoTube backend" /min cmd /c ".venv\Scripts\python.exe backend\run.py >> data\logs\server.log 2>&1"

powershell -NoProfile -Command "for($i=0;$i -lt 60;$i++){ try { Invoke-WebRequest http://127.0.0.1:8000/api/ping -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { Start-Sleep 1 } }; exit 1"
if errorlevel 1 (
  echo [AutoTube] Backend did not start. Check data\logs\server.log
  if not defined SILENT pause
  exit /b 1
)
if not defined SILENT call :open_dashboard
echo [AutoTube] Running. Dashboard: http://localhost:8000   (stop with STOP_AUTOTUBE.bat)
if not defined SILENT timeout /t 5 >nul
exit /b 0

REM --- open the dashboard in Chrome or Edge. Never Internet Explorer: Google sign-in fails there
REM     (error 400 at accounts.google.../SetSID) and the dashboard needs a modern browser.
:open_dashboard
set "BROWSER="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set "BROWSER=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not defined BROWSER start "" http://localhost:8000
if defined BROWSER start "" "%BROWSER%" http://localhost:8000
exit /b 0
