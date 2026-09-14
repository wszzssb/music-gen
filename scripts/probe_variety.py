#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_variety.py —— **多样性体检**：量用户听感"好多部分都是一样的"到底在哪一层。

和 `probe_melody_lang`（只量跨曲语言重合）分工不同：那个只回答"歌与歌像不像"；
用户说的是"**好多部分**"——曲内段落之间、跨曲之间、以及主题之间是不是有区分力，
是三个不同的问题，必须分开量、还要有**基线**才判得了。

三层 + 基线：
  ① 曲内：每首歌里不同旋律线（A/B/C…）两两的语言重合度 + 段落级节奏签名复用率
  ② 跨曲：全库两两语言重合度（落点/时值/音程/句长/拱形 的平均交叠率）
  ③ 主题：同主题曲 vs 跨主题曲的重合度之差（**区分力** = 组内 − 组间）
  ④ 基线：`refs/midi2/` 真实模板库按目录（=风格）做同一件事 —— 真实音乐里
     "同风格"到底该像到什么程度，只有它说了算；我的主题差如果比真实小 → 主题是假的

口径与 `probe_melody_lang` 完全一致（直接复用 `feats/sim`），另加两点：
  · 真实 MIDI 的起音做 1/16 拍**吸附容差**（真实演奏/未量化 → 落点分布本来就散，
    不吸附会跟我方的严格量化格子不可比）
  · 段落级节奏签名（每小节 16 分格的起音数序列）→ 复用率，直接对应"编配听着一样"

用法:
  python scripts\probe_variety.py                 # 四层报告
  python scripts\probe_variety.py --top 5         # 最像的 5 对
  python scripts\probe_variety.py --self            # 省掉 ④（不读 225 首 MIDI，秒出）

