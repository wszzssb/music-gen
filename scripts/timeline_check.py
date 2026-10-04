#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""timeline_check.py —— **整轨落点体检**：抓"整轨错位"（比精度问题更严重的一类缺陷）。

## 为什么需要它（2026-10-02，BGM35 新版实测）

新版 `bgm35_reextract` 的 Bass 轨**整条被压缩到 0.801×**：末音落在 264.1 秒，而全曲 331.9 秒
—— **266 秒之后的真实低音一个事件都没有**。而所有既有工具都没报警：
`preflight` 的逐层空洞只看"该层有没有起音 / 我们覆盖不到三分之一"（Bass 只占 54 秒）；
`transcribe_audit` 的帧级一致率把它读成"10.4%，先修识别" —— 看着像**精度**问题，
真因是**整轨错位**。两个原因的修法完全不同，所以要先分开量。

## 判据

把每条轨的音按 **MIDI 自己声明的 bpm** 铺到音频时间轴上，量它到"该轨对应的 demucs 分轨
**真实起音**"的最近距离：

  · 中位 ≤60ms、末音 ≈ 曲长 ⇒ 落点可信
  · 中位远大于 60ms、**末音明显早于曲长** ⇒ 整轨错位（例如 bpm 口径写错、时间轴被缩放）

⚠ **这不是"精度"判据**：复音轨（Strings/Glock/Pad）音符多、起音检测漏检 ⇒ 中位会虚高，
   同一条轨对应多条分轨时（`other`）也会。**只用来抓整轨错位，不要当精度用**。
⚠ 根因排查顺序：先看"末音秒"（错位会一眼看出）→ 再看逐 60 秒段的中位是否有**线性漂移**
   （漂移 = 缩放；整体偏大但无漂移 = 起音检测/复音干扰）。

## 用法

```bash
python scripts/timeline_check.py <成品.mid> --stems <demucs六轨目录> [--json 出.json]
python scripts/timeline_check.py --selftest
#   <demucs六轨目录> 里应有 bass.wav / drums.wav / piano.wav / guitar.wav / other.wav / vocals.wav
```
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SR, HOP = 22050, 512
# 引擎轨 → demucs 分轨（`other` 同时供 Strings/Pad/Glock，所以那三条只当线索）
STEM_OF = {'Drums': 'drums', 'Bass': 'bass', 'Piano': 'piano', 'Hook': 'guitar',
           'Strings': 'other', 'Pad': 'other', 'Melody': 'vocals', 'Glock': 'other'}
# 一对一分轨的轨才算"可下结论"
ONE_TO_ONE = {'Bass', 'Drums', 'Piano', 'Hook', 'Melody'}
OK_MS = 60.0


def onsets(path):
    import soundfile as sf
    import librosa
    y, sr = sf.read(path, dtype='float32', always_2d=True)
    y = y.mean(axis=1)
    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    return np.asarray(librosa.onset.onset_detect(
        y=y, sr=SR, hop_length=HOP, units='time', pre_max=3, post_max=3,
        pre_avg=8, post_avg=8, delta=0.05, wait=2))


def track_beats(mid_path):
    """→ (bpm, {轨名: [起音拍, ...]})；鼓轨按通道 9 归到 'Drums'"""
    import midi_file
    m = midi_file.import_midi(mid_path)
    bpm = float(m.get('bpm') or 120.0)
    out = defaultdict(list)
    for tr in m['tracks']:
        nm = 'Drums' if (tr.get('drum') or tr.get('channel') == 9) else tr.get('name')
        out[nm].extend(float(x[0]) for x in tr.get('notes', []))
    return bpm, out


def check(mid, stems, tol_ms=OK_MS):
    bpm, tracks = track_beats(mid)
    spb = 60.0 / bpm
    on = {}                       # 分轨名 → 起音时刻（每条分轨只算一次）
    rows = []
    for tr, bs in sorted(tracks.items()):
        stem = STEM_OF.get(tr)
        if not stem or not bs:
            continue
        if stem not in on:
            p = os.path.join(stems, stem + '.wav')
            if not os.path.exists(p):
                continue
            on[stem] = onsets(p)
        ref = on[stem]
        if not len(ref):
            continue
        t = np.array(sorted(bs)) * spb
        d = np.abs(t[:, None] - ref[None, :]).min(axis=1)
        med = float(np.median(d)) * 1000
        seg = []
        for s0 in range(0, int(t[-1]) + 1, 60):
            msk = (t >= s0) & (t < s0 + 60)
            if msk.sum() >= 5:
                seg.append(round(float(np.median(d[msk])) * 1000, 1))
        # ⚠ "末音 ≥ 该轨自己那条分轨末起音的 95%"：拿全局最长分轨当基准会让"被压缩的轨"
        #   蒙混过关（BGM35 实测：Bass 末音 264 秒 vs 全曲 332 秒）
        short = t[-1] < 0.95 * float(ref[-1])
        rows.append(dict(track=tr, stem=stem, n=len(t), end=round(float(t[-1]), 1),
                         ref_end=round(float(ref[-1]), 1),
                         med_ms=round(med, 1), hit60=round(100 * float(np.mean(d <= 0.06)), 0),
                         seg_ms=seg, one_to_one=tr in ONE_TO_ONE,
                         verdict=('OK' if (med <= tol_ms and not short) else '整轨可疑')))
    ref_end = max((o[-1] for o in on.values() if len(o)), default=0.0)
    return dict(bpm=bpm, ref_end=round(ref_end, 1), rows=rows)


