#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_sameness.py —— **同质化体检**：量"好多部分都是一样的"出在哪一层。

听感问题拆成 5 个可测维度，每个都给「本库实测 vs 判据」两栏，判据来源写在判据旁：

  A 结构模板化  段落长度的取值数与多样性熵 + 8 小节占比
                （模板惯例 8 小节，但不能**只有** 8 小节 → 熵要够）
  B 编配同质化  ① 乐器在场率（bass 100% 这种"万年在场"）
                ② 曲内同角色段落的编配变体数（A/A2/A3 是不是同一套）
                ③ **段间编配 Jaccard**（乐器组合的两两相似度中位）
  C 段落对比度  ① 段间响度起伏 dB（实际渲染音频，按小节位置切段算 RMS）
                ② 曲内音轨数落差
  D 曲内旋律    不同旋律线两两的语言重合度（≥85% = 同一支）+ 节奏签名复用率
  E 曲间冗余    全库两两语言重合（交给 `probe_variety`，这里只报速览）
  F 主题区分力  同一主题 vs 不同主题的重合度之差（要基线：真实模板库同风格/跨风格）

用法:
  python scripts\probe_sameness.py                # 全量（含音频段间 RMS）
  python scripts\probe_sameness.py --no-audio     # 跳过音频（秒出）
  python scripts\probe_sameness.py --song 39_tender_probe   # 只看一首

