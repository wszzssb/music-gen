@echo off
rem 本文件用 GBK 编码 + CRLF 保存（cmd.exe 按系统代码页解析批处理；改成 UTF-8 会报 "x is not recognized"）。
chcp 936 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo   music-gen 一键安装（首次运行；已装过的步骤会自动跳过）
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [X] 找不到 python。请先安装 Python 3.10 或更高版本，
  echo     安装时勾选 "Add python.exe to PATH"。
  echo     下载地址: https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

echo [1/4] 虚拟环境 .venv
if exist ".venv\Scripts\python.exe" (
  echo       已存在，跳过
) else (
  python -m venv .venv
  if errorlevel 1 goto fail
)

echo [2/4] 依赖 numpy / soundfile / imageio-ffmpeg
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q numpy soundfile imageio-ffmpeg
if errorlevel 1 goto fail

echo [3/4] 音源 GeneralUser GS + fluidsynth（约 32MB）
echo       多镜像自动重试；国内网络前几次断连很正常，它会换镜像继续
".venv\Scripts\python.exe" scripts\setup_soundfont.py
if errorlevel 1 goto fail

echo.
echo [4/4] 自检：78 项全绿就说明环境可用
".venv\Scripts\python.exe" scripts\selftest.py
echo.
echo ============================================================
echo   装好了。
echo   想听现成的示例:  songs\23_d150_skip_along\d150_skip_along_sf.ogg
echo   想写第一首歌:    看 INSTALL.md 的「五分钟出第一首」
echo ============================================================
pause
exit /b 0

:fail
echo.
echo [X] 这一步失败了 —— 看上面的报错；也可以按 INSTALL.md 手动做一遍。
echo.
pause
exit /b 1
