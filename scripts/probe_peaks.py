#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_peaks.py —— **谱峰驱动**的逐小节扒谱（低音根音 / 和弦候选 / 亮度）。

为什么要再写一个（库里已有 analyze_chords.py 的 chroma 模板法、analyze_prog.py 的
根音锚定法）：8192 点 FFT 在 96kHz 下 bin 宽 11.7Hz —— **低频一个 bin 就跨半个半音**，
按"bin → 音级"直接归属会被低频泄漏带偏。实测：整首歌都被读成 C/C#/E/G#/A（假的和声表）。
本工具改用两步：
  ① **谱峰**（局部极大 + 抛物线插值）→ 映射音级 → chroma。峰才代表真实分音，泄漏不参与。
  ② 低音用**长窗（32768）40-170Hz 的峰**定根音（真基频），避开底鼓/宽带泄漏。
    21_g150_velvet 的和声表（Bb / C7sus4 / Dm7 / C#maj7 / D#6/9 / F7sus4 …）就是这么扒出来的，
    顺带发现"安静段低音在低八度、主体段在高八度"——这条是配方里最难猜的部分。

用法:
  python scripts\\probe_peaks.py <音频> [--bpm 150] [--bars 1-16] [--json]
  # 建议先 check_audio.py 确认速度层级，再 --bpm 钉死（否则小节网格整体错位）
产出: 逐小节 根音 / 低音峰(前3) / 音级占比(前5) / 根音锚定的和弦候选(前2)。
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import metrics          # noqa: E402
import cli_utf8 as _cu  # noqa: E402

NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
A4 = 440.0
# 和弦模板（音级相对根音）；名字与 selftest.parse_chord 保持一致的写法
TEMPLATES = {
    'maj': [0, 4, 7], 'min': [0, 3, 7], 'maj7': [0, 4, 7, 11], 'min7': [0, 3, 7, 10],
    '7': [0, 4, 7, 10], 'm7b5': [0, 3, 6, 10], 'dim': [0, 3, 6], 'sus4': [0, 5, 7],
    'sus2': [0, 2, 7], '6': [0, 4, 7, 9], 'm6': [0, 3, 7, 9], 'add9': [0, 2, 4, 7],
    'm9': [0, 3, 7, 10, 2], 'maj9': [0, 2, 4, 7, 11], '9': [0, 4, 7, 10, 2],
    '6/9': [0, 2, 4, 7, 9],
}


def f2midi(f):
    return 69 + 12 * np.log2(np.maximum(f, 1e-9) / A4)


