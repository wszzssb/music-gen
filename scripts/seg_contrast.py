"""按 song.json 的段落计划，逐段量「响度 / 频谱质心 / 高频占比」——用来看段间对比。

为什么自建：`section_probe.py` 的段落地图是硬编码某首曲的（40 小节 × 1.6s），
换曲子直接报「需要 10060s」。这里直接按 BPM + 各段小节数算时间窗，
对 WAV 做逐段 RMS / 谱质心 / 5-18k 相对能量，输出一张段表。
"""
import json
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu          # 编码兜底：守卫 console_encoding_safe 要求入口脚本调它


def read_wav(path):
    with wave.open(path, 'rb') as w:
        n, ch, sr, nf = w.getnframes(), w.getnchannels(), w.getframerate(), w.getsampwidth()
        raw = w.readframes(n)
    dt = {1: np.int8, 2: np.int16, 4: np.int32}[nf]
    a = np.frombuffer(raw, dtype=dt).astype(np.float64) / float(2 ** (8 * nf - 1))
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr


def seg_stats(x, sr):
    if len(x) < 2048:
        return None
    rms = float(20 * np.log10(max(1e-9, np.sqrt((x ** 2).mean()))))
    mono = x * np.hanning(len(x))
    sp = np.abs(np.fft.rfft(mono))
    fr = np.fft.rfftfreq(len(mono), 1.0 / sr)
    pw = sp ** 2 + 1e-12
    cent = float((fr * pw).sum() / pw.sum())
    tot = pw.sum()

    def band(lo, hi):
        m = (fr >= lo) & (fr < hi)
        return float(10 * np.log10(max(1e-12, pw[m].sum() / tot)))

    return rms, cent, band(5000, 18000), band(40, 160)


def main():
    _cu.setup()
    wav, song_json = sys.argv[1], sys.argv[2]
    d = json.load(open(song_json, encoding='utf-8'))
    a, sr = read_wav(wav)
    B = float(d.get('meter', [4, 4])[0]) * 4.0 / float(d.get('meter', [4, 4])[1])
    spb = 60.0 / float(d['bpm'])          # 一拍秒数
    bar_s = B * spb
    print('段         小节  起(s)   时长   RMS dB   质心Hz  5-18k dB  40-160dB')
    t = 0.0
    prev = None
    rows = []
    for s in d['sections']:
        dur = s['bars'] * bar_s
        i0, i1 = int(t * sr), int((t + dur) * sr)
        st = seg_stats(a[i0:i1], sr)
        if st:
            rms, cent, hi, lo = st
            d_rms = '' if prev is None else ' (%+.1f)' % (rms - prev)
            print('%-10s %4d  %6.1f  %5.1f  %7.2f%s  %7.0f  %8.1f  %8.1f'
                  % (s['name'], s['bars'], t, dur, rms, d_rms, cent, hi, lo))
            rows.append((s['name'], rms, cent, hi))
            prev = rms
        t += dur
    if rows:
        r = np.array([x[1] for x in rows])
        c = np.array([x[2] for x in rows])
        h = np.array([x[3] for x in rows])
        print('\n段间起伏：RMS 极差 %.2f dB (σ %.2f) · 质心极差 %.0f Hz (σ %.0f) · '
              '5-18k 极差 %.2f dB (σ %.2f)'
              % (r.max() - r.min(), r.std(), c.max() - c.min(), c.std(),
                 h.max() - h.min(), h.std()))


if __name__ == '__main__':
    main()
