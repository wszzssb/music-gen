#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""根音/调式分析（比全频 chroma 模板匹配可靠）：
  1) 逐小节取 40-250Hz 最强音级 → 贝斯根音 = 和弦根音
  2) 该根音上方 3/4 半音的能量对比 → 大/小三和弦
  3) 全频 chroma 前几名 → 判断是否七和弦/附加音
  4) 低音区脉冲周期性 → 是否有鼓组节奏型

用法: python analyze_bass.py <file> [--bpm N] [--bars-per-line N]
"""
import math
import sys

import numpy as np
import soundfile as sf

NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def chroma(seg, sr, lo=55.0, hi=2000.0, harmonics=5):
    n = 8192
    if len(seg) < n:
        seg = np.pad(seg, (0, n - len(seg)))
    seg = seg[:n] * np.hanning(n)
    S = np.abs(np.fft.rfft(seg))
    f = np.fft.rfftfreq(n, 1 / sr)
    ch = np.zeros(12)
    for h in range(1, harmonics + 1):
        m = (f >= lo * h) & (f <= hi * h)
        for k in np.where(m)[0]:
            fh = f[k] / h
            if fh < lo:
                continue
            ch[int(round(12 * math.log2(fh / 261.6256))) % 12] += S[k] / h
    return ch / max(1e-9, ch.sum())


def bass_pc(seg, sr):
    """低音区 40-250Hz 的 chroma → 根音音级"""
    n = 16384
    if len(seg) < n:
        seg = np.pad(seg, (0, n - len(seg)))
    seg = seg[:n] * np.hanning(n)
    S = np.abs(np.fft.rfft(seg))
    f = np.fft.rfftfreq(n, 1 / sr)
    ch = np.zeros(12)
    for h in (1, 2):
        m = (f >= 40 * h) & (f <= 250 * h)
        for k in np.where(m)[0]:
            fh = f[k] / h
            if fh < 38:
                continue
            ch[int(round(12 * math.log2(fh / 261.6256))) % 12] += S[k] / h
    return ch / max(1e-9, ch.sum())


def main(path, bpm=None, per_line=1):
    x, sr = metrics.read_audio(path, dtype='float32')
    m = x.mean(axis=1)
    dur = len(m) / sr
    if bpm is None:
        bpm = 128.0
    bar = 4 * 60.0 / bpm
    nbars = int(dur / bar)
    print('%s  %.1fs  %.0fBPM  小节%.3fs  共%d小节' %
          (path.split('\\')[-1], dur, bpm, bar, nbars))
    print('小节 | 根音 | 三度  | 七度  | 全频前四音级')
    full_ch = np.zeros(12)
    lines = []
    for b in range(nbars):
        seg = m[int(b * bar * sr):int((b + 1) * bar * sr)]
        if len(seg) < 4096:
            break
        bc = bass_pc(seg, sr)
        root = int(np.argmax(bc))
        fc = chroma(seg, sr)
        full_ch += fc
        maj3 = fc[(root + 4) % 12]
        min3 = fc[(root + 3) % 12]
        qual = 'maj' if maj3 > min3 else 'min'
        sev = fc[(root + 10) % 12] + fc[(root + 11) % 12]
        third = (fc[(root + 3) % 12] + fc[(root + 4) % 12])
        top = sorted(range(12), key=lambda i: -fc[i])[:4]
        lines.append('%4d | %-4s | %-4s(%.2f/%.2f) | %.2f | %s'
                     % (b + 1, NAMES[root], qual, maj3, min3, sev,
                        ' '.join(NAMES[i] for i in top)))
        if (b + 1) % per_line == 0:
            print('\n'.join(lines))
            lines = []
    if lines:
        print('\n'.join(lines))
    fc = full_ch / max(1e-9, full_ch.max())
    order = sorted(range(12), key=lambda i: -full_ch[i])
    print('\n全曲音级排序: ' +
          ', '.join('%s(%.2f)' % (NAMES[i], fc[i]) for i in order[:8]))
    print('三度倾向: E(%.2f) vs D#(%.2f) → %s' %
          (fc[4], fc[3], '大调色彩' if fc[4] > fc[3] else '小调色彩'))
    print('六度倾向: A(%.2f) vs G#(%.2f) → %s' %
          (fc[9], fc[8], '多利亚/大六度' if fc[9] > fc[8] else '自然小调 b6'))


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
import metrics      # noqa: E402  # 统一音频读取（含 ffmpeg 兜底）
if __name__ == '__main__':
    argv = sys.argv[1:]
    bpm = None
    if '--bpm' in argv:
        i = argv.index('--bpm')
        bpm = float(argv[i + 1])
        del argv[i:i + 2]
    main(argv[0], bpm)
