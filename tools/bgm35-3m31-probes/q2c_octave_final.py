#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q2c_final.py —— Q2 定稿测量（修掉 v2 的两个口径问题 + 补对照）

v2 的两个问题（都实测暴露）：
  ① `hs`（谐波筛）跑在**600-2600Hz 带通**后的信号上 → 高八度候选的 2f 已被滤掉
     → 它**结构上不可能**报出 p89+（"0% 高八度"是假读数）。本版 `hs` 跑 600-8000Hz。
  ② `lsp`/`hs` 的"最低峰"会被低 10-20dB 的杂峰带偏（205-208s 真值 p77 处误报 p76）→ 只做旁证。

本版新增：
  · 我们主奏轨在 213-216.5s 的**逐音清单**（我们要问的"我们弹了什么"）
  · guitar / piano 分轨的同窗读数（**对照**：698Hz 会不会是别的乐器漏进 other 的？）
  · **起音锁定**分析（166ms 脉冲串的每一击后 60ms 的谱）→ 不依赖长窗平均
"""
import json
import os

import numpy as np
import soundfile as sf
import librosa
from scipy.signal import butter, sosfiltfilt

PY = r"D:\software\skill\music-gen\.venv-ml\Scripts\python.exe"
W = r"D:\test\_tmp\b35"
OUT = r"D:\test\_tmp\b35-models\q2"
STEMS = os.path.join(W, r"stems\htdemucs_6s\BGM35")
REF_FULL = os.path.join(W, "BGM35.flac")
MINE_WAV = r"D:\test\BGM35_提取\BGM35_r21_补脉冲.wav"
MINE_MID = r"D:\test\BGM35_提取\BGM35_r21_补脉冲.mid"

SR, HOP = 16000, 160
NOTE = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
LOW_SET = {77, 80, 82, 84}          # 698/831/932/1046 Hz
UP_SET = {89, 92, 94, 96}           # 1397/1661/1865/2093 Hz（= LOW_SET + 12）


def nn(m):
    m = int(round(m))
    return "%s%d" % (NOTE[m % 12], m // 12 - 1)


def f2midi(f):
    return 69.0 + 12.0 * np.log2(np.maximum(np.asarray(f, float), 1e-9) / 440.0)


def midi2f(m):
    return 440.0 * 2.0 ** ((np.asarray(m, float) - 69.0) / 12.0)


def load_mono(path, t0=None, t1=None, sr=SR):
    x, fs = sf.read(path, dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if t0 is not None:
        a = int(round(t0 * fs))
        b = int(round(t1 * fs)) if t1 is not None else len(x)
        x = x[max(0, a):max(0, min(len(x), b))]
    if fs != sr:
        x = librosa.resample(x, orig_sr=fs, target_sr=sr, res_type="kaiser_best")
        fs = sr
    return np.ascontiguousarray(x, dtype=np.float32), fs


def bandpass(x, fs, lo, hi, order=6):
    ny = fs / 2.0
    sos = butter(order, [lo / ny, min(hi / ny, 0.99)], btype="band", output="sos")
    return sosfiltfilt(sos, x.astype(np.float64)).astype(np.float32)


# ---------------------------------------------------------------- 尺子
def r_pyin(x, fs):
    lo, hi = 550.0, 2700.0
    f0, vf, vp = librosa.pyin(x.astype(np.float64), fmin=lo, fmax=hi, sr=fs,
                              frame_length=1024, hop_length=HOP, resolution=0.05,
                              fill_na=np.nan)
    return f0, vp


def r_hs(x, fs, lo=600.0, hi=2600.0, rel_db=-25.0, need=2, cents=35.0, n_fft=4096):
    """谐波筛（**在宽带上跑**：候选基频限 600-2600，谐波验证到 8kHz）"""
    S = np.abs(librosa.stft(x.astype(np.float64), n_fft=n_fft, hop_length=HOP,
                            win_length=n_fft, window="hann", center=True))
    freqs = librosa.fft_frequencies(sr=fs, n_fft=n_fft)
    i0, i1 = int(np.searchsorted(freqs, lo)), int(np.searchsorted(freqs, hi))
    iw = int(np.searchsorted(freqs, min(8000.0, fs / 2 * 0.98)))
    f0 = np.full(S.shape[1], np.nan)
    for i in range(S.shape[1]):
        spec = S[:, i]
        seg = spec[i0:iw]
        if seg.size < 3:
            continue
        ref = seg.max()
        if ref <= 0:
            continue
        thr = ref * 10.0 ** (rel_db / 20.0)
        pk = [j for j in range(1, len(seg) - 1)
              if seg[j] > seg[j - 1] and seg[j] >= seg[j + 1] and seg[j] > thr]
        for j in pk:
            f = freqs[i0 + j]
            if f > hi:
                break
            sup = 0
            for k in (2, 3, 4):
                fk = f * k
                if fk >= freqs[-1]:
                    continue
                a = int(np.searchsorted(freqs, fk * 2 ** (-cents / 1200.0)))
                b = int(np.searchsorted(freqs, fk * 2 ** (cents / 1200.0))) + 1
                if b > a and spec[a:b].size and spec[a:b].max() > thr:
                    sup += 1
            if sup >= need:
                f0[i] = f
                break
    return f0, None


RULERS = (("pyin", r_pyin), ("hs", r_hs))


def hist(f0, prob=None, thr=None, lo=60, hi=110):
    m = f2midi(f0)
    ok = np.isfinite(m)
    if prob is not None and thr is not None:
        ok &= (prob >= thr)
    m = np.round(m[ok]).astype(int)
    m = m[(m >= lo) & (m <= hi)]
    v, c = np.unique(m, return_counts=True)
    return {int(a): int(b) for a, b in zip(v, c)}


def frac(h, keys):
    t = sum(h.values())
    return (sum(c for k, c in h.items() if k in keys) / t) if t else 0.0


def energy_share(x, fs, pitches, tol=60.0, n_fft=8192, band=(600.0, 2600.0)):
    S = np.abs(librosa.stft(x.astype(np.float64), n_fft=n_fft, hop_length=HOP,
                            win_length=n_fft, window="hann", center=True))
    P = (S ** 2).sum(axis=1)
    fr = librosa.fft_frequencies(sr=fs, n_fft=n_fft)
    ib = (fr >= band[0]) & (fr <= band[1])
    tot = P[ib].sum()
    out = {}
    for p in pitches:
        f = float(midi2f(p))
        sel = (fr >= f * 2 ** (-tol / 1200.0)) & (fr <= f * 2 ** (tol / 1200.0))
        out[p] = float(P[sel].sum() / max(tot, 1e-20))
    return out


def run(tag, path, t0, t1, wide_hs=250.0):
    x, fs = load_mono(path, t0, t1)
    xb = bandpass(x, fs, 600.0, 2600.0)
    xw = bandpass(x, fs, wide_hs, 8000.0)
    print("\n===== %s  [%.1f-%.1f s]  band RMS %.1f dB" % (tag, t0, t1,
          20 * np.log10(max(1e-9, float(np.sqrt((xb ** 2).mean()))))))
    item = {"path": path, "t0": t0, "t1": t1, "methods": {}}
    for mname, m in RULERS:
        sig = xw if mname == "hs" else xb
        f0, prob = m(sig, fs)
        thr = 0.0 if prob is not None else None
        h = hist(f0, prob, thr)
        top = sorted(h.items(), key=lambda kv: -kv[1])[:10]
        fl, fu = frac(h, LOW_SET), frac(h, UP_SET)
        item["methods"][mname] = {"top": top, "hist": h, "low": round(fl, 3),
                                  "up": round(fu, 3), "n": sum(h.values())}
        print("  %-5s n=%4d  top10 %s" % (mname, sum(h.values()), top))
        print("        → 低八度组(77/80/82/84) %.0f%%  |  高八度组(89/92/94/96) %.0f%%"
              % (100 * fl, 100 * fu))
    es = energy_share(xb, fs, [74, 76, 77, 79, 80, 82, 84, 86, 88, 89, 91, 92, 94, 96, 97])
    item["energy_share"] = {int(k): round(v, 4) for k, v in es.items()}
    lo_sum = sum(es[p] for p in (77, 80, 82, 84))
    up_sum = sum(es[p] for p in (89, 92, 94, 96))
    item["energy_low_sum"], item["energy_up_sum"] = round(lo_sum, 4), round(up_sum, 4)
    print("  能量份额: 低八度组 %.1f%%  高八度组 %.1f%%  （其余 %.1f%%）"
          % (100 * lo_sum, 100 * up_sum, 100 * (1 - lo_sum - up_sum)))
    print("  逐音高 >2%%: " + "  ".join("%s(%.0fHz) %.1f%%" % (nn(p), midi2f(p), 100 * v)
                                        for p, v in sorted(es.items()) if v > 0.02))
    return item


def lead_notes():
    """我们的主奏轨在 213-216.5s 到底弹了什么（逐音）"""
    import pretty_midi
    pm = pretty_midi.PrettyMIDI(MINE_MID)
    ins = max([i for i in pm.instruments if i.program == 80 and not i.is_drum],
              key=lambda i: len(i.notes))
    print("\n===== 我们 MIDI 的方波主奏（program 80）在 212.5-217.0s =====")
    rows = [n for n in ins.notes if 212.5 <= n.start <= 217.0]
    rows.sort(key=lambda n: n.start)
    for n in rows:
        print("   %7.3f - %7.3f  p%-3d %-4s vel %d  时值 %5.0fms"
              % (n.start, n.end, n.pitch, nn(n.pitch), n.velocity,
                 1000 * (n.end - n.start)))
    print("   小计 %d 音；音高集合 %s" % (len(rows), sorted({n.pitch for n in rows})))
    # 全曲主奏在 210-222.5 的音高集合（看"整体是不是也偏高"）
    allr = [n for n in ins.notes if 210.0 <= n.start <= 222.5]
    from collections import Counter
    c = Counter(n.pitch for n in allr)
    print("   210-222.5s 主奏音高分布 %s" % sorted(c.items(), key=lambda kv: -kv[1]))
    return rows


def onset_locked(tag, path, t0, t1, pre=0.0, win_ms=60.0):
    """**起音锁定**：脉冲串每一击后 60ms 的谱 → 最低的、有谐波支撑的峰（宽带）"""
    x, fs = load_mono(path, t0, t1)
    xb = bandpass(x, fs, 600.0, 2600.0)
    xw = bandpass(x, fs, 250.0, 8000.0)
    on = librosa.onset.onset_detect(y=xb.astype(np.float64), sr=fs, hop_length=HOP,
                                    backtrack=False, units="time",
                                    pre_max=3, post_max=3, pre_avg=3, post_avg=3,
                                    delta=0.02, wait=3)
    print("\n===== %s 起音锁定（%.1f-%.1f s）: %d 个起音" % (tag, t0, t1, len(on)))
    dt = np.median(np.diff(on)) if len(on) > 1 else float("nan")
    print("   起音间隔中位 %.0f ms" % (1000 * dt))
    n = int(win_ms / 1000.0 * fs)
    w = np.hanning(n)
    keep = []
    for t in on:
        a = int((t + pre) * fs)
        b = a + n
        if a < 0 or b > len(xw):
            continue
        spec = np.abs(np.fft.rfft(xw[a:b] * w))
        fr = np.fft.rfftfreq(n, 1.0 / fs)
        i0, i1 = int(np.searchsorted(fr, 600.0)), int(np.searchsorted(fr, 2600.0))
        iw = int(np.searchsorted(fr, min(8000.0, fs / 2 * 0.98)))
        seg = spec[i0:iw]
        if seg.size < 3:
            continue
        thr = seg.max() * 10 ** (-25.0 / 20.0)
        pk = [j for j in range(1, len(seg) - 1)
              if seg[j] > seg[j - 1] and seg[j] >= seg[j + 1] and seg[j] > thr]
        pick, pklist = None, []
        for j in pk:
            f = fr[i0 + j]
            if f > 2600.0:
                break
            pklist.append(round(float(f), 1))
            if pick is None:
                sup = 0
                for k in (2, 3, 4):
                    fk = f * k
                    aa = int(np.searchsorted(fr, fk * 2 ** (-35 / 1200.0)))
                    bb = int(np.searchsorted(fr, fk * 2 ** (35 / 1200.0))) + 1
                    if bb <= len(spec) and bb > aa and spec[aa:bb].max() > thr:
                        sup += 1
                if sup >= 2:
                    pick = f
        keep.append({"t": round(float(t + pre), 3), "f0": round(pick, 1) if pick else None,
                     "midi": int(round(float(f2midi(pick)))) if pick else None,
                     "lowest_peak": pklist[0] if pklist else None,
                     "lowest_peak_midi": int(round(float(f2midi(pklist[0])))) if pklist else None})
    seq = [k["midi"] for k in keep]
    print("   **谐波筛**逐击基频: %s" % [k["f0"] for k in keep])
    print("   **谐波筛**逐击音高: %s" % seq)
    print("   最低显著峰  逐击音高: %s" % [k["lowest_peak_midi"] for k in keep])
    from collections import Counter
    print("   谐波筛 音高分布 %s" % sorted(Counter([s for s in seq if s]).items(),
                                       key=lambda kv: -kv[1]))
    return keep


def main():
    os.makedirs(OUT, exist_ok=True)
    res = {}
    res["lead_notes_rows"] = [
        {"start": round(n.start, 3), "end": round(n.end, 3), "pitch": n.pitch,
         "name": nn(n.pitch), "vel": n.velocity} for n in lead_notes()]
    jobs = [
        ("ref_other_213.0-216.5", os.path.join(STEMS, "other.wav"), 213.0, 216.5),
        ("ref_full_213.0-216.5", REF_FULL, 213.0, 216.5),
        ("ref_guitar_213.0-216.5", os.path.join(STEMS, "guitar.wav"), 213.0, 216.5),
        ("ref_piano_213.0-216.5", os.path.join(STEMS, "piano.wav"), 213.0, 216.5),
        ("ref_other_205.0-208.0(已知 p77 对照)", os.path.join(STEMS, "other.wav"), 205.0, 208.0),
        ("mine_full_213.0-216.5", MINE_WAV, 213.0, 216.5),
        ("mine_lead_solo(实测窗约213.4-216.9)", os.path.join(OUT, "solo_asis.wav"), 0.5, 4.0),
        ("mine_lead_solo_down12(对照)", os.path.join(OUT, "solo_down12.wav"), 0.5, 4.0),
    ]
    for tag, path, t0, t1 in jobs:
        if not os.path.exists(path):
            print("跳过（缺）", path)
            continue
        res[tag] = run(tag, path, t0, t1)
    res["onset_other_213.0-216.5"] = onset_locked(
        "原曲 other", os.path.join(STEMS, "other.wav"), 213.0, 216.5)
    res["onset_mine_213.0-216.5"] = onset_locked("我们的渲染", MINE_WAV, 213.0, 216.5)
    with open(os.path.join(OUT, "final.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("\n写出 %s" % os.path.join(OUT, "final.json"))


if __name__ == "__main__":
    main()
