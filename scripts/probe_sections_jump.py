#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量每首曲的**段间突变** —— 落实用户首要标准"和谐、不突兀"。

对每首曲按 `sections` 的 bars 切成段，算相邻段的：
  · 质心相对跳变（%）
  · RMS 跳变（dB）
依据：46 号 Intro(3218Hz) → A(4389Hz) 是 **+36%**，用户当场听出
"从 7 秒开始音轨不是很配合、听起来分开了" —— 所以段间突变就是"突兀"的一种可测形态。
"""
import glob
import json
import os
import sys

import numpy as np

ROOT = r'D:\test\galgame\music-gen'
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
os.chdir(ROOT)
import metrics  # noqa: E402

rows = []
for p in sorted(glob.glob('songs/*/song.json')):
    sid = os.path.basename(os.path.dirname(p))
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception:
        continue
    bpm = d.get('bpm') or 120.0
    hit = glob.glob(os.path.join('songs', sid, '*_sf.wav'))
    if not hit:
        continue
    m, sr, x = metrics.load(hit[0])
    secs = d.get('sections') or []
    if len(secs) < 3:
        continue
    bar_s = 4 * 60.0 / float(bpm)
    segs, t = [], 0.0
    for s in secs:
        b = int(s.get('bars') or 0)
        a0, a1 = int(t * sr), int((t + b * bar_s) * sr)
        if a1 - a0 > sr:                      # 至少 1 秒才算
            seg = x[a0:a1]
            mono = seg.mean(axis=1) if seg.ndim > 1 else seg
            S, f = metrics._avg_spec(mono, sr)
            segs.append((s.get('name'), metrics.centroid(mono, sr, S, f),
                         metrics.rms_db(mono)))
        t += b * bar_s
    if len(segs) < 3:
        continue
    worst_c, worst_r, where = 0.0, 0.0, ''
    for (n0, c0, r0), (n1, c1, r1) in zip(segs, segs[1:]):
        dc = abs(c1 - c0) / max(1.0, c0) * 100.0
        dr = abs(r1 - r0)
        if dc > worst_c:
            worst_c, where = dc, '%s→%s' % (n0, n1)
        worst_r = max(worst_r, dr)
    rows.append((sid, worst_c, worst_r, where))

rows.sort(key=lambda r: -r[1])
print('%-22s %8s %8s  %s' % ('曲目', '质心跳%', 'RMS跳dB', '最陡处'))
for sid, c, r, w in rows:
    flag = '  <== 突变' if (c > 30 or r > 6) else ''
    print('%-22s %7.0f%% %8.1f  %s%s' % (sid, c, r, w, flag))
cs = [r[1] for r in rows]
rs = [r[2] for r in rows]
print()
print('%d 首：质心跳变 中位 %.0f%% / 最大 %.0f%%   RMS 跳变 中位 %.1fdB / 最大 %.1fdB'
      % (len(rows), sorted(cs)[len(cs) // 2], max(cs),
         sorted(rs)[len(rs) // 2], max(rs)))
