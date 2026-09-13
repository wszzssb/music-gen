#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_melody_health.py —— **旋律听感体检**：量那些"频段守卫管不到"的毛病。

为什么需要它：这一轮用户连报三次听感问题（"镫镫地卡"、"d d d d ddd"），而当时
84 项自检**全绿** —— 它们只管频段/响度/结构/和弦贴合，没有一项在看旋律的**形态**。
本工具补的就是这一层，量 6 维：

  ① 密度（音/小节）       —— 太低 = 音少、重复感被放大（实测 56 小节只剩 54 个音）
  ② 同音重复%            —— 画像的 `iv=0` 是 F0 连续帧造成的放大，不是真方言
  ③ **最长连续同音串**    —— ≥4 就是"d d d d ddd"（听感"卡住/念经"）
  ④ 碎音%（≤0.25 拍）    —— "卡卡的"的来源之一；⚠ 快曲的装饰音会被误算，需对照画像
  ⑤ 强拍和弦贴合%        —— 硬纪律（`melody_chord_fit` 要求 ≥70%）
  ⑥ 落点格数 / 正拍%     —— 节奏语言的丰富度（手写常见只有 2-4 格）

用法:
  python scripts\probe_melody_health.py            # 全库
  python scripts\probe_melody_health.py 31_f123_night_drift

阈值（`--strict` 时非零退出，供 CI/自检用）：
  最长同音串 ≤3 · 密度 ≥1.2 · 碎音 ≤8% · 强拍贴合 =100%（有强拍样本时）
⚠ **碎音这一维要看画像**：快曲（150BPM+）里十六分音符是正常语言，慢曲里才是截断伪影。
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）

import song_engine as SE                            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SONGS = os.path.join(ROOT, 'songs')

MAX_RUN = 4          # 最长连续同音上限（4 连在慢歌里不算罕见；≥5 才是"念经"）
SMALL_IV_MAX = 35.0  # |iv|≤1（同音/半音级进）占比上限 —— "小步打转"的判据
MIN_DENS = 1.2       # 每小节最少音符数
MAX_CHOP = 8.0       # 碎音（≤0.25 拍）比例上限


def probe(path):
    d = json.load(open(path, encoding='utf-8'))
    ch = d['chords']
    strong = SE.strong_beats(d.get('meter'))
    bars = sum(s['bars'] for s in d['sections'])
    notes, ons = [], {}
    tot_s = fit_s = 0
    for si, sec in enumerate(d['sections']):
        base = sum(s['bars'] for s in d['sections'][:si])
        for (b, bt, du, p) in d['melody'].get(sec['melody'], []):
            notes.append((base * 4 + b * 4 + bt, du, p))
            ons[int(round(bt * 4)) % 16] = ons.get(int(round(bt * 4)) % 16, 0) + 1
            if round(bt, 2) in strong and b < len(sec.get('chords') or []):
                tones = [t % 12 for t in ch[sec['chords'][b]][1]]
                tot_s += 1
                fit_s += p % 12 in tones
    notes.sort()
    n = len(notes)
    if not n:
        return None
    same = sum(1 for i in range(1, n) if notes[i][2] == notes[i - 1][2])
    run = mx = 1
    for i in range(1, n):
        run = run + 1 if notes[i][2] == notes[i - 1][2] else 1
        mx = max(mx, run)
    chop = sum(1 for x in notes if round(x[1] * 4) <= 1)
    onb = sum(v for k, v in ons.items() if k % 4 == 0)
    pitches = [x[2] for x in notes]
    ivs = [pitches[i + 1] - pitches[i] for i in range(len(pitches) - 1)]
    small = 100.0 * sum(1 for x in ivs if abs(x) <= 1) / max(1, len(ivs))
    return {
        'name': os.path.basename(os.path.dirname(path)), 'notes': n, 'bars': bars,
        'bpm': d.get('bpm') or 0, 'dens': n / bars if bars else 0,
        'same': 100 * same / n, 'maxrun': mx, 'chop': 100 * chop / n,
        'grids': len(ons), 'onbeat': 100 * onb / n,
        'small': small, 'uniq': len(set(pitches)),
        'span': (max(pitches) - min(pitches)) if pitches else 0,
        'fit': (100 * fit_s / tot_s) if tot_s else -1,
        'gen': (d.get('melody_gen') or {}).get('profile'),
    }