判读：③ 的主题区分力若 ≤ 0（或远小于 ④ 的基线），说明主题只是"标签不同、说话方式相同"。
改完旋律/主题参数后跑一次对照。
"""
import glob
import json
import os
import statistics as st
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底

from probe_melody_lang import feats, sim

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SONGS = os.path.join(ROOT, 'songs')
LIB = os.path.join(ROOT, 'refs', 'midi2')
TWIN = 0.85
SNAP = 0.125          # 真实 MIDI 起音吸附容差（1/16 拍）


def snap_notes(notes):
    """起音吸附到 1/16 拍网格（我方本来就是整数格 → 不变；真实演奏 → 可比重化）"""
    out = []
    for (a, d, p) in notes:
        s = round(a / SNAP) * SNAP
        out.append((s, max(SNAP / 2, d), p))
    out.sort()
    return out


def bars_of(notes):
    """→ (每小节起音数序列, 每小节 16 分格起音数)"""
    on, per = [], {}
    if not notes:
        return [], {}
    nbar = int(notes[-1][0] // 4.0) + 1
    cnt = [0] * nbar
    for (a, _d, _p) in notes:
        b = int(a // 4.0)
        if 0 <= b < nbar:
            cnt[b] += 1
            per.setdefault(b, [0] * 16)[int(round(a * 4)) % 16] += 1
    return cnt, per


def sig_dup(notes):
    """段落级节奏签名复用率：每小节 → 起音数档（1/2/3/4/5+）串，重复小节占比"""
    cnt, _ = bars_of(notes)
    if len(cnt) < 4:
        return None
    sig = [str(min(5, c)) for c in cnt]
    return 1.0 - len(set(sig)) / float(len(sig))


def grid_dup(notes):
    """小节内网格复用率：把每小节 16 格起音向量串成签名，统计重复率"""
    _cnt, per = bars_of(notes)
    if len(per) < 4:
        return None
    sig = [''.join(str(x) for x in per[b]) for b in sorted(per)]
    return 1.0 - len(set(sig)) / float(len(sig))


def song_notes(path):
    """song.json → 每支旋律线的音符（与 probe_melody_lang.notes_of 同口径，但按名字分开）"""
    j = json.load(open(path, encoding='utf-8'))
    bb = 4.0 if (j.get('meter') or [4, 4]) == [4, 4] else 3.0
    lines, tmpl = {}, {}
    pos = 0.0
    for sec in j['sections']:
        nm = sec['melody']
        for x in (j['melody'].get(nm) or []):
            if 0 <= x[0] < sec['bars']:
                lines.setdefault(nm, []).append((pos + x[0] * bb + x[1], x[2], x[3]))
        tmpl.setdefault(nm, []).append((sec['name'], pos, sec['bars']))
        pos += sec['bars'] * bb
    for nm in lines:
        lines[nm].sort()
    return lines, tmpl, (bb == 4.0)


def midi_notes(path):
    """真实 MIDI → 旋律音（复用 theme_pack 的最高声部单音化 + 吸附容差）"""
    import midi_probe as mp
    import theme_pack as tp
    try:
        res = mp.parse(path, quiet=True)
    except Exception:
        return []
    mel, _mt = tp._melody_notes(res)
    return snap_notes([(a, b - a, p) for (a, b, p) in mel])


def real_lib_groups():
    """refs/midi2/<风格目录>/ 分组 → {风格: [曲名...]}（旋律音太少/解析失败的跳过）"""
    groups = {}
    for p in sorted(glob.glob(os.path.join(LIB, '*', '*.mid'))):
        g = os.path.basename(os.path.dirname(p))
        groups.setdefault(g, []).append(p)
    return groups


def pairs_of(items, meta):
    """items:{名:特征} meta:{名:组} → (组内对, 跨组对) 的相似度列表"""
    names = sorted(items)
    ins, outs = [], []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            c = sim(items[a], items[b])
            (ins if meta.get(a) == meta.get(b) else outs).append(c)
    return ins, outs


def _fmt(v):
    return '%3.0f%%' % (v * 100) if v is not None else ' -- '


def report(self_only=False, top=3):
    songs = {}
    meta = {}
    intra = []          # (曲内最像的一对, 曲, [线名...])
    sigs = []
    for d in sorted(glob.glob(os.path.join(SONGS, '*'))):
        p = os.path.join(d, 'song.json')
        if not os.path.isfile(p):
            continue
        name = os.path.basename(d)
        try:
            lines, _t, is44 = song_notes(p)
        except Exception:
            continue
        if not lines:
            continue
        j = json.load(open(p, encoding='utf-8'))
        th = (j.get('theme') or {}).get('name') or '(未标注)'
        alln = sorted(n for ln in lines.values() for n in ln)
        f = feats(alln, is44)
        if f:
            songs[name] = f
            meta[name] = th
        ln = list(lines)
        if len(ln) >= 2:
            ps = sorted(((sim(feats(lines[x], is44) or {}, feats(lines[y], is44) or {}), x, y)
                         for i, x in enumerate(ln) for y in ln[i + 1:]), reverse=True)
            intra.append((ps[0][0], name, ps[0][1], ps[0][2]))
        for nm, arr in lines.items():
            s = grid_dup(arr)
            if s is not None:
                sigs.append((s, name, nm))
    out = {'songs': songs, 'meta': meta, 'intra': intra, 'sig': sigs}

    # ③ 主题区分力（我方）+ ④ 基线（真实库）
    my_in, my_out = pairs_of(songs, meta)
    out['my_in'], out['my_out'] = my_in, my_out
    if not self_only:
        real = {}
        rmeta = {}
        for g, ps in real_lib_groups().items():
            for p in ps:
                n = midi_notes(p)
                f = feats(n, True) if len(n) >= 8 else None
                if f:
                    real[os.path.basename(p)] = f
                    rmeta[os.path.basename(p)] = g
        out['real'] = real
        out['real_in'], out['real_out'] = pairs_of(real, rmeta)
    return out


def main():
    top = int(sys.argv[sys.argv.index('--top') + 1]) if '--top' in sys.argv else 3
    self_only = '--self' in sys.argv
    r = report(self_only=self_only, top=top)
    songs = r['songs']
    print('参检 %d 首曲目（%d 个主题标签）\n' % (len(songs), len(set(r['meta'].values()))))

    print('【① 曲内】同一首歌里不同旋律线之间（≥%.0f%% = 就是同一支）' % (TWIN * 100))
    for c, nm, a, b in sorted(r['intra'], reverse=True)[:top]:
        print('  %s  %-22s %s ~ %s' % (_fmt(c), nm, a, b))
    if r['sig']:
        v = [s for s, _n, _m in r['sig']]
        v.sort()
        print('  段落级节奏签名复用率  中位 %s  90分位 %s  最高 %s（%d 支旋律线）'
              % (_fmt(st.median(v)), _fmt(v[int(len(v) * 0.9)]), _fmt(v[-1]), len(v)))

    print('\n【② 跨曲】全库两两语言重合度')
    names = sorted(songs)
    ps = sorted((sim(songs[a], songs[b]) for i, a in enumerate(names) for b in names[i + 1:]),
                reverse=True)
    print('  中位 %s  90分位 %s  最高 %s   孪生对(≥%.0f%%) %d'
          % (_fmt(st.median(ps)), _fmt(ps[int(len(ps) * 0.1)]), _fmt(ps[0]),
             TWIN * 100, sum(1 for x in ps if x >= TWIN)))

    print('\n【③/④ 主题区分力（组内 − 组间）】')
    rows = [('我方曲库', r['my_in'], r['my_out'])]
    if 'real_in' in r:
        rows.append(('真实模板库', r['real_in'], r['real_out']))
    for tag, ins, outs in rows:
        if not ins or not outs:
            print('  %-10s 组内/组间样本不足（%d/%d）' % (tag, len(ins), len(outs)))
            continue
        mi, mo = st.median(ins), st.median(outs)
        print('  %-10s 组内中位 %s  组间中位 %s  → 区分力 %+5.1f%%  (n=%d/%d)'
              % (tag, _fmt(mi), _fmt(mo), (mi - mo) * 100, len(ins), len(outs)))
    if 'real_in' in r and r['my_in'] and r['my_out']:
        d_my = st.median(r['my_in']) - st.median(r['my_out'])
        d_rl = st.median(r['real_in']) - st.median(r['real_out'])
        print('  → %s' % ('主题有区分力（不低于真实库）' if d_my >= d_rl
                          else '!! 主题区分力低于真实库基线 %.1f 个百分点 → 主题只是标签'
                               % ((d_rl - d_my) * 100)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
