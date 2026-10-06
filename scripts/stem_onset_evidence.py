#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""stem_onset_evidence.py —— **逐音量"这个音有冲击/起音支撑吗"**（假音治理 B 的取证工具）

## 与 A（`stem_note_evidence.py`）的分工

A 量的判据是"该时刻该音高带**有没有能量**"；实测（BGM35 / あまくて）**能量判据几乎不涨分**
（门放到 0.25 才 +3pt）—— 因为假音**在音高带上是有能量的**（乐器真的在响），错的是
**"这一刻该不该有这个音"**。B 换一个维度：**起音/冲击支撑**。

两条判据（都按 `preflight.py` ⑤ 的既有口径，别再自创）：
1. **3–8kHz 抬升**：`post(起音后 0–46ms 均值) / pre(起音前 70ms 最小值)` 的 dB 值
   > `--rise-db`（默认 6.0）算这一下有冲击；
2. **onset_strength 局部峰**：`librosa.onset.onset_strength`（hop 10ms，照 `PITFALLS` **334**
   的读法：**不要**开 `normalize`），该音起音 ±30ms 内有没有局部极大值。

⚠ **两条都只看"全混音"**：Demucs 的鼓分轨给真鼓花"支撑率 0.00"（`preflight` 纪律 2 实测），
拿分轨当判据会**误删真鼓**。分轨那条只作为**旁证**印出来，不当门。

## 用法

```powershell
$py = "<工具链>\.venv-ml\Scripts\python.exe"
& $py scripts\stem_onset_evidence.py songs\<曲>\song.json --ref <原曲.wav> `
      [--tracks Drums] [--rise-db 6] [--sweep 3,6,9,12] [--json <读数.json>]
