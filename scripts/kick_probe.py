#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""低频打击体检：一个鼓/贝斯音色在 40-160Hz 上能"垫"多久

背景：14_d75_pulse 与例曲 BGM33 对比时，唯一剩下的硬缺口是**低频连续性**
（全混音 40/80Hz 占用率 86/83% vs 95/95%）。`sf2_lib.py --drums` 显示
GeneralUser GS 的底鼓采样只有 0.14-0.18s，而 Tom 有 2.3s —— 所以要实测
"哪个音色在 40-160Hz 的尾巴最长、能量最足"，而不是靠猜。

做法：给每个候选写一小段 MIDI（单音、力度固定）→ FluidSynth 干声渲染（关混响，
测采样本体）→ 算 40-160Hz 带能量的包络，报告：
  tail   带能量落在"峰值-25dB"以上的总时长（ms）——能垫多久
  e40/80/160  三个倍频程带里的峰值能量（dB，相对最大值）——能量落在哪
  cen    该音色的频谱质心

用法:
  python scripts/kick_probe.py                       # 默认一套候选
  python scripts/kick_probe.py --kit 25 --notes 35,36
"""
import argparse
import os
import struct
import subprocess
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import render_midi as RM   # noqa: E402  (find_exe / find_sf2 / 频域滤波)

CAND = [(0, 35), (0, 36), (0, 41), (0, 43), (0, 45), (0, 47),
        (8, 35), (8, 36), (16, 35), (16, 36), (24, 35), (24, 36),
        (25, 35), (25, 36), (26, 35), (26, 36), (32, 36), (48, 41)]


def vlq(n):
    out = bytearray([n & 0x7F])
    n >>= 7
    while n:
        out.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out)


def one_note_midi(path, kit, note, vel=100, beats=4, div=480):
    ch = 9
    trk = vlq(0) + bytes([0xC0 | ch, kit])
    trk += vlq(0) + bytes([0x90 | ch, note, vel])
    trk += vlq(div * beats) + bytes([0x80 | ch, note, 0])
    trk += vlq(0) + b'\xFF\x2F\x00'
    data = b'MThd' + struct.pack('>IHHH', 6, 0, 1, div) + \
           b'MTrk' + struct.pack('>I', len(trk)) + trk
    open(path, 'wb').write(data)


def band_env_db(mono, sr, lo=40.0, hi=160.0, n=1024, hop=256):
    """40-160Hz 带能量的帧包络（dB）"""
    win = np.hanning(n)
    f = np.fft.rfftfreq(n, 1 / sr)
    sel = (f >= lo) & (f < hi)
    out = []
    for i in range(0, max(1, len(mono) - n), hop):
        sp = np.abs(np.fft.rfft(mono[i:i + n] * win)) ** 2
        out.append(sp[sel].sum())
    e = 10 * np.log10(np.array(out) + 1e-20)
    return e


def probe(exe, sf2, kit, note, tmp):
    mid = os.path.join(tmp, 'probe.mid')
    wav = os.path.join(tmp, 'probe.wav')
    one_note_midi(mid, kit, note)
    cmd = [exe, '-ni', '-r', '44100', '-F', wav, sf2, mid]
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-200:])
    x, sr = sf.read(wav, dtype='float64', always_2d=True)
    m = x.mean(axis=1)
    e = band_env_db(m, sr)
    if e.size == 0 or e.max() < -100:
        return None
    pk = e.max()
    live = np.where(e > pk - 25.0)[0]
    tail_ms = 1000.0 * (live[-1] - live[0] + 1) * 256 / sr if live.size else 0.0
    # 每个倍频程带里的能量峰（相对全曲最大）
    acc = np.abs(np.fft.rfft(m)) ** 2
    fr = np.fft.rfftfreq(len(m), 1 / sr)
    tot = acc.max() + 1e-20
    bands = []
    for lo, hi in ((40, 80), (80, 160), (160, 315), (315, 630)):
        sel = (fr >= lo) & (fr < hi)
        bands.append(10 * np.log10((acc[sel].max() if sel.any() else 0) / tot + 1e-20))
    cen = float((acc * fr).sum() / (acc.sum() + 1e-20))
    return dict(tail=tail_ms, bands=bands, cen=cen)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kit', type=int, help='只测这个 kit')
    ap.add_argument('--notes', help='逗号分隔的 note')
    a = ap.parse_args()
    cand = CAND
    if a.kit is not None:
        notes = [int(x) for x in (a.notes or '35,36').split(',')]
        cand = [(a.kit, n) for n in notes]
    exe, sf2 = RM.find_exe(), RM.find_sf2()
    tmp = os.path.join(os.environ.get('TEMP', '.'), 'kickprobe')
    os.makedirs(tmp, exist_ok=True)
    print('音源: %s' % os.path.basename(sf2))
    print('%-6s %-6s %8s  %s  %7s' % ('kit', 'note', '尾长ms', '40/80/160/315dB', '质心Hz'))
    rows = []
    for kit, note in cand:
        try:
            r = probe(exe, sf2, kit, note, tmp)
        except Exception as ex:
            print('%-6d %-6d  失败 %s' % (kit, note, ex))
            continue
        if not r:
            continue
        rows.append((r['tail'], kit, note, r))
        print('%-6d %-6d %8.0f  %s  %7.0f'
              % (kit, note, r['tail'],
                 ' '.join('%6.1f' % b for b in r['bands']), r['cen']))
    rows.sort(reverse=True)
    print('\n低频尾巴最长的前 5 个：')
    for tail, kit, note, r in rows[:5]:
        print('  kit %-3d note %-3d  %6.0f ms' % (kit, note, tail))
    return 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印会崩）
if __name__ == '__main__':
    sys.exit(main())
