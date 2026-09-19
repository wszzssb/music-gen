#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""studio_guard.py —— 生成类脚本开工前的**面板守卫**：把"记得开面板"从文档变成代码。

为什么要有它（2026-09-19 落）：
  `SKILL.md` §2 第 ① 条早就写着"动手前先 curl 一下 8765，非 200 就先
  `studio\\start.cmd`"，`token_audit.py` 里还记着它当时是"2026-09-18 **第二次**犯"。
  可是**文档不是约束**：2026-09-19 那轮仿写 4 首（`40/41/42/43`）**生成全部走 CLI**，
  面板直到用户开口问"有没有用 `http://127.0.0.1:8765/`"之后 37 秒才被接上。
  同一类错第四次 ⇒ 按 `~/.dsh/AGENTS.md`「犯到第三次就必须写进硬形式」，
  改成由**脚本自己**保证：生成开工前先把面板探活/拉起，不靠"记得"。

它做什么（三步）：
  ① 探活 `http://127.0.0.1:<port>/`：HTTP 2xx/3xx 算在跑；
  ② 不在跑 → **detached** 起 `studio/server.py`，轮询等它就绪（默认最多 15 秒）。
     ⚠ **不调 `studio\\start.cmd`**：那是**阻塞式前台**（`"%PY%" server.py --port %PORT% --open`
     跑在窗口里），从生成脚本里调它会连生成一起卡住 —— 这与"长任务别占前台"同源；
  ③ 起不来 → **只警告、不中断**（面板是辅助通道，缺了不该让生成失败），
     并把手工命令和 URL 打出来（可见性：对话里能一眼看到入口）。

接法（生成类脚本 `main()` 里、参数校验通过之后一行）：
    import studio_guard
    studio_guard.ensure_panel()

环境变量：
    BGM_NO_PANEL=1       整段跳过（无头 / CI）
    BGM_PANEL_PORT=8765  换端口
    BGM_PANEL_WAIT=15    等它就绪的秒数

单独跑（确认守卫生效）：
    python scripts\\studio_guard.py           # 探活，必要时拉起
    python scripts\\studio_guard.py --check    # 只报状态不拉起（0=在跑 / 1=没在跑）

判据（`selftest.py` 的 `panel_guard_wired` 守）：本文件存在 + 三个生成脚本
（`new_song.py` / `make_song.py` / `melody_gen.py`）都接了线 + `panel_alive()`
对本机一个必然没人听的端口返回 False（反向对照，防"恒返回 True"的装饰性探活）。
"""
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STUDIO = os.path.join(ROOT, 'studio')
SERVER = os.path.join(STUDIO, 'server.py')
DEFAULT_PORT = 8765
# DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP：面板不随调用它的生成脚本一起死
_DETACHED = 0x00000008 | 0x00000200


def panel_port():
    try:
        return int(os.environ.get('BGM_PANEL_PORT') or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def panel_url(port=None):
    return 'http://127.0.0.1:%d/' % (port or panel_port())


def panel_alive(port=None, timeout=1.0):
    """HTTP 探活。

    为什么不是"看端口有没有人听"：端口被别的进程占着时端口探测会**假阳性**
    （那正是"探到 200 就用"这种写法的隐患）；这里要的是**面板本身**在响应。
    """
    try:
        r = urllib.request.urlopen(panel_url(port), timeout=timeout)
    except Exception:
        return False
    try:
        code = getattr(r, 'status', None) or r.getcode()
    except Exception:
        return False
    finally:
        try:
            r.close()
        except Exception:
            pass
    try:
        return 200 <= int(code) < 400
    except (TypeError, ValueError):
        return False


def start_panel(port=None, quiet=False):
    """detached 起面板；成功返回 Popen，失败返回 None（**不抛**）。"""
    if not os.path.exists(SERVER):
        if not quiet:
            print('  !! 找不到 %s —— 面板起不来' % SERVER)
        return None
    py = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')
    if not os.path.exists(py):
        py = os.path.join(ROOT, '.venv', 'bin', 'python')
    if not os.path.exists(py):
        py = sys.executable
    kw = {'cwd': STUDIO, 'stdin': subprocess.DEVNULL,
          'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
    if os.name == 'nt':
        kw['creationflags'] = _DETACHED
    try:
        # 不加 `--open`：那是给人双击 `start.cmd` 用的，从脚本里拉起来不该抢浏览器焦点
        return subprocess.Popen([py, SERVER, '--port', str(port or panel_port())], **kw)
    except Exception as e:                       # noqa: BLE001
        if not quiet:
            print('  !! 面板启动失败：%s' % e)
        return None


def ensure_panel(auto_start=True, wait=None, quiet=False, port=None):
    """生成开工前调用。返回 True = 面板可用（在跑 / 刚被拉起来）。"""
    if os.environ.get('BGM_NO_PANEL'):
        return False
    port = port or panel_port()
    if panel_alive(port):
        if not quiet:
            print('  面板  %s  （已在跑）' % panel_url(port))
        return True
    if not auto_start:
        if not quiet:
            print('  !! 面板没在跑：%s\n     先起它：studio\\start.cmd' % panel_url(port))
        return False
    if not quiet:
        print('  面板没在跑 → 拉起 %s ...' % panel_url(port))
    if start_panel(port, quiet=quiet) is None:
        return False
    if wait is None:
        try:
            wait = float(os.environ.get('BGM_PANEL_WAIT') or 15.0)
        except ValueError:
            wait = 15.0
    deadline = time.time() + max(0.0, wait)
    while time.time() < deadline:
        if panel_alive(port):
            if not quiet:
                print('  面板  %s  ✓（生成前先在这里试听本段，别拿整曲渲染当探针）'
                      % panel_url(port))
            return True
        time.sleep(0.4)
    if not quiet:
        print('  !! 面板还没起来（等了 %.0f 秒）—— 手工：studio\\start.cmd\n     %s'
              % (wait, panel_url(port)))
    return False


def main():
    if panel_alive():
        print('面板在跑：%s' % panel_url())
        return 0
    if '--check' in sys.argv:
        print('面板没在跑：%s' % panel_url())
        return 1
    return 0 if ensure_panel() else 1


if __name__ == '__main__':
    sys.exit(main())
