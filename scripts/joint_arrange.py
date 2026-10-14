#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""joint_arrange.py —— **联合句法层**：一次看全部轨，按乐句做"让位 / 收束"（opt-in）

## 为什么要有这一层（2026-10-13）

用户口径：**"音乐软件生成音乐功能看看能不能直接全部分轨一起做，发现分开做的效果没达到预期
（音乐性不够）"**。而引擎原来是**逐层各写各的音**（`piano_part` / `bass_part` / `perc_part` /
`pad_part` / …，见 `song_engine.build_events`），跨轨耦合只有 4 处
（`arr_share` 筛层 · `space_on` 削薄 · `harmony_below` · `yield_to_melody`）——
**"谁在动"是逐段联合决定的，"每轨弹什么"不是**。

本模块是 M1 的**第一步，只做一件能独立量的事**（技能纪律：一次只改一个维度 §18）：

**乐句句尾收束**。依据（本机实测，`D:\test\_tmp\gen-joint\m0_summary.py`）：
我方 15 首生成曲的"每 4 小节句尾那一拍**主奏没有起音**"比例 **中位 0.00（IQR 0.11）**，
而真人多轨 MIDI **中位 0.32**、扒谱版 **中位 0.68**。
⇒ 乐句感（句尾落住、其它层让位）在我方产物里**系统性缺失**，这是"音乐性不够"里能指认的一条。

## 做什么（只有 `patterns.joint_cadence` 为真才动；**默认关 = 全库逐字节不变**）

1. 每 `patterns.joint_phrase_bars`（默认 **4**）小节为一个乐句；
2. **句尾那一拍**：除保留层（默认 `Bass`/`Perc`，低频与鼓是节奏锚点）外，
   其余轨**不在该拍发起音**（= 让位；长音照旧延续，不砍持续音）；
3. **主奏收束**：把句尾前最后一个主奏音**延长到句尾**，且不与后面的起音重叠；
4. **打印逐乐句改动**（红线 5：不许静默）。

## 不做什么

* 不新增音高 · 不改和弦/编配/调式 · 不改力度（起伏归 `patterns.seg_fade` 与编配层）；
* 不动小节内的节奏型（那是 `bass_style` / `drum_grid` / 各 part 函数的职责）；
* **段界上主奏"早停留白"、且不撤伴奏**（见 `SKIP_SECTION_ENDS`）—— 段界的"渐弱/留白"
  归 `patterns.seg_fade` / `section_gap`。实测（2026-10-13，三轮才定下来）：
  ① 让位照做 + 延长到句尾 ⇒ `_ab_joint_sorrow` 边界「A」跳 **7.2dB**、两端渐弱只 **3.5dB**，
     守卫 `section_transition`（门限 4dB）当场留红 —— **本层引入的真回归，靠守卫抓到**；
  ② 整句跳过 ⇒ 效果几乎全丢（`battle` 句尾收束率 **47.1% → 5.9%**）；
  ③ **只撤伴奏、仍延长** ⇒ 两个读数几乎没变（7.2 / 3.5，说明真凶是"延长"而不是"让位"）；
  ④ ⇒ 正解：段界处**主奏早停**（留白，守卫明确允许的形态）+ 伴奏一个音不动。

## 用法

```bash
# 在 song.json 里开（opt-in）：
#   "patterns": {"joint_cadence": true, "joint_phrase_bars": 4}
python scripts\song_engine.py songs\<曲>\song.json     # 作曲（会打印 joint 改动表）
python scripts\joint_arrange.py --selftest            # 纯函数自检（不碰磁盘）
```

