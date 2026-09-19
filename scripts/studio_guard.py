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
import json
import os
import subprocess
import sys
import time
import urllib.parse
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


# ---------------------------------------------------------------- A′：面板 = 唯一入口
# 用户 2026-09-19 拍板。背景：面板那条规矩**文档写了四遍、用户当场问了五次**
# （02:34 / 08:08 / 08:44 / 09:31 / 09:44）仍没被执行 —— 那轮 4 首仿写的生成
# **全程走 CLI**，面板只被 curl 过 2 次探活。既然"写文档"和"追问"都失效，就改由
# **代码物理保证**：
#
#   · 人/agent 手敲 `new_song.py` / `make_song.py` → **委托面板 API**：任务在 GUI 里
#     全程可见、产物立刻能听，"看一眼 / 听一版"不再依赖记性；
#   · 面板自己 subprocess 起脚本 → 带 `BGM_STUDIO_INNER=1`（`studio/server.py` 注入）
#     → 走原生实现（**防递归**：面板 `/api/new` 背后正是 run_py 起 `new_song.py`）；
#   · 批量 / CI → 显式 `BGM_CLI_DIRECT=1` 走直连（`SKILL.md` 第 58 行"CLI 只留给批量并行"）。
#
# 只委托**面板能等价表达**的调用；带面板没有的开关（`--check`/`--no-compose`/
# `--candidates`…）时**不委托**并说明原因 —— 宁可退化，也不偷偷改语义。

def delegate_blocked():
    """None = 可委托；否则返回"为什么不委托"（调用方打印给人看）。"""
    if os.environ.get('BGM_STUDIO_INNER'):
        return 'BGM_STUDIO_INNER=1（面板内部调用 → 走原生，防递归）'
    if os.environ.get('BGM_CLI_DIRECT'):
        return 'BGM_CLI_DIRECT=1（显式直连）'
    if os.environ.get('BGM_NO_PANEL'):
        return 'BGM_NO_PANEL=1'
    return None


