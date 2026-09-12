#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""preview_job.py —— ⚡ 试听本段：只渲染**当前段落切片**（默认 8 小节 ≈ 12.8s）供循环试听。

为什么需要（速度）：整曲一轮 30~120 秒，而改一段编排/音色时的反馈只要**这一段**。
切片渲染实测 **0.4~1.5 秒**（同一套 FluidSynth + 音源 + render.json 的母带参数），
面板里"改一版 → 立刻循环听"就是靠它。

用法: python tools/preview_job.py <曲目id> --sec N [--bars 8]
输出: 一行 JSON（含 ogg 路径 + 耗时），面板拿它当播放源。
"""
import argparse
import copy
import json
import os
import sys
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MG = (os.environ.get('BGM_STUDIO_ROOT')
      or os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(
          os.path.abspath(__file__))), '..')))
sys.path.insert(0, os.path.join(MG, 'scripts'))

import json_io              # noqa: E402
import render_midi as rm    # noqa: E402
import song_engine          # noqa: E402
import tempfile             # noqa: E402


def build_mini(song, sec_idx, max_bars):
    mini = copy.deepcopy(song)
    sec = copy.deepcopy(song['sections'][sec_idx])
    sec['bars'] = min(sec['bars'], max_bars)
    ch = (sec['chords'] or [])[:sec['bars']]
    sec['chords'] = ch + [ch[-1]] * (sec['bars'] - len(ch)) if ch else ch
    key = sec.get('melody')
    mini['melody'] = {key: (song['melody'].get(key) or [])} if key else {}
    mini['sections'] = [sec]
    return mini


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sid')
    ap.add_argument('--sec', type=int, default=0)
    ap.add_argument('--bars', type=int, default=8)
    a = ap.parse_args()
    folder = os.path.join(MG, 'songs', a.sid)
    song = json.load(open(os.path.join(folder, 'song.json'), encoding='utf-8'))
    cfg_path = os.path.join(folder, 'render.json')
    cfg = json.load(open(cfg_path, encoding='utf-8')) if os.path.isfile(cfg_path) else {}
    idx = max(0, min(len(song['sections']) - 1, a.sec))
    mini = build_mini(song, idx, a.bars)
    out_dir = os.path.join(tempfile.gettempdir(), 'bgm-studio-audio', 'preview', a.sid)
    os.makedirs(out_dir, exist_ok=True)
    tag = 'sec%d_%dbars' % (idx, mini['sections'][0]['bars'])
    sj = os.path.join(out_dir, tag + '.json')
    json_io.save(sj, mini)
    mid = os.path.join(out_dir, tag + '.mid')
    base = os.path.join(out_dir, tag)
    t0 = time.time()
    song_engine.compose(sj, mid)
    rm.render(mid, base, rms_db=cfg.get('rms', -16.9), width=cfg.get('width', 1.0),
              shelf_db=cfg.get('shelf', 3.0), hp_hz=cfg.get('hp', 28.0),
              low_db=cfg.get('low', 0.0), drive=cfg.get('drive', 1.5),
              mid_db=cfg.get('mid_db', 0.0), verbose=False, ogg=True)
    dur = mini['sections'][0]['bars'] * 4 * 60.0 / song['bpm']
    print(json.dumps({'ok': True, 'sid': a.sid, 'sec': idx, 'bars': mini['sections'][0]['bars'],
                      'seconds_audio': round(dur, 2), 'render_sec': round(time.time() - t0, 2),
                      'file': base + '.ogg'}, ensure_ascii=False))


if __name__ == '__main__':
    main()