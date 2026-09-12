#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""编配诊断：逐段统计音高分布/音符密度/亮度，找出哪一段缺高频或太稀

（旧版 `import summer_seaside` —— 那个脚本搬迁到 songs/ 后本工具就静默失效了，
  现改为读 song.json，对所有曲目通用。）

用法:
  python arrange_probe.py songs/06_d150_calm/song.json
  python arrange_probe.py 06_d150_calm            # 也可以只给曲目名
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import song_engine          # noqa: E402


def resolve(arg):
    if os.path.sep in arg or '/' in arg:
        return arg
    p = os.path.join(ROOT, 'songs', arg, 'song.json')
    return p if os.path.exists(p) else arg


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = resolve(sys.argv[1])
    if not os.path.exists(path):
        print('找不到 %s' % path)
        return 1
    d = song_engine.load(path)
    ev, nbars = song_engine.build_events(d)
    print('%s  %d 小节 @%.1fBPM' % (os.path.basename(path), nbars, d['bpm']))
    print('%-8s %6s %6s %6s %8s %8s %8s  %s' %
          ('段落', '音符', '≥C5', '≥C6', '平均音高', '亮度指数', '每小节', '编配'))
    bar0 = 0
    for sec in d['sections']:
        n = sec['bars']
        t0, t1 = bar0 * 4.0, (bar0 + n) * 4.0
        sel = [(tr, m, v) for tr, notes in ev.items() for (t, _, m, v) in notes
               if t0 <= t < t1]
        arr = sec.get('arr', {})
        flags = ','.join(k for k in ('uku', 'piano', 'ep', 'pad', 'strings',
                                     'glock', 'arp', 'bass', 'perc')
                         if arr.get(k))
        if not sel:
            print('%-8s %6d  —— 该段无音符' % (sec['name'], 0))
            bar0 += n
            continue
        mean = sum(m for (_, m, _) in sel) / len(sel)
        bright = sum((m - 48) * (v / 100.0) for (_, m, v) in sel) / len(sel)
        print('%-8s %6d %6d %6d %8.1f %8.1f %8.1f  %s'
              % (sec['name'], len(sel),
                 sum(1 for (_, m, _) in sel if m >= 72),
                 sum(1 for (_, m, _) in sel if m >= 84),
                 mean, bright, len(sel) / n, flags))
        bar0 += n
    return 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    sys.exit(main())