判据/守卫：`selftest.py#t_joint_cadence_contracts` + `mutation_check.py` 对应注入用例。
"""

import os
import sys

KEEP_DEFAULT = ('Bass', 'Perc')      # 句尾仍可发起音的层（低频 + 鼓：节奏/低频锚点）
LEAD_PRIORITY = ('Melody', 'Hook', 'Lead')
MAX_HOLD = 2.0                       # 收束最多延长多少拍（别把整句糊成一坨）
#: **乐句末落在段落边界上时：主奏"早停留白"，伴奏一个音都不动、主奏末音也不延长**
#: （2026-10-13 三轮实测定下来的）。段界的渐弱/留白有它自己的机制
#: （`patterns.seg_fade` / `section_gap`）—— 句法层不去动它。实测：
#: ① 让位+延长 → `_ab_joint_sorrow` 边界「A」跳 7.2dB / 两端渐弱 3.5dB（守卫留红）；
#: ② 整句跳过 → battle 收束率 47.1%→**5.9%**；③ 只撤伴奏（不延长）→ 7.2/3.5 几乎没变
#: ⇒ **真凶是"延长"**，所以段界处只"早停"、不延长。
SKIP_SECTION_ENDS = True


def phrase_ends(nbars, bar_beats, phrase_bars=4):
    """乐句结束的**绝对拍位**（不含第 0 个，即不在曲首收束）。"""
    out = []
    k = 1
    while k * phrase_bars < nbars:
        out.append(k * phrase_bars * bar_beats)
        k += 1
    return out


def pick_lead(ev, d=None):
    """主奏轨：`patterns.lead_track` > Melody/Hook/Lead > 音高中位最高的轨。"""
    pats = (d or {}).get('patterns') or {}
    named = pats.get('lead_track')
    if named and ev.get(named):
        return named
    for k in LEAD_PRIORITY:
        if ev.get(k):
            return k
    best, best_med = None, -1
    for k, lst in ev.items():
        if not lst:
            continue
        ps = sorted(x[2] for x in lst)
        med = ps[len(ps) // 2]
        if med > best_med:
            best, best_med = k, med
    return best


def apply(ev, d, quiet=False):
    """就地改 `ev`（dict：轨名 → [(t, dur, mid, vel)]，t/dur 单位是**拍**）。

    返回统计 dict（供调用方/守卫断言）。`ev` 里的列表会被替换成新列表（不改入参容器）。
    """
    B = float(d.get('bar_beats') or 4.0)
    pats = d.get('patterns') or {}
    pbar = int(pats.get('joint_phrase_bars') or 4)
    nbars = sum(int(s.get('bars') or 0) for s in (d.get('sections') or []))
    guard = tuple(pats.get('joint_keep') or KEEP_DEFAULT)
    lead = pick_lead(ev, d)
    stats = {'phrases': 0, 'yield': 0, 'hold': 0, 'hold_beats': 0.0, 'lead': lead,
             'keep': list(guard), 'phrase_bars': pbar, 'at_section_end': 0,
             'rest_at_section_end': 0}
    if not nbars or pbar < 2 or lead is None:
        return stats

    out = {k: list(v) for k, v in ev.items()}
    # **段界不归句法管**（2026-10-13 实测，守卫 `section_transition` 抓到的真回归）：
    # 乐句末若正好落在**段落边界**上，让位会把段末那几拍撤得更空 ⇒ 边界跳变反而更大
    # （实测 `117_sorrow_joint` 边界「A」跳 7.2dB、两端渐弱/渐入只 3.6dB > 门限 4dB 被留红）。
    # 段界的"渐弱/留白"有它自己的机制（`patterns.seg_fade` / `section_gap`），句法层**主动让开**。
    _sec_ends, _b = set(), 0
    for _s in (d.get('sections') or []):
        _b += int(_s.get('bars') or 0)
        _sec_ends.add(round(_b * B, 6))
    for e in phrase_ends(nbars, B, pbar):
        # **段界上的乐句末：只做"主奏收束"，不做伴奏让位"**（2026-10-13 两轮实测定下来的）：
        #   · 第一版**什么都不做**（整句跳过）→ 效果几乎全丢（battle 47.1% → **5.9%**），
        #     因为这些曲子的段长多是 4~8 小节，乐句末**大半落在段界上**；
        #   · 第二版（本版）只撤伴奏、不动段界附近的电平 ⇒ 段界的渐弱/渐入不被破坏，
        #     而"句尾收束"这个**听感目标本来就定义在主奏上**（守卫量的也是主奏）。
        at_sec = round(e, 6) in _sec_ends
        if at_sec:
            stats['at_section_end'] += 1
            if SKIP_SECTION_ENDS:
                pass                        # 伴奏**一个音都不动**（下面 ② 会跳过它）
        c0, c1 = e - 1.0, e                     # 句尾那一拍
        stats['phrases'] += 1
        # ① 主奏收束：删掉句尾拍上的主奏起音
        #    ⚠ **段界上不延长**（2026-10-13 第三轮实测）：段界处把末音"延长到句尾"会让边界
        #      前的窗口更响 ⇒ 渐弱读数掉、跳变变大（实测 `_ab_joint_sorrow` 边界「A」跳 7.2dB、
        #      两端渐弱只 3.5dB，守卫 `section_transition` 留红；且撤不撤伴奏几乎不影响这两个数）。
        #      正解：段界处主奏**早停**（留白），那正是守卫允许的"过渡有交代"形态之一。
        L = out.get(lead) or []
        in_cad = [n for n in L if c0 <= n[0] < c1]
        if in_cad:
            out[lead] = [n for n in L if not (c0 <= n[0] < c1)]
            L = out[lead]
            before = [n for n in L if n[0] < c0]
            if at_sec and SKIP_SECTION_ENDS:
                stats['rest_at_section_end'] += 1
            elif before:
                i = max(range(len(L)), key=lambda j: L[j][0] if L[j][0] < c0 else -1)
                t, dur, m, v = L[i]
                want = min(e - t, dur + MAX_HOLD)
                nxt = [n[0] for n in L if n[0] > t]
                if nxt:
                    want = min(want, min(nxt) - t - 0.02)
                if want > dur + 1e-6:
                    L[i] = (t, want, m, v)
                    stats['hold'] += 1
                    stats['hold_beats'] += want - dur
        # ② 其余层让位：句尾那一拍**不发起音**（长音延续不动）
        #    ⚠ **段界上的乐句末不做这一步**（见上面那段注释与 `SKIP_SECTION_ENDS`）。
        for k, lst in out.items():
            if k == lead or k in guard or not lst:
                continue
            if at_sec and SKIP_SECTION_ENDS:
                continue
            kept = [n for n in lst if not (c0 <= n[0] < c1)]
            if len(kept) != len(lst):
                stats['yield'] += len(lst) - len(kept)
                out[k] = kept
    ev.clear()
    ev.update(out)
    if not quiet and (stats['yield'] or stats['hold']):
        print('  联合句法（joint_cadence）：%d 个乐句（段界 %d 个：主奏**早停留白**、伴奏不动）· '
              '让位 %d 个起音 · 收束 %d 处（共延长 %.1f 拍） · 主奏=%s · 保留层=%s · 乐句长=%d 小节'
              % (stats['phrases'], stats['at_section_end'], stats['yield'], stats['hold'],
                 stats['hold_beats'], stats['lead'], '/'.join(stats['keep']), stats['phrase_bars']))
    return stats


# ------------------------------------------------------------------ 自检
def _selftest():
    """纯函数自检：4 组断言（正控 2 + 负控 2），不碰磁盘、不依赖引擎。"""
    ok, bad = 0, []

    def chk(name, cond):
        nonlocal ok
        if cond:
            ok += 1
        else:
            bad.append(name)

    d = {'bar_beats': 4.0, 'sections': [{'bars': 8}], 'patterns': {'joint_cadence': True}}
    # ⚠ 乐句长 4 小节 = 16 拍；`phrase_ends` **不在曲首收束**，所以 8 小节只有拍 16 这一个句尾。
    ev = {'Melody': [(14.0, 1.0, 72, 90), (15.0, 0.5, 74, 90), (17.0, 1.0, 76, 90)],
          'Piano': [(14.0, 1.0, 60, 80), (15.5, 0.5, 64, 80)],
          'Bass': [(14.0, 1.0, 40, 90), (15.5, 0.5, 43, 90)],
          'Arp': [(15.5, 0.25, 79, 70)]}
    st = apply(ev, d, quiet=True)
    got = dict_of(ev)
    # 1) 句尾拍（15.0~16.0）的非保留层起音被清掉
    chk('非保留层句尾让位', all(not (15.0 <= n[0] < 16.0) for n in got['Piano'] + got['Arp']))
    # 2) 保留层（Bass）在句尾拍**没被删**
    chk('保留层不动', any(15.0 <= n[0] < 16.0 for n in got['Bass']))
    # 3) 主奏句尾拍起音被删 + 前一个音延到句尾
    chk('主奏收束', all(not (15.0 <= n[0] < 16.0) for n in got['Melody']))
    hold = [n for n in got['Melody'] if n[0] < 15.0 and n[1] > 1.0 + 1e-6]
    chk('主奏延长到句尾', bool(hold) and abs(hold[0][0] + hold[0][1] - 16.0) < 1e-6)
    # 4) 负控：句尾拍本来就空 → 不许凭空造音（音高集合不变）
    ev2 = {'Melody': [(0.0, 1.0, 72, 90)], 'Piano': [(0.0, 1.0, 60, 80)],
           'Bass': [(0.0, 1.0, 40, 90)]}
    apply(ev2, d, quiet=True)
    chk('负控：不新增音高', sorted(n[2] for n in ev2['Piano']) == [60]
        and sorted(n[2] for n in ev2['Melody']) == [72])
    # 5) stats 口径
    chk('统计口径', st['phrases'] == 1 and st['yield'] == 2 and st['hold'] == 1)
    return ok, bad


def dict_of(ev):
    return {k: list(v) for k, v in ev.items()}


def main():
    if '--selftest' in sys.argv:
        ok, bad = _selftest()
        print('joint_arrange selftest: %d/%d PASS%s'
              % (ok, ok + len(bad), '' if not bad else ' | FAIL: ' + ', '.join(bad)))
        return 1 if bad else 0
    print(__doc__)
    return 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓/中文会崩 —— 守卫 console_encoding_safe 盯着）
if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main())
