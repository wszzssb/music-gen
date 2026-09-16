#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**按段对齐（密度层）**：从参考 MIDI / 参考曲量出"每 8 小节的音符数曲线"，
并把它映射成引擎的 `arr.density` 档（0–4）。

为什么需要它（与 `arr.density` 的分工）：
  · 段间**响度**起伏已有链路：参考曲画像 `structure` → `theme_pack` 的 `energy_curve_db`
    → `new_song.energy_mix` 写每段 `arr.mix`（整段抬/压 CC7）。
  · 但"像不像"更主要来自**音符密度**的起承转合 —— 用户对还原曲的原话是
    **"乐器有点乱，没有像原曲一样很好控制"**，而实测原曲的逐段密度
    （BGM35：块 26 只有 0.4 起音/秒、块 22 有 42.4）**在 66 倍范围内反复**，
    我们的编配若在段间一条平线，听感就是"一直很满、没有呼吸"。
  · `arr.density` 是引擎**已有**的段级密度开关（影响贝斯音型 / 吉他落点 /
    钢琴反音数 / 琶音间隔），`selftest` 的 `t_density_dynamic_range` 要求 ≥4 倍起伏。
    本模块负责**给它提供目标曲线**，让"该疏则疏"来自参考曲而不是拍脑袋。

口径：一小节的音符数 = 该小节内**所有轨**的 note-on 数（与 `b35_seccmp.py` 同口径）。
段长默认 8 小节（与 `metrics.structure` 的 group 一致）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()          # noqa: E402  控制台编码兜底

GROUP = 8            # 每段小节数（与 metrics.structure 的 group 对齐）
LEVELS = ('很疏', '疏', '中', '密', '很密')


