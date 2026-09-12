#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""midi_ref.py —— 把 MIDI 变成**音符层参考报告**（和声 / 低音 / 旋律 / 节奏 / 曲式）。

为什么需要它：参考曲画像（`refs/*.json`）是从**音频**扒的 —— 频谱/宽度/响度**只能**那样拿；
但**和声进行、声部走向、节奏型、曲式**属于音符层，从音频扒必然有误差（和弦识别错、
速度测出层级歧义，库里都踩过）。MIDI 在这几项上是**精确的**，这个工具专吃 MIDI。

它**不碰音源、不做渲染**，所以不受 GM 音色限制；反过来它也**给不了频谱参考** ——
MIDI 渲染出来的音频和真实录音是两回事，别拿它当 `refs/` 画像用。两者是互补的：
**音符层看 MIDI，频谱层看音频画像。**

和弦识别口径：把该小节的音级集合拿去和 `selftest.parse_chord`（全链唯一的和弦解析器）
枚举出的模板做**反向匹配**，模板必须**全部落在实际音级里**，取覆盖最多、最简单的那个。
所以这里认出来的 `Cmaj7` / `Em7b5` / `Bbm6` 与 `song.json` 里写的符号是同一套语言。

用法:
  python scripts\\midi_ref.py <file.mid> [...]          # 一个或多个文件
  python scripts\\midi_ref.py <dir> --recursive         # 递归扫目录（只收 .mid/.midi）
  python scripts\\midi_ref.py <file.mid> --json         # 机器可读（给她/写歌当参考）
  python scripts\\midi_ref.py <file.mid> --bars 5-20    # 只看第 5~20 小节

注意：MIDI 素材请自备。公开来源里 **Mutopia / IMSLP（公共领域）** 可放心用；
**VGMusic / Kaggle 的游戏动漫 MIDI** 风格更贴近日系 BGM，但多为粉丝转录、**版权灰色**，
只当内部学习素材，**不进仓库、不外发**。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()          # noqa: E402

import midi_probe as mp                       # noqa: E402  唯一的 SMF 解析口径
import selftest as st                         # noqa: E402  唯一的和弦符号口径

NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
ROOTS = list(NAMES)
SUFFIX = ['', 'm', '7', 'maj7', 'm7', 'm6', 'm7b5', 'dim', 'sus4', '6']


