#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""melody_gen.py —— **按参考曲的"旋律语言"生成旋律**，写进 song.json。

为什么需要它：手写旋律总带着"我自己的习惯"，而例曲的语言是另一套
（级进为主、切分明显、句子碎、时值长短相间）。用画像驱动生成，才能系统性地贴近。

────────────────────────────────────────────────────────────────────────
⚠ 2026-09-13 重写：**让每首曲子有自己的说话方式**
────────────────────────────────────────────────────────────────────────
旧版只读画像的 4 个标量（`dur16_hist` 抽样 + `onbeat_pct` 二值落点 +
`stepwise_pct` 级进率 + `range`），其余维度全丢；再加上 4 条固定纪律
（时值 GRID 吸附 / 落点八分吸附 / 级进下限 62% / 间隙归零），
结果是**"换了画像也换不出新说话方式"**。实测（12 份画像 × 同一首曲子）：

| 维度 | 生成结果保住画像方言的比例 | 换画像后结果之间仍重合 |
|---|---|---|
| 句长（呼吸） | **2%** | **100%** |
| 落点（律动） | 42% | 88% |
| 音程（走向） | 63% | 90% |
| 时值（长短） | 60% | 80% |

根因逐条对上：
  · 句长 2% —— 生成器**从不产生休止**（末段把音间空隙全部归零）→ 每句都填满整段；
  · 落点 42% —— 只用 `onbeat_pct` 做**二值**判正/反拍，`onset16_hist` 的 16 格方言没用；
  · 音程 63% —— 级进用 `max(0.62, stepwise_pct)` 全局下限，`interval_hist` 没用，
    且**从不产生"同音重复"**（`iv=0`）——而参考曲 BGM33 画像里 `iv=0` 占 **35%**；
  · 时值 60% —— 时值被吸附到全局固定 `GRID=(0.5,1,1.5,2,3,4)`，画像的三连音/十六分值全被削平。

新版做法（纪律一条不删，只把"说话方式"还给画像）：
  1. **句长**：按画像 `phrase_bars` 分布切乐句，**句末留休止**（这才是呼吸，也不再归零填空隙）
  2. **落点**：按画像 `onset16_hist` 的 16 格方言抽样（不再二值化、不再八分吸附）
  3. **时值**：按画像 `dur16_hist` 抽样（不再全局 GRID 吸附）
  4. **音程**：按画像 `interval_hist` 抽样 —— 级进/同音/跳进的比例与方向都由画像说话
  5. **拱形**：每句给一个起伏目标（幅度每首随机取一组），音符向目标漂移
  6. **个性参数**（`persona`）：切分偏好 / 跳进缩放 / 句长缩放 / 拱形幅度 —— 每首随机取一组
  7. **去重筛选**（`--avoid` + `--candidates`）：生成多条候选，挑与库里已有旋律最不像的那条
  保留的纪律：强拍吸附该小节和弦音、弱拍半音（♭9/♮7）回避、音域折返、只支持 4/4。

用法:
  python scripts\\melody_gen.py songs\\14_d75_pulse\\song.json refs\\melody\\BGM33_melody.json [--seed 7]
  python scripts\\melody_gen.py <song.json> <画像> --avoid songs --candidates 8
  python scripts\\melody_gen.py <song.json> <画像> --motif off   # 退回旧的"逐音直方图"版（A/B 用）

