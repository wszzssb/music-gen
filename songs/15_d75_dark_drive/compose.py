#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""d75_dark_drive —— 编配数据在 song.json，本文件只负责调用引擎"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', 'scripts'))
import song_engine

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == '__main__':
    song_engine.compose(os.path.join(HERE, 'song.json'))
