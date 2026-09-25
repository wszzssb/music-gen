#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""filter_by_stem.py —— **按分轨能量筛掉"这条轨根本没在响"的段落里的音**（对所有轨）。

## 为什么（2026-09-25 实测，本次事故的直接产物）

`siren_end2` 开头 18 秒我们弹了 **42 个凭空贝斯音**（13+29），而原曲那两段的 `h6_bass`
是 **−87.4 / −80.8 dB**（与 `h6_other` 差 50~60dB，等于没有）；真贝斯要到 **24.7s** 才进来。
根因：合并器**不检查"这条分轨在这一段到底有没有声音"** —— 转录在近乎静音的素材上照样出音
（`bp_h6_bass` 就贡献了那批音）。用户听感："不像"。

⚠ 门限**用分布双峰标定**，不许拍：贝斯那两簇之间空档 **31.8dB**（S01/S02 在 −80 以下、
S03 起跳到 −49），门限取 −55dB 落在空档中间。**每条轨各自标定**，本工具会把分布打出来。

## 用法

```bash
python scripts\filter_by_stem.py <merged 目录> --stems <demucs 分轨目录> --song <song.json>
python scripts\filter_by_stem.py ... --apply          # 写盘（逐文件 .pre_stemfilter.bak）
python scripts\filter_by_stem.py --selftest
```
"""
import argparse
import json
import os
import shutil
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()
import metrics
import midi_file as mf

# 我方轨名 → 该轨内容应当来自哪条分轨（可多条，取最大能量）
TRACK_STEM = {
    'Bass': ('h6_bass', 'h4_bass'),
    'Piano': ('h6_piano', 'h6_other'),
    'Pad': ('h6_other',),
    'Strings': ('h6_other',),
    'Hook': ('h6_guitar', 'h6_other'),
    'Drums': ('h6_drums', 'h4_drums'),
}


GRID = None               # 占位：本工具按段处理，不用格
GAP_MIN = 12.0            # 双峰之间的空档至少这么大才筛（实测贝斯 31.8dB、其它轨 3.7~11.0dB）


def seg_bounds(song_json, bpm):
    sj = json.load(open(song_json, encoding='utf-8'))
    out, acc = [], 0.0
    spb = 60.0 / float(sj.get('bpm') or bpm)
    for s in sj['sections']:
        bars = float(s.get('bars') or 0)
        out.append((s.get('name'), acc, acc + bars))
        acc += bars
    return out, spb


def stem_rms(path, bounds, spb):
    """→ [(段名, dBFS)]"""
    y, sr = metrics.read_audio(path, mono=True)
    y = np.asarray(y, dtype='float32')
    out = []
    for (nm, b0, b1) in bounds:
        seg = y[int(b0 * spb * sr):int(b1 * spb * sr)]
        out.append((nm, 20 * np.log10(max(float(np.sqrt(np.mean(seg ** 2))), 1e-12))
                    if len(seg) else -120.0))
    return out


def gap_threshold(vals, default=-55.0):
    """**分布双峰**标定：把排序后的值找最大空档，门限取空档中点（空档 <12dB 则不筛）。"""
    v = sorted(x for x in vals if x > -110)
    if len(v) < 4:
        return None, 0.0
    best_gap, at = 0.0, None
    for a, b in zip(v, v[1:]):
        if b - a > best_gap:
            best_gap, at = b - a, (a + b) / 2.0
    if at is None or best_gap < GAP_MIN:
        return None, best_gap
    return at, best_gap


def main():
    ap = argparse.ArgumentParser(description='按分轨能量筛掉"该段该轨根本没在响"的音')
    ap.add_argument('merged', nargs='?', help='合并产物目录（含 Bass.mid / Piano.mid ...）')
    ap.add_argument('--stems', help='demucs 分轨 wav 目录')
    ap.add_argument('--song', help='song.json（取段界）')
    ap.add_argument('--bpm', type=float, default=145.9605)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not (a.merged and a.stems and a.song):
        ap.print_help(); return 1

    bounds, spb = seg_bounds(a.song, a.bpm)
    total_drop = 0
    for tr, stems in TRACK_STEM.items():
        p = os.path.join(a.merged, tr + '.mid')
        if not os.path.isfile(p):
            continue
        doc = mf.import_midi(p)
        notes = doc['tracks'][0]['notes']
        # 该轨每段的分轨能量（取可用来源里最大的）
        per_stem = {}
        for s in stems:
            sp = os.path.join(a.stems, s + '.wav')
            if os.path.isfile(sp):
                per_stem[s] = dict(stem_rms(sp, bounds, spb))
        if not per_stem:
            print('%-8s 找不到分轨（%s）→ 跳过' % (tr, '/'.join(stems)))
            continue
        seg_db = {nm: max(per_stem[s][nm] for s in per_stem) for (nm, _b0, _b1) in bounds}
        thr, gap = gap_threshold(list(seg_db.values()))
        quiet = {nm for nm, v in seg_db.items() if thr is not None and v < thr}
        drop_beats = [(b0, b1) for (nm, b0, b1) in bounds if nm in quiet]
        keep = [n for n in notes if not any(b0 <= n[0] < b1 for (b0, b1) in drop_beats)]
        ndrop = len(notes) - len(keep)
        total_drop += ndrop
        print('%-8s 音 %4d → %4d（删 %3d）  门限 %s（最大空档 %.1fdB）  静音段 %d/%d %s'
              % (tr, len(notes), len(keep), ndrop,
                 ('%.1fdB' % thr) if thr is not None else '不筛', gap, len(quiet), len(bounds),
                 ('→ ' + ','.join(sorted(quiet))[:60]) if quiet else ''))
        if a.apply and ndrop:
            bak = p + '.pre_stemfilter.bak'
            if not os.path.exists(bak):
                shutil.copy2(p, bak)
            doc['tracks'][0]['notes'] = keep
            mf.export_midi(doc, p, fmt=1)
            back = mf.import_midi(p)
            assert len(back['tracks'][0]['notes']) == len(keep), '读回音数不符（写盘异常）'
    print('\n合计拟删 %d 音（%s）' % (total_drop, '已写盘' if a.apply else '--apply 才写盘'))
    return 0


def selftest(verbose=True):
    """尺子自检：双峰分布必须标出中间的空档；无空档时必须**拒筛**（不许硬挑一个门限）。"""
    thr, gap = gap_threshold([-80.0, -79.0, -81.0, -20.0, -19.0, -21.0])
    assert thr is not None and -79.0 < thr < -21.0, \
        '双峰之间的门限应落在空档里，实得 %s（空档 %.1f）' % (thr, gap)
    thr2, gap2 = gap_threshold([-30.0, -29.5, -29.8, -30.2, -29.9])
    assert thr2 is None, '无空档的分布**必须拒筛**（实得门限 %s）—— 硬挑门限就是拍数字' % thr2
    if verbose:
        print('filter_by_stem 自检 PASS：双峰门限 %.1fdB（空档 %.1f）· 无空档拒筛（空档 %.1f）'
              % (thr, gap, gap2))
    return True


if __name__ == '__main__':
    sys.exit(main())
