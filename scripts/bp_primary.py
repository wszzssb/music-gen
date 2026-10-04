#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""bp_primary.py —— **BP 为主的还原转录**：Basic Pitch 出音符 · 分轨出鼓 · 按音区/先验定归属。

## 何时用

扒带/还原时，**YMT3 在密集混音上会漏旋律线**，而 Basic Pitch（Spotify 开源 ONNX）是
**独立模型**（错误与 YMT3 互不相关）。同一首《どうぞめしあがれ》实测（判据 = 盖住**原曲
主导线**的比例，谱峰法 + 负控、八度敏感）：

| 来源 | 耗时 | 音符 | 音域 | 净覆盖 |
|---|---|---|---|---|
| YourMT3+ | 36.2 s | 2649 | — | **62.0%** |
| Basic Pitch | **4.2 s** | 1506 | 36–91 | **71.4%** |

⇒ 转录阶段快 **8.6×**、覆盖高 ~10 个点。**但 BP 是音高模型，不分乐器通道、也不分鼓**，
所以本工具补上那两件事：

## ⚠ 实测结论：**它不是"全面更好"的路径**（2026-10-04，用户听感判的）

同一首《どうぞめしあがれ》四个版本、同一引擎、同一渲染链：

| 版本 | 音符源 | 归属 | 鼓 | 起音/s | chroma | 净覆盖 | **听感** |
|---|---|---|---|---|---|---|---|
| V1 原路 | YMT3 | YMT3 通道 | YMT3 | 4.363 | 0.868 | 62.0% | **用户：最好** |
| V2 | BP | YMT3 先验 | YMT3 | 4.179 | 0.878 | 71.4% | 先说过"更好一点"，后否 |
| V2c | BP | 音区兜底 | 分频 | 4.477 | 0.887 | 71.3% | 用户："有点奇怪" |

**我的"净覆盖"代理指标说 BP 更好，用户的耳朵说不是** —— 按本项目自己的规矩
（"指标涨 ≠ 听感好"、`SKILL` §9c），**听感赢**。已定位的三处具体差距：

1. **归属塌成两条轨**：BP 路径下 `Hook 1088 / Piano 245 / Pad 1 / Glock 3`，
   而 V1 是 `Hook 906 / Piano 386 / Pad 9 / Glock 15` —— **原本分在 4 条轨上的内容
   被压进 2 条**（`48–67 → Hook` 这条音区兜底把 C3–B3 那 708 个音全给了吉他）。
   **这是归属问题，不是音符源问题**；要修得走 `probe_instruments.py` 的第 ⓿ 步证据，
   而不是音区兜底。
2. **鼓两版都不对**：原曲 999 个起音里"低频主导"的只有 **12** 个（这首没有可检测的
   底鼓瞬态），而两版分别塞了 638 / 795 个军鼓、每小节 14 / 17 个鼓点（该速度通常 8–16）。
   分频判据**在自检上是过的**（合成件 3/3），但对这首的真实鼓不适用 —— 自检过了不等于适用。
   ⚠⚠ **用户 2026-10-04 定案："如果你找不到怎么把 BP 的鼓做好，以后还是用 YMT3"**
   ⇒ 本工具 `--drum-source` **默认 `ymt3`**（有 `--prior-midi` 时就用它的鼓轨）；
   要 BP 的分频鼓必须**显式** `--drum-source bp`。同日后来的独立量法也印证了这条：
   该曲 `drums` 分轨 RMS **−34.2dB**（比全混音低 15.8dB）、几乎没有 <120Hz 能量
   ⇒ **原曲根本没有鼓组**，两版的鼓都是凭空的（`PITFALLS` **316**）。
3. **先验几乎不值钱**（带先验 71.4% vs 不带 70.6%），所以"省一次 YMT3"换不来音质。

⇒ **默认用途**：把它当**第二个独立视角**（`--prior-midi` 给 YMT3 时两者互补），
**不要**默认拿它替掉 `transcribe_ymt3.py` 那条链。要替，先解决上面第 1 条。

## 三步（每步都有独立判据，别跳）

