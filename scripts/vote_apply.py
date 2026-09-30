#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""vote_apply.py —— 多视图**投票装配** + 用尺子验收（应用层，**不跑任何模型**）

把"哪些视图提出的新音值得收"变成一条无监督规则，插进 base 转录里。两条规则**互斥**：

| 规则 | 开关 | 口径 | 10 首留出集实测（`docs/HANDOFF-TRANSCRIBE.md` §12.2 / §12.7） |
|---|---|---|---|
| **族票**（推荐） | `--family-min [N]` | 每个**族**至少 N 个视图提过 | 每族≥1：**ΔF1 +0.0083**（涨 9/跌 1）· 收进精度 0.394 |
| 视图票（旧） | `--k N` | 被 N 个**视图**提过（同族多视图各算一票） | 9 视图 k=3：+0.0041（涨 7/跌 3）· 精度 0.258 |

**为什么族票更好**：9 视图里 5 个是 BP 变体、3 个是 YMT3 变体，"3 票"可能只是"同一族的
3 个近似视角"——票**不独立**（BP 彼此重合 0.459），弱视图靠数量压倒强视图。按族算之后
**只要 2 个视图**就够（比 5 视图那套 +0.0073 更高、更省）。

**族名从哪来**（`--view` 左边那一串）：

· `ymt3_novox=P.mid` —— 含下划线 ⇒ 当**标签**，族名取**第一个下划线之前**的前缀（`ymt3`）；
· `bp=P.mid` / `ymt3=P.mid` —— 不含下划线 ⇒ 整串就是**显式族名**；
· 参与"必须有几族提过"的族 = **有新音被提出来的族**（某族一个音都没提 ⇒ 它对结果没有
  约束力，否则会把整首收空）。文件里会打印实际生效的族清单与逐族视图数。

**S1（无监督风险开关，`--s1` 才打印）**：`S1 = base 的音里能被任一视图（`best_dt` 对齐后）
在同一 0.1s 格 + 同音高认出来的比例`。实测它 ↔ ΔF1 的 Spearman **−0.782**：S1 高 = base
已被视图认全 = 补进来的多半是噪声。阈值 **0.825**，`0.825–0.900` 整段同为 +0.0087（16 个
阈值点扫过）⇒ 不是挑出来的单点。**只打印建议，不改行为**（阈值是在 13 首、5 视图口径上
事后选的，见下）。

用法：
  # ① 族票装配（推荐；两族都提才收）
  python scripts\vote_apply.py <真值.mid> <base.mid> <out.mid> --family-min 1 \
      --view ymt3_nodrums=Y1.mid --view bp_on70=B1.mid ...
  # ② 旧规则（视图票，逐字节复现 2026-09-27 台账）
  python scripts\vote_apply.py <真值.mid> <base.mid> <out.mid> --k 3 --view lab=path ...
  # ③ 只想看风险读数：加 --s1
  python scripts\vote_apply.py <真值.mid> <base.mid> <out.mid> --family-min 1 --s1 --view ...

**规则开关的选择**（判据是"旧台账要能逐字节复现"）：
· 不给 `--family-min` ⇒ 走**旧视图票**路径，默认 `--k 3`（与 2026-10-01 之前逐字节相同）；
· 给了 `--family-min` ⇒ 走族票；`--family-min` 后不写数字 = `N=1`（= 每族至少 1 个视图提）；
· 两个都给 ⇒ **报错退出**（两条规则互斥，别猜）。

⚠ **真值必须是独立的**：拿 base 自己当"真值"时打印的 F1 是**自指**（PITFALLS/§12.6：
"vote_apply 在无真值时打印的 F1 是自指，不是精度"）。本工具会**当场检出并告警**
（base 对真值的 F1 ≥0.999 时）。部署时没有真值 ⇒ 只信 S1 与票型，别信那个 F1。