def _prof_short(gen):
    """画像自己的"极短音"占比（≤0.25 拍 = 16 分格 1）。

    碎音这一维**必须对照画像**：同样 18% 的十六分，在 66BPM 的密集曲里是方言、
    在慢歌里是"长音被截断"的伪影。没有画像的曲子才退回按速度分档。"""
    if not gen:
        return None
    p = os.path.join(ROOT, 'refs', 'melody', gen + '_melody.json')
    if not os.path.isfile(p):
        return None
    h = json.load(open(p, encoding='utf-8')).get('dur16_hist') or {}
    tot = sum(h.values())
    return (100.0 * h.get('1', 0) / tot) if tot else None


def issues(r, strict=False):
    """→ 问题清单（判据见模块 docstring）。"""
    out = []
    if r['maxrun'] > MAX_RUN:
        out.append('同音串%d' % r['maxrun'])
    if r['dens'] < MIN_DENS:
        out.append('密度%.2f' % r['dens'])
    # **小步打转**：|iv|≤1 占比。这是"d d d d ddd"的真身 —— 早先只量相邻同音，
    # 结果用户报的问题一条都抓不到（生成器产物曾达 39~44%，手写曲只有 6~15%）。
    if r.get('small', 0) > SMALL_IV_MAX:
        out.append('小步%.0f%%' % r['small'])
    lim = MAX_CHOP if (strict or (r['bpm'] or 0) < 140) else 20.0
    sh = _prof_short(r.get('gen'))
    if sh is not None and not strict:
        lim = max(lim, min(30.0, sh * 2.0))      # 画像就有这么多 → 方言；2 倍以内算正常波动
    if r['chop'] > lim:
        out.append('碎音%.0f%%' % r['chop'])
    if 0 <= r['fit'] < 100:
        out.append('强拍%.0f%%' % r['fit'])
    return out


def collect(names=None):
    rows = []
    for d in sorted(glob.glob(os.path.join(SONGS, '*'))):
        nm = os.path.basename(d)
        if names and nm not in names:
            continue
        p = os.path.join(d, 'song.json')
        if not os.path.isfile(p):
            continue
        r = probe(p)
        if r:
            rows.append(r)
    return rows


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    rows = collect(set(args) if args else None)
    if not rows:
        print('没有可体检的曲目')
        return 1
    print('体检 %d 首\n' % len(rows))
    print('%-22s %5s %7s %6s %6s %6s %6s %7s %7s %s'
          % ('曲目', '音数', '音/小节', '同音%', '最长串', '小步%', '碎音%', '落点格',
             '正拍%', '强拍'))
    bad = []
    for r in sorted(rows, key=lambda x: (-x['maxrun'], x['dens'])):
        iss = issues(r)
        if iss:
            bad.append((r['name'], iss))
        print('%-22s %5d %7.2f %5.0f%% %6d %5.0f%% %5.0f%% %6d %6.0f%% %6s%s'
              % (r['name'], r['notes'], r['dens'], r['same'], r['maxrun'], r['small'],
                 r['chop'], r['grids'], r['onbeat'],
                 ('%.0f%%' % r['fit']) if r['fit'] >= 0 else '无',
                 '  ← ' + '、'.join(iss) if iss else ''))
    print('\n有问题 %d / %d 首' % (len(bad), len(rows)))
    if '--strict' in sys.argv and bad:
        print('（--strict：非零退出）')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
