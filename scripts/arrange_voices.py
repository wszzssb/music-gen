#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""arrange_voices.py —— **按逐段编制表分配发声轨**（B 线第 2 条：把"体检判据"接成"编配的输入"）。

## 它替代了什么（三个**全局规则**，全部不读转录证据）

| 老脚本（`D:\test\_tmp\ymt3-vs-ours\`） | 老做法 | 实测后果 |
|---|---|---|
| `four_inst.py` | 每段把 `Pad`/`Hook` 一起并进"主奏轨"，再整轨删掉 Pad/Hook | 原曲那层**合成器垫**被切给弦乐 |
| `kill_pad.py` | 把**全 25 段**的 `sections[].arr.pad` 一律置 0 | S01 40–80Hz **掉 39dB**、2500–5000Hz **涨 33dB** |
| `voice_split.py` | 每段"每刻最高音→小提琴、其余→中提琴"+ 强制 GM40/41 与 `tr_shift:0` | 垫子层被提到原音区、用弓弦音色拉长音 → **"蚊子叫"** |

三者的共同病根：**段级证据（这一段原曲到底是什么在响）从来没进过决策**。

## 判据（来自 `probe_instruments.py` 的逐段编制表 —— 每一行都有物理量）

每段读表里两列：`lead`（该段能量最高的分轨）与 `attack_ms`（该段主奏分轨的起音 10→90% 时间）。

| 计划 | 条件 | 动作 | 依据 |
|---|---|---|---|
| **pad** | `lead == 'other'` **且** 起音 > 150ms（软起音+持续） | 该段 `Pad`/`Strings`/`Hook` 的音**全部归 `Pad`**，不切声部 | 原曲这一段整层是垫子/合成器（`other` 是弦乐/合成器/铜管的混合堆），用垫子音色还原 |
| **strings** | `lead == 'vocals'` **且** 软起音 | 该段 `Hook` 的音 → `Strings`；`Pad` 的音**留在 Pad** | 人声/主奏合成器当家的段：旋律给弦乐，垫子仍是垫子 |
| **keep** | 其余（击弦/钢琴主导、或测不到起音） | 一个音都不动 | 没有证据就不改（`RESTORE-METHOD` §10"分段默认"） |

另外两条**只用一种口径**的动作（都有认可版本的独立证据）：

1. **`Pad` 轨的音永不并入 `Strings`/`Hook`** —— 判据：用户认可的 v22a 里，`Pad` 轨 **663 个音 100%**
   是链④产物的 `Pad` 子集（逐音指纹比对，`D:\test\_tmp\siren2-r7\04_diff.log`）。
2. **`arr.pad` 保持链④的值** —— 判据：v22a 全 25 段 `arr.pad` 都是开着的，
   而 `kill_pad` 的全局置 0 正是 S01/S02 chroma 掉的机制（0.933/0.929 → 0.875/0.876）。
   即：**"要不要垫层"由编制表判据说了算，不由"收编制"这个目标说了算。**

`dedup=True`（默认）额外删掉**跨轨完全重复**的音（同 小节/拍/时值/音高）—— 保留优先级
`Strings > Piano > Pad > Hook`。判据：v22a 在 S16–S19 删掉的 `Piano` 副本里
**91/79/119/77 个中 91/76/111/75 个是 `Strings` 上的同音**（同一句被两条轨一起弹）。

## 用法

```bash
python scripts\arrange_voices.py --song-json songs\<曲>\song.json \
       --table <probe_instruments --json 产物> [--dedup on|off] [--voices engine|quartet] [--dry]
