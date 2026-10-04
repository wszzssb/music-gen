#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""shaker_layer.py —— **给"原曲没有鼓组"的还原曲补一层沙锤/闭镲**（去鼓版的收尾工序）。

## 什么时候用

用户口径（2026-10-04，`どうぞめしあがれ`）："感觉还行只是少了鼓，比之前的好" ——
去鼓版对了，但高频跟着塌了（质心 3960→1779 Hz）。**先确认原曲有没有鼓组**：
实测该曲 Demucs 的 `drums` 分轨 RMS **−34.2 dB**（比全混音低 15.8 dB）、
**几乎没有 <120 Hz 的能量**、79% 的起音落在 2 kHz 以上 ⇒ 那是高频碎点（吉他/人声残留），
**不是鼓组**。⇒ 这种情况要补的是**沙锤/闭镲**，不是 kick+snare。

## 做法（**证据在分轨上，不是拍脑袋**）

① 取参考 `drums.wav` 的 **4–10 kHz 带通谱通量峰**（原曲真实的高频律动）；
② 对到 **16 分格**，统计落点分布与逐小节密度（实测本曲：**74% 落在十六分反拍**、
   逐小节中位 11 点、结尾 2 小节 0 点）；
③ **逐小节**建 `drum_grid`（哪些小节有、几点、力度多少，全部来自 ② 的实测）；
④ 逐段 `arr.perc` 按**该段自身**的高频能量分档（不是整曲一刀切）。

⚠ **只放沙锤类音色**（GM **82** shaker / **70** maracas / **44** 闭镲）——
`perc_style: light` 那个"无鼓组"档实测仍会带 kick 36 + snare 38（`song_engine.perc_part`），
用户要的是"沙锤层"，不是把鼓组换个名字加回来。

## 用法

```bash
python scripts\shaker_layer.py <曲目> --ref-drums <drums.wav> [--out 新曲名]
       [--dry] [--no-render]
python scripts\shaker_layer.py douzo_ymt3_nodrum --ref-drums "D:\...\drums.wav" --dry
```
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()          # noqa: E402
import json_io                               # noqa: E402

SONGS = os.path.join(ROOT, 'songs')
SHAKER, MARR, CHH = 82, 70, 44          # GM：沙锤 / 沙锤（响一点）/ 闭镲


def hf_onsets(wav, bpm, band=(4000, 10000), k=1.2, tol=0.40):
    """参考分轨的高频起音 → 对到 16 分格。返回 (per_bar 网格, 统计)。"""
    import numpy as np
    import soundfile as sf
    import scipy.signal as sg
    x, sr = sf.read(wav, always_2d=True)
    mono = x.mean(axis=1)
    sos = sg.butter(4, [band[0] / (sr / 2), band[1] / (sr / 2)], btype='band',
                    output='sos')
    hf = sg.sosfilt(sos, mono)
    hop = 64
    fr = np.lib.stride_tricks.sliding_window_view(hf, 1024)[::hop]
    W = np.abs(np.fft.rfft(fr * np.hanning(1024), axis=1))
    flux = np.maximum(0, np.diff(W, axis=0)).sum(axis=1)
    thr = flux.mean() + k * flux.std()
    step = (60.0 / bpm) / 4.0                     # 16 分格秒数
    peaks = []
    for i in range(1, len(flux) - 1):
        if flux[i] > thr and flux[i] >= flux[i - 1] and flux[i] > flux[i + 1]:
            t = i * hop / sr
            if not peaks or t - peaks[-1][0] > 0.06:
                peaks.append((t, float(flux[i])))
    nbar = int(round((len(mono) / sr) / (60.0 / bpm * 4)))
    per_bar = [{} for _ in range(nbar)]           # bar → {grid: 强度}
    stats = {'peaks': len(peaks), 'grid': [0] * 16, 'perbar': [0] * nbar}
    for t, a in peaks:
        gi = t / step
        g = int(round(gi))
        if abs(gi - g) > tol:
            continue                              # 不对格的 = 不是律动点
        b, cell = divmod(g, 16)
        if not (0 <= b < nbar):
            continue
        per_bar[b][cell] = max(per_bar[b].get(cell, 0.0), a)
        stats['grid'][cell] += 1
        stats['perbar'][b] += 1
    return per_bar, stats


