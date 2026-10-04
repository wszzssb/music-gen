#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""timbre_audit.py —— **逐段"音色对不对"的自动体检**（把编制表接到我方编配上）。

## 为什么需要（2026-09-25，本次事故的直接产物）

`chroma` 只能证明"音高内容大体对"（实测 0.85~0.95，区分度低），而用户听到的"不像"
**全在音色层**，音色一直没有尺子 → 每改一版都要等人耳验收，一轮 5~10 分钟。
本次两个真实事故都出在这一层、而且**都能被这个工具提前抓出来**：

| 事故 | 原曲那层是什么 | 我们用了什么 | 后果 |
|---|---|---|---|
| 开头的"厚合成器垫" | `h6_other`，起音 **284ms**（软起音+持续） | **Strings 长音**（GM 49） | 高频嘶声 = 用户说的"**蚊子叫**"（2–6kHz 占比 2.73%→回退后 0.00%） |
| 开头 18 秒的低音 | **不存在**（`h6_bass` −87/−81dB） | **Bass 13+29 音**（Acoustic Bass） | 凭空贝斯（源头筛掉 42 音） |

## 判据（五层，全部可复核）

① **原曲侧**：逐段量 `stems_flat\*` 各分轨的 RMS → 主导分轨（能量最高）+ 它的**物理形态**
   （起音 10→90% 时间、谱质心）；② **我方侧**：逐段从 MIDI 数各轨音数 → 主导轨 +
   从渲染音频量同段特征；③ **对照**：原曲主导分轨 → 期望乐器族（other→垫子 ·
   piano→钢琴 · bass→贝斯 · vocals→人声/主奏 · drums→鼓）；
④ **判定**：期望族与我方主导轨不符 → 报警（并给出"该换成什么"）。
⑤ **主奏归属对照**（2026-10-02 接进交付链）：前四层只看"**整段谁最响**"，
   抓不到"**主奏旋律用错音色**"（旋律往往不是最响的那条）。第⑤层用
   `who_plays_lead.py` 的按**音高**判定，逐段回答"我方这条旋律线在参考曲里是谁在弹"：

```bash
python scripts\timbre_audit.py <song.json> --ref <原曲.wav> --stems <分轨目录> --mine <我方.wav>
#   第⑤层自动跑；--lead-track 指定我方主奏轨 · --no-lead 跳过（快跑）
```

⚠ **"用哪条轨量"会 3 倍地改变读数**（实测同一首：`Hook` 轨报警 33% vs `Piano` 轨 93%）
   ⇒ 第⑤层**不猜**：候选轨各量一遍，取**旋律族占比最高**的那条，其余作为备选列出。
   ⚠ **"原曲哪段最响"不能当"哪段有旋律"用**：实测参考曲里"主导分轨 ∈ 旋律族"的段
   只占 **12%~17%**，据它做"覆盖判据"在改前/改后触发**同样 3 段**（无区分度，`PITFALLS.md` 307）。

⚠ 与 `probe_instruments.py` 的分工：那个查**原曲**（该用什么乐器），这个查**我方**
（实际用了什么、差在哪）—— 两者接起来才是闭环。

## 用法

```bash
python scripts\timbre_audit.py <song.json 或曲名> --ref <原曲.wav> --stems <demucs分轨目录> \
       --mine <我方渲染.wav> [--json out.json]
python scripts\timbre_audit.py --selftest
```
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()

import metrics
import midi_file as mf

# 原曲分轨名 → 期望的我方轨（乐器族）
EXPECT = {
    'other': ('Pad', 'Strings'),
    'piano': ('Piano',),
    'bass': ('Bass',),
    'vocals': ('Hook', 'Piano', 'Strings'),
    'guitar': ('Hook', 'Strings'),
    'drums': ('Perc',),
}
ATTACK_MS = 0.30          # 起音窗口（秒）
# 判据常量（改前先按真病例重标定：v22a 全曲 2–6k% ≤0.25%，v25 命中段 3.1~28.6%）
HISS_MIN_PCT = 3.0        # 我方该段 2–6k% 至少这么高
HISS_RATIO = 3.0          # 且达原曲的这么多倍，才算"高频嘶声"
SOFT_ATTACK_MS = 150.0    # 原曲起音慢于它 = 软起音（垫子/弦乐）
HARD_ATTACK_MS = 30.0     # 我方起音快于它 = 硬起音（击弦）→ 形态不符
CENTROID_RATIO = 2.0      # 相对差 >2.0（≈ 我方/原曲 >3 倍）才报（**仅供参考**：原曲分轨 vs 我方混音本不可比）
# 第⑤层（主奏归属对照）的候选轨与口径
LEAD_ORDER = ('Melody', 'Hook', 'Strings', 'Glock', 'Arp', 'Guitar', 'Synth', 'Piano', 'Organ')
LEAD_EXCLUDE = ('Drums', 'Perc', 'Bass', 'Pad')   # 不当主奏参选：打击/低音/垫子
MIN_LEAD_NOTES = 10       # 候选主奏轨至少这么多音（`Glock 6 音`这种点缀轨不参选）


