#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""add_sub_layer.py —— **低频补层工序**（用户 2026-10-06 定："以后有低于 50Hz 的都编排一下"）。

## 判据：什么时候该补

`rel = ref_bands['20-40'] − ref_bands['80-160']`（取自画像 `refs/<ref>.json`）：

| rel | 含义 | 动作 |
|---|---|---|
| **≥ −10 dB** | 原曲**有 50Hz 以下的内容** | **补 sub 层**（实测 psg23 −9.3 · psg29 −9.1） |
| < −10 dB | 原曲本来就没有低频 | **不补**（psg33 −20.1 · psg35 −17.0） |

阈值锚点来自 `imitate_ref.py`（BGM29 rel=−7.9 → 补；BGM35 rel=−18.2 → 不补）。

## 为什么必须"人工合成"

YourMT3 的输入特征写死 `f_min=50Hz` ⇒ 20–40Hz **物理听不见**（`RESTORE-METHOD.md` §2b）；
GM 音源里也没有能产生 sub 的音色 ⇒ 只能人工合成 —— 这属于**编配**，不是提取。

## 三步（2026-10-06 在 psg23 上跑通并收敛）

1. **合成 sub**：以 `song.json` 的 **Bass 轨音符**为准，生成**低八度纯正弦**（`f0/2`），
   8ms 起音 / 40ms 收尾 / 平台包络，幅度 `(vel/127)^1.5 × 0.5`。
2. **低通 + 标定**：sub 先低通 55Hz（只负责 50Hz 以下那层），迭代增益使 **20–55Hz 带差 ≤1dB**。
3. **逐带整形 + 顶格**：逐 1/3 倍频程把 **20–1000Hz** 拉到参考形状（±6dB 上限），
   **每轮把 RMS 拉回原值** —— 形状与响度必须解耦：不然整形顺带抬总能量（实测 +1.65dB）→
   峰值 1.209 → 归一化又整体降 1.5dB，把刚调好的相对关系整个平移掉。
   最后在不削波前提下把峰值顶到 0.995。

⚠ **别用 `band_match.py` 干这件事**：它做整体缩放，在这种"整片缺层"的曲子上会把还缺的带
   **压得更低**（psg23 实测 50–80Hz 从 −8.8 掉到 −10.6）。

## 用法

```bash
python scripts\add_sub_layer.py <曲名> --ref <参考音频> [--out <目录>] [--force]
python scripts\add_sub_layer.py --selftest            # 纯合成信号自检（不读任何曲目）
```

产物：`<out>/<曲名>_sub.wav` + `.ogg` + `<曲名>_sub.json`（判据 rel / sub 增益 / 逐带差 / 峰值）。
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

SR = 44100
LO_HZ, HI_HZ = 20.0, 80.0          # 判据/报告用的低频带
SUB_LP_HZ = 55.0                   # sub 只负责这一段
FIX_LO, FIX_HI = 20.0, 1000.0      # 逐带整形的频率范围
CENTERS = [20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250,
           315, 400, 500, 630, 800, 1000]
REL_THR = -10.0                    # rel ≥ 这个值 ⇒ 该补（见文件头表格）
MAX_GAIN_DB = 6.0
PEAK_CEIL = 0.995


def should_add_sub(ref_bands, thr=REL_THR):
    """→ `(该补?, rel)`。`rel = 20-40 − 80-160`（缺任一键时返回 `(None, None)` 不判）。"""
    try:
        rel = float(ref_bands['20-40']) - float(ref_bands['80-160'])
    except Exception:                                              # noqa: BLE001
        return None, None
    return (rel >= thr), rel


def read_ref_bands(ref_json):
    """从画像 json 读 `bands`；也接受直接给 bands 字典。"""
    if isinstance(ref_json, dict) and 'bands' in ref_json:
        return ref_json['bands']
    with open(ref_json, encoding='utf-8') as f:
        return json.load(f)['bands']


