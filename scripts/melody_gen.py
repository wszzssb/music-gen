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

SCALE_MINOR = [0, 2, 3, 5, 7, 8, 10]          # 自然小调
SCALE_MAJOR = [0, 2, 4, 5, 7, 9, 11]          # 自然大调
SCALE_DORIAN = [0, 2, 3, 5, 7, 9, 10]         # 多利亚（B 段色彩）
SPB = 4.0                                     # 只支持 4/4：一小节 4 拍、16 个十六分格
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


def gen_section(sec, chords, prof, rng, mode_scale, tonic, per=None, dens=None,
                motif=None):
    """给一个段落生成旋律：返回 [(bar, beat, dur, pitch)]

    `tonic` 是**主音的音级**（0-11），由调用方按该曲的和弦推断 —— 以前这里硬编码
    `tonic = 7`（G），换成 D 小调就会把多利亚判定整错。
    `per` 是说话方式（`persona()` 产出）；不传则按 prof 现算。
    `dens` 是这首曲子的目标密度（音/小节，通常沿用原旋律）。**画像的 `notes_per_bar`
    不能直接当密度**：它来自混音 F0 跟踪，漏检严重（实测 BGM33 只有 1.09 音/小节，
    而那是首 75BPM 的舞曲）—— 所以密度服从曲目本身，画像只提供**相对**的长短/落点/走向。"""
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
                bar_v[bb] = v
                for off in frag:
                    on = bb * SPB + off / 4.0
                    if t - 1e-9 <= on < end - 1e-9:
                        ons.append(round(on, 4))
        ons_us = sorted(set(ons))
        ons = ons_us
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


def cand_score(shape_share, lang_share, clash, stepwise, step_bias, onset_dist=0.0,
               form_pen=0.0):
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
    return (shape_share * 2.0 + lang_share + clash * 0.5
            - step_bias * stepwise + onset_dist + form_pen)


def form_penalty(fs, ms):
    """形态罚分（0 = 全达标）。阈值与守卫同源：`FORM_MIN_LAST8 / FORM_MAX_GAP_MED /
    FORM_MAX_G0`（见 `selftest`）与跳后反向门 0.50（`melody_motif_rules`）。"""
    pen = 0.0
    if fs:
        pen += max(0.0, 0.65 - fs.get('last8', 1.0)) * 3.0      # 末落点不够靠后
        pen += max(0.0, fs.get('maxgap_med', 0.0) - 1.70) * 0.8  # 小节内空档过大
        pen += max(0.0, fs.get('g0', 0.0) - 0.22) * 2.0          # 都砸第 1 拍
    la = (ms or {}).get('leap_after') or 0
    lr = (ms or {}).get('leap_reverse') or 0
    if la:
        pen += max(0.0, 0.50 - lr / la) * 2.0                    # 跳后不反向
    return pen


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    song, prof_path = sys.argv[1], sys.argv[2]
    seed = int(sys.argv[sys.argv.index('--seed') + 1]) if '--seed' in sys.argv else 7
    ncand = int(sys.argv[sys.argv.index('--candidates') + 1]) \
        if '--candidates' in sys.argv else 1
    avoid = sys.argv[sys.argv.index('--avoid') + 1] if '--avoid' in sys.argv else None
    # 级进偏好（opt-in；默认 0 = 与旧版逐字一致）。见 `stepwise_pct` 的说明：
    # 它只在**同批候选之间**排序，不是绝对门槛。
    step_bias = float(sys.argv[sys.argv.index('--step-bias') + 1]) \
        if '--step-bias' in sys.argv else 0.0
    prof = json.load(open(prof_path, encoding='utf-8'))
    d = json.load(open(song, encoding='utf-8'))
    # **拍号守卫**：落点/时值/拱形全是按"一小节 4 拍、16 个十六分格"写的 ——
    # 非 4/4 时会静默把音撒到小节外。宁可拒绝，也不给错旋律。
    meter = song_engine._norm_meter(d.get('meter'))
    if meter != [4, 4]:
        print('melody_gen 目前只支持 4/4（本曲 meter=%s）。'
              '引擎侧已支持非 4/4 编配，但**旋律生成器还没适配** —— '
              '请手写 melody，或先把 meter 改成 [4,4]。' % meter)
        return 1
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

    # 同名旋律只生成一次：段落按名复用旋律是设计意图（A' 复用 A），
    # 若每个 section 都重新生成，最后一个会覆盖前面的、且拿别的段落和弦去对，必然打架。
    refs = {}
    for si, sec in enumerate(d['sections']):
        refs.setdefault(sec['melody'], []).append(si)

    best = None
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
            m = gen_section(sec, chords, prof, rng, scale, tonic, per, dens, motif=cmotif)
            nfix += _enforce_strong(m, sec, chords, per['range'][0], per['range'][1])
            # 复用同一支旋律的其它段落：和弦若不同就无法同时满足 → 计数（不静默）
            for si in idxs[1:]:
                clash += _count_strong_bad(m, d['sections'][si], chords)
            mel[key] = m
        for sec in d['sections']:
            sec.pop('melody_extra', None)
        tmp = dict(d); tmp['melody'] = mel
        alln = _abs_notes(tmp, mel)
        sc = _distinct(alln, lib) if lib else (0.0, 0.0)
        sw = stepwise_pct(mel)
        ot = onset_tvd(mel, prof)          # 落点格分布与画像的距离（0 = 一致）
        # 形态判据（守卫同源）：末落点/空档/格0 + 跳后反向 → 折成罚分参与挑候选
        fs = form_stats(mel, d['sections']) or {}
        try:
            ms = motif_stats(mel, d['sections'], chords, tonic) or {}
        except Exception:                                        # noqa: BLE001
            ms = {}
        fp = form_penalty(fs, ms)
        print('  候选 %d（seed=%d）：音符 %d  与库里最大形状共享 %.1f%%  语言重合 %.1f%%'
              '  级进 %.0f%%  落点偏离 %.3f  形态罚 %.2f  强拍复核修正 %d  复用段冲突 %d'
              % (ci + 1, seed + ci * 1000, len(alln), sc[0] * 100, sc[1] * 100,
                 sw * 100, ot, fp, nfix, clash))
        # 越小越好，见 `cand_score`：去重为主，级进/落点分散/形态判据为次（都只对候选间排序）
        score = cand_score(sc[0], sc[1], clash, sw, step_bias, ot, fp)
        # 旧挑法只等于 `score = sc[0]*2 + sc[1] + clash*0.5`（`step_bias=0` 时逐字一致）。
        # 用户在 2026-09-14 实测：同骨架 4 条候选"级进 17% → 52% 越来越顺，202 之后
        # 两条都比原版好"，而旧挑法完全不看听感维度 → 会随机挑到跳进多的那条
        # （根因还有 `persona` 里 `leap = uniform(0.70, 1.40)` 的两倍范围）。
        if best is None or score < best[0]:
            best = (score, mel, per, ci, nfix, clash)
            best_sw = sw
    d['melody'] = best[1]
    if step_bias:
        print('  ✓ 级进偏好 %.2f 生效：选中候选级进 %.0f%%（候选 %d 条里挑）'
              % (step_bias, best_sw * 100, max(1, ncand)))
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
    }
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