① **音符** = `bp_transcribe.py` 跑全混音（跑在独立 `bp-venv`，见 `pyenv`）。
② **鼓** = **分轨起音分频**（**不依赖 YMT3**）：`drums` 分轨 → 军鼓/闭镲；
   **底鼓要从 `bass` 分轨的低通取** —— 实测 Demucs 把底鼓归到了 bass
   （`drums` 的 60–120Hz 只占 **0.1%**，`bass` 占 **45.5%**）。
   ⚠ 尺子**先拿合成鼓型自检**（底鼓/军鼓/闭镲各 4 击必须全判对），不过就退出。
③ **归属** = 音区兜底（≤47→Bass · 48–67→Hook · 68–79→Piano · ≥80→Glock）
   +（可选）**YMT3 通道先验**（同刻 ±80ms/同音高 ±1 半音那条 YMT3 音属于哪个通道）
   + **`bass` 分轨能量门**（低音区的音必须先证明 bass 分轨真的在响，否则会把底鼓当贝斯）。

## 用法

```bash
$py = <工具链>\\.venv\\Scripts\\python.exe
# ① 分轨（4 轨就够；6s 在部分曲子上会塌）
& $ml -m demucs -n htdemucs -o stems <音频>
# ② BP 为主转录 → 各引擎轨单轨 MIDI（+ 可选直接生成 song.json）
& $py scripts\\bp_primary.py <音频> --stems-dir stems/htdemucs/<名> \\
      --bpm auto [--prior-midi <YMT3.mid>] --out <输出目录> \\
      [--song <曲名>] [--render]
# 尺子自检（合成鼓型 + 合成 click），不碰真音频
& $py scripts\\bp_primary.py --selftest
```

## 边界（**别越界使用**）

- **净覆盖是"覆盖率"口径，不是逐音精度** —— 没有真值时它只能证明"盖住了多少"，
  不能证明"盖住的都对"。精度仍要用独立方法量（`transcribe_audit.py` / `truth_eval.py`）。
- **先验几乎不值钱**：实测带先验 71.4% vs 不带 70.6%（chroma 反而 0.878→0.887）——
  先验只是"锦上添花"，**不给它也能跑，且省一次 YMT3**。
- **归属里的音区兜底很粗**（Hook 会吞掉中音区大部分音），没经过乐器验证；
  要更准得走 `probe_instruments.py` 的第 ⓿ 步。
- 生成 `song.json` 后仍要过引擎（`transcribe_to_song.py`），**本工具不自己拼 MIDI**（PITFALLS 185）。
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

SR, HOP = 22050, 512
# 引擎轨 → GM 音色（BP 不给乐器，这里按"中音区=吉他 / 高音区=钢琴"的实测先验定）
DEFAULT_PROGS = {'Piano': [0, 2], 'Hook': [24, 1], 'Bass': [33, 6], 'Pad': [89, 4],
                 'Strings': [48, 5], 'Glock': [9, 7], 'Melody': [73, 0]}
# BP 轨名（转录音源）→ 引擎轨名
ENGINE_MAP = {'Drums': 'Drums', 'Bass': 'Bass', 'Guitar (clean)': 'Hook',
              'Guitar (distortion)': 'Hook', 'Acoustic Piano': 'Piano',
              'Electric Piano': 'Piano', 'Strings': 'Strings', 'Organ': 'Pad',
              'Reed': 'Pad', 'Brass': 'Pad', 'Pipe': 'Glock', 'Synth Pad': 'Pad',
              'Synth Lead': 'Melody', 'Chromatic Percussion': 'Glock',
              'Singing Voice': 'Melody', 'Singing Voice (chorus)': 'Melody'}
KICK, SNARE, HAT = 36, 38, 42


