#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**逐块频带对齐**：按 8 小节块，把"高频能量占比"压到参考曲上。

为什么需要（`variation` 修不动的原因）：
  `band_match.py` 只对齐**整曲**的频谱倾斜（全曲平均带差 1.29dB 已经很准），
  但 `variation` 罚的是**块间**的亮度跳变。实测 9/26 块的 4–16kHz 能量占比
  偏离参考曲 1.2~1.33×（偏亮）或 0.24~0.67×（偏暗），
  于是块间质心差分 σ 从原曲的 366 涨到 879、`variation` 41。
  **逐块补乐器没用**（试过：安静段加三音高频骨架，块 26 的占比 0.077 → 0.077 没动）。

做法：对每个块算"参考 - 我的"在该频段的差，用**单段 EQ 增益**补上
（`high_shelf`，转折频率取该频段下沿）。块间用短交叉淡化避免台阶。
与 `section_gain.py` 的区别：那个调**整体响度**，这个只调**高频占比**
（不动 RMS 曲线，所以不会碰"段间响度起伏"那一维）。

用法：
  python scripts\\block_eq.py <我的> <参考> <输出> --bpm 150 [--group 8]
                              [--band 4000 16000] [--max 6]
"""
import argparse
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()          # noqa: E402
import band_match as BM                        # noqa: E402


def block_hf(path, sr_want, group, band, bar_s, target_sr=None):
    """逐块的高频能量占比 + 质心

    `target_sr`：把音频重采样到这个采样率再算（参考曲常是 96kHz、成品 44.1kHz，
    不统一的话"高频占比"两侧根本不可比 —— 踩过）。
    """
    y, sr = sf.read(path, dtype='float64', always_2d=True)
    if target_sr and sr != target_sr:
        from math import gcd
        g0 = gcd(int(sr), int(target_sr))
        try:
            from scipy.signal import resample_poly
            y = resample_poly(y, int(target_sr // g0), int(sr // g0), axis=0)
        except Exception:
            idx = np.arange(0, len(y), float(sr) / target_sr)
            i0 = np.floor(idx).astype(np.int64)
            i1 = np.minimum(i0 + 1, len(y) - 1)
            f = (idx - i0)[:, None]
            y = y[i0] * (1 - f) + y[i1] * f
        sr = int(target_sr)
    mono = y.mean(axis=1)
    step = int(group * bar_s * sr)
    lo, hi = band
    rows = []
    for i in range(0, max(1, len(mono) - step // 2), step):
        seg = mono[i:i + step]
        if len(seg) < sr // 2:
            continue
        bd, _fr, _p = BM.band_energy_db(seg, sr)     # 复用同一套带口径
        # 直接算 band 内的占比（用 STFT 平均谱）
        nfft = 1
        while nfft < sr * 0.186:
            nfft *= 2
        hop = nfft // 4
        fr = np.lib.stride_tricks.sliding_window_view(seg, nfft)[::hop]
        if len(fr) > 400:
            fr = fr[:: max(1, len(fr) // 400)]
        S = np.abs(np.fft.rfft(fr * np.hanning(nfft), axis=1)) ** 2
        P = S.mean(axis=0)
        f = np.fft.rfftfreq(nfft, 1.0 / sr)
        tot = float(P.sum())
        hf = float(P[(f >= lo) & (f < hi)].sum())
        cen = float((f * P).sum() / max(1e-20, tot))
        rows.append((hf / max(1e-20, tot), cen))
    return np.array(rows), sr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mine')
    ap.add_argument('ref')
    ap.add_argument('out')
    ap.add_argument('--bpm', type=float, default=150.0)
    ap.add_argument('--group', type=int, default=8)
    ap.add_argument('--band', type=float, nargs=2, default=[4000.0, 16000.0])
    ap.add_argument('--max', type=float, default=6.0, help='单块最大增益 dB')
    ap.add_argument('--smooth', type=int, default=0, help='块间交叉淡化样本数（0=自动 5%%）')
    a = ap.parse_args()
    bar_s = 4 * 60.0 / a.bpm

    _y0, sr0 = sf.read(a.mine, dtype='float64', always_2d=True)
    A, srA = block_hf(a.ref, None, a.group, a.band, bar_s, target_sr=sr0)
    B, sr = block_hf(a.mine, None, a.group, a.band, bar_s, target_sr=sr0)
    sr = sr0
    n = min(len(A), len(B))
    A, B = A[:n], B[:n]
    # 只在**偏亮**方向修（安静段偏暗是"缺内容"，拉 EQ 只会把噪声抬起来）
    want = np.clip(20 * np.log10(np.maximum(1e-6, A[:, 0] / np.maximum(1e-9, B[:, 0]))),
                   -a.max, 0.0)
    print('%-5s %10s %10s %9s %10s' % ('块', '原曲HF', '我HF', '比', '施加dB'))
    for i in range(n):
        print('%-5d %10.3f %10.3f %8.2fx %+9.2f'
              % (i + 1, A[i, 0], B[i, 0], B[i, 0] / max(1e-9, A[i, 0]), want[i]))

    y, sr2 = sf.read(a.mine, dtype='float64', always_2d=True)
    step = int(a.group * bar_s * sr2)
    fc = float(a.band[0])
    g = np.zeros(len(y))
    sm = a.smooth or int(step * 0.05)
    for i in range(n):
        s0, s1 = i * step, min(len(g), (i + 1) * step)
        if s1 <= s0:
            break
        seg_len = s1 - s0
        ramp = np.ones(seg_len)
        k = min(sm, seg_len // 3)
        if k > 1:
            ramp[:k] = np.linspace(0.5, 1.0, k)
            ramp[-k:] = np.linspace(1.0, 0.5, k)
        g[s0:s1] = want[i] * ramp
    g[len(g) - (len(g) % step or step):] = 0.0
    # 逐样本 EQ：对整轨做一次"按时间变化的架子增益"
    out = y.copy()
    nfft = 1
    while nfft < sr2 * 0.186:
        nfft *= 2
    hop = nfft // 4
    win = np.hanning(nfft)
    fr = np.fft.rfftfreq(nfft, 1.0 / sr2)
    mask = (fr >= fc).astype(np.float64)
    acc = np.zeros_like(out)
    wsum = np.zeros(len(out))
    for i in range(0, max(1, len(out) - nfft), hop):
        seg = out[i:i + nfft]
        if len(seg) < nfft:
            seg = np.pad(seg, ((0, nfft - len(seg)), (0, 0)))
        gval = float(np.mean(g[i:i + nfft]))
        gain = 10.0 ** (gval * mask / 20.0)
        X = np.fft.rfft(seg * win[:, None], axis=0)
        X *= gain[:, None]
        acc[i:i + nfft] += np.fft.irfft(X, n=nfft, axis=0) * win[:, None]
        wsum[i:i + nfft] += win ** 2
    out = acc[:len(out)] / np.maximum(wsum[:len(out)], 1e-8)[:, None]
    pk = float(np.abs(out).max())
    if pk > 1.0:
        out /= pk
        print('柔性峰值限制 ×%.3f' % (1.0 / pk))
    sf.write(a.out, out, sr2)
    C, _ = block_hf(a.out, sr2, a.group, a.band, bar_s, target_sr=sr2)
    C = C[:n]
    print('\n块间 HF 比 标准差：处理前 %.3f → 处理后 %.3f'
          % (np.std(np.log2(np.maximum(1e-6, B[:, 0] / np.maximum(1e-9, A[:, 0])))),
             np.std(np.log2(np.maximum(1e-6, C[:, 0] / np.maximum(1e-9, A[:, 0]))))))
    print('已写 %s' % a.out)


if __name__ == '__main__':
    main()
