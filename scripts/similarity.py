#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**统一判断标准**：`像不像 <参考曲>` —— 把"还原度"合成一个 0–100 的总分。

为什么需要（用户 2026-09-15 明确要求）：
  之前我逐个指标调参 —— 提亮就密度过头、降密又变暗、加鼓又压暗 600Hz，
  **按下葫芦浮起瓢**。根因是没有单一判据，只剩试错。
  听觉是整体的，所以这里把六个**可测**维度按"与原曲的距离"归一化后加权成一个数。

六个维度（权重按"对'像不像'的贡献"分配）：
  ① **和弦序列一致率**（30）—— 逐小节对比。和弦走向是"像不像"的第一层，
     而且**这一项能做到 100%**（照抄即可）。
  ② **节奏型格子一致率**（20）—— 16 格上 kick/snare/hat 的"敲不敲"是否一致。
     律动的骨架；也能照抄。
  ③ **密度曲线形状**（15）—— 每 8 小节起音序列的相关系数。
     段落起伏对不对；能照抄。
  ④ **倍频程轮廓距离**（15）—— 10 个频段的平均绝对差（dB）。
  ⑤ **质心 / 带宽 / RMS 相对差**（10）—— 整体音色与响度。
  ⑥ **段间变化度相似性**（10）—— 跨块方差之比（"有没有变化"）。

⚠ **诚实标注**：①②③ 属于"照抄就能一样"，做不到是方法问题；
  ④⑤⑥ 受**音源**限制（GeneralUser GS vs 商业音源），只能"接近"不能"一样"。
  所以总分天然有上限 —— 别拿音量/EQ 去追 ④⑤⑥，那是音源的差距。

用法:
  python scripts\\similarity.py <参考音频> <我的音频> [--json]
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()                      # noqa: E402
import metrics                                           # noqa: E402

BPM_DEF = 150.0
W = (('chord', 30), ('grid', 20), ('density', 15), ('octave', 15),
     ('timbre', 10), ('variation', 10))


def _chords(path, bpm, song_json=None):
    """逐小节和弦序列。

    ⚠ **有 `song.json` 就直接读它，不要重扒**（2026-09-15 修）：
    渲染成品重新扒和弦时，`m7↔7`、`sus4↔7` 这类三度性质分辨不出 ——
    实测一首**逐小节照抄原曲（抄写准确率 100%）**的曲子，重扒后只有 39% 一致。
    拿重扒的结果去评"和弦抄得准不准"是错的口径（工具第一版就这么写的，自己给自己扣了分）。
    """
    if song_json and os.path.exists(song_json):
        import json
        d = json.load(open(song_json, encoding='utf-8'))
        return [c for sec in (d.get('sections') or []) for c in (sec.get('chords') or [])]
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                     'analyze_chords.py'), path,
                        '--bpm', str(bpm)], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    seq = []
    for line in (r.stdout or '').splitlines():
        m = re.match(r'\s*(\d+)\s*\|\s*(\S+)\s*\|', line)
        if m:
            seq.append(m.group(2))
    return seq


