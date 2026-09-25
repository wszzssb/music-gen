#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""unison_guard.py —— **补音 / 加层之前的"同刻打架"守卫**：同刻 + 音程 Δ∈{0,1,2} 半音 + 两边都够响。

## 为什么需要（2026-09-25 实测，不是推测）

`siren_end2` 的 v19 把 Strings 轨 47 个越界低音**搬进已经在弹奏的 Bass 轨**，
指标上"20–40Hz +1.5dB"看着变好了 —— 用户听感却是 **"太吵太乱了还没有之前好听"**。
逐音量出来：那 48 个音里

| 与既有贝斯音的同刻音程 | 个数 | 对照组（v12 自身同刻音之间，398 例） |
|---|---|---|
| **同度 Δ0**（同一音高弹两遍） | **25** | 38 |
| **大二度 Δ2**（低音区最浑浊） | **13** | 16（仅 **4%**） |
| 八度 Δ12 | 4 | 197 |

力度 96–106 顶格。**同度 = 该音被加倍、大二度 = 低音区拍频打架** —— 这就是"糊/吵"的物理来源。
回退那 47 个音（= v22a）后用户确认"现在好多了"。

⚠ **它治的病和"复音上限"不是同一个**：那 47 个音塞进去之后，Bass 轨的**同刻最大并发仍是 4**
（= music-to-midi 那张逐乐器上限表里贝斯的上限 4）——**上限机制根本看不见它**。
所以这个守卫查的不是"同时几个音"，而是"同一个音（或相邻半音）被两层同时弹"。

⚠ **判据只报离群量、不报"有问题"**（技能 §16）：同度叠置在真实编配里也会出现
（齐奏、八度加倍），本工具给的是**计数与位置**，删不删由人或分段规则决定。

## 用法

```bash
python scripts\unison_guard.py <file.mid>                  # 体检（离群量报告）
python scripts\unison_guard.py <file.mid> --segments       # 逐段报告（改前先定位到段）
python scripts\unison_guard.py <file.mid> --apply <out.mid> # 只对 Δ0 去重（保留力度大/优先级高的）
python scripts\unison_guard.py --selftest                  # 尺子自检（正控 2 + 负控 3）
```

判据常量：`VEL_MIN = 90`（两边都要够响）· `DELTAS = (0, 1, 2)`（同度 / 小二度 / 大二度）。
**两个数都可被 `--vel-min` / `--deltas` 覆盖，但改之前先想清楚** —— 它们是从上面那张表标定的。
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()

import midi_file as mf

VEL_MIN = 90                 # 两边力度都要 ≥ 它才算"都够响"
# ⚠ **默认只查严格同度 Δ0**（2026-09-25 实测标定）：
#   · Δ∈{0,1,2} 一起查时，**基线率 93%**（v12 的 Bass 轨里 93% 的音都能找到同刻的
#     Δ≤2 邻居）→ 判据退化成恒真，v21 的 93% 完全看不出来（技能 §16 记过同一现象）；
#   · 只查 Δ0 时：真病例 25/49 = **51%** vs 基线 **5.7%** → **9 倍区分度**。
#   要一并看 Δ1/Δ2 用 `--deltas 0,1,2`（读数当参考，别单独下结论）。
DELTAS = (0,)
ALL_DELTAS = (0, 1, 2)
GRID = 4                     # 1/16 拍格（与本轮实测口径一致）
# 补音体检时**排除"音自身"**：patch 与 base 是同一份数据（基线计算）时，
# 少了这条每个音都会与它自己配成 Δ0 → 基线虚顶到 96%（判据退化成恒真）。
# 提成模块级常量是为了**可被变异测试注入**（见 `mutation_check` 的同刻打架那组）。
SKIP_SELF = True
# Δ0 去重时保留谁：数值越小越优先（低音区留给低音乐器、中音区留给主奏）
PRIO = ['Bass', 'Piano', 'Strings', 'Pad', 'Hook', 'Melody', 'Perc']


def _prio(name):
    return PRIO.index(name) if name in PRIO else len(PRIO)


