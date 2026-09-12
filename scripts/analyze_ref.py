#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参考曲分析：时长/响度/速度/调性/频段分布/立体声宽度（用于给合成器定风格参数）"""
import sys
import numpy as np
import soundfile as sf

A4 = 440.0
NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def band_energy(spec, freqs, lo, hi):
    m = (freqs >= lo) & (freqs < hi)
    return float(np.sqrt((spec[m] ** 2).mean())) if m.any() else 0.0


def main(path):
    x, sr = metrics.read_audio(path, dtype='float32')
    mono = x.mean(axis=1)
    dur = len(mono) / sr
    rms = float(np.sqrt((mono ** 2).mean()))
    peak = float(np.abs(x).max())
    # 立体声宽度（侧/中比）
    if x.shape[1] > 1:
        mid = (x[:, 0] + x[:, 1]) / 2
        side = (x[:, 0] - x[:, 1]) / 2
        width = float(np.sqrt((side ** 2).mean()) / max(1e-9, np.sqrt((mid ** 2).mean())))
    else:
        width = 0.0

    # 频谱（整曲平均）
    n = 1 << 15
    win = np.hanning(n)
    spec = np.zeros(n // 2 + 1)
    hop = n // 2
    cnt = 0
    for i in range(0, len(mono) - n, hop):
        spec += np.abs(np.fft.rfft(mono[i:i + n] * win))
        cnt += 1
    spec /= max(1, cnt)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    total = band_energy(spec, freqs, 20, 20000)
    bands = [('sub 20-60', 20, 60), ('bass 60-250', 60, 250), ('low-mid 250-800', 250, 800),
             ('mid 800-2.5k', 800, 2500), ('hi-mid 2.5k-6k', 2500, 6000),
             ('air 6k-16k', 6000, 16000)]
    # 频谱质心 + 谱滚降
    centroid = float((spec * freqs).sum() / max(1e-9, spec.sum()))
    cum = np.cumsum(spec ** 2)
    rolloff = float(freqs[np.searchsorted(cum, 0.85 * cum[-1])])

    # 起音包络 → 速度估计
    env = np.abs(mono)
    env = np.convolve(env, np.ones(int(sr * 0.01)) / int(sr * 0.01), mode='same')
    flux = np.diff(np.maximum(env, 0))
    flux = np.maximum(flux, 0)
    ds = 1
    hop2 = int(sr * 0.01)
    fe = np.array([flux[i:i + hop2].sum() for i in range(0, len(flux) - hop2, hop2)])
    fe = fe - fe.mean()
    ac = np.correlate(fe, fe, 'full')[len(fe) - 1:]
    ac /= max(1e-9, ac[0])
    lo = int(0.30 / 0.01)          # 200 BPM
    hi = min(len(ac) - 1, int(2.0 / 0.01))   # 30 BPM
    if hi > lo:
        lag = lo + int(np.argmax(ac[lo:hi]))
        bpm = 60.0 / (lag * 0.01)
        while bpm < 70:
            bpm *= 2
        while bpm > 180:
            bpm /= 2
    else:
        bpm = 0.0
    onset_rate = float((fe > fe.std() * 2.5).sum() / dur)

    # 调性（chroma）
    chroma = np.zeros(12)
    for i in range(0, len(mono) - n, hop * 2):
        s = np.abs(np.fft.rfft(mono[i:i + n] * win))
        for k in range(1, 12 * 6):
            f = A4 * 2 ** ((k - 69) / 12)
            if f >= sr / 2:
                break
            j = int(round(f / (sr / n)))
            if j < len(s):
                chroma[k % 12] += s[j]
    chroma /= max(1e-9, chroma.max())
    best = None
    for i in range(12):
        for scale, tag in ((MAJOR, 'maj'), (MINOR, 'min')):
            r = float(np.corrcoef(np.roll(chroma, -i), scale)[0, 1])
            if best is None or r > best[0]:
                best = (r, NAMES[i] + ' ' + tag, i)
    key = best[1]
    # 音高分布前 6
    top = sorted(range(12), key=lambda i: -chroma[i])[:6]

    print('文件      :', path)
    print('时长      : %.2fs   采样率 %d  声道 %d' % (dur, sr, x.shape[1]))
    print('峰值/RMS  : %.3f / %.4f  (%.1f dBFS / %.1f dBFS)'
          % (peak, rms, 20 * np.log10(max(peak, 1e-9)), 20 * np.log10(max(rms, 1e-9))))
    print('立体声宽度: %.3f (side/mid)' % width)
    print('速度估计  : %.0f BPM   起音密度 %.1f 个/秒' % (bpm, onset_rate))
    print('调性估计  : %s (相关 %.2f)  音级分布 %s'
          % (key, best[0], ', '.join(NAMES[i] for i in top)))
    print('频谱质心  : %.0f Hz   85%% 滚降 %.0f Hz' % (centroid, rolloff))
    print('频段占比  :')
    for name, lo_, hi_ in bands:
        e = band_energy(spec, freqs, lo_, hi_)
        print('   %-16s %5.1f%%' % (name, 100 * e / max(1e-9, total)))


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
import metrics      # noqa: E402  # 统一音频读取（含 ffmpeg 兜底）
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python scripts\\analyze_ref.py <音频文件>')
        raise SystemExit(2)
    main(sys.argv[1])
