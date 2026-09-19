#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参考曲和声/结构分析：节拍跟踪 → 逐小节 chroma → 和弦模板匹配 → 结构段落

输出：估计速度、每小节和弦（含相对调性的罗马数字）、每小节起音密度与响度、
      以及按和声变化/响度切出的段落边界。

用法: python analyze_chords.py <file> [--bars N]
"""
import math
import sys

import numpy as np
import soundfile as sf

NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def load_mono(path):
    x, sr = metrics.read_audio(path, dtype='float32')
    return x.mean(axis=1), sr


def flux_env(m, sr, hop=512, n=2048):
    win = np.hanning(n)
    frames = []
    for i in range(0, len(m) - n, hop):
        frames.append(np.abs(np.fft.rfft(m[i:i + n] * win)))
    F = np.array(frames)
    d = np.diff(F, axis=0)
    d = np.maximum(d, 0).sum(axis=1)
    return d, hop / sr


def estimate_tempo(flux, dt, lo=60.0, hi=170.0):
    f = flux - flux.mean()
    ac = np.correlate(f, f, 'full')[len(f) - 1:]
    ac /= max(1e-9, ac[0])
    lags = np.arange(int(0.25 / dt), min(len(ac) - 1, int(2.5 / dt)))
    best, best_score = None, -9
    for lag in lags:
        bpm = 60.0 / (lag * dt)
        while bpm < lo:
            bpm *= 2
        while bpm > hi:
            bpm /= 2
        # 加上倍频/半频的加权，避免选到 2 倍速
        s = ac[lag]
        for k in (2, 3, 4):
            j = lag * k
            if j < len(ac):
                s += 0.5 * ac[j] / k
            j2 = lag // k
            if j2 > 2:
                s += 0.35 * ac[j2] / k
        if s > best_score:
            best_score, best = s, bpm
    return best


def chroma_of(seg, sr, n=8192):
    """对数频率映射的 chroma（含谐波加权，减少八度误差）
    基准必须取 C（C4=261.6256Hz），否则音名会整体偏移"""
    if len(seg) < n:
        seg = np.pad(seg, (0, n - len(seg)))
    seg = seg[:n] * np.hanning(n)
    S = np.abs(np.fft.rfft(seg))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    ch = np.zeros(12)
    for h in range(1, 7):                      # 1-6 次谐波，权重递减
        w = 1.0 / h
        m = (freqs > 55 * h) & (freqs < 3000 * h)
        idx = np.where(m)[0]
        for k in idx:
            f = freqs[k] / h
            if f < 55:
                continue
            pc = int(round(12 * math.log2(f / 261.6256))) % 12
            ch[pc] += S[k] * w
    return ch / max(1e-9, np.linalg.norm(ch))


def chord_templates():
    t = {}
    for r in range(12):
        maj = np.zeros(12)
        maj[[r, (r + 4) % 12, (r + 7) % 12]] = [1.0, 0.85, 0.9]
        t[NAMES[r]] = maj
        mi = np.zeros(12)
        mi[[r, (r + 3) % 12, (r + 7) % 12]] = [1.0, 0.85, 0.9]
        t[NAMES[r] + 'm'] = mi
        s7 = np.zeros(12)
        s7[[r, (r + 4) % 12, (r + 7) % 12, (r + 10) % 12]] = [1.0, 0.8, 0.85, 0.6]
        t[NAMES[r] + '7'] = s7
        m7 = np.zeros(12)
        m7[[r, (r + 3) % 12, (r + 7) % 12, (r + 10) % 12]] = [1.0, 0.8, 0.85, 0.6]
        t[NAMES[r] + 'm7'] = m7
        sus = np.zeros(12)
        sus[[r, (r + 5) % 12, (r + 7) % 12]] = [1.0, 0.85, 0.9]
        t[NAMES[r] + 'sus4'] = sus
    return t


ROMAN = {0: 'I', 2: 'II', 4: 'III', 5: 'IV', 7: 'V', 9: 'VI', 11: 'VII',
         3: 'III', 8: 'VI', 10: 'VII', 1: 'II', 6: 'VII'}


def roman_of(root_pc, quality, key_pc):
    deg = (root_pc - key_pc) % 12
    base = {0: 'I', 1: 'bII', 2: 'II', 3: 'bIII', 4: 'III', 5: 'IV',
            6: '#IV', 7: 'V', 8: 'bVI', 9: 'VI', 10: 'bVII', 11: 'VII'}[deg]
    if quality == 'm':
        return base.lower()
    if quality == '7':
        return base + '7'
    if quality == 'm7':
        return base.lower() + '7'
    if quality == 'sus4':
        return base + 'sus4'
    return base


def main(path, force_bars=None, brief=False):
    # 面板守卫（硬形式）：没在跑就先拉起来 —— 见 scripts/studio_guard.py 顶部那段。
    try:
        import studio_guard
        studio_guard.ensure_panel()
    except Exception as _e:                                        # noqa: BLE001
        print('  （面板守卫跳过：%s）' % str(_e)[:80])
    m, sr = load_mono(path)
    dur = len(m) / sr
    flux, dt = flux_env(m, sr)
    bpm = force_bars if force_bars else estimate_tempo(flux, dt)
    beat = 60.0 / bpm
    bar = beat * 4
    nbars = int(dur / bar)
    print('文件 %s  %.1fs  速度 %.1f BPM  小节长 %.3fs  共 %d 小节'
          % (path.split('\\')[-1], dur, bpm, bar, nbars))
    # 半/倍速是本质歧义（60/75/80 vs 120/150/160 八度关系）：本文件的测速器与
    # metrics.detect_bpm 口径不同，两者差一倍时**不要各信一个** —— 明说"层级有争议"
    # 并指向能给出各层支持度的工具（check_audio.py）。
    if not force_bars:
        try:
            import metrics as _mx
            _m, _sr, _x = _mx.load(path)
            _b2, _peak, _info = _mx.detect_bpm(_m, _sr)
            _ratio = max(_b2, bpm) / max(1e-9, min(_b2, bpm))
            _hs, _ls = _info['level_hi_score'], _info['level_lo_score']
            # 判据是"**两个工具各选了一层**"（差一倍）——这才是要提醒的场景：
            # 一处按 80 切小节、另一处按 151 谈速度，谁都对、合起来会翻车。
            # 支持度只作为参考信息报出来，不做门槛（实测真正的歧义恰好是支持度不接近的那种）。
            if 1.85 < _ratio < 2.15:
                print('  ! 速度层级有歧义：本工具 %.1f，metrics 口径 %.1f'
                      '（支持度 高层级 %.3f / 低层级 %.3f）'
                      % (bpm, _b2, _hs, _ls))
                print('    -> check_audio.py <文件> 看各层支持度；定参考曲时用 --bpm 钉死一层。'
                      '下面小节与罗马数字按 %.1f 切' % bpm)
        except Exception:
            pass

    tmpl = chord_templates()
    key_chroma = np.zeros(12)
    prog, dens, loud = [], [], []
    for b in range(nbars):
        s = int(b * bar * sr)
        e = int(min(len(m), (b + 1) * bar * sr))
        seg = m[s:e]
        if len(seg) < 1024:
            break
        c = chroma_of(seg, sr)
        key_chroma += c
        best, score = None, -9
        for name, tpl in tmpl.items():
            denom = np.linalg.norm(tpl) * np.linalg.norm(c)
            sc = float(np.dot(c, tpl) / max(1e-9, denom))
            if sc > score:
                score, best = sc, name
        prog.append(best)
        fs = int(b * bar / dt)
        fe = int((b + 1) * bar / dt)
        dens.append(float((flux[fs:fe] > flux.mean() * 2).sum()))
        loud.append(20 * np.log10(max(1e-9, np.sqrt((seg ** 2).mean()))))

    # 调性：整曲 chroma 与大小调模板相关
    kc = key_chroma / max(1e-9, key_chroma.max())
    best_key = None
    for i in range(12):
        for scale, tag in ((MAJOR, 'maj'), (MINOR, 'min')):
            r = float(np.corrcoef(np.roll(kc, -i), scale)[0, 1])
            if best_key is None or r > best_key[0]:
                best_key = (r, NAMES[i], tag, i)
    _, keyname, kind, key_pc = best_key
    print('调性 %s %s (相关 %.2f)' % (keyname, kind, best_key[0]))

    if not brief:
        print('\n小节 | 和弦      | 罗马数字  | 起音数 | 响度dBFS')
    for b in range(len(prog)):
        if brief:
            continue
        ch = prog[b]
        if len(ch) > 1 and ch[1] in '#b':
            root = NAMES.index(ch[:2])
            quality = ch[2:]
        else:
            root = NAMES.index(ch[0])
            quality = ch[1:]
        print('%4d | %-9s | %-9s | %5d  | %7.1f'
              % (b + 1, ch, roman_of(root, quality, key_pc), dens[b], loud[b]))

    # 结构：响度或和声大变化处切段
    if not brief:
        print('\n段落边界（响度突变 >3dB 或连续两小节和弦相同处开始）:')
        prev = None
        for b in range(len(prog)):
            mark = []
            if b and abs(loud[b] - loud[b - 1]) > 3.0:
                mark.append('响度突变%.1fdB' % (loud[b] - loud[b - 1]))
            if prev and prog[b] == prev and (b < 2 or prog[b - 1] != prev):
                mark.append('进入稳定和弦 %s' % prog[b])
            if mark:
                print('  小节 %2d: %s' % (b + 1, ', '.join(mark)))
            prev = prog[b]


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
import metrics      # noqa: E402  # 统一音频读取（含 ffmpeg 兜底）
if __name__ == '__main__':
    argv = sys.argv[1:]
    bpm_force = None
    brief = '--brief' in argv
    if '--bpm' in argv:
        i = argv.index('--bpm')
        bpm_force = float(argv[i + 1])
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith('--')]
    if not args:
        print('用法: python scripts\\analyze_chords.py <音频文件> [--bpm N] [--brief]')
        raise SystemExit(2)
    main(args[0], bpm_force, brief)