# ─────────────────────────── ① 鼓：分频判据（带自检） ───────────────────────────
def classify_drums(sig, sr=SR, hop=HOP):
    """起音 + 四频带能量比 → GM 鼓键。返回 `[(秒, 键)]`。

    判据（每条都能拿合成件验，见 `drum_selftest`）：
      · 30–120Hz 占比 > 0.45  → 底鼓(36)
      · 6–12kHz 占比 > 0.45 且强于 2.5–5kHz → 闭镲(42)
      · 其余 → 军鼓(38)
    """
    import librosa
    oe = librosa.onset.onset_strength(y=sig, sr=sr, hop_length=hop)
    idx = librosa.onset.onset_detect(onset_envelope=oe, sr=sr, hop_length=hop,
                                     backtrack=True, units='frames')
    S = np.abs(librosa.stft(sig, n_fft=2048, hop_length=hop))
    f = librosa.fft_frequencies(sr=sr, n_fft=2048)
    out = []
    for i in idx:
        a, b = max(0, i - 2), min(S.shape[1], i + 12)      # 起音后 ~140ms
        seg = (S[:, a:b] ** 2).sum(axis=1)
        lo = seg[(f >= 30) & (f < 120)].sum()
        hi5 = seg[(f >= 2500) & (f < 5000)].sum()
        hi10 = seg[(f >= 6000) & (f < 12000)].sum()
        tot = max(lo + hi5 + hi10, 1e-12)
        if lo / tot > 0.45:
            key = KICK
        elif hi10 / tot > 0.45 and hi10 > hi5:
            key = HAT
        else:
            key = SNARE
        out.append((a * hop / sr, key))
    return out


def drum_selftest(verbose=True):
    """**合成鼓型自检** —— 不过就不许量真音频。

    ⚠ 夹具要**像真鼓**：第一版拿"白噪声"当军鼓 —— 它频谱是平的，被**正确**判成闭镲，
    于是自检 FAIL。真军鼓 = 200Hz 鼓皮 + **限带**噪声（10k 以上滚降），闭镲才是纯高频噪声。
    夹具不真实时，"判据坏了"和"夹具不满足前提"长得一模一样（本项目反复踩的一类）。
    """
    import scipy.signal as ss
    import librosa
    rng = np.random.default_rng(7)
    n = int(SR * 8.0)
    tt = np.arange(n) / SR
    noise = rng.normal(0, 1, n)
    sn = ss.butter(4, 8000 / (SR / 2), btype='low', output='sos')
    hh = ss.butter(4, 6000 / (SR / 2), btype='high', output='sos')
    ok = True
    for tag, want, sig in (
            ('底鼓', KICK, np.sin(2 * np.pi * 60 * tt) * np.exp(-tt * 30)),
            ('军鼓', SNARE, (0.7 * np.sin(2 * np.pi * 200 * tt + 0.4 * np.sin(2 * np.pi * 330 * tt))
                             + 0.9 * ss.sosfilt(sn, noise)) * np.exp(-tt * 25)),
            ('闭镲', HAT, ss.sosfilt(hh, noise) * np.exp(-tt * 60))):
        y = np.zeros(n, dtype='float32')
        for k in range(4):
            i = int((1 + k * 1.5) * SR)
            win = int(0.08 * SR)
            y[i:i + win] += (sig[:win] * np.hanning(win)).astype('float32')
        got = classify_drums(y)
        hit = sum(1 for _t, k in got if k == want)
        good = hit >= 3
        if verbose:
            print('  鼓尺子自检 %s → %d/%d 判对（要 ≥3）%s' % (tag, hit, len(got),
                                                             'OK' if good else ' FAIL'))
        ok = ok and good
    return ok


def drums_from_stems(stems_dir):
    """从 Demucs 分轨出鼓：`drums` 取军鼓/闭镲，`bass` **低通**取底鼓（见模块 docstring ②）。"""
    import librosa
    import scipy.signal as ss
    import soundfile as sf

    def rd(name):
        y, s = sf.read(os.path.join(stems_dir, name), dtype='float32', always_2d=True)
        y = y.mean(axis=1)
        if s != SR:
            y = librosa.resample(y, orig_sr=s, target_sr=SR)
        return y

    yb, yd = rd('bass.wav'), rd('drums.wav')
    kick_sig = ss.sosfilt(ss.butter(4, 130 / (SR / 2), btype='low', output='sos'), yb)
    got = [(t, k) for (t, k) in classify_drums(yd) if k in (SNARE, HAT)]
    got += [(t, KICK) for (t, k) in classify_drums(kick_sig) if k == KICK]
    got.sort()
    return got


