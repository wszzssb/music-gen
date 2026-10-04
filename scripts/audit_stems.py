#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""audit_stems.py —— **用分轨当独立尺子，逐轨量"提取精度 + 召回"**（声源分离 → 判归属）。

## 什么时候用（**每个提取/还原交付前，标准动作**）

用户口径（2026-10-04）："我要你可以应用到以后的所有音乐生成和提取上"。
这是**转录之后、动手修之前**的那把尺子：回答"我发出来的音对不对"（精度）与
"原曲有的我漏了没有"（召回），**不依赖转录模型自己**。

⚠ **别拿 4 轨分轨判钢琴/吉他/垫子层**（实测教训，见 `PITFALLS.md` 316）：
`htdemucs` 4 轨把 piano + guitar + 人声残留全塞进 `other`，于是
· 拿 `other` 量吉他轨（`Hook`）→ **低估**：65.2%（6 轨尺子给 **81.1%**）；
· 拿 `other` 量 `Pad` → 55.6% 看着"还行"，换 6 轨后是 **0%**（那 9 个音根本不是 Pad）。
**默认用 6 轨**（`htdemucs_6s`：drums/bass/other/vocals/**guitar**/**piano**）。

## 两个口径（都写进报告，别只报一个）

· **精度**＝我方每个音在**它该在的那条分轨**上、该音高带（0.75~1.5×f0）的能量占比 ≥0.25
  记为"落到了"。**这是可信的那一半**（"我发的音对不对"）。
· **召回**＝分轨逐小节 RMS > （**全混音逐小节 RMS 的中位 − 30dB**）算"真在响"，
  我方该轨在该小节有音的占比。⚠ **必须用绝对门**：用"该轨自己的中位 −6dB"这种相对门，
  分轨的噪声底（实测 −77~−85dB 的小节）会被当成"在响"，召回读数就废了（实测踩过）。
· **移调单列**：引擎 `TR_SHIFT` 会把伴奏层整轨 −12 ⇒ 再按**低一个八度**算一次命中，单列出来，
  不算进精度分子（否则把"移调"当成"错音"）。

## 用法

```bash
python scripts\audit_stems.py <曲目> --stems <分轨目录> [--ref <参考混音.wav>] [--json 输出.json]
# 分轨目录 = demucs 的 -o 目录下的 <模型>/<曲名>/（工具会自动认出 6 轨还是 4 轨）
python scripts\audit_stems.py douzo_ymt3_nodrum --stems "D:\test\_tmp\douzo-ls\stems6\htdemucs_6s\ref_44k"
python scripts\audit_stems.py <曲目> --stems <目录> --selftest   # 尺子自检（合成件，不依赖曲库）
```
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
HIT, EMPTY = 0.25, 0.05          # 精度门：≥HIT 算"落到了"；<EMPTY 算"分轨此刻没这个音"
RECALL_DROP_DB = 30.0            # 召回门：分轨小节 RMS 低于（全混音中位 − 这么多 dB）算"没这段"
# **准入闸门**（`--gate` 用；2026-10-04 泛化测试定的门）：
#   可量轨里 ≥50% 同时满足「精度 ≥PREC_GATE 且召回 ≥REC_GATE」⇒ `fixable`，否则 `unusable`。
#   依据：douzo（稀疏）达标轨 4/4 ⇒ 可修且修完有效；s01（密集流行）只有 4/8 过线、
#   s02（只吐一层）1/1 但整体漏 5 层 ⇒ 这两首**该先解决转录**，不该进"去鼓/补层"。
PREC_GATE = 0.50                 # 可修轨的精度下限
REC_GATE = 0.50                  # 可修轨的召回下限
MIN_LAYER_BARS = 8               # 分轨"在响"少于这么多小节 ⇒ 不算"整层缺失"
LAYER_COVER_FRAC = 0.30          # 我方覆盖 < 该比例 ⇒ 判定该层缺失
LAYER_DROP_DB = 8.0              # "整层缺失"用**分轨自身中位 − 这么多 dB** 当门（别用绝对门）

# 引擎轨 → 该归的分轨（**唯一真源**；`Glock` 有多个候选时取精度最高那条，理由见 __doc__）
STEM_OF = {
    'Bass': ['bass'], 'Piano': ['piano'], 'Hook': ['guitar'], 'Pad': ['other'],
    'Strings': ['other'], 'Arp': ['other'], 'Glock': ['piano', 'guitar'],
    'Melody': ['vocals', 'other'], 'Perc': ['drums'], 'Drums': ['drums'],
}
# 4 轨分轨里没有 piano/guitar ⇒ 这些轨在 4 轨尺子下**不可信**（不是"还行"，是测不到）
NEEDS_6S = {'Piano', 'Hook', 'Glock', 'Strings', 'Arp', 'Pad'}


