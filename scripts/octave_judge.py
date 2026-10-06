# -*- coding: utf-8 -*-
r"""octave_judge.py —— **八度判决器 v0.1**（只报建议，不改任何数据）

干什么：对我们 MIDI 里的每个音，比较"我们弹的这个八度"与"低一个八度"，在**原曲**里谁的
**基频**更强 —— 判"该不该降八度"。

判据复用（不新造）：
  · `bass_layer_enhance.band_amp`：只取基频 ±3% 窄带最大幅度。
    ⚠ 其 docstring 记着实测：比"基频+谐波和"会被"低八度的 2 次谐波 = 高八度基频"污染
    （110Hz 正弦上 p45 与 p33 读数 549.3 vs 549.6）⇒ 只比基频。
  · **三把独立尺子**：① 原曲全混音 ② htdemucs_6s 的对应分轨 ③ htdemucs(4轨) 的对应分轨
    —— 后两者是**两个不同的分离模型**（本仓库口径："互为独立证据"）。多数票才给建议。
    （v0 只用了 ①+②，实测 **41% 的音两把尺子不一致** —— 主因 `other.wav` 是大杂烩。）

## ⚠ 2026-10-06 标定结论：**它不能当"判决器"用**（假阳率 95–97%）

在 BGM35 上拿认可版 `b35_clean` 当参照做阈值扫描（1.30 → 3.00）：

| 阈值 | 建议"降八度" | 真阳 | 假阳 | 假阳率 |
|---|---|---|---|---|
| 1.30 | 343 | 11 | 332 | **97%** |
| 1.80 | 221 | 8 | 213 | 96% |
| 2.50 | 128 | 7 | 121 | 95% |
| 3.00 | 83 | 3 | 80 | **96%** |

**提高阈值完全不改善**。根因（机理，不是调参能救的）：`E(f−12) > E(f)` 在真实复音音乐里
**系统性成立** —— 低八度区能量天然更强，且高八度音的谐波也落在低八度 ⇒ 单比基频能量
**分不清"原曲的基频"与"低八度音的谐波"**。（`bass_layer_enhance.octave_check` 有效是因为
它只用在**单乐器 bass 轨**上；复音混音上同族教训见 `PITFALLS` 325。）

正例窗（BGM35 19.0–19.6s）在阈值 1.30 下确实命中 `p84`（认可版弹 `p72`）——
**方向对，但整体不可用**。

⇒ 本工具只能当**筛查器**（列出"能量上低八度更强"的可疑处），**不许当门、不许直接改数据**；
   要动必须逐处 A/B（用户听感判）。标定脚本口径见交接文档。

三态输出：`降八度` / `保持` / `测不到`。**本工具不改曲子** —— 改动要另行做，且必须 A/B。

用法：
    <py-ml> octave_judge.py <我们的.mid> --audio <原曲.wav> [--stems-dir <h6_*.wav,h4_*.wav 目录>]
                           [--at t0-t1] [--tracks Strings,Piano] [--only-runs] [--top 30]
                           [--min-pitch 60] [--ratio-thr 1.30] [--selftest]
"""
import argparse
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from bass_layer_enhance import band_amp            # noqa: E402  只比基频
try:
    from transcribe_to_song import STEM_FILE       # noqa: E402
except Exception:                                   # noqa: BLE001
    STEM_FILE = {'Piano': 'piano.wav', 'Bass': 'bass.wav', 'Drums': 'drums.wav',
                 'Hook': 'guitar.wav', 'Strings': 'other.wav', 'Pad': 'other.wav',
                 'Arp': 'other.wav', 'Glock': 'other.wav'}

THR_LO = 1.30      # 低八度基频 ≥ 高八度 ×1.30 → 投"降八度"
THR_KEEP = 0.77    # ≤0.77 → 投"保持"（与上互为倒数的中间带 → 该尺子弃权）


def find_stems(stems_dir, track):
    """该轨可用的分轨尺子：[(标签, 路径)] —— h6 / h4 / 裸名 都收。"""
    out = []
    if not stems_dir:
        return out
    base = STEM_FILE.get(track)
    if not base:
        return out
    for tag, nm in (("h6", "h6_%s" % base), ("h4", "h4_%s" % base), ("x", base)):
        p = os.path.join(stems_dir, nm)
        if os.path.exists(p):
            out.append((tag, p))
    return out


