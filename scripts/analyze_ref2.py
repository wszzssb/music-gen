#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参考曲二次分析：倍频程音色平衡 + 鼓组/速度确认 + 段落响度曲线"""
import sys
import numpy as np
import soundfile as sf


def main(path):
    x, sr = metrics.read_audio(path, dtype='float32')
    mono = x.mean(axis=1)
    dur = len(mono) / sr
    n = 1 << 15
    win = np.hanning(n)
    hop = n // 2

    spec = np.zeros(n // 2 + 1)
    frames = []
    for i in range(0, len(mono) - n, hop):
        s = np.abs(np.fft.rfft(mono[i:i + n] * win))
        spec += s
        frames.append(s)
    spec /= max(1, len(frames))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    m20 = freqs <= 20000
    e_tot = (spec[m20] ** 2).sum()

    print('== 倍频程能量分布（相对最强频段, dB） ==')
    octs = [(20, 40), (40, 80), (80, 160), (160, 315), (315, 630), (630, 1250),
            (1250, 2500), (2500, 5000), (5000, 10000), (10000, 18000)]
    lv = []
    for lo, hi in octs:
        m = (freqs >= lo) & (freqs < hi)
        lv.append(10 * np.log10(max(1e-20, (spec[m] ** 2).sum() / e_tot)))
    mx = max(lv)
    for (lo, hi), v in zip(octs, lv):
        bar = '#' * int(max(0, 40 + v - mx))
        print('  %6d-%-6d %7.1f dB  %s' % (lo, hi, v - mx, bar))

    # 低频起始（是否有鼓）
    def env_of(lo, hi, smooth=0.005):
        m = (freqs >= lo) & (freqs < hi)
        e = np.array([ (f[m] ** 2).sum() for f in frames ])
        k = max(1, int(smooth * sr / hop))
        return np.convolve(e, np.ones(k) / k, mode='same')

    lo_e = env_of(40, 130)
    lo_e = np.maximum(np.diff(lo_e), 0)
    lo_e -= lo_e.mean()
    ac = np.correlate(lo_e, lo_e, 'full')[len(lo_e) - 1:]
    ac /= max(1e-9, ac[0])
    fps = sr / hop
    print('== 低频(40-130Hz)脉冲周期性 → 是否有底鼓 ==')
    top = np.argsort(-ac[3:int(3 * fps)])[:6] + 3
    for lag in sorted(top):
        t = lag / fps
        print('   lag %.3fs  (%6.1f BPM 若为四分音符 / %5.1f 小节位) 自相关 %.3f'
              % (t, 60 / t, t, ac[lag]))

    # 打击/起音密度：宽频起音
    hi_e = env_of(2500, 12000, 0.01)
    hf = np.maximum(np.diff(hi_e), 0)
    thr = hf.mean() + 2.0 * hf.std()
    hits = int((hf > thr).sum())
    print('== 高频起音(踩镲/泛音) 密度: %.1f 个/秒 ==' % (hits / dur))

    # 段落响度（每 4 秒）
    print('== 响度走势（每 4 秒 RMS dB） ==')
    seg = int(sr * 4)
    out = []
    for i in range(0, len(mono) - seg, seg):
        r = np.sqrt((mono[i:i + seg] ** 2).mean())
        out.append(20 * np.log10(max(r, 1e-9)))
    line = ' '.join('%5.1f' % v for v in out[:26])
    print('  ' + line)


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
import metrics      # noqa: E402  # 统一音频读取（含 ffmpeg 兜底）
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python scripts\\analyze_ref2.py <音频文件>')
        raise SystemExit(2)
    main(sys.argv[1])