# ─────────────────────────── ② 速度：自检过的拟合尺子 ───────────────────────────
def _conc(ts, p):
    if len(ts) < 10:
        return 0.0
    return float(abs(np.mean(np.exp(2j * np.pi * ((np.asarray(ts) / p) % 1.0)))))


def fit_tempo(onsets, p0, span=0.10, tol=0.12, iters=6):
    """相位聚集度粗搜 + 紧窗口最小二乘精修。返回 `(bpm, 命中率, 拍数)` 或 None。

    为什么不用 `metrics.detect_bpm`：它在实测里错过两次（《ほっとティータイム》报 60.1，
    真值 90.009 —— 三比二错层）。本尺子拿"已知 BPM + 12ms 抖动 + 初值偏 5%"自检过，
    偏差 <0.001 BPM（`tempo_selftest`）。
    """
    t = np.sort(np.asarray(onsets, float))
    if len(t) < 20:
        return None
    grid = p0 * (1 + np.linspace(-span, span, 4001))
    sc = np.array([_conc(t, p) for p in grid])
    p, t0 = float(grid[int(np.argmax(sc))]), 0.0
    for it in range(iters):
        k = np.round((t - t0) / p)
        keep = np.abs(t - (t0 + k * p)) < (tol if it else 0.35) * p
        if keep.sum() < 8:
            return None
        kk, tt = k[keep], t[keep]
        A = np.vstack([np.ones_like(kk), kk]).T
        sol, *_ = np.linalg.lstsq(A, tt, rcond=None)
        t0, p = float(sol[0]), float(sol[1])
    k = np.round((t - t0) / p)
    keep = np.abs(t - (t0 + k * p)) < tol * p
    return 60.0 / p, float(keep.mean()), int(keep.sum())


def tempo_selftest(verbose=True):
    """合成 click（含 12ms 抖动、初值故意偏 5%）—— 四项偏差必须 <0.15 BPM。"""
    import librosa
    rng = np.random.default_rng(7)
    ok = True
    for bpm in (87.5, 120.0, 43.8, 135.0):
        y = np.zeros(int(SR * 200), dtype='float32')
        step, pos = 60.0 / bpm, 0.013
        while pos < 200 - 0.05:
            i = int((pos + rng.normal(0, 0.012)) * SR)
            if 0 <= i < len(y) - 150:
                y[i:i + 150] += (np.hanning(150) * 0.9).astype('float32')
            pos += step
        oe = librosa.onset.onset_strength(y=y, sr=SR, hop_length=256)
        t = librosa.frames_to_time(librosa.onset.onset_detect(
            onset_envelope=oe, sr=SR, hop_length=256, units='frames'),
            sr=SR, hop_length=256)
        r = fit_tempo(t, 60.0 / (bpm * 1.05))
        got = r[0] if r else float('nan')
        good = abs(got - bpm) < 0.15
        if verbose:
            print('  速度尺子自检 %6.1f → %.2f  %s' % (bpm, got, 'OK' if good else 'FAIL'))
        ok = ok and good
    return ok


