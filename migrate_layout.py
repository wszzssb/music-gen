#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性迁移：把 music-gen 从平铺整理成分层结构

  music-gen/
    README.md           总索引
    vendor/             外部二进制（fluidsynth + SF2 音源）   ← 原 tools/
    scripts/            引擎 + 分析/体检/渲染工具（互相 import，必须同目录）
    songs/<曲名>/       每首歌的作曲脚本 + MIDI/WAV/OGG + notes.md
    .venv/              依赖环境（不动）

运行: python migrate_layout.py          （幂等，可重复跑）
      python migrate_layout.py --dry    （只看计划）
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DRY = '--dry' in sys.argv

SCRIPTS = [
    'bgm_synth.py', 'bgm_acoustic.py', 'to_ogg.py', 'render_midi.py',
    'analyze_bass.py', 'analyze_chords.py', 'analyze_prog.py',
    'analyze_ref.py', 'analyze_ref2.py',
    'arrange_probe.py', 'midi_probe.py', 'noise_probe.py',
    'probe_style.py', 'section_probe.py',
    'check_soundfont.py', 'probe_mirrors.py', 'setup_soundfont.py',
    'play_midi.py',
]

# 歌曲: 目录名 -> (作曲脚本或 None, 产物前缀, 说明)
SONGS = {
    '01_ac150_seaside': ('summer_seaside.py', 'summer_seaside',
                          '夏日海边风格（A 大调 150BPM 40 小节 / 64 秒）'),
    '02_dn128_drive': ('drive_pop.py', 'drive_pop',
                     '仿 bgm01c.ogg（D 小调 128BPM 64 小节 / 120 秒）'),
    '03_ac76_garden': (None, 'garden_warm_test',
                       '原声测试曲（F 大调 76BPM 4 小节）；作曲脚本是 scripts/bgm_acoustic.py'),
    '04_dn92_garden_theme': (None, 'pure_garden_theme',
                             '早期电音向 demo（D 大调 92BPM 8 小节）；作曲脚本是 scripts/bgm_synth.py'),
}


def move(src, dst):
    if not os.path.exists(src):
        return False
    if os.path.exists(dst):
        print('   跳过（目标已存在）: %s' % os.path.basename(dst))
        return False
    print('   %s -> %s' % (os.path.relpath(src, HERE), os.path.relpath(dst, HERE)))
    if not DRY:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
    return True


def patch(path, pairs):
    """精确字符串替换（只动路径常量，不碰音乐数据）"""
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as f:
        txt = f.read()
    orig = txt
    for a, b in pairs:
        if a in txt:
            txt = txt.replace(a, b)
    if txt != orig:
        print('   改写路径常量: %s' % os.path.relpath(path, HERE))
        if not DRY:
            with open(path, 'w', encoding='utf-8', newline='') as f:
                f.write(txt)


def main():
    print('== 1. 建目录 ==')
    for d in ('vendor', 'scripts', os.path.join('songs')):
        p = os.path.join(HERE, d)
        print('   %s' % d)
        if not DRY:
            os.makedirs(p, exist_ok=True)

    print('== 2. 二进制工具 tools/ -> vendor/ ==')
    src_tools = os.path.join(HERE, 'tools')
    if os.path.isdir(src_tools):
        for name in os.listdir(src_tools):
            move(os.path.join(src_tools, name),
                 os.path.join(HERE, 'vendor', name))
        if not DRY and not os.listdir(src_tools):
            os.rmdir(src_tools)

    print('== 3. 脚本 -> scripts/ ==')
    for s in SCRIPTS:
        move(os.path.join(HERE, s), os.path.join(HERE, 'scripts', s))

    print('== 4. 歌曲 -> songs/<曲名>/ ==')
    for folder, (composer, prefix, _note) in SONGS.items():
        d = os.path.join(HERE, 'songs', folder)
        if not DRY:
            os.makedirs(d, exist_ok=True)
        print('  [%s]' % folder)
        if composer:
            move(os.path.join(HERE, composer), os.path.join(d, composer))
        for f in sorted(os.listdir(HERE)):
            if f.startswith(prefix + '.') or f.startswith(prefix + '_'):
                move(os.path.join(HERE, f), os.path.join(d, f))

    print('== 5. 修正脚本里的路径常量 ==')
    # setup_soundfont.py: tools -> vendor（路径相对 scripts/ 的上一级）
    patch(os.path.join(HERE, 'scripts', 'setup_soundfont.py'), [
        ("TOOLS = os.path.join(HERE, 'tools')",
         "TOOLS = os.path.join(os.path.dirname(HERE), 'vendor')"),
        ("print('  tools/fluidsynth/", "print('  vendor/fluidsynth/"),
    ])
    # 作曲脚本搬到 songs/ 后要能找到 scripts/ 里的引擎
    boot = ("import os\nimport sys\n\n"
            "# 引擎在 scripts/，本脚本在 songs/<曲名>/ —— 加进搜索路径\n"
            "sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),\n"
            "                                '..', '..', 'scripts'))\n\n")
    for folder, (composer, _p, _n) in SONGS.items():
        if not composer:
            continue
        p = os.path.join(HERE, 'songs', folder, composer)
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                txt = f.read()
            if 'sys.path.insert' not in txt:
                # 插到第一个 import 之前
                idx = txt.find('import os')
                if idx > 0:
                    txt = txt[:idx] + boot + txt[idx + len('import os\n'):]
                    print('   加 sys.path 引导: %s' % folder)
                    if not DRY:
                        with open(p, 'w', encoding='utf-8', newline='') as f:
                            f.write(txt)

    print('== 完成 %s ==' % ('(DRY RUN)' if DRY else ''))
    print('  引擎/工具: scripts/   二进制: vendor/   歌曲: songs/*/')
    return 0


if __name__ == '__main__':
    sys.exit(main())