def index_grid(tracks, t0=None, t1=None):
    """→ {1/16 格: [(track_idx, note_idx, pitch, vel, start_beat)]}
    （带 start_beat 是为了能判"这是不是同一个音本身" —— 少了它，基线会把每个音
    与它自己配成 Δ0，实测把基线率虚顶到 96%。）"""
    g = defaultdict(list)
    for ti, (name, notes) in enumerate(tracks):
        for ni, (st, du, p, v) in enumerate(notes):
            if t0 is not None and st < t0:
                continue
            if t1 is not None and st >= t1:
                continue
            q0 = int(round(st * GRID))
            q1 = max(int(round((st + du) * GRID)), q0 + 1)
            for q in range(q0, q1):
                g[q].append((ti, ni, int(p), int(v), st))
    return g


def find_conflicts(tracks, t0=None, t1=None, vel_min=VEL_MIN, deltas=DELTAS,
                   cross_only=True):
    """找同刻打架的音对（**按音对去重**：同一对音跨多个 1/16 格只报一次）。

    `tracks` = `[(name, [(start_beat, dur_beat, pitch, vel), ...]), ...]`（**拍**）
    → `[(beat, p_a, name_a, vel_a, p_b, name_b, vel_b, delta), ...]`

    ⚠ **别拿它的全曲总数当结论**（2026-09-25 实测）：v12 自身就有 3782 处、
    v21 是 3793 处 —— **基线噪声把真信号淹掉了**（技能 §16：撞音判据在本库接近恒真）。
    有区分度的用法是 `patch_against_base()`（补丁 vs 基准），或按段看**离群尖峰**。
    """
    g = index_grid(tracks, t0, t1)
    out = []
    seen = set()
    for q in sorted(g):
        byp = defaultdict(list)
        for (ti, ni, p, v, _st) in g[q]:
            byp[p].append((ti, ni, p, v))
        ps = sorted(byp)
        for i, pa in enumerate(ps):
            la = byp[pa]
            for pb in ps[i:]:
                d = pb - pa
                if d not in deltas:
                    continue
                lb = byp[pb]
                for x in la:
                    for y in lb:
                        if x[:2] == y[:2]:          # 同一个音自己
                            continue
                        if cross_only and x[0] == y[0]:   # 同一轨
                            continue
                        if x[3] < vel_min or y[3] < vel_min:
                            continue
                        key = (min(x[:2], y[:2]), max(x[:2], y[:2]))
                        if key in seen:
                            continue
                        seen.add(key)
                        out.append((q / float(GRID), x[2], tracks[x[0]][0], x[3],
                                    y[2], tracks[y[0]][0], y[3], d))
    return out


def patch_against_base(base_tracks, patch_tracks, vel_min=VEL_MIN, deltas=DELTAS,
                       cross_only=True, only_new=True):
    """**补音体检**：`patch_tracks` 里**新增的**音，有多少个与 `base_tracks` **已经在响**的音打架。

    这正是 2026-09-25 那次的病：v19 把 47 个弦乐越界低音搬进已经在弹奏的 Bass 轨。
    实测 48 个新增音里 **38 个（79%）**命中 Δ∈{0,2}；而**整个文件**的跨轨重叠率基线是 **36%**
    —— 所以判读要拿**新增音的命中率**比基线（79% vs 36%），不是拿全曲总数。

    `only_new=True`：跳过在 base 里"同轨 + 同起点（±1e-3 拍）+ 同音高"的音（那些不是新增）。
    → `(命中列表, 命中音数, 新增音数)`
    """
    gbase = index_grid(base_tracks)
    base_keys = set()
    if only_new:
        for (nm_b, notes_b) in base_tracks:
            for (st_b, _du_b, p_b, _v_b) in notes_b:
                base_keys.add((nm_b, round(st_b, 3), int(p_b)))
    hits = []
    seen = set()
    hit_notes = set()
    n_patch = 0
    for tj, (name_j, notes_j) in enumerate(patch_tracks):
        for nj, (st, du, p, v) in enumerate(notes_j):
            if only_new and (name_j, round(st, 3), int(p)) in base_keys:
                continue
            n_patch += 1
            if int(v) < vel_min:
                continue
            q0 = int(round(st * GRID))
            q1 = max(int(round((st + du) * GRID)), q0 + 1)
            for q in range(q0, q1):
                for (ti, ni, p2, v2, st_b) in gbase.get(q, ()):
                    # **排除音自身**（patch 与 base 是同一份数据时必须）：
                    # 少了这一条，基线会把每个音与它自己配成 Δ0 → 实测虚顶到 96%
                    if SKIP_SELF and (name_j, round(st, 3), int(p)) == (base_tracks[ti][0],
                                                                       round(st_b, 3), p2):
                        continue
                    d = abs(int(p) - p2)
                    if d not in deltas or v2 < vel_min:
                        continue
                    if cross_only and name_j == base_tracks[ti][0]:
                        continue
                    key = (nj, ti, ni)
                    if key in seen:
                        continue
                    seen.add(key)
                    hit_notes.add((tj, nj))
                    hits.append((q / float(GRID), int(p), name_j, int(v),
                                 p2, base_tracks[ti][0], v2, d, tj, nj, ti, ni))
    return hits, len(hit_notes), n_patch