#   --voices engine ：不写 programs/tr_shift（引擎默认 Strings=GM48/tr_shift −12，= v22a 口径）
#   --voices quartet：Strings=GM40 小提琴 / Hook=GM41 中提琴 + tr_shift 0（v25 口径，用于消融对照）
```

⚠ 与 `timbre_audit.py` 的分工：那个**量结果**（我方差在哪），这个**按量到的证据改编配**。
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()                      # noqa: E402
import json_io                                            # noqa: E402

SOFT_ATTACK_MS = 150.0      # 软起音门限（与 timbre_audit.SOFT_ATTACK_MS 同口径）
MEL = ("Pad", "Strings", "Hook")
DEDUP_PRIO = ("Strings", "Piano", "Pad", "Hook")          # 重复音保留优先级（v22a 口径）


def notes_of(ne, tr, create=False):
    v = ne.get(tr)
    if v is None:
        if not create:
            return None
        ne[tr] = {"notes": []}
        return ne[tr]["notes"]
    return v["notes"] if isinstance(v, dict) else v


def sec_bounds(d):
    """→ [(名字, 起始小节, 结束小节, 起秒, 止秒)]"""
    bar = 4 * 60.0 / float(d.get("bpm") or 120.0)
    out, b0 = [], 0
    for s in d.get("sections") or []:
        L = int(s.get("bars") or 4)
        out.append((s.get("name"), b0, b0 + L, b0 * bar, (b0 + L) * bar))
        b0 += L
    return out


def match_row(rows, name, t0, t1):
    """表行对齐：先按段名，再按时间重叠最大。"""
    for r in rows:
        if r.get("name") == name:
            return r
    best, bi = None, 0.0
    for r in rows:
        ov = min(t1, r.get("t1", 0)) - max(t0, r.get("t0", 0))
        if ov > bi:
            best, bi = r, ov
    return best


def plan_of(row, soft_ms=None):
    """→ (plan, why)；判据即模块文档开头那张表。plan ∈ {pad, strings, keep}

    `soft_ms=None` 时**动态**取模块常量 `SOFT_ATTACK_MS`（写成默认参数会让变异测试注入不进来）。
    """
    soft_ms = SOFT_ATTACK_MS if soft_ms is None else soft_ms
    if not row:
        return "keep", "表里没有这一段"
    lead, att = row.get("lead"), row.get("attack_ms")
    soft = (att is not None) and (att > soft_ms)
    if lead == "other" and soft:
        return "pad", "other 主导 + 起音 %.0fms>%.0f" % (att, soft_ms)
    if lead == "vocals" and soft:
        return "strings", "vocals 主导 + 起音 %.0fms>%.0f" % (att, soft_ms)
    if att is None:
        return "keep", "起音测不到（主奏层过弱）"
    return "keep", "%s 主导 + 起音 %.0fms" % (lead, att)


def dedup_cross_track(ne, prio=None):
    """删掉**跨轨完全重复**的音（同 小节/拍/时值/音高）；→ {轨: 删了几个}

    `prio=None` 时**动态**取模块常量 `DEDUP_PRIO` —— 写成默认参数会让变异测试注入不进来
    （实测：`Mut(av,'DEDUP_PRIO',())` 曾因此**漏检**，见 mutation_check 第 67 组）。
    """
    prio = DEDUP_PRIO if prio is None else prio
    seen, dropped = {}, {}
    for tr in prio:
        ns = notes_of(ne, tr)
        if not ns:
            continue
        keep = []
        for it in ns:
            key = (int(it[0]), round(float(it[1]), 3), round(float(it[2]), 3), int(it[3]))
            if key in seen:
                dropped[tr] = dropped.get(tr, 0) + 1
                continue
            seen[key] = tr
            keep.append(it)
        if isinstance(ne[tr], dict):
            ne[tr]["notes"] = keep
        else:
            ne[tr] = keep
    return dropped


def arrange(d, rows, soft_ms=None, dedup=True, voices="engine", vocals_move=False):
    """就地改编配（**不写盘**）；→ 统计 dict（含逐段计划，供打印/断言）。

    硬不变量：**`Pad` 轨的音只会增加（段级归入），永不流向 `Strings`/`Hook`**。

    `vocals_move` 管 `strings` 计划那一段要不要把 `Hook` 并进 `Strings`：默认 **False（不动）**——
    判据：v22a 在 vocals 段（S10/S12–S14/S20/S21）`Hook` 是 **0 音**，即当年是**删掉**而不是并进去；
    而"删"我们没有独立证据（那 190 个音在分轨里是有的）→ 按 `RESTORE-METHOD` §10
    "分段默认：没有证据就不改"，只保留 `Pad` 的作用。
    """
    soft_ms = SOFT_ATTACK_MS if soft_ms is None else soft_ms
    ne = d["notes_extra"]
    moved = {"pad": 0, "strings": 0}
    table = []
    for (nm, b0, b1, t0, t1) in sec_bounds(d):
        row = match_row(rows, nm, t0, t1)
        plan, why = plan_of(row, soft_ms)
        acts = []
        if plan == "pad":
            tgt = notes_of(ne, "Pad", create=True)
            for tr in ("Strings", "Hook"):
                src = notes_of(ne, tr)
                if not src:
                    continue
                take = [it for it in src if b0 <= int(it[0]) < b1]
                if take:
                    tgt.extend(take)
                    src[:] = [it for it in src if not (b0 <= int(it[0]) < b1)]
                    moved["pad"] += len(take)
                    acts.append("%s→Pad:%d" % (tr, len(take)))
            tgt.sort(key=lambda x: (x[0], x[1]))
        elif plan == "strings":
            src = notes_of(ne, "Hook")
            if vocals_move and src:
                tgt = notes_of(ne, "Strings", create=True)
                take = [it for it in src if b0 <= int(it[0]) < b1]
                if take:
                    tgt.extend(take)
                    src[:] = [it for it in src if not (b0 <= int(it[0]) < b1)]
                    moved["strings"] += len(take)
                    acts.append("Hook→Strings:%d" % len(take))
            tgt = notes_of(ne, "Strings")          # ⚠ 默认口径下**不新建空轨**
            if tgt is not None:
                tgt.sort(key=lambda x: (x[0], x[1]))
        table.append({"name": nm, "t0": t0, "t1": t1, "lead": (row or {}).get("lead"),
                      "attack_ms": (row or {}).get("attack_ms"), "plan": plan,
                      "why": why, "acts": acts})
    dropped = dedup_cross_track(ne) if dedup else {}
    if voices == "quartet":
        d["programs"] = {"Strings": [40, 5], "Hook": [41, 1]}
        d["tr_shift"] = {"Strings": 0, "Hook": 0}
    n_pad_on = sum(1 for s in (d.get("sections") or []) if (s.get("arr") or {}).get("pad"))
    return {"table": table, "moved": moved, "dropped": dropped, "n_pad_on": n_pad_on,
            "n_sec": len(d.get("sections") or []),
            "plans": {p: sum(1 for r in table if r["plan"] == p) for p in ("pad", "strings", "keep")},
            "counts": {k: len(notes_of(ne, k) or []) for k in
                       ("Piano", "Strings", "Pad", "Hook", "Bass", "Perc")
                       if notes_of(ne, k) is not None}}


def main():
    ap = argparse.ArgumentParser(description="按逐段编制表分配发声轨")
    ap.add_argument("--song-json", required=True)
    ap.add_argument("--table", required=True, help="probe_instruments.py --json 的产物")
    ap.add_argument("--dedup", default="on", choices=("on", "off"))
    ap.add_argument("--voices", default="engine", choices=("engine", "quartet"))
    ap.add_argument("--vocals-move", default="off", choices=("on", "off"),
                    help="strings 计划要不要把 Hook 并进 Strings（默认 off = 不动）")
    ap.add_argument("--soft-ms", type=float, default=SOFT_ATTACK_MS)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    d = json_io.load(a.song_json)
    tab = json.load(open(a.table, encoding="utf-8"))
    rows = tab.get("sections") or []
    print("编制表: %s（%d 段 · 能量门限 %s dB）" % (a.table, len(rows), tab.get("rel_floor")))
    print("曲目:   %s" % a.song_json)
    st = arrange(d, rows, a.soft_ms, a.dedup == "on", a.voices, a.vocals_move == "on")

    print()
    print("%-6s %-13s %-10s %7s %-8s | %s" % ("段", "时间", "原曲主导", "起音ms", "计划", "动作"))
    print("-" * 104)
    for r in st["table"]:
        print("%-6s %5.1f-%-7.1f %-10s %7s %-8s | %s"
              % (r["name"], r["t0"], r["t1"], r["lead"] or "-",
                 "-" if r["attack_ms"] is None else "%.1f" % r["attack_ms"], r["plan"],
                 (" ".join(r["acts"]) + "   [" + r["why"] + "]") if r["acts"] else "—  " + r["why"]))
    print()
    print("归 Pad %d 音 · Hook→Strings %d 音 · 去重删除 %s"
          % (st["moved"]["pad"], st["moved"]["strings"],
             " ".join("%s:%d" % kv for kv in st["dropped"].items()) or "0"))
    print("计划分布: %s" % st["plans"])
    print("vocals 段的 Hook：%s" % ("并入 Strings" if a.vocals_move == "on"
                                   else "不动（v22a 口径是删不是并，我们没有删的证据）"))
    print("arr.pad 开启段数 %d/%d（**保持链④值** —— v22a 全 25 段都开着）"
          % (st["n_pad_on"], st["n_sec"]))
    print("音色口径: %s" % ("引擎默认（Strings=GM48 · tr_shift −12，= v22a）" if a.voices == "engine"
                            else "GM40 小提琴 / GM41 中提琴 + tr_shift 0（v25 口径）"))
    print("逐轨音数: %s" % st["counts"])

    if a.dry:
        print("（--dry：没写文件）")
        return 0
    shutil.copy2(a.song_json, a.song_json + ".pre_arrange.bak")
    json_io.save(a.song_json, d)
    print("已写回 %s（备份 .pre_arrange.bak）" % a.song_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
