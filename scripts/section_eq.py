#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""section_eq.py —— **段落级 EQ 自动化**（新能力）

为什么需要：参考曲的亮度**逐段变化**（实测 `しみじみゅどうふ` 的副歌比主歌亮 8.6dB，
`BGM16c` 的 5–10k 在副歌是 −44.5dB、尾奏 −56.1dB）。而我们的渲染链只有**全局** EQ
（`render_midi` 的 shelf/mid_db/low），所以"整曲一条曲线"必然在某些段偏亮、某些段偏暗。
实测 22 号：5–10k 比参考亮 +6.8dB，而参考那段本身是**极端滚降**（3–5k 就比中频低 41dB、
5–10k 低 60dB —— 接近"没有高频"）。

做法（**渲染后分段落套 EQ**，不动渲染内核）：
  1. 按 `song.json` 的段落边界把成品 WAV 切片
  2. 每片套一个**高搁架**（`high_shelf`，默认 6kHz 以上按 dB 升降）
  3. 段间用 40ms 交叉淡化拼接（避免爆音）
  4. 每片后做**峰值保护**（超 0.99 就整体回缩，不做限幅染色）

`<曲名>_sf.wav` 原地重写（先备份为 `_sf.preshelf.wav`），OGG 同步重编码。

用法:
  python scripts\\section_eq.py 20_d73_quiet_night            # 用 song.json 里的 eq_shelf 配置
  python scripts\\section_eq.py 20_d73_quiet_night --db -6     # 全场统一降 6dB（对比实验用）
  python scripts\\section_eq.py <曲名> --dry-run               # 只打印将要应用的曲线

配置写在 `song.json` 的段落里（与 `arr.mix` 同级）：
  {"name":"A", "eq_shelf": -3.0, ...}      # 该段 6kHz 以上降 3dB
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()

import numpy as np                            # noqa: E402
import soundfile as sf                        # noqa: E402

import json_io                                # noqa: E402
import render_midi                            # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SONGS = os.path.join(ROOT, 'songs')
FC = 6000.0            # 高搁架起始频率（5–10k 的主战场）
XFADE = 0.04           # 段间交叉淡化（秒）


def _shelf(x, sr, gain_db, fc=FC):
    """对整段套高搁架（复用 render_midi 的实现，保证与全局 EQ 同一口径）"""
    if not gain_db:
        return x
    return render_midi.high_shelf_np(x, sr, fc=fc, gain_db=gain_db)


def apply_section_eq(song_json, wav_path, ogg=True, force_db=None, verbose=True):
    """按段落套高搁架 EQ → 原地重写 wav（并重编 ogg）"""
    d = json_io.load(song_json)
    x, sr = sf.read(wav_path, dtype='float32', always_2d=True)
    n = len(x)
    beat = 4.0 * 60.0 / float(d.get('bpm') or 120.0)
    bounds, pos = [0], 0.0
    for s in d['sections']:
        pos += s['bars'] * beat
        bounds.append(min(int(round(pos * sr)), n))
    if bounds[-1] < n:
        bounds[-1] = n
    gains = [float((force_db if force_db is not None else s.get('eq_shelf', 0.0)) or 0.0)
             for s in d['sections']]
    if verbose:
        print('段落 EQ（%d Hz 以上，%s）:' % (int(FC), '统一值' if force_db is not None else '来自 song.json'))
        for s, g in zip(d['sections'], gains):
            print('  %-8s %+5.1f dB' % (s['name'], g))
    if not any(abs(g) > 1e-6 for g in gains):
        print('  所有段落都是 0dB —— 无需处理')
        return False
    out = np.array(x, dtype=np.float32)
    for i, (a, b) in enumerate(zip(bounds[:-1], bounds[1:])):
        if b <= a:
            continue
        out[a:b] = _shelf(out[a:b], sr, gains[i])
    # 段间交叉淡化：边界两侧各 xf 样本，用两段各自增益分别滤波再混合
    xf = int(XFADE * sr)
    for i in range(1, len(gains)):
        c = bounds[i]
        lo, hi = max(0, c - xf), min(n, c + xf)
        if hi <= lo or bounds[i - 1] >= lo or bounds[i + 1] <= hi:
            continue
        w = np.linspace(0.0, 1.0, hi - lo, dtype=np.float32)[:, None]
        left = _shelf(x[lo:hi].copy(), sr, gains[i - 1])
        right = _shelf(x[lo:hi].copy(), sr, gains[i])
        out[lo:hi] = left * (1.0 - w) + right * w
    pk = float(np.max(np.abs(out)))
    if pk > 0.999:
        out *= 0.999 / pk
    sf.write(wav_path, out, sr)
    if verbose:
        print('  已写入 %s（峰值 %.4f）' % (os.path.basename(wav_path), float(np.max(np.abs(out)))))
    if ogg:
        try:
            render_midi.encode_ogg(os.path.splitext(wav_path)[0])
            if verbose:
                print('  已重编 OGG')
        except Exception as e:                                  # noqa: BLE001
            print('  （OGG 重编跳过: %s）' % type(e).__name__)
    return True


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        return 1
    name = args[0]
    folder = os.path.join(SONGS, name)
    song_json = os.path.join(folder, 'song.json')
    cfg_path = os.path.join(folder, 'render.json')
    cfg = json_io.load(cfg_path) if os.path.exists(cfg_path) else {}
    out_base = os.path.join(folder, cfg.get('out', name + '_sf'))
    wav = out_base + '.wav'
    if not os.path.exists(wav):
        print('找不到成品 WAV: %s（先跑 make_song.py）' % wav)
        return 1
    force = float(sys.argv[sys.argv.index('--db') + 1]) if '--db' in sys.argv else None
    if '--dry-run' in sys.argv:
        d = json_io.load(song_json)
        for s in d['sections']:
            print('  %-8s eq_shelf = %s' % (s['name'], s.get('eq_shelf', 0.0)))
        return 0
    return 0 if apply_section_eq(song_json, wav, ogg=True, force_db=force) else 0


if __name__ == '__main__':
    sys.exit(main())
