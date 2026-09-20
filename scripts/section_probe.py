#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分段体检：找出哪一段弱（响度/亮度/低频/宽度/起音密度）
用法: python section_probe.py <file> [小节长秒数] [歌曲目录]

段落地图（2026-09-20 修，实测踩过）：
  原来 `SECS` 是**按某首 40 小节曲子硬编码**的，换曲子直接打印
  「!! 段落地图不适用：SECS 需要 10060.0s，文件只有 251.5s」——等于对任何
  其它曲子都不可用（实测写钢琴曲时被迫另写一个脚本）。
现在优先顺序：① `<歌曲目录>/song.json` 的 sections（最准：段名/段数/BPM 全来自数据）
  ② 命令行的「小节长秒数」+ 默认 6 段 ③ 都没有才退回旧硬编码 SECS。
"""
import json
import os
import sys

import numpy as np

SECS = [('Intro', 0, 4), ('A', 4, 12), ('B', 12, 20), ("A'", 20, 28),
        ("B'", 28, 36), ('Outro', 36, 40)]


def build_map(song_json=None, bar=None):
    """段落地图：返回 [(段名, 起始小节, 结束小节)] 与每小节秒数。

    给了 `song.json` 就**完全按数据**算（BPM 也从里面读，不再要求手给 bar）——
    这样 `section_probe.py <曲目录>/xxx_sf.wav <曲目录>/song.json` 一步到位。
    """
    if song_json and os.path.exists(song_json):
        d = json.load(open(song_json, encoding='utf-8'))
        spb = 60.0 / float(d['bpm'])
        mt = d.get('meter') or [4, 4]
        bsec = float(mt[0]) * 4.0 / float(mt[1]) * spb
        out, b0 = [], 0
        for s in d.get('sections') or []:
            n = int(s.get('bars') or 0)
            out.append((str(s.get('name') or '?'), b0, b0 + n))
            b0 += n
        if out:
            return out, bsec
    if bar:
        return SECS, float(bar)
    return SECS, 1.6



def avg_spectrum(m, sr, n=8192):
    """整段平均频谱（单窗口测亮度会被随机相位骗，必须在段内多帧平均）"""
    win = np.hanning(n)
    acc = None
    cnt = 0
    for i in range(0, max(1, len(m) - n), n // 2):
        s = np.abs(np.fft.rfft(m[i:i + n] * win))
        acc = s if acc is None else acc + s
        cnt += 1
    if acc is None:
        acc = np.abs(np.fft.rfft(np.pad(m, (0, n - len(m)))[:n] * win))
        cnt = 1
    return acc / max(1, cnt)


def band(S, sr, lo, hi):
    f = np.fft.rfftfreq((len(S) - 1) * 2, 1 / sr)
    k = (f >= lo) & (f <= hi)
    return 20 * np.log10(max(1e-12, np.sqrt((S[k] ** 2).mean())))


def main(path, bar=None, song_json=None):
    x, sr = metrics.read_audio(path, dtype='float32')
    dur = len(x) / sr
    secs, bar = build_map(song_json, bar)
    need = secs[-1][2] * bar
    if need > dur + 0.5:
        print('!! 段落地图不适用：需要 %.1fs，文件只有 %.1fs\n'
              '   给一个 song.json（段名/小节数/BPM 从数据来）或命令行的小节秒数。'
              % (need, dur))
        return
    print('%-7s %7s %8s %8s %10s %7s %8s' %
          ('段落', 'RMS', '质心Hz', '6-16k', '低频40-160', '宽度', '起音/秒'))
    if song_json:
        print('  (段落地图来自 %s：%d 段 × %.3fs/小节)'
              % (os.path.relpath(song_json), len(secs), bar))
    else:
        print('  (段落地图：命令行 %.3fs/小节 + 默认 6 段；给 song.json 更准)' % bar)
    for name, b0, b1 in secs:
        seg = x[int(b0 * bar * sr):int(b1 * bar * sr)]
        if len(seg) < 4096:
            continue
        m = seg.mean(axis=1)
        rms = 20 * np.log10(max(1e-9, np.sqrt((m ** 2).mean())))
        S = avg_spectrum(m, sr)
        f = np.fft.rfftfreq((len(S) - 1) * 2, 1 / sr)
        cen = float((S * f).sum() / max(1e-9, S.sum()))
        mid = (seg[:, 0] + seg[:, 1]) / 2
        side = (seg[:, 0] - seg[:, 1]) / 2
        w = float(np.sqrt((side ** 2).mean()) / max(1e-9, np.sqrt((mid ** 2).mean())))
        env = np.abs(m)
        hop = int(sr * 0.01)
        fl = np.maximum(np.diff(env), 0)
        fe = np.array([fl[i:i + hop].sum() for i in range(0, len(fl) - hop, hop)])
        ons = int((fe > fe.mean() + 2 * fe.std()).sum()) / (len(m) / sr)
        print('%-7s %7.1f %8.0f %8.1f %10.1f %7.3f %8.1f'
              % (name, rms, cen, band(S, sr, 6000, 16000),
                 band(S, sr, 40, 160), w, ons))


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
import metrics      # noqa: E402  # 统一音频读取（含 ffmpeg 兜底）


def _cli(argv):
    """命令行：`<音频> [小节秒数 | song.json]`。

    第 2 个参数是**数字**时 = 用户显式指定小节秒数 → **不再自动找 song.json**
    （否则显式值会被同目录的 song.json 静默盖掉，实测就是这么踩的：
    `section_probe.py x.wav 3.478` 打出来的还是 song.json 的地图）。
    不给第 2 个参数时才自动找同目录的 song.json。
    """
    path = argv[1]
    bar = sj = None
    for a in argv[2:]:
        try:
            bar = float(a)
        except ValueError:
            sj = a
    if sj is None and bar is None:
        cand = os.path.join(os.path.dirname(os.path.abspath(path)), 'song.json')
        if os.path.isfile(cand):
            sj = cand
    return path, bar, sj


if __name__ == '__main__':
    main(*_cli(sys.argv))