def build_grid(per_bar, vel_off=(42, 62), vel_on=(52, 78), note_off=SHAKER,
               note_on=MARR, section_of=None, target=None):
    """实测网格 → `drum_grid.per_bar`（**只放沙锤类音色**）。

    ⚠ **件名只能用引擎认的四件**（`kick`/`snare`/`hat`/`open`）——两条契约都硬编码了这四件：
    ① 写盘循环 `for _nm, _note in (('kick',36),('snare',38),('hat',42),('open',46))`
       ⇒ 自定义件名（`shaker`/`marr`）**一个音都写不出来**；
    ② "整小节静音"门 `_dn = len(kick)+len(snare)+len(hat) < 3`（前 16 小节）
       ⇒ 不落在这三件里的点**不计入**，整段被当静音吃掉。
    实测两次：第一次写成扁平 list → `AttributeError`；第二次用自定义件名 →
    命令 exit 0、通道 9 **一个音都没有**（静默失败）。
    ⇒ 音高走**条目第三列**（`grid_entries` 支持 `[格,力度,音高]`），件名只借 `hat`/`open` 两个桶。

    **力度三层一起定**（只做一层就会"整曲一个样"）：
      ① **逐点**：由实测强度归一化（反拍轻、正拍重）；
      ② **逐段**：该段的实测点数 / 该段目标点数的比例 —— 参考曲的鼓是**逐段起伏**的
         （实测 3~15 点/小节），整曲一个力度等于把起伏抹平；
      ③ **逐小节**：本小节点数 / 该段每小节均点数，再乘一次（段内也有强弱）。
    最终力度 = ①×②×③，夹在 **[20, 84]**（沙锤不该抢旋律；上限也防 GM 沙锤"炸"）。
    ⚠ **别把网格做太稀**：引擎的静音门（前 16 小节 `本小节鼓点<3` ⇒ 一个小节都不出）
    实测会把 1~2 点/小节的网格**整段吃光** ⇒ `section_of`/`target` 给定时按
    "**段内实测密度、但不低于 3 点/小节**"生成。
    """
    if section_of is None or target is None:            # 兼容老用法（无逐段缩放）
        out = []
        for bar in per_bar:
            ev = {}
            for cell in sorted(bar):
                off = bool(cell % 2)
                lo, hi = vel_off if off else vel_on
                t = min(1.0, max(0.0, (bar[cell] - 10.0) / 15.0))
                v = int(round(lo + (hi - lo) * t))
                ev.setdefault('hat' if off else 'open', []).append(
                    [cell, v, note_off if off else note_on])
            out.append(ev)
        return out
    out = []
    for i, bar in enumerate(per_bar):
        sec = section_of[i]
        tgt = max(1.0, float(target[sec]))
        n = len(bar)
        # ② 逐段 + ③ 逐小节：本小节点数相对该段目标
        scale = min(1.35, max(0.35, (n / tgt) ** 0.5))
        ev = {}
        for cell in sorted(bar):
            off = bool(cell % 2)
            lo, hi = vel_off if off else vel_on
            t = min(1.0, max(0.0, (bar[cell] - 10.0) / 15.0))
            v = int(round((lo + (hi - lo) * t) * scale))
            v = max(20, min(84, v))
            ev.setdefault('hat' if off else 'open', []).append(
                [cell, v, note_off if off else note_on])
        out.append(ev)
    return out


