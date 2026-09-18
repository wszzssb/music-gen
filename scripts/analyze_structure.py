# -*- coding: utf-8 -*-
"""按**音乐内容**自动切段 —— 段数/段长不固定，跟着音乐走。

## 为什么需要

固定"每 8 小节一段"会让曲式同质化。实测对照：
· 它的最终版是 **26 段 × 8 小节**（规整）；
· 而 `docs/CASE-BGM36.md` 里的好曲段落是 **29.2 / 42.0 / 18.2 / 24.8 / 11.8 秒**（全不等）；
· 用户口径：「**古典的段落是一样的，现代大多数不一样，要看情况**」。

所以段边界要**由音乐决定**，而不是钉死小节数 —— 这也是"更多切分"的前提：
想要更细就调低 `--min-bars`，想让音乐自己决定就用默认。

## 做法（标准音乐结构分析）

1. **逐帧特征**：RMS（响度）· 谱质心（亮度）· 起音密度 · chroma（和声）
2. **新颖度曲线（novelty）**：相邻窗口的特征差（自相似矩阵的对角线邻域差）
3. **峰值检测**：novelty 的局部极大 + 最小段长约束 → 段边界
4. **对齐小节线**：作曲要按小节，所以边界吸附到最近的**小节线**（`--bpm` 决定小节长度）

## 用法

```bash
python scripts/analyze_structure.py <音频> --bpm 75
python scripts/analyze_structure.py <音频> --bpm 75 --min-bars 4      # 要更细的切分
python scripts/analyze_structure.py <音频> --bpm 75 --json out.json   # 直接给 transcribe_to_song
```

⚠ **先看输出的段长分布**：全是同一个值说明检测退化了（该调 `--sensitivity`）；
`CASE-BGM36` 那种好曲是"长短都有"。
"""
import argparse
import json
import os
import sys

import numpy as np
import soundfile as sf


