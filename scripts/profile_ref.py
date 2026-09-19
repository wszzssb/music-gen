#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剖析参考曲 → 存成紧凑 JSON（只跑一次，之后对比不用再读参考曲）

用法:
  python profile_ref.py <参考曲> [名字] [--bpm N] [--meter 3/4]
  # 例: python profile_ref.py "D:/.../bgm01c.ogg" bgm01c --bpm 128
产出: refs/<名字>.json
说明: **分析侧只支持 4/4**（小节网格）；--meter 给了别的拍号会直接拒绝，而不是算错。
"""
import json
import os
import sys

import metrics

HERE = os.path.dirname(os.path.abspath(__file__))
REFS = os.path.join(os.path.dirname(HERE), 'refs')


def main():
    # 面板守卫（硬形式）：没在跑就先拉起来 —— 见 scripts/studio_guard.py 顶部那段。
    try:
        import studio_guard
        studio_guard.ensure_panel()
    except Exception as _e:                                        # noqa: BLE001
        print('  （面板守卫跳过：%s）' % str(_e)[:80])
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        return 1
    path = args[0]
    name = args[1] if len(args) > 1 else os.path.splitext(os.path.basename(path))[0]
    bpm = None
    if '--bpm' in sys.argv:
        bpm = float(sys.argv[sys.argv.index('--bpm') + 1])
    meter = [4, 4]
    if '--meter' in sys.argv:
        raw = sys.argv[sys.argv.index('--meter') + 1]
        try:
            a, b = [int(v) for v in str(raw).replace('/', ' ').split()]
        except Exception:
            print('--meter 要写成 3/4 或 6/8；收到 %r' % (raw,))
            return 1
        meter = [a, b]
        if meter != [4, 4]:
            print('参考曲画像**只按 4/4 切小节**（结构曲线 / 安静段 chroma / 节奏型格数都建在'
                  '小节网格上）。作曲侧已支持非 4/4，但分析侧还没适配 —— 与其给你一份算错的'
                  '画像，不如在这里拒绝。')
            return 1
    if not os.path.exists(path):
        print('找不到音频文件: %s' % path)
        return 1
    try:
        p = metrics.profile(path, bpm, name, meter)
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
    if str(p.get('bpm_source', '')).startswith('auto'):
        print('  ⚠ 这个速度是**自动测速**出来的，没人核对过。实测：外部参考曲上与人工钉死值的'
              '一致率 4%（23 首里精确 1、八度内 3）—— 它只是"给的候选"，不是真相。')
        lad = p.get('bpm_ladder') or {}
        if lad:
            print('     候选层级（值: 支持度）: %s'
                  % '  '.join('%s:%.3f' % (k, v) for k, v in
                              sorted(lad.items(), key=lambda kv: -kv[1])[:6]))
        print('     → 与 `probe_style.py` 的逐拍节奏型对不上就是判错了；'
              '确认后用 `profile_ref.py <音频> %s --bpm <值>` 重写画像' % p['name'])
    return 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    sys.exit(main())
