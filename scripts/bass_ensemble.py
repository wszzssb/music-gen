# -*- coding: utf-8 -*-
"""低音专项：把整曲里某条轨换成"分轨多来源交叉验证"的版本（+ 可选低八度 sub 层）。

为什么需要单独立一个工具（BGM29 实测，见 docs/CASE-BGM35-FINDINGS.md 第 37 条）：
  · YourMT3 在**全混音**上只给出 279 个低音（均音高 42.7 ≈ 93Hz）；
  · 同一个模型在 **demucs 的 bass 分轨**上给出 818 个（均 39.0）；
    Basic Pitch 在两条 bass 分轨上给出 1307 / 1384 个（均 ~37 ≈ 74Hz）。
  → 低音是"面"不是"点"，全混音里 279 个音根本铺不满：成品 20-80Hz 比参考低 6~8 dB，
    而 160-315Hz 反而高 4.5 dB。换成集成后的低音轨（1416 音）+ 低八度 sub 层，
    统一口径平均相对带差从 **2.16 → 0.95 dB**。

规则（沿用 ensemble_transcribe 的实测教训）：
  · 同 0.1s 格 + 同音高合并，按跨来源支持率软评分，score ≥ thr 才留；
  · **不做碎片合并**（那些是真实重复起音，合并会让 F1 从 0.481 掉到 0.283）；
  · 时值取各来源**最大值**（低音要连奏，短促会丢掉低频能量），
    截断到"同音高下一音之前"与 --max-dur，避免同音高重叠（重叠会被音源吞音）。

用法：
    python scripts/bass_ensemble.py --base 全曲.mid --out 输出.mid \
        --source "ymt3b=bass分轨转录.mid|24|60" \
        --source "bp4=bp_bass4.mid|24|60" --source "bp6=bp_bass6.mid|24|60" \
        --layer Bass [--program 38] [--sub]

⚠ 字段分隔符用 `|`（Windows 路径自带 `D:`，用 `:` 会把盘符切断 —— 见 PITFALLS 170）。
"""
import argparse
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import midi_file  # noqa: E402

GRID = 0.1


def load_notes(path, lo, hi):
    """→ [(秒, 音高, 时值秒, 力度)]，跳过第 10 通道（鼓）"""
    m = midi_file.import_midi(path)
    spb = 60.0 / float(m.get('bpm') or 120.0)
    out = []
    for tr in m.get('tracks', []):
        if tr.get('drum') or tr.get('channel') == 9:
            continue
        for (st, du, p, v) in tr.get('notes', []):
            if lo <= p <= hi:
                out.append((float(st) * spb, int(p), float(du) * spb, int(v)))
    out.sort()
    return out


def parse_source(spec):
    name, rest = spec.split('=', 1)
    parts = rest.split('|')
    lo = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    hi = int(parts[2]) if len(parts) > 2 and parts[2] else 127
    return name, parts[0], lo, hi