def _notes(ne, tr):
    v = ne.get(tr)
    v = (v.get('notes') if isinstance(v, dict) else v) or []
    return v


def load_stems(folder):
    """返回 ({分轨名: mono}, sr, 是 6 轨还是 4 轨)"""
    import soundfile as sf
    names = ('drums', 'bass', 'other', 'vocals', 'guitar', 'piano')
    out, sr = {}, None
    for nm in names:
        for cand in (nm + '.wav', nm.capitalize() + '.wav'):
            p = os.path.join(folder, cand)
            if os.path.exists(p):
                x, sr = sf.read(p, always_2d=True)
                out[nm] = x.mean(axis=1)
                break
    if not out:
        raise SystemExit('分轨目录里没找到 drums/bass/other/vocals(.wav)：%s' % folder)
    return out, sr, ('guitar' in out and 'piano' in out)


def _ratio(mono, sr, t, f0):
    """该时刻、该音高带（0.75~1.5×f0）的能量占该窗总能量的比例。

    ⚠ **窗内几乎是静音时返回 `None`**（不是 0）：0 会被下游当成"分轨此刻没这个音"，
    于是一段**根本没乐器在响**的静音（或分轨没声）被算成"空音"，把精度压低。
    判据是能量门而不是"全零"—— 实测全零数组经 FFT 后仍有 1e-30 的浮点残差，
    `sum()==0` 那种写法抓不住（本轮自检当场抓到）。"""
    import numpy as np
    i = int(t * sr)
    seg = mono[i:i + int(0.06 * sr)]
    if len(seg) < 128:
        return None
    rms = float(np.sqrt((seg ** 2).mean()))
    if rms < 1e-6:                       # ≈ −120 dBFS：窗里没有东西，无从判断
        return None
    W = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    f = np.fft.rfftfreq(len(seg), 1.0 / sr)
    m = (f >= f0 * 0.75) & (f <= f0 * 1.5)
    if not m.any():
        return None
    tot = float((W ** 2).sum())
    if tot < 1e-24:
        return None
    return float((W[m] ** 2).sum() / tot)


def _bar_rms(mono, sr, bar_sec):
    import numpy as np
    nb = int(np.ceil(len(mono) / (bar_sec * sr)))
    out = []
    for b in range(nb):
        seg = mono[int(b * bar_sec * sr):int((b + 1) * bar_sec * sr)]
        out.append(20 * np.log10(max(1e-9, float(np.sqrt((seg ** 2).mean())))))
    return np.array(out)


