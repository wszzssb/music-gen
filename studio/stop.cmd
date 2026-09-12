@echo off
rem Stop BGM Studio (kill the process listening on PORT)
setlocal
set PORT=8765
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
  echo Stopping PID %%p ...
  taskkill /F /T /PID %%p >nul 2>&1
)
echo Done. (no output above = it was not running)