def main():
    ap = argparse.ArgumentParser(description='低音专项：分轨多来源交叉验证 + sub 层')
    ap.add_argument('--base', required=True, help='基准整曲 MIDI（保留其所有轨）')
    ap.add_argument('--out', required=True)
    ap.add_argument('--source', action='append', required=True,
                    help='名字=文件|音域低|音域高，可重复')
    ap.add_argument('--layer', default='Bass', help='被替换的轨名（默认 Bass）')
    ap.add_argument('--thr', type=float, default=0.90, help='跨来源支持率阈值')
    ap.add_argument('--max-dur', type=float, default=1.6, help='单音最长时值（秒）')
    ap.add_argument('--program', type=int, default=None, help='替换后该轨的 GM 音色号')
    ap.add_argument('--sub', action='store_true', help='额外生成低八度 sub 层（补 20-40Hz）')
    a = ap.parse_args()

    srcs = {}
    for spec in a.source:
        name, path, lo, hi = parse_source(spec)
        if not os.path.isfile(path):
            print('  跳过（文件不存在）：%s' % path)
            continue
        srcs[name] = load_notes(path, lo, hi)
        print('  来源 %-10s %5d 音（音域 %d-%d）' % (name, len(srcs[name]), lo, hi))
    if not srcs:
        raise SystemExit('没有可用来源')

    # 跨来源支持率（无参照也能算）：某来源的音在 ±1 格内被别的来源支持的比例
    sets = {}
    for name, ns in srcs.items():
        s = set()
        for (t, p, _d, _v) in ns:
            g = int(round(t / GRID))
            for dg in (-1, 0, 1):
                s.add((g + dg, p))
        sets[name] = s
    w = {}
    for name in srcs:
        others = set()
        for n2 in srcs:
            if n2 != name:
                others |= sets[n2]
        w[name] = len(sets[name] & others) / max(1, len(sets[name]))
    print('  跨来源支持率：%s'
          % {k: round(v, 3) for k, v in sorted(w.items(), key=lambda z: -z[1])})

    merged = {}
    for name, ns in srcs.items():
        for (t, p, d, v) in ns:
            k = (int(round(t / GRID)), p)
            rec = merged.get(k)
            if rec is None:
                merged[k] = [(t, p, d, v), {name}]
            else:
                rec[1].add(name)
                best = rec[0]
                rec[0] = (best[0], best[1], max(d, best[2]), max(v, best[3]))
    print('  合并后 %d 个 (格,音高)' % len(merged))

    by_pitch = defaultdict(list)
    kept = 0
    for (note, s) in merged.values():
        if sum(w.get(n, 0.0) for n in s) < a.thr:
            continue
        t, p, d, v = note
        by_pitch[p].append([t, min(d, a.max_dur), v])
        kept += 1
    print('  阈值 %.2f → 保留 %d 音' % (a.thr, kept))
    for p, lst in by_pitch.items():                 # 同音高截断，避免重叠
        lst.sort()
        out = []
        for j, cur in enumerate(lst):
            nxt = lst[j + 1][0] if j + 1 < len(lst) else None
            if nxt is not None:
                gap = nxt - cur[0]
                # ⚠ 间隔 < 20ms 的同音高重复：**直接丢掉前一个**。
                #   原来是 `d = max(0.05, gap - 0.01)` —— gap < 60ms 时下限 0.05 反而
                #   **大于** gap，于是照样重叠（实测全曲 Bass 37 处、Sub 27 处）。
                #   同音高重叠会被音源吞音（note-off 只带音高不带 id）→ 听感"这个音没响"。
                if gap < 0.02:
                    continue
                if cur[1] > gap - 0.005:
                    cur[1] = max(0.01, gap - 0.005)
            out.append(cur)
        lst[:] = out

    base = midi_file.import_midi(a.base)
    spb = 60.0 / float(base.get('bpm') or 120.0)
    hit = False
    for tr in base['tracks']:
        if tr.get('name') == a.layer:
            tr['notes'] = sorted([round(t / spb, 6), round(d / spb, 6), int(p), int(v)]
                                 for (p, lst) in by_pitch.items() for (t, d, v) in lst)
            if a.program is not None:
                # ⚠ 只改 `program` 不够：`midi_file.export_midi` 在**有 program_changes 时
                # 优先用它、忽略 program**（实测：只改 program，渲染结果与原来逐位相同
                # —— SHA256 一模一样才发现）。两条都写。
                tr['program'] = int(a.program)
                tr['program_changes'] = [[0.0, int(a.program)]]
            hit = True
            print('  替换轨 %s → %d 音%s' % (a.layer, len(tr['notes']),
                                             '' if a.program is None else '（音色→%d）' % a.program))
    if not hit:
        raise SystemExit('基准 MIDI 里没有名为 %s 的轨：%s'
                         % (a.layer, [t.get('name') for t in base['tracks']]))

    if a.sub:
        subs = []
        for (p, lst) in by_pitch.items():
            # 低八度落在 24~45（C1~A2 = 32.7~110Hz）才要：
            # 再低掉到 20Hz 以下（听不见、只吃动态余量），再高就与主层打架。
            if not (24 <= p - 12 <= 45):
                continue
            for (t, d, v) in lst:
                subs.append([round(t / spb, 6), round(d / spb, 6), p - 12, max(30, int(v * 0.8))])
        subs.sort()
        base['tracks'].append({'index': len(base['tracks']), 'name': 'Sub', 'channel': 8,
                               'program': 38, 'drum': False, 'mute': False, 'solo': False,
                               'hidden': False, 'notes': subs, 'ccs': [],
                               'program_changes': [[0.0, 38]], 'markers': []})
        print('  sub 层：%d 音（低八度，Synth Bass 1）' % len(subs))

    base['end_beat'] = max([n[0] + n[1] for t in base['tracks'] for n in t['notes']] or [0.0])
    midi_file.export_midi(base, a.out)
    print('  写 %s（%d 字节）' % (a.out, os.path.getsize(a.out)))


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    main()
