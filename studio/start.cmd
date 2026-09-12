@echo off
rem BGM Studio launcher -- keeps everything inside <music-gen>\studio (no absolute paths)
setlocal
set PORT=8765
set PY=%~dp0..\.venv\Scripts\python.exe
if not exist "%PY%" set PY=python
cd /d "%~dp0"

netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
  echo BGM Studio is already running: http://127.0.0.1:%PORT%/
  start "" http://127.0.0.1:%PORT%/
  goto :eof
)

echo Starting BGM Studio ...
echo   URL: http://127.0.0.1:%PORT%/   (close this window or press Ctrl+C to stop)
"%PY%" server.py --port %PORT% --open
endlocal
