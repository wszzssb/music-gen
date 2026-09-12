#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给整库旋律补"换气"：在**乐句边界**把一个音收短，留出 ≥0.5 拍的空。

规则（口径见 `breath.py`，与自检 `melody_breathing` **共用同一份判据**）：
  · 最长不间断段 > 20 秒 → 需要换气（用户口径："不一定每一段都要停顿，而是太长的话要停顿换气"）
  · 换气点落在**小节号是 N 的倍数**处；N 由速度推（取时长 ≤ 0.9×阈值的最大 4 小节倍数）
  · **只改音符时值**，不动音高/落点/和弦 → 强拍贴合、节奏型、旋律轮廓全不变
  · 有 `spec.json` 的曲目：同步把该音写成 4 元显式时值（`[小节,拍,时值,音高]`），
    保证 `song_spec_sync` 仍然通过（3 元音的推导时值只看下一个落点，收短一个音不影响别的）

**改完必须重作曲 + 重渲染**：`python scripts/make_song.py <曲目>`

用法:
  python scripts/fix_breathing.py --dry              # 只报告要改哪里（不动文件）
  python scripts/fix_breathing.py                    # 全库改
  python scripts/fix_breathing.py 11_dn75_neon 12_d75_warm
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import breath                                        # noqa: E402
import cli_utf8                                      # noqa: E402
import json_io                                       # noqa: E402
import song_engine                                   # noqa: E402


def spacing_bars(bb, spb):
    """换气间距（小节）：取"时长 ≤ 0.9×阈值"的**最大 4 小节倍数**（音乐上乐意落在 4/8/12…）"""
    bar_sec = bb * spb
    k = 4
    while (k + 4) * bar_sec <= breath.BREATH_SEC * 0.9:
        k += 4
    return k


def records(j2, bb):
    """全曲旋律音符：[(绝对起拍, 绝对止拍, 旋律名, 数组下标, 小节, 拍, 时值, 音高)]"""
    out, pos = [], 0.0
    for sec in j2['sections']:
        key = sec['melody']
        arr = j2['melody'].get(key)
        if isinstance(arr, list):
            for idx, x in enumerate(arr):
                if 0 <= x[0] < sec['bars']:
                    a = pos + x[0] * bb + x[1]
                    out.append((a, a + x[2], key, idx, x[0], x[1], x[2], x[3]))
        pos += sec['bars'] * bb
    return out


