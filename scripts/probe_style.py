#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""风格画像：安静段 chroma（干净） + 逐拍节奏型（底鼓/踩镲） + 结构
用法: python probe_style.py <file> [--bpm N]
"""
import math
import sys

import numpy as np
import soundfile as sf

NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def chroma(seg, sr, lo=55.0, hi=2500.0, harmonics=6):
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
    return ch


def band_env(m, sr, lo, hi, hop=256):
    n = 1024
    win = np.hanning(n)
    f = np.fft.rfftfreq(n, 1 / sr)
    k = (f >= lo) & (f <= hi)
    out = []
    for i in range(0, len(m) - n, hop):
        S = np.abs(np.fft.rfft(m[i:i + n] * win))[k]
        out.append(float(np.sqrt((S ** 2).mean())))
    return np.array(out), hop / sr


def main(path, bpm=128.0):
    x, sr = metrics.read_audio(path, dtype='float32')
    m = x.mean(axis=1)
    dur = len(m) / sr
    beat = 60.0 / bpm
    bar = 4 * beat
    print('%s %.1fs %.0fBPM' % (path.split('\\')[-1], dur, bpm))

    # 1) 最安静的连续 8 小节（乐器最少）→ 干净 chroma
    bar_rms = []
    for b in range(int(dur / bar)):
        seg = m[int(b * bar * sr):int((b + 1) * bar * sr)]
        bar_rms.append(20 * np.log10(max(1e-9, np.sqrt((seg ** 2).mean()))))
    bar_rms = np.array(bar_rms)
    k = int(np.argmin(np.convolve(bar_rms, np.ones(8) / 8, 'valid')))
    quiet = m[int(k * bar * sr):int((k + 8) * bar * sr)]
    qc = chroma(quiet, sr)
    qc /= max(1e-9, qc.max())
    order = sorted(range(12), key=lambda i: -qc[i])
    print('\n[最安静段 第%d-%d小节, RMS %.1fdB] 音级: %s' %
          (k + 1, k + 8, bar_rms[k:k + 8].mean(),
           ', '.join('%s(%.2f)' % (NAMES[i], qc[i]) for i in order[:8])))
    tri = qc[order[0]] + qc[(order[0] + 4) % 12] + qc[(order[0] + 7) % 12]
    print('  以 %s 为根的三和弦能量 %.2f；三度 E/D#=%.2f/%.2f 六度 A/G#=%.2f/%.2f'
          % (NAMES[order[0]], tri / 3, qc[4], qc[3], qc[9], qc[8]))

    # 2) 逐拍节奏型：低频(40-120)看底鼓，高频(7-12k)看踩镲
    lo_e, dt = band_env(m, sr, 40, 120)
    hi_e, _ = band_env(m, sr, 7000, 12000)
    # 取最响的 16 小节做模板
    loud0 = int(np.argmax(np.convolve(bar_rms, np.ones(16) / 16, 'valid')))
    b0 = int(loud0 * bar / dt)
    bl = int(16 * bar / dt)
    seg_lo = lo_e[b0:b0 + bl]
    seg_hi = hi_e[b0:b0 + bl]
    print('\n[最响段 第%d-%d小节] 16 小节平均节奏型（每格=1/4拍，★=强 ◇=中 ·=弱）'
          % (loud0 + 1, loud0 + 16))
    for name, env in (('低频40-120(底鼓/贝斯)', seg_lo), ('高频7-12k(踩镲/泛音)', seg_hi)):
        step = max(1, int(beat / 4 / dt))          # 十六分音符
        prof = []
        for i in range(16):                        # 一小节 16 个十六分
            s = i * step
            prof.append(float(env[s:s + step].max()) if s < len(env) else 0.0)
        prof = np.array(prof) / max(1e-9, max(prof))
        row = ''.join('★' if v > 0.66 else ('◇' if v > 0.33 else '·') for v in prof)
        print('  %-22s %s' % (name, row))

    # 3) 结构：每 8 小节响度
    print('\n[结构] 每 8 小节 RMS:')
    nb = len(bar_rms) // 8
    print('  ' + ' '.join('%d:%.0f' % (i * 8 + 1, bar_rms[i * 8:(i + 1) * 8].mean())
                          for i in range(nb)))


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
import metrics      # noqa: E402  # 统一音频读取（含 ffmpeg 兜底）
if __name__ == '__main__':
    argv = sys.argv[1:]
    bpm = 128.0
    if '--bpm' in argv:
        i = argv.index('--bpm')
        bpm = float(argv[i + 1])
        del argv[i:i + 2]
    main(argv[0], bpm)
