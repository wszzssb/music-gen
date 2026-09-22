#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成期与交付前的**和谐体检**（共享模块）—— 用户 2026-09-21：
"以后生成音乐最后检查是否和谐" + "生成期间也要注意"。

为什么要有独立模块（而不是留在 `check_song.py` 里）：
  · 交付前：`check_song.py` 调用它（`make_song` 每步都会调 check_song → 必然跑到）
  · **生成期间**：`make_song` 的自动调参每轮也要调它 —— 调参改的是混音参数，
    但"调参把某轨推得盖住旋律"这类不和谐只有**生成期**才拦得住
  · 两处共用同一份判据（`CONVENTION.md` §1：抄一份 = 埋一处漂移）

三项判据（实测来源：`20_piano_rain` 那一轮，见 `PITFALLS.md` 219）：
  ① **旋律↔和弦音区间距** 5–22 半音 —— 过大 = 中间空掉（听感"空 + 发尖、不融合"）；
     为负 = 旋律掉进左手区、撞在一起
  ② **跨轨同刻同音高（撞音）** —— 两条"靠衰减收尾"音色族的轨音区相近就会撞，
     音源发浑（实测 148 处 → 抬八度/砍轨后 28 处）
  ③ **长音层** —— `Pad`(4.1 拍) / `Strings`(4.1 拍) / `shimmer`(4 拍，塞在 Arp 轨)：
     "靠衰减收尾"的音色按住不放 = 不和谐
