@echo off
rem ============================================================
rem  BGM Studio 创作台 —— 浏览器窗口版（兜底）
rem  用 Edge 的「应用模式」开独立窗口：没有地址栏/标签页，但进程与任务栏图标仍是 Edge。
rem  正常情况请用「创作台.cmd」（原生窗口）。出问题看：%TEMP%\bgm-studio-launcher.log
rem ============================================================
setlocal
set "LOG=%TEMP%\bgm-studio-launcher.log"
echo [%date% %time%] 启动器开始（%~dp0） >> "%LOG%"

set "PORT=8765"
set "HERE=%~dp0"
set "PY=%HERE%..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
cd /d "%HERE%"

rem ① 服务已经在跑吗？（只看 LISTENING）
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [%date% %time%] 端口 %PORT% 已在监听，直接开窗口 >> "%LOG%"
  goto open
)

echo 正在启动 BGM Studio 服务（第一次启动要加载音源，请稍等）...
echo [%date% %time%] 起服务：%PY% server.py --port %PORT% >> "%LOG%"
start "BGM Studio 服务" /min "%PY%" "server.py" --port %PORT%

set /a n=0
:wait
set /a n+=1
timeout /t 1 /nobreak >nul
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto ready
if %n% lss 30 goto wait
echo [%date% %time%] 30 秒没等到端口就绪 >> "%LOG%"
echo.
echo [失败] 服务 30 秒内没起来。手动双击 studio\start.cmd 看报错，或看日志：
echo         %LOG%
pause
goto :eof

:ready
echo [%date% %time%] 端口就绪（等了 %n% 秒） >> "%LOG%"

:open
set "URL=http://127.0.0.1:%PORT%/create"
rem [坑] 这里不许用 for %%P in (...) 收集路径：%ProgramFiles(x86)% 展开后带 )，
rem   会把集合提前闭合 -> 语法错误 -> 整个批处理静默中止（退出码 0、无输出、不起服务）。
set "PF86=%ProgramFiles(x86)%"
set "PF=%ProgramFiles%"
set "LAD=%LocalAppData%"
set "EDGE="
if exist "%PF86%\Microsoft\Edge\Application\msedge.exe" set "EDGE=%PF86%\Microsoft\Edge\Application\msedge.exe"
if not defined EDGE if exist "%PF%\Microsoft\Edge\Application\msedge.exe" set "EDGE=%PF%\Microsoft\Edge\Application\msedge.exe"
if not defined EDGE if exist "%LAD%\Microsoft\Edge\Application\msedge.exe" set "EDGE=%LAD%\Microsoft\Edge\Application\msedge.exe"

if defined EDGE (
  echo [%date% %time%] 开 Edge 应用窗口 >> "%LOG%"
  start "" "%EDGE%" --app=%URL% --window-size=1320,900
) else (
  echo [%date% %time%] 没找到 Edge，退回默认浏览器 >> "%LOG%"
  start "" %URL%
)
endlocal
