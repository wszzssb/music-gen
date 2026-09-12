#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""噪声/杂音体检：
  1) 安静段与尾巴的 6-16kHz 电平（沙沙声/底噪）
  2) 谱平坦度（越高越像噪声）
  3) 逐音符起音后的瞬态电平（'嚓'声）
用法: python noise_probe.py <file> [file...]
"""
import sys

import numpy as np
import soundfile as sf


def band_level(seg, sr, lo=6000.0, hi=16000.0):
    n = 1 << 14
    if len(seg) < n:
        seg = np.pad(seg, (0, n - len(seg)))
    win = np.hanning(n)
    S = np.abs(np.fft.rfft(seg[:n] * win))
    f = np.fft.rfftfreq(n, 1 / sr)
    m = (f >= lo) & (f <= hi)
    return 20 * np.log10(max(1e-12, np.sqrt((S[m] ** 2).mean())))


def flatness(seg, sr, lo=1000.0, hi=16000.0):
    n = 1 << 15
    if len(seg) < n:
        seg = np.pad(seg, (0, n - len(seg)))
    S = np.abs(np.fft.rfft(seg[:n] * np.hanning(n))) + 1e-12
    f = np.fft.rfftfreq(n, 1 / sr)
    m = (f >= lo) & (f <= hi)
    p = S[m] ** 2
    return float(np.exp(np.log(p).mean()) / p.mean())


def probe(path):
    x, sr = metrics.read_audio(path, dtype='float32')
    m = x.mean(axis=1)
    rms = 20 * np.log10(max(1e-12, np.sqrt((m ** 2).mean())))
    quiet = m[int(0.04 * sr):int(0.24 * sr)]
    tail = m[int(-1.2 * sr):]
    # 最安静的 0.5 秒（滑动找），更能暴露底噪
    w = int(0.5 * sr)
    if len(m) > w:
        e = np.array([np.sqrt((m[i:i + w] ** 2).mean())
                      for i in range(0, len(m) - w, w // 4)])
        k = int(np.argmin(e)) * (w // 4)
        soft = m[k:k + w]
    else:
        soft = m
    print('%-30s 全曲RMS %6.1f | 6-16k: 开头%7.1f 最静%7.1f 尾巴%7.1f dBFS | 平坦度 %.4f'
          % (path.split('\\')[-1], rms, band_level(quiet, sr),
             band_level(soft, sr), band_level(tail, sr), flatness(m, sr)))


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
import metrics      # noqa: E402  # 统一音频读取（含 ffmpeg 兜底）
if __name__ == '__main__':
    for p in sys.argv[1:]:
        probe(p)
