#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""filter_song_by_stem.py —— **按分轨能量筛出"这条轨在该段根本没在响"的音**（作用在 `song.json`）

与 `filter_by_stem.py` 的分工：那个改 **MIDI**（合并产物目录），本工具改 **`song.json`** ——
因为"产物必须能从谱面复现"（HANDOFF-ROUND10 §4 查出的那类不一致）。

## 判据（门限**由分布双峰标定、不许拍**；空档不够就**拒筛**）
逐段量该分轨的 RMS → 排序找**最大空档** → 门限取空档中点；**空档 < `GAP_MIN`(12dB) 直接拒筛**。
`siren_end2` 实测：`h6_bass` 门限 **−64.9dB（最大空档 31.8dB）**，命中 S01（−87.4dB）/ S02（−80.8dB）
—— 与"开头 18 秒弹了 42 个凭空贝斯音"的结论一致。

## ⚠⚠ 默认**只报不删**（`--apply` 才删）—— 这条来自实测翻车
删掉那 42 个音之后：S01 的逐段 RMS 差 **−5.9 → −14.4dB**、20–40Hz 带 **−26.7 → −34.8dB**、还冒出一段嘶声。
原因：原曲那两段的 **20–80Hz 其实来自 `other`（垫子）**，而我们垫子的基频落在 58/116Hz ——
那段低频**没有等效物**，凭空贝斯正好把洞填上了。
⇒ **要删，必须先用垫子把那段低频补上**（从同一素材派生，不是造音）。顺序反了就会把开头掏空。

## 用法

```bash
python scripts\filter_song_by_stem.py --song-json songs\<曲>\song.json \
       --stem <h6_bass.wav> [--track Bass] [--gap-min 12] [--apply|--dry]
python scripts\filter_song_by_stem.py --selftest
```
"""
import argparse
import os
import shutil
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()          # noqa: E402
import json_io                                # noqa: E402

GAP_MIN = 12.0


def seg_db(y, sr, t0, t1):
    a, b = int(max(0.0, t0) * sr), int(min(len(y) / sr, t1) * sr)
    if b <= a:
        return -120.0
    return 20 * np.log10(max(float(np.sqrt(np.mean(y[a:b] ** 2))), 1e-12))


def gap_threshold(vals, gap_min=GAP_MIN):
    """**纯函数**：双峰标定 → `(门限|None, 最大空档)`；空档 < `gap_min` → 门限 `None`（拒筛）。"""
    v = sorted(float(x) for x in vals)
    best, bi = 0.0, None
    for i in range(len(v) - 1):
        g = v[i + 1] - v[i]
        if g > best:
            best, bi = g, (v[i] + v[i + 1]) / 2.0
    return (bi, best) if best >= gap_min else (None, best)


def selftest(verbose=True):
    """尺子自检：**双峰必须给出中间门限** · **单峰（无空档）必须拒筛**。"""
    ok = []
    thr, gap = gap_threshold([-87, -81, -49, -27, -23, -19])
    ok.append(("双峰→门限落在空档里", thr is not None and -81 < thr < -49))
    thr2, gap2 = gap_threshold([-20.0, -19.5, -19.0, -18.6, -18.2])
    ok.append(("单峰→拒筛", thr2 is None))
    ok.append(("空档门限常量", abs(GAP_MIN - 12.0) < 1e-9))
    if verbose:
        for nm, v in ok:
            print("  %-22s %s" % (nm, "PASS" if v else "FAIL"))
        print("filter_song_by_stem 自检 " + ("PASS" if all(v for _n, v in ok) else "FAIL"))
    return all(v for _n, v in ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--song-json")
    ap.add_argument("--stem", help="该轨的原曲分轨 wav（如 h6_bass.wav）")
    ap.add_argument("--track", default="Bass")
    ap.add_argument("--gap-min", type=float, default=GAP_MIN)
    ap.add_argument("--apply", action="store_true", help="真删（⚠ 先补低频，见文档头）")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 2
    if not (a.song_json and a.stem):
        ap.print_help(); return 1
    import metrics
    d = json_io.load(a.song_json)
    B = float(d.get("bar_beats") or 4.0)
    bar = B * 60.0 / float(d.get("bpm") or 145.96)
    y, sr = metrics.read_audio(a.stem, mono=True)
    y = np.asarray(y, dtype="float32")
    secs, b0 = [], 0
    for s in d["sections"]:
        nb = int(s.get("bars") or 4)
        secs.append((s["name"], b0, b0 + nb, b0 * bar, (b0 + nb) * bar))
        b0 += nb
    lv = [(nm, seg_db(y, sr, t0, t1)) for (nm, _x, _y, t0, t1) in secs]
    thr, gap = gap_threshold([v for (_n, v) in lv], a.gap_min)
    print("分轨 %s · 门限 %s（最大空档 %.1fdB%s）"
          % (os.path.basename(a.stem), "%.1f" % thr if thr is not None else "拒筛", gap,
             "" if thr is not None else " < %.0f → 拒绝筛选" % a.gap_min))
    v = d["notes_extra"].get(a.track)
    ns = (v["notes"] if isinstance(v, dict) else v) if v else []
    quiet = {nm for (nm, x) in lv if thr is not None and x < thr}
    print("%-7s %9s %s" % ("段", "该分轨dB", "动作"))
    ndrop = 0
    for (nm, x, y2, t0, t1) in secs:
        c = sum(1 for it in ns if x <= int(it[0]) < y2)
        act = "—"
        if nm in quiet:
            act = ("⚠ 拟删 %d 音" % c) if c else "（本来就没音）"
            ndrop += c
        print("%-7s %9.1f %s" % (nm, dict(lv)[nm], act))
    print("合计拟删 %d 音（%s）" % (ndrop, "已写盘" if a.apply else "**只报不删**（默认）"))
    if thr is None:
        print("⚠ 空档不够，拒筛（判据的一部分，不是失败）")
        return 0
    if a.dry or not a.apply:
        print("（要真删加 --apply —— 但先读文档头那段：删之前必须先把该段低频补上）")
        return 0
    keep = [it for it in ns if not any(x <= int(it[0]) < y2
                                       for (nm, x, y2, _t0, _t1) in secs if nm in quiet)]
    if isinstance(v, dict):
        v["notes"] = keep
    else:
        d["notes_extra"][a.track] = keep
    shutil.copy2(a.song_json, a.song_json + ".pre_stemcut.bak")
    json_io.save(a.song_json, d)
    print("已写回 %s（%d → %d 音）" % (a.song_json, len(ns), len(keep)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
