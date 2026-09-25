#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""bpm_fit.py —— **速度不是一个标量**：拍点证据 → 拟合 → 双闸门 → 半/双拍归一化。

## 为什么（2026-09-25，对标 `mason369/music-to-midi` 的 `telknet_beat_grid_v12`）

原来的 `metrics.detect_bpm` 只给**一个标量 + 两层自相关支持度**，**没有"这个读数可不可信"的判据**。
实测踩过：**连奏编配**下 106 BPM 被测成 **154.3** —— 小节网格全错位，之后所有逐小节指标跟着错。

上游的做法不是"换个检测器"（它的 `beat_detector.py:26` 明写 *never use a second detector*），
而是把同一批拍点拿来做**可判定的校验**：

| 它的做法 | 位置 |
|---|---|
| 保留**拍点时间戳**（不只用 BPM 标量），两种拟合：原点约束最小二乘 / minimax | `telknet_beat_grid_v12.py:455-498` / `:501-549` |
| **双闸门**：最大偏差既不能超 3/8 拍、也不能超 40ms | `:33-34` / `:832-838` |
| 可疑时**降级 + 带数字的警告**，不硬给一个数 | `:839-868` |
| 半/双拍：八度家族选择 + 逐点倍频折叠 + 漏拍整数倍补齐 | `:177-229` / `telknet_tempo_map.py:94-102` |

⚠ **它那两个阈值绑定它 20ms 帧的检测器，不能照抄** —— 本工具用自己的材料标定（`--selftest`）。
⚠ 它与 `metrics.detect_bpm` 是**互补**关系：那个快、给候选；这个慢（要 beat tracking）、给**判据**。
   技能 §3-2 的"速度一律取自 MIDI"仍然成立 —— 本工具用于**没有 MIDI 时**判断音频测速可不可信。

## 用法

```bash
python scripts\bpm_fit.py <音频> [--hint 145.96]   # 拍点拟合 + 双闸门 + 家族歧义
python scripts\bpm_fit.py --selftest               # 尺子自检（正控 2 + 负控 2）
```
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()

import metrics

GATE_BEAT = 0.375          # 双闸门 A：最大偏差 ≤ 3/8 拍（上游口径）
GATE_MS = 40.0             # 双闸门 B：且 ≤ 40ms（上游口径）
FAMILY_K = ((0.25, '¼×'), (1 / 3.0, '⅓×'), (0.5, '½×'), (2 / 3.0, '⅔×'), (1.0, '×1'),
            (1.5, '×1.5'), (2.0, '×2'), (3.0, '×3'), (4.0, '×4'))
# ⚠ **×1.5 / ⅔× 这一档必须有**（2026-09-25 实测）：`siren_end2` 原曲真值 145.96，
#   而音频层拍点跟踪给 98.2 —— 两者是 **3:2**（145.96 = 98.2×1.5）而不是 2:1。
#   少了这档，hint 会被判成"完全不匹配（差 47 BPM）"，其实只是家族关系不同。


def beat_times(y, sr):
    """librosa 拍点跟踪 → (tempo 估计, 拍点秒数组)。**保留时间戳**，后面全靠它判可信度。"""
    import librosa
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units='time', trim=False)
    return float(np.atleast_1d(tempo)[0]), np.asarray(beats, dtype=float)


def fit_origin_ls(beats):
    """**原点约束最小二乘**：`beat[i] ≈ i·T`（固定原点）→ `T = Σ(i·t_i)/Σ(i²)`。"""
    i = np.arange(len(beats), dtype=float)
    T = float((i * beats).sum() / max(float((i * i).sum()), 1e-9))
    return 60.0 / max(T, 1e-9)


def fit_minimax(beats, bpm0, span=0.08, steps=1601):
    """**minimax 拟合**：在 bpm0±span 上最小化 `max|beat[i] − (t0 + i·T)|`（t0 = 首拍）。"""
    best = (bpm0, float('inf'))
    for bpm in np.linspace(bpm0 * (1 - span), bpm0 * (1 + span), steps):
        T = 60.0 / bpm
        dev = beats - (beats[0] + np.arange(len(beats)) * T)
        m = float(np.abs(dev).max())
        if m < best[1]:
            best = (float(bpm), m)
    return best


def deviations(beats, bpm):
    """→ (最大偏差[拍], 最大偏差[ms], 平均偏差[ms])"""
    T = 60.0 / max(bpm, 1e-9)
    dev = beats - (beats[0] + np.arange(len(beats)) * T)
    a = np.abs(dev)
    return float(a.max() / T), float(a.max() * 1000.0), float(a.mean() * 1000.0)


def gate(beats, bpm):
    """**双闸门**：两个条件**同时**满足才算"这层速度站得住"。"""
    mb, mms, ams = deviations(beats, bpm)
    return bool(mb <= GATE_BEAT and mms <= GATE_MS), mb, mms, ams


