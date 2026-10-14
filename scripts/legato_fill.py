#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""legato_fill.py —— **把内声部的空隙补满**（opt-in `patterns.legato_fill`，默认关）

## 为什么要有它（2026-10-13，用户口径）

用户听 A/B 时的原话：**"有时候连续有时候不连续也没有衔接好"** + **"是中间的音符"**。
本机实测（`D:\test\_tmp\gen-joint\inner_notes.py`，逐轨量音长与断连）：

| 轨 | 我方（daily 基线）音长中位 | 极短音(≤0.3 拍)占比 | 真人 `BGM29` 音长中位 / 极短音 |
|---|---|---|---|
| **Bass** | **0.18 拍** | **87.1%** | 0.51 拍 / 3.9% |
| **Piano** | **0.28 拍** | **79.0%** | 0.51 拍 / 28.3% |
| Hook | 0.22 拍 | — | 0.48 拍 / 13.5% |
| Melody | 0.91 拍 | 4.5% | 0.32 拍 / 48.4% |
| Arp / Pad / Strings | 0.90 / 4.05 / 4.05 | 0% | — |

⇒ **内声部（贝斯/钢琴/Hook）是一串 0.18~0.28 拍的"点"**，点和点之间是空隙；
而 Pad/Strings 却是 4 拍长音 —— 于是同一首里**有时候连着、有时候断开**，中间的音符接不上。
`patterns.staccato` / `piano_stab_dur` 只改"更短"，**没有一个是把空隙补满的**。

## 做什么

**只延长、绝不缩短、绝不重叠**：对每条目标轨，把音的时值补到
`min(起音 + cap, 下一个起音 − 1ms)`（`cap` = `patterns.legato_fill`，单位=拍）。
音高 / 力度 / 起音**一个都不动** —— 所以和声、节奏型、守卫读数都不受影响。

| 轨 | 是否补 |
|---|---|
| `Bass` / `Piano` / `Hook` / `Arp` / `Glock` / `Strings` / `Pad` | ✅（本来就是"音"） |
| `Perc` / `Drums` | ❌ **绝不补**（打击乐补长 = 变成铃鼓/镲的糊声） |

## 用法

