#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""midi_chords.py —— **和弦识别**（对标 miditoolbox 的"和弦检测"）

用途：导入一个 .mid（别人的曲子/没有 song.json 的文件）后，想知道"这里是什么和弦"——
面板按小节/选区显示和弦名，也可以**生成一条和弦轨**写回 MIDI（给 DAW 里继续编曲用）。

做法（模板匹配，不猜调）：
  1. 把一段时间窗内的音符按音高折成**音级集合**（pitch class set）
  2. 对每个候选根音、每个模板（大三/小三/属七/挂四/半减…）算匹配分：
     `score = 命中音数 - 0.7 × 多出的音 - 0.3 × 缺失的音`，并列时取**更简单**的模板
     （大三优先于 add9 之类 —— 避免"什么都识别成复杂和弦"）
  3. 缺失音只扣 0.3：真实编配里和弦音常常被省略（尤其五音），不能因为少了五音就判成别的
  4. 相邻同名的段**合并**成一段（输出"每小节一个和弦"而不是"每个音一个和弦"）

为什么不用调性推断：识别单个和弦不需要知道调（`C-E-G` 就是 C）；调性是另一件事，
`theme_pack` 那条路已经在做。**不知道调也能给出正确和弦名**是这里的关键性质。

用法（CLI）:
  python scripts\midi_chords.py <file.mid> [--per-bar] [--json]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()

NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

# (后缀, 相对根音的半音集合, 复杂度) —— 复杂度只用于**并列时的取舍**（小 = 更简单 = 优先）
TEMPLATES = (
    ('', (0, 4, 7), 0),               # 大三
    ('m', (0, 3, 7), 0),              # 小三
    ('7', (0, 4, 7, 10), 1),          # 属七
    ('maj7', (0, 4, 7, 11), 1),
    ('m7', (0, 3, 7, 10), 1),
    ('dim', (0, 3, 6), 1),
    ('aug', (0, 4, 8), 1),
    ('sus2', (0, 2, 7), 1),
    ('sus4', (0, 5, 7), 1),
    ('6', (0, 4, 7, 9), 2),
    ('m6', (0, 3, 7, 9), 2),
    ('m7b5', (0, 3, 6, 10), 2),
    ('dim7', (0, 3, 6, 9), 2),
    ('add9', (0, 2, 4, 7), 2),
    ('madd9', (0, 2, 3, 7), 2),
    ('9', (0, 2, 4, 7, 10), 3),
    ('maj9', (0, 2, 4, 7, 11), 3),
    ('m9', (0, 2, 3, 7, 10), 3),
    ('7sus4', (0, 5, 7, 10), 3),
)


def match(pcs, bass_pc=None):
    """音级集合 → (和弦名, 得分, 详情)。空集合 → ('-', 0, …)

    评分（三个权重都是实测调的，改动前先跑 `--per-bar` 对已知曲子复核）：
      `score = 1.0×命中 − 0.5×多出的音 − 0.5×缺失的音`
      · **缺失音只扣一半**：真实编配常省略五音，不能因为少一个音就判成别的和弦
      · `bass_pc`（低音）给**根音位置加分**：低音是和弦根音 → +0.5，是三音/五音 → +0.25。
        实测价值：38 号第一小节真实是 `Am6`（音级 {9,0,4,6}，低音 A）。不加这项时，
        只取到 {4,6,9}（少了 A）会被判成 `F#m7/A` —— 低音加分把根音拉回 A。
    """
    pcs = {int(p) % 12 for p in pcs}
    if not pcs:
        return '-', 0.0, {'hit': 0, 'extra': 0, 'miss': 0, 'root': None, 'suffix': None}
    bass = None if bass_pc is None else int(bass_pc) % 12
    best = None
    for root in range(12):
        rel = {(p - root) % 12 for p in pcs}
        for suf, tpl, cx in TEMPLATES:
            tset = set(tpl)
            hit = len(rel & tset)
            extra = len(rel - tset)
            miss = len(tset - rel)
            if hit == 0:
                continue
            score = hit - 0.5 * extra - 0.5 * miss
            if bass is not None:
                if bass == root:
                    score += 0.5
                elif (bass - root) % 12 in (3, 4, 7, 8):
                    score += 0.25
            key = (round(score, 6), -cx, -root)      # 得分高 → 模板简单 → 根音低
            if best is None or key > best[0]:
                best = (key, root, suf, hit, extra, miss)
    if best is None:
        return '-', 0.0, {'hit': 0, 'extra': 0, 'miss': 0, 'root': None, 'suffix': None}
    _key, root, suf, hit, extra, miss = best
    name = NAMES[root] + suf
    sc = hit - 0.5 * extra - 0.5 * miss
    if bass is not None and bass != root:
        if (bass - root) % 12 in (3, 4, 7, 8):
            sc += 0.25
        name += '/' + NAMES[bass]
    elif bass is not None:
        sc += 0.5
    return name, sc, {'hit': hit, 'extra': extra, 'miss': miss, 'root': root, 'suffix': suf}