def mix_tempo(audio, stems_dir=None, bpm=None):
    """**核对**给定的 BPM：以 `60/bpm` 为初值精修，并检查 2× / 0.5× 层级。

    ⚠ **本函数只做核对，不做"检测"** —— 两个版本都栽在这一点上：
      · 第一版初值取 `p0 ∈ (0.5, 1.0, 1.4)` 秒（≈120/60/43 BPM），而 87.5 BPM 的周期是
        **0.6857 s**、不在任何初值的 ±10% 窗里 ⇒ 锁到 **131.2 BPM** 的假峰；
      · 第二版改成"按 40–200 BPM 铺初值、取**命中率最高**的那个" ⇒ 全部来源一致报
        **175.0 BPM**（87.5 的两倍）。**原因是命中率与周期不可比**：网格越密，
        落在格上（±tol）的音符天然越多 —— **命中率高 ≠ 速度对**。
      ⇒ 正解：**速度是"钉一层"的事**（同族纪律），本函数只回答"你钉的这层，
      独立方法支不支持"，以及"有没有层级（2×/0.5×）歧义"。
    """
    import librosa
    import soundfile as sf

    def onsets_of(path):
        y, s = sf.read(path, dtype='float32', always_2d=True)
        y = y.mean(axis=1)
        if s != SR:
            y = librosa.resample(y, orig_sr=s, target_sr=SR)
        oe = librosa.onset.onset_strength(y=y, sr=SR, hop_length=256)
        return librosa.frames_to_time(librosa.onset.onset_detect(
            onset_envelope=oe, sr=SR, hop_length=256, units='frames'),
            sr=SR, hop_length=256)

    srcs = [('mix', audio)] + (
        [(f[:-4], os.path.join(stems_dir, f)) for f in sorted(os.listdir(stems_dir))
         if f.endswith('.wav')] if stems_dir and os.path.isdir(stems_dir) else [])
    out = []
    for tag, p in srcs:
        on = onsets_of(p)
        r0 = fit_tempo(on, 60.0 / bpm)
        if not r0:
            continue
        # 层级歧义：2× / 0.5× 的命中率若明显更高，说明可能钉错了层
        r2 = fit_tempo(on, 60.0 / (bpm * 2))
        rh = fit_tempo(on, 60.0 / (bpm / 2))
        note = ''
        if r0[1] < 0.15:
            note = '命中率偏低（这一层支撑不足）'
        for rr, lab in ((r2, '2×'), (rh, '0.5×')):
            if rr and rr[1] > r0[1] + 0.12:
                note = (note + ' ' if note else '') + \
                       '**层级可疑**：%s 的命中率高 %.0f 个点' % (lab, 100 * (rr[1] - r0[1]))
        out.append((tag, r0[0], r0[1], note))
    return out


# ─────────────────────────── ③ 归属 ───────────────────────────
def bass_gate(stems_dir, sr=SR, hop=HOP):
    """`bass` 分轨的能量门（p60）—— 低音区的音要先证明 bass 真在响。"""
    import librosa
    import soundfile as sf
    y, s = sf.read(os.path.join(stems_dir, 'bass.wav'), dtype='float32', always_2d=True)
    y = y.mean(axis=1)
    if s != sr:
        y = librosa.resample(y, orig_sr=s, target_sr=sr)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    db = 20 * np.log10(np.maximum(rms, 1e-12))
    t = librosa.frames_to_time(np.arange(len(db)), sr=sr, hop_length=hop)
    return t, db, float(np.percentile(db, 60))


def fallback_track(p):
    """音区兜底：≤47→Bass · 48–67→Hook · 68–79→Piano · ≥80→Glock。"""
    return 'Bass' if p <= 47 else ('Hook' if p <= 67 else ('Piano' if p <= 79 else 'Glock'))


def assign(bp_notes, prior=None, gate=None):
    """BP 音符 → 引擎轨。`bp_notes` = `[(秒, 时长, 音高)]`；`prior` = `[(秒, 音高, 轨道名)]`。"""
    ynt = np.array([x[0] for x in prior]) if prior else np.zeros(0)
    ynp = np.array([x[1] for x in prior]) if prior else np.zeros(0)
    ynm = [x[2] for x in prior] if prior else []
    tracks, stat = {}, {}
    for t, dur, p in bp_notes:
        trk, why = None, '音区兜底'
        if len(ynt):
            m = np.where((np.abs(ynt - t) <= 0.08) & (np.abs(ynp - p) <= 1))[0]
            if len(m):
                from collections import Counter
                src = Counter(ynm[i] for i in m).most_common(1)[0][0]
                eng = ENGINE_MAP.get(src)
                if eng == 'Drums':
                    trk, why = None, '落在 YMT3 鼓通道 → 交给鼓轨'
                elif eng:
                    trk, why = eng, 'YMT3 先验'
        if trk is None and why == '音区兜底':
            trk = fallback_track(p)
        if trk == 'Bass' and gate is not None:
            tg, dg, thr = gate
            k = (tg >= t - 0.05) & (tg <= t + max(dur, 0.1) + 0.05)
            if not (k.any() and np.max(dg[k]) >= thr):
                trk, why = ('Hook' if p <= 67 else 'Piano'), '低音门未过 → 改判'
        if trk is None:
            stat[('跳过', why)] = stat.get(('跳过', why), 0) + 1
            continue
        tracks.setdefault(trk, []).append([t, dur, p])
        stat[(trk, why)] = stat.get((trk, why), 0) + 1
    return tracks, stat


