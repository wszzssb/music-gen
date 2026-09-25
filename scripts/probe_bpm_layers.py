# -*- coding: utf-8 -*-
"""probe_bpm_layers.py —— **BPM 层级判定**：一首曲子的"速度"是**层级**，不是单值。

## 何时用

扒谱 / 还原 / 模仿**开工定速度**时，或 `check_audio.py` 报"两个层级都成立"时。
**别把某一个数直接写进 `song.json`**：层级选错会让**小节数翻倍/减半**，段落切分与
逐小节鼓型跟着错位（时间轴本身仍对，错的是段级控制）。

## 三把互相独立的尺子（都打在**同一份音频**上）

| # | 尺子 | 看什么 |
|---|---|---|
| ① | **自相关层级** | onset 强度包络的自相关峰 → 候选周期 + **每层支持度**（可能不止一个） |
| ② | **IOI 直方图** | 起音间隔的众数 / 中位 —— 真实脉冲落在哪个周期上 |
| ③ | **网格贴合** | 每个候选 BPM 下，起音到最近网格点的**中位偏差** + `<25ms` 占比 |
| ＋ | `--stems <demucs 目录>` | 把 ①②③ 也打在 `drums` 分轨上（节拍在鼓上最干净） |
| ＋ | `--deep`（`.venv-ml`） | 再加 percussive 分离 + librosa 的 **DP beat tracking**（含 IOI 的 CV，越小越稳） |

## 读法（**三条，别只看一个数**）

1. **支持度最高 ≠ 一定是它** —— 先看 ①②③ 是否**指向同一层**；不指向就把候选都写进
   `notes.md`，再用**每拍鼓音数**与**段长分布**拍板。
2. **半速/双速层天然都成立**（同一条 onset 序列，1 拍 1 个音还是 2 拍 1 个音都说得通）——
   工具会标出倍数关系。
3. ⚠ `octave` 那类**频谱**指标与"速度层级"无关，别混用。

## 实测（2026-09-25 · `siren_end`，塞壬唱片「......已至」）

- ① 自相关：**193.6**（支持度 0.214）与 **96.8**（0.182）两层成立
- ②＋ DR beat tracking（`drums` 分轨）：**143.55**，IOI 中位 0.418s、**CV 0.033**（很稳）
- ③ kick 起音间隔中位 **0.615s ≈ 2×0.3099** → 正是 193.6 的"1、3 拍"
- 三个层级彼此是 4:3 / 1:2 关系，**都"成立"** → 最后按"**每拍鼓音数** 3.2 个（若取 96.8 要 6.4 个，
  不现实）"与"**段长分布自洽**"选了 **193.6**

## 用法

```bash
python scripts/probe_bpm_layers.py <音频> [--stems <demucs 输出目录>] [--deep] [--top 4]
python scripts/probe_bpm_layers.py <音频> --json        # 机读（给别的工具用）
```

⚠ 核心（`onset_env` / `autocorr_layers` / `pick_peaks` / `grid_fit` / `analyze_signal`）
**只用 numpy** —— 所以守卫 `t_bpm_layers_contract` 能在主 venv 里用**合成 click 自证**；
librosa 只在 `--deep` 里延迟导入。
"""
import argparse
import json
import os
import sys

import numpy as np

# 自证/默认用的候选层级范围
LO_BPM, HI_BPM = 40.0, 220.0
# STFT 帧参数（**帧中心偏移依赖它**：`t0 = N_FFT/2/sr`）
N_FFT, HOP = 1024, 256
# 网格贴合只用「包络 ≥ 该分位」的起音（见 `onsets_of`）
STRONG_Q = 0.60