def resolve_song(spec):
    """曲目名 → `song.json` 路径。**必须按"曲内 name"兜底**，别只按参数拼目录。

    ⚠ 实测踩到（2026-10-04，`douzo_r2` 那一轮）：`strip_drums.py` 产出的目录叫
    `douzo_r2_nodrum`，而**曲内 `name` 仍是 `douzo_r2`**（引擎写 MIDI 用的是 `name`）。
    本工具原来拿**参数**拼 `songs/<参数>/song.json`，于是拿"去鼓后的目录名"去写
    → 它把网格写进了 **`songs/douzo_r2/`（另一首！）**，目标目录的 `song.json`
    反而被**备份覆盖**（新加的 `--force` 备份逻辑正好帮了倒忙）。
    症状是"命令 exit 0、处理表全对、产物里**没有沙锤**"——又是一次静默失败。
    修法：参数要么是现成路径，要么在 `songs/` 下**按目录找**；找不到再按"曲内 name"找。
    """
    cands = [spec] if os.path.isabs(spec) else [
        os.path.join(SONGS, spec, 'song.json'), spec]
    for c in cands:
        if os.path.exists(c):
            return os.path.abspath(c)
    # 兜底：遍历曲库，看哪首的 `name` 等于参数
    if os.path.isdir(SONGS):
        for nm in sorted(os.listdir(SONGS)):
            p = os.path.join(SONGS, nm, 'song.json')
            if not os.path.exists(p):
                continue
            try:
                if str(json.load(open(p, encoding='utf-8')).get('name') or '') == spec:
                    return p
            except Exception:                              # noqa: BLE001
                continue
    return None


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('song')
    ap.add_argument('--ref-drums', required=True, help='参考曲的 drums 分轨 wav')
    ap.add_argument('--bpm', type=float, default=None, help='默认取曲目自己的 bpm')
    ap.add_argument('--out', default=None)
    ap.add_argument('--band', default='4000,10000')
    ap.add_argument('--k', type=float, default=1.2,
                    help='起音阈值 = mean + k*std（默认 1.2：实测此值下逐小节 11 点，与参考 8~14 吻合）')
    ap.add_argument('--tol', type=float, default=0.40,
                    help='对格容差（格；默认 0.40 —— 0.25 太紧，720 个峰只剩 256 个）')
    ap.add_argument('--vel-off', default='42,62')
    ap.add_argument('--vel-on', default='52,78')
    ap.add_argument('--force', action='store_true',
                    help='同曲已有 `drum_grid`/`Drums` 内容时**覆盖**（默认会先备份成 .bak_<时间>）')
    ap.add_argument('--yes', action='store_true',
                    help='非交互：跳过"已有内容将被覆盖"的提示（脚本内/批量用）')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--no-render', action='store_true')
    a = ap.parse_args()

    p = resolve_song(a.song)
    if not p:
        print('找不到曲目 %s（songs/<名>/song.json 也不存在）' % a.song)
        return 1
    print('[曲目] %s（参数 %s）' % (p, a.song))
    d = json_io.load(p)
    # **幂等 + 留痕**（`--force` / `--yes` 真的被用到，别声明了不读）：
    # 已有网格/鼓音时先把原文件备份一份，再覆盖 —— 沙锤层是**派生物**，覆盖前留退路。
    _had = bool(((d.get('patterns') or {}).get('drum_grid') or {}).get('per_bar')) or \
        bool((d.get('notes_extra') or {}).get('Drums'))
    if _had and not a.force:
        # **默认不覆盖**（与 `strip_drums.py` 同一口径：要覆盖得显式说）——
        # 沙锤层是派生物，静默覆盖会把上一次的实测网格换成另一套参数的结果，无从对比。
        print('  本曲已有鼓网格/鼓音 —— 不覆盖（要重算加 --force；原文件会先备份成 .bak_<时间戳>）')
        return 0
    if _had and not a.yes:
        print('  ! --force：将覆盖已有鼓网格/鼓音（原文件先备份成 .bak_<时间戳>）')
    if _had and not a.dry:
        import time as _t
        bak = p + '.bak_' + _t.strftime('%Y%m%d_%H%M%S')
        shutil.copy2(p, bak)
        print('  已备份 %s' % bak)
    bpm = a.bpm or float(d.get('bpm') or 120.0)
    band = tuple(float(x) for x in a.band.split(','))
    vb = tuple(int(x) for x in a.vel_off.split(','))
    vn = tuple(int(x) for x in a.vel_on.split(','))
    per_bar, st = hf_onsets(a.ref_drums, bpm, band, a.k, a.tol)
    nbar = len(per_bar)
    want = sum(s.get('bars', 0) for s in d['sections'])
    print('[参考] %s' % a.ref_drums)
    print('       高频起音 %d 个 · 对格命中 %d 个 · 逐小节中位 %d 点'
          % (st['peaks'], sum(st['perbar']),
             sorted(st['perbar'])[len(st['perbar']) // 2] if st['perbar'] else 0))
    print('       16 分格分布: %s' % st['grid'])
    print('       段总小节 %d vs 参考小节 %d %s'
          % (want, nbar, '' if want == nbar else '⚠ 不一致，按较短者裁'))
    if want != nbar:                       # 段数多出来就补空小节，多出来的裁掉
        per_bar = (per_bar + [{} for _ in range(max(0, want - nbar))])[:want]
    # ① 逐段：段内实测点数 → 目标点数（沙锤层每小节至少 3 点，
    #    ⚠ 低于 3 会被引擎的静音门整段吃光，见 build_grid 的文档）
    section_of, target, seg, bar0 = [], {}, [], 0
    for s in d['sections']:
        nb = int(s.get('bars') or 0)
        n = sum(len(per_bar[i]) for i in range(bar0, min(bar0 + nb, len(per_bar))))
        per = n / max(1, nb)
        tgt = max(3.0, per)                # 目标 = 实测密度，但不低于静音门
        for i in range(bar0, bar0 + nb):
            section_of.append(s.get('name'))
            target[s.get('name')] = tgt
        seg.append('%s:%.1f→%.1f点/小节' % (s.get('name'), per, tgt))
        bar0 += nb
    grid = build_grid(per_bar, vb, vn, section_of=section_of, target=target)
    # ② 逐段 `arr.perc`：**逐段写**（不是整曲一刀切）；本段实测 0 点 ⇒ 不出
    bar0 = 0
    for s in d['sections']:
        nb = int(s.get('bars') or 0)
        n = sum(len(per_bar[i]) for i in range(bar0, min(bar0 + nb, len(per_bar))))
        s.setdefault('arr', {})['perc'] = 0 if n == 0 else 2   # 2 = 满档力度（±0dB）
        bar0 += nb
    # 只留本层：清空转录鼓轨，写入网格
    d.setdefault('notes_extra', {})['Drums'] = []
    d.setdefault('patterns', {})['drum_grid'] = {'per_bar': grid}
    d['name'] = a.out or d['name']
    d['desc'] = (d.get('desc', '') +
                 '｜沙锤层：由参考 drums 分轨 4–10kHz 起音逐小节建网格（只放 GM 82/70，无 kick/snare）')
    print('[处理表] 逐段（实测→目标 点/小节）: %s' % ' '.join(seg))
    print('         网格：%d 小节 · 共 %d 点 · 空小节 %d'
          % (len(grid), sum(sum(len(v) for v in g.values()) for g in grid),
             sum(1 for g in grid if not g)))
    if a.dry:
        print('[dry] 不写盘')
        return 0
    json_io.save(p, d)
    print('[写] %s' % p)
    if a.no_render:
        return 0
    cmd = [sys.executable, os.path.join(HERE, 'make_song.py'), a.song, '--check']
    print('  $ %s' % ' '.join(cmd))
    rc = subprocess.run(cmd, cwd=ROOT).returncode
    if rc:
        return rc
    import mido
    folder = os.path.dirname(p)                 # **以解析出的 song.json 所在目录为准**
    cfg = json.load(open(os.path.join(folder, 'render.json'), encoding='utf-8'))
    mid = os.path.join(folder, cfg.get('mid') or (a.song + '.mid'))
    m = mido.MidiFile(mid)
    ch9 = {}
    for t in m.tracks:
        nm = ([x.name for x in t if x.type == 'track_name'] or ['?'])[0]
        for x in t:
            if x.type == 'note_on' and x.velocity > 0 and getattr(x, 'channel', -1) == 9:
                ch9.setdefault(nm, {}).setdefault(x.note, 0)
                ch9[nm][x.note] += 1
    print('[核对] %s' % mid)
    for nm, cnt in ch9.items():
        print('   %s 通道9 音高分布 %s' % (nm, cnt))
        bad = {k: v for k, v in cnt.items() if k in (35, 36, 38, 39, 40, 41, 43, 45, 47, 48, 50)}
        print('   ⇒ %s' % ('**有鼓组音色** ✗ %s' % bad if bad else '只有沙锤类音色 ✔'))
    if not ch9:
        print('   ⚠ 通道 9 一个音都没有（网格没生效？）')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
