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
"""

import glob
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
    P = {'range': (min(lo + 2, 74), max(hi - 1, 80))}

    # ① 落点方言（16 分格，0=小节第 1 拍）
    #   keep 用 0.92（比其他维度宽）：实测 0.80 会把**格 0/8（第 1、3 拍）**这种
    #   "频次不是最高、但音乐上必须有"的落点当长尾滤掉 —— 13 号因此生成出一条
    #   完全没有正拍音的旋律。另外补一道"第 1 拍保底"。
    ons = _top_hist(prof.get('onset16_hist'), 0.92)
    if ons and not any(k == 0 for k, _w in ons):
        ons.append((0, max(1, min(w for _k, w in ons))))
    P['onsets'] = sorted(ons) or [(0, 3), (4, 3), (8, 2), (12, 2)]

    # ② 时值方言（16 分格 → 拍）
    P['durs'] = [(k / 4.0, v) for k, v in _top_hist(prof.get('dur16_hist'), 0.85)]
    if not P['durs']:
        P['durs'] = [(0.5, 3), (1.0, 3), (2.0, 2)]

    # ③ 音程方言：同音重复 / 级进 / 跳进，各自的比例与方向都听画像的
    ivh = {int(k): v for k, v in (prof.get('interval_hist') or {}).items() if v > 0}
    near = [(k, v) for k, v in ivh.items() if abs(k) <= 2]
    # **同音重复（iv=0）要设上限**：F0 跟踪在每个稳定音上连续出帧，量化后全变成"同音重复"，
    # 画像里的 0 因此被系统性放大 —— 照抄会写出 64% 同音重复的旋律（实测 23 号），
    # 听感等于原地踏步。上限 30%（BGM33 画像本身约 35%，是真实区间）。
    tot_n = sum(v for _k, v in near)
    zero_n = sum(v for k, v in near if k == 0)
    if tot_n and zero_n > 0.30 * tot_n:
        sc0 = 0.30 * tot_n / zero_n
        near = [(k, v * sc0 if k == 0 else v) for k, v in near]
    # 跳进档要**双过滤**：① 只留 3~7 半音 —— 画像里 ±8 以上的多是 F0 八度误判
    # （实测 hitorigohan2 画像含 ±11/±12，照抄会生成 20 处 >7 半音的大跳，而旧版只有 6 处，
    # 旋律立刻失去歌唱性）② 再取累计 85% 的高频档滤长尾。
    leap = [(k, v) for k, v in ivh.items() if 3 <= abs(k) <= 7]
    leap = _top_hist(dict(leap), 0.85) if leap else []
    P['near'] = near or [(-2, 1), (-1, 2), (0, 2), (1, 2), (2, 1)]
    P['leap'] = leap or [(-4, 1), (-3, 1), (3, 1), (4, 1)]
    P['step_w'] = min(0.92, max(0.25, prof.get('stepwise_pct', 55) / 100.0
                               + rng.uniform(-jitter, jitter)))

    # ④ 句长方言（小节）—— 句末休止由此而来（旧版从不休止，句长承接度只有 2%）
    phr = [b for b in (prof.get('phrase_bars') or []) if b > 0][:32]
    P['phr'] = phr or [1.0, 1.0, 2.0, 2.0, 3.0]

    # ⑤ 个性（用户口径：切分比例 / 跳进率 / 乐句长度 / 拱形幅度，每首随机取一组）
    P['pers'] = {
        'syncop': rng.uniform(0.80, 1.30),   # >1 更爱反拍与切分
        'leap': rng.uniform(0.70, 1.40),     # 跳进率缩放（除到级进概率上）
        'phrase': rng.uniform(0.70, 1.40),   # 乐句长度缩放
        'arch': rng.uniform(1.5, 6.5),       # 每句拱形幅度（半音）
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


def gen_section(sec, chords, prof, rng, mode_scale, tonic, per=None, dens=None):
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
    same_run = 0                       # 连续同音计数（上限 2，见下面 ⑤b）
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
        plen = _pick_phrase_bars(P, rng) * SPB
        plen = max(2.0, min(plen, total - t))
        rest = rng.choice([0.5, 1.0, 1.0, 1.5]) if plen >= 3.0 else 0.25
        end = t + plen - rest
        # ② 拱形：这一句要走到哪（幅度每首不同）
        start_ref = prev if prev is not None else rng.choice(
            [lo + 4, (lo + hi) // 2, hi - 4])
        aim = start_ref + rng.choice([-1, 1]) * pers['arch']

        # ①b 落点：句内按**目标密度**分段，每段从画像的方言格里抽一个落点。
        #     密度服从曲目（`dens`，默认沿用原旋律），落点形状服从画像 ——
        #     旧版让"抽到的时值"决定推进速度，慢曲画像的长音会把句腹掏空（1 音/小节）。
        nb = plen / SPB
        n_t = max(2, int(round(nb * (dens or 2.0))))
        seg = (end - t) / n_t
        ons = []
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
        ons = sorted(set(ons))
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

        for oi, on in enumerate(ons):
            nxt = ons[oi + 1] if oi + 1 < len(ons) else end
            bar = int(on // SPB)
            # ③ 时值：听画像（不吸附到全局 GRID）；不叠到下一个音、不出句。
            # **允许延音跨小节** —— 早先这里还有个 `SPB-(on-bar*SPB)`（不许出小节），
            # 于是落在 3.75 拍（16 分格 15，画像里很常见的方言落点）的音一律被截成
            # **0.25 拍**：实测 12% 的音变成十六分碎点，听感"镫镫"地卡（用户反馈）。
            # 画像里这种末格落点本来是"延续到下一小节"的长音。跨小节只影响这一口气的长短，
            # 不改变音高（音高仍按落点所在小节的和弦判定）。
            raw = _pick(P['durs'], rng)
            cap = dur_cap * 1.5 if oi + 1 == len(ons) else dur_cap    # 句末才允许长音
            dur = max(0.25, round(min(raw, cap, nxt - on, end - on) * 4) / 4)
            if os.environ.get('MELODY_DEBUG'):
                print('    on=%.2f nxt=%.2f end=%.2f raw=%.2f cap=%.2f 段末%s dur=%.2f%s'
                      % (on, nxt, end, raw, cap, oi + 1 == len(ons), dur,
                         '   ← 碎音' if dur <= 0.25 else ''))

            # ④ 音高
            tset = chord_tones(bar)
            strong = abs((on - bar * SPB) - round(on - bar * SPB)) < 1e-6 and \
                int(round(on - bar * SPB)) % 2 == 0          # 第 1、3 拍
            if prev is None:
                cand = [tt for tt in range(lo, hi + 1) if tt % 12 in tset]
                p = rng.choice(cand) if cand else rng.choice(
                    [lo + 4, (lo + hi) // 2, hi - 4])
            elif strong:
                cand = [tt for tt in range(prev - 3, prev + 4)   # 窗口 ±3：别为凑弦内音跳四五度
                        if tt % 12 in tset and lo <= tt <= hi]
                p = rng.choice(cand) if cand else prev
            else:
                stepw = max(0.20, min(0.95, P['step_w'] / pers['leap']))
                if rng.random() < stepw:
                    step = _pick(P['near'], rng)
                else:
                    step = _pick(P['leap'], rng)
                p = prev + step
                if p < lo or p > hi:                          # 越界折返
                    p = prev - step
                # ⑤ 拱形：向本句的目标轨迹漂移（旧版是纯随机游走 → 没有句形）
                prog = (on - t) / max(1e-6, end - t)
                tgt = start_ref + (aim - start_ref) * prog
                p = int(round(p * 0.72 + tgt * 0.28))
            p = int(max(lo, min(hi, p)))
            # ⑥ 调内 + 半音回避（纪律，保留）
            if mode_scale is not None and p % 12 not in pcs:
                p += 1 if rng.random() < 0.5 else -1
                if p % 12 not in pcs:
                    p -= 2 if p > 0 else -1
            if clash(tset, p):
                fixed = None
                for cand2 in (p - 1, p + 1, p - 2, p + 2):
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
            if prev is not None and same_run >= 2:
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


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    song, prof_path = sys.argv[1], sys.argv[2]
    seed = int(sys.argv[sys.argv.index('--seed') + 1]) if '--seed' in sys.argv else 7
    ncand = int(sys.argv[sys.argv.index('--candidates') + 1]) \
        if '--candidates' in sys.argv else 1
    avoid = sys.argv[sys.argv.index('--avoid') + 1] if '--avoid' in sys.argv else None
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
        # 也不能照抄画像值：画像的 `notes_per_bar` 来自混音 F0 跟踪、系统性偏低
        # （BGM16 画像 0.90 音/小节）。→ 画像值 × 1.3，下限 2.0。
        dens = max(2.0, min(3.2, (prof.get('notes_per_bar') or 2.0) * 1.3))
    if '--dens' in sys.argv:       # 显式覆盖（改过旋律的曲子想按画像密度重写时用）
        dens = float(sys.argv[sys.argv.index('--dens') + 1])
    base_scale = infer_scale(d, tonic)

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
            m = gen_section(sec, chords, prof, rng, scale, tonic, per, dens)
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
        print('  候选 %d（seed=%d）：音符 %d  与库里最大形状共享 %.1f%%  语言重合 %.1f%%'
              '  强拍复核修正 %d  复用段冲突 %d'
              % (ci + 1, seed + ci * 1000, len(alln), sc[0] * 100, sc[1] * 100,
                 nfix, clash))
        score = sc[0] * 2.0 + sc[1] + clash * 0.5     # 复用段冲突要付出代价
        if best is None or score < best[0]:
            best = (score, mel, per, ci, nfix, clash)
    d['melody'] = best[1]
    # **生成元数据**：写进 song.json，让"这首该像哪份画像"变成可查的事实 ——
    # 自检 `melody_matches_profile` 靠它决定查谁，人复盘时也不必翻 notes（复现会漂移）。
    d['melody_gen'] = {
        'profile': os.path.basename(prof_path).replace('_melody.json', ''),
        'seed': seed,
        'dens': round(dens, 3),
        'candidates': max(1, ncand),
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
    print('  音阶：%s（按本曲和弦推断；段落 mode 可覆盖）'
          % ('大调' if base_scale is SCALE_MAJOR else '小调'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
