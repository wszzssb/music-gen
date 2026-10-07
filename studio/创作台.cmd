@echo off
rem ============================================================
rem  BGM Studio 创作台 —— 桌面窗口版（双击即用）
rem  用 pywebview + 系统 WebView2 开一个原生窗口：没有地址栏、没有标签页，
rem  任务栏上是它自己的标题，**不经过 Edge**。关窗即退出（自己起的服务会一起停）。
rem  出错看日志：%TEMP%\bgm-studio-desktop.log
rem  想要浏览器窗口版（Edge 应用模式）就双击「创作台-浏览器窗口版.cmd」
rem ============================================================
setlocal
set "HERE=%~dp0"
set "PYW=%HERE%..\.venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
if not exist "%HERE%desktop_app.py" (
  echo [失败] 找不到 desktop_app.py（应在 %HERE%）
  pause
  goto :eof
)
start "" "%PYW%" "%HERE%desktop_app.py"
endlocal