def write_tracks(tracks, out_dir, bpm, name):
    import midi_file
    os.makedirs(out_dir, exist_ok=True)
    sp = 60.0 / bpm
    files = {}
    for trk, rows in tracks.items():
        rows = sorted(rows, key=lambda z: (z[0], z[2]))
        mm = {'format': 1, 'division': 480, 'bpm': bpm, 'timesig': [4, 4],
              'title': '%s %s' % (name, trk), 'source': '',
              'end_beat': max((r[0] + r[1]) / sp for r in rows),
              'tracks': [{'index': 0, 'name': trk, 'channel': 9 if trk == 'Drums' else 0,
                          'program': 0, 'drum': trk == 'Drums', 'mute': False,
                          'solo': False, 'hidden': False,
                          'notes': [[r[0] / sp, r[1] / sp, int(r[2]), 90] for r in rows],
                          'ccs': [], 'program_changes': [], 'markers': []}]}
        p = os.path.join(out_dir, '%s_%s.mid' % (name, trk))
        midi_file.export_midi(mm, p)
        files[trk] = p
    return files


def read_midi_notes(path):
    import midi_file
    d = midi_file.import_midi(path)
    spb = 60.0 / float(d.get('bpm') or 120)
    out = []
    for tr in d['tracks']:
        nm = str(tr.get('name') or '')
        for n in (tr.get('notes') or []):
            out.append((float(n[0]) * spb, float(n[1]) * spb, int(n[2]), nm))
    return out


def find_stems(d):
    """`--stems-dir` 可以给模型目录本身，也可以给它的上级。"""
    if d and os.path.exists(os.path.join(d, 'drums.wav')):
        return d
    if d and os.path.isdir(d):
        for root, _dn, fn in os.walk(d):
            if 'drums.wav' in fn and 'bass.wav' in fn:
                return root
    return None