def summarize(conflicts, n_notes):
    """→ 计数字典（离群量报告用）"""
    c = Counter(x[7] for x in conflicts)
    return {
        'total_notes': n_notes,
        'n_conflicts': len(conflicts),
        'per_1k': round(1000.0 * len(conflicts) / max(n_notes, 1), 2),
        'by_delta': {d: c.get(d, 0) for d in DELTAS},
        'by_pair': Counter('%s+%s' % tuple(sorted((x[2], x[5]))) for x in conflicts).most_common(6),
    }


def dedupe_unison(tracks, t0=None, t1=None, vel_min=VEL_MIN):
    """**只对 Δ0（严格同音高同刻）**去重：保留力度大的；力度相同保留 `PRIO` 靠前的轨。

    Δ1/Δ2 **不自动删** —— 那可能是真实的二度叠置（和弦内音），删了就是改内容。
    → `(删除集合 {track_idx: {note_idx}}, 日志)`
    """
    g = index_grid(tracks, t0, t1)
    kill = defaultdict(set)
    log = []
    for q in sorted(g):
        byp = defaultdict(list)
        for (ti, ni, p, v, _st) in g[q]:
            byp[p].append((ti, ni, p, v))
        for p, lst in byp.items():
            if len(lst) < 2:
                continue
            keep = max(lst, key=lambda x: (x[3], -_prio(tracks[x[0]][0])))
            for x in lst:
                if x[:2] == keep[:2] or x[1] in kill[x[0]]:
                    continue
                kill[x[0]].add(x[1])
                log.append((q / float(GRID), p, tracks[x[0]][0], x[3],
                            tracks[keep[0]][0], keep[3]))
    return kill, log


def apply_dedupe(doc, kill, out_path):
    """按 kill 集合删音 → 导出（读回由调用方核）"""
    for ti, idxs in kill.items():
        doc['tracks'][ti]['notes'] = [n for ni, n in enumerate(doc['tracks'][ti]['notes'])
                                      if ni not in idxs]
    return mf.export_midi(doc, out_path, fmt=1)


