#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
「Drive Pop」—— 仿 bgm01c.ogg 风格（128BPM 四踩底鼓 + 反拍踩镲 + 厚贝斯）

参考曲 bgm01c.ogg 分析所得的硬特征（这些是可靠的）：
  * 128 BPM，4/4，小节 1.875s（低频周期性 0.96 → 稳定节奏型）
  * 节奏型：低频每拍强脉冲（四踩底鼓 + 八分贝斯）
            高频在每拍后半拍（"&"）→ 反拍踩镲
  * 调式：最安静段音级 A D G A# D# → 开放五度 D-A 铺底 + Bb/Eb 色彩
          → D 为中心的自然小调（多利亚六度色彩）
  * 音色画像：40-80Hz 最强；质心 3683Hz；10-18k −21.8dB；宽度 0.316
  * 响度：RMS −14.3dBFS（比 BGM16c 响 2.6dB）；前 8 小节安静后全程满编

结构: Intro 8 + A 16 + B 16 + A' 16 + Outro 8 = 64 小节 ≈ 120 秒
输出: drive_pop.mid（主交付）→ 再用 render_midi.py 出真音源成品

用法: python drive_pop.py
"""
import os
import sys

# 引擎在 scripts/，本脚本在 songs/<曲名>/ —— 加进搜索路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', 'scripts'))

import sys

import bgm_synth as bs

BPM = 128.0
BEAT = 60.0 / BPM
BAR = 4 * BEAT                     # 1.875s

# ---------------------------------------------------------------- 和弦库 (D 小调)
# 名称: (贝斯根音, [和弦音 低→高])
CHORDS = {
    'D5':  (38, [57, 62, 69, 74, 81]),    # D-A 开放五度（前奏铺底）
    'Dm':  (38, [57, 62, 65, 69, 74]),    # D  F  A  D  F
    'Bb':  (34, [58, 62, 65, 70, 74]),    # Bb D  F  Bb D
    'F':   (41, [57, 60, 65, 69, 72]),    # A  C  F  A  C
    'C':   (36, [55, 60, 64, 67, 72]),    # G  C  E  G  C
    'Gm':  (31, [55, 58, 62, 67, 70]),    # G  Bb D  G  Bb
    'A7':  (33, [57, 61, 64, 67, 73]),    # A  C# E  G  C#
}


def tone(tones, i):
    """取和弦音（越界回落到最高音，避免索引崩溃）"""
    return tones[min(max(0, i), len(tones) - 1)]


# ---------------------------------------------------------------- 旋律
# (小节, 拍, 时值拍, midi) —— D 小调，主奏音区 D5-F6
MEL_A = [
    (0, 0.0, 0.5, 74), (0, 0.5, 0.5, 77), (0, 1.0, 1.0, 81),
    (0, 2.0, 0.5, 77), (0, 2.5, 0.5, 76), (0, 3.0, 1.0, 74),
    (1, 0.0, 1.0, 77), (1, 1.0, 1.0, 74), (1, 2.0, 2.0, 70),
    (2, 0.0, 0.5, 69), (2, 0.5, 0.5, 72), (2, 1.0, 1.5, 77),
    (2, 2.5, 0.5, 76), (2, 3.0, 1.0, 74),
    (3, 0.0, 1.0, 76), (3, 1.0, 1.0, 79), (3, 2.0, 2.0, 76),
    (4, 0.0, 0.5, 74), (4, 0.5, 0.5, 77), (4, 1.0, 1.0, 81), (4, 2.0, 2.0, 86),
    (5, 0.0, 1.0, 84), (5, 1.0, 1.0, 82), (5, 2.0, 2.0, 77),
    (6, 0.0, 1.0, 79), (6, 1.0, 1.0, 82), (6, 2.0, 0.5, 86),
    (6, 2.5, 0.5, 84), (6, 3.0, 1.0, 82),
    (7, 0.0, 2.0, 81), (7, 2.0, 1.0, 73), (7, 3.0, 1.0, 76),
]

MEL_B = [
    (0, 0.0, 0.5, 77), (0, 0.5, 0.5, 79), (0, 1.0, 2.0, 81), (0, 3.0, 1.0, 82),
    (1, 0.0, 1.0, 84), (1, 1.0, 1.0, 82), (1, 2.0, 2.0, 79),
    (2, 0.0, 1.0, 81), (2, 1.0, 2.0, 86), (2, 3.0, 1.0, 84),
    (3, 0.0, 2.0, 86), (3, 2.0, 2.0, 81),
    (4, 0.0, 1.0, 82), (4, 1.0, 1.0, 86), (4, 2.0, 2.0, 89),
    (5, 0.0, 1.0, 88), (5, 1.0, 1.0, 86), (5, 2.0, 2.0, 84),
    (6, 0.0, 1.0, 81), (6, 1.0, 1.0, 84), (6, 2.0, 1.0, 89), (6, 3.0, 1.0, 88),
    (7, 0.0, 2.0, 86), (7, 2.0, 1.0, 85), (7, 3.0, 1.0, 81),
]

INTRO_MEL = [
    (4, 0.0, 1.0, 74), (4, 1.0, 1.0, 77), (4, 2.0, 2.0, 81),
    (5, 0.0, 1.0, 79), (5, 1.0, 1.0, 77), (5, 2.0, 2.0, 74),
    (6, 0.0, 1.0, 72), (6, 1.0, 1.0, 74), (6, 2.0, 1.0, 77), (6, 3.0, 1.0, 76),
    (7, 0.0, 2.0, 74), (7, 2.0, 2.0, 73),
]

OUTRO_MEL = [
    (0, 0.0, 2.0, 74), (0, 2.0, 2.0, 77),
    (1, 0.0, 2.0, 70), (1, 2.0, 2.0, 74),
    (2, 0.0, 2.0, 67), (2, 2.0, 2.0, 73),
    (3, 0.0, 4.0, 74),
    (4, 0.0, 4.0, 74),
    (5, 0.0, 4.0, 70),
    (6, 0.0, 8.0, 62),
    (7, 0.0, 8.0, 62),
]

# ---------------------------------------------------------------- 段落
SECTIONS = [
    ('Intro', 8, ['D5', 'D5', 'D5', 'D5', 'Dm', 'Dm', 'Bb', 'C'], INTRO_MEL,
     dict(drum=1, bass=True, pad=True, ep=False, arp=True, hook=False,
          glock=False, strings=False, vel=0.88)),
    ('A', 16, ['Dm', 'Bb', 'F', 'C', 'Dm', 'Bb', 'Gm', 'A7'] * 2, MEL_A,
     dict(drum=2, bass=True, pad=True, ep=True, arp=True, hook=True,
          glock=True, strings=False, vel=1.0)),
    ('B', 16, ['Bb', 'C', 'Dm', 'Dm', 'Bb', 'C', 'F', 'A7'] * 2, MEL_B,
     dict(drum=3, bass=True, pad=True, ep=True, arp=True, hook=True,
          glock=True, strings=True, vel=1.04)),
    ("A'", 16, ['Dm', 'Bb', 'F', 'C', 'Dm', 'Bb', 'Gm', 'A7'] * 2, MEL_A,
     dict(drum=2, bass=True, pad=True, ep=True, arp=True, hook=True,
          glock=True, strings=True, vel=1.02)),
    ('Outro', 8, ['Dm', 'Bb', 'Gm', 'A7', 'Dm', 'Bb', 'Dm', 'Dm'], OUTRO_MEL,
     dict(drum=2, bass=True, pad=True, ep=True, arp=False, hook=False,
          glock=True, strings=True, vel=0.94)),
]

PROGRAMS = {
    'Melody': (81, 0),    # GM81 主奏合成器
    'Hook': (4, 1),       # GM4  电钢琴（反拍切分和弦）
    'Arp': (87, 2),       # GM87 细碎琶音
    'Pad': (89, 3),       # GM89 暖 Pad
    'Strings': (48, 4),   # GM48 弦乐
    'Bass': (38, 5),      # GM38 合成贝斯
    'Glock': (9, 6),      # GM9  钟琴
    'Perc': (None, 9),    # 鼓组
}

MIX = {
    'Melody':  (72, 100),
    'Hook':    (48, 78),
    'Arp':     (92, 58),
    'Pad':     (64, 66),
    'Strings': (56, 62),
    'Bass':    (64, 96),
    'Glock':   (104, 58),
    'Perc':    (64, 84),
}


# ---------------------------------------------------------------- 编配生成
def drums(level, bar_i, nbars):
    """四踩底鼓 + 反拍踩镲 + 2/4 军鼓
    踩镲刻意做成"反拍为主、正拍很轻"——参考曲高频型是 ◇·★◇·◇★◇◇·★◇·◇★◇
    （★落在每拍后半拍），如果正拍和十六分都加满，高频会糊成一片、失去律动感"""
    out = []
    for b in range(4):
        out.append((b, 0.1, 36, 100 if b % 2 == 0 else 94))
        out.append((b + 0.25, 0.1, 36, 84))           # 双踩：正拍+十六分（对齐参考曲 ★★◇·）
    out.append((3.75, 0.1, 36, 88))
    for b in (1, 3):
        out.append((b, 0.1, 38, 96))
    for b in range(4):
        # 只留反拍踩镲，且音量拉高 —— 正拍也加的话 7-12kHz 会糊成一片、律动就没了
        out.append((b + 0.5, 0.1, 42, 98))
        if level >= 3:
            out.append((b + 0.25, 0.1, 42, 20))       # 十六分幽灵音（极轻）
    if level >= 3:
        out.append((3.5, 0.1, 46, 72))                # 开镲
    if bar_i == 0:
        out.append((0.0, 0.1, 49, 92))                # 段首吊镲
    if bar_i == nbars - 1:                            # 段尾过门
        out.append((3.5, 0.1, 48, 88))
        out.append((3.75, 0.1, 45, 92))
    return out


def bass_line(chord, bar_i):
    """十六分驱动贝斯 —— 参考曲低频型 ★★◇·（每拍：正拍强、十六分次强、八分中）"""
    bass, tones = CHORDS[chord]
    f5 = bass + 7
    oct_up = bass + 12
    pat = [(0.0, bass, 106), (0.25, bass, 104), (0.5, bass, 80),
           (1.0, bass, 100), (1.25, bass, 100), (1.5, oct_up, 78),
           (2.0, bass, 106), (2.25, bass, 104), (2.5, bass, 80),
           (3.0, bass, 100), (3.25, f5, 100), (3.5, oct_up, 82)]
    if bar_i % 4 == 3:
        pat.append((3.75, tone(tones, 2) - 12, 80))
    return [(t, 0.18, m, v) for (t, m, v) in pat]


def pad_part(chord):
    _, tones = CHORDS[chord]
    return [(0.0, 4.1, m, 52) for m in tones[:3]]


def ep_part(chord, bar_i):
    """电钢琴：反拍切分和弦（与反拍踩镲咬合）"""
    _, tones = CHORDS[chord]
    out = []
    for beat in (0.5, 1.5, 2.5, 3.5):
        for m in tones[1:4]:
            out.append((beat, 0.22, m, 54 if beat != 2.5 else 62))
    if bar_i % 4 == 3:
        out.append((3.25, 0.2, tone(tones, 4), 66))
    return out


def arp_part(chord, bar_i):
    """八分琶音（细碎、靠右）。改成八分而不是十六分：十六分会让 7-12kHz 糊成一片，
    参考曲高频是清晰的"反拍踩镲"型，琶音必须让位"""
    _, tones = CHORDS[chord]
    seq = [tone(tones, 0), tone(tones, 2), tone(tones, 4), tone(tones, 2)]
    out = []
    for k in range(8):
        out.append((k * 0.5, 0.28, seq[k % 4] + 12, 42 + (8 if k % 2 == 0 else 0)))
    return out


def hook_part(chord, bar_i):
    """副旋律动机：两小节的短句，落和弦音"""
    _, tones = CHORDS[chord]
    if bar_i % 2 == 0:
        return [(0.0, 0.5, tone(tones, 3) + 12, 68), (1.0, 0.5, tone(tones, 2) + 12, 64),
                (2.0, 0.5, tone(tones, 1) + 12, 62), (3.0, 1.0, tone(tones, 3) + 12, 66)]
    return [(0.5, 0.5, tone(tones, 4) + 12, 64), (1.5, 0.5, tone(tones, 2) + 12, 62),
            (2.5, 0.5, tone(tones, 3) + 12, 66)]


def glock_part(chord, bar_i):
    _, tones = CHORDS[chord]
    if bar_i % 4 == 2:
        return [(1.5, 0.4, tone(tones, 3) + 24, 58), (3.0, 0.4, tone(tones, 2) + 24, 54)]
    if bar_i % 4 == 3:
        return [(0.0, 0.4, tone(tones, 4) + 24, 60)]
    return []


def build_events():
    ev = {k: [] for k in PROGRAMS}
    bar0 = 0
    for (name, nbars, chords, mel, arr) in SECTIONS:
        vs = arr.get('vel', 1.0)
        sec = {k: [] for k in PROGRAMS}
        for i in range(nbars):
            chord = chords[i]
            t0 = (bar0 + i) * 4.0
            if arr['drum']:
                for (b, d, m, v) in drums(arr['drum'], i, nbars):
                    sec['Perc'].append((t0 + b, d, m, v))
            if arr['bass']:
                for (b, d, m, v) in bass_line(chord, i):
                    sec['Bass'].append((t0 + b, d, m, v))
            if arr['pad']:
                for (b, d, m, v) in pad_part(chord):
                    sec['Pad'].append((t0 + b, d, m, v))
            if arr['ep']:
                for (b, d, m, v) in ep_part(chord, i):
                    sec['Hook'].append((t0 + b, d, m, v))
            if arr['arp']:
                for (b, d, m, v) in arp_part(chord, i):
                    sec['Arp'].append((t0 + b, d, m, v))
            if arr['hook']:
                for (b, d, m, v) in hook_part(chord, i):
                    sec['Glock'].append((t0 + b, d, m, v))
            if arr['glock']:
                for (b, d, m, v) in glock_part(chord, i):
                    sec['Glock'].append((t0 + b, d, m, v))
            if arr['strings']:
                _, tones = CHORDS[chord]
                for m in tones[:3]:
                    sec['Strings'].append((t0, 4.1, m + 12, 50))
        for (b, beat, dur, m) in mel:
            t = (bar0 + b) * 4.0 + beat
            sec['Melody'].append((t, dur * 0.96, m, 96))
            sec['Melody'].append((t, dur * 0.9, m - 12, 62))   # 低八度加厚
        for k in sec:
            for (t, d, m, v) in sec[k]:
                ev[k].append((t, d, m, max(1, min(127, int(round(v * vs))))))
        bar0 += nbars
    for k in ev:
        ev[k].sort()
    return ev, bar0


def write_midi(path, ev):
    tracks = []
    for name, (prog, ch) in PROGRAMS.items():
        pan, vol = MIX[name]
        tracks.append((name, prog, ch, ev[name], [(0.0, 10, pan), (0.0, 7, vol)]))
    bs.write_midi(path, tracks, ppq=480)


def main():
    outdir = os.path.dirname(os.path.abspath(__file__))
    print('[1/2] 展开编配 ...')
    ev, nbars = build_events()
    counts = ', '.join('%s:%d' % (k, len(v)) for k, v in ev.items())
    print('      %d 小节 ≈ %.0f 秒 @%.0fBPM' % (nbars, nbars * BAR, BPM))
    print('      音符: %s' % counts)
    mid = os.path.join(outdir, 'drive_pop.mid')
    bs.BPM = BPM
    write_midi(mid, ev)
    print('[2/2] MIDI: %s' % mid)
    print('      接着跑（在 music-gen 根目录）:')
    print('        .venv\\Scripts\\python scripts\\render_midi.py '
          'songs\\02_dn128_drive\\drive_pop.mid drive_pop_sf '
          '--width 1.0 --rms -14.3 --shelf 2.5 --hp 42 --low 3.5 --drive 2.0')


if __name__ == '__main__':
    sys.exit(main())
