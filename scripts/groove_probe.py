#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量原曲的**微时序**（起音偏移 / swing / 人性化）—— `AUDIT-CHECKLIST.md` 第 5 层第 1 条。

背景：我一直把音符量化到 **0.25 拍**（16 分格），而真实演奏的起音**不会正好落在格子上**
——爵士/流行里普遍存在"八分音符成对演奏时后一个延迟"（swing）以及整体的**提前量**
（推着走的段落在拍点前几毫秒起音）。这一层完全没抄，是我"听着不像"的一个已知盲区。

口径（能测的那部分）：
  ① 从 Demucs 分轨取**贝斯或鼓**（起音最清晰）：用谱通量做逐音符起音时刻
  ② 每个起音落在**最近的 16 分格**上，量它偏离格子的量（毫秒 + 拍的比例）
  ③ 按"格内位置"（16 分格的第几格）统计偏移 → 如果**偶数格早、奇数格晚**就是 swing
  ④ 输出可直接套到 MIDI 上的**偏移表**（每格给一个偏移，单位拍）

用法：
  python scripts\\groove_probe.py <音频或分轨目录> --bpm 150 [--track bass|drums] [--json]
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()          # noqa: E402
import metrics                                 # noqa: E402

BPM = 150.0


def onsets_flux(y, sr, k=2.0, min_gap=0.045, hop=128):
    """谱通量起音（比 `melody_profile.onsets` 更密：hop 128 ≈ 5.8ms 分辨率）。

    `min_gap` 取 45ms：16 分格在 150BPM 是 100ms，取一半能分开相邻格，
    又不会把同一音的多次激励算成多个起音。
    """
    n = 1024
    frames = np.lib.stride_tricks.sliding_window_view(y, n)[::hop]
    X = np.abs(np.fft.rfft(frames * np.hanning(n), axis=1))
    flux = np.maximum(X[1:] - X[:-1], 0).sum(axis=1)
    # 局部自适应阈值（全局阈值会漏掉轻的起音）
    w = 12
    thr = np.array([flux[max(0, i - w):i + w].mean() + k * flux[max(0, i - w):i + w].std()
                    for i in range(len(flux))])
    keep, last = [], -1e9
    for i in range(1, len(flux) - 1):
        t = i * hop / sr
        if flux[i] > thr[i] and flux[i] >= flux[i - 1] and flux[i] > flux[i + 1] \
                and t - last >= min_gap:
            keep.append(t)
            last = t
    return np.array(keep)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('src', help='音频文件或 Demucs 分轨目录')
    ap.add_argument('--bpm', type=float, default=BPM)
    ap.add_argument('--track', default=None, help='分轨名（bass/drums/other…），目录模式用')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    path = a.src
    if os.path.isdir(path):
        cands = ([a.track] if a.track else []) + ['bass', 'drums', 'other', 'piano']
        path = None
        for c in cands:
            p = os.path.join(a.src, '%s.wav' % c)
            if os.path.exists(p):
                path = p
                break
        if not path:
            raise SystemExit('目录里找不到可用的分轨：%s' % a.src)
    m, sr, x = metrics.load(path)
    mono = x.mean(axis=1) if x.ndim > 1 else x
    spb = 60.0 / a.bpm
    grid = spb / 4.0                        # 16 分格
    ons = onsets_flux(mono, sr)
    if len(ons) < 16:
        raise SystemExit('起音太少（%d），换一条分轨' % len(ons))

    # 每个起音 → 最近的 16 分格 + 偏移
    gi = np.round(ons / grid).astype(np.int64)
    dev = ons - gi * grid                   # 秒；正 = 晚于格子
    slot = ((gi % 16) + 16) % 16            # 格内位置 0..15
    print('源：%s（%.0f 秒，%d 个起音）' % (os.path.basename(path), len(mono) / sr, len(ons)))
    print('网格 %.1f ms（16 分格 @ %.0f BPM）' % (grid * 1000, a.bpm))
    print('\n整体偏移：中位 %+.1f ms · 均值 %+.1f ms · 标准差 %.1f ms'
          % (np.median(dev) * 1000, dev.mean() * 1000, dev.std() * 1000))
    print('（正 = 习惯性晚于格子，负 = 推着走）')

    # 按格内位置统计
    print('\n%-6s %7s %9s %9s %9s' % ('格', '起音数', '中位偏移', 'P25', 'P75'))
    table = []
    for s in range(16):
        d = dev[slot == s]
        if len(d) < 4:
            table.append(None)
            continue
        q = np.percentile(d, [25, 50, 75])
        table.append(float(q[1]))
        print('%-6d %7d %+8.1fms %+8.1fms %+8.1fms'
              % (s, len(d), q[1] * 1000, q[0] * 1000, q[2] * 1000))

    # swing 判据：偶数格（正拍/八分正位）vs 奇数格（八分反位）
    ev = dev[(slot % 2 == 0)]
    od = dev[(slot % 2 == 1)]
    if len(ev) >= 8 and len(od) >= 8:
        swing = (np.median(od) - np.median(ev)) * 1000
        print('\nswing 判据：偶数格中位 %+.1f ms · 奇数格中位 %+.1f ms → **差 %+.1f ms**'
              % (np.median(ev) * 1000, np.median(od) * 1000, swing))
        print('（差 >15ms 且为正 = 后一个八分被推后，是 swing；接近 0 = 直拍）')

    # 可直接套用的偏移表（单位：拍）
    offs = [0.0 if t is None else round(t / spb, 4) for t in table]
    print('\n可用于 MIDI 的偏移表（每格偏移，单位拍）：')
    print('  [%s]' % ', '.join('%.4f' % v for v in offs))
    if a.json:
        json.dump({'bpm': a.bpm, 'n': len(ons), 'dev_ms_med': float(np.median(dev) * 1000),
                   'dev_ms_std': float(dev.std() * 1000),
                   'per_slot_beats': offs}, sys.stdout, ensure_ascii=False)
        print()
    print('\n⚠ 测不到的部分：滑音/颤音/踏板不在这张表里（另见 AUDIT-CHECKLIST 第 5 层）。')


if __name__ == '__main__':
    main()
