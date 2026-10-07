# -*- coding: utf-8 -*-
"""BGM Studio 创作台 —— **桌面窗口版**（真窗口，不是浏览器）。

它做三件事：
  ① **探活**：本地端口有没有在跑面板；没有就**用同一个 .venv** 起一个（`CREATE_NO_WINDOW`，
     不弹控制台黑窗）；
  ② 用 `pywebview` 开一个**原生窗口**加载创作台，由系统自带的 **WebView2** 渲染 ——
     没有地址栏、没有标签页，任务栏上是它自己的图标与标题，**不经过 Edge**；
  ③ **关窗即退出**，并且只停"自己起的那个服务"（复用别人已开的实例时不动它）。

## 为什么后端仍是一个本地 HTTP 服务

面板的前端、引擎调用、后台任务、音频流全都围绕 `studio/server.py` 的二十多个路由写的 ——
换掉它等于重写半个工具链。VS Code / Slack / 微信桌面版也都是"外壳 + 内嵌浏览器内核"这个形状，
区别只在外壳**是不是浏览器进程**、有没有浏览器 UI。本文件负责的就是那个外壳。

## 用法

```bash
.venv\\Scripts\\pythonw.exe studio\\desktop_app.py          # 无控制台（双击入口就用它）
.venv\\Scripts\\python.exe  studio\\desktop_app.py --debug   # 带控制台 + 可开 devtools
python studio\\desktop_app.py --selftest                    # 只体检环境，不开窗
```

⚠ 无控制台运行时出错是**看不见**的 ⇒ 任何异常都会写进 `%TEMP%\\bgm-studio-desktop.log`。
"""
import argparse
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')
LOG = os.path.join(os.environ.get('TEMP', HERE), 'bgm-studio-desktop.log')


def port_open(port, host='127.0.0.1', timeout=0.5):
    """端口上有东西在听吗（探活用，不做 HTTP 请求 —— 服务刚起时可能还没准备好应答）"""
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def webview2_dir():
    for d in (r'C:\Program Files (x86)\Microsoft\EdgeWebView\Application',
              r'C:\Program Files\Microsoft\EdgeWebView\Application'):
        if os.path.isdir(d):
            return d
    return None


