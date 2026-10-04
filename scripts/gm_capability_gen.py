#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gm_capability_gen.py —— 生成 **GM 音色能力表**（`gm_capability.json`）。

为什么要进仓库（2026-10-02）：这张表是 `pick_timbre.py`（音色自适应）的**唯一依据**，
而原来那版 `gm_capability.py` 只在临时目录里、**只打印不落盘** —— 换台机器就"没有依据可查"。

做法（一次渲染出全表，约 1 分钟）：造一个测试 MIDI —— 每 8 秒一段换一个 program，
段内弹固定乐句（4 拍长音 → 2 拍弱长音 → 半拍短音 → 音阶 → 和弦），
用 **FluidSynth 纯渲染**（不带项目混响/中侧加宽，那些会污染"纯音色"读数），
再按段量：谐波% · 2–6k% · 质心Hz · 动态dB · 12dB 衰减 ms。

用法：
  python scripts/gm_capability_gen.py [--out-dir <目录>] [--programs 0,24,30,...]
  python scripts/gm_capability_gen.py --to-json          # 打印已有表（不重渲染）
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

TPB = 480
BAR = 4 * TPB
SEG_TICKS = 4 * BAR              # 每段 4 小节 = 8 秒 @120BPM

# 默认覆盖"引擎真会用到的族"（12 族 × 代表音色；不足的族由 `pick_timbre` 的
# `FAMILIES` 表兜底 —— 它只会推荐表里有的 program，不会推荐没量过的）
DEFAULT_PROGRAMS = [
    0, 1, 2, 3, 4, 5,                                   # piano
    8, 9, 10, 11, 12, 13, 14, 15,                       # chromatic percussion
    16, 17, 18, 19, 20, 21, 22, 23,                     # organ
    24, 25, 26, 27, 28, 29, 30, 31,                     # guitar
    32, 33, 34, 35, 36, 37, 38, 39,                     # bass
    40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51,     # strings / ensemble
    52, 53, 54, 55,
    56, 57, 58, 59, 60, 61, 62, 63,                     # brass
    64, 65, 66, 67, 68, 69, 70, 71,                     # reed
    72, 73, 74, 75, 76, 77, 78, 79,                     # pipe
    80, 81, 82, 83, 84, 85, 86, 87,                     # synth lead
    88, 89, 90, 91, 92, 93, 94, 95,                     # synth pad
]


def _exe_sf2():
    """FluidSynth 与音源：优先仓库 `vendor/`，其次环境变量（换机器时不必改代码）"""
    exe = os.environ.get('FLUIDSYNTH')
    sf2 = os.environ.get('SF2')
    if not exe:
        base = os.path.join(ROOT, 'vendor', 'fluidsynth')
        if os.path.isdir(base):
            for d in sorted(os.listdir(base)):
                p = os.path.join(base, d, 'bin', 'fluidsynth.exe')
                if os.path.isfile(p):
                    exe = p
                    break
    if not sf2:
        v = os.path.join(ROOT, 'vendor')
        if os.path.isdir(v):
            for f in sorted(os.listdir(v)):
                if f.lower().endswith('.sf2'):
                    sf2 = os.path.join(v, f)
                    break
    if not exe or not os.path.isfile(exe):
        raise SystemExit('找不到 fluidsynth.exe（可用环境变量 FLUIDSYNTH 指定）')
    if not sf2 or not os.path.isfile(sf2):
        raise SystemExit('找不到 .sf2 音源（可用环境变量 SF2 指定）')
    return exe, sf2


