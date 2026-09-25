#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""vel_from_ref.py —— **把"原曲响度 → 力度"写回 `song.json`**（可复现版的 `revel.py`）

## 为什么（siren_end2 实测，12 轮里**唯一**把指标推上去的杠杆）
交付那一支的亮度来自手工链里的 `revel.py`：**逐音**按原曲该时刻的响度给力度
（−20dB→75 · 0dB→110 · 夹 60–118）。而**从 `song.json` 重新生成**的那一支力度中位只有 **61–77**，
于是 **19/25 段比原曲暗得多**（2–6kHz 占比比值 0.01–0.8）。
写回之后：力度中位 **74 → 105**、亮度比中位 **0.41 → 0.67**、平均 |逐段 RMS 差| **2.56 → 2.07 dB**
（`HANDOFF-ROUND12.md`）。老 `revel.py` 改 **MIDI**（产物与谱面脱钩）→ 本工具改 **`notes_extra` 第 5 元素**。

## 口径（与 `revel.py` v3 **逐字一致**，改它要重标定）
· 包络：`librosa.stft(n_fft=2048, hop=512, sr=22050)` → 帧 RMS → 9 帧滑动平均 → 按**全曲 P95** 归一到 dB；
· 每音取 `[i-2, i+5)` 帧的**最大值**（`i = 起音秒 × sr / 512`）；
· `vel = clip(75 + (dB + 20) / 20 × 35, 60, 118)`。

## 用法

```bash
python scripts\vel_from_ref.py --song-json songs\<曲>\song.json --ref <原曲音频> [--apply|--dry]
python scripts\vel_from_ref.py --selftest
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

SR, HOP, NFFT = 22050, 512, 2048
DB_LO, DB_HI = -20.0, 0.0
V_LO, V_HI = 75.0, 110.0
CLIP = (60, 118)


def map_vel(db_value):
    """**纯映射**：dB（相对全曲 P95）→ 力度（夹 60–118）。写成纯函数供自检/变异直取。"""
    return int(np.clip(V_LO + (float(db_value) - DB_LO) / (DB_HI - DB_LO) * (V_HI - V_LO), *CLIP))


def envelope(y, sr=SR):
    """→ 逐帧 dB（相对全曲 P95），与 revel.py v3 一致"""
    import librosa
    S = np.abs(librosa.stft(np.asarray(y, dtype="float32"), n_fft=NFFT, hop_length=HOP))
    e = np.convolve(np.sqrt((S ** 2).mean(axis=0)), np.ones(9) / 9, mode="same")
    return 20 * np.log10(np.maximum(e, 1e-12) / (np.percentile(e, 95) + 1e-12))


def selftest(verbose=True):
    """尺子自检：映射的**五个已知点**必须落在设计值上（0dB→110 · −20dB→75 · 越界夹 60/118）。"""
    cases = [(0.0, 110), (-20.0, 75), (-10.0, 92), (-60.0, 60), (10.0, 118)]
    ok = []
    for db, want in cases:
        got = map_vel(db)
        ok.append(("%.0fdB→%d" % (db, want), abs(got - want) <= 1))
    if verbose:
        for nm, v in ok:
            print("  %-12s %s" % (nm, "PASS" if v else "FAIL"))
        print("vel_from_ref 自检 " + ("PASS" if all(v for _n, v in ok) else "FAIL"))
    return all(v for _n, v in ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--song-json")
    ap.add_argument("--ref", help="原曲音频（定力度用）")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 2
    if not (a.song_json and a.ref):
        ap.print_help(); return 1

    import metrics
    import pyenv
    pyenv.ensure("librosa", ".venv-ml", "原曲响度包络要 librosa/soundfile")
    d = json_io.load(a.song_json)
    B = float(d.get("bar_beats") or 4.0)
    beat = 60.0 / float(d.get("bpm") or 145.96)
    y, sr = metrics.read_audio(a.ref, mono=True)
    db = envelope(y, sr)
    print("映射 %gdB→%.0f · %gdB→%.0f · 夹 %s（与 revel.py v3 逐字一致）"
          % (DB_LO, V_LO, DB_HI, V_HI, CLIP))
    old, new = [], []
    secs, b0 = [], 0
    for s in d["sections"]:
        nb = int(s.get("bars") or 4)
        secs.append((s["name"], b0, b0 + nb))
        b0 += nb
    per_sec = {nm: [[], []] for (nm, _x, _y) in secs}
    for tr, v in d["notes_extra"].items():
        ns = v["notes"] if isinstance(v, dict) else v
        out = []
        for it in ns:
            t = (float(it[0]) * B + float(it[1])) * beat
            i = int(t * sr / HOP)
            p, q = max(i - 2, 0), min(i + 5, len(db))
            val = float(db[p:q].max()) if q > p else DB_LO
            vel = map_vel(val)
            it = list(it)
            # ⚠ 先读旧值再写新值 —— 反过来会让"改前"那列读到刚写进去的新值（自欺的读数，实测踩过）
            old.append(int(it[4]) if len(it) > 4 else 80)
            new.append(vel)
            for (nm, x, y2) in secs:
                if x <= int(it[0]) < y2:
                    per_sec[nm][0].append(old[-1]); per_sec[nm][1].append(vel)
                    break
            while len(it) < 4:
                it.append(0)
            if len(it) == 4:
                it.append(vel)
            else:
                it[4] = vel
            out.append(it)
        if isinstance(v, dict):
            v["notes"] = out
        else:
            d["notes_extra"][tr] = out
    o, w = np.array(old), np.array(new)
    print("全曲力度：中位 %.0f → %.0f · p10 %.0f → %.0f · <55 占 %.0f%% → %.0f%%"
          % (np.median(o), np.median(w), np.percentile(o, 10), np.percentile(w, 10),
             100 * np.mean(o < 55), 100 * np.mean(w < 55)))
    print("%-7s %8s %8s" % ("段", "改前", "改后"))
    for (nm, _x, _y) in secs:
        a0, a1 = per_sec[nm]
        if a0:
            print("%-7s %8.0f %8.0f" % (nm, np.median(a0), np.median(a1)))
    if a.dry or not a.apply:
        print("（--dry / 未加 --apply：没写文件）")
        return 0
    shutil.copy2(a.song_json, a.song_json + ".pre_velref.bak")
    json_io.save(a.song_json, d)
    print("已写回 %s（备份 .pre_velref.bak）" % a.song_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