def vote(r):
    if r is None:
        return None
    if r >= THR_LO:
        return "降八度"
    if r <= THR_KEEP:
        return "保持"
    return None


def notes_of(mid_path, tracks=None):
    import pretty_midi          # 延迟导入：主 venv(.venv) 没装它 —— 本工具用 .venv-ml 跑
    pm = pretty_midi.PrettyMIDI(mid_path)
    out = []
    for ins in pm.instruments:
        nm = ins.name or "?"
        if nm == "Drums":
            continue
        if tracks and nm not in tracks:
            continue
        for n in ins.notes:
            out.append((nm, n.start, n.end - n.start, n.pitch, n.velocity))
    return sorted(out, key=lambda x: (x[0], x[1]))


def selftest():
    sr = 22050
    t = np.arange(int(1.0 * sr)) / float(sr)
    a4 = (0.5 * np.sin(2 * np.pi * 440.0 * t)).astype("float32")
    a5 = (0.5 * np.sin(2 * np.pi * 880.0 * t)).astype("float32")
    checks = []
    e69, e57 = band_amp(a4, sr, 0.1, 69), band_amp(a4, sr, 0.1, 57)
    checks.append(("A4 信号：p69 > p57 ×3（只比基频的方向）", e69 > e57 * 3,
                   "%.1f vs %.1f" % (e69, e57)))
    e81, e69b = band_amp(a5, sr, 0.1, 81), band_amp(a5, sr, 0.1, 69)
    checks.append(("A5 信号：p81 > p69 ×3", e81 > e69b * 3, "%.1f vs %.1f" % (e81, e69b)))
    checks.append(("ratio 2.0 → 投票降八度", vote(2.0) == "降八度", vote(2.0)))
    checks.append(("ratio 0.5 → 投票保持", vote(0.5) == "保持", vote(0.5)))
    checks.append(("ratio 1.0 → 弃权", vote(1.0) is None, "None"))
    checks.append(("能量为 0 → 弃权", vote(None) is None, "None"))
    ok = True
    for name, passed, extra in checks:
        print("  %s %-42s %s" % ("PASS" if passed else "FAIL", name, extra))
        ok = ok and passed
    print("octave_judge 自检 %s" % ("PASS" if ok else "FAIL"))
    return ok