def onset_ms(y, sr):
    """起音 10%→90% 的时间（ms）—— 软起音（垫子/弦乐）几十~几百 ms，击弦 <20ms。"""
    if len(y) < int(sr * 0.05):
        return None
    e = np.abs(y)
    k = max(1, int(sr * 0.002))
    e = np.convolve(e, np.ones(k) / k, mode='same')
    pk = float(e.max())
    if pk <= 1e-9:
        return None
    i10 = int(np.argmax(e >= 0.10 * pk))
    i90 = int(np.argmax(e >= 0.90 * pk))
    return round(1000.0 * max(i90 - i10, 1) / sr, 1)


def centroid(y, sr):
    n = 4096
    if len(y) < n:
        return None
    P = np.abs(np.fft.rfft(y[:n] * np.hanning(n))) ** 2
    f = np.fft.rfftfreq(n, 1.0 / sr)
    s = P.sum()
    return round(float((f * P).sum() / s), 1) if s > 1e-12 else None


def hi_ratio(y, sr, lo=2000, hi=6000):
    """2–6kHz 占比（%）—— "蚊子叫"就是它。"""
    n = 8192
    if len(y) < n:
        return None
    P = np.abs(np.fft.rfft(y[:n] * np.hanning(n))) ** 2
    f = np.fft.rfftfreq(n, 1.0 / sr)
    m = (f >= lo) & (f <= hi)
    s = P.sum()
    return round(100.0 * P[m].sum() / s, 2) if s > 1e-12 else None


def rms_db(y):
    return round(20 * np.log10(max(float(np.sqrt(np.mean(y ** 2))), 1e-12)), 1) if len(y) else -120.0


def section_bounds(sj):
    """→ [(段名, 起秒, 止秒)]。**每小节拍数必须乘进去。**

    ⚠ 2026-09-25 修**真 bug**：原来写的是 `acc * (60/bpm)`（`acc` 累计的是**小节**数），
    漏了"每小节几拍"→ 段界整体**小 4 倍**（4/4 下），于是：
      · "S01" 其实是 0–1.6s、"S13" 其实是 18.5–23.0s（**标签全错位**）；
      · 末段只到 **44s** —— 整首 176 秒的曲子，**44 秒之后从来没被量过**
        （这正是"85s / 146s 的高频来源"一直定位不到的直接原因之一）。
    错窗口在版本间是同一套，所以当时的**相对**结论（v22a 4 段 vs v25 8 段）没被推翻，
    但**段归属与覆盖时长**全错 —— 修完必须重测（见 `selftest.t_timbre_and_stem_filter` 的段界断言）。
    """
    bpm = float(sj.get('bpm') or 145.96)
    beats = float((sj.get('meter') or [4, 4])[0] or 4.0)
    bar = beats * 60.0 / bpm
    out, acc = [], 0.0
    for s in (sj.get('sections') or []):
        bars = float(s.get('bars') or 0)
        out.append((s.get('name'), acc * bar, (acc + bars) * bar))
        acc += bars
    return out


