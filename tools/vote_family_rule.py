#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""vote_family_rule.py —— **族票**装配 + **S1 自适应开关**（提取流水线的应用层优化）。

## 它解决什么（第十三轮实测，10 首留出集 + 3 首对照）

1. **族内合并（本轮到目前最好的应用层规则）**：把"同族多视图"当**一票**，而不是各算一票。
   实测（9 视图 · 10 首）：`票数≥3` **+0.0041** → `每族≥1票` **+0.0083**（涨 9/跌 1，收进精度 0.394）。
   为什么会读错：9 视图里有 **5 个 BP 变体 + 3 个 YMT3 变体**，"3 票"可能只是
   "同一族的 3 个近似视角" —— 票**不独立**，弱视图靠数量压倒强视图。
   ⇒ 5 视图那套（+0.0073）其实是"族票"的一个巧合近似；族票把它变成**显式规则**且更省（2 视图即可）。

2. **S1 自适应开关**（无监督，不需要真值）：`S1 = base 的音里能被任一视图 ±50ms 认出`。
   实测 S1 ↔ ΔF1 的 Spearman **−0.782**；投票为负的两首 S1（0.901/0.908）**高于**所有正收益曲目
   （最高 0.824）⇒ 阈值 **0.825** 可事前决定"开不开投票"（0.825–0.900 整段同为 +0.0087）。
   ⚠ S1 **只在同一视图口径内可比**：视图数 5→2 会让它掉 0.30–0.44（真实商业录音实测 0.174）。

## 用法

```bash
# ① 只看诊断（不写文件）：报 S1、各族票数分布、建议动作
python scripts\vote_family_rule.py --base <base.mid> \
    --view ymt3=Y1.mid --view ymt3=Y2.mid --view bp=B1.mid --view bp=B2.mid
# ② 装配（族票 ≥1：两族都提才收）+ 写出
python scripts\vote_family_rule.py --base <base.mid> --out <out.mid> --family-min 1 --view ...
# ③ 带真值时才有的验收（可选）：--truth <真值.mid> → 打印 ΔF1（**没有真值就别给**）
```