def onset_env(y, sr, n_fft=N_FFT, hop=HOP):
    """谱通量 onset 强度包络 → `(env[0..1], fps)`（纯 numpy，不依赖 librosa）。"""
    y = np.asarray(y, dtype=np.float64)
    if y.ndim > 1:
        y = y.mean(axis=1)
    n = 1 + (len(y) - n_fft) // hop
    if n < 4:
        return np.zeros(1), float(sr) / hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    fr = y[idx] * np.hanning(n_fft)[None, :]
    S = np.abs(np.fft.rfft(fr, axis=1))
    flux = np.maximum(0.0, np.diff(S, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    return flux / (flux.max() + 1e-9), float(sr) / hop


def autocorr_layers(env, fps, lo_bpm=LO_BPM, hi_bpm=HI_BPM, top=4):
    """→ `[(bpm, 支持度)]`，按支持度降序（**可能有多层**：半速/双速天然都成立）。"""
    e = np.asarray(env, dtype=np.float64)
    if e.size < 8:
        return []
    e = e - e.mean()
    ac = np.correlate(e, e, mode="full")[e.size - 1:]
    ac = ac / (ac[0] + 1e-9)
    lo = max(2, int(round(fps * 60.0 / hi_bpm)))
    hi = min(len(ac) - 2, int(round(fps * 60.0 / lo_bpm)))
    if hi <= lo:
        return []
    peaks = []
    for lg in range(lo, hi + 1):
        if ac[lg] >= ac[lg - 1] and ac[lg] >= ac[lg + 1]:
            peaks.append((60.0 * fps / lg, float(ac[lg])))
    peaks.sort(key=lambda x: -x[1])
    return peaks[:top]


def pick_peaks(env, fps, delta=0.12, wait_s=0.10, t0=0.0, smooth=3):
    """起音时刻（秒）—— 平滑 + 局部窗口极大 + 阈值 + 不应期。

    ⚠ `t0` = **帧中心偏移**（`n_fft/2/sr`）：STFT 第 i 帧代表的是 `i*hop + n_fft/2` 处的信号，
    不给这个偏移，所有起音会系统性**晚 23ms**（@22050/1024）—— 网格贴合率会从 90%+
    掉到 **13%**（2026-09-25 由 `t_bpm_layers_contract` 的合成 click 当场抓到）。
    """
    d = np.asarray(env, dtype=np.float64)
    if d.size < 3:
        return np.zeros(0)
    if smooth and smooth > 1:
        k = np.ones(smooth) / float(smooth)
        d = np.convolve(d, k, mode="same")
    d = d - np.median(d)
    d = d / (np.abs(d).max() + 1e-9)
    wait = max(1, int(wait_s * fps))
    out, last = [], -10 ** 9
    for i in range(1, len(d) - 1):
        if d[i] < delta or (i - last) < wait:
            continue
        lo, hi = max(0, i - 2), min(len(d), i + 3)      # 局部窗口极大（比相邻两点稳）
        if d[i] >= d[lo:hi].max():
            out.append(i)
            last = i
    return np.asarray(out, dtype=np.float64) / fps + t0


def onsets_of(y, sr, n_fft=N_FFT, hop=HOP, strength=None):
    """→ 起音时刻（秒），**已含帧中心偏移** —— 外部一律用这个。

    ⚠ 别自己拼 `onset_env` + `pick_peaks`：`t0` 一旦漏掉，所有起音会系统性晚 23ms，
    网格贴合率从 ~100% 掉到 26%（自证当场抓到过两次）—— 对齐只留这一处实现。

    `strength`（0~1）：只要**包络 ≥ 该分位**的起音。**网格贴合必须用它**（默认 `STRONG_Q`）——
    密集材料里弱起音会把相位抹平，任何 BPM 的贴合率都掉到 ~10%，判据就没有区分度了。
    """
    env, fps = onset_env(y, sr, n_fft=n_fft, hop=hop)
    pk = pick_peaks(env, fps, t0=(n_fft / 2.0) / float(sr))
    if strength:
        tt = np.arange(len(env)) / float(fps) + (n_fft / 2.0) / float(sr)
        vals = np.interp(pk, tt, env)
        if vals.size:
            pk = pk[vals >= np.quantile(vals, float(strength))]
    return pk


def grid_fit(onsets, bpm):
    """→ `(中位偏差秒, <25ms 占比)`：这批起音离该 BPM 网格有多近。"""
    onsets = np.asarray(onsets, dtype=np.float64)
    if onsets.size == 0 or bpm <= 0:
        return float("nan"), 0.0
    per = 60.0 / bpm
    ph = (onsets / per) % 1.0
    d = np.minimum(ph, 1 - ph) * per
    return float(np.median(d)), float(np.mean(d < 0.025))


def ioi_stats(onsets, lo=0.05, hi=2.0):
    """→ `(IOI 中位, [(IOI 众数, 计数)...])`。"""
    onsets = np.asarray(onsets, dtype=np.float64)
    if onsets.size < 3:
        return float("nan"), []
    iv = np.diff(np.sort(onsets))
    iv = iv[(iv > lo) & (iv < hi)]
    if iv.size == 0:
        return float("nan"), []
    hist, edges = np.histogram(iv, bins=np.arange(lo, hi, 0.02))
    order = np.argsort(hist)[::-1][:5]
    modes = [(round(float(edges[i]), 3), int(hist[i])) for i in order if hist[i]]
    return float(np.median(iv)), modes


def analyze_signal(y, sr, cands=None, top=4, n_fft=N_FFT, hop=HOP):
    """→ 每个候选层的读数（**纯 numpy**，守卫拿它做自证）。

    每项：`{'bpm', 'ac'(支持度), 'ioi_med', 'fit_med', 'fit_frac'}`。
    """
    env, fps = onset_env(y, sr, n_fft=n_fft, hop=hop)
    layers = autocorr_layers(env, fps, top=top)
    ons = onsets_of(y, sr, n_fft=n_fft, hop=hop)
    ons_g = onsets_of(y, sr, n_fft=n_fft, hop=hop, strength=STRONG_Q)  # 网格只信强起音
    ioi_med, _modes = ioi_stats(ons)
    if cands:
        layers = [(float(b), 0.0) for b in cands]
    rows = []
    for bpm, sup in layers:
        med, frac = grid_fit(ons_g, bpm)
        rows.append({"bpm": round(float(bpm), 2), "ac": round(float(sup), 4),
                     "ioi_med": round(ioi_med, 4) if ioi_med == ioi_med else None,
                     "fit_med": round(med, 4) if med == med else None,
                     "fit_frac": round(frac, 4), "ons_used": int(ons_g.size)})
    return rows


def synth_click(bpm=120.0, dur=12.0, sr=22050, off_beat=False):
    """合成 click 轨（每拍一个短脉冲）—— **自证用**。

    `off_beat=True` 时再在每拍**后半拍**加一个弱脉冲。
    ⚠ **自证默认不加后半拍**：那是**半拍**上的音，拿"全拍网格"去量它天然只贴合 47%
    （2026-09-25 实测，我一度以为是自己帧中心算错了）—— 自证要用**最简信号**，
    否则量的是"信号构造"而不是工具本身。
    """
    n = int(dur * sr)
    y = np.zeros(n, dtype=np.float64)
    per = 60.0 / bpm
    offs = [(0.0, 1.0)] + ([(0.5 * per, 0.4)] if off_beat else [])
    for k in range(int(dur / per)):
        for off, amp in offs:
            i0 = int((k * per + off) * sr)
            ln = int(0.02 * sr)
            if 0 <= i0 < n - ln:
                t = np.arange(ln) / float(sr)
                y[i0:i0 + ln] += amp * np.sin(2 * np.pi * 1000.0 * t) * np.exp(-t * 60.0)
    return y


def _deep_rows(path, top):
    """`--deep`：percussive 分离 + DP beat tracking（需要 `.venv-ml` 的 librosa）。"""
    import librosa
    y, sr = librosa.load(path, sr=22050, mono=True)
    yh = librosa.effects.percussive(y, margin=3.0)
    out = []
    for tight in (100, 50):
        tempo, beats = librosa.beat.beat_track(y=yh, sr=sr, trim=False, tightness=tight)
        bt = librosa.frames_to_time(beats, sr=sr)
        iv = np.diff(bt)
        iv = iv[iv > 0.05]
        out.append({"tight": tight, "tempo": round(float(np.atleast_1d(tempo)[0]), 2),
                    "beats": int(len(beats)),
                    "ioi_med": round(float(np.median(iv)), 4) if iv.size else None,
                    "ioi_cv": round(float(np.std(iv) / np.mean(iv)), 4) if iv.size else None})
    return out


def main():
    ap = argparse.ArgumentParser(description='BPM 层级判定（自相关 / IOI / 网格贴合 + 可选 DR）')
    ap.add_argument('audio')
    ap.add_argument('--stems', default=None, help='demucs 输出目录（用其中的 drums 分轨复算）')
    ap.add_argument('--deep', action='store_true', help='加 percussive + DP beat tracking（.venv-ml）')
    ap.add_argument('--top', type=int, default=4)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    import soundfile as sf
    y, sr = sf.read(a.audio, always_2d=True)
    y = y.mean(axis=1)
    rows = analyze_signal(y, sr, top=a.top)
    ons = onsets_of(y, sr)
    ioi_med, modes = ioi_stats(ons)
    res = {'audio': os.path.abspath(a.audio), 'sr': sr, 'dur': round(len(y) / float(sr), 2),
           'onsets': int(ons.size), 'ioi_med': round(ioi_med, 4) if ioi_med == ioi_med else None,
           'ioi_modes': modes, 'layers': rows}

    drums = None
    if a.stems:
        for root, _dirs, files in os.walk(a.stems):
            for f in files:
                if f == 'drums.wav':
                    drums = os.path.join(root, f)
    if drums:
        dy, dsr = sf.read(drums, always_2d=True)
        dy = dy.mean(axis=1)
        dons = onsets_of(dy, dsr)
        d_ioi, d_modes = ioi_stats(dons)
        res['drums'] = {'file': drums, 'onsets': int(dons.size),
                        'ioi_med': round(d_ioi, 4) if d_ioi == d_ioi else None,
                        'ioi_modes': d_modes}
    if a.deep:
        try:
            res['deep'] = _deep_rows(a.audio, a.top)
        except Exception as e:                                     # noqa: BLE001
            res['deep_error'] = '%s: %s' % (type(e).__name__, str(e)[:80])

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0

    print('== %s（%.1fs · %d 起音）==' % (os.path.basename(a.audio), res['dur'], res['onsets']))
    print('%-9s %8s %10s %10s %8s %6s'
          % ('候选BPM', '自相关', 'IOI中位', '网格中位差', '贴合<25ms', '强起音'))
    for r in rows:
        print('%-9.2f %8.4f %10s %10s %7.0f%% %6d'
              % (r['bpm'], r['ac'],
                 ('%.4f' % r['ioi_med']) if r['ioi_med'] is not None else '—',
                 ('%.4f' % r['fit_med']) if r['fit_med'] is not None else '—',
                 r['fit_frac'] * 100, r.get('ons_used', 0)))
    if modes:
        print('IOI 众数：%s' % ' · '.join('%.2fs×%d' % (m, c) for m, c in modes))
    if drums:
        print('drums 分轨：%d 起音 · IOI 中位 %s · 众数 %s'
              % (res['drums']['onsets'], res['drums']['ioi_med'],
                 ' · '.join('%.2fs×%d' % (m, c) for m, c in res['drums']['ioi_modes'][:4])))
    for d in res.get('deep', []) or []:
        print('DR beat_track(tight=%d)：%s BPM · IOI 中位 %s · CV %s'
              % (d['tight'], d['tempo'], d['ioi_med'], d['ioi_cv']))
    if res.get('deep_error'):
        print('（--deep 失败：%s）' % res['deep_error'])
    print()
    print('关系提示（同一条 onset 序列的其它读法 —— 这些层通常**也"成立"**）：')
    base = rows[0]['bpm'] if rows else 0.0
    for lab, k in (('×2（双速）', 2.0), ('÷2（半速）', 0.5), ('×3/2（附点）', 1.5),
                   ('×2/3（反附点）', 2 / 3.0), ('×4/3', 4 / 3.0), ('×3/4', 3 / 4.0)):
        cand = base * k
        if not (LO_BPM - 5 <= cand <= HI_BPM + 5):
            continue
        hit = [r for r in rows if abs(r['bpm'] - cand) < 2.0]
        print('   %-14s %7.2f BPM%s' % (lab, cand, '   ← 也在候选层里' if hit else ''))
    print()
    print('⚠ 三层都可能"成立"（半速/双速/附点关系）—— 别只看支持度最高的那个：')
    print('   再用【每拍鼓音数】与【段长分布】拍板，并把候选层级一起写进 notes.md。')
    return 0


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                              # noqa: BLE001
        pass
    sys.exit(main())