def main():
    global THR_LO
    ap = argparse.ArgumentParser(description="八度判决器 v0.1（只报建议）")
    ap.add_argument("mid", nargs="?", help="我们的 MIDI（--selftest 时可省）")
    ap.add_argument("--audio", default=None, help="原曲音频（全混音）")
    ap.add_argument("--stems-dir", default=None, help="分轨目录（h6_*.wav / h4_*.wav）")
    ap.add_argument("--at", default=None, help="只判这一段，如 18.9-19.7")
    ap.add_argument("--tracks", default=None, help="只判这些轨（逗号分隔）")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--min-pitch", type=int, default=60, help="只判 ≥ 该音高的音（默认 60=C4）")
    ap.add_argument("--only-runs", action="store_true",
                    help="只判短音串（转音/跑动）：相邻 ≤3 半音、时值 ≤1.2s、同向 ≥0.7")
    ap.add_argument("--run-min", type=int, default=4, help="短音串最少几个音（默认 4）")
    ap.add_argument("--ratio-thr", type=float, default=THR_LO, help="降八度阈值（默认 1.30）")
    ap.add_argument("--json", default=None, help="把逐音读数写成 JSON（供阈值标定）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    THR_LO = a.ratio_thr

    if a.selftest:
        return 0 if selftest() else 1
    if not a.mid or not a.audio:
        ap.error("要 <mid> 与 --audio（除非 --selftest）")

    y_mix, sr_mix = sf.read(a.audio, dtype="float32", always_2d=True)
    y_mix = y_mix.mean(axis=1)
    t0 = t1 = None
    if a.at:
        t0, t1 = [float(x) for x in a.at.replace("–", "-").replace("~", "-").split("-")]
    tracks = set(a.tracks.split(",")) if a.tracks else None

    all_notes = notes_of(a.mid, tracks)
    if a.only_runs:
        from collections import defaultdict
        by = defaultdict(list)
        for r in all_notes:
            by[r[0]].append(r)
        keep = set()
        for _tr, seg in by.items():
            seg.sort(key=lambda x: x[1])
            i = 0
            while i < len(seg):
                j = i
                while (j + 1 < len(seg) and abs(seg[j + 1][3] - seg[j][3]) <= 3
                       and seg[j][2] <= 1.2 and seg[j + 1][2] <= 1.2):
                    j += 1
                sub = seg[i:j + 1]
                steps = [sub[k + 1][3] - sub[k][3] for k in range(len(sub) - 1)]
                if (len(sub) >= a.run_min
                        and max(x[3] for x in sub) - min(x[3] for x in sub) >= 2
                        and steps
                        and sum(1 for s in steps if (s > 0) == (steps[0] > 0)) / len(steps) >= 0.7):
                    for x in sub:
                        keep.add((x[0], round(x[1], 3)))
                i = j + 1
        before = len(all_notes)
        all_notes = [r for r in all_notes if (r[0], round(r[1], 3)) in keep]
        print("[串过滤] %d -> %d 音（--only-runs，串长 ≥%d）" % (before, len(all_notes), a.run_min))

    rows = []
    cache = {}

    def amp(path, t, p, win, tag):
        if path not in cache:
            yy, ss = sf.read(path, dtype="float32", always_2d=True)
            cache[path] = (yy.mean(axis=1), ss)
        ys, ss = cache[path]
        return band_amp(ys, ss, t, p, win=win)

    for tr, st, dur, p, vel in all_notes:
        if p < a.min_pitch:
            continue
        if t0 is not None and not (t0 <= st <= t1):
            continue
        win = max(0.06, min(0.20, dur))
        ratios = []
        e_hi, e_lo = amp(a.audio, st, p, win, "mix"), amp(a.audio, st, p - 12, win, "mix")
        ratios.append(("mix", (e_lo / e_hi) if e_hi > 0 and e_lo > 0 else None))
        for tag, path in find_stems(a.stems_dir, tr):
            eh, el = amp(path, st, p, win, tag), amp(path, st, p - 12, win, tag)
            ratios.append((tag, (el / eh) if eh > 0 and el > 0 else None))
        votes = [v for _t, r in ratios for v in [vote(r)] if v]
        n_lo = votes.count("降八度")
        n_keep = votes.count("保持")
        if len(votes) < 2:
            final = "测不到(有效尺子<2)"
        elif n_lo >= 2 and n_lo > n_keep:
            final = "降八度(%d/%d)" % (n_lo, len(votes))
        elif n_keep >= 2 and n_keep > n_lo:
            final = "保持(%d/%d)" % (n_keep, len(votes))
        else:
            final = "测不到(尺子分歧)"
        rows.append((tr, st, dur, p, ratios, final))

    print("=" * 108)
    print("八度判决 v0.1 · %s" % os.path.basename(a.mid))
    print("  原曲=%s  分轨=%s  窗=%s  轨=%s  ≥p%d  阈值=%.2f  共 %d 音"
          % (os.path.basename(a.audio), a.stems_dir or "(无)", a.at or "全曲",
             a.tracks or "全部", a.min_pitch, THR_LO, len(rows)))
    print("  判据：band_amp 只比基频 ±3%%；**多数票**（全混音 + h6 分轨 + h4 分轨）")
    print("-" * 108)
    for tr, st, dur, p, ratios, final in rows[:a.top]:
        rs = "  ".join("%s=%s" % (t, "%.2f" % r if r is not None else "—") for t, r in ratios)
        print("%-8s %8.3f %6.3f p%-4d %-40s %s" % (tr, st, dur, p, rs, final))
    if len(rows) > a.top:
        print("  …（共 %d 行，只印前 %d）" % (len(rows), a.top))

    from collections import Counter
    c = Counter(r[5].split("(")[0] for r in rows)
    print("-" * 108)
    print("  汇总：%s" % dict(c))
    if "降八度" in c:
        print("  ⚠ 这只是**建议**：过修风险未标定 —— 要动数据必须先在多首上做 A/B（用户听感定阈值）")
    if a.json:
        import json as _json
        with open(a.json, "w", encoding="utf-8") as fh:
            _json.dump([{"track": t, "start": s, "dur": d, "pitch": p,
                         "ratios": {k: v for k, v in rs}, "final": f}
                        for t, s, d, p, rs, f in rows], fh, ensure_ascii=False)
        print("  JSON -> %s（%d 行）" % (a.json, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