def bass_events(sj, folder, bpm):
    """取本曲的低音线 → `[(起秒, 时长秒, 音高, 力度), …]`。

    **还原曲**（有 `notes_extra['Bass']`）直接用它的音符；
    **生成/仿写曲**没有 `notes_extra`，改用引擎编配后的 `Bass` 事件（`build_events`）——
    这样本工序对两条路径都能用。⚠ 2026-10-06 实测：只认 `notes_extra` 时，
    对仿写曲会 `KeyError: 'notes_extra'` 直接崩（而"仿写曲要不要补 sub"和还原曲是同一个判据）。
    """
    ne = (sj.get('notes_extra') or {}).get('Bass')
    spb = 60.0 / float(bpm)
    if ne:
        notes = ne['notes'] if isinstance(ne, dict) else ne
        bb = float(sj.get('bar_beats') or 4.0)
        return [((float(n[0]) * bb + float(n[1])) * spb, float(n[2]) * spb,
                 int(n[3]), int(n[4])) for n in notes]
    import song_engine as SE
    data = SE.load(os.path.join(folder, 'song.json'))
    ev = SE.build_events(data)
    if not hasattr(ev, 'items'):
        ev = ev[0]
    B = SE.bar_beats(data)
    return [(float(t) * 60.0 / float(data.get('bpm') or bpm), float(d) * 60.0 / float(data.get('bpm') or bpm),
             int(m), int(v)) for (t, d, m, v) in ev.get('Bass', []) if v > 0]


def synth_sub(events, sr, n_samples):
    """低音事件（**秒**）→ 低八度正弦 sub（8ms 起音 / 40ms 收尾 / 平台包络）。"""
    out = np.zeros(int(n_samples), dtype=np.float64)
    a_n = max(1, int(sr * 0.008))
    r_n = max(1, int(sr * 0.040))
    for (t0, dur, pitch, vel) in events:
        i0 = int(t0 * sr)
        if i0 < 0 or i0 >= len(out):
            continue
        length = max(int(dur * sr), a_n + r_n + 1)
        i1 = min(len(out), i0 + length)
        k = np.arange(i1 - i0)
        f0 = 440.0 * 2 ** ((pitch - 24 - 69) / 12.0)      # 低两个八度：重心落到 25–50Hz
        if f0 < 15.0:                                      # 15Hz 以下不可听，别浪费能量
            continue
        env = np.ones(len(k))
        env[:a_n] = np.linspace(0, 1, a_n)
        env[-r_n:] *= np.linspace(1, 0, r_n)
        out[i0:i1] += (vel / 127.0) ** 1.5 * 0.5 * env * np.sin(2 * np.pi * f0 * k / sr)
    return out


def lowpass(x, sr, fc=SUB_LP_HZ):
    from scipy.signal import butter, sosfilt
    return sosfilt(butter(4, fc / (sr / 2), btype='low', output='sos'), x)


def band_db(y, sr, centers=None, n_fft=8192, hop=2048):
    """逐 1/3 倍频程带能量（dB）。"""
    import librosa
    S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop)) ** 2
    f = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    avg = S.mean(axis=1)
    cs = centers or CENTERS
    return {c: 10 * np.log10(float(avg[(f >= c / 2 ** (1 / 6.0)) & (f < c * 2 ** (1 / 6.0))].sum()) + 1e-12)
            for c in cs}


def band_sum_db(y, sr, lo, hi, n_fft=8192, hop=2048):
    import librosa
    S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop)) ** 2
    f = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    return 10 * np.log10(float(S[(f >= lo) & (f < hi)].sum()) + 1e-12)


def rms_db(y):
    return 20 * np.log10(float(np.sqrt(np.mean(np.asarray(y, dtype=np.float64) ** 2))) + 1e-12)


