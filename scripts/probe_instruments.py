#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_instruments.py —— **扒带第 ⓿ 步：逐段乐器编制表**（用户 2026-09-25 要求固化；v4 同日升级）。

## 为什么必须有这一步

直接照抄"分轨 → 引擎轨"的映射会**把乐器认错**：`h6_piano` 分轨上 110–120s 的高音
其实**是小提琴**，于是成品用**钢琴音色**弹了小提琴的旋律 —— 用户一听就指出
"原曲 1 分 50 后面就是小提琴"。没有这一步，后面七步做得再准，音色也是错的。

## 四层证据（**互相独立**；前两层是模型产物，后两层是物理事实）

| 层 | 是什么 | 边界（实测） |
|---|---|---|
| ① YMT3 乐器通道 | 一次转录已分好 13 个乐器通道，逐段统计音数/音区 | **只是初筛**：会把弦乐错标成 `Acoustic Piano`（S16–S18 各 128/128/154 音） |
| ② 本地 Qwen2-Audio（`--qwen`） | 开放式问"这段有哪些乐器"（≈1–3s/段，离线跑） | **会漏**：69–104s 的人声吟唱它没报出来；⚠ 不能问"有没有 X"（它一律顺从） |
| ③a 分轨转录 | 每条分轨各自的 `*.mid` 音数（**来源不丢**，v1 曾把它们混在一起重复计数） | 也会被上游分离错误带跑（钢琴轨在弦乐段照样转录出 128 个"钢琴"音） |
| ③b **分轨音频能量** | 逐段 RMS 相对段内最响分轨 | **物理事实**，不依赖任何标签；门限由分布**双峰**标定（在场 −0~−18dB / 缺席 −25~−65dB） |
| ③c **物理判据** | 对主奏分轨实测：起音时间(10→90%) · 起音后 300ms 衰减 · 颤动 3–9Hz 带内 RMS | 见下"已验证/未验证" |
| ④ **用户** | 他给的原曲事实 | **最高优先**；与上面冲突时以他为准（69–104s 人声就是这么定的） |

## 已验证 / 未验证（**别越界使用**）

**已验证**（`siren_end2`：正控 2 条=用户给的人声吟唱/弦乐 · 负控 1 条=钢琴轨）：
· 逐段"哪条分轨在响" → 乐器族：用户真值 **8/8**（S12–S15→`vocals` · S16–S19→`other`）。
· 演奏形态：钢琴轨起音 **11–17ms** vs 弦乐/人声轨 **73–194ms**；钢琴轨 300ms 内掉 5–12dB。
· 颤音（3–9Hz 带内）：负控（钢琴轨）**2.1¢** vs 正控 **5.2–6.8¢** @4.4–4.7Hz。
· 顺带能拆穿**分轨名说谎**：`h6_guitar` 在 S16–S19 平坦度 0.029 / 2–6kHz 占 **50.7%**
  = 镲片泄漏，不是吉他。

**未验证**（**不许拿本表当结论**）：`other` 轨里**弦乐 vs 合成器 pad**、
`vocals` 轨里**人声 vs 主奏合成器** —— 没有标注样本可标定。
**逐段**物理量在密集段样本会不足（颤音常只剩 1–4 个可量段）→ 逐段读数只当参考，
可靠的是**多段聚合**；表里 `样本` 列 = `独奏起音数/可量颤音段数`，样本 <5 会打 `⚠样本不足`。

## 颤音尺子为什么要自检（**三次全被自检拦下**，不是靠听感发现的）

① pyin 逐帧抖动被当颤音（钢琴轨读出 226¢）→ 改 STFT 谱峰追踪；
② `n_fft=8192`=186ms ≈ 5.5Hz 颤音的一个整周期，窗内平均把调制抹平（真值 40¢ 读成 18.4¢）；
③ Hilbert 在包络趋零处相位跳变（稳定音读出 40¢、钢琴式 364¢）→ 加幅度门控 + 边缘裁剪。
④ 极窄带用 `butter` 的 ba 形式病态出 NaN → 改 **SOS**；滤波带开 ±35% 会把和弦邻音放进来
   → 拍频当颤音（钢琴轨 64.9¢ 全场最高），收窄到 ±10% 并**只看带内 RMS**。
