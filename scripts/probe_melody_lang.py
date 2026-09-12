#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_melody_lang.py —— **旋律语言重合度体检**：量"是不是每首歌都一个说话方式"。

和 `melody_distinct`（自检项，量连续 8 音的形状复用）分工不同：
  · 形状级（`melody_distinct`）：抓"照抄/退化" —— 同一支旋律换个调/换配器
  · **分布级（本工具）**：抓"两个人说着同一种方言" —— 用户听感"怎么这么多歌都像"
    的真正来源。实测：全库跨曲共享旋律片段只有 0.5%（不是照抄），
    但**旋律语言**在 2026-09-13 前落点重合 93–99%、时值 87–98%。

把每首曲子的旋律按 sections 拼成一条线，抽 5 组归一化分布：
  ① 落点：onset 在 16 分格里的位置      → 律动语言（正拍/反拍/切分）
  ② 时值：音符长度分档                  → 呼吸语言（长短相间还是均等）
  ③ 音程：相邻音的半音差（-12..+12）     → 走向语言（级进/同音/跳进）
  ④ 句长：按休止切出的乐句（小节分档）    → 造句语言（碎句还是长句）
  ⑤ 拱形：每 2 小节音高的极差与净位移     → 起伏语言（平/拱/一路向上）
两两相似度 = 5 组直方图的平均交叠率。判读：≥85% = 孪生（同一种说话方式）。

用法:
  python scripts\probe_melody_lang.py [--top N]     # 曲目两两 + 逐维分解 + 各曲最近画像

