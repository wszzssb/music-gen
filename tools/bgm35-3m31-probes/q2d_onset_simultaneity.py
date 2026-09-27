#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q2d_onsets.py —— 原曲 other 213-216.5s：**单音线条** 还是 **同时发声的和弦/pad**？

逐起音取 60ms 谱，给出候选音高（±35音分内峰值，相对带内峰值 dB）。
判据：一次起音里同时"在场"（≥峰值-12dB）的音高个数 —— 单线=1；和弦/pad≥3。
对照窗 = 205.0-208.0s（台账认定那里是单音线条 p77）。
另外给基频的**谐波剖面**（f0/2f0/3f0/4f0 相对电平）——区分"真基频"与"次谐波假象"。
"""
import json
import os

import numpy as np
import soundfile as sf
import librosa

W = r"D:\test\_tmp\b35"
OUT = r"D:\test\_tmp\b35-models\q2"
STEMS = os.path.join(W, r"stems\htdemucs_6s\BGM35")
SR, HOP = 16000, 160
CAND = [72, 74, 75, 76, 77, 79, 80, 82, 83, 84, 86, 87, 88, 89, 90, 91, 92, 94, 96, 97]
NOTE = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def nn(m):
    m = int(round(m))
    return "%s%d" % (NOTE[m % 12], m // 12 - 1)


def midi2f(m):
    return 440.0 * 2.0 ** ((np.asarray(m, float) - 69.0) / 12.0)


def load(path, t0, t1):
    x, fs = sf.read(path, dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if fs != SR:
        x = librosa.resample(x, orig_sr=fs, target_sr=SR, res_type="kaiser_best")
    a, b = int(t0 * SR), int(t1 * SR)
    return np.ascontiguousarray(x[a:b], dtype=np.float32)


def peaks_and_levels(spec, fr, p):
    f = float(midi2f(p))
    lo = int(np.searchsorted(fr, f * 2 ** (-35 / 1200.0)))
    hi = int(np.searchsorted(fr, f * 2 ** (35 / 1200.0))) + 1
    if hi <= lo or lo >= len(spec):
        return None
    return float(spec[lo:hi].max())


def run(tag, path, t0, t1, win_ms=60.0):
    x = load(path, t0, t1)
    on = librosa.onset.onset_detect(y=x, sr=SR, hop_length=HOP, units="time",
                                    backtrack=False, pre_max=3, post_max=3,
                                    pre_avg=3, post_avg=3, delta=0.02, wait=3)
    n = int(win_ms / 1000.0 * SR)
    w = np.hanning(n)
    fr = np.fft.rfftfreq(n, 1.0 / SR)
    i0 = int(np.searchsorted(fr, 600.0))
    i1 = int(np.searchsorted(fr, 2600.0))
    iw = int(np.searchsorted(fr, min(8000.0, SR / 2 * 0.98)))
    print("\n===== %s  [%.1f-%.1f s]  起音 %d 个" % (tag, t0, t1, len(on)))
    rows = []
    for t in on:
        a = int(t * SR)
        if a + n > len(x):
            continue
        spec = np.abs(np.fft.rfft(x[a:a + n] * w))
        ref = spec[i0:i1].max()
        lv = {}
        for p in CAND:
            v = peaks_and_levels(spec, fr, p)
            if v is not None:
                lv[p] = round(20 * np.log10(max(v, 1e-12) / max(ref, 1e-12)), 1)
        present = sorted([p for p, d in lv.items() if d >= -12.0])
        # 谐波剖面（用最低在场音高当 f0）
        prof = None
        if present:
            f0 = float(midi2f(min(present)))
            prof = []
            for k in (1, 2, 3, 4, 5):
                fk = f0 * k
                if fk > 8000:
                    break
                lo = int(np.searchsorted(fr, fk * 2 ** (-35 / 1200.0)))
                hi = int(np.searchsorted(fr, fk * 2 ** (35 / 1200.0))) + 1
                v = spec[lo:hi].max() if hi > lo else 0.0
                prof.append(round(20 * np.log10(max(v, 1e-12) / max(ref, 1e-12)), 1))
        rows.append({"t": round(float(t + t0), 3), "n_present": len(present),
                     "present": present, "levels": lv, "harm_profile": prof})
    print("  逐起音同时在场的音高（≥带内峰值-12dB）:")
    for r in rows:
        print("    %7.3fs  n=%d  %-28s 谐波剖面(f0/2f/3f/4f/5f) %s"
              % (r["t"], r["n_present"], " ".join("%s" % nn(p) for p in r["present"]),
                 r["harm_profile"]))
    cnt = [r["n_present"] for r in rows]
    print("  同时在场的音高个数：中位 %.1f  均值 %.2f  分布 %s"
          % (float(np.median(cnt)), float(np.mean(cnt)),
             {k: cnt.count(k) for k in sorted(set(cnt))}))
    from collections import Counter
    c = Counter()
    for r in rows:
        for p in r["present"][:1]:
            c[p] += 1
    print("  最低在场音高分布 %s" % sorted(c.items(), key=lambda kv: -kv[1])[:8])
    json.dump(rows, open(os.path.join(OUT, "onsets_%s.json" % tag.replace(" ", "_")
                                      .replace("/", "_")), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    run("原曲other_213.0-216.5", os.path.join(STEMS, "other.wav"), 213.0, 216.5)
    run("原曲other_205.0-208.0(单线对照)", os.path.join(STEMS, "other.wav"), 205.0, 208.0)
    run("原曲other_216.0-220.0", os.path.join(STEMS, "other.wav"), 216.0, 220.0)


if __name__ == "__main__":
    main()
