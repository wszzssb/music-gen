#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`report_sections.py` —— **逐段体检**：一张表把"哪一段不像、差在哪一层"列清楚。

## 为什么要它（用户 2026-09-25 口径）

> "**以后要全曲读的先看看要不要分段**"

同一条曲子的读数在段间常常**不是一个方向**。实测《......已至。》逐段量出来：
频谱质心差从引子的 **+43Hz** 一路到尾段的 **−944Hz**、chroma 从 **0.96** 到 **0.77** ——
而"全曲中位"把这**两头抹平**，会得出"整体差不多"的**假结论**（我照着它去做了整曲 EQ，
方向就是错的）。所以：**量之前先分段，默认逐段**；要给整曲读数必须明说理由。

⚠ 这条口径在 `SKILL.md` §20 只写了"**改**要分段"，"**读**"是我这次又踩的（当场被用户纠正）。

## 用法

```bash
python scripts/report_sections.py siren_end2                          # 只列我这边（RMS/轨构成）
python scripts/report_sections.py siren_end2 --ref <原曲.wav> --mine songs/siren_end2/siren_end2_sf.wav
python scripts/report_sections.py siren_end2 --ref <原曲.wav> --mine <我的.wav> --json out.json
```

输出（一段一行）：**时间 · 我/参考 RMS 与差 · chroma 相似度 · 质心（我/参考）·
最差 3 条倍频程带 · 我这段的主要轨**。
`--ref` 存在时才算 chroma/质心/带差（那些需要对照）；否则只列"我这边"的结构量。

⚠ 需要 librosa → 用 `.venv-ml`；本脚本用 `pyenv.ensure` **自动换解释器**，直接
`python scripts/report_sections.py …` 跑即可（见 PITFALLS 258）。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

BANDS = [(20, 40), (40, 80), (80, 160), (160, 315), (315, 630),
         (630, 1250), (1250, 2500), (2500, 5000), (5000, 10000), (10000, 18000)]


def section_spans(song, bpm=None):
    """`song.json` → **段边界（秒）** [b0, b1, …, bn]（n 段 → n+1 个边界）。

    ⚠ 抽成模块级函数是为了让守卫能**行为测试**它：边界少于 3 个 = 退化成了"整曲一段"，
    那正是本条口径要防的东西（`t_sections_not_whole_song`）。
    """
    bpm = float(bpm or song.get('bpm') or 120.0)
    bar = 4 * 60.0 / bpm
    out, acc = [0.0], 0
    for s in (song.get('sections') or []):
        acc += int(s.get('bars') or 0)
        out.append(acc * bar)
    return out


def load_song(name_or_path):
    p = name_or_path
    if not os.path.exists(p):
        p = os.path.join(os.path.dirname(HERE), 'songs', name_or_path, 'song.json')
    if not os.path.exists(p):
        raise SystemExit('找不到 song.json：%s' % name_or_path)
    return json.load(open(p, encoding='utf-8')), p


