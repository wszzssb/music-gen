#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""piano_rain —— 编配数据在 song.json，本文件只负责调用引擎"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# ⚠ **面板是按"曲库路径"执行本文件的**（曲目用 `cmd /c mklink /J <曲库>\\<曲> <真身>\\<曲>`
# 挂进曲库），于是 `HERE/../../scripts` 会解析到**曲库的上级**、`import song_engine` 直接
# `ModuleNotFoundError` —— 2026-09-19 实测：面板「🎼 作曲」按钮对**所有**曲目都失败。
# 面板与它起的任务都会设 `BGM_STUDIO_ROOT` → 优先用它；没设（手工直跑）才回退相对路径。
_ROOT = os.environ.get('BGM_STUDIO_ROOT') or os.path.join(HERE, '..', '..')
sys.path.insert(0, os.path.join(_ROOT, 'scripts'))
import song_engine

if __name__ == '__main__':
    song_engine.compose(os.path.join(HERE, 'song.json'))
