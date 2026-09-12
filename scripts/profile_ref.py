#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剖析参考曲 → 存成紧凑 JSON（只跑一次，之后对比不用再读参考曲）

用法:
  python profile_ref.py <参考曲> [名字] [--bpm N]
  # 例: python profile_ref.py "D:/.../bgm01c.ogg" bgm01c --bpm 128
产出: refs/<名字>.json
"""
import json
import os
import sys

import metrics

HERE = os.path.dirname(os.path.abspath(__file__))
REFS = os.path.join(os.path.dirname(HERE), 'refs')


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        return 1
    path = args[0]
    name = args[1] if len(args) > 1 else os.path.splitext(os.path.basename(path))[0]
    bpm = None
    if '--bpm' in sys.argv:
        bpm = float(sys.argv[sys.argv.index('--bpm') + 1])
    if not os.path.exists(path):
        print('找不到音频文件: %s' % path)
        return 1
    try:
        p = metrics.profile(path, bpm, name)
    except Exception as e:
        print('无法解析 %s：%s\n  （支持 libsndfile 的 26 种容器 + 本机 ffmpeg 兜底的 '
              'm4a/mp4/aac/wma/ape/视频容器等；先跑 `check_audio.py <文件>` 确认能不能读）'
              % (os.path.basename(path), e))
        return 1
    os.makedirs(REFS, exist_ok=True)
    out = os.path.join(REFS, name + '.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(p, f, ensure_ascii=False, indent=1)
    print('已写入 %s' % out)
    print('  %.1fBPM 小节%.3fs  %.1fs  RMS%.1f  宽度%.3f  质心%dHz  角色=%s'
          % (p['bpm'], p['bar'], p['duration'], p['rms_db'], p['width'],
             p['centroid'], p.get('character', 'instrumental')))
    if p.get('character') == 'vocal_forward':
        print('  注意：这是人声主导的参考曲 → 只对齐中频（%s），'
              '跳过 sub/低音/空气感'
              % ', '.join(p['align_bands']))
    print('  低频型 %s' % p['rhythm_low'])
    print('  高频型 %s' % p['rhythm_high'])
    print('  调式   %s' % ' '.join('%s(%.2f)' % (k, v)
                                   for k, v in list(p['quiet_chroma'].items())[:5]))
    return 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    sys.exit(main())
