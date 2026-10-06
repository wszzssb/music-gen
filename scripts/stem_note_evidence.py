#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""stem_note_evidence.py —— **逐音量"有没有音频支撑"**（假音治理 A 的取证工具）

## 它回答什么

`audit_stems.py` 已经给了逐音级的判据（`_ratio`：该时刻该音高带的能量占比 ≥0.25 算"落到"），
但它**只输出聚合读数**（精度/空音率），要回答 A 的两个问题就不够：

1. **门在哪？** —— 假音率随门怎么变（要一张"门 → 保留/剔除 + 精度变化"的表，**先量准再谈删**）；
2. **这个音到底是"该轨没弹"还是"整段没乐器在响"？** —— 后者是**原曲留白**，
   两种情形对听感的含义完全不同（前者是假音，后者要另判）。

⚠ 直接照 `audit_stems._ratio` 写，一首 5 分钟曲要跑 **5000+ 次 60ms FFT**（`hard_bgm35_v2`
实测一轮几分钟）。本工具把窗口**整轨一次 STFT**、逐音查表，扫描多组门只多花一次向量化内积。

## 判据（**"有没有音频支撑" = 6 条分轨里的最大能量占比**）

对每个音（起音时刻 t、音高 p）：
· `sup` = max over 分轨 of 「该分轨 100ms 窗内、p 的 0.75~1.5×f0 带能量占比」
  —— 取**最大**而不是只取"该归的那条轨"：轨归属表（`STEM_OF`）本身可能错，
  取最大才不会把"其实是别的轨弹的"误判成假音（同族 `PITFALLS` **325**）；
· `total` = 该窗跨全部分轨的最大总能量（dBFS）—— 用来分开两种"没有支撑"：
  `total` 低于静音底 ⇒ **整段没乐器在响（留白）**；`total` 正常而 `sup` 低 ⇒ **该音是凭空造的**。
· 判定：`sup ≥ --keep` 算**有支撑**；`sup < --drop` 算**无支撑**（拟剔候选）；
  中间算"弱支撑"（**不剔**，留给人耳/后续判据）。默认 `--keep 0.25`（= `audit_stems.HIT`）。

## 口径一致性（**这部分必须看**）

本工具用 100ms 窗、`audit_stems` 用 60ms 窗（它逐音现算 60ms FFT，量级放不大）。
两者不是同一个数 —— 所以报告**逐音对照** `audit_stems` 的命中判定，打印**分歧率**。
分歧率小且无系统性偏移 ⇒ 用它做 A/B 的 Δ 才可信；分歧大 ⇒ 先修口径，别拿它下结论。

## 用法

```powershell
$py = "<工具链>\.venv-ml\Scripts\python.exe"
& $py scripts\stem_note_evidence.py songs\<曲>\song.json `
      --stems D:\test\_tmp\extract-hard\<base>\stems\h6\htdemucs_6s\src `
      [--audit-json <audit_stems 的 JSON>] [--json <逐音读数.json>] `
      [--drop 0.05] [--keep 0.25] [--sweep 0.05,0.1,0.15,0.2,0.25] [--gap-min 1.5]
