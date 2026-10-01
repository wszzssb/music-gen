#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_playable.py —— **双手可弹性体检**：这首曲子，业余琴手能不能弹？

## 为什么需要（2026-10-01，用户："让简单的音乐人用双手也能弹"）

单乐器独奏化（`solo_instrument.py`）把全部声部并进一条钢琴轨之后，"能不能弹"**没有任何判据**：
实测 `dear_good_friends_solo` **同时按键最多 9 个**、单手跨度 **24 半音**、每拍 7 个按键 ——
MIDI 合法、听感也没坏，但**人弹不了**。可弹性是物理约束（一只手只有五个手指、一张手有多少跨度），
必须单独量。

## 判据从哪来（**实测锚点，不是拍的**）

同一把尺子量 `refs\midi2\` 里 **86 首钢琴曲**（钢琴族音色占比 ≥60% 且非空轨 ≤4）：

| 指标 | p10（= 简单档） | p50（中位） | 我们改之前的 `dear_good_friends_solo` |
|---|---|---|---|
| 同时按键数（max） | **3** | 6 | **9** |
| 单手跨度 p95（半音） | **12** | 17 | 17~19（max 31） |
| 每拍按键数（中位） | **3** | 5 | **7** |
| 同音连击最长串 | — | — | 6 |

`--level easy` 取 **p10~p50 之间偏简单侧**（即库内最简单那一批：`Minuet-in-Mozart` 同按 3 /
跨度 p95 9、`public_domain/03-gavotte` 同按 4 / 跨度 p95 11），`normal` 取中位附近：

| 档 | 同时按键 | 单手跨度 | 每拍按键 | 同音串 | 单手跳进(≤0.5拍) |
|---|---|---|---|---|---|
| `easy`（默认） | ≤5（左手 ≤2 · 右手 ≤3 含旋律） | ≤12（八度） | ≤6 | ≤3 | ≤12 |
| `normal` | ≤7 | ≤14 | ≤8 | ≤4 | ≤14 |

⚠ **门槛必须与削音端同口径**：`solo_instrument.make_playable` 用的就是本表的
`rh`/`lh`/`span`/`beat`/`run`/`jump`（两处漂移 = 削完仍不达标，或判据恒真）。

口径：只数 **onset**（按键那一刻）—— 钢琴按键即衰减，"同时要按下几个键"看的是起音；
1/16 拍格 = 一个时刻，左右手按 `--split`（默认 60 = C4）分。

## 用法

```bash
python scripts\probe_playable.py <song.json 或 .mid>            # 体检（人话报告）
python scripts\probe_playable.py <...> --level normal --split 62 --json
python scripts\probe_playable.py --selftest                      # 尺子自检（已知正/负例）
```

⚠ **判据不是"好听度"**：它只在"能不能弹"这一维上说话 —— 达标不等于好听，
不达标也不等于难听（`20_piano_rain` 是我们认可的曲子，它的单手跨度 p95 是 **33**，
按这把尺子就"不可弹"）。
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()          # noqa: E402
import midi_file as mf                       # noqa: E402
import song_engine as se                     # noqa: E402

LEVELS = {
    # `easy` = 库内 86 首钢琴曲的 **p10~p50 之间偏简单侧**（p10 同按 3 / p50 6 → 取 5：
    # 左手 ≤2（根音+五度）· 右手 ≤3（含旋律）· 单手跨度 ≤八度 · 每拍 ≤6 · 同音串 ≤3）。
    # ⚠ 与削音端的档位必须同口径 —— 那边按这些门槛削，这边按同样的门槛判
    #   （`solo_instrument.make_playable` 用的就是本表的 `rh`/`lh`/`span`/`beat`/`run`/`jump`）。
    'easy': {'poly': 5, 'span': 12, 'beat': 6, 'run': 3, 'jump': 12,
             'rh': 3, 'lh': 2},
    'normal': {'poly': 7, 'span': 14, 'beat': 8, 'run': 4, 'jump': 14,
               'rh': 4, 'lh': 3},
}
SPLIT = 60                                   # C4：左手 <60、右手 ≥60


def load_notes(path):
    """song.json（走引擎展开）或 .mid（直读）→ [(拍, 音高, 力度, 来源)]"""
    out = []
    if str(path).lower().endswith('.json'):
        d = se.load(path)
        ev, _nb = se.build_events(d)
        for tr, lst in ev.items():
            if tr == 'Perc':
                continue
            for (t, _dur, p, v) in lst:
                out.append((float(t), int(p), int(v), tr))
    else:
        m = mf.import_midi(path)
        for tr in m['tracks']:
            if tr.get('drum'):
                continue
            for (bt, _du, pi, ve) in tr['notes']:
                out.append((float(bt), int(pi), int(ve), 'T%d' % tr['index']))
    out.sort()
    return out


def metrics(notes, split=SPLIT):
    """onset 层可弹性读数（全部是"同一时刻要按下几个键"的统计）"""
    at = defaultdict(list)
    for (t, p, v, tr) in notes:
        at[int(round(t * 4))].append((p, v))
    poly, lp, rp, sl, sr = [], [], [], [], []
    cross = 0
    for _g, lst in at.items():
        ps = sorted({p for (p, _v) in lst})
        lh = [p for p in ps if p < split]
        rh = [p for p in ps if p >= split]
        poly.append(len(ps)); lp.append(len(lh)); rp.append(len(rh))
        if lh:
            sl.append(max(lh) - min(lh))
        if rh:
            sr.append(max(rh) - min(rh))
        if lh and rh and max(lh) > min(rh):
            cross += 1
    per_beat = Counter(int(x[0]) for x in notes)
    run = mx = 1
    last = None
    for x in sorted(notes):
        if x[1] == last:
            run += 1
            mx = max(mx, run)
        else:
            run = 1
        last = x[1]
    # 同手相邻 onset（≤0.5 拍）的跳进
    jumps = defaultdict(list)
    prev = {'L': None, 'R': None}
    for g in sorted(at):
        lst = sorted(at[g])
        for hand, sel in (('L', [x for x in lst if x[0] < split]),
                          ('R', [x for x in lst if x[0] >= split])):
            if not sel:
                continue
            cur = (g / 4.0, sel[0][0])
            if prev[hand] is not None and 0 < cur[0] - prev[hand][0] <= 0.5:
                jumps[hand].append(abs(cur[1] - prev[hand][1]))
            prev[hand] = cur

    def q(a, k):
        a = sorted(a)
        return a[min(len(a) - 1, int(len(a) * k))] if a else 0
    beats = sorted(per_beat.values())
    return {
        'notes': len(notes), 'moments': len(at),
        'poly_max': max(poly or [0]), 'poly_p95': q(poly, .95),
        'lh_max': max(lp or [0]), 'rh_max': max(rp or [0]),
        'span_p95': max(q(sl, .95), q(sr, .95)),
        'span_max': max((sl or [0]) + (sr or [0])),
        'beat_med': beats[len(beats) // 2] if beats else 0,
        'beat_max': max(beats or [0]),
        'run': mx,
        'jump_p95': max(q(jumps['L'], .95), q(jumps['R'], .95)),
        'cross': cross,
    }


def verdict(m, level='easy'):
    """→ (是否达标, [(指标, 读数, 门槛, 达标)])"""
    lim = LEVELS[level]
    rows = [('同时按键数 max', m['poly_max'], lim['poly']),
            ('单手跨度 max', m['span_max'], lim['span']),
            ('每拍按键 中位', m['beat_med'], lim['beat']),
            ('同音连击 max', m['run'], lim['run']),
            ('单手跳进 p95', m['jump_p95'], lim['jump'])]
    return all(r[1] <= r[2] for r in rows), rows


def main():
    ap = argparse.ArgumentParser(description='双手可弹性体检（onset 层）')
    ap.add_argument('path', nargs='?', help='song.json 或 .mid')
    ap.add_argument('--level', choices=tuple(LEVELS), default='easy')
    ap.add_argument('--split', type=int, default=SPLIT, help='左右手分界音高（默认 60 = C4）')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.path:
        ap.error('要给 song.json 或 .mid，或加 --selftest')
    notes = load_notes(a.path)
    if not notes:
        raise SystemExit('一个音都没有：%s' % a.path)
    m = metrics(notes, a.split)
    ok, rows = verdict(m, a.level)
    if a.json:
        print(json.dumps({'file': a.path, 'level': a.level, 'ok': ok, 'metrics': m},
                         ensure_ascii=False, indent=1))
        return 0 if ok else 1
    print('== 可弹性体检：%s（档 %s · 分界 %d）' % (os.path.basename(a.path), a.level, a.split))
    print('   音 %d · 时刻 %d · 左右手交叉 %d' % (m['notes'], m['moments'], m['cross']))
    for (name, got, lim) in rows:
        print('   %-16s %4d   门槛 ≤%d   %s' % (name, got, lim, '✓' if got <= lim else '✗ 超'))
    print('   （参考锚点：库内 86 首钢琴曲实测 —— 同按 max p10 3 / p50 6 · 单手跨度 p95'
          ' p10 12 / p50 17 · 每拍按键中位 p10 3 / p50 5）')
    print('   结论：%s' % ('这一档弹得下来' if ok else '超出这一档（要削音或降档）'))
    return 0 if ok else 1


# ---------------------------------------------------------------- 自检
def _mk(notes):
    return [(float(t), int(p), 90, 'X') for (t, p) in notes]


def selftest():
    """尺子自检（硬门）：已知正/负例 + 分界口径 —— 判据不许恒真也不许恒假。"""
    easy = _mk([(0, 60), (0, 64), (1, 62), (1, 67), (2, 55), (2, 60), (3, 64), (3, 67)])
    m = metrics(easy)
    ok, rows = verdict(m, 'easy')
    assert ok, '简单素材（同刻 ≤2 音、跨度 ≤7）必须判达标：%r' % (rows,)
    hard = _mk([(0, p) for p in (40, 43, 47, 52, 55, 59, 64, 67, 71)] +
               [(1, p) for p in (41, 45, 48, 53, 57, 60, 65, 69, 72)])
    m2 = metrics(hard)
    ok2, rows2 = verdict(m2, 'easy')
    assert not ok2, '难素材（同刻 9 音）必须判不达标'
    assert m2['poly_max'] == 9 and m2['span_max'] >= 19, m2
    # 分界口径：<60 归左手、≥60 归右手
    m3 = metrics(_mk([(0, 59), (0, 60)]))
    assert m3['lh_max'] == 1 and m3['rh_max'] == 1 and m3['cross'] == 0, m3
    # 跳进：同手相邻 0.25 拍跨 24 半音 → 必须被量到
    m4 = metrics(_mk([(0, 60), (0.25, 84)]))
    assert m4['jump_p95'] == 24, m4
    # 空素材不崩
    m5 = metrics([])
    assert m5['notes'] == 0 and m5['poly_max'] == 0, m5
    print('probe_playable --selftest：正例达标 / 负例不达标 / 分界·跳进·空素材 全过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
