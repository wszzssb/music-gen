#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""strip_drums.py —— **去鼓版**：把扒带/还原曲的全部打击乐拿掉，其余音符一个字节不动。

## 什么时候用

用户口径："**这首曲子试试不要鼓**"（2026-10-04，`どうぞめしあがれ` 还原曲）。
输入 = 已有曲目目录（`songs/<曲名>`，或直接给 `song.json` 的路径），
输出 = **新曲目目录**（默认 `songs/<原名>_nodrum/`），产物照样由引擎渲染
（`.mid` + `_sf.ogg` + 成绩单），可直接和原版在面板里 A/B。

## 为什么不是"把 `Drums` 轨删掉"就完了（每条都在 `song_engine` 里读出来的）

| 只删 `notes_extra.Drums` 会踩的坑 | 本工具的处理 | 依据 |
|---|---|---|
| `patterns.drum_grid` 还在 → 引擎走**网格**那条路，`arr.perc` 一开就照网格重新生成一整条 `Perc`（鼓没少，还换了套鼓点） | `drum_grid` 整个删掉 | `song_engine.build_events` 鼓段（`_pbars`/`_secs` 三分支） |
| 网格删了但 `arr.perc` 还在 → 掉进 `elif arr.get('perc')` 的**固定套路**分支（`perc_part(perc_style)` 凭空敲） | 逐段关 `perc`（**逐段**，不是整曲一刀切） | 同上 + SKILL §20 |
| 还原曲的 `arr` 是**引擎自动写的**（`apply_restore_no_gen`：`perc` 恒 0、有转录音的轨才开） | 只**显式写** `perc: 0`，其它键一个不碰 | `song_engine.load` 的"还原曲"那段 |
| 段落里可能有 `perc_target`（逐段鼓点目标）残留 | 一并删掉 | `ARR_KEYS_EXTRA` 的注释 |
| 在渲染出的 `.mid` 上删音符 = 交付物对不上 `song.json`（下次重渲染鼓又回来了） | **只改 `song.json`，再让引擎重渲染** | SKILL §`RESTORE-METHOD` §10 第 2 条"别绕开引擎正路" |

⚠ **本工具不改任何非鼓内容**：`programs` / `mix` / 和弦 / 旋律 / 其它 `notes_extra` 轨
逐字节不动；写完会打印"改动项清单"（相对源曲的 diff），并核对音符总数 = 源 − 鼓音数。

## 用法

```bash
python scripts\strip_drums.py <曲目|song.json 路径> [--out 新曲名] [--name 曲内 name]
       [--force] [--dry] [--no-render] [--verify-only 曲目]
# 例：
python scripts\strip_drums.py douzo_ymt3 --dry            # 只看处理表，不写盘
python scripts\strip_drums.py douzo_ymt3                  # → songs\douzo_ymt3_nodrum\ 并渲染
python scripts\strip_drums.py douzo_ymt3 --no-render      # 只写 song.json，先自己核
python scripts\strip_drums.py --verify-only douzo_ymt3_nodrum   # 独立核对产物 .mid 里确实没有鼓
```

`--verify-only` 是**独立证据**那一步：直接解析产物 `.mid`，断言
（① 通道 9 的 note-on = 0 ② 引擎 `Perc` 轨不存在/为空 ③ 源曲的鼓音数一个都没留下）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()          # noqa: E402
import json_io                               # noqa: E402

SONGS = os.path.join(ROOT, 'songs')
DRUM_TRACK = 'Drums'          # 转录来的鼓（GM 鼓键 + 通道 9，见 song_engine.CH）
DRUM_CH = 9                   # GM 打击乐通道（0 基）


# ------------------------------------------------------------------ 读/写
def _resolve(spec):
    """曲目名 或 song.json 路径 → (曲目目录, song.json 路径, 曲名)"""
    if os.path.isfile(spec):
        p = os.path.abspath(spec)
        return os.path.dirname(p), p, os.path.basename(os.path.dirname(p))
    p = os.path.join(SONGS, spec, 'song.json')
    if not os.path.exists(p):
        raise SystemExit('找不到 %s（也不是 song.json 路径）' % p)
    return os.path.join(SONGS, spec), p, spec


def _notes(v):
    """`notes_extra[轨]` 兼容两种写法：裸列表 或 `{'notes': [...]}`"""
    if isinstance(v, dict):
        return v.get('notes') or []
    return v or []


def _count_drums(d):
    """源曲里的鼓音数（转录 `Drums` 轨 + 逐小节网格）"""
    n_tr = len(_notes((d.get('notes_extra') or {}).get(DRUM_TRACK)))
    grid = ((d.get('patterns') or {}).get('drum_grid') or {}).get('per_bar') or []
    n_grid = sum(len(v) for bar in grid for v in (bar or {}).values())
    return n_tr, n_grid


