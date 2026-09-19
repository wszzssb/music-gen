#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""waltz_ballroom —— 编配数据在 song.json，本文件只负责调用引擎"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 面板按"曲库路径"执行本文件 → `HERE/../../scripts` 会指到曲库的上级，找不到 song_engine。
# 面板与它起的任务都设 `BGM_STUDIO_ROOT`，优先用它；没设（手工直跑）才回退相对路径。
_ROOT = os.environ.get('BGM_STUDIO_ROOT') or os.path.join(HERE, '..', '..')
sys.path.insert(0, os.path.join(_ROOT, 'scripts'))
import song_engine

if __name__ == '__main__':
    song_engine.compose(os.path.join(HERE, 'song.json'))
