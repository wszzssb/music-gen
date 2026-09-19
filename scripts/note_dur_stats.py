#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""note_dur_stats.py —— 逐轨量「音数 / 时值中位 / 碎音率」，和另一份 MIDI 对照。

为什么要有它（2026-09-19）：用户的听感反馈必须能**落到数上**才改得动。
那轮反馈"太杂乱，而且不流畅"→ 逐轨量出来才是这句结论（BGM16）：

    ┌─────────────┬──────────────────────┬───────────────────────┐
    │ 轨          │ 我出的（修前）        │ 认可版 v5_source.mid   │
    ├─────────────┼──────────────────────┼───────────────────────┤
    │ Piano       │ 0.190s · 碎音 30.4%  │ 0.264s · 2.7%         │
    │ Bass        │ 0.175s · 22.1%       │ 0.232s · 2.7%         │
    │ Guitar      │ 0.100s · 92.5%       │ 0.344s · 3.9%         │
    │ Strings     │ 0.400s ·  7.8%       │ 0.600s · 0.9%         │
    │ Perc/Drums  │ 0.010s · 100%        │ 0.012s · 100%（一样） │
    └─────────────┴──────────────────────┴───────────────────────┘

**碎音** = 时值 ≤ 0.25 拍的音（16 分音符）。判读：
  · **旋律轨**（Piano/Bass/Guitar/Strings）碎音率应 **< 5%**、时值中位 **≥ 0.25s**；
  · **打击轨**（Drums/Perc）碎音率**本来就该 ~100%** —— 别拿这条去"修"鼓。
⚠ **一律换算成秒再比**：本链的 `song.mid` 是 120BPM 语义、参考曲常是 150BPM，
  同一个"0.55 拍"在两边不是同一个时长（跨曲比"拍"会得出错的倍数，实测踩过）。

用法:
  python scripts\note_dur_stats.py <我的.mid> [对照.mid]
  python scripts\note_dur_stats.py --floor 0.55 <我的.mid>     # 顺带报"低于下限的音占比"
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cli_utf8 as _cu                                              # noqa: E402
_cu.setup()

import midi_file                                                    # noqa: E402

CHOP = 0.25          # 碎音判据：≤ 0.25 拍


def measure(path, floor_beats=0.0):
    """→ (bpm, 逐轨统计)。时值统一换算成**秒**（跨曲可比）。"""
    m = midi_file.import_midi(path)
    spb = 60.0 / float(m.get('bpm') or 120.0)
    out = []
    for t in m.get('tracks') or []:
        ns = t.get('notes') or []
        if not ns:
            continue
        ds = sorted(float(n[1]) for n in ns)
        n = len(ds)
        chop = sum(1 for x in ds if x <= CHOP + 1e-9)
        row = {'name': t.get('name') or '(无名)', 'n': n, 'med': ds[n // 2] * spb,
               'mean': (sum(ds) / n) * spb, 'chop': 100.0 * chop / n}
        if floor_beats > 0:
            row['under'] = 100.0 * sum(1 for x in ds if x < floor_beats - 1e-9) / n
        out.append(row)
    return float(m.get('bpm') or 120.0), out


def show(tag, path, floor_beats=0.0):
    bpm, rows = measure(path, floor_beats)
    print('%s  %s' % (tag, os.path.basename(path)))
    tot = sum(r['n'] for r in rows)
    print('  bpm=%.1f · 轨 %d · 音 %d' % (bpm, len(rows), tot))
    hdr = '    %-24s %6s %11s %9s %9s' % ('轨', '音数', '时值中位s', '均值s', '碎音率')
    if floor_beats > 0:
        hdr += ' %9s' % '低于下限'
    print(hdr)
    for r in sorted(rows, key=lambda x: -x['n']):
        line = '    %-24s %6d %11.3f %9.3f %8.1f%%' % (
            r['name'][:24], r['n'], r['med'], r['mean'], r['chop'])
        if floor_beats > 0:
            line += ' %8.1f%%' % r.get('under', 0.0)
        print(line)
    print()
    return rows


def main():
    ap = argparse.ArgumentParser(description='逐轨时值统计（音数 / 时值中位 / 碎音率）')
    ap.add_argument('mine', help='要量的 MIDI')
    ap.add_argument('ref', nargs='?', default=None, help='对照 MIDI（可选）')
    ap.add_argument('--floor', type=float, default=0.0,
                    help='时值下限（拍）——顺带报"低于下限的音占比"（还原链用的是 0.55）')
    a = ap.parse_args()
    # 面板守卫（硬形式）：没在跑就先拉起来 —— 见 scripts/studio_guard.py 顶部那段。
    try:
        import studio_guard
        studio_guard.ensure_panel()
    except Exception as _e:                                        # noqa: BLE001
        print('  （面板守卫跳过：%s）' % str(_e)[:80])
    for tag, p in (('我的', a.mine), ('对照', a.ref)):
        if not p:
            continue
        if not os.path.isfile(p):
            print('%s（缺）: %s' % (tag, p))
            continue
        show(tag, p, a.floor)
    print('判读：旋律轨（Piano/Bass/Guitar/Strings）碎音率 <5%、时值中位 >=0.25s；'
          '打击轨碎音 ~100% 是**正常**的')
    return 0


if __name__ == '__main__':
    sys.exit(main())
