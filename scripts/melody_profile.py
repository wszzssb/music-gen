#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""melody_profile.py —— 从**成品混音**里扒"旋律语言"，并和我们的 song.json 对比。

为什么需要它：前面的工作都在对齐"频段/律动/织体"，但用户要的是"像例曲"，
而**旋律语言**（音域/音级偏好/音程走向/节奏密度/乐句长短）是最直接的一层。
密集混音里的主旋律**不可能扒得逐音准确**（这是公认难题），所以本工具的目标不是
"复制旋律"，而是**统计特征**：把它当作"这位作曲家在这种曲子里怎么写旋律"的画像。

做法：
  1. 对数频率上的谐波求和（HPS）逐帧估 F0，候选音域可配置（默认 MIDI 55–86）
  2. 谐波求和的经典陷阱是"低八度误判" → 取 salience ≥ 0.9×最大值里**最高**的那个候选
  3. 中值滤波 + 连续稳定 ≥3 帧算一个音 → 量化到 16 分格
  4. 只保留"附近有起音"的音（避开持续垫底），再统计：
     音级直方图（相对主音）、音程直方图、音域、每小节音符数、时值分布、
     16 分格落点分布（切分倾向）、乐句长度（按休止切分）
  5. **自检**：统计落在调内音的比例（调内率）——低说明扒错了，必须如实报告

用法:
  python scripts\\melody_profile.py <参考曲> <画像名> [--tonic G] [--minor]
  python scripts\\melody_profile.py --compare songs\\14_d75_pulse\\song.json refs\\BGM33_melody.json
