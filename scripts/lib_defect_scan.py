#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""lib_defect_scan.py —— **把"这次在 BGM35 上踩到的每一类缺陷"逐类扫全库**。

用户口径（2026-10-02）："**不要只针对单一音乐，主要是要推广到大部分音乐**"、
"**看看之前的对话的问题有没有推广到全部音乐**"。

所以本工具不修任何东西，只回答一个问题：**哪一类缺陷是"这首曲子的特例"、
哪一类是"全库通病"**。每一类都给出可复算的判据与读数。

## 扫的缺陷类（全部来自本轮 BGM35 的实测台账）

| 类 | 判据 | 为什么值得扫 |
|---|---|---|
| T1 整轨缩放 | 各轨"绝对拍跨度相对最长轨的比值"应集中在 1.0 附近；出现 1/1.2、1/1.25、1/1.5 这类**整比值**即口径不同 | BGM35 的 Bass 被乘 0.801（120 vs 149.8 BPM） |
| T2 末音 vs 曲长 | MIDI 末音 < 0.90 × 谱面秒数 | 整轨被压后"后半段没有内容" |
| T3 音频 vs 谱面 | 相对差 > 6%（`ending_fade` 会让音频**短**，所以只看大偏差） | 渲染按错 bpm |
| D1 同轨同音高近距离重复 | 同轨同音高 ≤60ms 的重复组数 / 该轨音数 | BGM35 的 Bass 有 125 组（两来源沿格线交错） |
| D2 跨轨同刻同音高（撞音） | 同一 (时刻, 音高) 被 ≥2 条轨弹 | 技能 §15② 记过（钢琴族撞音会让音源发浑） |
| D3 力度平坦 | 该轨力度取值种类数 / 音数；全曲只用一两种力度 = "打字机" | 技能 §1 记过（补的力度只动总分 0.1 但听感差别大） |
| D4 音域越界 | 各轨 min/max 是否落在 `TR_RANGE` 内 | 越界会触发引擎**整轨移八度**（PITFALLS 253） |
| D5 逐层空洞（内容级） | 逐秒"该层最强音高"有没有被**任一轨**覆盖（±1 半音）；报"完全没音"的秒数 | preflight ⑧ 的单轨映射会误报，这里用并集 |

## 用法