def frame_feats(y, sr, hop=0.05, win=0.2):
    """逐帧特征（hop 秒）：rms / 质心 / 起音密度 / chroma(12)。"""
    h = max(1, int(sr * hop))
    w = max(2, int(sr * win))
    n = max(1, (len(y) - w) // h + 1)
    rms = np.zeros(n)
    cen = np.zeros(n)
    chroma = np.zeros((n, 12))
    onset = np.zeros(n)
    prev = None
    # chroma 的滤波器组（按音级中心频率的邻近 bin 加权）
    freqs = np.fft.rfftfreq(w, 1.0 / sr)
    A4 = 440.0
    with np.errstate(divide='ignore'):
        pcs = np.round(12 * np.log2(np.maximum(freqs, 1e-6) / A4)) % 12
    ok = (freqs > 55) & (freqs < 2000)
    for i in range(n):
        seg = y[i * h:i * h + w]
        if len(seg) < w:
            break
        seg = seg * np.hanning(len(seg))
        rms[i] = np.sqrt((seg ** 2).mean())
        sp = np.abs(np.fft.rfft(seg))
        tot = sp.sum() + 1e-9
        cen[i] = (freqs * sp).sum() / tot
        for pc in range(12):
            m = ok & (pcs == pc)
            chroma[i, pc] = sp[m].sum()
        if prev is not None:
            onset[i] = max(0.0, rms[i] - prev)
        prev = rms[i]
    return rms, cen, onset, chroma, hop


def novelty(rms, cen, onset, chroma, k=8):
    """相邻窗口的特征差 → 新颖度（每帧一个值）。

    `k` = 比较窗口的帧数（默认 8 帧 × 50ms = ±0.4s）。
    """
    def diff(a):
        a = np.asarray(a, dtype=float)
        if a.ndim == 1:
            a = a[:, None]
        d = np.zeros(len(a))
        for i in range(k, len(a) - k):
            before = a[max(0, i - k):i].mean(axis=0)
            after = a[i:min(len(a), i + k)].mean(axis=0)
            d[i] = float(np.abs(after - before).sum())
        return d

    # 各特征各自归一化再相加（量纲差太大，不归一化会被质心主导）
    parts = []
    for a in (np.log(rms + 1e-9), np.log(cen + 1.0), onset, chroma):
        d = diff(a)
        s = d.max()
        parts.append(d / s if s > 1e-12 else d)
    return sum(parts) / len(parts)


def pick_boundaries(nov, hop, dur, min_sec, sens, target=0):
    """novelty 的峰值 → 边界（秒）。

    ⚠ 第一版直接对原始 novelty 找"严格局部极大"，实测**只能检出 4 段**
      （其中一段吃掉 71 小节 = 227 秒），而且 `--sensitivity` 调了没反应 ——
      因为原始曲线锯齿多、真正的段落变化反而被噪声淹没。
    修法两条：① **3 帧平滑**（去锯齿）；② 支持 `--target-segments`，
    **直接取 novelty 最强的 N−1 个峰**（用户要"更多切分"时这是最可控的旋钮）。
    """
    k = np.ones(3) / 3.0
    sm = np.convolve(nov, k, mode='same')
    thr = float(sm.mean() + sens * sm.std())
    cand = [i for i in range(1, len(sm) - 1)
            if sm[i] >= sm[i - 1] and sm[i] >= sm[i + 1] and sm[i] > thr]
    # 同一边界附近的连续峰只留最强的一个
    dedup = []
    for i in sorted(cand, key=lambda x: -sm[x]):
        if all(abs(i - j) * hop >= min_sec for j in dedup):
            dedup.append(i)
    if target > 0:
        picked = sorted(sorted(dedup, key=lambda x: -sm[x])[:max(0, target - 1)])
    else:
        picked = sorted(dedup)
    keep = [0]
    for i in picked:
        t = i * hop
        if t - keep[-1] * hop >= min_sec and dur - t >= min_sec:
            keep.append(i)
    keep.append(int(dur / hop))
    return [p * hop for p in keep], thr


def snap_to_bars(times, bpm, meter=4):
    """把段边界吸附到最近的**小节线**（作曲要按小节）。"""
    bar = meter * 60.0 / bpm
    out = [round(t / bar) for t in times]
    for i in range(1, len(out)):              # 去重 + 保序
        if out[i] <= out[i - 1]:
            out[i] = out[i - 1] + 1
    return out


def main():
    import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
    ap = argparse.ArgumentParser(description='按音乐内容自动切段')
    ap.add_argument('audio', help='参考音频（ogg/wav）')
    ap.add_argument('--bpm', type=float, required=True, help='速度（决定小节长度）')
    ap.add_argument('--meter', type=int, default=4, help='每小节拍数（默认 4）')
    ap.add_argument('--min-bars', type=int, default=4,
                    help='最小段长（小节，默认 4；调小 = 更多切分）')
    ap.add_argument('--sensitivity', type=float, default=1.0,
                    help='峰值阈值 = 均值 + sensitivity×标准差（调小 = 更多边界）')
    ap.add_argument('--target-segments', type=int, default=0,
                    help='目标段数：取 novelty 最强的 N−1 个边界（0 = 用阈值自动定）'
                         '；要"更多切分"时这个旋钮最可控')
    ap.add_argument('--json', default=None, help='把边界写成 json（给 transcribe_to_song）')
    a = ap.parse_args()
    if not os.path.exists(a.audio):
        raise SystemExit('找不到 %s' % a.audio)

    y, sr = sf.read(a.audio, always_2d=True)
    y = y.mean(axis=1)
    dur = len(y) / sr
    bar = a.meter * 60.0 / a.bpm
    min_sec = a.min_bars * bar
    print('音频 %.1fs · %.0f bpm · 小节 %.3fs · 最小段长 %.1f 小节 = %.1fs'
          % (dur, a.bpm, bar, a.min_bars, min_sec))

    rms, cen, onset, chroma, hop = frame_feats(y, sr)
    nov = novelty(rms, cen, onset, chroma)
    times, thr = pick_boundaries(nov, hop, dur, min_sec, a.sensitivity,
                                 a.target_segments or 0)
    bars = snap_to_bars(times, a.bpm, a.meter)
    # 去掉吸附后重复/倒退造成的空段
    clean = [bars[0]]
    for b in bars[1:]:
        if b - clean[-1] >= a.min_bars:
            clean.append(b)
    if clean[-1] < round(dur / bar):
        clean.append(round(dur / bar))

    print('阈值 %.3f · 检出 %d 段' % (thr, len(clean) - 1))
    print('%-5s %8s %8s %6s %s' % ('段', '起(小节)', '共小节', '起(秒)', '长度分布'))
    lens = []
    for i in range(len(clean) - 1):
        b0, b1 = clean[i], clean[i + 1]
        lens.append(b1 - b0)
        print('%-5d %8d %8d %8.1f' % (i + 1, b0, b1 - b0, b0 * bar))
    print()
    print('段长分布: %s（%d 种取值）' % (sorted(set(lens)), len(set(lens))))
    if len(set(lens)) == 1 and len(lens) >= 3:
        print('  ⚠ 全部等长 —— 检测退化了：调小 --min-bars 或 --sensitivity')
    if a.json:
        json.dump({'bpm': a.bpm, 'meter': a.meter, 'bar_sec': bar,
                   'boundaries_sec': [b * bar for b in clean],
                   'boundaries_bar': clean, 'bars': lens},
                  open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('写 %s' % a.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
