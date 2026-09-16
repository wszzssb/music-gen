#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**逐段响度对齐**：把成品的每 8 小节响度曲线压到参考曲上（沿时间轴，非 EQ）。

为什么不能用 `arr.mix`（CC7）：渲染端会做**整体响度归一化**，CC7 的差异会被
归一化吃掉（技能里记过："实测 60→127 只差 0.23dB"）。所以段间响度只能在**音频层**做。

与 `band_match.py` 的分工：
  · `band_match` 管**频带**（频谱形状，与时间无关）
  · 本工具管**时间轴上的响度包络**（哪一段该响、哪一段该轻）

为什么需要：用户对还原曲说"开头一直都不像" —— 量出来开头 8 小节比原曲
**轻 3~7dB**（原曲第 1 小节 −25.8、第 7 小节 −17.3，而我是 −30.4 / −23.9），
而第 9 小节起又基本持平（±0.2dB）。也就是说"渐入"做出来了、但**幅度不对**。

用法：
  python scripts\\section_gain.py <我的音频> <参考音频> <输出> --bpm 150 [--group 8] [--max 8]
"""
import argparse
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()          # noqa: E402
import metrics                                 # noqa: E402


def bar_rms(m, sr, bar_s, group):
    """逐 group 小节的 RMS（dB）"""
    step = max(1, int(group * bar_s * sr))
    out = []
    for i in range(0, max(1, len(m) - step // 3), step):
        seg = m[i:i + step]
        if len(seg) < sr // 2:
            continue
        out.append(20 * np.log10(max(1e-9, float(np.sqrt((seg ** 2).mean())))))
    return np.array(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mine')
    ap.add_argument('ref')
    ap.add_argument('out')
    ap.add_argument('--bpm', type=float, default=150.0)
    ap.add_argument('--group', type=int, default=8, help='每几小节一段（默认 8）')
    ap.add_argument('--max', type=float, default=8.0, help='单段最大增益 dB（默认 8）')
    ap.add_argument('--smooth', type=float, default=0.35,
                    help='段内前后过渡比例（0=硬切，0.35=段首尾 35%% 线性过渡）')
    ap.add_argument('--only-over', type=float, default=2.0,
                    help='只修差异超过这个 dB 的段（0 = 每段都修）。'
                         '⚠ 每段都修会把**整曲的段间起伏**也拉成参考曲的复制品：'
                         '实测 `similarity.variation` 84.6 → **34.9**、总分 80.2 → 71.8。'
                         '这个轴比的是"我的块间方差 vs 原曲的"，全段拉平 = 方差比崩掉。')
    ap.add_argument('--only-bars', type=int, default=0,
                    help='只修前 N 小节（0 = 全曲）。用来只治"开头不像"。')
    a = ap.parse_args()

    y, sr = sf.read(a.mine, dtype='float64', always_2d=True)
    r, sr2 = sf.read(a.ref, dtype='float64', always_2d=True)
    if sr != sr2:
        from math import gcd
        g0 = gcd(int(sr), int(sr2))
        try:
            from scipy.signal import resample_poly
            r = resample_poly(r, int(sr // g0), int(sr2 // g0), axis=0)
        except Exception:
            idx = np.arange(0, len(r), float(sr2) / sr)
            i0 = np.floor(idx).astype(np.int64)
            i1 = np.minimum(i0 + 1, len(r) - 1)
            f = (idx - i0)[:, None]
            r = r[i0] * (1 - f) + r[i1] * f
        sr2 = sr
        print('参考已重采样到 %d Hz' % sr)

    mono_m = y.mean(axis=1)
    mono_r = r.mean(axis=1)
    bar_s = 4 * 60.0 / a.bpm
    A = bar_rms(mono_r, sr, bar_s, a.group)
    B = bar_rms(mono_m, sr, bar_s, a.group)
    n = min(len(A), len(B))
    A, B = A[:n], B[:n]
    # **绝对响度**对齐（不是"相对曲线"）：去掉各曲自身均值会让"整体偏轻的段落"
    # 无法被补回来 —— 实测踩过：去均值后前 10 小节的差反而从 3.41 变 3.55 dB。
    # 直接 `ref - mine`：哪一段轻就抬哪一段（单段上限 `--max`，防爆）。
    want = np.clip(A - B, -a.max, a.max)
    if a.only_over > 0:
        small = np.abs(want) <= a.only_over
        want[small] = 0.0
    if a.only_bars > 0:
        lim = int(np.ceil(a.only_bars / float(a.group)))
        want[lim:] = 0.0
    keep = np.median(A - B)                          # 记下整体偏移，报告用
    print('段数 %d（每段 %d 小节）· 整体偏移 %+.1f dB · 单段增益 中位 %+.1f · 范围 %+.1f ~ %+.1f'
          % (n, a.group, keep, float(np.median(want)), float(want.min()), float(want.max())))
    print('   最大的几段：' + ', '.join(
        '第%d段 %+.1f' % (i + 1, want[i]) for i in np.argsort(-np.abs(want))[:5]))

    # 逐样本增益：段内平滑过渡（避免段界"台阶"）
    step = int(a.group * bar_s * sr)
    g = np.zeros(len(mono_m))
    for i in range(n):
        s0, s1 = i * step, min(len(g), (i + 1) * step)
        if s1 <= s0:
            break
        seg_len = s1 - s0
        k = int(seg_len * max(0.0, min(0.45, a.smooth)))
        ramp = np.ones(seg_len)
        if k > 1:
            ramp[:k] = np.linspace(0.5, 1.0, k)     # 段首从"上一段的一半"升上来
            ramp[-k:] = np.linspace(1.0, 0.5, k)
        g[s0:s1] = 10 ** (want[i] / 20.0) * ramp
    g[g == 0] = 1.0
    out = y * g[:, None]
    # ⚠ 峰值保护**要轻**：这里只是整链的中间一步，大比例缩放会把刚对齐好的响度拉偏
    #   （实测：+4.7dB 的段把峰值顶到 0.99 → 整体 ×0.834 = 全曲掉 1.6dB，
    #   平均绝对差反而从 1.95 涨到 3.27）。只做"刚好不越界"的柔性限制，
    #   真正削峰交给链尾（`band_match` / ogg 编码前）。
    pk = float(np.abs(out).max())
    if pk > 1.0:
        out /= pk
        print('柔性峰值限制 ×%.3f（只到 1.0，不做额外留白）' % (1.0 / pk))
    sf.write(a.out, out, sr)
    mono2 = out.mean(axis=1)
    B2 = bar_rms(mono2, sr, bar_s, a.group)[:n]
    print('对齐前平均绝对差 %.2f dB → 对齐后 %.2f dB'
          % (np.abs(A - B).mean(), np.abs(A - B2).mean()))
    print('已写 %s' % a.out)


if __name__ == '__main__':
    main()
