#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""旋律"换气"判据（**全链唯一口径**）—— 自检 `melody_breathing` 与修复工具 `fix_breathing.py` 共用。

**口径来源（用户校准）**："不一定每一段都要停顿，而是**太长的话**要停顿换气。"
所以量的是**最长不间断段**，不是"每段有没有停"：
  · 相邻音之间缝隙 < `BREATH_GAP` 拍 → 视为连着（连奏不断句）
  · 缝隙 ≥ `BREATH_GAP` 拍 → 算换气（真停顿）
  · 最长不间断段超过 `BREATH_SEC` 秒 → 提示/需要修

用**秒**而不用小节：慢曲 8 小节 = 30 秒、快曲 = 9 秒，按小节会随速度漂。
"""
import json
import os

import song_engine

BREATH_SEC = 20.0      # 超过这么多秒没换气 → 需要停顿
BREATH_GAP = 0.5       # 缝隙阈值（拍）
PHRASE_BARS = 4        # 修复时优先在"4 小节乐句边界"换气（音乐上的常识位置）


def intervals(j2):
    """全曲旋律区间：`([(起拍, 止拍)], 一小节拍数, 每拍秒数)`（按 sections 顺序拼接）。"""
    bb = song_engine.bar_beats(j2)
    spb = 60.0 / float(j2.get('bpm') or 120.0)
    pos, iv = 0.0, []
    for sec in j2['sections']:
        bars = sec['bars']
        for x in (j2['melody'].get(sec['melody']) or []):
            if 0 <= x[0] < bars:
                s = pos + x[0] * bb + x[1]
                iv.append((s, s + x[2]))
        pos += bars * bb
    iv.sort()
    return iv, bb, spb


def runs(iv, gap=BREATH_GAP):
    """把区间合并成"不间断段"（缝隙 < gap 视为连着）。"""
    if not iv:
        return []
    out, cur = [], list(iv[0])
    for (s, e) in iv[1:]:
        if s - cur[1] < gap:
            cur[1] = max(cur[1], e)
        else:
            out.append(tuple(cur))
            cur = [s, e]
    out.append(tuple(cur))
    return out


def long_runs(j2, sec=None, gap=None):
    """需要换气的段：[(起拍, 止拍, 秒数)]（按出现顺序）。

    ⚠ 默认值在调用时取模块常量（不是 def 时捕获）—— 否则注入用例改不动阈值。
    """
    sec = BREATH_SEC if sec is None else sec
    gap = BREATH_GAP if gap is None else gap
    iv, bb, spb = intervals(j2)
    if not iv:
        return [], bb, spb
    return ([(s, e, (e - s) * spb) for (s, e) in runs(iv, gap) if (e - s) * spb > sec],
            bb, spb)


def load(path_or_dir):
    p = path_or_dir
    if os.path.isdir(p):
        p = os.path.join(p, 'song.json')
    return json.load(open(p, encoding='utf-8'))
