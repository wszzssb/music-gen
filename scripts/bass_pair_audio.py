# -*- coding: utf-8 -*-
r"""bass_pair_audio.py —— **同音高近距离重复对**的独立音频判据（自检内置）。

## 为什么

`bass_ensemble.py` 的并集口径是"同 0.1s 格 + 同音高合并"，所以它**不会**产出同格两份；
但两个来源（YMT3 分轨 / Basic Pitch）沿 0.1s 格线交错时，会产出**相邻格**的一对音 ——
落到秒上就是 20~60ms 间隔。`preflight` ② 的判据是"同轨同音高 ≤60ms"，于是这 100 多对
全被计成"重复组"。**但"间隔小"本身不等于"错"**：bass 真的有把同一个音连拨两下的写法。

判据（不需要真值，只用**这一对音所在的音频**）：

    H1 两次真实起音 —— 第二下会把包络顶起来
    H2 一次起音被两个模型各检出一次 —— 包络本来就在按自己的衰减走

做法：在 A 起音 +3~+25ms 拟合 dB 包络的衰减直线 → 外推到 B 起音时刻 → 实测包络与
外推值之差 = **burst**。`burst ≥ 5 dB` ⇒ 衰减被打断 ⇒ H1；否则 H2。

⚠ **量程（实测标定，别当它无上限）**：第二下比第一下弱 **≥10 dB** 时 burst 掉到 ~3 dB，
   会被判成 H2。这是**保守方向的漏判**（把"其实有两下"合成一下），不会把一次起音判成两次。
   两个负控（单音 54ms / 单音 2dB/s 慢衰减）实测 burst ≤0.6 dB。

⚠ 三条已经试过并**作废**的写法（别再回去）：
  ① "冲量 = 前 20ms 极小值 → 后 30ms 极大值"：正弦起音必被吞（合成信号真值 30dB 读成 −0.1dB）
  ② "A→B 之间存在低于两端的极小值"：单音负控也判成两次（单调衰减里必有数值极小）
  ③ "B 处 500–3000Hz 起音瞬态上升"：负控（已衰减到静音）反而读到 **27.2dB** 上升 —— 无意义

## 用法

```bash
python bass_pair_audio.py --selftest                     # 只跑尺子自检
python bass_pair_audio.py <bass分轨.wav> --mid <mid> [--track Bass] [--tol 0.06] [--json 出.json]
```
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SR = 22050
BURST_DB = 5.0


# ─────────────────────────── 尺子 ───────────────────────────
def _hilbert_env(y):
    from scipy.signal import hilbert, medfilt
    e = np.abs(hilbert(y))
    return medfilt(e, 5)


def env_curve(y, t0, t1, sr=SR):
    i0, i1 = max(0, int(t0 * sr)), min(len(y), int(t1 * sr))
    if i1 - i0 < 8:
        return None, None
    return 20 * np.log10(np.maximum(_hilbert_env(y[i0:i1]), 1e-10)), np.arange(i0, i1) / float(sr)


def _local_max(curve, tt, t, half_ms=1.5, sr=SR):
    k = int(sr * half_ms / 1000)
    i = int(np.argmin(np.abs(tt - t)))
    a, b = max(0, i - k), min(len(curve), i + k + 1)
    return float(curve[a:b].max())


def burst_db(y, ta, tb, sr=SR):
    """→ (burst dB, 衰减率 dB/s, 实测@B, 外推@B)；burst>0 = A 的衰减被 B 打断（两次起音）"""
    c, tt = env_curve(y, ta - 0.01, tb + 0.02, sr)
    if c is None:
        return None, None, None, None
    f0, f1 = ta + 0.003, min(ta + 0.025, tb - 0.005)
    if f1 - f0 < 0.004:
        f0, f1 = ta + 0.002, max(ta + 0.004, tb - 0.002)
    if f1 - f0 < 0.002:
        return None, None, None, None
    sel = (tt >= f0) & (tt <= f1)
    if sel.sum() < 4:
        return None, None, None, None
    A = np.polyfit(tt[sel], c[sel], 1)
    pred = float(np.polyval(A, tb))
    act = _local_max(c, tt, tb, sr=sr)
    return act - pred, float(A[0]), act, pred


def load_audio(path, sr=SR):
    import soundfile as sf
    y, sr0 = sf.read(path, dtype='float32', always_2d=True)
    y = y.mean(axis=1).astype(np.float64)
    if sr0 != sr:
        import librosa
        y = librosa.resample(y, orig_sr=sr0, target_sr=sr)
    return y


def notes_sec(mid_path, track='Bass', lo=0, hi=127):
    """→ [(起音秒, 时值秒, 音高, 力度)]，跳过鼓通道"""
    import midi_file
    m = midi_file.import_midi(mid_path)
    spb = 60.0 / float(m.get('bpm') or 120.0)
    out = []
    for tr in m['tracks']:
        if tr.get('drum') or tr.get('channel') == 9:
            continue
        if track and tr.get('name') != track:
            continue
        for (st, du, p, v) in tr.get('notes', []):
            if lo <= p <= hi:
                out.append((float(st) * spb, float(du) * spb, int(p), int(v)))
    out.sort()
    return out


def close_pairs(notes, tol=0.06):
    """同音高、起音相差 ≤tol 秒的相邻对 → [(音高, A, B)]（与 preflight.dup_groups_in 同口径）"""
    byp = defaultdict(list)
    for n in notes:
        byp[n[2]].append(n)
    out = []
    for p, lst in byp.items():
        lst.sort()
        for a, b in zip(lst, lst[1:]):
            if b[0] - a[0] <= tol:
                out.append((p, a, b))
    return out


def judge_pairs(y, pairs, sr=SR, thr=BURST_DB):
    """→ [{p, gap, burst, rate, act, pred, two(True=两次起音)}]"""
    rows = []
    for (p, a, b) in pairs:
        bd, rate, act, pred = burst_db(y, a[0], b[0], sr)
        rows.append(dict(p=p, gap=b[0] - a[0], a_t=a[0], b_t=b[0],
                         a_dur=a[1], b_dur=b[1], a_vel=a[3], b_vel=b[3],
                         burst=bd, rate=rate, act=act, pred=pred,
                         two=None if bd is None else bool(bd >= thr)))
    return rows


# ─────────────────────────── 自检 ───────────────────────────
def selftest():
    t = np.arange(int(1.0 * SR)) / float(SR)
    a1 = np.sin(2 * np.pi * 70 * t) * np.exp(-t * 6.0)
    a2 = a1.copy()
    i2 = int(0.05 * SR)
    a2[i2:] = a2[i2:] + np.sin(2 * np.pi * 70 * t[i2:]) * np.exp(-(t[i2:] - 0.05) * 6.0)
    a3 = a1.copy()
    a3[i2:] = a3[i2:] + 0.25 * np.sin(2 * np.pi * 70 * t[i2:]) * np.exp(-(t[i2:] - 0.05) * 6.0)
    a4 = a1.copy()
    i4 = int(0.02 * SR)
    a4[i4:] = a4[i4:] + np.sin(2 * np.pi * 70 * t[i4:]) * np.exp(-(t[i4:] - 0.02) * 6.0)
    slow = np.sin(2 * np.pi * 70 * t) * np.exp(-t * 2.0)      # 负控 2：慢衰减
    cases = (('单音（负控）', a1, 0.004, 0.054, False),
             ('单音慢衰减（负控 2）', slow, 0.004, 0.054, False),
             ('双音 50ms', a2, 0.004, 0.054, True),
             ('双音 20ms', a4, 0.004, 0.024, True))
    ok = True
    for (lab, sig, ta, tb, want) in cases:
        bd, rate, act, pred = burst_db(sig, ta, tb)
        got = bd is not None and bd >= BURST_DB
        flag = 'PASS' if got == want else 'FAIL'
        ok = ok and got == want
        print('  [%s] %-20s burst %6.1f dB（衰减率 %6.0f dB/s · 实测 %.1f vs 外推 %.1f）期望 %s'
              % (flag, lab, bd, rate, act, pred, '两次起音' if want else '一次起音'))
    # 量程（如实记录，不参与 PASS/FAIL）：第二下弱 −12dB
    bd3, _r, _a, _p = burst_db(a3, 0.004, 0.054)
    print('  [量程] 双音 50ms 弱 −12dB  burst %6.1f dB（门 %.0f）⇒ 会被判 H2 —— '
          '这是**保守方向的已知漏判**，写进 __doc__' % (bd3, BURST_DB))
    print('  selftest %s' % ('全部通过' if ok else '有失败'))
    return ok


def main():
    ap = argparse.ArgumentParser(description='同音高 ≤60ms 重复对的独立音频判据')
    ap.add_argument('wav', nargs='?', help='该层的分轨音频（如 demucs 的 bass.wav）')
    ap.add_argument('--mid', help='要检查的 MIDI')
    ap.add_argument('--track', default='Bass')
    ap.add_argument('--tol', type=float, default=0.06)
    ap.add_argument('--json', default=None)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not (a.wav and a.mid):
        ap.print_help()
        return 2
    if not selftest():
        print('✗ 尺子自检不过，不许量真实音频')
        return 1
    y = load_audio(a.wav)
    ns = notes_sec(a.mid, a.track)
    pairs = close_pairs(ns, a.tol)
    rows = judge_pairs(y, pairs)
    ok = [r for r in rows if r['two'] is not None]
    n2 = sum(1 for r in ok if r['two'])
    print('\n%s 轨 %d 音 · 同音高 ≤%.0fms 对 %d 个' % (a.track, len(ns), a.tol * 1000, len(rows)))
    print('  H1 两次真实起音（burst ≥ %.0f dB）：%d（%.0f%%）' % (BURST_DB, n2, 100.0 * n2 / max(1, len(ok))))
    print('  H2 一次起音被重复检出：%d（%.0f%%）' % (len(ok) - n2, 100.0 * (len(ok) - n2) / max(1, len(ok))))
    if a.json:
        json.dump(rows, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('  → %s' % a.json)
    return 0


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                          # noqa: BLE001
        pass
    sys.exit(main())
