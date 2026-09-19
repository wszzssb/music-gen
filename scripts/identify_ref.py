#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""identify_ref.py —— **参考曲识别**：一次跑完"这首曲子里到底有什么"。

为什么要独立成一个工具（用户 2026-09-19："不能每次都靠别人给音频级分轨，其它的歌没有，
能不能强化你自己的识别功能"）：

识别原曲配器以前是**靠外部产物**——要么等别人给分轨，要么读转录工具的通道标签。
实测两次翻车都出在这里：
  · 拿 YourMT3 的通道名当配器（那轨 'Arp' 8 千个音）→ 用户当场否掉："原曲是没有的"；
  · 反过来又拿 `probe_timbre --solo` 的三个高频段说"钢琴被埋"—— 钢琴能量在 160–1250Hz，
    那三列根本看不到 → 口径误用。
本工具把识别做成**可复现的一步**，并且**两个独立来源交叉验证**：

  ① **分离**（Demucs 6s，GPU ≈ 十几秒/首）：drums / bass / other / vocals / guitar / piano
     → 逐轨 **RMS · 活跃占比 · 频段占用** —— 配器构成的**能量口径**证据
  ② **多乐器转录**（YourMT3+ 13 通道，`--no-ymt3` 可关）：逐通道音符数/音域
     → 与 ① **交叉验证**：某声部只有转录说有、分离里没有能量 → 大概率是转录伪影
  ③ **画像**（复用 `profile_ref.py`）：BPM 层 · 调式 chroma · 8 小节块曲线

用法:
  python scripts\identify_ref.py "<参考音频>" [名字] [--bpm N]
                                 [--model htdemucs_6s] [--no-ymt3] [--ymt3-bsz auto]

  名字缺省取文件名；`--bpm` 建议显式给（两层速度都成立时必须钉死一层）。

产出:
  · 分轨音频  `stems/<模型>/<名字>/*.wav`（Demucs 原始产物）
  · 识别报告  `refs/identify/<名字>.json` —— 机器可读，含
      stems[]        逐声部：rms_db / active_pct / bands / energy_share
      ymt3_channels  逐通道音符数（转录侧；可能含伪影，按 ① 交叉判读）
      profile        画像名（BPM/调式/结构在 refs/<画像>.json）
      verdict        自动结论：哪些声部"确有"、哪些"疑似伪影"、哪些"分离没抓到"
  · 屏幕：一张"原曲里有什么"的表

判读口径（写死，免得下次又凭感觉）:
  · 某声部 **能量占比 ≥5% 且活跃 ≥40%** → 确有，配器要照做
  · **能量 <2% 或活跃 <15%** → 基本不存在，别为它加层（这条正是"琶音层"那次踩的坑）
  · 转录有、分离无 → 标 `suspect`（伪影候选），**不许**当配器依据
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cli_utf8 as _cu                                              # noqa: E402
_cu.setup()

import json_io                                                      # noqa: E402

ROOT = os.path.dirname(HERE)
STEMS = os.path.join(ROOT, 'stems')
IDENT = os.path.join(ROOT, 'refs', 'identify')
BANDS = [(20, 40), (40, 80), (80, 160), (160, 315), (315, 630),
         (630, 1250), (1250, 2500), (2500, 5000), (5000, 10000), (10000, 18000)]
STEM_ORDER = ('drums', 'bass', 'other', 'vocals', 'guitar', 'piano')
# 判读阈值（写死在这里 = 判据只有一份，自检与文档都引它）
SHARE_REAL = 5.0        # 能量占比 ≥5% 且
ACT_REAL = 40.0         # 活跃 ≥40% → 确有
SHARE_ABSENT = 2.0      # 能量 <2% 或
ACT_ABSENT = 15.0       # 活跃 <15% → 基本不存在
# YourMT3+ MC13 的 13 个解码通道名 —— **从转录 MIDI 的轨名读出来的**（不是猜的，也不是抄论文）：
# `stems/_ymt3/<曲>/<曲>.mid` 里轨名依次是 Acoustic Piano / Chromatic Percussion /
# Guitar (clean) / Bass / Strings / Synth Lead / Synth Pad / Drums。
YMT3_CHANNELS = ('Acoustic Piano', 'Chromatic Percussion', '', 'Guitar (clean)', 'Bass',
                 'Strings', '', '', '', 'Synth Lead', 'Synth Pad', '', 'Drums')
