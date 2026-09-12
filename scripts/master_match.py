#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""master_match.py —— 交付前的可选最后一步：**按参考曲做母带匹配**（matchering）。

做什么：把成品与参考曲做频谱/响度/动态匹配（参考曲的"成品感"里有一半是母带），
实测效果：峰值因数 14.6 → 16.3dB（例曲 15.9）、峰值 0.844 → 0.989（例曲 1.000）。
**频段/织体这类"内容"问题它不会解决** —— 那是编排与音源的事。

为什么单独一步、不塞进 make_song：它需要 `.venv-ml`（Python 3.13 + matchering），
而主管线是纯 numpy 的确定性管线，两者分开更好维护；另外匹配是**可选的最后一步**。

用法:
  python scripts\\master_match.py songs\\14_d75_pulse\\pulse_sf.wav "D:\\refs\\BGM33.ogg"
  # 产物：<同名>_matched.wav / .ogg（原文件不动）
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MLPY = os.path.join(ROOT, '.venv-ml', 'Scripts', 'python.exe')

RUNNER = r'''
import sys, soundfile as sf, numpy as np, matchering as mg
import metrics      # noqa: E402  # 统一音频读取（含 ffmpeg 兜底）
target, ref, out = sys.argv[1], sys.argv[2], sys.argv[3]
# 参考若是 ogg/mp3 等，先转 wav（matchering 需要 wav/flac）
if not ref.lower().endswith(('.wav', '.flac')):
    y, sr = metrics.read_audio(ref, dtype='float64')
    refw = out + '.ref.wav'
    sf.write(refw, y, sr, subtype='PCM_24')
    ref = refw
mg.log(lambda s: None)
mg.process(target=target, reference=ref, results=[mg.pcm24(out)])
print('OK')
'''


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    target, ref = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
    if not os.path.exists(MLPY):
        print('找不到 .venv-ml —— 先按 ML.md 建环境（Python 3.13 + matchering）')
        return 1
    out = os.path.splitext(target)[0] + '_matched.wav'
    r = subprocess.run([MLPY, '-c', RUNNER, target, ref, out],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    if r.returncode != 0 or not os.path.exists(out):
        print('匹配失败: %s' % (r.stderr or '')[-400:])
        return 1
    refw = out + '.ref.wav'
    if os.path.exists(refw):
        os.remove(refw)
    print('匹配完成: %s' % out)
    # 顺手转 OGG（用主 venv 的 ffmpeg，别用 libsndfile 的 Vorbis —— 见坑 3）
    import to_ogg
    to_ogg.convert(out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
