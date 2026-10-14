#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""phrase_vary.py —— **伴奏逐句换句型**（opt-in `patterns.phrase_vary`，默认关）

## 为什么要有它（2026-10-13，用户口径）

用户听新生成的曲子：**"整体节奏还是这样的，以前不是改了吗""感觉背景一直重复"**。
本机实测（`D:\test\_tmp\gen-joint\bg_repeat.py` / `bg_repeat_detail.py`）：

| 项 | 我方 15 首生成曲 | 真人扒谱版（bgm23/bgm35/BGM29/已至） |
|---|---|---|
| 一个 4 小节句型占全曲的比例 | **20~40%**（Bass 20~28% · Piano 23~31% · Perc 26~35%） | — |
| 句型去重率（不同句型/非空乐句） | **0.26~0.60** | **0.99~1.00** |
| 跨曲节奏指纹相似度（16 格直方图两两余弦） | **中位 0.974**（90% 的对 ≥0.90） | — |

根因（读代码得到的，不是猜）：`song_engine.piano_part` 的小节内容是**写死的两个反拍和弦短音**
（`ALT = ((0.5, B-1.5),) * 3 + ((0.5, B-1.0),)`），`bass_part` 的句型由**整曲一个**
`bass_style` 决定 ⇒ 伴奏每小节一字不差地重复，只有第 4 小节动半拍。
已有工具 `vary_comp`（`patterns.comp_vary`，2026-10-05）**太弱**：每个乐句最多只动
**一个短音**，实测整首只改 ~25 个音 ⇒ 用户听不出变化，而且 15 首里**一个都没开**。

## 做什么

**只换节奏落点，不动音高、不动和弦、不动力度、不增删音高集合**（和弦贴合与"撞音"守卫因此不受影响）：

* **钢琴（`arr.piano`）逐乐句轮换反拍位置**：从 `COMP_BANK_44`（4/4）/ `COMP_BANK_34`（3/4）
  里按 `(曲名 seed, 乐句序号)` 挑一组落点，替换该小节的落点；含**留白句**
  （只留一个和弦短音）与"原样"句（保证音乐上仍有锚点）。
* **贝斯（`arr.bass`）句末轻变化**（level 2）：句末小节最后一个音——长音则**缩短留出空隙**，
  短音则**前移 0.25 拍**（推进），二选一按 hash。⚠ 贝斯是律动骨架，只动句末、不动强拍。

## 等级（`patterns.phrase_vary`）

| 值 | 内容 |
|---|---|
| `0` / 缺省 | **关** = 老行为**逐字节不变** |
| `1` | 钢琴逐乐句轮换落点 |
| `2` | 1 + 贝斯句末轻变化 |

## 安全边界（都写死在代码里，守卫 `t_phrase_vary_contracts` 断言）

1. **不动小节第一拍**（`t == 0` 的事件永不触碰）；
2. **不动强拍**（按拍号：4/4 = 0、2 拍；3/4 = 0 拍；6/8 = 0、1.5 拍）—— 和弦贴合就靠这些音；
3. **不改音高、不改力度**（只改 `t` 与 `dur`）；
4. 落点必须仍落在本小节内（`0 < t < B`）；
5. 落点组数不够（<2 组）时**原样返回**（例如极简段落只留一个长音）。

## 用法

```bash
# song.json:  "patterns": {"phrase_vary": 2}
python scripts\song_engine.py songs\<曲>\song.json
python scripts\phrase_vary.py --selftest        # 纯函数自检（6 组断言）
```

