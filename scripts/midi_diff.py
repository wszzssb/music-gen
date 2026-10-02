#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""midi_diff.py —— **两份 MIDI 的"到底动了哪一层"对照表**（改前 vs 改后）。

## 为什么需要（2026-10-02，BGM35 复刻那一轮）

改完一首曲子，第一件要回答的事是"**我到底动了什么**"。渲染日志与 `song.json` 的 diff
都答不准（前者会被自动调参/收尾步骤搅浑，后者动辄几千行）。真正硬的证据在 `.mid` 上：
**逐轨音符数 + 音域 + program change 的时间线**。

实测用途：给 `bgm35_extract` 做完"段级换主奏音色 + 前半段拆轨"之后，这张表直接证明
**音符总数 8461 一个没变**、只有 `Melody` 的 program 时间线与 `Piano/Melody` 的音数变了
—— 也就是"只动了想动的那一层"。

## 用法

```powershell
& $py scripts\midi_diff.py <改前.mid> <改后.mid> [--json out.json]
```

⚠ 读 MIDI 走的是本仓库的 `midi_file.import_midi`（**不需要 mido**，主 venv 就能跑），
它把 `program_changes` 直接给成**绝对拍**——所以不会踩 `mido` 那个"delta 要自己累加、
只看 program_change 的 delta 会算错时刻"的坑（2026-10-02 真踩过）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()                                    # noqa: E402

import midi_file                                                       # noqa: E402


def beat_progs_to_sec(progs, bpm):
    """`[(拍, program), …]` → `[(秒, program), …]`（抽成纯函数，便于自检）。"""
    spb = 60.0 / float(bpm or 120)
    return [(round(float(b) * spb, 1), int(p)) for (b, p) in progs]


def scan(path):
    """→ {轨名: {'notes': n, 'range': (lo, hi)|None, 'progs': [(秒, program), …]}} + 总秒数"""
    m = midi_file.import_midi(path)
    bpm = float(m.get('bpm') or 120)
    spb = 60.0 / bpm
    out, total = {}, 0
    for t in m['tracks']:
        notes = t.get('notes') or []
        progs = beat_progs_to_sec(t.get('program_changes') or [], bpm)
        if not notes and not progs:
            continue
        ps = [int(n[2]) for n in notes]
        total += len(notes)
        out[t['name']] = {'notes': len(notes),
                          'range': (min(ps), max(ps)) if ps else None,
                          'progs': progs,
                          'program': t.get('program')}
    return out, total, round(float(m.get('end_beat') or 0) * spb, 1)


def diff(a, b):
    """→ [{'track': …, 'notes': (旧, 新), 'progs': (旧, 新)}, …]（只列有差异的轨）"""
    rows = []
    for nm in sorted(set(a) | set(b)):
        x, y = a.get(nm, {}), b.get(nm, {})
        if x.get('notes') != y.get('notes') or x.get('progs') != y.get('progs'):
            rows.append({'track': nm, 'notes': (x.get('notes'), y.get('notes')),
                         'progs': (x.get('progs'), y.get('progs'))})
    return rows


def main():
    ap = argparse.ArgumentParser(description='两份 MIDI 的逐轨对照（音符数 / 音域 / 音色时间线）')
    ap.add_argument('before')
    ap.add_argument('after')
    ap.add_argument('--json')
    a = ap.parse_args()
    A, na, la = scan(a.before)
    B, nb, lb = scan(a.after)
    for tag, D, n, ln in (('改前', A, na, la), ('改后', B, nb, lb)):
        print('=== %s（%s，%.1f 秒，合计 %d 音）==='
              % (tag, os.path.basename(a.before if tag == '改前' else a.after), ln, n))
        for nm in sorted(D):
            v = D[nm]
            pr = ' '.join('%gs→%d' % x for x in v['progs']) or '（无）'
            print('   %-9s 音符 %5d  音域 %-12s program: %s'
                  % (nm, v['notes'], str(v['range']), pr))
        print()
    rows = diff(A, B)
    print('=== 差异（%d 处）===' % len(rows))
    for r in rows:
        print('  %-9s 音符 %s→%s' % (r['track'], r['notes'][0], r['notes'][1]))
        if r['progs'][0] != r['progs'][1]:
            print('            program: %s' % (r['progs'][0],))
            print('                 →  %s' % (r['progs'][1],))
    if na == nb:
        print('\n音符总数未变（%d）—— 只动了音色/轨分配这一层' % na)
    else:
        print('\n⚠ 音符总数变了：%d → %d（差 %+d）' % (na, nb, nb - na))
    if a.json:
        json.dump({'before': A, 'after': B, 'total': [na, nb], 'diff': rows},
                  open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('[i] 写了 %s' % a.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
