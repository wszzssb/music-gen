# -*- coding: utf-8 -*-
"""逐带体检（跨采样率可比、且把"电平差"与"音色平衡差"分开报）。

为什么必须有这个工具（每条都是踩出来的）：
  ① **`metrics._avg_spec` 用固定 8192 点窗**（不按采样率折算）—— 同一段音乐
     44.1kHz 下窗长 186ms、96kHz 下只有 85ms，逐带能量因此不同
     （实测 BGM29 的 20-40 带差 1.3 dB、BGM35 差 2.6 dB）。
     所以直接拿"96kHz 参考"比"44.1kHz 候选"得到的低频差里有一两 dB 是假的。
     → 本工具**先把两边都降到 44.1kHz**再算（用 `band_match.band_energy_db` 的口径，
       它的窗口按采样率折算成 ~186ms 的自洽长度）。
  ② **绝对带差里混着整体电平差**：把成品 RMS 从 −15.0 归一到 −16.1，
     十条带会**一起**掉 1.1 dB，看起来"频谱全歪了"，其实平衡一点没变。
     → 本工具同时给"绝对带差"与"相对带差（每带先减本曲 80-160Hz 带值）"，
       **判断音色平衡只看后者**，判断响度看 RMS 差。

用法：
    python scripts/band_grade.py <参考音频> <候选.wav> [候选2 ...]
"""
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import band_match as BM          # noqa: E402
import metrics                   # noqa: E402

TARGET_SR = 44100
PIVOT = '80-160'                 # 相对基准带（低中频，两条曲都稳）


def _resample(x, sr_from, sr_to):
    if sr_from == sr_to:
        return x
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(int(sr_from), int(sr_to))
    return resample_poly(x, int(sr_to // g), int(sr_from // g), axis=0)


def load44(path):
    """→ (多声道 float64 @44.1kHz, sr)"""
    x, sr = sf.read(path, dtype='float64', always_2d=True)
    return _resample(x, sr, TARGET_SR), TARGET_SR


def bands44(path):
    """→ (逐带绝对 dB 列表, 单声道, mid, 宽度, RMS dB)"""
    x, sr = load44(path)
    b, _f, _p = BM.band_energy_db(x, sr)
    mono = x.mean(axis=1)
    rms = 20 * np.log10(max(1e-9, np.sqrt((mono ** 2).mean())))
    return b, mono, x, rms


NAMES = ['%d-%d' % (BM.EDGES[i], BM.EDGES[i + 1]) for i in range(len(BM.EDGES) - 1)]


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    ref_path, cands = sys.argv[1], sys.argv[2:]
    br, _m, _x, rms_r = bands44(ref_path)
    piv_r = br[NAMES.index(PIVOT)]
    print('参考 %s  RMS %.2f dB' % (os.path.basename(ref_path), rms_r))
    print('  逐带 dB: ' + ' '.join('%s=%.1f' % (NAMES[i], br[i]) for i in range(len(NAMES))))

    for path in cands:
        if not os.path.exists(path):
            print('\n[缺] %s' % path)
            continue
        bc, mono, x, rms_c = bands44(path)
        piv_c = bc[NAMES.index(PIVOT)]
        # metrics 口径（固定 8192 窗；在 44.1k 下与上面窗长不同，作对照）
        key = '20-40'
        print('\n[%s]  RMS %.2f dB（比参考 %+.2f）· 宽度 %.3f · 质心 %d Hz'
              % (os.path.basename(path), rms_c, rms_c - rms_r,
                 metrics.width(x.astype('float32')),
                 metrics.centroid(mono, TARGET_SR)))
        d_abs, d_rel = [], []
        print('  带        我(dB)   绝对差   相对差')
        for i, k in enumerate(NAMES):
            da = bc[i] - br[i]
            dr = (bc[i] - piv_c) - (br[i] - piv_r)
            d_abs.append(abs(da))
            if k != PIVOT:
                d_rel.append(abs(dr))
            print('  %-9s %7.1f  %+7.2f  %+7.2f %s'
                  % (k, bc[i], da, dr, '<<<' if abs(dr) >= 2 else ''))
        print('  ★平均|绝对带差| %.2f dB（含整体电平）  平均|相对带差| %.2f dB（**音色平衡**）'
              % (np.mean(d_abs), np.mean(d_rel)))
        worst = max(range(len(NAMES)), key=lambda i: abs((bc[i] - piv_c) - (br[i] - piv_r)))
        print('  最差带 %s 相对 %+.2f dB'
              % (NAMES[worst], (bc[worst] - piv_c) - (br[worst] - piv_r)))
    return 0


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    sys.exit(main())
