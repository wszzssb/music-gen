#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""who_has_melody.py —— **参考曲的单旋律在哪个音区/哪条分轨**（一条命令，任何曲子可用）。

## 为什么（2026-10-02，BGM35 的任务 4 卡在这里）

还原链的 `Melody` 轨是**从某条分轨的高音区抽出来的**（`transcribe_to_song --melody-from`）。
BGM35 上它抽自 `vocals` 分轨 —— 而那条分轨 **整轨 RMS −57.4dB（等于静音）**，
于是 Melody 轨的 151 个音**全是假音**。问题不在"用什么音色"，
而在"**这层到底该不该有旋律、该从哪条分轨抽**"。

本工具只回答这一个问题，判据全部来自**音频**（不看任何转录模型的标签）：

1. **主旋律通常是最高的那条持续线**：逐帧取"能量最强音高"（谐波梳），
   再看它落在哪个音区、时间上是否连续（旋律的特征：单音、时值长、跨小节延续）；
2. **逐分轨的"单音性"**：旋律层同一时刻通常只有 1–2 个音高在响
   （和弦层会有 3+ 个）—— 用"逐帧非零谐波峰的个数"衡量；
3. **分轨能量占比**：哪条分轨在最响；（`vocals` 若整轨 −57dB 就是"没有这一层"）。

输出：每条分轨的"单音性 / 能量 / 最强音高中位"，以及**综合建议"旋律该从哪条分轨抽"**。

## 用法

```bash
python scripts/who_has_melody.py --stems <demucs六轨目录> [--json 出.json] [--top 6]
python scripts/who_has_melody.py --selftest
```
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SR = 22050
PITCH_LO = 24


def load_mono(path, sr=SR):
    import soundfile as sf
    y, s0 = sf.read(path, dtype='float32', always_2d=True)
    y = y.mean(axis=1).astype(np.float64)
    if s0 != sr:
        import librosa
        y = librosa.resample(y, orig_sr=s0, target_sr=sr)
    return y


