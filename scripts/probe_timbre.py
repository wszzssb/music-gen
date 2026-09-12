#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_timbre.py —— 用**测量**挑音色 / 查"谁在贡献哪个频段"。

为什么需要它：整曲渲染一轮 30~120 秒，而**单音色 / 单轨只要 4~8 秒**。
先把候选缩到 1~2 个再上整曲，省掉"整曲试错 → 看不出变化 → 再试"的循环。

两个用法（实测都用过）：
  ① `--programs 8,9,51,95,99`：同一段 8 分网格高音和弦，换 GM 音色各渲染一遍。
     选"空气层"音色靠这张表：**等响度**下 GM95 Pad Sweep 的 10-18k 有 15.9dB 且占用率 95%
     （Celesta 只有 7.4dB/19%、Glock 20.0dB/21%）——"高频是连续的墙"这一条只有它满足，
     参考曲要的正是这个（见 PITFALLS 60）。
  ② `--solo songs/xx/song.json`：把该曲每一轨单独渲染（量的是**未归一化**的 raw）。
     干嘛用：21_g150_velvet 连改 5 版编配、整曲频段数字几乎不动 —— 一 solo 就看出
     改的是力度 42~72、CC7 60 的 Arp 轨，它在整混里本来就听不见（要改就改 Melody/Bass 那种大轨）。

用法:
  python scripts\\probe_timbre.py --programs 8,9,51,95,99 [--bars 2] [--bpm 150]
  python scripts\\probe_timbre.py --solo songs\\21_g150_velvet\\song.json [--json]
产出: 每行"音色/轨 | 力度 min/中/max | CC7 | 2.5-5k/5-10k/10-18k 绝对 dB | 占用率"，
      按 10-18k 排序。候选音色模式归一过响度（比"等响度下的形状"），
      solo 模式量未归一化的 raw（比"谁在整混里真的响"）。
