#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""song_events.py —— 把 song.json 展开成**每轨音符事件**（JSON），给钢琴卷帘 / 排查用。

为什么需要：`song_engine.build_events()` 本来就在内存里产出这张表（拍 / 时值 / 音高 / 力度），
但**没有出口** —— 想看"这一轨到底会弹哪些音"只能去解析渲染出来的 MIDI（`midi_probe.py` 只给汇总）。
可视化编辑器（dsh-bgm-studio 面板）的钢琴卷帘就是消费这个出口。

用法:
  python scripts\\song_events.py songs\\21_g150_velvet\\song.json [--track Melody] [--json]
产出: {name, bpm, bars, beat_per_bar, tracks: {轨: {prog, chan, pan, vol, n, notes: [[拍,时值,音高,力度], …]}}}
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import song_engine      # noqa: E402
import cli_utf8 as _cu  # noqa: E402


def dump(song_json, track=None):
    """→ 每轨事件表（JSON 友好：全是 list/数字）"""
    d = song_engine.load(song_json)
    ev, nbars = song_engine.build_events(d)[:2]
    out = {'name': d.get('name') or os.path.basename(os.path.dirname(song_json)),
           'bpm': d['bpm'], 'bars': nbars,
           'beat_per_bar': 4,
           'style': d.get('style', ''), 'seconds': round(nbars * 4 * 60.0 / d['bpm'], 2),
           'tracks': {}}
    for name, notes in ev.items():
        if not notes:                    # 空轨不出现在事件出口里（面板另有全轨列表）
            continue
        if track and name != track:
            continue
        prog, chan = d['programs'][name]
        pan, vol = d['mix'][name]
        sec_mix = []
        bar0 = 0
        for sec in d['sections']:
            v = ((sec.get('arr') or {}).get('mix') or {}).get(name)
            if v is not None:
                sec_mix.append({'bar': bar0, 'cc7': int(v)})
            bar0 += sec['bars']
        out['tracks'][name] = {
            'prog': prog, 'chan': chan, 'pan': pan, 'vol': vol,
            'n': len(notes), 'sec_mix': sec_mix,
            'notes': [[round(float(t), 3), round(float(dd), 3), int(m), int(v)]
                      for (t, dd, m, v) in notes],
        }
    out['sections'] = [{'name': s['name'], 'bars': s['bars'],
                        'chords': list(s['chords']),
                        'arr': {k: v for k, v in (s.get('arr') or {}).items()}}
                       for s in d['sections']]
    out['programs'] = {k: list(v) for k, v in d['programs'].items()}
    out['mix'] = {k: list(v) for k, v in d['mix'].items()}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('song_json')
    ap.add_argument('--track', default=None, help='只要这一轨（如 Melody）')
    ap.add_argument('--json', action='store_true', help='带缩进（给人看）')
    a = ap.parse_args()
    res = dump(a.song_json, a.track)
    print(json.dumps(res, ensure_ascii=False, indent=1 if a.json else None))


_cu.setup()          # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    main()