# 通道 → Demucs 6s 的轨（用于交叉；没有独立轨的乐器并进 other）
YMT3_TO_STEM = {'Acoustic Piano': 'piano', 'Chromatic Percussion': 'other',
                'Guitar (clean)': 'guitar', 'Bass': 'bass', 'Strings': 'other',
                'Synth Lead': 'other', 'Synth Pad': 'other', 'Drums': 'drums'}


def ml_python():
    """找 .venv-ml 的解释器（demucs / YourMT3 都在那里）"""
    env = os.environ.get('DSH_ML_PY')
    for c in ([env] if env else []) + [os.path.join(ROOT, '.venv-ml', 'Scripts', 'python.exe'),
                                       os.path.join(ROOT, '.venv-ml', 'bin', 'python')]:
        if c and os.path.exists(c):
            return c
    raise SystemExit('找不到 .venv-ml（demucs/YourMT3 在它里面）—— 见 ML.md §为什么要单独的 venv')


def run_stems(audio, name, model):
    """① Demucs 分离 → 分轨 wav 目录

    ⚠ `-o` 只给**根目录**：demucs 自己会加一层 `<模型名>/<曲名>/`
    （实测多给一层模型名 → 产物落在 `stems/htdemucs_6s/htdemucs_6s/<曲>/`，
      代码按单层找目录就会误判成"demucs 失败：0"）。
    """
    dst = os.path.join(STEMS, model, name)
    if os.path.isdir(dst) and any(f.endswith('.wav') for f in os.listdir(dst)):
        print('  分离结果已存在，跳过：%s' % dst)
        return dst
    cmd = [ml_python(), '-m', 'demucs', '-n', model, '-o', STEMS, audio]
    print('  分离中（%s）…' % model)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if not os.path.isdir(dst):
        raise SystemExit('demucs 没产出预期目录 %s（rc=%s）\n%s\n%s'
                         % (dst, r.returncode, (r.stdout or '')[-300:], (r.stderr or '')[-600:]))
    return dst