def main():
    ap = argparse.ArgumentParser(description='逐段体检（"读也要分段"的标准入口）')
    ap.add_argument('song', help='曲名或 song.json 路径')
    ap.add_argument('--ref', default=None, help='参考音频（原曲）；给了才算 chroma/质心/带差')
    ap.add_argument('--mine', default=None, help='我的音频（默认找 songs/<曲>/<曲>_sf.wav）')
    ap.add_argument('--mid', default=None, help='我的 MIDI（默认 songs/<曲>/<曲>.mid）—— 用来列逐段轨构成')
    ap.add_argument('--json', default=None, help='把逐段读数写成 json')
    a = ap.parse_args()

    song, sp = load_song(a.song)
    root = os.path.dirname(HERE)
    base = os.path.dirname(sp)
    name = os.path.basename(base)
    mine = a.mine or os.path.join(base, name + '_sf.wav')
    mid = a.mid or os.path.join(base, name + '.mid')

    import pyenv
    pyenv.ensure('librosa', '.venv-ml', '逐段体检要 librosa 算 chroma/质心/带能量')
    import numpy as np
    import librosa
    import soundfile as sf

    SR = 22050
    bounds = section_spans(song)
    names = [s['name'] for s in song['sections']]

    def load(p):
        y, sr = sf.read(p, always_2d=True)
        y = librosa.to_mono(y.T).astype(np.float32)
        return librosa.resample(y, orig_sr=sr, target_sr=SR) if sr != SR else y

    def rms_db(y, t0, t1):
        seg = y[int(t0 * SR):int(t1 * SR)]
        if seg.size == 0:
            return -120.0
        return 20 * np.log10(max(float(np.sqrt((seg ** 2).mean())), 1e-12))

    def band_db(y, t0, t1):
        seg = y[int(t0 * SR):int(t1 * SR)]
        if seg.size < 4096:
            return None
        S = np.abs(librosa.stft(seg, n_fft=8192, hop_length=2048)) ** 2
        fr = librosa.fft_frequencies(sr=SR, n_fft=8192)
        return np.array([10 * np.log10(max(float(S[(fr >= lo) & (fr < hi)].mean()), 1e-14))
                         for lo, hi in BANDS])

    b = load(mine) if os.path.exists(mine) else None
    r = load(a.ref) if (a.ref and os.path.exists(a.ref)) else None

    tracks = {}
    if os.path.exists(mid):
        import midi_file
        m = midi_file.import_midi(mid)
        spb = 60.0 / float(m.get('bpm') or 120.0)
        for t in m.get('tracks', []):
            ns = t.get('notes') or []
            if ns:
                tracks[str(t.get('name'))] = [float(x[0]) * spb for x in ns]

    rows = []
    head = '段     时间          RMS(我/参考/差)'
    if r is not None:
        head += '   chroma  质心(我/参考)      最差 3 带'
    head += '   我这段的主要轨'
    print(head)
    print('-' * len(head))
    for i, nm in enumerate(names):
        t0, t1 = bounds[i], bounds[i + 1]
        row = {'name': nm, 't0': round(t0, 2), 't1': round(t1, 2)}
        mb = rms_db(b, t0, t1) if b is not None else float('nan')
        rb = rms_db(r, t0, t1) if r is not None else float('nan')
        row.update(rms_mine=round(mb, 1), rms_ref=round(rb, 1) if r is not None else None)
        line = '%-5s %5.0f-%5.0fs  %6.1f/%6.1f/%+6.1f' % (nm, t0, t1, mb, rb, mb - rb)
        if r is not None and b is not None:
            ia, ib = int(t0 * SR), int(t1 * SR)
            if ib - ia > 8192:
                ca = librosa.feature.chroma_cqt(y=r[ia:ib], sr=SR, hop_length=1024)
                cb = librosa.feature.chroma_cqt(y=b[ia:ib], sr=SR, hop_length=1024)
                ca = ca / (np.linalg.norm(ca, axis=0, keepdims=True) + 1e-9)
                cb = cb / (np.linalg.norm(cb, axis=0, keepdims=True) + 1e-9)
                n = min(ca.shape[1], cb.shape[1])
                ch = float(np.median(np.sum(ca[:, :n] * cb[:, :n], axis=0)))
                cta = float(librosa.feature.spectral_centroid(y=r[ia:ib], sr=SR).mean())
                ctb = float(librosa.feature.spectral_centroid(y=b[ia:ib], sr=SR).mean())
                row.update(chroma=round(ch, 3), centroid_mine=round(ctb), centroid_ref=round(cta))
                ba, bb = band_db(r, t0, t1), band_db(b, t0, t1)
                w = ''
                if ba is not None:
                    d = bb - ba
                    row['worst_bands'] = [[BANDS[j][0], BANDS[j][1], round(float(d[j]), 1)]
                                          for j in np.argsort(np.abs(d))[::-1][:3]]
                    w = ' '.join('%d-%d:%+.0f' % (BANDS[j][0], BANDS[j][1], d[j])
                                 for j in np.argsort(np.abs(d))[::-1][:3])
                line += '   %.3f  %5.0f/%5.0f  %-22s' % (ch, ctb, cta, w)
        cnt = sorted(((k, sum(1 for x in v if t0 <= x < t1)) for k, v in tracks.items()),
                     key=lambda z: -z[1])[:4]
        row['tracks'] = {k: v for k, v in cnt if v}
        line += '   ' + ' '.join('%s:%d' % kv for kv in cnt if kv[1])
        rows.append(row)
        print(line)

    if r is not None:
        chs = [x['chroma'] for x in rows if 'chroma' in x]
        rts = [(x['centroid_mine'] - x['centroid_ref']) / max(1.0, x['centroid_ref'])
               for x in rows if 'centroid_mine' in x]
        if chs:
            worst = sorted([x for x in rows if 'chroma' in x], key=lambda z: z['chroma'])[:3]
            print('-' * 40)
            print('⚠ 逐段看（**别用全曲中位**）：chroma 最低三段 %s；质心差最大三段 %s'
                  % (', '.join('%s(%.2f)' % (x['name'], x['chroma']) for x in worst),
                     ', '.join('%s(%+.0f%%)' % (x['name'], 100 * z)
                               for x, z in sorted(zip([x for x in rows if 'centroid_mine' in x], rts),
                                                  key=lambda p: p[1])[:3])))
            print('   chroma 区间 %.2f~%.2f · 质心差区间 %+.0f%%~%+.0f%%（**跨度大就说明不能整曲一刀切**）'
                  % (min(chs), max(chs), 100 * min(rts), 100 * max(rts)))
    if a.json:
        json.dump({'song': name, 'bounds': bounds, 'rows': rows},
                  open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('逐段读数 → %s' % a.json)
    return 0


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                            # noqa: BLE001
        pass
    sys.exit(main())
