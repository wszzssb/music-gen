#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""和声张力模型（**判据自证优先**）—— 依据 Nikrang, Sears & Widmer,
《Automatic estimation of harmonic tension by distributed representation of chords》
(arXiv 1707.00972)。

为什么要有它：`mix_target.energy_curve_db` 量的是**响度**起伏，不是**张力**。
那篇的结论是"**张力的变化**中介情绪"（同 Music Perception 42(3)），而我们的生成端
从来没有"张力"这个量 —— 段间只有响度与密度两条曲线。

本工具的做法（可解释近似，不是 word2vec）：
  对每个和弦，按**调内功能距离 + 和弦质量**给一个张力分：
    · 调内自然音级 + 稳定功能（主/下属/属）→ 低张力
    · 调外（借用/变化）音级 → 高张力
    · 质量惩罚：大 < 小 < 减/增（论文实证的 major<minor<dim<aug 方向）
    · 七和弦 > 三和弦（论文实证）
  逐小节的张力 = 该小节所有和弦的均值。

⚠ **必须先过判据自证**（`--selftest`）：拿论文那 3 条**实证排序**当已知答案。
  新尺子没过已知答案，就不许用它下方向性判断（同 RESTORE-METHOD §10 第 4 条）。

用法:
  python scripts\\tension_model.py --selftest        # 只跑判据自证
  python scripts\\tension_model.py --themes          # 逐主题的张力曲线摘要
  python scripts\\tension_model.py --curve <主题>     # 某主题的逐小节张力曲线