def main():
    ap = argparse.ArgumentParser(description='BP 为主的还原转录（BP 出音符 · 分轨出鼓 · 定归属）')
    ap.add_argument('audio', nargs='?', help='原曲音频')
    ap.add_argument('--bpm', default='auto', help='"auto"（用分轨最小二乘拟合，自检过）或数字')
    ap.add_argument('--stems-dir', default=None, help='Demucs 分轨目录（含 drums.wav/bass.wav）')
    ap.add_argument('--bp-midi', default=None, help='已算好的 Basic Pitch MIDI（省一次推理）')
    ap.add_argument('--prior-midi', default=None, help='可选：YMT3 MIDI，当通道先验')
    ap.add_argument('--out', default=None, help='各引擎轨单轨 MIDI 的输出目录')
    ap.add_argument('--song', default=None, help='给了就继续跑 transcribe_to_song 生成 song.json')
    ap.add_argument('--render', action='store_true', help='再把 make_song 也跑了')
    ap.add_argument('--drum-source', choices=('ymt3', 'bp'), default='ymt3',
                    help='鼓从哪来：**默认 ymt3**（用 --prior-midi 那条 YMT3 的鼓通道）；'
                         '没有 prior-midi 时自动退回 bp（分轨起音分频）')
    ap.add_argument('--selftest', action='store_true', help='只跑两把尺子的自检')
    a = ap.parse_args()

    if a.selftest or not a.audio:
        print('--- 尺子自检（合成件，不碰真音频）---')
        ok1 = drum_selftest()
        ok2 = tempo_selftest()
        print('  结论：%s' % ('PASS' if ok1 and ok2 else 'FAIL'))
        return 0 if (ok1 and ok2) else 1

    stems = find_stems(a.stems_dir)
    name = os.path.splitext(os.path.basename(a.audio))[0]
    out = a.out or os.path.join(ROOT, '_transcribe', name + '_bp')
    os.makedirs(out, exist_ok=True)

    # ① 音符（Basic Pitch）
    bpm = None
    if a.bpm != 'auto':
        bpm = float(a.bpm)
    if a.bpm == 'auto':
        print('① 速度：分轨最小二乘拟合…')
        for tag, v, hit in mix_tempo(a.audio, stems):
            print('   %-8s → %.3f BPM（命中率 %.0f%%）' % (tag, v, hit * 100))
        print('  ⚠ 请用 `--bpm <数字>` 钉死一层（同族纪律：链上默认 detect_bpm 错过两次）')
        return 2
    vals = mix_tempo(a.audio, stems, bpm)
    if vals:
        print('① 速度核对（独立方法：相位聚集度 + 最小二乘精修；只核对、不检测）：')
        for tag, v, hit, note in vals:
            print('   %-8s %7.3f BPM（命中率 %.0f%%）%s%s'
                  % (tag, v, hit * 100,
                     '' if abs(v - bpm) / bpm < 0.01 else '  ← 与 --bpm 不一致',
                     ('  ' + note) if note else ''))
        agree = sum(1 for _t, v, _h, _n in vals if abs(v - bpm) / bpm < 0.01)
        print('   %d/%d 条来源支持 --bpm %.3f%s'
              % (agree, len(vals), bpm, '' if agree >= 2 else
                 '  ⚠ **请核对** —— 同族纪律：链上默认 detect_bpm 已错过两次（三比二错层）'))

    bp_mid = a.bp_midi
    if not bp_mid:
        bp_mid = os.path.join(out, name + '_bp.mid')
        print('② Basic Pitch 转录 → %s' % bp_mid)
        r = subprocess.run([sys.executable, os.path.join(HERE, 'bp_transcribe.py'),
                            a.audio, bp_mid], cwd=ROOT, capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        print('   ' + (r.stdout or '').strip().splitlines()[-1] if r.stdout else '')
        if r.returncode or not os.path.exists(bp_mid):
            print('   ✗ BP 失败：%s' % (r.stderr or '')[-300:])
            return 1
    bp = [(x[0], x[1], x[2]) for x in read_midi_notes(bp_mid)]
    print('   音符 %d' % len(bp))

    # ③ 鼓：**默认取 YMT3 的鼓通道**（用户 2026-10-04 定："BP 的鼓搞不好就还是用 YMT3"）
    #    依据（本模块 docstring 第 2 条 + `PITFALLS` 316）：BP 路径的鼓由"分轨起音分频"造，
    #    实测在《どうぞめしあがれ》上塞了 638/795 个军鼓、每小节 14/17 点 —— 而原曲
    #    `drums` 分轨 RMS −34.2dB、几乎没有 <120Hz ⇒ **原曲没有鼓组**，那层全是假的。
    #    分频判据**自检 3/3 通过**但对真实音频不适用（"自检过了 ≠ 适用"）。
    #    ⇒ 只有在**明确要**（`--drum-source bp`）或**没有 YMT3 先验**时才用 BP 的鼓。
    drum, drum_src = [], 'bp'
    if a.drum_source == 'ymt3' and a.prior_midi:
        ym = read_midi_notes(a.prior_midi)
        # 鼓 = **轨名**里含 drum 的那条（YMT3 写出的轨名就是 `Drums`；按名字取，
        # 别按通道号 —— 引擎的鼓走通道 9，但转录 MIDI 的通道分配不是同一套）。
        drum = [(x[0], int(x[2])) for x in ym if 'drum' in str(x[3]).lower()]
        drum_src = 'ymt3' if drum else 'bp'
        print('③ 鼓 = **YMT3 鼓通道** %d 个（默认口径；要 BP 的分频鼓加 `--drum-source bp`）'
              % len(drum))
        if not drum:
            print('   ⚠ YMT3 先验里没有鼓轨 ⇒ 退回 BP 分频鼓（注意：实测它在无鼓组的曲子上会凭空生成）')
    elif a.drum_source == 'ymt3' and not a.prior_midi:
        print('③ `--drum-source ymt3` 但没给 `--prior-midi` ⇒ 退回 BP 分频鼓'
              '（实测口径：**BP 的鼓在无鼓组的曲子上会凭空生成**，优先给 YMT3 先验）')
    if not drum:
        if not drum_selftest(verbose=False):
            print('✗ 鼓尺子自检不过 —— 拒绝量真音频（判据先拿合成件验）')
            return 1
        if stems:
            drum = drums_from_stems(stems)
            drum_src = 'bp'
            print('③ 分轨起音分频 → 鼓 %d 个（底鼓 %d · 军鼓 %d · 闭镲 %d）'
                  % (len(drum), sum(1 for _t, k in drum if k == KICK),
                     sum(1 for _t, k in drum if k == SNARE),
                     sum(1 for _t, k in drum if k == HAT)))
        else:
            print('③ 没给 --stems-dir：**出不了鼓**（BP 是音高模型，不分鼓）')

    # ④ 归属
    prior = [(x[0], x[2], x[3]) for x in read_midi_notes(a.prior_midi)] if a.prior_midi else None
    gate = bass_gate(stems) if stems else None
    tracks, stat = assign(bp, prior, gate)
    if drum:
        tracks['Drums'] = [[t, 0.05, int(k)] for t, k in drum]
    print('④ 归属：%s' % {k: len(v) for k, v in sorted(tracks.items())})
    print('   来源明细：%s' % {('%s/%s' % k): v for k, v in sorted(stat.items())})
    if not prior:
        print('   （没给 --prior-midi：全部走音区兜底 —— 实测净覆盖只差 0.8 个点，可接受）')

    files = write_tracks(tracks, os.path.join(out, 'split'), bpm, name)
    print('⑤ 单轨 MIDI → %s' % os.path.join(out, 'split'))

    if not a.song:
        print('\n下一步：`transcribe_to_song.py <曲名> --bpm %.3f --chords-log … '
              '--mid 轨=文件…`（或加 `--song <曲名>` 让本工具接着跑）' % bpm)
        return 0

    # ⑤ 接引擎正路
    print('⑥ 接引擎正路：songs/%s' % a.song)
    sd = os.path.join(ROOT, 'songs', a.song)
    os.makedirs(sd, exist_ok=True)
    cl = os.path.join(sd, 'chords.txt')
    with open(cl, 'w', encoding='utf-8') as fh:
        subprocess.run([sys.executable, os.path.join(HERE, 'analyze_chords.py'), a.audio,
                        '--bpm', str(bpm)], stdout=fh, stderr=subprocess.PIPE,
                       text=True, encoding='utf-8', errors='replace')
    cmd = [sys.executable, os.path.join(HERE, 'transcribe_to_song.py'), a.song,
           '--bpm', '%.4f' % bpm, '--chords-log', cl, '--no-melody', '--auto',
           '--audio', a.audio, '--out', os.path.join(sd, 'song.json')]
    for trk, p in sorted(files.items()):
        cmd += ['--mid', '%s=%s' % (trk, p)]
    if stems:
        pair = {'Piano': 'other.wav', 'Pad': 'other.wav', 'Strings': 'other.wav',
                'Glock': 'other.wav', 'Hook': 'other.wav', 'Melody': 'other.wav',
                'Bass': 'bass.wav', 'Drums': 'drums.wav'}
        for trk in files:
            w = os.path.join(stems, pair.get(trk, 'other.wav'))
            if os.path.exists(w):
                cmd += ['--stem-audio', '%s=%s' % (trk, w)]
        if 'Drums' in files:
            cmd += ['--drums-mid', files['Drums']]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    print('   transcribe_to_song 退出码 %d' % r.returncode)
    if r.returncode:
        print('   ' + (r.stderr or '')[-400:])
        return 1
    p = os.path.join(sd, 'song.json')
    d = json.load(open(p, encoding='utf-8'))
    d['programs'] = {k: v for k, v in DEFAULT_PROGS.items()
                     if k in (d.get('notes_extra') or {})}
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    # compose.py（照曲库里现成的模板）
    tpl = os.path.join(ROOT, 'songs', 'hot_tea_time', 'compose.py')
    if os.path.exists(tpl):
        open(os.path.join(sd, 'compose.py'), 'w', encoding='utf-8').write(
            open(tpl, encoding='utf-8').read().replace('hot_tea_time', a.song))
    print('   ✓ songs/%s/song.json（生成层由引擎按"输入哪个才开哪个"自动关）' % a.song)
    if a.render:
        subprocess.run([sys.executable, os.path.join(HERE, 'make_song.py'), a.song],
                       cwd=ROOT)
    return 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）


if __name__ == '__main__':
    sys.exit(main())
