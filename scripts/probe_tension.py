#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_tension.py —— **张力曲线**：一首曲子从头到尾"紧不紧"有没有起落。

为什么需要它（2026-09-15）：用户问"还能不能优化 MIDI 的编写方法让他更好听"。
把常规维度全量过一遍后（声部进行、和声节奏、落点、时值、音域、人性化，见
`probe_voicing.py` 的对照表）**全都在模板范围内**，只剩这一维**从没量过** ——
听感上"平淡、没有推进感"最可能的藏身处。

口径（三档都是**MIDI 层可算**的代理量，模板与我们同一套）：
  · **d 密度**：本小节发声的音符数
  · **s 跨度**：本小节最高音 − 最低音（半音）
  · **x 摩擦**：本小节内**同时发声**的小二度（pc 差 1）与三全音（pc 差 6）对数
  张力 = z(d) + z(s) + 2·z(x)（各自对**本曲**做 z-score 再相加，所以量的是**曲内起伏**，
  跨曲比较看的是**曲线的形状**而不是绝对值）

判据（**只报告、不设门**）：看 ① 全曲标准差（有没有起伏）、② 首尾段均值差
（"越走越紧"还是"越来越松"）、③ 最高点落在哪里（模板通常在中后段）。
模板真值由本工具一并打印，用来对照形状。

用法:
  python scripts\probe_tension.py                        # 模板 + 全库
  python scripts\probe_tension.py 43_joy_to_sorrow 45_sorrow_to_joy
"""
import glob
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()          # noqa: E402

import midi_probe as mp                      # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def curve(path):
    """→ [(小节, 张力)]；样本太少返回 None"""
    res = mp.parse_full(path)
    div = float(res['division'] or 480)
    per = {}
    for t in res['tracks']:
        if t.get('channel') == 9:
            continue
        for (a, d, p, v) in t['notes']:
            per.setdefault(int(a / div / 4.0), []).append((a / div, p))
    if len(per) < 8:
        return None
    raw = []
    for b in sorted(per):
        notes = per[b]
        ps = [p for (_t, p) in notes]
        d = len(notes)
        s = max(ps) - min(ps)
        # 同时发声的摩擦音程
        # 同时发声的摩擦音程 —— **按「音级对」去重**：同一个 pc 对不同八度叠层只算一次。
        # （初版按"音高对"计数，八度叠层（Bass −12 / Glock +12 / sub）会把 Gmaj7 的
        #  G–F# 大七度重复计成 8 对，直接把 43 号的张力峰值顶到第 3 小节 —— 口径伪影。）
        x = 0
        slots = {}
        for (tt, p) in notes:
            slots.setdefault(round(tt * 4), set()).add(p % 12)
        for pcs in slots.values():
            v = sorted(pcs)
            for i in range(len(v)):
                for j in range(i + 1, len(v)):
                    if (v[j] - v[i]) % 12 in (1, 6, 11):
                        x += 1
        raw.append((b, d, s, x))

    def z(vals):
        n = len(vals)
        m = sum(vals) / n
        sd = (sum((v - m) ** 2 for v in vals) / n) ** 0.5 or 1.0
        return [(v - m) / sd for v in vals]

    zd, zs, zx = z([r[1] for r in raw]), z([r[2] for r in raw]), z([r[3] for r in raw])
    return [(raw[i][0], zd[i] + zs[i] + 2.0 * zx[i]) for i in range(len(raw))]


def report(tag, cv):
    vals = [v for (_b, v) in cv]
    n = len(vals)
    m = sum(vals) / n
    sd = (sum((v - m) ** 2 for v in vals) / n) ** 0.5
    head = sum(vals[:max(1, n // 4)]) / max(1, n // 4)
    tail = sum(vals[-(max(1, n // 4)):]) / max(1, n // 4)
    peak = max(range(n), key=lambda i: vals[i])
    print('   %-28s 小节%3d  起伏σ %5.2f  首段 %+5.2f → 尾段 %+5.2f（Δ %+5.2f）  最高点在 %d%%'
          % (tag[:28], n, sd, head, tail, tail - head, int(100.0 * peak / n)))


def templates():
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
    want = sys.argv[1:] or None
    print('=== 真实模板（张力曲线的形状真值）===')
    sds, deltas = [], []
    for rel, fp in templates()[:12]:
        try:
            cv = curve(fp)
        except Exception:                                      # noqa: BLE001
            continue
        if not cv:
            continue
        report(os.path.basename(rel), cv)
        vals = [v for (_b, v) in cv]
        n = len(vals)
        m = sum(vals) / n
        sds.append((sum((v - m) ** 2 for v in vals) / n) ** 0.5)
        deltas.append(sum(vals[-(max(1, n // 4)):]) / max(1, n // 4)
                      - sum(vals[:max(1, n // 4)]) / max(1, n // 4))
    if sds:
        v = sorted(sds)
        w = sorted(deltas)
        print('   → 模板：起伏σ min %.2f / 中位 %.2f / max %.2f ；首尾差 min %+.2f / 中位 %+.2f / max %+.2f'
              % (v[0], v[len(v) // 2], v[-1], w[0], w[len(w) // 2], w[-1]))
    print()
    print('=== 我们自己的歌 ===')
    for sd in sorted(glob.glob(os.path.join(REPO, 'songs', '*'))):
        sid = os.path.basename(sd)
        if want and sid not in want:
            continue
        cands = [x for x in glob.glob(os.path.join(sd, '*.mid'))
                 if '_sf' not in x and '.hum' not in x]
        if not cands:
            continue
        try:
            cv = curve(cands[0])
        except Exception:                                      # noqa: BLE001
            continue
        if cv:
            report(sid, cv)


if __name__ == '__main__':
    main()
