#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""segment_edit.py —— 每段按需调长度 + 段末轻过渡

把任意 song.json 的**每一段**按目标小节数重排：
  · 目标 > 原段 → 原段音符按原段长度**循环复制**填满
  · 目标 < 原段 → 截断
  · 段末最后 1 小节力度线性降到 `--fade-vel`；段首第 1 拍从 `--fade-in` 渐入

写回 song.json，并默认调 `song_engine.compose` 出 MIDI。

用法:
  & $py scripts\segment_edit.py 204_midi_llm_seg --target S01=16,S02=16,S03=16,S04=16,S05=16,S06=14
  & $py scripts\segment_edit.py songs\x\song.json --target A=16,B=16 --no-compose

⚠ 段内循环必须**复制**音符（按 `src_bars` 步长），只取模会留下空白段
   —— 204 实测：只取模时 16–32 秒全空，复制后 1365→2730 音。
"""
import argparse
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import song_engine
import json_io
import cli_utf8


def parse_target(text):
    out = {}
    for part in text.split(','):
        part = part.strip()
        if not part:
            continue
        name, bars = part.split('=')
        out[name.strip()] = int(bars)
    return out


def main():
    ap = argparse.ArgumentParser(description='每段按需调长度 + 段末轻过渡')
    ap.add_argument('song', help='曲目名（songs/<名>/song.json）或 song.json 路径')
    ap.add_argument('--target', required=True, help='每段目标小节数，如 S01=16,S02=16')
    ap.add_argument('--fade-vel', type=float, default=0.75, help='段末力度下限（默认 0.75）')
    ap.add_argument('--fade-in', type=float, default=0.85, help='段首第 1 拍力度起点（默认 0.85）')
    ap.add_argument('--no-compose', action='store_true', help='只写 song.json，不出 MIDI')
    a = ap.parse_args()

    song_path = a.song
    if not song_path.lower().endswith('.json'):
        song_path = os.path.join(ROOT, 'songs', a.song, 'song.json')
    if not os.path.exists(song_path):
        raise SystemExit('找不到 ' + song_path)

    target = parse_target(a.target)
    s = json_io.load(song_path)
    old = s.get('sections') or []
    if not old:
        raise SystemExit('song.json 里没有 sections')

    info = {}
    acc = 0
    for sec in old:
        info[sec['name']] = {
            'start': acc,
            'bars': sec['bars'],
            'chords': list(sec['chords']),
            'melody': sec.get('melody', 'a'),
            'arr': copy.deepcopy(sec.get('arr', {})),
        }
        acc += sec['bars']

    new_sections = []
    new_notes = {tr: [] for tr in (s.get('notes_extra') or {})}
    dst_start = 0
    for sec in old:
        name = sec['name']
        inf = info[name]
        tgt = target.get(name, inf['bars'])
        if tgt <= 0:
            raise SystemExit('段 %s 的目标小节数必须 > 0' % name)

        chords = (inf['chords'] * 64)[:tgt]
        new_sections.append({
            'name': name,
            'bars': tgt,
            'chords': chords,
            'melody': inf['melody'],
            'arr': copy.deepcopy(inf['arr']),
        })

        for tr, arr in (s.get('notes_extra') or {}).items():
            for n in arr:
                bar, beat, dur, pitch, vel = n
                if inf['start'] <= bar < inf['start'] + inf['bars']:
                    rel = bar - inf['start']
                    k = 0
                    while rel + k < tgt:
                        rel2 = rel + k
                        v = vel
                        if rel2 == tgt - 1:
                            v = int(v * max(a.fade_vel, 1.0 - (1.0 - a.fade_vel) * (beat / 4.0)))
                        elif rel2 == 0 and beat < 1.0:
                            v = int(v * (a.fade_in + (1.0 - a.fade_in) * beat))
                        new_notes[tr].append([dst_start + rel2, beat, dur, pitch, max(1, v)])
                        k += inf['bars']
        dst_start += tgt

    s['sections'] = new_sections
    s['notes_extra'] = new_notes
    s['bars'] = dst_start
    json_io.save(song_path, s)

    bpm = s.get('bpm', 120.0)
    print('sections:', [(x['name'], x['bars']) for x in new_sections])
    print('total bars %d (%.1f s @ %s BPM)' % (dst_start, dst_start * 4 * 60.0 / bpm, bpm))
    print('notes:', {k: len(v) for k, v in new_notes.items()})

    if not a.no_compose:
        song_dir = os.path.dirname(os.path.abspath(song_path))
        name = os.path.basename(song_dir)
        mid = os.path.join(song_dir, name + '.mid')
        song_engine.compose(song_path, mid, quiet=True)
        print('MIDI:', mid)


if __name__ == '__main__':
    import cli_utf8 as _cu
    _cu.setup()
    main()