def _slice_notes(model, t0, t1, track_idx=None, min_dur=0.03, pre_max=2.0, pre_w=0.35):
    """时间窗内**参与和声**的音 → [(音高, 权重)]

    口径（这几条都是实测调出来的，别改回去）：
      ① **窗内的音**（起音 ∈ [t0, t1)）：权重按持续时长 `min(1.0, 0.35 + 时值/步长)`。
      ② **先前延续的长音**（起音 ∈ [t0-pre_max, t0)，时值盖过 t0）：权重 = 时长权重 × `pre_w`
         —— 真实编配里低音/内声部常整小节持续，起音落在窗口之前；完全不管就会**漏和弦音**
         （实测 38 号 `Am6` 被漏成 {E,F#,A} → 误判 `A6`）。
      ③ **窗后的音一律不看**：第一版借了下一小节的音（tail），结果相邻格互相污染
         （`B7` 与 `Gmaj7` 糊成 `Gmaj9`、64 格并成 43 段）—— 借用必须**只向前**。
      ④ **鼓组轨排除**（没有和声意义）。
    """
    out = []
    step = max(0.25, float(t1) - float(t0))
    tr = model.get('tracks') or []
    idxs = range(len(tr)) if track_idx is None else [track_idx]
    for ti in idxs:
        t = tr[ti]
        if t.get('hidden') or t.get('mute') or t.get('drum'):
            continue
        for n in (t.get('notes') or []):
            a, d, p = float(n[0]), float(n[1]), int(n[2])
            if d < min_dur:
                continue
            if t0 - 1e-9 <= a < t1 - 1e-9:
                out.append((p, min(1.0, 0.35 + d / step)))
            elif t0 - pre_max <= a < t0 - 1e-9 and a + d > t0 + 1e-9:
                out.append((p, min(1.0, 0.35 + d / step) * pre_w))
    return out


def _weighted_pcs(notes, tol):
    """按权重折成音级集合，丢掉权重占比 < `tol` 的（"偶尔经过"的音不算和弦音）"""
    wsum = {}
    for (p, w) in notes:
        wsum[p % 12] = wsum.get(p % 12, 0.0) + w
    if not wsum:
        return set(), {}
    top = max(wsum.values())
    if top <= 0:
        return set(), wsum
    keep = {pc for pc, w in wsum.items() if w / top >= tol}
    # **低音音级永远保留**：它是转位/根音的唯一依据（实测：短促的低音会被占比阈值丢掉，
    # 于是 `Am6` 少了 A 变成 `A6`、`B7` 少了 A# 变成 `B7/G#` —— 名字全错但音集只差一个音）
    lo_pc = min(notes, key=lambda x: x[0])[0] % 12
    keep.add(lo_pc)
    # 至少 3 个音级（三和弦），最多 6 个（再多就是音簇了）
    if len(keep) < 3:
        keep = {pc for pc, _w in sorted(wsum.items(), key=lambda x: -x[1])[:3]} | {lo_pc}
    if len(keep) > 6:
        keep = {pc for pc, _w in sorted(wsum.items(), key=lambda x: -x[1])[:6]} | {lo_pc}
    return keep, wsum


def detect_at(model, beat, span=None):
    """某一刻（或某一段）的和弦 → (名字, 得分, 详情)"""
    span = span if span else beat_bar(model)
    return detect_range(model, beat, beat + span)


def beat_bar(model):
    """一小节几拍（四分音符）"""
    num, den = (model.get('timesig') or [4, 4])
    return float(num) * 4.0 / float(den)


def detect_range(model, t0, t1, track_idx=None, tol=0.30):
    """区间 [t0, t1) 的和弦"""
    notes = _slice_notes(model, t0, t1, track_idx)
    if not notes:
        return '-', 0.0, {'hit': 0, 'extra': 0, 'miss': 0, 'root': None, 'suffix': None,
                          'notes': 0}
    pcs, wsum = _weighted_pcs(notes, tol)
    # 转位：取窗口内**最低音**（低音线/左手）
    low = min(notes, key=lambda x: x[0])[0]
    name, score, det = match(pcs, bass_pc=low)
    det = dict(det)
    det['notes'] = len(notes)
    det['pcs'] = sorted(pcs)
    det['weights'] = {k: round(v, 2) for k, v in sorted(wsum.items())}
    return name, score, det