"""
from __future__ import annotations

import os
import re
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 音名 → 音级（只用升号，降号在解析时折成等音）
PC = {'C': 0, 'C#': 1, 'D': 2, 'D#': 3, 'E': 4, 'F': 5, 'F#': 6,
      'G': 7, 'G#': 8, 'A': 9, 'A#': 10, 'B': 11}
FLAT = {'Db': 'C#', 'Eb': 'D#', 'Gb': 'F#', 'Ab': 'G#', 'Bb': 'A#'}
MAJOR = (0, 2, 4, 5, 7, 9, 11)
MINOR = (0, 2, 3, 5, 7, 8, 10)          # 自然小调
# 大调里各音级的"功能张力"权重（主/属最稳，二级与导音最不稳）
# ⚠ 底数 **0.15 而不是 0**：主音也不能是绝对零点（见 `chord_tension` 里的配方注释）。
DEG_TENSION_MAJOR = {0: 0.15, 7: 0.2, 5: 0.35, 9: 0.5, 2: 0.6, 4: 0.55, 11: 0.9}
QUAL_TENSION = {'': 0.0, '6': 0.1, 'maj7': 0.15, 'm': 0.25, 'm6': 0.3, 'm7': 0.35,
                '7': 0.45, 'sus4': 0.5, 'sus2': 0.5, 'dim': 0.75, 'm7b5': 0.8,
                'dim7': 0.85, 'aug': 0.95, '+': 0.95, 'mM7': 0.7}
# 调外惩罚：不属于调内音级
CHROMATIC_PENALTY = 1.0
# 七和弦加成（论文：triads < seventh chords）
SEVENTH_BONUS = 0.15

CHORD_RE = re.compile(r'^([A-G][#b]?)(.*)$')


def parse_symbol(sym):
    """'F#m7' → ('F#', 'm7')；解析不了返回 (None, None)。

    ⚠ 识别失败的符号在模板里是最高频的（`?` 1359 次）⇒ 调用方必须显式跳过。
    """
    if not sym or sym == '?':
        return None, None
    m = CHORD_RE.match(str(sym).strip())
    if not m:
        return None, None
    root, qual = m.group(1), m.group(2) or ''
    root = FLAT.get(root, root)
    if root not in PC:
        return None, None
    return root, qual


def chord_tension(sym, tonic_pc, mode='major'):
    """单个和弦的张力（越大越紧张）。返回 None 表示解析不了。"""
    root, qual = parse_symbol(sym)
    if root is None:
        return None
    scale = MAJOR if mode == 'major' else MINOR
    deg = (PC[root] - tonic_pc) % 12
    if mode == 'major':
        base = DEG_TENSION_MAJOR.get(deg, CHROMATIC_PENALTY)
    else:
        # 小调：主/属稳，其余按到主音的五度距离给弱惩罚（同样不自塌到 0）
        base = {0: 0.15, 7: 0.25, 5: 0.4, 3: 0.35, 10: 0.6, 8: 0.5, 2: 0.7}.get(deg, 0.9)
    if deg not in scale:
        base += CHROMATIC_PENALTY
    # ⚠ 配方**不许让主和弦塌到 0**（第一版 `base*0.6 + qual*0.4` 让 C=0.0）：
    #   张力曲线要用来找"峰/谷"，主和弦恒 0 会让每个乐句结尾都变成同一个零点，
    #   **句内对比全被抹平**。改成"基础距离 + 0.5·质量"（Lerdahl 的调性距离里，
    #   主音也是"到锚点距离 0、但质量仍有粗糙度"，不是绝对无张力）。
    t = base + QUAL_TENSION.get(qual, 0.5) * 0.5
    if '7' in qual:
        t += SEVENTH_BONUS
    return round(t, 4)


def curve_from_chords(chords, tonic_pc, mode='major'):
    """和弦序列 → 逐小节**张力水平**曲线（解析不了的跳过）。

    ⚠ **不要拿它当生成端的控制轴**（2026-10-07 实测否证）：逐小节取多模板中位数后，
      15 个主题的极差中位只有 **0.11**、**10/15 主题 <0.15**（`mystery`/`waltz` 只 0.01）。
      原因：逐小节平均把"变化"又抹平了 —— 而论文量的本来就是**变化量**。
      要控制生成端请用 `variation_curve`。
    """
    out = []
    for c in chords:
        v = chord_tension(c, tonic_pc, mode)
        if v is not None:
            out.append(v)
    return out


def variation_curve(chords, tonic_pc, mode='major', window=4):
    """和弦序列 → **张力的变化**曲线（论文口径的近似，**生成端该用这个**）。

    `tension(H_t)` 在原文里是"与**前 n 个和弦**的加权余弦距离"⇒ 量的是**意外程度（变化量）**。
    这里用同样的加权思路（近的重、远的轻），把"余弦距离"换成"张力差"：
        v_t = Σ_k w_k · |tension(H_{t-k}) − tension(H_t)| / Σ_k w_k,  w_k = 1 − (k−1)/window

    实测（223 首模板、15 主题）：变化极差中位 **0.250**（范围 0.125~0.447），
    没有主题塌到 <0.10 ⇒ **有可用信号**，可作为生成端的新控制轴（响度/密度之外的第三条）。
    """
    seq = [(c, chord_tension(c, tonic_pc, mode)) for c in chords]
    seq = [(c, v) for c, v in seq if v is not None]
    out = []
    for i, (_c, v) in enumerate(seq):
        num = den = 0.0
        for k in range(1, min(window, i) + 1):
            w = 1.0 - (k - 1) / float(window)
            num += abs(v - seq[i - k][1]) * w
            den += w
        out.append(num / den if den else 0.0)
    return out


def selftest():
    """**判据自证**：拿论文的 3 条实证排序当已知答案。全部通过才算这把尺子能用。"""
    fails = []
    # ① 质量排序：major < minor < diminished < augmented（C 大调主音上的四种三和弦）
    order = ['C', 'Cm', 'Cdim', 'Caug']
    vals = [chord_tension(s, 0, 'major') for s in order]
    if not all(v is not None for v in vals):
        fails.append('① 质量排序：有和弦解析失败 %s' % vals)
    elif not (vals[0] < vals[1] < vals[2] < vals[3]):
        fails.append('① 质量排序应为 major<minor<dim<aug，实得 %s' % vals)
    # ② 七和弦 > 三和弦（同一根音）
    a, b = chord_tension('C', 0, 'major'), chord_tension('C7', 0, 'major')
    if not (b > a):
        fails.append('② 七和弦应比三和弦紧张：C=%s C7=%s' % (a, b))
    # ③ 终止式：PAC(→主) < HC(→属) < DC(→六级)
    pac = chord_tension('C', 0, 'major')
    hc = chord_tension('G7', 0, 'major')
    dc = chord_tension('Am', 0, 'major')
    if not (pac < hc and pac < dc):
        fails.append('③ 终止式 PAC 应最松：PAC=%s HC=%s DC=%s' % (pac, hc, dc))
    if not (hc < dc):
        fails.append('③ 终止式应 PAC<HC<DC，实得 HC=%s DC=%s' % (hc, dc))
    # ④ 反例守卫：调外和弦必须比调内同质量更紧张（C 大调里 F# 大三 vs F 大三）
    din, dout = chord_tension('F', 0, 'major'), chord_tension('F#', 0, 'major')
    if not (dout > din):
        fails.append('④ 调外应更紧张：F=%s F#=%s' % (din, dout))
    # ⑤ 解析守卫：`?` 与空必须返回 None（不许悄悄当成 0 张力）
    for bad in ('?', '', None, 'H', 'xyz'):
        if chord_tension(bad, 0, 'major') is not None:
            fails.append('⑤ %r 应解析失败返回 None' % (bad,))
    # ⑥ **变化曲线必须真的"随和弦变"**（这是生成端要用的那条）：
    #    同一和弦重复 → 全 0；插入一个调外和弦 → 该处必须跳起来。
    flat = variation_curve(['C', 'C', 'C', 'C', 'C', 'C'], 0, 'major')
    if max(flat) > 1e-9:
        fails.append('⑥ 同和弦重复时变化曲线应为 0，实得 %s' % flat)
    jump = variation_curve(['C', 'C', 'C', 'F#', 'C', 'C'], 0, 'major')
    if not (max(jump) > 0.3 * max(chord_tension('F#', 0, 'major'), 1e-9)):
        fails.append('⑥ 插入调外和弦后变化曲线没有跳起：%s' % jump)
    return fails, dict(order_major_minor_dim_aug=vals, triad=a, seventh=b,
                       cadence=dict(PAC=pac, HC=hc, DC=dc), diatonic_F=din, chromatic_Fs=dout,
                       variation_flat=flat, variation_jump=[round(x, 3) for x in jump])


def theme_curves(theme=None, window=4):
    """主题 → (变化曲线, 元信息)。数据 = `theme_pack.THEMES` 的风格集合里那批模板。"""
    import glob
    import statistics
    import theme_pack as TP
    import midi_ref as mr
    LIB = os.path.join(ROOT, 'refs', 'midi2')
    idx = json.load(open(os.path.join(LIB, '_index.json'), encoding='utf-8'))
    bystyle = {}
    for e in idx:
        bystyle.setdefault(e['style'], []).append(e['file'])
    themes = [theme] if theme else sorted(TP.THEMES)
    out = {}
    for th in themes:
        cfg = TP.THEMES.get(th)
        if not cfg:
            continue
        tp_path = os.path.join(ROOT, 'refs', 'themes', th + '.json')
        h = {}
        if os.path.isfile(tp_path):
            h = (json.load(open(tp_path, encoding='utf-8')).get('harmony') or {})
        ton = parse_symbol(str(h.get('tonic') or 'C') + '')[0] or 'C'
        tonic_pc = PC[ton]
        mode = h.get('mode') or 'major'
        curves = []
        n = 0
        for s in cfg.get('styles', []):
            for f in bystyle.get(s, []):
                fp = os.path.join(LIB, f.replace('/', os.sep))
                if not os.path.isfile(fp):
                    continue
                try:
                    rep = mr.analyze(fp)
                    ch = [b.get('chord') for b in rep.get('bar_detail') or []]
                except Exception:
                    continue
                ch = [c for c in ch if c]
                if len(ch) < 8:
                    continue
                c = variation_curve(ch, tonic_pc, mode, window)
                if c:
                    curves.append(c)
                    n += 1
        if not curves:
            continue
        m = min(len(c) for c in curves)
        med = [statistics.median([c[i] for c in curves]) for i in range(m)]
        out[th] = dict(curve=[round(v, 3) for v in med], n_templates=n,
                       tonic=ton, mode=mode, window=window,
                       spread=round(max(med) - min(med), 3))
    return out


def main():
    import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印中文/✓ 会崩）
    argv = sys.argv[1:]
    if '--selftest' in argv or not argv:
        fails, detail = selftest()
        print('=== 和声张力模型 · 判据自证（对照 arXiv 1707.00972 的实证排序）===')
        for k, v in detail.items():
            print('  %-28s %s' % (k, v))
        if fails:
            print('\n**自证失败 %d 条**：' % len(fails))
            for f in fails:
                print('  - %s' % f)
            return 1
        print('\n全部通过（① 质量排序 ② 七和弦更紧张 ③ PAC<HC<DC ④ 调外更紧张 '
              '⑤ 解析守卫 ⑥ 变化曲线会随和弦变）')
        return 0
    if '--themes' in argv:
        cur = theme_curves()
        print('%-11s %-5s %-8s %-6s %s' % ('主题', '模板', '变化极差', '调', '变化曲线（前 24）'))
        for th in sorted(cur):
            v = cur[th]
            print('%-11s %-5d %-8.3f %-6s %s' % (
                th, v['n_templates'], v['spread'], v['tonic'] + ('m' if v['mode'] == 'minor' else ''),
                ' '.join('%.2f' % x for x in v['curve'][:24])))
        sp = [cur[t]['spread'] for t in cur]
        if sp:
            sp.sort()
            print('\n变化极差：最小 %.3f · 中位 %.3f · 最大 %.3f'
                  % (sp[0], sp[len(sp) // 2], sp[-1]))
        return 0
    if '--curve' in argv:
        i = argv.index('--curve')
        th = argv[i + 1] if len(argv) > i + 1 else None
        if not th:
            print('用法: --curve <主题>')
            return 2
        cur = theme_curves(th)
        if th not in cur:
            print('取不到 %s 的曲线' % th)
            return 3
        v = cur[th]
        print('%s（%s %s，模板 %d 首）变化极差 %.3f'
              % (th, v['tonic'], v['mode'], v['n_templates'], v['spread']))
        print('  ' + ' '.join('%.2f' % x for x in v['curve']))
        return 0
    print('用法: --selftest | --themes | --curve <主题>')
    return 0


if __name__ == '__main__':
    sys.exit(main())