def lead_layer(song, stems, lead_track=None, thr=None, max_candidates=6):
    """**第⑤层：主奏归属对照** —— "参考曲里这条旋律是谁在弹，我们用的是哪条轨"。

    为什么单列一层（2026-10-02 实测）：上面四层量的是"**整段谁最响**"（编配层），
    而用户说的"不像"最常出在**主奏旋律**上：参考曲是合成器/吉他弹的那条线，
    我们拿钢琴弹了 —— 单看"谁最响"永远抓不到（旋律往往不是最响的那条）。
    口径 = `who_plays_lead.py`（按**音高**判，不是按响度；按响度会被低音/鼓骗）。

    ⚠ **"用哪条轨量"会 3 倍地改变读数** —— 实测同一首 `dear_good_friends`：
    `Hook` 轨报警 **33%**、`Piano` 轨报警 **93%**（钢琴轨同时承担伴奏，它的音在参考曲里
    大多被别的分轨盖过）。所以这里**不猜**：把所有候选轨各量一遍，
    **取旋律族占比最高的那条**（= "我们哪条轨最像参考曲的主奏"），其余作为备选一并报出。
    → `--lead-track` 可显式指定；`--no-lead` 跳过这一层。

    → (结果 dict, None)：`{'picked','thr','candidates':[{track,notes,lead_share,rows,…}]}`
    """
    import who_plays_lead as W
    thr = W.LEAD_SHARE_MIN if thr is None else thr
    if not os.path.isdir(stems):
        return None
    sigs, sr = W.load_stems(stems)
    if not sigs:
        return None
    ne = (json.load(open(song, encoding='utf-8')).get('notes_extra') or {}) if song else {}
    ok = lambda t: t not in LEAD_EXCLUDE and len(ne.get(t) or []) >= MIN_LEAD_NOTES   # noqa: E731
    names = [t for t in LEAD_ORDER if ok(t)]
    names += [t for t in ne if t not in names and ok(t)]
    if lead_track:
        names = [lead_track] + [t for t in names if t != lead_track]
    _bar, secs = W.song_timeline(song)
    cands = []
    for t in names[:max_candidates]:
        pts = W.melody_points(song, t)
        if not pts:
            continue
        _win, seg, agg = W.attribute(pts, sigs, sr, secs)
        rows = W.section_verdicts(seg, secs, thr=thr)
        s = W.summary(rows)
        cands.append({'track': t, 'notes': agg['notes'],
                      'lead_share': round(agg['lead_share'], 3),
                      'n_judged': s['n_judged'], 'n_alarm': s['n_alarm'],
                      'alarm_ratio': None if s['alarm_ratio'] is None else round(s['alarm_ratio'], 3),
                      'alarms': [r['seg'] for r in s['alarms']], 'rows': rows})
    if not cands:
        return None
    pick = max(cands, key=lambda c: (c['lead_share'], c['n_judged']))
    return {'picked': pick['track'], 'thr': thr, 'candidates': cands, 'pick': pick}


def lead_report(res, out=print):
    """第⑤层的可读输出（含"换条轨会怎样"的备选读数）。"""
    if not res:
        out('⑤ 主奏归属对照：跳过（没有可用分轨 / 没有候选轨）')
        return
    out('⑤ 主奏归属对照（按音高判"参考曲里这条线是谁在弹"；门 旋律族 ≥ %.0f%%）'
        % (100 * res['thr']))
    out('   候选轨：' + ' · '.join(
        '%s %s %.0f%%（%d 音%s）' % ('**' + c['track'] + '**' if c['track'] == res['picked'] else c['track'],
                                     '' if c['alarm_ratio'] is None else '段报警%.0f%%' % (100 * c['alarm_ratio']),
                                     100 * c['lead_share'], c['notes'],
                                     '' if c['lead_share'] >= res['thr'] else ' ← 旋律族低于门')
        for c in sorted(res['candidates'], key=lambda x: -x['lead_share'])))
    p = res['pick']
    if p['alarm_ratio'] is None:
        out('   ⚠ 轨 %s 没有可判的段' % p['track'])
    elif p['lead_share'] < res['thr']:
        out('   **报警**：连最好的那条轨（%s）旋律族占比也只有 %.0f%% —— '
            '这段旋律在我们的编配里**没有一条轨承担参考曲的主奏角色**' % (p['track'], 100 * p['lead_share']))
    else:
        out('   选中 **%s**（旋律族 %.0f%% ≥ 门）· 段级 **%d/%d 段报警（%.0f%%）**'
            % (p['track'], 100 * p['lead_share'], p['n_alarm'], p['n_judged'],
               100 * p['alarm_ratio']))
        for r in p['rows']:
            if not r['ok']:
                out('     · %-7s %-12s 旋律族 %3.0f%%  %s'
                    % (r['seg'], r['t'], 100 * r['lead_share'],
                       ' · '.join('%s %d' % (k, v) for k, v in
                                  sorted(r['share'].items(), key=lambda x: -x[1]))))


