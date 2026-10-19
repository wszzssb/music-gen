#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""ai_entry.py —— **AI 进这个目录后的唯一入口**（先跑它，别先读 1.6MB 文档）。

设计目标：一个模型被丢进 `D:\software\skill\music-gen` 之后，
**一条命令就能知道"我在哪、能做什么、红线是什么"，第二条命令就能出成品**。

用法（用仓库自带 venv 的 python）：

    $py = D:\software\skill\music-gen\.venv\Scripts\python.exe

    & $py scripts\ai_entry.py                      # ① 定位：目录地图 / 曲库 / 面板 / 红线
    & $py scripts\ai_entry.py doctor               # ② 体检：venv / 音源 / ffmpeg / 面板 / 曲库
    & $py scripts\ai_entry.py brief gorgeous       # ③ 设计说明书（JSON，给模型自己排曲子）
    & $py scripts\ai_entry.py new 140_x --theme gorgeous --seconds 120
    & $py scripts\ai_entry.py plan 140_x --plan p.json    # 套结构表（--report 会打逐段×逐轨）
    & $py scripts\ai_entry.py check 140_x [--deep]        # 默认 --fast（秒级）；--deep 全库 2~3 分钟
    & $py scripts\ai_entry.py render 140_x
    & $py scripts\ai_entry.py report 140_x         # 人话离群清单

**红线（写了就别想绕过，守卫会拦）**：
  1. 新歌只写 `song.json` / 结构表，**不改代码**；依据只能是 `refs/themes/<主题>.json`（主题模板包）。
  2. 改 `song.json` 前先记 SHA256；**改必须分段**（逐段/逐小节），不许整曲一刀切。
  3. 交付前跑 `check_song`；**"没达标"不许说成"达标"**；测不到的项要明说没验证。
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SONGS = os.path.join(ROOT, 'songs')
sys.path.insert(0, HERE)
import cli_utf8 as _cu  # noqa: E402
_cu.setup()             # 控制台编码兜底（GBK 下打 ✓/中文会崩）


def _songs():
    out = []
    if not os.path.isdir(SONGS):
        return out
    for d in sorted(os.listdir(SONGS)):
        p = os.path.join(SONGS, d, 'song.json')
        if os.path.isfile(p):
            out.append(d)
    return out


def _panel():
    try:
        import urllib.request
        with urllib.request.urlopen('http://127.0.0.1:8765/api/songs', timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def banner():
    songs = _songs()
    print('=' * 72)
    print('BGM Studio —— 仓库根：%s' % ROOT)
    print('曲库：%d 首  %s' % (len(songs), ', '.join(songs[:8]) + (' …' if len(songs) > 8 else '')))
    print('面板：http://127.0.0.1:8765  %s' % ('（在跑）' if _panel() else '（没跑：双击 studio\\start.cmd）'))
    print('=' * 72)
    print('目录地图（只记这几个）：')
    print('  scripts\\        工具（new_song / expand_sections / melody_gen / make_song / check_song）')
    print('  refs\\themes\\    15 个主题模板包 = **写歌的唯一依据**（probe 用 --brief 看可写空间）')
    print('  songs\\          曲库（每首一个目录：song.json + render.json + .mid + _sf.ogg + notes.md）')
    print('  docs\\           细则（STANDARD / SONG-FORMAT / THEME-PACK / IMITATE-PATH …）')
    print('  studio\\         可视化面板（不读代码也能听/改）')
    print()
    print('最常用五条（$py = .venv\\Scripts\\python.exe）：')
    print('  & $py scripts\\ai_entry.py doctor                  # 环境体检')
    print('  & $py scripts\\ai_entry.py brief <主题>            # 拿"能写什么"（和弦词表/arr 键/示例表）')
    print('  & $py scripts\\ai_entry.py new <名> --theme <主题> --seconds 120')
    print('  & $py scripts\\ai_entry.py plan <名> --plan p.json --report')
    print('  & $py scripts\\ai_entry.py render <名>')
    print()
    print('红线：新歌不改代码 · 改必须分段 · 交付前 check · 没达标不许说达标')


def doctor():
    ok = True
    print('== doctor ==')
    # 1) 解释器
    v = sys.version_info
    in_venv = os.path.normcase(sys.executable).startswith(
        os.path.normcase(os.path.join(ROOT, '.venv')))
    print('  python      %s.%s.%s  %s' % (v.major, v.minor, v.micro,
                                          '（仓库 venv ✓）' if in_venv else '（**不是仓库 .venv**，numpy/soundfile 可能缺）'))
    for m in ('numpy', 'soundfile', 'librosa'):
        try:
            __import__(m)
            print('  %-11s ✓' % m)
        except Exception as e:
            ok = False
            print('  %-11s ✗ %s' % (m, e))
    # 2) 音源
    vendor = os.path.join(ROOT, 'vendor')
    cand = [os.path.join(vendor, f) for f in os.listdir(vendor)] if os.path.isdir(vendor) else []
    hit = next((p for p in cand if p.lower().endswith('.sf2')), None)
    print('  soundfont   %s' % (hit or '✗ 没找到 .sf2（跑 scripts\\setup_soundfont.py）'))
    ok = ok and bool(hit)
    # 3) ogg 导出能力（本链用 libsndfile，不是 ffmpeg —— 早先查 PATH 是**假报警**）
    try:
        import soundfile as _sf
        fmts = _sf.available_formats()
        print('  ogg 导出    %s' % ('✓ libsndfile 支持 OGG' if 'OGG' in fmts else '✗ 不支持 OGG'))
        ok = ok and ('OGG' in fmts)
    except Exception as e:
        print('  ogg 导出    ✗ soundfile 不可用：%s' % e)
        ok = False
    import shutil
    print('  ffmpeg      %s（可选，本链不依赖）' % (shutil.which('ffmpeg') or '未安装'))
    # 4) 主题包
    themes = sorted(f[:-5] for f in os.listdir(os.path.join(ROOT, 'refs', 'themes'))
                    if f.endswith('.json') and not f.endswith('_melody.json'))
    print('  主题包      %d 个：%s' % (len(themes), ', '.join(themes)))
    ok = ok and len(themes) >= 8
    # 5) 曲库 / 面板
    print('  曲库        %d 首' % len(_songs()))
    print('  面板        %s' % ('✓ 在跑' if _panel() else '✗ 没跑（studio\\start.cmd）'))
    print('== %s ==' % ('可以开工' if ok else '有硬缺失，先修上面标 ✗ 的'))
    return 0 if ok else 1


def run(script, args):
    cmd = [sys.executable, os.path.join(HERE, script)] + args
    print('$ %s' % ' '.join(cmd[1:]))
    return subprocess.call(cmd)


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] in ('-h', '--help', 'help'):
        banner()
        return 0
    sub, rest = a[0], a[1:]
    if sub == 'doctor':
        return doctor()
    if sub == 'brief':
        if not rest:
            print('用法：ai_entry.py brief <主题>')
            return 2
        return run('expand_sections.py', ['--brief', rest[0]])
    if sub == 'new':
        return run('new_song.py', rest)
    if sub == 'plan':
        return run('expand_sections.py', rest + ['--report'])
    if sub == 'render':
        return run('make_song.py', rest)
    if sub == 'check':
        deep = '--deep' in rest
        rest = [x for x in rest if x != '--deep']
        return run('check_song.py', rest + ([] if deep else ['--fast']))
    if sub == 'report':
        return run('selfcheck.py', rest)
    print('未知子命令：%s（可用：doctor / brief / new / plan / render / check / report）' % sub)
    return 2


if __name__ == '__main__':
    sys.exit(main())
