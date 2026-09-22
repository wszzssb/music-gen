#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""**"每个音实际响多久 / 谁在跳出来"体检** —— 抓"只响 0.几秒的音"与"镫一下"。

用户 2026-09-22 的两条听感（`20_piano_rain`）：
  ① "有很多响了 0.几秒就没了的音" —— 旋律写的是长音，音色却让它 0.2 秒就掉下去
  ② "还有镫一下的" —— 伴奏轨的**短促音**在平稳的旋律之上跳出来

**为什么必须量音频、不能只看 MIDI**：MIDI 时值 ≠ 听感时长。同一个 1.35 拍的音，
衰减型钢琴（GM 0-7）从峰值掉 12 dB 只要 **0.19~0.25 秒**，持续型电钢琴（GM 4）**根本不掉**。
实测（GeneralUser GS · tempo 78 · 音高 78 · 力度 84）：
    掉 12 dB：0.24(0) 0.24(1) 0.43(2) 0.19(3) **不掉(4)** 0.58(5) 0.64(6) 0.59(7) 0.84(11)
    起音 10→90%：钢琴 38–41ms · 电钢1 **0–1ms** · 弦乐 48 **427ms** · 慢弦乐 62 **621ms**
    ⇒ 换弦乐能解决"0.几秒"，但 **427ms 的起音会重演"慢半拍"**（判据 ≤20ms）。

⚠ **量测纪律（三版才写对，前两版的读数都是噪声）**：
  1. **只信"独奏窗口"** —— 从该音起音到下一个任何轨起音之间，且**没有别的轨在响**。
     多轨齐奏时，音头会被更响的那一轨"借走"（实测把 GM 4 的持续音误报成"0.04s 就掉 12dB"）。
  2. **回声/静音前的音不算跳变** —— 背景必须是"有声"的。
  3. 所以本工具**不报"每轨一个中位数"**，只报：**独奏窗口的可靠读数** + **结构性可疑时刻**
     （后者是纯数据判据，来自 `harmony_check.stab_candidates()`，与生成期用的是同一份）。

用法：
    python scripts/probe_sustain.py <曲目> [--top 8]