# ----------------------------------------------------------------------------- 自检
def selftest(verbose=True):
    """**尺子先拿已知答案跑**（技能 §10 第 4 条）。正控 2 条 + 负控 3 条。"""
    ok = []

    def t(name, tracks, expect, **kw):
        got = len(find_conflicts(tracks, **kw))
        assert got == expect, '%s：期望 %d 处，实得 %d' % (name, expect, got)
        ok.append('%s=%d' % (name, got))

    # ① 正控：同度 + 都够响
    t('同度/双响', [('Bass', [(0.0, 1.0, 40, 100)]), ('Pad', [(0.0, 1.0, 40, 95)])], 1)
    # ② 正控：大二度 —— **只在显式给 deltas 时才算**（默认只查 Δ0，见 DELTAS 的注释）
    t('大二度(显式开)', [('Bass', [(0.0, 1.0, 40, 100)]), ('Pad', [(0.0, 1.0, 42, 95)])], 1,
      deltas=ALL_DELTAS)
    t('大二度(默认不查)', [('Bass', [(0.0, 1.0, 40, 100)]), ('Pad', [(0.0, 1.0, 42, 95)])], 0)
    # ③ 负控：小二度但一方很弱（<VEL_MIN）→ 不算打架
    t('弱音不算', [('Bass', [(0.0, 1.0, 40, 100)]), ('Pad', [(0.0, 1.0, 41, 40)])], 0)
    # ④ 负控：同度但不重叠（先后）
    t('不重叠不算', [('Bass', [(0.0, 1.0, 40, 100)]), ('Pad', [(2.0, 1.0, 40, 100)])], 0)
    # ⑤ 负控：五度（Δ7）不在此判据内
    t('五度不算', [('Bass', [(0.0, 1.0, 40, 100)]), ('Pad', [(0.0, 1.0, 47, 100)])], 0)
    # ⑥ 负控：同轨内的同度（cross_only 默认）→ 不算跨轨打架
    t('同轨不算', [('Piano', [(0.0, 1.0, 40, 100), (0.0, 1.0, 40, 100)])], 0)

    # ⑦ 去重语义：Δ0 该删 1 个，Δ2 **不许动**
    doc = [('Bass', [(0.0, 1.0, 40, 80)]), ('Pad', [(0.0, 1.0, 42, 100)])]
    kill, _log = dedupe_unison(doc)
    assert sum(len(v) for v in kill.values()) == 0, \
        'Δ2（大二度）**不许**被自动删 —— 那可能是真实的二度和声；实删 %r' % dict(kill)
    doc2 = [('Bass', [(0.0, 1.0, 40, 95)]), ('Pad', [(0.0, 1.0, 40, 100)])]
    kill2, log2 = dedupe_unison(doc2)
    assert sum(len(v) for v in kill2.values()) == 1 and kill2.get(0) == {0}, \
        'Δ0 该删力度小的那条（Bass 95 < Pad 100），实得 %r' % dict(kill2)
    ok.append('去重:Δ2不动/Δ0删弱者')

    # ⑧ **补音体检**（这条判据的真正用法）：patch 撞 base 已在响的音
    base = [('Bass', [(0.0, 2.0, 40, 100)])]
    patch = [('Strings', [(0.0, 1.0, 40, 100),    # Δ0 → 命中
                          (1.0, 1.0, 42, 100),    # Δ2 → 命中（都在 base 的 2 拍内）
                          (0.0, 1.0, 47, 100),    # Δ7 → 不命中（不在 DELTAS）
                          (0.0, 1.0, 41, 50)])]   # 力度 50 <VEL_MIN → 不命中
    _hits, n_hit, n_all = patch_against_base(base, patch, deltas=ALL_DELTAS)
    assert (n_hit, n_all) == (2, 4), \
        '补音体检：4 个补丁音里该有 2 个命中（Δ0 与 Δ2），实得 %d/%d' % (n_hit, n_all)
    _h_d, n_hit_d, n_all_d = patch_against_base(base, patch)
    assert (n_hit_d, n_all_d) == (1, 4), \
        '默认（只查 Δ0）该有 1 个命中，实得 %d/%d' % (n_hit_d, n_all_d)
    # ⑨ **基线对照**：base vs base 自身 —— 本轮实测这个基线只有 4%，
    #    所以"命中率"必须**与它比**，不能单独看（技能 §16：本库撞音判据接近恒真）
    _h2, n_hit_base, n_base = patch_against_base(base, base)
    assert n_hit_base == 0, '单轨基准自我对照应为 0（cross_only 排除同轨），实得 %d' % n_hit_base
    ok.append('补音体检:2/4 · 基线0/%d' % n_base)
    if verbose:
        print('unison_guard 自检 PASS：' + ' · '.join(ok))
    return True


