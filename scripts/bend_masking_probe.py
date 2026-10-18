#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bend_masking_probe.py —— **复杂编配下弯音（pitch bend）还能不能被听见**（2026-10-17）。

## 为什么要有这个工具

交接 `HANDOFF-A1-20261017.md` §6 项 5 留的问题是："复杂编配下弯音是否被掩蔽：只测了单乐器长音"。
`automation.pitch_bends` 现在能写进 MIDI（1/16 拍插值），但**能不能听见**取决于那条轨在混音里
有没有被别的声部压住 —— 这是"写了却没用"的典型静默失效，光看 MIDI 事件数是看不出来的。

## 量法（**同一条曲子的两版之差**，不拿两次归一化渲染相减）

固定素材：Strings 长音 A3(57) 占 bar4-12，弯音在 bar6 抬到 +`depth` 半音、bar8 回 0。
做 **4 个渲染**：`solo_bend` / `solo_nobend` / `dense_bend` / `dense_nobend`
（dense = 同一轨 + 钢琴和弦 / 贝斯 / Pad / Arp 四轨）。

在弯音保持段（bar6-8）取**窄带**能量：
  `E220` = 220Hz±10（标称 A3）· `E247` = 弯音目标±10（+2 半音时 246.9Hz）
  `Δ247 = 10log10(E247_bend / E247_nobend)` —— 弯音把能量搬到目标频率上，这个数应该**变正**。

- **尺子自检（solo）**：`Δ247` 必须显著为正（>+3dB）而 `Δ220` 为负 → 音源真的把弯音渲染出来了。
  不成立就说明尺子不成立，后面的数字一律不看（PITFALLS 187②）。
- **掩蔽读数（dense）**：同样两个数。`Δ247` 掉到 ~0（<1.5dB）就是**被埋** ——
  混音里那个频率上的能量没变，耳朵自然听不出弯音。
- 辅以 `librosa.pyin` 的 F0 轨迹（solo 能像地跟出音高；dense 上跟不出来也支持"被埋"这个结论）。

## 用法

```powershell
& $py scripts\\bend_masking_probe.py                # +2 半音（默认）
& $py scripts\\bend_masking_probe.py --depth 0.4    # 浅弯音
& $py scripts\\bend_masking_probe.py --keep          # 保留临时目录（排查用）
```