**不收什么**（别误以为它会做）：不跑任何模型（视图要在外面先转好）；
不做"哪一族的票更可信"的加权（实测加权 w_ymt3≥1.5 反而降分）；
不做 TTA（实测把变速视图加进来 **降分**：+0.0083 → +0.0075，虽然它确实更独立 —— 与既有 BP 的重合
只有 0.032，而 BP 彼此是 0.459）。
"""
import argparse
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))
import cli_utf8 as _cu          # noqa: E402
_cu.setup()

import truth_eval as TE         # noqa: E402
import midi_file as MF          # noqa: E402

GRID = 0.1          # 0.1s 格（与 vote_apply.py 同口径）
TOL = 0.05          # ±50ms 同音高算同一个音
S1_THRESHOLD = 0.825    # 第十三轮标定：≥ 它就别开投票（0.825–0.900 整段平台）


def load_pitched(path):
    ns, _ = TE.load_notes(path, drop_drum=False)
    return [x for x in ns if x[4] != "drums"]


def _key(t, p):
    return (int(round(t / GRID)), p)


def compute_views(base, views, offset=True):
    """→ (vote_cells, note_of, s1)

    `vote_cells[cell] = {'ymt3': n, 'bp': n, ...}`（**按族计数**，不是按视图）
    `s1 = base 被任一视图覆盖率`（视图先按 `best_dt` 与 base 对齐）
    """
    base_cells = set(_key(x[0], x[2]) for x in base)
    cells = defaultdict(lambda: defaultdict(set))
    note_of = {}
    for fam, path in views:
        vn = load_pitched(path)
        off = 0.0
        if offset:
            off, _n = TE.best_dt(base, vn)
        for x in vn:
            t = x[0] + off
            k = _key(t, x[2])
            if k in base_cells:
                continue
            cells[k][fam].add(path)
            note_of.setdefault(k, (t, x[1], x[2], x[3]))
    # S1：base 的每个音，有没有任一视图（对齐后）在 ±TOL 内认出来
    covered_cells = set()
    for fam, path in views:
        vn = load_pitched(path)
        off, _n = TE.best_dt(base, vn) if offset else (0.0, 0)
        covered_cells |= set(_key(x[0] + off, x[2]) for x in vn)
    g = int(TOL / GRID)
    hit = sum(1 for (f, p) in base_cells
              if any((f + d, p) in covered_cells for d in range(-g, g + 1)))
    s1 = hit / max(1, len(base_cells))
    return cells, note_of, s1


def pick(cells, note_of, family_min=1, require_families=None):
    """族票规则：**每个要求的族至少 family_min 个视图提过**"""
    fams = require_families or sorted({f for c in cells.values() for f in c})
    out = []
    for k in sorted(note_of):
        c = cells[k]
        if all(len(c.get(f, ())) >= family_min for f in fams):
            out.append(note_of[k])
    return out


def main():
    ap = argparse.ArgumentParser(description="族票装配 + S1 诊断（应用层，不跑模型）")
    ap.add_argument('--base', required=True, help='base MIDI（如 YMT3 单来源）')
    ap.add_argument('--view', action='append', default=[],
                    help='fam=path（可重复；fam 是**族名**：ymt3 / bp / 自定义）')
    ap.add_argument('--out', help='写了就输出装配后的 MIDI（不写 = 只诊断）')
    ap.add_argument('--family-min', type=int, default=1,
                    help='每族至少几个视图提过才算数（默认 1 = 两族都提；实测 2 更严但收益下降）')
    ap.add_argument('--truth', help='可选：有真值时打印 ΔF1（**没有就别传**）')
    ap.add_argument('--no-offset', action='store_true', help='不做 best_dt 对齐（默认做）')
    a = ap.parse_args()

    if not a.view:
        print('至少给一个 --view fam=path')
        return 2
    views = []
    for s in a.view:
        if '=' not in s:
            print('--view 要写成 fam=path：%r' % s)
            return 2
        fam, path = s.split('=', 1)
        if not os.path.exists(path):
            print('视图不存在：%s' % path)
            return 2
        views.append((fam, path))

    # ⚠ `load_notes` 返回 **(notes, sr)**，notes 是 5 元组 (t, dur, pitch, vel, role)，
    #   不是列表 —— 实测第一版按列表写会 `KeyError: 4`。
    base_all, _sr = TE.load_notes(a.base, drop_drum=False)
    base = [x for x in base_all if x[4] != 'drums']
    cells, note_of, s1 = compute_views(base, views, offset=not a.no_offset)
    fams = sorted({f for c in cells.values() for f in c})

    print('base 非鼓音 %d ；视图 %d 个（族：%s）' % (len(base), len(views), ', '.join(fams)))
    print('**S1（base 被视图覆盖率）= %.3f**  → 阈值 %.3f ⇒ %s'
          % (s1, S1_THRESHOLD,
             '**别开投票**（base 已被视图认全，补进来的多半是噪声）' if s1 >= S1_THRESHOLD
             else '可以开投票（base 还有大片视图认不出的区域）'))
    print('   ⚠ **S1 只在同一视图口径内可比**（实测：同一首 5 视图 = 0.691 / 9 视图 = **0.823**，'
          '差 0.13）；阈值 %.3f 是在 **5 视图**口径上标的 —— 视图集换了要重新标。'
          % S1_THRESHOLD)
    cnt = defaultdict(int)
    for k, c in cells.items():
        sig = '+'.join('%s%d' % (f, len(c.get(f, ()))) for f in fams)
        cnt[sig] += 1
    print('新音票型分布（前 8）：')
    for sig, n in sorted(cnt.items(), key=lambda kv: -kv[1])[:8]:
        print('    %-22s %5d 音' % (sig, n))

    kept = pick(cells, note_of, family_min=a.family_min, require_families=fams)
    print('族票规则（每族 ≥%d）：收 %d / 新音 %d' % (a.family_min, len(kept), len(note_of)))

    if a.truth:
        truth, _sr2 = TE.load_notes(a.truth, drop_drum=False)
        truth = [x for x in truth if x[4] != 'drums']
        f0, P0, R0, _a, _b, _c = TE.note_f1(truth, base, tol=TOL)
        f1, P1, R1, _a, _b, _c = TE.note_f1(truth, base + kept, tol=TOL)
        print('（有真值）非鼓 F1 %.3f → %.3f（Δ %+.4f）· P %.3f→%.3f · R %.3f→%.3f'
              % (f0, f1, f1 - f0, P0, P1, R0, R1))
    else:
        print('（没给 --truth：**没有精度读数**，只有票型与 S1 —— 别把票数当精度）')

    if a.out:
        # ⚠ **保留音色骨架**（坑 277 的教训）：不是"新建一条轨"，而是把新音**加回 base 对应的轨**
        #   —— 摊平成"一条钢琴轨"会让音符数/音高/时间全对、只有听感暴露。
        model = MF.import_midi(a.base)
        trs = model.get('tracks') or []
        if len(trs) != 1:
            print('⚠ base 有 %d 条轨：新音会加进**第一条音高轨**；'
                  '要精确分配音色请用带 skeleton 的装配（resid_bp.rebuild(skeleton=…)）' % len(trs))
        target = None
        for t in trs:
            if not t.get('drum') and t.get('notes'):
                target = t
                break
        if target is None:
            target = trs[0] if trs else None
        if target is None:
            print('base 里没有可写的轨，放弃输出')
            return 1
        for (t, dur, p, vel) in kept:
            target['notes'].append([round(float(t), 4), round(float(dur), 4), int(p), int(vel)])
        target['notes'].sort(key=lambda n: n[0])
        model['end_beat'] = max(model.get('end_beat') or 0,
                                max([n[0] + n[1] for n in target['notes']] or [0]))
        MF.export_midi(model, a.out)
        print('已写 %s（base %d 音 + 新收 %d 音 · 写进轨 %r · 骨架未摊平）'
              % (a.out, len(base), len(kept), target.get('name')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
