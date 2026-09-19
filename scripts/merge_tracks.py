#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""merge_tracks.py —— 把若干轨**并进**一条轨（还原/提取用）。

为什么需要（2026-09-19，用户反馈"16/23/29 太杂乱、不流畅"）：
逐轨量时值后发现两条病：
  ① **旋律轨时值太短**：我的 Piano 0.38 拍 / Guitar 0.20 拍，
     而用户认可版 `v5_source.mid` 是 0.66 / 0.86，且碎音只有 2.7% / 3.9%（我 30% / 92%）；
  ② **YMT3 的合成器通道全留着**：Synth Pad 637 音（碎音 68%）、Organ 294（81%）、
     Chromatic Percussion 315（84%）—— 而 v5 **只有 5 条轨**
     （Piano/Perc/Bass/Strings/Guitar），这些键盘类的音是被**并进 Piano** 的。

本工具就做第 ② 件：把 `--from` 列的轨全部并进 `--into`，并清空它们。
（第 ① 件由 `bass_ensemble.py --min-beats` 负责。）

用法:
  python scripts\merge_tracks.py <song.mid> --into "Acoustic Piano" \
      --from "Synth Pad,Organ,Chromatic Percussion,Synth Lead" [--out 输出.mid]

  · `--into` 不存在时会新建；存在则追加（音按时间排序）
  · 源轨清空后**保留轨壳**（引擎按轨名找轨，删了会出问题）
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cli_utf8 as _cu                                              # noqa: E402
_cu.setup()

import midi_file                                                    # noqa: E402


def main():
    ap = argparse.ArgumentParser(description='把若干轨并进一条轨')
    ap.add_argument('mid')
    ap.add_argument('--into', required=True, help='目标轨名（不存在则新建）')
    ap.add_argument('--from', dest='srcs', default='',
                    help='要并入的轨名，逗号分隔')
    ap.add_argument('--out', default=None, help='输出（缺省原地覆盖）')
    ap.add_argument('--min-beats', type=float, default=0.0,
                    help='时值下限（拍）——对**目标轨全部音**生效（含并进来的与原本就在的）')
    a = ap.parse_args()
    # 面板守卫（硬形式）：没在跑就先拉起来 —— 见 scripts/studio_guard.py 顶部那段。
    try:
        import studio_guard
        studio_guard.ensure_panel()
    except Exception as _e:                                        # noqa: BLE001
        print('  （面板守卫跳过：%s）' % str(_e)[:80])
    srcs = [s.strip() for s in a.srcs.split(',') if s.strip()]
    if not srcs:
        raise SystemExit('--from 是空的')

    m = midi_file.import_midi(a.mid)
    tracks = m.get('tracks', [])
    tgt = next((t for t in tracks if t.get('name') == a.into), None)
    if tgt is None:
        # ⚠ 新建轨要给**全套字段**：`export_midi` 读 channel/program/notes/ccs/…，
        # 少字段不报错但会静默用默认值（channel 0 → 与别的轨撞通道、串音色）。
        used_ch = {int(t.get('channel', 0)) for t in tracks}
        free = [c for c in range(16) if c != 9 and c not in used_ch] or [0]
        tgt = {'index': len(tracks), 'name': a.into, 'channel': free[0],
               'program': 0, 'drum': False,
               'mute': False, 'solo': False, 'hidden': False,
               'notes': [], 'ccs': [], 'program_changes': [], 'markers': []}
        tracks.append(tgt)
        print('  新建轨 %s（ch%d）' % (a.into, tgt['channel'] + 1))
    moved = 0
    for nm in srcs:
        for t in tracks:
            if t.get('name') == nm and t.get('notes'):
                tgt.setdefault('notes', []).extend(t['notes'])
                moved += len(t['notes'])
                print('  %s: %d 音（prog=%s）→ %s' % (nm, len(t['notes']), t.get('program'), a.into))
                t['notes'] = []
                # 清空后**隐藏**：留空壳 MTrk 会在面板/渲染里多出空轨，而 `export_midi`
                # 会跳过 hidden —— 于是输出正好是"并完的轨数"（v5 就是 5 轨）。
                t['hidden'] = True
    tgt['notes'] = sorted(tgt.get('notes') or [])

    # **时值下限**（opt-in）：为什么放在合并**之后** ——
    # 用户 2026-09-19 听感"16/23/29 太杂乱、不流畅"，逐轨量出来是**两条病**：
    #   ① 每层时值太短（Piano 0.38 拍 / Guitar 0.20 拍，认可版 v5 是 0.66 / 0.86）；
    #   ② YMT3 的合成器通道全留着且碎音 68~84%，而 v5 **只有 5 轨**（那些音是并进 Piano 的）。
    # 先把 ② 并进来（同音高的重叠音此刻才看得到），再统一拉 ① —— 顺序反了会把
    # 并进来的碎音当成"已经处理过"（实测先并后拉：Piano 碎音 46.6%；只拉不并：30.4%）。
    #
    # ⚠ 与 `bass_ensemble --min-beats` 的**故意不同**：那边宁可让同音高重叠（`max(_FLOOR, gap-0.005)`），
    #   这里**不重叠**（`min(..., gap-0.005)`）。理由：MIDI 的 note-off 只带音高不带 id，
    #   同音高重叠会被音源吞掉后一个音；Piano 轨合并后同音高重复极密（同一键被多条轨弹），
    #   宁可留短也不留吞音。代价：极密处仍有短音（听感上是"同音重复"，不是"碎"）。
    floored = 0
    if a.min_beats > 0:
        byp = {}
        for n in tgt['notes']:
            byp.setdefault(int(n[2]), []).append(n)
        keep = []
        for p, lst in byp.items():
            lst.sort(key=lambda x: x[0])
            for j, cur in enumerate(lst):
                want = max(float(cur[1]), a.min_beats)
                if want != float(cur[1]):
                    floored += 1
                if j + 1 < len(lst):
                    gap = lst[j + 1][0] - cur[0]
                    if gap < 0.02:      # <20ms 的同音高重复：丢掉前一个（重复按同一键）
                        continue
                    cur[1] = min(want, gap - 0.005)
                else:
                    cur[1] = want
                keep.append(cur)
        tgt['notes'] = sorted(keep)
        print('  时值下限 %.2f 拍：拉长 %d 音（同轨音高数 %d）' % (a.min_beats, floored, len(byp)))

    out = a.out or a.mid
    midi_file.export_midi(m, out)
    print('  合计并入 %d 音；%s → %d 音' % (moved, a.into, len(tgt['notes'])))
    print('  写 %s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
