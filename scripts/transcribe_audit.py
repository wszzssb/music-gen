# -*- coding: utf-8 -*-
"""**转录（识别）精度体检** —— 还原任务的第一步，先量"识别准不准"，再谈混音/音色。

为什么必须排在前面（用户 2026-09-17 定的规矩："以后要先确定识别问题"）：
  混音、音色、EQ、宽度这些都是**在转录结果之上**的加工。识别错一个八度，
  后面所有工序都在认真地加工一个错东西 —— 而且**越调越像在改进**，
  因为频谱/响度指标确实会变好（BGM29 实测：把低音补到 1416 音后
  平均相对带差 2.61 → 0.83 dB，可低音有 25% 的时间弹的是错八度）。

基准选谁：
  · 用 **demucs 分出来的参考分轨**（内容就是原曲本身，不引入另一个模型的偏差）；
  · 用 **pyin** 提基频 —— 它与 YourMT3 / Basic Pitch **完全独立**（自相关 + HMM），
    三方都错才会同时错，所以"基准有我方无"基本就是漏检、"差八度"基本就是识别错。
  · ⚠ **只适用于单音性强的层**（bass 最典型）。复音层（钢琴+吉他+合成器叠在一起）不能用：
    pyin 只能给出一条最强基频，实测 `other` 层会得到"假音 66%"这种**方法本身造成的**假数。

输出的五个数（逐帧，默认 23ms/帧）：
    ✓ 音高一致 · ~ 差八度 · ✗ 音高不同 · ✗ 漏检 · ✗ 假音
判据（BGM29 bass 实测做参照）：**一致率 < 70% 就先修识别**，别去调混音。

用法：
    python scripts/transcribe_audit.py <参考分轨.wav> <我方.mid> --tracks "Bass" \
        [--fmin 40] [--fmax 300] [--t0 0 --t1 0]
"""
import argparse
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import midi_file  # noqa: E402

SR = 22050
HOP = 512
GRID_MS = HOP / SR * 1000.0


def main():
    ap = argparse.ArgumentParser(description='转录（识别）精度体检：与参考分轨逐帧比音高')
    ap.add_argument('stem', help='参考分轨音频（demucs 的 bass.wav 等）')
    ap.add_argument('mid', help='我方转录 MIDI')
    ap.add_argument('--tracks', required=True, help='参与比对的轨名，逗号分隔')
    ap.add_argument('--fmin', type=float, default=40.0)
    ap.add_argument('--fmax', type=float, default=300.0)
    ap.add_argument('--t0', type=float, default=0.0)
    ap.add_argument('--t1', type=float, default=0.0, help='0 = 到结尾')
    a = ap.parse_args()

    import numpy as np
    import soundfile as sf
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), '.venv-ml', 'Lib', 'site-packages'))
    import librosa

    y, sr0 = sf.read(a.stem, dtype='float32', always_2d=True)
    y = y.mean(axis=1)
    if sr0 != SR:
        y = librosa.resample(y, orig_sr=sr0, target_sr=SR)
    i0 = int(a.t0 * SR)
    i1 = int(a.t1 * SR) if a.t1 > 0 else len(y)
    seg = y[i0:i1]
    f0, _vf, _vp = librosa.pyin(seg, fmin=a.fmin, fmax=a.fmax, sr=SR,
                                frame_length=2048, hop_length=HOP)
    times = np.arange(len(f0)) * HOP / SR + a.t0
    ref = np.full(len(f0), -1, dtype=int)
    for i, v in enumerate(f0):
        if np.isfinite(v):
            ref[i] = int(round(69 + 12 * np.log2(v / 440.0)))

    m = midi_file.import_midi(a.mid)
    spb = 60.0 / float(m.get('bpm') or 120.0)
    want = set(t.strip() for t in a.tracks.split(','))
    mine = np.full(len(f0), -1, dtype=int)
    used = []
    for tr in m['tracks']:
        if tr.get('name') not in want:
            continue
        used.append(tr['name'])
        for n in tr['notes']:
            t0 = n[0] * spb
            j0 = max(0, int((t0 - a.t0) * SR / HOP))
            j1 = min(len(mine), int(np.ceil((t0 + n[1] * spb - a.t0) * SR / HOP)))
            for j in range(j0, j1):
                if mine[j] == -1:
                    mine[j] = n[2]

    both = (ref >= 0) & (mine >= 0)
    same = both & (ref == mine)
    oct8 = both & (np.abs(ref - mine) == 12)
    diff = both & (ref != mine) & (np.abs(ref - mine) != 12)
    miss = (ref >= 0) & (mine < 0)
    fake = (ref < 0) & (mine >= 0)
    tot = max(1, int((ref >= 0).sum()))
    tot_mine = max(1, int((mine >= 0).sum()))
    print('== 识别体检：%s vs %s' % (os.path.basename(a.stem), os.path.basename(a.mid)))
    print('   比对轨 %s · %.1f–%.1fs（%d 帧 @%.1fms）' % (used, a.t0, a.t1 or len(y) / SR,
                                                        len(f0), GRID_MS))
    print('   基准有声帧 %d · 我方有声帧 %d' % (tot, tot_mine))
    print('   ✓ 音高一致   %5d  （占基准 %5.1f%%）' % (int(same.sum()), 100 * same.sum() / tot))
    print('   ~ 差八度     %5d  （占基准 %5.1f%%）← 识别错，且**多模型集成本来修不掉**'
          % (int(oct8.sum()), 100 * oct8.sum() / tot))
    print('   ✗ 音高不同   %5d  （占基准 %5.1f%%）' % (int(diff.sum()), 100 * diff.sum() / tot))
    print('   ✗ 漏检       %5d  （占基准 %5.1f%%）' % (int(miss.sum()), 100 * miss.sum() / tot))
    print('   ✗ 假音       %5d  （占我方 %5.1f%%）' % (int(fake.sum()), 100 * fake.sum() / tot_mine))
    acc = 100 * same.sum() / tot
    print('   → 一致率 **%.1f%%**：%s' % (acc, '合格' if acc >= 70 else
                                       '**先修识别**（<70%），别去调混音/音色/EQ'))
    per = defaultdict(int)
    for i in np.where(miss)[0]:
        per[int(times[i])] += 1
    worst = sorted(per.items(), key=lambda kv: -kv[1])[:6]
    if worst:
        print('   漏检最多的秒：' + ', '.join('%ds(%d帧)' % (k, v) for k, v in worst))
    return 0


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    sys.exit(main())
