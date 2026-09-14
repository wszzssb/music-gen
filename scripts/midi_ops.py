#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""midi_ops.py —— MIDI **编辑操作**（量化 / 移调 / 力度 / 吸附 / 增删改 / 复制粘贴）

为什么单独一层（而不是写在前端 JS 里）：同一套操作要被**三处**用到 ——
① 面板 UI ② 无浏览器冒烟测试（`studio/tools/smoke_ui.js` 与 `selftest`）③ 以后可能的批处理。
写在 Python 里三处共用一份口径，且能直接对着"真实 MIDI 文件"做回归。

所有函数都是 **`fn(model, params) → report`**，**原地修改** `model`，返回改了什么的报告
（`{op, notes, tracks, ...}`），便于 UI 显示与测试断言。时间一律是**拍**（四分音符）。

外部依赖：只依赖标准库（不 import 引擎），这样 `selftest` 与冒烟测试都能独立跑。
"""
import copy
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()

GRIDS = {                       # 量化/吸附网格 → 每拍的分数（1/4=0.25 拍 …… 1/32=0.125 拍）
    '1/1': 1.0, '1/2': 0.5, '1/4': 0.25, '1/8': 0.125,
    '1/16': 0.0625, '1/32': 0.03125,
    '1/4T': 1.0 / 6, '1/8T': 1.0 / 12, '1/16T': 1.0 / 24,
}


def grid_step(grid):
    """网格名或数值 → 步长（拍）。非法值报错，不静默退回（静默给错答案最难查）。"""
    if isinstance(grid, (int, float)):
        if grid <= 0:
            raise SystemExit('网格步长必须 > 0：%r' % (grid,))
        return float(grid)
    if grid in GRIDS:
        return GRIDS[grid]
    raise SystemExit('未知网格 %r（可用：%s）' % (grid, ', '.join(sorted(GRIDS))))


def _sel_tracks(model, track_idx=None, note_idx=None):
    """→ [(轨对象, 轨下标, 音符下标 或 None)]；`None` = 整轨（或全部轨）"""
    tracks = model.get('tracks') or []
    if track_idx is None:
        out = []
        for i, t in enumerate(tracks):
            if note_idx is None:
                out.append((t, i, None))
            else:
                out += [(t, i, j) for j in note_idx if 0 <= j < len(t.get('notes') or [])]
        return out
    if not 0 <= track_idx < len(tracks):
        raise SystemExit('轨下标越界：%r（共 %d 轨）' % (track_idx, len(tracks)))
    t = tracks[track_idx]
    if note_idx is None:
        return [(t, track_idx, None)]
    return [(t, track_idx, j) for j in note_idx if 0 <= j < len(t.get('notes') or [])]


def _each_note(model, track_idx=None, note_idx=None, fn=None):
    """对选中的音符逐个调用 fn(note_list)（note 是 4 元列表，原地改）"""
    n = 0
    for t, _ti, _j in _sel_tracks(model, track_idx, note_idx):
        notes = t.get('notes') or []
        idxs = range(len(notes)) if _j is None else [_j]
        for j in idxs:
            if 0 <= j < len(notes):
                fn(notes[j])
                n += 1
    return n


# --------------------------------------------------------------------------- 量化
def quantize(model, grid='1/16', strength=1.0, track_idx=None, note_idx=None,
             quant_dur=False, swing=0.0):
    """**智能量化**：把起点吸附到网格（可选同时量化时值）。

    `strength`（0~1）= 量化强度：1.0 = 完全对齐网格；0.5 = 只走一半
    （"部分量化"能保住演奏的呼吸感，miditoolbox 的常见建议）。
    `swing`（0~1）= 摇摆：把**网格的第二个十六分**往后推，最多推到下一个网格的 2/3
    （`swing=1` → 三连音感）。只作用于落在"偶数格"上的音。
    返回 {op, grid, step, strength, swing, notes}
    """
    step = grid_step(grid)
    st = max(0.0, min(1.0, float(strength)))
    sw = max(0.0, min(1.0, float(swing)))
    moved = 0

    def one(nt):
        nonlocal moved
        a, d = float(nt[0]), float(nt[1])
        q = round(a / step) * step
        if sw > 0:
            k = a / step
            if abs(k - round(k)) < 1e-9 and int(round(k)) % 2 == 1:
                q = q + sw * (step * 2.0 / 3.0)
        na = a + (q - a) * st
        if abs(na - a) > 1e-9:
            moved += 1
        nt[0] = round(max(0.0, na), 6)
        if quant_dur:
            dq = max(step, round(d / step) * step)
            nt[1] = round(d + (dq - d) * st, 6)
    n = _each_note(model, track_idx, note_idx, one)
    _sort_notes(model, track_idx)
    return {'op': 'quantize', 'grid': grid, 'step': step, 'strength': st,
            'swing': sw, 'notes': n, 'moved': moved}


# --------------------------------------------------------------------------- 移调
def transpose(model, semitones=0, track_idx=None, note_idx=None, clamp=True):
    """**移调**：按半音整体上/下移（正数向上）。超出 0~127 时夹取并计入报告。"""
    st = int(semitones)
    if st == 0:
        return {'op': 'transpose', 'semis': 0, 'notes': 0, 'clamped': 0}
    clamped = 0

    def one(nt):
        nonlocal clamped
        p = int(nt[2]) + st
        if p < 0 or p > 127:
            clamped += 1
            p = 0 if p < 0 else 127
        nt[2] = p
    n = _each_note(model, track_idx, note_idx, one)
    return {'op': 'transpose', 'semis': st, 'notes': n, 'clamped': clamped,
            'clamp': bool(clamp)}


def transpose_key(model, semitones=0):
    """整曲移调（所有轨）—— DAW 里"换到歌手的调"就是这个操作"""
    return transpose(model, semitones)


# --------------------------------------------------------------------------- 力度
def set_velocity(model, mode='scale', value=1.0, track_idx=None, note_idx=None,
                 lo=1, hi=127):
    """**力度与动态**：`mode='scale'` 乘系数 · `'offset'` 加减 · `'set'` 设为固定值 ·
    `'limit'` 夹到 [lo,hi] · `'humanize'` 加小随机（±`value` 力度）保留人性。
    返回 {op, mode, value, notes, before_min/max, after_min/max}（UI 直接显示对比）。
    """
    vals_before = []
    for t, _i, _j in _sel_tracks(model, track_idx, note_idx):
        vals_before += [n[3] for n in (t.get('notes') or [])]
    rnd = None
    if mode == 'humanize':
        import random
        rnd = random.Random(20260914)          # 固定种子：同样的操作得到同样的结果（可复现）

    def one(nt):
        v = int(nt[3])
        if mode == 'scale':
            v = int(round(v * float(value)))
        elif mode == 'offset':
            v = int(round(v + float(value)))
        elif mode == 'set':
            v = int(round(float(value)))
        elif mode == 'limit':
            v = int(v)
        elif mode == 'humanize':
            v = int(round(v + rnd.uniform(-abs(float(value)), abs(float(value)))))
        else:
            raise SystemExit('未知力度模式 %r（scale/offset/set/limit/humanize）' % (mode,))
        if mode in ('limit', 'humanize'):
            v = max(int(lo), min(int(hi), v))
        nt[3] = max(1, min(127, v))
    n = _each_note(model, track_idx, note_idx, one)
    vals_after = []
    for t, _i, _j in _sel_tracks(model, track_idx, note_idx):
        vals_after += [n[3] for n in (t.get('notes') or [])]
    return {'op': 'velocity', 'mode': mode, 'value': value, 'notes': n,
            'before': [min(vals_before or [0]), max(vals_before or [0])],
            'after': [min(vals_after or [0]), max(vals_after or [0])]}


def ramp_velocity(model, start=60, end=110, track_idx=None, note_idx=None):
    """**画力度曲线**：把选中的音按时间顺序做线性渐变（渐强/渐弱）"""
    picked = []
    for t, ti, j in _sel_tracks(model, track_idx, note_idx):
        notes = t.get('notes') or []
        idxs = range(len(notes)) if j is None else [j]
        picked += [(ti, k) for k in idxs if 0 <= k < len(notes)]
    if not picked:
        return {'op': 'ramp', 'notes': 0}
    picked.sort(key=lambda x: model['tracks'][x[0]]['notes'][x[1]][0])
    for k, (ti, ni) in enumerate(picked):
        f = 0.0 if len(picked) == 1 else k / float(len(picked) - 1)
        v = int(round(start + (end - start) * f))
        model['tracks'][ti]['notes'][ni][3] = max(1, min(127, v))
    return {'op': 'ramp', 'notes': len(picked), 'from': int(start), 'to': int(end)}


# --------------------------------------------------------------------------- 音符增删改
def add_note(model, track_idx, start, dur, pitch, vel=96):
    """加一个音（返回新音符在轨内的下标）——UI 里"空白处点击新建"走它"""
    if not 0 <= int(pitch) <= 127:
        raise SystemExit('音高越界：%r' % (pitch,))
    if float(dur) <= 0:
        raise SystemExit('时值必须 > 0：%r' % (dur,))
    t = model['tracks'][track_idx]
    t.setdefault('notes', []).append([round(max(0.0, float(start)), 6),
                                      round(float(dur), 6), int(pitch),
                                      max(1, min(127, int(vel)))])
    _sort_notes(model, track_idx)
    t = model['tracks'][track_idx]
    idx = next(i for i, n in enumerate(t['notes'])
               if abs(n[0] - float(start)) < 1e-9 and n[2] == int(pitch))
    return {'op': 'add_note', 'track': track_idx, 'index': idx,
            'note': list(t['notes'][idx])}


def delete_notes(model, track_idx=None, note_idx=None):
    """删音符（`note_idx` 为空/None = 整轨清空 —— 危险操作，UI 要二次确认）"""
    removed = 0
    tracks = model.get('tracks') or []
    if track_idx is None:
        for t in tracks:
            removed += len(t.get('notes') or [])
            t['notes'] = []
        return {'op': 'delete', 'removed': removed, 'tracks': len(tracks)}
    t = tracks[track_idx]
    if note_idx is None:
        removed = len(t.get('notes') or [])
        t['notes'] = []
    else:
        keep = []
        for i, n in enumerate(t.get('notes') or []):
            if i in note_idx:
                removed += 1
            else:
                keep.append(n)
        t['notes'] = keep
    return {'op': 'delete', 'removed': removed, 'track': track_idx}


def move_notes(model, track_idx, note_idx, d_beat=0.0, d_pitch=0, d_dur=0.0,
               grid=None, min_dur=1.0 / 16):
    """拖拽：同时可改起点/音高/时值；`grid` 给了就吸附（UI 的吸附开关）"""
    step = grid_step(grid) if grid else None
    touched = 0

    def one(nt):
        nonlocal touched
        a = float(nt[0]) + float(d_beat)
        if step:
            a = round(a / step) * step
        a = max(0.0, a)
        d = max(float(min_dur), float(nt[1]) + float(d_dur))
        if step and d_dur:
            d = max(float(min_dur), round(d / step) * step)
        p = int(nt[2]) + int(d_pitch)
        nt[0] = round(a, 6)
        nt[1] = round(d, 6)
        nt[2] = max(0, min(127, p))
        touched += 1
    _each_note(model, track_idx, note_idx, one)
    _sort_notes(model, track_idx)
    return {'op': 'move', 'notes': touched, 'd_beat': d_beat, 'd_pitch': d_pitch,
            'd_dur': d_dur, 'grid': grid}


def set_note(model, track_idx, note_idx, start=None, dur=None, pitch=None, vel=None):
    """直接设某个音的字段（UI 的数字输入框）"""
    t = model['tracks'][track_idx]
    n = t['notes'][note_idx]
    if start is not None:
        n[0] = round(max(0.0, float(start)), 6)
    if dur is not None:
        n[1] = round(max(1.0 / 64, float(dur)), 6)
    if pitch is not None:
        n[2] = max(0, min(127, int(pitch)))
    if vel is not None:
        n[3] = max(1, min(127, int(vel)))
    _sort_notes(model, track_idx)
    return {'op': 'set_note', 'track': track_idx, 'note': list(n)}


def copy_range(model, t0, t1, track_idx=None, cut=False):
    """按时间区间复制（`cut=True` = 剪切）：→ 片段字典（`paste` 用）"""
    clip = {'t0': float(t0), 't1': float(t1), 'tracks': []}
    for ti, t in enumerate(model.get('tracks') or []):
        if track_idx is not None and ti != track_idx:
            continue
        picked = [copy.deepcopy(n) for n in (t.get('notes') or [])
                  if float(t0) - 1e-9 <= n[0] < float(t1) - 1e-9]
        if not picked:
            continue
        clip['tracks'].append({'name': t.get('name'), 'channel': t.get('channel'),
                               'program': t.get('program'), 'notes': picked,
                               'ccs': [copy.deepcopy(c) for c in (t.get('ccs') or [])
                                       if float(t0) - 1e-9 <= c[0] < float(t1) - 1e-9]})
        if cut:
            t['notes'] = [n for n in t['notes'] if not (float(t0) - 1e-9 <= n[0] < float(t1) - 1e-9)]
    return clip


def paste(model, clip, at_beat, track_idx=None, merge=True):
    """把片段贴到 `at_beat`（`track_idx` 给了就贴进那一轨，否则按名字匹配/新建轨）"""
    off = float(at_beat) - float(clip.get('t0') or 0)
    added, made = 0, 0
    for src in clip.get('tracks') or []:
        tgt = None
        if track_idx is not None:
            tgt = model['tracks'][track_idx]
        else:
            tgt = next((t for t in model['tracks'] if t.get('name') == src['name']), None)
            if tgt is None:
                tgt = {'index': len(model['tracks']), 'name': src['name'],
                       'channel': src.get('channel', 0), 'program': src.get('program'),
                       'drum': src.get('channel') == 9, 'mute': False, 'solo': False,
                       'hidden': False, 'notes': [], 'ccs': [], 'program_changes': [],
                       'markers': []}
                model['tracks'].append(tgt)
                made += 1
        for n in src['notes']:
            tgt.setdefault('notes', []).append([round(max(0.0, n[0] + off), 6), n[1], n[2], n[3]])
            added += 1
        for c in src.get('ccs') or []:
            tgt.setdefault('ccs', []).append([round(max(0.0, c[0] + off), 6), c[1], c[2]])
        _sort_notes(model, model['tracks'].index(tgt))
    return {'op': 'paste', 'notes': added, 'tracks_created': made, 'at': at_beat}


def duplicate_track(model, track_idx):
    """复制整轨（含音符/CC/音色）→ 新轨下标"""
    src = copy.deepcopy(model['tracks'][track_idx])
    src['index'] = len(model['tracks'])
    src['name'] = (src.get('name') or 'Track') + ' copy'
    src['solo'] = False
    model['tracks'].append(src)
    return {'op': 'duplicate_track', 'from': track_idx, 'to': src['index'],
            'notes': len(src.get('notes') or [])}


def delete_track(model, track_idx):
    """删轨"""
    t = model['tracks'].pop(track_idx)
    for i, x in enumerate(model['tracks']):
        x['index'] = i
    return {'op': 'delete_track', 'removed': t.get('name'),
            'notes': len(t.get('notes') or [])}


# --------------------------------------------------------------------------- 轨道属性
def set_track(model, track_idx, name=None, program=None, channel=None,
              mute=None, solo=None, hidden=None, drum=None):
    """改轨属性（**换乐器** / 通道 / 独奏 / 静音 / 隐藏 —— miditoolbox 的多轨管理）"""
    t = model['tracks'][track_idx]
    before = {k: t.get(k) for k in ('name', 'program', 'channel', 'mute', 'solo', 'hidden', 'drum')}
    if name is not None:
        t['name'] = str(name)
    if program is not None:
        t['program'] = None if program in ('', None) else max(0, min(127, int(program)))
    if channel is not None:
        ch = max(0, min(15, int(channel)))
        t['channel'] = ch
        t['drum'] = (ch == 9) if drum is None else bool(drum)
    if drum is not None:
        t['drum'] = bool(drum)
    for k, v in (('mute', mute), ('solo', solo), ('hidden', hidden)):
        if v is not None:
            t[k] = bool(v)
    return {'op': 'set_track', 'track': track_idx, 'before': before,
            'after': {k: t.get(k) for k in before}}


def clean(model, mode='dedupe'):
    """清理：`dedupe` 去掉完全重复的音（同起/同时值/同音高）· `zero_dur` 去零时值 ·
    `overlap` 修剪同音高重叠（后一个音把前一个截断，DAW 的"mono"行为）"""
    n = 0
    for t in model.get('tracks') or []:
        notes = t.get('notes') or []
        if mode == 'dedupe':
            seen, keep = set(), []
            for x in notes:
                k = (round(x[0], 6), round(x[1], 6), x[2], x[3])
                if k in seen:
                    n += 1
                    continue
                seen.add(k)
                keep.append(x)
            t['notes'] = keep
        elif mode == 'zero_dur':
            keep = [x for x in notes if x[1] > 0]
            n += len(notes) - len(keep)
            t['notes'] = keep
        elif mode == 'overlap':
            by = {}
            for x in sorted(notes, key=lambda z: (z[2], z[0])):
                p = x[2]
                if by.get(p) is not None:
                    prev = by[p]
                    if prev[0] + prev[1] > x[0] + 1e-9:
                        newd = max(1.0 / 64, x[0] - prev[0])
                        if abs(newd - prev[1]) > 1e-9:
                            n += 1
                        prev[1] = newd
                by[p] = x
        else:
            raise SystemExit('未知清理模式 %r（dedupe/zero_dur/overlap）' % (mode,))
    return {'op': 'clean', 'mode': mode, 'changed': n}


def snap_all(model, grid='1/16'):
    """全库吸附（不改时值，只把起点吸到网格并去掉浮点毛刺）"""
    step = grid_step(grid)
    n = 0

    def one(nt):
        nonlocal n
        a = round(float(nt[0]) / step) * step
        if abs(a - nt[0]) > 1e-9:
            n += 1
        nt[0] = round(a, 6)
        nt[1] = round(max(1.0 / 64, nt[1]), 6)
    _each_note(model, None, None, one)
    for i in range(len(model.get('tracks') or [])):
        _sort_notes(model, i)
    return {'op': 'snap', 'grid': grid, 'step': step, 'moved': n}


def stats(model):
    """模型统计（UI 状态栏 + 测试断言都用它）"""
    tr = model.get('tracks') or []
    notes = [n for t in tr for n in (t.get('notes') or [])]
    return {
        'tracks': len(tr), 'sounding_tracks': sum(1 for t in tr if t.get('notes')),
        'notes': len(notes),
        'vel_min': min([n[3] for n in notes] or [0]),
        'vel_max': max([n[3] for n in notes] or [0]),
        'pitch_min': min([n[2] for n in notes] or [0]),
        'pitch_max': max([n[2] for n in notes] or [0]),
        'end_beat': max([n[0] + n[1] for n in notes] or [0.0]),
        'ccs': sum(len(t.get('ccs') or []) for t in tr),
    }


def _sort_notes(model, track_idx):
    if track_idx is None:
        for t in model.get('tracks') or []:
            t['notes'] = sorted(t.get('notes') or [], key=lambda z: (z[0], z[2]))
        return
    t = model['tracks'][track_idx]
    t['notes'] = sorted(t.get('notes') or [], key=lambda z: (z[0], z[2]))


def main():
    print(__doc__)
    return 0


# 编辑器 API 的**操作别名**（UI 习惯的短名 → 本模块的函数名）。
# 为什么要有：面板按钮就写 `op=velocity` / `op=ramp`，而函数名为了可读性是
# `set_velocity` / `ramp_velocity` —— 让唯一的 API 层去兼容短名，
# 比要求前端记住两套命名可靠（实测第一次接就撞了：`没有这个编辑操作：velocity`）。
OP_ALIASES = {
    'velocity': 'set_velocity',
    'vel': 'set_velocity',
    'ramp': 'ramp_velocity',
    'snap': 'snap_all',
    'transpose_key': 'transpose',
    'move': 'move_notes',
    'del': 'delete_notes',
    'add': 'add_note',
}


def resolve_op(name):
    """操作名 → 可调用函数（支持别名）；未知操作**当场报错**，不静默忽略"""
    n = OP_ALIASES.get(name, name)
    fn = globals().get(n)
    if not callable(fn) or n.startswith('_'):
        raise SystemExit('没有这个编辑操作：%s（可用：%s）'
                         % (name, ', '.join(sorted(
                             k for k, v in globals().items()
                             if callable(v) and not k.startswith('_')
                             and k not in ('copy', 'math')))))
    return fn


if __name__ == '__main__':
    sys.exit(main())
