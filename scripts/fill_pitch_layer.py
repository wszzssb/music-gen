#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""fill_pitch_layer.py —— **按分轨的"亮音起音"补一层高音轨**（Glock / Melody 这类容易扒空的层）。

## 什么时候用

`audit_stems.py` 报了某条轨 **召回 < 50%**（原曲该分轨有高音、我方几乎没有），例如
《どうぞめしあがれ》的 `Glock`：piano 分轨 1.2–6kHz 有 **160** 个高音窗，我方只覆盖 **10** 个
（**6.2%**）。⚠ **补之前必须先量"音高可不可信"**：本工具取每个窗的**峰频**当音高，
实测峰频在分轨上的音高带命中率 **77.5%**（>50% 才许补）—— 否则补进去的音高是猜的。

## 做法（**每个音都能追到分轨上的一个窗**）

① 分轨 → 逐 1/4 拍窗算"目标带（默认 1.2–6 kHz）占比 + 该窗 dB"；
② 挑出**该有高音**的窗（占比 > P70 且不静音）；
③ 每个窗取峰频当音高，**把八度移到目标轨的音域**（`TR_RANGE`，`song_engine` 的唯一真源）；
④ 力度 = 该窗相对能量的映射（夹在 `--vel`）；时值固定（点状音色给起音长度即可）；
⑤ **合并**进目标轨（保留原有音，不覆盖），逐点打印"窗时刻 → 音高 → 力度"。

⚠ **不许"整轨移八度"糊过去**（PITFALLS 253）：这里逐点按 `TR_RANGE` 折八度，
且**音级不变**（只 ±12 的整数倍）。
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()          # noqa: E402
import json_io                               # noqa: E402
import song_engine as _se                    # noqa: E402

SONGS = os.path.join(ROOT, 'songs')


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('song')
    ap.add_argument('--stem', required=True, help='拿哪条分轨当证据（wav）')
    ap.add_argument('--track', required=True, help='写进哪条引擎轨（Glock/Melody/…）')
    ap.add_argument('--band', default='1200,6000', help='目标带（Hz，默认 1.2–6k）')
    ap.add_argument('--pct', type=float, default=70.0, help='"该有高音"的百分位门')
    ap.add_argument('--vel', default='52,84', help='力度范围')
    ap.add_argument('--dur', type=float, default=0.5, help='时值（拍）')
    ap.add_argument('--max', type=int, default=400, help='最多补多少个音')
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()

    import numpy as np
    import soundfile as sf
    # ⚠ **按曲内 `name` 兜底解析**（同 `shaker_layer.resolve_song` 的坑）：
    # `strip_drums.py` 产出的目录名（`<原名>_nodrum`）与曲内 `name` 可以**不一致**
    # （引擎写 MIDI 用 `name`）—— 按参数拼目录会写到**另一首曲子**上去，且完全静默。
    p = os.path.join(SONGS, a.song, 'song.json')
    if not os.path.exists(p):
        p = None
        for _nm in sorted(os.listdir(SONGS)):
            _q = os.path.join(SONGS, _nm, 'song.json')
            if os.path.exists(_q):
                try:
                    if str(json.load(open(_q, encoding='utf-8')).get('name') or '') == a.song:
                        p = _q
                        break
                except Exception:                          # noqa: BLE001
                    continue
    if not p:
        print('找不到曲目 %s' % a.song)
        return 1
    print('[曲目] %s' % p)
    d = json_io.load(p)
    bpm = float(d.get('bpm') or 120.0)
    bb = float(d.get('bar_beats') or 4.0)
    SPB, BAR = 60.0 / bpm, 60.0 / bpm * bb
    lo, hi = (float(t) for t in a.band.split(','))
    vlo, vhi = (int(t) for t in a.vel.split(','))
    rg = _se.TR_RANGE.get(a.track)
    if not rg:
        print('未知轨 %s（引擎 TR_RANGE 里没有）' % a.track)
        return 1
    x, sr = sf.read(a.stem, always_2d=True)
    m = x.mean(axis=1)
    step = SPB / 4.0
    n = int(len(m) / (step * sr))
    ratios, dbs = [], []
    for i in range(n):
        seg = m[int(i * step * sr):int((i + 1) * step * sr)]
        if len(seg) < 128:
            ratios.append(0.0); dbs.append(-99.0); continue
        if float(np.sqrt((seg ** 2).mean())) < 1e-6:
            ratios.append(0.0); dbs.append(-99.0); continue
        W = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        f = np.fft.rfftfreq(len(seg), 1.0 / sr)
        k = (f >= lo) & (f < hi)
        tot = float((W ** 2).sum())
        ratios.append(float((W[k] ** 2).sum()) / tot * 100 if tot > 1e-24 else 0.0)
        dbs.append(20 * np.log10(max(1e-9, float(np.sqrt((seg ** 2).mean())))))
    ratios = np.array(ratios); dbs = np.array(dbs)
    thr = float(np.percentile(ratios, a.pct))
    want = np.where((ratios > thr) & (dbs > -60))[0][:a.max]
    # 原有音（保留）
    old = d.setdefault('notes_extra', {}).get(a.track) or []
    old = list(old.get('notes') if isinstance(old, dict) else old)
    add = []
    dmax = float(dbs[want].max()) if len(want) else 0.0
    for i in want:
        t = float(i) * step
        seg = m[int(t * sr):int((i + 1) * step * sr)]
        W = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        f = np.fft.rfftfreq(len(seg), 1.0 / sr)
        k = (f >= lo) & (f < hi)
        if not k.any():
            continue
        pk = int(np.argmax(W * k))
        if W[pk] <= 0:
            continue
        pitch = int(round(69 + 12 * np.log2(max(20.0, f[pk]) / 440.0)))
        while pitch < rg[0]:
            pitch += 12
        while pitch > rg[1]:
            pitch -= 12
        rel = (dbs[i] - (dmax - 18.0)) / 18.0
        vel = int(round(vlo + (vhi - vlo) * min(1.0, max(0.0, rel))))
        bar = int(t // BAR); beat = (t - bar * BAR) / SPB
        add.append([bar, round(beat, 3), a.dur, int(pitch), vel])
    print('[%s ← %s] 目标带 %s 占 P%.0f 的窗 %d 个 → 补 %d 音（原有 %d 音保留）'
          % (a.track, os.path.basename(a.stem), a.band, a.pct, len(want), len(add), len(old)))
    print('   音域折进 TR_RANGE%s：%s' % (rg, sorted({e[3] for e in add})[:12]))
    for e in add[:6]:
        print('   例：第 %d 小节 %.2f 拍 音高 %d 力度 %d' % (e[0], e[1], e[3], e[4]))
    if a.dry:
        print('[dry] 不写盘')
        return 0
    d['notes_extra'][a.track] = old + add
    d['notes_extra'][a.track] = sorted(d['notes_extra'][a.track], key=lambda z: (z[0], z[1]))
    json_io.save(p, d)
    print('[写] %s（%s 轨 %d → %d 音）' % (p, a.track, len(old), len(d['notes_extra'][a.track])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
