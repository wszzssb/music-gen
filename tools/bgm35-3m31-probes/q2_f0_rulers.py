#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q2_melody.py —— BGM35 211-222s「旋律到底是什么音高」的三把独立尺子 + 已知答案自检

问题（交接文档 Q2）：3:33-3:41 的旋律线原曲到底是 p77/p80/p97 还是 p89/p92？
  注意 p89 = 1396.9Hz = 2 x 698.5Hz = p77 的**第二分音** —— 这正是典型的**八度错**症状。

三把尺子（模型族各不相同，互为独立证据）：
  1. crepe  : torchcrepe（CNN，16k，fmax=2006Hz 硬上限）
  2. pyin   : librosa.pyin（自相关 + HMM，范围任意）
  3. lsp    : **最低显著谱峰**（调和音的基频 = 最低分音；纯 DSP，无模型）
     —— 只有它不依赖任何训练分布，"原曲 698Hz 处到底有没有峰"由它直接回答。

自检（先做，不通过就不许拿读数下结论）：合成**已知音高**的信号（含"二次谐波更强"的坏情况）
+ GM 真音源渲染**已知 MIDI**，看三把尺子各报什么。

用法：
  python q2_melody.py synth                  # 合成自检
  python q2_melody.py render                 # 造 GM 独奏自检素材（需 render_midi.py）
  python q2_melody.py measure                # 真音频测量（原曲 other 分轨 / 原曲整曲 / 我们的渲染）
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np
import soundfile as sf

import librosa
from scipy.signal import butter, sosfiltfilt

# ---------------------------------------------------------------- 环境常量
PY = r"D:\software\skill\music-gen\.venv-ml\Scripts\python.exe"
REPO = r"D:\software\skill\music-gen"
W = r"D:\test\_tmp\b35"
OUT = r"D:\test\_tmp\b35-models\q2"
STEMS = os.path.join(W, r"stems\htdemucs_6s\BGM35")
REF_FULL = os.path.join(W, "BGM35.flac")
MINE_WAV = r"D:\test\BGM35_提取\BGM35_r21_补脉冲.wav"
MINE_MID = r"D:\test\BGM35_提取\BGM35_r21_补脉冲.mid"

SR = 16000
HOP = 160                     # 10 ms
BAND = (600.0, 2600.0)        # 旋律带（p77=698Hz … p97=2217Hz）
FMIN, FMAX = 550.0, 2700.0


# ---------------------------------------------------------------- 基础工具
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


def bandpass(x, fs, lo=BAND[0], hi=BAND[1], order=6):
    ny = fs / 2.0
    sos = butter(order, [lo / ny, min(hi / ny, 0.99)], btype="band", output="sos")
    return sosfiltfilt(sos, x.astype(np.float64)).astype(np.float32)


def f2midi(f):
    f = np.asarray(f, dtype=np.float64)
    return 69.0 + 12.0 * np.log2(np.maximum(f, 1e-9) / 440.0)


def midi2f(m):
    return 440.0 * 2.0 ** ((np.asarray(m, dtype=np.float64) - 69.0) / 12.0)


# ---------------------------------------------------------------- 三把尺子
def est_crepe(x, fs, fmin=FMIN, fmax=2000.0):
    """torchcrepe（CNN）。注意 fmax 硬上限 2006Hz（模型只到 16k/2006Hz）。"""
    import torch
    import torchcrepe
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    t = torch.from_numpy(np.ascontiguousarray(x, dtype=np.float32))[None]
    with torch.no_grad():
        f0, pd = torchcrepe.predict(t, fs, hop_length=HOP, fmin=fmin, fmax=fmax,
                                    model="full", batch_size=2048, device=dev,
                                    return_periodicity=True, pad=True)
    f0 = f0[0].cpu().numpy().astype(np.float64)
    pd = pd[0].cpu().numpy().astype(np.float64)
    return f0, pd, 0.21          # torchcrepe 默认 voiced 阈值


