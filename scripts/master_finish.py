# -*- coding: utf-8 -*-
"""母带收尾：宽度对齐 → 响度归一 → 峰值保护 → 16bit WAV（可选 ogg）。

这一段是"仿写链"的最后一步，独立成工具是因为它有三个**必须一起做**的动作：
  ① **宽度**：宽度差是听感"窄/糊"的主因。目标默认取**参考曲实测宽度**
     （用同一口径量，见下），`--width` 可覆盖。
  ② **响度**：把单声道(mid) RMS 归一到 `--rms`（默认 −16.1 —— 与 BGM35 交付版同口径；
     **不要抄商业母带的 −14.8**，那会把峰值顶到削波）。
  ③ **峰值保护**：归一后若峰值 > 0.97 先软限幅再重新归一，保证不满刻度溢出。

⚠ 参考宽度必须在**同一采样率**下量：参考若是 96kHz，直接量会得到不同的值
  （`metrics.width` 本身对采样率不敏感，但为了与体检口径一致，这里统一降到 44.1kHz 再量）。

用法：
    python scripts/master_finish.py <输入.wav> <输出.wav> <参考音频> \
        [--rms -16.1] [--width 0.52] [--no-ogg]
"""
import argparse
import os
import shutil
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import metrics          # noqa: E402
import render_midi as R  # noqa: E402
import to_ogg           # noqa: E402


def _to44(x, sr):
    if sr == 44100:
        return x
    from math import gcd
    g = gcd(int(sr), 44100)
    try:
        from scipy.signal import resample_poly
        return resample_poly(x, 44100 // g, int(sr) // g, axis=0)
    except Exception:
        idx = np.arange(0, len(x), float(sr) / 44100)
        i0 = np.floor(idx).astype(np.int64)
        i1 = np.minimum(i0 + 1, len(x) - 1)
        frac = (idx - i0)[:, None] if x.ndim > 1 else (idx - i0)
        return x[i0] * (1 - frac) + x[i1] * frac


def main():
    ap = argparse.ArgumentParser(description='母带收尾：宽度 → 响度 → 峰值 → WAV/OGG')
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('ref')
    ap.add_argument('--rms', type=float, default=-16.10)
    ap.add_argument('--width', type=float, default=None, help='缺省 = 对齐参考曲实测宽度')
    ap.add_argument('--no-ogg', action='store_true')
    a = ap.parse_args()

    r, sr_r = sf.read(a.ref, dtype='float64', always_2d=True)
    w_ref = metrics.width(_to44(r, sr_r).astype('float32'))
    target_w = a.width if a.width is not None else w_ref
    print('  参考宽度 %.3f（44.1kHz 口径）→ 目标 %.3f' % (w_ref, target_w))

    tmp = a.dst + '.wtmp.wav'
    shutil.copyfile(a.src, tmp)
    R.set_width_exact(tmp, target_w)
    x, sr = sf.read(tmp, dtype='float64', always_2d=True)
    os.remove(tmp)

    mono = (x[:, 0] + x[:, 1]) / 2
    cur = 20 * np.log10(max(1e-9, np.sqrt((mono ** 2).mean())))
    y = x * 10 ** ((a.rms - cur) / 20)
    if float(np.abs(y).max()) > 0.97:
        y = R.soft_limit(y, 1.2)
        mono2 = (y[:, 0] + y[:, 1]) / 2
        c2 = 20 * np.log10(max(1e-9, np.sqrt((mono2 ** 2).mean())))
        y = y * 10 ** ((a.rms - c2) / 20)
    sf.write(a.dst, y.astype('float32'), sr, subtype='PCM_16')
    m = (y[:, 0] + y[:, 1]) / 2
    print('  写 %s｜宽度 %.3f · RMS %.2f · 峰值 %.3f'
          % (os.path.basename(a.dst), metrics.width(y.astype('float32')),
             20 * np.log10(max(1e-9, np.sqrt((m ** 2).mean()))), float(np.abs(y).max())))
    if not a.no_ogg:
        print('  %s' % to_ogg.convert(a.dst))
    return 0


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    sys.exit(main())