所以：**合成信号自检不过就不许量真音频**（默认强制，`--no-selftest` 只用于调试）。
**自检能抓 / 抓不到**（`selftest.py::inst_probe_vibrato_ruler` + `mutation_check` 第 67 组）：
· 抓得到：解析信号退回实带通（相位只剩 0/π）· 关掉幅度门控与边缘裁剪（伪调制回来）。
· **抓不到**：带内 RMS 退回总 RMS、滤波带从 ±10% 开宽到 ±35%（邻音 → 拍频）——
  这两类只在**真实复音音频**上现形，而"哪里算有颤音"没有标注样本，所以不宣称已验。

## 用法

  python scripts\probe_instruments.py --ymt3-dir <转录目录> --stems-dir <分轨wav目录> \
         --song <曲名> [--song-json <任意 song.json>] [--json out.json]
  #   --ymt3-dir ：含 mix.mid（全混音转录）与各分轨 *.mid
  #   --stems-dir：Demucs 分轨 wav（h4_* / h6_*）；不给则只出①②③a 三层
  #   --selftest ：只跑颤音尺子自检（合成已知信号）后退出（**纯 numpy，任何 venv 都能跑**）
  # ⚠ 用到 ③b/③c（`--stems-dir`）时会**自动换成 `.venv-ml` 的解释器**（`scripts/pyenv.py`）——
  #   按上面这条命令直接跑即可，主 venv 没有 librosa 也不会崩在半路。