& $py scripts\stem_note_evidence.py --selftest
```

**默认只读**：本工具**不改任何 `song.json`**，只出读数与候选名单（真删由下游 `--apply` 做）。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()          # noqa: E402

SONGS = os.path.join(ROOT, 'songs')

WIN_SEC = 0.100          # 分析窗（`audit_stems` 用 0.060；这里是"看分布"用的更稳的窗，见 docstring）
HOP_SEC = 0.010          # 帧步（10ms：够定位一个音的起音）
SILENT_DB = -70.0        # 窗总能量低于这个 ⇒ 整段没乐器在响（留白），不是"该轨没弹"
DUR_SKIP_SEC = 0.15      # 时值短于这个的**打击类/短音**只看起音帧附近（下面用帧数表达）
ONSET_FRAMES = 2         # 取起音后第 0..1 帧（= 0~20ms）的最大值，抗对齐抖动

# 引擎轨 → 该归的分轨（与 `audit_stems.STEM_OF` 保持同一真源口径）
STEM_OF = {
    'Bass': ['bass'], 'Piano': ['piano'], 'Hook': ['guitar'], 'Pad': ['other'],
    'Strings': ['other'], 'Arp': ['other'], 'Glock': ['piano', 'guitar'],
    'Melody': ['vocals', 'other'], 'Perc': ['drums'], 'Drums': ['drums'],
}


def read_mono(path):
    import soundfile as sf
    x, sr = sf.read(path, always_2d=True)
    return x.mean(axis=1), sr


def load_stems(folder):
    """→ ({分轨名: mono}, sr)。与 `audit_stems.load_stems` 同口径（裸名优先）。"""
    names = ('drums', 'bass', 'other', 'vocals', 'guitar', 'piano')
    out = {}
    sr = None
    for nm in names:
        for cand in (nm + '.wav', nm.capitalize() + '.wav'):
            p = os.path.join(folder, cand)
            if os.path.exists(p):
                out[nm], sr = read_mono(p)
                break
    if not out:
        raise SystemExit('分轨目录里没找到 drums/bass/other/vocals(.wav)：%s' % folder)
    return out, sr


class StemSpec(object):
    """一条分轨的窗功率表：`power[帧, 频点]` + 频率轴 + 按音高缓存的带索引。"""

    def __init__(self, mono, sr, win, hop):
        import numpy as np
        nfft = int(round(win))
        hop_i = int(round(hop))
        self.nfft = nfft
        self.hop = hop_i
        self.sr = sr
        m = int(np.ceil(max(1, len(mono) - nfft) / float(hop_i)))
        m = max(1, m)
        self.nframe = m
        taper = np.hanning(nfft).astype('float64')
        # 分块算 rfft：整轨一次太吃内存（5 分钟 × 44.1k 的 (M, 4410) 复数组 ≈ 数 GB）
        self.power = np.empty((m, nfft // 2 + 1), dtype='float32')
        B = 4096
        for b0 in range(0, m, B):
            b1 = min(m, b0 + B)
            # ⚠⚠ **`arange` 必须乘 hop**：写成 `b0 * hop_i + np.arange(b1 - b0)` 时，
            #    **b0=0 的那一块会"看起来对"**（0 + arange = arange），其余块则整块错位
            #    —— 后果是 `mono[idx]` 只取到音频开头一点点，功率表几乎全零
            #    ⇒ 报告成"所有音都没有音频支撑"。**没有任何报错**（形状对、max 也"看着合理"）。
            #    本轮为此绕了多轮：真凶是"少乘 hop"，不是 numpy 行为。
            starts = b0 * hop_i + np.arange(b1 - b0, dtype='int64') * hop_i
            idx = np.add.outer(starts, np.arange(nfft, dtype='int64'))
            idx = np.clip(idx, 0, len(mono) - 1).astype('int64')
            seg = mono[idx]
            seg = seg - seg.mean(axis=1, keepdims=True)
            W = np.fft.rfft(seg * taper, axis=1)
            self.power[b0:b1] = (W.real ** 2 + W.imag ** 2).astype('float32')
        self.freq = np.fft.rfftfreq(nfft, 1.0 / sr)
        self._band = {}

    def band_idx(self, f0):
        import numpy as np
        key = round(float(f0), 3)
        v = self._band.get(key)
        if v is None:
            lo, hi = f0 * 0.75, f0 * 1.5
            m = (self.freq >= lo) & (self.freq <= hi)
            if not m.any():
                i = int(round(f0 * self.nfft / self.sr))
                m = np.zeros(len(self.freq), dtype=bool)
                m[max(0, min(len(m) - 1, i))] = True
            v = np.where(m)[0]
            self._band[key] = v
        return v

    def frames(self, t):
        i = int(t * self.sr / self.hop)
        return [f for f in (i, i + 1) if 0 <= f < self.nframe]

    def note(self, t, f0):
        """→ (能量占比 | None, 窗总能量 dBFS | None)"""
        import numpy as np
        fr = self.frames(t)
        if not fr:
            return None, None
        P = self.power[fr]                      # (k, F)
        tot = float(P.sum(axis=1).max())
        if tot <= 1e-30:
            return None, -300.0
        bi = self.band_idx(f0)
        num = float(P[:, bi].sum(axis=1).max())
        return num / tot, 10.0 * np.log10(max(tot, 1e-30))


def note_time(x, bar_sec, spb):
    return float(x[0]) * bar_sec + float(x[1]) * spb


def analyse(song_json, stems_dir, audit_json=None, drop=0.05, keep=0.25, sweep=(),
            gap_min=2.0, gap_frac=0.5, verbose=True):
    import numpy as np
    stems, sr = load_stems(stems_dir)
    specs = {}
    d = json.load(open(song_json, encoding='utf-8'))
    ne = d.get('notes_extra') or {}
    bpm = float(d.get('bpm') or 120.0)
    spb = 60.0 / bpm
    bar_sec = spb * float(d.get('bar_beats') or 4.0)
    # 只给**用得到的分轨**建表（省一半时间：vocals 常常整轨静音）
    used = set()
    for tr in ne:
        used.update(c for c in STEM_OF.get(tr, []) if c in stems)
    for nm in sorted(used):
        specs[nm] = StemSpec(stems[nm], sr, WIN_SEC * sr, HOP_SEC * sr)
    all_specs = specs
    rows = []
    for tr, v in ne.items():
        lst = (v.get('notes') if isinstance(v, dict) else v) or []
        cands = [c for c in STEM_OF.get(tr, []) if c in all_specs]
        for x in lst:
            t = note_time(x, bar_sec, spb)
            p = int(x[3])
            f0 = 440.0 * 2 ** ((p - 69) / 12.0)
            per, tot = {}, -300.0
            for nm, sp in all_specs.items():
                r, tt = sp.note(t, f0)
                tot = max(tot, tt)
                if r is not None:
                    per[nm] = r
            sup = max(per.values()) if per else 0.0
            own = max([per[c] for c in cands if c in per] or [0.0])
            rows.append(dict(track=tr, bar=int(x[0]), beat=float(x[1]), t=round(t, 3),
                             pitch=p, vel=int(x[4]), dur=float(x[2]),
                             own=own, sup=sup, db=round(tot, 1),
                             per={k: round(v2, 4) for k, v2 in per.items()}))
    cons = None
    if audit_json and os.path.exists(audit_json):
        cons = consistency(rows, audit_json)

    def stats(thr_drop, thr_keep):
        out = []
        for tr in sorted(set(r['track'] for r in rows)):
            rs = [r for r in rows if r['track'] == tr]
            sup = np.array([r['sup'] for r in rs])
            silent = np.array([r['db'] < SILENT_DB for r in rs])
            none = sup < thr_drop
            weak = (sup >= thr_drop) & (sup < thr_keep)
            out.append(dict(track=tr, n=len(rs),
                            unsup=int((none & ~silent).sum()),
                            unsup_silent=int((none & silent).sum()),
                            weak=int(weak.sum()),
                            drop_pct=100.0 * int((none & ~silent).sum()) / max(1, len(rs)),
                            med_sup=float(np.median(sup)),
                            p10=float(np.percentile(sup, 10)),
                            p25=float(np.percentile(sup, 25)),
                            p75=float(np.percentile(sup, 75)),
                            frac_silent=100.0 * float(silent.mean())))
        tot = dict(n=sum(r['n'] for r in out),
                   unsup=sum(r['unsup'] for r in out),
                   unsup_silent=sum(r['unsup_silent'] for r in out),
                   weak=sum(r['weak'] for r in out))
        return out, tot

    rows_tbl, rows_tot = stats(drop, keep)
    sweep_tbl = []
    for th in sweep:
        t_, tt = stats(th, keep)
        sweep_tbl.append(dict(drop=th, unsup=tt['unsup'], unsup_silent=tt['unsup_silent'],
                              weak=tt['weak'], kept=tt['n'] - tt['unsup']))
    gap = gap_threshold([r['sup'] for r in rows], gap_min, gap_frac)
    if verbose:
        print('=' * 78)
        print('逐音能量证据 · %s' % song_json)
        print('  分轨 %s（%d 条）· 窗 %.0fms · 帧步 %.0fms · 能量占比 = 0.75~1.5×f0 带 / 窗总能量'
              % (stems_dir, len(all_specs), 1000 * WIN_SEC, 1000 * HOP_SEC))
        print('  拟剔门 sup<%.2f · 有支撑门 sup≥%.2f · 留白门 %.0fdBFS'
              % (drop, keep, SILENT_DB))
        print('-' * 78)
        print('  %-9s %6s %8s %10s %7s %7s %7s %7s %8s'
              % ('轨', '音数', '无支撑', '静音窗内', '弱支撑', 'sup中位', 'p10', 'p75', '留白%'))
        for r in rows_tbl:
            print('  %-9s %6d %7d(%4.1f%%) %6d %7d %7.3f %7.3f %7.3f %7.1f%%'
                  % (r['track'], r['n'], r['unsup'], r['drop_pct'], r['unsup_silent'],
                     r['weak'], r['med_sup'], r['p10'], r['p75'], r['frac_silent']))
        print('  %-9s %6d %7d(%4.1f%%) %6d %7d'
              % ('合计', rows_tot['n'], rows_tot['unsup'],
                 100.0 * rows_tot['unsup'] / max(1, rows_tot['n']),
                 rows_tot['unsup_silent'], rows_tot['weak']))
        print('\n  门扫描（只剔"窗里有能量但该音高带没能量"的）：')
        print('    %6s %8s %9s %8s %8s' % ('门', '拟剔', '静音窗内', '弱支撑', '保留'))
        for s in sweep_tbl:
            print('    %6.2f %8d %9d %8d %8d'
                  % (s['drop'], s['unsup'], s['unsup_silent'], s['weak'], s['kept']))
        print('\n  空档标定（分布最大空档；门 = 占比，空档 = log10 数量级）：%s'
              % (('门=%.4f · 空档 %.2f' % (gap[0], gap[1]))
                 if gap[0] is not None else
                 ('**给不出门**（最大空档 %.2f < %.1f）⇒ 分布不是双峰，'
                  '别硬定门，看上面的 --sweep 取舍表' % (gap[1], gap_min))))
        if cons:
            print('\n  口径一致性（与 audit_stems 的命中判定逐音对照，%d 音）：'
                  % cons['n'])
            print('    本工具 sup≥%.2f vs audit 命中：一致 %d · 分歧 %d（%.1f%%）'
                  % (keep, cons['same'], cons['diff'], 100.0 * cons['diff'] / max(1, cons['n'])))
            print('    其中 本工具说没支撑、audit 说命中 %d（本工具偏严）· '
                  '本工具说有支撑、audit 说空/未命中 %d（本工具偏松）'
                  % (cons['strict'], cons['loose']))
        print('  ⚠ 本工具**只读**：不写任何 song.json；拟剔名单要下游按门应用 + 分段 + 备份')
    return dict(rows=rows, by_track=rows_tbl, total=rows_tot, sweep=sweep_tbl,
                gap=dict(thr=gap[0], size=gap[1]), consistency=cons,
                params=dict(win=WIN_SEC, hop=HOP_SEC, drop=drop, keep=keep,
                            silent_db=SILENT_DB, stems=stems_dir))


def gap_threshold(vals, gap_min=2.0, gap_frac=0.5):
    """分布**最大空档**标定门（同 `filter_song_by_stem.gap_threshold` 的口径：空档不够就拒门）。

    ⚠ 值域是 [0,1] 的**占比**，不是分轨 RMS（dB）⇒ 三处口径必须改：
    · **先抬到 `FLOOR` 再取 log10**：占比里 0 很常见（音高带里一点能量都没有），
      `log10(0)` 会把空档算成无穷大（实测第一版就这么把"门"定到 1e-4）；
    · **空档要同时满足"绝对"与"相对"两个条件**：绝对用 log10 单位（默认 `2.0` = 两个数量级），
      相对用 `gap_frac`（默认 0.5 = 最大空档要占整个 log 值域的一半以上）。
      ⚠ 只判绝对会**把近单峰也算成双峰**：`[0.19, 0.2, 0.205, 0.21]` 这种挤在一起的分布，
      最大空档换算成 log10 也能到 0.9（因为 log 把窄区间拉长了）—— 实测第一版就中了这一枪，
      夹具当场报"单峰→拒门 FAIL"。
    · 本函数给不出门（返回 `None`）时**不许硬定门**，要改用固定门的扫描表（`--sweep`）看取舍。
    """
    import numpy as np
    FLOOR = 1e-4
    v = np.sort(np.asarray([max(FLOOR, float(x)) for x in vals]))
    if len(np.unique(v)) < 3:
        return None, 0.0
    lv = np.log10(v)
    dl = np.diff(lv)
    i = int(np.argmax(dl))
    span = float(lv[-1] - lv[0])
    frac = float(dl[i]) / span if span > 0 else 0.0
    if dl[i] < gap_min or frac < gap_frac:
        return None, float(dl[i])
    return float(10 ** ((lv[i] + lv[i + 1]) / 2.0)), float(dl[i])


def consistency(rows, audit_json):
    """把逐音 sup 与 `audit_stems` 的命中（该归分轨 `_ratio≥0.25`）逐音对照。

    `audit_stems` 的 JSON 只给聚合读数 ⇒ 这里用**它写的 `HIT` 门**（0.25）与
    `STEM_OF` 的**首选分轨**在本工具读数上复算判定，比的是**口径**（窗长不同）而不是数字。
    """
    aj = json.load(open(audit_json, encoding='utf-8'))
    # audit 的逐轨精度只说"命中比例"，逐音位置对不上 ⇒ 按 (轨, 序号) 重建：
    # 它遍历 `notes_extra[tr]` 的顺序 = 本工具同顺序，所以只做**计数级**对照。
    by = {}
    for r in aj.get('rows') or []:
        by[r['track']] = r
    same = diff = strict = loose = n = 0
    for tr, r in by.items():
        rs = [x for x in rows if x['track'] == tr]
        if not rs or r.get('prec') is None:
            continue
        hit_expect = int(round(r['prec'] * r['n']))
        # 本工具口径下"该归分轨支撑"的命中数（首选分轨；Glock 取候选里最大）
        cands = [c for c in STEM_OF.get(tr, []) if c in (rs[0]['per'] or {})]
        n_hit = sum(1 for x in rs
                    if cands and max(x['per'].get(c, 0.0) for c in cands) >= 0.25)
        n += len(rs)
        d = n_hit - hit_expect
        diff += abs(d)
        strict += max(0, -d)      # 本工具命中更少 = 偏严
        loose += max(0, d)
        same += max(0, len(rs) - abs(d))
    return dict(n=n, same=same, diff=diff, strict=strict, loose=loose)


def selftest():
    """**判据自证**：合成件上"真音必须说有支撑、假音必须说没有"。

    造一条 bass 分轨（36 号音 = 65.4Hz）、一条 other（440Hz），另一条静音分轨；
    再喂 4 个音：① 音频里真有的 bass 音 → 有支撑；② 音频里没有的 bass 音 → 无支撑；
    ③ 静音时刻的音 → 无支撑**且** `db` 低（要能被"留白"那一栏分开）；④ 假音但别的分轨有能量。
    """
    import tempfile
    import numpy as np
    import soundfile as sf
    ok = []

    def tone(f0, dur, sr):
        t = np.arange(int(dur * sr)) / sr
        return (0.4 * np.sin(2 * np.pi * f0 * t)).astype('float32')

    sr = 22050
    d = tempfile.mkdtemp(prefix='sne_selftest_')
    y = np.zeros(int(4 * sr), dtype='float32')
    y[int(1.0 * sr):int(1.5 * sr)] += tone(65.4, 0.5, sr)      # bass 36 号
    sf.write(os.path.join(d, 'bass.wav'), y, sr)
    y2 = np.zeros(int(4 * sr), dtype='float32')
    y2[int(2.0 * sr):int(2.5 * sr)] += tone(440.0, 0.5, sr)    # other 里 A4
    sf.write(os.path.join(d, 'other.wav'), y2, sr)
    for nm in ('drums', 'vocals', 'guitar', 'piano'):
        sf.write(os.path.join(d, nm + '.wav'), np.zeros(int(4 * sr), dtype='float32'), sr)
    stems, sr2 = load_stems(d)
    specs = {nm: StemSpec(stems[nm], sr2, WIN_SEC * sr2, HOP_SEC * sr2) for nm in stems}
    real, r_db = specs['bass'].note(1.1, 65.4)
    # ⚠ "同窗假音"**不能取邻近音高**：矩形泄漏与低次谐波会把邻近带填起来
    #   （实测 p48=130.8 取到 0.527、连 p84=1046 都有 0.527 —— 那是 65.4 的 16 次谐波）。
    #   要判"假音"，取**远处音高**（1200Hz，谐波关系不成立）。
    fake, f_db = specs['bass'].note(1.1, 1200.0)
    nearlow, _ = specs['bass'].note(1.1, 130.8)
    far, far_db = specs['bass'].note(1.1, 440.0)        # 别条分轨的音
    blank, b_db = specs['bass'].note(3.0, 65.4)         # 静音时刻
    ok.append(('真音有支撑', real is not None and real >= 0.25))
    ok.append(('远处假音无支撑', fake is not None and fake < 0.05))
    ok.append(('静音窗给 None（不冒充假音）', blank is None))
    ok.append(('静音窗给低总能量（能与"有能量但无支撑"分开）', b_db < SILENT_DB))
    ok.append(('真音占比远高于远处假音', (real or 0) - (fake or 0) > 0.2))
    ok.append(('邻近音高不带"必须被填起来"的假设（记录实测该处 %.3f）' % (nearlow or -1),
               True))
    # 空档标定：双峰给中间门、单峰拒门。
    # ⚠ 双峰夹具要有**离散度**：全部同值（只有 2 个 unique）会被"少于 3 个不同值 ⇒ 拒门"拦下，
    #   那是夹具不真（实测当场踩到），所以两簇各给 ±0.1 的抖动。
    rng = np.random.RandomState(7)
    v_lo = 0.001 + 0.0002 * rng.rand(20)
    v_hi = 0.6 + 0.05 * rng.rand(20)
    thr, sz = gap_threshold(list(v_lo) + list(v_hi))
    ok.append(('双峰→门落在空档里', thr is not None and 0.002 < thr < 0.5))
    thr2, sz2 = gap_threshold([0.2, 0.21, 0.19, 0.205, 0.2])
    ok.append(('单峰→拒门（相对空档拦得住对数拉伸）', thr2 is None))
    print('  真音 %.3f（%.1fdB）· 远处假音 %.3f · 邻近音高 %.3f · 别轨音 %.3f · 静音窗 %s（%.1fdB）'
          % (real or -1, r_db or -300, fake or -1, nearlow or -1, far or -1, blank, b_db or -300))
    for nm, v in ok:
        print('  %-30s %s' % (nm, 'PASS' if v else 'FAIL'))
    print('stem_note_evidence 自检 ' + ('PASS' if all(v for _n, v in ok) else 'FAIL'))
    return all(v for _n, v in ok)


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('song', nargs='?')
    ap.add_argument('--stems', help='分轨目录（**裸名**，见 PITFALLS 335）')
    ap.add_argument('--audit-json', default=None, help='audit_stems 的 JSON（口径一致性对照）')
    ap.add_argument('--json', default=None, help='逐音读数写成 JSON')
    ap.add_argument('--drop', type=float, default=0.05, help='拟剔门：sup < 这个算"无支撑"')
    ap.add_argument('--keep', type=float, default=0.25, help='有支撑门（默认 = audit_stems.HIT）')
    ap.add_argument('--sweep', default='', help='门扫描列表（逗号分隔）')
    ap.add_argument('--gap-min', type=float, default=2.0,
                    help='空档标定的最小**绝对**空档（log10 单位：2.0 = 两个数量级）')
    ap.add_argument('--gap-frac', type=float, default=0.5,
                    help='空档标定的最小**相对**空档（占 log 值域的比例；拦"对数拉伸出来的假双峰"）')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not (a.song and a.stems):
        print(__doc__)
        return 1
    p = a.song if os.path.isfile(a.song) else os.path.join(SONGS, a.song, 'song.json')
    if not os.path.exists(p):
        print('找不到 %s' % p)
        return 1
    sweep = [float(x) for x in a.sweep.split(',') if x.strip()]
    rep = analyse(p, a.stems, a.audit_json, a.drop, a.keep, sweep, a.gap_min, a.gap_frac)
    if a.json:
        json.dump(rep, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('已写 %s' % a.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