def families(bpm):
    """半/双拍家族：把候选按 ¼ ⅓ ½ ×1 ×2 ×3 ×4 展开（只留 30–300 BPM 的）。"""
    out = []
    for k, lab in FAMILY_K:
        b = bpm * k
        if 30.0 <= b <= 300.0:
            out.append((lab, b))
    return out


def analyze(path, hint=None, max_sec=None):
    """音频 → 结构化结论（**每个数字都可复核**）"""
    y, sr = metrics.read_audio(path, mono=True)
    y = np.asarray(y, dtype='float32')
    if max_sec:
        y = y[:int(max_sec * sr)]
    lib_tempo, beats = beat_times(y, sr)
    res = {'file': os.path.basename(path), 'sr': sr, 'duration': round(len(y) / float(sr), 2),
           'librosa_tempo': round(lib_tempo, 3), 'n_beats': int(len(beats))}
    if len(beats) < 4:
        res.update({'reliable': False, 'bpm': None,
                    'warning': '拍点只有 %d 个（<4）—— **判不了**，别拿任何数字当速度' % len(beats)})
        return res
    b_ls = fit_origin_ls(beats)
    b_mm, dev_mm = fit_minimax(beats, b_ls)
    ok, mb, mms, ams = gate(beats, b_mm)
    res.update({'bpm': round(b_mm, 3), 'bpm_origin_ls': round(b_ls, 3),
                'minimax_dev_ms': round(dev_mm * 1000.0, 2),
                'gate_ok': ok, 'max_dev_beat': round(mb, 4), 'max_dev_ms': round(mms, 2),
                'mean_dev_ms': round(ams, 2)})
    # 半/双拍：**每个家族各自过一遍闸门** —— 都过 = 有歧义，必须显式选一层
    fam = []
    for lab, b in families(b_mm):
        o, f_mb, f_mms, f_ams = gate(beats, b)
        fam.append({'label': lab, 'bpm': round(b, 3), 'gate_ok': o,
                    'max_dev_ms': round(f_mms, 2)})
    res['families'] = fam
    passing = [f for f in fam if f['gate_ok']]
    res['families_passing'] = [f['label'] for f in passing]
    if hint:
        # 给了已知 BPM 就**直接验它**：hint 这一层的闸门过不过、与拟合值差多少
        o_h, h_mb, h_mms, h_ams = gate(beats, float(hint))
        ratio = float(hint) / max(b_mm, 1e-9)
        near = min(FAMILY_K, key=lambda kv: abs(kv[0] - ratio))
        res['hint'] = {'bpm': float(hint), 'gate_ok': o_h, 'max_dev_beat': round(h_mb, 4),
                       'max_dev_ms': round(h_mms, 2), 'ratio_vs_fit': round(ratio, 3),
                       'nearest_family': near[1] if abs(near[0] - ratio) < 0.12 else None,
                       'delta_vs_fit': round(float(hint) - b_mm, 3)}
    if not ok:
        res['reliable'] = False
        res['warning'] = ('**恒速假设不成立**：最大偏差 %.0fms > 闸门 %.0fms（%.3f 拍 > %.3f 拍）'
                          '—— 可能是变速曲，或拍点被连奏/弱起音带偏；'
                          '**别拿 %.1f 当速度**' % (mms, GATE_MS, mb, GATE_BEAT, b_mm))
    elif len(passing) > 1:
        res['reliable'] = True
        res['warning'] = ('有**半/双拍歧义**：%s 这几个家族的闸门都过 —— '
                          '必须显式选一层并写进画像（技能 §3-2），别两边各选一层'
                          % '、'.join(f['label'] for f in passing))
    else:
        res['reliable'] = True
        res['warning'] = None
    return res


def report(r, out=print):
    out('文件: %s（%.1fs · %d 拍点）' % (r['file'], r['duration'], r['n_beats']))
    if r.get('bpm') is None:
        out('  ⚠ %s' % r['warning'])
        return
    out('  librosa 原始估计 : %.2f BPM' % r['librosa_tempo'])
    out('  原点约束最小二乘 : %.3f BPM' % r['bpm_origin_ls'])
    out('  minimax 拟合     : **%.3f BPM**（最大残差 %.2fms）' % (r['bpm'], r['minimax_dev_ms']))
    out('  双闸门           : %s（最大偏差 %.3f 拍 / %.1fms · 均值 %.1fms）'
        % ('**过**' if r['gate_ok'] else '**不过**', r['max_dev_beat'], r['max_dev_ms'],
           r['mean_dev_ms']))
    out('  家族             : ' + ' · '.join(
        '%s=%.1f%s' % (f['label'], f['bpm'], '✓' if f['gate_ok'] else '') for f in r['families']))
    h = r.get('hint')
    if h:
        out('  已知 BPM %.2f    : %s（偏差 %.3f 拍 / %.1fms · 与拟合差 %+.2f BPM，比值 %.3f%s）'
            % (h['bpm'], '**过**' if h['gate_ok'] else '**不过**',
               h['max_dev_beat'], h['max_dev_ms'], h['delta_vs_fit'], h['ratio_vs_fit'],
               (' = %s 家族' % h['nearest_family']) if h.get('nearest_family') else ''))
    if r['warning']:
        out('  → %s' % r['warning'])
    else:
        out('  → 单一家族过闸门，速度可信')