"""
import sys

# "靠衰减收尾"的 GM 音色族：钢琴 0-7 · 音高打击 8-15 · 拨弦 24-31 · 贝斯 32-39
DECAY_FAMILIES = ((0, 7), (8, 15), (24, 31), (32, 39))
# 音区间距的"正统织体"区间（抒情钢琴/小编制；大编制或特殊配器会有别的合理值）
GAP_MIN, GAP_MAX = 5, 22

# ---- 2026-09-22 新增：主奏音色的"持续能力"（用户："只响 0.几秒的音" + "还有镫一下的"）----
# **实测值**（GeneralUser GS · tempo 78 · 音高 78 · 力度 84 · 单音渲染；
#   判据与全部对照见 `docs/HANDOFF.md` §7 / §7.10，量测脚本见 `scripts/probe_sustain.py` 的 docstring）：
#     掉 12 dB：0.24(0) 0.24(1) 0.43(2) 0.19(3) **不掉(4)** 0.58(5) 0.64(6) 0.59(7) 0.84(11)
#     起音 10→90%：钢琴 38–41ms · 电钢1 **0–1ms** · 弦乐 48 **427ms** · 慢弦乐 62 **621ms**
# ⚠ 表里**只有实测过的音色**；其余按族兜底（`DECAY_MAX` 那一档），兜底值是**保守估计**、
#   不作精确判据 —— 要用就补测并加进表（别把估值当实测用）。
SUSTAIN_DB12 = {0: 0.24, 1: 0.24, 2: 0.43, 3: 0.19, 4: float('inf'), 5: 0.58,
                6: 0.64, 7: 0.59, 11: 0.84, 48: float('inf'), 62: float('inf')}
SLOW_ATTACK = {48: 0.427, 62: 0.621}     # 起音 > 20ms = 会"慢半拍"（实测）
DECAY_MAX = 0.45                         # 掉 12dB 快于它 = "音头重"（钢琴/拨弦/音高打击）


def db12(prog):
    """该 GM 音色"从峰值掉 12 dB"要多久（秒）；`inf` = 实测完全不掉。返回 None = 判不了。"""
    if prog is None:
        return None
    try:
        p = int(prog)
    except (TypeError, ValueError):
        return None
    if p in SUSTAIN_DB12:
        return SUSTAIN_DB12[p]
    # ⚠ **未实测的音色一律"判不了"**（返回 None），不许拿族兜底值当判据 ——
    #   第一版我用兜底（衰减族 0.30 / 其余 0.60）跑全库，"只响 0.几秒"从 2 首误报成 **8 首**
    #   （管乐/合成 71/73/75/80/81 被当成"衰减型"）。要用就补测、加进 `SUSTAIN_DB12`。
    return None


def is_decay_timbre(prog):
    if prog is None:
        return False
    try:
        p = int(prog)
    except (TypeError, ValueError):
        return False
    return any(lo <= p <= hi for lo, hi in DECAY_FAMILIES)


def register_gaps(song):
    """逐段算「旋律最低音 − 和弦最高音（渲染后）」—— 就是 `GAP_MIN..GAP_MAX` 那条判据的量。

    返回 `[(段名, 旋律最低, 和弦最高, gap)]`；缺数据（没旋律 / 没和弦 / 和弦不在表里）的段跳过。

    ⚠ 为什么单独抽出来：`new_song` 生成后要**按同一口径自动修正**（`new_song.fix_melody_register`），
    抄第二份 = 埋一处漂移（CONVENTION §1）。Piano/Hook 会被引擎整体 −12，所以按**渲染后**比。
    """
    ch = song.get('chords') or {}
    out = []
    for sec in song.get('sections', []):
        mel = (song.get('melody') or {}).get(sec.get('melody'))
        chs = sec.get('chords') or []
        if not mel or not chs:
            continue
        lo = min(int(x[3]) for x in mel)
        chi = max((max(ch[c][1]) - 12 for c in chs if c in ch), default=None)
        if chi is None:
            continue
        out.append((sec['name'], lo, chi, lo - chi))
    return out


def register_top_gaps(song):
    """逐段算「旋律**最高**音 − 和弦最高音（渲染后）」—— 用户 2026-09-22："感觉这个音有点高了"。

    ⚠ 为什么要单列这一侧：`register_gaps` 只看**最低音**，于是"开头冲到 A6"这种
    **最高音飘太高**的问题它一声不吭 —— 实测 `20_piano_rain` 的 Intro 开头是
    **93(A6) / 88(E6) / 90(F#6)**，比该段和弦最高音（59 = B3）高 **34 半音**（近 3 个八度），
    而"最低音"那一侧 74−59 = 15 判合规 → **盲区**（用户听出来才发现）。
    正统织体两侧都该落在 `GAP_MIN..GAP_MAX` 附近。
    返回 `[(段名, 旋律最高, 和弦最高, top_gap)]`。
    """
    ch = song.get('chords') or {}
    out = []
    for sec in song.get('sections', []):
        mel = (song.get('melody') or {}).get(sec.get('melody'))
        chs = sec.get('chords') or []
        if not mel or not chs:
            continue
        hi = max(int(x[3]) for x in mel)
        chi = max((max(ch[c][1]) - 12 for c in chs if c in ch), default=None)
        if chi is None:
            continue
        out.append((sec['name'], hi, chi, hi - chi))
    return out


def check(song):
    """`song` = 已 load 的 song.json dict → 问题清单（空 = 三项都过）。

    只在**数据层**判（不读 MIDI），所以生成期间每轮都能跑（毫秒级）。
    """
    out = []
    ch = song.get('chords') or {}
    progs = song.get('programs') or {}

    def _prog(tr):
        v = progs.get(tr)
        return (v[0] if isinstance(v, (list, tuple)) and v else v)

    # ---- ① 音区间距（量法在 `register_gaps`，与 `new_song` 的自动修正共用一份）----
    for (nm, lo, chi, gap) in register_gaps(song):
        if gap > GAP_MAX:
            out.append('音区：%s 段旋律最低 %d 比和弦最高(渲染后 %d) 高 %d 半音'
                       '（正统 %d–%d）→ 中间空掉' % (nm, lo, chi, gap, GAP_MIN, GAP_MAX))
        elif gap < 0:
            out.append('音区：%s 段旋律最低 %d 落在和弦最高(渲染后 %d) 之下 →'
                       ' 与左手撞在一起' % (nm, lo, chi))

    # ---- ①b 旋律**最高**音那一侧（2026-09-22 补：用户"感觉这个音有点高了"）----
    # ① 只看最低音 ⇒ 开头冲到 A6（比伴奏高 34 半音）它也判合规 —— 这侧一直是盲区。
    for (nm, hi, chi, tg) in register_top_gaps(song):
        if tg > GAP_MAX:
            out.append('音区：%s 段旋律**最高** %d 比和弦最高(渲染后 %d) 高 %d 半音'
                       '（上限 %d）→ 飘太高（听感"这个音有点高了"）；'
                       '修法 = 把超出上限的音**降八度**（`new_song.fix_melody_register` 已自动做）'
                       % (nm, hi, chi, tg, GAP_MAX))

    # ---- ② 撞音（数据层判据：同族音色 + **音域真的重叠**）----
    # ⚠ 光"同族 + 都开着"不够 —— 实测 Piano(渲染后 40–69) 与 Glock(64–98) 同族但只重叠
    #   5 个音；真正的 85 处撞音是 Piano(40–69) 与 Hook(渲染后 40–59) 这种**大面积重叠**。
    #   所以判据带上音域：用引擎自己的 `TR_RANGE` 做边界，再用实际取音范围估。
    import song_engine as _SE

    def _range_of(tr):
        """该轨的实际取音范围（估）：和弦音组 ± 引擎的整轨移调（`TR_SHIFT`）"""
        if tr == 'Melody':
            ps = [int(x[3]) for mel in (song.get('melody') or {}).values() for x in mel]
            return (min(ps), max(ps)) if ps else None
        if tr == 'Bass':
            bs = [int(v[0]) for v in ch.values()] or [40]
            return (min(bs), max(bs) + 12)          # 引擎的 simple 风格会走 40–55 一带
        ts = [int(t) for v in ch.values() for t in v[1]]
        if not ts:
            return None
        sh = _SE.TR_SHIFT.get(tr, 0)
        return (min(ts) + sh, max(ts) + sh)

    layers = []
    for tr in ('Piano', 'Hook', 'Arp', 'Strings'):
        on = any((s.get('arr') or {}).get(k) for s in song.get('sections', [])
                 for k in ({'Piano': ('piano',), 'Hook': ('uku',),
                            'Arp': ('arp', 'shimmer'), 'Strings': ('strings',)}[tr]))
        if on and is_decay_timbre(_prog(tr)):
            rg = _range_of(tr)
            if rg:
                layers.append((tr, rg))
    # 逐对检查音域重叠半音数（>5 才算真会撞）
    for i in range(len(layers)):
        for j in range(i + 1, len(layers)):
            (t1, r1), (t2, r2) = layers[i], layers[j]
            ov = min(r1[1], r2[1]) - max(r1[0], r2[0])
            if ov > 5:
                out.append('撞音：%s(%d–%d) 与 %s(%d–%d) 音域重叠 %d 半音，且同属'
                           '"靠衰减收尾"音色族 → 同刻同音高会发浑'
                           '（实测：抬八度或砍一层，148 → 28 处）'
                           % (t1, r1[0], r1[1], t2, r2[0], r2[1], ov))

    # ---- ③ 长音层 ----
    seen = set()
    for sec in song.get('sections', []):
        a = sec.get('arr') or {}
        for tr, key, why in (('Pad', 'pad', '4.1 拍长音'),
                             ('Strings', 'strings', '4.1 拍长音'),
                             ('Arp', 'shimmer', '4 拍长音（塞在 Arp 轨）')):
            if a.get(key) and is_decay_timbre(_prog(tr)) and (tr, key) not in seen:
                seen.add((tr, key))
                out.append('长音层：%s 轨（GM %s）是"靠衰减收尾"音色，但开着 `arr.%s`（%s）'
                           '→ 按住不放会发浑' % (tr, _prog(tr), key, why))

    # ---- ④ 主奏"写的比响的长"（用户："只响 0.几秒的音"）----
    # 判据：主奏音色掉 12dB 的时间 << 该段旋律真正写的时值 → 写的是长音、听感只有 0.几秒。
    # 反例校验（全库 30 首，2026-09-22）：只有 `20_piano_rain` 命中过（改前），
    # 其余曲子的旋律时值中位 0.2~0.6 秒（快速走句）→ 不触发。**这条判据平时不响是对的**。
    spb = 60.0 / float(song.get('bpm') or 120)
    for sec in song.get('sections', []):
        a = sec.get('arr') or {}
        prog = a.get('melody_prog')
        if prog is None:
            prog = _prog('Melody')
        # ⚠ 慢起音要**先判**：弦乐 48/62 的"掉 12dB"是 inf，写在下面那个 continue 之后
        #   就永远走不到（自检 `sustain_criteria` 的断言④当场抓到过这个 bug）。
        if prog in SLOW_ATTACK:
            out.append('慢起音：%s 段主奏 GM %s 起音实测 %.0fms（判据 ≤20ms）→ 会"慢半拍"'
                       % (sec['name'], prog, SLOW_ATTACK[prog] * 1000))
        d12 = db12(prog)
        mel = (song.get('melody') or {}).get(sec.get('melody'))
        if d12 is None or d12 == float('inf') or not mel:
            continue
        med_s = sorted(float(x[2]) for x in mel)[len(mel) // 2] * spb
        if med_s > 2.0 * d12:
            out.append('只响 0.几秒：%s 段主奏 GM %s 掉 12dB 只要 %.2fs，但该段旋律时值中位'
                       ' %.3fs（%.2f 拍）→ 写的是长音、听感只有 0.几秒。换持续型：**GM 4**'
                       '（钢琴族内 · 起音 1ms）✓ / 弦乐 48（起音 427ms）✗ 会"慢半拍"；'
                       '段级用 `sections[i].arr.melody_prog`（改 `programs.Melody` 会被覆盖）；'
                       '换完**连带**压短音层 `patterns.piano_stab_vel`（→ PITFALLS 235/236）'
                       % (sec['name'], prog, d12, med_s, med_s / spb))
    # ⚠ **不再单列一条"镫"判据**：试过按"衰减型短音轨 + 持续型主奏"判，全库 **27/30 首**
    #   命中（恒真 = 噪声，技能口径 <30% 才有区分度）—— 因为"钢琴轨 + 非钢琴主奏"是常态。
    #   "镫"是**换持续型主奏之后的伴生问题**，所以并进上面那条（只在真要换音色时出现）；
    #   要定位"具体在哪一秒"，用 `scripts/probe_sustain.py`（走 `stab_candidates()`，带力度判据）。
    return out


STAB_BEAT = 0.35        # 多短算"短促音"（引擎的钢琴反拍音是 0.28 拍）


def stab_candidates(song, model, spb):
    """从**已渲染的 MIDI**里捞出"镫"的具体位置（给 `probe_sustain.py` 用）。

    `check()` 的第 ⑤ 项只报"这一段有风险"；这里回答"**在哪一秒**"。
    返回 [(时刻秒, 轨, 音高, 力度, 时值拍, 说明)]，按时刻排序。
    """
    progs = song.get('programs') or {}

    def _p(tr):
        v = progs.get(tr)
        return (v[0] if isinstance(v, (list, tuple)) and v else v)

    mel_prog = _p('Melody')
    m12 = db12(mel_prog)
    if m12 is None or m12 <= 0.5:
        return []                     # 主奏本身就是衰减型 → 短音不会"跳出来"（它的病是别的）
    notes = []
    for tr in model['tracks']:
        for n in tr.get('notes', []):
            notes.append((n[0] * spb, n[1] * spb, int(n[2]), int(n[3]), tr['name']))
    mel = [x for x in notes if x[4] == 'Melody']
    out = []
    for (s, d, p, v, nm) in notes:
        if nm == 'Melody' or d / spb > STAB_BEAT:
            continue
        q12 = db12(_p(nm))
        if q12 is None or q12 > DECAY_MAX:
            continue                  # 音头不重的轨不会"镫"
        co = [x for x in mel if x[0] <= s <= x[0] + x[1]]
        if not co:
            continue
        vmax = max(x[3] for x in co)
        if v < 0.8 * vmax:
            continue                  # 比主奏轻太多 → 跳不出来
        out.append((s, nm, p, v, d / spb, v - vmax,
                    '同刻主奏 GM %s（持续型）力度 %d' % (mel_prog, vmax)))
    return sorted(out, key=lambda r: -r[5])       # 按"比主奏响多少"排（越前越可疑）


if __name__ == '__main__':
    import io
    import json
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cli_utf8 as _cu
    _cu.setup()
    for p in sys.argv[1:]:
        d = json.load(io.open(p, encoding='utf-8'))
        probs = check(d)
        print('%s → %s' % (p, ('和谐三项都过' if not probs else '%d 个问题' % len(probs))))
        for x in probs:
            print('  · ' + x)