def calibrate_sub(y, sub, ref, sr, lo=LO_HZ, hi=SUB_LP_HZ, max_iter=8, tol=1.0):
    """迭代 sub 增益，使 `y + sub*g` 的 [lo,hi) 带能量对齐 ref。→ (mix, gain, 残差dB)"""
    e_ref = band_sum_db(ref, sr, lo, hi)
    gain, diff = 0.2, None
    for _i in range(max_iter):
        mix = y + sub * gain
        diff = band_sum_db(mix, sr, lo, hi) - e_ref
        if abs(diff) <= tol:
            return mix, gain, diff
        gain *= 10 ** (-diff / 20.0)
    return y + sub * gain, gain, diff


def shape_to_ref(y, ref, sr, max_iter=5, max_gain_db=MAX_GAIN_DB,
                 lo=FIX_LO, hi=FIX_HI, centers=None):
    """逐 1/3 倍频程把 `[lo,hi]` 拉到 ref 的形状；**每轮把 RMS 拉回起始值**（形状/响度解耦）。"""
    cs = centers or CENTERS
    rms0 = rms_db(y)
    Y = np.fft.rfft(y)
    freqs = np.fft.rfftfreq(len(y), 1.0 / sr)
    gain = np.ones(len(freqs))
    cur = np.asarray(y, dtype=np.float64)
    for _i in range(max_iter):
        b, br = band_db(cur, sr, cs), band_db(ref, sr, cs)
        rel = np.array([br[c] - b[c] for c in cs])
        rel = rel - float(np.median(rel))                 # ← 只取相对部分，不抬整体电平
        pts_c = [lo * 0.8] + [float(c) for c in cs] + [hi * 1.25]
        pts_g = [0.0] + list(np.clip(rel, -max_gain_db, max_gain_db)) + [0.0]
        g_db = np.interp(np.log(np.maximum(freqs, 1.0)), np.log(pts_c), pts_g,
                         left=0.0, right=0.0)
        gain *= 10 ** (g_db / 20.0)
        cur = np.fft.irfft(Y * gain, n=len(y))
        cur *= 10 ** ((rms0 - rms_db(cur)) / 20.0)        # ← 每轮拉回原响度
    return cur


def top_to_ceiling(y, ceil=PEAK_CEIL):
    """在不削波前提下把峰值顶到 `ceil`。"""
    peak = float(np.max(np.abs(y)))
    if peak <= 0 or peak >= ceil:
        return y, peak, 1.0
    return y * (ceil / peak), peak, ceil / peak