def fix_song(path, dry=False):
    """返回 [(旋律名, 小节, 拍, 原时值, 新时值, 让出的空档, 段长秒)]"""
    j2 = json_io.load(path)
    bb = song_engine.bar_beats(j2)
    spb = 60.0 / float(j2.get('bpm') or 120.0)
    step = spacing_bars(bb, spb)
    changes = []
    for _pass in range(120):
        longs, _b, _s = breath.long_runs(j2)
        if not longs:
            break
        s, e, sec = max(longs, key=lambda t: t[1] - t[0])      # 先修最长的那段
        recs = records(j2, bb)
        mid = (s + e) / 2.0
        # 段内**所有** step 小节边界，按"离段中点近"排序（先试中间，一次就把长段劈两半）
        cands, c = [], (int(s // (bb * step)) + 1) * bb * step
        while c < e:
            cands.append(c)
            c += bb * step
        cands.sort(key=lambda v: abs(v - mid))
        options = []               # [(要砍掉几拍, 方案)] —— 取**改动最小**的那个
        for cand in cands:
            target = cand - breath.BREATH_GAP
            if not (s + 0.25 < target < e - 0.25):
                continue
            # ① 收短"跨过换气点"的那个音（起点 ≤ 目标，终点 > 目标）
            cov = [r for r in recs if r[0] <= target + 1e-9 < r[1]]
            if cov:
                a, _en, key, idx, bar, beat, dur, _pitch = max(cov, key=lambda r: r[0])
                new_dur = round(target - a, 4)
                if 0.25 <= new_dur < dur - 1e-9:
                    options.append((round(dur - new_dur, 4),
                                    (key, idx, bar, beat, dur, new_dur,
                                     cand - (a + new_dur), sec)))
            # ② 换气点正好落在音头上（或 ① 要砍太多）→ 收短**它前面那个音**，只在后面留 GAP
            before = [r for r in recs if r[1] <= target + 1e-9
                      and r[6] - breath.BREATH_GAP >= 0.25]
            if before:
                a, _en, key, idx, bar, beat, dur, _pitch = max(before, key=lambda r: r[1])
                options.append((breath.BREATH_GAP,
                                (key, idx, bar, beat, dur,
                                 round(dur - breath.BREATH_GAP, 4), breath.BREATH_GAP, sec)))
        if not options:
            break                      # 没有可用的边界（段太短/音符太短）→ 留给人工
        options.sort(key=lambda t: t[0])
        picked = options[0][1]
        key, idx, bar, beat, old, new, gap, sec = picked
        changes.append((key, bar, beat, old, new, gap, sec))
        # **即使 --dry 也要在内存里应用**：否则下一轮又挑中同一处，循环原地打转
        j2['melody'][key][idx][2] = new
    if not dry and changes:
        json_io.save(path, j2)
    return changes, round(step * bb * spb, 1), step


def patch_spec(d, changes):
    """把改动同步进 spec.json（写成 4 元显式时值）；无 spec 或没匹配上就返回提示"""
    sp = os.path.join(d, 'spec.json')
    if not changes or not os.path.exists(sp):
        return ''
    spec = json_io.load(sp)
    idx = {}
    for s in spec.get('sections', []):
        ms = s.get('melody')
        if isinstance(ms, dict):
            for mname, evs in ms.items():
                for ev in evs:
                    idx[(mname, int(ev[0]), round(float(ev[1]), 4))] = ev
    missed = []
    for (key, bar, beat, _o, new, _g, _sec) in changes:
        ev = idx.get((key, int(bar), round(float(beat), 4)))
        if ev is None:
            missed.append('%s bar%d 拍%.2f' % (key, bar + 1, beat))
            continue
        pitch = ev[3] if len(ev) >= 4 else ev[2]
        ev[:] = [int(ev[0]), float(ev[1]), float(new), int(pitch)]
    json_io.save(sp, spec)
    return ('（spec 同步失败: %s）' % '; '.join(missed)) if missed else ''


def main():
    dry = '--dry' in sys.argv
    want = [a for a in sys.argv[1:] if not a.startswith('-')]
    dirs = ([os.path.join(ROOT, 'songs', w) for w in want] if want
            else sorted(d for d in glob.glob(os.path.join(ROOT, 'songs', '*'))
                        if os.path.isfile(os.path.join(d, 'song.json'))))
    total = 0
    for d in dirs:
        p = os.path.join(d, 'song.json')
        if not os.path.isfile(p):
            print('  跳过（没有 song.json）: %s' % os.path.basename(d))
            continue
        changes, run_sec, step = fix_song(p, dry)
        if not changes:
            continue
        total += len(changes)
        name = os.path.basename(d)
        warns = patch_spec(d, changes) if not dry else ''
        print('%-22s 换气点 %2d 处（间距 %d 小节 ≈ %.0f 秒）%s'
              % (name, len(changes), step, run_sec, warns))
        for (key, bar, beat, old, new, gap, _sec) in changes[:4]:
            print('      %s bar%d 拍%.1f 时值 %.2f→%.2f（让出 %.1f 拍空档）'
                  % (key, bar + 1, beat, old, new, gap))
        if len(changes) > 4:
            print('      … 另外 %d 处' % (len(changes) - 4))
    print('\n共 %d 处%s' % (total, '（--dry 预览，未写文件）' if dry else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())

import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