def measure(d):
    """逐声部量：RMS / 活跃占比 / 每倍频程相对电平 / 能量占比（与 v4stems 那次同口径）"""
    import numpy as np
    import soundfile as sf
    rows = []
    tot = 0.0
    for f in sorted(os.listdir(d)):
        if not f.lower().endswith(('.wav', '.flac', '.ogg')):
            continue
        p = os.path.join(d, f)
        y, sr = sf.read(p, dtype='float32', always_2d=True)
        y = y.mean(axis=1)
        k = max(1, sr // 12000)
        n = (len(y) // k) * k
        y = y[:n].reshape(-1, k).mean(axis=1)
        sr2 = sr // k
        win = max(1, sr2 // 10)
        nfr = len(y) // win
        fr = np.sqrt((y[:nfr * win] ** 2).reshape(nfr, win).mean(axis=1)) if nfr else np.zeros(1)
        peak = float(fr.max()) or 1e-9
        active = float((fr > peak * 0.05).mean())
        w = 2048
        nf = max(1, (len(y) - w) // (w // 2))
        idx = np.arange(w)[None, :] + (w // 2) * np.arange(nf)[:, None]
        P = np.abs(np.fft.rfft(y[idx] * np.hanning(w), axis=1)) ** 2
        freqs = np.fft.rfftfreq(w, 1 / sr2)
        bd = {}
        tot_band = P.sum(axis=1).mean() + 1e-12
        for lo, hi in BANDS:
            m = (freqs >= lo) & (freqs < hi)
            bd['%d-%d' % (lo, hi)] = round(float(10 * np.log10(
                max(1e-12, P[:, m].sum(axis=1).mean()) / tot_band)), 1)
        e = float((fr ** 2).sum())
        tot += e
        rows.append({'file': f, 'rms_db': round(float(20 * np.log10(max(1e-9, np.sqrt((y ** 2).mean())))), 2),
                     'active_pct': round(active * 100, 1), 'bands': bd,
                     'dur': round(len(y) / sr2, 1), '_e': e})
    for r in rows:
        r['energy_share'] = round(100 * r.pop('_e') / (tot or 1), 1)
    rows.sort(key=lambda r: -r['energy_share'])
    return rows


def run_ymt3(audio, outdir, bsz):
    """② YourMT3+ 转录（13 通道）→ 逐通道音符数"""
    script = os.path.join(HERE, 'transcribe_ymt3.py')
    if not os.path.exists(script):
        return None, '没有 transcribe_ymt3.py'
    os.makedirs(outdir, exist_ok=True)
    rep = os.path.join(outdir, '_report.json')
    if os.path.exists(rep):                     # 有缓存就别重跑（转录 ~75s/首）
        print('  转录结果已存在，跳过：%s' % rep)
    else:
        cmd = [ml_python(), script, audio, '-o', outdir, '--bsz', str(bsz)]
        print('  转录中（YourMT3+，13 通道）…')
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           errors='replace')
        if r.returncode != 0:
            return None, (r.stderr or r.stdout or '')[-400:]
    rep = os.path.join(outdir, '_report.json')
    if not os.path.exists(rep):
        return None, '没有 _report.json（见 %s）' % outdir
    raw = json.load(open(rep, encoding='utf-8'))
    it = (raw.get('items') or [{}])[0]
    pc = it.get('per_channel') or []
    chans = [{'channel': i,
              'name': (YMT3_CHANNELS[i] if i < len(YMT3_CHANNELS) else '?'),
              'notes': int(n)} for i, n in enumerate(pc)]
    tot = sum(c['notes'] for c in chans) or 1
    for c in chans:
        c['share_pct'] = round(100.0 * c['notes'] / tot, 1)
    chans = [c for c in chans if c['name']]
    return {'notes_total': it.get('notes'), 'infer_sec': it.get('infer_sec'),
            'midi': it.get('midi'),
            'channels': sorted(chans, key=lambda c: -c['notes'])}, None


def cross_check(stems, ymt3):
    """**两路交叉**：Demucs 分离（能量口径） × YourMT3 转录（音符口径）。

    为什么必须交叉（BGM35 上拿历史音频分轨当部分真值实测）：
      · **Piano**：真值 29.4% 能量；YourMT3 给 2377 音（27.4%）✓ 一致；
        **Demucs 6s 只给 7.0%** ✗ —— 单靠 demucs 会把"第一主力钢琴"判成小配角，
        这正是本轮把 40 号改编配时差点走错的依据。
      · **Guitar**：真值 5.8%；YourMT3 450 音（5.2%）✓；Demucs 给 14.7% ✗。
    所以：**冲突时以 YourMT3 的音数为准**，Demucs 用于"某声部是否存在 / 活跃度"。
    """
    out = {'agree': [], 'conflict': [], 'ymt3_only': [], 'demucs_only': [], 'absent': [],
           'rule': '名次差 ≤1 才算一致；量级差多少都照实写。冲突时**以 ymt3 音数为准**'}
    ymap = {}
    for c in (ymt3 or {}).get('channels') or []:
        k = YMT3_TO_STEM.get(c['name'], 'other')
        ymap[k] = ymap.get(k, 0) + c['share_pct']
    smap = {os.path.splitext(s['file'])[0].lower(): s for s in stems}
    rk_d = [n for n in sorted(smap, key=lambda n: -smap[n]['energy_share']) if n != 'vocals']
    rk_y = sorted(ymap, key=lambda k: -ymap[k])
    for name in rk_d:
        s = smap[name]
        yshare = ymap.get(name)
        if yshare is None:
            out['demucs_only'].append(name)
            continue
        rd = rk_d.index(name) + 1
        ry = rk_y.index(name) + 1 if name in rk_y else 99
        txt = ('%s（demucs %.1f%% 第%d / ymt3 %.1f%% 第%d）'
               % (name, s['energy_share'], rd, yshare, ry))
        (out['agree'] if abs(rd - ry) <= 1 else out['conflict']).append(txt)
    for name in sorted(smap):
        s = smap[name]
        if name == 'vocals' and s['energy_share'] < SHARE_ABSENT and s['active_pct'] < ACT_ABSENT:
            out['absent'].append('人声（分离能量 0%% / 活跃 %.1f%% → 器乐版）' % s['active_pct'])
    for c in (ymt3 or {}).get('channels') or []:
        k = YMT3_TO_STEM.get(c['name'])
        if k and k not in smap and c['notes'] >= 100:
            out['ymt3_only'].append('%s（%d 音）' % (c['name'], c['notes']))
    return out


def verdict(stems, ymt3):
    """（旧接口保留）单路判读：确有的 / 基本不存在的"""
    real, absent = [], []
    for s in stems:
        nm = os.path.splitext(s['file'])[0].lower()
        if s['energy_share'] >= SHARE_REAL and s['active_pct'] >= ACT_REAL:
            real.append(nm)
        elif s['energy_share'] < SHARE_ABSENT or s['active_pct'] < ACT_ABSENT:
            absent.append(nm)
    return {'real': real, 'absent': absent}


def main():
    ap = argparse.ArgumentParser(description='参考曲识别：分离 + 转录交叉验证 → 原曲里有什么')
    ap.add_argument('audio')
    ap.add_argument('name', nargs='?', default='')
    ap.add_argument('--bpm', type=float, default=0.0)
    ap.add_argument('--model', default='htdemucs_6s')
    ap.add_argument('--no-ymt3', action='store_true')
    ap.add_argument('--ymt3-bsz', default='auto')
    a = ap.parse_args()

    if not os.path.isfile(a.audio):
        raise SystemExit('找不到音频 %s' % a.audio)
    name = a.name or re.sub(r'\W+', '_', os.path.splitext(os.path.basename(a.audio))[0])
    print('== 识别参考曲 %s ==' % name)

    dst = run_stems(a.audio, name, a.model)
    stems = measure(dst)
    print('  逐声部（能量口径）：')
    for s in stems:
        print('    %-10s RMS %6.2f dB  活跃 %5.1f%%  能量占比 %5.1f%%'
              % (s['file'], s['rms_db'], s['active_pct'], s['energy_share']))

    ymt3, err = (None, 'by request') if a.no_ymt3 else run_ymt3(
        a.audio, os.path.join(STEMS, '_ymt3', name), a.ymt3_bsz)
    if err:
        print('  转录侧：未取到（%s）' % err)

    # ③ 画像（复用 profile_ref.py，BPM 建议显式钉死）
    prof = None
    try:
        args = [sys.executable, os.path.join(HERE, 'profile_ref.py'), a.audio, name]
        if a.bpm:
            args += ['--bpm', str(a.bpm)]
        subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                       errors='replace', cwd=ROOT)
        pp = os.path.join(ROOT, 'refs', '%s.json' % name)
        if os.path.exists(pp):
            prof = json.load(open(pp, encoding='utf-8'))
    except Exception as e:                                          # noqa: BLE001
        print('  画像侧失败：%s' % str(e)[:80])

    cc = cross_check(stems, ymt3)
    v = verdict(stems, ymt3)
    os.makedirs(IDENT, exist_ok=True)
    out = {'name': name, 'audio': a.audio, 'model': a.model,
           'stems': [{k: x for k, x in s.items()} for s in stems],
           'ymt3': ymt3, 'ymt3_error': err,
           'profile': {'bpm': (prof or {}).get('bpm'), 'duration': (prof or {}).get('duration'),
                       'width': (prof or {}).get('width'), 'centroid': (prof or {}).get('centroid'),
                       'rms_db': (prof or {}).get('rms_db'),
                       'quiet_chroma': (prof or {}).get('quiet_chroma')},
           'cross': cc, 'verdict': v,
           'thresholds': {'share_real': SHARE_REAL, 'active_real': ACT_REAL,
                          'share_absent': SHARE_ABSENT, 'active_absent': ACT_ABSENT}}
    p = os.path.join(IDENT, name + '.json')
    open(p, 'w', encoding='utf-8').write(json_io.dumps(out))
    if isinstance(ymt3, dict) and (ymt3.get('channels')):
        print()
        print('  多乐器转录（YourMT3+ 13 通道，音符口径）：')
        for c in ymt3['channels']:
            if c['notes']:
                print('    ch%-3d %-24s %5d 音  %5.1f%%' %
                      (c['channel'], c['name'], c['notes'], c['share_pct']))
    print()
    print('  **两路一致**（demucs 能量 × ymt3 音数都指向同一结论）：')
    for x in cc['agree'] or ['—']:
        print('    ✓ %s' % x)
    print('  **两路冲突**（以 ymt3 音数为准 —— BGM35 实测 demucs 会把钢琴判小）：')
    for x in cc['conflict'] or ['—']:
        print('    ⚠ %s' % x)
    if cc['ymt3_only']:
        print('  **只有转录说有**（≥100 音；当配器依据前先复核）：%s' % cc['ymt3_only'])
    if cc['absent']:
        print('  **基本不存在**（别为它加层）：%s' % cc['absent'])
    print('报告：%s' % p)
    print('分轨：%s' % dst)
    return 0


if __name__ == '__main__':
    sys.exit(main())