def _http(path, body=None, timeout=15.0):
    data = None if body is None else json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        panel_url() + path.lstrip('/'), data=data,
        headers={'Content-Type': 'application/json'},
        method='POST' if data is not None else 'GET')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def _opt(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


def _wait_job(jid, poll=1.0, timeout=2400.0):
    """轮询 `GET /api/job?id=<jid>` 到终态，把面板任务日志增量打到 stdout。"""
    t0, shown = time.time(), 0
    while True:
        try:
            r = _http('/api/job?id=%s' % urllib.parse.quote(jid), timeout=15.0)
        except Exception as e:                       # noqa: BLE001
            print('  !! 面板任务查询失败：%s' % e)
            return 1
        lines = str((r or {}).get('log') or '').splitlines()
        for ln in lines[shown:]:
            print('  | %s' % ln)
        shown = max(shown, len(lines))
        j = (r or {}).get('job') or {}
        if j.get('state') in ('done', 'failed', 'stopped'):
            return int(j.get('rc') or 0)
        if time.time() - t0 > timeout:
            print('  !! 等面板任务超时（%.0f 秒）；任务仍在面板里跑：%s'
                  % (timeout, panel_url()))
            return 1
        time.sleep(poll)


def delegate_make_song(argv):
    """`make_song.py` 的委托。返回 rc；**None = 别委托，调用方走原生**。"""
    why = delegate_blocked()
    if why:
        print('  面板：不委托（%s）' % why)
        return None
    rest = [a for a in argv[1:] if not a.startswith('--')]
    if len(rest) != 1:
        return None                     # 用法不对 → 交给原生打印 __doc__
    for bad in ('--check', '--no-compose', '--no-render', '--bpm'):
        if bad in argv:
            print('  面板：不委托（%s 面板没有等价任务）' % bad)
            return None
    song = rest[0]
    # 裸调用 = 渲染 + 自动调参 → `render-tune`（`server.py:651` 正是 `make_song.py <曲>`）；
    # 带 `--no-tune` → `render`（快渲染 + finalize 收尾）
    kind = 'render' if '--no-tune' in argv else 'render-tune'
    if not ensure_panel(quiet=True):
        print('  !! 面板拉不起来 → 这次走 CLI 直连')
        return None
    try:
        r = _http('/api/job?id=%s&kind=%s' % (urllib.parse.quote(song), kind),
                  body={}, timeout=30.0)
    except Exception as e:                           # noqa: BLE001
        print('  !! 面板接单失败（%s）→ 这次走 CLI 直连' % e)
        return None
    jid = (r or {}).get('job')
    if not jid:
        print('  !! 面板没返回任务号（%s）→ 这次走 CLI 直连' % r)
        return None
    print('  面板  已接单 %s（kind=%s）· 进度与产物都在 %s' % (jid, kind, panel_url()))
    return _wait_job(jid)


def delegate_new_song(argv):
    """`new_song.py` 的委托。返回 rc；**None = 别委托，调用方走原生**。"""
    why = delegate_blocked()
    if why:
        print('  面板：不委托（%s）' % why)
        return None
    if '--list-styles' in argv or '--list-themes' in argv:
        return None                     # 纯打印，不碰面板
    for bad in ('--candidates', '--energy-gain'):
        if bad in argv:
            print('  面板：不委托（%s 面板 /api/new 没有这个参数）' % bad)
            return None
    theme = _opt(argv, '--theme')
    # 位置参数要**跳过带值选项的值**：`new_song.py` 自己的解析是"所有非 `--` 开头都算位置参数，
    # 再把 theme 的值单独滤掉"（`new_song.py:1162-1165`）→ 于是 `--ref daily_mix` 里的
    # `daily_mix` 也会被它当成位置参数。首版在这里照抄了那个写法，`pos` 就变成两项
    # （`_probe` + `daily_mix`）→ **永远不委托**；自检只查"有没有接线"、查不出这个，
    # 是端到端试跑才逮到的（所以这条路径必须留一个真跑的用例，别只靠静态断言）。
    _VALUED = ('--theme', '--from', '--style', '--ref', '--seed',
               '--candidates', '--energy-gain')
    pos, i = [], 1
    while i < len(argv):
        if argv[i] in _VALUED:
            i += 2
            continue
        if not argv[i].startswith('--'):
            pos.append(argv[i])
        i += 1
    if len(pos) != 1:
        return None
    nid = pos[0]
    # **必须显式带 `--ref` 才委托**：`/api/new` 没收到 ref 时会补 `first_ref()`
    # （`server.py:1032/1062`），而 CLI 省略 `--ref` 的语义是"**取主题包里的混音目标**"
    # （`SKILL.md:54`）—— 两者不是一回事。宁可这次退回直连，也不偷偷换掉依据。
    if not _opt(argv, '--ref'):
        print('  面板：不委托（没给 --ref —— 面板会补 first_ref()，'
              '与"取主题包混音目标"不是一回事）')
        return None
    # ⚠ `render` **必须显式 false**：`/api/new` 缺省 `do_render = bool(force)`
    #   （`server.py:1074`），而 CLI 的 `new_song --force` **只重建 song.json、不渲染** ——
    #   少了这一行，批量里 `new_song --force` + `make_song --check` 会**渲染两遍**。
    body = {'id': nid, 'force': '--force' in argv, 'render': False}
    if theme:
        body['theme'] = theme
    elif _opt(argv, '--from'):
        body['from'] = _opt(argv, '--from')
        body['style'] = _opt(argv, '--style') or 'daily'
    else:
        return None                     # 用法交给原生报错
    for k, flag in (('seed', '--seed'), ('ref', '--ref')):
        v = _opt(argv, flag)
        if v:
            body[k] = v
    if not ensure_panel(quiet=True):
        print('  !! 面板拉不起来 → 这次走 CLI 直连')
        return None
    try:
        r = _http('/api/new', body=body, timeout=900.0)
    except Exception as e:                           # noqa: BLE001
        print('  !! 面板接单失败（%s）→ 这次走 CLI 直连' % e)
        return None
    if not isinstance(r, dict):
        return None
    print('  面板  已生成 %s（theme=%s force=%s）· 看/听：%s'
          % (nid, body.get('theme') or body.get('from'), body['force'], panel_url()))
    for ln in str(r.get('log') or '').splitlines()[-12:]:
        print('  | %s' % ln)
    return 0 if r.get('ok') else 1


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