def scan(model, step=None, track_idx=None, merge=True):
    """整曲逐个时间格识别 → [(起始拍, 结束拍, 和弦名, 得分, 详情)]

    `step` 缺省 = 一小节；`merge=True` 把相邻同名的段合并（输出"和弦进行"而不是逐格）。
    """
    bar = beat_bar(model)
    step = float(step or bar)
    end = 0.0
    for t in (model.get('tracks') or []):
        for n in (t.get('notes') or []):
            end = max(end, float(n[0]) + float(n[1]))
    out = []
    b = 0.0
    while b < end - 1e-9:
        name, score, det = detect_range(model, b, min(end, b + step), track_idx)
        out.append([round(b, 6), round(min(end, b + step), 6), name, round(score, 4), det])
        b += step
    if merge:
        merged = []
        for seg in out:
            if merged and merged[-1][2] == seg[2]:
                merged[-1][1] = seg[1]
            else:
                merged.append(list(seg))
        out = merged
    return out


def progression(model, **kw):
    """]和弦进行（跳过识别不出的格），便于一眼看懂整首"""
    return [(a, b, nm) for (a, b, nm, _s, _d) in scan(model, **kw) if nm and nm != '-']


def chords_track(model, chords, name='Chords', program=0):
    """把识别出的和弦写成一条**和弦轨**（只加轨，不改任何已有轨）

    `chords` = `scan()` 的输出（或 [[起始拍, 结束拍, 名字], …]）。
    音符按"根音 + 三和弦"展开成**块状长音**（每段一个和弦），音区放在 C3–C4 附近，方便对照。
    """
    if not chords:
        return {'op': 'chords_track', 'notes': 0}
    notes = []
    for seg in chords:
        nm = seg[2] if len(seg) > 2 else '-'
        if not nm or nm == '-':
            continue
        root_name = nm.split('/')[0]
        suf = root_name[1:] if len(root_name) > 1 and root_name[1] in '#b' else root_name[1:]
        pc = NAMES.index(root_name[0] + ('#' if root_name[1:2] == '#' else ''))
        tpl = next((t for s, t, _ in TEMPLATES if s == suf), (0, 4, 7))
        base = 48 + pc                      # C3 起
        if base > 59:
            base -= 12
        dur = max(0.25, float(seg[1]) - float(seg[0]))
        for iv in tpl:
            notes.append([round(float(seg[0]), 6), round(dur, 6), base + iv, 80])
    if not notes:
        return {'op': 'chords_track', 'notes': 0}
    t = {'index': len(model.get('tracks') or []), 'name': name, 'channel': 0,
         'program': program, 'drum': False, 'mute': False, 'solo': False, 'hidden': False,
         'notes': notes, 'ccs': [], 'program_changes': [], 'markers': []}
    used = {x.get('channel') for x in (model.get('tracks') or [])}
    free = [c for c in range(16) if c != 9 and c not in used]
    t['channel'] = free[0] if free else 0
    model.setdefault('tracks', []).append(t)
    for i, x in enumerate(model['tracks']):
        x['index'] = i
    return {'op': 'chords_track', 'notes': len(notes), 'track': t['index'],
            'name': name, 'channel': t['channel']}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        return 1
    import midi_file as mf
    model = mf.import_midi(args[0])
    per_bar = '--per-bar' in sys.argv
    segs = scan(model, step=None if per_bar else beat_bar(model))
    bar = beat_bar(model)
    if '--json' in sys.argv:
        print(json.dumps([{'from': a, 'to': b, 'chord': nm, 'score': s}
                          for (a, b, nm, s, _d) in segs], ensure_ascii=False))
        return 0
    print('%s —— %d 小节 %s拍/小节，识别出 %d 段和弦'
          % (os.path.basename(args[0]), int((segs[-1][1] if segs else 0) / bar + 0.5),
             ('%.0f' % bar), len(segs)))
    for (a, b, nm, s, det) in segs:
        if nm == '-':
            print('  第 %5.1f~%-5.1f 拍  （无和声）' % (a, b))
        else:
            print('  第 %5.1f~%-5.1f 拍  %-8s 得分 %+5.2f（命中 %d / 多 %d / 缺 %d，音级 %s）'
                  % (a, b, nm, s, det.get('hit', 0), det.get('extra', 0),
                     det.get('miss', 0), det.get('pcs')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
