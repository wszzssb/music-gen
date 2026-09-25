#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""merge_sustain.py —— **把持续层被转录切成的"音头串"并回长音**（改 `song.json`，保持可复现）

## 为什么（siren_end2 实测）
原曲开头（S01/S02）**几乎没有起音**（音频 onset 数 **0 / 1**），而我方是 **26 / 78** ——
转录把原曲那层**长垫子**切成了"音头串"（每片 0.1~0.5 拍）。实测合片后
`Strings` **1139 → 333 音 · 时值中位 169ms → 728ms**（用户 2026-09-25 认可保留这一版）。

## 判据（两道门，缺一不并 —— 与 `_tmp\siren2\fix_sustain.py` 同一口径）
1. **间隔门**：同音高相邻碎片，间隔 ≤ `--gap` 拍（默认 1.0）；
2. **原曲能量门**：原曲该音高在**这个间隔里**的能量 ≥ `--keep` ×（两侧能量较小者），
   默认 **0.30** —— 原曲真的"断了又起"的地方**不并**，否则会把真实的重新起音抹掉。

⚠ 与老脚本的区别：那个改 **MIDI**（产物与 `song.json` 脱钩 → 正是"交付与谱面不一致"那类问题的根源）；
本工具改 **`song.json` 的 `notes_extra`**，所以 `make_song` 能复现。

## 用法