守卫：`selftest.py#t_phrase_vary_contracts` + `mutation_check.py` 第 102 组。
"""

import os
import sys

#: 4/4 钢琴反拍落点库（每个条目 = 该小节要落的**组数**；`(a,)` = 留白句只留第一组）
COMP_BANK_44 = (
    (0.5, 2.5),      # 原样（老行为；留一个锚点句）
    (0.5, 3.5),
    (1.5, 3.5),
    (0.5, 2.0),
    (1.0, 3.0),
    (0.5, 3.0),
    (2.0, 3.5),
    (1.5, 2.5),
    (0.5,),          # 留白句
)
#: 3/4（圆舞曲）落点库：只落在第 2、3 拍上（`piano_part` 的华尔兹写法）
COMP_BANK_34 = (
    (1.0, 2.0),
    (0.5, 2.0),
    (1.0, 2.5),
    (1.5, 2.5),
    (1.0,),
)
#: 6/8 的"拍"仍是四分音符（`_norm_meter` 口径），落点库按 3 个四分算
COMP_BANK_68 = COMP_BANK_34


def _h(seed, salt):
    """(seed, salt) → 稳定哈希（**不能用 `hash()`**：CPython 每进程加盐）。"""
    import zlib
    return zlib.crc32(('%s|%d' % (salt, int(seed) & 0xFFFFFFFF)).encode('utf-8')) & 0xFFFFFFFF


def strong_beats(meter):
    """本模块自己的一份强拍表（**不 import `song_engine`**：那会形成循环导入）。

    ⚠ 口径**逐字照抄 `song_engine.strong_beats`**（唯一真源在那里；守卫
    `t_phrase_vary_contracts` 会对 5 个拍号逐一对账两处相等）：
    偶数拍 → `[0, 半小节]`（`[4,4]`→`[0,2]` · `[6,8]`→`[0,1.5]` · `[2,4]`→`[0,1]`）；
    奇数拍 → `[0]`；`[5,4]` 例外 → `[0,3]`（3+2 分组）。
    """
    num, den = (meter or (4, 4))[:2]
    num, den = int(num), int(den)
    unit = 4.0 / den
    if (num, den) == (5, 4):
        return (0.0, 3.0)
    if num % 2:
        return (0.0,)
    return (0.0, round(num / 2.0 * unit, 4))


def pick_bank(B, meter):
    if int(round(B)) % 3 == 0 and 3 <= int(round(B)) <= 3:
        return COMP_BANK_34
    num = int((meter or (4, 4))[0])
    if num == 3:
        return COMP_BANK_34
    if num == 6:
        return COMP_BANK_68
    return COMP_BANK_44


def vary_piano(events, i, B, seed, meter=(4, 4), level=1, span=4):
    r"""钢琴轨：把本小节的**反拍落点整组**换到本乐句抽中的落点上（音高/力度/组内音数不变）。

    ⚠ **锚点音（强拍 / 第 1 拍上的音）原样保留**，只换**其余落点组**。
    这一条是实测踩出来的：`piano_part` 每小节除两个反拍和弦短音外，还有**一条 t=0 的持续高音**
    （`(0.0, 1.5, 72, 54)`）—— 第一版写成"第一拍上有音就整层返回"，于是
    **20 次调用 0 次改动、产物与关掉时逐字节相同**（读回验证才发现；`D:\test\_tmp\gen-joint\probe_pv.py`）。

    返回新 list（不改入参）。没有可换的落点组 → **原样返回**。
    """
    out = list(events or [])
    if level < 1 or not out:
        return out
    sb = strong_beats(meter)
    groups = {}
    for e in out:
        groups.setdefault(round(float(e[0]), 4), []).append(e)
    anchors = [o for o in groups
               if o <= 1e-9 or any(abs(o - s) < 1e-6 for s in sb)]
    movable = sorted(o for o in groups if o not in anchors)
    if not movable:
        return out
    bank = pick_bank(B, meter)
    pos = bank[_h(seed, 'pv_p%d' % (i // span)) % len(bank)]
    new = []
    for o in sorted(groups):                     # 锚点音原样带回
        if o in anchors:
            new.extend(groups[o])
    for k, o in enumerate(movable):
        if k >= len(pos):
            continue                             # 留白句：多出来的落点组撤掉
        tgt = float(pos[k])
        if not (0.0 < tgt < float(B) - 1e-9):    # 落点必须仍在本小节内
            tgt = o
        for (t, dur, m, v) in groups[o]:
            new.append((tgt, dur, m, v))
    return sorted(new, key=lambda x: (x[0], x[2]))


def vary_bass(events, i, B, seed, meter=(4, 4), level=2, span=4):
    """贝斯：**只在乐句末小节**做一处轻变化（缩末音时值留空隙 / 末音前移 0.25 拍）。

    强拍与第一拍永不触碰（贝斯是和声/律动锚点）。返回新 list。
    """
    out = list(events or [])
    if level < 2 or not out or (i % span) != span - 1:
        return out
    sb = strong_beats(meter)
    cand = [k for k, e in enumerate(out)
            if not any(abs(float(e[0]) - s) < 1e-6 for s in sb) and float(e[0]) > 1e-9]
    if not cand:
        return out
    k = max(cand, key=lambda x: out[x][0])     # 最后一个非强拍音
    t, dur, m, v = out[k]
    if _h(seed + i, 'pv_bass') % 2 == 0:
        if dur > 1.0:                          # 长音 → 缩到 1 拍，句尾留出空隙
            out[k] = (t, 1.0, m, v)
        else:                                  # 本来就是短音 → 不动（留白交给别的句）
            return out
    else:
        nt = t + 0.25
        if 0.0 < nt < float(B) - 1e-9:         # 推进 0.25 拍，但仍在本小节内
            out[k] = (nt, dur, m, v)
    return out


# ------------------------------------------------------------------ 自检
def _selftest():
    ok, bad = 0, []

    def chk(name, cond):
        nonlocal ok
        if cond:
            ok += 1
        else:
            bad.append(name)

    B = 4.0
    # ① level=0 → 逐字节不动
    base = [(0.5, 0.28, 60, 58), (0.5, 0.28, 64, 58), (0.5, 0.28, 67, 58),
            (2.5, 0.28, 60, 58), (2.5, 0.28, 64, 58), (2.5, 0.28, 67, 58)]
    chk('level=0 惰性', vary_piano(base, 0, B, 7, level=0) == base)
    # ② 真·钢琴小节（含 t=0 的持续高音，实测形状）：
    #    锚点音必须原样留下，反拍两组才换 —— 第一版"第一拍有音就整层返回"实测 20 次调用 0 次改动
    real = [(0.5, 0.28, 63, 58), (0.5, 0.28, 67, 58), (2.5, 0.28, 63, 58),
            (2.5, 0.28, 67, 58), (0.0, 1.5, 72, 54)]
    chk('锚点音(t=0)原样保留', (0.0, 1.5, 72, 54) in vary_piano(real, 0, B, 7, level=1))
    chk('锚点音在真小节里也不动', vary_piano(real, 0, B, 7, level=1) != real)
    got = vary_piano(base, 0, B, 7, level=1)
    chk('音高不新增', all(got.count(x) <= base.count(x) for x in set(got))
        and {x[2] for x in got} <= {x[2] for x in base})
    chk('落点在本小节内', all(0.0 < x[0] < B for x in got))
    chk('组数不超原组数', len({round(x[0], 4) for x in got}) <= 2)
    # ③ 不同乐句抽到不同落点（至少两个乐句不同）—— "逐句换句型"的判据
    seen = {tuple(sorted({round(x[0], 4) for x in vary_piano(base, p * 4, B, 7, level=1)}))
            for p in range(8)}
    chk('逐句有变化', len(seen) >= 2)
    # ④ 只有锚点音（没有可换的落点组）→ 原样返回
    b2 = [(0.0, 1.5, 72, 54), (2.0, 0.5, 43, 90)]
    chk('无可换落点则不动', vary_piano(b2, 0, B, 7, level=1) == b2)
    # ⑤ 贝斯：只在句末小节动，强拍不动，且不改音高
    bs = [(0.0, 1.0, 40, 90), (1.0, 0.5, 43, 90), (2.0, 1.0, 45, 90), (3.0, 1.5, 47, 90)]
    chk('贝斯非句末不动', vary_bass(bs, 0, B, 7, level=2) == bs)
    bv = vary_bass(bs, 3, B, 7, level=2)
    chk('贝斯不改音高/强拍', sorted(x[2] for x in bv) == sorted(x[2] for x in bs)
        and all(any(abs(x[0] - s) < 1e-6 for s in (0.0, 2.0)) for x in bv
                if x[2] in (40, 45)))
    chk('贝斯动了末音', bv != bs)
    # ⑥ 强拍表与引擎同口径（守卫会另核一处；这里自检 3 个拍号）
    chk('强拍表', strong_beats((4, 4)) == (0.0, 2.0) and strong_beats((3, 4)) == (0.0,)
        and strong_beats((6, 8)) == (0.0, 1.5) and strong_beats((5, 4)) == (0.0, 3.0))
    return ok, bad


def main():
    if '--selftest' in sys.argv:
        ok, bad = _selftest()
        print('phrase_vary selftest: %d/%d PASS%s'
              % (ok, ok + len(bad), '' if not bad else ' | FAIL: ' + ', '.join(bad)))
        return 1 if bad else 0
    print(__doc__)
    return 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（守卫 console_encoding_safe 盯着）
if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main())