def build_midi(path, programs):
    import mido
    mid = mido.MidiFile(ticks_per_beat=TPB)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tr.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(120), time=0))
    events = []
    for i, prog in enumerate(programs):
        base = i * SEG_TICKS
        events.append((base, mido.Message('program_change', program=prog, channel=0)))
        events.append((base, mido.Message('note_on', note=60, velocity=100, channel=0)))
        events.append((base + 4 * TPB, mido.Message('note_off', note=60, velocity=0, channel=0)))
        events.append((base + 4 * TPB, mido.Message('note_on', note=60, velocity=55, channel=0)))
        events.append((base + 6 * TPB, mido.Message('note_off', note=60, velocity=0, channel=0)))
        events.append((base + 6 * TPB, mido.Message('note_on', note=72, velocity=110, channel=0)))
        events.append((base + 6 * TPB + TPB // 2,
                       mido.Message('note_off', note=72, velocity=0, channel=0)))
        for k, p in enumerate((60, 62, 64, 65, 67)):            # 音阶：量"短音"的表现
            t = base + 7 * TPB + k * (TPB // 2)
            events.append((t, mido.Message('note_on', note=p, velocity=88, channel=0)))
            events.append((t + TPB // 2 - 10,
                           mido.Message('note_off', note=p, velocity=0, channel=0)))
        for p in (60, 64, 67):                                  # 和弦
            events.append((base + 11 * TPB, mido.Message('note_on', note=p, velocity=92, channel=0)))
            events.append((base + 15 * TPB, mido.Message('note_off', note=p, velocity=0, channel=0)))
    events.sort(key=lambda z: (z[0], 0 if z[1].type == 'note_off' else 1))
    last = 0
    for tick, msg in events:
        msg.time = tick - last
        last = tick
        tr.append(msg)
    mid.save(path)
    return len(programs) * SEG_TICKS / float(TPB) / 2.0


def seg_stats(y, sr, t0, t1):
    """段内物理量（与 `乐器能力表.md` 同口径）+ 12dB 衰减时间"""
    a, b = int(t0 * sr), min(len(y), int(t1 * sr))
    x = y[a:b]
    n, hop = 4096, 2048
    fr = np.fft.rfftfreq(n, 1.0 / sr)
    win = np.hanning(n)
    b26 = (fr >= 2000) & (fr <= 6000)
    bf0 = (fr >= 60) & (fr <= 1000)
    harms, his, cens, ens = [], [], [], []
    for i in range(0, max(1, len(x) - n), hop * 4):
        sp = np.abs(np.fft.rfft(x[i:i + n] * win))
        p = sp ** 2
        tot = float(p.sum())
        if tot <= 1e-12:
            continue
        ens.append(10 * np.log10(tot))
        his.append(100.0 * float(p[b26].sum()) / tot)
        cens.append(float((fr * p).sum() / tot))
        if bf0.any():
            f0 = float(fr[bf0][int(np.argmax(sp[bf0]))])
            he = 0.0
            for k in range(1, 6):
                f = f0 * k
                if f > sr * 0.45:
                    break
                m = (fr >= f * 0.98) & (fr <= f * 1.02)
                if m.any():
                    he += float(p[m].sum())
            harms.append(100.0 * he / tot)
    if not harms:
        return None
    # 短音占比（"拨弦型 vs 持续型"）：段内 0.5 拍短音那一段的能量包络落差
    env = np.abs(x[:int(4 * sr)])
    dec = None
    if len(env) > 64:
        k = max(1, int(0.002 * sr))
        e2 = np.convolve(env, np.ones(k) / k, mode='same')
        pk = float(e2.max())
        if pk > 1e-9:
            below = np.where(e2 <= pk * 0.2512)[0]
            below = below[below > int(np.argmax(e2))]
            dec = round(1000.0 * (below[0] - int(np.argmax(e2))) / sr, 0) if len(below) else None
    # **短音占比**：长音段（0–4 拍）之后，短音/音阶段（7–9 拍）还剩多少能量
    tail = float(np.sqrt(np.mean(x[int(9.5 * sr / 2):int(10.0 * sr / 2)] ** 2) + 1e-12))
    head = float(np.sqrt(np.mean(x[:int(0.5 * sr)] ** 2) + 1e-12))
    short_ratio = float(np.clip(1.0 - (tail / max(head, 1e-9)) * 4.0, 0.0, 1.0))
    e = np.asarray(ens)
    return dict(harm=float(np.median(harms)), hi26=float(np.median(his)),
                cent=float(np.median(cens)),
                dyn=float(np.percentile(e, 90) - np.percentile(e, 10)) if len(e) > 3 else 0.0,
                decay12_ms=dec, short=round(short_ratio, 3))


def generate(out_dir, programs):
    exe, sf2 = _exe_sf2()
    os.makedirs(out_dir, exist_ok=True)
    mid = os.path.join(out_dir, 'gm_probe.mid')
    wav = os.path.join(out_dir, 'gm_probe.wav')
    secs = build_midi(mid, programs)
    print('测试 MIDI：%d 个音色 × 8 秒 = %.0f 秒' % (len(programs), secs))
    cmd = [exe, '-ni', '-g', '1.0', '-r', '44100', '-F', wav, sf2, mid]
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0 or not os.path.isfile(wav):
        raise SystemExit('fluidsynth 失败：%s' % (r.stderr or '')[-400:])
    import soundfile as sf
    y, sr = sf.read(wav, dtype='float32', always_2d=True)
    y = y.mean(axis=1)
    rows = []
    for i, prog in enumerate(programs):
        s = seg_stats(y, sr, i * 8.0, (i + 1) * 8.0)
        if not s:
            continue
        s['program'] = prog
        rows.append(s)
    out = os.path.join(out_dir, 'gm_capability.json')
    json.dump({'source': os.path.basename(sf2), 'sr': sr, 'rows': rows},
              open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('已写 %s（%d 个音色）' % (out, len(rows)))
    return out


def selftest():
    """契约自检（不跑 fluidsynth）：段长/程序表/统计函数形状正确。"""
    ok = True

    def chk(lab, got, want):
        nonlocal ok
        f = 'PASS' if got == want else 'FAIL'
        ok = ok and got == want
        print('  [%s] %-46s 期望 %-8s 实得 %-8s' % (f, lab, want, got))

    chk('每段 4 小节 @480tpb = 1920 tick', SEG_TICKS, 4 * 4 * TPB)
    chk('默认程序表没有重复项', len(set(DEFAULT_PROGRAMS)), len(DEFAULT_PROGRAMS))
    chk('默认表程序数 = 94（12 族）', len(DEFAULT_PROGRAMS), 94)
    chk('默认表覆盖吉他族全部 8 个', all(p in DEFAULT_PROGRAMS for p in range(24, 32)), True)
    # 统计函数：给一段白噪声，谐波%应偏低、2–6k% 应明显
    y = (np.random.RandomState(0).randn(44100 * 2) * 0.2).astype('float32')
    s = seg_stats(y, 44100, 0.0, 2.0)
    chk('白噪声能算出统计量', s is not None, True)
    chk('白噪声谐波% 明显低于 60', s['harm'] < 60, True)
    print('  selftest %s' % ('全部通过' if ok else '有失败'))
    return ok


def main():
    ap = argparse.ArgumentParser(description='生成 GM 音色能力表（pick_timbre 的依据）')
    ap.add_argument('--out-dir', default=os.path.join(ROOT, '_gmcap'))
    ap.add_argument('--programs', default=None, help='逗号分隔的 GM 号（默认 12 族代表音色）')
    ap.add_argument('--to-json', action='store_true', help='只打印已有表')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if a.to_json:
        p = os.path.join(a.out_dir, 'gm_capability.json')
        print(open(p, encoding='utf-8').read() if os.path.isfile(p) else '没有 %s' % p)
        return 0
    progs = ([int(x) for x in a.programs.split(',') if x.strip()] if a.programs
             else DEFAULT_PROGRAMS)
    generate(a.out_dir, progs)
    return 0


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                          # noqa: BLE001
        pass
    sys.exit(main())