def est_pyin(x, fs, fmin=FMIN, fmax=FMAX):
    """librosa.pyin（自相关 + HMM），范围不受 2006Hz 限制。"""
    f0, vflag, vprob = librosa.pyin(x.astype(np.float64), fmin=fmin, fmax=fmax, sr=fs,
                                    frame_length=1024, hop_length=HOP,
                                    resolution=0.1, fill_na=np.nan)
    return f0, vprob, 0.0


def est_lsp(x, fs, band=BAND, rel_db=-20.0, n_fft=4096):
    """**最低显著谱峰**：带内低于"带内最大峰 - rel_db"的局部极大不算，
    取剩下里频率最低的那个（调和音的基频 = 最低分音）。纯 DSP。"""
    S = np.abs(librosa.stft(x.astype(np.float64), n_fft=n_fft, hop_length=HOP,
                            win_length=n_fft, window="hann", center=True))
    freqs = librosa.fft_frequencies(sr=fs, n_fft=n_fft)
    lo = int(np.searchsorted(freqs, band[0]))
    hi = int(np.searchsorted(freqs, band[1]))
    df = freqs[1] - freqs[0]
    f0 = np.full(S.shape[1], np.nan)
    for i in range(S.shape[1]):
        seg = S[lo:hi, i]
        if seg.size < 3:
            continue
        ref = seg.max()
        if ref <= 0:
            continue
        thr = ref * 10.0 ** (rel_db / 20.0)
        for j in range(1, len(seg) - 1):
            if seg[j] > seg[j - 1] and seg[j] >= seg[j + 1] and seg[j] > thr:
                a, b, c = seg[j - 1], seg[j], seg[j + 1]
                den = a - 2 * b + c
                off = 0.5 * (a - c) / den if abs(den) > 1e-12 else 0.0
                f0[i] = freqs[lo + j] + off * df
                break
    return f0, None, None


def est_hs(x, fs, band=BAND, rel_db=-25.0, n_fft=4096, need=2, cents=35.0):
    """**谐波筛**（第四把尺子，纯 DSP）：基频 = 最低的、且**有谐波支撑**的显著谱峰。

    为什么不用"最低显著谱峰"（实测在已知 p77 的 205-208s 上误报 p76）：
    任何比它低 10-20dB 的杂峰（其它乐器泄漏 / 颤音的边带）都会把它带偏一个半音。
    这里加一条硬条件：候选 f 必须在 2f/3f/4f 里至少拿到 `need` 个**同带内**的显著峰，
    否则往下一个峰走 —— 单个孤峰不再能当基频。
    """
    S = np.abs(librosa.stft(x.astype(np.float64), n_fft=n_fft, hop_length=HOP,
                            win_length=n_fft, window="hann", center=True))
    freqs = librosa.fft_frequencies(sr=fs, n_fft=n_fft)
    df = freqs[1] - freqs[0]
    lo = int(np.searchsorted(freqs, band[0]))
    hi = int(np.searchsorted(freqs, min(band[1] * 4, fs / 2 * 0.98)))
    f0 = np.full(S.shape[1], np.nan)
    for i in range(S.shape[1]):
        spec = S[:, i]
        seg = spec[lo:hi]
        if seg.size < 3:
            continue
        ref = seg.max()
        if ref <= 0:
            continue
        thr = ref * 10.0 ** (rel_db / 20.0)
        pk = [j for j in range(1, len(seg) - 1)
              if seg[j] > seg[j - 1] and seg[j] >= seg[j + 1] and seg[j] > thr]
        if not pk:
            continue
        for j in pk:                                   # 频率从低到高
            f = freqs[lo + j]
            if f > band[1]:
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
    return f0, None, None


def crepe_periodicity(x, fs, fmin=FMIN, fmax=2000.0):
    """只取 periodicity 统计量（crepe 的"这像不像一个有基频的音"自评）"""
    import torch
    import torchcrepe
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    t = torch.from_numpy(np.ascontiguousarray(x, dtype=np.float32))[None]
    with torch.no_grad():
        f0, pd = torchcrepe.predict(t, fs, hop_length=HOP, fmin=fmin, fmax=fmax,
                                    model="full", batch_size=2048, device=dev,
                                    return_periodicity=True, pad=True)
    return f0[0].cpu().numpy().astype(np.float64), pd[0].cpu().numpy().astype(np.float64)