def audit(song, ref, stems, mine, lead_track=None, do_lead=True):
    sj = json.load(open(song, encoding='utf-8')) if os.path.isfile(song) else None
    name = os.path.basename(os.path.dirname(song)) if sj else str(song)
    bpm = float(sj['bpm']) if sj else 145.96
    spb = 60.0 / bpm
    segs = section_bounds(sj) if sj else []
    mid = None
    for cand in (os.path.join(os.path.dirname(song), name + '.mid'),
                 os.path.join(os.path.dirname(song), 'siren_end2.mid')):
        if os.path.isfile(cand):
            mid = mf.import_midi(cand)
            break
    refY, refSr = metrics.read_audio(ref, mono=True)
    mineY, mineSr = metrics.read_audio(mine, mono=True)
    refY = np.asarray(refY, dtype='float32'); mineY = np.asarray(mineY, dtype='float32')
    st = {}
    for f in os.listdir(stems):
        if f.lower().endswith('.wav'):
            y, sr = metrics.read_audio(os.path.join(stems, f), mono=True)
            st[f[:-4]] = (np.asarray(y, dtype='float32'), sr)

    rows, bad = [], []
    for (nm, t0, t1) in segs:
        r = {}
        # ① 原曲主导分轨
        for k, (y, sr) in st.items():
            r[k] = rms_db(y[int(t0 * sr):int(t1 * sr)])
        dom = max(r, key=r.get) if r else None
        dom_short = (dom or '').split('_')[-1]
        rs = (int(t0 * 60 / spb * spb * 0 + t0 * 0), 0)  # 占位，避免误用
        del rs
        ry = refY[int(t0 * refSr):int(t1 * refSr)]
        # ② 我方该段主导轨（MIDI 音数）
        cnt = {}
        if mid:
            b0, b1 = t0 / spb, t1 / spb
            for t in mid['tracks']:
                c = sum(1 for x in t['notes'] if b0 <= x[0] < b1)
                if c:
                    cnt[t['name']] = c
        my = max(cnt, key=cnt.get) if cnt else None
        myy = mineY[int(t0 * mineSr):int(t1 * mineSr)]
        exp = EXPECT.get(dom_short, ())
        # ③ **物理量对照**（轨名对照在本库恒真：`other` 装着所有杂项，21/25 段都是它能量最高 ——
        #    实测按轨名判 → 21/25 段"不符"，没有区分度。物理量才有：v25 的 2–6k% 能到 28.58%，
        #    而 v22a 全是 0.0%）。
        rh, mh = hi_ratio(ry, refSr), hi_ratio(myy, mineSr)
        ra, ma = onset_ms(ry, refSr), onset_ms(myy, mineSr)
        rcen, mcen = centroid(ry, refSr), centroid(myy, mineSr)
        issues = []
        if rh is not None and mh is not None and mh > max(HISS_MIN_PCT, rh * HISS_RATIO):
            issues.append('高频嘶声(2-6k %.1f%% vs 原曲 %.1f%%)' % (mh, rh))
        if ra is not None and ma is not None and ra > SOFT_ATTACK_MS and ma < HARD_ATTACK_MS:
            issues.append('形态不符(原曲软起音 %sms · 我方硬起音 %sms)' % (ra, ma))
        if rcen and mcen and abs(mcen - rcen) / max(rcen, 1.0) > CENTROID_RATIO:
            # ⚠ 阈值 2.0（差 3 倍）而不是 0.5：**原曲是"分轨"的质心、我方是"混音"的质心**，
            #   两者本来就不可比 —— 实测 0.5 时连用户认可的 v22a 都中 16/25 段（噪声）。
            #   只有极端偏离才报，并注明口径不同。
            issues.append('亮度极端偏离(质心 %.0f vs 原曲分轨 %.0f，口径不同仅供参考)'
                          % (mcen, rcen))
        # **只有"实质问题"参与判定**："口径不同仅供参考"那类（原曲分轨 vs 我方混音的质心）
        # 只印出来给人看 —— 实测它会把用户认可的 v22a 也报成 10/25 段（噪声）。
        real = [x for x in issues if '仅供参考' not in x]
        ok = not real
        row = {'seg': nm, 't': '%0.1f-%0.1f' % (t0, t1), 'ref_dom': dom_short,
               'ref_onset_ms': ra, 'ref_centroid': rcen, 'ref_hi_2_6k': rh,
               'mine_dom': my, 'mine_onset_ms': ma, 'mine_centroid': mcen,
               'mine_hi_2_6k': mh, 'expect': '/'.join(exp), 'ok': ok,
               'issues': issues, 'real_issues': real}
        rows.append(row)
        if not ok:
            bad.append(row)
    return {'song': name, 'rows': rows, 'mismatch': bad,
            'n_bad': len(bad), 'n_total': len(rows),
            'lead': lead_layer(song, stems, lead_track) if (do_lead and song) else None}


