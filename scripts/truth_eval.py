#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""truth_eval.py —— **对着真值 MIDI 量转录精度**（网络成品提取审计用的那把尺子）

什么时候用它：手上**同时有**"音频提取出来的 MIDI"和"该曲的权威 MIDI"（真值）时。
`eval_transcription.py` 是通用口径（16 分格 F1），本工具在**有真值**的前提下多回答四件事，
每一条都是 2026-09-27 那轮审计实测踩出来的（细节 → `docs/TRANSCRIBE-AUDIT.md`）：

  ① **时间轴要按完整 tempo map 算** —— `midi_file.import_midi` 只暴露**首个** bpm。
     实测（Scarlatti K.159，25 条 set_tempo、两处渐慢各堆积 537ms）：按单值换算时
     16 分格 F1 只有 **0.208**，而真值是 **0.947** —— 差的是**尺子**，不是转录。
  ② **恒定偏移**：同一编配的 MP3 与 MIDI 常差一个**常数**（实测 −78ms，逐 10 秒窗极差仅 5ms）。
     `eval_transcription.py --shift` 步长 0.1s，对 <100ms 的偏移**没有分辨力**（它给 0.113）。
     本工具用**同音高 dt 直方图峰位 + 抛物插值**（尖峰，不受 ±tol 平台影响）。
  ③ **同曲不同版本**：网上的 MP3 可能挂的是**另一版本**（实测差一个恒定百分比速度 0.9904/0.9908）
     —— `--warp` 拟线性时间规整（速度比 + 偏移），并用**逐窗偏移极差**当准入检查
     （同源 ≤13ms、不同版本 ~1.5s）。
  ④ **度量口径三处必须改**（否则读数是假的）：
     · 鼓**按通道 9** 判（外部 GM 编配的鼓轨名五花八门：`Drums`/`Track 3`/`By Stellar Ice`…）；
     · 鼓**按鼓族**评（不同鼓组的 GM 键词汇不同，按 raw key 比会得到"0 匹配"的假读数）；
     · 真值里**同音高近同时**的音（两件乐器齐奏/扫弦）转录方物理上分不开，
       一对一匹配会把它们算成漏音（实测占 2.4%–28.6%，Muse 的音高声部 39.1%）
       → 提供**去重口径**（同音高 ≤30ms 折叠，两侧对称）。

用法：
    python scripts\truth_eval.py <被评.mid> --ref <真值.mid> [--json out.json]
    python scripts\truth_eval.py <被评.mid> --ref <真值.mid> --warp       # 允许速度差
    python scripts\truth_eval.py --selftest                              # 尺子自检（6 组已知答案）