#: WebView2 的 .NET SDK 就是靠这两个注册表键找 Runtime 的（机器级 / 用户级）
WEBVIEW2_KEYS = (
    (r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients'
     r'\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'HKLM'),
    (r'Software\Microsoft\EdgeUpdate\Clients'
     r'\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'HKCU'),
)


def webview2_ready():
    """→ (可用吗, 版本号, 说明)。

    ⚠ **只看目录会误判**（2026-10-07 实测本机）：`EdgeWebView\\Application\\142.0.3595.80\\`
    目录是存在的，但里面**没有 `msedgewebview2.exe`**、注册表两个键也没有 —— 那是一次
    不完整的残留。此时 pywebview 只抛一句 `Couldn't find a compatible Webview2 Runtime`，
    **窗口照样开出来、整片白**，用户看不到任何提示。
    所以判据必须是**注册表里登记的版本号**（SDK 找 Runtime 用的就是它）。
    """
    import winreg
    for sub, hive in WEBVIEW2_KEYS:
        h = winreg.HKEY_LOCAL_MACHINE if hive == 'HKLM' else winreg.HKEY_CURRENT_USER
        try:
            with winreg.OpenKey(h, sub) as kh:
                pv, _ = winreg.QueryValueEx(kh, 'pv')
                if pv:
                    return True, str(pv), '%s\\%s' % (hive, sub)
        except OSError:
            continue
    return False, '', '注册表里没有 WebView2 Runtime 的登记（目录里有残留不算）'


def _log(text):
    """无控制台运行时（pythonw）出错是看不见的 —— 一律留档"""
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(text.rstrip() + '\n')
    except OSError:
        pass


def _warn_box(title, text):
    """弹一个看得见的 Windows 消息框（**另起进程**：tkinter 只能在主线程用，看门狗是子线程）。

    用 `-EncodedCommand` 传 PowerShell 脚本：中文/引号/换行都不用转义，实测最稳。
    """
    import base64
    ps = ("Add-Type -AssemblyName System.Windows.Forms\n"
          "$t = @'\n%s\n'@\n$m = @'\n%s\n'@\n"
          "[System.Windows.Forms.MessageBox]::Show($m, $t, 'OK', 'Warning') | Out-Null\n"
          % (title, text))
    enc = base64.b64encode(ps.encode('utf-16-le')).decode('ascii')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    try:
        subprocess.Popen(['powershell', '-NoProfile', '-EncodedCommand', enc],
                         creationflags=flags, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError as e:
        _log('[warn] 弹窗失败：%s' % e)


def selftest():
    """只体检：窗口壳少了任何一环，双击都会"没反应"（无控制台，看不到报错）"""
    ok = True

    def line(name, good, extra=''):
        nonlocal ok
        ok = ok and bool(good)
        print('  %-22s %s %s' % (name, 'PASS' if good else 'FAIL', extra))

    line('pythonw.exe', os.path.isfile(os.path.join(ROOT, '.venv', 'Scripts', 'pythonw.exe')))
    line('server.py', os.path.isfile(os.path.join(HERE, 'server.py')))
    w2ok, w2ver, w2why = webview2_ready()
    line('WebView2 Runtime', w2ok,
         ('版本 %s' % w2ver) if w2ok else ('%s —— 装它：winget install --id Microsoft.EdgeWebView2Runtime'
                                           % w2why))
    line('WebView2 目录', True, webview2_dir() or '（没有该目录）')
    try:
        import webview
        try:
            from importlib.metadata import version as _v
            _ver = _v('pywebview')
        except Exception:                                         # noqa: BLE001
            _ver = getattr(webview, '__version__', '?')
        line('pywebview', True, _ver)
    except Exception as e:                                        # noqa: BLE001
        line('pywebview', False, '%s: %s' % (type(e).__name__, e))
    line('端口探活可用', port_open(8765) or not port_open(8765), '')
    print('自检 %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 2


def _stop_child(proc):
    """停掉"我们自己起的"那个面板服务（幂等；别人起的传 None 进来什么都不做）。

    ⚠ **必须杀进程树**：`.venv\\Scripts\\python.exe` 在本机是 venv 的 **launcher**
    （Python 3.14 的行为），它会再 spawn 一个真正跑 `server.py` 的解释器 ——
    实测父子两个命令行一模一样，而**监听 8765 的是子进程**。只 `terminate()` launcher
    会把子进程留成孤儿继续占着端口（"关窗了 8765 还在听"）。
    """
    if proc is None or proc.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()


def _fallback_edge():
    """降级：调浏览器窗口版启动器（服务与窗口都由它处理），**别让用户对着白窗发呆**"""
    cmd = os.path.join(HERE, '创作台-浏览器窗口版.cmd')
    if not os.path.isfile(cmd):
        _log('[fallback] 找不到 %s' % cmd)
        return 3
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    subprocess.Popen(['cmd', '/c', cmd], creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


def main():
    ap = argparse.ArgumentParser(description='BGM Studio 创作台（桌面窗口版）')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--page', default='/create')
    ap.add_argument('--width', type=int, default=1320)
    ap.add_argument('--height', type=int, default=900)
    ap.add_argument('--debug', action='store_true', help='开 devtools + 保留控制台')
    ap.add_argument('--force-edge', action='store_true', help='跳过原生窗口，直接用 Edge 应用模式')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.force_edge:
        return _fallback_edge()

    # ⚠ **开窗前先认 WebView2**：认不出来时 pywebview 会开出一个**整片白的窗口并且不报错**
    #   （2026-10-07 用户实际遇到：目录里有残留、注册表没登记）。宁可降级，也不能白屏。
    w2ok, w2ver, w2why = webview2_ready()
    if not w2ok:
        _log('[webview2] 不可用：%s' % w2why)
        _warn_box('BGM Studio 创作台',
                  '原生窗口需要系统的 WebView2 Runtime，但%s。\n\n'
                  '已改用 Edge 应用模式打开（功能一样，只是窗口属于 Edge）。\n\n'
                  '想要原生窗口就装一下运行时（约 100MB，需要管理员权限）：\n'
                  '    winget install --id Microsoft.EdgeWebView2Runtime\n'
                  '装完再双击本启动器即可。' % w2why)
        return _fallback_edge()
    _log('[webview2] 版本 %s' % w2ver)

    import webview                                    # 只有真要开窗才需要它

    # ⚠ **看门狗**：pywebview 在 WebView2 起不来时**不抛异常、只打一句日志**（实测：窗口照样
    #   开出来、整片白，用户完全不知道发生了什么）。所以这里盯着它的日志，一旦出现初始化失败
    #   就**关掉白窗、换成 Edge 应用模式**，并弹窗说明 —— 绝不把用户留在白屏上。
    import logging
    import threading

    class _W2Watch(logging.Handler):
        def __init__(self):
            logging.Handler.__init__(self)
            self.failed = False

        def emit(self, rec):
            try:
                m = rec.getMessage().lower()
                if 'initialization failed' in m or 'compatible webview2' in m:
                    self.failed = True
            except Exception:                                     # noqa: BLE001
                pass

    _w2 = _W2Watch()
    logging.getLogger().addHandler(_w2)

    started = None
    if not port_open(a.port):
        py = PY if os.path.isfile(PY) else sys.executable
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
        started = subprocess.Popen(
            [py, os.path.join(HERE, 'server.py'), '--port', str(a.port)],
            cwd=HERE, creationflags=flags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(120):                          # 最多等 60 秒（首次要加载音源索引）
            if port_open(a.port):
                break
            time.sleep(0.5)
        if not port_open(a.port):
            raise SystemExit('面板服务 60 秒内没起来 —— 手动跑 studio\\start.cmd 看报错')

    url = 'http://127.0.0.1:%d%s' % (a.port, a.page)
    win = webview.create_window('BGM Studio 创作台', url,
                                width=a.width, height=a.height, min_size=(1000, 660))

    def _on_closed():
        # ⚠ 关窗这一下**必须自己收尾**：实测（2026-10-07）`webview.start()` 在窗口关闭后
        #   有时**不返回** —— 那时 `finally` 永远跑不到，服务就成了孤儿进程一直挂着
        #   （表现为"关掉窗口了，8765 还在听"）。所以这里显式停服务再强制退出。
        _stop_child(started)
        os._exit(0)

    try:
        win.events.closed += _on_closed
    except Exception:                                             # noqa: BLE001
        pass                                                      # 老版本没有这个事件就靠 finally

    def _watch_webview2():
        # 盯 20 秒：pywebview 在 WebView2 起不来时**只打日志、不抛异常**，窗口照样开出来却是
        # 一片白（2026-10-07 用户实际遇到，截图确认）。一旦看到失败就关掉白窗、换成 Edge 应用模式，
        # 并弹窗说明 —— 宁可降级，也不能让人对着白屏发呆。
        for _ in range(40):
            time.sleep(0.5)
            if _w2.failed:
                _log('[webview2] 初始化失败 → 关掉白窗，改用 Edge 应用模式')
                _warn_box('BGM Studio 创作台',
                          '这台机器的 WebView2 运行时不可用，原生窗口没法渲染（会是一片白）。\n\n'
                          '已自动改用 Edge 应用模式打开 —— 功能完全一样，只是窗口属于 Edge。\n\n'
                          '想用原生窗口：以管理员身份装一次运行时\n'
                          '    winget install --id Microsoft.EdgeWebView2Runtime\n'
                          '装完再双击本启动器。')
                try:
                    win.destroy()
                except Exception:                                 # noqa: BLE001
                    pass
                _fallback_edge()
                os._exit(0)

    threading.Thread(target=_watch_webview2, daemon=True).start()

    try:
        webview.start(debug=a.debug)
    finally:
        # 只停自己起的那个：复用别人已开的实例时别把人家关了
        _stop_child(started)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:                                 # noqa: BLE001
        # 无控制台跑的时候出错是「双击没反应」，所以留一份可查的日志
        import traceback
        try:
            with open(LOG, 'w', encoding='utf-8') as f:
                f.write(traceback.format_exc())
        except OSError:
            pass
        raise
