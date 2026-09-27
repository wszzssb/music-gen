#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q1_drums.py —— Q1：216-220s 的 drums 分轨到底是**真打击**还是 **Demucs 串音**？

四条互不依赖的判据（都在**同一首曲子的两个对照窗**上做，避免"单首素材下结论"）：
  A 电平对比   ：drums 分轨 vs other 分轨的逐秒 RMS（真鼓段 drums 应**高于** other）
  B 线性泄漏回归：drums ≈ Σ α_s · (其它 5 条分轨) → **R²** = drums 的能量里有多少
                  能被"其它层的线性组合"解释。串音是软掩码漏过来的同一信号 → R² 高；
                  真鼓是独立声源 → R² 低。
  C 包络相关   ：旋律带(600-2600Hz)内 drums 包络 vs other 包络的相关系数（串音→高相关）
  D 起音共位   ：drums 分轨的起音有多少落在 other 分轨起音的 ±50ms 内
判据 A-D 全部**先在一段公认有真鼓的窗口上标定**，再看 216-220s。
"""
import json
import os
import sys

import numpy as np
import soundfile as sf
import librosa
from scipy.signal import butter, sosfiltfilt

W = r"D:\test\_tmp\b35"
OUT = r"D:\test\_tmp\b35-models\q1"
STEMS = os.path.join(W, r"stems\htdemucs_6s\BGM35")
SR = 22050
HOP = 256
NAMES = ["drums", "other", "piano", "guitar", "bass", "vocals"]


def load(path, sr=SR):
    x, fs = sf.read(path, dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if fs != sr:
        x = librosa.resample(x, orig_sr=fs, target_sr=sr, res_type="kaiser_best")
    return np.ascontiguousarray(x, dtype=np.float32)


def bp(x, fs, lo, hi, order=4):
    ny = fs / 2.0
    sos = butter(order, [lo / ny, min(hi / ny, 0.99)], btype="band", output="sos")
    return sosfiltfilt(sos, x.astype(np.float64))


def seg(x, fs, t0, t1):
    a, b = int(t0 * fs), int(t1 * fs)
    return x[max(0, a):min(len(x), b)]


def rms_db(x):
    return 20 * np.log10(max(1e-9, float(np.sqrt(np.mean(x ** 2)))))


def envelope(x, fs, lo=600.0, hi=2600.0, win=0.01):
    xb = bp(x, fs, lo, hi)
    n = max(1, int(win * fs))
    S = np.abs(librosa.stft(xb, n_fft=1024, hop_length=HOP, center=True))
    return S.sum(axis=0)


def leakage_r2(target, sources, fs):
    """drums ≈ Σ α·src 的最小二乘 → R²"""
    n = min([len(target)] + [len(s) for s in sources])
    A = np.stack([s[:n] for s in sources], axis=1)
    y = target[:n]
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    pred = A @ coef
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum(y ** 2))
    return 1.0 - ss_res / max(ss_tot, 1e-20), coef


def onset_times(x, fs, band=(600.0, 2600.0), delta=0.02):
    xb = bp(x, fs, band[0], band[1]).astype(np.float32)
    return librosa.onset.onset_detect(y=xb, sr=fs, hop_length=HOP, units="time",
                                      backtrack=False, pre_max=3, post_max=3,
                                      pre_avg=3, post_avg=3, delta=delta, wait=3)


def main():
    os.makedirs(OUT, exist_ok=True)
    print("载入 6 条分轨 ...")
    X = {n: load(os.path.join(STEMS, n + ".wav")) for n in NAMES}
    fs = SR
    mix = load(os.path.join(W, "BGM35.flac"))
    L = min(len(mix), *(len(v) for v in X.values()))

    # ---------- 逐秒电平表（全曲）→ 自动挑"公认有真鼓"的窗口
    per_sec = []
    for t in range(int(L / fs) - 1):
        a, b = t * fs, (t + 1) * fs
        per_sec.append({"t": t, "drums": rms_db(X["drums"][a:b]),
                        "other": rms_db(X["other"][a:b]),
                        "piano": rms_db(X["piano"][a:b])})
    strong = sorted(per_sec, key=lambda r: -r["drums"])[:20]
    print("\n=== 全曲 drums 分轨最响的 20 秒（取连续段当'公认真鼓'对照）===")
    print("   " + " ".join("%ds(%.0f)" % (r["t"], r["drums"]) for r in strong))
    # 取最响秒所在的连续 3 秒窗
    tmax = strong[0]["t"]
    ctrl_real = (float(max(0, tmax - 1)), float(tmax + 2))

    WINS = [
        ("A_真鼓对照(全曲最响处)", ctrl_real),
        ("B_交接的对照窗 205-208", (205.0, 208.0)),
        ("C_争议窗 213-216.5", (213.0, 216.5)),
        ("D_争议窗 216-220", (216.0, 220.0)),
        ("E_争议窗 220-222", (220.0, 222.0)),
    ]
    report = {"windows": {}, "ctrl_real": ctrl_real}
    print("\n" + "=" * 96)
    print("%-26s %8s %8s %8s %8s %8s %8s %8s" %
          ("窗口", "drums", "other", "piano", "guitar", "Δ(d-o)", "泄漏R²", "包络r"))
    print("-" * 96)
    for tag, (t0, t1) in WINS:
        d = seg(X["drums"], fs, t0, t1)
        o = seg(X["other"], fs, t0, t1)
        p = seg(X["piano"], fs, t0, t1)
        g = seg(X["guitar"], fs, t0, t1)
        srcs = [seg(X[n], fs, t0, t1) for n in ("other", "piano", "guitar", "bass", "vocals")]
        # 全带泄漏回归（100-8000Hz，去掉低频轰鸣与超声）
        dl = bp(d, fs, 100.0, 8000.0)
        sl = [bp(s, fs, 100.0, 8000.0) for s in srcs]
        r2, coef = leakage_r2(dl, sl, fs)
        # 旋律带包络相关
        ed, eo = envelope(d, fs), envelope(o, fs)
        n = min(len(ed), len(eo))
        r = float(np.corrcoef(ed[:n], eo[:n])[0, 1]) if n > 8 else float("nan")
        row = {"t0": t0, "t1": t1, "drums_db": round(rms_db(d), 1),
               "other_db": round(rms_db(o), 1), "piano_db": round(rms_db(p), 1),
               "guitar_db": round(rms_db(g), 1),
               "delta_drums_other": round(rms_db(d) - rms_db(o), 1),
               "leak_r2": round(float(r2), 4),
               "leak_coef": [round(float(c), 3) for c in coef],
               "env_corr_600_2600": round(r, 3)}
        report["windows"][tag] = row
        print("%-26s %8.1f %8.1f %8.1f %8.1f %8.1f %8.3f %8.2f"
              % (tag, row["drums_db"], row["other_db"], row["piano_db"], row["guitar_db"],
                 row["delta_drums_other"], r2, r))

    # ---------- 起音共位
    print("\n=== 起音共位（drums 分轨起音 vs other 分轨起音，±50ms）===")
    onset_rep = {}
    for tag, (t0, t1) in WINS:
        d = seg(X["drums"], fs, t0, t1)
        o = seg(X["other"], fs, t0, t1)
        od, oo = onset_times(d, fs), onset_times(o, fs)
        hit = sum(1 for t in od if len(oo) and np.min(np.abs(oo - t)) <= 0.05)
        row = {"n_drums_onsets": int(len(od)), "n_other_onsets": int(len(oo)),
               "drums_onsets_on_other": int(hit),
               "frac": round(hit / len(od), 3) if len(od) else None,
               "drums_onsets": [round(float(t), 3) for t in od],
               "other_onsets": [round(float(t), 3) for t in oo]}
        onset_rep[tag] = row
        print("  %-26s drums 起音 %3d · other 起音 %3d · drums 落在 other ±50ms 内 %3d (%.0f%%)"
              % (tag, len(od), len(oo), hit, 100 * (hit / len(od) if len(od) else 0)))
    report["onsets"] = onset_rep

    # ---------- 逐秒表（205-222s）
    print("\n=== 逐秒电平（205-222s，dB）===")
    print("   t     drums   other   piano  guitar   Δ(d-o)")
    sec_rows = []
    for t in range(205, 222):
        a, b = t * fs, (t + 1) * fs
        row = {"t": t, "drums": round(rms_db(X["drums"][a:b]), 1),
               "other": round(rms_db(X["other"][a:b]), 1),
               "piano": round(rms_db(X["piano"][a:b]), 1),
               "guitar": round(rms_db(X["guitar"][a:b]), 1)}
        row["delta"] = round(row["drums"] - row["other"], 1)
        sec_rows.append(row)
        print("  %4d  %7.1f %7.1f %7.1f %7.1f  %7.1f"
              % (t, row["drums"], row["other"], row["piano"], row["guitar"], row["delta"]))
    report["per_second_205_222"] = sec_rows

    with open(os.path.join(OUT, "q1.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("\n写出 %s" % os.path.join(OUT, "q1.json"))


if __name__ == "__main__":
    main()
