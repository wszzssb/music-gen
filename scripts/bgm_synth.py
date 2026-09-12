#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
程序化 BGM 生成器（纯标准库，零第三方依赖）

作曲 : D 大调 8 小节循环，和声进行 D - A - Bm - G - D - A - G - A
配器 : 主旋律(拨弦/钢琴感) + 和弦铺底(Pad) + 贝斯 + 高音铃音 + 轻打击
合成 : 加法合成（分音 + 指数衰减包络）+ 立体声乒乓延迟 + Schroeder 混响
输出 : WAV (16bit 44.1k 立体声，可循环) + MIDI (可丢进 DAW 继续改)

用法 : python bgm_synth.py [输出目录]
"""
import math
import os
import random
import struct
import sys
import time
import wave

SR = 44100
BPM = 92.0
BEAT = 60.0 / BPM
BAR_LEN = 4 * BEAT
NBARS = 8
TOTAL = NBARS * BAR_LEN
TAIL = 2.4                      # 混响/延迟尾巴

random.seed(20240607)

# ---------------------------------------------------------------- 音高工具
def m2f(m):
    return 440.0 * (2.0 ** ((m - 69) / 12.0))


# ---------------------------------------------------------------- 加法合成
def add_voice(L, R, t0, dur, freq, amp, pan, partials,
              attack=0.006, release=0.25, detune=0.0):
    """在 t0 秒处叠加一个音色。partials = [(倍频, 振幅, 衰减时间常数s, 0=不衰减)]"""
    n = int(dur * SR)
    s0 = int(t0 * SR)
    if s0 < 0:
        n += s0
        s0 = 0
    if n <= 0 or s0 >= len(L):
        return
    n = min(n, len(L) - s0)
    if n <= 4:
        return

    a = max(1, int(attack * SR))
    r = max(1, int(release * SR))
    sus = n - a - r
    if sus < 1:                                  # 短音：等比压缩包络段
        a = max(1, n // 3)
        r = max(1, n // 3)
        sus = max(1, n - a - r)

    gl = amp * math.sqrt(max(0.0, (1.0 - pan)) * 0.5)
    gr = amp * math.sqrt(max(0.0, (1.0 + pan)) * 0.5)
    tp = 2.0 * math.pi
    osc = []
    for ratio, pamp, pdec in partials:
        f = freq * ratio * (2.0 ** (detune / 1200.0))
        if f <= 0.0 or f >= SR * 0.45:
            continue
        dec = math.exp(-1.0 / (pdec * SR)) if pdec > 0 else 1.0
        osc.append([0.0, tp * f / SR, pamp, 1.0, dec])
    if not osc:
        return

    sin = math.sin
    for i in range(n):
        if i < a:
            e = i / a
        elif i < a + sus:
            e = 1.0
        else:
            e = (n - i) / r
        if e <= 0.0:
            continue
        s = 0.0
        for o in osc:
            s += sin(o[0]) * o[2] * o[3]
            o[0] += o[1]
            o[3] *= o[4]
        v = e * s
        L[s0 + i] += v * gl
        R[s0 + i] += v * gr


# 音色模板 ---------------------------------------------------------------
PIANO = [(1, 1.0, 1.10), (2, 0.42, 0.55), (3, 0.18, 0.32), (4, 0.08, 0.20)]
BELL = [(1, 1.0, 1.60), (2.76, 0.46, 0.70), (5.40, 0.20, 0.34)]
PAD = [(1, 1.0, 0), (2, 0.46, 0), (3, 0.20, 0), (4, 0.09, 0)]
BASS = [(1, 1.0, 0), (2, 0.34, 0), (3, 0.10, 0)]


def pluck(L, R, t, dur, midi, amp, pan):
    add_voice(L, R, t, dur, m2f(midi), amp, pan, PIANO, 0.005, 0.30)


def bell(L, R, t, dur, midi, amp, pan):
    add_voice(L, R, t, dur, m2f(midi), amp, pan, BELL, 0.002, 0.55)


def pad_note(L, R, t, dur, midi, amp, pan):
    add_voice(L, R, t, dur, m2f(midi), amp, pan, PAD, 0.45, 0.90, detune=-6.0)
    add_voice(L, R, t, dur, m2f(midi), amp * 0.8, pan, PAD, 0.60, 1.00, detune=+7.0)


def bass_note(L, R, t, dur, midi, amp):
    add_voice(L, R, t, dur, m2f(midi), amp, 0.0, BASS, 0.010, 0.10)


# 打击 -------------------------------------------------------------------
def kick(L, R, t0, amp=0.55):
    n = int(0.34 * SR)
    s0 = int(t0 * SR)
    n = min(n, len(L) - s0)
    ph = 0.0
    for i in range(max(0, n)):
        t = i / SR
        f = 47.0 + 95.0 * math.exp(-t / 0.032)
        ph += 2.0 * math.pi * f / SR
        v = math.sin(ph) * math.exp(-t / 0.095) * amp
        L[s0 + i] += v
        R[s0 + i] += v


def hat(L, R, t0, amp=0.10, dur=0.06, pan=0.35):
    n = int(dur * SR)
    s0 = int(t0 * SR)
    n = min(n, len(L) - s0)
    prev = 0.0
    gl = amp * math.sqrt((1 - pan) * 0.5)
    gr = amp * math.sqrt((1 + pan) * 0.5)
    for i in range(max(0, n)):
        t = i / SR
        x = random.uniform(-1.0, 1.0)
        hp = x - prev                      # 一阶差分 ≈ 高通，得"沙沙"声
        prev = x
        v = hp * math.exp(-t / 0.018)
        L[s0 + i] += v * gl
        R[s0 + i] += v * gr


def snare(L, R, t0, amp=0.22):
    n = int(0.20 * SR)
    s0 = int(t0 * SR)
    n = min(n, len(L) - s0)
    ph = 0.0
    prev = 0.0
    for i in range(max(0, n)):
        t = i / SR
        x = random.uniform(-1.0, 1.0)
        noise = (x - prev * 0.6)
        prev = x
        ph += 2.0 * math.pi * 195.0 / SR
        v = (noise * math.exp(-t / 0.075) * 0.8 +
             math.sin(ph) * math.exp(-t / 0.045) * 0.5) * amp
        L[s0 + i] += v
        R[s0 + i] += v


# ---------------------------------------------------------------- 效果器
def ping_pong(L, R, dt, fb, mix=1.0):
    d = int(dt * SR)
    n = len(L)
    for i in range(d, n):
        L[i] += R[i - d] * fb * mix
        R[i] += L[i - d] * fb * mix


def reverb(L, R, wet=0.30):
    n = len(L)
    mono = [(L[i] + R[i]) * 0.5 for i in range(n)]
    out = [0.0] * n
    for dt, g in ((0.0297, 0.79), (0.0371, 0.77), (0.0411, 0.75), (0.0437, 0.73)):
        d = int(dt * SR)
        for i in range(d, n):
            out[i] += (mono[i] + out[i - d] * g) * 0.25
    for dt, g in ((0.0050, 0.70), (0.0017, 0.70)):
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
    for i in range(n):
        L[i] += out[i] * wet
        R[i] += out[i] * wet * 0.92


# ---------------------------------------------------------------- 乐谱
# (小节, 拍, 时值拍, midi)
MELODY = [
    (0, 0.0, 1.5, 78), (0, 1.5, 0.5, 76), (0, 2.0, 2.0, 74),
    (1, 0.0, 1.0, 73), (1, 1.0, 1.0, 76), (1, 2.0, 1.0, 78), (1, 3.0, 1.0, 76),
    (2, 0.0, 1.0, 74), (2, 1.0, 1.0, 78), (2, 2.0, 1.0, 71), (2, 3.0, 1.0, 74),
    (3, 0.0, 1.5, 76), (3, 1.5, 0.5, 74), (3, 2.0, 2.0, 71),
    (4, 0.0, 1.0, 78), (4, 1.0, 1.0, 81), (4, 2.0, 0.5, 83), (4, 2.5, 0.5, 81),
    (4, 3.0, 1.0, 78),
    (5, 0.0, 1.0, 76), (5, 1.0, 1.0, 73), (5, 2.0, 2.0, 76),
    (6, 0.0, 1.0, 74), (6, 1.0, 1.0, 76), (6, 2.0, 1.0, 74), (6, 3.0, 1.0, 71),
    (7, 0.0, 1.5, 73), (7, 1.5, 0.5, 71), (7, 2.0, 2.0, 69),
]

# 每小节: (贝斯根音, pad 声部, 琶音音阶)
CHORDS = [
    (38, [50, 57, 62], [62, 66, 69, 74]),   # D
    (33, [45, 52, 57], [57, 61, 64, 69]),   # A
    (35, [47, 54, 59], [59, 62, 66, 71]),   # Bm
    (31, [43, 50, 55], [55, 59, 62, 67]),   # G
    (38, [50, 57, 62], [62, 66, 69, 74]),
    (33, [45, 52, 57], [57, 61, 64, 69]),
    (31, [43, 50, 55], [55, 59, 62, 67]),
    (33, [45, 52, 57], [57, 61, 64, 69]),
]


def compose(L, R):
    # Pad：每小节一个和弦，相互重叠一点更连贯
    for b, (_, voicing, _) in enumerate(CHORDS):
        t = b * BAR_LEN
        for k, m in enumerate(voicing):
            pan = -0.55 + 1.1 * (k / max(1, len(voicing) - 1))
            pad_note(L, R, t, BAR_LEN + 0.35, m, 0.115, pan)

    # 贝斯：根音(长音) + 第3拍低八度点缀
    for b, (root, _, _) in enumerate(CHORDS):
        t = b * BAR_LEN
        bass_note(L, R, t, BEAT * 2.1, root, 0.30)
        bass_note(L, R, t + BEAT * 2.5, BEAT * 0.9, root, 0.20)

    # 主旋律（第 5 小节起加高八度铃音齐奏）
    for (b, beat, dur, m) in MELODY:
        t = b * BAR_LEN + beat * BEAT
        d = dur * BEAT * 0.98
        pluck(L, R, t, d, m, 0.26, 0.05)
        if b >= 4:
            bell(L, R, t, d * 0.9, m + 12, 0.055, 0.25)
        if b >= 2:                                  # 低八度垫音，厚度
            pluck(L, R, t, d * 0.8, m - 12, 0.07, -0.30)

    # 琶音（第 3 小节起，八分音符上行，安静铺底）
    for b in range(2, NBARS):
        _, _, arp = CHORDS[b]
        for step in range(8):
            m = arp[step % len(arp)] + (12 if step >= 4 else 0)
            t = b * BAR_LEN + step * BEAT * 0.5
            pluck(L, R, t, BEAT * 0.42, m, 0.055, -0.45 + 0.9 * (step / 7.0))

    # 轻打击：3-6 小节铺底，7 小节小过门
    for b in range(2, NBARS):
        t = b * BAR_LEN
        kick(L, R, t, 0.42)
        kick(L, R, t + BEAT * 2, 0.34)
        for step in (1, 3, 5, 7):
            hat(L, R, t + step * BEAT * 0.5, 0.075 + 0.02 * (step % 2))
        if b >= 4:
            snare(L, R, t + BEAT * 2, 0.17)
        if b == 6:
            for k in range(4):                      # 过门
                hat(L, R, t + BEAT * 3 + k * BEAT * 0.25, 0.10, 0.05, 0.5)
                snare(L, R, t + BEAT * 3 + k * BEAT * 0.25, 0.07)


# ---------------------------------------------------------------- 母带
def master(L, R, peak_db=-1.0):
    n = len(L)
    # 软限幅 + 归一化
    target = 10.0 ** (peak_db / 20.0)
    pk = 0.0
    for i in range(n):
        l = math.tanh(L[i] * 1.15)
        r = math.tanh(R[i] * 1.15)
        L[i] = l
        R[i] = r
        if abs(l) > pk:
            pk = abs(l)
        if abs(r) > pk:
            pk = abs(r)
    if pk > 0:
        g = target / pk
        for i in range(n):
            L[i] *= g
            R[i] *= g
    # 首尾淡入淡出（便于无缝循环 / 避免爆音）
    fi = int(0.012 * SR)
    fo = int(0.060 * SR)
    for i in range(fi):
        L[i] *= i / fi
        R[i] *= i / fi
    for i in range(fo):
        k = (n - 1 - i)
        L[k] *= i / fo
        R[k] *= i / fo


def stats(L, R):
    n = len(L)
    pk = rms = dc = 0.0
    for i in range(n):
        l = L[i]
        r = R[i]
        pk = max(pk, abs(l), abs(r))
        rms += l * l + r * r
        dc += l + r
    rms = math.sqrt(rms / (2 * n))
    dc /= (2 * n)
    return pk, rms, dc, n / SR


def write_wav(path, L, R):
    frames = bytearray()
    for i in range(len(L)):
        l = int(max(-1.0, min(1.0, L[i])) * 32767)
        r = int(max(-1.0, min(1.0, R[i])) * 32767)
        frames += struct.pack('<hh', l, r)
    with wave.open(path, 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(frames))


# ---------------------------------------------------------------- MIDI 导出
def _vlq(n):
    out = bytearray([n & 0x7F])
    n >>= 7
    while n:
        out.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out)


def write_midi(path, tracks, ppq=480):
    """tracks: (name, program, channel, events[, ccs])
    events = [(起始拍, 时值拍, 音高, 力度)]
    ccs    = [(拍, CC号, 值)]  —— 用来写声像 CC10 / 音量 CC7 等"""
    chunks = []
    for idx, track in enumerate(tracks):
        name, program, channel, events = track[:4]
        ccs = track[4] if len(track) > 4 else []
        ev = []
        # 轨名放在 tick 0（规范做法：必须在 End-of-Track 之前，否则 DAW 读不到）
        nb = name.encode()
        ev.append((0, b'\xff\x03' + _vlq(len(nb)) + nb))
        if idx == 0:
            micro = int(60_000_000 / BPM)
            ev.append((0, b'\xff\x51\x03' + micro.to_bytes(3, 'big')))
            ev.append((0, b'\xff\x58\x04\x04\x02\x18\x08'))
        if program is not None:
            ev.append((0, bytes([0xC0 | channel, program])))
        for (tick, cc, val) in ccs:
            ev.append((int(tick * ppq), bytes([0xB0 | channel, cc & 0x7F,
                                               max(0, min(127, int(val)))])))
        for (start, dur, note, vel) in events:
            # 越界数据必须报错，不能靠 & 0x7F 静默回绕（128 会变成 0，音高悄悄错掉）
            if not 0 <= note <= 127:
                raise ValueError('音高越界: %s（轨 %s）' % (note, name))
            if not 1 <= vel <= 127:
                raise ValueError('力度越界: %s（轨 %s）' % (vel, name))
            if start < 0 or dur <= 0:
                raise ValueError('时值异常: start=%s dur=%s（轨 %s）' % (start, dur, name))
            ev.append((int(start * ppq), bytes([0x90 | channel, note & 0x7F, vel])))
            ev.append((int((start + dur) * ppq),
                       bytes([0x80 | channel, note & 0x7F, 0])))
        ev.sort(key=lambda x: x[0])
        data = bytearray()
        last = 0
        for tick, payload in ev:
            data += _vlq(max(0, tick - last))
            data += payload
            last = tick
        data += _vlq(0) + b'\xff\x2f\x00'
        chunks.append(b'MTrk' + struct.pack('>I', len(data)) + bytes(data))
    header = b'MThd' + struct.pack('>IHHH', 6, 1, len(chunks), ppq)
    with open(path, 'wb') as f:
        f.write(header + b''.join(chunks))


def compose_midi():
    mel, padt, bsst, drm = [], [], [], []
    for (b, beat, dur, m) in MELODY:
        mel.append((b * 4 + beat, dur * 0.98, m, 92))
        if b >= 2:
            mel.append((b * 4 + beat, dur * 0.8, m - 12, 60))
    for b in range(2, NBARS):
        for step in range(8):
            arp = CHORDS[b][2]
            mel.append((b * 4 + step * 0.5, 0.42,
                        arp[step % len(arp)] + (12 if step >= 4 else 0), 52))
    for b, (_, voicing, _) in enumerate(CHORDS):
        for m in voicing:
            padt.append((b * 4, 4.2, m, 70))
    for b, (root, _, _) in enumerate(CHORDS):
        bsst.append((b * 4, 2.1, root, 100))
        bsst.append((b * 4 + 2.5, 0.9, root, 78))
    for b in range(2, NBARS):
        drm.append((b * 4, 0.1, 36, 100))
        drm.append((b * 4 + 2, 0.1, 36, 82))
        for step in (1, 3, 5, 7):
            drm.append((b * 4 + step * 0.5, 0.1, 42, 60))
        if b >= 4:
            drm.append((b * 4 + 2, 0.1, 38, 78))
    return [("Melody", 0, 0, mel), ("Pad", 89, 1, padt),
            ("Bass", 33, 2, bsst), ("Drums", None, 9, drm)]


# ---------------------------------------------------------------- main
def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    os.makedirs(outdir, exist_ok=True)
    n = int((TOTAL + TAIL) * SR)
    print('[1/5] 分配缓冲 %.1fs @ %dHz' % (n / SR, SR))
    L = [0.0] * n
    R = [0.0] * n

    t0 = time.time()
    print('[2/5] 合成配器 (旋律/铺底/贝斯/琶音/打击) ...')
    compose(L, R)
    print('      合成耗时 %.1fs' % (time.time() - t0))

    t0 = time.time()
    print('[3/5] 效果器 (乒乓延迟 + Schroeder 混响) ...')
    ping_pong(L, R, BEAT * 0.5, 0.30)      # 八分音符延迟
    ping_pong(L, R, BEAT * 0.75, 0.20)     # 附点延迟
    reverb(L, R, 0.30)
    print('      效果耗时 %.1fs' % (time.time() - t0))

    print('[4/5] 母带 (软限幅/归一/淡入淡出) ...')
    master(L, R, -1.0)
    pk, rms, dc, dur = stats(L, R)

    wav = os.path.join(outdir, 'pure_garden_theme.wav')
    mid = os.path.join(outdir, 'pure_garden_theme.mid')
    write_wav(wav, L, R)
    write_midi(mid, compose_midi())

    print('[5/5] 完成')
    print('      WAV : %s  (%.1f MB)' % (wav, os.path.getsize(wav) / 1048576))
    print('      MIDI: %s' % mid)
    print('      时长 %.2fs  峰值 %.3f (%.2f dBFS)  RMS %.4f  直流 %.5f'
          % (dur, pk, 20 * math.log10(max(pk, 1e-9)), rms, dc))
    return 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    sys.exit(main())