def report(rep):
    print('MIDI bpm %.1f · 参考分轨末起音 %.1f 秒' % (rep['bpm'], rep['ref_end']))
    print('%-9s %-7s %6s %9s %9s %9s %8s %9s  %s'
          % ('轨', '分轨', '音数', '末音秒', '分轨末', '中位ms', '≤60ms', '逐段中位', '判'))
    for r in rep['rows']:
        tag = r['verdict'] if r['one_to_one'] else r['verdict'] + '(仅线索)'
        print('%-9s %-7s %6d %9.1f %9.1f %9.1f %7.0f%% %9s  %s'
              % (r['track'], r['stem'], r['n'], r['end'], r['ref_end'], r['med_ms'], r['hit60'],
                 ','.join('%.0f' % x for x in r['seg_ms'][:6]), tag))
    bad = [r['track'] for r in rep['rows'] if r['one_to_one'] and r['verdict'] != 'OK']
    print()
    if bad:
        print('  ✗ 整轨可疑：%s —— 先看"末音秒"是否明显早于参考末起音（= 时间轴被缩放），'
              '再看逐段中位有没有线性漂移' % '、'.join(bad))
    else:
        print('  ✓ 一对一分轨的那些轨落点 OK')
    return 1 if bad else 0


def selftest():
    """尺子自检：缩放过的轨必须被抓出来，未缩放的不许误报。
    造一份合成 MIDI + 合成"分轨"（起音时刻已知），再故意把 MIDI 的时间轴缩放 1.25。"""
    import tempfile
    import soundfile as sf
    import midi_file
    d = tempfile.mkdtemp(prefix='tl_selftest_')
    ok = True

    def mk_mid(path, times_beat, bpm=120.0):
        m = dict(bpm=bpm, division=480, tracks=[dict(
            index=0, name='Bass', channel=0, program=32, drum=False, mute=False,
            solo=False, hidden=False, ccs=[], program_changes=[[0.0, 32]], markers=[],
            notes=[[float(b), 0.5, 40, 90] for b in times_beat])])
        midi_file.export_midi(m, path)

    def mk_stem(path, times_sec):
        n = int((max(times_sec) + 1.0) * SR)
        y = np.zeros(n, dtype='float32')
        rng = np.random.RandomState(0)
        for t in times_sec:
            i = int(t * SR)
            y[i:i + int(0.3 * SR)] += (rng.randn(int(0.3 * SR)) * np.hanning(int(0.3 * SR))
                                       * 0.3).astype('float32')
        sf.write(path, y, SR)

    true_sec = np.arange(1.0, 20.0, 1.0)
    mk_stem(os.path.join(d, 'bass.wav'), true_sec)
    mk_mid(os.path.join(d, 'good.mid'), true_sec * 2.0)          # 120BPM：拍=秒×2 ⇒ 正确
    mk_mid(os.path.join(d, 'bad.mid'), true_sec * 2.0 * 1.25)    # 故意把时间轴拉长 1.25×
    for (lab, f, want) in (('未缩放 → 必须 OK', 'good.mid', 'OK'),
                           ('缩放 1.25 → 必须报可疑', 'bad.mid', '整轨可疑')):
        r = check(os.path.join(d, f), d)['rows'][0]
        got = r['verdict']
        flag = 'PASS' if got == want else 'FAIL'
        ok = ok and got == want
        print('  [%s] %-26s 期望 %-8s 实得 %-8s（中位 %.1f ms · 末音 %.1f 秒）'
              % (flag, lab, want, got, r['med_ms'], r['end']))
    print('  selftest %s' % ('全部通过' if ok else '有失败'))
    return ok


def main():
    ap = argparse.ArgumentParser(description='整轨落点体检：抓整轨错位（不是精度）')
    ap.add_argument('mid', nargs='?')
    ap.add_argument('--stems', help='demucs 六轨目录（bass.wav/drums.wav/...）')
    ap.add_argument('--json', default=None)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not (a.mid and a.stems):
        ap.print_help()
        return 2
    if not os.path.isdir(a.stems):
        raise SystemExit('--stems 不是目录：%s' % a.stems)
    try:
        import pyenv
        pyenv.ensure('librosa', '.venv-ml', '本工具要 librosa/soundfile（装在 .venv-ml）')
    except Exception:                                          # noqa: BLE001
        pass
    rep = check(a.mid, a.stems)
    rc = report(rep)
    if a.json:
        json.dump(rep, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('  → %s' % a.json)
    return rc


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                          # noqa: BLE001
        pass
    sys.exit(main())