"""
import argparse
import json
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import bgm_synth as bs      # noqa: E402
import metrics              # noqa: E402
import render_midi as rm    # noqa: E402
import song_engine          # noqa: E402
import cli_utf8 as _cu      # noqa: E402

BANDS = [(160, 315), (315, 630), (630, 1250), (1250, 2500), (2500, 5000),
         (5000, 10000), (10000, 18000)]
SHOW = ['2500-5000', '5000-10000', '10000-18000']
OCC = ['5000-10000', '10000-18000']
OCC_N, OCC_STEP, OCC_FLOOR_DB = 4096, 1024, 20.0


def band_stats(path):
    """→ {'abs': {带: 绝对 dB}, 'occ': {带: 占用率%}, 'peak': 最响带, 'rms': 全曲 dB}

    **绝对**（平均谱口径，同 metrics.octave_bands 的算法，只是不归一）：
    候选音色模式里每段渲染都归一过响度 → 横向比就是"等响度下这个音色在哪个带更强"。"""
    m, sr, _x = metrics.load(path)
    S, f = metrics._avg_spec(m, sr)
    power = {}
    for lo, hi in BANDS:
        k = (f >= lo) & (f < hi)
        power['%d-%d' % (lo, hi)] = float((S[k] ** 2).sum())
    absd = {k: round(10 * np.log10(max(v, 1e-20)), 1) for k, v in power.items()}
    peak = max(power, key=power.get)
    occ = {}
    for lo, hi in BANDS:
        key = '%d-%d' % (lo, hi)
        env = metrics._band_env(m, sr, OCC_N, OCC_STEP, float(lo), float(hi))
        occ[key] = metrics.occupancy(env, OCC_FLOOR_DB)
    return {'abs': absd, 'occ': occ, 'peak': peak, 'rms': metrics.rms_db(m)}


def phrase_midi(path, prog, notes, bars, bpm):
    """固定乐句：每拍 2 个八分的高音和弦（力度 74 / CC7 100），音色之间只有音色不同"""
    bs.BPM = bpm
    ev = []
    for b in range(bars):
        for k in range(8):
            for nm in notes:
                ev.append((b * 4 + k * 0.5, 0.24, nm, 74))
    bs.write_midi(path, [('P', prog, 0, ev, [(0.0, 7, 100)])], ppq=480)


def render_bare(mid, base):
    """中性渲染（不动 EQ/宽度/响度），保证音色之间的差别不被母带链掩盖"""
    rm.render(mid, base, rms_db=-16.9, width=1.0, shelf_db=0.0, hp_hz=20.0, low_db=0.0,
              drive=1.0, mid_db=0.0, verbose=False, ogg=False)


def _outdir(tag, out=None):
    """中间产物一律写**系统临时目录**（仓库卫生守卫禁止 songs/_* 与 *.raw.wav 留在仓库里：
    render 过程会写 .raw.wav，所以默认目录必须在仓库外）"""
    d = out or os.path.join(tempfile.gettempdir(), 'mg_%s' % tag)
    os.makedirs(d, exist_ok=True)
    return d


def probe_programs(progs, bars=2, bpm=150.0, notes=(84, 88, 91, 96), out=None):
    """候选音色：每段都归一过响度 → 横向比的是**等响度下的频谱形状**"""
    out = _outdir('probe_timbre', out)
    rows = []
    for prog in progs:
        mid = os.path.join(out, 'p%03d.mid' % prog)
        base = os.path.join(out, 'p%03d' % prog)
        phrase_midi(mid, prog, notes, bars, bpm)
        render_bare(mid, base)
        st = band_stats(base + '.wav')
        rows.append({'prog': prog, 'name': 'GM%d' % prog, 'abs': st['abs'],
                     'occ': st['occ'], 'sort': st['abs']['10000-18000']})
    rows.sort(key=lambda r: -r['sort'])
    return rows


def solo_song(song_json, out=None):
    """逐轨 solo：**量的是未归一化的 raw 渲染**，所以轨与轨之间的电平是真实比例
    （同一首歌里 CC7/力度小的轨在这里就是小声 —— 这正是"改了没效果"的原因）"""
    out = _outdir('solo_' + os.path.basename(os.path.dirname(os.path.abspath(song_json))),
                  out)
    d = song_engine.load(song_json)
    ev, _n = song_engine.build_events(d)
    bs.BPM = d['bpm']
    rows = []
    for tr, notes in ev.items():
        if not notes:
            continue
        prog, chan = d['programs'][tr]
        pan, vol = d['mix'][tr]
        ccs = [(0.0, 10, pan), (0.0, 7, vol)]
        bar0 = 0
        for sec in d['sections']:
            amix = (sec.get('arr') or {}).get('mix') or {}
            if tr in amix:
                ccs.append((bar0 * 4.0, 7, int(amix[tr])))
            bar0 += sec['bars']
        mid = os.path.join(out, tr + '.mid')
        base = os.path.join(out, tr)
        bs.write_midi(mid, [(tr, prog, chan, notes, sorted(ccs))], ppq=480)
        render_bare(mid, base)
        raw = base + '.raw.wav'
        st = band_stats(raw if os.path.exists(raw) else base + '.wav')
        vel = sorted(n[3] for n in notes)
        sec_vols = [int(v) for v in
                    [((sec.get('arr') or {}).get('mix') or {}).get(tr)
                     for sec in d['sections']] if v is not None]
        vol_s = str(vol) if not sec_vols else '%d→%d~%d' % (vol, min(sec_vols), max(sec_vols))
        rows.append({'prog': prog, 'name': tr, 'notes': len(notes),
                     'vel': [vel[0], vel[len(vel) // 2], vel[-1]], 'cc7': vol,
                     'cc7_s': vol_s, 'rms': st['rms'], 'abs': st['abs'], 'occ': st['occ'],
                     'sort': st['abs']['10000-18000']})
    rows.sort(key=lambda r: -r['sort'])
    return rows


def report(rows):
    print('%-10s %14s %10s' % ('音色/轨', '力度min/中/max', 'CC7') +
          ''.join('%12s' % k for k in SHOW) + ''.join('%9s' % ('占用' + k[:5]) for k in OCC))
    for r in rows:
        vel = '/'.join(str(v) for v in r['vel']) if r.get('vel') else '-'
        print('%-10s %14s %10s' % (r['name'], vel, r.get('cc7_s', r.get('cc7', '-')) ) +
              ''.join('%12.1f' % r['abs'][k] for k in SHOW) +
              ''.join('%9.0f%%' % r['occ'][k] for k in OCC))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--programs', default=None, help='逗号分隔的 GM 音色号（候选音色模式）')
    ap.add_argument('--solo', default=None, help='song.json 路径（逐轨 solo 模式）')
    ap.add_argument('--bars', type=int, default=2)
    ap.add_argument('--bpm', type=float, default=150.0)
    ap.add_argument('--out', default=None)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    if a.solo:
        rows = solo_song(a.solo, a.out)
    elif a.programs:
        rows = probe_programs([int(x) for x in a.programs.split(',') if x.strip()],
                              a.bars, a.bpm, out=a.out)
    else:
        raise SystemExit('要么 --programs 8,9,95，要么 --solo songs/xx/song.json')
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        report(rows)
        print('  读法：三个频段列是**绝对 dB**（候选音色已归一过响度 → 横向比"等响度下谁的高频更足"；'
              'solo 模式量的是未归一化的 raw → 横向比"谁在整混里真的响"）。'
              '占用率 = 帧能量 > 本带峰值-20dB 的帧占比：接近 100% 是"连续的墙"，30~50% 是"点"。')


_cu.setup()          # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    main()