**v2 动机层**（默认开）：动机（1 小节固定节奏 figure + 固定音程 cell，音程过 Narmour 期待规则）
逐小节重复、并按该小节和弦**整体移调**（= 模进），句末截断 + 长音 + 落和弦根音/主音（= 终止式）。
为什么：旧版逐音抽样只满足"方言统计"，旋律没有动机/句法/期待 → 听完记不住（用户反馈"不好听"）。
结构层指标由 `motif_stats()` 统计，自检 `melody_motif_rules` 守着。
"""

import glob
import itertools
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）

import json_io      # noqa: E402
import song_engine  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SCALE_MINOR = [0, 2, 3, 5, 7, 8, 10]          # 自然小调
SCALE_MAJOR = [0, 2, 4, 5, 7, 9, 11]          # 自然大调
SCALE_DORIAN = [0, 2, 3, 5, 7, 9, 10]         # 多利亚（B 段色彩）
SPB = 4.0                                     # 一小节的四分音符数（本项目的"拍"口径）
# ⚠ `SPB` 由 `set_meter()` 按曲子拍号设置：4/4→4.0 · 3/4→3.0 · 6/8→3.0 · 2/2→4.0。
# 用**模块全局**是有意的：本文件引用它 33 处，语义只有一个 —— "一小节几拍"
# （强拍位置 `o % SPB`、小节起点 `int(t // SPB)`、句长 `bars * SPB`、十六分格→拍 `off/4.0`）。
# 改成参数贯穿要动 33 处、漏一处就会静默把音撒到小节外；前提是**串行调用**
# （本项目是 CLI，一次一曲），`main()` 的拍号守卫里已接上。


def set_meter(meter):
    """按拍号设置"一小节几拍"（`SPB`），返回新的 SPB。

    `meter` 为 `[拍数, 音符单位]`；引擎的"拍"**一律是四分音符**（`bpm` 也是四分音符速度），
    所以一小节几个四分音符 = `拍数 * 4 / 单位`（同 `song_engine._norm_meter` 的口径）。
    """
    global SPB
    n, d = int(meter[0]), int(meter[1])
    SPB = float(n) * 4.0 / float(d)
    # ⚠ 默认参数在**函数定义时**就绑定了旧 SPB（`def form_stats(..., bar_beats=SPB)`）——
    # 不重绑的话这两个统计函数在 3/4 下仍按 4 拍切小节，守卫会拿错基准（静默给错答案）。
    # 它们定义在本函数之后，而调用发生在模块加载完毕的 `main()` 里，所以这里引用得到。
    for _fn in (form_stats, small_step_pct):
        _fn.__defaults__ = (SPB,)
    return SPB
DENS_MAX = 2.6                                # 密度上限（音/小节，用户口径，见 main() 里 dens）


def infer_scale(d, tonic):
    """按**本曲自己的和弦**判大小调。

    旧版写死小调（只有段落 `"mode": "dorian"` 能切换）—— 大调曲上（23 号 D 大调）
    三度/七度音被当成调外，每次生成都被"修正"±1 半音，实测把 65% 的音压成同音重复，
    旋律等于原地踏步。大调与小调只共享 4 个音级（0/2/5/7），按和弦音级的匹配率能分清。

    ⚠ 但**光看匹配率会被属七和弦骗**：F7(F A C Eb) 的 Eb 让"F 小调"的匹配数反超，
    于是一首 F blues/mixolydian 的曲子被判成小调（编 31 号时实测）。所以先看
    **主和弦的三度**（大三度=大调、小三度=小调）——这才是音乐上的第一判据，
    没有三度（sus4/五度和弦）才退回匹配率。"""
    try:
        first = d['chords'][d['sections'][0]['chords'][0]][1]
        ivs = {(t - tonic) % 12 for t in first}
        if 4 in ivs:
            return SCALE_MAJOR
        if 3 in ivs:
            return SCALE_MINOR
    except Exception:
        pass
    pcs = []
    try:
        for sec in d['sections']:
            for cn in (sec.get('chords') or []):
                entry = d['chords'].get(cn)
                if entry:
                    pcs += [t % 12 for t in entry[1]]
    except Exception:
        pcs = []
    if not pcs:
        return SCALE_MINOR
    maj = sum(1 for x in pcs if (x - tonic) % 12 in SCALE_MAJOR)
    mino = sum(1 for x in pcs if (x - tonic) % 12 in SCALE_MINOR)
    return SCALE_MAJOR if maj > mino * 1.15 else SCALE_MINOR


# ────────────────────────── 画像 → 说话方式 ──────────────────────────

def _top_hist(hist, keep=0.85):
    """直方图 → [(键(int), 权重)]，只留累计 `keep` 的高频档。

    滤掉扒谱长尾（单次出现的格子多是 F0 跟踪噪声），同时保住方言主体。"""
    items = sorted(((int(k), v) for k, v in (hist or {}).items() if v > 0),
                   key=lambda kv: -kv[1])
    tot = sum(v for _k, v in items)
    if not tot:
        return []
    out, acc = [], 0
    for k, v in items:
        out.append((k, v))
        acc += v
        if acc >= tot * keep:
            break
    return out


def persona(prof, rng, jitter=0.15):
    """画像 → 这一首曲子的"说话方式"，含**每首随机取一组**的个性参数。

    同 seed 同画像 → 同结果（保持确定性）。个性参数用调用方的 rng 抽，
    所以"每首用不同 seed"就等于"每首有自己的说话方式"。"""
    lo, hi = prof.get('range', [62, 84])
    # **音域：照画像取，不内收**（`SPAN_TRIM = 0`）。旧版是 `(min(lo+2,74), max(hi-1,80))` ——
    # 收窄的初衷是给拱形留余量，但拱形与移调本来就有夹取；实测 37 号因此只用了 13 个半音
    # （画像 64–81 = 17 个），用户口径是"用足 1.5 个八度（真实 BGM 21 个半音）"。
    # ⚠ **2026-10-05 补**：画像的 `range` 是**模板之间**的 p10~p90 跨度（实测 15~50 半音、
    # 中位 **39**），把它当"一条旋律的音域"用，旋律就会在三个八度里游走 —— 重做 100-108
    # 后实测 lounge 旋律 **38..72（34 半音）**，直接导致"旋律落在伴奏最高音之下"
    # 从 12% 涨到 38.5%。这里按 `mean_pitch ± MELODY_SPAN/2` 取窗口（**夹在画像
    # p10~p90 之内**、不越出依据），跨度超过 `MELODY_SPAN` 时才收；缺 `mean_pitch`
    # 或画像本来就窄 = 原行为不变。
    _mp = prof.get('mean_pitch')
    if _mp and (hi - lo) > MELODY_SPAN:
        _c = min(max(float(_mp), lo + MELODY_SPAN / 2.0), hi - MELODY_SPAN / 2.0)
        lo, hi = int(round(_c - MELODY_SPAN / 2.0)), int(round(_c + MELODY_SPAN / 2.0))
    P = {'range': (lo + SPAN_TRIM, hi - SPAN_TRIM if SPAN_TRIM else hi)}

    # ① 落点方言（16 分格，0=小节第 1 拍）
    #   keep 用 0.92（比其他维度宽）：实测 0.80 会把**格 0/8（第 1、3 拍）**这种
    #   "频次不是最高、但音乐上必须有"的落点当长尾滤掉 —— 13 号因此生成出一条
    #   完全没有正拍音的旋律。另外补一道"第 1 拍保底"。
    ons = _top_hist(prof.get('onset16_hist'), 0.92)
    if ons and not any(k == 0 for k, _w in ons):
        ons.append((0, max(1, min(w for _k, w in ons))))
    P['onsets'] = sorted(ons) or [(0, 3), (4, 3), (8, 2), (12, 2)]
    # 原始落点直方图留着：动机层要用它给候选 cell 打分（`_cell_fit`）——
    # 自动调参/自检比的就是这份分布，动机的落点分布就是整条旋律的落点分布。
    P['_onset_hist'] = dict(prof.get('onset16_hist') or {})

    # ② 时值方言（16 分格 → 拍）
    P['durs'] = [(k / 4.0, v) for k, v in _top_hist(prof.get('dur16_hist'), 0.85)]
    if not P['durs']:
        P['durs'] = [(0.5, 3), (1.0, 3), (2.0, 2)]

    # ③ 音程方言：同音重复 / 级进 / 跳进，各自的比例与方向都听画像的
    ivh = {int(k): v for k, v in (prof.get('interval_hist') or {}).items() if v > 0}
    near = [(k, v) for k, v in ivh.items() if abs(k) <= 2]
    # **同音重复（iv=0）上限 12%**：F0 跟踪在每个稳定音上连续出帧，量化后全变成"同音重复"，
    # 画像里的 0 因此被**系统性放大**（实测 fine_BGM11 34%、BGM33 36%、bgm41 46%）。
    # 早先只压到 30% —— 仍然远高于真实旋律（手写曲实测 1~9%），听感就是"d d d d ddd"。
    tot_n = sum(v for _k, v in near)
    zero_n = sum(v for k, v in near if k == 0)
    if tot_n and zero_n > 0.08 * tot_n:
        sc0 = 0.08 * tot_n / zero_n
        near = [(k, v * sc0 if k == 0 else v) for k, v in near]
    # **±1（半音）权重减半**：画像里 ±1 高是 F0 频率抖动的产物；真实旋律的级进以
    # 全音（±2）为主。不减这一刀，整条旋律就在小二度里蹭（实测 |iv|≤1 曾达 44%，
    # 而手写曲只有 6~15%）。
    near = [(k, v * 0.25 if abs(k) == 1 else v) for k, v in near]
    # 跳进档要**双过滤**：① 只留 3~7 半音 —— 画像里 ±8 以上的多是 F0 八度误判
    # （实测 hitorigohan2 画像含 ±11/±12，照抄会生成 20 处 >7 半音的大跳，而旧版只有 6 处，
    # 旋律立刻失去歌唱性）② 再取累计 85% 的高频档滤长尾。
    leap = [(k, v) for k, v in ivh.items() if 3 <= abs(k) <= 7]
    leap = _top_hist(dict(leap), 0.85) if leap else []
    P['near'] = near or [(-2, 1), (-1, 2), (0, 2), (1, 2), (2, 1)]
    P['leap'] = leap or [(-4, 1), (-3, 1), (3, 1), (4, 1)]
    # 级进率：画像的 `stepwise_pct` 也是 F0 的产物（实测 45~89%，而手写曲的实际旋律只有
    # |iv|≤1 占 6~15%）→ **上限压到 0.62**，否则整条旋律在小二度/大二度里原地打转。
    P['step_w'] = min(0.52, max(0.25, prof.get('stepwise_pct', 55) / 100.0
                               + rng.uniform(-jitter, jitter)))

    # ④ 句长方言（小节）—— 句末休止由此而来（旧版从不休止，句长承接度只有 2%）
    phr = [b for b in (prof.get('phrase_bars') or []) if b > 0][:32]
    P['phr'] = phr or [1.0, 1.0, 2.0, 2.0, 3.0]

    # ⑤ 个性（用户口径：切分比例 / 跳进率 / 乐句长度 / 拱形幅度，每首随机取一组）
    P['pers'] = {
        'syncop': rng.uniform(0.80, 1.30),   # >1 更爱反拍与切分
        'leap': rng.uniform(0.70, 1.40),     # 跳进率缩放（除到级进概率上）
        'phrase': rng.uniform(0.70, 1.40),   # 乐句长度缩放
        'arch': rng.uniform(3.0, 9.0),       # 每句拱形幅度（半音）——
        # 早先 1.5~6.5 太保守：实测 16 号 252 个音只用了 6 个不同音高、音域仅 9 半音，
        # 整条旋律挤在一个小盒子里（听感也是"d d d d"）。句子要真的走出去再回来。
    }
    return P


def _pick(pairs, rng, boost=None):
    """带权重抽一个键；`boost(key)→float` 可对某些键加权（切分偏好用）"""
    ks = [k for k, _v in pairs]
    ws = [v * (boost(k) if boost else 1.0) for k, v in pairs]
    if sum(ws) <= 0:
        return ks[0]
    return rng.choices(ks, weights=ws)[0]


def _pick_phrase_bars(P, rng):
    """按画像句长分布抽一个乐句长度（小节），乘本曲的句长个性。

    ⚠ **下限 1.5 小节**：画像 `phrase_bars` 里大量 0.5 小节的值是**扒谱断裂**
    （F0 只抓到有起音的稳定段）留下的碎片，不是作曲家的乐句 —— 直接照抄会让
    旋律密度掉到 0.9 音/小节（实测 14 号 16 小节只出 29 个音、句间空 2~4 拍，
    一听就是"稀碎"）。画像在这里只提供**相对长短的形状**，绝对长度要落在能成句的区间。"""
    b = rng.choice(P['phr'])
    b = max(b, 1.5) * P['pers']['phrase']
    return max(1.0, min(8.0, round(b * 2) / 2))


# ────────────────────────── 旋律生成 ──────────────────────────

def _ensure_strong_onsets(ons, t, end):
    """**强拍保底**：每 2 小节至少有一个落点在第 1 或第 3 拍（格 0 / 格 8）。

    为什么不能只靠画像权重：落点是**逐段抽样**出来的，段的位置会把强拍格挡在外面
    （段起点在拍 1.25 时，候选里根本没有格 0）。实测 30 号画像的格 0/8 各占 6%，
    生成后 36 小节**只有 3 个强拍音** —— 自检 `melody_chord_fit` 因样本太少直接空转，
    音乐上也失去拍点感（"这条纪律是硬要求，不是口味"）。保底只挪动**已有落点**，
    不新增音、不改音高，所以不影响密度与画像的其它维度。"""
    out = list(ons)
    b0, b1 = int(t // SPB), int((end - 0.01) // SPB)
    b = b0
    while b <= b1:
        grp = range(b, min(b + 2, b1 + 1))
        has = any(any(abs(o - (bb * SPB)) < 1e-6 or abs(o - (bb * SPB + 2)) < 1e-6
                      for o in out) for bb in grp)
        if not has:
            cand = [o for o in out if b * SPB - 1e-9 <= o < min(b + 2, b1 + 1) * SPB]
            if cand:
                o = min(cand, key=lambda x: min(abs(x - int(x // SPB) * SPB),
                                                abs(x - (int(x // SPB) * SPB + 2))))
                bb = int(o // SPB)
                tgt = bb * SPB if abs(o - bb * SPB) <= abs(o - (bb * SPB + 2)) \
                    else bb * SPB + 2.0
                if tgt < end - 0.1:
                    out = [tgt if abs(x - o) < 1e-9 else x for x in out]
        b += 2
    return sorted(set(round(x, 4) for x in out))


# ────────────────────────── 动机层（v2：音乐性） ──────────────────────────
# 为什么加：旧版**逐音**从画像直方图抽样 → 每个音都"合理"，但整条旋律没有动机、没有句法、
# 不满足期待规则 —— 听感是"漫游"，听完记不住（用户原话："这个不是很好听"）。
# 理论依据（详见 HISTORY/`--motif` 的说明）：动机发展（重复/模进/分裂/倒影）+ Narmour 的
# 期待规则（大跳后反向 + 回填、小音程倾向同向）+ 句末终止式。
LEAP_IV = 5            # |音程| ≥ 这个值算"大跳"（Narmour 的反向规则从这里开始）
GAP_FILL_P = 0.55      # 大跳后"回填"（落回跳进区间内）的概率
SAME_DIR_P = 0.50      # 小音程（|iv| ≤ 2）后同向继续的概率
MOTIF_TECHS = ('repeat', 'repeat', 'sequence', 'fragment')


def _ir_intervals(P, rng, k, stepw=0.6):
    """抽 k 个音程，**施加 Narmour 期待规则**（这是"像人写的"最直接的一招）：

      ① 大跳（|iv| ≥ `LEAP_IV`）之后 → **反向**
      ② 反向那一步按 `GAP_FILL_P` **回填**：幅度略小，落回跳进跨过的区间内
      ③ 小音程（|iv| ≤ 2）之后按 `SAME_DIR_P` **同向继续**（惯性）
      ④ 其余情况按画像的级进/跳进比例抽

    旧的逐音抽样只满足 ④ —— 所以旋律会"漫游"：大跳之后继续往同方向跑，听感是悬空的。
    """
    out = []
    for _i in range(max(0, k)):
        prev = out[-1] if out else None
        if prev is not None and abs(prev) >= LEAP_IV:
            back = -1 if prev > 0 else 1
            if rng.random() < GAP_FILL_P:                  # 回填：幅度收一点，落在区间内
                step = back * max(1, abs(prev) - rng.choice([1, 2, 3]))
            else:
                step = back * abs(_pick(P['near'], rng))
            out.append(int(step) or back)
            continue
        if prev is not None and abs(prev) <= 2 and rng.random() < SAME_DIR_P:
            pool = [(s, w) for s, w in P['near'] if s * prev > 0] or P['near']
            out.append(int(_pick(pool, rng)))
            continue
        pool = P['near'] if rng.random() < stepw else P['leap']
        out.append(int(_pick(pool, rng)) or 2)
    return out


# ── 覆盖性硬约束：音要**铺满小节**，不是说完前半句就停（2026-09-14）──────────
# 为什么加（用户原话："好了一点，但还是不如普通的曲子"）：实测 37 号的小节落点是
# `0 / 0.5 / 1.5 / 2.0` 拍 —— 四个音**挤在前 2 拍**，之后空 1.5~2 拍，于是**每小节都
# 被切成一句**（断句 74 处 / 64 小节），听感就是"呆板 + 说一句停一下"。
# 对照真实画像（cheerful，2808 个音）的落点格：0(17%) 6(13%) 4(12.5%) 8(12.4%)
# 14(9.8%) 12(9.1%) 2(9.3%) —— **格 14 = 3.5 拍也是高频格**，真实旋律一直说到小节末。
# 旧约束 `last <= 12`（为"可循环"加的，见坑 126）正是"每小节后半空掉"的来源。
CELL_FIRST_MAX = 6    # 首落点 ≤ 1.5 拍：起句不许拖到后半（小节头空着 = 另一种"停"）
CELL_LAST_MIN = 8     # 末落点 ≥ 2.0 拍：必须**跨过第 2 拍**（治"只说前半句"）
CELL_LAST_MAX = 14    # 末落点 ≤ 3.5 拍：格 15 到下一小节首音只隔 0.25 拍，会被出口的
                      # "落点间距 ≥0.5 拍"规则删掉（可循环靠这一条保住）
CELL_GAP_MAX = 6      # 相邻落点间隔 ≤ 1.5 拍：治"说一句停一下"
CELL_GAP_MIN = 2      # 相邻落点间隔 ≥ 0.5 拍：可循环 + 不许碎音（原约束，保留）
CELL_N_DENSE = 2.8    # 密度目标超过这个值才用 4 个落点（否则 3 个：见 `_motif_cell`）
CELL_DUR_FILL = 0.45  # 动机内每个音至少覆盖到"下一个落点"的多少比例（见 `_motif_cell`）
CELL_TAIL_W = 0.02    # 打分里"末落点越靠后越好"的权重（见 `_cell_fit`）
# **同音串**（2026-10-05 增）：门同 `probe_melody_health` 第③维（≤4）；
# 罚分只进候选排序（`cand_score(run_pen=…)`），超门部分 1.0/音。
SAME_RUN_MAX = 4
SAME_RUN_W = 1.0
# **一条旋律的音域跨度上限**（半音）。依据：真实 BGM 实测 21 个半音 ≈ 1.5 个八度
# （见 `persona` 的注释）；画像 `range` 是模板之间的 p10~p90，跨度可达 50。
MELODY_SPAN = 21
# **逐段密度增益**（2026-10-05）：键 = `sections[i].arr.density`（0~4）。
# 依据：`--dens` 是全局单值，实测所有段落的旋律密度几乎一样；真实曲副歌比主歌密。
# ⚠ 上限守住形态门（`t_melody_form_rules` 密度 1.8~2.9）：dens 2.4 × 1.2 = 2.88。
# ⚠⚠ **2026-10-05 第二次修**：第一版只把这个增益乘进 `dens`，而 `motif` 模式**不读 `dens`**
#   （落点由画像变体铺，实测 80 段全是 2.50 音/小节）→ 增益等于没接。
#   现在它经 `gen_section(dens_gain=…)` 换算成"每小节落点上限"，由 `_cap_onsets` 裁/补。
#   **增益幅度按实测下调**（0.65→0.80、1.2→1.15）：动机铺法是 3~4 个落点/小节，
#   0.65 那一档会把落点压到 2 个/小节（整段退化成"只有骨架音"），听感是"这一段怎么空了"。
SEC_DENS_GAIN = {0: 0.80, 1: 0.90, 2: 1.0, 3: 1.05, 4: 1.10}
# `dens` → 每小节落点数的**标定常数**（2026-10-05 实测两轮）：
#   第一版 1.8 → 逐段中位 0:2.25 / 2:2.75，但**最大值顶到 3.25 音/小节**（形态门 1.8~2.9）
#   → 收到 **1.6**（同一批数据重测：上限不再破门，见 §④ 的读数）。
# 稀疏段的衰减比密段小（落点本来就少），所以这是"够用"的一阶标定，不是精确模型。
_ONS_PER_BAR_AT_2 = 1.6
# 动机层两条守卫的门（`selftest.t_melody_motif_rules` 同源）：句末收束 / 跳后反向。
# 打分里按这两个门补罚（见 `main` 的 `mp`），避免"4 条候选全在门外而排序看不见"。
MOTIF_CAD_MIN = 0.25
MOTIF_REV_MIN = 0.60
ARCH_W = 1.0          # 骨架音的**拱形代价权重**（0 = 句内拱形失效；变异用例注入 0 验判据）
SPAN_TRIM = 0         # 画像 range 两端各收窄几个半音（0 = 用足；旧版是 lo+2 / hi-1）
# **变体层消融开关**：False = 每小节复刻原型 figure（旧形态）。
# 变异用例注入 False 证明 `melody_motif_rules` 的"重复率上限"真的坏得起来；
# 也用于 A/B 对照（同一 seed 下，有/无变体层两版旋律）。
VARIANTS_ON = True


def _cell_candidates(bars, n):
    """枚举满足**覆盖性硬约束**的落点组合（16 格选 n 个，n=3/4 时几百种，直接枚举）。

    为什么枚举而不是"抽样 + 重试"：约束有 4 条且互相牵扯（首/末/间隔上下限），
    随机构造要几十次才命中一次，而且**命中的分布并不均匀**（会偏向容易满足的格）；
    枚举能拿到干净、完整的候选集，再交给 `_cell_fit` 打分 —— 判据与候选解耦。
    """
    slots = int(bars) * 16
    out = []
    for comb in itertools.combinations(range(slots), n):
        if comb[0] > CELL_FIRST_MAX:
            break
        if not (CELL_LAST_MIN <= comb[-1] <= CELL_LAST_MAX):
            continue
        if any(not (CELL_GAP_MIN <= comb[i + 1] - comb[i] <= CELL_GAP_MAX)
               for i in range(n - 1)):
            continue
        out.append(comb)
    return out


def _cell_ok(onsets):
    """覆盖性硬约束校验 —— **生成与变体共用同一份判据**（两处各写一份必然漂移）。

    稀疏变体（1~2 个音的长音小节）故意**不满足**：真实模板里本来就有 21% 的小节
    末落点 <8 格（cheerful，见 `_sparse_cell`），那是乐句的呼吸格，不是错误。
    """
    if len(onsets) < 3:
        return False
    if onsets[0] > CELL_FIRST_MAX:
        return False
    if not (CELL_LAST_MIN <= onsets[-1] <= CELL_LAST_MAX):
        return False
    return all(CELL_GAP_MIN <= onsets[i + 1] - onsets[i] <= CELL_GAP_MAX
               for i in range(len(onsets) - 1))


def _cell_fit(cell, P):
    """动机的落点分布与**画像落点方言**的重叠度（0~1，越大越像）。

    为什么要有这个：动机是"一小节 figure 反复"，它的落点分布**就是**整条旋律的落点分布。
    随机抽一个 cell 可能与画像差很远（实测正拍占比 44%→66%，落点承接度掉到 54%，
    差点跌破自检 `melody_matches_profile` 的 55% 门）。所以抽若干候选，挑最贴的那个 ——
    与 melody_gen 里"多条候选取最不像库里的那条"是同一个思路（候选 + 评分）。
    """
    prof = {int(k): v for k, v in (P.get('_onset_hist') or {}).items() if v > 0}
    if not prof:
        return 1.0
    tot = float(sum(prof.values()))
    want = {k: v / tot for k, v in prof.items()}
    got = {}
    for k in cell['onsets']:
        got[k] = got.get(k, 0) + 1.0 / max(1, len(cell['onsets']))
    base = sum(min(want.get(k, 0.0), w) for k, w in got.items())
    # **末落点偏好靠后**：光看分布重叠，"末落点 8（2.0 拍，之后空 2 拍）"与
    # "末落点 14（3.5 拍，铺到小节末）"几乎同分（画像里格 8 与格 14 的权重只差 2.6 点）
    # —— 但这两者对听感是天壤之别。所以显式加一档"越靠后越好"。
    # ⚠ 试过再加一档对称的"首落点偏好靠前"（`- 0.015*min`）：末落点 ≥8 的小节占比
    # 反而从 77% 掉到 **61%**（首尾都要"贴边"→ 跨度超 6 格间隔上限 → 候选被挤走），
    # 密度也掉到 2.2。**这一条不要加**。"第 1/3 拍有音"由 `_ensure_strong_onsets` 保底。
    return base + CELL_TAIL_W * max(cell['onsets'])


def _make_cell(onsets, P, rng, ivs=None, tail=True, fill=None):
    """落点组合 → 一个动机（补上时值 + 音程 cell）。

    **时值 = 至少覆盖到下一个落点**（`CELL_DUR_FILL`，末音覆盖到小节末）——
    落点铺满 ≠ **声音**铺满。实测本轮：只改落点后末落点中位从 8 格升到 12 格，
    听感仍然断续；量真实模板才发现判据在"无声空档"上：真实旋律的"上一个音结束 →
    下一个音起点"中位只有 **0.12 拍**（音长几乎贴着下一个落点，MIDI 里是连的），
    我们旧版是 **1.0~1.5 拍**。所以音长必须跟着落点走，不能只照画像的时值直方图抽
    （那个直方图是 F0 跟踪量出来的"发声时长"，与 MIDI 的 note-off 不是一回事）。
    ⚠ 但也不能一律拉满：试过 fill=1.0，时值方言承接度掉到 33%（自检门 55%）。
    """
    onsets = list(onsets)
    ends = [k / 4.0 for k in onsets] + [SPB]
    durs = []
    for i in range(len(onsets)):
        span = ends[i + 1] - ends[i]
        need = span if (tail and i == len(onsets) - 1) \
            else span * (CELL_DUR_FILL if fill is None else fill)
        durs.append(round(max(_pick(P['durs'], rng), need), 2))
    return {'onsets': onsets, 'durs': durs,
            'ivs': list(ivs) if ivs else _ir_intervals(P, rng, max(0, len(onsets) - 1))}


def _variants_of(proto, P, rng):
    """原型动机 → 一组**变体**（同一腔调的不同说法）：移位 / 压缩 / 扩展 / 稀疏长音。

    为什么必须有这一层（本轮最重要的实测发现）：真实模板旋律（150 首，`refs/midi2/`）
    的"小节节奏签名重复率"**中位只有 23%**（cheerful 主题均值 33%），而我们的旧版是
    **66~70%** —— 每小节复刻同一个 figure。用户说的"呆板"就是它，量出来了。
    动机不等于"每小节一模一样"：真实旋律保留的是**腔调**（音程走向 + 大致疏密），
    落点则逐小节变（移位、增减音、拉长）。`motif_stats.rhythm_repeat` 因此从
    "下限"改成**上限**（见 selftest 的 `melody_form_rules`）。
    """
    out = [proto]
    seen = {tuple(proto['onsets'])}
    ivs = proto['ivs']

    def add(o, fill=None):
        o = sorted(set(int(x) for x in o))
        if tuple(o) in seen or not _cell_ok(o):
            return
        if len(o) < 1 or o[-1] > CELL_LAST_MAX:
            return
        seen.add(tuple(o))
        out.append(_make_cell(o, P, rng, ivs=ivs, fill=fill))

    # ① **移位**（同 figure 换个位置说 = 节奏层的模进）：±1/±2 个十六分格
    for d in (-2, 2, -1, 1):
        add([k + d for k in proto['onsets']])
    # ② **压缩**（去掉一个中间落点 → 音更少、时长更长）
    if len(proto['onsets']) >= 4:
        for i in range(1, len(proto['onsets']) - 1):
            add(proto['onsets'][:i] + proto['onsets'][i + 1:])
    # ③ **扩展**（在最大间隔里插一个落点 → 更密）
    o = proto['onsets']
    if len(o) >= 2:
        gap_i = max(range(len(o) - 1), key=lambda i: o[i + 1] - o[i])
        mid = (o[gap_i] + o[gap_i + 1]) // 2
        if mid not in o:
            add(o[:gap_i + 1] + [mid] + o[gap_i + 1:])
    return out


def _sparse_cell(P, rng, ivs=None):
    """**稀疏变体**（长音小节）：1~2 个落点，末音铺到小节末。

    依据：真实模板里 21% 的小节末落点 <8 格（cheerful），乐句的"呼吸格"就长这样
    —— 一个音拖住整小节。密度目标 2.0~2.6 音/小节也主要靠它达成：
    密集小节 3~4 音 + 稀疏小节 1 音 ≈ 2.5 音/小节（真实 cheerful 模板均值 2.63）。
    """
    n = rng.choice([1, 2])
    pool = [(k, w) for k, w in P['onsets'] if 0 <= k <= 12]
    if not pool:
        pool = [(0, 1), (4, 1), (8, 1)]
    picks = set()
    for _ in range(8):
        if len(picks) >= n:
            break
        picks.add(_pick(pool, rng))
    ons = sorted(picks)[:n] or [0]
    return _make_cell(ons, P, rng, ivs=ivs, fill=1.0)


def _motif_cell(P, rng, bars=1, tries=20, n=None):
    """抽一个**动机**：一小节的固定节奏 figure（落点 + 时值）+ 固定音程 cell。

    动机 = "一句话的腔调"：重复它、移调它、截断它，才有"记忆点"。
    落点在**满足覆盖性硬约束的候选集**里按 `_cell_fit` 打分挑（不再随机构造）；
    音程走 `_ir_intervals`（Narmour 期待规则）。返回的 dict 里除原型外还有
    `variants`（逐小节轮换的变体，见 `_variants_of`）—— `gen_section` 用它铺满乐句。

    `n` = 原型落点数（默认 3）。**为什么是 3 而不是 4**：密度目标 2.0~2.6 音/小节
    （用户口径）；一小节 3 个落点 + 变体里的稀疏小节 = 实测 2.4 音/小节。
    """
    n = int(n or 3)
    cands = _cell_candidates(bars, n) or [(0, 4, 8)]
    scored = sorted(((_cell_fit({'onsets': list(c)}, P), c) for c in cands),
                    key=lambda x: -x[0])
    # **在 top-k 里随机取，不是取 argmax**：纯取最高分会让每首曲子都落到同一个
    # "最像方言"的节奏型上 → 15 首同款（那正是 `melody_distinct` / `melody_lang_diverse`
    # 两条自检在防的"每首都像"）。k 大一点，"贴方言"与"每首有自己的腔调"都要。
    k = max(1, min(len(scored), max(1, tries)))
    sc, comb = scored[rng.randrange(k)]
    proto = _make_cell(list(comb), P, rng)
    proto['bars'] = bars
    proto['onset_fit'] = round(sc, 3)
    vlist = _variants_of(proto, P, rng) if VARIANTS_ON else [proto]
    # 稀疏变体（乐句的呼吸格）：只配 1 个 —— 配 2 个实测把密度压到 2.38，
    # 而"落点间隔 ≤1.5 拍"要求密度 ≥2.67（间隔 ≤1.5 拍 ⇒ 4/1.5）。密度取用户口径的
    # 上沿 2.6，稀疏格只在需要呼吸时出现。
    if VARIANTS_ON:
        vlist.append(_sparse_cell(P, rng, ivs=proto['ivs']))
    proto['variants'] = vlist
    return proto


def _bar_shift(root, tset, lo, hi, rng):
    """动机根音 → 该小节的**整体移调量**：优先 0（保持动机同一性），
    其次取其和弦音里离得最近的那个（这就是"模进"的实现：同一 figure 换个高度说）。"""
    for off in (0, 2, -2, 3, -3, 5, -5, 4, -4, 7, -7, 12, -12):
        p = root + off
        if lo <= p <= hi and p % 12 in tset:
            return off
    return 0


def parallel_fifths(mel, bass):
    """**平行五/八度**（声部进行的基本禁忌）→ (平行五数, 平行八数, 可判对数)。

    口径（教科书，见坑 131 的链接）：相邻两个"旋律音-低音"的时刻，若两个声部**同向**进行、
    且两次的音程**都是纯五度（7 半音）或八度（0）**，就是平行五度 / 平行八度。

    实测真值（`refs/midi2/` 54 首模板）：平行五度占比**中位 0.000**（75% 分位 0.006）、
    平行八度**中位 0.002**（75% 分位 0.035）—— 我们 0~0.009 / 0.007~0.010，**本来就在范围内**。
    所以这条**不加约束、只做守卫**（防未来退化）。"""
    if len(mel) < 4 or len(bass) < 4:
        return 0, 0, 0
    p5 = p8 = tot = 0
    for i in range(len(mel) - 1):
        t1, t2 = mel[i][0], mel[i + 1][0]
        b1 = [b for (t, b) in bass if t <= t1 + 1e-6]
        b2 = [b for (t, b) in bass if t <= t2 + 1e-6]
        if not b1 or not b2:
            continue
        tot += 1
        m1, m2, l1, l2 = mel[i][1], mel[i + 1][1], b1[-1], b2[-1]
        if (m2 - m1) * (l2 - l1) <= 0:        # 不同向（含一方不动）
            continue
        i1, i2 = (m1 - l1) % 12, (m2 - l2) % 12
        if i1 == i2 == 7:
            p5 += 1
        elif i1 == i2 == 0:
            p8 += 1
    return p5, p8, tot


def form_stats(melody, sections, bar_beats=SPB):
    """**形态层**统计（守卫用）：音有没有**铺满小节**、是不是每小节复刻同一 figure。

    每个指标的对照值都来自真实模板（`refs/midi2/` 150 首，`theme_pack._melody_notes`
    提取旋律，与生成端同一套定义 —— 口径见坑 127）：

    | 指标 | 含义 | 真实中位 | cheerful 主题 | 旧版 37 号 |
    |---|---|---|---|---|
    | `last8` | 小节末落点 ≥8 格（跨过第 2 拍）的**小节占比** | 90% | 79% | **61%** |
    | `maxgap_med` | 小节内最大空档（落点到落点）中位 | 1.03 拍 | 1.40 拍 | **2.00 拍** |
    | `g0` | 格 0（小节第 1 拍）落点占比 | 12.9% | 14.8% | **25.7%** |

    旧版那三个数正好对上用户的听感："三音挤前半小节、空 1.5 拍"（last8/maxgap）、
    "每小节都砸第 1 拍"（g0）。
    """
    notes = []                                  # [(绝对拍, 时值, 音高)]
    pos = 0.0
    for sec in sections:
        for (b, bt, du, p) in (melody.get(sec.get('melody')) or []):
            if 0 <= b < sec['bars']:
                notes.append((pos + b * bar_beats + bt, du, p))
        pos += sec['bars'] * bar_beats
    notes.sort()
    bars = sum(int(s['bars']) for s in sections)
    if len(notes) < 8 or bars <= 0:
        return {}
    perbar, g0 = {}, 0
    for (a, _du, _p) in notes:
        k = int(round(a * 4))                 # 16 分格（只支持 4/4，调用方保证）
        if k % 16 == 0:
            g0 += 1
        perbar.setdefault(int(a // bar_beats), []).append(a)
    last8 = gaps = 0
    mgs = []
    for bar, xs in perbar.items():
        xs = sorted(xs)
        if max(int(round(x * 4)) % 16 for x in xs) >= 8:
            last8 += 1
        ext = xs + [(bar + 1) * bar_beats]
        mgs.append(max(ext[i + 1] - ext[i] for i in range(len(ext) - 1)))
    mgs.sort()
    # **句内高点位置**（旋律写作的拱形：起 → 高点（约 2/3 处）→ 落）。
    # 用**三音滑动平均的轮廓**而不是单音尖峰：听感上的"旋律轮廓"就是平滑后的形状，
    # 单音尖峰会被装饰性跳进带偏（同一批样本实测：原音高点中位 0.40、平滑后 **0.67**）。
    # 句边界用"休止 >0.5 拍"（与 `_hists` 的句长口径一致）。
    peaks, cur = [], []
    for i, (a, du, _p) in enumerate(notes):
        cur.append(notes[i][2])
        nxt = notes[i + 1][0] if i + 1 < len(notes) else a + du
        if nxt - (a + du) > 0.5 or i == len(notes) - 1:
            if len(cur) >= 4:
                sm = [sum(cur[max(0, k - 1):k + 2]) / len(cur[max(0, k - 1):k + 2])
                      for k in range(len(cur))]
                mx = max(sm)
                peaks.append([k for k, v in enumerate(sm) if abs(v - mx) < 1e-9][0]
                             / max(1, len(sm) - 1))
            cur = []
    peaks.sort()
    ps = [p for (_a, _du, p) in notes]
    return {
        'notes': len(notes), 'bars': bars, 'dens': round(len(notes) / bars, 2),
        'last8': round(last8 / max(1, len(perbar)), 3),
        'g0': round(g0 / len(notes), 3),
        'maxgap_med': mgs[len(mgs) // 2] if mgs else 0.0,
        'maxgap_max': mgs[-1] if mgs else 0.0,
        'peak_pos': round(peaks[len(peaks) // 2], 3) if peaks else None,
        'phrases': len(peaks),
        # **音域**（半音）：旧版把画像 range 两头各砍一点（`lo+2 / hi-1`），实测 37 号
        # 只用了 13 个半音而画像有 17 个 —— 用户口径是"音域用足 1.5 个八度"。
        # 判据要**对着画像**判（`>= 画像 span × 0.85`），不是拍一个绝对下限。
        'span': (max(ps) - min(ps)) if ps else 0,
        'lo': min(ps) if ps else 0, 'hi': max(ps) if ps else 0,
    }


def motif_stats(melody, sections, chords=None, tonic=None):
    """**结构层**统计（守卫用；非频谱、非统计方言）：动机/期待/终止式到底有没有发生。

    返回 dict：
      · `bars` 小节数（有音符的）
      · `rhythm_repeat` 节奏动机重复率：**最多见的"小节节奏签名"占比**（签名 = 该小节所有音的
        (16 分格落点, 16 分格时值) 元组）—— 动机重复的直接度量
      · `leap_after` / `leap_reverse` / `gap_fill` 大跳数、跳后反向数、反向里"回填"的数
      · `cadence` 句末长音率：每 4 小节窗口的最后一个音，时值 ≥1.0 拍且落在主/属和弦音的占比
    """
    notes = []                                  # [(绝对拍, 时值, 音高)]
    pos = 0.0
    for sec in sections:
        for (b, beat, dur, p) in (melody.get(sec.get('melody')) or []):
            if 0 <= b < sec['bars']:
                notes.append((pos + b * 4.0 + beat, dur, p))
        pos += sec['bars'] * 4.0
    notes.sort()
    if len(notes) < 4:
        return {}
    iv = [notes[i + 1][2] - notes[i][2] for i in range(len(notes) - 1)]
    leaps = [i for i, x in enumerate(iv) if abs(x) >= LEAP_IV]
    rev = [i for i in leaps if i + 1 < len(iv) and iv[i + 1] * iv[i] < 0]
    fill = [i for i in rev if abs(iv[i + 1]) < abs(iv[i])]
    # 节奏签名**逐段算**（每段一个动机：跨段混在一起统计会把 8 个动机取平均 → 假低）
    rep_each, rep_full_each, nb_each = [], [], []
    pos = 0.0
    for sec in sections:
        sig_on, sig_full = {}, {}
        for (b, beat, dur, _p) in (melody.get(sec.get('melody')) or []):
            if not (0 <= b < sec['bars']):
                continue
            sig_on.setdefault(b, []).append(int(round(beat * 4)))
            sig_full.setdefault(b, []).append((int(round(beat * 4)), int(round(dur * 4))))
        if not sig_on:
            pos += sec['bars'] * 4.0
            continue
        c1, c2 = {}, {}
        for v in sig_on.values():
            k = tuple(sorted(v))
            c1[k] = c1.get(k, 0) + 1
        for v in sig_full.values():
            k = tuple(sorted(v))
            c2[k] = c2.get(k, 0) + 1
        rep_each.append(max(c1.values()) / max(1, len(sig_on)))
        rep_full_each.append(max(c2.values()) / max(1, len(sig_full)))
        nb_each.append(len(sig_on))
        pos += sec['bars'] * 4.0
    top, top_full = sum(rep_each), sum(rep_full_each)
    nbars = sum(nb_each)
    # 句末收束（每 4 小节窗口的最后一个音）：两档 ——
    #   `cadence_rate`：长音（≥1.0 拍）且落在该小节**和弦音**上（旋律层能保证的）
    #   `cadence_root_rate`：更严的"落在和弦根音或主音"（理论上的终止式落点）
    cad_tot = cad_ok = cad_root = 0
    for bar0 in range(0, 9999, 4):
        win = [n for n in notes if bar0 * 4.0 <= n[0] < (bar0 + 4) * 4.0]
        if not win:
            break
        last = win[-1]
        cad_tot += 1
        tones, root = None, None
        if chords is not None:
            pos2, bb = 0.0, int(last[0] // 4.0)
            for sec in sections:
                if pos2 <= last[0] < pos2 + sec['bars'] * 4.0:
                    idx = bb - int(pos2 // 4.0)
                    cl = sec.get('chords') or []
                    cname = cl[idx] if 0 <= idx < len(cl) else None
                    if cname and cname in chords:
                        tones = [t % 12 for t in chords[cname][1]]
                        root = chords[cname][0] % 12
                    break
                pos2 += sec['bars'] * 4.0
        if last[1] >= 1.0:
            if tones is None or last[2] % 12 in tones:
                cad_ok += 1
            if root is not None and last[2] % 12 in (root, tonic):
                cad_root += 1
    return {
        'notes': len(notes), 'bars': nbars,
        # 段内节奏动机重复率（逐段算再平均）：动机在**自己那段**里被重复的比例
        'rhythm_repeat': round(top / max(1, len(rep_each)), 3),
        'rhythm_repeat_full': round(top_full / max(1, len(rep_full_each)), 3),
        'leap_after': len(leaps), 'leap_reverse': len(rev),
        'leap_reverse_rate': round(len(rev) / max(1, len(leaps)), 3),
        'gap_fill': len(fill), 'gap_fill_rate': round(len(fill) / max(1, len(rev)), 3),
        'cadence_rate': round(cad_ok / max(1, cad_tot), 3),
        'cadence_root_rate': round(cad_root / max(1, cad_tot), 3),
    }


# ===================== 节奏细胞层（2026-09-22 新增；见 PITFALLS 237）=====================
# 为什么要有它：落点一直是**逐音从画像分布独立采样**（动机层只管音高图式），于是
# 实测引擎产出的落点是「人类单声主奏」的 2~3 倍散：
#     弱格(16分 e/a) 26.8% vs 人类 12.9% · IOI 熵 0.646 vs 0.310 · 邻小节同型 0% vs 3.1%
# 用户原话："音符的位置有点乱没有规律或者规律不好听"。人类旋律靠**同一个节奏细胞反复**
# 建立规律；逐音抽样给不出这个。做法：每小节把落点换成反复出现的细胞（正拍为主 +
# 每 8 小节约 3 个 16 分装饰 = 弱格 ~11%），**音高顺序不动**（旋律轮廓/和声关系全保留）。
# ⚠ 第一版细胞把弱格压到 0（落点熵 0.49 vs 人类 0.75）= 修过头成"太方"，故保留装饰音。
# ⚠ 形态门（`t_melody_form_rules`）：末落点≥8 格的小节 ≥65%、格 0 占比 ≤22% —— 所以细胞
#   **每一条的末落点都 ≥ 第 2 拍**，且只有约 1/3 的变体从正拍（格 0）起（旧版全从 0 起，
#   实测格 0 冲到 41%、末落点 52% → 两条门当场破）。
CELLS_BY_N = {              # key = 该小节的音数；值是候选落点组（4/4 基准，按 spb/4 缩放）
    # ⚠ 每个列表**只有 1/6 的变体带一个 16 分装饰** —— 组织成 4~5 个变体时实测弱格冲到
    #   18.8%（每段），超人类 11% 太多；1/6 落在 ~8~12%。
    1: [[2.0], [3.0], [1.5], [2.75], [2.5], [3.5]],
    2: [[0.5, 3.0], [1.0, 2.5], [0.0, 2.0], [1.5, 3.0], [0.5, 2.5], [0.75, 2.75]],
    3: [[0.5, 1.5, 3.0], [1.0, 2.0, 3.0], [0.0, 1.0, 2.0], [1.0, 1.5, 3.0],
        [0.5, 2.0, 3.5], [0.0, 1.5, 2.75]],
    4: [[0.0, 1.0, 2.0, 3.0], [0.5, 1.5, 2.0, 3.0], [1.0, 1.5, 2.0, 3.0],
        [0.5, 1.0, 2.0, 3.0], [1.0, 2.0, 2.5, 3.0], [0.0, 0.75, 2.0, 3.0]],
    5: [[0.0, 1.0, 2.0, 2.5, 3.0], [0.5, 1.0, 2.0, 2.5, 3.0], [1.0, 1.5, 2.0, 2.5, 3.0],
        [0.5, 1.5, 2.0, 2.5, 3.0], [0.0, 1.0, 1.5, 2.0, 3.0], [0.5, 1.0, 2.0, 2.75, 3.0]],
    6: [[0.0, 0.5, 1.0, 2.0, 2.5, 3.0], [0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
        [1.0, 1.5, 2.0, 2.5, 3.0, 3.5], [0.5, 1.0, 1.5, 2.0, 2.75, 3.5]],
}
RHYTHM_EXTRA = [0.5, 1.5, 2.5, 0.75]     # 音数超过 6 时的补充落点（都在 8 分/16 分格上）
# **装饰位与轮换**（2026-10-05 改；起因：直接作曲 100-108 的旋律 16 格里有 6~7 格
# **恒为 0**，而它自己吃的主题画像一个恒 0 格都没有）：
# · 旧版装饰位写死在 0.75 / 2.75（= 格 3 / 11）两处 → 格 1/5/7/9/13/15 永远不会出现。
#   现在装饰位从 `DECOR_CELLS` 里**按画像 `onset16_hist` 的权重抽**（画像说哪格有音就
#   往哪格放），弱格总量不变（还是"搬一个已有的装饰"而不是"多加一个"）。
# · 旧版 `v = variants[bar % len(variants)]` 是**按小节号取模**：周期 6 小节，全曲就那
#   6 个型循环（实测 100-108 的"每小节节奏型去重"只有 11~24%）。现在用 rng 抽 + 不许
#   连着两小节同型（`_pick_variant`，rng 缺省时退回旧的取模行为，便于 A/B 与旧曲复现）。
DECOR_CELLS = [1, 3, 5, 7, 9, 11, 13]    # 16 分弱格；**不含格 15**（紧贴下一小节首音会出碎音）
# 弱格比例上限。依据：人类单声主奏实测 12.9%（旧注释），旧版细胞层口径 ≤15%；
# 画像自己的弱格占比是 9%~32%（battle 24% / sorrow 25% / folk_tale 32%）——
# 取 `min(画像, WEAK_CAP)`：跟画像走，但不越过人类区间。
WEAK_CAP = 0.15


def _pick_variant(variants, bar, rng, state):
    """挑这一小节用哪个细胞。`rng is None` → 旧的 `bar % len` 取模（周期 6 小节）。

    周期化轮换实测的后果：全曲只有那 6 个型在循环，"每小节节奏型去重" 11~24%，
    听感是机器。改成 rng 抽 + **不许连着两小节同型**。

    ⚠ **末落点 ≥8 格**（= 2.0 拍）的细胞优先：守卫 `t_melody_form_rules` 要求
    "末落点 ≥8 格的小节 ≥65%"，而表里 cnt=1 有一档 `[1.5]`（格 6）本身就越界 ——
    旧版按 `bar % 6` 轮换时它几乎不落到"只 1 个音"的小节上，改成 rng 抽之后实测
    末落点达标率从 100% 掉到 68~96%（102 号当场逼近门）。这里显式过滤。
    """
    n = len(variants)
    if n <= 1:
        return variants[0]
    if rng is None:
        return variants[bar % n]
    pool = [v for v in variants if round(max(v) * 4) >= CELL_LAST_MIN] or variants
    i = rng.randrange(len(pool))
    prev = state.get('prev')
    if prev is not None and len(pool) > 1 and pool[i] is prev:
        i = (i + 1 + rng.randrange(len(pool) - 1)) % len(pool)
    state['prev'] = pool[i]
    return pool[i]


def _decorate_cell(cell, prof_onsets, rng, b, force=False):
    """管这一小节那**一个** 16 分装饰：已有的搬到画像权重抽出来的弱格上；没有且
    `force` 就把一个内部落点挪到弱格去（音数不变，所以音高分配不受影响）。

    依据（2026-10-05，直接作曲 100-108 实测）：
    · 旧版装饰位写死 0.75 / 2.75 两处 → 9 首里 **6~7 格恒为 0**，而主题画像
      `onset16_hist` 的奇数格各占 2~5%（一个恒 0 格都没有）；生成的弱格总量
      7~11%，画像的弱格是 **9%~32%**（battle 24% / sorrow 25% / folk_tale 32%）。
    · 所以装饰位与装饰**密度**都交给画像：位置按画像权重抽，数量由调用方按
      `min(画像弱格占比, WEAK_CAP)` 的收支跑（见 `apply_rhythm_cells`）。
    ⚠ 两条硬约束不能破：搬/挪之后**末落点仍 ≥ `CELL_LAST_MIN` 格**（守卫
      `t_melody_form_rules` 的"末落点≥8 格 ≥65%"），且与邻音间距 ≥0.5 拍
      （不许出碎音）。做不到就原样返回。
    """
    base = list(cell)
    if rng is None or not prof_onsets or b <= 0 or len(base) < 1:
        return base
    # **只对 4/4 搬**：细胞表是 4/4 基准，3/4 下 `p * k` 缩放出来的落点本来就不落在
    # 八分/十六分格上（实测 `102_waltz_court` 的"弱格" 37% → 63%），
    # 16 格口径在 3/4 下没有意义 —— 那里保持旧行为。
    if abs(b - 4.0) > 1e-9:
        return base
    odd = [x for x in base if int(round(x / b * 16)) % 2 == 1]
    pool = [(c, prof_onsets.get(c, 0)) for c in DECOR_CELLS if prof_onsets.get(c, 0) > 0]
    pool = [(c, w) for c, w in pool
            if all(abs(c / 16.0 * b - x) > 1e-9 for x in base)]
    if not pool:
        return base
    if odd:
        moving = odd[0]
    elif force:
        # 挪**内部**落点（不动最后一个，末落点规则靠它）：挑与邻音最挤的那个
        movable = base[:-1] if len(base) > 1 else base
        if not movable:
            return base
        moving = min(movable, key=lambda x: min(abs(x - y) for y in base if y != x)
                     if len(base) > 1 else 0.0)
    else:
        return base
    others = [x for x in base if x != moving]
    tot = sum(w for _c, w in pool)
    r, acc, new_c = rng.random() * tot, 0.0, pool[-1][0]
    for c, w in pool:
        acc += w
        if r <= acc:
            new_c = c
            break
    new = new_c / 16.0 * b
    if new > b - 0.25 - 1e-9:
        return base
    if any(abs(new - x) < 0.5 - 1e-9 for x in others):
        return base
    out = sorted(others + [new])
    if round(max(out) / b * 16) < CELL_LAST_MIN:      # 末落点不许被搬垮
        return base
    return out


def _cap_onsets(ons, b0, b1, cap, rng):
    """**逐小节落点上限**（2026-10-05，④ 逐段旋律密度的实现本体）。

    为什么需要：`--dens` 是全局单值，而 `motif` 模式下每小节铺的是**画像的变体**
    （`_motif_cell` 铺 3~4 个落点 + `_variants_of` 的压缩/稀疏变体），**完全没读 `dens`**
    —— 实测 16 首 80 个段落的旋律密度中位 **2.50 音/小节**，最小 1.50、最大 2.88，
    与"这段该疏还是该密"（`sections[i].arr.density`）**无关**；`SEC_DENS_GAIN` 乘进去的
    `dens` 只影响一个阈值跳变（`n=(4 if dens > CELL_N_DENSE else 3)`）。
    真实曲的副歌就是比主歌密 → 这条按小节**裁/补**落点，让"段落密度"真的落地。

    · `cap <= 现有`：**裁**。先保强拍（格 0 = 第 1 拍；`SPB==4` 时格 2 = 第 3 拍），
      其余按节拍顺序保留 —— 稀疏段于是退化成"长音骨架"，而不是把强拍裁掉。
    · `cap > 现有`：**补**（至多 +1，且只补**反拍八分**格 2/6/…）：动机模式靠"变体"保持
      "一支旋律一个动机"，补太密会破掉它。上限 +1 时听感是"句子里多一次推动"。
    · 句末小节**不动**（`b1`）：收束是守卫的硬门（末落点 ≥8 格、句末长音）。
    """
    if cap is None or not ons or b1 <= b0:
        return ons
    by = {}
    for o in ons:
        by.setdefault(int(o // SPB), []).append(o)
    out = []
    for b in range(b0, b1 + 1):
        xs = sorted(by.get(b) or [])
        if not xs or b == b1:
            out += xs
            continue
        if len(xs) > cap:
            strong = [o for o in xs
                      if abs(o % SPB) < 1e-6 or (SPB >= 4 and abs(abs(o % SPB) - SPB / 2) < 1e-6)]
            rest = [o for o in xs if o not in strong]
            xs = sorted((strong + rest)[:cap]) if cap >= len(strong) else sorted(strong[:cap])
        elif len(xs) < cap:
            half = SPB / 2.0
            cand = [b * SPB + half * k for k in (1, 3)
                    if b * SPB + half * k < (b + 1) * SPB]
            for c in cand:
                if len(xs) >= cap:
                    break
                if all(abs(c - o) >= 0.5 - 1e-9 for o in xs):
                    xs = sorted(xs + [c])
        out += xs
    return sorted(out)


def apply_rhythm_cells(notes, spb=None, prof=None, rng=None):
    """把一节旋律的**落点**换成反复出现的节奏细胞；音高按原先后顺序贴上（轮廓不变）。

    返回新的 `[[bar, beat, dur, pitch], ...]`。`spb` 缺省取当前拍号写进全局的 `SPB`。
    时值 = 到下一个落点的距离 × 0.95（末音到小节末）—— 顺带把 IOI 熵压回人类区间。
    门（与 `t_melody_form_rules` / `t_melody_motif_rules` 同源）：
    · 末落点 ≥8 格的小节 100%（门 65%）· 格 0 占比 ≤15%（门 22%）· 弱格 ~10%
    · **每 4 小节窗口的末音原样保留**（落点/时值都不动）—— 守卫的"句末收束"要求它是
      长音 + 和弦音；一律套细胞会把它压到 1 拍以内，实测收束率 85% → 0%，当场破门。
    """
    b = float(SPB if spb is None else spb)
    k = b / 4.0

    def snap(x):
        return round(min(b - 0.25, max(0.0, x)) * 4) / 4.0     # 吸到 16 分格，且不越小节

    by_bar = {}
    for n in notes:
        by_bar.setdefault(int(n[0]), []).append(n)
    out = []
    state = {}
    prof_onsets = {int(c): w for c, w in (prof or {}).get('onset16_hist', {}).items() if w > 0}
    p_tot = float(sum(prof_onsets.values()))
    want_weak = (min(WEAK_CAP, sum(w for c, w in prof_onsets.items() if c % 2 == 1) / p_tot)
                 if p_tot else 0.0)
    made = weak_made = 0
    for bar, ns in sorted(by_bar.items()):
        ns = sorted(ns, key=lambda x: x[1])                    # 音高出现的先后 = 轮廓
        tail = ns[-1] if (bar % 4 == 3 and len(ns) > 1) else None
        body = ns[:-1] if tail is not None else ns
        if body:
            cnt = len(body)
            variants = CELLS_BY_N.get(cnt) or CELLS_BY_N[6]
            v = _pick_variant(variants, bar, rng, state)
            limit = float(tail[1]) if tail is not None else b
            # ⚠ 保留末音时，**正身的落点必须全部排在末音之前**：否则 `out.sort()` 之后
            #   音高的先后被换掉 → 跳后反向率实测 0.81 → 0.44（破门 `MOTIF_MIN_REVERSE`）。
            cell = sorted({snap(p * k) for p in v if snap(p * k) < limit - 0.24})
            force = want_weak > 0 and (want_weak * (made + cnt) - weak_made) >= 1.0
            cell = _decorate_cell(cell, prof_onsets, rng, b, force=force)
            if len(cell) < cnt:
                cell = [snap(limit * (i + 1) / (cnt + 1.0)) for i in range(cnt)]
            j = 0
            while len(cell) < cnt:
                cell.append(snap(RHYTHM_EXTRA[j % len(RHYTHM_EXTRA)] * k))
                j += 1
            onsets = sorted(cell)[:cnt]
            made += len(onsets)
            weak_made += sum(1 for x in onsets if int(round(x / b * 16)) % 2 == 1)
            for i, o in enumerate(onsets):
                nxt = onsets[i + 1] if i + 1 < len(onsets) else limit
                out.append([bar, o, round(max(0.25, nxt - o) * 0.95, 3), body[i][3]])
        if tail is not None:                                   # 乐句末音：原样保留
            out.append([tail[0], tail[1], tail[2], tail[3]])
    out.sort()
    return out


def rhythm_cell_stats(notes, spb=None):
    """落点体检（守卫口径）：on8 = 落 8 分格比例 · weak = 落 16 分弱格比例。"""
    import math
    b = float(SPB if spb is None else spb)
    hist = [0] * 16
    for n in notes:
        hist[int(round(((n[0] % 1) * b + n[1]) * 4)) % 16] += 1
    tot = sum(hist) or 1
    ent = 0.0
    for h in hist:
        if h:
            p = h / tot
            ent -= p * math.log2(p)
    return (100.0 * sum(h for i, h in enumerate(hist) if i % 2 == 0) / tot,
            100.0 * sum(h for i, h in enumerate(hist) if i % 4 in (1, 3)) / tot,
            ent / math.log2(16))


def gen_section(sec, chords, prof, rng, mode_scale, tonic, per=None, dens=None,
                motif=None, dens_gain=1.0):
    """给一个段落生成旋律：返回 [(bar, beat, dur, pitch)]

    `tonic` 是**主音的音级**（0-11），由调用方按该曲的和弦推断 —— 以前这里硬编码
    `tonic = 7`（G），换成 D 小调就会把多利亚判定整错。
    `per` 是说话方式（`persona()` 产出）；不传则按 prof 现算。
    `dens` 是这首曲子的目标密度（音/小节，通常沿用原旋律）。**画像的 `notes_per_bar`
    不能直接当密度**：它来自混音 F0 跟踪，漏检严重（实测 BGM33 只有 1.09 音/小节，
    而那是首 75BPM 的舞曲）—— 所以密度服从曲目本身，画像只提供**相对**的长短/落点/走向。
    `dens_gain`（2026-10-05）：**本段**的密度增益（`SEC_DENS_GAIN[arr.density]`）。
    `dens` 全局单值在 `motif` 模式下是**没人读**的（实测 80 段全是 2.50 音/小节），
    所以逐段密度由它换算成"每小节落点上限"（`_ONS_PER_BAR_AT_2` × `dens` × `dens_gain`），
    在 `_cap_onsets` 里裁/补 —— 缺省 1.0 = 老行为逐字节不变（rehearsal 夹具不受影响）。"""
    P = per if per is not None else persona(prof, rng)
    lo, hi = P['range']
    pcs = [(tonic + d) % 12 for d in (mode_scale or [])]
    pers = P['pers']
    # 句内时值上限。**别设太紧**：画像里"长音"往往正是它流动感的来源
    # （BGM09 画像 48% 是 2 拍音），早先设 1.5 会让这些长音全被截成 1.5 拍，
    # 而落点又按画像撒在十六分反拍上 → 结果 82% 的时值跨拍、59% 是"跨拍+反拍"的碎音，
    # 听感就是"镫镫地卡着走"（用户实测反馈）。现在上限放到 2.0 拍：
    # 密度本来就由 `n_t`（每小节几个落点）保证，不需要靠砍长音来凑。
    dur_cap = 2.0 if not dens else max(0.75, min(2.0, 4.0 / max(0.5, dens)))
    out = []
    prev = None
    prev2 = None
    same_run = 0                       # 连续同音计数（上限 2，见下面 ⑤b）
    bar_v = {}                         # 动机模式：小节 → 该小节铺的那个**变体**

    def _sub(frag, cap, bb):
        """从本小节的变体落点里挑 `cap` 个（**按小节确定性轮换**，不是永远留最早的）。

        ⚠ 为什么必须轮换：变体的落点表就是 3~4 个格（如 `[0,1,2]` / `[0,2,3]`），
        一律 `frag[:cap]` 会让整段每小节的落点型**完全相同** —— 实测 102_waltz_court
        的 B/B2 段"落点偏离 0.677"（门 0.65）当场破门。轮换保留 = 同一个变体在不同小节
        用不同的"说法"，这正是动机模式想要的"同一腔调的另一种说法"。
        """
        if cap is None or len(frag) <= cap:
            return frag
        k = max(1, int(cap))
        # ⚠ **纯确定性**（只用小节号）：这里不能碰 `rng` —— 多抽一个随机数会挪动
        #   后面所有随机决策（音高/时值），实测那种"顺手 randrange"会让整首旋律换掉。
        start = (bb * 3) % len(frag)
        return sorted(frag[(start + j) % len(frag)] for j in range(k))

    # **每小节落点上限**（逐段密度，见 `_cap_onsets`）：由本段目标密度换算。
    # `_ONS_PER_BAR_AT_2` = 实测标定（dens 2.39 时落点 ~4.3 个/小节才落到 2.88 音/小节）。
    cap_ons = (None if motif is None
               else max(1, int(round(_ONS_PER_BAR_AT_2 * float(dens or 2.0)
                                     * float(dens_gain or 1.0)))))
    total = sec['bars'] * SPB

    def chord_tones(bar):
        cname = (sec.get('chords') or [])[min(bar, len(sec.get('chords') or []) - 1)] \
            if sec.get('chords') else None
        entry = chords.get(cname) if cname else None
        return sorted({t % 12 for t in (entry[1] if entry else [])})

    def clash(tset, t):
        """半音冲突（♭9/♮7）—— 最刺耳的和声关系，弱拍也要避开"""
        return any(min((t - x) % 12, (x - t) % 12) == 1 for x in tset)

    t = 0.0
    while t < total - 0.5:
        # ① 乐句：长度听画像，**句末留休止**（呼吸 = 句长方言的载体）
        # **动机模式例外**：乐句固定 4 小节（偶尔 2）—— 调性音乐的句法骨架就是 2/4 小节一句
        # （问答句 antecedent–consequent），而且"句末终止式"只有落在固定格上才可测。
        # 画像的 `phrase_bars` 本来就是 F0 断裂留下的碎片（见 `_pick_phrase_bars` 的说明）。
        plen = (rng.choice([4, 4, 4, 2]) if motif is not None
                else _pick_phrase_bars(P, rng)) * SPB
        plen = max(2.0, min(plen, total - t))
        rest = rng.choice([0.5, 1.0, 1.0, 1.5]) if plen >= 3.0 else 0.25
        end = t + plen - rest
        # ② 拱形：这一句要走到哪（幅度每首不同）
        # **新句起点别总继承上一句的末音** —— 那样每句都会收敛回同一小段音区：
        # 实测 32 号 16 小节 31 个音全挤在 F5–D6 九个半音里、隔一两小节就回到 A5，
        # 听感正是用户说的"d d d d ddd"。给 35% 的句子换个音区起头。
        if prev is None:
            start_ref = rng.choice([lo + 4, (lo + hi) // 2, hi - 4])
        elif rng.random() < 0.35:
            start_ref = int(max(lo, min(hi, prev + rng.choice([-7, -5, -3, 3, 5, 7]))))
        else:
            start_ref = prev
        # **拱形方向：不能让一半的句子"倒拱"**。旧版 `aim` 的符号随机（±1 各半），
        # 于是近一半句子"起点就是最高点、一路往下" —— 实测**每句高点中位落在句内
        # 0.225 处**（用户口径要的是"高点在约 2/3 处"），听感"句句都在往下掉"。
        # 现在：**绝大多数句子是上拱**（爬升到 2/3 处的高点、句末回落）；段末句强制下拱
        # （= 全段收束）；剩下 12% 随机下拱当作变化 —— 下拱句的"高点"必然落在句首，
        # 比例一高，整体高点位置就被拽到句子前段（实测 25% 时中位只有 0.22）。
        is_last_phrase = (t + plen) >= total - 1e-6
        d = -1 if (is_last_phrase or rng.random() < 0.12) else 1
        # **上拱句的起点要留出爬升空间**：`start_ref` 可能已经贴近音域上限
        # （`hi - 4`），这时 "高点 = 起点 + arch" 被夹在句首 —— 拱形等于没发生。
        if d > 0:
            start_ref = int(min(start_ref, hi - max(3, round(pers['arch'] * 0.7))))
        aim = start_ref + d * pers['arch']
        # **高点位置在句子的 2/3 处**（不是句末）：旋律写作的拱形是
        # "起 → 高点（约 0.6~0.7 处）→ 落"。把高点放句末就成"一路爬升"——
        # 实测旧版最高音出现在全曲 3~41% 处（拱形幅度又小），整句没有"雕塑感"。
        aim_at = rng.choice([0.62, 2.0 / 3.0, 0.70])
        end_ref = int(round(start_ref + (aim - start_ref) * 0.35))   # 句末落回（不回起点 = 有推进）

        # ①b 落点：句内按**目标密度**分段，每段从画像的方言格里抽一个落点。
        #     密度服从曲目（`dens`，默认沿用原旋律），落点形状服从画像 ——
        #     旧版让"抽到的时值"决定推进速度，慢曲画像的长音会把句腹掏空（1 音/小节）。
        nb = plen / SPB
        # ⚠ 下限必须是 **1**，不能是 2：短句（plen 最小 2 拍 = 0.5 小节）塞 2 个音 = 4 音/小节，
        # 实测把 23 号的密度顶到 3.43（画像只有 0.82 音/小节）→ 时值承接度掉到 26%。
        n_t = max(1, int(round(nb * (dens or 2.0))))
        seg = (end - t) / n_t
        ons = []
        tech = rng.choice(MOTIF_TECHS) if motif is not None else None
        if motif is None:
            for k in range(n_t):
                s0, s1 = t + k * seg, t + (k + 1) * seg
                cands = []
                for barx in range(int(s0 // SPB), int(min(s1, s0 + SPB) // SPB) + 1):
                    base = barx * SPB
                    for x, w in P['onsets']:
                        on = base + x / 4.0
                        if s0 - 1e-9 <= on < s1 - 1e-9:
                            cands.append((round(on, 4), w))
                if k == 0 and cands:                       # 句首别拖：起步限在前半小节
                    c2 = [(o, w) for o, w in cands if o - t < 2.0]
                    cands = c2 or cands
                ons.append(_pick(cands, rng,
                                 boost=lambda kk: pers['syncop'] if int(round(kk * 4)) % 2
                                 else 1.0 / pers['syncop'])
                           if cands else round(s0, 4))
        else:
            # **动机模式**：每小节铺一个**变体**（同一腔调的另一种说法）。
            # ⚠ 不是"每小节复刻同一 figure"：实测真实模板旋律（150 首）的"小节节奏
            # 签名重复率"中位只有 **23%**，我们旧版是 66~70% —— 复刻就是"呆板"。
            # 变体来自 `_variants_of`（移位/压缩/扩展）+ `_sparse_cell`（长音呼吸格）。
            b0, b1 = int(t // SPB), int((end - 0.01) // SPB)
            vs = motif.get('variants') or [motif]
            prev_v = None
            for bb in range(b0, b1 + 1):
                if bb == b1:
                    # 句末小节 = **分裂 + 扩时**（收束写法，坑 126）：优先用稀疏变体，
                    # 末音自然延长成大长音，终止音才长得起来。
                    pool = [x for x in vs if len(x['onsets']) <= 2] or vs
                    v = rng.choice(pool)
                else:
                    # **不许连续两小节用同一个变体** —— 否则又变回"每小节一样"
                    pool = [x for x in vs if x is not prev_v and len(x['onsets']) >= 2] or vs
                    v = rng.choice(pool)
                prev_v = v
                frag = list(v['onsets'])
                if bb == b1 and len(frag) > 1:
                    frag = frag[:1]
                # **逐段密度**（2026-10-05）：本小节多铺一个落点就多一个音 ——
                # 密度高的段允许铺满，低的段只留骨架（详见 `_cap_onsets`）。
                # ⚠ **必须按小节轮换保留哪几个**：一开始写成 `frag[:cap]`（永远留最早的），
                #   实测把同一段里不同变体全裁成同一个型 → `melody_onset_spread` 落点偏离
                #   破门（102_waltz_court 段 B 0.677 > 0.65）。现在用 `_sub` 确定性轮换。
                if cap_ons is not None and bb != b1:
                    frag = _sub(frag, cap_ons, bb)
                bar_v[bb] = v
                for off in frag:
                    on = bb * SPB + off / 4.0
                    if t - 1e-9 <= on < end - 1e-9:
                        ons.append(round(on, 4))
        ons_us = sorted(set(ons))
        ons = ons_us
        # **逐段密度：按小节裁/补**（动机模式的密度实现 —— 必须放在 `_ensure_strong_onsets`
        # 之后，否则强拍保底会把裁掉的位置又补回来）。
        if motif is not None and cap_ons is not None:
            ons = _cap_onsets(ons, b0, b1, cap_ons, rng)
        ons = _ensure_strong_onsets(ons, t, end)
        # **落点间距下限**（跑两遍）：画像里几乎没有极短音（BGM09 画像 0.25 拍 0%、
        # 0.5 拍 2%），而我们生成出 12~15% 的 0.25 拍音 —— 那全是"两个落点挤在一起、
        # 后一个被 `nxt-on` 截断"的伪影，听感就是"镫镫"的碎点。宁可少一个音，不要一个碎音。
        # ① 必须放在强拍保底**之后**（放在之前试过：保底又把落点挪回去，碎音从 12% 反弹到 15%）；
        # ② 要跑**两遍** —— 第一遍遇到贴太近的强拍落点会替换前一个，替换后可能又贴上前前一个。
        def _space(xs, protect=True):
            """落点最小间距 0.5 拍；贴太近时强拍落点顶掉前一个（while 回溯，
            否则"顶掉前一个"之后它可能又贴上**前前**一个 —— 实测那样碎音仍在）。"""
            keep = []
            for o in xs:
                strong = abs(o % SPB) < 1e-6 or abs(abs(o % SPB) - 2.0) < 1e-6
                while keep and o - keep[-1] < 0.5 - 1e-9:
                    if protect and strong:
                        keep.pop()
                    else:
                        o = None
                        break
                if o is not None:
                    keep.append(o)
            return keep
        ons = _space(ons)
        # ③ 离**句末**太近的落点直接丢掉：不然 `end-on` 只剩 0.25 拍 → 又一个十六分碎音。
        # 这是碎音的第二个来源（第一个是落点互挤）。宁可少一个音，不要一个碎音。
        ons = [o for o in ons if end - o >= 0.5 - 1e-9]

        bar_first_i = None          # 动机模式：本小节第一个音在 ons 里的下标
        iv_i = 0                    # 动机模式：音程 cell 的指针
        for oi, on in enumerate(ons):
            nxt = ons[oi + 1] if oi + 1 < len(ons) else end
            bar = int(on // SPB)
            tset = chord_tones(bar)
            if bar_first_i is None or int(ons[bar_first_i] // SPB) != bar:
                bar_first_i = oi        # 进入新小节：figure 从头再说一遍（动机重复）
                iv_i = 0
            # ③ 时值：听画像（不吸附到全局 GRID）；不叠到下一个音、不出句。
            # **允许延音跨小节** —— 早先这里还有个 `SPB-(on-bar*SPB)`（不许出小节），
            # 于是落在 3.75 拍（16 分格 15，画像里很常见的方言落点）的音一律被截成
            # **0.25 拍**：实测 12% 的音变成十六分碎点，听感"镫镫"地卡（用户反馈）。
            # 画像里这种末格落点本来是"延续到下一小节"的长音。跨小节只影响这一口气的长短，
            # 不改变音高（音高仍按落点所在小节的和弦判定）。
            if motif is not None:      # 动机模式：时值来自**本小节那个变体**（同一腔调），按位置循环取
                _v = bar_v.get(bar) or motif
                raw = _v['durs'][(oi - bar_first_i) % len(_v['durs'])]
                if bar == int((end - 0.01) // SPB):
                    raw = max(raw, 1.5)          # 句末长音（收束）
            else:
                raw = _pick(P['durs'], rng)
            cap = dur_cap * 1.5 if oi + 1 == len(ons) else dur_cap    # 句末才允许长音
            dur = max(0.25, round(min(raw, cap, nxt - on, end - on) * 4) / 4)
            if os.environ.get('MELODY_DEBUG'):
                print('    on=%.2f nxt=%.2f end=%.2f raw=%.2f cap=%.2f 段末%s dur=%.2f%s'
                      % (on, nxt, end, raw, cap, oi + 1 == len(ons), dur,
                         '   ← 碎音' if dur <= 0.25 else ''))

            # ④ 音高
            cad_tone = False            # 本音是不是句末终止音（⑧⑨ 要跳过它，见 ④ 末）
            strong = abs((on - bar * SPB) - round(on - bar * SPB)) < 1e-6 and \
                int(round(on - bar * SPB)) % 2 == 0          # 第 1、3 拍
            if motif is not None:
                # **动机模式的音高**：每小节第一个音 = 动机根音**整体移调**到该小节和弦
                # （同 figure 换高度说 = 模进）；其后每个音按动机的**音程 cell** 走。
                if oi == bar_first_i:
                    base = prev if prev is not None else \
                        rng.choice([lo + 4, (lo + hi) // 2, hi - 4])
                    seq = rng.choice([-2, 2]) if tech == 'sequence' else 0
                    # **接缝也要守期待规则**：音程 cell 只管小节内部，小节之间（上一小节末音 →
                    # 本小节首音）原来是自由的 → 实测"跳后反向率"被接缝拉到 52%。
                    # 所以在"能落该小节和弦音"的若干移调里，按期待规则挑代价最小的那个：
                    # ① 离 prev 越近越好（动机同一性 + 平滑）② 大跳后必须反向 ③ 反向要回填。
                    cands = [base + _bar_shift(base, tset, lo, hi, rng) + seq]
                    # **候选要够宽，期待规则才守得住**：早先只在**和弦音**里挑，而
                    # Am6 = A C E F# 这种和弦可用的方向本来就只有一两个（F# 还常常是
                    # 调外音，会被下面的 ⑥ 再挪一次）→ 实测"接缝大跳的反向率只有 43%"
                    # （整曲 48%，自检门 60%）。现在：强拍仍只落和弦音；**弱拍首音**
                    # 允许走音阶内的自由音（代价 +4，见 `_cost`）。
                    for off in (0, 2, -2, 3, -3, 5, -5, 4, -4, 7, -7, 12, -12, 1, -1):
                        pp = base + off + seq
                        if not (lo <= pp <= hi):
                            continue
                        if pp % 12 in tset:
                            cands.append(pp)
                        elif not strong and (mode_scale is None or pp % 12 in pcs):
                            cands.append(pp)
                    pv = prev - prev2 if (prev is not None and prev2 is not None) else None
                    # **拱形落在骨架音上**（小节首音 = 句子的骨架）：只在骨架音处施加
                    # 拱形目标，小节内的音程 cell 完全不动 —— 否则拱形与
                    # `_ir_intervals` 的期待规则互相打架（试过把拱形加在每个音上：
                    # 句高点中位仍只有 0.13，因为随机游走把它淹了）。
                    prog_b = (on - t) / max(1e-6, end - t)
                    if prog_b <= aim_at:
                        tgt_b = start_ref + (aim - start_ref) * (prog_b / aim_at)
                    else:
                        tgt_b = aim + (end_ref - aim) * ((prog_b - aim_at) /
                                                         max(1e-6, 1.0 - aim_at))

                    def _cost(pp):
                        d = pp - prev if prev is not None else 0
                        # **骨架音的第一目标是拱形轨迹**（句子要有轮廓），"离上一音近"
                        # 退居其次 —— 旧版把平滑当第一目标（`c = abs(d)`），骨架音于是
                        # 永远只是"上一个音的邻居"，整句走不出形状（实测句高点中位 0.19）。
                        # 权重 1.0 : 0.45：轮廓主导，但同分时仍优先平滑的那个（动机同一性
                        # 靠 `_bar_shift` 的 off=0 候选与 `seq` 保留，不靠这一项）。
                        c = ARCH_W * abs(pp - tgt_b) + 0.45 * abs(d)
                        # 调外音会被 ⑥ 挪走（±1/±2）→ 期待规则当场失效：提前加代价，
                        # 让"反向级进"优先选**不用再修**的那个候选。
                        if mode_scale is not None and pp % 12 not in pcs:
                            c += 8
                        if pp % 12 not in tset:
                            c += 4                     # 弱拍自由音：可用，但不如和弦音
                        if pv is not None and abs(pv) >= LEAP_IV:
                            if d * pv > 0:
                                c += 14                      # 大跳后同向 = 违反期待
                            elif abs(d) >= abs(pv):
                                c += 6                       # 反向但没回填
                        return c
                    p = int(min(cands, key=_cost))
                else:
                    _v = bar_v.get(bar) or motif
                    ivc = _v['ivs'] or [2]
                    p = prev + ivc[iv_i % len(ivc)]
                    iv_i += 1
                    # **拱形在动机模式里也要生效**：旧版这一段没有 ⑤ —— 实测"最高音"
                    # 落在全曲 3~41% 处，句子根本没有"起 → 高点 → 落"的形状
                    # （用户口径："短语给高点目标（约 2/3 处）"）。
                    # 这里是**轻推**（0.75/0.25，比非动机模式的 0.55/0.45 温和）：
                    # 动机的音程走向是骨架，推太狠会把 `_ir_intervals` 的 Narmour
                    # 期待规则冲掉（⑨ 兜底只救"大跳"，救不了级进的走向）。
                    prog = (on - t) / max(1e-6, end - t)
                    if prog <= aim_at:
                        tgt = start_ref + (aim - start_ref) * (prog / aim_at)
                    else:
                        tgt = aim + (end_ref - aim) * ((prog - aim_at) / max(1e-6, 1.0 - aim_at))
                    p = int(round(p * 0.70 + tgt * 0.30))
                # **句末终止式**：短语最后一小节的最后一个音 → 拉向该小节和弦根音 / 主音。
                # ⚠ 必须按"**最后一小节的最后一个音**"判，不能按"短语的最后一个音"——
                # 截断（fragment）与后移变体会让两者错位（实测收束率只有 38%）。
                if motif is not None and bar == int((end - 0.01) // SPB) and \
                        (oi == len(ons) - 1 or int(ons[oi + 1] // SPB) != bar):
                    cs = [tt for tt in range(lo, hi + 1) if tt % 12 in tset]
                    cl = sec.get('chords') or []
                    cname = cl[min(bar, len(cl) - 1)] if cl else None
                    rpc = (chords or {}).get(cname, [None])[0]
                    rpc = rpc % 12 if rpc is not None else None
                    pref = [tt for tt in cs
                            if tt % 12 == tonic or (rpc is not None and tt % 12 == rpc)]
                    if pref:
                        p = min(pref, key=lambda tt: abs(tt - p))
                        # **终止音标记**：⑧（同音去重）与 ⑨（期待规则兜底）都要跳过它 ——
                        # 实测这两道修正会把刚拉好的终止音又改走，收束率在 50~69% 之间抖
                        # （自检门 55%）。终止音是乐句的落点，优先级高于这两条局部规则。
                        cad_tone = True
            elif prev is None:
                cand = [tt for tt in range(lo, hi + 1) if tt % 12 in tset]
                p = rng.choice(cand) if cand else rng.choice(
                    [lo + 4, (lo + hi) // 2, hi - 4])
            elif strong:
                cand = [tt for tt in range(prev - 3, prev + 4)   # 窗口 ±3：别为凑弦内音跳四五度
                        if tt % 12 in tset and lo <= tt <= hi]
                # ⚠ **别吸回同一个音**：强拍占全部音的 1/4~1/2，每次都选"离 prev 最近的和弦音"
                # 会反复吸回 prev —— 实测这条贡献了大部分同音重复（同音 28% 里抽样只占 12%，
                # 剩下全是这里来的），听感就是每个小节强拍都"d"一下。
                alt = [tt for tt in cand if tt != prev]
                p = rng.choice(alt) if alt else (rng.choice(cand) if cand else prev)
            else:
                stepw = max(0.20, min(0.95, P['step_w'] / pers['leap']))
                if rng.random() < stepw:
                    step = _pick(P['near'], rng)
                else:
                    step = _pick(P['leap'], rng)
                p = prev + step
                if p < lo or p > hi:                          # 越界折返
                    p = prev - step
                # ⑤ 拱形：向本句的目标轨迹漂移（旧版是纯随机游走 → 没有句形）。
                # 轨迹是**两段折线**：先爬到句 2/3 处的高点，再落回句末目标 ——
                # 单段直线（旧版）等于"高点永远在句末"，旋律听着没有起落。
                prog = (on - t) / max(1e-6, end - t)
                if prog <= aim_at:
                    tgt = start_ref + (aim - start_ref) * (prog / aim_at)
                else:
                    tgt = aim + (end_ref - aim) * ((prog - aim_at) / max(1e-6, 1.0 - aim_at))
                # 向本句目标轨迹漂移。原先只拉 28% —— 实测效果是根本没走出去：
                # 32 号 31 个音的音域利用率只有一半（画像 17 个半音，实际只用 9 个）。
                p = int(round(p * 0.55 + tgt * 0.45))
            p = int(max(lo, min(hi, p)))
            # ⑥ 调内 + 半音回避（纪律，保留）
            if mode_scale is not None and p % 12 not in pcs:
                # **优先走全音（±2）**：用 ±1 微调会让整条旋律在小二度里蹭 ——
                # 实测 |iv|<=1 占比里，±1 的大头正是这里和下面的半音回避。
                for _st in (2, -2, 1, -1):
                    if (p + _st) % 12 in pcs and lo <= p + _st <= hi:
                        p += _st
                        break
            if clash(tset, p):
                fixed = None
                for cand2 in (p - 2, p + 2, p - 1, p + 1):   # 先全音后半音
                    if lo <= cand2 <= hi and not clash(tset, cand2) and \
                            (mode_scale is None or cand2 % 12 in pcs):
                        fixed = cand2
                        break
                if fixed is None:
                    cs = [tt for tt in range(lo, hi + 1) if tt % 12 in tset]
                    if cs:
                        fixed = min(cs, key=lambda tt: abs(tt - p))
                if fixed is not None:
                    p = fixed
            # ⑦ 强拍落弦内音（网格化前的最后一道，保留）
            if strong and p % 12 not in tset:
                cs = [tt for tt in range(lo, hi + 1) if tt % 12 in tset]
                if cs:
                    p = min(cs, key=lambda tt: (abs(tt - p), tt < p))

            # ⑧ **同一音高最多连续 2 次** —— 必须放在**所有修正之后**：⑥（调内 ±1）与
            # ⑦（强拍吸附到最近弦内音）都会把音拉回 prev，放在前面根本挡不住
            # （实测放前面时 33 号仍有 6 个连续同音）。画像的 `iv=0` 占 30~46% 是 F0
            # 在稳定音上连续出帧造成的放大，照抄就是"d d d d ddd"。
            if prev is not None and p == prev:
                same_run += 1
            else:
                same_run = 0
            if prev is not None and p == prev and not cad_tone and \
                    (same_run >= 2 or rng.random() < 0.65):
                if strong:                       # 强拍：仍要落弦内音，只在弦内音里换
                    pool = [tt for tt in range(lo, hi + 1)
                            if tt % 12 in tset and tt != prev]
                    if pool:
                        p = min(pool, key=lambda tt: abs(tt - prev))
                if p == prev:                    # 弱拍（或弦内音只有这一个）→ 用非零级进
                    nz = [(k, v) for k, v in P['near'] if k != 0] or [(-1, 1), (1, 1)]
                    step = _pick(nz, rng)
                    p = prev + step
                    if p < lo or p > hi:
                        p = prev - step
                    p = int(max(lo, min(hi, p)))
                same_run = 0

            out.append([bar, round(on - bar * SPB, 2), round(dur, 2), int(p)])
            # ⑨ **期待规则兜底**（Narmour 的 I-R 规则，必须放在**所有修正之后**）：
            # ⑤ 拱形漂移、⑥ 调内 ±1/±2、⑦ 强拍吸附、⑧ 同音去重**每一道都可能**把
            # "大跳后反向"改回同向 —— 实测多 seed 平均因此只有 55.7%（自检门 60%、
            # 真实模板 62~66%）。这里最后复查一次：只在大跳后同向时才动手，
            # 在音域内找最近的**反向**音（优先"回填"= 幅度小于跳进本身）。
            if not cad_tone and prev is not None and prev2 is not None:
                pv = prev - prev2
                if abs(pv) >= LEAP_IV and (p - prev) * pv > 0:
                    back = -1 if pv > 0 else 1

                    def _ok9(tt):
                        if not (lo <= tt <= hi):
                            return False
                        if mode_scale is not None and tt % 12 not in pcs:
                            return False
                        return (tt % 12 in tset) if strong else True
                    alt = [tt for tt in (prev + back * k for k in (1, 2, 3, 4, 5))
                           if _ok9(tt)]
                    fill = [tt for tt in alt if abs(tt - prev) < abs(pv)]
                    if alt:
                        p = min(fill or alt, key=lambda tt: abs(tt - p))
            out[-1][3] = int(p)
            prev2 = prev
            prev = int(p)
        t += plen

    # 去重（同一落点只留一个）+ 排序
    dedup = {}
    for e in out:
        dedup[(e[0], round(e[1], 4))] = e
    out = sorted(dedup.values(), key=lambda e: (e[0], e[1]))
    # 落盘前的兜底：只保证不短于 0.25 拍。
    # ⚠ 这里**曾经**是 `min(e[2], SPB - e[1])`（不许音出小节）—— 上面 ③ 放开跨小节后，
    # 这句又把长音重新截成"到小节末的长度"：落在 16 分格 15（3.75 拍，画像里很常见的方言落点）
    # 的音一律变 0.25 拍碎音。调试时最坑的一点是**打印在裁剪之前**，于是"生成时没有碎音、
    # 落盘却有 9 个"，白查了三轮。规则：裁剪必须与计算用同一套约束，别在出口处另立一套。
    for e in out:
        e[2] = max(0.25, e[2])

    # **出口不变量**（坑 114）：出口**只做上面那一件事**。下面两条是"改动有人把关"的防线 ——
    # 破了就当场炸，而不是留到渲染完靠耳朵发现。① 相邻落点间距 ≥0.5 拍（碎音）
    # ② 音不叠到下一个音（重叠会让 FluidSynth 留悬空 voice）。
    for i in range(len(out) - 1):
        gapv = (out[i + 1][0] - out[i][0]) * SPB + (out[i + 1][1] - out[i][1])
        assert gapv >= 0.5 - 1e-6, \
            'melody_gen 内部错：相邻落点只有 %.2f 拍（<0.5，会变成碎音）—— 查落点过滤' % gapv
        assert out[i][2] <= gapv + 1e-6, \
            'melody_gen 内部错：音长 %.2f > 到下一个音的距离 %.2f（重叠）' % (out[i][2], gapv)
    return out


# ────────────────────────── 去重筛选（对库里已有旋律） ──────────────────────────

def _abs_notes(d, melody):
    """{段落名: [(bar,beat,dur,pitch)]} → [(绝对拍, 时值, 音高)]"""
    pos, out = 0.0, []
    for sec in d['sections']:
        for (b, beat, dur, p) in (melody.get(sec['melody']) or []):
            if 0 <= b < sec['bars']:
                out.append((pos + b * SPB + beat, dur, p))
        pos += sec['bars'] * SPB
    out.sort()
    return out


def _windows(notes, w=8):
    """连续 w 个音的"形状"（音程 + 相对时值）—— 转调/变速不变"""
    out = set()
    for i in range(max(0, len(notes) - w)):
        seg = notes[i:i + w]
        out.add((tuple(seg[k + 1][2] - seg[k][2] for k in range(w - 1)),
                 tuple(round(seg[k + 1][0] - seg[k][0], 2) for k in range(w - 1))))
    return out


def _hists(notes):
    """5 组归一化分布（与 probe_melody_lang.py 同口径）"""
    if not notes:
        return None
    def h(vals, keys):
        v = [0.0] * len(keys)
        idx = {k: i for i, k in enumerate(keys)}
        for x in vals:
            if x in idx:
                v[idx[x]] += 1
        s = sum(v)
        return [q / s for q in v] if s else v
    ons = [int(round(n[0] * 4)) % 16 for n in notes]
    durs = [min(16, max(1, int(round(n[1] * 4)))) for n in notes]
    ivs = [max(-12, min(12, notes[i + 1][2] - notes[i][2]))
           for i in range(len(notes) - 1)]
    PHR = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]
    phrases, cur = [], 0.0
    for i, n in enumerate(notes):
        nxt = notes[i + 1][0] if i + 1 < len(notes) else n[0] + n[1]
        cur += n[1]
        if nxt - (n[0] + n[1]) > 0.5:
            phrases.append(cur / 4.0)
            cur = 0.0
    if cur > 0:
        phrases.append(cur / 4.0)
    return {'onset': h(ons, list(range(16))),
            'dur': h(durs, list(range(1, 17))),
            'iv': h(ivs, list(range(-12, 13))),
            'phrase': h([next((k for k in PHR if b <= k), 8.0) for b in phrases], PHR)}


def _distinct(mel_notes, lib):
    """与库里已有旋律的相似度 → (形状共享率, 语言重合度均值)。
    两个都低 = 这一首在库里"说话方式"最独特。"""
    w = _windows(mel_notes)
    f = _hists(mel_notes)
    shared = shape_sim = 0.0
    for lw, lf in lib:
        if w and lw:
            shape_sim = max(shape_sim, len(w & lw) / len(w))
        if f and lf:
            hs = []
            for k in f:
                if k in lf and sum(f[k]) and sum(lf[k]):
                    hs.append(sum(min(a, b) for a, b in zip(f[k], lf[k])))
            if hs:
                shared = max(shared, sum(hs) / len(hs))
    return shape_sim, shared


def _enforce_strong(mel, sec, chords, lo, hi):
    """写入前的**强拍复核**：第 1、3 拍必须落该小节和弦音。

    `gen_section` 内部已做（⑦），但实测会漏网（16 号 4 处 / 11 号 1 处 —— 首轮候选里
    仍有强拍音不在弦内）。这条纪律有独立守卫（`melody_chord_fit`），漏一个就是渲染前
    被拦下重来，所以宁可在写盘前再扫一遍。返回修正条数（>0 说明内部有洞，要记数）。"""
    fixed = 0
    ch = sec.get('chords') or []
    for e in mel:
        b, beat, _d, p = e
        if round(beat, 2) not in (0.0, 2.0) or b >= len(ch):
            continue
        entry = chords.get(ch[b])
        tones = sorted({t % 12 for t in (entry[1] if entry else [])})
        if not tones or p % 12 in tones:
            continue
        cs = [tt for tt in range(lo, hi + 1) if tt % 12 in tones]
        if cs:
            e[3] = min(cs, key=lambda tt: (abs(tt - p), tt < p))
            fixed += 1
    return fixed


def _count_strong_bad(mel, sec, chords):
    """数这支旋律在**另一个复用它的段落**里有几个强拍音不合弦（只数不改）。

    复用同名旋律是设计意图（A' 复用 A），但两个段落的和弦若不同，一支旋律不可能
    同时满足两边 —— 这种冲突必须**如实报出来**，不能静默留下一堆错音。"""
    n = 0
    ch = sec.get('chords') or []
    for e in mel:
        b, beat, _d, p = e
        if round(beat, 2) not in (0.0, 2.0) or b >= len(ch):
            continue
        entry = chords.get(ch[b])
        tones = {t % 12 for t in (entry[1] if entry else [])}
        if tones and p % 12 not in tones:
            n += 1
    return n


def load_lib(songs_dir, exclude=None):
    """库里已有旋律 → [(形状窗口集, 分布集)]，用于生成时去重"""
    lib = []
    for d in sorted(glob.glob(os.path.join(songs_dir, '*'))):
        p = os.path.join(d, 'song.json')
        if not os.path.isfile(p) or (exclude and os.path.abspath(d) == os.path.abspath(exclude)):
            continue
        try:
            j = json.load(open(p, encoding='utf-8'))
            if song_engine._norm_meter(j.get('meter')) != [4, 4]:
                continue
            n = _abs_notes(j, j['melody'])
        except Exception:
            continue
        if len(n) > 20:
            lib.append((_windows(n), _hists(n)))
    return lib


# ────────────────────────── 自检与入口 ──────────────────────────

def _accept(notes, prof):
    """生成结果 vs 画像：四个维度的**承接度**（旧版 落点42/时值60/音程63/句长2）"""
    f = _hists(notes)
    if not f:
        return {}
    def over(a, b):
        return 100.0 * sum(min(x, y) for x, y in zip(a, b)) if sum(a) and sum(b) else 0.0
    def nh(hist, keys):
        v = [0.0] * len(keys)
        idx = {k: i for i, k in enumerate(keys)}
        for k, c in (hist or {}).items():
            if int(k) in idx:
                v[idx[int(k)]] += c
        s = sum(v)
        return [q / s for q in v] if s else v
    return {
        '落点': over(f['onset'], nh(prof.get('onset16_hist'), list(range(16)))),
        '时值': over(f['dur'], nh(prof.get('dur16_hist'), list(range(1, 17)))),
        '音程': over(f['iv'], nh(prof.get('interval_hist'), list(range(-12, 13)))),
        '句长': over(f['phrase'], _hist_phrase(prof)),
    }


def _hist_phrase(prof):
    PHR = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]
    v = [0.0] * len(PHR)
    idx = {k: i for i, k in enumerate(PHR)}
    for b in (prof.get('phrase_bars') or []):
        v[idx[next((k for k in PHR if b <= k), 8.0)]] += 1
    s = sum(v)
    return [q / s for q in v] if s else v


def stepwise_pct(mel):
    """级进率 = |相邻音程| ≤ 2 半音 的占比（**只用于候选之间的相对排序，不当门槛**）。

    为什么不设门槛：画像的 `stepwise_pct` 是 F0 跟踪的产物（实测 45~89%），而手写曲的
    实际旋律只有 6~15%（见 `persona` 里那句注释）——两个口径不可比，拿画像值当门会把
    正常旋律判成不合格（我为此连推翻过三次自己的诊断）。

    但**同一次生成的多条候选之间**它是有序的：用户实测同一骨架的 4 条候选
    "级进 17% → 52% **越来越顺**，202 之后都比原版好"。所以它适合做**相对偏好**
    （`--step-bias`），而不是绝对判据。
    """
    iv = []
    for notes in (mel or {}).values():
        ns = sorted(notes, key=lambda x: (x[0], x[1]))
        iv += [abs(b[3] - a[3]) for a, b in zip(ns, ns[1:])]
    if not iv:
        return 0.0
    return sum(1 for x in iv if x <= 2) / float(len(iv))


def onset_tvd(mel, prof):
    """候选旋律的**落点格分布**与画像 `onset16_hist` 的总变差距离（0 = 与画像一致）。

    为什么加（用户 2026-09-15："全部都检查一下过渡问题，限制的条件也有可能出错"）：
    43 号 B/Outro 的落点集中在 3~4 个格（0.5 / 1.5 / 2.0 拍），而 ballad 组四首参考曲
    都是 6+ 个格 —— 听感上就是"整段一个节奏型、没有推进"。候选循环此前只看
    "与库里不像"（`_distinct`）与级进，**从没看过落点**。

    与 `stepwise_pct` 同一条纪律：**只当候选之间的相对排序，不当绝对门槛** ——
    画像的 `onset16_hist` 是十首模板聚合（sorrow 画像里 6/10 是古典钢琴），
    当门槛会把正常写法判死（我在 43 号上先按"倍率 ≥3"下过结论，后来用 TVD 口径推翻）。
    """
    h, pt = {}, 0.0
    for notes in (mel or {}).values():
        for x in notes:
            g = int(round(x[1] * 4)) % 16
            h[g] = h.get(g, 0) + 1
    tot = float(sum(h.values())) or 1.0
    P = (prof or {}).get('onset16_hist') or {}
    pt = float(sum(P.values())) or 1.0
    if not P:
        return 0.0
    keys = set(h) | {int(k) for k in P}
    return 0.5 * sum(abs(h.get(k, 0) / tot - (P.get(str(k), 0) / pt)) for k in keys)


def onset_tvd_worst(mel, sections, prof, min_notes=8):
    """**逐段**算落点 TVD，返回最坏的一段 —— 与守卫 `t_melody_onset_spread` **同源同门**。

    守卫是**逐段**判的（每段 notes vs 主题画像，门 `ONSET_TVD_MAX = 0.65`），而候选打分
    原来只看**全曲** TVD（见 `main` 里的 `ot = onset_tvd(mel, prof)`）—— 两者不同源：
    实测 `20_piano_rain` 的候选全曲只有 **0.199**（看着很好），可它的 Intro 段是 **0.679**
    （破门），**选择时压根没看见**，于是 4 条候选都带着这个毛病被挑进来。
    段内音数 < `min_notes` 的跳过（守卫也是这么做的，避免小样本虚高）。
    """
    worst = 0.0
    for sec in (sections or []):
        notes = (mel or {}).get(sec.get('melody')) or []
        if len(notes) < min_notes:
            continue
        worst = max(worst, onset_tvd({'_': notes}, prof))
    return worst


def dur_tvd(mel, prof):
    """**时值**分布与画像 `dur16_hist` 的 TVD（0 = 一致）。与 `onset_tvd` 同族、同纪律。

    为什么补这一维（2026-09-18）：`onset_tvd` 只管落点，候选打分里**没有时值项** ——
    于是同批候选里"时值分布更像画像"的那条不会被偏好。实测两首生成曲的时值承接度
    只有 **40% / 31%**（守卫 `melody_matches_profile` 的门是 40%），而它们的落点维
    是 78% / 79% —— **差的正是时值这一维**（画像多 0.25~0.5 拍短音，生成的是 0.5~1.5 拍）。

    ⚠ 桶口径必须与 `_hists`／守卫一致：`min(16, max(1, round(时值 × 4)))`（16 分格）。
    ⚠ 与 `onset_tvd` 一样**只当候选之间的相对排序，不当绝对门槛** —— 画像的
    `dur16_hist` 是 F0 跟踪量出来的"发声时长"，与 MIDI 的 note-off 不是同一个量
    （见 `_make_cell` 的说明），拿它当硬门会把正常写法判死。
    """
    h, tot = {}, 0.0
    for notes in (mel or {}).values():
        for x in notes:
            g = min(16, max(1, int(round(x[2] * 4))))
            h[g] = h.get(g, 0) + 1
    tot = float(sum(h.values())) or 1.0
    P = (prof or {}).get('dur16_hist') or {}
    pt = float(sum(P.values())) or 1.0
    if not P:
        return 0.0
    keys = set(h) | {int(k) for k in P}
    return 0.5 * sum(abs(h.get(k, 0) / tot - (P.get(str(k), 0) / pt)) for k in keys)


def select_candidate(scored):
    """**按"落点是否超门"分档挑候选**（2026-10-07）：门内候选一律优于门外候选，同档内按打分。

    `scored` = `[(score, over), ...]`；`over` = 该候选的**逐段最坏落点 TVD 是否超 `ONSET_TVD_MAX`**
    （0 = 门内）。返回下标。**门本身没动** —— 改的只是"挑哪条"。

    为什么必须分档（而不是继续用 `cand_score` 里的 6 倍超门罚）：**软罚会被别的项盖过**。
    实测 `107_mystery_door`（seed 6326，4 条候选）：候选 4 的 worst **0.656**（超门 0.006 ⇒
    只罚 0.036），却靠"形态罚 0.00（对手 0.24）+ 时值偏离 0.478（对手 0.564）"胜出被选中
    ⇒ 成品 Outro 破门，是全库 235 段里**唯一**一段破门。守卫 `t_melody_onset_spread` 是**硬门**，
    所以"在不在门内"必须先于任何别的维度比较。
    全候选都超门时退回纯打分（如实打印，不假装没事）—— 不改门限、不做豁免。

    抽成**模块级函数**的理由与 `cand_score` 相同：`mutation_check` 的注入机制是改内存里的
    模块属性，写在 `main()` 里就只能靠 subprocess 端到端验（守卫 `t_melody_candidate_gate`
    直接喂合成读数给这个函数）。
    """
    return min(range(len(scored)), key=lambda i: (scored[i][1], scored[i][0]))


# **落点分布的门**（守卫 `t_melody_onset_spread` 用的就是它 —— 单一真源，别在两处各写一份）
ONSET_TVD_MAX = 0.65


def same_run_longest(notes):
    """最长连续同音串（口径同 `probe_melody_health` 第③维；`notes` = [(拍, 时值, 音高)]）。

    2026-10-05 新增到候选打分里：节奏细胞层把落点换掉之后，`_enforce_strong` 吸附
    强拍音的对象跟着换，**同音串会长出来** —— 实测重做 100-108 时 `103` 4→**6**、
    `108` 3→**7**（`probe_melody_health` 的门是 ≤4，听感是"d d d d d d d"）。
    它只在**候选之间**排序（`cand_score(run_pen=…)`），不改生成逻辑。
    """
    best = cur = 0
    for i, n in enumerate(notes):
        cur = cur + 1 if (i and n[2] == notes[i - 1][2]) else 1
        best = max(best, cur)
    return best


def cand_score(shape_share, lang_share, clash, stepwise, step_bias, onset_dist=0.0,
               form_pen=0.0, dur_dist=0.0, run_pen=0.0, motif_pen=0.0):
    """候选打分（**越小越好**）：以"不像库里已有旋律"为主，级进偏好为次（opt-in）。

    抽成独立函数有两个原因：① `mutation_check` 的注入机制是**改内存里的模块属性**，
    而打分原先写在 `main()` 里、只能靠 subprocess 端到端验（注入打不进去）；
    ② 打分公式本身值得单独守住（它是"挑哪条候选"的唯一依据）。

    量级：`shape_share`/`lang_share` 是 0~1 的比例，`clash` 是计数 → 前两项和约 0~3，
    `step_bias × stepwise` 最多 1（`step_bias` 默认 1.0），所以级进偏好**不会盖过**
    "去重"这个主要目标，但足以在同分候选之间改变选择（实测 44% → 68%）。
    `onset_dist`（落点分布与画像的 TVD，0~1）同量级，理由见 `onset_tvd`。

    **`form_pen`（2026-09-18 新增，opt-in）**：把守卫一直在报的**形态判据**折算成罚分
    —— 末落点 `last8`、小节内空档 `maxgap_med`、格 0 占比 `g0`、以及**跳后反向率**。
    为什么必须加：候选原先只看"去重 + 级进 + 落点分散"，于是**选出来的那条形态可能一直
    不达标**（实测 18 首里 5 项守卫常年 FAIL：小步 38%、跳后反向 21~33%、空档 2.75 拍、
    末落点 50%）。这些都是**同一批候选里可比较**的量，接进打分即可 —— 不改生成逻辑，
    只改"挑哪条"，风险最小。`form_pen=0` 时与旧版逐字一致。
    """
    # **落点超门重罚**（2026-09-22）：守卫 `t_melody_onset_spread` 是**硬门**（每段 ≤ `ONSET_TVD_MAX`），
    #   而这里原来只把 TVD 当"同量级的一项"。实测那次：候选 1（最坏 0.432，达标）输给
    #   候选 2（最坏 **0.688**，破门），只因对方级进高 0.12 —— 结果成品 Outro 段 0.688 破门。
    #   落点破门是守卫会 FAIL 的硬伤，不该被"级进好一点"换掉 ⇒ 超门部分按 6 倍罚
    #   （**只影响候选之间的相对排序，门本身没动** —— 与 `stepwise_pct` 同一条纪律）。
    # ⚠ **但 6 倍罚是软的，照样会被别的项盖过**（2026-10-07 实测，当场抓到）：
    #   `107_mystery_door`（seed 6326）的候选 4 worst **0.656**（超门 0.006 ⇒ 只罚 0.036），
    #   靠"形态罚省 0.24 + 时值偏离省 0.086"胜出被选中 ⇒ 成品 Outro 破门
    #   （全库扫描 235 段里**唯一**一段）。⇒ 真正的把守是 `main` 里**按"是否超门"分档**
    #   （门内候选一律优于门外候选），这里只负责"门内候选之间"的次级排序。
    onset_pen = 6.0 * max(0.0, onset_dist - ONSET_TVD_MAX)
    return (shape_share * 2.0 + lang_share + clash * 0.5
            - step_bias * stepwise + onset_dist + onset_pen + form_pen + dur_dist + run_pen
            + motif_pen)


def small_step_pct(melody, sections, bar_beats=SPB):
    """**小步打转**占比：|音程| ≤ 1（同音或半音级进）的相邻音对比例。

    ⚠ 与 `stepwise_pct`（|iv| ≤ 2，**越高越顺**）是两个方向：
    `probe_melody_health.SMALL_IV_MAX = 35.0` 把"|iv|≤1 占比过高"判为问题，
    注释原文 —— **"这是 'd d d d ddd' 的真身"**（用户听到的正是这个）。
    所以这一项**越低越好**，进 `form_penalty` 时取罚分。
    """
    notes = []
    pos = 0.0
    for sec in sections:
        for (b, bt, _du, p) in (melody.get(sec.get('melody')) or []):
            if 0 <= b < sec['bars']:
                notes.append((pos + b * bar_beats + bt, p))
        pos += sec['bars'] * bar_beats
    notes.sort()
    ps = [p for (_t, p) in notes]
    if len(ps) < 2:
        return 0.0
    ivs = [abs(ps[i + 1] - ps[i]) for i in range(len(ps) - 1)]
    return sum(1 for x in ivs if x <= 1) / len(ivs)


def form_penalty(fs, ms, small=None, span=None):
    """形态罚分（0 = 全达标）。阈值与守卫同源：`FORM_MIN_LAST8 / FORM_MAX_GAP_MED /
    FORM_MAX_G0`（见 `selftest`）、跳后反向门 0.50（`melody_motif_rules`）、
    小步门 `SMALL_IV_MAX=0.35`、音域门 `FORM_SPAN_MIN=8`（`probe_melody_health` / `form_stats`）。"""
    pen = 0.0
    if fs:
        pen += max(0.0, 0.65 - fs.get('last8', 1.0)) * 3.0       # 末落点不够靠后
        pen += max(0.0, fs.get('maxgap_med', 0.0) - 1.70) * 0.8   # 小节内空档过大
        pen += max(0.0, fs.get('g0', 0.0) - 0.22) * 2.0           # 都砸第 1 拍
        sp = fs.get('span') if fs.get('span') is not None else span
        if sp is not None and sp < 8:                             # 音域太窄（15 号 7 半音）
            pen += (8 - sp) * 0.30
    la = (ms or {}).get('leap_after') or 0
    lr = (ms or {}).get('leap_reverse') or 0
    if la:
        pen += max(0.0, 0.50 - lr / la) * 2.0                     # 跳后不反向
    if small is not None:                                         # 小步打转（"d d d d ddd"）
        pen += max(0.0, small - 0.35) * 4.0
    return pen


def _hard_form_gates():
    """形态**硬门**的单一真源（懒加载，避免与 `selftest` 循环导入）。

    返回 `(FORM_DENS, small_iv_max_ratio)` —— 与守卫**同一份口径**：
    `selftest.FORM_DENS`（密度区间，用户口径 2.0~2.6 + 余量）与
    `probe_melody_health.SMALL_IV_MAX`（小步占比上限，百分数 → 这里换算成比例）。
    为什么要有它：候选选择要按这些门**分档**（见 `main` 里 `_over` 的说明），
    而门必须只有一处定义（PITFALLS 353：同一件事两处各算一份 ⇒ 必然漂移）。
    """
    import selftest as _st
    import probe_melody_health as _pm
    return tuple(_st.FORM_DENS), float(_pm.SMALL_IV_MAX) / 100.0


# ────────────────────────── 转音细胞（2026-10-06 · HANDOFF-ORNAMENT §4） ──────────
# 用户口径（2026-10-05）："以后其它地方有能识别到吗，推广一下让直接写音乐也能尝试写出来
#   **不同的**转音" —— 已知实例是 BGM35 19.0–19.6s 那处（`A♯5→A5→F5→C5→A♯4→A4→F4`，
#   每音约 0.1s；`songs/b35_clean/notes.md` 有全部读数）。
# 形态取自 §4-1：**3~5 音 · 总时长 0.3~0.8s · 同向级进为主（允许一步 ≤5 半音）·
#   音高只取该小节的和弦音/音阶音**。两条"不改别处"的纪律：§4-2 **不动骨架**（只把一个
#   长音**拆**成串：起点不变、总时值不变、下一个落点不变）· §4-3/4 **逐段决定**、跨曲不同
#   （同族纪律：驱动种子必须**曲名派生** —— `--seed` 会被"多首显式同一个 seed"抹平，
#   `PITFALLS` **304**）。
# ⚠ 预算只卡**现有门**，不新造口径：
#   · 密度 ≤ `ORN_DENS_HI`（= `selftest.FORM_DENS[1]` **2.9** 的生成时候选门；库里查 1.6~3.2）
#   · 0.25 拍音占比 ≤ `ORN_CHOP_CAP`（`probe_melody_health.MAX_CHOP` 是 **8%**，先按一半控）
#   —— 实测（`109_sunlit_desk` 原型）：不设预算会插 13 处、密度 2.63 → **5.22**（门 2.9，
#     碎音同时爆）⇒ 转音在整曲里本来就稀有，"有几处"是由这两条门**算出来的**，不是拍的。
ORN_STEP = 0.25                       # 细胞内部的音距（16 分）
ORN_NOTES = (3, 4, 5)                 # §4-1 的音数
ORN_DUR = (0.30, 0.80)                # §4-1 的总时长（秒）——**按秒**卡，别只看拍数
ORN_STEP_MAX = 5                      # §4-1 允许的一步上限（半音）
ORN_DENS_HI = 2.9                     # = selftest.FORM_DENS[1]（生成时候选门）—— 现在只当**参考门**
ORN_CHOP_CAP = 0.04                   # = probe_melody_health.MAX_CHOP(8%) 的一半 —— 只当**参考门**
# ⚠ 上面两个现在**不再参与决策**：真正的约束是"**置换池**"（插几个 0.25 拍的音就从同一条
#   旋律里删几个弱格装饰音 ⇒ 净音数与碎音数都不增）。留它们是给"想把门也改掉"的人一个参照。
ORN_TARGET_MAX = 8                    # 目标处数上限（B2 的"每小节处数 × 曲长"可能很大；池是硬约束）


def ornament_tendency(prof):
    """**B1 派生量**：这个主题的"转音倾向"（0~1）。

    ⚠ 这是**派生量** —— 把画像里"短时值多（`dur16_hist` 的 16/8 分占比）+ 级进多
    （`stepwise_pct`）+ 弱格多（`onset16_hist` 的奇数格占比）"三件事组合出来，
    **不是**"模板里的真实转音密度"。后者只能靠 B2（`fetch_midi_lib` 重抓 + 逐首量
    run 密度）拿到（`HANDOFF-ORNAMENT` §3）。所以这个数只用于"这首该多写几处"，
    **不许当成"模板实际有多少转音"引用**。
    """
    d = {int(k): float(v) for k, v in (prof.get('dur16_hist') or {}).items()}
    o = {int(k): float(v) for k, v in (prof.get('onset16_hist') or {}).items()}
    dt, ot = (sum(d.values()) or 1.0), (sum(o.values()) or 1.0)
    short = (d.get(1, 0.0) + d.get(2, 0.0)) / dt
    stepw = float(prof.get('stepwise_pct') or 50.0) / 100.0
    weak = sum(v for k, v in o.items() if k % 2 == 1) / ot
    return {'short': short, 'stepw': stepw, 'weak': weak,
            'tend': 0.40 * short + 0.35 * stepw + 0.25 * weak}


def ornament_density_of(profile):
    """**B2 模板直接量**：从 `refs/ornament_density.json` 取该主题的"真实转音密度"。

    为什么要有这条路（2026-10-06 实测）：原先只看 `ornament_tendency` 那个**派生量**，
    而交接文档写过"派生量与模板真实转音密度**差多少没人量过**"——现在量了：
    15 个主题上两者的 **Spearman 只有 0.12**（`scripts/ornament_density.py`）
    ⇒ 派生量**不能**当转音密度用。所以这里**优先**读模板直接量，读不到才退回 B1。
    """
    p = os.path.join(ROOT, 'refs', 'ornament_density.json')
    try:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return ((d.get('themes') or {}).get(profile) or {}).get('grids', {}).get('strict')


def _orn_pts(mode_scale, tonic, lo=55, hi=88):
    """该调式的可用音高（音阶音）。`mode_scale` 是半音级集合，`tonic` 是 pitch class。"""
    return [p for p in range(lo, hi + 1) if (p - tonic) % 12 in mode_scale]


def _orn_seq(pitch, n, pts, direction, chord_pcs):
    """从 `pitch` 出发的**同向级进**序列 · 只取音阶音 · 至少含一个和弦音。

    返回 None = 这个方向/t 音数构不出合规的串（宁可不插，也不插不和谐的）。
    """
    if pitch not in pts:
        return None
    i = pts.index(pitch)
    idx = [i + direction * k for k in range(n)]
    if idx[0] < 0 or idx[-1] >= len(pts):
        return None
    seq = [pts[j] for j in idx]
    if max(abs(b - a) for a, b in zip(seq[1:], seq[:-1])) > ORN_STEP_MAX:
        return None
    if chord_pcs and not any(p % 12 in chord_pcs for p in seq):
        return None                                     # 和谐优先（用户第一条口径）
    return seq


def _orn_expand(melody, sections):
    """`{键: notes}` 展开成"每段各算一遍"：同名旋律被多段复用时，它在音频里响几次就算几次。"""
    out = []
    for sec in sections:
        for nt in (melody.get(sec.get('melody')) or []):
            if 0 <= nt[0] < sec['bars']:
                out.append(nt)
    return out


def _orn_kill(notes, pool, i, beat, need):
    """选出要被**置换掉**的装饰音：离插入点最近的 `need` 个（听感上像"把花搬到这一串上"）。

    抽成独立函数是为了**能配变异用例**（`mutation_check`）：把它注入成"永远返回空表"
    就等于"只插不删"，`melody_health` 的碎音占比会被顶上去 —— 那条必须被 `t_ornament_cells` 抓到。
    """
    cand = [j for j in pool if j != i]
    return sorted(cand, key=lambda j: abs(notes[j][1] - beat))[:need]


def _orn_existing(melody, sections, bpm):
    """本曲**当前（还没插之前）**已经有几处 §4-1 形态的级进 run —— 用与 B2 **同一把尺子**量。

    为什么要有它（2026-10-06 实测）：目标处数原来是"按模板密度**追加**"，于是
    `108_carnival_party`（cheerful · 模板 0.018 处/小节）本来就自带 3 处，追加后到 **0.042**
    —— 超了模板值 2.3 倍。改成"**补齐差额**"（要的是"这首的总密度像模板"，不是"在模板之上再加"）。
    """
    import ornament_density as OD
    absn = sorted(_abs_notes({'sections': sections}, melody))
    seq = [(a, a + du, p) for (a, du, p) in absn]
    return len(OD.find_runs(seq, bpm or 120.0, 2, 3, 5, 0.30, 0.80))


def apply_ornaments(melody, sections, chords, prof, tonic, mode_of, rng, bpm=None,
                    profile=None, verbose=True):
    """全曲级：把**最合适的长音**拆成转音细胞（原地改 `melody`），返回报告 dict。

    `mode_of(sec)` → 该段的音阶集合（段落 `mode` 可覆盖）。`tonic` 是 pitch class。
    `profile`（主题名）：给了就**优先**读 `refs/ornament_density.json` 的
    **模板直接量（B2）** 来定目标处数；读不到才退回画像派生量（B1）。两条依据都进 `rep['basis']`。
    """
    rep = dict(inserted=[], tendency=round(ornament_tendency(prof)['tend'], 4),
               added=0, target=0, basis='', dens_before=None, dens_after=None,
               chop_before=None, chop_after=None)
    ex = _orn_expand(melody, sections)
    n_all = len(ex) or 1
    bars = sum(int(s['bars']) for s in sections) or 1
    dens0 = len(ex) / float(bars)
    chop0 = sum(1 for nt in ex if round(nt[2] * 4) <= 1) / float(n_all)
    rep['dens_before'], rep['chop_before'] = round(dens0, 3), round(chop0, 4)
    # **置换池**（2026-10-06 实测后重定）：本曲的碎音预算早被 `apply_rhythm_cells` 的弱格
    # 装饰用掉（109 的生成结果 **5.9% / 门 8%**）—— 再**净增**就破 `melody_health` 的碎音门，
    # 而"松门"是自创口径（不许）。装饰与转音**性质相同**（都是弱格上的花）⇒ 让转音**接管**
    # 这些位置：插 N 音就从**同一个键**里删掉 N-1 个 0.25 拍装饰音 ⇒ **净音数守恒**，
    # 密度与碎音占比**都不变**（逐项复核见 `rep` 的 before/after）。
    # 处数上限 = ① §4-4 的画像派生量（"这首该多花"）② 全曲装饰池够不够置换。
    # **目标处数**：优先**模板直接量（B2）**，读不到才退回画像派生量（B1）。
    # ⚠ 为什么改（2026-10-06 实测）：B1 与 B2 的 Spearman 只有 **0.12**（15 主题）⇒ 派生量
    #   不能当"转音密度"用（交接口径：`HANDOFF-ORNAMENT` §3）。**daily/folk_tale/seaside/
    #   tender/mystery/battle 六个主题的模板里**（严档）**一处都没有** ⇒ 那些主题目标 = 0
    #   （与 109 实测"置换池不够、插不进去"完全一致 —— 两条独立证据指向同一结论）。
    _dens2 = ornament_density_of(profile) if profile else None
    if _dens2:
        _rpb = float(_dens2.get('runs_per_bar') or 0.0)
        _want = int(round(_rpb * bars))
        _have = _orn_existing(melody, sections, bpm)
        target = max(0, _want - _have)          # **补齐差额**（不是"在模板之上再加"）
        rep['basis'] = ('B2 模板直接量（%s 严档 %.3f 处/小节 × %d 小节 = %d 处；'
                        '本曲自带 %d 处 ⇒ 补 %d 处）' % (profile, _rpb, bars, _want, _have, target))
    else:
        target = int(round(ornament_tendency(prof)['tend'] * 4.0))
        rep['basis'] = 'B1 画像派生量（派生量，非模板真实转音密度）'
    target = max(0, min(ORN_TARGET_MAX, target))
    rep['target'] = target
    if target <= 0:
        if verbose:
            print('  转音：依据「%s」⇒ 本主题目标 0 处（模板里本来就没这种跑动）' % rep['basis'])
        return rep
    spb = 60.0 / float(bpm or 120.0)                    # 一拍多少秒（§4-1 的时长按**秒**卡）
    # 候选：每个**键**里时值最长的非末音（末音 = 句末收束，一律不许动）；弱格优先
    ohist = {int(k): float(v) for k, v in (prof.get('onset16_hist') or {}).items()}
    cand, pools = [], {}
    for key, notes in melody.items():
        # ⚠ **别插在会被引子编配削掉的地方**（2026-10-06 读回验证抓到，不是推的）：
        #   带 `arr.intro_style` 的段，前 2 小节会被 `song_engine.shape_intro` 改写 ——
        #   `drums_first` 把**除鼓以外**的轨（含 Melody）第一小节整段删掉。实测
        #   `103_sorrow_letter` 的 intro 转音就是这么"写了但一个字没响"（MIDI 里查不到、
        #   音频里检不出）。只有**该键引用的每一段**都带 intro_style 时才回避。
        _secs_of = [s for s in sections if s.get('melody') == key]
        _intro_only = all((s.get('arr') or {}).get('intro_style') for s in _secs_of)
        # 可置换的装饰音：0.25 拍、**不是它所在小节的最后一个落点**
        # （保住"末落点 ≥8 格 ≥65%"那条守卫门）
        per_bar = {}
        for nt in notes:
            per_bar.setdefault(nt[0], []).append(int(round(nt[1] * 4)) % 16)
        pools[key] = []
        for j, nt in enumerate(notes):
            if round(nt[2] * 4) > 1 or j == len(notes) - 1:
                continue                      # 只拿 0.25 拍的装饰音；末音（句末收束）不动
            g = int(round(nt[1] * 4)) % 16
            rest = [x for x in (per_bar.get(nt[0]) or []) if x != g]
            # 删掉它之后该小节**仍要有 ≥8 格的落点**（`last8` 门 65%，当前 100% —— 有余量，
            # 但不拿它去赌）；小节里只剩它一个落点时也不删。
            if rest and max(rest) >= 8:
                pools[key].append(j)
        best = None
        for i, nt in enumerate(notes):
            if i == len(notes) - 1 or nt[2] < ORN_NOTES[0] * ORN_STEP:
                continue
            if _intro_only and nt[0] < 2:
                continue                      # 引子前 2 小节可能被 `shape_intro` 削掉
            w = ohist.get(int(round((nt[1] % 1) * 16)) % 16, 0.0)
            sc = nt[2] - 0.02 * w                       # 长音优先；落点越弱越优先
            if best is None or sc > best[0]:
                best = (sc, i)
        if best:
            cand.append((best[0], key, best[1]))
    cand.sort(reverse=True)
    used_key, added = set(), 0
    for _sc, key, i in cand:
        if key in used_key:
            continue                      # 一个键只插一处（同名旋律复用多次会累积）
        bar, beat, dur, pitch = melody[key][i][:4]
        # 音数 = 能拆得下、且**总时长落在 §4-1 的 0.3~0.8s** 的那个最大值
        n = 0
        for k in sorted(ORN_NOTES, reverse=True):
            if dur < k * ORN_STEP:
                continue
            if ORN_DUR[0] <= k * ORN_STEP * spb <= ORN_DUR[1]:
                n = k
                break
        if not n:
            continue
        if len(rep['inserted']) >= target:
            break
        # **碎音数守恒**（不只是净音数）：插入几个 0.25 拍的音就置换掉几个装饰音 ——
        # 否则 `melody_health` 的碎音占比会被推上去（实测 109：只 2 处就把 8.1% 顶到 9.6%）
        new_durs = [ORN_STEP] * (n - 1) + [round(dur - (n - 1) * ORN_STEP, 3)]
        need = sum(1 for d in new_durs if round(d * 4) <= 1)
        pool = [j for j in pools.get(key, []) if j != i]
        if len(pool) < need:
            continue          # 装饰池不够置换 ⇒ 这处不插（宁可不插，也不破碎音门）
        # 删离插入点最近的 need 个装饰音（"把花搬到这一串上"，听感最自然）
        kill = _orn_kill(melody[key], pool, i, beat, need)
        sec = [s for s in sections if s.get('melody') == key][0]
        cn = (sec.get('chords') or [None])[min(bar, len(sec.get('chords') or [1]) - 1)] \
            if sec.get('chords') else None
        cpcs = sorted({t % 12 for t in (chords.get(cn) or [[], []])[1]}) if cn else []
        pts = _orn_pts(mode_of(sec), tonic)
        seq = None
        for direction in ((1, -1) if rng.random() < 0.5 else (-1, 1)):
            seq = _orn_seq(pitch, n, pts, direction, cpcs)
            if seq:
                break
        if not seq:
            continue
        new = [[bar, round(beat + j * ORN_STEP, 3), new_durs[j], p]
               for j, p in enumerate(seq)]
        # **置换落地**：先算 keep 再整体重建（边删边移会把索引搞乱）
        kset = set(kill)
        keep = [j for j in range(len(melody[key])) if j not in kset]
        pos = keep.index(i)
        rebuilt = [melody[key][j] for j in keep]
        rebuilt[pos:pos + 1] = new
        melody[key] = rebuilt
        used_key.add(key)
        added += (n - 1) - len(kill)      # 相对"被替换的那一个音"的净增（替换本身不增）
        rep['inserted'].append(dict(key=key, bar=bar, beat=beat, n=n, dur=dur, seq=seq,
                                    total_sec=round(n * ORN_STEP * spb, 3),
                                    killed=len(kill),
                                    sections=[s['name'] for s in sections
                                              if s.get('melody') == key]))
    rep['added'] = added
    ex2 = _orn_expand(melody, sections)
    rep['dens_after'] = round(len(ex2) / float(bars), 3)
    rep['chop_after'] = round(sum(1 for nt in ex2 if round(nt[2] * 4) <= 1)
                              / float(len(ex2) or 1), 4)
    if verbose and rep['inserted']:
        print('  转音细胞 %d/%d 处（置换后净增 %d 音 · 密度 %.2f→%.2f · 0.25 拍占比 '
              '%.1f%%→%.1f%%）依据：%s'
              % (len(rep['inserted']), rep['target'], added, rep['dens_before'],
                 rep['dens_after'], 100 * rep['chop_before'],
                 100 * rep['chop_after'], rep['basis']))
        for r in rep['inserted']:
            print('    %-8s 小节%-3d %d 音 %.2f 拍（%.2fs）%s  ← 复用段 %s · 置换掉 %d 个装饰音'
                  % (r['key'], r['bar'], r['n'], r['dur'], r['total_sec'],
                     ' '.join(str(p) for p in r['seq']), '/'.join(r['sections']), r['killed']))
    elif verbose:
        print('  转音：目标 %d 处，但装饰池/合规拆分点不够 ⇒ 本曲不插（密度与碎音一字未动）'
              % rep['target'])
        for key in melody:
            _ns = len([s for s in sections if s.get('melody') == key])
            print('      键 %-8s 可置换装饰音 %d 个（该键被 %d 段复用 · 本键 %d 音）'
                  % (key, len(pools.get(key, [])), _ns, len(melody[key])))
    return rep


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    # 面板守卫（硬形式）：没在跑就先拉起来 —— 见 scripts/studio_guard.py 顶部那段
    import studio_guard
    studio_guard.ensure_panel()
    song, prof_path = sys.argv[1], sys.argv[2]
    seed = int(sys.argv[sys.argv.index('--seed') + 1]) if '--seed' in sys.argv else 7
    ncand = int(sys.argv[sys.argv.index('--candidates') + 1]) \
        if '--candidates' in sys.argv else 1
    avoid = sys.argv[sys.argv.index('--avoid') + 1] if '--avoid' in sys.argv else None
    # 级进偏好（opt-in；默认 0 = 与旧版逐字一致）。见 `stepwise_pct` 的说明：
    # 它只在**同批候选之间**排序，不是绝对门槛。
    step_bias = float(sys.argv[sys.argv.index('--step-bias') + 1]) \
        if '--step-bias' in sys.argv else 0.0
    # **时值偏好**（opt-in；默认 0 = 与旧版逐字一致）：把"时值分布与画像的 TVD"
    # 接进候选打分（见 `dur_tvd`）—— 落点维早有 `onset_tvd`，**时值维原先没人管**。
    dur_bias = float(sys.argv[sys.argv.index('--dur-bias') + 1]) \
        if '--dur-bias' in sys.argv else 0.0
    # **时值填充系数**（opt-in `--dur-fill`；缺省 = `CELL_DUR_FILL`，见 `_make_cell`）。
    # 何时用：画像的时值偏短、而 `need`（音至少覆盖到下一个落点的比例）把音统一拉长 →
    # 候选之间**没有差异**、`--dur-bias` 也就挑不出来。实测 07_hidden_door（画像 mystery
    # 44% 是 0.25 拍）时值承接度卡在 40%（门 40%）：0.45→0.35 升到 41%、→0.25 升到 54%。
    # ⚠ 代价是"音与下一个落点之间出现间隙"（`_make_cell` 的注释警告过 fill 的另一侧），
    #   所以它是**逐首 opt-in**，不做成默认。
    if '--dur-fill' in sys.argv:
        globals()['CELL_DUR_FILL'] = float(sys.argv[sys.argv.index('--dur-fill') + 1])
    prof = json.load(open(prof_path, encoding='utf-8'))
    d = json.load(open(song, encoding='utf-8'))
    # **拍号**：落点/时值/拱形全是按"一小节几拍、每拍 4 个十六分格"写的 —— 所以进生成前
    # 必须按本曲拍号把 `SPB`（一小节拍数）设对，否则会静默把音撒到小节外。
    # 2026-09-18 前这里**直接拒绝非 4/4**（"宁可拒绝，也不给错旋律"）。现在 3/4 放开
    # （用户要求：waltz 主题的 10 首模板全是 3/4）；**其余拍号仍拒绝** —— 强拍位置、
    # 句法都还没在那些拍号上量过，宁可拒绝也不给没验过的答案。
    meter = song_engine._norm_meter(d.get('meter'))
    if meter not in ([4, 4], [3, 4]):
        print('melody_gen 目前只支持 4/4 与 3/4（本曲 meter=%s）。'
              '其余拍号请手写 melody，或先用 3/4 试。' % meter)
        return 1
    set_meter(meter)
    print('  拍号 %s → 一小节 %.2f 拍' % (meter, SPB))
    chords = d['chords']
    names = list(d['melody'].keys())
    if '--tonic' in sys.argv:
        tonic = int(sys.argv[sys.argv.index('--tonic') + 1]) % 12
    else:
        try:
            tonic = chords[d['sections'][0]['chords'][0]][0] % 12
        except Exception:
            tonic = 0
    lib = load_lib(avoid, exclude=os.path.dirname(os.path.abspath(song))) if avoid else []
    # 目标密度：改过旋律的曲子沿用原旋律的密度；**新曲听画像**。
    # 为什么新曲不能默认 2.0：画像的密度就是它"呼吸"的一部分 —— BGM09 画像 1.51 音/小节、
    # 48% 是 2 拍长音；按 2.0 生成会把落点排密，2 拍音放不下、时值被 `nxt-on` 截成碎音
    # （实测跨拍时值 69%、"跨拍+反拍"56%，听感"镫镫卡着走"）。下限 1.2 防扒谱漏检写空，
    # 上限 2.6 防过密。
    bars_tot = sum(s['bars'] for s in d['sections'])
    old_n = len(_abs_notes(d, d['melody']))
    mgmeta = d.get('melody_gen') or {}
    if old_n > 20 and bars_tot and not mgmeta:
        # 手写旋律：沿用它的密度（尊重原设计）
        dens = old_n / bars_tot
    else:
        # 新曲，**或这条旋律本来就是生成器写的**（`melody_gen` 元数据在）：
        # 后者不能用"原旋律密度" —— 那等于把上一版的密度锁定下来，越重跑越偏
        # （实测：32 号第二版沿用第一版的 1.34 音/小节，56 小节只剩 75 个音）。
        # **口径（2026-09-14 改）**：密度目标 2.0~2.6 音/小节。旧版是
        # `notes_per_bar × 1.3`（上限 3.2）—— 而 `notes_per_bar` 是混音 F0 跟踪的
        # **上界估计**（一个持续音被连续帧量化成好几个音），照抄再放大实测把 37 号顶到
        # **3.41 音/小节**：音长被压短、每小节挤成一句，听感"呆板 + 说一句停一下"。
        # 下限 2.0 仍防"扒谱漏检写空"，上限 2.6 与动机图式（3 音铺满 + 句末 1 音）对齐。
        dens = max(2.0, min(DENS_MAX, (prof.get('notes_per_bar') or 2.0)))
    if '--dens' in sys.argv:       # 显式覆盖（改过旋律的曲子想按画像密度重写时用）
        dens = float(sys.argv[sys.argv.index('--dens') + 1])
    base_scale = infer_scale(d, tonic)
    use_motif = '--motif' not in sys.argv or \
        sys.argv[sys.argv.index('--motif') + 1] not in ('off', '0', 'none')
    # **节奏细胞**（opt-in，2026-09-22）：默认关 = 与旧版逐字一致；`new_song` 对新歌默认加。
    use_cells = '--rhythm-cells' in sys.argv

    # 同名旋律只生成一次：段落按名复用旋律是设计意图（A' 复用 A），
    # 若每个 section 都重新生成，最后一个会覆盖前面的、且拿别的段落和弦去对，必然打架。
    # ⚠ **用这个键里"最长的那一段"当模板**（2026-10-06 修，实测踩到）：原来取**第一次出现**，
    #   而首段常常是**最短**的那一段（如 `A` 首段 2 小节，其余 A/A2… 各 8 小节）——
    #   2 小节只生成出 3 个落点，这 3 个音被后面 8 个段**整段复用** ⇒ 全曲密度被首段钉死
    #   （实测 `114_soft_letter` 密度 **0.76**，把 `--dens` 从 1.3 提到 4.5 也只到 1.49 ——
    #   瓶颈不在 dens，在"用哪一段当模板"）。
    #   ⚠ 但**最长段的旋律会越出较短段**（`104_lounge_night` 的 `A` 首段 6 小节、模板按 8 小节
    #   生成 ⇒ 有音落在小节 6/7，引擎当场拒收："旋律的小节号必须是**段内**的"）。
    #   ⇒ 两半都要：**按最长段生成**（音多）＋ **复用到某个段落时丢弃越界小节**（下面 `_fit`）。
    refs = {}
    for si, sec in enumerate(d['sections']):
        refs.setdefault(sec['melody'], []).append(si)
    refs = {k: sorted(v, key=lambda i: (-int(d['sections'][i].get('bars') or 0), i))
            for k, v in refs.items()}

    def _fit(notes, bars):
        """把旋律放进"该段落的小节数"内（**循环折回**，不是丢弃）。

        为什么两半都要（2026-10-06 实测）：
          · **模板取最长段** —— 取第一次出现时，首段常是最短的（`114_soft_letter` 的 `A`
            首段只有 2 小节），2 小节只生成 3 个落点、再被 8 个段整段复用 ⇒ 全曲密度被钉死在
            **0.76**（`--dens` 从 1.3 提到 4.5 也只到 1.49）。
          · **复用段折回** —— 按最长段生成的音会落到较短段的小节号之外（`104_lounge_night`
            的 `A` 首段 6 小节），引擎当场拒收（"旋律的小节号必须是**段内**的"）。
            折回（`bar % bars`）保住音数、且不越界；同刻撞音才丢弃。
        """
        if not bars:
            return []
        out, seen = [], set()
        for n in notes:
            b = int(n[0]) % bars
            key = (b, round(float(n[1]), 4))
            if key in seen:
                continue
            seen.add(key)
            out.append([b] + list(n[1:]))
        return sorted(out, key=lambda x: (x[0], x[1]))

    cands = []                   # 逐候选收集：(score, over, mel, per, ci, nfix, clash, sw)
    _over_list = []              # 超门的候选（候选号, worst TVD, 打分），用于复盘打印
    _FD, _SIV = _hard_form_gates()   # 形态硬门（密度区间 / 小步上限）—— 单一真源，见其 docstring
    for ci in range(max(1, ncand)):
        rng = random.Random(seed + ci * 1000)
        per = persona(prof, rng)
        mel, nfix, clash = {}, 0, 0
        for key, idxs in refs.items():
            sec = d['sections'][idxs[0]]
            mode = sec.get('mode')
            scale = SCALE_DORIAN if mode == 'dorian' else (
                SCALE_MAJOR if mode == 'major' else (
                    SCALE_MINOR if mode == 'minor' else base_scale))
            # **一支旋律一个动机**：同名段落（A' 复用 A）本来就共用旋律，动机自然共用；
            # 不同的旋律名各自抽自己的动机（B 段是"另一句话"）。
            # 落点数：3 是常态（密度 ~2.5 音/小节，见 `_motif_cell`）；只有密度目标
            # 本身就密的主题（>2.8）才用 4 个落点。
            cmotif = _motif_cell(per, rng, bars=1,
                                 n=(4 if dens > CELL_N_DENSE else 3)) \
                if use_motif else None
            # **逐段旋律密度**（2026-10-05）：`--dens` 原来是**全局单值**（2.0~2.6），
            # 副歌不可能比主歌密。这里按该段的 `arr.density`（0~4）缩放，**上限守住
            # 形态门 2.9 音/小节**（dens 2.4 × 1.2 = 2.88）。缺 `arr.density` = 1.0。
            _g = SEC_DENS_GAIN.get(int((sec.get('arr') or {}).get('density', 2) or 0), 1.0)
            m = gen_section(sec, chords, prof, rng, scale, tonic, per, dens * _g, motif=cmotif,
                            dens_gain=_g)
            # 节奏细胞（opt-in `--rhythm-cells`；`new_song` 会给新歌默认带上）：
            # 必须在 `_enforce_strong` **之前**——换完落点，强拍上的音才由它统一核准。
            if use_cells:
                m = apply_rhythm_cells(m, prof=prof, rng=rng)
            nfix += _enforce_strong(m, sec, chords, per['range'][0], per['range'][1])
            # 复用同一支旋律的其它段落：和弦若不同就无法同时满足 → 计数（不静默）
            # ⚠ 这里落盘的是**按该键最长段生成的那一支**，并且已经 `_fit` 到**该段自己的小节数**
            #   （`sec` 就是 `idxs[0]`，即排序后的第一个 = 最长段）⇒ 不会越界。
            #   较短的同名段由 `song_engine.build_events` 逐段折回（见那边的 `_wrap_mel`）。
            for si in idxs[1:]:
                clash += _count_strong_bad(m, d['sections'][si], chords)
            m = _fit(m, int(sec.get('bars') or 0))
            # ⚠ **再按本键里"最短的那一段"收一遍**：`song_engine.load()` 在 `build_events`
            #   之前就逐段校验"旋律小节号 < 该段小节数"，所以只按最长段裁**过不了关**
            #   （实测 104 的 `A` 首段 6 小节，仍有 2 个音落在小节 6/7）。这里取
            #   `min(各段小节数)`，越界的音**丢弃**（不是折回 —— 折回留给 `build_events` 处理
            #   那些"刚落进短段"的音；落盘的数据本身必须落在最小段的界内）。
            _bars_min = min(int(d['sections'][i].get('bars') or 0) for i in idxs) or 0
            if _bars_min and _bars_min < int(sec.get('bars') or 0):
                m = [n for n in m if 0 <= int(n[0]) < _bars_min]
            mel[key] = m
        for sec in d['sections']:
            sec.pop('melody_extra', None)
        tmp = dict(d); tmp['melody'] = mel
        alln = _abs_notes(tmp, mel)
        sc = _distinct(alln, lib) if lib else (0.0, 0.0)
        sw = stepwise_pct(mel)
        ot = onset_tvd(mel, prof)          # 落点格分布与画像的距离（0 = 一致）
        # ⚠ **取"全曲 与 逐段最坏"的较大者**（2026-09-22 修）：守卫 `t_melody_onset_spread`
        #   是**逐段**判的（门 0.65），只算全曲会漏掉"落点全挤在某一段里"——
        #   实测 `20_piano_rain` 候选全曲 0.199（很好）而 Intro 段 **0.679**（破门），
        #   选择时看不见，4 条候选全带这个毛病。同源之后才会去挑落点真的分散的那条。
        ot = max(ot, onset_tvd_worst(mel, d.get('sections'), prof))
        # 形态判据（守卫同源）：末落点/空档/格0 + 跳后反向 → 折成罚分参与挑候选
        fs = form_stats(mel, d['sections']) or {}
        try:
            ms = motif_stats(mel, d['sections'], chords, tonic) or {}
        except Exception:                                        # noqa: BLE001
            ms = {}
        _sm = small_step_pct(mel, d['sections'])
        fp = form_penalty(fs, ms, small=_sm,
                          span=(fs or {}).get('span'))
        dt = dur_tvd(mel, prof) * dur_bias
        # **最长同音串罚分**（见 `same_run_longest` 的 docstring）：超门部分按 1.0/音罚。
        rl = same_run_longest(alln)
        rp = SAME_RUN_W * max(0, rl - SAME_RUN_MAX)
        # **动机层两条守卫判据也进排序**（2026-10-05）：`melody_motif_rules` 要求
        # 句末收束 ≥0.25、跳后反向 ≥0.60；而 `form_penalty` 只按 **0.50** 罚"跳后反向"、
        # 完全不看收束 —— 实测把旋律音域收到真实跨度后 103 的收束掉到 **3%**、
        # 107 反向掉到 **46%**，4 条候选全在门外而**打分看不见**（形态罚全是 0.00）。
        # 这里按**守卫同门**补两项，仍然只影响"挑哪条候选"。
        mp = (max(0.0, MOTIF_CAD_MIN - ((ms or {}).get('cadence_rate') or 0.0)) * 3.0
              + max(0.0, MOTIF_REV_MIN - ((ms or {}).get('leap_reverse_rate') or 0.0)) * 3.0)
        print('  候选 %d（seed=%d）：音符 %d  与库里最大形状共享 %.1f%%  语言重合 %.1f%%'
              '  级进 %.0f%%  小步 %.0f%%  落点偏离 %.3f  时值偏离 %.3f  形态罚 %.2f'
              '  同音串 %d（罚 %.2f）  收束 %.0f%%  反向 %.0f%%  动机罚 %.2f'
              '  强拍复核修正 %d  复用段冲突 %d'
              % (ci + 1, seed + ci * 1000, len(alln), sc[0] * 100, sc[1] * 100,
                 sw * 100, small_step_pct(mel, d['sections']) * 100, ot, dt, fp,
                 rl, rp, (ms or {}).get('cadence_rate', 0) * 100,
                 (ms or {}).get('leap_reverse_rate', 0) * 100, mp, nfix, clash))
        # 越小越好，见 `cand_score`：去重为主，级进/落点分散/时值/形态判据为次（都只对候选间排序）
        score = cand_score(sc[0], sc[1], clash, sw, step_bias, ot, fp, dur_dist=dt, run_pen=rp,
                           motif_pen=mp)
        # 旧挑法只等于 `score = sc[0]*2 + sc[1] + clash*0.5`（`step_bias=0` 时逐字一致）。
        # 用户在 2026-09-14 实测：同骨架 4 条候选"级进 17% → 52% 越来越顺，202 之后
        # 两条都比原版好"，而旧挑法完全不看听感维度 → 会随机挑到跳进多的那条
        # （根因还有 `persona` 里 `leap = uniform(0.70, 1.40)` 的两倍范围）。
        # **落点门是硬门，候选选择必须先按"是否超门"分档**（2026-10-07 修，见下）：
        #   守卫 `t_melody_onset_spread` 是**硬门**（每段 ≤ `ONSET_TVD_MAX`），而这里原先只有
        #   `cand_score` 里的"超门罚"（6 倍）——**软罚能被别的项盖过**。实测 `107_mystery_door`
        #   （seed 6326，4 条候选）：
        #       候选1 worst **0.541**（门内，形态罚 0.24，时值 0.564）
        #       候选4 worst **0.656**（**破门**，形态罚 0.00，时值 0.478）
        #   候选4 的超门罚只有 6×0.006 = **0.036**，被"形态罚省下 0.24 + 时值省下 0.086"
        #   轻易盖过 ⇒ 它被选中 ⇒ 成品 Outro 破门（全库扫描 235 段里**唯一**一段破门）。
        #   ⇒ 分档顺序：**门内候选一律优于门外候选**；门内一条都没有时才退回原打分
        #   （全破门的情况要打印出来，不能假装没事）。**门本身没动**，改的只是"挑哪条"。
        # **形态硬门也纳入分档**（2026-10-07 扩展；机制同 §18 的"落点门分档"）：
        #   `melody_health` 的小步门（≤35%）与 `melody_form_rules` 的密度门（`FORM_DENS`）
        #   都是**硬门**，而 `form_penalty` 里小步只按"4 倍超门量"罚（超 1 个百分点 = 0.04 分，
        #   随便被别的项盖过）、**密度根本没有罚分** ⇒ 实测选中了密度 **1.57** 的
        #   `102_waltz_court` 与小步 **36%** 的 `121_battle_onslaught`（都是守卫会 FAIL 的硬伤）。
        #   结论同 PITFALLS 352：**硬门必须分档**（门内候选一律优于门外候选），软罚不够。
        #   口径全部取自单一真源：`fs['dens']`（`form_stats`，守卫同款）与
        #   `_hard_form_gates()`（`selftest.FORM_DENS` / `probe_melody_health.SMALL_IV_MAX`）。
        _dens = (fs or {}).get('dens')
        _ov_form = 0
        if _dens is not None and not (_FD[0] <= _dens <= _FD[1]):
            _ov_form = 1
        if _sm > _SIV:
            _ov_form = 1
        _over = 1 if (ot > ONSET_TVD_MAX or _ov_form) else 0
        cands.append((score, _over, mel, per, ci, nfix, clash, sw))
        if _over:
            _over_list.append((ci + 1, ot, score))
    # **挑候选**（口径见 `select_candidate`）：门内候选一律优于门外候选，同档内按打分。
    _bi = select_candidate([(c[0], c[1]) for c in cands])
    _bc = cands[_bi]
    best = (_bc[0], _bc[2], _bc[3], _bc[4], _bc[5], _bc[6])
    best_over, best_sw = _bc[1], _bc[7]
    d['melody'] = best[1]
    # **转音细胞**（2026-10-06，HANDOFF-ORNAMENT §4）：全曲级 · 只在密度/碎音**预算**内插 ·
    # 逐段决定 · 种子**曲名派生**（同族纪律 `PITFALLS` **304**：`--seed` 会被"多首显式同一个
    # seed"抹平）。`--no-ornaments` 关掉它 —— A/B 要"同 seed、只差这一个维度"就用它。
    orn_rep = None
    if '--no-ornaments' not in sys.argv:
        import zlib
        _oname = os.path.basename(os.path.dirname(os.path.abspath(song)))
        _orn_rng = random.Random((zlib.crc32(_oname.encode('utf-8')) & 0xffffffff)
                                 ^ (seed * 2654435761) ^ 0x5EED)
        orn_rep = apply_ornaments(
            d['melody'], d['sections'], chords, prof, tonic,
            lambda s: {'minor': SCALE_MINOR, 'dorian': SCALE_DORIAN}.get(
                s.get('mode') or 'major', base_scale),
            _orn_rng, bpm=d.get('bpm'),
            profile=os.path.basename(prof_path).replace('_melody.json', ''))
    if use_cells:                     # 落点体检（守卫口径：on8 ≥ 85% / 弱格 ≤ 15%）
        for _k, _m in d['melody'].items():
            _o8, _wk, _ent = rhythm_cell_stats(_m)
            print('  ✓ 节奏细胞 %-6s on8 %.0f%% · 弱格 %.0f%% · 落点熵 %.2f'
                  % (_k, _o8, _wk, _ent))
    if step_bias:
        print('  ✓ 级进偏好 %.2f 生效：选中候选级进 %.0f%%（候选 %d 条里挑）'
              % (step_bias, best_sw * 100, max(1, ncand)))
    # **落点门分档留痕**（2026-10-07）：把"这条旋律是门内挑的、还是全候选破门被迫挑的"打印出来
    # （不写进 song.json：那是"引擎的挑选过程"，不是曲目数据 —— 要查看生成日志）。
    if _over_list:
        _in_gate = max(1, ncand) - len(_over_list)
        print('  %s 硬门（落点 %.2f · 密度 %.1f~%.1f · 小步 ≤%.0f%%）：%d/%d 条候选在门内 ⇒ '
              '选中候选%s（超门候选：%s）'
              % ('!' if best_over else '·', ONSET_TVD_MAX, _FD[0], _FD[1], _SIV * 100,
                 _in_gate, max(1, ncand),
                 '**全是超门的**' if best_over else '在门内',
                 ' · '.join('候选%d %.3f' % (a, b) for (a, b, _s) in _over_list)))
    # **生成元数据**：写进 song.json，让"这首该像哪份画像"变成可查的事实 ——
    # 自检 `melody_matches_profile` 靠它决定查谁，人复盘时也不必翻 notes（复现会漂移）。
    d['melody_gen'] = {
        'profile': os.path.basename(prof_path).replace('_melody.json', ''),
        'seed': seed,
        'dens': round(dens, 3),
        'candidates': max(1, ncand),
        # v2 动机层：`mode='motif'` 的曲子才有"动机重复/期待规则/终止式"这三层，
        # 自检 `melody_motif_rules` 只对这类曲子判结构层判据（旧曲不受影响）。
        'mode': 'motif' if use_motif else 'histogram',
        # **变体层标记**（2026-09-14）：这一版动机不再是"每小节复刻同一 figure"，
        # 而是逐小节铺变体（移位/压缩/扩展/稀疏长音）。自检 `melody_form_rules`
        # 只对带这个标记的曲目判"形态层"判据（旧曲的形态是历史数据，不追溯）。
        'variants': bool(use_motif),
        # **级进偏好留痕**：>0 才写，方便复盘"这条旋律是挑了级进高的那条"。
        # 默认 0 时不写字段 → 旧曲的 song.json 逐字节不变。
        **({'step_bias': step_bias} if step_bias else {}),
        # **转音细胞留痕**（2026-10-06）：插了几处 / 净增几音 / 依据是什么，全部写进 song.json
        # —— 让"这条旋律的转音是引擎**有意**写的"变成**可查事实**（`--no-ornaments` 时不写字段，
        # 于是关掉它生成的 song.json 与旧版逐字节一致）。
        **({'ornaments': {'cells': len(orn_rep['inserted']), 'added': orn_rep['added'],
                          'tendency': orn_rep['tendency'], 'target': orn_rep['target'],
                          'dens': [orn_rep['dens_before'], orn_rep['dens_after']],
                          # **逐处位置**（键/小节/拍/音数/音高序列/落在哪些段）：验收要按窗剪片段、
                          # 将来若要"把转音从碎音统计里排除"也只有这里能查得到 —— 别只留一个总数。
                          'at': [{'key': r['key'], 'bar': r['bar'], 'beat': r['beat'],
                                  'n': r['n'], 'seq': r['seq'], 'sections': r['sections']}
                                 for r in orn_rep['inserted']],
                          'basis': orn_rep['basis']}}
           if orn_rep and orn_rep['inserted'] else {}),
    }
    # **`--dry-run`**（2026-09-20 加，实测事故后）：这个脚本是**写盘**工具 ——
    # 它的 `json_io.save` 会把 `song.json` 的 `melody` **整段换掉**（手写的引子/尾声、
    # 逐段微调过的音、变奏加音全部不再存在），而且**不报错**。
    # 现场：把它当"只读诊断"跑（想复现 `melody_step_bias` 的非零退出），当场覆盖了
    # 一首已定稿曲目的 5 支旋律；`git` 没追踪那首曲子，只能从覆盖前渲染的 MIDI 抢救
    # （`tools/restore_melody_from_midi.py`）。
    # 所以：**只想看数字就加 `--dry-run`** —— 它把结果打印出来但不落盘。
    if '--dry-run' in sys.argv:
        print('\n[dry-run] **没有写盘**（旋律结果只在内存里）。当前 song.json 的旋律会是：')
        for _k, _v in d['melody'].items():
            _old = (json.load(open(song, encoding='utf-8')).get('melody') or {}).get(_k) or []
            print('  %-8s 新 %3d 音 / 盘上现有 %3d 音' % (_k, len(_v), len(_old)))
        print('  想真写盘：去掉 --dry-run 重跑（会覆盖上面列出的每一支旋律）。')
        return 0
    json_io.save(song, d)
    # **落盘重读复核**（坑 114）：一律不信生成过程中的统计 —— 出口处可能还有第二套裁剪。
    # 实测那次正是"内存里没有碎音、落盘有 9 个"，绕三轮才找到出口那句 min()。
    chk = json.load(open(song, encoding='utf-8'))
    for _k, _v in d['melody'].items():
        if chk['melody'].get(_k) != _v:
            print('  !! 落盘数据与生成结果不一致（旋律 %s）—— 出口处有第二套裁剪，'
                  '先查它，别去调生成参数' % _k)
            return 1

    notes = _abs_notes(d, d['melody'])
    tot = fit = 0
    for sec in d['sections']:
        for (b, beat, _dur, m) in d['melody'].get(sec['melody'], []):
            if beat in (0.0, 2.0) and b < len(sec.get('chords') or []):
                tones = [t % 12 for t in chords[sec['chords'][b]][1]]
                tot += 1
                fit += 1 if m % 12 in tones else 0
    iv = [notes[i + 1][2] - notes[i][2] for i in range(len(notes) - 1)]
    ons = [int(round(n[0] * 4)) % 16 for n in notes]
    use = best[2]['pers']
    print('生成完毕（seed=%d，%d 条候选取第 %d 条）：%d 个段落旋律段，%d 个音'
          % (seed, max(1, ncand), best[3] + 1, len(names), len(notes)))
    print('  个性：切分 %.2f / 跳进 %.2f / 句长 %.2f / 拱形 %.1f'
          % (use['syncop'], use['leap'], use['phrase'], use['arch']))
    print('  %-10s %8s %8s' % ('承接度', '画像', '生成'))
    print('  %-10s %8s %8.1f' % ('每小节音符', '%.2f' % prof['notes_per_bar'],
                                 len(notes) / max(1.0, sum(s['bars'] for s in d['sections']))))
    print('  %-10s %8.0f %8.0f' % ('级进占比%', prof.get('stepwise_pct', 0),
                                   100.0 * sum(1 for x in iv if abs(x) <= 2) / max(1, len(iv))))
    print('  %-10s %8.0f %8.0f' % ('正拍占比%', prof.get('onbeat_pct', 0),
                                   100.0 * sum(1 for k in ons if k % 4 == 0) / max(1, len(ons))))
    print('  %-10s %8s %8s' % ('音域', '%d-%d' % tuple(prof['range']),
                               '%d-%d' % (min(n[2] for n in notes), max(n[2] for n in notes))))
    ac = _accept(notes, prof)
    if ac:
        print('  承接度（生成结果保住画像方言的比例）：' +
              '  '.join('%s %.0f%%' % (k, v) for k, v in ac.items()))
    print('强拍和弦贴合：%d/%d = %.0f%%（写入前复核修正 %d 处；复用段冲突 %d）'
          % (fit, tot, 100.0 * fit / max(1, tot), best[4], best[5]))
    if use_motif:
        st = motif_stats(d['melody'], d['sections'], chords, tonic)
        if st:
            print('  结构层（动机/期待/终止式）：节奏动机重复 %.0f%% · 大跳 %d 处（跳后反向 %.0f%%、'
                  '其中回填 %.0f%%）· 句末收束 %.0f%%（落根音/主音 %.0f%%）'
                  % (st['rhythm_repeat'] * 100, st['leap_after'],
                     st['leap_reverse_rate'] * 100, st['gap_fill_rate'] * 100,
                     st['cadence_rate'] * 100, st['cadence_root_rate'] * 100))
    fs = form_stats(d['melody'], d['sections'])
    if fs:
        print('  形态层（铺满小节）：末落点≥8 格的小节 %.0f%%（真实模板 79~90%%）· '
              '小节内最大空档中位 %.2f 拍（真实 1.0~1.4）· 格 0 占比 %.0f%%（真实 13~15%%）'
              % (fs['last8'] * 100, fs['maxgap_med'], fs['g0'] * 100))
    print('  音阶：%s（按本曲和弦推断；段落 mode 可覆盖）'
          % ('大调' if base_scale is SCALE_MAJOR else '小调'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