& $py scripts\stem_onset_evidence.py --selftest
```

**默认只读**：不改 `song.json`；拟剔名单交给 `filter_song_by_evidence.py --from-onset` 应用。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()          # noqa: E402

SONGS = os.path.join(ROOT, 'songs')
SR, HOP = 22050, 512
BAND_LO, BAND_HI = 3000.0, 8000.0
NPOST, NPRE = 2, 3           # 起音后 46ms 均值 / 起音前 70ms 最小值（与 preflight ⑤ 一致）
PEAK_WIN_SEC = 0.030         # onset_strength 局部峰的搜索半径


def rise_curve(ref):
    """→ (每帧的 3–8kHz 能量, fps)（口径照 `preflight.check_drums`）"""
    import numpy as np
    import soundfile as sf
    import librosa
    y, sr = sf.read(ref, dtype='float32', always_2d=True)
    y = y.mean(axis=1)
    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=HOP))
    fr = librosa.fft_frequencies(sr=SR, n_fft=1024)
    band = S[(fr >= BAND_LO) & (fr <= BAND_HI)].sum(axis=0)
    return band, HOP / float(SR), y


def onset_env(y):
    import numpy as np
    import librosa
    oe = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)   # ⚠ 不开 normalize
    return np.asarray(oe), HOP / float(SR)


def rise_at(band, fps, t, npost=NPOST, npre=NPRE):
    import numpy as np
    i = int(t / fps)
    post = band[i:i + npost]
    pre = band[max(0, i - npre):i]
    if not len(post) or not len(pre):
        return None
    p = float(post.mean())
    if p <= 1e-9:
        return None
    return 20 * np.log10(p / max(float(pre.min()), 1e-12))


def peak_at(oe, fps, t, win=PEAK_WIN_SEC, prom=1.5):
    """该时刻 ±win 内有**够凸**的局部极大值吗 → (有没有, 峰高/全局中位)。

    ⚠ 只判"局部极大"会**在底噪上处处为真**（自检当场抓到：静音处的音也报 `peak=True`
    —— 噪声里每个点几乎都是局部极大）。所以还要**相对本底凸起** `peak_h ≥ prom`
    （峰高 / 全曲 `onset_strength` 中位）。同族：`preflight` 纪律 2 的
    `normalize=True` 把静音区噪声归一化到 1.0 ⇒ 坏件假 PASS。
    """
    import numpy as np
    i = int(t / fps)
    lo, hi = max(1, i - int(win / fps)), min(len(oe) - 1, i + int(win / fps) + 1)
    if hi <= lo:
        return False, 0.0
    seg = oe[lo:hi]
    med = float(np.median(oe)) or 1.0
    k = int(np.argmax(seg))
    j = lo + k
    is_peak = oe[j] > oe[j - 1] and oe[j] >= oe[j + 1]
    h = float(oe[j]) / med
    return bool(is_peak and h >= prom), h


def analyse(song_json, ref, tracks=('Drums',), rise_db=6.0, sweep=(), peak_prom=1.5,
            verbose=True):
    import numpy as np
    band, fps, y = rise_curve(ref)
    oe, ofps = onset_env(y)
    d = json.load(open(song_json, encoding='utf-8'))
    ne = d.get('notes_extra') or {}
    bpm = float(d.get('bpm') or 120.0)
    spb = 60.0 / bpm
    bar_sec = spb * float(d.get('bar_beats') or 4.0)
    rows = []
    for tr, v in ne.items():
        lst = (v.get('notes') if isinstance(v, dict) else v) or []
        for k, x in enumerate(lst):
            t = float(x[0]) * bar_sec + float(x[1]) * spb
            r = rise_at(band, fps, t)
            pk, ph = peak_at(oe, ofps, t, prom=peak_prom)
            rows.append(dict(track=tr, idx=k, t=round(t, 3), pitch=int(x[3]),
                             rise=None if r is None else round(r, 2), peak=pk,
                             peak_h=round(ph, 2)))
    def stats(rise_thr):
        out = []
        for tr in sorted(set(r_['track'] for r_ in rows)):
            rs = [r_ for r_ in rows if r_['track'] == tr]
            ns = [r_ for r_ in rs if r_['rise'] is None]
            ok = [r_ for r_ in rs if r_['rise'] is not None and r_['rise'] > rise_thr]
            pk = [r_ for r_ in rs if r_['peak']]
            nosup = [r_ for r_ in rs if (r_['rise'] is None or r_['rise'] <= rise_thr)
                     and not r_['peak']]
            out.append(dict(track=tr, n=len(rs), unsup=len(nosup),
                            rate=100.0 * len(nosup) / max(1, len(rs)),
                            rise_ok=100.0 * len(ok) / max(1, len(rs)),
                            peak_ok=100.0 * len(pk) / max(1, len(rs)),
                            silent=int(len(ns)),
                            med_rise=float(np.median([r_['rise'] for r_ in rs
                                                      if r_['rise'] is not None] or [0.0]))))
        return out
    tbl = [s for s in stats(rise_db) if not tracks or s['track'] in tracks]
    sweep_tbl = []
    for th in sweep:
        t_ = [s for s in stats(th) if not tracks or s['track'] in tracks]
        sweep_tbl.append(dict(rise_db=th, unsup=sum(s['unsup'] for s in t_),
                              n=sum(s['n'] for s in t_),
                              by_track={s['track']: s['unsup'] for s in t_}))
    if verbose:
        print('=' * 78)
        print('逐音起音/冲击证据 · %s' % song_json)
        print('  对照 %s · 判据：3–8kHz 抬升 >%.1fdB **或** onset_strength 局部峰（±30ms）'
              % (os.path.basename(ref), rise_db))
        print('  ⚠ 只看全混音（Demucs 鼓分轨给真鼓花支撑率 0.00，拿它当门会误删真鼓）')
        print('-' * 78)
        print('  %-9s %6s %8s %9s %9s %9s %9s'
              % ('轨', '音数', '无支撑', '占比', '抬升过门', '有局部峰', '抬升中位'))
        for s in tbl:
            print('  %-9s %6d %8d %8.1f%% %8.1f%% %8.1f%% %8.1fdB'
                  % (s['track'], s['n'], s['unsup'], s['rate'], s['rise_ok'],
                     s['peak_ok'], s['med_rise']))
        if sweep_tbl:
            print('\n  门扫描（抬升门 → 无支撑音数）：')
            print('    %8s %8s %s' % ('门dB', '无支撑', '逐轨'))
            for s in sweep_tbl:
                print('    %8.1f %8d %s' % (s['rise_db'], s['unsup'],
                                            ' '.join('%s=%d' % (k, v)
                                                     for k, v in sorted(s['by_track'].items()))))
        print('  ⚠ 本工具**只读**；拟剔要下游按门应用（分段 + 备份 + 逐轨列明）')
    return dict(rows=rows, by_track=tbl, sweep=sweep_tbl,
                params=dict(rise_db=rise_db, band=(BAND_LO, BAND_HI), ref=ref,
                            hop=HOP, sr=SR, peak_win=PEAK_WIN_SEC))


def selftest():
    """判据自证：① 音频里有冲击 ⇒ 该时刻的音"有支撑"；② 静音时刻的音"无支撑"。"""
    import tempfile
    import numpy as np
    import soundfile as sf
    import json as _json
    ok = []
    d = tempfile.mkdtemp(prefix='soe_')
    sr = SR
    rng = np.random.RandomState(3)
    y = np.zeros(int(4 * sr), dtype='float32')
    # 2.0s 处放一次"冲击"（3–8kHz 带限噪声爆发）
    for t0 in (2.0, 2.2, 2.4, 2.6, 2.8):
        i = int(t0 * sr)
        k = int(0.05 * sr)
        burst = rng.randn(k) * np.hanning(k) * 0.5
        # 带通到 3–8kHz
        f = np.fft.rfft(burst)
        fr = np.fft.rfftfreq(k, 1.0 / sr)
        f[(fr < BAND_LO) | (fr > BAND_HI)] = 0
        y[i:i + k] += np.fft.irfft(f, n=k).astype('float32')
    # 底噪（不是全零，模拟真实录音底噪）
    y += (rng.randn(len(y)) * 1e-4).astype('float32')
    ref = os.path.join(d, 'ref.wav')
    sf.write(ref, y, sr)
    song = os.path.join(d, 'song.json')
    _json.dump(dict(bpm=60.0, notes_extra={'Drums': [
        [0, 2.0, 0.25, 38, 100],      # 正对冲击 ⇒ 有支撑
        [0, 3.5, 0.25, 38, 100],      # 静音处 ⇒ 无支撑
    ]}), open(song, 'w', encoding='utf-8'))
    rep = analyse(song, ref, tracks=('Drums',), rise_db=6.0, verbose=False)
    r0, r1 = rep['rows'][0], rep['rows'][1]
    ok.append(('冲击处的音：3–8kHz 抬升过门或检出局部峰',
               (r0['rise'] is not None and r0['rise'] > 6.0) or r0['peak']))
    ok.append(('静音处的音：无抬升且无局部峰', (r1['rise'] is None or r1['rise'] <= 6.0)
               and not r1['peak']))
    ok.append(('汇总里"无支撑"计数 = 1', rep['by_track'][0]['unsup'] == 1))
    print('  冲击处 rise=%s peak=%s · 静音处 rise=%s peak=%s'
          % (r0['rise'], r0['peak'], r1['rise'], r1['peak']))
    for nm, v in ok:
        print('  %-34s %s' % (nm, 'PASS' if v else 'FAIL'))
    print('stem_onset_evidence 自检 ' + ('PASS' if all(v for _n, v in ok) else 'FAIL'))
    return all(v for _n, v in ok)


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('song', nargs='?')
    ap.add_argument('--ref', help='原曲音频（判据只看全混音）')
    ap.add_argument('--tracks', default='Drums', help='要量哪些轨（逗号分隔）')
    ap.add_argument('--rise-db', type=float, default=6.0)
    ap.add_argument('--sweep', default='')
    ap.add_argument('--peak-prom', type=float, default=1.5,
                    help='局部峰要高过全曲 onset_strength 中位这么多倍才算"有起音"')
    ap.add_argument('--json', default=None)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not (a.song and a.ref):
        print(__doc__)
        return 1
    p = a.song if os.path.isfile(a.song) else os.path.join(SONGS, a.song, 'song.json')
    if not os.path.exists(p):
        print('找不到 %s' % p)
        return 1
    tracks = tuple(x.strip() for x in a.tracks.split(',') if x.strip())
    sweep = [float(x) for x in a.sweep.split(',') if x.strip()]
    rep = analyse(p, a.ref, tracks, a.rise_db, sweep, a.peak_prom)
    if a.json:
        json.dump(rep, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('已写 %s' % a.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
