@echo off
REM Double-click to copy AutoTube to your Linux server and install or update it.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0DEPLOY_TO_SERVER.ps1"
pause