⚠ **S1 的口径敏感**：同一首 5 视图 0.691 / 9 视图 0.823（差 0.13），真实商业录音 2 视图
口径只有 0.174 ⇒ **换视图集必须重新标定**，别直接套 0.825。另：实现上"±50ms"是
`int(TOL/GRID)=0` ⇒ 实际判据是**同一 0.1s 格**（阈值是按这个实现标出来的，别只改判据）。
"""
import argparse
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cli_utf8 as _cu          # noqa: E402
_cu.setup()

import truth_eval as TE         # noqa: E402
import midi_file as MF          # noqa: E402

GRID = 0.1                  # 0.1s 格（与 truth_eval / vote_diag 同口径）
TOL = 0.05                  # 同音高 ±50ms 算同一个音
S1_THRESHOLD = 0.825        # 第十三轮标定（5 视图口径；0.825–0.900 是平台）
DEFAULT_K = 3               # 旧规则的默认值（与 2026-09-27 版一致）
BUILD_BPM = 120.0           # 重建 MIDI 的定速（秒↔拍无歧义）
DEFAULT_SKELETON = os.environ.get('RESID_TIMBRE_FROM') or None


# ------------------------------------------------------------------ 规则层
def family_of(label):
    """`--view` 左边那一串 → **族名**：`ymt3_novox` → `ymt3`；`bp` → `bp`。"""
    return label.split('_', 1)[0]


def parse_views(specs):
    """`["lab=path", ...]` → `[(label, family, path), ...]`（顺序即视图顺序）。"""
    out = []
    for s in specs:
        if '=' not in s:
            raise SystemExit('--view 要写成 lab=path（或 族=path）：%r' % s)
        label, path = s.split('=', 1)
        label = label.strip()
        if not label:
            raise SystemExit('--view 的左边不能是空：%r' % s)
        if not os.path.exists(path):
            raise SystemExit('视图不存在：%s' % path)
        out.append((label, family_of(label), path))
    if not out:
        raise SystemExit('至少给一个 --view lab=path')
    return out


def load_pitched(path):
    """一个视图的**非鼓**音（尺子读文件：时间轴走完整 tempo map）。"""
    ns, _sr = TE.load_notes(path, drop_drum=False)
    return [x for x in ns if x[4] != 'drums']


def _cells(notes):
    return set((int(round(x[0] / GRID)), x[2]) for x in notes)


def collect_family(base, views, offset=True):
    """族票口径收集 → `(fam_views, note_of, s1)`

    `fam_views[cell] = {族: {视图路径, ...}}` —— **按族**计票（同族多视图只算一族）；
    `note_of[cell] = (秒, 时值, 音高, 力度, 'v')`（新音的**首个**提出者，与旧版同）；
    `s1` = base 被视图覆盖率。

    口径与 `tools/vote_family_rule.py` **逐字对齐**（同一条规则的两个入口必须给出同一个
    ΔF1，见 `selftest.t_vote_apply_family_rule` 与 `docs/HANDOFF-FAMILY-VOTE.md` §1 验收①）。
    """
    base_cells = _cells(base)
    fam_views = defaultdict(lambda: defaultdict(set))
    note_of = {}
    covered = set()
    for _label, fam, path in views:
        vn = load_pitched(path)
        off = TE.best_dt(base, vn)[0] if offset else 0.0
        for x in vn:
            t = x[0] + off
            covered.add((int(round(t / GRID)), x[2]))
            k = (int(round(t / GRID)), x[2])
            if k in base_cells:
                continue
            fam_views[k][fam].add(path)
            note_of.setdefault(k, (t, x[1], x[2], x[3], 'v'))
    return fam_views, note_of, _coverage(base_cells, covered)


def _coverage(base_cells, covered):
    """S1：base 的音里，同 0.1s 格 + 同音高被任一视图认出来的比例。

    ⚠ `int(TOL / GRID)` = **0**（0.05/0.1 截断）⇒ 判据实际是"同一格"，不是"±50ms 邻格"。
    阈值 0.825 就是在这个实现上标定的 —— 要么整条照抄，要么重新标定，**别只改判据**。
    """
    g = int(TOL / GRID)
    hit = sum(1 for (f, p) in base_cells
              if any((f + d, p) in covered for d in range(-g, g + 1)))
    return hit / max(1, len(base_cells))


def fams_present(fam_views):
    """真正"提过新音"的族（一个音都没提的族不参与要求，否则会把结果收空）。"""
    return sorted({f for c in fam_views.values() for f in c})


def pick_family(note_of, fam_views, family_min=1, require_families=None):
    """族票：**每个要求的族至少 `family_min` 个视图提过**这个音。"""
    fams = require_families if require_families is not None else fams_present(fam_views)
    return [note_of[k] for k in sorted(note_of)
            if all(len(fam_views[k].get(f, ())) >= family_min for f in fams)]


def collect_votes(base, views, offset=True):
    """**旧规则**（视图票）的收集 —— 与 2026-09-27 版逐行同口径（标签计票，不看族）。

    刻意与 `collect_family` 分开写、不合并：旧路径要能**逐字节复现台账**
    （`docs/HANDOFF-TRANSCRIBE.md` §12.1），合并成一个函数就得靠开关走两条分支，
    改一处就可能悄悄动到旧产物。这里宁可重复 10 行。
    """
    base_cells = _cells(base)
    votes = defaultdict(set)
    note_of = {}
    for label, _fam, path in views:
        vn = load_pitched(path)
        off = TE.best_dt(base, vn)[0] if offset else 0.0
        for x in vn:
            k = (int(round((x[0] + off) / GRID)), x[2])
            if k in base_cells:
                continue
            votes[k].add(label)
            note_of.setdefault(k, (x[0] + off, x[1], x[2], x[3], 'v'))
    return votes, note_of


def pick_k(note_of, votes, k):
    return [note_of[c] for c in sorted(note_of) if len(votes[c]) >= k]


# ------------------------------------------------------------------ 写出层
def rebuild(notes_by_track, out_mid, skeleton=None):
    """把 [(轨名, 通道, program, drum, [(秒,时值,音高,力度)])] 写成定速 120BPM 的 MIDI。

    **与 `resid_bp.rebuild` 同实现**（复制而非 import：那份在交付/工作目录里，仓库要自足）。
    2026-10-01 用 10 首留出集逐字节验过（产物 SHA256 与旧版一致）。

    ⚠ **必须给 `skeleton`（源转录文件）**（坑 277）：不给就退化成"一条钢琴轨"——
    音符数/音高/时间**一个都不差**，只有**听感**会暴露音色被压平。这里给的是同一条
    告警 + 同一条 `RESID_TIMBRE_FROM` 兜底。
    """
    if skeleton is None:
        skeleton = DEFAULT_SKELETON
    if skeleton is not None:
        notes_by_track = _onto_skeleton(notes_by_track, skeleton)
    else:
        n_p = sum(1 for (_nm, _ch, _pr, d, ns) in notes_by_track if not d and ns)
        if n_p <= 1:
            print('  ⚠ rebuild() 未给 skeleton（也没设 RESID_TIMBRE_FROM）：输出只有 %d 条音高轨 —— '
                  '音色分配会被压平（坑 277：音符数/音高/时间全看不出来，只有听感会暴露）。'
                  '交付前必须跑 restore_timbre.py，或设 RESID_TIMBRE_FROM=<源转录.mid>。' % n_p)
    m = {'format': 1, 'division': 480, 'bpm': BUILD_BPM, 'timesig': [4, 4],
         'title': 'resid', 'end_beat': 4.0, 'source': out_mid, 'tracks': []}
    spb = 60.0 / BUILD_BPM
    end = 0.0
    for i, (nm, ch, prog, drum, notes) in enumerate(notes_by_track):
        tr = {'index': i, 'name': nm, 'channel': 9 if drum else ch,
              'program': int(prog or 0), 'drum': bool(drum), 'mute': False,
              'solo': False, 'hidden': False, 'notes': [], 'ccs': [],
              'program_changes': [], 'markers': []}
        for (t, d, p, v) in notes:
            tr['notes'].append([t / spb, max(1, int(round(d / spb * 480))) / 480.0,
                                int(p), int(v)])
            end = max(end, t + d)
        m['tracks'].append(tr)
    m['end_beat'] = round(end / spb + 1.0, 3)
    MF.export_midi(m, out_mid, fmt=1)
    return out_mid


def _onto_skeleton(notes_by_track, skeleton, tol=0.03):
    """把（可能已压平的）音符按骨架的轨/通道/program 放回去（坑 277 的根治实现，同 `resid_bp`）。"""
    m = MF.import_midi(skeleton)
    t2s, div, _ev = TE.make_tick2sec(skeleton)
    sk = []
    for tr in m.get('tracks', []):
        drum = bool(tr.get('drum')) or tr.get('channel') == TE.DRUM_CH
        ns = []
        for (st, du, p, v) in tr.get('notes', []):
            a = t2s(float(st) * div)
            b = t2s((float(st) + float(du)) * div)
            ns.append((a, max(0.0, b - a), int(p), int(v)))
        sk.append((tr.get('name') or 'skel', tr.get('channel'), tr.get('program'), drum, ns))
    pitched = [(i, sorted(n[2] for n in ns)[len(ns) // 2])
               for i, (_nm, _ch, _pr, d, ns) in enumerate(sk) if not d and ns]
    drum_i = [i for i, (_nm, _ch, _pr, d, _ns) in enumerate(sk) if d]
    idx = {}
    for i, (_nm, _ch, _pr, d, ns) in enumerate(sk):
        if d:
            continue
        for (t, _du, p, _v) in ns:
            idx.setdefault((int(p), round(t, 2)), []).append(i)
    buckets = [[] for _ in sk]
    used = {}
    hit = miss = 0
    for (_nm, _ch, _pr, drum, ns) in notes_by_track:
        for (t, d, p, v) in ns:
            if drum:
                if drum_i:
                    buckets[drum_i[0]].append((t, d, p, v))
                continue
            cands, key = [], None
            for k in (round(t, 2), round(t + 0.01, 2), round(t - 0.01, 2)):
                cands = idx.get((int(p), k), [])
                if cands:
                    key = (int(p), k)
                    break
            n_used = used.get(key, 0) if key else 0
            if cands and n_used < len(cands):
                ti = cands[n_used]
                used[key] = n_used + 1
                hit += 1
            elif pitched:
                ti = min(pitched, key=lambda x: abs(x[1] - int(p)))[0]
                miss += 1
            else:
                buckets[0].append((t, d, p, v))
                continue
            buckets[ti].append((t, d, p, v))
    print('  rebuild(skeleton)：放回原轨 %d 个 · 按音高就近继承 %d 个 · 轨 %d 条'
          % (hit, miss, len(sk)))
    return [(nm, ch, pr, d, sorted(buckets[i], key=lambda x: x[0]))
            for i, (nm, ch, pr, d, _ns) in enumerate(sk)]


# ------------------------------------------------------------------ 主流程
def main(argv=None):
    ap = argparse.ArgumentParser(
        description='多视图投票装配（族票 / 视图票）+ 尺子验收；不跑模型')
    ap.add_argument('truth_mid', help='真值 MIDI（**没有真值就别拿 base 冒充**——会被检出并告警）')
    ap.add_argument('base_mid', help='base 转录（如 YMT3 单来源）')
    ap.add_argument('out_mid', help='输出 MIDI')
    ap.add_argument('--k', type=int, default=None,
                    help='旧规则：至少 %d 个**视图**提过才收（给了就走旧路径，逐字节复现台账）'
                         % DEFAULT_K)
    ap.add_argument('--family-min', type=int, nargs='?', const=1, default=None,
                    help='族票规则：每个**族**至少 N 个视图提过才收（不写 N = 1；与 --k 互斥）')
    ap.add_argument('--view', action='append', default=[],
                    help='lab=path（可重复）；lab 含下划线时族名取其前缀，否则整串是族名')
    ap.add_argument('--s1', action='store_true',
                    help='打印 S1（base 被视图覆盖率）+ 阈值 %.3f 的建议（**只提示，不改行为**）'
                         % S1_THRESHOLD)
    ap.add_argument('--skeleton',
                    help='源转录 MIDI（音色骨架）：新音按"同音高同刻 → 回原轨"放回去，'
                         '轨/通道/program 一条不改（坑 277）。不给则取环境变量 RESID_TIMBRE_FROM')
    ap.add_argument('--no-offset', action='store_true', help='不做 best_dt 对齐（默认做）')
    a = ap.parse_args(argv)

    if a.family_min is not None and a.k is not None:
        raise SystemExit('--k（视图票）与 --family-min（族票）互斥：选一条规则，别两条一起给')
    views = parse_views(a.view)
    offset = not a.no_offset
    fam_mode = a.family_min is not None
    k = DEFAULT_K if a.k is None else a.k

    truth, _m = TE.load_notes(a.truth_mid, drop_drum=False)
    ref_nd = [x for x in truth if x[4] != 'drums']
    base, _m = TE.load_notes(a.base_mid, drop_drum=False)
    base_nd = [x for x in base if x[4] != 'drums']
    base_dr = [x for x in base if x[4] == 'drums']

    fams = []
    if fam_mode:
        fam_views, note_of, s1 = collect_family(base_nd, views, offset=offset)
        fams = fams_present(fam_views)
        kept = pick_family(note_of, fam_views, family_min=a.family_min,
                           require_families=fams)
        rule = '族票>=%d' % a.family_min
        cnt = defaultdict(int)
        for _c, v in fam_views.items():
            cnt['+'.join('%s%d' % (f, len(v.get(f, ()))) for f in fams)] += 1
    else:
        votes, note_of = collect_votes(base_nd, views, offset=offset)
        kept = pick_k(note_of, votes, k)
        rule = 'k>=%d' % k
        s1, cnt = None, defaultdict(int)

    # 收进来的音**自己**的精度（判断"票"值不值钱的唯一读数）
    if kept:
        _f, pk, _r, _pr, _mx, _ex = TE.note_f1(ref_nd, kept, tol=TOL)
        # ⚠ 两个"精度"口径差得不小，**都印**免得跟旧表对不上：
        #   书架那把尺子 = 一对一匹配（`note_f1`）；台账（`_tmp/mg-audit/voteA2.json` 的
        #   `prec`，§12.7 表里 0.258 / 0.394 那两个数）是**格重叠**口径（kept 的 0.1s 格+
        #   音高落在真值格里就算命中）—— 后者更松（同一首歌 0.307 vs 0.451）。
        _tcells = _cells(ref_nd)
        pk_grid = sum(1 for x in kept
                      if (int(round(x[0] / GRID)), x[2]) in _tcells) / len(kept)
    else:
        pk = pk_grid = float('nan')
    out_nd = base_nd + kept
    f0, P0, R0, _p, _mi, _ex = TE.note_f1(ref_nd, base_nd, tol=TOL)
    f1, P1, R1, _p, _mi, _ex = TE.note_f1(ref_nd, out_nd, tol=TOL)

    print('base 非鼓音 %d ；视图 %d 个' % (len(base_nd), len(views))
          + ('（族：%s）' % ', '.join(fams) if fam_mode else ''))
    if fam_mode:
        per = defaultdict(int)
        for _lab, fam, _p_ in views:
            per[fam] += 1
        print('  逐族视图数：%s ⇒ 要求每个族至少 %d 个视图提过'
              % (', '.join('%s×%d' % (f, per[f]) for f in fams), a.family_min))
        print('  新音票型分布（前 8）：')
        for sig, n in sorted(cnt.items(), key=lambda kv: -kv[1])[:8]:
            print('    %-22s %5d 音' % (sig, n))
    print('    %s：收 %4d / 新音 %4d · **收进来的精度 %.3f**（格口径 %.3f）· F1 %.3f→%.3f '
          '(Δ %+.3f) [P %.3f→%.3f · R %.3f→%.3f]'
          % (rule, len(kept), len(note_of), pk, pk_grid, f0, f1, f1 - f0, P0, P1, R0, R1))
    if f0 >= 0.999:
        print('    ⚠ **真值 == base**（F1 起点 %.3f）：这里的 F1/ΔF1 是**自指**，不是精度 —— '
              '部署时没有真值就只看 S1 与票型。' % f0)

    if a.s1:
        if s1 is None:                     # 旧路径没算 S1（它不影响选音），这里补算
            _fv, _no, s1 = collect_family(base_nd, views, offset=offset)
        print('**S1（base 被视图覆盖率）= %.3f**  → 阈值 %.3f ⇒ %s'
              % (s1, S1_THRESHOLD,
                 '**别开投票**（base 已被视图认全，补进来的多半是噪声）' if s1 >= S1_THRESHOLD
                 else '可以开投票（base 还有大片视图认不出的区域）'))
        print('   ⚠ **S1 只在同一视图口径内可比**（实测同一首 5 视图 0.691 / 9 视图 0.823，'
              '差 0.13；真实录音 2 视图只有 0.174）；阈值 %.3f 是在 **5 视图**口径上标的 —— '
              '视图集换了要重新标定。' % S1_THRESHOLD)
    elif fam_mode:
        print('  （`--s1` 可打印无监督风险开关；`--help` 里有口径与阈值说明）')

    rebuild([('pitched', 1, 0, False, [(x[0], x[1], x[2], x[3]) for x in out_nd]),
             ('drums', 9, 0, True, [(x[0], x[1], x[2], x[3]) for x in base_dr])], a.out_mid,
            skeleton=(a.skeleton or None))
    return 0


if __name__ == '__main__':
    sys.exit(main())