只写系统 TEMP，**不碰曲库**。`--depth` 跑几档就能看出"多浅的弯音在这种编制下就废了"。
"""
import argparse
import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PY = sys.executable
BPM = 120.0
BAR = 4.0
BARS = 12
PLATEAU = (24.0 * 60.0 / BPM, 32.0 * 60.0 / BPM)     # bar6..8（弯音保持段，秒）


def _note(bar, beat, dur, pitch, vel):
    return [bar, beat, dur, pitch, vel]


def build_song(depth=2.0, with_bend=True, dense=False):
    """→ song.json 数据。`with_bend=False` 时同一素材去掉弯音（唯一变量）。"""
    bend = max(-8191, min(8191, int(round(4096.0 * float(depth)))))   # 1 半音 = 4096
    target = [_note(4, 0.0, 8.0, 57, 100), _note(8, 0.0, 8.0, 57, 100)]
    d = {
        'name': 'bend_probe', 'bpm': BPM, 'meter': [4, 4], 'bar_beats': BAR,
        'chords': {'C': [48, [60, 64, 67]], 'Am': [45, [57, 60, 64]],
                   'F': [41, [53, 57, 60]]},
        'sections': [{'name': 'A', 'bars': BARS, 'chords': ['C'] * BARS, 'melody': 'm',
                      'arr': {'perc': 0}}],
        'melody': {'m': []},
        'programs': {'Strings': [48, 1], 'Piano': [0, 2], 'Bass': [33, 3],
                     'Pad': [89, 4], 'Arp': [4, 5]},
        'patterns': {'notes_extra_full': True, 'automation_step': 0.0625},
        'notes_extra': {'Strings': [list(x) for x in target]},
    }
    if with_bend:
        d['automation'] = {'Strings': {'pitch_bends': [[16.0, 0], [24.0, bend], [32.0, 0]]}}
    if dense:
        piano, bass, pad, arp = [], [], [], []
        prog = ['C', 'Am', 'F', 'C'] * 3
        pitches = {'C': [60, 64, 67], 'Am': [57, 60, 64], 'F': [53, 57, 60]}
        for b in range(BARS):
            ps = pitches[prog[b]]
            for k in range(4):
                for p in ps:
                    piano.append(_note(b, float(k), 0.9, p, 84))
                bass.append(_note(b, float(k), 0.45, 36 + (ps[0] % 12), 92))
                bass.append(_note(b, float(k) + 0.5, 0.4, 36 + (ps[0] % 12), 78))
            pad.append(_note(b, 0.0, 4.0, ps[0] + 12, 64))
            for k in range(8):
                arp.append(_note(b, k * 0.5, 0.45, ps[k % len(ps)] + 12, 70))
        d['notes_extra'].update({'Piano': piano, 'Bass': bass, 'Pad': pad, 'Arp': arp})
    return d


def _render(tmp, name, data):
    import io
    import json_io
    import song_engine
    from contextlib import redirect_stdout
    js = os.path.join(tmp, name + '.json')
    json_io.save(js, data)
    mid = os.path.join(tmp, name + '.mid')
    with redirect_stdout(io.StringIO()):
        song_engine.compose(js, mid, quiet=True)
    base = os.path.join(tmp, name + '_sf')
    # ⚠ `render_midi.py` 是"位置参数 + 手写 --开关"（没有 argparse），`--rms` 要跟值
    r = subprocess.run([PY, os.path.join(HERE, 'render_midi.py'), mid, base,
                        '--rms', '-16.9'],
                       capture_output=True, text=True, errors='replace')
    wav = base + '.wav'
    if r.returncode != 0 or not os.path.exists(wav):
        raise SystemExit('渲染失败（%s）：%s' % (name, (r.stderr or r.stdout)[-400:]))
    return wav


def _load(wav):
    import soundfile as sf
    y, sr = sf.read(wav, always_2d=True)
    return y.mean(axis=1), sr


def _narrow(y, sr, t0, t1, f0, half=10.0):
    """[t0,t1) 内 f0±half 的**线性功率**（Hann 窗 + FFT 求和）。"""
    seg = y[int(t0 * sr):int(t1 * sr)]
    if not len(seg):
        return 1e-20
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    freqs = np.fft.rfftfreq(len(seg), 1.0 / sr)
    m = (freqs >= f0 - half) & (freqs <= f0 + half)
    return float(np.sum(spec[m]))


def _db(x):
    return 10.0 * np.log10(max(float(x), 1e-30))


def _f0_median(wav, t0, t1):
    import librosa
    y, sr = _load(wav)
    seg = y[int(t0 * sr):int(t1 * sr)]
    f0, _v, _vo = librosa.pyin(seg, fmin=150.0, fmax=500.0, sr=sr,
                               frame_length=2048, hop_length=512)
    good = f0[np.isfinite(f0)]
    return float(np.median(good)) if good.size else None


def main():
    import cli_utf8 as _cu; _cu.setup()
    ap = argparse.ArgumentParser(description='量复杂编配下弯音是否被掩蔽')
    ap.add_argument('--depth', type=float, default=2.0, help='弯音深度（半音），默认 2')
    ap.add_argument('--keep', action='store_true', help='保留临时目录')
    a = ap.parse_args()

    depth = float(a.depth)
    if abs(depth) < 0.5:
        # 0.4 半音在 A3 上只挪 ≈5Hz，而窄带是 ±10Hz ⇒ 两段必然重叠，Δ220/Δ247 一起变正，
        # 读数没法解释（实测：0.4 半音时两者都是 +7.5dB）。尺子不适用就**说清**，别硬给结论。
        raise SystemExit('弯音深度 %.2f 半音太小：A3 上只挪 %.1fHz，而窄带是 ±10Hz，'
                         '两段会重叠、读不出掩蔽。请用 ≥0.5 半音（推荐 1~2）'
                         % (depth, 220.0 * (2 ** (depth / 12.0) - 1)))
    bend = max(-8191, min(8191, int(round(4096.0 * depth))))
    eff = bend / 4096.0                                 # **实际**深度（SMF 上限 2 个半音）
    if abs(eff - depth) > 1e-6:
        print('⚠ SMF 的弯音上限是 8191（≈2 个半音）：要的 %.2f 半音已夹到 %.2f 半音'
              % (depth, eff))
    depth = eff
    nominal = 440.0 * 2 ** ((57 - 69) / 12.0)          # A3 = 220Hz
    bent = nominal * 2 ** (depth / 12.0)
    print('弯音 %.2f 半音（%d）· A3 %.1fHz → 弯音目标 %.1fHz · 保持段 %.1f~%.1fs'
          % (depth, bend, nominal, bent, PLATEAU[0], PLATEAU[1]))

    tmp = tempfile.mkdtemp(prefix='bendmask_')
    try:
        jobs = [('solo_bend', build_song(depth, True, False)),
                ('solo_nobend', build_song(depth, False, False)),
                ('dense_bend', build_song(depth, True, True)),
                ('dense_nobend', build_song(depth, False, True))]
        wavs = {}
        for name, data in jobs:
            wavs[name] = _render(tmp, name, data)
            print('  渲染好 %s' % name)

        print('\n%-12s %9s %9s %10s' % ('版本', 'Δ220', 'Δ247±', 'F0(中位)'))
        res = {}
        for kind in ('solo', 'dense'):
            yb, sr = _load(wavs[kind + '_bend'])
            yn, _ = _load(wavs[kind + '_nobend'])
            d220 = _db(_narrow(yb, sr, *PLATEAU, nominal)) - _db(_narrow(yn, sr, *PLATEAU, nominal))
            d247 = _db(_narrow(yb, sr, *PLATEAU, bent)) - _db(_narrow(yn, sr, *PLATEAU, bent))
            f0 = _f0_median(wavs[kind + '_bend'], *PLATEAU)
            res[kind] = (d220, d247, f0)
            print('%-12s %+8.1f %+8.1f %9s'
                  % (kind, d220, d247, ('%.1fHz' % f0) if f0 else '量不到'))

        solo_d247 = res['solo'][1]
        dense_d247 = res['dense'][1]
        print('\n尺子自检：solo 的 Δ247 = %+.1f dB（> +3 才算音源真渲染了弯音）→ %s'
              % (solo_d247, '通过' if solo_d247 > 3.0 else '**不通过**'))
        if solo_d247 <= 3.0:
            print('结论：**尺子不成立** —— 先查音源/弯音范围，别用后面的数字判断掩蔽')
        elif dense_d247 < 1.5:
            print('结论：**被掩蔽** —— 混音里 247Hz 的能量几乎没动（Δ%+.1f dB），'
                  '弯音在这个编制下等于没写' % dense_d247)
        elif dense_d247 < 4.0:
            print('结论：**部分掩蔽** —— 弯音还在但只剩 %+.1f dB 的频谱位移' % dense_d247)
        else:
            print('结论：**没被掩蔽** —— 混音里弯音仍有 %+.1f dB 的频谱位移' % dense_d247)
        print('（Δ220 应为负：弯音把能量**搬离**标称音高；solo %+.1f / dense %+.1f dB）'
              % (res['solo'][0], res['dense'][0]))
        if a.keep:
            print('\n临时目录：%s' % tmp)
    finally:
        if not a.keep:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