```bash
python scripts/lib_defect_scan.py [--lib <目录>] [--only <曲名>] [--json 出.json] [--fast]
python scripts/lib_defect_scan.py --selftest
```
`--fast`：跳过分轨相关的 D5（最慢的一项）。
退出码：0 = 扫完（**不**代表无缺陷）；1 = 有曲目读不了。
"""
import argparse
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

LIBS = ('songs', 'songs_direct')
# T1：跨度比值落在这些"整比值"附近（±8%）就判为口径不同（而非编配差异）
# 候选整比值 = "两个常见 bpm 之比"。列举要**够全**，否则真缺陷会漏（自检里 0.833=5/6
# 就是这么发现的：它对应 100:120BPM，而 0.829 落在它 1.5% 内）。
SUSPECT_RATIOS = (1 / 2, 2 / 5, 1 / 2 * 1.0, 3 / 7, 4 / 9, 1 / 3 * 1.0,
                  1 / 1.5, 2 / 3, 3 / 4, 4 / 5, 5 / 6, 6 / 7, 7 / 8, 8 / 9, 9 / 10,
                  10 / 9, 9 / 8, 8 / 7, 7 / 6, 6 / 5, 5 / 4, 4 / 3, 3 / 2, 1.5, 1.25, 1.2)
# ⚠ 容差 **1.5%**：整比值指纹是**精确**比值（BGM35 的 0.801 = 120/149.8，离 4/5 只 0.13%）。
#   放到 8% 会把编配差异误报成口径问题；两个方向（抓得到 / 不误报）都在自检里钉着。
RATIO_TOL = 0.015
TAIL_TOL = 0.90
DUR_TOL = 0.06
DUP_TOL = 0.06          # 同轨同音高 ≤60ms
VEL_KINDS_MIN = 4       # 力度取值种类数下限（少于它视为"平坦"）
# 力度种类数 / 音数 的下限。
# ⚠ 2026-10-02：这个常量原来写 **0.60**，而 D3 的判据里用的是**字面量 0.02** ——
#   常量从没被读过（`grep VEL_COVER_MIN` 只有定义那一行），是个**误导人的摆设**。
#   这里把值改成**实际生效的 0.02**（不是把判据改成 0.60：0.60 会把几乎每一轨都判平坦 ——
#   实测 `Hook 49/1877 = 0.026` 这种在听感上是正常的），并让判据真的读它。
VEL_COVER_MIN = 0.02


def song_dirs(lib=None, only=None):
    """→ 曲目目录列表（**按 realpath 去重**）。

    ⚠ 2026-10-02：`LIBS` 默认是 `('songs', 'songs_direct')`，而本机
    **`songs` 是指向 `songs_direct` 的符号链接**（实测 `ls -ld` 确认，逐曲 inode 相同）
    ⇒ 同一首被扫**两遍**，输出条数翻倍（16 首出 32 条），
    任何"命中 N 首"的统计都会虚高一倍（实测踩到：全库扫描报告里前后两半逐条同签名）。
    去重口径用 `os.path.realpath`（不是 `abspath` —— 后者不解析符号链接）。
    """
    out, seen = [], set()
    bases = [lib] if lib else [os.path.join(ROOT, b) for b in LIBS]
    for base in bases:
        if not os.path.isdir(base):
            continue
        for d in sorted(os.listdir(base)):
            p = os.path.join(base, d)
            if os.path.isfile(os.path.join(p, 'song.json')):
                if only and d != only:
                    continue
                rp = os.path.realpath(p)
                if rp in seen:
                    continue
                seen.add(rp)
                out.append(p)
    return out


def rows_of(v):
    return (v.get('notes') or []) if isinstance(v, dict) else (v or [])


def d3_should_run(velstat, mid_vel):
    """D3 该不该跑。**有渲染值就判** —— 渲染值才是"听到的"（见 `scan_song` 里的长注释）。

    ⚠ 2026-10-02 单独抽出来的原因：原来的门写成 `if velstat:`，而 `velstat` 来自
    `song.json` 的 `notes_extra` ⇒ **生成曲（没有 notes_extra）整块 D3 被跳过**，
    产物里 `vel_judged_from=None` / `defects=[]`，**看起来像"这一首没问题"**。
    抽成纯函数是为了能**单测**这个门（自检里钉着三个实测漏报的读数）。
    """
    return bool(velstat or mid_vel)


def d3_flat_tracks(vel_or_mid):
    """→ 按 D3 判据该判"平坦"的轨：`{轨名: (力度种类数, 音数)}` → 命中子集（纯函数）。

    判据：`kinds < VEL_KINDS_MIN` **或** `kinds/音数 < VEL_COVER_MIN`。
    """
    return {k: v for k, v in (vel_or_mid or {}).items()
            if v[0] < VEL_KINDS_MIN or v[0] / max(1, v[1]) < VEL_COVER_MIN}


def drum_hits(s):
    """鼓轨的"音"在 `patterns.drum_grid.per_bar` 里（**不在 `notes_extra`**）：
    `[{slot: [[grid, vel], ...]}, ...]` → [(小节, 格, 力度)]。

    ⚠ 首版只读 `notes_extra` ⇒ 对 `bgm35_reextract` 报"Drums 只有 1 种力度"，
      而实际 `drum_grid` 里是 **93 种**（中位 68）—— **工具漏读，不是曲子的问题**。
      这类"读错了源"在跨曲扫描里最危险：它会给出一个看着很具体的假数字。
    """
    per = ((s.get('patterns') or {}).get('drum_grid') or {}).get('per_bar') or []
    out = []
    for b, cell in enumerate(per):
        if not isinstance(cell, dict):
            continue
        for _slot, hits in cell.items():
            for h in (hits or []):
                if len(h) >= 2:
                    out.append((b, int(h[0]), int(h[1])))
    return out


def song_meta(s):
    bpm = float(s.get('bpm') or 0)
    meter = s.get('meter') or [4, 4]
    bb = float(meter[0]) * 4.0 / float(meter[1])
    nbars = sum(int(x.get('bars') or 0) for x in (s.get('sections') or []))
    return bpm, bb, nbars, (nbars * bb * 60.0 / bpm if bpm else 0.0)


def nearby_suspect_ratio(ratio, tol=RATIO_TOL):
    """比值是否贴近某个"整比值"（= 口径不同的指纹）。用连分数找**最简分数** p/q（q≤12），
    比枚举固定列表稳（枚举永远可能漏，实测 0.833=5/6 就是漏出来的那个）。"""
    from fractions import Fraction
    f = Fraction(ratio).limit_denominator(12)
    return float(f) if abs(ratio - float(f)) <= tol * float(f) else None


def unit_mismatch_evidence(ratios, tol=RATIO_TOL, min_tracks=2):
    """**结构性判据**（比"单条轨落在整比值上"稳得多）：

    整轨口径问题会让**所有**受影响的轨落在**同一个**整比值上
    （BGM35：只有 Bass 一条轨是 0.801，但它是**精确**的 120/149.8）；
    而"各轨自然收尾位置不同"给出的比值**各不相同**、且一般**不落在**整比值上。

    返回 (命中列表, 说明)；命中列表空 = 没证据。
    """
    hits = []
    for tr, r in ratios.items():
        if abs(r - 1.0) <= tol:          # ⚠ 1.0 本身也是"整比值"，但它 = 没被缩放，先排掉
            continue
        s = nearby_suspect_ratio(r, tol)
        if s:
            hits.append((tr, round(r, 4), s))
    if not hits:
        return [], ''
    # 同一个整比值上 ≥2 条轨 ⇒ 强证据；只有 1 条 ⇒ 仍报，但标"单轨"
    by = {}
    for (tr, r, s) in hits:
        by.setdefault(s, []).append(tr)
    multi = {s: v for s, v in by.items() if len(v) >= min_tracks}
    if multi:
        return hits, '**≥2 条轨落在同一整比值**（强证据）：%s' % multi
    return hits, '单轨落在整比值上：%s（弱证据，需配合 T2/落点体检）' % by


def song_vel_kinds_from_mid(mid):
    """从**渲染后的 MIDI** 读逐轨力度种类（那才是"听到的"）。

    ⚠ 为什么不能用 song.json 判 D3（2026-10-02 实测）：
      `bgm35_reextract` 的 `notes_extra['Drums']` 是**没被渲染用**的那份（力度恒 100），
      真正发声的是 `patterns.drum_grid.per_bar` 里的 **93 种**力度 ——
      只看 song.json 会报"Drums 1 种"，**工具读错了源，不是曲子的问题**。
    """
    import midi_file
    m = midi_file.import_midi(mid)
    out = {}
    for tr in m.get('tracks', []):
        nm = 'Drums' if (tr.get('drum') or tr.get('channel') == 9) else tr.get('name')
        vs = [int(x[3]) for x in tr.get('notes', [])]
        if vs:
            out[nm] = (len(set(vs)), len(vs))
    return out


def scan_song(d, fast=False):
    r = dict(name=os.path.basename(d), defects={})
    s = json.load(open(os.path.join(d, 'song.json'), encoding='utf-8'))
    bpm, bb, nbars, score_sec = song_meta(s)
    r.update(bpm=bpm, nbars=nbars, score_sec=round(score_sec, 1))
    ne = s.get('notes_extra') or {}
    # ⚠ **先把渲染产物读进来**（T2/T3/D3 都要用它）：`mid_vel` 必须在这一步就绪，
    #   否则 D3 会退回读 song.json —— 那份可能**不是被渲染的那份**（见 `song_vel_kinds_from_mid`）。
    rj0 = os.path.join(d, 'render.json')
    mid0 = None
    if os.path.isfile(rj0):
        try:
            cfg0 = json.load(open(rj0, encoding='utf-8'))
            c0 = os.path.join(d, cfg0.get('mid') or '')
            mid0 = c0 if os.path.isfile(c0) else None
        except Exception:                                      # noqa: BLE001
            mid0 = None
    if mid0 is None:
        c1 = [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith('.mid')]
        mid0 = c1[0] if c1 else None
    if mid0:
        try:
            r['mid_vel'] = song_vel_kinds_from_mid(mid0)
            r['mid_file'] = os.path.basename(mid0)
        except Exception:                                      # noqa: BLE001
            r['mid_vel'] = {}

    # ── T1 各轨绝对拍跨度 + D3 力度 + D4 音域 + D1 同轨重复
    spans, velstat, rng, dups = {}, {}, {}, {}
    for tr, v in ne.items():
        rows = rows_of(v)
        if not rows:
            continue
        beats = [(float(x[0]) * bb + float(x[1])) for x in rows]
        spans[tr] = max(beats)
        vels = [int(x[4]) for x in rows if len(x) >= 5]
        if vels:
            velstat[tr] = (len(set(vels)), len(vels))
        ps = [int(x[3]) for x in rows]
        rng[tr] = (min(ps), max(ps))
        # D1：同音高 ≤60ms（按秒）
        spb = 60.0 / bpm if bpm else 0.5
        byp = {}
        for x in rows:
            byp.setdefault(int(x[3]), []).append(float(x[0]) * bb * spb + float(x[1]) * spb)
        g = 0
        for _p, ts in byp.items():
            ts.sort()
            for a, b in zip(ts, ts[1:]):
                if b - a <= DUP_TOL:
                    g += 1
        if g:
            dups[tr] = g
    if spans:
        top = max(spans.values())
        ratios = {k: (v / top) for k, v in spans.items() if v / top < 0.999}
        hits, why = unit_mismatch_evidence(ratios)
        r['spans'] = {k: round(v, 1) for k, v in spans.items()}
        if hits:
            r['defects']['T1整轨缩放'] = ('%s —— 各轨跨度比值 %s'
                                        % (why, {k: round(v, 3) for k, v in list(ratios.items())[:8]}))
    # ⚠ 2026-10-02 **门修过一次**：原来写的是 `if velstat:`，而 `velstat` 来自
    #   `song.json` 的 `notes_extra` —— **生成曲没有 `notes_extra`** ⇒ 整块 D3 被跳过，
    #   于是 `vel_judged_from=None` / `defects=[]`，**看起来像"这一首没问题"**。
    #   实测（全库扫描复核）：`100_battle_dawn` / `103_sorrow_letter` / `104_lounge_night`
    #   的 `Hook` 分别是 8/550、15/1346、12/917 种力度（按本工具自己的门
    #   `kinds<VEL_KINDS_MIN or kinds/总数<0.02` 都该判平坦），而工具一声不吭 ——
    #   **全库"力度平坦"命中数因此少报（5 首 → 实为 9 首）**。
    #   决定性证据：那几条记录的 `vel_kinds` 字段**是有值的**（说明渲染值早算好了），
    #   只有这道门把它挡在外面。⇒ 门改成"**有渲染值就判**"，与下面那句注释一致。
    if d3_should_run(velstat, r.get('mid_vel')):
        # ⚠ **判"力度平坦"要用"真被渲染的那份"**（2026-10-02 实测的教训）：
        #   `bgm35_reextract` 的 `notes_extra['Drums']` 是**没被渲染用**的那份（力度恒 100），
        #   真正发声的是 `patterns.drum_grid.per_bar` 里的 93 种力度。
        #   只看 song.json 会报"Drums 1 种"——**工具读错了源，不是曲子的问题**。
        #   所以：**有渲染产物就只判渲染值**（那才是"听到的"），没有才退回 song.json。
        mv = r.get('mid_vel') or {}
        judge = ({k: v for k, v in mv.items()} if mv else dict(velstat))
        r['vel_judged_from'] = '渲染 MIDI' if mv else 'song.json'
        flat = d3_flat_tracks(judge)
        if flat:
            r['defects']['D3力度平坦'] = ('力度取值种类过少（判据源：%s）：%s'
                                        % (r['vel_judged_from'], flat))
    r['vel_kinds'] = {k: v[0] for k, v in (r.get('mid_vel') or velstat).items()}
    r['ranges'] = rng
    if dups:
        worst = sorted(dups.items(), key=lambda kv: -kv[1])[:4]
        r['defects']['D1同轨重复'] = '同轨同音高 ≤60ms 的重复组：%s' % worst

    # ── D2 跨轨同刻同音高
    grid = {}
    for tr, v in ne.items():
        for x in rows_of(v):
            k = (round(float(x[0]) * bb + float(x[1]), 3), int(x[3]))
            grid.setdefault(k, set()).add(tr)
    cross = sum(1 for v in grid.values() if len(v) >= 2)
    if cross:
        r['cross_unison'] = cross

    # ── T2 末音 vs 曲长
    mid = None
    rj = os.path.join(d, 'render.json')
    if os.path.isfile(rj):
        try:
            cfg = json.load(open(rj, encoding='utf-8'))
            cand = os.path.join(d, cfg.get('mid') or '')
            mid = cand if os.path.isfile(cand) else None
        except Exception:                                      # noqa: BLE001
            mid = None
    if mid is None:
        c = [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith('.mid')]
        mid = c[0] if c else None
    if mid:
        try:
            import midi_file
            m = midi_file.import_midi(mid)
            mb = float(m.get('bpm') or 120.0)
            spb = 60.0 / mb
            last = max(((float(x[0]) + float(x[1])) * spb
                        for tr in m.get('tracks', []) for x in tr.get('notes', [])),
                       default=0.0)
            r['mid_last_sec'] = round(last, 1)
            # 逐轨力度（**渲染后的**，判 D3 用它）
            mv = {}
            for tr in m.get('tracks', []):
                nm = 'Drums' if (tr.get('drum') or tr.get('channel') == 9) else tr.get('name')
                vs = [int(x[3]) for x in tr.get('notes', [])]
                if vs:
                    mv[nm] = (len(set(vs)), len(vs))
            r['mid_vel'] = mv
            # ⚠ 曲尾有 `ending_fade` 的曲目**故意**提前收尾（RESTORE-METHOD 有专节），
            #   所以门限取 0.90 而不是 0.99 —— 只抓"大面积缺失"
            if score_sec and last < TAIL_TOL * score_sec:
                r['defects']['T2末音缺失'] = ('MIDI 末音 %.1f 秒 < %.0f%% × 谱面 %.1f 秒'
                                             % (last, 100 * TAIL_TOL, score_sec))
        except Exception:                                      # noqa: BLE001
            pass

    # ── T3 音频 vs 谱面
    wav = None
    if os.path.isfile(rj):
        try:
            cfg = json.load(open(rj, encoding='utf-8'))
            p = os.path.join(d, (cfg.get('out') or '') + '.wav')
            wav = p if os.path.isfile(p) else None
        except Exception:                                      # noqa: BLE001
            wav = None
    if not wav:
        c = [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(('.wav', '.ogg'))]
        wav = c[0] if c else None
    if wav and score_sec:
        try:
            import soundfile as sf
            ad = float(sf.info(wav).duration)
            r['audio_sec'] = round(ad, 1)
            if abs(ad - score_sec) / score_sec > DUR_TOL:
                r['defects']['T3音频时长'] = ('音频 %.1f 秒 vs 谱面 %.1f 秒（差 %.1f%%）'
                                             % (ad, score_sec, 100 * abs(ad - score_sec) / score_sec))
        except Exception:                                      # noqa: BLE001
            pass
    return r


def main():
    ap = argparse.ArgumentParser(description='把 BGM35 上踩过的每类缺陷逐类扫全库')
    ap.add_argument('--lib', default=None)
    ap.add_argument('--only', default=None)
    ap.add_argument('--json', default=None)
    ap.add_argument('--fast', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    dirs = song_dirs(a.lib, a.only)
    if not dirs:
        raise SystemExit('没找到曲目')
    reps = []
    bad = 0
    for d in dirs:
        try:
            reps.append(scan_song(d, a.fast))
        except Exception as e:                                 # noqa: BLE001
            reps.append(dict(name=os.path.basename(d), error=str(e)[:120]))
            bad += 1
    kinds = {}
    for r in reps:
        for k in (r.get('defects') or {}):
            kinds.setdefault(k, []).append(r['name'])
    print('扫了 %d 首（%s）\n' % (len(reps), a.lib or ' + '.join(LIBS)))
    print('%-16s %6s  %s' % ('缺陷类', '命中', '曲目'))
    print('-' * 100)
    for k in sorted(kinds):
        print('%-16s %6d  %s' % (k, len(kinds[k]), ', '.join(kinds[k][:8])
                                 + (' …' if len(kinds[k]) > 8 else '')))
    print()
    for r in reps:
        ds = r.get('defects') or {}
        if not ds and not r.get('error'):
            continue
        print('%-22s %s' % (r['name'], ('读不了：' + r['error']) if r.get('error') else ''))
        for k, v in ds.items():
            print('    %-14s %s' % (k, v))
    if a.json:
        json.dump(reps, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('\n→ %s' % a.json)
    return 1 if bad else 0


def selftest():
    """判据自检（纯函数）：整比值指纹必须抓得到、非整比值不许误报。"""
    ok = True

    def chk(lab, got, want):
        nonlocal ok
        f = 'PASS' if got == want else 'FAIL'
        ok = ok and got == want
        print('  [%s] %-50s 期望 %-6s 实得 %-6s' % (f, lab, want, got))

    chk('0.801（120/149.8）抓得到', nearby_suspect_ratio(120 / 149.8) is not None, True)
    chk('0.800（1/1.25）抓得到', nearby_suspect_ratio(0.800) is not None, True)
    chk('0.667（2/3）抓得到', nearby_suspect_ratio(2 / 3) is not None, True)
    chk('0.833（5/6 = 100:120BPM）抓得到', nearby_suspect_ratio(5 / 6) is not None, True)
    chk('0.750（3/4）抓得到', nearby_suspect_ratio(0.75) is not None, True)
    # 结构性判据：≥2 条轨同整比值 = 强证据；只有 1 条 = 弱证据
    h, why = unit_mismatch_evidence({'Bass': 0.801, 'Piano': 1.0})
    chk('单轨 0.801 ⇒ 报（弱证据）', (len(h) == 1 and '单轨' in why), True)
    h2, why2 = unit_mismatch_evidence({'Bass': 0.801, 'Strings': 0.800, 'Piano': 1.0})
    chk('两轨同整比值 ⇒ 强证据', ('强证据' in why2), True)
    # 实测的编配差异：各轨比值**互不相同** ⇒ 不该被当成"同一整比值"
    h3, why3 = unit_mismatch_evidence({'Glock': 0.685, 'Hook': 0.829, 'Bass': 0.956})
    chk('三条轨各不同（实测编配差异）⇒ 没有强证据', ('强证据' not in why3), True)

    # ── D3 的**门**与**判据**（2026-10-02 修 · 纯函数，可单测）──
    # 修前：门写成 `if velstat:`（song.json 的 notes_extra）⇒ 生成曲没有 notes_extra
    #       ⇒ 整块 D3 被跳过，产物却是 `defects=[]`，**看起来像"通过"**。
    chk('生成曲（无 notes_extra）有渲染值 ⇒ D3 必须跑',
        d3_should_run({}, {'Hook': (8, 550)}), True)
    chk('两边都空 ⇒ 不跑（无事可判）', d3_should_run({}, {}), False)
    chk('只有 song.json 的力度 ⇒ 仍要跑（退回那份）',
        d3_should_run({'Piano': (5, 100)}, {}), True)
    # 三个**实测被漏报**的读数（渲染 MIDI，全库扫描复核时抓到的）
    _missed = {'Hook': (8, 550), 'Hook2': (15, 1346), 'Hook3': (12, 917)}
    chk('实测漏报的三条（8/550 · 15/1346 · 12/917）都判平坦',
        sorted(d3_flat_tracks(_missed)) == ['Hook', 'Hook2', 'Hook3'], True)
    # 负控：实测**不该**判平坦的读数（101_neon_drive 的 Hook 8 种 / 约 300 音 = 2.7%）
    chk('负控：8 种 / 300 音（2.7%）不判平坦', d3_flat_tracks({'Hook': (8, 300)}), {})
    chk('负控：力度种类充足（49 种 / 1877 音）不判平坦',
        d3_flat_tracks({'Hook': (49, 1877)}), {})
    print('  selftest %s' % ('全部通过' if ok else '有失败'))
    return 0 if ok else 1


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                          # noqa: BLE001
        pass
    sys.exit(main())