"""
import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()          # noqa: E402
import midi_file                             # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# YMT3 乐器通道 → 写谱时的 GM 音色候选（扒带编配用；顺序 = 优先）
GM = {"Acoustic Piano": [0], "Chromatic Percussion": [8, 9, 10, 11, 12, 13, 14],
      "Guitar (clean)": [24, 25], "Bass": [32, 33],
      "Strings": [48, 49, 40, 41, 42, 43, 44, 45], "Brass": [61, 62, 60],
      "Reed": [68, 71, 70], "Synth Lead": [80, 81], "Synth Pad": [88, 89, 90],
      "Organ": [16, 17], "Singing Voice": [53, 54, 52]}
# YMT3 通道 → 它**应该**落在哪条 Demucs 分轨（冲突检查用）
CH2STEM = {"Acoustic Piano": "piano", "Guitar (clean)": "guitar", "Bass": "bass",
           "Drums": "drums", "Singing Voice": "vocals", "Strings": "other",
           "Brass": "other", "Reed": "other", "Organ": "other", "Synth Pad": "other",
           "Synth Lead": "other", "Chromatic Percussion": "other"}
# 分轨 → 乐器名（Demucs 口径）；⚠ `vocals` 不保证是人声、`other` 是弦乐/合成器/铜管的混合堆
STEM_INST = {"piano": "钢琴", "guitar": "吉他", "other": "弦乐/合成器/其它",
             "vocals": "人声或主奏合成器", "bass": "贝斯", "drums": "鼓组"}
MELODIC = ("piano", "guitar", "other", "vocals")     # 参与"主奏"评选
SKIP_CH = ("Track 1", "Drums")                       # 兜底轨（鼓单列）
# 颤音尺子的两道防线参数（**提成模块常量是为了让变异测试能注入坏法** —— 它们各自
# 对应一次真踩：没有门控/裁剪时，稳定音读出 40¢、钢琴式读出 364¢；见下方 `vibrato`）
VIB_GATE_DB = 10.0     # 幅度门控：只取包络峰 −N dB 以内的最长连续段
VIB_TRIM_FRAC = 0.15   # 两端各裁掉的比例（伪调制集中在两端）


# ──────────────────────────── 段落 ────────────────────────────
def sections_of(song, bpm, song_json=None):
    """→ [(名字, 起秒, 止秒)]；没有 song.json 就整曲当一段。"""
    p = song_json or (os.path.join(ROOT, "songs", song, "song.json") if song else None)
    if p and os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        bar = 4 * 60.0 / float(d.get("bpm") or bpm)
        out, b0 = [], 0
        for s in d["sections"]:
            out.append((s["name"], b0 * bar, (b0 + s["bars"]) * bar))
            b0 += s["bars"]
        return out
    return [("all", 0.0, 1e9)]


# ─────────────────── ① ② ③a：转录侧（纯 numpy） ───────────────────
def read_notes(ymt3_dir):
    """→ {(来源tag, 通道名): [(起秒, 音高)]}；**来源不丢**，且按每个文件自身 bpm 换算
    （⚠ 单位坑：`mix.mid` 的 bpm 常是 120，拿全曲 bpm 硬套会让小节号整体错位）。"""
    files = []
    mix = os.path.join(ymt3_dir, "mix.mid")
    if os.path.exists(mix):
        files.append((mix, "mix"))
    for f in sorted(os.listdir(ymt3_dir)):
        if f.endswith(".mid") and f != "mix.mid":
            files.append((os.path.join(ymt3_dir, f), f[:-4]))
    out = defaultdict(list)
    for path, tag in files:
        m = midi_file.import_midi(path)
        spb = 60.0 / float(m.get("bpm") or 120.0)
        for t in m.get("tracks", []):
            ch = str(t.get("name"))
            for n in t.get("notes", []):
                out[(tag, ch)].append((float(n[0]) * spb, int(n[2])))
    return out


def qwen_table(wav, nseg=24, ask=None, python_ml=None, script=None):
    """第②层：跑本地 Qwen2-Audio 逐段问（离线 + GPU 4bit）。失败返回 []（不阻断主流程）。"""
    python_ml = python_ml or os.path.join(ROOT, ".venv-ml", "Scripts", "python.exe")
    script = script or os.path.join(ROOT, "scripts", "ask_audio_critic.py")
    if not (os.path.exists(python_ml) and os.path.exists(script)):
        print("  (跳过 Qwen：缺 .venv-ml 或 ask_audio_critic.py)")
        return []
    ask = ask or ("这段音乐里你能听到哪些乐器？请逐一列出，并说明哪个乐器在演奏主旋律。"
                  "用中文回答，乐器名要具体（例如：钢琴、小提琴、弦乐组、合成器垫、贝斯、鼓、人声吟唱）。")
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONIOENCODING="utf-8")
    cmd = [python_ml, script, wav, "--segments", str(nseg), "--load-4bit",
           "--ask", ask, "--json", os.path.join(os.path.dirname(wav), "inst_qwen.json")]
    print("  跑 Qwen2-Audio（离线 4bit）…")
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = []
    for line in (r.stdout or "").splitlines():
        if "] " in line and "|" in line and "段" in line:
            try:
                head, ans = line.split("|", 1)
                t0 = float(head.split("[", 1)[1].split("秒", 1)[0].split("~")[0])
                out.append((t0, ans.strip()))
            except Exception:
                continue
    if not out:
        print("  (Qwen 无输出；rc=%s)" % r.returncode)
    return out


# ─────────────────── ③b：分轨音频能量（物理事实） ───────────────────
def load_stems(stems_dir):
    """→ {分轨名: (单声道 float32, sr)}；文件名形如 h6_piano.wav / h4_vocals.wav。"""
    import soundfile as sf
    out = {}
    for f in sorted(os.listdir(stems_dir)):
        if not f.lower().endswith(".wav"):
            continue
        x, sr = sf.read(os.path.join(stems_dir, f), dtype="float32", always_2d=True)
        out[f[:-4]] = (x.mean(axis=1), sr)
    return out


def seg_db(x, sr, t0, t1):
    a, b = int(max(0.0, t0) * sr), int(min(len(x) / sr, t1) * sr)
    if b <= a:
        return -120.0
    return 20 * np.log10(max(float(np.sqrt(np.mean(x[a:b] ** 2))), 1e-9))


# ─────────────────── ③c：物理判据（包络 + 颤音） ───────────────────
def env_desc(y, sr, t0, t1, solo_ms=60, win_ms=450):
    """起音时间 10→90% / 起音后 300ms 衰减 / 300ms 净变化 / 频谱。**只信独奏窗口**：
    起音前后 `solo_ms` 内有第二个起音的样本一律丢弃（复音下包络不可解释）。"""
    import librosa
    a, b = int(max(0, t0) * sr), int(min(len(y) / sr, t1) * sr)
    seg = y[a:b]
    if len(seg) < sr // 2:
        return None
    hop = 256
    rms = librosa.feature.rms(y=seg, frame_length=2048, hop_length=hop)[0]
    dbr = 20 * np.log10(np.maximum(rms, 1e-7))
    fps = sr / hop
    ons = librosa.onset.onset_detect(y=seg, sr=sr, hop_length=hop, units="frames",
                                     backtrack=False, pre_max=3, post_max=3, pre_avg=8,
                                     post_avg=8, delta=0.06, wait=3)
    out = {"n_onset": int(len(ons)), "n_solo": 0, "attack_ms": None,
           "decay_db_s": None, "sustain_db300": None}
    floor = float(np.percentile(dbr, 10))
    att, dec, sus = [], [], []
    for o in ons:
        if any(abs(o - o2) < int(solo_ms / 1000.0 * fps) for o2 in ons if o2 != o):
            continue
        if o + int(win_ms / 1000.0 * fps) >= len(dbr):
            continue
        out["n_solo"] += 1
        w = dbr[o:o + int(win_ms / 1000.0 * fps)]
        lo, hi = float(w.min()), float(w.max())
        if hi - floor < 6:                      # 这个起音没有实质能量
            continue
        i10 = int(np.argmax(w >= lo + 0.1 * (hi - lo)))
        i90 = int(np.argmax(w >= lo + 0.9 * (hi - lo)))
        att.append((i90 - i10) / fps * 1000.0)
        ipk = int(np.argmax(w))
        tail = w[ipk:ipk + int(0.30 * fps)]
        if len(tail) > 5:
            xs = np.arange(len(tail)) / fps
            dec.append(float(np.polyfit(xs, tail, 1)[0]))    # dB/s（负=衰减）
            sus.append(float(tail[-1] - tail[0]))
    if att:
        out["attack_ms"] = float(np.median(att))
    if dec:
        out["decay_db_s"] = float(np.median(dec))
        out["sustain_db300"] = float(np.median(sus))
    S = np.abs(librosa.stft(seg, n_fft=2048, hop_length=hop)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    tot = S.sum() + 1e-12
    out["centroid_hz"] = float(np.median(librosa.feature.spectral_centroid(S=S, sr=sr)[0]))
    out["hf26_ratio"] = float(S[(freqs >= 2000) & (freqs < 6000)].sum() / tot)
    out["flatness"] = float(np.median(librosa.feature.spectral_flatness(S=S)[0]))
    return out


def _stft_mag(y, n_fft=2048, hop=128, chunk=512):
    """纯 numpy 短时傅里叶幅度（分块做，避免 `(帧数 × n_fft)` 一次性展开吃光内存）。"""
    win = np.hanning(n_fft)
    nfr = 1 + max(0, (len(y) - n_fft)) // hop
    if nfr <= 0:
        return np.zeros((n_fft // 2 + 1, 0))
    out = np.empty((n_fft // 2 + 1, nfr))
    base = np.arange(n_fft)[None, :]
    for s in range(0, nfr, chunk):
        e = min(nfr, s + chunk)
        idx = base + hop * np.arange(s, e)[:, None]
        out[:, s:e] = np.abs(np.fft.rfft(y[idx] * win, axis=1)).T
    return out


def coarse_track(y, sr, fmin=100.0, fmax=1200.0, n_fft=2048, hop=128):
    """短窗 STFT 谱峰追踪（抛物线插值）→ (f0 逐帧 Hz, 显著度, 帧能量 dB)。
    ⚠ 窗长别用 8192：186ms ≈ 5.5Hz 颤音一个整周期，窗内平均会把调制抹平
    （真值 40¢ 会读成 18.4¢ —— 自检当场抓到过）。"""
    S = _stft_mag(y, n_fft, hop)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    idx = np.where((freqs >= fmin) & (freqs <= fmax))[0]
    mag = S[idx]
    if mag.size == 0:
        return np.full(0, np.nan), np.zeros(0), np.zeros(0)
    k = np.argmax(mag, axis=0)
    n = mag.shape[0]
    f0 = np.full(mag.shape[1], np.nan)
    prom = np.zeros(mag.shape[1])
    for j in range(mag.shape[1]):
        i = k[j]
        if i <= 0 or i >= n - 1:
            continue
        a, b, c = mag[i - 1, j], mag[i, j], mag[i + 1, j]
        den = a - 2 * b + c
        d = float(np.clip(0.5 * (a - c) / den, -0.5, 0.5)) if abs(den) > 1e-12 else 0.0
        f0[j] = freqs[idx[i]] + d * (freqs[1] - freqs[0])
        prom[j] = b / (np.median(mag[:, j]) + 1e-12)
    return f0, prom, 20 * np.log10(np.maximum(np.sqrt((mag ** 2).sum(axis=0)), 1e-9))


def runs_of(f0, prom, e, fps, min_run=0.30, prom_thr=6.0, efloor_db=-45.0):
    """连续同音段（f0 稳定、谱峰显著、能量够）—— 颤音只能在这样的段里量。"""
    ok = (~np.isnan(f0)) & (prom >= prom_thr) & (e >= efloor_db)
    runs, cur = [], []
    for j, good in enumerate(ok):
        if good and cur and abs(1200 * np.log2(f0[j] / f0[cur[-1]])) < 60:
            cur.append(j)
        else:
            if len(cur) >= int(min_run * fps):
                runs.append(cur)
            cur = [j] if good else []
    if len(cur) >= int(min_run * fps):
        runs.append(cur)
    return runs


def analytic_band(x, sr, f0c, band=0.10):
    """在 f0c 附近**窄带**取解析信号（FFT 掩码 + 正频×2）→ 复信号 z。
    ⚠ 带宽别开大：±35%（≈±5 半音）会把和弦邻音放进来 → 拍频被当成颤音
    （钢琴轨因此读出 64.9¢、全场最高）。
    ⚠ 纯 numpy（不用 scipy）：`butter` 的 ba 形式在 ±10% 窄带下**病态，filtfilt 直接出 NaN**
    （自检里三个已知颤音信号曾全部"未测到"），改 SOS 能修，但**FFT 掩码零相位、无此问题**，
    而且不引依赖 —— 本工具的 `--selftest` 因此能在主 venv（无 librosa/scipy）里跑。"""
    N = len(x)
    if N < 256:
        return None
    X = np.fft.rfft(x)
    fr = np.fft.rfftfreq(N, 1.0 / sr)
    lo, hi = f0c * (1 - band), f0c * (1 + band)
    if hi >= sr / 2 or lo <= 0:
        return None
    Xb = np.where((fr >= lo) & (fr <= hi), X, 0)
    full = np.zeros(N, dtype=complex)
    full[:len(Xb)] = Xb
    full[1:len(Xb) - 1] *= 2.0            # 解析信号：正频加倍，Nyquist 不加
    return np.fft.ifft(full)


def hilbert_mod(x, sr, f0c, band=0.10):
    """→ (音分调制轨迹, 包络 dB)。名字沿用（算法已是 FFT 解析信号，见 `analytic_band`）。"""
    z = analytic_band(x, sr, f0c, band)
    if z is None:
        return None, None
    ph = np.unwrap(np.angle(z))
    inst = np.diff(ph) / (2 * np.pi) * sr
    inst = np.concatenate([[inst[0]], inst])
    inst = np.clip(inst, f0c * 0.5, f0c * 2.0)
    return 1200 * np.log2(inst / f0c), 20 * np.log10(np.maximum(np.abs(z), 1e-9))


def mod_stats(sig, sr, band=(3.0, 9.0)):
    """去线性趋势后：总 RMS + **3–9Hz 带内 RMS** + 峰频 + 带占比。
    ⚠ 判"有没有颤音"看**带内** RMS —— 总 RMS 会被拍频/干扰抬高（实测踩过）。"""
    if sig is None or len(sig) < 32:
        return None
    t = np.arange(len(sig)) / sr
    sig = sig - np.polyval(np.polyfit(t, sig, 1), t)
    sig = sig - sig.mean()
    C = np.fft.rfft(sig)
    fr = np.fft.rfftfreq(len(sig), 1 / sr)
    bm = (fr >= band[0]) & (fr <= band[1])
    s_band = np.fft.irfft(np.where(bm, C, 0), n=len(sig))
    sp = np.abs(C)
    return {"rms": float(np.sqrt(np.mean(sig ** 2))),
            "rms_band": float(np.sqrt(np.mean(s_band ** 2))),
            "peak_hz": float(fr[bm][int(np.argmax(sp[bm]))]) if bm.any() else None,
            "band_ratio": float(np.sum(sp[bm] ** 2) / (np.sum(sp[1:] ** 2) + 1e-12))}


def vibrato(y, sr, min_keep_s=0.20):
    """→ dict：3–9Hz 带内调制深度（音分）与速率（Hz）。**含两道防线**：
    幅度门控（包络峰 −10dB 内）+ 边缘裁剪（两端各 15%）—— 实测伪调制全在两端。"""
    fps = sr / 128
    f0, prom, e = coarse_track(y, sr)
    runs = runs_of(f0, prom, e, fps)
    vb, vh, vr, n_run, run_s = [], [], [], 0, 0.0
    for r in runs:
        n_run += 1
        run_s += len(r) / fps
        f0c = float(np.median(f0[r]))
        seg = y[max(0, int(r[0] / fps * sr) - 64):min(len(y), int(r[-1] / fps * sr) + 64)]
        cents, env = hilbert_mod(seg, sr, f0c)
        if cents is None:
            continue
        keep = env >= (np.max(env) - VIB_GATE_DB)
        best, cur = (0, 0), None
        for i, k in enumerate(keep):
            if k and cur is None:
                cur = i
            elif not k and cur is not None:
                if i - cur > best[1] - best[0]:
                    best = (cur, i)
                cur = None
        if cur is not None and len(keep) - cur > best[1] - best[0]:
            best = (cur, len(keep))
        i0, i1 = best
        pad = int(min(VIB_TRIM_FRAC * sr, VIB_TRIM_FRAC * (i1 - i0)))
        c2 = cents[i0 + pad:i1 - pad]
        if len(c2) < int(min_keep_s * sr):
            continue
        s = mod_stats(c2, sr)
        if s:
            vb.append(s["rms_band"])
            vh.append(s["peak_hz"])
            vr.append(s["band_ratio"])
    md = lambda v: float(np.median(v)) if v else None
    return {"n_runs": n_run, "run_s": round(run_s, 1),
            "vib_cents_band": md(vb), "vib_hz": md(vh), "vib_band_ratio": md(vr)}


def _tone(vib_hz=0.0, vib_cents=0.0, f=220.0, decay=0.0, sr=44100, dur=2.0):
    t = np.arange(int(dur * sr)) / sr
    inst = f * 2 ** ((vib_cents / 1200) * np.sin(2 * np.pi * vib_hz * t)) if vib_hz \
        else np.full_like(t, f)
    ph = 2 * np.pi * np.cumsum(inst) / sr
    x = sum((1.0 / (h ** 1.3)) * np.sin(h * ph) for h in range(1, 9))
    env = np.exp(-decay * t)
    return (x * env / np.max(np.abs(x * env))).astype("float32")


def selftest_vibrato(verbose=True):
    """**硬门**：拿已知答案的合成信号量，不过就不许量真音频。
    预期 RMS = 幅度/√2；负控（稳定音/钢琴式）必须 <8¢ 或"测不到"。"""
    cases = [("5.5Hz/40¢", _tone(5.5, 40.0), 5.5, 40.0),
             ("6.5Hz/25¢", _tone(6.5, 25.0), 6.5, 25.0),
             ("4.5Hz/60¢", _tone(4.5, 60.0), 4.5, 60.0),
             ("稳定音", _tone(0.0, 0.0), None, 0.0),
             ("钢琴式(快衰减)", _tone(0.0, 0.0, decay=6.0), None, 0.0)]
    ok_all = True
    if verbose:
        print(f"{'颤音尺子自检':22s} {'读出Hz':>8s} {'读出¢':>9s} {'预期¢':>8s} {'判定':>6s}")
    for nm, x, ehz, ec in cases:
        r = vibrato(x, 44100)
        gh, gc = r["vib_hz"], r["vib_cents_band"]
        exp = ec / np.sqrt(2) if ec else 0.0
        if ehz is None:                       # 负控：<8¢ 或 None（没有持续音可量）都算合格
            ok = (gc is None) or (gc < 8.0)
        else:
            ok = (gh is not None and abs(gh - ehz) < 1.2 and abs((gc or 0) - exp) < 0.30 * exp)
        ok_all &= ok
        if verbose:
            print(f"{nm:22s} {('-' if gh is None else f'{gh:.2f}'):>8s} "
                  f"{('未测到' if gc is None else f'{gc:.1f}'):>9s} {exp:>8.1f} "
                  f"{'PASS' if ok else 'FAIL':>6s}")
    if verbose:
        print("自检 " + ("PASS（尺子可用）" if ok_all else "FAIL（**不许**拿它报结论）"))
    return ok_all


def form_of(att, dec):
    """演奏形态（门限来自实测分离：钢琴 11–17ms vs 弦乐/人声 73–194ms）。"""
    if att is None or dec is None:
        return "测不到"
    if att <= 25 and dec <= -2.5:
        return "击弦/弹拨型（快起音+衰减）"
    if att >= 45 and dec > -2.5:
        return "弓弦/人声型（软起音+持续）"
    return "不确定"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ymt3-dir", required=True, help="含 mix.mid 与各分轨 *.mid 的转录目录")
    ap.add_argument("--stems-dir", default=None, help="Demucs 分轨 wav 目录（第③b/③c 层）")
    ap.add_argument("--song", default=None, help="曲名（读 songs/<曲>/song.json 的段落）")
    ap.add_argument("--song-json", default=None, help="直接指定 song.json（交付目录用）")
    ap.add_argument("--bpm", type=float, default=145.96)
    ap.add_argument("--rel-floor", type=float, default=-22.0, help="在场门限（相对段内最响分轨 dB）")
    ap.add_argument("--min-ch-notes", type=int, default=8, help="YMT3 通道至少这么多音才算证据")
    ap.add_argument("--model", default="h6", help="以哪套分轨做能量/物理判定（h6=6轨 / h4=4轨）")
    ap.add_argument("--qwen", action="store_true")
    ap.add_argument("--wav", default=None, help="原曲音频（--qwen 用）")
    ap.add_argument("--nseg", type=int, default=24)
    ap.add_argument("--selftest", action="store_true", help="只跑颤音尺子自检后退出")
    ap.add_argument("--no-selftest", action="store_true", help="跳过自检门（**只用于调试**）")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    if a.selftest:
        # 尺子是**纯 numpy**（见 `analytic_band`）→ 主 venv 直接能跑，不必换解释器。
        return 0 if selftest_vibrato() else 2

    # ③b/③c 层要 librosa/soundfile/scipy（`.venv-ml`）→ 按文档用主 venv 跑也能自动换解释器
    if a.stems_dir:
        import pyenv
        pyenv.ensure('librosa', '.venv-ml',
                     '第③b/③c 层（分轨能量+物理判据）要 librosa/soundfile/scipy',)

    secs = sections_of(a.song, a.bpm, a.song_json)
    notes = read_notes(a.ymt3_dir)
    if not notes:
        raise SystemExit("没读到音符：%s 里找不到 mix.mid / *.mid" % a.ymt3_dir)
    mix_ch = {ch: v for (tag, ch), v in notes.items() if tag == "mix"}
    stem_ch = defaultdict(dict)
    for (tag, ch), v in notes.items():
        if tag.startswith(("h4_", "h6_")):
            stem_ch[tag][ch] = v

    audio = {}
    if a.stems_dir:
        if not os.path.isdir(a.stems_dir):
            raise SystemExit("--stems-dir 不存在：%s" % a.stems_dir)
        audio = load_stems(a.stems_dir)
        if not a.no_selftest and not selftest_vibrato():
            raise SystemExit("颤音尺子自检 FAIL → 拒绝输出物理量（调试可加 --no-selftest）")
    else:
        print("⚠ 没给 --stems-dir：只出 ①②③a 三层（没有能量与物理判据）\n")

    print("=" * 124)
    print(f"逐段乐器编制表 v4 · 能量门限 {a.rel_floor:g}dB · YMT3 通道门限 {a.min_ch_notes} 音"
          f" · 物理量实测于**该段主奏分轨**（{a.model}）")
    print("=" * 124)
    print(f"{'seg':6s}{'时间':>14s} | {'主奏':13s} | {'在场分轨':22s} | "
          f"{'起音ms':>7s}{'300msΔ':>8s}{'颤音¢内':>8s}{'样本':>8s} | {'演奏形态':20s} | 疑似乐器 / 标记")
    rows = []
    for (nm, t0, t1) in secs:
        row = {"name": nm, "t0": t0, "t1": t1, "flags": []}
        cand = sorted(((c, len([1 for (t, _p) in v if t0 <= t < t1])) for c, v in mix_ch.items()
                       if c not in SKIP_CH), key=lambda kv: -kv[1])
        cand = [(c, n) for c, n in cand if n >= a.min_ch_notes]
        row["ymt3_top"] = [list(c) for c in cand[:3]]
        lead = top4 = None
        if audio:
            e = {k: seg_db(x, s, t0, t1) for k, (x, s) in audio.items()}
            h6 = {k[3:]: v for k, v in e.items() if k.startswith(a.model + "_")}
            other_model = "h4" if a.model == "h6" else "h6"
            h4 = {k[3:]: v for k, v in e.items() if k.startswith(other_model + "_")}
            if h6:
                base = max(h6.values())
                rel = {k: v - base for k, v in h6.items()}
                mel = sorted([(s, rel[s]) for s in MELODIC if s in rel], key=lambda kv: -kv[1])
                lead, lead_rel = mel[0]
                lead_abs = h6[lead]
                row.update(rel=rel, lead=lead, lead_rel=lead_rel, lead_abs=lead_abs,
                           present=[s for s, r in sorted(rel.items(), key=lambda kv: -kv[1])
                                    if r >= a.rel_floor])
                if h4:
                    base4 = max(h4.values())
                    rel4 = {k: v - base4 for k, v in h4.items()}
                    top4 = sorted([(s, rel4[s]) for s in MELODIC if s in rel4],
                                  key=lambda kv: -kv[1])[0][0]
                    row["other_model_lead"] = top4
                    # ⚠ 4 轨模型**没有钢琴轨**（钢琴并进 other）→ 只有 vocals 归属两边都能独立回答
                    if (lead == "vocals") != (top4 == "vocals"):
                        row["flags"].append(f"⚠vocals归属分歧({other_model}={top4})")
                if cand and CH2STEM.get(cand[0][0]) in MELODIC:
                    # 量化判据（**报离群量、不报"有问题"**）：只在"YMT3 指的那条分轨
                    # **实测比主奏低 ≥6dB**"时才报，并把差值写出来。阈值 6dB 的来由：
                    # 二元 flag 版本在本曲报了 11/25 段（5dB 以内的分歧全被算成冲突）= 噪声。
                    c_stem = CH2STEM[cand[0][0]]
                    gap = rel.get(c_stem, -120.0) - rel[lead]
                    if c_stem != lead and gap <= -6.0:
                        row["flags"].append(f"⚠标签冲突(YMT3={cand[0][0]},该轨比主奏低{-gap:.0f}dB)")
                if lead_abs >= -45.0:
                    x, s = audio[f"{a.model}_{lead}"]
                    ed = env_desc(x, s, t0, t1)
                    vb = vibrato(x[int(t0 * s):int(t1 * s)], s)
                    row.update(ed or {}, **{"vib_cents_band": vb["vib_cents_band"],
                                            "vib_hz": vb["vib_hz"], "vib_runs": vb["n_runs"]})
                    if ed and (ed.get("n_solo") or 0) < 5:
                        row["flags"].append(f"⚠样本不足(独奏起音{ed.get('n_solo')})")
                    # 颤音样本数已单列在「样本」列（独奏起音/可量颤音段），不再重复打旗
                else:
                    row["flags"].append("主奏层过弱")
        fmt = lambda v, p=1: "-" if v is None else f"{v:.{p}f}"
        if lead:
            fm = form_of(row.get("attack_ms"), row.get("sustain_db300"))
            if row.get("vib_cents_band") is not None:
                fm += "·带颤音" if row["vib_cents_band"] >= 4.0 else "·颤音弱"
            row["form"] = fm
            lead_cell = "%s%+.0f" % (lead, row["lead_rel"])
            samp = "%s/%s" % (row.get("n_solo"), row.get("vib_runs"))
            print(f"{nm:6s}{t0:6.1f}-{t1:6.1f} | {lead_cell:13s} | "
                  f"{(' '.join(row['present']))[:22]:22s} | {fmt(row.get('attack_ms')):>7s}"
                  f"{fmt(row.get('sustain_db300')):>8s}{fmt(row.get('vib_cents_band')):>8s}"
                  f"{samp:>8s} | {fm:20s} | "
                  f"{STEM_INST.get(lead, lead)} {' '.join(row['flags'])}")
        else:
            cell = "  ".join(f"{c}:{n}" for c, n in cand[:4])
            print(f"{nm:6s}{t0:6.1f}-{t1:6.1f} | {'-':13s} | {'-':22s} | "
                  f"{'-':>7s}{'-':>8s}{'-':>8s}{'-':>8s} | {'(只出①②③a)':20s} | {cell}")
        rows.append(row)

    qw = []
    if a.qwen and a.wav and os.path.exists(a.wav):
        print("\n" + "=" * 124)
        print("第②层：本地 Qwen2-Audio 逐段描述（**只能开放式问，不能问'有没有 X'**）")
        print("=" * 124)
        qw = qwen_table(a.wav, a.nseg)
        for (t0, ans) in qw:
            print(f"  {t0:7.1f}s | {ans}")
        print("\n⚠ 它的可信度**一段一议**：本轮 104–130s 弦乐 ✅ 对上用户，69–104s 人声吟唱 ❌ 没报出来。")
    print()
    n_conf = sum(1 for r in rows if any("标签冲突" in f for f in r["flags"]))
    print(f"⚠ 标签冲突 {n_conf} 段 · **未成立**：other 轨里弦乐 vs 合成器 pad、"
          f"vocals 轨里人声 vs 主奏合成器（无标注样本，不许当结论）")
    print("⚠ 第④层是用户 —— 与上表冲突时以用户为准。")
    if a.json:
        json.dump({"tool": "probe_instruments", "version": 4, "song": a.song,
                   "bpm": a.bpm, "rel_floor": a.rel_floor, "sections": rows, "qwen": qw},
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("→", a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
