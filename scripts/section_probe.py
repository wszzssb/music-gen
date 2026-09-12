#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分段体检：找出哪一段弱（响度/亮度/低频/宽度/起音密度）
用法: python section_probe.py <file> [小节长秒数]
"""
import sys

import numpy as np
import soundfile as sf

SECS = [('Intro', 0, 4), ('A', 4, 12), ('B', 12, 20), ("A'", 20, 28),
        ("B'", 28, 36), ('Outro', 36, 40)]


def avg_spectrum(m, sr, n=8192):
    """整段平均频谱（单窗口测亮度会被随机相位骗，必须在段内多帧平均）"""
    win = np.hanning(n)
    acc = None
    cnt = 0
    for i in range(0, max(1, len(m) - n), n // 2):
        s = np.abs(np.fft.rfft(m[i:i + n] * win))
        acc = s if acc is None else acc + s
        cnt += 1
    if acc is None:
        acc = np.abs(np.fft.rfft(np.pad(m, (0, n - len(m)))[:n] * win))
        cnt = 1
    return acc / max(1, cnt)


def band(S, sr, lo, hi):
    f = np.fft.rfftfreq((len(S) - 1) * 2, 1 / sr)
    k = (f >= lo) & (f <= hi)
    return 20 * np.log10(max(1e-12, np.sqrt((S[k] ** 2).mean())))


def main(path, bar):
    x, sr = metrics.read_audio(path, dtype='float32')
    dur = len(x) / sr
    need = SECS[-1][2] * bar
    if need > dur + 0.5:
        print('!! 段落地图不适用：SECS 需要 %.1fs，文件只有 %.1fs\n'
              '   SECS 是按某首曲子硬编码的（40 小节 × 1.6s）。换曲子请改脚本里的 SECS，\n'
              '   或改用 analyze_ref2.py / scorecard.py（不依赖小节数）。' % (need, dur))
        return
    print('%-7s %7s %8s %8s %10s %7s %8s' %
          ('段落', 'RMS', '质心Hz', '6-16k', '低频40-160', '宽度', '起音/秒'))
    print('  (段落地图 SECS 按 40 小节 × %.3fs 硬编码；换曲子请改 SECS 或改用 scorecard.py)' % bar)
    for name, b0, b1 in SECS:
        seg = x[int(b0 * bar * sr):int(b1 * bar * sr)]
        if len(seg) < 4096:
            continue
        m = seg.mean(axis=1)
        rms = 20 * np.log10(max(1e-9, np.sqrt((m ** 2).mean())))
        S = avg_spectrum(m, sr)
        f = np.fft.rfftfreq((len(S) - 1) * 2, 1 / sr)
        cen = float((S * f).sum() / max(1e-9, S.sum()))
        mid = (seg[:, 0] + seg[:, 1]) / 2
        side = (seg[:, 0] - seg[:, 1]) / 2
        w = float(np.sqrt((side ** 2).mean()) / max(1e-9, np.sqrt((mid ** 2).mean())))
        env = np.abs(m)
        hop = int(sr * 0.01)
        fl = np.maximum(np.diff(env), 0)
        fe = np.array([fl[i:i + hop].sum() for i in range(0, len(fl) - hop, hop)])
        ons = int((fe > fe.mean() + 2 * fe.std()).sum()) / (len(m) / sr)
        print('%-7s %7.1f %8.0f %8.1f %10.1f %7.3f %8.1f'
              % (name, rms, cen, band(S, sr, 6000, 16000),
                 band(S, sr, 40, 160), w, ons))


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
import metrics      # noqa: E402  # 统一音频读取（含 ffmpeg 兜底）
if __name__ == '__main__':
    main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 1.6)