def note_name(m):
    m = int(round(m))
    return '%s%d' % (NAMES[m % 12], m // 12 - 1)


def segment_spec(y, sr, t0, t1, nfft):
    """区间平均幅度谱（长窗、半窗重叠）"""
    a, b = max(0, int(t0 * sr)), min(len(y), int(t1 * sr))
    if b - a < nfft:
        a = max(0, b - nfft)
    seg = y[a:b]
    if len(seg) < nfft:
        seg = np.pad(seg, (0, nfft - len(seg)))
    step = nfft // 2
    n = max(1, (len(seg) - nfft) // step + 1)
    idx = np.arange(nfft)[None, :] + step * np.arange(n)[:, None]
    S = np.abs(np.fft.rfft(seg[idx] * np.hanning(nfft), axis=1)).mean(axis=0)
    return S, np.fft.rfftfreq(nfft, 1.0 / sr)


def find_peaks(S, f, fmin, fmax, n=6, rel_db=-25.0):
    """局部极大 + 抛物线插值 → [(频率, 幅度), …]（只取比带内峰值高 rel_db 的）"""
    lo, hi = int(fmin / (f[1] - f[0])), min(len(S) - 1, int(fmax / (f[1] - f[0])))
    seg = S[lo:hi]
    if not len(seg):
        return []
    ref = seg.max()
    out = []
    for i in range(1, len(seg) - 1):
        if seg[i] > seg[i - 1] and seg[i] >= seg[i + 1] and seg[i] > ref * 10 ** (rel_db / 20):
            a, b, c = seg[i - 1], seg[i], seg[i + 1]
            den = a - 2 * b + c
            off = 0.5 * (a - c) / den if abs(den) > 1e-12 else 0.0
            out.append((seg[i], (lo + i + off) * (f[1] - f[0])))
    out.sort(reverse=True)
    return out[:n]


def pick_root(low_peaks):
    """低音根音 = 40-170Hz 里最强峰的**音级**（没有峰返回 None）"""
    if not low_peaks:
        return None
    return int(round(f2midi(low_peaks[0][1]))) % 12


def chroma_of(S, f):
    """谱峰 → 12 音级能量（60-3000Hz；峰才代表真实分音）"""
    v = np.zeros(12)
    for mag, fq in find_peaks(S, f, 60.0, 3000.0, n=60, rel_db=-35.0):
        v[int(round(f2midi(fq))) % 12] += mag
    return v


def best_chords(chroma, root_pc, k=2):
    """**根音锚定**：只考虑根音等于低音音级的和弦模板，按余弦相似度排序"""
    c = chroma / max(chroma.max(), 1e-12)
    scored = []
    for name, iv in TEMPLATES.items():
        v = np.zeros(12)
        for i in iv:
            v[(root_pc + i) % 12] = 1.0
        v /= v.sum()
        sc = float((c * v).sum()) / (np.linalg.norm(v) * np.linalg.norm(c) + 1e-12)
        scored.append((round(sc, 2), name))
    scored.sort(reverse=True)
    return scored[:k]


def analyze(path, bpm=150.0, bars=None, low_nfft=32768, nfft=8192):
    """逐小节：RMS / 质心 / 低音峰 / 根音 / 和弦候选 / 音级占比"""
    m, sr, _x = metrics.load(path)
    bar = 4 * 60.0 / bpm
    nbars = int(len(m) / (bar * sr))
    rng = range(nbars) if not bars else range(max(0, bars[0] - 1), min(nbars, bars[1]))
    rows = []
    for b in rng:
        t0, t1 = b * bar, (b + 1) * bar
        S, f = segment_spec(m, sr, t0, t1, nfft)
        Sl, fl = segment_spec(m, sr, t0, t1, low_nfft)
        low = find_peaks(Sl, fl, 38.0, 170.0, n=3, rel_db=-30.0)
        root = pick_root(low)
        ch = chroma_of(S, f)
        cc = ch / max(ch.max(), 1e-12)
        top = sorted(range(12), key=lambda i: -cc[i])[:5]
        seg = m[int(t0 * sr):int(t1 * sr)]
        rows.append({
            'bar': b + 1,
            'rms': round(20 * np.log10(max(float(np.sqrt((seg ** 2).mean())), 1e-9)), 1),
            'centroid': metrics.centroid(seg, sr, S, f),
            'low': [(round(fq), round(f2midi(fq))) for _mag, fq in low],
            'root': root,
            'root_name': (NAMES[root] if root is not None else '?'),
            'chords': best_chords(ch, root) if root is not None else [],
            'chroma': [(NAMES[i], round(float(cc[i]), 2)) for i in top],
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('audio')
    ap.add_argument('--bpm', type=float, default=150.0)
    ap.add_argument('--bars', default=None, help='只分析 a-b 小节（如 1-16）')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    bars = None
    if a.bars:
        lo, hi = a.bars.split('-')
        bars = (int(lo), int(hi))
    rows = analyze(a.audio, a.bpm, bars)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    print('%s  %.1fBPM  小节 %.2fs' % (a.audio, a.bpm, 4 * 60.0 / a.bpm))
    print(' 小节  RMSdB  质心  低音峰(Hz)          | 根音 | 和弦候选        | 音级占比(前5)')
    for r in rows:
        print(' %3d  %6.1f %6.0f  %-20s | %-4s | %-15s | %s'
              % (r['bar'], r['rms'], r['centroid'],
                 ' '.join('%s(%d)' % (note_name(mi), fq) for fq, mi in r['low']),
                 r['root_name'],
                 ' '.join('%s%.2f' % (n, s) for s, n in r['chords']),
                 ' '.join('%s%.2f' % (n, v) for n, v in r['chroma'])))


_cu.setup()          # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    main()
