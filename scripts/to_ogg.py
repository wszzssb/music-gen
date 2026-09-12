#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WAV -> OGG(Vorbis) 高质量转换 + 编码质量校验

之前用 libsndfile 默认质量编码（约 q=0.4/116kbps），安静段落会出现可听的编码杂音；
改用 ffmpeg 的 libvorbis -q:a 6（约 192kbps）并做 null test 验证残留误差。

用法: .venv\\Scripts\\python to_ogg.py <in.wav> [out.ogg] [quality 0-10 默认8]
"""
import os
import subprocess
import sys

import numpy as np
import soundfile as sf

# 默认 q=8 ≈ 146kbps，和游戏原曲 BGM16c.ogg（145kbps）同级
DEFAULT_Q = 8.0


def convert(src, dst=None, q=DEFAULT_Q):
    dst = dst or (src.rsplit('.', 1)[0] + '.ogg')
    import imageio_ffmpeg
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [exe, '-hide_banner', '-loglevel', 'error', '-y', '-i', src,
           '-c:a', 'libvorbis', '-q:a', str(q), dst]
    subprocess.run(cmd, check=True)
    info = sf.info(dst)
    kbps = os.path.getsize(dst) * 8 / info.duration / 1000
    print('%s -> %s | %dch %dHz %.2fs %.2fMB %.0fkbps (q=%s)'
          % (os.path.basename(src), os.path.basename(dst), info.channels,
             info.samplerate, info.duration, os.path.getsize(dst) / 1048576,
             kbps, q))
    # null test：解码回来和原 WAV 相减，看残留误差
    a, sr = sf.read(src, dtype='float64', always_2d=True)
    b, _ = sf.read(dst, dtype='float64', always_2d=True)
    n = min(len(a), len(b))
    d = a[:n] - b[:n]
    rms_a = np.sqrt((a[:n] ** 2).mean())
    rms_d = np.sqrt((d ** 2).mean())
    print('    null test: 残留 %.1f dBFS（相对信号 %.1f dB） 峰值残差 %.4f'
          % (20 * np.log10(max(rms_d, 1e-12)),
             20 * np.log10(max(rms_d / max(rms_a, 1e-12), 1e-12)),
             float(np.abs(d).max())))
    return dst


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    convert(sys.argv[1],
            sys.argv[2] if len(sys.argv) > 2 else None,
            float(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_Q)