"""

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）

import metrics      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# 旋律画像放子目录：`refs/*.json` 是**参考曲频谱画像**（自检会校验它们的字段），
# 混在一起会让 refs_schema 报"缺字段"（踩过）
MELDIR = os.path.join(ROOT, 'refs', 'melody')
NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
MAJOR = [0, 2, 4, 5, 7, 9, 11]
MINOR = [0, 2, 3, 5, 7, 8, 10]          # 自然小调（多利亚另算 A=9 也在调内）


def f0_track(m, sr, lo=67, hi=88, n=4096, hop=512):
    """逐帧估 F0。密集混音里"响度最大"的往往是低音/伴奏，所以这里做三件事：
      ① 高通 250Hz 去掉贝斯与底鼓（它们会主导谐波求和）
      ② **谱白化**（除以全曲平均谱）→ 按"谐波结构"而不是"响度"判 F0
      ③ 候选取 salience ≥0.9×最大里**最高**的 → 抗低八度误判
    返回 (帧时刻, MIDI 音高 or -1, 该帧置信度)"""
    # ① 简单一阶高通（差分 + 去直流）在频域实现：直接用 250Hz 以上的频点
    frames = np.lib.stride_tricks.sliding_window_view(m, n)[::hop]
    X = np.abs(np.fft.rfft(frames * np.hanning(n), axis=1))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    keep = freqs >= 250.0
    # ② 谱白化：除以全曲平均谱（平滑后），压掉音色包络
    avg = X.mean(axis=0)
    k = 41
    smooth = np.convolve(avg, np.ones(k) / k, mode='same') + 1e-9
    W = X / smooth
    W[:, ~keep] = 0.0
    cands = np.arange(lo, hi + 1)
    sal = np.zeros((len(cands), W.shape[0]))
    tot = W.sum(axis=1) + 1e-9
    for i, midi in enumerate(cands):
        f0 = 440.0 * 2 ** ((midi - 69) / 12)
        harm = [f0 * h for h in range(1, 5) if f0 * h < sr / 2]
        idx = np.clip(np.round(np.array(harm) / (sr / n)).astype(int), 0, len(freqs) - 1)
        sal[i] = W[:, idx].sum(axis=1) / tot          # 谐波占该帧能量的比例 = 谐波性
    best = sal.argmax(axis=0)
    mx = sal.max(axis=0)
    out, conf = [], []
    prev = None
    for j in range(sal.shape[1]):
        if mx[j] <= 0:
            out.append(-1)
            conf.append(0.0)
            continue
        cand = np.nonzero(sal[:, j] >= 0.85 * mx[j])[0]
        # **连续性先验**：近似并列的候选里，选离上一个音最近的那个
        # （"永远取最高"会系统性地偏高八度 —— 之前均值 81.3 就是这么来的）
        opts = list(cands[cand])
        if prev is None:
            pick = opts[-1]
        else:
            pick = min(opts, key=lambda m: abs(int(m) - prev))
        out.append(int(pick))
        conf.append(float(mx[j]))
        if mx[j] > 0.5 * mx.max():
            prev = int(pick)
    return np.arange(len(out)) * hop / sr, np.array(out), np.array(conf)


def onsets(m, sr, n=1024, hop=256, k_sigma=2.5, min_gap=0.15):
    """谱通量起音峰：阈值取 mean+2.5σ 且**强制最小间隔**（密集混音里每个镲都会触发，
    不设间隔会得到 30+/秒 的假起音）"""
    frames = np.lib.stride_tricks.sliding_window_view(m, n)[::hop]
    X = np.abs(np.fft.rfft(frames * np.hanning(n), axis=1))
    flux = np.maximum(X[1:] - X[:-1], 0).sum(axis=1)
    thr = flux.mean() + k_sigma * flux.std()
    keep, last = [], -1e9
    for i in range(1, len(flux) - 1):
        t = i * hop / sr
        if flux[i] > thr and flux[i] >= flux[i - 1] and flux[i] > flux[i + 1] \
                and t - last >= min_gap:
            keep.append(t)
            last = t
    return np.array(keep)


def track_to_notes(times, pitch, conf, bpm, onset_times, min_conf=0.006,
                   stable_tol=1):
    """在**起音处**取音，并要求音高在起音后**保持稳定**（旋律音会持续，
    打击瞬态的 F0 是随机的 → 用"稳定性"把鼓点筛掉）。"""
    spb = 60.0 / bpm
    notes = []
    for t in onset_times:
        lo = np.searchsorted(times, t + 0.02)
        hi = np.searchsorted(times, t + 0.12)
        if hi <= lo:
            continue
        k = lo + int(np.argmax(conf[lo:hi]))
        if conf[k] < min_conf or pitch[k] <= 0:
            continue
        # 稳定性：起音后 0.05/0.12/0.2 秒处的音高要与它一致（±stable_tol 半音）
        later = []
        for dt in (0.05, 0.12, 0.20):
            j = np.searchsorted(times, t + dt)
            if j < len(pitch) and pitch[j] > 0:
                later.append(int(pitch[j]))
        if not later or not all(abs(p - pitch[k]) <= stable_tol for p in later):
            continue
        b = round(t / spb * 4) / 4
        notes.append((b, int(pitch[k])))
    # 同拍上多个 → 保留一个；相邻同音高 → 合并起点
    out = []
    for (b, p) in notes:
        if out and abs(out[-1][0] - b) < 0.24:
            continue
        out.append((b, p))
    merged = []
    for (b, p) in out:
        if merged and merged[-1][1] == p and b - merged[-1][0] < 0.6:
            continue
        merged.append((b, p))
    # 生成时值：到下一个音为止（上限 8 个十六分）
    notes2 = []
    for i, (b, p) in enumerate(merged):
        nb = merged[i + 1][0] if i + 1 < len(merged) else b + 1.0
        notes2.append((b, min(nb, b + 2.0), p))
    return notes2


def stats(notes, bpm, tonic_pc, scale):
    """从音符序列算旋律语言统计"""
    bars_per = 4.0
    if not notes:
        return {}
    deg = [((p - tonic_pc) % 12) for (_a, _b, p) in notes]
    hist = {NAMES[(tonic_pc + d) % 12]: int(deg.count(d)) for d in range(12)}
    dia = sum(1 for d in deg if d in scale)
    iv = [notes[i + 1][2] - notes[i][2] for i in range(len(notes) - 1)]
    dur16 = [int(round((b - a) * 4)) for (a, b, _p) in notes]
    pos16 = [int(round(a * 4)) % 16 for (a, _b, _p) in notes]
    end = max(b for (_a, b, _p) in notes)
    bars = max(1.0, end / bars_per)
    # 乐句：休止 ≥ 1 拍切开
    phrases, cur = [], []
    prev_end = None
    for (a, b, p) in notes:
        if prev_end is not None and a - prev_end >= 1.0:
            phrases.append(cur)
            cur = []
        cur.append((a, b, p))
        prev_end = b
    if cur:
        phrases.append(cur)
    return {
        'notes': len(notes),
        'range': [min(p for (_a, _b, p) in notes), max(p for (_a, _b, p) in notes)],
        'mean_pitch': round(float(np.mean([p for (_a, _b, p) in notes])), 1),
        'scale_degree_hist': hist,
        'diatonic_pct': round(100.0 * dia / len(notes), 1),
        'interval_hist': {str(k): int(iv.count(k)) for k in sorted(set(iv))},
        'stepwise_pct': round(100.0 * sum(1 for x in iv if abs(x) <= 2) / max(1, len(iv)), 1),
        'notes_per_bar': round(len(notes) / bars, 2),
        'dur16_hist': {str(k): int(dur16.count(k)) for k in sorted(set(dur16))},
        'onset16_hist': {str(k): int(pos16.count(k)) for k in sorted(set(pos16))},
        'onbeat_pct': round(100.0 * sum(1 for k in pos16 if k % 4 == 0) / len(pos16), 1),
        'phrases': len(phrases),
        'phrase_bars': [round(v[-1][1] / 4.0 - v[0][0] / 4.0, 2) for v in phrases],
        'bars': round(bars, 1),
    }


def find_profile(name, root=None):
    """画像名 → 画像文件路径（**唯一解析口径**：`refs/melody/` → `refs/themes/`）。

    为什么要有这个函数：主题模板包也会产出旋律画像（`refs/themes/<主题>_melody.json`，
    来自 MIDI 模板、比扒谱更准），但过去三处工具各自按 `refs/melody/` 硬拼路径 ——
    于是主题生成的曲子会被**静默跳过**（"碎音对照画像"没对照、"旋律像不像画像"没检查）。
    一处漏 + 一处漏 = 守卫看起来全绿，其实什么都没查。
    """
    root = root or ROOT
    for sub in (os.path.join(root, 'refs', 'melody'), os.path.join(root, 'refs', 'themes')):
        p = os.path.join(sub, name + '_melody.json')
        if os.path.isfile(p):
            return p
    return None


def profile_ref(path, name, tonic='G', minor=True):
    m, sr, _x = metrics.load(path)
    bpm = metrics.detect_bpm(m, sr)[0]
    times, pitch, conf = f0_track(m, sr)
    ons = onsets(m, sr)
    notes = track_to_notes(times, pitch, conf, bpm, ons)
    pc = NAMES.index(tonic)
    st = stats(notes, bpm, pc, MINOR if minor else MAJOR)
    st.update({'name': name, 'file': path, 'bpm': bpm, 'tonic': tonic,
               'mode': 'minor' if minor else 'major'})
    os.makedirs(MELDIR, exist_ok=True)
    out = os.path.join(MELDIR, name + '_melody.json')
    json.dump(st, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('== %s 的旋律语言 ==  调内率 %.1f%%（低于 80%% 说明扒错了，别用）'
          % (name, st['diatonic_pct']))
    # 调内率低有两种原因：**扒错了**，或**主音给错了**。后者靠"换主音重算"即可区分 ——
    # 实测：BGM16c 用 --tonic C# 得 9.6%（像扒错），换 F 得 90.4%（其实没扒错）。
    if st['diatonic_pct'] < 80.0:
        cands = []
        for i2, tn in enumerate(NAMES):
            d2 = sum(1 for (_a, _b, p) in notes if ((p - i2) % 12) in (MINOR if minor else MAJOR))
            cands.append((tn, 100.0 * d2 / max(1, len(notes))))
        cands.sort(key=lambda z: -z[1])
        # **不能只取"调内率最高"的那个**：实测 BGM16c 的 5 个音级（F G A A# C）
        # 恰好被 D 大调全覆盖 → D 得 100%，而真正的主音 F 只有 90.4%。
        # 所以要跟**和声分析**（analyze_chords 的调性）对齐，一致的那个优先。
        ref_pc = None
        try:
            import glob as _g
            for fp in _g.glob(os.path.join(ROOT, 'refs', '*.json')):
                fn = os.path.basename(fp)[:-5]
                if fn in path or os.path.basename(path).startswith(fn):
                    rp = json.load(open(fp, encoding='utf-8'))
                    qc = rp.get('quiet_chroma') or {}
                    if qc and rp.get('centroid') is not None:
                        ref_pc = NAMES.index(max(qc, key=qc.get))
                    break
        except Exception:
            ref_pc = None
        pick = None
        if ref_pc is not None:
            for tn, pct in cands:
                if NAMES.index(tn) == ref_pc and pct >= 80.0:
                    pick = (tn, pct)
                    break
        if pick is None:
            pick = cands[0] if cands and cands[0][1] >= 80.0 else None
        if pick and pick[0] != tonic:
            extra = ('（与和声分析的调性一致）' if ref_pc is not None
                     and NAMES.index(pick[0]) == ref_pc else '')
            print('  ! 但换个主音就成立了：--tonic %s → %.1f%%%s —— **先确认是不是主音给错了**，'
                  '别急着当"扒错"（实测踩过）' % (pick[0], pick[1], extra))
        else:
            print('  （换遍 12 个主音都不达标 → 确实是扒取失败：加人声分离，'
                  '或用 --lo/--hi 缩窄音域再试）')
    print('  音域 %d–%d（均值 %.1f）  每小节 %.2f 个音  乐句 %d 个，长度(小节) %s'
          % (st['range'][0], st['range'][1], st['mean_pitch'], st['notes_per_bar'],
             st['phrases'], st['phrase_bars'][:8]))
    print('  音级直方图: %s' % ' '.join('%s:%d' % (k, v) for k, v in st['scale_degree_hist'].items() if v))
    print('  音程: %s（级进占 %.0f%%）' % (st['interval_hist'], st['stepwise_pct']))
    print('  时值(16分): %s' % st['dur16_hist'])
    print('  落点(16分格): %s  正拍占 %.0f%%' % (st['onset16_hist'], st['onbeat_pct']))
    print('  画像已存: %s' % out)
    return st


def song_melody_stats(song_json):
    """把 song.json 里的旋律（含加花）按同样口径统计（便于对比）"""
    import song_engine
    d = song_engine.load(song_json)
    notes = []
    for sec in d['sections']:
        for (b, beat, dur, p) in list(d['melody'].get(sec['melody'], [])) + \
                list(sec.get('melody_extra') or []):
            base = sum(s['bars'] for s in d['sections'][:d['sections'].index(sec)])
            notes.append(((base + b) * 4.0 + beat, (base + b) * 4.0 + beat + dur, int(p)))
    notes.sort()
    st = stats(notes, d['bpm'], NAMES.index('G'), MINOR)
    st['name'] = d.get('name', '?')
    return st


def compare(song_json, ref_json):
    a = json.load(open(ref_json, encoding='utf-8'))
    b = song_melody_stats(song_json)
    print('%-14s %10s %10s' % ('指标', '例曲', '本曲'))
    for k, label, fmt in (('notes_per_bar', '每小节音符', '%.2f'),
                          ('stepwise_pct', '级进占比%', '%.0f'),
                          ('onbeat_pct', '正拍占比%', '%.0f'),
                          ('mean_pitch', '平均音高', '%.1f'),
                          ('phrases', '乐句数', '%d')):
        print('%-14s %10s %10s' % (label, fmt % a[k], fmt % b[k]))
    print('%-14s %10s %10s' % ('音域', '%d-%d' % tuple(a['range']), '%d-%d' % tuple(b['range'])))
    print('\n音级直方图（相对主音）:')
    print('  例曲 %s' % ' '.join('%s:%d' % (k, v) for k, v in a['scale_degree_hist'].items() if v))
    print('  本曲 %s' % ' '.join('%s:%d' % (k, v) for k, v in b['scale_degree_hist'].items() if v))
    print('\n时值(16分):')
    print('  例曲 %s' % a['dur16_hist'])
    print('  本曲 %s' % b['dur16_hist'])
    print('\n落点(16分格):')
    print('  例曲 %s' % a['onset16_hist'])
    print('  本曲 %s' % b['onset16_hist'])


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--compare' in sys.argv:
        compare(args[0], args[1])
        return 0
    if len(args) < 2:
        print(__doc__)
        return 1
    tonic = sys.argv[sys.argv.index('--tonic') + 1] if '--tonic' in sys.argv else 'G'
    profile_ref(args[0], args[1], tonic, '--major' not in sys.argv)
    return 0


if __name__ == '__main__':
    sys.exit(main())
