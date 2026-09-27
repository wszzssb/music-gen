#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q1b_drums.py —— Q1 补充：换一个 Demucs 模型再分一次 + 谱平坦度/谐波对齐

判据 E（换模型复分）：把 205-222s 的**原曲混音**用 `htdemucs_ft`（另一套权重）再分一次，
  与 `htdemucs_6s` 的 drums 轨逐秒比。两个模型都在 216-220s 给出能量 → 才可能是真内容；
  只有一个模型有 → 是 Demucs 的软掩码残留。
判据 F（谱平坦度 + 谐波对齐）：真镲/鼓是**噪声型**（谱平坦度高）；
  漏过来的合成器旋律是**谐波型**（平坦度低、峰落在 melody F0 的整数倍上）。
"""
import json
import os
import subprocess
import sys

import numpy as np
import soundfile as sf
import librosa
from scipy.signal import butter, sosfiltfilt

PY = r"D:\software\skill\music-gen\.venv-ml\Scripts\python.exe"
W = r"D:\test\_tmp\b35"
OUT = r"D:\test\_tmp\b35-models\q1"
STEMS = os.path.join(W, r"stems\htdemucs_6s\BGM35")
CLIP = os.path.join(OUT, "clip_204_223.wav")
SR = 22050


def load(path, sr=SR, t0=None, t1=None):
    x, fs = sf.read(path, dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if t0 is not None:
        a, b = int(t0 * fs), int(t1 * fs)
        x = x[a:b]
    if fs != sr:
        x = librosa.resample(x, orig_sr=fs, target_sr=sr, res_type="kaiser_best")
    return np.ascontiguousarray(x, dtype=np.float32)


def rms_db(x):
    return 20 * np.log10(max(1e-9, float(np.sqrt(np.mean(x ** 2)))))


def make_clip():
    if os.path.exists(CLIP):
        return
    x, fs = sf.read(os.path.join(W, "BGM35.flac"), dtype="float32", always_2d=True)
    a, b = int(204.0 * fs), int(223.0 * fs)
    sf.write(CLIP, x[a:b], fs)
    print("写好 %s" % CLIP)


def sep(model):
    outdir = os.path.join(OUT, "sep_" + model)
    d = os.path.join(outdir, model, "clip_204_223", "drums.wav")
    if os.path.exists(d):
        print("已有 %s" % d)
        return d
    cmd = [PY, "-m", "demucs", "-n", model, "--two-stems=drums",
           "-o", outdir, CLIP]
    print("跑:", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    print("  exit=%d" % r.returncode)
    tail = (r.stdout or "")[-800:] + (r.stderr or "")[-800:]
    print("  " + tail.replace("\n", "\n  "))
    return d if os.path.exists(d) else None


def flatness(x, fs, band=(200.0, 12000.0), n_fft=2048):
    S = np.abs(librosa.stft(x.astype(np.float64), n_fft=n_fft, hop_length=512)) ** 2
    fr = librosa.fft_frequencies(sr=fs, n_fft=n_fft)
    sel = (fr >= band[0]) & (fr <= band[1])
    P = S[sel]
    gm = np.exp(np.mean(np.log(P + 1e-12), axis=0))
    am = np.mean(P, axis=0)
    return float(np.median(gm / np.maximum(am, 1e-20)))


def harmonic_align(x, fs, f0_list, band=(200.0, 12000.0), n_fft=4096):
    """该窗平均谱里，落在 f0 整数倍(±35音分)上的能量占带内比例"""
    S = np.abs(librosa.stft(x.astype(np.float64), n_fft=n_fft, hop_length=512,
                            win_length=n_fft, window="hann", center=True))
    P = (S ** 2).mean(axis=1)
    fr = librosa.fft_frequencies(sr=fs, n_fft=n_fft)
    sel = (fr >= band[0]) & (fr <= band[1])
    tot = P[sel].sum()
    hit = 0.0
    for p in f0_list:
        f = 440.0 * 2 ** ((p - 69) / 12.0)
        k = 1
        while f * k < band[1] and k <= 16:
            fk = f * k
            lo, hi = fk * 2 ** (-35 / 1200.0), fk * 2 ** (35 / 1200.0)
            m = (fr >= lo) & (fr <= hi)
            hit += P[m].sum()
            k += 1
    return float(hit / max(tot, 1e-20))


def main():
    os.makedirs(OUT, exist_ok=True)
    make_clip()
    rep = {}
    stems = {}
    for m in ("htdemucs_6s", "htdemucs_ft"):
        p = sep(m)
        if p:
            stems[m] = load(p)
    if "htdemucs_6s" not in stems:
        stems["htdemucs_6s"] = load(os.path.join(STEMS, "drums.wav"), t0=204.0, t1=223.0)
    # 对齐长度
    n = min(len(v) for v in stems.values())
    print("\n=== 判据 E：两个 Demucs 模型分出的 drums 轨（204-223s，逐秒 dB）===")
    print("   t    htdemucs_6s  htdemucs_ft   差")
    rows = []
    for i in range(n // SR):
        a, b = i * SR, (i + 1) * SR
        v6, vf = rms_db(stems["htdemucs_6s"][a:b]), rms_db(stems["htdemucs_ft"][a:b])
        rows.append({"t": 204 + i, "six": round(v6, 1), "ft": round(vf, 1),
                     "diff": round(vf - v6, 1)})
        if 204 + i >= 205:
            print("  %4d %11.1f %12.1f %6.1f" % (204 + i, v6, vf, vf - v6))
    rep["E_two_models"] = rows

    print("\n=== 判据 F：谱平坦度（噪声型↔谐波型）与谐波对齐 ===")
    other = load(os.path.join(STEMS, "other.wav"))
    drums_full = load(os.path.join(STEMS, "drums.wav"))
    for tag, src, t0, t1 in (
            ("drums 205-208(真鼓对照)", drums_full, 205.0, 208.0),
            ("drums 216-220(争议)", drums_full, 216.0, 220.0),
            ("drums 213-216.5(争议)", drums_full, 213.0, 216.5),
            ("drums 221-223(鼓层回来)", drums_full, 221.0, 223.0),
            ("other 216-220(纯旋律参照)", other, 216.0, 220.0),
            ("other 205-208(参照)", other, 205.0, 208.0)):
        a, b = int(t0 * SR), int(t1 * SR)
        seg = src[a:b]
        if not len(seg):
            continue
        fl = flatness(seg, SR)
        ha = harmonic_align(seg, SR, [77, 80, 82, 84])
        rep.setdefault("F_flatness", {})[tag] = {"spectral_flatness": round(fl, 5),
                                                 "harm_align_77_84": round(ha, 3),
                                                 "rms_db": round(rms_db(seg), 1)}
        print("  %-26s 谱平坦度 %.5f   77/80/82/84 谐波对齐 %.1f%%   RMS %.1f dB"
              % (tag, fl, 100 * ha, rms_db(seg)))

    with open(os.path.join(OUT, "q1b.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print("\n写出 %s" % os.path.join(OUT, "q1b.json"))


if __name__ == "__main__":
    main()
