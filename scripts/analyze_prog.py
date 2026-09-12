#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""和弦进行读取（root-anchored 法，比全频模板匹配可靠）：
  1) 根音取 80-220Hz（避开底鼓的 40-60Hz 泥浆）
  2) 以该根音为锚，比较三度/五度/七度/四度能量 → 定 maj/min/7/sus/6
  3) 输出逐小节进行 + 合并后的进行

用法: python analyze_prog.py <file> [--bpm N] [--from N] [--to N]
"""
import math
import sys

import numpy as np
import soundfile as sf

NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def chroma_band(seg, sr, lo, hi, n=16384, harmonics=4, center=261.6256):
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
            ch[int(round(12 * math.log2(fh / center))) % 12] += S[k] / h
    return ch


def identify(seg, sr):
    root_ch = chroma_band(seg, sr, 80, 220, harmonics=2)
    root = int(np.argmax(root_ch))
    fc = chroma_band(seg, sr, 180, 2000, harmonics=4)
    fc /= max(1e-9, fc.sum())
    def e(semi):
        return fc[(root + semi) % 12]
    maj3, min3, perf5, dim5, min7, maj7, sus4, six = (e(4), e(3), e(7), e(6),
                                                      e(10), e(11), e(5), e(9))
    third = 'maj' if maj3 > min3 else 'min'
    name = NAMES[root] + ('' if third == 'maj' else 'm')
    # 七和弦判定
    if third == 'maj' and maj7 > min7 * 1.15 and maj7 > 0.08:
        name += 'maj7'
    elif min7 > 0.08:
        name += '7'
    elif six > perf5 * 0.9 and six > 0.10:
        name += '6'
    elif sus4 > (maj3 + min3) * 0.75 and sus4 > 0.12:
        name = NAMES[root] + 'sus4'
    conf = max(maj3, min3) / max(1e-9, maj3 + min3 + 1e-9)
    return name, root, (maj3 + min3), perf5


def main(path, bpm, b0, b1):
    x, sr = metrics.read_audio(path, dtype='float32')
    m = x.mean(axis=1)
    bar = 4 * 60.0 / bpm
    nbars = int(len(m) / bar)
    b1 = min(b1 or nbars, nbars)
    seq = []
    print('%s  %.0fBPM  小节%.3fs  分析第%d-%d小节' %
          (path.split('\\')[-1], bpm, bar, b0, b1))
    for b in range(b0 - 1, b1):
        seg = m[int(b * bar * sr):int((b + 1) * bar * sr)]
        if len(seg) < 8192:
            break
        name, root, third_e, fifth_e = identify(seg, sr)
        seq.append(name)
    # 打印，每 8 小节一行
    for i in range(0, len(seq), 8):
        print('  %3d: %s' % (b0 + i, '  '.join('%-8s' % c for c in seq[i:i + 8])))
    # 合并连续相同
    merged = []
    for c in seq:
        if not merged or merged[-1][0] != c:
            merged.append([c, 1])
        else:
            merged[-1][1] += 1
    print('\n合并后的进行（和弦 x 小节数）:')
    print('  ' + ' | '.join('%s×%d' % (c, n) for c, n in merged))


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
import metrics      # noqa: E402  # 统一音频读取（含 ffmpeg 兜底）
if __name__ == '__main__':
    argv = sys.argv[1:]
    bpm, b0, b1 = 128.0, 1, None
    for flag, cast in (('--bpm', float), ('--from', int), ('--to', int)):
        if flag in argv:
            i = argv.index(flag)
            v = cast(argv[i + 1])
            del argv[i:i + 2]
            if flag == '--bpm':
                bpm = v
            elif flag == '--from':
                b0 = v
            else:
                b1 = v
    main(argv[0], bpm, b0, b1)
