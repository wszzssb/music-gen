#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 Windows 自带 GS Wavetable 合成器试听 MIDI（不需要 DAW）

通过 WinMM 的 MCI sequencer 接口播放，走系统默认 MIDI 输出。
音色是 Windows 自带的 GM 音源（很一般，但能立刻听出旋律/和声对不对）。

用法:
  python play_midi.py <file.mid>            播放（不等，立即返回）
  python play_midi.py <file.mid> --wait     播放并等它放完
  python play_midi.py --stop                停止
"""
import ctypes
import os
import sys
import time

mci = ctypes.windll.winmm.mciSendStringW


def send(cmd):
    buf = ctypes.create_unicode_buffer(512)
    err = mci(cmd, buf, 512, None)
    if err:
        ebuf = ctypes.create_unicode_buffer(512)
        ctypes.windll.winmm.mciGetErrorStringW(err, ebuf, 512)
        raise RuntimeError('MCI 错误 %d: %s (cmd=%s)' % (err, ebuf.value, cmd))
    return buf.value


def play(path, wait=False):
    path = os.path.abspath(path)
    try:
        send('close dshmidi')
    except RuntimeError:
        pass
    send('open "%s" type sequencer alias dshmidi' % path)
    length = send('status dshmidi length')          # 毫秒
    ms = int(length) if length.isdigit() else 0
    print('播放 %s（约 %.1fs）' % (os.path.basename(path), ms / 1000.0))
    send('play dshmidi')
    if wait:
        time.sleep(ms / 1000.0 + 0.5)
        send('close dshmidi')
        print('播放结束')


def stop():
    try:
        send('stop dshmidi')
        send('close dshmidi')
        print('已停止')
    except RuntimeError as e:
        print(e)


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == '--stop':
        stop()
    else:
        play(sys.argv[1], '--wait' in sys.argv)
