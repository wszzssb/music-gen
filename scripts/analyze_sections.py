#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""analyze_sections.py —— **分段**剖析参考曲，为"分段拟合"提供每段的客观目标

为什么需要它（实测教训）：`profile_ref.py` 把整首歌平均成**一个**画像，于是
「一首歌里引子/A段/副歌/尾奏各有各的亮度与密度」这件事被抹平了。实测
`ひとりごはん` 参考曲：第 3 段（副歌）5–10kHz 是 −44.5dB，第 5 段（尾）−56.1dB
—— **同一首歌里差 11.6dB**；拿全曲平均（−47.3）去拟合，副歌必然偏暗、尾奏必然偏亮，
听感就是"整体发闷、没有起伏、不像"。

本工具把参考曲按时间分段（默认自动找段落边界，也可 `--bars N` 手动切），每段给：
  ① 倍频程平衡（对"清脆度"最关键的是 2500-5000 / 5000-10000 两段）
  ② 质心 / 响度 / 宽度 / 起音密度
  ③ 速度层级与该段的自相关支持度（段落之间可能不同层）
  ④ 与全曲均值的**偏离**（→ 直接告诉我们"哪段该亮、哪段该暗"）
并按"该段做什么"给一句可执行建议（编配/音色/打击开关）。

用法:
  python scripts\\analyze_sections.py <参考曲> [名字] [--bars N] [--json]
  # 自动分段（默认 5 段，也可 --segments N）
  python scripts\\analyze_sections.py "<参考曲目录>/(17) … .flac" hitorigohan2
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()

import numpy as np                            # noqa: E402
import metrics                                # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _seg_spec(m, sr):
    spec, freqs = metrics._avg_spec(m, sr)
    return metrics.octave_bands(m, sr, spec, freqs)


def _boundaries(m, sr, nseg, smooth=2.0):
    """按响度突变切段（粗糙但够用）：把整曲的短时 RMS 曲线分成 nseg 段、
    让"段内响度方差和"最小 —— 即经典的 1 维 k-means 式切分。"""
    hop = max(1, int(sr * 0.25))
    env = np.array([np.sqrt((m[i:i + hop] ** 2).mean())
                    for i in range(0, len(m) - hop, hop)])
    n = len(env)
    if n < nseg * 4:
        return [int(i * n / nseg) for i in range(nseg + 1)]
    # 一维动态规划：最小化段内方差
    csum = np.concatenate([[0.0], np.cumsum(env)])
    csq = np.concatenate([[0.0], np.cumsum(env ** 2)])

    def cost(a, b):
        k = b - a
        if k <= 1:
            return 0.0
        s = csum[b] - csum[a]
        return float(csq[b] - csq[a] - s * s / k)

    INF = float('inf')
    dp = [[INF] * (n + 1) for _ in range(nseg + 1)]
    back = [[0] * (n + 1) for _ in range(nseg + 1)]
    dp[0][0] = 0.0
    for s in range(1, nseg + 1):
        for b in range(s, n + 1):
            for a in range(s - 1, b):
                if dp[s - 1][a] == INF:
                    continue
                c = dp[s - 1][a] + cost(a, b)
                if c < dp[s][b]:
                    dp[s][b], back[s][b] = c, a
    cuts, b = [], n
    for s in range(nseg, 0, -1):
        a = back[s][b]
        cuts.append((a, b))
        b = a
    cuts.reverse()
    return [0] + [int(round(c[1] * hop)) for c in cuts] + [len(m)]