"""
import json
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import harmony_check as HC  # noqa: E402
import midi_file  # noqa: E402

HOP = 0.005
WIN_MAX = 0.70
WIN_MIN = 0.40


def _env(path):
    y, sr = sf.read(path, dtype='float32', always_2d=True)
    mono = y.mean(axis=1)
    hop = int(sr * HOP)
    n = len(mono) // hop
    e = np.sqrt((mono[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
    return 20.0 * np.log10(np.maximum(e, 1e-9))


# ---- 用户 2026-09-22 追加的两个量法："重要是更流畅，不要有突然的突兀杂音" ----
FLOW_GAP_BEAT = 0.25      # 断开超过它算"一句一句断"的候选（单位：拍）
SUD_JUMP = 8.0            # 孤立脉冲：比前后 100ms 中位高出多少 dB
SUD_FLAT = 6.0            # 前后中位本身要比峰值低多少（否则是持续音、不是脉冲）
SUD_MAXLEN = 0.080        # 脉冲最长多久算"突然的一下"
SUD_ALIGN = 0.180         # ⚠ 与 MIDI 起音的对齐窗口：GM 音源 + 混响**实测起音延迟 42~78ms**，
                          #    第一版给 ±40ms ⇒ 把正常的音头全判成"没有起音的杂音"
                          #    （`20_piano_rain` 5 处"杂音"**全是误报**，每个都有起音）。
                          #    第二版给 120ms 后仍有 2 处"可疑"，复查发现它们**前后都有起音**
                          #    （−187ms/+132ms、−132ms），只是那两处音间隔大 ⇒ 放到 180ms。


def melody_flow(notes, spb, gap_beat=FLOW_GAP_BEAT):
    """**流畅度**：一个旋律轨的音间空隙与断奏率。

    `notes` = [(起音秒, 时值秒, ...)]（后面几项随便，只看前两个）；`spb` = 秒/拍。
    返回 dict 或 None（音太少）。

    用户口径"**重要是更流畅**"（2026-09-22）：实测 `20_piano_rain` 重生成版空隙中位
    **0.332s**、断开 >0.25 拍占 **69%**、最长 **2.27s** —— 听感"一句一句断开"；
    而真实模板（9 首 tender 的旋律轨共 4849 音）空隙中位只有 **0.001s**、≥0.3s 仅 **3%**。
    ⇒ 报**离群量**（数字 + 占比），不是"通过/不通过"。
    """
    ns = sorted((float(s), float(d)) for (s, d, *_r) in notes)
    if len(ns) < 3:
        return None
    gaps = [ns[i + 1][0] - (ns[i][0] + ns[i][1]) for i in range(len(ns) - 1)]
    g = np.array(gaps)
    dd = np.array([d / spb for (_s, d) in ns])
    return {'n': len(ns),
            'gap_med': float(np.median(g)),
            'gap_gt': float((g > gap_beat * spb).mean()),
            'gap_gt2': float((g > 2 * gap_beat * spb).mean()),
            'gap_max': float(g.max()),
            'dur_med': float(np.median(dd)),
            'staccato': float((dd < 0.3).mean()),
            'worst': sorted([(float(x), ns[i][0]) for i, x in enumerate(gaps)],
                            reverse=True)[:5]}


def sudden_sounds(db, onsets, hop=HOP, jump=SUD_JUMP, flat=SUD_FLAT,
                  maxlen=SUD_MAXLEN, align=SUD_ALIGN):
    """**"突然冒出来的声音"**：音频里的孤立脉冲中，**没有 MIDI 起音对应**的那些。

    返回 `(pulses, orphans)`：`pulses` = 全部孤立脉冲（含正常音头），
    `orphans` = 距最近 MIDI 起音 > `align` 秒的 —— **这些才是可疑的"突兀声"**。

    ⚠ 对齐窗口必须够宽：GM 音源 + 混响**实测起音延迟 42~78ms**；
    第一版给 ±40ms，结果 `20_piano_rain` 报的 5 处"杂音"**全是误报**
    （每个都有起音，只是晚了几十毫秒）——改窗口后复量，那 5 处一字未变，
    证明它们与"旋律断开/连奏"无关，也不是新引入的。
    """
    W = int(0.100 / hop)
    on = np.asarray(sorted(float(x) for x in onsets)) if len(onsets) else np.array([])
    pulses, i = [], W
    while i < len(db) - W:
        back = float(np.median(db[i - W:i]))
        fwd = float(np.median(db[i + 1:i + 1 + W]))
        peak = float(db[i])
        if peak - back >= jump and peak - fwd >= jump and back < peak - flat:
            j = i
            while j + 1 < len(db) and db[j + 1] > peak - 10.0:
                j += 1
            dur = (j - i + 1) * hop
            if dur <= maxlen:
                t = i * hop
                d = float(np.min(np.abs(on - t))) if len(on) else float('inf')
                pulses.append((t, dur, peak, peak - back, d))
            i = max(j + 1, i + 1)
        else:
            i += 1
    return pulses, [p for p in pulses if p[4] > align]


def main():
    argv = sys.argv[1:]
    top = int(argv[argv.index('--top') + 1]) if '--top' in argv else 8
    args = [a for a in argv if not a.startswith('--') and not a.isdigit()]
    if not args:
        print(__doc__)
        return 2
    sid = args[0]
    folder = os.path.join(ROOT, 'songs', sid)
    if not os.path.isdir(folder):
        print('找不到曲目目录：%s' % folder)
        return 2
    mids = sorted([f for f in os.listdir(folder) if f.endswith('.mid')], key=len)
    wavs = [f for f in os.listdir(folder) if f.endswith('_sf.wav')]
    if not mids or not wavs:
        print('%s 缺 .mid 或 _sf.wav（先跑 make_song）' % sid)
        return 2
    song = json.load(open(os.path.join(folder, 'song.json'), encoding='utf-8'))
    spb = 60.0 / float(song.get('bpm') or 120)
    model = midi_file.import_midi(os.path.join(folder, mids[0]))
    db = _env(os.path.join(folder, wavs[0]))

    notes = []
    for tr in model['tracks']:
        for n in tr.get('notes', []):
            notes.append((n[0] * spb, n[1] * spb, int(n[2]), int(n[3]), tr['name']))
    notes.sort()
    print('== %s · %.0f BPM · %d 音 · 音频 %.1f 秒 ==' % (sid, song.get('bpm', 0), len(notes),
                                                       len(db) * HOP))

    # ---- A. 独奏窗口：只有它在响的那些音（读数可靠）----
    solo = []
    for idx, (s, d, p, v, nm) in enumerate(notes):
        i0 = int(s / HOP)
        if i0 <= 40 or i0 + int(WIN_MIN / HOP) >= len(db):
            continue
        nxt = [x[0] for x in notes if x[0] > s + 1e-6]
        t_end = min(nxt) if nxt else s + WIN_MAX
        t_end = min(t_end, s + WIN_MAX)
        if t_end - s < WIN_MIN:
            continue
        # 别的轨有没有覆盖这段窗口
        busy = any(x[4] != nm and x[0] < t_end - 1e-6 and s < x[0] + x[1] - 1e-6
                   for x in notes)
        if busy:
            continue
        win = db[i0:int(t_end / HOP)]
        ipk = int(np.argmax(win))
        tail = win[ipk:]
        k = np.nonzero(tail <= tail[0] - 12.0)[0]
        t12 = float(k[0] * HOP) if len(k) else float('inf')
        solo.append((s, p, v, nm, d, t12, float(tail[0])))

    print('\n-- ① **独奏窗口**的音（只有这一条轨在响 → 读数可靠）：%d 个 --' % len(solo))
    if solo:
        fin = [x[5] for x in solo if x[5] != float('inf')]
        print('   其中 %d 个"在窗口内掉了 12dB"、%d 个**没掉**（= 音色真的在持续）'
              % (len(fin), len(solo) - len(fin)))
        bad = [x for x in solo if x[5] != float('inf') and x[4] > 2.0 * x[5]]
        print('   ⚠ "写的比响的长"（时值 > 2× 掉12dB）：%d 个' % len(bad))
        print('   %-9s %-7s %5s %5s %8s %9s %8s' % ('时刻', '轨', '音高', '力度', '时值s', '掉12dB', '倍数'))
        for (s, p, v, nm, d, t12, pk) in sorted(bad, key=lambda r: -(r[4] / r[5]))[:top]:
            print('   %8.3fs %-7s %5d %5d %8.3f %8.3fs %7.1fx' % (s, nm, p, v, d, t12, d / t12))
        if not bad:
            print('   （无）')
    else:
        print('   （全曲没有"独奏窗口"的音 —— 编制一直很满，量不出可靠的逐音读数）')

    # ---- B. "镫"候选（纯数据判据，与生成期同一份）----
    cand = HC.stab_candidates(song, model, spb)
    print('\n-- ② "镫"候选（短促音 + 同刻有持续型主奏 + 力度不低于主奏）：%d 处'
          '（按"比主奏响多少"排序）--' % len(cand))
    for (s, nm, p, v, b, lift, why) in cand[:top]:
        print('   %8.3fs %-7s 音高%3d 力度%3d 时值%.2f拍 · 比主奏 %+d · %s'
              % (s, nm, p, v, b, lift, why))
    if not cand:
        print('   （无）')

    # ---- C. 流畅度（用户 2026-09-22："重要是更流畅"）----
    mel = [x for x in notes if x[4] == 'Melody']
    fl = melody_flow(mel, spb)
    print('\n-- ③ **流畅度**（主奏轨的音间空隙 / 断奏）--')
    if fl:
        print('   %d 音 · 空隙中位 %+.3fs（%.2f 拍）· 断开 >%.2f 拍占 %.0f%% · >%.2f 拍占 %.0f%%'
              % (fl['n'], fl['gap_med'], fl['gap_med'] / spb, FLOW_GAP_BEAT,
                 100 * fl['gap_gt'], 2 * FLOW_GAP_BEAT, 100 * fl['gap_gt2']))
        print('   时值中位 %.2f 拍 · 断奏(<0.3 拍)占 %.0f%% · 最长空隙 %.3fs'
              % (fl['dur_med'], 100 * fl['staccato'], fl['gap_max']))
        print('   最长的几处：%s' % ' · '.join('%.3fs@%.2fs' % (g, t) for (g, t) in fl['worst']))
        print('   参照：真值模板空隙中位 0.001s、≥0.3s 仅 3%；`20_piano_rain` 连奏前'
              ' 0.332s/69%，连奏后 0.088s/20%')
    else:
        print('   （旋律音太少，量不了）')

    # ---- D. 突兀声（用户 2026-09-22："不要有突然的突兀杂音"）----
    pulses, orphans = sudden_sounds(db, [x[0] for x in notes])
    print('\n-- ④ **"突然冒出来的声音"**（孤立脉冲，且没有 MIDI 起音对应）--')
    print('   孤立脉冲共 %d 个；其中可疑的（距最近起音 >%.0fms）**%d 个** ← 这些才是突兀声'
          % (len(pulses), SUD_ALIGN * 1000, len(orphans)))
    for (t, dur, pk, lift, d) in sorted(orphans, key=lambda p: -p[3])[:top]:
        print('   %8.3fs 时长 %.3fs 峰值 %6.1f dB · 比前后中位高 %+5.1f dB · 距最近起音 %.0fms'
              % (t, dur, pk, lift, d * 1000))
    if not orphans:
        print('   （无 —— 每个孤立脉冲都有 MIDI 起音对应，属正常音头）')
    print('   ⚠ 对齐窗口是 %.0fms：GM 音源+混响起音延迟实测 42~78ms，窗口给窄了会把'
          '正常音头全判成"杂音"（第一版 ±40ms 就 5/5 误报）' % (SUD_ALIGN * 1000))

    print('\n⚠ 报的是**离群量 / 结构性条件**，不是"有问题"；要不要改由耳朵定。'
          '背景读一次 `docs/HANDOFF.md` §7 与 §7.10。')
    return 0


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    sys.exit(main())
