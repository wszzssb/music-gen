#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分轨客观体检：占用率 / 动态范围（Demucs 分轨逐声部对比）

上一轮这套指标是临时脚本算的（算完就删），导致"成绩单"无法复现。
这里把它固化 + 加自检（`selftest.py` 的 `stem_metrics_sanity`）。

两个指标（口径固定，改口径必须同步改自检）：
  占用率 = "有声帧"占比：把音频切帧做 rfft，对每个倍频程带求帧能量，
           阈值 = 该带**峰值** − 25dB，占比越高说明这个带"一直在响"。
  动态范围 = 峰值 − 25 分位（dB）：越高说明这个带"忽有忽无"。

用法:
  # 逐声部对比（参考分轨目录 vs 成品分轨目录）
  python scripts/stem_compare.py --ref stems/BGM33 --cur stems/htdemucs/pulse_sf
  # 只看总和（低频连续感主要看 drums+bass 之和，见 PITFALLS 坑 77）
  python scripts/stem_compare.py --ref stems/BGM33 --cur ... --sum drums+bass
  # 量单个文件/整混音
  python scripts/stem_compare.py --cur songs/14_d75_pulse/pulse_sf.wav
"""
import argparse
import os
import sys

import numpy as np
import soundfile as sf

BANDS = [(40, 80), (80, 160), (160, 315), (315, 630),
         (630, 1250), (1250, 2500), (2500, 5000), (5000, 10000)]
UNITS = ('40', '80', '160', '315', '630', '1250', '2500', '5000')
STEMS = ('drums', 'bass', 'other', 'vocals')
N = 4096          # 帧长 ~93ms
DROP_DB = 25.0    # 占用率阈值：峰值 −25dB


def find_file(d, stem):
    """在目录里找 <stem>.<ext>（flac/wav 都认）"""
    for ext in ('.flac', '.wav', '.ogg', '.mp3'):
        p = os.path.join(d, stem + ext)
        if os.path.exists(p):
            return p
    return None


def load(path, secs=40.0, start=0.0):
    x, sr = metrics.read_audio(path, dtype='float64')
    i0 = int(start * sr)
    x = x[i0:i0 + int(secs * sr)]
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    return x.mean(axis=1), sr


def bands_frame_db(mono, sr):
    """按帧算每个倍频程带的能量（dB），返回 (帧数, 8)"""
    rms = np.sqrt((mono ** 2).mean()) + 1e-12
    mono = mono / rms                      # 先归一，免得 dB 参考飘
    win = np.hanning(N)
    f = np.fft.rfftfreq(N, 1 / sr)
    idx = [np.where((f >= lo) & (f < hi))[0] for lo, hi in BANDS]
    rows = []
    for i in range(0, max(1, len(mono) - N), N // 2):
        sp = np.abs(np.fft.rfft(mono[i:i + N] * win)) ** 2
        # 带内能量和；空带（低频带在 4096 帧里 bin 很少）也至少取一个 bin
        rows.append([sp[j].sum() if len(j) else 0.0 for j in idx])
    e = 10 * np.log10(np.array(rows) + 1e-20)
    return e


def metrics(mono, sr):
    e = bands_frame_db(mono, sr)
    occ, dyn = [], []
    for b in range(len(BANDS)):
        col = e[:, b]
        pk = col.max()
        occ.append(100.0 * float((col > pk - DROP_DB).mean()))
        dyn.append(float(pk - np.percentile(col, 25)))
    return np.array(occ), np.array(dyn)


def mix_of(d, stems, secs, start=0.0):
    """把若干声部相加（长度取最短），返回 (mono, sr)"""
    acc, sr = None, None
    for s in stems:
        p = find_file(d, s)
        if not p:
            raise SystemExit('缺声部文件: %s/%s.*' % (d, s))
        m, sr = load(p, secs, start)
        acc = m if acc is None else acc[:len(m)] + m[:len(acc)]
    return acc, sr


def line(tag, occ, dyn):
    return '%-14s %s   | %s' % (
        tag,
        ' '.join('%4.0f' % v for v in occ),
        ' '.join('%3.0f' % v for v in dyn))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref', help='参考分轨目录（BGM33）')
    ap.add_argument('--cur', help='成品分轨目录，或单个音频文件')
    ap.add_argument('--sum', default='', help='相加的声部，如 drums+bass（逗号亦可）')
    ap.add_argument('--secs', type=float, default=40.0)
    ap.add_argument('--start', type=float, default=0.0, help='从第几秒开始量')
    a = ap.parse_args()
    if not a.ref and not a.cur:
        ap.print_help()
        return 1
    print('口径：占用率=帧能量>峰值-%.0fdB 的帧占比；动态=峰值-25分位；%g~%gs'
          % (DROP_DB, a.start, a.start + a.secs))
    print('%-14s %s   | 动态(dB)' % ('频带(Hz)',
                                     ' '.join('%4s' % u for u in UNITS)))

    def show(tag, path_or_dir):
        if os.path.isdir(path_or_dir):
            if a.sum:
                m, sr = mix_of(path_or_dir, [s for s in a.sum.replace(',', '+').split('+') if s],
                               a.secs, a.start)
                occ, dyn = metrics(m, sr)
                print(line(tag, occ, dyn))
                return occ, dyn
            out = []
            for s in STEMS:
                p = find_file(path_or_dir, s)
                if not p:
                    continue
                m, sr = load(p, a.secs, a.start)
                occ, dyn = metrics(m, sr)
                print(line('%s %s' % (tag, s), occ, dyn))
                out.append((s, occ, dyn))
            return out
        m, sr = load(path_or_dir, a.secs, a.start)
        occ, dyn = metrics(m, sr)
        print(line(tag, occ, dyn))
        return occ, dyn

    if a.ref:
        print('--- 参考 %s ---' % a.ref)
        r = show('参考', a.ref)
    if a.cur:
        print('--- 成品 %s ---' % a.cur)
        c = show('成品', a.cur)
    if a.ref and a.cur and not a.sum and isinstance(r, list) and isinstance(c, list):
        rd, cd = dict((s, (o, d)) for s, o, d in r), dict((s, (o, d)) for s, o, d in c)
        print('--- 差（成品−参考，占用率 百分点；正=更满）---')
        for s in STEMS:
            if s in rd and s in cd:
                d = cd[s][0] - rd[s][0]
                print('%-14s %s' % (s, ' '.join('%+4.0f' % v for v in d)))
    return 0


import cli_utf8 as _cu; _cu.setup()
import metrics      # noqa: E402  # 统一音频读取（含 ffmpeg 兜底）
if __name__ == '__main__':
    sys.exit(main())