改旋律之后、交付之前跑一次：**孪生对（≥85%）应当为 0**；维度分解里
"落点/音程"两项的全库平均重合度若明显回升，说明又回到"一套口音"了。
"""
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SONGS = os.path.join(ROOT, 'songs')
MELDIR = os.path.join(ROOT, 'refs', 'melody')
TWIN = 0.85                     # ≥ 这个相似度 = 孪生（同一种说话方式）
DIMS = [('onset', '落点(律动)'), ('dur', '时值(呼吸)'), ('iv', '音程(走向)'),
        ('phrase', '句长(造句)'), ('arch', '拱形(起伏)')]


def notes_of(path):
    """song.json → [(绝对起始拍, 时值, 音高)]（按 sections 拼接）"""
    j = json.load(open(path, encoding='utf-8'))
    bb = 4.0 if (j.get('meter') or [4, 4]) == [4, 4] else 3.0
    pos, out = 0.0, []
    for sec in j['sections']:
        for x in (j['melody'].get(sec['melody']) or []):
            if 0 <= x[0] < sec['bars']:
                out.append((pos + x[0] * bb + x[1], x[2], x[3]))
        pos += sec['bars'] * bb
    out.sort()
    return out, (bb == 4.0)


def _hist(vals, keys):
    v = np.zeros(len(keys), dtype=float)
    idx = {k: i for i, k in enumerate(keys)}
    for x in vals:
        if x in idx:
            v[idx[x]] += 1
    s = v.sum()
    return v / s if s else v


def feats(notes, is44=True):
    """一条旋律 → 5 组归一化分布（非 4/4 跳过落点维，格数不同不可比）"""
    if not notes:
        return None
    ons = [round(n[0] * 4) % 16 for n in notes] if is44 else None
    durs = [min(16, max(1, round(n[1] * 4))) for n in notes]
    ivs = [max(-12, min(12, notes[i + 1][2] - notes[i][2]))
           for i in range(len(notes) - 1)]
    PHR = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]
    phrases, cur = [], 0.0
    for i, n in enumerate(notes):
        nxt = notes[i + 1][0] if i + 1 < len(notes) else n[0] + n[1]
        cur += n[1]
        if nxt - (n[0] + n[1]) > 0.5:
            phrases.append(cur / 4.0)
            cur = 0.0
    if cur > 0:
        phrases.append(cur / 4.0)
    AS = [1, 2, 3, 4, 6, 8, 12]
    arch = []
    per = 8.0 if is44 else 6.0
    for t in np.arange(0, notes[-1][0] + per, per):
        w = [n[2] for n in notes if t <= n[0] < t + per]
        if len(w) >= 2:
            arch.append((next((a for a in AS if max(w) - min(w) <= a), 12),
                         next((a for a in AS if abs(w[-1] - w[0]) <= a), 12)))
    f = {}
    if ons is not None:
        f['onset'] = _hist(ons, list(range(16)))
    f['dur'] = _hist(durs, list(range(1, 17)))
    f['iv'] = _hist(ivs, list(range(-12, 13)))
    f['phrase'] = _hist([next((k for k in PHR if b <= k), 8.0) for b in phrases], PHR)
    f['arch'] = _hist([a * 100 + b for a, b in arch],
                      [a * 100 + b for a in AS for b in AS])
    return f


def prof_feats(p):
    """旋律画像 json → 同尺度的分布（画像没有的维度就不放，比对时自动跳过）"""
    f = {}
    if p.get('onset16_hist'):
        f['onset'] = _hist([int(k) for k, v in p['onset16_hist'].items()
                            for _ in range(v)], list(range(16)))
    if p.get('dur16_hist'):
        f['dur'] = _hist([int(k) for k, v in p['dur16_hist'].items()
                          for _ in range(v)], list(range(1, 17)))
    if p.get('interval_hist'):
        f['iv'] = _hist([max(-12, min(12, int(k)))
                         for k, v in p['interval_hist'].items() for _ in range(v)],
                        list(range(-12, 13)))
    if p.get('phrase_bars'):
        PHR = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]
        f['phrase'] = _hist([next((k for k in PHR if b <= k), 8.0)
                             for b in p['phrase_bars']], PHR)
    return f or None


def sim(fa, fb):
    """平均交叠率（只比两边都有的维度）"""
    common = [k for k in fa if k in fb]
    if not common:
        return 0.0
    hs = []
    for k in common:
        a, b = fa[k], fb[k]
        if a.sum() and b.sum():
            hs.append(float(np.minimum(a / a.sum(), b / b.sum()).sum()))
    return sum(hs) / len(hs) if hs else 0.0


def report(songs_dir=None, meldir=None):
    """→ dict(items, pairs, twin, dims, profs)：供自检与 CLI 共用"""
    items = {}
    for d in sorted(glob.glob(os.path.join(songs_dir or SONGS, '*'))):
        p = os.path.join(d, 'song.json')
        if os.path.isfile(p):
            n, is44 = notes_of(p)
            f = feats(n, is44)
            if f:
                items[os.path.basename(d)] = f
    profs = {}
    for p in sorted(glob.glob(os.path.join(meldir or MELDIR, '*.json'))):
        pf = prof_feats(json.load(open(p, encoding='utf-8')))
        if pf:
            profs[os.path.basename(p).replace('_melody.json', '')] = pf
    names = list(items)
    pairs = sorted(((sim(items[a], items[b]), a, b)
                    for i, a in enumerate(names) for b in names[i + 1:]), reverse=True)
    twin = [p for p in pairs if p[0] >= TWIN]
    dims = {}
    for k, _cn in DIMS:
        v = [sim({k: items[a][k]}, {k: items[b][k]})
             for i, a in enumerate(names) for b in names[i + 1:]
             if k in items[a] and k in items[b]]
        dims[k] = (sum(v) / len(v), max(v)) if v else (0.0, 0.0)
    return {'items': items, 'pairs': pairs, 'twin': twin, 'dims': dims, 'profs': profs}


def main():
    top_n = int(sys.argv[sys.argv.index('--top') + 1]) if '--top' in sys.argv else 3
    r = report()
    items, names = r['items'], list(r['items'])
    print('参检 %d 首曲目 + %d 份旋律画像\n' % (len(items), len(r['profs'])))
    print('【最像的曲目对】语言重合度（落点/时值/音程/句长/拱形 的平均交叠率）')
    for c, a, b in r['pairs'][:top_n]:
        print('  %3.0f%%  %-22s ~ %s' % (c * 100, a, b))
    if r['pairs']:
        print('  ...')
        for c, a, b in r['pairs'][-2:]:
            print('  %3.0f%%  %-22s ~ %s' % (c * 100, a, b))
    print('\n孪生对（≥%.0f%% = 同一种说话方式）：%d 对' % (TWIN * 100, len(r['twin'])))
    for c, a, b in r['twin']:
        print('  !! %3.0f%%  %s ~ %s' % (c * 100, a, b))
    print('\n【维度分解】全库两两平均重合度（越高 = 大家越说同一种话）')
    for k, cn in DIMS:
        avg, hi = r['dims'][k]
        print('  %-12s 平均 %3.0f%%  最高 %3.0f%%' % (cn, avg * 100, hi * 100))
    print('\n【每首最像的一首】')
    for a in names:
        best = max(((sim(items[a], items[b]), b) for b in names if b != a),
                   default=(0.0, '-'))
        print('  %-22s → %5.0f%%  %s%s' % (a, best[0] * 100, best[1],
                                           '  ← 孪生' if best[0] >= TWIN else ''))
    if r['profs']:
        print('\n【各曲离哪份画像方言最近】（画像没有的维度自动跳过）')
        for a in names:
            sc = sorted(((sim(items[a], pf), pn) for pn, pf in r['profs'].items()),
                        reverse=True)
            if sc:
                print('  %-22s 最近 %5.0f%% %-26s 最远 %5.0f%% %s'
                      % (a, sc[0][0] * 100, sc[0][1], sc[-1][0] * 100, sc[-1][1]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
