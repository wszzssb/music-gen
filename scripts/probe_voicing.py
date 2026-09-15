#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_voicing.py —— **声部进行体检**：伴奏各声部移动多平滑、有没有声部交叉、和声节奏多快。

为什么需要它（2026-09-15）：用户问"还能不能优化 MIDI 的编写方法让他更好听"，
于是把网上的主流方案逐条对照了一遍 —— SATB 对位（`fleet-midi-harmonizer` 那类）、
量化人性化（`schulz.audio: Beyond the Grid`）、微妙表情（`Numula`）、动机/乐句结构
（`Motifs, Phrases, and Beyond`）。**结论是这些方向的短板我们基本没有**：
人性化有 `render_midi.humanize_midi`、乐句表情有 `song_engine.mel_dyn_env`、
结构有 `melody_gen` 的 v2 动机层。唯一**从没量过**的是"声部进行本身"——
我们只有 `melody_voice_leading` 在**守卫**平行五八度，从没看过"移动多平滑"。
本工具补的就是这一维（工具与守卫共用一套口径）。

口径（与真实模板同源，`refs/midi2/`）：
  · **声部移动**：相邻小节之间，把伴奏音**由低到高第 k 个**两两配对，取 |Δ音高| 的中位。
    越小越像"有人在写声部"，越大越像"每小节独立拍一个和弦"。
  · **保持不动%**：|Δ|=0 的占比 —— 对位写作里"共同音保持"的核心指标。
  · **大跳%**：|Δ|≥5 的比例。
  · **声部交叉%**：低音线跑到中音线之上的时间占比（对位禁忌之一）。
  · **和声节奏**：和弦变化数 / 小节数。

实测真值（12 首模板）：
  | 指标 | 模板 min | 中位 | max | 我们（43/45/42/36/40） |
  |---|---|---|---|---|
  | 声部移动中位 | 2 | **4** | 5 | 2~6 ✓ |
  | 保持不动% | 7.6 | **16.9** | 24.1 | 12~19 ✓ |
  | 大跳% | 28.3 | **38.6** | 60.8 | 6~63 ⚠ 宽 |
  | 声部交叉% | 1.1 | **11.4** | 33.3 | 0~29 ✓ |
  | 和弦/小节 | 0.89(主题) | **0.90** | — | 0.51~1.00 ✓ |
**在本范围内 ⇒ 不是瓶颈**，所以本工具**只报告、不设门**（它没有对应的自检项）。

用法:
  python scripts\probe_voicing.py              # 全库 + 模板对照
  python scripts\probe_voicing.py 45_sorrow_to_joy
"""
import glob
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()          # noqa: E402  控制台编码兜底

import midi_probe as mp                      # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _moves(path, mel_name=None):
    """→ (移动中位, 保持%, 大跳%, 交叉%)；样本太少返回 None"""
    res = mp.parse_full(path)
    div = float(res['division'] or 480)
    per = {}
    for t in res['tracks']:
        if t.get('channel') == 9:
            continue
        if mel_name and (t.get('name') or '') == mel_name:
            continue                                  # 伴奏 = 除旋律与打击
        for (a, d, p, v) in t['notes']:
            per.setdefault(int(a / div / 4.0), []).append(p)
    bars = sorted(per)
    moves, cross = [], 0
    for i in range(1, len(bars)):
        if bars[i] != bars[i - 1] + 1:
            continue
        a, b = sorted(set(per[bars[i - 1]])), sorted(set(per[bars[i]]))
        for k in range(min(len(a), len(b))):
            moves.append(abs(b[k] - a[k]))
        if a and b and a[0] > b[len(b) // 2]:
            cross += 1
    if len(moves) < 10:
        return None
    moves.sort()
    n = len(moves)
    return (moves[n // 2],
            100.0 * sum(1 for x in moves if x == 0) / n,
            100.0 * sum(1 for x in moves if x >= 5) / n,
            100.0 * cross / max(1, len(bars) - 1))


def _harm_rhythm(song_json):
    """和弦变化数 / 小节数"""
    try:
        d = json.load(io.open(song_json, encoding='utf-8'))
    except Exception:                                          # noqa: BLE001
        return None
    bars = sum(s.get('bars') or 0 for s in (d.get('sections') or []))
    if not bars:
        return None
    ch, prev = 0, None
    for s in d['sections']:
        for c in (s.get('chords') or []):
            if c != prev:
                ch += 1
            prev = c
    return ch / float(bars)


def _templates():
    out = []
    for th in ('cheerful', 'sorrow'):
        p = os.path.join(REPO, 'refs', 'themes', '%s.json' % th)
        if not os.path.isfile(p):
            continue
        d = json.load(io.open(p, encoding='utf-8'))
        for tm in (d.get('templates') or []):
            rel = tm if isinstance(tm, str) else (tm.get('path') or tm.get('file'))
            fp = os.path.join(REPO, 'refs', 'midi2', rel)
            if os.path.isfile(fp):
                out.append((rel, fp))
    return out


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else None
    print('%-30s %8s %8s %8s %8s %8s' % ('', '移动中位', '保持%', '大跳%', '交叉%', '和弦/小节'))
    rows = []
    print('--- 真实模板（只有 MIDI，没有 song.json → 无和弦节奏列）---')
    for rel, fp in _templates()[:12]:
        m = _moves(fp)
        if not m:
            continue
        rows.append(m)
        print('%-30s %8d %8.0f %8.0f %8.0f %8s'
              % (os.path.basename(rel)[:30], m[0], m[1], m[2], m[3], '-'))
    if rows:
        for i, lab in enumerate(('移动中位', '保持%', '大跳%', '交叉%')):
            v = sorted(r[i] for r in rows)
            print('    → 模板 %-7s min %.1f / 中位 %.1f / max %.1f'
                  % (lab, v[0], v[len(v) // 2], v[-1]))
    print()
    print('--- 我们自己的歌 ---')
    for sd in sorted(glob.glob(os.path.join(REPO, 'songs', '*'))):
        sid = os.path.basename(sd)
        if want and sid != want:
            continue
        cands = [x for x in glob.glob(os.path.join(sd, '*.mid'))
                 if '_sf' not in x and '.hum' not in x]
        if not cands:
            continue
        m = _moves(cands[0], mel_name='Melody')
        hr = _harm_rhythm(os.path.join(sd, 'song.json'))
        if not m:
            continue
        print('%-30s %8d %8.0f %8.0f %8.0f %8s'
              % (sid[:30], m[0], m[1], m[2], m[3],
                 ('%.2f' % hr) if hr is not None else '-'))


if __name__ == '__main__':
    main()