判读：任一维度红了**不代表必崩**——它是"和真实/惯例的差距"，改完要复跑看是否收窄。
"""
import glob
import json
import math
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SONGS = os.path.join(ROOT, 'songs')
INSTR = ('uku', 'piano', 'ep', 'strings', 'glock', 'bass', 'pad', 'arp',
         'perc', 'harmony', 'shimmer', 'glock_all')
# 判据（来源写在注释里，改判据要一起改注释）
SEC_ENT_MIN = 1.6        # 段落长度取值熵下限：真实曲式有 intro2/4/8 + 8/16 主副歌 → 实测真库 ≥1.9
EIGHT_MAX = 0.70         # 8 小节段落占比上限：模板惯例是 8，但真实曲式里 16/4/2 也不少
INSTR_ALWAYS = 0.95      # 任一乐器在场率超过此值 = "万年在场"（编配退化成固定音色表）
ARR_VAR_MIN = 2          # 同角色段落至少要有 2 种编配变体才算"曲内有编配变化"
DYN_MIN_DB = 2.0         # 段间响度起伏下限（参考混音目标实测 2~3 dB，按位置切段复算）
TRACK_SPAN_MIN = 2       # 曲内轨数落差下限
INTRA_TWIN = 0.85        # 曲内两条旋律线 ≥ 此值 = 其实是同一支
SIG_DUP_MAX = 0.60       # 节奏签名复用率上限（真实模板中位 23%）
ARR_JACCARD_MAX = 0.78   # 段间编配 Jaccard 上限（旧行为实测 0.86、5 首为 1.00；见自检同名常量）


def load_songs(only=None):
    out = []
    for d in sorted(glob.glob(os.path.join(SONGS, '*'))):
        p = os.path.join(d, 'song.json')
        if not os.path.isfile(p):
            continue
        name = os.path.basename(d)
        if only and only not in name:
            continue
        try:
            out.append((name, d, json.load(open(p, encoding='utf-8'))))
        except Exception:
            continue
    return out


def arr_sig(a):
    return tuple(sorted(k for k in a if k != 'mix' and a[k] and k in INSTR))


def role_of(name):
    return name.rstrip('0123456789') or name


def sec_rms(d, j, ogg):
    """段落级 RMS（dBFS）：按 bpm + 拍号把小节位置换算成秒"""
    import numpy as np
    import soundfile as sf
    x, sr = sf.read(ogg, always_2d=True)
    m = x.mean(axis=1)
    spb = 60.0 / j['bpm']
    bb = 4.0 if (j.get('meter') or [4, 4]) == [4, 4] else 3.0
    t, res = 0.0, []
    for s in j['sections']:
        dur = s['bars'] * bb * spb
        i, k = int(t * sr), min(len(m), int((t + dur) * sr))
        t += dur
        if k - i < sr // 4:
            continue
        seg = m[i:k]
        r = float(np.sqrt((seg ** 2).mean()))
        res.append((s['name'], 20 * math.log10(max(r, 1e-9))))
    return res


def _jac_med(sigs):
    """一组乐器组合 → 两两 Jaccard 的中位数（1.00 = 每段同一套乐器）"""
    v = [len(a & b) / len(a | b) for i, a in enumerate(sigs) for b in sigs[i + 1:]
         if (a | b)]
    return st.median(v) if v else 1.0


def measure(songs, with_audio=True):
    """→ 明细行列表 + 汇总统计（供 CLI 与自检共用）"""
    rows = []
    instr = {}
    sec_lens = []
    bpm = {}
    for name, d, j in songs:
        secs = j['sections']
        ss = [arr_sig(s.get('arr') or {}) for s in secs]
        cnt = [len(x) for x in ss]
        byrole = {}
        for s, sec in zip(ss, secs):
            byrole.setdefault(role_of(sec['name']), set()).add(s)
        for s in ss:
            for k in s:
                instr[k] = instr.get(k, 0) + 1
        sec_lens += [s['bars'] for s in secs]
        bpm[j['bpm']] = bpm.get(j['bpm'], 0) + 1
        r = {'name': name, 'dir': d, 'secs': len(secs),
             'arr_kinds': len(set(ss)),
             'same_role_max': max(len(v) for v in byrole.values()) if byrole else 0,
             'track_span': (max(cnt) - min(cnt)) if cnt else 0,
             'jac': _jac_med(ss),
             'dyn_db': None, 'audio': '-'}
        if with_audio:
            ogg = glob.glob(os.path.join(d, '*_sf.ogg'))
            if ogg:
                try:
                    res = sec_rms(d, j, ogg[0])
                    if len(res) >= 2:
                        v = [x[1] for x in res]
                        r['dyn_db'] = max(v) - min(v)
                        r['audio'] = '%s %+.1f ~ %s %+.1f' % (
                            min(res, key=lambda z: z[1])[0], min(v),
                            max(res, key=lambda z: z[1])[0], max(v))
                except Exception as e:
                    r['audio'] = 'ERR %s' % e
        rows.append(r)
    agg = {
        'n_songs': len(rows),
        'n_secs': len(sec_lens),
        'sec_ent': _entropy(sec_lens),
        'sec_values': len(set(sec_lens)),
        'eight_share': (sum(1 for x in sec_lens if x == 8) / len(sec_lens)) if sec_lens else 0.0,
        'instr_share': {k: v / max(1, len(sec_lens)) for k, v in instr.items()},
        'arr_kinds_med': st.median([r['arr_kinds'] for r in rows]) if rows else 0,
        'jac_med': st.median([r['jac'] for r in rows]) if rows else 1.0,
        'same_role_med': st.median([r['same_role_max'] for r in rows]) if rows else 0,
        'track_span_med': st.median([r['track_span'] for r in rows]) if rows else 0,
        'dyn_med': st.median([r['dyn_db'] for r in rows if r['dyn_db'] is not None])
        if any(r['dyn_db'] is not None for r in rows) else None,
        'bpm_values': len(bpm),
        'bpm_top': max(bpm.values()) / max(1, len(rows)) if bpm else 0.0,
    }
    return rows, agg


def _entropy(vals):
    if not vals:
        return 0.0
    c = {}
    for v in vals:
        c[v] = c.get(v, 0) + 1
    n = float(len(vals))
    return -sum((k / n) * math.log(k / n, 2) for k in c.values())


def verdicts(agg, intra=None, variety=None):
    """→ [(维度, 实测, 判据, 是否达标)]"""
    v = []
    v.append(('A 段落长度熵', '%.2f bits（%d 种取值）' % (agg['sec_ent'], agg['sec_values']),
              '≥%.1f' % SEC_ENT_MIN, agg['sec_ent'] >= SEC_ENT_MIN))
    v.append(('A 8小节段占比', '%.0f%%' % (agg['eight_share'] * 100),
              '≤%.0f%%' % (EIGHT_MAX * 100), agg['eight_share'] <= EIGHT_MAX))
    hi = [(k, s) for k, s in agg['instr_share'].items() if s > INSTR_ALWAYS]
    v.append(('B 万年在场乐器', '%d 件（%s）' % (len(hi), '、'.join(k for k, _ in hi[:4]) or '无'),
              '0 件（>%.0f%%=万年）' % (INSTR_ALWAYS * 100), not hi))
    v.append(('B 曲内编配变体', '中位 %.0f 种/同角色' % agg['same_role_med'],
              '≥%d' % ARR_VAR_MIN, agg['same_role_med'] >= ARR_VAR_MIN))
    v.append(('B 段间编配相似度', '中位 %.2f（1.00=每段同套乐器）' % agg['jac_med'],
              '≤%.2f' % ARR_JACCARD_MAX, agg['jac_med'] <= ARR_JACCARD_MAX))
    v.append(('C 段间响度起伏', '%.1f dB' % agg['dyn_med'] if agg['dyn_med'] is not None else '未测',
              '≥%.1f dB' % DYN_MIN_DB,
              agg['dyn_med'] is not None and agg['dyn_med'] >= DYN_MIN_DB))
    v.append(('C 曲内轨数落差', '中位 %.0f' % agg['track_span_med'],
              '≥%d' % TRACK_SPAN_MIN, agg['track_span_med'] >= TRACK_SPAN_MIN))
    v.append(('E BPM 集中度', '最高档占 %.0f%%（%d 种取值）' % (agg['bpm_top'] * 100, agg['bpm_values']),
              '≤25%', agg['bpm_top'] <= 0.25))
    if intra:
        mx = max(intra)
        v.append(('D 曲内旋律孪生', '最高 %.0f%%' % (mx * 100), '≤%.0f%%' % (INTRA_TWIN * 100),
                  mx <= INTRA_TWIN))
    if variety:
        v.append(('F 主题区分力', '%+.1f%%（基线 %+.1f%%）' % (variety[0] * 100, variety[1] * 100),
                  '≥ 基线', variety[0] >= variety[1]))
    return v


def main():
    only = sys.argv[sys.argv.index('--song') + 1] if '--song' in sys.argv else None
    with_audio = '--no-audio' not in sys.argv
    songs = load_songs(only)
    rows, agg = measure(songs, with_audio)
    print('参检 %d 首曲目 / %d 个段落\n' % (agg['n_songs'], agg['n_secs']))
    print('%-22s %4s %5s %6s %5s %6s %8s'
          % ('曲', '段数', '编配种', '同角色变体', '轨落差', '编配相似', '段间起伏'))
    for r in rows:
        print('%-22s %4d %5d %8d %7d %8.2f %8s'
              % (r['name'], r['secs'], r['arr_kinds'], r['same_role_max'], r['track_span'],
                 r['jac'],
                 ('%.1f dB' % r['dyn_db']) if r['dyn_db'] is not None else '  --'))
    print('\n乐器在场率:')
    for k, s in sorted(agg['instr_share'].items(), key=lambda x: -x[1]):
        flag = '  ← 万年在场' if s > INSTR_ALWAYS else ''
        print('  %-10s %5.0f%%%s' % (k, s * 100, flag))
    print('\n【判据对照】')
    v = verdicts(agg, intra=None, variety=None)
    for dim, got, lim, ok in v:
        print('  %s %-16s 实测 %-28s 判据 %s' % ('OK  ' if ok else '!!  ', dim, got, lim))
    print()
    print('提示：D（曲内旋律）与 F（主题区分力）分别由 `probe_variety` 给出——'
          '先跑 `probe_variety.py`，把它的两个数填进本工具的判据表。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
