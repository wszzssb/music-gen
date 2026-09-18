# -*- coding: utf-8 -*-
"""从**分轨音频**给转录 MIDI 量**逐音力度** —— 还原要的"真实强弱"，不是引擎的默认值。

## 为什么必须做（`docs/RESTORE-METHOD.md` §4）

缺逐音力度 → 引擎套默认力度 → **"打字机"听感**。
实测对照（同一首参考曲）：带力度的版本 `notes_extra` **17773/17773 全带**，
不带的版本 **0/23033** —— 这是"不如"最直接的一条。

## 做法

对**每个音符**，在它的 `[起音, 起音 + 时值]` 窗口内取分轨音频的**峰值幅度**，
再按**分位校准**映射成 velocity：

```
vel = clip(round(P50_target + (peak_db - ref_p50_db) * k), 1, 127)
```

`ref_p50_db` 是**本轨音符峰值的中位**（dB），`P50_target` 是目标中位力度
（默认 51，与实战口径一致）。这样力度分布以"本轨自己的中位"为锚，
不会因为分轨电平不同而整体偏移。

⚠ **先自检再读数**（PITFALLS 187②）：加 `--self-test` 打印本轨峰值分布，
若中位落在噪声底附近（例如 −60dB 以下）说明分轨是空的，别拿它校准。

## 用法

```bash
python scripts/measure_velocity.py <分轨.wav> <转录.mid> <输出.mid> \
       [--p50 51] [--k 9.0] [--max-db 18]
# 多轨一起做（轨名用引擎认识的）
python scripts/measure_velocity.py --track Piano=piano.wav:piano.mid --track Bass=bass.wav:bass.mid ...
```

输出 MIDI 的每个音符带第 5 个元素（力度），可直接喂 `transcribe_to_song.py`。
"""
import argparse
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import midi_file                                              # noqa: E402


def peak_db_map(wav, notes, spb):
    """→ 每个音符在其窗口内的峰值（dBFS）。`notes` 是 [start_beat, dur_beat, pitch, vel]。"""
    y, sr = sf.read(wav, always_2d=True)
    y = y.mean(axis=1)
    if y.size == 0:
        raise SystemExit('分轨是空的：%s' % wav)
    # 20ms 包络（峰值跟随），避免瞬时尖峰骗人
    hop = max(1, int(sr * 0.02))
    n = len(y) // hop
    env = np.abs(y[:n * hop]).reshape(n, hop).max(axis=1)
    out = []
    for it in notes:
        t0 = float(it[0]) * spb
        dur = max(0.05, float(it[1]) * spb)
        i0, i1 = int(t0 / 0.02), int((t0 + dur) / 0.02) + 1
        i0, i1 = max(0, i0), min(n, i1)
        if i1 <= i0:
            out.append(-120.0)
            continue
        v = float(env[i0:i1].max())
        out.append(20.0 * np.log10(v) if v > 1e-9 else -120.0)
    return out


def calibrate(peaks, p50=51.0, k=9.0, max_db=18.0):
    """峰值 dB → velocity。以**本轨中位**为锚，避免分轨电平差异造成整体偏移。"""
    good = [p for p in peaks if p > -60.0]
    if not good:
        raise SystemExit('所有音符的峰值都低于 −60dB —— 分轨基本是静音，别拿它校准力度')
    ref = float(np.median(good))
    out = []
    for p in peaks:
        d = max(-max_db, min(max_db, p - ref))       # 限制偏离量（防止个别爆点拉飞）
        out.append(int(max(1, min(127, round(p50 + d * k)))))
    return out, ref


def do_one(wav, mid, out, p50, k, max_db, report=True):
    d = midi_file.import_midi(mid)
    spb = 60.0 / max(1e-9, float(d.get('bpm') or 120.0))
    total = 0
    for tr in d.get('tracks') or []:
        notes = tr.get('notes') or []
        if not notes:
            continue
        peaks = peak_db_map(wav, notes, spb)
        vels, ref = calibrate(peaks, p50, k, max_db)
        for it, v in zip(notes, vels):
            # ⚠ **vel 在 index 3** —— `midi_file` 的模型是
            #   `[start_beat, dur_beat, pitch, vel]`。第一版 append 到第 5 位，
            #   导出后**读不回**（MIDI 文件只有那 4 个字段），实测"写了 6115 音带力度、
            #   读回 0 音带力度"。别把 `song.json` 的 5 元组格式搬到 MIDI 层来。
            it[3] = int(max(1, min(127, v)))
        total += len(notes)
        if report:
            print('  轨 %-10s %5d 音 · 峰值中位 %.1f dB · 力度 %d~%d（中位 %d）'
                  % (tr.get('name'), len(notes), ref, min(vels), max(vels),
                     int(np.median(vels))))
    midi_file.export_midi(d, out)
    print('写 %s（%d 音带力度）' % (out, total))
    return total


def main():
    import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
    ap = argparse.ArgumentParser(description='从分轨音频量逐音力度')
    ap.add_argument('wav', nargs='?', help='分轨音频')
    ap.add_argument('mid', nargs='?', help='转录 MIDI')
    ap.add_argument('out', nargs='?', help='输出 MIDI')
    ap.add_argument('--track', action='append', default=[],
                    help='轨名=wav:mid（可多次，用于一次处理多轨）')
    ap.add_argument('--p50', type=float, default=51.0, help='目标中位力度（默认 51）')
    ap.add_argument('--k', type=float, default=9.0, help='dB→力度 的斜率（默认 9）')
    ap.add_argument('--max-db', type=float, default=18.0, help='允许偏离中位的上限 dB')
    a = ap.parse_args()
    if not (a.wav and a.mid and a.out) and not a.track:
        raise SystemExit('要么给三个位置参数，要么用 --track 轨名=wav:mid')
    if a.track:
        for spec in a.track:
            if '=' not in spec or ':' not in spec.split('=', 1)[1]:
                raise SystemExit('--track 要写成 轨名=wav:mid，收到 %r' % spec)
            nm, rest = spec.split('=', 1)
            w, m = rest.rsplit(':', 1)
            print('%s:' % nm)
            do_one(w, m, m.replace('.mid', '_vel.mid'), a.p50, a.k, a.max_db)
        return 0
    do_one(a.wav, a.mid, a.out, a.p50, a.k, a.max_db)
    return 0


if __name__ == '__main__':
    sys.exit(main())
