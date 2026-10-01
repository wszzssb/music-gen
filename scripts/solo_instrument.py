#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""solo_instrument.py —— **单乐器独奏化**：把一首曲子的全部声部改成"只用一件指定乐器演奏"。

## 什么时候用

用户口径："**把一首曲子的 MIDI 提取出来之后，完全用指定乐器演奏**（比如完全用钢琴）"。
输入 = 曲目目录里的 `song.json`（扒带/还原产物，或生成曲），输出 = **新曲目目录**
（默认 `songs/<原名>_solo[_<乐器>]/`），产物仍由引擎渲染 → 照样有 `.mid` + `_sf.ogg` + 成绩单。

## 为什么不是"把 programs 全改成 0"就完了（每条都是实测依据，不是推测）

| 只换音色会踩的坑 | 本工具的处理 | 依据 |
|---|---|---|
| 钢琴/拨弦靠**衰减**收尾，而垫子层写死一小节 = **4.1 拍长音** → 听感"音符延长得太奇怪" | `--max-beats`（默认 2.0 拍）裁长短音 | PITFALLS **219** 第 1 条 |
| 低音落在 24–45（E1 起）在 GM 钢琴上只有"嗡" | 低音区整理到 **≥33**（A1 起） | PITFALLS **219** 第 3 条 |
| 跨轨同刻同音高（两条轨都变钢琴 = 同一个音弹两遍）→ 音源发浑 | 同刻同音高**去重**（保留优先级 Melody > 主轨 > 其余） | SKILL §15（实测 148 处） |
| **鼓轨在通道 10 上 `program` 根本不生效** —— 改成钢琴也还是鼓声 | 鼓 → 乐器音型（默认 `--drums piano`）：kick→低音区根音 · snare→中音区和弦音 · hat→高音区轻点 | GM 规范 + 本工具 `--selftest` |
| 引擎还会按 `arr` 生成 pad/strings/glock 层 → 独奏版里凭空多出内容 | 全部音符**冻结**进 `notes_extra`，`arr` 生成层全关（`notes_extra_full: true`） | `song_engine.build_events` 的覆盖语义 |
| Bass 轨用钢琴音色会被守卫拦（且与钢琴左手撞成一片） | 低音**并入主轨**（钢琴左手就是钢琴），不保留 `Bass` 轨 | 守卫 `t_bass_timbre_is_low` + PITFALLS 219 |

**旋律轨（`melody` 字段）不动**：它继续由引擎按段生成，只是换成目标乐器音色 ——
这样 `t_melody_health` 那批守卫仍然有效，也不丢旋律的乐句力度（`patterns.melody_dyn`）。

## 用法

```bash
python scripts\solo_instrument.py <曲目> [--instrument piano] [--out 新曲名]
       [--drums piano|drop|keep] [--max-beats 2.0] [--track Piano] [--keep-mix]
       [--no-render] [--dry] [--selftest]
# 例：
python scripts\solo_instrument.py dear_good_friends                  # → songs\dear_good_friends_solo\
python scripts\solo_instrument.py bgm35_extract --instrument strings # → songs\bgm35_extract_solo_strings\
python scripts\solo_instrument.py siren_end --drums drop --no-render # 只看数据，不渲染
```