```json
"patterns": { "legato_fill": 0.5 }
```
`0` / 缺省 = **关**（老行为逐字节不变）。实测（daily，cap=0.5）：Bass 0.18→**0.48 拍**、
极短音 87.1%→**4.3%**、Piano 0.28→**0.48 拍** / 79%→**5.3%** —— 与真人（0.51 / 3.9%）
同一量级。守卫 `selftest.t_legato_fill_contracts` + `mutation_check.py` 第 103 组。
"""

import os
import sys

#: 允许补空隙的轨（**打击乐绝不在内**：补长 = 糊）
TARGETS = ('Bass', 'Piano', 'Hook', 'Arp', 'Glock', 'Strings', 'Pad')
EPS = 1e-3


def fill(events, cap, tracks=None):
    """按轨补满空隙（`cap` = 最多延长到多少拍；返回**新的** dict）。

    `events`：`{轨名: [(t, dur, mid, vel), ...]}`，时间单位是拍（引擎口径）。
    规则：`dur_new = max(dur, min(t + cap, next_t − 1ms) − t)` —— **只增不减、不重叠**。

    ⚠ `tracks=None` 而不是 `tracks=TARGETS`：**默认参数在函数定义时求值**，写成默认值会让
    `TARGETS` 变成"定义时那份"的闭包引用 —— 变异的属性注入（`Mut(LF,'TARGETS',…)`）**打不进去**，
    判据于是永远抓不到"打击乐被补长"（本轮实测**漏了**一次，才改成现在这样）。
    """
    if not cap or cap <= 0:
        return events
    tr = TARGETS if tracks is None else tracks
    out = {}
    for k, lst in (events or {}).items():
        if k not in tr or not lst:
            out[k] = list(lst or [])
            continue
        srt = sorted(lst, key=lambda x: (x[0], x[2]))
        new = []
        for i, (t, dur, m, v) in enumerate(srt):
            limit = (srt[i + 1][0] - EPS) if i + 1 < len(srt) else (t + cap)
            want = min(t + cap, limit) - t
            new.append((t, max(dur, want) if want > dur else dur, m, v))
        out[k] = new
    return out


def stats(events, cap, tracks=None):
    """补了多少（给调用方/守卫看，不许静默）。`tracks=None` 同 `fill`（调用时解析）。"""
    if not cap or cap <= 0:
        return {'tracks': 0, 'grown': 0, 'add_beats': 0.0}
    tr_set = TARGETS if tracks is None else tracks
    full = fill(events, cap, tr_set)
    tr = gr = 0
    add = 0.0
    for k in full:
        if k not in tr_set:
            continue
        tr += 1
        old = {round(x[0], 4): x[1] for x in (events.get(k) or [])}
        for (t, d, m, v) in full[k]:
            o = old.get(round(t, 4))
            if o is not None and d > o + 1e-9:
                gr += 1
                add += d - o
    return {'tracks': tr, 'grown': gr, 'add_beats': round(add, 2)}


# ------------------------------------------------------------------ 自检
def _selftest():
    ok, bad = 0, []

    def chk(name, cond):
        nonlocal ok
        if cond:
            ok += 1
        else:
            bad.append(name)

    ev = {'Bass': [(0.0, 0.18, 40, 90), (0.25, 0.18, 40, 80), (0.5, 0.18, 43, 80)],
          'Piano': [(0.5, 0.28, 60, 58), (2.5, 0.28, 64, 58)],
          'Perc': [(0.0, 0.12, 36, 90), (0.5, 0.12, 42, 90)],
          'Pad': [(0.0, 4.05, 60, 40)]}
    chk('cap=0 惰性', fill(ev, 0) is ev)
    got = fill(ev, 0.5)
    # ① 贝斯补到"下一个起音前 1ms"（0.25 网格 → 0.249）
    chk('贝斯补到下一音前', abs(got['Bass'][0][1] - 0.249) < 1e-6)
    # ② 最后一音补到 cap（0.5 → 1.0）
    chk('末音补到 cap', abs(got['Bass'][2][1] - 0.5) < 1e-6)
    # ③ 钢琴 0.5 → 2.5 之间补到 cap（0.5），不是补到 2.0
    chk('钢琴按 cap 补', abs(got['Piano'][0][1] - 0.5) < 1e-6)
    # ④ 打击乐一个都不许动
    chk('打击乐不动', got['Perc'] == ev['Perc'])
    # ⑤ 长音（Pad 4.05 > cap）不许被缩短
    chk('长音不缩短', got['Pad'][0][1] == 4.05)
    # ⑥ 只增不减：全部 dur ≥ 原值
    chk('只增不减', all(got[k][i][1] >= ev[k][i][1]
                        for k in got for i in range(len(got[k]))))
    # ⑦ 不重叠：同轨相邻音 t+dur ≤ 下一音 t
    okk = True
    for k in ('Bass', 'Piano'):
        s = got[k]
        for i in range(len(s) - 1):
            if s[i][0] + s[i][1] > s[i + 1][0] + 1e-9:
                okk = False
    chk('不重叠', okk)
    st = stats(ev, 0.5)
    chk('统计口径', st['grown'] == 5 and st['tracks'] == 3)   # Bass 3 + Piano 2（Pad 已够长不算）
    return ok, bad


def main():
    if '--selftest' in sys.argv:
        ok, bad = _selftest()
        print('legato_fill selftest: %d/%d PASS%s'
              % (ok, ok + len(bad), '' if not bad else ' | FAIL: ' + ', '.join(bad)))
        return 1 if bad else 0
    print(__doc__)
    return 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（守卫 console_encoding_safe 盯着）
if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main())
