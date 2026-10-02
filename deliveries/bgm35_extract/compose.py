#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bgm35_extract —— 编配数据在 song.json，本文件只负责调用引擎（引擎通用模板）

来源：`D:\\test\\galgame\\ピュアソングガーデン！解包\\Bgm\\BGM35.ogg` 的扒带（2026-10-01）。
转录 + 引擎编配由 `transcribe_ymt3.py`（默认接续）落成 `song.json`；本文件是曲库要求的入口。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 面板按"曲库路径"执行本文件时，HERE/../../scripts 会解析到曲库的上级 →
# 面板与它起的任务都会设 BGM_STUDIO_ROOT，优先用它；没设（手工直跑）才回退相对路径。
_ROOT = os.environ.get('BGM_STUDIO_ROOT') or os.path.join(HERE, '..', '..')
sys.path.insert(0, os.path.join(_ROOT, 'scripts'))
import song_engine

if __name__ == '__main__':
    song_engine.compose(os.path.join(HERE, 'song.json'))