def subharmonic_fix(f0, x, fs, drop_db=12.0):
    """八度守卫：若 f0 处有峰、且 f0/2 处**也有一个峰**（≤ f0 峰 - drop_db dB 以内），
    则真基频多半是 f0/2 → 下调一个八度。返回 (修正后 f0, 被修正帧数, 说明)"""
    n_fft = 4096
    S = np.abs(librosa.stft(x.astype(np.float64), n_fft=n_fft, hop_length=HOP,
                            win_length=n_fft, window="hann", center=True))
    freqs = librosa.fft_frequencies(sr=fs, n_fft=n_fft)
    out = f0.copy()
    fixed = 0
    for i in range(min(len(f0), S.shape[1])):
        f = f0[i]
        if not np.isfinite(f) or f <= 0:
            continue
        half = f / 2.0
        if half < BAND[0]:
            continue
        lv = _peak_db(S[:, i], freqs, f)
        lh = _peak_db(S[:, i], freqs, half)
        if lv is None or lh is None:
            continue
        if lh >= lv - drop_db:
            out[i] = half
            fixed += 1
    return out, fixed


def _peak_db(spec, freqs, f, cents=50.0):
    """f 附近 ±cents 内的最大谱值（dB，相对该帧带内峰）"""
    lo_f, hi_f = f * 2 ** (-cents / 1200.0), f * 2 ** (cents / 1200.0)
    lo = int(np.searchsorted(freqs, lo_f))
    hi = int(np.searchsorted(freqs, hi_f)) + 1
    if hi <= lo or lo >= len(spec):
        return None
    band = spec[max(0, int(np.searchsorted(freqs, BAND[0]))):int(np.searchsorted(freqs, BAND[1]))]
    ref = band.max() if band.size else spec.max()
    if ref <= 0:
        return None
    return 20.0 * np.log10(max(spec[lo:hi].max(), 1e-12) / ref)


# ---------------------------------------------------------------- 统计口径
def hist_of(f0, voiced_floor=0.0, prob=None, prob_thr=None, lo=60, hi=110):
    """把逐帧 f0 折成 MIDI 直方图（按帧计）"""
    m = f2midi(f0)
    ok = np.isfinite(m)
    if prob is not None and prob_thr is not None:
        ok &= (prob >= prob_thr)
    elif prob_thr is not None:
        ok &= (prob >= prob_thr)
    m = np.round(m[ok]).astype(int)
    m = m[(m >= lo) & (m <= hi)]
    vals, cnt = np.unique(m, return_counts=True)
    return dict(zip([int(v) for v in vals], [int(c) for c in cnt])), int(ok.sum())


