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


def is_decay_timbre(prog):
    if prog is None:
        return False
    try:
        p = int(prog)
    except (TypeError, ValueError):
        return False
    return any(lo <= p <= hi for lo, hi in DECAY_FAMILIES)


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

    # ---- ① 音区间距 ----
    for sec in song.get('sections', []):
        mel = (song.get('melody') or {}).get(sec.get('melody'))
        chs = sec.get('chords') or []
        if not mel or not chs:
            continue
        lo = min(int(x[3]) for x in mel)
        # Piano/Hook 会被引擎整体 −12，所以按"渲染后"的和弦最高音比
        chi = max((max(ch[c][1]) - 12 for c in chs if c in ch), default=None)
        if chi is None:
            continue
        gap = lo - chi
        if gap > GAP_MAX:
            out.append('音区：%s 段旋律最低 %d 比和弦最高(渲染后 %d) 高 %d 半音'
                       '（正统 %d–%d）→ 中间空掉' % (sec['name'], lo, chi, gap,
                                                 GAP_MIN, GAP_MAX))
        elif gap < 0:
            out.append('音区：%s 段旋律最低 %d 落在和弦最高(渲染后 %d) 之下 →'
                       ' 与左手撞在一起' % (sec['name'], lo, chi))

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
    return out


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