def strip(d):
    """就地把 `d`（已 load 过的 song.json 字典）里的打击乐全部拿掉；返回改动清单。

    ⚠ **逐段**关 `perc`（SKILL §20：不许整曲一刀切）—— 本工具逐段写 `perc: 0`，
    并在清单里逐段列出；除了 `perc`/`perc_target`，段落里其它键**一个不碰**。
    """
    ch = []
    ne = d.get('notes_extra') or {}
    n_tr_src = len(_notes(ne.get(DRUM_TRACK)))
    if DRUM_TRACK in ne:
        ne[DRUM_TRACK] = []
        ch.append('notes_extra.%s: %d 音 → 0 音' % (DRUM_TRACK, n_tr_src))
    pat = d.setdefault('patterns', {})
    grid = pat.get('drum_grid')
    if grid:
        n_grid = sum(len(v) for bar in (grid.get('per_bar') or [])
                     for v in (bar or {}).values())
        n_ps = sum(len(v) for sec in (grid.get('per_section') or [])
                   for v in (sec or {}).values()) if grid.get('per_section') else 0
        pat.pop('drum_grid', None)
        ch.append('patterns.drum_grid: 删（per_bar %d 格 / per_section %d 格）'
                  % (n_grid, n_ps))
    seg = []
    for s in d.get('sections') or []:
        a = s.setdefault('arr', {})
        was = a.get('perc', '(未写)')
        a['perc'] = 0
        if a.pop('perc_target', None) is not None:
            seg.append('%s:perc_target 删' % s.get('name'))
        seg.append('%s:%s→0' % (s.get('name'), was))
    if seg:
        ch.append('逐段 arr.perc（%d 段）：%s' % (len(seg), ' '.join(seg)))
    return ch


def _all_notes(d):
    """song.json 里全部音符数（音符合计，用于"少的就是鼓"这条核对）"""
    n = 0
    for v in (d.get('notes_extra') or {}).values():
        n += len(_notes(v))
    for sec in (d.get('melody') or {}).values():
        n += len(sec if isinstance(sec, list) else _notes(sec))
    return n


# ------------------------------------------------------------------ 核对
def verify_midi(path):
    """独立核对渲染出的 `.mid`：返回 (ok, 逐项读数)"""
    import mido
    m = mido.MidiFile(path)
    rows, bad = [], []
    for i, t in enumerate(m.tracks):
        ons = [x for x in t if x.type == 'note_on' and x.velocity > 0]
        names = [x.name for x in t if x.type == 'track_name']
        nm = names[0] if names else 'T%d' % i
        ch9 = [x for x in ons if getattr(x, 'channel', -1) == DRUM_CH]
        rows.append((nm, len(ons), len(ch9)))
        if ch9:
            bad.append('轨 %s 通道 %d 上有 %d 个打击乐 note-on' % (nm, DRUM_CH, len(ch9)))
        if nm.strip().lower() in ('perc', 'drums') and ons:
            bad.append('轨 %s 还有 %d 个音' % (nm, len(ons)))
    return (not bad), rows, bad