def report(res, out=print):
    out('曲目 %s：**%d/%d 段有音色问题**' % (res['song'], res.get('n_bad', len(res['mismatch'])),
                                        res.get('n_total', len(res['rows']))))
    out('%-6s %-8s %-11s %-11s %-9s %-9s %s'
        % ('段', '原曲主导', '原曲起音ms', '我方起音ms', '原曲2-6k%', '我方2-6k%', '判定'))
    for r in res['rows']:
        out('%-6s %-8s %-11s %-11s %-9s %-9s %s'
            % (r['seg'], r['ref_dom'], r['ref_onset_ms'], r['mine_onset_ms'],
               r['ref_hi_2_6k'], r['mine_hi_2_6k'],
               'OK' if r['ok'] else '**' + ' · '.join(r.get('issues') or []) + '**'))
    if res['mismatch']:
        out('\n⚠ 有问题的段（%d 个）：' % len(res['mismatch']))
        for r in res['mismatch']:
            out('  %-6s %s' % (r['seg'], ' · '.join(r.get('issues') or [])))
    out('')
    lead_report(res.get('lead'), out)


def selftest(verbose=True):
    """尺子自检：物理量必须能分开"软起音/硬起音"与"有无高频嘶声"。"""
    sr = 22050
    t = np.arange(int(sr * 0.5)) / sr
    soft = np.sin(2 * np.pi * 233 * t) * (1 - np.exp(-t * 6))      # 软起音（垫子/弦乐）
    hard = np.sin(2 * np.pi * 233 * t) * np.exp(-t * 30)            # 硬起音（击弦）
    o_soft, o_hard = onset_ms(soft, sr), onset_ms(hard, sr)
    assert o_soft > o_hard, '自检①：软起音该比硬起音慢（软 %.1fms vs 硬 %.1fms）' % (o_soft, o_hard)
    # 高频嘶声：233Hz 叠 3.5kHz
    hiss = soft + 0.35 * np.sin(2 * np.pi * 3500 * t)
    r_clean, r_hiss = hi_ratio(soft, sr), hi_ratio(hiss, sr)
    assert r_hiss > r_clean * 3, \
        '自检②：叠了 3.5kHz 的该读出更高 2–6k 占比（干净 %.2f%% vs 嘶声 %.2f%%）' % (r_clean, r_hiss)
    c_soft = centroid(soft, sr)
    assert c_soft is not None and 100 < c_soft < 800, \
        '自检③：233Hz 正弦的质心该在中低频，实得 %s' % c_soft
    if verbose:
        print('timbre_audit 自检 PASS：软起音 %.0fms > 硬起音 %.0fms · 嘶声 %.2f%% vs 干净 %.2f%% · 质心 %.0fHz'
              % (o_soft, o_hard, r_hiss, r_clean, c_soft))
    return True


def main():
    ap = argparse.ArgumentParser(description='逐段音色对照（原曲编制 vs 我方编配）')
    ap.add_argument('song', nargs='?')
    ap.add_argument('--ref', help='原曲音频')
    ap.add_argument('--stems', help='demucs 分轨目录（h4_*/h6_*）')
    ap.add_argument('--mine', help='我方渲染音频')
    ap.add_argument('--lead-track', default=None,
                    help='第⑤层显式指定"我方主奏轨"（默认自动：取旋律族占比最高那条）')
    ap.add_argument('--no-lead', action='store_true', help='跳过第⑤层（主奏归属对照）')
    ap.add_argument('--json', default=None)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not (a.song and a.ref and a.stems and a.mine):
        ap.print_help(); return 1
    res = audit(a.song, a.ref, a.stems, a.mine, a.lead_track, not a.no_lead)
    report(res)
    if a.json:
        json.dump(res, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