⚠ **`--track` 只是"通道容器"**：引擎按轨名分通道与音域（`song_engine.CH` / `TR_RANGE`），
与音色无关。用 `Bass` 当主轨名会撞上守卫 `t_bass_timbre_is_low`（它只认低音乐器音色）。
"""
import argparse
import copy
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()          # noqa: E402
import json_io                               # noqa: E402
import song_engine as se                     # noqa: E402

SONGS = os.path.join(ROOT, 'songs')

# ---------------------------------------------------------------- 目标乐器
# 别名 → (GM program, 显示名)。想用数字就直接给 GM 号（0–127）。
INSTRUMENTS = {
    'piano': (0, 'Acoustic Grand 钢琴'), 'bright': (1, 'Bright 亮钢琴'),
    'ep': (4, 'Electric Piano 1 电钢琴'), 'honky': (3, 'Honky-tonk'),
    'harpsi': (6, 'Harpsichord 大键琴'), 'clav': (7, 'Clavinet'),
    'celesta': (8, 'Celesta 钢片琴'), 'glock': (9, 'Glockenspiel 钟琴'),
    'musicbox': (10, 'Music Box'), 'vibes': (11, 'Vibraphone 颤音琴'),
    'marimba': (12, 'Marimba'), 'organ': (19, 'Church Organ 管风琴'),
    'guitar': (24, 'Nylon Guitar 尼龙弦'), 'steel': (25, 'Steel Guitar 钢弦'),
    'jazzguitar': (26, 'Jazz Guitar'), 'clean': (27, 'Clean Guitar'),
    'harp': (46, 'Harp 竖琴'), 'strings': (48, 'String Ensemble 弦乐'),
    'slowstrings': (51, 'Slow Strings'), 'choir': (52, 'Choir Aahs 人声合唱'),
    'violin': (40, 'Violin 小提琴'), 'cello': (42, 'Cello 大提琴'),
    'pizz': (45, 'Pizzicato'), 'trumpet': (56, 'Trumpet'), 'brass': (61, 'Brass'),
    'sax': (65, 'Alto Sax'), 'flute': (73, 'Flute 长笛'), 'clarinet': (71, 'Clarinet'),
    'oboe': (68, 'Oboe'), 'pan': (75, 'Pan Flute'), 'saw': (81, 'Saw Lead'),
    'square': (80, 'Square Lead'), 'sine': (79, 'Sine Lead'),
    'bell': (14, 'Tubular Bells 管钟'), 'sitar': (104, 'Sitar'),
}

# ---------------------------------------------------------------- 鼓件分类
DRUM_KIND = {35: 'kick', 36: 'kick', 38: 'snare', 40: 'snare', 37: 'snare', 39: 'snare',
             42: 'hat', 44: 'hat', 46: 'hat',
             49: 'cymbal', 57: 'cymbal', 52: 'cymbal', 55: 'cymbal',
             51: 'cymbal', 59: 'cymbal', 53: 'cymbal',
             41: 'tom', 43: 'tom', 45: 'tom', 47: 'tom', 48: 'tom', 50: 'tom'}
# 各类的落点音区与力度系数（(音区下限, 上限), vel 系数, 时值拍）
DRUM_PLAN = {'kick': ((33, 45), 1.00, 0.55),
             'snare': ((55, 67), 0.85, 0.35),
             'hat': ((76, 88), 0.45, 0.22),
             'cymbal': ((79, 91), 0.80, 1.00)}
# **每拍最多留几个**（钢琴独奏里鼓只当"节奏骨架"，全留会变成打击乐）
# 依据：`dear_good_friends` 的 906 个鼓事件若全转，是 9 音/小节的打击织体 —— 那不是钢琴。
DRUM_PER_BEAT = {'kick': 1, 'snare': 1, 'hat': 1}
DRUM_PER_BAR = {'cymbal': 1}

# 去重优先级：越小越该保留（旋律 > 其余并入的轨 > 鼓转写）。
# 并入主轨的那些轨之间**不再分先后** —— 合并后它们已经是同一条钢琴轨上的音。
PRIO = {'Melody': 0}
SRC_MERGED, SRC_DRUM, SRC_FILL = 2, 3, 4


def parse_instrument(s):
    """'piano' / '48' / 'strings' → (program, 显示名)；不认识就报错（别静默给钢琴）"""
    t = str(s).strip().lower()
    if t.isdigit():
        p = int(t)
        if not 0 <= p <= 127:
            raise SystemExit('GM 音色号要在 0–127：%s' % s)
        return p, 'GM %d' % p
    if t not in INSTRUMENTS:
        raise SystemExit('不认识的乐器 %r；可用别名：%s（或直接给 GM 音色号 0–127）'
                         % (s, ', '.join(sorted(INSTRUMENTS))))
    return INSTRUMENTS[t]


def bar_chord_table(d):
    """全局小节号 → (根音, 和弦音集, 和弦名)；段落 `chords` 数必须 = bars（引擎同款契约）"""
    out = []
    for sec in d.get('sections') or []:
        names = sec.get('chords') or []
        for i in range(int(sec.get('bars') or 0)):
            cn = names[i] if i < len(names) else (names[-1] if names else None)
            ch = (d.get('chords') or {}).get(cn)
            if not ch:
                out.append((None, [], cn))
            else:
                out.append((int(ch[0]), [int(x) for x in ch[1]], cn))
    return out


def _nearest(tones, center, lo, hi):
    """和弦音里最接近 center 的、且能靠八度挪进 [lo,hi] 的音（挪不进就 None）"""
    best, bestd = None, None
    for t in tones:
        p = int(t)
        while p < lo:
            p += 12
        while p > hi:
            p -= 12
        if not (lo <= p <= hi):
            continue
        dd = abs(p - center)
        if bestd is None or dd < bestd:
            best, bestd = p, dd
    return best


def drums_to_notes(drum_ev, ctab, used_keys, opts):
    """鼓事件 → 乐器音型。返回 (notes, stats)。

    `used_keys` = 已占用的 (16 分格, 音高) 集合（**避让既有音**：撞了就换和弦音，都撞就丢）。
    稀疏化两道：① 每拍每类最多 `DRUM_PER_BEAT` 个（留力度最大的）② 每小节 cymbal 最多 1 个。
    """
    stats = {'in': len(drum_ev), 'out': 0, 'drop_kind': 0, 'drop_thin': 0, 'drop_collide': 0,
             'by_kind': {}}
    if not drum_ev:
        return [], stats
    # ① 分类
    items = []
    for (t, dur, pitch, vel) in drum_ev:
        k = DRUM_KIND.get(int(pitch))
        if k is None or k == 'tom':                       # tom/未登记件：钢琴上不做 fill
            stats['drop_kind'] += 1
            continue
        items.append([float(t), k, int(vel), int(pitch)])
    # ② 每拍每类留一个（力度最大）；cymbal 每小节一个
    picked = []
    by_beat = {}
    for it in items:
        by_beat.setdefault((it[1], int(it[0] // 1.0)), []).append(it)
    for (kind, _beat), lst in sorted(by_beat.items()):
        lim = DRUM_PER_BEAT.get(kind)
        if lim is None:
            continue
        lst.sort(key=lambda x: -x[2])
        picked.extend(lst[:lim])
        stats['drop_thin'] += max(0, len(lst) - lim)
    # cymbal 单独口径：**每小节**最多一个（`DRUM_PER_BEAT` 里没有它）
    by_bar = {}
    for it in items:
        if it[1] == 'cymbal':
            by_bar.setdefault(int(it[0] // 4.0), []).append(it)
    for _b, lst in sorted(by_bar.items()):
        lim = DRUM_PER_BAR.get('cymbal', 1)
        lst.sort(key=lambda x: -x[2])
        picked.extend(lst[:lim])
        stats['drop_thin'] += max(0, len(lst) - lim)
    # ③ 落到和弦音上（撞音就换候选，换不动就丢）
    out = []
    for (t, kind, vel, _src) in sorted(picked, key=lambda x: x[0]):
        bar = int(t // 4.0)
        root, tones, _cn = ctab[bar] if 0 <= bar < len(ctab) else (None, [], None)
        if not tones:
            stats['drop_collide'] += 1
            continue
        (lo, hi), vk, dd = DRUM_PLAN[kind]
        cands = []
        if kind == 'kick':
            p = int(root)
            while p < lo:
                p += 12
            while p > hi:
                p -= 12
            cands = [p] + [x for x in (_nearest(tones, p + 7, lo, hi),
                                       _nearest(tones, p - 5, lo, hi)) if x]
        else:
            center = 60 if kind == 'snare' else (82 if kind == 'hat' else 85)
            n = _nearest(tones, center, lo, hi)
            cands = [x for x in (n, _nearest(tones, center + 4, lo, hi),
                                 _nearest(tones, center - 3, lo, hi)) if x]
        hit = None
        for c in cands:
            if (int(round(t * 4)), int(c)) not in used_keys:
                hit = c
                break
        if hit is None:
            stats['drop_collide'] += 1
            continue
        used_keys.add((int(round(t * 4)), int(hit)))
        out.append((float(t), float(dd), int(hit), int(max(1, min(127, round(vel * vk))))))
        stats['by_kind'][kind] = stats['by_kind'].get(kind, 0) + 1
    stats['out'] = len(out)
    return out, stats


def dedupe(notes, tol=0.1):
    """同音高 + **时间很近**去重 —— 保留优先级高、其次力度大的那个。

    ⚠ **只去 Δ0（严格同音高）**：低音区 Δ1/Δ2 的"打架"可能是真实二度和声，一律不动
    （口径同 `unison_guard.py`：Δ2 只报不删）。

    ⚠ **容差不能用 16 分格**：80BPM 下一格 = **187ms**，而钢琴上同音高相隔 1/16 音符的
    重复是**真实演奏**（实测第一版按格判重，`dear_good_friends` 删了 277 音 / 15%）。
    改为**按音高分组 + 时间邻域聚类**，默认容差 `0.1` 拍（80BPM ≈ 75ms；转录的时间抖动
    在这个量级以内，真实的重复敲击在它之上），`--dedupe-tol` 可调。

    聚类用**簇首**（不是簇内最新的音）比较：链式比较会把一串间隔 0.09 拍的重复音
    一路并成一簇（渐进吞并），簇首比较最多只吃掉"真的挤在一起"的那几个。
    """
    byp, dropped = {}, 0
    for n in notes:
        byp.setdefault(int(n[2]), []).append(n)
    out = []
    for _p, lst in byp.items():
        lst.sort(key=lambda x: x[0])
        cluster = [lst[0]]
        for n in lst[1:]:
            if n[0] - cluster[0][0] < tol:
                cluster.append(n)
            else:
                out.append(sorted(cluster, key=lambda x: (x[4], -x[3]))[0])
                dropped += len(cluster) - 1
                cluster = [n]
        out.append(sorted(cluster, key=lambda x: (x[4], -x[3]))[0])
        dropped += len(cluster) - 1
    out.sort(key=lambda x: (x[0], x[2]))
    return out, dropped


def _chord_pcs(ctab, bar):
    """`bar_chord_table` 的一项 → `(根音, 音级集合)`。

    ⚠ **必须取 `% 12`**：`bar_chord_table` 给的是**绝对音高**（如 `[60, 64, 67]`），
    直接拿 `pitch % 12 in tones` 比会**永远不命中** → 打分里所有音都被当成和弦外音
    （实测踩到：特征保留率打出"和弦内音 **0%**"这种一眼假的数）。
    """
    if 0 <= bar < len(ctab):
        root, tones, _cn = ctab[bar]
        return (int(root) if root is not None else None,
                {int(y) % 12 for y in tones})
    return None, set()


def _note_score(x, ctab, B=4.0):
    """**音乐重要性打分**（越大越该留）—— 这是"聚集关键特征"的全部依据。

    ⚠ 为什么不能按几何选音（2026-10-01 用户："感觉不行，没有聚集关键特征 …… 反而是留下了
    一些错误或不重要的音"）：第一版按"左手取最低音 / 右手取离旋律最近"削，实测（`diag_thin`）：

    | 保留率 | 和弦内音 | 和弦外音 | 踩拍点 | 非踩拍点 |
    |---|---|---|---|---|
    | 第一版几何削音 | **51.3%** | **54.1%**（无区分） | **39.2%** | **56.1%**（反向！） |

    即：和声上没做取舍、还**专挑踩拍点的特征音删**。这一版按属性打分：
    和弦内音 **+3**（其中根音音级再 +1）· 和弦外音 **−2** · 踩拍点 **+1.5**（第 1、3 拍再 +0.5）·
    长音（≥1 拍）**+1** · 力度微调 ±0.5 · **鼓写音 −1.5**（转录音才是原曲内容，鼓写是这一版新造的）。
    """
    t, dur, p, v = float(x[0]), float(x[1]), int(x[2]), int(x[3])
    src = x[4] if len(x) > 4 else SRC_MERGED
    bar = int(t // B)
    beat = t - bar * B
    root, pcs = _chord_pcs(ctab, bar)
    s = 0.0
    if pcs and p % 12 in pcs:
        s += 3.0
        if root is not None and p % 12 == root % 12:
            s += 1.0
    else:
        s -= 2.0
    if abs(beat - round(beat)) < 1e-6:
        s += 1.5
        if int(round(beat)) in (0, 2):
            s += 0.5
    if dur >= 1.0:
        s += 1.0
    s += max(-0.5, min(0.5, (v - 95) / 60.0))
    if src == SRC_DRUM:
        s -= 1.5
    elif src == SRC_FILL:
        s += 1.5          # 段界过渡音：新造的，但位置/音高都最不可替代（别被可弹化先删掉）
    return s


def feature_keep(kept, alln, ctab, B=4.0):
    """各类音的**保留率**（key → [留下, 总数]）—— 报告里的"关键特征保住了吗"读数"""
    keys = {(round(x[0], 6), int(x[2])) for x in kept}
    tot, kep = {}, {}
    for x in alln:
        bar = int(x[0] // B)
        beat = x[0] - bar * B
        root, pcs = _chord_pcs(ctab, bar)
        tags = ['in_chord' if (pcs and int(x[2]) % 12 in pcs) else 'off_chord',
                'strong' if abs(beat - round(beat)) < 1e-6 else 'weak',
                'trans' if (len(x) <= 4 or x[4] != SRC_DRUM) else 'drum']
        for tag in tags:
            tot[tag] = tot.get(tag, 0) + 1
            if (round(x[0], 6), int(x[2])) in keys:
                kep[tag] = kep.get(tag, 0) + 1
    return {k: [kep.get(k, 0), tot[k]] for k in tot}


def make_playable(notes, mel_at, ctab, lv, split, B=4.0):
    """把主轨事件削成**双手可弹、且保住关键特征**的版本。

    `notes` = `[[t, dur, pitch, vel, src]]` · `mel_at` = `{16分格: {旋律音高}}`（不计入主轨但要占位）
    · `ctab` = 逐小节 `(根音, 和弦音集)`。

    两段式（**先硬约束、再软约束**，全程按 `_note_score` 排序 —— 先删"最不重要"的）：
      ① **物理硬约束**（每时刻）：单手同时按键 ≤ `rh`/`lh` · 单手跨度 ≤ `span`（八度）；
      ② **难度软约束**：每拍按键 ≤ `beat`（旋律先占位）· 同音连击 ≤ `run` ·
         同手相邻跳进 ≤ `jump`（先 ±12 靠近，仍超才删）。
    """
    st = {'in': len(notes), 'drop_hand': 0, 'drop_beat': 0, 'drop_run': 0,
          'drop_jump': 0, 'octave': 0, 'drop_off_chord': 0}
    at = {}
    for x in notes:
        at.setdefault(int(round(float(x[0]) * 4)), []).append(
            [float(x[0]), float(x[1]), int(x[2]), int(x[3]),
             x[4] if len(x) > 4 else SRC_MERGED])
    kept = []
    for g in sorted(at):
        items = sorted(at[g], key=lambda x: -_note_score(x, ctab, B))
        mels = sorted(mel_at.get(g, ()))
        lh = [x for x in items if x[2] < split]
        rh = [x for x in items if x[2] >= split]
        keep_lh = []
        for x in lh:
            if len(keep_lh) >= int(lv['lh']):
                break
            ps = [y[2] for y in keep_lh] + [x[2]]
            if max(ps) - min(ps) > int(lv['span']):
                continue
            keep_lh.append(x)
        keep_rh = []
        room = max(0, int(lv['rh']) - len(mels))
        for x in rh:
            if len(keep_rh) >= room:
                break
            ps = [y[2] for y in keep_rh] + [x[2]] + mels
            if max(ps) - min(ps) > int(lv['span']):
                continue
            keep_rh.append(x)
        dropped = lh[len(keep_lh):] + rh[len(keep_rh):]
        st['drop_hand'] += len(dropped)
        for x in dropped:
            _root, pcs = _chord_pcs(ctab, int(x[0] // B))
            if not pcs or x[2] % 12 not in pcs:
                st['drop_off_chord'] += 1
        kept.extend(keep_lh + keep_rh)
    # ② 每拍上限（同样按重要性删）
    by_beat = {}
    for x in kept:
        by_beat.setdefault(int(x[0]), []).append(x)
    out = []
    for b, lst in by_beat.items():
        n_mel = sum(len(mel_at.get(gg, ())) for gg in range(b * 4, b * 4 + 4))
        room = max(0, int(lv['beat']) - n_mel)
        if len(lst) <= room:
            out.extend(lst)
            continue
        lst.sort(key=lambda x: -_note_score(x, ctab, B))
        for x in lst[room:]:
            _root, pcs = _chord_pcs(ctab, int(x[0] // B))
            if not pcs or x[2] % 12 not in pcs:
                st['drop_off_chord'] += 1
        out.extend(lst[:room])
        st['drop_beat'] += len(lst) - room
    # ③ 同音连击串
    out.sort(key=lambda x: (x[0], x[2]))
    res, run, lastp = [], 1, None
    for x in out:
        if x[2] == lastp:
            run += 1
            if run > int(lv['run']):
                st['drop_run'] += 1
                continue
        else:
            run = 1
        lastp = x[2]
        res.append(x)
    # ④ 单手跳进（先 ±12 靠近，仍超就删）
    prev = {'L': None, 'R': None}
    final = []
    for x in res:
        hand = 'L' if x[2] < split else 'R'
        pv = prev[hand]
        if pv is not None and 0 < x[0] - pv[0] <= 0.5 and abs(x[2] - pv[1]) > int(lv['jump']):
            cand = None
            for sh in (12, -12):
                p2 = x[2] + sh
                if (abs(p2 - pv[1]) <= int(lv['jump']) and (p2 < split) == (hand == 'L')
                        and p2 not in mel_at.get(int(round(x[0] * 4)), ())):
                    cand = p2
                    break
            if cand is None:
                st['drop_jump'] += 1
                continue
            x = [x[0], x[1], cand, x[3], x[4]]
            st['octave'] += 1
        prev[hand] = (x[0], x[2])
        final.append(x)
    st['out'] = len(final)
    return final, st


def _scale_pool(pcs_a, pcs_b, lo=60, hi=85):
    """两个和弦的**音级并集** → 落在 [lo,hi) 的可用音高列表（升序）。

    用"段末和弦 ∪ 段首和弦"当材料：过渡必须**同时**属于两边（否则听起来是错的音）。
    """
    pool = set(pcs_a or ()) | set(pcs_b or ())
    return [p for p in range(lo, hi) if p % 12 in pool]


def make_transitions(d, notes, ctab, ev, opts):
    """**用钢琴手法补段落过渡**（用户 2026-10-01："能不能用钢琴的技巧模拟过渡"）。

    原曲的过渡往往由**别的乐器**做（鼓 fill / 贝斯推进 / 合成器 riser）—— 全钢琴化之后
    这些全没了，段界就"硬切"。这里**按每个段界自己的原曲证据**选手法（技能 §20：不许一刀切）：

    | 原曲信号（逐段界量） | 钢琴手法 |
    |---|---|
    | 段末 2 小节鼓事件 ≥ 全曲中位 ×1.15（= 有过门） | 右手 `arp`（段首和弦分解上行，16 分音符） |
    | 后 2 小节音数 > 前 ×1.4（段落变密 = 推） | 右手 `run_up` 级进上行 + 左手 `push` 八度推进 |
    | 前 > 后 ×1.4（段落变疏 = 收） | 右手 `run_down` 级进下行（渐弱） |
    | 两边都 < `quiet` 音（安静交界） | `rest`（**留白**，不填） |

    素材全部取自**段末和弦 ∪ 段首和弦的音级**（和谐优先 ＞ 花哨）；力度 70~88，**低于旋律**（96）
    以免盖主奏；跑动只占**段末最后 1 拍**（不是整小节填满）。`--fills off` 可整条关掉。
    """
    B = float(d.get('bar_beats') or 4.0)
    rows, out = [], list(notes)
    bounds, acc = [], 0
    for s in d['sections']:
        bounds.append((s.get('name'), acc, acc + int(s['bars'])))
        acc += int(s['bars'])
    if len(bounds) < 2:
        return out, rows
    perc = ev.get('Perc') or []
    alln = [(t, p) for tr, lst in ev.items() if tr != 'Perc'
            for (t, _dd, p, _v) in lst]

    def _cnt(items, b0, b1, idx=0):
        return sum(1 for x in items if b0 * B <= x[idx] < b1 * B)

    nbar = max(1, bounds[-1][2])
    ev_per_bar = [_cnt(alln, b, b + 1) for b in range(nbar)]
    # 全曲"每小节鼓事件"均值 = 判"这一段是不是在打过门"的基线
    d_med = len(perc) / float(nbar)
    used = {(int(round(x[0] * 4)), int(x[2])) for x in out}
    quiet = int(opts.get('fill_quiet', 6))

    for i in range(1, len(bounds)):
        nm, b0, b1 = bounds[i]
        pb0 = bounds[i - 1][1]
        n_prev = sum(ev_per_bar[max(pb0, b1 - 2):b1])
        n_next = sum(ev_per_bar[b1:min(b1 + 2, len(ev_per_bar))])
        d_prev = _cnt(perc, max(pb0, b1 - 2), b1)
        d_prev2 = d_prev / 2.0                      # 段末 2 小节的**每小节**鼓事件
        has_fill = d_prev2 >= d_med * 1.15
        up = n_next > n_prev * 1.4
        down = n_prev > n_next * 1.4
        if n_prev < quiet and n_next < quiet:
            kind = 'rest'                           # 安静交界：留白，不填
        elif up:
            kind = 'run_up'
        elif down:
            kind = 'run_down'
        elif has_fill:
            kind = 'arp'
        else:
            kind = 'rest'
        cF = _chord_pcs(ctab, max(0, b1 - 1))
        cT = _chord_pcs(ctab, min(len(ctab) - 1, b1))
        made = 0
        if kind != 'rest' and cF[1] and cT[1]:
            # 跑动长度（拍）：150BPM 下 1 拍只有 0.4 秒 —— 太短听不出过渡，默认 2 拍
            fb = max(0.5, float(opts.get('fill_beats', 2.0)))
            step = 0.25                             # 16 分音符
            n_slot = max(2, int(round(fb / step)))
            pool = _scale_pool(cF[1], cT[1], 55, 96)
            t0 = b1 * B - fb
            if kind == 'arp':
                base = sorted(p for p in pool if p >= 60)
                seq = [base[k % len(base)] for k in range(n_slot)] if base else []
            elif kind == 'run_down':
                hi = [p for p in pool if p <= 88]
                seq = list(reversed(hi[-n_slot:])) if hi else []
            else:
                lo_i = next((k for k, p in enumerate(pool) if p >= 57), 0)
                seq = pool[lo_i:lo_i + n_slot]
            last = max(1, len(seq) - 1)
            for k, p in enumerate(seq):
                t = t0 + k * step
                if t >= b1 * B:
                    break
                key = (int(round(t * 4)), int(p))
                if key in used:                     # 撞既有音 → 跳过（宁缺勿撞）
                    continue
                if kind == 'run_up':
                    v = int(round(74 + 14 * (k / last)))
                elif kind == 'run_down':
                    v = int(round(86 - 16 * (k / last)))
                else:
                    v = 78
                used.add(key)
                out.append([t, 0.22, int(p), max(60, min(92, v)), SRC_FILL])
                made += 1
            if kind == 'run_up' and cT[0] is not None:      # 左手八度推进（与右手同步到段首）
                p = int(cT[0])
                while p < 33:
                    p += 12
                while p > 47:
                    p -= 12
                for k in range(n_slot):
                    t = t0 + k * step
                    key = (int(round(t * 4)), p)
                    if key in used or t >= b1 * B:
                        continue
                    used.add(key)
                    out.append([t, 0.22, p, 74 + int(10 * k / max(1, n_slot - 1)), SRC_FILL])
                    made += 1
        rows.append({'sec': nm, 'bar': b1, 'kind': kind, 'n_prev': n_prev, 'n_next': n_next,
                     'drum_per_bar': round(d_prev2, 1), 'drum_med': round(d_med, 1),
                     'has_fill': bool(has_fill), 'notes': made})
    out.sort(key=lambda x: (x[0], x[2]))
    return out, rows


def to_solo(d, prog, label, opts):
    """**纯函数**：曲目数据 → 单乐器版数据 + 报告（自检直接调它，不写盘）"""
    d = copy.deepcopy(d)
    rep = {'instrument': label, 'gm': prog, 'src_tracks': {}, 'dropped_tracks': []}
    ev, _nbars = se.build_events(d)
    B = float(d.get('bar_beats') or 4.0)
    if abs(B - 4.0) > 1e-9:
        rep['warn_meter'] = ('本曲 %g 拍/小节，而引擎读 `notes_extra` 时按 4 拍/小节换算 '
                             '（`song_engine` 2092/2105 行）→ 冻结后的音符会错位' % B)
    ctab = bar_chord_table(d)

    mel = [(float(t), float(dd), int(p), int(v), PRIO['Melody'])
           for (t, dd, p, v) in (ev.get('Melody') or [])]
    # **旋律的"家"在哪**（⚠ 2026-10-01 实测踩到）：多数曲目走 `melody` 字段；而还原曲被
    # `--no-melody` 清空过字段、旋律音改住在 `notes_extra['Melody']`（实测 `bgm35_extract`
    # **160 音** / `siren_end` 33 音）。前者由字段生成（不能重复写进主轨）；
    # **后者必须原样写回 `notes_extra`，否则整条旋律被静默丢掉**（第一版就是这么错的：
    # 它把 Melody 轨排除在主轨之外、又假设字段里有音）。
    _mel_field = sum(len(v or []) for v in (d.get('melody') or {}).values())
    _mel_in_extra = bool((d.get('notes_extra') or {}).get('Melody'))
    rep['melody_home'] = 'field' if _mel_field else ('notes_extra' if _mel_in_extra else 'none')
    pool = []
    for tr, lst in ev.items():
        if tr in ('Melody', 'Perc'):
            continue
        if lst:
            rep['src_tracks'][tr] = len(lst)
        for (t, dd, p, v) in lst:
            pool.append((float(t), float(dd), int(p), int(v), SRC_MERGED))

    used = {(int(round(t * 4)), int(p)) for (t, _dd, p, _v, _s) in (mel + pool)}
    drum_notes, dstat = ([], {'in': 0, 'out': 0, 'by_kind': {}})
    if opts['drums'] == 'piano':
        drum_notes, dstat = drums_to_notes(ev.get('Perc') or [], ctab, used, opts)
        pool.extend([(t, dd, p, v, SRC_DRUM) for (t, dd, p, v) in drum_notes])
    rep['drums'] = dstat

    merged, n_drop = dedupe(mel + pool, float(opts.get('dedupe_tol', 0.1)))
    if _mel_field:
        merged = [x for x in merged if x[4] != PRIO['Melody']]      # 旋律仍走 melody 字段
    rep['dedupe_dropped'] = n_drop

    lo = int(opts['low_floor'])
    hi = 96
    n_long = n_oct = 0
    fixed = []
    for (t, dd, p, v, src) in merged:
        dd2, p2 = float(dd), int(p)
        if opts['max_beats'] and dd2 > opts['max_beats']:
            dd2, n_long = float(opts['max_beats']), n_long + 1
        while p2 < lo:
            p2 += 12
        while p2 > hi:
            p2 -= 12
        if p2 != p:
            n_oct += 1
        fixed.append([t, dd2, p2, v, src])
    rep['long_trimmed'] = n_long
    rep['octave_moved'] = n_oct
    fixed.sort(key=lambda x: (x[0], x[2]))
    # **用钢琴手法补段落过渡**（用户 2026-10-01："能不能用钢琴的技巧模拟过渡"）——
    # 放在可弹化**之前**：过渡音在打分里有加分（`SRC_FILL`），不会被削音优先删掉。
    if (opts.get('fills') or 'off') != 'off':
        fixed, _frows = make_transitions(d, fixed, ctab, ev, opts)
        rep['fills'] = _frows
        rep['fill_notes'] = sum(r['notes'] for r in _frows)
    # **削成"简单音乐人双手能弹"的版本**（默认关 —— 用户 2026-10-01："让简单的音乐人用双手也能弹"、
    # "如果是太复杂的音乐就不用了，反正尽量简单一点"）。判据与门槛同 `probe_playable`（一把尺子）。
    lvl = opts.get('playable') or 'off'
    if lvl != 'off':
        import probe_playable as pp
        split = int(opts.get('split') or 60)
        mel_at = {}
        for (t, _dd, p, _v, _s) in mel:
            mel_at.setdefault(int(round(t * 4)), set()).add(int(p))
        mnotes_before = [(x[0], x[2], x[3], 'X') for x in fixed] + \
                        [(t, p, v, 'M') for (t, _dd, p, _v, _s) in mel]
        alln = [list(x) for x in fixed]
        before = pp.metrics(mnotes_before, split)
        fixed, pst = make_playable(fixed, mel_at, ctab, pp.LEVELS[lvl], split)
        after = pp.metrics([(x[0], x[2], x[3], 'X') for x in fixed] +
                           [(t, p, v, 'M') for (t, _dd, p, _v, _s) in mel], split)
        ok, _rows = pp.verdict(after, lvl)
        rep['playable'] = {'level': lvl, 'stats': pst, 'before': before, 'after': after,
                           'ok': ok, 'keep': feature_keep(fixed, alln, ctab)}
    rep['main_notes'] = len(fixed)
    rep['melody_notes'] = len(mel)
    ne = [[int(t // 4), round(t - int(t // 4) * 4.0, 6), round(dd, 6), int(p), int(v)]
          for (t, dd, p, v, _src) in fixed]

    out = copy.deepcopy(d)
    main = opts['track']
    off = {'bass': False, 'piano': False, 'uku': False, 'ep': False, 'arp': False,
           'pad': False, 'strings': False, 'glock': False, 'shimmer': False,
           'harmony': False, 'perc': 0, 'density': 0}
    for sec in out.get('sections') or []:
        arr = dict(sec.get('arr') or {})
        arr.update(off)
        sec['arr'] = arr
    pat = dict(out.get('patterns') or {})
    for k in ('drum_grid', 'voicing_shift', 'perc_target', 'perc_vel_max',
              'glock_oct', 'glock_from_bar', 'glock_all', 'glock_starved',
              'shimmer_db', 'perc_in', 'notes_extra_sample_reason'):
        pat.pop(k, None)
    pat['notes_extra_full'] = True                 # 逐音照写（扒带曲的守卫 `t_restore_notes_full` 认它）
    out['patterns'] = pat
    out['notes_extra'] = {main: ne}
    if _mel_in_extra and not _mel_field:
        # 旋律音原样搬过去（逐音照写：小节 / 拍内 / 时值 / 音高 / 力度）
        out['notes_extra']['Melody'] = sorted(
            [[int(t // 4), round(t - int(t // 4) * 4.0, 6), round(dd, 6), int(p), int(v)]
             for (t, dd, p, v, _s) in mel], key=lambda x: (x[0], x[1], x[3]))
        rep['melody_kept_in_extra'] = len(mel)
    out['programs'] = {'Melody': [prog, se.CH['Melody']], main: [prog, se.CH[main]]}
    if opts['keep_mix']:
        mix = dict(out.get('mix') or {})
        out['mix'] = {k: mix.get(k, se.DEFAULT_MIX.get(k)) for k in ('Melody', main)}
    else:
        # 一架钢琴 = **一个声像**（居中）；两轨只是"右手/左手"的组织方式
        out['mix'] = {'Melody': [64, 100], main: [64, 88]}
    out['name'] = opts['name']
    # **派生标记**：告诉全库判据"这是改编版、不是新作曲"——`t_melody_distinct`（跨曲旋律雷同）
    # 会跳过它，否则同一条旋律会被当成"另一首新歌照抄"（实测 3.2% → 6.3%，越过 5% 上限）。
    out['derived_from'] = d.get('name') or '?'
    out['desc'] = ('单乐器独奏版（%s）：全部声部由 %s 演奏，源自 %s'
                   % (label, label, d.get('name') or '?'))
    out.pop('basis', None)
    out.pop('tr_shift', None)
    return out, rep


def _print_report(rep):
    print('  乐器：%s（GM %d）' % (rep['instrument'], rep['gm']))
    if rep.get('warn_meter'):
        print('  ⚠ %s' % rep['warn_meter'])
    src = ', '.join('%s %d' % (k, v) for k, v in sorted(rep['src_tracks'].items()))
    print('  并入主轨的音源：%s' % (src or '（无 —— 原曲本来就只有旋律）'))
    d = rep.get('drums') or {}
    if d.get('in'):
        kinds = ' · '.join('%s %d' % (k, v) for k, v in sorted((d.get('by_kind') or {}).items()))
        print('  鼓 → 音型：%d 事件 → %d 音（%s）；丢弃 %d（同类同拍重复 %d / 无和弦或撞音 %d）'
              % (d['in'], d['out'], kinds or '无', d['in'] - d['out'],
                 d.get('drop_thin', 0), d.get('drop_collide', 0)))
    print('  去重（同刻同音高）：删 %d 音' % rep['dedupe_dropped'])
    print('  长音裁剪（> %.2f 拍）：%d 个 · 低音区整理（<%d 抬八度）：%d 个'
          % (rep.get('max_beats', 2.0), rep['long_trimmed'], rep.get('low_floor', 33),
             rep['octave_moved']))
    print('  产出：主轨 %d 音 + 旋律轨 %d 音（旋律住 %s）'
          % (rep['main_notes'], rep['melody_notes'], rep.get('melody_home', '?')))
    pl = rep.get('playable')
    if pl:
        b, af, s = pl['before'], pl['after'], pl['stats']
        print('  可弹化（档 %s）：%d → %d 音（削 %d = 每手上限 %d + 每拍上限 %d + 同音串 %d +'
              ' 跳进 %d；移八度 %d）'
              % (pl['level'], s['in'], s['out'], s['in'] - s['out'], s['drop_hand'],
                 s['drop_beat'], s['drop_run'], s['drop_jump'], s['octave']))
        print('    读数：同按 max %d→%d · 单手跨度 max %d→%d · 每拍中位 %d→%d · 同音串 %d→%d ·'
              ' 跳进 p95 %d→%d   %s'
              % (b['poly_max'], af['poly_max'], b['span_max'], af['span_max'],
                 b['beat_med'], af['beat_med'], b['run'], af['run'],
                 b['jump_p95'], af['jump_p95'], '✓ 这一档弹得下来' if pl['ok'] else '✗ 仍超'))
        kp = pl.get('keep') or {}
        if kp:
            def _r(k):
                a, b2 = kp.get(k, [0, 0])
                return '%.0f%%' % (100.0 * a / max(1, b2))
            print('    关键特征保留率：和弦内音 %s / 和弦外音 %s · 踩拍点 %s / 弱拍 %s ·'
                  ' 转录音 %s / 鼓写音 %s'
                  % (_r('in_chord'), _r('off_chord'), _r('strong'), _r('weak'),
                     _r('trans'), _r('drum')))
    fl = rep.get('fills')
    if fl:
        cnt = {}
        for r in fl:
            cnt[r['kind']] = cnt.get(r['kind'], 0) + 1
        print('  过渡填充（钢琴手法）：%d 个段界 → %d 音 · 手法分布 %s'
              % (len(fl), rep.get('fill_notes', 0),
                 ' · '.join('%s %d' % (k, v) for k, v in sorted(cnt.items()))))
        for r in fl:
            if r['notes']:
                print('      %-6s bar%-4d %-9s 前 %d → 后 %d 音 · 段末鼓 %.1f/小节'
                      '（全曲均值 %.1f）→ 出 %d 音'
                      % (r['sec'], r['bar'], r['kind'], r['n_prev'], r['n_next'],
                         r['drum_per_bar'], r['drum_med'], r['notes']))


COMPOSE_TMPL = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""%s —— 单乐器独奏版（由 `scripts/solo_instrument.py` 生成）；编配数据全在 song.json"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.environ.get('BGM_STUDIO_ROOT') or os.path.join(HERE, '..', '..')
sys.path.insert(0, os.path.join(_ROOT, 'scripts'))
import song_engine

if __name__ == '__main__':
    song_engine.compose(os.path.join(HERE, 'song.json'))
'''


def notes_md(name, base, src_json, prog, label, rep, a):
    """曲目目录的 `notes.md`：**处理读数 + 没验证什么**（守卫 `notes_present` 要求这个文件）"""
    d = rep.get('drums') or {}
    src = ' · '.join('%s %d' % (k, v) for k, v in sorted(rep['src_tracks'].items())) or '—'
    kinds = ' · '.join('%s %d' % (k, v) for k, v in sorted((d.get('by_kind') or {}).items()))
    L = ['# %s —— 单乐器独奏版说明（源：%s）' % (name, base), '',
         '**来源**：`%s`' % src_json,
         '**乐器**：%s（GM %d）· 处理日 %s' % (label, prog, time.strftime('%Y-%m-%d')),
         '**工序**：`python scripts\\solo_instrument.py %s --instrument %s --drums %s '
         '--max-beats %g --playable %s`' % (base, a.instrument, a.drums, a.max_beats,
                                            a.playable), '',
         '## 这一版做了什么（读数，机器可复现）', '',
         '| 步骤 | 读数 |', '|---|---|',
         '| 并入主轨的音源 | %s |' % src,
         ('| 鼓 → 音型 | %d 事件 → %d 音（%s）；丢弃 %d |'
          % (d['in'], d['out'], kinds, d['in'] - d['out'])) if d.get('in')
         else ('| 鼓 → 音型 | 无鼓事件（`--drums %s`） |' % a.drums),
         '| 跨轨同音去重 | 删 %d 音（同音高 %.2f 拍内，只去 Δ0） |'
         % (rep['dedupe_dropped'], a.dedupe_tol),
         '| 长音裁剪 | %d 个 → 上限 %.2f 拍 |' % (rep['long_trimmed'], a.max_beats),
         '| 低音区整理 | %d 个 → 下限 %d（A1） |' % (rep['octave_moved'], a.low_floor),
         '| 产出 | 主轨 %d 音 + 旋律轨 %d 音（旋律住 `%s`） |'
         % (rep['main_notes'], rep['melody_notes'], rep.get('melody_home', '?'))]
    _pl = rep.get('playable')
    if _pl:
        _s, _af = _pl['stats'], _pl['after']
        L.append('| 可弹化（档 %s） | 削 %d 音（%d → %d）：同按 max %d · 单手跨度 max %d 半音 · '
                 '每拍中位 %d · 同音串 %d %s |'
                 % (_pl['level'], _s['in'] - _s['out'], _s['in'], _s['out'],
                    _af['poly_max'], _af['span_max'], _af['beat_med'], _af['run'],
                    '（**达标**）' if _pl['ok'] else '（**仍超**）'))
    L += ['', '## 该知道的缺陷 / 没验证什么', '']
    items = []
    if d.get('in'):
        items.append('**鼓是"转写"不是"还原"**：kick→低音区根音 · snare→中音区和弦音 · '
                     'hat→高音区轻点，每拍每类最多一个（`--drums piano`）。'
                     '原曲鼓组的音色与瞬态在钢琴上不可能复现。')
    if _pl:
        _s, _af = _pl['stats'], _pl['after']
        _kp = _pl.get('keep') or {}

        def _rate(k):
            a, b2 = _kp.get(k, [0, 0])
            return '%.0f%%' % (100.0 * a / max(1, b2))
        _lim = __import__('probe_playable').LEVELS[_pl['level']]
        items.append('**可弹化削掉了 %d 音（%d → %d）** —— 门槛取 '
                     '`probe_playable.LEVELS["%s"]`（同时按键 ≤%d · 单手跨度 ≤%d 半音 · '
                     '每拍按键 ≤%d · 同音串 ≤%d；左手 ≤%d 音 = 根音+五度，右手 ≤%d 音含旋律），'
                     '**按音乐重要性削**：和弦内音 +3（根音再 +1）· 和弦外音 −2 · 踩拍点 +1.5 · '
                     '长音 +1 · 鼓写音 −1.5。实测关键特征保留率：**和弦内音 %s / 和弦外音 %s · '
                     '踩拍点 %s / 弱拍 %s · 转录音 %s / 鼓写音 %s**。'
                     '嫌稀用 `--playable normal`，完全不削用 `--playable off`。'
                     % (_s['in'] - _s['out'], _s['in'], _s['out'], _pl['level'],
                        _lim['poly'], _lim['span'], _lim['beat'], _lim['run'],
                        _lim['lh'], _lim['rh'], _rate('in_chord'), _rate('off_chord'),
                        _rate('strong'), _rate('weak'), _rate('trans'), _rate('drum')))
    items += ['**长音被裁到 %.2f 拍**：钢琴靠衰减收尾（PITFALLS 219 第 1 条），'
              '原曲垫子层的持续感会变薄。' % a.max_beats,
              '**同音色多轨 → 默认声像居中**（一架钢琴 = 一个位置）；要保留原曲声像用 `--keep-mix`。',
              '**旋律仍是 GM %d**：`check_song` 会提示"只响 0.几秒"（钢琴掉 12dB 只要 ~0.24s）。'
              '要持续型可 `--instrument ep`（GM 4 电钢琴，钢琴族内）出对照版。' % prog,
              '**没验证**：踏板 · 演奏法 · 与人工钢琴改编的听感差距 —— 这些要人耳 A/B'
              '（`docs/UNMEASURABLE-SOLUTIONS.md` 的 8 条）。可弹性**已量**（见上表，'
              '`scripts\\probe_playable.py` 可复跑）。']
    L += ['%d. %s' % (i + 1, t) for i, t in enumerate(items)]
    return '\n'.join(L) + '\n'


def main():
    ap = argparse.ArgumentParser(description='把一首曲子改成"只用一件指定乐器演奏"')
    ap.add_argument('song', nargs='?', help='曲目名（songs/<名字>）或 song.json 的路径')
    ap.add_argument('--instrument', default='piano', help='钢琴/弦乐/吉他…别名，或 GM 号（默认 piano）')
    ap.add_argument('--out', default=None, help='输出曲目名（默认 <原名>_solo[_<乐器>]）')
    ap.add_argument('--drums', choices=('piano', 'drop', 'keep'), default='piano',
                    help='鼓轨：转成乐器音型（默认）/ 丢掉 / 原样保留（通道 10 上仍是鼓声）')
    ap.add_argument('--max-beats', type=float, default=2.0, help='时值上限（拍，默认 2.0）')
    ap.add_argument('--dedupe-tol', type=float, default=0.1,
                    help='同音高在这段时间内（拍）视为同一个音，默认 0.1')
    ap.add_argument('--playable', choices=('easy', 'normal', 'off'), default='off',
                    help='削成双手可弹（**默认 off = 不削、保真优先**）；easy/normal 是显式可选的'
                         '简化档 —— 用户 2026-10-01 试听判定："感觉效果不好"，简化版不如保真版')
    ap.add_argument('--fills', choices=('off', 'auto'), default='off',
                    help='用钢琴手法补段落过渡（默认 off）；auto = 按**每个段界的原曲证据**选：'
                         '段落变密→上行跑动+左手八度推进 · 变疏→下行跑动渐弱 · 段末有过门→'
                         '和弦分解 · 安静交界→留白')
    ap.add_argument('--fill-beats', type=float, default=2.0,
                    help='每个段界的过渡跑动长度（拍，默认 2.0 ≈ 150BPM 下 0.8 秒）')
    ap.add_argument('--split', type=int, default=60,
                    help='左右手分界音高（默认 60 = C4；<split 归左手）')
    ap.add_argument('--low-floor', type=int, default=33, help='低音下限（默认 33 = A1）')
    ap.add_argument('--track', default='Piano', help="承载伴奏的主轨名（默认 Piano）")
    ap.add_argument('--keep-mix', action='store_true', help='沿用原曲的声像/音量（默认居中收敛）')
    ap.add_argument('--no-render', action='store_true', help='只写 song.json，不渲染')
    ap.add_argument('--force', action='store_true',
                    help='输出目录已存在时也照写（默认拒绝，避免静默覆盖）')
    ap.add_argument('--dry', action='store_true', help='只打印处理表，不写盘')
    ap.add_argument('--selftest', action='store_true', help='合成素材自检（不依赖曲库）')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.song:
        ap.error('要给曲目名（songs/<名字>），或加 --selftest 只跑自检')
    prog, label = parse_instrument(a.instrument)
    src_json = a.song if a.song.endswith('.json') else os.path.join(SONGS, a.song, 'song.json')
    if not os.path.exists(src_json):
        raise SystemExit('找不到 %s' % src_json)
    src_dir = os.path.dirname(os.path.abspath(src_json))
    base = os.path.basename(src_dir)
    suffix = '_solo' if prog == 0 else '_solo_%s' % str(a.instrument).strip().lower()
    if a.out and os.path.isabs(a.out):          # 绝对路径 = 写到曲库外（自检/交付用）
        out_dir = a.out
        name = os.path.basename(out_dir.rstrip('\\/'))
    else:
        name = a.out or (base + suffix)
        out_dir = os.path.join(SONGS, name)
    opts = {'drums': a.drums, 'max_beats': a.max_beats, 'low_floor': a.low_floor,
            'track': a.track, 'keep_mix': a.keep_mix, 'name': name,
            'dedupe_tol': a.dedupe_tol, 'playable': a.playable, 'split': a.split,
            'fills': a.fills, 'fill_beats': a.fill_beats}
    if a.track not in se.CH:
        raise SystemExit('主轨名只能是 %s 之一' % ', '.join(sorted(se.CH)))
    print('== solo_instrument: %s → %s' % (base, name))
    d = se.load(src_json)
    out, rep = to_solo(d, prog, label, opts)
    rep['max_beats'], rep['low_floor'] = a.max_beats, a.low_floor
    _print_report(rep)
    if a.dry:
        print('  （--dry：没有写盘）')
        return 0
    # 面板守卫（同 `new_song`/`make_song` 的硬形式）：探活 + 不在跑就 detached 拉起。
    # 本工具没有面板端点可委托（它是"数据变换 + 渲染"两步），所以只保证面板在场。
    # ⚠ 放在 `--dry` **之后**：只读路径不该有"拉起一个服务"的副作用（自检会跑 `--dry`）。
    try:
        import studio_guard
        studio_guard.ensure_panel()
    except Exception as _e:                                        # noqa: BLE001
        print('  （面板守卫跳过：%s）' % str(_e)[:80])
    if os.path.isdir(out_dir) and not (a.force or a.out):
        print('  ! %s 已存在 —— 用 --out 换名字，或加 --force 覆盖（避免静默覆盖）' % out_dir)
        return 1
    os.makedirs(out_dir, exist_ok=True)
    json_io.save(os.path.join(out_dir, 'song.json'), out)
    with open(os.path.join(out_dir, 'compose.py'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(COMPOSE_TMPL % name)
    # `notes.md` 不是可选项：曲目目录必须齐 4 件（`song.json`+`compose.py`+`notes.md`+`render.json`），
    # 缺它会红两条守卫（`notes_present` / `render_json_schema`）—— PITFALLS 297 ③。
    with open(os.path.join(out_dir, 'notes.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(notes_md(name, base, src_json, prog, label, rep, a))
    # 渲染参数：**沿用原曲**（rms/宽度/搁架是同一批素材调出来的）；ref 保留 → 成绩单有对照
    cfg_src = os.path.join(src_dir, 'render.json')
    cfg = {}
    if os.path.exists(cfg_src):
        cfg = json.load(open(cfg_src, encoding='utf-8'))
    cfg.pop('last_bands', None)
    cfg.update({'song': name, 'mid': name + '.mid', 'out': name + '_sf', 'composer': 'compose.py',
                '_note': '单乐器独奏版（solo_instrument.py 生成，源自 %s）；'
                         '不对标原曲混音 —— 编制变了，频谱差异是物理结果（SKILL §3-9c）' % base})
    json.dump(cfg, open(os.path.join(out_dir, 'render.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('  已写：%s' % os.path.join(out_dir, 'song.json'))
    if a.no_render:
        print('  渲染：python scripts\\make_song.py %s --no-tune' % name)
        return 0
    py = sys.executable
    env = dict(os.environ, BGM_CLI_DIRECT='1')        # 批量路径：直连引擎（面板委托留给单曲手敲）
    print('  渲染：make_song %s --no-tune（autotune 会硬把独奏版往原曲混音推，故不调参）' % name)
    rc = subprocess.run([py, os.path.join(HERE, 'make_song.py'), name, '--no-tune'],
                        cwd=ROOT, env=env).returncode
    if rc != 0:
        print('  ! 渲染返回 %d —— 先看上面的输出' % rc)
    return rc


# ---------------------------------------------------------------- 自检
def _fixture():
    """合成素材：**不依赖曲库**（自检要能在任何机器上跑）。含鼓、长音、低音、跨轨同音。

    ⚠ 鼓必须放在**第 3 小节之后**：引擎对曲首有"鼓太稀就静音"的规则
    （`song_engine` 1554-1556：前 2 小节 <8 个点、前 16 小节 <3 个点一律不出鼓），
    夹具要是把鼓放在 bar0/1，自检会读到"0 个鼓事件"而误判成"鼓没转"。
    """
    return {
        'name': 'fx', 'bpm': 100.0, 'meter': [4, 4], 'bar_beats': 4.0,
        'chords': {'C': [36, [60, 64, 67]], 'F': [41, [65, 69, 72]]},
        'melody': {'A': [[0, 0.0, 1.0, 72], [2, 0.0, 1.0, 74]]},
        'sections': [{'name': 'A', 'bars': 4, 'chords': ['C', 'C', 'F', 'F'], 'melody': 'A',
                      'arr': {'bass': True, 'piano': True, 'perc': 2, 'pad': True}}],
        'patterns': {'perc_style': 'light', 'bass_style': 'simple',
                     'drum_grid': {'per_bar': [
                         {}, {},
                         {'kick': [[0, 110, 36], [8, 100, 36]], 'snare': [[4, 90, 38]],
                          'hat': [[2, 60, 42], [10, 58, 42]]},
                         {'kick': [[0, 108, 36]], 'snare': [[4, 88, 38]], 'hat': [[2, 56, 42]]},
                     ]}},
        'programs': {'Melody': [0, 0], 'Hook': [24, 1], 'Piano': [0, 2], 'Arp': [87, 3],
                     'Pad': [89, 4], 'Strings': [48, 5], 'Bass': [32, 6], 'Glock': [9, 7],
                     'Perc': [None, 9]},
        'mix': {'Melody': [76, 100], 'Hook': [46, 76], 'Piano': [86, 74], 'Strings': [52, 74],
                'Bass': [64, 90], 'Perc': [64, 70]},
        'notes_extra': {'Strings': [[0, 0.0, 4.1, 64, 80], [3, 0.0, 4.1, 65, 80]],
                        'Bass': [[0, 0.0, 1.0, 24, 100]],
                        # 与旋律同刻同音高 → 必须被去重；bar2 的 61 是 F 和弦的**外音**
                        # → 可弹化时必须**先被删**（"关键特征优先"的最低分）
                        'Piano': [[0, 0.0, 0.5, 72, 100], [2, 0.0, 2.0, 61, 100]]},
    }


def selftest():
    """尺子自检（硬门）：在**合成素材**上验七条不变量；不碰曲库、不写盘。"""
    fx = _fixture()
    out, rep = to_solo(fx, 0, 'Acoustic Grand 钢琴',
                       {'drums': 'piano', 'max_beats': 2.0, 'low_floor': 33,
                        'track': 'Piano', 'keep_mix': False, 'name': 'fx_solo',
                        'dedupe_tol': 0.1, 'playable': 'easy', 'split': 60})
    ne = out['notes_extra']
    assert list(ne.keys()) == ['Piano'], '主轨应只有一条：%r' % list(ne.keys())
    notes = ne['Piano']
    assert notes, '主轨一个音都没有（合并/去重把它们全吃了）'
    # ① 音色统一 + 轨收缩
    assert out['programs'] == {'Melody': [0, 0], 'Piano': [0, 2]}, out['programs']
    for sec in out['sections']:
        arr = sec['arr']
        assert not any(arr.get(k) for k in ('bass', 'piano', 'uku', 'ep', 'arp', 'pad',
                                            'strings', 'glock', 'shimmer')), arr
        assert int(arr.get('perc') or 0) == 0, arr
    assert 'drum_grid' not in (out.get('patterns') or {}), 'drum_grid 没清掉会重复发声'
    assert out.get('derived_from'), \
        '派生标记 `derived_from` 没写 —— `t_melody_distinct` 会把改编版当"新歌照抄"（实测 3.2%→6.3%）'
    # ② 鼓真的被转成音（不是静默丢掉）
    assert rep['drums']['out'] >= 2, '鼓没有转成音：%r' % rep['drums']
    assert any(p in (33, 36, 40, 41, 43, 45) for (_b, _bt, _d, p, _v) in notes), \
        'kick 应落成低音区根音，实得音高：%r' % sorted({p for *_x, p, _v in notes})
    # ③ 同音高不再"贴在一起"（去重生效，口径 = 容差 0.1 拍）
    byp = {}
    for (b, bt, _d, p, _v) in notes:
        byp.setdefault(p, []).append(b * 4.0 + bt)
    for p, ts in byp.items():
        ts.sort()
        for t1, t2 in zip(ts, ts[1:]):
            assert t2 - t1 >= 0.1 - 1e-6, '音高 %d 还有 %.3f 拍内的重复' % (p, t2 - t1)
    # ④ 长音都裁到上限内
    assert max(d for (_b, _bt, d, _p, _v) in notes) <= 2.0 + 1e-6, '还有超长音'
    assert rep['long_trimmed'] >= 1, '夹具里那个 4.1 拍长音没被裁（判据空转）'
    # ⑤ 低音区整理
    assert min(p for (_b, _bt, _d, p, _v) in notes) >= 33, '还有低于 A1 的低音'
    # ⑥ 主轨里不许有"**与旋律同刻同音高**"的音（跳进移八度可能落到同一个音高值，
    #    那必须是**别的时刻** —— 同刻就是齐奏加倍）。夹具：旋律 = bar0 拍0 的 72、bar2 拍0 的 74。
    mel_keys = {(0, 72), (2 * 16, 74)}
    bad = [(b, bt, p) for (b, bt, _d, p, _v) in notes
           if (b * 16 + int(round(bt * 4)), p) in mel_keys]
    assert not bad, '主轨里有与旋律同刻同音高的音：%r' % (bad,)
    assert rep['dedupe_dropped'] >= 1, '与旋律撞的那个 Piano 音没被去重'
    # ⑦ --drums drop 时不产生鼓音
    out2, rep2 = to_solo(fx, 0, 'x', {'drums': 'drop', 'max_beats': 2.0, 'low_floor': 33,
                                      'track': 'Piano', 'keep_mix': False, 'name': 'y',
                                      'dedupe_tol': 0.1, 'playable': 'easy', 'split': 60})
    assert rep2['drums'].get('out', 0) == 0 and len(out2['notes_extra']['Piano']) < len(notes), \
        '--drums drop 应该比 piano 少音'
    # ⑧ **可弹化**：削完必须过同一把尺子（`probe_playable.verdict`），且 `off` 时不做
    import probe_playable as _pp
    pl = rep.get('playable')
    assert pl, '可弹化没跑（--playable easy 时 rep 里必须有 playable）'
    assert pl['ok'], '削完仍不达标：%r' % (_pp.verdict(pl['after'], pl['level'])[1],)
    assert pl['stats']['out'] < pl['stats']['in'], \
        '可弹化一个音都没削（夹具素材本来就达标？这条断言会失去意义 —— 换个更密的夹具）'
    # ⑨ **关键特征优先**（用户 2026-10-01："没有聚集关键特征 …… 反而留下了一些错误或不重要的音"）：
    #    和弦内音的保留率必须**高于**和弦外音，踩拍点必须**高于**弱拍，转录音必须**高于**鼓写音。
    #    第一版按几何选音时这三条全是反的（和弦内 51.3% vs 外 54.1%、踩拍点 39.2% vs 弱拍 56.1%）。
    kp = pl['keep']

    def _rate(k):
        a, b2 = kp.get(k, [0, 0])
        return a / float(b2) if b2 else 1.0
    assert _rate('in_chord') > _rate('off_chord'), \
        '和弦内音保留率没有高于外音：%r' % {k: kp.get(k) for k in ('in_chord', 'off_chord')}
    assert _rate('strong') >= _rate('weak'), \
        '踩拍点音被删得比弱拍还狠：%r' % {k: kp.get(k) for k in ('strong', 'weak')}
    assert _rate('trans') >= _rate('drum'), \
        '转录（原曲内容）被删得比鼓写音还狠：%r' % {k: kp.get(k) for k in ('trans', 'drum')}
    out3, rep3 = to_solo(fx, 0, 'x', {'drums': 'piano', 'max_beats': 2.0, 'low_floor': 33,
                                      'track': 'Piano', 'keep_mix': False, 'name': 'z',
                                      'dedupe_tol': 0.1, 'playable': 'off', 'split': 60})
    assert 'playable' not in rep3, '--playable off 不该做可弹化'
    # ⑩ **旋律住在 `notes_extra['Melody']` 的曲目**（还原曲被 `--no-melody` 清空过字段：
    #    实测 `bgm35_extract` 160 音 / `siren_end` 33 音）：旋律必须原样保留、不许丢。
    fx2 = _fixture()
    fx2['melody'] = {}
    for _sec in fx2['sections']:
        _sec['melody'] = ''
    fx2['notes_extra']['Melody'] = [[0, 0.0, 1.0, 72, 96], [2, 0.0, 1.0, 74, 96]]
    out4, rep4 = to_solo(fx2, 0, 'x', {'drums': 'piano', 'max_beats': 2.0, 'low_floor': 33,
                                       'track': 'Piano', 'keep_mix': False, 'name': 'w',
                                       'dedupe_tol': 0.1, 'playable': 'off', 'split': 60})
    assert rep4['melody_home'] == 'notes_extra', rep4['melody_home']
    assert len(out4['notes_extra'].get('Melody') or []) == 2, \
        '旋律住 notes_extra 的曲目，旋律音被丢了：%r' % (list(out4['notes_extra'].keys()),)
    print('solo_instrument --selftest：9 条不变量全过'
          '（鼓 %d→%d 音 · 去重 %d · 裁长 %d · 低音整理 %d · 可弹化 %d→%d 音达标 · 特征优先）'
          % (rep['drums']['in'], rep['drums']['out'], rep['dedupe_dropped'],
             rep['long_trimmed'], rep['octave_moved'],
             rep['playable']['stats']['in'], rep['playable']['stats']['out']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
