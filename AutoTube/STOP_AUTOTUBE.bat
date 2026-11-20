@echo off
REM Stops the AutoTube backend (and optionally Ollama). Videos in progress resume on next start.
cd /d "%~dp0"
if exist "data\autotube.pid" (
  set /p PID=<data\autotube.pid
  call :kill
) else (
  echo [AutoTube] No PID file - looking for the backend process...
  powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*backend\run.py*' } | % { Stop-Process -Id $_.ProcessId -Force; 'stopped ' + $_.ProcessId }"
)
choice /c YN /n /t 10 /d N /m "Also stop Ollama (local AI)? [y/N] "
if errorlevel 2 goto :end
taskkill /im ollama.exe /f >nul 2>nul
taskkill /im "ollama app.exe" /f >nul 2>nul
echo [AutoTube] Ollama stopped.
:end
echo [AutoTube] Stopped.
exit /b 0

:kill
taskkill /pid %PID% /t /f >nul 2>nul && echo [AutoTube] Backend stopped (pid %PID%).
del /q "data\autotube.pid" >nul 2>nul
exit /b 0