def _note(n):
    return '%s%d' % (NAMES[n % 12], n // 12 - 1)


def _candidates():
    """(符号, 音级集合) 候选表 —— 用全链的 parse_chord 生成，保证口径一致"""
    out = []
    for r in ROOTS:
        for s in SUFFIX:
            sym = r + s
            try:
                root, _, want = st.parse_chord(sym)
            except Exception:
                continue
            if want is None or root is None:
                continue
            out.append((sym, int(root) % 12, frozenset(int(x) % 12 for x in want)))
    return out


CANDS = _candidates()


def detect_chord(pcs_all, strong=None, bass_pc=None):
    """音级集合 → 和弦符号。

    **必须锚定低音**：只取"覆盖最多音级"的模板会让大和弦（C6/C7）赢过真实和弦
    —— 实测巴洛克每小节有 7~10 个音级（含经过音），C6 覆盖 4 个就压过了真实的 F 三和弦。
    所以：① 优先取**根音 == 低音**的模板；② 用**强拍音**（拍 0）加权，弱位经过音不投票；
    ③ 找不到再放宽到"低音是模板内音"（转位）。
    """
    if not pcs_all:
        return '-'
    strong = strong or pcs_all
    for rooted in (True, False):
        best = None
        for sym, root, want in CANDS:
            if rooted and bass_pc is not None and root != bass_pc:
                continue
            if not want <= pcs_all:
                continue
            if not rooted and bass_pc is not None and bass_pc not in want:
                continue
            cover = len(want & strong)                 # 强拍覆盖
            if strong and not (want & strong):
                continue                               # 强拍一个和弦音都没有 → 不可能
            # 打分：强拍覆盖最重；**简单优先**（短符号的三和弦该胜过大六和弦 F6）；
            # 未被模板解释的音（多出来的经过音）轻罚
            score = cover * 10 - len(pcs_all - want) * 3 - len(sym) * 2
            if best is None or score > best[0]:
                best = (score, sym)
        if best:
            return best[1]
    return '?'


def _compress(names):
    """连续相同项合并成 `A | B | A` 形式"""
    out = []
    for n in names:
        if not out or out[-1] != n:
            out.append(n)
    return out


def analyze(path, lo=None, hi=None):
    res = mp.parse(path, quiet=True)
    div = res['division'] or 480
    bpm = res['bpm'] or 120.0
    num, den = res['timesig'] or (4, 4)
    # 一小节 tick 数**按拍号算**（以前写死 div*4）：4/4 → div*4；3/4 → div*3；6/8 → div*3。
    # 拍号取自 MIDI 元事件 —— 注意 `midi_probe` 过去把它读成 None（字段写在轨道循环里被抹掉），
    # 所以这里以前拿到的其实一直是默认值（见坑 107）。
    bar_ticks = int(round(div * num * 4.0 / den))
    slots = int(round(num * 16.0 / den))      # 一小节的十六分格数：4/4 → 16；3/4、6/8 → 12
    notes = []
    for t in res['tracks']:
        for (s, d, n, v) in t['notes']:
            notes.append((s, d, n, v, t['index']))
    if not notes:
        return None
    end = max(s + d for s, d, _, _, _ in notes)
    nbars = int((end + bar_ticks - 1) // bar_ticks)
    lo = lo or 1
    hi = hi or nbars

    bars = []
    for b in range(1, nbars + 1):
        t0, t1 = (b - 1) * bar_ticks, b * bar_ticks
        inbar = [n for n in notes if n[0] < t1 and n[0] + n[1] > t0]
        pcs = set(n[2] % 12 for n in inbar)
        # 强拍音：在拍 0 的八分内起始、或从前面延续过来的 —— 代表和声；弱位起始的多是经过音
        strong = set(n[2] % 12 for n in inbar if n[0] < t0 + div * 0.5)
        bass = min((n[2] for n in inbar), default=None)
        top = max((n[2] for n in inbar), default=None)
        bars.append({'bar': b,
                     'chord': detect_chord(pcs, strong,
                                           bass % 12 if bass is not None else None),
                     'bass': bass, 'top': top, 'pcs': sorted(pcs)})

    # 16 分节奏型（全曲合并；按音高拆低频/高频两组）
    grid_lo, grid_hi = [0] * slots, [0] * slots
    for s, d, n, v, _ in notes:
        slot = int(round((s % bar_ticks) / (bar_ticks / float(slots)))) % slots
        if n < 60:
            grid_lo[slot] += 1
        else:
            grid_hi[slot] += 1
    pat_lo = ''.join('★' if c else '·' for c in grid_lo)
    pat_hi = ''.join('★' if c else '·' for c in grid_hi)

    # 曲式：每 4 小节一个单元，用（和弦序列 + 节奏型）做指纹，相同的归一段
    units, labels = [], {}
    for u0 in range(0, nbars, 4):
        chunk = bars[u0:u0 + 4]
        if not chunk:
            continue
        fp = tuple(b['chord'] for b in chunk)
        key = fp
        if key not in labels:
            labels[key] = chr(ord('A') + len(labels) % 26)
        units.append((u0 + 1, min(u0 + 4, nbars), labels[key]))

    tracks = []
    for t in res['tracks']:
        if not t['notes']:
            continue
        ps = [n[2] for n in t['notes']]
        tracks.append({'index': t['index'], 'name': t['name'] or '(未命名)',
                       'channel': t['channel'] + 1, 'program': t['program'],
                       'notes': len(t['notes']), 'lo': min(ps), 'hi': max(ps)})

    return {'file': os.path.basename(path), 'bpm': bpm,
            'timesig': res['timesig'] or (4, 4), 'bars': nbars,
            'seconds': end * 60.0 / (bpm * div),
            'tracks': tracks, 'bar_detail': bars,
            'progression': _compress([b['chord'] for b in bars]),
            'bassline': _compress([_note(b['bass']) for b in bars if b['bass'] is not None]),
            'topline': _compress([_note(b['top']) for b in bars if b['top'] is not None]),
            'rhythm_low': pat_lo, 'rhythm_high': pat_hi,
            'form': [{'bars': '%d-%d' % (a, b), 'label': l} for a, b, l in units],
            '_lo': lo, '_hi': hi}


def show(r):
    if r is None:
        print('  （没有音符，跳过）')
        return
    ts = '%d/%d' % r['timesig'] if isinstance(r['timesig'], tuple) else str(r['timesig'])
    print('=' * 74)
    print('%s   %.1f BPM  %s  %d 小节  约 %.1fs' %
          (r['file'], r['bpm'], ts, r['bars'], r['seconds']))
    print('  轨: ' + ' | '.join('%s(音域 %s-%s, %d 音符)' %
                               (t['name'], _note(t['lo']), _note(t['hi']), t['notes'])
                               for t in r['tracks'][:8]))
    lo, hi = r['_lo'], r['_hi']
    print('  ── 每小节和弦 ──')
    for b in r['bar_detail']:
        if not (lo <= b['bar'] <= hi):
            continue
        print('    bar %-3d %-8s 低音 %-5s 音级 [%s]' %
              (b['bar'], b['chord'],
               _note(b['bass']) if b['bass'] is not None else '-',
               ' '.join(NAMES[p] for p in b['pcs'])))
    print('  ── 和声进行（连续相同已合并）──')
    print('    ' + ' '.join(r['progression']))
    segs = [' '.join(r['progression'][i:i + 8])
            for i in range(0, len(r['progression']), 8)]
    print('    （每 8 个一段看：%s）' % ' | '.join(segs))
    print('  ── 低音线 ──')
    print('    ' + ' '.join(r['bassline']))
    print('  ── 旋律线（每小节最高音）──')
    print('    ' + ' '.join(r['topline']))
    print('  ── 16 分节奏型（全曲合并）──')
    print('    低频(<C4) %s' % r['rhythm_low'])
    print('    高频(≥C4) %s' % r['rhythm_high'])
    print('  ── 曲式（每 4 小节一单元，指纹相同即同段）──')
    print('    ' + '  '.join('%s[%s]' % (u['label'], u['bars']) for u in r['form']))


def collect(paths, recursive=False):
    out = []
    for p in paths:
        if os.path.isdir(p):
            if recursive:
                for dp, _, fns in os.walk(p):
                    for f in fns:
                        if f.lower().endswith(('.mid', '.midi')):
                            out.append(os.path.join(dp, f))
            else:
                for f in os.listdir(p):
                    if f.lower().endswith(('.mid', '.midi')):
                        out.append(os.path.join(p, f))
        elif os.path.exists(p):
            out.append(p)
        else:
            print('  找不到: %s' % p)
    return sorted(out)


def main():
    argv = sys.argv[1:]
    as_json = '--json' in argv
    argv = [a for a in argv if a != '--json']
    recursive = '--recursive' in argv
    argv = [a for a in argv if a != '--recursive']
    lo = hi = None
    if '--bars' in argv:
        i = argv.index('--bars')
        spec = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
        a, _, b = spec.partition('-')
        lo, hi = int(a), int(b or a)
    if not argv:
        print(__doc__)
        return 2
    files = collect(argv, recursive)
    if not files:
        print('  没有可分析的 MIDI')
        return 1
    out = []
    for f in files:
        try:
            r = analyze(f, lo, hi)
        except Exception as e:                      # noqa: BLE001
            print('  %s 解析失败: %s: %s' % (os.path.basename(f), type(e).__name__, e))
            continue
        if r is None:
            print('  %s 没有音符，跳过' % os.path.basename(f))
            continue
        r.pop('_lo', None); r.pop('_hi', None)
        out.append(r)
        if not as_json:
            show(analyze(f, lo, hi))
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