def midi_per_bar(path):
    """MIDI → 每小节的音符数（`[n_bar0, n_bar1, ...]`）"""
    import midi_probe as mp
    r = mp.parse_full(path)          # ⚠ 它没有 quiet 参数，只有 parse(path, quiet)
    tpb = float(r.get('ticks_per_beat') or 480)
    ts = r.get('timesig') or (4, 4)
    bar_ticks = tpb * 4.0 * ts[0] / ts[1]
    counts = {}
    for tr in r.get('tracks') or []:
        for (start, _dur, _pitch, _vel) in tr.get('notes') or []:
            b = int(start // bar_ticks)
            counts[b] = counts.get(b, 0) + 1
    nbar = (max(counts) + 1) if counts else 0
    return [counts.get(i, 0) for i in range(nbar)]


def section_curve(per_bar, group=GROUP):
    """每小节音符数 → 每段（group 小节）的**每小节平均音符数**"""
    out = []
    for i in range(0, max(0, len(per_bar) - group + 1), group):
        seg = per_bar[i:i + group]
        out.append(round(sum(seg) / float(len(seg)), 2))
    return out


def norm_to(curve, n):
    """把任意长度的曲线**线性重采样**到 n 段（模板长度不一，不能硬凑）"""
    if not curve:
        return [0.0] * n
    if len(curve) == n:
        return list(curve)
    out = []
    for i in range(n):
        pos = (i / float(n - 1)) * (len(curve) - 1) if n > 1 else 0.0
        lo = int(pos)
        hi = min(lo + 1, len(curve) - 1)
        f = pos - lo
        out.append(curve[lo] * (1 - f) + curve[hi] * f)
    return [round(v, 2) for v in out]


def rel_curve(curve):
    """绝对曲线 → **相对中位数**的 dB 偏差（与 `energy_curve_db` 同口径，可存包）

    为什么要转相对：同主题的模板规模与我们自己的编配规模差很多
    （实测 cheerful 模板中位 **79 音/小节**，而我们的曲子整轨也就 40~60）——
    绝对音符数直接写进 `arr.density` 台阶会全部顶到 4 档。
    相对曲线只保留"**哪一段相对疏、哪一段相对密**"，这才是要跟参考曲对齐的东西。
    """
    vals = [v for v in curve if v > 0]
    if not vals:
        return []
    med = sorted(vals)[len(vals) // 2]
    import math
    return [round(20 * math.log10(max(1e-6, v / med)), 2) for v in curve]


def curve_from_midis(files, nsec, group=GROUP, root=None):
    """多份 MIDI → 逐段密度曲线（**中位数**，抗单首怪结构）

    返回 `(curve, meta)`；curve 为空表示模板不足（别硬造）。
    """
    import collections
    per = collections.defaultdict(list)
    used = []
    for f in files:
        p = f if os.path.isabs(f) else os.path.join(root or '.', f)
        if not os.path.exists(p):
            continue
        try:
            pb = midi_per_bar(p)
        except Exception:
            continue
        c = section_curve(pb, group)
        if len(c) < 3:
            continue
        per['all'].append(norm_to(c, nsec))
        used.append(os.path.basename(p))
    if len(per['all']) < 2:
        return [], {'n': len(used), 'note': '模板少于 2 份，不给密度曲线'}
    med = []
    for i in range(nsec):
        vals = sorted(c[i] for c in per['all'])
        med.append(round(vals[len(vals) // 2], 2))
    return med, {'n': len(used), 'files': used}


def to_levels(curve, span=None, verbose=False):
    """密度曲线（每小节音符数）→ `arr.density` 档 0–4。

    两条原则（都是实测踩出来的）：

    ① **档位跨度由实测起伏决定，不能一律拉到 0–4**
       同主题 8~9 份模板聚合后，段间密度起伏只有 **1.3~2.5 倍**
       （`night` 2.5 倍、`battle` 1.3 倍）。若强行铺满 0–4 档，等于把
       "参考曲本来就很平"编造成"大起大落" —— 听感就是我们自己造的，
       而对齐的全部意义就是**让疏密走势来自参考曲**。
       → `K = clip(round(2*log2(起伏倍数)), 1, 4)`，档位只占 `0..K`。

    ② **档位必须随音数单调**（保序回归）
       按分位阈值直接判会出非单调：曲线 [37, 74.9, 77.9, 72.8] 里 77.9 判 2 档
       而 72.8 判 **0 档**，听感"忽然空一下又满起来"，比不做还糟。

    对数尺度映射：**中位数落在 2 档**，`k = round(2 + 2*log2(v/med))`
    —— 音数翻倍正好升 2 档。⚠ 别让中位数落在 0 档：`arr.density=0` 是
    "每小节只留 1 个音"的**近乎独奏**档，整首偏 0 档听起来像"一直很空"，
    而不是"跟着参考曲的疏密走"。
    """
    import math
    if not curve:
        return []
    pos = [v for v in curve if v > 0]
    if not pos:
        return [0] * len(curve)
    s = sorted(pos)
    med = s[len(s) // 2]
    hi = s[min(len(s) - 1, int(len(s) * 0.9))]
    lo = s[max(0, int(len(s) * 0.1))]
    ratio = hi / max(1e-6, lo)
    udev = 2 * math.log(max(1.0, ratio)) / math.log(2)      # 起伏折成档位偏移
    if span is not None:
        udev = float(span)
    out = []
    for v in curve:
        if v <= 0:
            out.append(0)
            continue
        rel = math.log(max(1e-6, v / med)) / math.log(2.0)  # 相对中位数的倍频数
        k = int(round(2.0 + 2.0 * rel))
        out.append(max(0, min(4, k)))
    # 保序：音数多的段落档位不得更低
    order = sorted(range(len(curve)), key=lambda i: (curve[i], i))
    for a in range(1, len(order)):
        i, j = order[a - 1], order[a]
        if out[j] < out[i]:
            out[j] = out[i]
    if verbose:
        print('   起伏 %.2f 倍（P10 %.1f ~ P90 %.1f 音/小节，中位 %.1f）→ 档位偏移 ±%.1f'
              % (ratio, lo, hi, med, udev))
    return out


def main():
    import argparse
    import json
    ap = argparse.ArgumentParser(description='逐段密度曲线 / arr.density 档')
    ap.add_argument('midi', nargs='+', help='一个或多个 MIDI（多份取中位数）')
    ap.add_argument('--nsec', type=int, default=10, help='目标段数')
    ap.add_argument('--group', type=int, default=GROUP)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    curve, meta = curve_from_midis(a.midi, a.nsec, a.group)
    if not curve:
        print('取不到曲线：%s' % meta.get('note'))
        return
    lv = to_levels(curve)
    if a.json:
        print(json.dumps({'curve': curve, 'levels': lv, 'meta': meta},
                         ensure_ascii=False))
        return
    print('模板 %d 份 · 段长 %d 小节' % (meta['n'], a.group))
    print('%-6s %8s %6s  %s' % ('段', '音/小节', '档', '形态'))
    for i, (c, k) in enumerate(zip(curve, lv)):
        print('S%-5d %8.2f %6d  %s' % (i + 1, c, k, '#' * int(c)))
    print('\n档位分布：%s' % {k: lv.count(k) for k in range(5)})
    print('起伏：%.2f ~ %.2f 音/小节（**%.1f 倍**）'
          % (min(curve), max(curve), max(curve) / max(0.01, min(curve))))


if __name__ == '__main__':
    main()