def selftest(verbose=True):
    """**尺子先拿已知答案跑**（技能 §10 第 4 条）。正控 2 + 负控 2。"""
    sr = 22050
    got = []

    def _mk(period_s, dur=12.0, decay=8.0, jitter=0.0, seed=0):
        t = np.arange(int(sr * dur)) / sr
        y = np.zeros_like(t)
        rng = np.random.RandomState(seed)
        k = 0.0
        while k < dur:
            i = int((k + (rng.randn() * jitter if jitter else 0.0)) * sr)
            if 0 <= i < len(y):
                seg = np.arange(min(int(sr * 0.25), len(y) - i)) / sr
                y[i:i + len(seg)] += np.sin(2 * np.pi * 110 * seg) * np.exp(-seg * decay)
            k += period_s
        return y

    # ① 正控：标准脉冲串 @145.96
    _tempo, beats = beat_times(_mk(60.0 / 145.9605), sr)
    b = fit_minimax(beats, fit_origin_ls(beats))[0]
    ok, mb, mms, _ = gate(beats, b)
    assert abs(b - 145.9605) < 1.0, '正控①：145.96 BPM 该测出 ±1 内，实得 %.2f' % b
    assert ok, '正控①：标准脉冲串该过闸门（%.0fms / %.3f 拍）' % (mms, mb)
    got.append('脉冲串 %.2f' % b)

    # ② 正控：**连奏型**（慢衰减长音、弱起音）—— 这正是我们踩过 106→154.3 的模式
    _t2, beats2 = beat_times(_mk(60.0 / 106.0, decay=1.2), sr)
    b2 = fit_minimax(beats2, fit_origin_ls(beats2))[0]
    ok2, _, mms2, _ = gate(beats2, b2)
    rel = abs(b2 - 106.0) / 106.0
    assert rel < 0.06 or abs(b2 - 212.0) / 212.0 < 0.06 or abs(b2 - 53.0) / 53.0 < 0.06, \
        '正控②：连奏型该测到 106 的某一层（×1/×2/½），实得 %.2f' % b2
    got.append('连奏型 %.2f(过闸门=%s)' % (b2, ok2))

    # ③ 负控：白噪声 —— 必须判"不可靠"，不许给一个像样的 BPM
    rng = np.random.RandomState(7)
    _t3, beats3 = beat_times(rng.randn(sr * 8).astype('float32') * 0.3, sr)
    b3 = fit_minimax(beats3, fit_origin_ls(beats3))[0] if len(beats3) >= 4 else 120.0
    ok3, _, mms3, _ = gate(beats3, b3) if len(beats3) >= 4 else (True, 0, 0, 0)
    assert not ok3, \
        '负控③：白噪声**不该**过闸门（实得 %.2f BPM 却过了，最大偏差 %.0fms）—— 尺子太松' % (b3, mms3)
    got.append('白噪声不过闸门(%.0fms)' % mms3)

    # ④ 负控：拍点太少 —— analyze 必须走"判不了"分支，不许硬给数字
    import tempfile
    import soundfile as _sf
    _d = tempfile.mkdtemp(prefix='bpm_fit_st_')
    _p = os.path.join(_d, 'tiny.wav')
    _sf.write(_p, np.zeros(int(sr * 0.15), dtype='float32'), sr)
    r_tiny = analyze(_p)
    assert r_tiny.get('reliable') is False and r_tiny.get('bpm') is None \
        and '判不了' in (r_tiny.get('warning') or ''), \
        '负控④：拍点不足时必须明确"判不了"，实得 %r' % r_tiny.get('warning')
    got.append('拍点不足 → 判不了')

    if verbose:
        print('bpm_fit 自检 PASS：' + ' · '.join(got))
    return True


def main():
    ap = argparse.ArgumentParser(description='拍点拟合 + 双闸门 + 半/双拍归一化')
    ap.add_argument('audio', nargs='?')
    ap.add_argument('--hint', type=float, default=None, help='已知 BPM（用于对照）')
    ap.add_argument('--max-sec', type=float, default=None, help='只分析前 N 秒')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not a.audio:
        ap.print_help()
        return 1
    r = analyze(a.audio, a.hint, a.max_sec)
    if a.json:
        import json
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        report(r)
    return 0 if r.get('reliable') else 2


if __name__ == '__main__':
    sys.exit(main())