def main():
    ap = argparse.ArgumentParser(description='同刻打架守卫（Δ0/Δ1/Δ2 + 力度）')
    ap.add_argument('midi', nargs='?', help='要体检的 .mid')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--vel-min', type=int, default=VEL_MIN)
    ap.add_argument('--deltas', default=','.join(str(d) for d in DELTAS))
    ap.add_argument('--segments', action='store_true', help='逐段报告（段界取 song.json 的 sections）')
    ap.add_argument('--song', default=None, help='配 --segments：曲名或 song.json 路径')
    ap.add_argument('--apply', default=None, help='只对 Δ0 去重并写出到该路径')
    ap.add_argument('--base', default=None,
                    help='**补音体检**：把 midi 当补丁，检查它有多少音撞上 base 已在响的音')
    a = ap.parse_args()

    if a.selftest:
        return 0 if selftest() else 1
    if not a.midi:
        ap.print_help()
        return 1
    deltas = tuple(int(x) for x in a.deltas.split(',') if x.strip())
    doc = mf.import_midi(a.midi)

    def _melodic(tracks_in):
        """**排除鼓轨**：GM 鼓通道上的"音高"是乐器编号（36=Kick/38=Snare/42=HiHat），
        拿它跟贝斯比音程毫无意义 —— 实测不排除时基线率会被顶到 93~100%，
        整条判据退化成恒真（这就是"噪声源"长什么样）。"""
        out = []
        for t in tracks_in:
            nm = (t.get('name') or '').lower()
            if t.get('channel') == 9 or 'perc' in nm or 'drum' in nm:
                continue
            out.append((t['name'], t['notes']))
        return out

    tracks = _melodic(doc['tracks'])
    n_notes = sum(len(t[1]) for t in tracks)

    print('文件: %s' % os.path.basename(a.midi))
    print('判据: 同刻（1/16 格）+ 音程 Δ∈%s 半音 + 两边力度 ≥%d' % (list(deltas), a.vel_min))
    conf = find_conflicts(tracks, vel_min=a.vel_min, deltas=deltas)

    if a.base:
        bdoc = mf.import_midi(a.base)
        btracks = _melodic(bdoc['tracks'])
        # ⚠ **补音体检必须含同轨**：本轮的病就是"往 Bass 轨自己里面补音"
        #   （v19 把 47 个越界低音搬进已经在弹的 Bass 轨）—— 默认排同轨会把真病例滤掉
        #   （实测：只算跨轨 → 14%；含同轨 → 86%，与手算的 79% 同量级）。
        hits, _n, n_new = patch_against_base(btracks, tracks, vel_min=a.vel_min,
                                             deltas=deltas, only_new=True, cross_only=False)
        bhits, _nb, n_base_all = patch_against_base(btracks, btracks, vel_min=a.vel_min,
                                                    deltas=deltas, only_new=False, cross_only=False)
        hs = [h for h in hits if h[2] == h[5]]
        hc = [h for h in hits if h[2] != h[5]]
        bs = [h for h in bhits if h[2] == h[5]]
        bc = [h for h in bhits if h[2] != h[5]]
        # ⚠ 去重键必须是 **(补丁轨下标, 补丁音下标)** —— 按 (轨名,音高) 去重会把
        #   同一轨同一音高的多个音压成一个（实测把 86% 压成 10%，当场被发现）；
        #   基线同理要按 **(基准轨下标, 基准音下标)** 去重，否则一个音跨多格会被重复计。
        n_hit = len({(h[8], h[9]) for h in hits})
        n_hs = len({(h[8], h[9]) for h in hs})
        n_hc = len({(h[8], h[9]) for h in hc})
        bkeys = {(h[10], h[11]) for h in bhits}
        bsame = {(h[10], h[11]) for h in bhits if h[2] == h[5]}
        per_base = Counter(btracks[i][0] for i, _n in bkeys)
        per_base_same = Counter(btracks[i][0] for i, _n in bsame)
        per_total = Counter(nm for nm, notes in btracks for _ in notes)
        per_new = Counter(h[2] for h in hits)
        per_new_same = Counter(h[2] for h in hs)
        pct = 100.0 * n_hit / max(n_new, 1)
        print('\n=== 补音体检（这才是这条判据的用法）===')
        print('  基准: %s（%d 音）' % (os.path.basename(a.base), n_base_all))
        print('  补丁: %s（%d 音）' % (os.path.basename(a.midi), n_notes))
        print('  **新增音 %d 个，其中 %d 个（%.0f%%）撞上基准已在响的音**（Δ∈%s · 两边 ≥%d）'
              % (n_new, n_hit, pct, list(deltas), a.vel_min))
        print('     · **同轨 %d 个（%.0f%%）**（本轮的病全在这一类）' % (n_hs, 100.0 * n_hs / max(n_new, 1)))
        print('     · 跨轨 %d 个（%.0f%%）' % (n_hc, 100.0 * n_hc / max(n_new, 1)))
        print('  对照基线（**基准文件自己**的音里，有多少个处在同样的局面）:')
        print('     · 同轨 **%.0f%%** · 跨轨 %.0f%%'
              % (100.0 * len(bsame) / max(n_base_all, 1),
                 100.0 * len({(h[10], h[11]) for h in bc}) / max(n_base_all, 1)))
        print('\n  **逐轨对照**（新增音落在哪条轨，就拿那条轨自己的常态比）:')
        print('     %-9s %6s %6s %8s %10s' % ('轨', '新增音', '同轨命中', '命中率', '该轨基线率'))
        for nm in sorted(set(list(per_new) + list(per_base)), key=lambda x: -per_new_same.get(x, 0)):
            r_base = 100.0 * per_base_same.get(nm, 0) / max(per_total.get(nm, 1), 1)
            print('     %-9s %6d %6d %7.0f%% %9.0f%%'
                  % (nm, per_new.get(nm, 0), per_new_same.get(nm, 0),
                     100.0 * per_new_same.get(nm, 0) / max(per_new.get(nm, 0), 1), r_base))
        print('  ⚠ 判读：拿"命中率"比**同一行的基线率**。本轮真病例是 Bass 轨 '
              '**89%% vs 该轨基线 19%%（4.7 倍）**；技能 §16：本库撞音判据接近恒真，'
              '**只比基线高一点点的读数不许当"有问题"**。')
        for (bt, p, nj, vj, p2, nb, v2, d, _tj, _nj2, _ti, _ni) in sorted(hits)[:12]:
            print('    t=%7.2fs  Δ%-2d  补丁 %s(%d, vel %d) × 基准 %s(%d, vel %d)'
                  % (bt * 60.0 / float(doc['bpm'] or 120.0), d, nj, p, vj, nb, p2, v2))
        if len(hits) > 12:
            print('    ... 其余 %d 条' % (len(hits) - 12))
        return 0

    s = summarize(conf, n_notes)
    print('\n=== 离群量 ===')
    print('  音数 %d · 打架音对 **%d** 处（每千音 %.2f）' % (n_notes, s['n_conflicts'], s['per_1k']))
    print('  按音程: ' + ' · '.join('Δ%d=%d' % (d, s['by_delta'][d]) for d in deltas))
    if s['by_pair']:
        print('  最多的轨对: ' + ' · '.join('%s:%d' % (k, n) for k, n in s['by_pair']))
    print('  ⚠ **这个总数本身没有区分度**（实测 v12 自身 3782 处 / v21 3793 处，噪声把信号淹了）。'
          '要判断"这次补音有没有问题"用 `--base <基准.mid>`；要定位用 `--segments`。')
    for (bt, pa, na, va, pb, nb, vb, d) in sorted(conf)[:12]:
        print('    t=%7.2fs  Δ%-2d  %s(%d, vel %d) × %s(%d, vel %d)'
              % (bt * 60.0 / float(doc['bpm'] or 120.0), d, na, pa, va, nb, pb, vb))
    if len(conf) > 12:
        print('    ... 其余 %d 处' % (len(conf) - 12))

    if a.segments and a.song:
        sp = 60.0 / float(doc['bpm'] or 120.0)
        secs = []
        if os.path.isfile(a.song):
            import json
            sj = json.load(open(a.song, encoding='utf-8'))
        else:
            sj = json.load(open(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), 'songs', str(a.song), 'song.json'), encoding='utf-8'))
        acc = 0.0
        for sec in sj.get('sections', []):
            b = float(sec.get('bars') or 0) * 4.0
            secs.append((sec.get('name'), acc, acc + b, b))
            acc += b
        print('\n=== 逐段 ===')
        for (nm, b0, b1, _bars) in secs:
            c = find_conflicts(tracks, t0=b0, t1=b1, vel_min=a.vel_min, deltas=deltas)
            n = sum(1 for t in tracks for x in t[1] if b0 <= x[0] < b1)
            if c or n:
                print('  %-6s bar %5.0f-%5.0f  %5.1f-%6.1fs  音 %4d  打架 %3d'
                      % (nm, b0, b1, b0 * sp, b1 * sp, n, len(c)))

    if a.apply:
        kill, log = dedupe_unison(tracks, vel_min=a.vel_min)
        n = sum(len(v) for v in kill.values())
        print('\n=== Δ0 去重：删 %d 个音 ===' % n)
        for (bt, p, dn, dv, kn, kv) in sorted(log)[:10]:
            print('    t=%7.2fs p%-3d 删 %s(vel %d) 留 %s(vel %d)'
                  % (bt * 60.0 / float(doc['bpm'] or 120.0), p, dn, dv, kn, kv))
        if n:
            apply_dedupe(doc, kill, a.apply)
            back = mf.import_midi(a.apply)
            print('    读回: 轨 %d · 音 %d → %d'
                  % (len(back['tracks']), n_notes,
                     sum(len(t['notes']) for t in back['tracks'])))
            print('    已写出: %s' % a.apply)
    return 0


if __name__ == '__main__':
    sys.exit(main())