def process(song, ref_wav, out_dir=None, force=False, sr=SR):
    """主工序 → dict 报告（同时写 wav/ogg/json）。"""
    import soundfile as sf
    folder = os.path.join(ROOT, 'songs', song)
    sj = json.load(open(os.path.join(folder, 'song.json'), encoding='utf-8'))
    cfg_path = os.path.join(folder, 'render.json')
    cfg = json.load(open(cfg_path, encoding='utf-8')) if os.path.exists(cfg_path) else {}
    mine = os.path.join(folder, (cfg.get('out') or (song + '_sf')) + '.wav')
    if not os.path.exists(mine):
        raise SystemExit('找不到我方混音：%s（先 make_song）' % mine)
    ref_name = cfg.get('ref') or song
    ref_json = os.path.join(ROOT, 'refs', ref_name + '.json')
    rel = None
    if os.path.exists(ref_json):
        ok, rel = should_add_sub(read_ref_bands(ref_json))
        print('判据：画像 %s 的 rel(20-40 − 80-160) = %s ⇒ %s'
              % (ref_name, ('%.2f dB' % rel) if rel is not None else '缺键',
                 {True: '该补', False: '不该补（原曲本来没有低频）', None: '测不到'}[ok]))
        if ok is False and not force:
            raise SystemExit('判据说这首不该补（rel %.2f < %.0f）；确实要补加 --force'
                             % (rel, REL_THR))
    else:
        print('⚠ 找不到画像 %s —— 跳过判据（只做工序）' % ref_json)

    y, s0 = sf.read(mine, dtype='float32', always_2d=True)
    y = y.mean(axis=1).astype(np.float64)
    if s0 != sr:
        import librosa
        y = librosa.resample(y, orig_sr=s0, target_sr=sr)
    ref, s1 = sf.read(ref_wav, dtype='float32', always_2d=True)
    ref = ref.mean(axis=1)
    if s1 != sr:
        import librosa
        ref = librosa.resample(ref, orig_sr=s1, target_sr=sr)

    bpm = float(sj.get('bpm') or 120.0)
    ev = bass_events(sj, folder, bpm)
    if not ev:
        raise SystemExit('取不到低音线（既没有 notes_extra.Bass，引擎也没生成 Bass 事件）')
    sub = lowpass(synth_sub(ev, sr, len(y)), sr)
    print('低音 %d 个事件 → sub 层（低八度正弦，低通 %.0fHz）' % (len(ev), SUB_LP_HZ))

    mix, gain, diff = calibrate_sub(y, sub, ref, sr)
    print('sub 标定：gain %.4f ⇒ 20–%.0fHz 带差 %+.2f dB' % (gain, SUB_LP_HZ, diff))
    shaped = shape_to_ref(mix, ref, sr)
    out, peak0, scale = top_to_ceiling(shaped)
    b, br = band_db(out, sr), band_db(ref, sr)
    d = [b[c] - br[c] for c in CENTERS]
    print('整形后逐带差(dB)：%s' % ' '.join('%.0f%+.1f' % (c, x) for c, x in zip(CENTERS, d)))
    print('平均绝对差 %.2f dB（未扣整体偏移 %.2f dB）· 峰值 %.3f（顶格前 %.3f）'
          % (float(np.mean(np.abs(d))), float(np.median(d)),
             float(np.max(np.abs(out))), peak0))

    out_dir = out_dir or os.path.join(ROOT, 'songs', song)
    os.makedirs(out_dir, exist_ok=True)
    wav = os.path.join(out_dir, song + '_sub.wav')
    sf.write(wav, np.stack([out, out], 1).astype(np.float32), sr, subtype='PCM_16')
    ogg = os.path.join(out_dir, song + '_sub.ogg')
    rc = None
    try:
        import to_ogg
        exe = to_ogg._ffmpeg_exe()
        if exe:
            rc = subprocess.run([exe, '-y', '-hide_banner', '-loglevel', 'error',
                                 '-i', wav, '-c:a', 'libvorbis', '-q:a', '8',
                                 ogg]).returncode
    except Exception as e:                                          # noqa: BLE001
        print('⚠ ogg 编码跳过：%s' % str(e)[:80])
    rep = {'song': song, 'ref': ref_name, 'rel': rel,
           'sub_gain': gain, 'calib_diff_db': diff, 'peak_before_top': peak0,
           'top_scale': scale, 'avg_abs_band_diff_db': float(np.mean(np.abs(d))),
           'median_band_diff_db': float(np.median(d)),
           'bands_mine': b, 'bands_ref': br, 'bass_notes': len(ev),
           'wav': wav, 'ogg': ogg if rc == 0 else None}
    json.dump(rep, open(os.path.join(out_dir, song + '_sub.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('写 %s%s' % (wav, (' + ' + ogg) if rc == 0 else ''))
    return rep


def selftest():
    """纯合成信号自检（不读任何曲目/画像）：判据、sub 频率、整形收敛。"""
    import librosa
    sr = 22050
    # ① 判据：用四首实测值当夹具（正例该 True、负例该 False）
    cases = [(('20-40', -11.3), ('80-160', -2.0), True),      # psg23 rel −9.3
             (('20-40', -9.1), ('80-160', 0.0), True),        # psg29 rel −9.1
             (('20-40', -20.1), ('80-160', 0.0), False),      # psg33 rel −20.1
             (('20-40', -17.0), ('80-160', 0.0), False),      # psg35 rel −17.0
             (('20-40', -18.2), ('80-160', 0.0), False),      # imitate_ref 锚点 BGM35
             (('20-40', -7.9), ('80-160', 0.0), True)]        # 锚点 BGM29
    for (k1, v1), (k2, v2), want in cases:
        ok, rel = should_add_sub({k1: v1, k2: v2})
        assert ok is want, '判据错：rel %.1f 该判 %s，实得 %s' % (rel, want, ok)
    assert should_add_sub({'20-40': -5.0})[0] is None, '缺键时该返回 None 不判'
    # ② sub 合成：单音 A2(pitch 45) 低两个八度 ⇒ 期望基频 27.5Hz 附近
    n = sr * 2
    sub = synth_sub([(0.0, 2.0, 45, 100)], sr, n)
    S = np.abs(np.fft.rfft(sub * np.hanning(n)))
    f = np.fft.rfftfreq(n, 1.0 / sr)
    peak_hz = float(f[int(np.argmax(S))])
    want_hz = 440.0 * 2 ** ((45 - 24 - 69) / 12.0)
    assert abs(peak_hz - want_hz) < 2.0, \
        'sub 基频错：pitch 45 该 %.1fHz，实得 %.1fHz' % (want_hz, peak_hz)
    # ②b 低于可听下限的音必须被跳过（别拿 13Hz 去浪费能量）
    assert not np.any(synth_sub([(0.0, 2.0, 30, 100)], sr, sr)), \
        'pitch 30 低两个八度 = 12.2Hz，该被跳过（>15Hz 才生成）'
    # ③ 整形收敛：造一个"低频被压 −5dB"的信号（在 ±6dB 可修范围内），整形后带差应显著变小
    t = np.arange(sr * 3) / sr
    ref = sum(np.sin(2 * np.pi * fr * t) / (i + 1) for i, fr in enumerate((40, 80, 160, 400, 900)))
    R = np.fft.rfft(ref)
    fq = np.fft.rfftfreq(len(ref), 1.0 / sr)
    M = R.copy()
    M[fq < 100] *= 0.56                                # 低频 −5dB
    mine = np.fft.irfft(M, n=len(ref))
    before = band_db(mine, sr)
    refb = band_db(ref, sr)

    def rel_err(b, br):
        """扣掉整体偏移后的平均逐带误差 —— 这才是整形真正优化的量
        （整体电平由 `shape_to_ref` 里的"每轮拉回 RMS"那一步单独管）。"""
        d = np.array([b[c] - br[c] for c in CENTERS])
        return float(np.mean(np.abs(d - np.median(d))))

    d0 = rel_err(before, refb)
    fixed = shape_to_ref(mine, ref, sr, max_iter=5)
    after = band_db(fixed, sr)
    d1 = rel_err(after, refb)
    assert d1 < max(0.8, d0 * 0.35), \
        '整形没收敛：相对形状误差 %.2f → %.2f（该降到 0.8dB 以下或原值的 35%%）' % (d0, d1)
    # ④ 顶格不削波
    out, _p, _s = top_to_ceiling(np.ones(1000) * 0.3)
    assert abs(float(np.max(np.abs(out))) - PEAK_CEIL) < 1e-6, '顶格没顶到上限'
    print('自检 PASS：判据 6 例 · sub 基频 %.1fHz（目标 %.1f）· 整形 %.2f→%.2f dB · 顶格 %.3f'
          % (peak_hz, want_hz, d0, d1, PEAK_CEIL))
    return 0


def main():
    ap = argparse.ArgumentParser(description='低频补层工序（原曲有 50Hz 以下内容时补 sub 层）')
    ap.add_argument('song', nargs='?', help='曲名（读 songs/<曲名>/song.json 与 render.json）')
    ap.add_argument('--ref', help='原曲音频（参考）')
    ap.add_argument('--out', default=None, help='产物目录（默认写回曲目目录）')
    ap.add_argument('--force', action='store_true', help='判据说"不该补"时也照做')
    ap.add_argument('--selftest', action='store_true', help='只跑纯合成自检后退出')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.song or not a.ref:
        ap.print_help()
        return 1
    process(a.song, a.ref, a.out, a.force)
    return 0


import cli_utf8 as _cu                                                  # noqa: E402
_cu.setup()
if __name__ == '__main__':
    sys.exit(main())