def frame_stats(y, nthr=0.25, harm_w=True):
    """→ 逐帧：最强音高 / 谐波峰个数（单音性）/ 帧能量（dB）

    谐波梳与 `restore_gap_fill` 同法：对每个候选音高累加 1–8 次谐波的窄带幅度。

    ⚠ **必须按谐波次数加权（`1/k`）**，否则"低八度候选"永远赢：
    实测 440Hz 纯正弦的最强音高被读成 **低一个八度**（36 而不是 72）——
    因为低八度候选的 2/3/4 次谐波**正好落在该音的基频/谐波上**，
    它的和比真候选还大。这正是 `RESTORE-METHOD` 里"判八度只能比基频"那条的同族坑；
    按 `1/k` 加权后基频贡献最大，真候选胜出。
    """
    n, hop = 2048, 512
    fr = np.fft.rfftfreq(n, 1.0 / SR)
    win = np.hanning(n)
    nfr = 1 + max(0, (len(y) - n) // hop)
    if nfr < 2:
        return None
    idx = np.arange(n)[None, :] + hop * np.arange(nfr)[:, None]
    S = np.abs(np.fft.rfft(y[idx] * win, axis=1))            # (帧, 频点)
    E = np.zeros((128, nfr))
    for p in range(PITCH_LO, 128):
        f0 = 440.0 * 2 ** ((p - 69) / 12.0)
        if f0 > SR * 0.45:
            break
        acc = np.zeros(nfr)
        for k in range(1, 9):
            f = f0 * k
            if f > SR * 0.45:
                break
            m = (fr >= f * 0.98) & (fr <= f * 1.02)
            if m.any():
                w = (1.0 / k) if harm_w else 1.0
                acc += w * S[:, m].max(axis=1)
        E[p] = acc
    E = E[PITCH_LO:]
    peak = E.argmax(axis=0) + PITCH_LO
    mx = E.max(axis=0)
    # **八度无关的峰个数**：候选里的"强音高"若与更强的候选差整数个八度、或差 ≤1 半音，
    # 视为同一个音（同一个音在八度上的谐波不该被数成第二个声部）。
    # ⚠ 不加这一步时，单音正弦的"峰个数"会 ≥3（低两个八度的候选都过门）——
    #   自检里钉着这条（与"最强音高读低一个八度"是同族坑）。
    npeaks = np.zeros(nfr, dtype=int)
    for i in range(nfr):
        thr = nthr * mx[i]
        cand = [p for p in range(E.shape[0]) if E[p, i] >= thr]
        cand.sort(key=lambda p: -E[p, i])
        kept = []
        for p in cand:
            q = p + PITCH_LO
            if any(abs(q - k) <= 1 or abs(q - k) % 12 == 0 for k in kept):
                continue
            kept.append(q)
        npeaks[i] = len(kept)
    rmsf = np.array([(y[i:i + n] ** 2).mean() for i in range(0, len(y) - n, hop)])
    rms = 10 * np.log10(np.maximum(rmsf, 1e-12))
    return dict(peak=peak, npeaks=npeaks, rms=rms[:nfr], t=np.arange(nfr) * hop / SR)


def selftest():
    """尺子自检（合成已知答案）：
       · 单音旋律（一条正弦线）→ 最强音高 = 它自己，峰个数 ≈1
       · 三音和弦 → 最强音高在三个音里，峰个数 ≈3
       · 静音 → 能量很低（工具必须能判"这条分轨没有内容"）
    """
    ok = True

    def chk(lab, got, want):
        nonlocal ok
        f = 'PASS' if got == want else 'FAIL'
        ok = ok and got == want
        print('  [%s] %-48s 期望 %-8s 实得 %-8s' % (f, lab, want, got))

    sr = SR
    t = np.arange(int(3.0 * sr)) / float(sr)
    f0 = 440.0 * 2 ** ((72 - 69) / 12.0)
    mono = np.sin(2 * np.pi * f0 * t) * 0.5
    s = frame_stats(mono)
    mid = int(np.median(s['peak']))
    chk('单音：最强音高中位 = 72', mid, 72)
    chk('单音：峰个数中位 ≤2', int(np.median(s["npeaks"])) <= 2, True)
    chord = sum(np.sin(2 * np.pi * 440.0 * 2 ** ((p - 69) / 12.0) * t) / 3 for p in (60, 64, 67))
    s2 = frame_stats(chord)
    chk('和弦：峰个数中位 ≥2', int(np.median(s2['npeaks'])) >= 2, True)
    quiet = np.random.RandomState(0).randn(len(t)) * 1e-5
    s3 = frame_stats(quiet)
    chk('静音：能量中位 < −60dB', float(np.median(s3['rms'])) < -60, True)
    print('  selftest %s' % ('全部通过' if ok else '有失败'))
    return ok


def main():
    ap = argparse.ArgumentParser(description='参考曲的单旋律在哪条分轨/哪个音区')
    ap.add_argument('--stems', help='demucs 六轨目录')
    ap.add_argument('--top', type=int, default=6, help='打印前 N 条分轨')
    ap.add_argument('--json', default=None)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not a.stems or not os.path.isdir(a.stems):
        ap.print_help()
        return 2
    try:
        import pyenv
        pyenv.ensure('librosa', '.venv-ml', '本工具要 librosa/soundfile')
    except Exception:                                          # noqa: BLE001
        pass
    rows = []
    for f in sorted(os.listdir(a.stems)):
        if not f.lower().endswith(('.wav', '.flac')):
            continue
        name = os.path.splitext(f)[0]
        y = load_mono(os.path.join(a.stems, f))
        st = frame_stats(y)
        if st is None:
            continue
        rms = float(np.median(st['rms']))
        pk = st['peak'][st['rms'] > max(rms - 6.0, -60.0)]       # 只在"有内容"的帧上看音高
        rows.append(dict(stem=name, rms=rms,
                         peak_med=float(np.median(pk)) if len(pk) else None,
                         npeaks_med=float(np.median(st['npeaks'][st['rms'] > rms - 6.0]))
                         if len(pk) else None))
    rows.sort(key=lambda r: -r['rms'])
    shown = rows[:max(1, a.top)]
    print('%-10s %9s %11s %11s  %s' % ('分轨', 'RMS dB', '最强音高中位', '峰个数中位', '判读'))
    print('-' * 78)
    for r in shown:
        if r['rms'] < -45:
            verdict = '**这条分轨没有内容**（别从它抽任何东西）'
        elif r['npeaks_med'] is not None and r['npeaks_med'] <= 2.5:
            verdict = '单音性强 → **旋律候选**'
        else:
            verdict = '复音（和弦/织体）'
        print('%-10s %9.1f %11s %11s  %s'
              % (r['stem'], r['rms'],
                 '—' if r['peak_med'] is None else '%d' % r['peak_med'],
                 '—' if r['npeaks_med'] is None else '%.1f' % r['npeaks_med'], verdict))
    cand = [r for r in rows if r['rms'] > -45 and r['npeaks_med'] is not None
            and r['npeaks_med'] <= 2.5]
    print('\n结论：可从 %s 抽旋律'
          % (', '.join(r['stem'] for r in cand) if cand
             else '**没有任何一条**（这条参考曲没有可抽的单旋律层）'))
    if a.json:
        json.dump(rows, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return 0


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                          # noqa: BLE001
        pass
    sys.exit(main())
