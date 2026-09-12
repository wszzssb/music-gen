#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v2「原声/叙事」版 BGM 生成器 —— 对齐参考曲 BGM16c.ogg 的音色画像

与 v1 的区别（去电音感）:
  * 无鼓组        : 参考曲高频起音仅 0.3 个/秒 ≈ 无打击乐
  * 无乒乓延迟    : 节拍延迟是 EDM 标志，改用大厅混响
  * 钢琴主导      : 非谐分音 + 逐分音衰减 + 柔和击弦噪声，替代失谐叠加波
  * 暖弦垫        : 只保留 1-3 次谐波，±3 cent 轻微失谐（不做 supersaw）
  * 母带低通      : 9kHz 零相位一阶低通，把 5-18kHz 压到参考曲水平
  * 调性/响度     : F 大调；RMS 目标 -17 dBFS（参考 -16.9）

结构: F - Dm - Bb - C 四小节（约 12.6s）+ 混响尾 ≈ 15s
用法: python bgm_acoustic.py [输出目录]
"""
import math
import os
import random
import struct
import sys
import time
import wave

import bgm_synth as bs          # 复用 v1 的加法合成引擎与工具

SR = bs.SR
BPM = 76.0
BEAT = 60.0 / BPM
BAR_LEN = 4 * BEAT              # 3.158s
NBARS = 4
TOTAL = NBARS * BAR_LEN
TAIL = 3.0
RMS_TARGET_DB = -17.0

random.seed(1616)
m2f = bs.m2f
add_voice = bs.add_voice

# ---------------------------------------------------------------- 音色
def piano(L, R, t, midi, amp, pan, dur=None, bright=1.0):
    """钢琴：非谐分音 + 逐分音更快衰减（高音衰减更快的真实特征）+ 极轻击弦噪声"""
    f0 = m2f(midi)
    dur = dur if dur is not None else max(1.6, 3.6 - 0.045 * (midi - 40))
    # 高音区音色更薄、衰减更短
    tilt = min(1.0, max(0.55, (96 - midi) / 50.0))
    inc = math.sqrt(1.0 + 0.00042 * (midi - 40))          # 粗略非谐性
    # 8 个分音：低次厚、高次亮且衰减更快（真实钢琴的"击弦瞬间很亮"特征，
    # 中高频靠这些快速衰减的高次分音撑起来，而不是靠合成器锯齿波）
    ratios = [1.0, 2.003 * inc, 3.010 * inc, 4.030 * inc, 5.060 * inc,
              6.120 * inc, 7.190 * inc, 8.280 * inc, 9.390 * inc, 10.520 * inc]
    amps = [1.0, 0.60 * tilt, 0.46 * tilt, 0.34 * tilt, 0.25 * tilt,
            0.18 * tilt, 0.13 * tilt, 0.09 * tilt, 0.065 * tilt, 0.045 * tilt]
    decs = [max(0.55, dur * 0.62), max(0.45, dur * 0.36), max(0.34, dur * 0.24),
            max(0.26, dur * 0.17), max(0.20, dur * 0.12), max(0.16, dur * 0.09),
            max(0.13, dur * 0.07), max(0.11, dur * 0.055),
            max(0.09, dur * 0.045), max(0.08, dur * 0.04)]
    partials = []
    for k, (r, a, d) in enumerate(zip(ratios, amps, decs)):
        # bright 必须作用在**高次分音**上才叫提亮：
        # 之前写成 a*bright（所有分音等比缩放）＝只改音量，音色不变
        tilt_k = 1.0 + (bright - 1.0) * (k / max(1, len(ratios) - 1))
        partials.append((r, a * tilt_k, d))
    add_voice(L, R, t, dur, f0, amp, pan, partials, attack=0.006, release=0.30)
    noise_hammer(L, R, t, amp * 0.30, pan, f0)


# 噪声层总开关：默认全关。
# 这三层（击弦噪声/弓噪/房间底噪）本来是模仿真实录音的高频成分，
# 但实测就是可听的沙沙声 —— 用户反馈"很多杂音"，所以默认关闭。
# 想要更亮的音色请用搁架 EQ（high_shelf），不要用噪声。
USE_NOISE_LAYERS = False


def noise_hammer(L, R, t, amp, pan, f0):
    """击弦噪声（默认关闭）：真实钢琴按键瞬间的宽带噪声"""
    if not USE_NOISE_LAYERS:
        return
    n = int(0.012 * SR)
    s0 = int(t * SR)
    n = min(n, len(L) - s0)
    if n <= 1:
        return
    gl = amp * math.sqrt((1 - pan) * 0.5)
    gr = amp * math.sqrt((1 + pan) * 0.5)
    a_cut = 1.0 - math.exp(-2 * math.pi * 3000.0 / SR)
    lp = 0.0
    for i in range(n):
        lp += a_cut * (random.uniform(-1, 1) - lp)
        v = lp * math.exp(-(i / SR) / 0.005)
        L[s0 + i] += v * gl
        R[s0 + i] += v * gr


def bow_noise(L, R, t, dur, amp, pan, seed_shift=0):
    """弦乐弓噪（默认关闭）"""
    if not USE_NOISE_LAYERS:
        return
    n = int(dur * SR)
    s0 = int(t * SR)
    n = min(n, len(L) - s0)
    if n <= 1:
        return
    a = max(1, int(0.5 * SR))
    r = max(1, int(0.6 * SR))
    sus = max(1, n - a - r)
    gl = amp * math.sqrt((1 - pan) * 0.5)
    gr = amp * math.sqrt((1 + pan) * 0.5)
    a_hi = 1.0 - math.exp(-2 * math.pi * 6000.0 / SR)
    a_lo = 1.0 - math.exp(-2 * math.pi * 1200.0 / SR)
    lp_hi = lp_lo = 0.0
    for i in range(n):
        if i < a:
            e = i / a
        elif i < a + sus:
            e = 1.0
        else:
            e = (n - i) / r
        if e <= 0:
            continue
        x = random.uniform(-1, 1)
        lp_hi += a_hi * (x - lp_hi)
        lp_lo += a_lo * (x - lp_lo)
        v = (lp_hi - lp_lo) * e * 2.2
        L[s0 + i] += v * gl
        R[s0 + i] += v * gr


STRINGS = [(1, 1.0, 0), (2, 0.30, 0), (3, 0.09, 0)]


def shaker(L, R, t0, amp=0.09, pan=0.0, seed_shift=0):
    """沙锤/沙蛋：约 25ms 的带通噪声脉冲（3-9kHz）。
    这是**打击乐**不是底噪——短促、离散、跟着节奏走，不会听起来像沙沙的电流声。"""
    n = int(0.025 * SR)
    s0 = int(t0 * SR)
    n = min(n, len(L) - s0)
    if n <= 1:
        return
    gl = amp * math.sqrt((1 - pan) * 0.5)
    gr = amp * math.sqrt((1 + pan) * 0.5)
    a_hi = 1.0 - math.exp(-2 * math.pi * 9000.0 / SR)
    a_lo = 1.0 - math.exp(-2 * math.pi * 3000.0 / SR)
    lp_hi = lp_lo = 0.0
    for i in range(n):
        x = random.uniform(-1, 1)
        lp_hi += a_hi * (x - lp_hi)
        lp_lo += a_lo * (x - lp_lo)
        v = (lp_hi - lp_lo) * math.exp(-(i / SR) / 0.006) * 2.4
        L[s0 + i] += v * gl
        R[s0 + i] += v * gr


def spaced(L, R, t, midi, amp, dur, width, bright=1.0, off=0.010):
    """双轨演奏：同一音符录两遍、左右分开并错开几毫秒
    —— 立体声宽度靠"两个演奏者"，不是靠失谐叠加波"""
    piano(L, R, t, midi, amp, -width, dur=dur, bright=bright)
    piano(L, R, t + off, midi, amp * 0.85, width, dur=dur, bright=bright * 1.06)


def strings(L, R, t, dur, midi, amp, pan):
    """暖弦垫：低次谐波 + 极轻微失谐 + 弓噪"""
    add_voice(L, R, t, dur, m2f(midi), amp, pan, STRINGS, 0.70, 1.30, detune=-3.0)
    add_voice(L, R, t, dur, m2f(midi), amp * 0.85, pan, STRINGS, 0.85, 1.40, detune=+3.5)
    bow_noise(L, R, t, dur * 0.9, amp * 0.045, pan)


BASS_WARM = [(1, 1.0, 1.8), (2, 0.30, 1.2), (3, 0.07, 0.7)]


def bass_warm(L, R, t, dur, midi, amp):
    add_voice(L, R, t, dur, m2f(midi), amp, 0.0, BASS_WARM, 0.018, 0.25)


def celesta(L, R, t, midi, amp, pan):
    """钢片琴：纯音高频（1/2/4/6/8 次分音），用来补 3-9kHz 的明亮感 —— 不用噪声"""
    p = [(1, 1.0, 1.1), (2.01, 0.30, 0.6), (4.02, 0.12, 0.30),
         (6.05, 0.06, 0.18), (8.10, 0.03, 0.12)]
    add_voice(L, R, t, 2.2, m2f(midi), amp, pan, p, 0.003, 0.5)


# ---------------------------------------------------------------- 大厅混响
def hall(L, R, wet=0.36):
    """真实立体声混响：左右用不同梳状延迟时间 → 去相关，宽度上去（参考曲宽度 0.51）"""
    n = len(L)
    mono = [(L[i] + R[i]) * 0.5 for i in range(n)]
    wl = [0.0] * n
    wr = [0.0] * n
    combs_l = ((0.0437, 0.84), (0.0557, 0.82), (0.0671, 0.80), (0.0787, 0.78))
    combs_r = ((0.0461, 0.84), (0.0583, 0.82), (0.0703, 0.80), (0.0821, 0.78))
    for side, out, combs in ((0, wl, combs_l), (1, wr, combs_r)):
        for dt, g in combs:
            d = int(dt * SR)
            for i in range(d, n):
                out[i] += (mono[i] + out[i - d] * g) * 0.25
    for out in (wl, wr):
        for dt, g in ((0.0061, 0.72), (0.0023, 0.72)):
            d = max(1, int(dt * SR))
            buf = [0.0] * d
            ydel = [0.0] * d
            for i in range(n):
                j = i % d
                xd = buf[j]
                yd = ydel[j]
                y = -g * out[i] + xd + g * yd
                buf[j] = out[i]
                ydel[j] = y
                out[i] = y
    # 尾巴整体压暗，但保留到 12kHz
    a = 1.0 - math.exp(-2 * math.pi * 12000.0 / SR)
    for out in (wl, wr):
        lp = 0.0
        for i in range(n):
            lp += a * (out[i] - lp)
            out[i] = lp
    for i in range(n):
        L[i] += wl[i] * wet
        R[i] += wr[i] * wet * 1.08


def highpass_master(L, R, fc=32.0):
    """一阶高通：去掉 32Hz 以下无用低频，避免浑浊"""
    a = 1.0 - math.exp(-2 * math.pi * fc / SR)
    for ch in (L, R):
        lp = 0.0
        for i in range(len(ch)):
            orig = ch[i]
            lp += a * (orig - lp)
            ch[i] = orig - lp


def high_shelf(L, R, fc=2500.0, gain_db=5.0):
    """高频搁架提升：把 2.5kHz 以上抬起来，贴近参考曲的中高频占比"""
    g = 10.0 ** (gain_db / 20.0) - 1.0
    a = 1.0 - math.exp(-2 * math.pi * fc / SR)
    for ch in (L, R):
        lp = 0.0
        for i in range(len(ch)):
            orig = ch[i]
            lp += a * (orig - lp)
            ch[i] = orig + g * (orig - lp)


def low_shelf(L, R, fc=160.0, gain_db=3.0):
    """低频搁架提升：参考曲 40-160Hz 是最强频段"""
    g = 10.0 ** (gain_db / 20.0) - 1.0
    a = 1.0 - math.exp(-2 * math.pi * fc / SR)
    for ch in (L, R):
        lp = 0.0
        for i in range(len(ch)):
            orig = ch[i]
            lp += a * (orig - lp)
            ch[i] = orig + g * lp


def band_boost(L, R, f_lo=300.0, f_hi=1500.0, gain_db=5.0):
    """中频存在感提升（300-1500Hz）：参考曲这一段是平顺的，我这边原来有个凹陷"""
    g = 10.0 ** (gain_db / 20.0) - 1.0
    a_hi = 1.0 - math.exp(-2 * math.pi * f_hi / SR)
    a_lo = 1.0 - math.exp(-2 * math.pi * f_lo / SR)
    for ch in (L, R):
        lp_hi = lp_lo = 0.0
        for i in range(len(ch)):
            orig = ch[i]
            lp_hi += a_hi * (orig - lp_hi)
            lp_lo += a_lo * (orig - lp_lo)
            ch[i] = orig + g * (lp_hi - lp_lo)


def room_ambience(L, R, target_rms=0.006):
    """房间底噪层：真实录音里那层极轻的室内噪声，左右独立 → 明显加宽声场，
    并让整体'像录出来的'而不是'像渲染出来的'。电平约 -44dBFS，几乎听不见但可测。"""
    n = len(L)
    a_env = 1.0 - math.exp(-2 * math.pi * 3.0 / SR)
    a_hi = 1.0 - math.exp(-2 * math.pi * 6000.0 / SR)
    a_lo = 1.0 - math.exp(-2 * math.pi * 400.0 / SR)
    env = [0.0] * n
    e = 0.0
    for i in range(n):
        e += a_env * ((abs(L[i]) + abs(R[i])) * 0.5 - e)
        env[i] = e
    ref = sum(env) / max(1, n)
    if ref <= 1e-9:
        return
    gain = target_rms / max(1e-9, ref * 0.10)
    lp_hi = [0.0, 0.0]
    lp_lo = [0.0, 0.0]
    for i in range(n):
        v = env[i] * gain
        for k, ch in ((0, L), (1, R)):
            x = random.uniform(-1.0, 1.0)
            lp_hi[k] += a_hi * (x - lp_hi[k])
            lp_lo[k] += a_lo * (x - lp_lo[k])
            ch[i] += (lp_hi[k] - lp_lo[k]) * v


def stereo_width(L, R, amount=1.8):
    """中/侧加宽：只放大 side 分量，不引入噪声。
    （实测原曲宽度 0.5、我这边只有 0.19——纯靠摆位到不了，需要侧向增益）"""
    for i in range(len(L)):
        mid = (L[i] + R[i]) * 0.5
        side = (L[i] - R[i]) * 0.5 * amount
        L[i] = mid + side
        R[i] = mid - side


def lowpass_master(L, R, fc=13000.0):
    """零相位一阶低通：正向+反向各滤一次，音色柔和但无相位倾斜"""
    a = 1.0 - math.exp(-2 * math.pi * fc / SR)
    for ch in (L, R):
        y = 0.0
        for i in range(len(ch)):
            y += a * (ch[i] - y)
            ch[i] = y
        y = 0.0
        for i in range(len(ch) - 1, -1, -1):
            y += a * (ch[i] - y)
            ch[i] = y


# ---------------------------------------------------------------- 乐谱 (F 大调)
# 每小节: (贝斯根音, 左手和弦音, 弦垫声部)
CHORDS = [
    (29, [53, 57, 60, 65], [53, 60, 65]),   # F   (F1 贝斯 43.7Hz)
    (26, [50, 53, 57, 62], [50, 57, 62]),   # Dm  (D1 36.7Hz)
    (34, [46, 53, 58, 62], [53, 58, 62]),   # Bb  (Bb1 58.3Hz)
    (24, [48, 52, 55, 60], [52, 55, 60]),   # C   (C1 32.7Hz)
]

# (小节, 拍, 时值拍, midi) —— 长音为主，无八分音符跑动
MELODY = [
    (0, 0.0, 1.0, 69), (0, 1.0, 0.5, 72), (0, 1.5, 0.5, 74), (0, 2.0, 2.0, 77),
    (1, 0.0, 1.0, 76), (1, 1.0, 1.0, 74), (1, 2.0, 2.0, 69),
    (2, 0.0, 1.0, 70), (2, 1.0, 0.5, 74), (2, 1.5, 0.5, 72), (2, 2.0, 2.0, 77),
    (3, 0.0, 1.5, 76), (3, 1.5, 0.5, 74), (3, 2.0, 2.0, 72),
]

# 左手分解和弦型：(八度位移, 和弦音序号) —— 上行，跨两个八度，中频更饱满
LH_PATTERN = [(-12, 0), (0, 1), (0, 2), (0, 3)]


def compose(L, R):
    # 左手钢琴：四分音符分解和弦（上行跨八度）。双轨演奏（±7ms 错位 + 左右分开）
    # → 立体声宽度来自'两个演奏者/两把琴'，不是来自失谐叠加波
    for b, (_, voicing, _) in enumerate(CHORDS):
        for step, (oct_shift, idx) in enumerate(LH_PATTERN):
            m = voicing[idx] + oct_shift
            amp = 0.17 * (1.0 + 0.08 * math.sin(b))
            t = b * BAR_LEN + step * BEAT
            piano(L, R, t, m, amp, -0.78 + 0.10 * step)
            piano(L, R, t + 0.007, m, amp * 0.85, 0.74 - 0.10 * step,
                  bright=0.88)

    # 贝斯：整小节长音（58-117Hz）+ 低八度 sub 层（29-58Hz，对齐参考曲 20-80Hz 占比）
    for b, (root, _, _) in enumerate(CHORDS):
        t = b * BAR_LEN
        bass_warm(L, R, t, BAR_LEN * 0.95, root + 12, 0.40)
        bass_warm(L, R, t + BEAT * 2, BEAT * 1.6, root + 12, 0.20)
        add_voice(L, R, t, BAR_LEN * 0.95, m2f(root), 0.15, 0.0,
                  [(1, 1.0, 2.0)], 0.030, 0.30)

    # 弦垫：整小节持续，第 2 小节起进入，铺满左右
    for b in range(1, NBARS):
        t = b * BAR_LEN
        for k, m in enumerate(CHORDS[b][2]):
            strings(L, R, t, BAR_LEN + 0.5, m, 0.085, -0.75 + 0.75 * k)

    # 主旋律：高八度为主奏（880-1400Hz，抒情曲常见音区）+ 原八度加厚
    # 双轨一左一右 → 宽度来自两个演奏者，不是失谐叠加波
    for (b, beat, dur, m) in MELODY:
        t = b * BAR_LEN + beat * BEAT
        d = dur * BEAT * 0.96
        piano(L, R, t, m + 12, 0.26, -0.80, dur=max(d * 0.85, 1.0))
        piano(L, R, t + 0.012, m + 12, 0.20, 0.82, dur=max(d * 0.85, 1.0),
              bright=0.92)
        piano(L, R, t, m, 0.085, -0.12, dur=max(d * 0.9, 1.1))
        celesta(L, R, t, m + 24, 0.085, 0.55)


# ---------------------------------------------------------------- 母带
def master(L, R, rms_db=RMS_TARGET_DB):
    n = len(L)
    # 关键：先量原始混音峰值并归一化到 0.9，再做软限幅。
    # （之前直接 tanh(混音*1.05)，而混音峰值可能到 3-5，等于硬削波，
    #   会产生刺耳的交调失真 —— 这正是"杂音"的另一半来源）
    pk_raw = max(max(abs(v) for v in L), max(abs(v) for v in R))
    print('      母带前原始峰值 %.3f' % pk_raw)
    if pk_raw > 0:
        g = min(1.0, 0.9 / pk_raw)
        for i in range(n):
            L[i] *= g
            R[i] *= g
    # 轻微软限幅（只压 0.9 以上的极小部分），几乎不产生失真
    drive = 1.10
    k = math.tanh(drive)
    for i in range(n):
        L[i] = math.tanh(L[i] * drive) / k
        R[i] = math.tanh(R[i] * drive) / k
    rms = math.sqrt(sum(l * l + r * r for l, r in zip(L, R)) / (2 * n))
    if rms > 0:
        g = (10.0 ** (rms_db / 20.0)) / rms
        for i in range(n):
            L[i] *= g
            R[i] *= g
    pk = max(max(abs(v) for v in L), max(abs(v) for v in R))
    if pk > 0.96:
        g = 0.96 / pk
        for i in range(n):
            L[i] *= g
            R[i] *= g
    fi = int(0.030 * SR)
    fo = int(0.120 * SR)
    for i in range(fi):
        L[i] *= i / fi
        R[i] *= i / fi
    for i in range(fo):
        k = n - 1 - i
        L[k] *= i / fo
        R[k] *= i / fo


def compose_midi():
    """导出 MIDI：可在 DAW 里挂真实钢琴/弦乐音源，彻底摆脱合成器音色"""
    mel, lh, st, bs_t = [], [], [], []
    for (b, beat, dur, m) in MELODY:
        t = b * 4 + beat
        mel.append((t, dur * 0.95, m + 12, 88))      # 主奏（高八度）
        mel.append((t, dur * 0.9, m, 66))            # 加厚（原八度）
    for b, (_, voicing, _) in enumerate(CHORDS):
        for step, (oct_shift, idx) in enumerate(LH_PATTERN):
            lh.append((b * 4 + step, 0.98, voicing[idx] + oct_shift, 72))
    for b in range(1, NBARS):
        for m in CHORDS[b][2]:
            st.append((b * 4, 4.3, m, 62))
    for b, (root, _, _) in enumerate(CHORDS):
        bs_t.append((b * 4, 4.2, root + 12, 92))
        bs_t.append((b * 4 + 2, 1.6, root + 12, 70))
        bs_t.append((b * 4, 4.2, root, 72))
    return [("Melody", 0, 0, mel), ("PianoLH", 0, 1, lh),
            ("Strings", 48, 2, st), ("Bass", 32, 3, bs_t)]


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    os.makedirs(outdir, exist_ok=True)
    n = int((TOTAL + TAIL) * SR)
    L = [0.0] * n
    R = [0.0] * n
    print('[1/4] 合成：F 大调 %d 小节 %.1fs @%.0fBPM' % (NBARS, TOTAL, BPM))
    t0 = time.time()
    compose(L, R)
    print('      配器完成 %.1fs (钢琴/弦垫/贝斯/钢片琴，无鼓组)' % (time.time() - t0))
    t0 = time.time()
    hall(L, R, 0.55)
    # room_ambience 已关闭：那是纯底噪（用户反馈"很多杂音"）
    highpass_master(L, R, 24.0)
    low_shelf(L, R, 160.0, 1.5)
    band_boost(L, R, 300.0, 1500.0, 5.0)
    high_shelf(L, R, 2500.0, 7.0)
    lowpass_master(L, R, 13000.0)
    print('[2/4] 混响+24Hz高通+搁架(+1.5dB低频/+5dB中频/+5dB高频)+低通 %.1fs'
          % (time.time() - t0))
    print('[3/4] 母带：归一 → 轻软限幅 → RMS %+.0f dBFS' % RMS_TARGET_DB)
    master(L, R)
    pk, rms, dc, dur = bs.stats(L, R)
    wav = os.path.join(outdir, 'garden_warm_test.wav')
    mid = os.path.join(outdir, 'garden_warm_test.mid')
    print('[4/4] 渲染 WAV + MIDI ...')
    bs.write_wav(wav, L, R)
    bs.BPM = BPM                       # 让 MIDI 头写入 76BPM
    bs.write_midi(mid, compose_midi())
    print('      %s  %.1fMB' % (wav, os.path.getsize(wav) / 1048576))
    print('      %s  (可挂真实音源)' % mid)
    print('      时长 %.2fs  峰值 %.3f (%.2f dBFS)  RMS %.4f (%.1f dBFS)  直流 %.6f'
          % (dur, pk, 20 * math.log10(max(pk, 1e-9)), rms,
             20 * math.log10(max(rms, 1e-9)), dc))


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    main()