```bash
python scripts\merge_sustain.py --song-json songs\<曲>\song.json --ref <原曲 other 分轨 wav> \
       [--tracks Pad,Strings] [--keep 0.30] [--gap 1.0] [--apply|--dry]
python scripts\merge_sustain.py --selftest      # 纯函数自检（不需要材料）
```
"""
import argparse
import os
import shutil
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()          # noqa: E402
import json_io                                # noqa: E402

SR, HOP, NFFT = 22050, 512, 4096
KEEP_DEF = 0.30
GAP_DEF = 1.0


def should_merge(gap, gap_max, seg_env, pa, pb, keep=None):
    """**纯判据**：→ (是否并, 原因)。`seg_env` = 间隔内的能量序列（可为 None=测不到）。

    写成纯函数是为了让 `--selftest` 与变异测试能直接量它（不看集成路径）。
    ⚠ `keep=None` 时才**动态**取 `KEEP_DEF` —— 写成默认参数会让变异注入不进来（本链自己踩过两次）。
    """
    keep = KEEP_DEF if keep is None else keep
    if gap > gap_max + 1e-9:
        return False, "间隔 %.3f > %.3f 拍" % (gap, gap_max)
    if gap <= 1e-6:
        return True, "首尾相接"
    if seg_env is None or len(seg_env) == 0:
        return True, "测不到原曲能量（不拦）"
    ref = max(min(pa, pb), 1e-12)
    if float(np.min(seg_env)) < keep * ref:
        return False, "原曲该音高在间隔里断了（%.3g < %.2f×%.3g）" % (float(np.min(seg_env)), keep, ref)
    return True, "原曲持续"


def pitch_energy(y, sr=SR):
    """→ E[音高][帧] = f0 ±0.5 半音（含 2f0/3f0 谐波，权重 1 / 0.5 / 0.33）"""
    import librosa
    S = np.abs(librosa.stft(y, n_fft=NFFT, hop_length=HOP)) ** 2
    fr = librosa.fft_frequencies(sr=sr, n_fft=NFFT)
    E = {}
    for p in range(21, 109):
        f0 = 440.0 * 2 ** ((p - 69) / 12.0)
        tot = None
        for h, w in ((1, 1.0), (2, 0.5), (3, 0.33)):
            f = f0 * h
            if f >= fr[-1]:
                break
            m = (fr >= f * 2 ** (-0.5 / 12)) & (fr <= f * 2 ** (0.5 / 12))
            if not m.any():
                continue
            v = S[m].sum(axis=0) * w
            tot = v if tot is None else tot + v
        if tot is not None:
            E[p] = tot
    return E


def selftest(verbose=True):
    """尺子自检：**首尾相接必并**、**间隔太大必不并**、**原曲断了必不并**、**原曲持续才并**。
    ⚠ 调用时**不传 keep**，走模块默认值 —— 这样变异测试把 `KEEP_DEF` 改坏时才抓得到。"""
    ok = []
    ok.append(("首尾相接", should_merge(0.0, 1.0, None, 1, 1)[0] is True))
    ok.append(("间隔太大", should_merge(2.5, 1.0, np.ones(5), 1, 1)[0] is False))
    ok.append(("原曲断了", should_merge(0.5, 1.0, np.array([0.01, 0.01]), 1.0, 1.0)[0] is False))
    ok.append(("原曲持续", should_merge(0.5, 1.0, np.array([0.5, 0.6]), 1.0, 1.0)[0] is True))
    ok.append(("测不到不拦", should_merge(0.5, 1.0, None, 1, 1)[0] is True))
    if verbose:
        for nm, v in ok:
            print("  %-10s %s" % (nm, "PASS" if v else "FAIL"))
        print("merge_sustain 自检 " + ("PASS" if all(v for _n, v in ok) else "FAIL"))
    return all(v for _n, v in ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--song-json")
    ap.add_argument("--ref", help="原曲的 other 分轨 wav（能量闸门用）")
    ap.add_argument("--tracks", default="Pad,Strings")
    ap.add_argument("--keep", type=float, default=KEEP_DEF)
    ap.add_argument("--gap", type=float, default=GAP_DEF, help="只并间隔 <= 该拍数的碎片")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 2
    if not (a.song_json and a.ref):
        ap.print_help(); return 1

    import metrics
    d = json_io.load(a.song_json)
    # 原曲能量层要 librosa/soundfile（`.venv-ml`）—— 与 `probe_instruments.py` 同一套自动换解释器
    import pyenv
    pyenv.ensure("librosa", ".venv-ml", "原曲该音高的能量闸门（第②道门）要 librosa/soundfile")
    d = json_io.load(a.song_json)
    B = float(d.get("bar_beats") or 4.0)
    beat = 60.0 / float(d.get("bpm") or 145.96)
    y, sr = metrics.read_audio(a.ref, mono=True)
    E = pitch_energy(np.asarray(y, dtype="float32"), sr)
    print("闸门: 间隔 <= %.2f 拍 · 原曲能量 >= 两侧的 %.2f（源 %s）"
          % (a.gap, a.keep, os.path.basename(a.ref)))
    ne = d["notes_extra"]
    secs, b0 = [], 0
    for s in d["sections"]:
        nb = int(s.get("bars") or 4)
        secs.append((s["name"], b0, b0 + nb))
        b0 += nb
    tot0 = tot1 = 0
    for tr in [t.strip() for t in a.tracks.split(",") if t.strip()]:
        v = ne.get(tr)
        if not v:
            print("  %-8s 没有内容，跳过" % tr)
            continue
        ns = v["notes"] if isinstance(v, dict) else v
        items = [(float(it[0]) * B + float(it[1]), float(it[2]), int(it[3]),
                  float(it[4]) if len(it) > 4 else 80.0) for it in ns]
        by_p = defaultdict(list)
        for t, dd, p, vel in items:
            by_p[p].append((t, dd, p, vel))
        out, n_merge, n_gate, n_far = [], 0, 0, 0
        why = defaultdict(int)
        for p, lst in sorted(by_p.items()):
            lst.sort()
            cur = list(lst[0])
            for nxt in lst[1:]:
                gap = nxt[0] - (cur[0] + cur[1])
                seg = pa = pb = None
                env = E.get(int(p))
                if env is not None and gap > 1e-6 and gap <= a.gap + 1e-9:
                    i = int((cur[0] + cur[1]) * beat * SR / HOP)
                    j = int(nxt[0] * beat * SR / HOP)
                    i, j = max(i, 0), min(j, len(env) - 1)
                    if j > i:
                        seg = env[i:j]
                        pa = env[max(i - 3, 0):i + 3].max() if i > 0 else env[:1].max()
                        pb = env[j:j + 6].max()
                ok, why_s = should_merge(gap, a.gap, seg, pa, pb, a.keep)
                why[why_s.split("（")[0]] += 1
                if not ok:
                    n_gate += 1 if "原曲" in why_s else 0
                    n_far += 1 if "间隔" in why_s else 0
                if ok:
                    cur[1] = max(cur[0] + cur[1], nxt[0] + nxt[1]) - cur[0]
                    cur[3] = max(cur[3], nxt[3])
                    n_merge += 1
                else:
                    out.append(cur); cur = list(nxt)
            out.append(cur)
        out.sort(key=lambda z: (z[0], z[2]))
        new = [[int(t // B), round(t % B, 3), round(dd, 3), p, int(round(vel))]
               for (t, dd, p, vel) in out]
        d0 = np.median([x[1] for x in items]) * beat * 1000
        d1 = np.median([x[1] for x in out]) * beat * 1000
        print("  %-8s %5d -> %5d 音（并 %d · 原曲闸门拦下 %d · 间隔太大 %d）  时值中位 %.0fms -> %.0fms"
              % (tr, len(items), len(out), n_merge, n_gate, n_far, d0, d1))
        tot0 += len(items); tot1 += len(out)
        if isinstance(v, dict):
            v["notes"] = new
        else:
            ne[tr] = new
    print("  合计 %d -> %d 音（-%d%%）" % (tot0, tot1, round(100 * (tot0 - tot1) / max(tot0, 1))))
    if a.dry or not a.apply:
        print("（--dry / 未加 --apply：没写文件）")
        return 0
    shutil.copy2(a.song_json, a.song_json + ".pre_sustain.bak")
    json_io.save(a.song_json, d)
    print("已写回 %s（备份 .pre_sustain.bak）" % a.song_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