def analyze(path, name=None, nseg=5, bars=None, bpm=None):
    m, sr, x = metrics.load(path)
    dur = len(m) / sr
    if bars and bpm:
        step = int(round(bars * 4 * 60.0 / bpm * sr))
    else:
        step = None
    if step:
        bounds = list(range(0, len(m), step)) + [len(m)]
    else:
        bounds = _boundaries(m, sr, nseg)

    whole = _seg_spec(m, sr)
    wbpm, _s, winfo = metrics.detect_bpm(m, sr)
    out = {'name': name or os.path.basename(path).rsplit('.', 1)[0],
           'file': path, 'duration': round(dur, 1),
           'whole_bpm': wbpm, 'whole_bands': whole,
           'whole_density': winfo.get('onset_density'),
           'segments': []}
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        if b - a < sr:                      # 太短的段跳过
            continue
        s = m[a:b]
        seg = {'i': i + 1, 'start': round(a / sr, 1), 'end': round(b / sr, 1),
               'dur': round((b - a) / sr, 1),
               'rms_db': round(20 * float(np.log10(max(float(np.sqrt((s ** 2).mean())), 1e-9))), 1),
               'bands': _seg_spec(s, sr)}
        seg['centroid'] = int(metrics.centroid(s, sr, *metrics._avg_spec(s, sr)))
        if x.shape[1] > 1:
            seg['width'] = round(float(metrics.width(x[a:b])), 3)
        bpm_s, _p, info_s = metrics.detect_bpm(s, sr)
        seg['bpm'] = bpm_s
        seg['density'] = info_s.get('onset_density')
        seg['level_note'] = info_s.get('level_note')
        # 与全曲均值的偏离（正 = 该段比全曲亮/响）
        seg['dev'] = {k: round(seg['bands'][k] - whole[k], 1) for k in whole}
        hi = seg['bands'].get('5000-10000', -99)
        mid = seg['bands'].get('2500-5000', -99)
        if hi - whole.get('5000-10000', -99) > 2.5:
            seg['hint'] = '该段**偏亮**（清脆段）：可以开 glock/perc、提 2500-10k 轨的 mix'
        elif hi - whole.get('5000-10000', -99) < -2.5:
            seg['hint'] = '该段**偏暗**（收尾/安静段）：关 glock、perc 调低、弦乐/pad 收'
        elif seg['rms_db'] - (whole.get('80-160', -99)) > 0:
            seg['hint'] = '该段中等偏响：保持编制，靠 vel 做起伏'
        else:
            seg['hint'] = '该段接近全曲均值'
        out['segments'].append(seg)
    return out


def _fmt(res):
    w = res['whole_bands']
    print('== %s  全曲 %.1fs  速度 %.1f BPM  起音密度 %s' % (
        res['name'], res['duration'], res['whole_bpm'], res['whole_density']))
    print('   全曲倍频程 ' + ' '.join('%s %.1f' % (k, w[k]) for k in
                                  ('2500-5000', '5000-10000', '10000-18000')))
    print()
    print('  %-4s %-14s %6s %6s %7s %8s %9s %9s  %s' % (
        '段', '时间', '时长', 'RMS', '质心', '2500-5k', '5000-10k', '10-18k', '建议'))
    for s in res['segments']:
        b = s['bands']
        print('  %-4d %5.1f-%5.1fs %5.1fs %6.1f %6d %8.1f %9.1f %9.1f  %s' % (
            s['i'], s['start'], s['end'], s['dur'], s['rms_db'], s['centroid'],
            b['2500-5000'], b['5000-10000'], b['10000-18000'], s['hint']))
    print()
    print('  与全曲均值的偏离（+ = 该段比全曲亮）：')
    for s in res['segments']:
        print('    第%d段  2500-5k %+5.1f  5000-10k %+5.1f  10-18k %+5.1f   RMS %+5.1f'
              % (s['i'], s['dev']['2500-5000'], s['dev']['5000-10000'],
                 s['dev']['10000-18000'], s['rms_db'] - res['whole_bands']['315-630']))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        return 1
    nseg, bars, bpm = 5, None, None
    for flag, cast in (('--segments', int), ('--bars', int), ('--bpm', float)):
        if flag in sys.argv:
            v = cast(sys.argv[sys.argv.index(flag) + 1])
            if flag == '--segments':
                nseg = v
            elif flag == '--bars':
                bars = v
            else:
                bpm = v
    res = analyze(args[0], args[1] if len(args) > 1 else None, nseg, bars, bpm)
    if '--json' in sys.argv:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        _fmt(res)
    # 顺手落盘：分段目标给 new_song --from-sections 用。
    # **必须放子目录**：`refs/*.json` 是"参考曲画像"、自检会按那套字段校验，
    # 混进去会报"缺字段"（踩过，与 refs/melody/ 同一个原因）。
    nm = res['name'] + '_sections'
    os.makedirs(os.path.join(ROOT, 'refs', 'sections'), exist_ok=True)
    p = os.path.join(ROOT, 'refs', 'sections', nm + '.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print('\n分段目标已写入 refs/sections/%s.json' % nm)
    return 0


if __name__ == '__main__':
    sys.exit(main())