def sequence(f0, prob=None, prob_thr=None, step=8):
    """每 step 帧（默认 80ms）取一个中位音高 → 音高序列（对应台账的「谱峰序列」）"""
    m = f2midi(f0)
    if prob is not None and prob_thr is not None:
        m = np.where(prob >= prob_thr, m, np.nan)
    seq = []
    for i in range(0, len(m) - step + 1, step):
        w = m[i:i + step]
        w = w[np.isfinite(w)]
        if w.size >= max(2, step // 4):
            seq.append(int(round(float(np.median(w)))))
        else:
            seq.append(None)
    return seq


def frac_energy_at(x, fs, pitch, tol_cents=60.0, band=BAND, n_fft=8192):
    """该窗内 ±tol_cents 内的谱能量占带内总能量的比例（幅度平方和）"""
    S = np.abs(librosa.stft(x.astype(np.float64), n_fft=n_fft, hop_length=HOP,
                            win_length=n_fft, window="hann", center=True))
    P = (S ** 2).sum(axis=1)
    freqs = librosa.fft_frequencies(sr=fs, n_fft=n_fft)
    inband = (freqs >= band[0]) & (freqs <= band[1])
    tot = P[inband].sum()
    f = float(midi2f(pitch))
    lo = f * 2 ** (-tol_cents / 1200.0)
    hi = f * 2 ** (tol_cents / 1200.0)
    sel = (freqs >= lo) & (freqs <= hi)
    return float(P[sel].sum() / max(tot, 1e-20)), float(P[sel].sum()), float(tot)


def peak_table(x, fs, n=8, n_fft=8192, band=BAND):
    """窗内平均幅度谱的峰表（频率/音名/MIDI/相对dB）——直接回答"698 处有没有峰" """
    S = np.abs(librosa.stft(x.astype(np.float64), n_fft=n_fft, hop_length=HOP,
                            win_length=n_fft, window="hann", center=True)).mean(axis=1)
    freqs = librosa.fft_frequencies(sr=fs, n_fft=n_fft)
    lo = int(np.searchsorted(freqs, band[0]))
    hi = int(np.searchsorted(freqs, band[1]))
    seg = S[lo:hi]
    ref = seg.max()
    out = []
    for j in range(1, len(seg) - 1):
        if seg[j] > seg[j - 1] and seg[j] >= seg[j + 1]:
            a, b, c = seg[j - 1], seg[j], seg[j + 1]
            den = a - 2 * b + c
            off = 0.5 * (a - c) / den if abs(den) > 1e-12 else 0.0
            fq = freqs[lo + j] + off * (freqs[1] - freqs[0])
            out.append((float(seg[j]), float(fq)))
    out.sort(reverse=True)
    res = []
    for mag, fq in out[:n]:
        res.append({"hz": round(fq, 1), "midi": round(float(f2midi(fq)), 2),
                    "rel_db": round(20 * np.log10(max(mag, 1e-12) / ref), 1)})
    return res


# ---------------------------------------------------------------- 合成自检
def synth_seq(pitches, sr=SR, dur=0.166, harmonics=(1.0, 0.8, 0.6, 0.4, 0.3, 0.2),
              gap=0.03, seed=0):
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    env = np.minimum(1.0, t / 0.005) * np.exp(-t / (dur * 0.9))
    out = []
    for p in pitches:
        f = float(midi2f(p))
        s = np.zeros(n)
        for k, a in enumerate(harmonics, start=1):
            if f * k > sr / 2 * 0.95:
                break
            s += a * np.sin(2 * np.pi * f * k * t + rng.uniform(0, 2 * np.pi))
        out.append(s * env)
        out.append(np.zeros(int(gap * sr)))
    return np.concatenate(out).astype(np.float32)


def score_truth(f0, prob, thr, truth_pitches, step=1):
    """逐帧对齐真值序列（合成信号：每个音 dur+gap 对应固定帧数）"""
    m = f2midi(f0)
    ok = np.isfinite(m)
    if prob is not None and thr is not None:
        ok &= (prob >= thr)
    return m, ok


def _frac(hist, keys):
    tot = sum(hist.values())
    if not tot:
        return 0.0
    return sum(c for k, c in hist.items() if k in keys) / tot


RULERS = (("crepe", est_crepe), ("pyin", est_pyin), ("lsp", est_lsp), ("hs", est_hs))


def run_synth(band_limit=True):
    """自检的判据是**对齐无关**的（逐帧序列对齐会被"每音帧数估计不准"毁掉，实测第一版就毁在这）：
       · in_truth  = 有声帧落在真值音高**集合**里的比例
       · 八度判读 = 落在低八度组 / 高八度组的比例（这才是 p77 vs p89 那个问题）"""
    LOW = {77, 80, 82, 84}          # 低八度组（真值组的低八度部分）
    UP = {89, 92, 94, 96}           # 高八度组（= 低八度 + 12）
    cases = {
        "S1_亮合成器_低八度(真值 77/80/82/84/96/97)": dict(
            pitches=[77, 80, 82, 84, 96, 97] * 2,
            harmonics=(1.0, 0.8, 0.6, 0.4, 0.3, 0.2), keys=(LOW | {96, 97}, UP)),
        "S2_二次谐波更强_低八度(最难)": dict(
            pitches=[77, 80, 82, 84, 96, 97] * 2,
            harmonics=(1.0, 1.3, 0.9, 0.7, 0.5, 0.3, 0.2), keys=(LOW | {96, 97}, UP)),
        "S3_高八度(真值 89/92/94/96)": dict(
            pitches=[89, 92, 94, 96] * 3,
            harmonics=(1.0, 0.8, 0.6, 0.4, 0.3, 0.2), keys=(UP, LOW)),
        "S4_纯正弦高八度(真值 89/92)": dict(
            pitches=[89, 92] * 4, harmonics=(1.0,), keys=({89, 92}, LOW)),
    }
    res = {}
    for name, kw in cases.items():
        keys = kw.pop("keys")
        y = synth_seq(**kw)
        x = bandpass(y, SR) if band_limit else y
        item = {"truth": kw["pitches"], "keys": [sorted(keys[0]), sorted(keys[1])]}
        print("\n== 自检 %s" % name)
        for mname, fn in RULERS:
            f0, prob, thr = fn(x, SR)
            h, nv = hist_of(f0, prob=prob, prob_thr=thr)
            hi = _frac(h, keys[0])
            lo = _frac(h, keys[1])
            vals = sorted(h.items(), key=lambda kv: -kv[1])
            item[mname] = {"voiced_frames": nv, "in_truth": round(hi, 3),
                           "in_wrong_octave": round(lo, 3), "hist": h,
                           "top": vals[:8]}
            print("   %-6s 有声帧 %4d  **落在真值集合 %.1f%%**  落在错八度组 %.1f%%  "
                  "前6 %s" % (mname, nv, 100 * hi, 100 * lo, vals[:6]))
        res[name] = item
    return res


# ---------------------------------------------------------------- GM 独奏自检素材
def make_solo_midi():
    """从我们的 MIDI 里抽出 210-222.5s 的**方波主奏（program 80）**→ 原样 / 降八度 两份。

    ⚠ 第一版按"该窗音符数最多"选轨，选到了 Acoustic Piano（63 音）—— 那不是主奏。
    主奏按 program 认（本曲 = 80 方波主奏）。"""
    import pretty_midi
    os.makedirs(OUT, exist_ok=True)
    pm = pretty_midi.PrettyMIDI(MINE_MID)
    print("MIDI 全部轨：", [(i.program, i.name,
                          sum(1 for nt in i.notes if 210.0 <= nt.start <= 222.5))
                         for i in pm.instruments])
    lead = [i for i in pm.instruments if i.program == 80 and not i.is_drum]
    if not lead:
        raise SystemExit("MIDI 里找不到 program 80 的主奏轨")
    ins = max(lead, key=lambda i: sum(1 for nt in i.notes if 210.0 <= nt.start <= 222.5))
    notes = [nt for nt in ins.notes if 210.0 <= nt.start <= 222.5]
    t0 = min(nt.start for nt in notes) - 0.2
    tempo = float(pm.get_tempo_changes()[1][0])
    outs = {}
    for tag, shift in (("solo_asis", 0), ("solo_down12", -12)):
        p2 = pretty_midi.PrettyMIDI(initial_tempo=tempo)
        ni = pretty_midi.Instrument(program=ins.program, name="lead_%s" % tag)
        for nt in notes:
            ni.notes.append(pretty_midi.Note(velocity=int(nt.velocity),
                                             pitch=int(nt.pitch) + shift,
                                             start=float(nt.start) - t0,
                                             end=float(nt.end) - t0))
        p2.instruments.append(ni)
        mid = os.path.join(OUT, "%s.mid" % tag)
        p2.write(mid)
        outs[tag] = {"mid": mid, "program": int(ins.program), "t0": float(t0),
                     "truth": [int(nt.pitch) + shift for nt in notes],
                     "n_notes": len(notes), "tempo": tempo}
        print("  写好 %s（%d 音，program %d，起点偏移 %.2fs）"
              % (mid, len(notes), ins.program, t0))
    with open(os.path.join(OUT, "solo_truth.json"), "w", encoding="utf-8") as f:
        json.dump(outs, f, ensure_ascii=False, indent=1)
    return outs


def render_solo():
    outs = make_solo_midi()
    for tag, meta in outs.items():
        base = os.path.join(OUT, tag)
        if os.path.exists(base + ".wav"):
            print("已有 %s.wav，跳过" % tag)
            continue
        cmd = [PY, os.path.join(REPO, "scripts", "render_midi.py"), meta["mid"], base,
               "--rms", "-16.9"]
        print("渲染:", " ".join(cmd))
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        print("  exit=%d" % r.returncode, (r.stdout or "")[-400:], (r.stderr or "")[-200:])
    return outs


def check_solo():
    """自检：GM 独奏（真值 = 我们的 MIDI）→ 三把尺子各报什么"""
    with open(os.path.join(OUT, "solo_truth.json"), encoding="utf-8") as f:
        meta = json.load(f)
    res = {}
    for tag, m in meta.items():
        wav = os.path.join(OUT, tag + ".wav")
        if not os.path.exists(wav):
            print("缺 %s，先跑 render" % wav)
            continue
        x, fs = load_mono(wav)
        xb = bandpass(x, fs)
        truth = sorted(set(m["truth"]))
        item = {"truth_set": truth, "program": m["program"], "n_notes": m["n_notes"]}
        print("\n== GM 独奏自检 %s（真值音高集合 %s，%d 音）" % (tag, truth, m["n_notes"]))
        for mname, fn in RULERS:
            f0, prob, thr = fn(xb, fs)
            h, nv = hist_of(f0, prob=prob, prob_thr=thr)
            top = sorted(h.items(), key=lambda kv: -kv[1])[:8]
            item[mname] = {"voiced": nv, "top": top, "hist": h}
            hit = sum(c for k, c in h.items() if k in truth) / max(1, sum(h.values()))
            print("   %-6s 有声帧 %4d  落在真值集合内 %.1f%%  前8 %s"
                  % (mname, nv, 100 * hit, top))
            item[mname]["in_truth_frac"] = round(hit, 3)
            if mname in ("crepe", "pyin"):
                f0f, nfix = subharmonic_fix(f0, xb, fs)
                h2, _ = hist_of(f0f)
                top2 = sorted(h2.items(), key=lambda kv: -kv[1])[:8]
                hit2 = sum(c for k, c in h2.items() if k in truth) / max(1, sum(h2.values()))
                item[mname + "_subfix"] = {"fixed_frames": nfix, "top": top2,
                                           "in_truth_frac": round(hit2, 3)}
                print("   %-6s+八度守卫 修正 %d 帧  落在真值集合内 %.1f%%  前8 %s"
                      % (mname, nfix, 100 * hit2, top2))
        res[tag] = item
    with open(os.path.join(OUT, "selftest_solo.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    return res


# ---------------------------------------------------------------- 真音频测量
def measure_one(tag, path, t0, t1, band_limit=True):
    x, fs = load_mono(path, t0, t1)
    xb = bandpass(x, fs) if band_limit else x
    rms = 20 * np.log10(max(1e-9, float(np.sqrt((xb ** 2).mean()))))
    item = {"path": path, "t0": t0, "t1": t1, "rms_db": round(rms, 1),
            "peaks": peak_table(xb, fs), "methods": {}}
    print("\n== %s  [%.1f-%.1f s]  band RMS %.1f dB" % (tag, t0, t1, rms))
    print("   带内平均谱峰（前 8）: " + "  ".join(
        "%s(%.0fHz,%+.1fdB)" % (p["midi"], p["hz"], p["rel_db"]) for p in item["peaks"]))
    # crepe 的"这像不像有基频的音"自评（periodicity）—— 不看音高，只看周期性
    f0r, pdr = crepe_periodicity(xb, fs)
    item["crepe_periodicity"] = {"median": round(float(np.median(pdr)), 3),
                                 "p90": round(float(np.percentile(pdr, 90)), 3),
                                 "frac_ge_0.21": round(float(np.mean(pdr >= 0.21)), 3)}
    print("   crepe 周期性: 中位 %.3f  p90 %.3f  ≥0.21 的帧占比 %.0f%%"
          % (item["crepe_periodicity"]["median"], item["crepe_periodicity"]["p90"],
             100 * item["crepe_periodicity"]["frac_ge_0.21"]))
    for mname, fn in RULERS:
        f0, prob, thr = fn(xb, fs)
        h, nv = hist_of(f0, prob=prob, prob_thr=thr)
        top = sorted(h.items(), key=lambda kv: -kv[1])[:10]
        seq = sequence(f0, prob=prob, prob_thr=thr)
        item["methods"][mname] = {"voiced": nv, "top": top, "hist": h,
                                  "seq": [s for s in seq]}
        print("   %-6s 有声帧 %4d  top10 %s" % (mname, nv, top))
        print("          序列(80ms/格) %s" % (seq[:40],))
        if mname in ("crepe", "pyin"):
            f0f, nfix = subharmonic_fix(f0, xb, fs)
            h2, _ = hist_of(f0f)
            top2 = sorted(h2.items(), key=lambda kv: -kv[1])[:10]
            item["methods"][mname + "_subfix"] = {"fixed_frames": nfix, "top": top2,
                                                  "hist": h2}
            print("   %-6s+八度守卫 修正 %d 帧  top10 %s" % (mname, nfix, top2))
    # 候选音高的带内能量占比（直接、无模型）
    LOW, UP = {77, 80, 82, 84}, {89, 92, 94, 96}      # 两组互为八度
    item["octave_readout"] = {}
    print("   ★ 八度判读（低八度组 77/80/82/84 vs 高八度组 89/92/94/96）:")
    for mname, it in item["methods"].items():
        h = it["hist"]
        fl, fu = _frac(h, LOW), _frac(h, UP)
        item["octave_readout"][mname] = {"low": round(fl, 3), "up": round(fu, 3)}
        print("      %-12s 低八度 %.0f%%  高八度 %.0f%%  （%.0f%% 落在两组之外）"
              % (mname, 100 * fl, 100 * fu, 100 * max(0.0, 1 - fl - fu)))
    item["energy_share"] = {}
    print("   逐音高带内能量占比（±60音分）:")
    for p in (70, 72, 74, 75, 76, 77, 79, 80, 82, 84, 86, 88, 89, 91, 92, 94, 96, 97):
        frac, e, tot = frac_energy_at(xb, fs, p)
        item["energy_share"][p] = round(frac, 4)
        if frac > 0.01:
            print("      p%-3d %5.1fHz  %6.2f%%" % (p, midi2f(p), 100 * frac))
    return item


def run_measure():
    os.makedirs(OUT, exist_ok=True)
    res = {}
    jobs = [
        ("ref_other_213.0-216.5", os.path.join(STEMS, "other.wav"), 213.0, 216.5),
        ("ref_other_216.0-220.0", os.path.join(STEMS, "other.wav"), 216.0, 220.0),
        ("ref_other_205.0-208.0", os.path.join(STEMS, "other.wav"), 205.0, 208.0),
        ("ref_full_213.0-216.5", REF_FULL, 213.0, 216.5),
        ("mine_full_213.0-216.5", MINE_WAV, 213.0, 216.5),
        ("mine_solo_solo_asis", os.path.join(OUT, "solo_asis.wav"), 0.5, 4.0),
        ("mine_solo_solo_down12", os.path.join(OUT, "solo_down12.wav"), 0.5, 4.0),
    ]
    for tag, path, t0, t1 in jobs:
        if not os.path.exists(path):
            print("跳过（缺文件）%s" % path)
            continue
        res[tag] = measure_one(tag, path, t0, t1)
    with open(os.path.join(OUT, "measure.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("\n写出 %s" % os.path.join(OUT, "measure.json"))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["synth", "render", "checksolo", "measure", "all"])
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if a.mode in ("synth", "all"):
        r = run_synth()
        with open(os.path.join(OUT, "selftest_synth.json"), "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
    if a.mode in ("render", "all"):
        render_solo()
    if a.mode in ("checksolo", "all"):
        check_solo()
    if a.mode in ("measure", "all"):
        run_measure()
    return 0


if __name__ == "__main__":
    sys.exit(main())
