@echo off
REM Creates a temporary https link to the AutoTube dashboard for your phone (free Cloudflare quick tunnel).
REM The dashboard asks for your AutoTube username + password. Close this window to switch the link off.
title AutoTube phone link
set "CF=%LOCALAPPDATA%\AutoTube\cloudflared\cloudflared.exe"
if not exist "%CF%" (
  echo Downloading Cloudflare tunnel tool (one time, about 50 MB^)...
  powershell -NoProfile -Command "New-Item -ItemType Directory -Force (Split-Path '%CF%') | Out-Null; [Net.ServicePointManager]::SecurityProtocol='Tls12'; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile '%CF%' -UseBasicParsing"
)
powershell -NoProfile -Command "try { Invoke-WebRequest http://127.0.0.1:8000/api/ping -UseBasicParsing -TimeoutSec 3 | Out-Null } catch { exit 1 }"
if errorlevel 1 (
  echo AutoTube is not running - starting it...
  start "" /min cmd /c "%~dp0START_AUTOTUBE.bat" silent
  timeout /t 40 /nobreak >nul
)
echo.
echo ============================================================
echo  Your phone link appears below in a few seconds - look for:
echo      https://....trycloudflare.com
echo  Open it on your phone and log in. Keep this window open.
echo ============================================================
echo.
"%CF%" tunnel --no-autoupdate --url http://127.0.0.1:8000 2>&1 | findstr /i "trycloudflare.com error"
pause