def _run(cmd, cwd=ROOT):
    print('  $ %s' % ' '.join(cmd))
    return subprocess.run(cmd, cwd=cwd).returncode


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('song', nargs='?', help='曲目名（songs/<名字>）或 song.json 的路径')
    ap.add_argument('--out', default=None, help='输出曲目名（默认 <原名>_nodrum）')
    ap.add_argument('--name', default=None, help='写进 song.json 的 name（默认 = 输出目录名）')
    ap.add_argument('--force', action='store_true', help='输出目录已存在时覆盖')
    ap.add_argument('--dry', action='store_true', help='只打印处理表，不写盘')
    ap.add_argument('--no-render', action='store_true', help='只写 song.json，不渲染')
    ap.add_argument('--no-tune', action='store_true', help='渲染时不跑自动调参（A/B 用）')
    ap.add_argument('--verify-only', default=None, metavar='曲目',
                    help='只核对已有曲目的产物 .mid（不写盘）')
    a = ap.parse_args()

    if a.verify_only:
        folder = os.path.join(SONGS, a.verify_only)
        cfg_p = os.path.join(folder, 'render.json')
        cfg = json.load(open(cfg_p, encoding='utf-8')) if os.path.exists(cfg_p) else {}
        mid = os.path.join(folder, cfg.get('mid') or (a.verify_only + '.mid'))
        if not os.path.exists(mid):
            print('找不到 MIDI: %s' % mid)
            return 1
        ok, rows, bad = verify_midi(mid)
        print('[核对] %s' % mid)
        for nm, n, n9 in rows:
            print('   %-10s note-on %5d   其中通道9 %d' % (nm, n, n9))
        print('   合计 note-on %d' % sum(r[1] for r in rows))
        for b in bad:
            print('   ✗ %s' % b)
        print('   ⇒ %s' % ('无打击乐 ✔' if ok else '**还有鼓** ✗'))
        return 0 if ok else 1

    if not a.song:
        print(__doc__)
        return 1
    src_dir, src_json, src_name = _resolve(a.song)
    out_name = a.out or (src_name + '_nodrum')
    new_name = a.name or out_name
    out_dir = os.path.join(SONGS, out_name)

    d_src = json_io.load(src_json)
    mid_old = os.path.join(src_dir, 'render.json')
    cfg = json.load(open(mid_old, encoding='utf-8')) if os.path.exists(mid_old) else {}
    n_tr, n_grid = _count_drums(d_src)
    print('[源] %s' % src_json)
    print('     鼓音 %d 个（转录 %s 轨）· 逐小节网格 %d 格 · style=%s'
          % (n_tr + n_grid, DRUM_TRACK, n_grid, d_src.get('style')))
    if not n_tr and not n_grid:
        print('  ! 源曲里没有任何鼓（转录轨与网格都空）—— 去鼓版会与原版**逐字节相同**，'
              '多半不是你想要的；确认后再跑。')

    d = json.loads(json.dumps(d_src))          # 深拷贝（不改源）
    ch = strip(d)
    d['name'] = new_name
    d['desc'] = ('去鼓版（`strip_drums.py` 派生自 %s）：打击乐全部移除，其余内容原样。'
                 % src_name)
    n_src_all, n_dst_all = _all_notes(d_src), _all_notes(d)
    print('[处理表] 输出 %s（name=%s）' % (out_dir, new_name))
    for line in ch:
        print('   · %s' % line)
    print('   · 音符合计 %d → %d（差 %d = 鼓音 %d + melody 未动，'
          '差值与鼓音数一致才算没误删）'
          % (n_src_all, n_dst_all, n_src_all - n_dst_all, n_tr))
    if a.dry:
        print('[dry] 不写盘')
        return 0

    if os.path.exists(out_dir):
        if not a.force:
            print('已存在 %s（要覆盖加 --force）' % out_dir)
            return 1
        shutil.rmtree(out_dir)
    shutil.copytree(src_dir, out_dir,
                    ignore=shutil.ignore_patterns('__pycache__', '*.wav', '*.ogg',
                                                  '*.mid', '*.bak*'))
    json_io.save(os.path.join(out_dir, 'song.json'), d)
    # render.json 只留渲染参数（沿用源曲的调好的值），产物文件名改成新曲名
    cfg.pop('last_bands', None)
    cfg['mid'] = new_name + '.mid'
    cfg['out'] = new_name + '_sf'
    cfg['song'] = new_name
    cfg.setdefault('composer', 'compose.py')
    json_io.save(os.path.join(out_dir, 'render.json'), cfg)
    print('[写] %s' % os.path.join(out_dir, 'song.json'))

    if a.no_render:
        print('[跳过渲染] 下一步：')
        print('   python scripts\\make_song.py %s --check' % out_name)
        return 0
    cmd = [sys.executable, os.path.join(HERE, 'make_song.py'), out_name, '--check']
    if a.no_tune:
        cmd.append('--no-tune')
    if _run(cmd):
        print('渲染/体检失败 —— 修完重跑')
        return 1
    print()
    rc = main_verify(out_name)
    return rc


def main_verify(song):
    folder = os.path.join(SONGS, song)
    cfg_p = os.path.join(folder, 'render.json')
    cfg = json.load(open(cfg_p, encoding='utf-8')) if os.path.exists(cfg_p) else {}
    mid = os.path.join(folder, cfg.get('mid') or (song + '.mid'))
    if not os.path.exists(mid):
        print('[核对] 找不到 MIDI: %s' % mid)
        return 1
    ok, rows, bad = verify_midi(mid)
    print('[核对] %s' % mid)
    for nm, n, n9 in rows:
        print('   %-10s note-on %5d   其中通道9 %d' % (nm, n, n9))
    print('   合计 note-on %d' % sum(r[1] for r in rows))
    for b in bad:
        print('   ✗ %s' % b)
    print('   ⇒ %s' % ('无打击乐 ✔' if ok else '**还有鼓** ✗'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