def audit(song_json, stems_dir, ref=None, verbose=True, accept_missing=None):
    """返回逐轨读数列表（每项一个 dict）"""
    import numpy as np
    stems, sr, is6 = load_stems(stems_dir)
    d = json.load(open(song_json, encoding='utf-8'))
    ne = d.get('notes_extra') or {}
    bpm = float(d.get('bpm') or 120.0)
    bb = d.get('bar_beats')
    bar_sec = 60.0 / bpm * (float(bb) if bb else 4.0)      # 一小节几拍：引擎的真源是 bar_beats
    if ref and os.path.exists(ref):
        import soundfile as sf
        mix, _ = sf.read(ref, always_2d=True)
        thr = float(np.median(_bar_rms(mix.mean(axis=1), sr, bar_sec))) - RECALL_DROP_DB
    else:
        thr = float(np.median(_bar_rms(stems.get('other', next(iter(stems.values()))),
                                       sr, bar_sec))) - RECALL_DROP_DB
    rows = []
    for tr in ne:
        lst = _notes(ne, tr)
        if not lst:
            continue
        cands = [c for c in STEM_OF.get(tr, []) if c in stems]
        if not cands:
            # ⚠ **没有对应分轨 ⇒ 显式列成"测不到"**，不许静默跳过。
            #   实测：4 轨分轨没有 piano/guitar，第一次跑时 Piano/Hook/Glock 三行**整行消失**，
            #   看报告的人会以为"那几条没问题"（空集合假通过，坑 117 那一族）。
            rows.append(dict(track=tr, stem='—', n=len(lst), prec=None, empty=None,
                             oct_hit=None, recall=None, act_bars=0, gap=0,
                             trustworthy=False,
                             why='分轨里没有 %s（%s 轨尺子）'
                                 % ('/'.join(STEM_OF.get(tr, []) or ['?']),
                                    '6' if is6 else '4')))
            continue
        best = None
        for c in cands:
            sm = stems[c]
            hit = empty = oct_hit = 0
            for x in lst:
                t = x[0] * bar_sec + x[1] * (60.0 / bpm)
                f0 = 440.0 * 2 ** ((x[3] - 69) / 12.0)
                r = _ratio(sm, sr, t, f0)
                if r is None:
                    continue
                if r >= HIT:
                    hit += 1
                elif r < EMPTY:
                    empty += 1
                    r2 = _ratio(sm, sr, t, f0 / 2.0)      # 移调后的原音高
                    if r2 is not None and r2 >= HIT:
                        oct_hit += 1
            e = _bar_rms(sm, sr, bar_sec)
            act = e > thr
            have = np.zeros(len(e), dtype=bool)
            for x in lst:
                if 0 <= x[0] < len(have):
                    have[x[0]] = True
            n = max(1, len(lst))
            cov_bars = act & have                    # 该层"在响"且我方有音的小节
            row = dict(track=tr, stem=c, n=len(lst), prec=hit / n, empty=empty / n,
                       oct_hit=oct_hit / n, recall=float(cov_bars.sum() / max(1, act.sum())),
                       act_bars=int(act.sum()), gap=int((act & ~have).sum()),
                       cov_bars=cov_bars, trustworthy=(is6 or tr not in NEEDS_6S))
            if best is None or row['prec'] > best['prec']:
                best = row
        rows.append(best)
    rows.sort(key=lambda r: (-(r['n'] or 0)))
    # ① **整层缺失**：分轨有内容、我方**没有对应轨**（或对应轨几乎是空的）
    #    为什么必须有（2026-10-04 泛化测试实测）：某首慢速抒情曲转录**只吐出 1 条轨**
    #    （13 个解码通道只出 1 个），而逐轨审计**只报那 1 轨的读数** ——
    #    没被转录的 5 层在报告里**根本不出现**，看报告的人以为"没报就是没问题"
    #    （空集合假通过，同族坑 117）。这条专门抓"整层消失"。
    #    ⚠ 判据两处都被实测修过：
    #      · **不能用"我方该轨覆盖了分轨在响小节的 ≥30%"**（第一版就是这么写的）：
    #        整层缺失时**根本没有那条轨**，于是"覆盖 0%"反而漏报（实测 s02 的 5 层全漏）；
    #      · **门要用分轨自己的分布**（自身中位 −8dB），不能沿用召回的绝对门
    #        （全混音中位 −30dB 对低频/轻声层太严，实测把 s02 的 bass/other 判成"没内容"）；
    #        但也不能太松 —— 松到"只要有一点能量就算"，就会把**已按证据清掉的鼓**
    #        （douzo 去鼓版：分轨里有鼓声、我方有意不放）误报成缺失。
    missing = []
    # ⚠ `other` **不参与"整层缺失"判定**：它是 Demucs 的"其余桶"（钢琴/吉他/人声残留
    #   全在里面），不是一件乐器 —— 它的内容按设计就分散在 `Piano`/`Hook` 等轨上
    #   （实测 douzo 的 other 56 小节在响，而 Piano 49 小节 + Hook 70 小节正好盖着它）。
    #   把它当"层"会让每一首都被误报缺失。
    for nm in ('drums', 'bass', 'guitar', 'piano', 'vocals'):
        if nm not in stems:
            continue
        e = _bar_rms(stems[nm], sr, bar_sec)
        act = e > (float(np.median(e)) - LAYER_DROP_DB)
        n_act = int(act.sum())
        if n_act <= MIN_LAYER_BARS:                   # 该层本来就几乎不响 ⇒ 不算缺
            continue
        own = [r for r in rows if nm in (STEM_OF.get(r['track']) or [])]
        # **覆盖 = 该层"在响"的小节里，有多少小节有我方对应轨的音**（取各轨并集）。
        # ⚠ 第一版写成 `act_bars - gap`：那是"某一条轨自己的召回分子"，对
        #   `other` 这种**多条轨共用一条分轨**的情形会算成 0（实测 douzo 的
        #   Piano+Hook 明明盖着 other，却报"覆盖 0 小节"）。
        if own:
            cov = np.zeros(len(act), dtype=bool)
            for r in own:
                cb = r.get('cov_bars')
                if cb is not None:
                    cov = cov | np.asarray(cb)[:len(act)]
            covered = int((act & cov).sum())
        else:
            covered = 0
        if covered < LAYER_COVER_FRAC * n_act:
            missing.append(dict(stem=nm, bars=n_act, covered=covered,
                                pct=covered / max(1, n_act),
                                has_track=bool(own)))
    # ② **准入闸门**：可修 / 别做（**修之前先过这道门**）
    #    为什么必须有（同一次泛化测试）：s01 的 Piano 精度 **4.8%**、Hook **21.6%** ——
    #    这种错在**上游（转录本身）**，下游的"去鼓/补层"修不动，硬修只会把错音搬来搬去。
    #    判据：**可量的轨里 ≥50% 过线（精度 ≥PREC_GATE 且召回 ≥REC_GATE）** 才算 `可修`。
    meas = [r for r in rows if r['prec'] is not None and r['trustworthy']]
    passed = [r for r in meas if r['prec'] >= PREC_GATE and (r['recall'] or 0) >= REC_GATE]
    if not meas:
        verdict, why = 'unknown', '没有可量的轨（缺 6 轨分轨？）'
    elif len(passed) >= max(1, int(0.5 * len(meas))):
        verdict, why = 'fixable', ('%d/%d 条可量轨过线（精度≥%.0f%% 且召回≥%.0f%%）'
                                   % (len(passed), len(meas), 100 * PREC_GATE, 100 * REC_GATE))
    else:
        verdict, why = 'unusable', ('只有 %d/%d 条可量轨过线 —— **上游转录有问题，'
                                    '别进"去鼓/补层"这类修工序**（修不动）'
                                    % (len(passed), len(meas)))
    # ⚠ **整层缺失要压过"轨都过线"这个结论**：某首实测"13 个解码通道只出 1 个"，
    #   那条唯一的轨精度 60%、召回 100% ⇒ 只看轨的话它"全过线"，可它有 5 层根本没被转录。
    #   所以把缺失层数并进结论（≥2 层就算上游问题），别让"1/1 过线"骗人。
    unexpected = [m for m in missing if m['stem'] not in (accept_missing or ())]
    if len(unexpected) >= 2 and verdict == 'fixable':
        verdict = 'unusable'
        why = ('%d 层**整层缺失**（%s）—— 轨级读数好看是因为"没转录的层根本不出现"'
               '（空集合假通过），必须先解决转录'
               % (len(unexpected), '、'.join(m['stem'] for m in unexpected)))
    elif unexpected and verdict == 'fixable':
        why += '；⚠ 另有 %d 层缺失（%s）—— 若是**有意移除**（去鼓/去人声）可忽略，' \
               '否则先查转录' % (len(unexpected), '、'.join(m['stem'] for m in unexpected))
    elif missing and verdict == 'fixable':
        why += '；（缺失的 %s 已由 `--accept-missing` 声明为有意移除）' \
               % '、'.join(m['stem'] for m in missing)
    if verbose:
        print('尺子：%s（%s）· 精度门 ≥%.2f · 召回门 = 全混音中位 −%.0fdB'
              % (stems_dir, '6 轨 ✔' if is6 else '⚠ 4 轨（判不了 piano/guitar/垫子层）',
                 HIT, RECALL_DROP_DB))
        print('  %-8s %5s %10s %9s %9s %11s %9s %s'
              % ('轨', '音数', '精度', '空(<.05)', '移调命中', '召回(小节)', '缺口', '可信?'))
        for r in rows:
            if r['prec'] is None:
                print('  %-8s %5d %9s %8s %8s %10s %9s ✗ %s'
                      % (r['track'], r['n'], '测不到', '—', '—', '—', '—', r['why']))
                continue
            print('  %-8s %5d %9.1f%% %8.1f%% %8.1f%% %10.1f%% %4d/%-4d %s'
                  % (r['track'], r['n'], 100 * r['prec'], 100 * r['empty'],
                     100 * r['oct_hit'], 100 * r['recall'], r['gap'], r['act_bars'],
                     '✔' if r['trustworthy'] else '✗ 需 6 轨'))
        if missing:
            print('\n⚠ **整层缺失**（分轨有内容、我方没有对应轨或几乎是空的）：')
            for m in missing:
                print('   %-7s 分轨在响 %3d 小节 · 我方%s 覆盖 %3d 小节 = %3.0f%%'
                      % (m['stem'], m['bars'],
                         '该轨' if m['has_track'] else '**无对应轨**', m['covered'],
                         100 * m['pct']))
            print('   ⇒ 这是**漏整层**（不是错音）——先确认转录本身没失败'
                  '（`transcribe_ymt3` 的解码步数），再谈补层')
        print('\n【准入】%s —— %s' % (verdict, why))
    return rows, is6, missing, dict(verdict=verdict, why=why,
                                    passed=len(passed), measured=len(meas))


