#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
「海風のプレリュード」—— 夏日海边风格曲

仿照参考曲 BGM16c.ogg 的音乐语言（分析所得）：
  * 速度 150 BPM，4/4，八分音符驱动（不是慢歌）
  * 七和弦 + sus4 为主，核心进行 ii7 - III7 - iv7 - V7
  * 借用的 iv7（小四级）：A 大调里的 Dm7，最典型的"夏日日系"色彩
  * 结构：Intro - A - B - A' - B' - Outro，40 小节 ≈ 64 秒
参考是 Ab 大调，这里用 A 大调（高一个半音，吉他/尤克里里好按）

输出：
  summer_seaside.mid          —— 主交付：7 轨 GM 音色，丢进 DAW 挂真实音源
  summer_seaside.wav / .ogg   —— 合成器试听版（音色是替代品，仅用来确认旋律和声）

用法: python summer_seaside.py [--no-audio]
"""
import math
import os
import sys

# 引擎在 scripts/，本脚本在 songs/<曲名>/ —— 加进搜索路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', 'scripts'))

import sys

import bgm_synth as bs
import bgm_acoustic as ac

BPM = 150.0
BEAT = 60.0 / BPM
BAR = 4 * BEAT                     # 1.6s

# ---------------------------------------------------------------- 和弦库
# 名称: (贝斯音, [和弦音，低→高])   —— A 大调体系，含借用的 iv7
CHORDS = {
    'A':      (33, [57, 61, 64, 69, 73]),          # A  C# E  A  C#
    'Asus4':  (33, [57, 62, 64, 69, 74]),          # A  D  E  A  D
    'A6/9':   (33, [57, 61, 64, 66, 71]),          # A  C# E  F# B
    'A/E':    (40, [57, 61, 64, 69, 73]),          # 转位，贝斯走 E
    'C#m7':   (37, [56, 59, 61, 64, 68]),          # G# B  C# E  G#
    'C#7':    (37, [56, 61, 65, 68, 71]),          # G# C# F  G# B   (III7，副属)
    'D':      (38, [57, 62, 66, 69, 74]),          # A  D  F# A  D
    'Dmaj9':  (38, [57, 61, 64, 66, 69]),          # A  C# E  F# A  （含 9 音 E，故为 maj9）
    'Dm7':    (38, [57, 62, 65, 69, 72]),          # A  D  F  A  C   (借用 iv7)
    'F#m7':   (42, [54, 57, 61, 64, 69]),          # F# A  C# E  A
    'Bm7':    (35, [59, 62, 66, 69, 74]),          # B  D  F# A  D
    'B7':     (35, [57, 63, 66, 69, 75]),          # A  D# F# A  D#  （七音是本位 A，原来错写成 A#）
    'E7':     (40, [56, 59, 62, 68, 71]),          # G# B  D  G# B
    'E7sus4': (40, [57, 59, 64, 69, 71]),          # A  B  E  A  B
    # 参考曲最标志性的两个色彩，这里照搬：
    'A7':     (33, [57, 61, 64, 67, 73]),          # A  C# E  G   (I7，蓝调味主和弦)
    'A#7':    (34, [58, 62, 65, 68, 70]),          # A# D  F  G# (bII7，三全音代理→A)
}

# ---------------------------------------------------------------- 旋律
# (小节偏移, 拍, 时值拍, midi)
MEL_A = [
    (0, 0.5, 0.5, 61), (0, 1.0, 1.0, 64), (0, 2.0, 2.0, 69),
    (1, 0.0, 1.0, 68), (1, 1.0, 1.0, 64), (1, 2.0, 1.5, 66), (1, 3.5, 0.5, 64),
    (2, 0.0, 2.0, 66), (2, 2.0, 1.0, 69), (2, 3.0, 1.0, 71),
    (3, 0.0, 1.0, 69), (3, 1.0, 1.0, 65), (3, 2.0, 2.0, 64),
    (4, 0.0, 1.0, 61), (4, 1.0, 1.0, 64), (4, 2.0, 2.0, 69),
    (5, 0.0, 1.0, 68), (5, 1.0, 1.0, 66), (5, 2.0, 2.0, 61),
    (6, 0.0, 1.0, 62), (6, 1.0, 1.0, 66), (6, 2.0, 1.0, 71), (6, 3.0, 1.0, 69),
    (7, 0.0, 1.5, 68), (7, 1.5, 0.5, 71), (7, 2.0, 1.0, 69), (7, 3.0, 1.0, 68),
]

# 副歌：整体高一个八度区，起句更亮
MEL_B = [
    (0, 0.0, 1.0, 74), (0, 1.0, 1.0, 73), (0, 2.0, 2.0, 71),
    (1, 0.0, 1.0, 73), (1, 1.0, 1.0, 71), (1, 2.0, 2.0, 68),
    (2, 0.0, 1.0, 69), (2, 1.0, 1.0, 65), (2, 2.0, 2.0, 64),
    (3, 0.0, 1.0, 68), (3, 1.0, 1.0, 71), (3, 2.0, 1.0, 74), (3, 3.0, 1.0, 71),
    (4, 0.0, 2.0, 73), (4, 2.0, 1.0, 71), (4, 3.0, 1.0, 69),
    (5, 0.0, 1.0, 68), (5, 1.0, 1.0, 69), (5, 2.0, 2.0, 73),
    (6, 0.0, 1.0, 74), (6, 1.0, 1.0, 73), (6, 2.0, 1.0, 71), (6, 3.0, 1.0, 68),
    (7, 0.0, 3.0, 69),
]

# A' 变奏：主题加装饰音（(相对小节, 拍, 时值, 音)）
MEL_A2 = MEL_A + [
    (1, 3.0, 0.5, 66), (3, 3.5, 0.5, 66), (5, 3.0, 0.5, 64), (6, 3.5, 0.5, 73),
]

INTRO_MEL = [
    # 前奏用"八音盒"高八度陈述主题（原来在 F#4 附近，平均音高只有 A3，太闷）
    (2, 0.0, 1.0, 78), (2, 1.0, 1.0, 81), (2, 2.5, 1.5, 86),
    (3, 0.0, 2.0, 83), (3, 2.0, 2.0, 80),
]
OUTRO_MEL = [
    (0, 0.0, 2.0, 69), (0, 2.0, 2.0, 73),
    (1, 0.0, 2.0, 72), (1, 2.0, 2.0, 69),
    (2, 0.0, 4.0, 65),                     # 65=F，落在 A#7 上不打架
    (3, 0.0, 4.5, 69),
]

# ---------------------------------------------------------------- 段落
# (名称, 小节数, 每小节和弦, 旋律, 编配开关)
# vel = 该段整体力度系数（做段落起伏：前奏轻、副歌强，避免全曲力度一条直线）
SECTIONS = [
    ('Intro', 4, ['Asus4', 'A6/9', 'Dmaj9', 'E7sus4'], INTRO_MEL,
     dict(uku=True, piano=True, strings=True, glock=True, perc=1, bass=True,
          vel=0.95)),
    ('A', 8, ['A', 'C#m7', 'D', 'Dm7', 'A/E', 'F#m7', 'Bm7', 'E7'], MEL_A,
     dict(uku=True, piano=True, strings=False, glock=True, perc=1, bass=True,
          vel=0.94)),
    ('B', 8, ['Bm7', 'C#7', 'Dm7', 'E7', 'A', 'F#m7', 'Bm7', 'E7'], MEL_B,
     dict(uku=True, piano=True, strings=True, glock=True, perc=2, bass=True,
          vel=1.00)),
    ("A'", 8, ['A', 'C#m7', 'D', 'Dm7', 'A/E', 'F#m7', 'Bm7', 'E7'], MEL_A2,
     dict(uku=True, piano=True, strings=True, glock=True, perc=1, bass=True,
          vel=1.00)),
    ("B'", 8, ['Bm7', 'C#7', 'Dm7', 'E7', 'A7', 'F#m7', 'Bm7', 'E7'], MEL_B,
     # 高潮段：不再把旋律升八度（GM 钢琴高音区能量少，会变薄），
     # 改为整体力度 +10% 并让钟琴每小节都齐奏
     dict(uku=True, piano=True, strings=True, glock=True, perc=2, bass=True,
          vel=1.10, glock_all=True)),
    ('Outro', 4, ['D', 'Dm7', 'A#7', 'A6/9'], OUTRO_MEL,
     dict(uku=True, piano=True, strings=True, glock=True, perc=1, bass=True,
          vel=1.00)),
]


# ---------------------------------------------------------------- 编配生成
def guitar_arpeggio(chord, bar_i):
    """尤克里里/吉他：八分音符分解。
    关键：第 1 拍必须落在**根音**上（之前 order 从 tones[1] 起，根音只存在于贝斯，
    导致和声听觉上含糊、扒谱都对不上），再按 5-3-根 上行摆回。"""
    bass, tones = CHORDS[chord]
    pat = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    order = [0, 2, 3, 4, 3, 2, 4]              # 根 - 5 - 3 - 高八度 ...
    out = []
    for k, beat in enumerate(pat):
        idx = order[k % len(order)]
        m = tones[min(idx, len(tones) - 1)]
        vel = 80 if k % 2 == 1 else 68
        out.append((beat, 0.45, m, vel))
    return out


def piano_part(chord, bar_i):
    """钢琴：反拍和弦短音（含根音）+ 高音区持续音（夏日曲的'闪'）"""
    bass, tones = CHORDS[chord]
    out = []
    for beat in (0.5, 2.5):
        for m in tones[:3]:
            out.append((beat, 0.28, m, 60))
    out.append((0.0, 1.5, tones[3], 54))
    if bar_i % 4 == 3:                          # 每 4 小节句尾加一个上行过门
        out.append((3.5, 0.4, tones[2] + 12, 62))
    return out


def bass_part(chord, next_chord, bar_i):
    """贝斯：根音 + 五度 + 句尾半音过渡（贴合参考曲的和声走动习惯）"""
    bass, tones = CHORDS[chord]
    out = [(0.0, 1.4, bass, 96), (2.0, 0.9, bass, 80), (3.0, 0.9, bass, 74)]
    if bar_i % 2 == 1:
        fifth = bass + 7 if bass + 7 <= 47 else bass - 5
        out.append((2.5, 0.45, fifth, 72))
    if bar_i % 4 == 3 and next_chord:           # 走向下一个和弦的半音引导
        nb = CHORDS[next_chord][0]
        step = nb + (1 if nb > bass else -1)
        out.append((3.5, 0.45, step, 76))
    return out


def pad_part(chord):
    """弦乐：整小节持续的长音"""
    _, tones = CHORDS[chord]
    return [(0.0, 4.1, m, 54) for m in tones[:3]]


def glock_part(chord, bar_i):
    """钢片琴/钟琴：高音点缀，像海面反光的碎点"""
    _, tones = CHORDS[chord]
    out = []
    if bar_i % 2 == 0:
        out.append((1.5, 0.7, tones[2] + 12, 62))
        out.append((3.5, 0.7, tones[3] + 12, 58))
    else:
        out.append((2.5, 0.7, tones[4] + 12, 60))
    return out


def perc_part(level, bar_i):
    """打击：level 0=无 1=沙锤 2=沙锤+轻鼓（参考曲几乎无打击乐，这里刻意克制）"""
    out = []
    if level >= 1:
        for k in range(8):
            out.append((k * 0.5, 0.2, 82, 46 if k % 2 else 40))     # 沙锤
    if level >= 2:
        out.append((0.0, 0.2, 36, 72))                             # 轻底鼓
        out.append((2.0, 0.2, 36, 64))
        out.append((1.0, 0.2, 37, 58))                             # 边击
        out.append((3.0, 0.2, 37, 58))
    if level >= 1 and bar_i % 8 == 7:                              # 段尾小过门
        out.append((3.5, 0.2, 39, 62))
    return out


PROGRAMS = {
    'Melody': (0, 0),     # GM0  钢琴主奏
    'Uku': (25, 1),       # GM25 钢弦吉他（比 GM24 尼龙弦亮，更贴夏日的清脆感）
    'Piano': (0, 2),      # GM0  钢琴
    'Strings': (48, 3),   # GM48 弦乐合奏
    'Bass': (32, 4),      # GM32 原声贝斯
    'Glock': (9, 5),      # GM9  钟琴
    'Perc': (None, 9),    # 鼓组通道
}

# 混音：CC10 声像(64=中) / CC7 音量 —— 让 MIDI 自带摆位，FluidSynth 与 DAW 都会照着摆
MIX = {
    'Melody':  (76, 104),
    'Uku':     (42, 78),
    'Piano':   (88, 76),
    'Strings': (52, 70),
    'Bass':    (64, 92),
    'Glock':   (100, 76),
    'Perc':    (64, 72),
}


def build_events():
    """把所有段落展开成绝对时间的音符事件：{轨名: [(起始拍, 时值拍, 音高, 力度)]}"""
    ev = {k: [] for k in PROGRAMS}
    bar0 = 0
    for (name, nbars, chords, mel, arr) in SECTIONS:
        vel_scale = arr.get('vel', 1.0)
        sec = {k: [] for k in PROGRAMS}          # 先收集本段，再统一乘力度系数
        for i in range(nbars):
            chord = chords[i]
            nxt = chords[i + 1] if i + 1 < nbars else None
            t0 = (bar0 + i) * 4.0
            if arr['uku']:
                for (b, d, m, v) in guitar_arpeggio(chord, i):
                    sec['Uku'].append((t0 + b, d, m, v))
            if arr['piano']:
                for (b, d, m, v) in piano_part(chord, i):
                    sec['Piano'].append((t0 + b, d, m, v))
            if arr['strings']:
                for (b, d, m, v) in pad_part(chord):
                    sec['Strings'].append((t0 + b, d, m, v))
            if arr['glock']:
                for (b, d, m, v) in glock_part(chord, i):
                    sec['Glock'].append((t0 + b, d, m, v))
            if arr['bass']:
                for (b, d, m, v) in bass_part(chord, nxt, i):
                    sec['Bass'].append((t0 + b, d, m, v))
            if arr['perc']:
                for (b, d, m, v) in perc_part(arr['perc'], i):
                    sec['Perc'].append((t0 + b, d, m, v))
        for (b, beat, dur, m) in mel:
            t = (bar0 + b) * 4.0 + beat
            sec['Melody'].append((t, dur * 0.96, m, 92))
            if arr.get('glock') and (arr.get('glock_all') or b % 2 == 0):
                sec['Glock'].append((t, dur * 0.9, m + 12, 54))
        for k in sec:
            for (t, d, m, v) in sec[k]:
                ev[k].append((t, d, m, max(1, min(127, int(round(v * vel_scale))))))
        bar0 += nbars
    for k in ev:
        ev[k].sort()
    # 结尾：钟琴上行琶音（A5-C#6-E6-A6），像海浪退去的一道亮
    end = bar0 * 4.0
    for k, m in enumerate((81, 85, 88, 93)):
        ev['Glock'].append((end - 2.0 + k * 0.5, 1.6, m, 60))
    return ev, bar0


# ---------------------------------------------------------------- MIDI
def write_midi(path, ev):
    tracks = []
    for name, (prog, ch) in PROGRAMS.items():
        pan, vol = MIX[name]
        ccs = [(0.0, 10, pan), (0.0, 7, vol)]
        tracks.append((name, prog, ch, ev[name], ccs))
    bs.write_midi(path, tracks, ppq=480)


# ---------------------------------------------------------------- 试听音频
def render_audio(ev, outdir):
    nbars_total = max(t for k in ev for (t, d, m, v) in ev[k]) / 4.0 + 1.5
    total = nbars_total * BAR + 2.6
    n = int(total * bs.SR)
    L = [0.0] * n
    R = [0.0] * n
    # 摆位：硬摆双轨（±0.88），再在母带上做中/侧加宽
    PAN = {'Melody': 0.88, 'Uku': 0.90, 'Piano': 0.82, 'Strings': 0.75,
           'Bass': 0.0, 'Glock': 0.95}

    def soft_kick(Lb, Rb, t0, amp=0.32):
        """轻底鼓：55→45Hz 慢扫、长衰减，是"箱鼓/脚鼓"而不是 EDM 踢鼓"""
        m = int(0.16 * bs.SR)
        s0 = int(t0 * bs.SR)
        m = min(m, len(Lb) - s0)
        ph = 0.0
        for i in range(max(0, m)):
            tt = i / bs.SR
            f = 45.0 + 10.0 * math.exp(-tt / 0.045)
            ph += 2.0 * math.pi * f / bs.SR
            v = math.sin(ph) * math.exp(-tt / 0.070) * amp
            Lb[s0 + i] += v
            Rb[s0 + i] += v

    for name, notes in ev.items():
        pan = PAN.get(name, 0.0)
        for (start, dur, midi, vel) in notes:
            t = start * BEAT
            d = dur * BEAT
            a = vel / 100.0
            if name == 'Uku':
                # 尼龙吉他分解：双轨 + 提亮（bright 现在真的作用在高次分音上）
                ac.spaced(L, R, t, midi, 0.055 * a, max(d * 1.5, 0.45), pan,
                          bright=1.9, off=0.009)
            elif name == 'Piano':
                ac.spaced(L, R, t, midi, 0.048 * a, max(d * 2.2, 0.8), pan,
                          bright=1.35, off=0.012)
            elif name == 'Melody':
                ac.spaced(L, R, t, midi, 0.105 * a, max(d * 2.0, 1.0), pan,
                          bright=1.30, off=0.013)
            elif name == 'Strings':
                ac.strings(L, R, t, d + 0.3, midi, 0.050 * a, pan)
            elif name == 'Bass':
                ac.bass_warm(L, R, t, d + 0.15, midi, 0.30 * a)
            elif name == 'Glock':
                ac.celesta(L, R, t, midi, 0.070 * a, pan)
            elif name == 'Perc':
                # 试听版只还原沙锤与轻底鼓（边击/军鼓合成器做不像，省掉）
                if midi in (82, 39):
                    ac.shaker(L, R, t, 0.055 * a, pan=(0.35 if (int(start * 2) % 2) else -0.35))
                elif midi == 36:
                    soft_kick(L, R, t, 0.30 * a)
    ac.hall(L, R, 0.46)
    ac.stereo_width(L, R, 1.8)
    ac.highpass_master(L, R, 28.0)
    ac.low_shelf(L, R, 160.0, 1.5)
    ac.band_boost(L, R, 300.0, 1500.0, 4.0)
    ac.high_shelf(L, R, 2500.0, 10.0)
    ac.lowpass_master(L, R, 18000.0)
    ac.master(L, R, -17.0)
    pk, rms, dc, dur = bs.stats(L, R)
    wav = os.path.join(outdir, 'summer_seaside.wav')
    bs.write_wav(wav, L, R)
    print('      试听 WAV %s  %.1fMB  时长 %.1fs  峰值 %.3f (%.1fdBFS)  RMS %.1fdBFS'
          % (wav, os.path.getsize(wav) / 1048576, dur, pk,
             20 * math.log10(max(pk, 1e-9)), 20 * math.log10(max(rms, 1e-9))))
    return wav


def main():
    outdir = os.path.dirname(os.path.abspath(__file__))
    print('[1/3] 展开编配 ...')
    ev, nbars = build_events()
    counts = {k: len(v) for k, v in ev.items()}
    print('      %d 小节 ≈ %.1f 秒 @%.0fBPM | 音符数 %s' %
          (nbars, nbars * BAR, BPM,
           ', '.join('%s:%d' % (k, counts[k]) for k in PROGRAMS)))
    print('[2/3] 导出 MIDI ...')
    mid = os.path.join(outdir, 'summer_seaside.mid')
    bs.BPM = BPM
    write_midi(mid, ev)
    print('      %s  (7 轨 GM 音色)' % mid)
    if '--no-audio' not in sys.argv:
        print('[3/3] 渲染合成器试听版 ...')
        render_audio(ev, outdir)
    else:
        print('[3/3] 跳过试听音频')


if __name__ == '__main__':
    main()