⚠ **判断按逐角色看**（贝斯/旋律/和声/鼓）—— 全曲平均会把"哪一层塌了"抹平：
实测密集混音里贝斯召回 51–89%、和声织体只有 25–47%。
"""
import argparse
import bisect
import json
import os
import struct
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import midi_file          # noqa: E402
import midi_probe         # noqa: E402

GRID = 0.1                # 16 分格口径的格宽（秒），与 eval_transcription 一致
DEDUP_TOL = 0.03          # 同音高近同时折叠阈值（秒）
DRUM_CH = 9

# 鼓族：不同鼓组的 GM 键词汇不同，**按族比**才有意义
FAMILY = {}
for _ks, _nm in (((35, 36), 'kick'), ((37, 38, 40), 'snare'), ((39,), 'clap'),
                 ((42, 44, 46), 'hat'), ((49, 51, 52, 53, 55, 57, 59), 'cymbal'),
                 ((41, 43, 45, 47, 48, 50), 'tom')):
    for _k in _ks:
        FAMILY[_k] = _nm
FAMILIES = ['kick', 'snare', 'clap', 'hat', 'cymbal', 'tom', 'perc']

BASS_PROG = set(range(32, 40)) | {42, 43, 44}
MEL_PROG = set(range(65, 72)) | {53, 54}
MEL_NAME = ('melody', 'voice', 'vocal', 'lead', 'choir', 'ooh')


# ---------------------------------------------------------------- tempo map
def _vlq(d, i):
    v = 0
    while True:
        b = d[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not (b & 0x80):
            return v, i


def tempo_events(path):
    """扫**全部轨**的 set_tempo → (division, [(tick, us_per_quarter, bpm)])。

    同一 tick 多个事件取**文件里最后一条**（标准语义）。"""
    data = open(path, 'rb').read()
    if data[:4] != b'MThd':
        raise ValueError('不是 MIDI 文件: %s' % path)
    div = struct.unpack('>H', data[12:14])[0]
    ntrk = struct.unpack('>H', data[10:12])[0]
    pos, raw = 14, []
    for _ in range(ntrk):
        if data[pos:pos + 4] != b'MTrk':
            break
        ln = struct.unpack('>I', data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + ln]
        i, tick, running = 0, 0, None
        while i < len(body):
            d, i = _vlq(body, i)
            tick += d
            b = body[i]
            if b == 0xFF:
                i += 1
                mt = body[i]
                i += 1
                l, i = _vlq(body, i)
                payload = body[i:i + l]
                i += l
                if mt == 0x51:
                    raw.append((tick, int.from_bytes(payload, 'big')))
                running = None
            elif b in (0xF0, 0xF7):
                i += 1
                l, i = _vlq(body, i)
                i += l
                running = None
            else:
                if b & 0x80:
                    running = b
                    i += 1
                i += 1 if (running & 0xF0) in (0xC0, 0xD0) else 2
        pos += 8 + ln
    merged = {}
    for (t, us) in raw:
        merged[t] = us
    return div, [(t, us, 60_000_000.0 / us) for t, us in sorted(merged.items())]


def make_tick2sec(path, pre_tempo='first'):
    """→ (tick→秒 函数, division, tempo 事件表)。

    `pre_tempo`：首个 set_tempo **之前**那段怎么算 ——
      `'first'`（默认）从第 0 拍就用首个 tempo（渲染器多是这样）；
      `'default120'` 按 MIDI 标准语义（默认 120BPM）。两者只差一个**常数**，
      取 `first` 只是让"渲染起始偏移"这一项更小、更好归因（实测 78ms vs 252ms）。"""
    div, ev = tempo_events(path)
    if not ev:
        return (lambda tick: tick / float(div) * 0.5), div, ev

    def tick2sec(tick):
        t, prev_tick = 0.0, 0
        prev_us = 500000 if pre_tempo == 'default120' else ev[0][1]
        for (et, us, _bpm) in ev:
            if et >= tick:
                break
            t += (et - prev_tick) / float(div) * (prev_us / 1e6)
            prev_tick, prev_us = et, us
        t += (tick - prev_tick) / float(div) * (prev_us / 1e6)
        return t
    return tick2sec, div, ev


def load_notes(path, drop_drum=True):
    """→ (notes, meta)；notes = [(起秒, 时值秒, 音高, 力度, 角色标签)]。

    ⚠ 时间轴按 **tempo map** 算（不是 `60/bpm × 拍`）；鼓**按通道 9** 判并统一标 `drums`。"""
    m = midi_file.import_midi(path)
    tick2sec, div, ev = make_tick2sec(path)
    out = []
    for tr in m.get('tracks', []):
        is_drum = bool(tr.get('drum')) or tr.get('channel') == DRUM_CH
        if drop_drum and is_drum:
            continue
        name = 'drums' if is_drum else (tr.get('name') or '?')
        for (st, du, p, v) in tr.get('notes', []):
            a = tick2sec(float(st) * div)
            b = tick2sec((float(st) + float(du)) * div)
            out.append((a, max(0.0, b - a), int(p), int(v), name))
    out.sort()
    meta = dict(bpm=m.get('bpm'), division=div, n_tracks=len(m.get('tracks', [])),
                n_tempo_events=len(ev), first_bpm=(ev[0][2] if ev else None))
    return out, meta


# ---------------------------------------------------------------- 角色 / 鼓族
def roles_of(path):
    """→ {轨名: 角色}；角色 ∈ bass / melody / harmony / drums。"""
    m = midi_file.import_midi(path)
    out = {}
    for tr in m.get('tracks', []):
        nm = (tr.get('name') or '').lower()
        prog = tr.get('program')
        if tr.get('channel') == DRUM_CH or tr.get('drum'):
            r = 'drums'
        elif 'bass' in nm or prog in BASS_PROG:
            r = 'bass'
        elif any(k in nm for k in MEL_NAME) or prog in MEL_PROG:
            r = 'melody'
        else:
            r = 'harmony'
        out[tr.get('name') or '?'] = r
    return out


def role_tagged(path, notes):
    """给 load_notes 的结果贴上角色标签（鼓另走族映射）。"""
    rn = roles_of(path)
    return [(t, d, p, v, 'drums' if n == 'drums' else rn.get(n, 'harmony'))
            for (t, d, p, v, n) in notes]


def to_family(notes):
    """鼓 → 族编号（复用同一套匹配器；raw 鼓键不能直接比）。"""
    return [(t, d, FAMILIES.index(FAMILY.get(p, 'perc')), v, n) for (t, d, p, v, n) in notes]


def subset(notes, drums):
    return [n for n in notes if (n[4] == 'drums') == drums]


def dedup(notes, tol=DEDUP_TOL):
    """同音高、时间差 ≤tol 的音**链式归组**，每组留一个代表 —— 齐奏/扫弦在听感上是一个音事件。

    返回 [(代表音, 组内音数)]。⚠ 两侧要**对称**做（只折叠一侧会把读数带偏）。"""
    by_pitch = defaultdict(list)
    for n in notes:
        by_pitch[n[2]].append(n)
    out = []
    for _p, arr in by_pitch.items():
        arr.sort(key=lambda n: n[0])
        cur = [arr[0]]
        for n in arr[1:]:
            if n[0] - cur[-1][0] <= tol:
                cur.append(n)
            else:
                out.append((cur[0], len(cur)))
                cur = [n]
        out.append((cur[0], len(cur)))
    out.sort(key=lambda x: x[0][0])
    return out


# ---------------------------------------------------------------- 匹配 / 口径
def keymap(notes):
    d = defaultdict(int)
    for (t, _d, p, _v, _n) in notes:
        d[(int(round(t / GRID)), p)] += 1
    return d


def grid_f1(ref, mine, dt=0.0):
    """16 分格口径（与 eval_transcription 一致）：同格同音高一对一计数。"""
    a = keymap(ref)
    b = keymap([(t + dt, d, p, v, n) for (t, d, p, v, n) in mine])
    m = sum(min(n, b.get(k, 0)) for k, n in a.items())
    nr, nm = sum(a.values()), sum(b.values())
    P = m / nm if nm else 0.0
    R = m / nr if nr else 0.0
    return (2 * P * R / (P + R) if P + R else 0.0), P, R


def note_f1(ref, mine, tol=0.05):
    """音符级口径：同音高、|dt|≤tol 的**一对一**匹配。"""
    pairs, missed, extra = match_notes(ref, mine, tol)
    P = len(pairs) / max(1, len(mine))
    R = len(pairs) / max(1, len(ref))
    return (2 * P * R / (P + R) if P + R else 0.0), P, R, pairs, missed, extra


def match_notes(ref, mine, tol=0.05):
    """→ (pairs, missed, extra)；pairs = [(参照下标, 被评下标, dt)]（时间推进取最近）。"""
    by_pitch = defaultdict(list)
    for i, n in enumerate(mine):
        by_pitch[n[2]].append(i)
    used, pairs, missed = set(), [], []
    for ri, r in enumerate(ref):
        cands = [i for i in by_pitch.get(r[2], []) if i not in used and abs(mine[i][0] - r[0]) <= tol]
        if not cands:
            missed.append(ri)
            continue
        bi = min(cands, key=lambda i: abs(mine[i][0] - r[0]))
        used.add(bi)
        pairs.append((ri, bi, mine[bi][0] - r[0]))
    return pairs, missed, [i for i in range(len(mine)) if i not in used]


def dt_hist(ref, mine, lo=-0.6, hi=0.6, bin_s=0.005, smooth_ms=40):
    """同音高音符对的 dt 直方图 → **尖峰**估计 (最佳 dt 秒, 峰值得分, 峰值占比)。

    ⚠ 别用"匹配数最大"当目标：容差 ±tol 内匹配数是**平台**，恒等输入也会报出 −30ms 假偏移
    （自检当场抓出过一次）。"""
    by_pitch = defaultdict(list)
    for (t, _d, p, _v, _n) in mine:
        by_pitch[p].append(t)
    for k in by_pitch:
        by_pitch[k].sort()
    nb = int(round((hi - lo) / bin_s)) + 1
    hist = [0.0] * nb
    for (t, _d, p, _v, _n) in ref:
        arr = by_pitch.get(p)
        if not arr:
            continue
        for j in range(bisect.bisect_left(arr, t + lo), bisect.bisect_right(arr, t + hi)):
            k = int(round((arr[j] - t - lo) / bin_s))
            if 0 <= k < nb:
                hist[k] += 1.0
    w = max(1, int(round(smooth_ms / 1000.0 / bin_s)))
    sm = [0.0] * nb
    for k in range(nb):
        s = 0.0
        for o in range(-w, w + 1):
            kk = k + o
            if 0 <= kk < nb:
                s += hist[kk] * (1.0 - abs(o) / float(w + 1))
        sm[k] = s
    kb = max(range(nb), key=lambda k: sm[k])
    delta = 0.0
    if 0 < kb < nb - 1:
        y0, y1, y2 = sm[kb - 1], sm[kb], sm[kb + 1]
        den = y0 - 2 * y1 + y2
        if abs(den) > 1e-12:
            delta = max(-1.0, min(1.0, 0.5 * (y0 - y2) / den))
    return round(lo + (kb + delta) * bin_s, 4), sm[kb], sm[kb] / max(1e-9, sum(hist))


def best_dt(ref, mine, **kw):
    """→ (修正量 dt, 施加后的音符级匹配数)。`dt_hist` 给**观测量**（mine−ref），这里取负。"""
    obs, _s, _sh = dt_hist(ref, mine, **kw)
    dt = -obs
    pairs, _m, _e = match_notes(ref, [(t + dt, d, p, v, n) for (t, d, p, v, n) in mine])
    return dt, len(pairs)


def window_offsets(ref, mine, win=10.0, lo=-1.0, hi=1.0):
    """逐窗偏移（ms）—— **极差小 = 时间轴只差一个常数**；极差 ~1.5s 说明不是同一版本。"""
    d0, _n = best_dt(ref, mine)
    ms = [(t + d0, dd, p, v, n) for (t, dd, p, v, n) in mine]
    out = []
    for w0 in range(0, int(max(n[0] for n in ms)) + 1, int(win)):
        r = [n for n in ref if w0 <= n[0] < w0 + win]
        m = [n for n in ms if w0 <= n[0] < w0 + win]
        if len(r) < 20 or len(m) < 20:
            continue
        d, _s, sh = dt_hist(r, m, lo=lo, hi=hi)
        if sh > 0.05 and abs(d) < (hi - 0.5):
            out.append(round(d * 1000))
    return out


def fit_warp(ref, mine, win=10.0):
    """线性时间规整：t' = t − (a·t + b)。→ (a, b, 用了几个窗, 拟合残差秒)。

    `a` 是"被评比真值慢/快"的斜率（速度比 = 1−a）；同版本的 `a` ≈ 0。"""
    d0, _n = best_dt(ref, mine, lo=-3.0, hi=3.0)
    ms = [(t + d0, dd, p, v, n) for (t, dd, p, v, n) in mine]
    xs, ys = [], []
    for w0 in range(0, int(max(n[0] for n in ms)) + 1, int(win)):
        r = [n for n in ref if w0 <= n[0] < w0 + win]
        m = [n for n in ms if w0 <= n[0] < w0 + win]
        if len(r) < 20 or len(m) < 20:
            continue
        d, _s, sh = dt_hist(r, m, lo=-3.0, hi=3.0)
        if sh > 0.05 and abs(d) < 2.5:
            xs.append(w0 + win / 2.0)
            ys.append(d)
    if len(xs) < 4:
        return 0.0, d0, len(xs), 0.0
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    a = (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den) if den else 0.0
    b = my - a * mx
    resid = (sum(((y - (a * x + b)) ** 2) for x, y in zip(xs, ys)) / n) ** 0.5
    return a, b + d0, len(xs), resid


# ---------------------------------------------------------------- 报告
def _fmt_f1(m):
    return '%.3f (P %.2f / R %.2f · 匹配 %d 漏 %d 假 %d)' % m


# "这一对是不是同一版本"的判据阈值：`fit_warp` 的斜率 a（速度比 = 1−a），同版本 a ≈ 0。
WARP_TOL = 5e-3


def version_ok(a):
    """`fit_warp` 的斜率 a 是否落在"同版本"范围内（真 = 同一版本）。

    ⚠ **这里修过一次恒真判断**（2026-09-27 第五轮实测抓到）：原写法是
    `if abs(1 - a) > WARP_TOL: 警告"不是同一版本"` —— 但 `a` 同版本时 ≈ 0，
    `abs(1 - 0) = 1.0 > 0.005` **恒为真** → 这条警告对**任何**输入都触发。
    现场：Muse 真值 vs 它自己的渲染（速度比打印 **1.0000**、偏移 +2ms）照样被警告
    "很可能不是同一版本" —— **触发率 100% 的判据 = 噪声**（技能里那条口径），
    而且方向是**把好数据判成坏数据**。
    正确判据是 `abs(a)`（速度比偏离 1 的幅度），不是 `abs(1 - a)`（≈1 恒成立）。
    自检里补了两条已知答案的用例（a=0 不报警 / a=0.0092 报警）盯着它。"""
    return abs(a) <= WARP_TOL


def report(mine_path, ref_path, do_warp=False, keep_drum=False, out_json=None):
    ref_raw, rmeta = load_notes(ref_path, drop_drum=False)
    mine_raw, mmeta = load_notes(mine_path, drop_drum=False)
    print('真值 %s：%d 音（鼓 %d）· tempo 事件 %d · 首 tempo %.1f BPM'
          % (os.path.basename(ref_path), len(ref_raw), len(subset(ref_raw, True)),
             rmeta['n_tempo_events'], rmeta['first_bpm'] or 0))
    if rmeta['n_tempo_events'] > 1:
        print('  ⚠ 该文件有 %d 条 set_tempo —— 任何"按单个 bpm × 拍"的换算都会错，'
              '本工具已按 tempo map 算' % rmeta['n_tempo_events'])
    print('被评 %s：%d 音（鼓 %d）' % (os.path.basename(mine_path), len(mine_raw),
                                   len(subset(mine_raw, True))))
    print()

    a, b, nw, resid = (0.0, 0.0, 0, 0.0)
    offs = []
    if do_warp:
        a, b, nw, resid = fit_warp(ref_raw, mine_raw)
        print('【时间规整】速度比 %.4f · 偏移 %+.0fms · %d 个窗 · 拟合残差 %.0fms'
              % (1 - a, b * 1000, nw, resid * 1000))
        if not version_ok(a):
            print('  ⚠ 速度比偏离 1 超过 %.1f%%（a=%.4f）→ **这对很可能不是同一版本**，下方读数只作参照'
                  % (WARP_TOL * 100, a))
    else:
        d0, _n = best_dt(ref_raw, mine_raw)
        offs = window_offsets(ref_raw, mine_raw)
        print('【恒定偏移】修正 %+.0fms' % (d0 * 1000))
        if offs:
            print('  逐 10 秒窗偏移(ms)：%s → 极差 %dms（同源应 ≤20ms，~1.5s = 不同版本）'
                  % (offs, max(offs) - min(offs)))
        a, b = 0.0, -d0        # ⚠ 符号：best_dt 给的是**修正量**（加到被评时间上），
        #                          而下面统一按 `t −(a·t+b)` 施加 → 这里存 −d0
        #                          （自检当场抓出过一次：存成 +d0 会把偏移**翻倍**，0.947 掉到 0.093）
    mine = [(t - (a * t + b), d, p, v, n) for (t, d, p, v, n) in mine_raw]

    # 三口径
    nd = subset(ref_raw, False), subset(mine, False)
    dr = to_family(subset(ref_raw, True)), to_family(subset(mine, True))
    F_nd, P_nd, R_nd = grid_f1(nd[0], nd[1])
    F_dr, P_dr, R_dr = grid_f1(dr[0], dr[1])
    F_al, P_al, R_al = grid_f1(ref_raw, mine)
    print('【16 分格 F1】非鼓 %.3f · 鼓族 %.3f · 全部 %.3f' % (F_nd, F_dr, F_al))
    f, P, R, pairs, miss, exc = note_f1(nd[0], nd[1])
    print('【音符级 F1·非鼓】%.3f (P %.3f / R %.3f · 匹配 %d 漏 %d 假 %d)'
          % (f, P, R, len(pairs), len(miss), len(exc)))
    f2, P2, R2, pairs2, miss2, exc2 = note_f1(dr[0], dr[1])
    print('【音符级 F1·鼓族】%.3f (P %.3f / R %.3f · 匹配 %d 漏 %d 假 %d)'
          % (f2, P2, R2, len(pairs2), len(miss2), len(exc2)))

    # 去重口径（两侧对称）
    rd = [x[0] for x in dedup(ref_raw)]
    md = [x[0] for x in dedup(mine)]
    dup_rate = 100.0 * (len(ref_raw) - len(rd)) / max(1, len(ref_raw))
    f3, P3, R3, _p3, _m3, _e3 = note_f1(subset(rd, False), subset(md, False))
    print('【去重口径·非鼓】%.3f (P %.3f / R %.3f) —— 真值同音高近同时占 %.1f%%（两侧对称折叠）'
          % (f3, P3, R3, dup_rate))

    # 逐角色召回
    tagged = role_tagged(ref_path, ref_raw)
    rr = defaultdict(lambda: [0, 0])
    for i, r in enumerate(tagged):
        rr[r[4]][1] += 1
    refx = [(n[0], n[1], to_family([n])[0][2] if n[4] == 'drums' else n[2], n[3], n[4])
            for n in tagged]
    mxx = [(n[0], n[1], to_family([n])[0][2] if n[4] == 'drums' else n[2], n[3], n[4])
           for n in mine]
    pr, _m, _e = match_notes(refx, mxx)
    for (ri, _mi, _d) in pr:
        rr[tagged[ri][4]][0] += 1
    order = [r for r in ('bass', 'melody', 'harmony', 'drums') if rr[r][1]]
    print('【逐角色召回】' + ' · '.join(
        '%s %.1f%%(%d/%d)' % (r, 100.0 * rr[r][0] / rr[r][1], rr[r][0], rr[r][1]) for r in order))

    if out_json:
        json.dump(dict(ref=os.path.basename(ref_path), mine=os.path.basename(mine_path),
                       grid=dict(non_drum=F_nd, drum_family=F_dr, all=F_al),
                       note=dict(non_drum=f, P=P, R=R, matched=len(pairs),
                                 missed=len(miss), extra=len(exc)),
                       dedup=dict(non_drum=f3, P=P3, R=R3, truth_dup_rate=round(dup_rate, 2)),
                       speed_ratio=(1 - a), offset_ms=round(b * 1000, 1),
                       window_offsets_ms=offs,
                       roles={r: rr[r] for r in order}),
                  open(out_json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('读数已写：%s' % out_json)


# ---------------------------------------------------------------- 尺子自检
def _write_test_smf(path):
    """手搓一个**含两条 tempo 事件**的最小 SMF（验 tempo map 真的被用上）。"""
    def vlq(v):
        out = [v & 0x7F]
        v >>= 7
        while v:
            out.insert(0, 0x80 | (v & 0x7F))
            v >>= 7
        return bytes(out)
    body = b'\x00\xff\x51\x03' + (500000).to_bytes(3, 'big')          # tick 0: 120BPM
    body += vlq(480) + b'\xff\x51\x03' + (250000).to_bytes(3, 'big')  # tick 480: 240BPM
    body += b'\x00\x90\x3c\x64'                                       # tick 480: 音起
    body += vlq(480) + b'\x80\x3c\x40' + b'\x00\xff\x2f\x00'
    data = b'MThd' + struct.pack('>IHHH', 6, 0, 1, 480) + b'MTrk' + struct.pack('>I', len(body)) + body
    open(path, 'wb').write(data)


def selftest(verbose=True):
    """**已知答案**的用例（恒等 / 平移 / 小数偏移 / 丢音 / 升八度 / tempo map / 去重 / 版本判别）。"""
    import tempfile
    ok = True

    def chk(name, got, want, tol):
        nonlocal ok
        good = abs(got - want) <= tol
        ok = ok and good
        if verbose:
            print('  %-42s %-10s 期望 %-8s %s'
                  % (name, round(got, 4), want, 'PASS' if good else 'FAIL'))

    tmp = tempfile.mkdtemp(prefix='truth_eval_')
    t2s = os.path.join(tmp, 'tempo.smf')
    _write_test_smf(t2s)
    f, div, ev = make_tick2sec(t2s)
    assert len(ev) == 2, 'tempo map 没读到两条事件（读到 %d 条）' % len(ev)
    chk('tempo map: tick480 = 0.5s', f(480), 0.5, 1e-9)
    chk('tempo map: tick960 = 0.75s（非 1.0s）', f(960), 0.75, 1e-9)

    src = os.path.join(tmp, 'base.mid')
    m = midi_file.import_midi(t2s)
    for tr in m['tracks']:
        tr['notes'] = [[0.0, 1.0, 60, 90], [1.0, 1.0, 64, 90], [2.0, 1.0, 67, 90],
                       [3.0, 1.0, 60, 90], [4.0, 1.0, 62, 90]]
        tr['ccs'], tr['program_changes'], tr['markers'] = [], [], []
    m['bpm'], m['timesig'] = 120.0, [4, 4]
    midi_file.export_midi(m, src)
    ref, _ = load_notes(src)
    assert len(ref) == 5, '夹具音符数不对：%d' % len(ref)

    def variant(tag, fn):
        mm = midi_file.import_midi(src)
        for tr in mm['tracks']:
            fn(tr)
        p = os.path.join(tmp, tag + '.mid')
        midi_file.export_midi(mm, p)
        return load_notes(p)[0]

    ident = variant('ident', lambda tr: None)
    F, P, R = grid_f1(ref, ident)
    chk('恒等输入 16 分格 F1', F, 1.0, 1e-9)
    f0, P0, R0, pr, ms, ex = note_f1(ref, ident)
    chk('恒等输入 匹配数', len(pr), len(ref), 0)
    chk('恒等输入 最佳 dt', best_dt(ref, ident)[0], 0.0, 0.005)

    # ⚠ 夹具是 120BPM → 1 拍 = 0.5 秒：位移要按**拍**给（0.2 秒 = 0.4 拍）。
    #   （自检第一版直接写 `+ 0.2` 当秒用，读数 −0.10 被判 FAIL —— 是**用例**错、工具是对的。）
    sh = variant('shift', lambda tr: [n.__setitem__(0, n[0] + 0.4) for n in tr['notes']])
    chk('整体 +0.2s：搜到的修正量应 ≈ −0.20', best_dt(ref, sh)[0], -0.2, 0.011)
    sh37 = variant('shift37', lambda tr: [n.__setitem__(0, n[0] + 0.074) for n in tr['notes']])
    chk('整体 +37ms：尖峰法应分辨', best_dt(ref, sh37)[0], -0.037, 0.006)

    drop = variant('drop', lambda tr: tr.__setitem__('notes', tr['notes'][:4]))
    _F, _P, Rd = grid_f1(ref, drop)
    chk('丢 1/5 音：召回', Rd, 0.8, 1e-6)

    oct_ = variant('oct', lambda tr: tr['notes'][0].__setitem__(2, 72))
    _f, _P2, _R2, _pr2, ms2, ex2 = note_f1(ref, oct_)
    chk('一个音升八度：漏音数', len(ms2), 1, 0)
    chk('一个音升八度：假音数', len(ex2), 1, 0)

    dd = [(0.0, 1.0, 60, 90, 'h'), (0.01, 1.0, 60, 90, 'h'), (0.02, 1.0, 60, 90, 'h')]
    chk('去重：同音高 3 个 20ms 内 → 1 个事件', len(dedup(dd)), 1, 0)
    # ⚠ 用 `.get`（不是 `[]`）：注入用例会把 FAMILY 换成别的映射，KeyError 会让检查"崩掉"
    #   而不是**干净地 FAIL**（崩掉在变异测试里算"漏了"）。
    chk('鼓族：35 与 36 同族',
        float(FAMILY.get(35) is not None and FAMILY.get(35) == FAMILY.get(36)), 1.0, 0)
    # 「是不是同一版本」的判据 —— 原本写成 `abs(1-a)`，对**任何** a 都成立（a≈0 时 =1.0）
    # → 警告恒真；这两条用例就是当时缺的"已知答案"。
    chk('版本判别：a=0（速度比 1.0000）判同版本', float(version_ok(0.0)), 1.0, 0)
    chk('版本判别：a=0.0092（速度比 0.9908）判不同版本', float(version_ok(0.0092)), 0.0, 0)

    if verbose:
        print('  ===== 尺子自检：%s =====' % ('全部 PASS' if ok else '**有 FAIL**'))
    return ok


def main():
    ap = argparse.ArgumentParser(description='对着真值 MIDI 量转录精度（tempo map + 恒定偏移/线性规整 + 三口径 + 逐角色）')
    ap.add_argument('mine', nargs='?', help='被评 MIDI（转录产物）')
    ap.add_argument('--ref', help='真值 MIDI')
    ap.add_argument('--warp', action='store_true', help='按线性时间规整对齐（同曲不同版本时用）')
    ap.add_argument('--keep-drum', action='store_true', help='（默认已分开报，此项仅为兼容）')
    ap.add_argument('--json', default=None, help='把读数写进 JSON')
    ap.add_argument('--selftest', action='store_true', help='跑尺子自检（全部已知答案组）')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if not (a.mine and a.ref):
        ap.error('要么给 <被评.mid> --ref <真值.mid>，要么 --selftest')
    report(a.mine, a.ref, do_warp=a.warp, keep_drum=a.keep_drum, out_json=a.json)


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    main()