def selftest():
    """尺子自检：合成件上**已知答案**必须量对（含判据自证）。

    造一条 1 秒、含 440Hz 正弦的分轨：把音高 69（A4=440）标成"命中"、
    1200（音高带与 440 无交集）标成"空"，再检查 `_ratio` 分得开。
    """
    global HIT
    import numpy as np
    sr = 22050
    t = np.arange(sr) / sr
    tone = 0.5 * np.sin(2 * np.pi * 440.0 * t)
    r_in = _ratio(tone, sr, 0.1, 440.0)
    r_out = _ratio(tone, sr, 0.1, 1200.0)
    assert r_in is not None and r_in >= HIT, \
        '尺子自检失败：纯 440Hz 正弦在 440 的音高带上只给 %.3f（应 ≥%.2f）' % (r_in or -1, HIT)
    assert r_out is not None and r_out < EMPTY, \
        '尺子自检失败：纯 440Hz 正弦在 1200 的音高带上给了 %.3f（应 <%.2f）—— 判据没有分辨力' \
        % (r_out or -1, EMPTY)
    # 静音段必须给 None（而不是 0）—— 否则"分轨没声"会被算成"空音"
    assert _ratio(np.zeros(sr), sr, 0.1, 440.0) is None, \
        '全静音窗应返回 None（无能量无从判断），不该给一个数'
    # 判据自证：把门放宽到 0 → 必须立刻失去分辨力（"谁都算命中"）
    old = HIT
    try:
        HIT = 0.0
        assert _ratio(tone, sr, 0.1, 1200.0) >= HIT, '门为 0 时本该"谁都算命中"（自证夹具没生效）'
    finally:
        HIT = old
    print('尺子自检：440Hz 在 440 带 %.3f（命中）· 在 1200 带 %.3f（空）· 静音 None · 判据自证 OK'
          % (r_in, r_out))
    return True


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('song', nargs='?')
    ap.add_argument('--stems', help='分轨目录（demucs 的 <模型>/<曲名>/）')
    ap.add_argument('--ref', default=None, help='参考混音 wav（召回门用；缺省用分轨自身中位）')
    ap.add_argument('--json', default=None, help='把逐轨读数写成 JSON')
    ap.add_argument('--gate', action='store_true',
                    help='准入闸门：非 `fixable` 时**非零退出**（编排/CI 用；防"上游错还去修"）')
    ap.add_argument('--accept-missing', default='',
                    help='**有意移除**的分轨层（逗号分隔，如 `drums,vocals`）：'
                         '这些层的"缺失"不拉低准入判定（去鼓/去人声这类主动工序用）')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0
    if not a.song or not a.stems:
        print(__doc__)
        return 1
    p = a.song if os.path.isfile(a.song) else os.path.join(SONGS, a.song, 'song.json')
    if not os.path.exists(p):
        print('找不到 %s' % p)
        return 1
    rows, is6, missing, gate = audit(p, a.stems, a.ref,
                                     accept_missing=tuple(
                                         x.strip() for x in a.accept_missing.split(',') if x.strip()))
    if a.json:
        json.dump({'is6': is6, 'rows': rows, 'missing_layers': missing, 'gate': gate},
                  open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('已写 %s' % a.json)
    bad = [r for r in rows if r['trustworthy'] and r['prec'] is not None
           and (r['prec'] < PREC_GATE or (r['recall'] or 0) < REC_GATE)]
    if bad:
        print('\n⚠ 这些轨精度或召回 < %.0f%%（先修识别，再谈别的工序）：%s'
              % (100 * PREC_GATE,
                 '、'.join('%s(精度%.0f%%/召回%.0f%%)' % (r['track'], 100 * r['prec'],
                                                          100 * r['recall']) for r in bad)))
    unmeas = [r for r in rows if r['prec'] is None]
    if unmeas:
        print('⚠ 这些轨**测不到**（要 6 轨分轨）：%s' % '、'.join(r['track'] for r in unmeas))
    # **准入闸门**：非零退出码 = "别做修工序"（供编排脚本/CI 直接拦）
    if a.gate and gate['verdict'] != 'fixable':
        print('\n✗ 准入未过：%s —— **不许进"去鼓/补层"这类修工序**（先解决转录）' % gate['why'])
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
