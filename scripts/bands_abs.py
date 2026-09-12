#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bands_abs.py —— **绝对口径**的倍频程 + 连续性（占用率）对照。

为什么需要它（实测踩过，见 PITFALLS 60）：成绩单里的"倍频程"是
`metrics.octave_bands` 那个口径 —— **相对本曲最响频段 = 0dB**。低频一厚，
10 个数字整体平移，屏幕上看哪一档都只差 1~3dB，"看着挺像"；
实际 5-18k 差 10~18dB、40-160Hz 厚 4~6dB。21_g150_velvet 在这个口径上连改 5 版编配
"数字几乎不动"，换成绝对值才定位到真问题（音色太暗 + 低频太厚）。

第二个原口径没有的指标：**占用率**（帧能量 > 本带峰值-20dB 的帧占比）。
"高频是连续的墙还是稀疏的点"只有它能看出来 —— 参考曲 10-18k 占用 88%，
我第一版只有 25%，而两边的**频段电平**却几乎一样。

用法:
  python scripts\\bands_abs.py <我的文件> <参考音频 | refs 里的画像名 | 曲目目录> [--win 40-60] [--json]
  # 例: python scripts\\bands_abs.py songs\\21_g150_velvet\\bgm16c_v2_sf.wav BGM16c_v2
  #     python scripts\\bands_abs.py a.wav b.wav --win 6-20      # 只看安静段
产出: 一屏表（绝对 dB / 相对 dB / 差 / 占用率）；--json 给下游脚本。
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import metrics          # noqa: E402  # 唯一口径来源（平均谱/倍频程/宽度/质心）
import cli_utf8 as _cu  # noqa: E402

BANDS = [(20, 40), (40, 80), (80, 160), (160, 315), (315, 630), (630, 1250),
         (1250, 2500), (2500, 5000), (5000, 10000), (10000, 18000)]
OCC_FLOOR_DB = 20.0        # 占用率判据：帧能量 > 本带峰值 - 20dB
OCC_N, OCC_STEP = 4096, 1024


def band_power(S, f, lo, hi):
    """**平均谱**在带内的总功率（与 metrics.octave_bands 同一口径，只是不归一）。
    绝对电平 = 10log10(这里) → 例如参考曲 80-160Hz ≈ +44.8。"""
    k = (f >= lo) & (f < hi)
    return float((S[k] ** 2).sum())


def resolve(arg):
    """参数 → 音频路径：① 直接是音频文件 ② refs/<名>.json 里的 file ③ 曲目目录的 render.json"""
    if os.path.isfile(arg) and arg.lower().endswith(('.wav', '.ogg', '.flac', '.mp3')):
        return arg
    ref = os.path.join(ROOT, 'refs', '%s.json' % arg)
    if os.path.isfile(ref):
        d = json.load(open(ref, encoding='utf-8'))
        if d.get('file') and os.path.isfile(d['file']):
            return d['file']
        raise SystemExit('画像 %s 里的 file 不存在: %s' % (ref, d.get('file')))
    rj = os.path.join(arg, 'render.json')
    if os.path.isfile(rj):
        d = json.load(open(rj, encoding='utf-8'))
        wav = os.path.join(arg, d.get('out', '') + '.wav')
        if os.path.isfile(wav):
            return wav
    raise SystemExit('不认识这个参数: %s（要音频文件 / refs 画像名 / 曲目目录）' % arg)


def table(path, win=None):
    """一个文件的绝对口径画像：绝对 dB / 相对 dB / 占用率 + rms/宽度/质心"""
    m, sr, x = metrics.load(path)
    if win:
        a, b = int(win[0] * sr), int(win[1] * sr)
        m, x = m[a:b], x[a:b]
    S, f = metrics._avg_spec(m, sr)
    power = [band_power(S, f, lo, hi) for lo, hi in BANDS]
    pk = max(max(power), 1e-20)
    out = {'file': path, 'sec': round(len(m) / sr, 2),
           'rms': metrics.rms_db(m), 'width': metrics.width(x),
           'centroid': metrics.centroid(m, sr, S, f), 'abs': {}, 'rel': {}, 'occ': {}}
    for (lo, hi), p in zip(BANDS, power):
        key = '%d-%d' % (lo, hi)
        out['abs'][key] = round(10 * np.log10(max(p, 1e-20)), 1)
        out['rel'][key] = round(10 * np.log10(max(p, 1e-20) / pk), 1)
        env = metrics._band_env(m, sr, OCC_N, OCC_STEP, float(lo), float(hi))
        out['occ'][key] = metrics.occupancy(env, OCC_FLOOR_DB)
    return out


def compare(mine, ref, win=None):
    a, b = table(resolve(mine), win), table(resolve(ref), win)
    keys = ['%d-%d' % k for k in BANDS]
    return {'ref': b, 'mine': a,
            'diff_abs': {k: round(a['abs'][k] - b['abs'][k], 1) for k in keys},
            'diff_rel': {k: round(a['rel'][k] - b['rel'][k], 1) for k in keys},
            'diff_occ': {k: round(a['occ'][k] - b['occ'][k], 1) for k in keys}}


def report(res):
    ref, mine = res['ref'], res['mine']
    print('== 绝对口径对照（平均谱的带内总功率，dB；相对列 = 各自最响带归一）')
    print('   本曲 %s' % mine['file'])
    print('   参考 %s' % ref['file'])
    print('%-11s %10s %10s %8s | %8s %8s | %8s %8s' %
          ('频带', '参考ABS', '本曲ABS', '差', '参考REL', '本曲REL', '参考占用', '本曲占用'))
    for k in res['diff_abs']:
        print('%-11s %10.1f %10.1f %+8.1f | %8.1f %8.1f | %7.0f%% %7.0f%%'
              % (k, ref['abs'][k], mine['abs'][k], res['diff_abs'][k],
                 ref['rel'][k], mine['rel'][k], ref['occ'][k], mine['occ'][k]))
    print('RMS %6.1f / %6.1f dB   宽度 %.3f / %.3f   质心 %5.0f / %5.0f Hz'
          % (mine['rms'], ref['rms'], mine['width'], ref['width'],
             mine['centroid'], ref['centroid']))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mine')
    ap.add_argument('ref')
    ap.add_argument('--win', default=None, help='只分析 a-b 秒（如 6-20）')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    win = None
    if a.win:
        lo, hi = a.win.split('-')
        win = (float(lo), float(hi))
    res = compare(a.mine, a.ref, win)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        report(res)
    bad = [k for k, v in res['diff_abs'].items() if abs(v) > 6.0]
    if bad:
        print('  提示：绝对差 >6dB 的频带 %s —— **先改音色/音量，不要硬推 EQ**（EQ 上限很低）'
              % ', '.join(bad))


_cu.setup()          # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    main()
