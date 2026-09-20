#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一个音频的**倍频程平衡**对齐到目标音频（逐带增益，STFT 域实现）。

为什么需要（不是"EQ 凑分"，是混音工序）：
  `similarity.py` 的 `octave` 项口径是"10 条倍频程带平均差 3dB → 0 分"，
  而还原曲的带差往往是**成片**的（实测 BGM35 还原：20–40Hz −4.3、80–160Hz −2.5、
  630–18000Hz 全线 −2.3~−5.7）—— 这不是"某一轨冒出来"，
  是**整条频谱倾斜**，属于混音平衡问题，EQ 正是它的工序。
  反过来，**孤立的一两条带**差得多，多半是编配/音色问题，不该拿 EQ 硬填。

⚠ 用法边界：`--max-gain` 默认限制 ±6dB。超过这个量级说明编配或音源有问题，
   应该回去改编配，而不是把 EQ 拉爆（踩过：800Hz 以上整体 +9dB 补"高频层被关掉"，
   `octave` 反而从 78 掉到 41）。

用法：
  python band_match.py <输入音频> <目标音频> <输出音频> [--max-gain 6] [--report]
"""
import argparse
import os
import sys

import numpy as np
import soundfile as sf

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

EDGES = [20, 40, 80, 160, 315, 630, 1250, 2500, 5000, 10000, 18000]
NFFT = 8192
HOP = NFFT // 4


def band_energy_db(x, sr):
    """逐带相对能量（dB）—— 与 `metrics.octave_bands` 同一组边界。

    ⚠ 窗口长度**按采样率折算**（固定 ~186ms）：若固定 8192 点，96kHz 下窗口只有 85ms、
    44.1kHz 下 186ms —— 同一段音乐在两种采样率下会算出不同的带能量
    （实测 96kHz 参考曲 vs 44.1kHz 渲染曲的带差被算成 20dB 级别，全是假的）。
    """
    mono = x.mean(axis=1) if x.ndim > 1 else x
    n = min(len(mono), sr * 600)                  # 上限 10 分钟，够稳定
    mono = mono[:n]
    nfft = 1
    while nfft < sr * 0.186:
        nfft *= 2
    hop = nfft // 4
    frames = np.lib.stride_tricks.sliding_window_view(mono, nfft)[::hop]
    if len(frames) > 2000:
        frames = frames[:: max(1, len(frames) // 2000)]
    S = np.abs(np.fft.rfft(frames * np.hanning(nfft), axis=1)) ** 2
    P = S.mean(axis=0)
    fr = np.fft.rfftfreq(nfft, 1.0 / sr)
    out = []
    for i in range(len(EDGES) - 1):
        lo, hi = EDGES[i], EDGES[i + 1]
        e = float(P[(fr >= lo) & (fr < hi)].sum())
        out.append(10 * np.log10(max(1e-20, e)))
    return np.array(out), fr, P


def _nfft_for(sr):
    """按采样率取 2 的幂窗口（目标 ~186ms，与 `band_energy_db` 一致）"""
    n = 1
    while n < sr * 0.186:
        n *= 2
    return n


def gain_curve(sr, deltas, smooth_oct=0.5):
    """逐带**增益（dB）** → 平滑的对数频率**线性增益曲线**。

    ⚠ 返回值必须是**线性**增益：踩过 —— 这里曾直接返回 dB 值（0.6 / −1.8 / 2.4 …），
    调用端把它当线性系数乘进频谱，于是"低频被乘 −0.25"（= 相位反转 + 微小衰减）、
    整曲能量掉了 12dB，听起来像被砍了低频。
    """
    nfft = _nfft_for(sr)
    fr = np.fft.rfftfreq(nfft, 1.0 / sr)
    centers = np.array([np.sqrt(EDGES[i] * EDGES[i + 1])
                        for i in range(len(EDGES) - 1)])
    # 两端外推：最低带以下用最低带的 delta，最高带以上用最高带的 delta
    cf = np.concatenate([[max(20.0, centers[0] / 1.5)], centers,
                         [min(sr / 2.0, centers[-1] * 1.5)]])
    gv = np.concatenate([[deltas[0]], deltas, [deltas[-1]]])
    g = np.interp(np.log(np.maximum(fr, 1.0)), np.log(cf), gv)
    # 半倍频程平滑（避免带边界出现台阶）
    k = max(1, int(len(g) * smooth_oct / 10.0))
    if k > 1:
        ker = np.hanning(k * 2 + 1)
        ker /= ker.sum()
        g = np.convolve(np.pad(g, k, mode='edge'), ker, mode='same')[k:-k]
    return 10.0 ** (g / 20.0)                 # dB → 线性


def apply_eq(x, sr, g):
    """STFT 域逐点乘增益（重叠相加）。

    ⚠ 窗口必须与 `gain_curve` 的共同长度一致（都走 `_nfft_for(sr)`）——
    否则 96kHz 下增益曲线对应的频率点与 STFT bin 错位，EQ 会变成"乱砍"
    （实测：44.1kHz 的曲线套到 96kHz 上，低频被砍出 −35dB 的假象）。
    """
    nfft = len(g) * 2 - 2
    hop = nfft // 4
    mono_in = x.ndim > 1
    ch = x.shape[1] if mono_in else 1
    xin = x if mono_in else x[:, None]
    out = np.zeros_like(xin)
    win = np.hanning(nfft)
    n = len(xin)
    for ch_i in range(ch):
        y = xin[:, ch_i]
        acc = np.zeros(n + nfft)
        wsum = np.zeros(n + nfft)
        for i in range(0, max(1, n - nfft), hop):
            seg = y[i:i + nfft]
            if len(seg) < nfft:
                seg = np.pad(seg, (0, nfft - len(seg)))
            X = np.fft.rfft(seg * win)
            X *= g
            acc[i:i + nfft] += np.fft.irfft(X, n=nfft) * win
            wsum[i:i + nfft] += win ** 2
        y2 = acc[:n] / np.maximum(wsum[:n], 1e-8)
        out[:, ch_i] = y2
    return out if mono_in else out[:, 0]


def main():
    import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('ref')
    ap.add_argument('out')
    ap.add_argument('--max-gain', type=float, default=6.0,
                    help='单带最大补偿量（dB），超过说明该改编配而不是拉 EQ')
    ap.add_argument('--mix', type=float, default=1.0, help='补偿量缩放 0~1')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--segments', default=None,
                    help='**分段补偿**：段边界（秒，逗号分隔，如 "0,25"）—— 每段各算一套补偿、'
                         '再按时间权重混合。⚠ 整曲平均补偿对**分段音乐会过头**：实测开头 '
                         '630–1250Hz 从 −0.5 掉到 −9.4dB（PITFALLS 214）')
    a = ap.parse_args()

    x, sr = sf.read(a.src, dtype='float64')
    r, sr2 = sf.read(a.ref, dtype='float64')
    if sr != sr2:
        # ⚠ **把参考降到源的采样率**，不要升源 —— 实测重采样会改变逐带能量：
        #   同一段音乐 44.1kHz 下带差 2.82dB，升到 96kHz 后算成 1.21dB，
        #   升采样后的"目标"根本对不上（`metrics.profile` 内部窗口也是按采样率折算的，
        #   两套折算叠在一起就会系统性偏）。统一到**较低**的那个采样率最稳。
        from math import gcd
        tgt = min(int(sr), int(sr2))
        g0 = gcd(int(sr), int(sr2))
        for name in ('x', 'r'):
            pass
        if sr2 != tgt:                                  # 降参考
            up, down = int(tgt // g0), int(sr2 // g0)
            try:
                from scipy.signal import resample_poly
                r = resample_poly(r, up, down, axis=0)
            except Exception:
                idx = np.arange(0, len(r), float(sr2) / tgt)
                i0 = np.floor(idx).astype(np.int64)
                i1 = np.minimum(i0 + 1, len(r) - 1)
                frac = (idx - i0)[:, None] if r.ndim > 1 else (idx - i0)
                r = r[i0] * (1 - frac) + r[i1] * frac
            sr2 = tgt
        if sr != tgt:                                   # 升源（少见）
            up, down = int(tgt // g0), int(sr // g0)
            try:
                from scipy.signal import resample_poly
                x = resample_poly(x, up, down, axis=0)
            except Exception:
                idx = np.arange(0, len(x), float(sr) / tgt)
                i0 = np.floor(idx).astype(np.int64)
                i1 = np.minimum(i0 + 1, len(x) - 1)
                frac = (idx - i0)[:, None] if x.ndim > 1 else (idx - i0)
                x = x[i0] * (1 - frac) + x[i1] * frac
            sr = tgt
        print('已统一采样率到 %d Hz' % sr)
    bd, _fr, _p = band_energy_db(x, sr)
    br, _fr2, _p2 = band_energy_db(r, sr)
    names = ['%d-%d' % (EDGES[i], EDGES[i + 1]) for i in range(len(EDGES) - 1)]
    n_samp, dur = len(x), len(x) / float(sr)

    seg_pts = [float(t) for t in (a.segments or '').split(',') if t.strip()]
    y = None
    if len(seg_pts) >= 2:
        # ⚠ **分段补偿**（2026-09-20，PITFALLS 214）：整曲平均补偿对**分段音乐**会过头 ——
        #   实测开头 630–1250Hz 从 −0.5 掉到 −9.4dB（原曲开头 80–160Hz 主导、中频低 7dB，
        #   中段 315–630Hz 主导：两段分布不同）。做法：每段各算一套 → 各跑一遍**全程** EQ
        #   → 按时间权重**线性混合**（别拼接，避免边界不连续）。
        pts = sorted(set([0.0] + seg_pts + [dur]))
        idx = sorted(set(int(min(n_samp, max(0, round(t * sr)))) for t in pts))
        if len(idx) >= 3:
            xf = max(1, int(min(0.30, dur / (len(idx) * 4)) * sr))
            W = np.zeros((len(idx) - 1, n_samp))
            for k in range(len(idx) - 1):
                W[k, idx[k]:idx[k + 1]] = 1.0
                if k > 0:                       # 左边界前 xf 内淡入
                    a0 = max(0, idx[k] - xf)
                    W[k, a0:idx[k]] = np.linspace(0.0, 1.0, idx[k] - a0)
                if k < len(idx) - 2:            # 右边界后 xf 内淡出
                    b0 = min(n_samp, idx[k + 1] + xf)
                    W[k, idx[k + 1]:b0] = np.linspace(1.0, 0.0, b0 - idx[k + 1])
            W /= np.maximum(W.sum(axis=0, keepdims=True), 1e-9)      # 过渡区归一
            print('分段补偿 %d 段（交界 ±%.2fs 线性混合）' % (len(idx) - 1, xf / float(sr)))
            y = np.zeros_like(x)
            for k in range(len(idx) - 1):
                a0, b0 = idx[k], idx[k + 1]
                bdk, _f, _p = band_energy_db(x[a0:b0], sr)
                brk, _f2, _p2 = band_energy_db(r[a0:b0], sr)
                want_k = np.clip(brk - bdk, -a.max_gain, a.max_gain) * a.mix
                print('  %6.1f–%.1fs  平均带差 %.2f → ≈%.2f dB · 补偿 %s'
                      % (pts[k], pts[k + 1], np.abs(brk - bdk).mean(),
                         np.abs(brk - bdk - want_k).mean(),
                         ' '.join('%+.1f' % v for v in want_k)))
                y += W[k][:, None] * apply_eq(x, sr, gain_curve(sr, want_k))
        else:
            print('  ! --segments 给出的段太挤（有效边界不足）→ 退回整曲补偿')

    if y is None:
        want = np.clip(br - bd, -a.max_gain, a.max_gain) * a.mix
        print('%-12s %8s %8s %8s' % ('带', '源 dB', '目标 dB', '补偿 dB'))
        for i, nm in enumerate(names):
            print('%-12s %8.1f %8.1f %+8.2f' % (nm, bd[i], br[i], want[i]))
        print('平均带差 源 %.2f dB → 补偿后 ≈ %.2f dB'
              % (np.abs(br - bd).mean(), np.abs(br - bd - want).mean()))
        # ⚠ **整曲模式的"分段提示"**（PITFALLS 214）：开头窗的分布与整曲差得多时，
        #   统一补偿会在那一头过头 —— 直接把该用的命令打出来，别等人自己想起来。
        if dur > 60.0:
            hn = int(min(25.0, dur / 3.0) * sr)
            # ⚠ 要量**两侧**的分段差异：统一补偿会过头，根子在**参考（目标）**在开头与整曲
            #   分布不同（本例原曲开头 80–160Hz 主导、中段 315–630Hz 主导，差 7dB）。
            #   只量源那一侧会**漏报**（实测第一版就是这么漏的，提示一次都没打出来）。
            bh, _fh, _ph = band_energy_db(x[:hn], sr)
            rh, _fh2, _ph2 = band_energy_db(r[:hn], sr)
            # ⚠ **直接量"分段能带来多少不同"** —— 别用间接指标。第一版量的是"开头窗与整曲的
            #   频谱偏离"，实测在原曲上也没过阈值、提示一次都没打出来；而用户要回答的问题是
            #   "**该不该分段**"。所以直接比 **开头需要的补偿 vs 整曲补偿**（这就是分段的收益）。
            want_head = np.clip(rh - bh, -a.max_gain, a.max_gain) * a.mix
            gap = np.abs(want_head - want)
            wk = int(np.argmax(gap))
            if gap[wk] > 1.5:
                print('  ⚠ 开头 %.0fs 需要的补偿与整曲平均差 **%.1fdB**（%s：%+.1f vs %+.1f）'
                      ' —— 整曲统一补偿会在那一头过头 → 试 `--segments "0,%.0f"`（PITFALLS 214）'
                      % (hn / float(sr), gap[wk], names[wk], want_head[wk], want[wk],
                         hn / float(sr)))
        y = apply_eq(x, sr, gain_curve(sr, want))
    y *= min(1.0, 0.97 / max(1e-9, float(np.abs(y).max())))
    sf.write(a.out, y, sr)
    if a.report:
        bd2, _f, _pp = band_energy_db(y, sr)
        print('复核：' + ' '.join('%s %+.1f' % (names[i], bd2[i] - br[i])
                                 for i in range(len(names))))
        print('复核平均带差 %.2f dB' % np.abs(br - bd2).mean())
    print('已写 %s' % a.out)


if __name__ == '__main__':
    main()