def _env_grid(y, sr, lo, hi, grid_s):
    """逐格带内能量（取格内峰值）"""
    n = int(sr * 0.02)
    step = max(1, n // 2)
    hop_s = step / float(sr)
    out = []
    for i in range(0, len(y) - n, step):
        seg = y[i:i + n] * np.hanning(n)
        S = np.abs(np.fft.rfft(seg))
        f = np.fft.rfftfreq(n, 1.0 / sr)
        out.append(float((S[(f >= lo) & (f < hi)] ** 2).sum()))
    env = np.array(out)
    g = max(1, int(round(grid_s / hop_s)))
    agg = np.array([env[i:i + g].max() for i in range(0, max(1, len(env) - g), g)])
    ref = np.percentile(agg, 99) if len(agg) else 0.0
    return agg / max(1e-20, ref)


def _grid_bits(path, bpm):
    """鼓型：三轨各自 16 格的"敲/不敲"（bool）"""
    m, sr, x = metrics.load(path)
    y = x.mean(axis=1) if x.ndim > 1 else x
    grid_s = (60.0 / bpm) / 4.0
    bits = {}
    for nm, lo, hi in (('kick', 30, 120), ('snare', 2000, 6000), ('hat', 7000, 12000)):
        g = _env_grid(y, sr, lo, hi, grid_s)
        n16 = len(g) // 16
        if n16 < 2:
            bits[nm] = None
            continue
        avg = g[:n16 * 16].reshape(n16, 16).mean(axis=0)
        bits[nm] = avg > 0.22            # 阈值：实测原曲非敲击格 ≈0.12-0.2
    return bits


def _blocks(path, bpm):
    m, sr, x = metrics.load(path)
    mono = x.mean(axis=1) if x.ndim > 1 else x
    bar_s = 4 * 60.0 / bpm
    step = int(8 * bar_s * sr)
    rows = []
    for i in range(0, max(1, len(mono) - step // 2), step):
        seg = mono[i:i + step]
        if len(seg) < sr:
            continue
        pr = metrics.profile_of(seg, sr) if hasattr(metrics, 'profile_of') else None
        S, fr = metrics._avg_spec(seg, sr)
        p = S / max(1e-20, S.sum())
        cen = float((fr * p).sum())
        bw = float(np.sqrt((((fr - cen) ** 2) * p).sum()))
        hop = int(sr * 0.01)
        env = np.array([np.sqrt((seg[j:j + hop] ** 2).mean())
                        for j in range(0, max(1, len(seg) - hop), hop)])
        on = int((env[1:] / np.maximum(env[:-1], 1e-9) > 1.6).sum())
        rows.append((float(metrics.rms_db(seg)), cen, bw, on / (len(seg) / sr)))
    return np.array(rows)


def score(ref, mine, bpm=BPM_DEF, verbose=True, mine_json=None, ref_chords=None):
    """`ref_chords`：参考曲的**已知和弦序列**（有就免去重扒，如从 analyze_chords 存下来的）"""
    s = {}

    # ① 和弦
    a = list(ref_chords) if ref_chords else _chords(ref, bpm)
    b = _chords(mine, bpm, song_json=mine_json)
    n = min(len(a), len(b))
    s['chord'] = (100.0 * sum(1 for i in range(n) if a[i] == b[i]) / n) if n else 0.0
    # ② 节奏型
    ga, gb = _grid_bits(ref, bpm), _grid_bits(mine, bpm)
    hit = tot = 0
    for k in ('kick', 'snare', 'hat'):
        if ga.get(k) is None or gb.get(k) is None:
            continue
        hit += int((ga[k] == gb[k]).sum())
        tot += len(ga[k])
    s['grid'] = (100.0 * hit / tot) if tot else 0.0
    # ③/⑥ 块指标
    A, B = _blocks(ref, bpm), _blocks(mine, bpm)
    m = min(len(A), len(B))
    if m >= 3:
        da = A[:m, 3]
        db = B[:m, 3]
        cor = float(np.corrcoef(da, db)[0, 1]) if da.std() > 0 and db.std() > 0 else 0.0
        s['density'] = max(0.0, min(100.0, (cor + 1) / 2 * 100))
        va = float(np.std(np.diff(A[:m], axis=0), axis=0).mean())
        vb = float(np.std(np.diff(B[:m], axis=0), axis=0).mean())
        s['variation'] = 100.0 * min(va, vb) / max(1e-9, max(va, vb))
        # ④ 倍频程（用整曲画像）
        pa, pb = metrics.profile(ref), metrics.profile(mine)
        ba = np.array([pa['bands'][k] for k in pa['bands']])
        bb = np.array([pb['bands'][k] for k in pb['bands']])
        d = float(np.abs(ba - bb).mean())
        s['octave'] = max(0.0, 100.0 - d / 3.0 * 100)      # 平均差 3dB → 0 分
        # ⑤ 音色
        rel = (abs(pb['centroid'] - pa['centroid']) / max(1.0, pa['centroid'])
               + abs(pb['width'] - pa['width']) / max(0.05, pa['width'])
               + abs(pb['rms_db'] - pa['rms_db']) / 5.0) / 3.0
        s['timbre'] = max(0.0, 100.0 * (1 - rel))
    else:
        s.update({'density': 0, 'variation': 0, 'octave': 0, 'timbre': 0})

    total = sum(s[k] * w for k, w in W) / sum(w for _k, w in W)
    if verbose:
        print('=== 像不像参考曲：**总分 %.1f / 100**' % total)
        for k, w in W:
            tag = '照抄即可' if k in ('chord', 'grid', 'density') else '受音源限制'
            print('   %-10s %6.1f  (权重 %2d%%，%s)' % (k, s[k], w, tag))
        worst = sorted(s.items(), key=lambda kv: kv[1])[:3]
        print('   差距最大的三项：' + ' · '.join('%s %.1f' % kv for kv in worst))
    return total, s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ref')
    ap.add_argument('mine')
    ap.add_argument('--bpm', type=float, default=BPM_DEF)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--mine-json', default=None,
                    help='我那首的 song.json —— 直接读它抄进去的和弦，避免重扒的分辨极限')
    ap.add_argument('--ref-chords', default=None,
                    help='参考曲的已知和弦序列文件（analyze_chords 的输出，免去重扒）')
    a = ap.parse_args()
    rc = None
    if a.ref_chords and os.path.exists(a.ref_chords):
        rc = []
        for line in open(a.ref_chords, encoding='utf-8'):
            m = re.match(r'\s*(\d+)\s*\|\s*(\S+)\s*\|', line)
            if m:
                rc.append(m.group(2))
    t, s = score(a.ref, a.mine, a.bpm, verbose=not a.json,
                 mine_json=a.mine_json, ref_chords=rc)
    if a.json:
        import json
        print(json.dumps({'total': round(t, 1), **{k: round(v, 1) for k, v in s.items()}},
                         ensure_ascii=False))


if __name__ == '__main__':
    main()
