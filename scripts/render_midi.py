#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MIDI → 真音源渲染管线（FluidSynth + GeneralUser GS）

流程: MIDI → FluidSynth 渲染(含混响/合唱) → 归一化 → 中侧加宽 → WAV → OGG(q=8)
      → 客观体检（响度/宽度/质心/削波）

用法:
  python render_midi.py summer_seaside.mid [输出名] [--width 1.7] [--rms -17]
"""
import math
import os
import subprocess
import sys

import numpy as np
import soundfile as sf

import to_ogg

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(os.path.dirname(HERE), 'vendor')   # fluidsynth + SF2 音源

# 渲染超时（见 render()）：底 + 按曲长线性。实测 313 秒的歌单轮 29 秒，余量 >100 倍，
# 所以正常曲目**永远碰不到**；它只是防"坏音源/坏 MIDI 让 FluidSynth 永久阻塞"。
RENDER_TIMEOUT_BASE = 60.0
RENDER_TIMEOUT_PER_SEC = 20.0
RENDER_TIMEOUT_MAX = 900.0       # 硬上限 15 分钟：任何"正常的慢"都在里面


def _audio_seconds(mid_path):
    """从 MIDI 估个"不会低估"的时长（秒）——只读文件头 8KB，几毫秒的事。

    不做完整解析（那要引入 MIDI 解析器）：取 SMF header 的 division 与 header 之后的
    最大 delta-time 当**下界**，再乘 1.5 的余量。解析失败就按 300 秒算（足够宽松）。
    """
    try:
        with open(mid_path, 'rb') as f:
            raw = f.read(8192)
        if raw[:4] != b'MThd':
            return 300.0
        division = int.from_bytes(raw[12:14], 'big')
        if division & 0x8000:                      # SMPTE 时间码，少见
            return 300.0
        body = raw[14:]
        big, i = 0, 0
        while i < len(body):
            v, shift = 0, 0
            while i < len(body) and shift < 28:
                b = body[i]
                i += 1
                v = (v << 7) | (b & 0x7F)
                shift += 7
                if not b & 0x80:
                    break
            big = max(big, v)
        ticks = big + 96                           # 留一点尾巴
        secs = ticks / max(1, division) / 2.0 * 1.5     # 按 120BPM 二分音符保守估
        return min(600.0, max(10.0, secs))         # **必须封顶**：见函数 docstring
    except Exception:
        return 300.0


# FluidSynth 设置：房间大一点、混响左右拉开、合唱加宽（这些都在音频线程里完成）
FS_OPTS = [
    '-o', 'synth.reverb.active=1',
    '-o', 'synth.reverb.room-size=0.78',
    '-o', 'synth.reverb.damp=0.35',
    '-o', 'synth.reverb.width=1.0',
    '-o', 'synth.reverb.level=0.80',
    '-o', 'synth.chorus.active=1',
    '-o', 'synth.chorus.level=1.0',
    '-o', 'synth.chorus.depth=5.5',
    '-o', 'synth.gain=1.0',
]


def find_exe():
    for root, _, files in os.walk(VENDOR):
        if 'fluidsynth.exe' in files:
            return os.path.join(root, 'fluidsynth.exe')
    raise RuntimeError('找不到 fluidsynth.exe，先跑 scripts/setup_soundfont.py')


def find_sf2():
    """找音源：`.sf2` / `.sf3`（FluidSynth 都支持）都认 —— 换成更好的音源只要
    把文件丢进 `vendor/` 即可（例如 MuseScore_General.sf3、Arachno.sf2、FluidR3_GM.sf2）。
    多个文件时按名字里的质量提示优先（mscore/arachno/timbres > fluidr3 > generaluser）。"""
    cands = [f for f in os.listdir(VENDOR)
             if f.lower().endswith(('.sf2', '.sf3'))]
    if not cands:
        raise RuntimeError('找不到 .sf2/.sf3 音源，先跑 scripts/setup_soundfont.py')
    pref = ('mscore', 'musescore', 'arachno', 'timbres', 'fluidr3', 'sgm')
    def rank(f):
        low = f.lower()
        return next((i for i, k in enumerate(pref) if k in low), len(pref))
    cands.sort(key=lambda f: (rank(f), f))
    return os.path.join(VENDOR, cands[0])


def trim_tail(x, sr, floor_db=-60.0, keep=1.0, min_tail=3.0):
    """去掉末尾的"死气"（**只在尾巴明显过长时才动**）。

    为什么需要：FluidSynth 离线渲染在 MIDI 结束后**会一直渲染到所有 voice 完全停止**。
    带 loop 的镲片采样（`GeneralUser GS` 的开镲）在重叠音符下会留一个极低电平的长尾 voice，
    于是 4:42 的歌被渲染成 5:03 —— 实测多出来的 **15.7 秒**电平只有 **−72dBFS**
    （听不见，但真实存在，会让所有"时长/统计"失真）。

    `min_tail` 是**保险**：正常混响尾巴只有 2.0~3.7 秒（实测既有 13 首歌），
    短于它一律原样返回 —— 这样既有交付物与 `rehearsal.py` 的短样带**一个字节都不变**。

    `floor_db` 之下算静音；`keep` 是最后一声之后再保留的秒数。"""
    if len(x) == 0:
        return x
    m = np.abs(x).max(axis=1)
    nz = np.where(m > 10.0 ** (floor_db / 20.0))[0]
    if not nz.size:
        return x
    end = min(len(x), int(nz[-1] + keep * sr))
    if (len(x) - nz[-1]) / float(sr) <= min_tail:      # 尾巴不长 → 原样返回
        return x
    return x[:end] if end < len(x) else x


def measure(x, sr, tag):
    mid = x.mean(axis=1)
    side = (x[:, 0] - x[:, 1]) / 2
    m = (x[:, 0] + x[:, 1]) / 2
    rms = 20 * np.log10(max(1e-9, np.sqrt((mid ** 2).mean())))
    pk = float(np.abs(x).max())
    w = float(np.sqrt((side ** 2).mean()) / max(1e-9, np.sqrt((m ** 2).mean())))
    n = 8192
    acc = np.zeros(n // 2 + 1)
    cnt = 0
    for i in range(0, max(1, len(mid) - n), n * 2):
        acc += np.abs(np.fft.rfft(mid[i:i + n] * np.hanning(n)))
        cnt += 1
    acc /= max(1, cnt)
    f = np.fft.rfftfreq(n, 1 / sr)
    cen = float((acc * f).sum() / max(1e-9, acc.sum()))
    print('  %-12s 时长%.1fs 峰值%.3f(%.1fdBFS) RMS%.1fdBFS 宽度%.3f 质心%.0fHz 削波%d'
          % (tag, len(mid) / sr, pk, 20 * np.log10(max(pk, 1e-9)), rms, w, cen,
             int((np.abs(x) >= 0.999).sum())))
    return dict(rms=rms, width=w, centroid=cen, peak=pk)


# ---------------------------------------------------------------- 频域 DSP
# 下面四个滤波器原本都是**逐样本 Python 循环**（一阶递推）。5 分钟的歌有 2760 万样本，
# 实测 高架 12.3s + 三阶高通 50.6s = **63s/轮**，占单轮渲染时间的约 3/4。
# 它们都是 LTI 系统，改成频域实现**数学上完全等价**（零初始状态的响应）：
# 频率响应 H(w) 直接由递推系数写出，比逐样本循环快约两个数量级。
# 等价性有两道守卫：自检 `dsp_fft_equivalent`（与这里独立写的时域递推逐样本比对）
# + `mutation_check.py` 里"把 H 换成错的"那条注入用例。
def _pad_len(n, guard=65536):
    """零填充长度：>= n + guard 的 2 的幂。

    guard 必须长于任一滤波器冲激响应衰减到 double 精度之外所需的样本数：
    最慢的是 fc=38Hz 的高通（极点 0.9946 → 衰减到 1e-16 要 ~6800 样本），
    取 65536 留两个数量级余量 → 循环卷积的"绕回"污染 < 1e-70，
    与零初始状态的时域递推逐位等价（实测最大逐样本差 ~1e-15）。"""
    return 1 << max(8, (n + guard - 1).bit_length())


def _lp_response(sr, fc, nflt):
    """一阶低通 `lp[i] = lp[i-1] + a*(x[i]-lp[i-1])` 的频率响应 a/(1-(1-a)e^{-jw})"""
    a = 1.0 - math.exp(-2 * math.pi * fc / sr)
    w = 2.0 * math.pi * np.fft.rfftfreq(nflt)
    return a / (1.0 - (1.0 - a) * np.exp(-1j * w))


def _freq_filter(x, H, nflt):
    """按声道做频域滤波（逐声道变换以限制峰值内存），原地写回 x"""
    n = len(x)
    for c in range(x.shape[1]):
        x[:, c] = np.fft.irfft(np.fft.rfft(x[:, c], n=nflt) * H, n=nflt)[:n]
    return x


def high_shelf_np(x, sr, fc=3000.0, gain_db=3.0):
    """高频搁架（GM 音源偏暖，抬一点 3kHz 以上补"空气感"）"""
    if not gain_db:                       # 原实现也会原地跑一遍全曲再原样写回
        return x
    g = 10.0 ** (gain_db / 20.0) - 1.0
    nflt = _pad_len(len(x))
    return _freq_filter(x, 1.0 + g * (1.0 - _lp_response(sr, fc, nflt)), nflt)


def low_shelf_np(x, sr, fc=150.0, gain_db=3.0):
    """低频搁架：补 40-160Hz 的力度（参考曲这一段最强），不碰 20-40Hz"""
    if not gain_db:
        return x
    g = 10.0 ** (gain_db / 20.0) - 1.0
    nflt = _pad_len(len(x))
    return _freq_filter(x, 1.0 + g * _lp_response(sr, fc, nflt), nflt)


def highpass_np(x, sr, fc=38.0, order=3):
    """一阶高通：切掉参考曲里并不存在的 20-40Hz 轰鸣（sub 层的低八度会落在这里），
    保留 40-80Hz 的力度主体。

    原实现把同一级联跑了 order 次（`ch[i] = ch[i] - lp`，下一阶吃已经滤过的 ch），
    所以总响应就是 (1-H_lp)**order —— 一次频域乘法即可，不必跑三遍。"""
    nflt = _pad_len(len(x))
    H = 1.0 - _lp_response(sr, fc, nflt)
    return _freq_filter(x, H ** max(1, int(order)), nflt)


def mid_boost_np(x, sr, gain_db=0.0, f_lo=1200.0, f_hi=6000.0):
    """中高频带提升（默认 1.2-6kHz）：GM 音源的中高频常比商业混音薄，
    而 high_shelf 从 3kHz 才开始抬，管不到 1.2-3kHz 这一段。"""
    if not gain_db:
        return x
    g = 10.0 ** (gain_db / 20.0) - 1.0
    nflt = _pad_len(len(x))
    H = 1.0 + g * (_lp_response(sr, f_hi, nflt) - _lp_response(sr, f_lo, nflt))
    return _freq_filter(x, H, nflt)


def apply_chain_np(x, sr, shelf_db=0.0, mid_db=0.0, low_db=0.0, hp_hz=0.0,
                   shelf_fc=3000.0, mid_lo=1200.0, mid_hi=6000.0, low_fc=150.0, hp_order=3):
    """把 shelf / mid / low / hp 四个频域滤波**合并成一次 FFT**。

    为什么能合：这四个滤波在原实现里各自做一次全曲 `rfft`/`irfft`，但**它们都是
    频域乘法**（线性时不变）→ 级联等于响应相乘，一次变换就够。实测省下 ~70% 的
    滤波时间（BGM35 这类 5.5 分钟曲子：6.5s → 1.7s）。
    逐样本校验：与原实现差异 < 1e-12（纯浮点结合律误差）。

    为 0 的项按原实现的语义**直接跳过**（原实现里 `if not gain_db: return x`）。
    """
    nflt = _pad_len(len(x))
    H = None
    if shelf_db:
        g = 10.0 ** (shelf_db / 20.0) - 1.0
        H = 1.0 + g * (1.0 - _lp_response(sr, shelf_fc, nflt))
    if mid_db:
        g = 10.0 ** (mid_db / 20.0) - 1.0
        Hm = 1.0 + g * (_lp_response(sr, mid_hi, nflt) - _lp_response(sr, mid_lo, nflt))
        H = Hm if H is None else H * Hm
    if low_db:
        g = 10.0 ** (low_db / 20.0) - 1.0
        Hl = 1.0 + g * _lp_response(sr, low_fc, nflt)
        H = Hl if H is None else H * Hl
    if hp_hz:
        Hh = (1.0 - _lp_response(sr, hp_hz, nflt)) ** max(1, int(hp_order))
        H = Hh if H is None else H * Hh
    if H is None:
        return x
    return _freq_filter(x, H, nflt)


# 上一次 render() 的实测留痕（给自动调参判断"响度是不是被峰值上限挡住了"）。
# 用侧信道而不是改返回值：`render()` 的 (wav, ogg) 返回值被 make_song / rehearsal /
# mutation_check / 自检多处依赖，改签名等于把它们全拖下水。
LAST = {}

# **响度归一化的口径**（写进 render.json，用来判断"这份配置是哪个口径下调出来的"）。
# `mono` = 按**单声道(mid)** RMS 归一化 —— 与验收口径（metrics/scorecard）同量。
# 2026-09-14 之前用的是**双声道** 2D RMS：加宽把 side 放大 → 单声道响度比目标低最多 ~2.1dB，
# 自动调参因此永远追不上目标（详见 PITFALLS 119）。旧配置重渲染会**变响**（更贴近参考），
# 所以 make_song 见到没有这个标记的 render.json 会提示重跑自动调参。
NORM = 'mono'


def soft_limit(x, drive=1.6):
    """温和软限幅：压掉一点动态余量（crest），让响度能对上参考曲。
    注意 tanh(x*d)/tanh(d) 在 x>1 时会**超过 1.0**（不是真限幅器），
    所以末尾必须硬夹一下，保证不满刻度溢出。"""
    y = np.tanh(x * drive) / np.tanh(drive)
    return np.clip(y, -1.0, 1.0)


def set_width_exact(wav, target_width, ceiling=0.97):
    """把已渲染好的 WAV 的立体声宽度**精确**调到 target_width（无需重渲染）。

    加宽发生在整条链的最后，所以 mid 不变、side 只被乘了一个已知系数：
      side_src = side_out / w_used   →   k = rms(side_src)/rms(mid)
      w_needed = target_width / k
    这样不受"前面 EQ 改动导致比例系数漂移"的影响（线性拟合会因此失准）。"""
    y, sr = sf.read(wav, dtype='float64', always_2d=True)
    mid = (y[:, 0] + y[:, 1]) / 2
    side = (y[:, 0] - y[:, 1]) / 2
    # 从文件本身反推 w_used：先当作 1 处理，用两次测量消掉未知数
    r_mid = np.sqrt((mid ** 2).mean())
    r_side = np.sqrt((side ** 2).mean())
    if r_mid <= 1e-9 or r_side <= 1e-9:
        return None
    k = r_side / r_mid                    # 当前实际比例
    scale = target_width / k              # 需要给 side 乘的倍数
    side2 = side * scale
    out = np.stack([mid + side2, mid - side2], axis=1)
    pk = float(np.abs(out).max())
    if pk > ceiling:
        out *= ceiling / pk
    sf.write(wav, out, sr, subtype='PCM_16')
    return round(float(np.sqrt((side2 ** 2).mean())
                       / np.sqrt((mid ** 2).mean())), 3)


def encode_ogg(out_base):
    """把已渲染好的 `<out_base>.wav` 编码成 OGG（返回 ogg 路径）。

    单独开这个口子，是因为**自动调参要"循环里不编码、定稿只编一次"**：
    5 分钟的歌编码一次 ~8s（含 null test 回读），而中间轮次的 OGG 马上会被下一轮覆盖。
    OGG 的命名规则仍然只在本模块里出现一次（`render` 也走它）。"""
    return to_ogg.convert(out_base + '.wav')


def humanize_midi(src, dst, seed=20260915, time_ms=45.0, vel_amt=6.0, share=0.27):
    """把"精确吸在网格上"的 MIDI 加一点人手抖动（opt-in、**确定性 seed**、**只用于渲染**）。

    依据（2026-09-15 实测，cheerful 的 10 首真实模板）：
      · 最大时序偏差**中位 51.5ms**、**27%** 的音不在 16 分网格上（个别曲目 64% / 98%）；
        反过来说**七成以上的音仍然精确在网格上** —— 真实 MIDI 是"量化 + 少量自由"的混合，
        不是全量化也不是全自由（第一版按"每个音都抖"写，实测非零比例 **93%**，明显过头）。
      · 而我们的交付 MIDI：偏差**恒为 0.0ms、0% 非零** —— 用户听感反馈正是"假/不自然"。
    所以：只有 `share` 比例的音偏离网格（幅度 gauss(0, time_ms/2)，上限 time_ms），
    **鼓轨的比例与幅度都乘 0.35**（真人鼓组更贴拍）。力度按 ±`vel_amt` 正态微扰
    （真实力度 σ 中位 20.9，我们 15.7~18.5）。
    **交付 MIDI 不带抖动**：那些判据（`melody_form_rules` 等）都按 16 分格算，
    所以只在渲染前对副本动手，产物 MIDI 保持网格对齐。
    """
    import random
    import midi_file
    m = midi_file.import_midi(src)
    rng = random.Random(seed)
    bpm = float(m.get('bpm') or 120.0)
    ms_per_beat = 60000.0 / bpm
    n = n_off = 0
    for t in m['tracks']:
        drum = (t.get('channel') == 9)
        lim = time_ms * 0.35 if drum else time_ms
        sh = share * 0.35 if drum else share
        # **偏移按小节给**（同一小节内所有音同向移动）：真人演奏的微时序是**局部一致**的
        # （同一拍/同一小节的音一起略微提前或延后），所以听感是"律动在呼吸"。
        # ⚠ 第一版**逐音独立**随机偏移 → 相邻音互相错开，用户反馈"**不连贯**"。
        per_bar = {}
        for nt in t['notes']:
            bar = int(nt[0] // 4.0)
            if bar not in per_bar:
                per_bar[bar] = (max(-lim, min(lim, rng.gauss(0.0, lim / 2.0)))
                                if rng.random() < sh else 0.0)
            off = per_bar[bar]
            if off:
                nt[0] = max(0.0, nt[0] + off / ms_per_beat)
                n_off += 1
            nt[3] = max(1, min(127, int(round(nt[3] + rng.gauss(0.0, vel_amt / 2.0)))))
            n += 1
    midi_file.export_midi(m, dst, fmt=1)
    return n, n_off


def render(mid_path, out_base, rms_db=-16.9, width=2.2, shelf_db=3.0,
           hp_hz=38.0, low_db=0.0, drive=1.6, mid_db=0.0, keep_raw=False,
           verbose=True, ogg=True, reverb=None, trim=True, humanize=None):
    """out_base 只给名字时，产物写到 MIDI 所在目录（即该曲目的 songs/<曲名>/）

    `ogg=False` 时**不编码 OGG**（仍写 WAV）：自动调参的中间轮次用得上。
    `reverb`：可选 dict 覆盖 FluidSynth 混响（键如 'room-size'/'damp'/'width'/'level'）。
    给 None 时**完全用 FS_OPTS 的默认值**，既有曲目的渲染结果一个字节都不变（opt-in）。
    用途：参考曲的混响尾巴 6.8dB/300ms，我们默认只有 4.6dB —— 差的那截就是"空间感"。"""
    exe, sf2 = find_exe(), find_sf2()
    out_base = os.path.abspath(out_base)
    # **人手抖动**（opt-in）：只对渲染用的副本动手，交付 MIDI 一个字节不动。
    if humanize:
        hum = out_base + '.hum.mid'
        k, koff = humanize_midi(mid_path, hum)
        print('  人手抖动：%d 个音里 %d 个偏离网格（%.0f%%，真实模板 27%%）'
              % (k, koff, 100.0 * koff / max(1, k)))
        mid_path = hum
    LAST.clear()                  # 先清空：调用方只该看到**本次**渲染的实测留痕
    raw = out_base + '.raw.wav'
    opts = []
    skip = {'synth.reverb.%s' % k for k in (reverb or {})}
    it = iter(FS_OPTS)
    for a in it:
        b = next(it)
        if a == '-o' and b.split('=')[0] in skip:
            continue
        opts += [a, b]
    for k, v in (reverb or {}).items():
        opts += ['-o', 'synth.reverb.%s=%s' % (k, v)]
    cmd = [exe, '-ni', '-g', '1.0', '-r', '44100'] + opts + \
          ['-F', raw, sf2, mid_path]
    print('  渲染中 ...')
    # **这是全链路最容易挂住的地方**：FluidSynth 遇到坏音源/坏 MIDI 会一直阻塞，
    # 而它被 make_song 的自动调参反复调用（最多 6 轮）→ 一旦挂住就是整晚不动。
    limit = RENDER_TIMEOUT_BASE + RENDER_TIMEOUT_PER_SEC * _audio_seconds(mid_path)
    limit = min(RENDER_TIMEOUT_MAX, limit)     # 兜住"估出超长"的病态输入
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           text=True, encoding='utf-8', errors='replace', timeout=limit)
    except subprocess.TimeoutExpired:
        raise RuntimeError('fluidsynth 渲染超时（>%.0fs）：%s\n'
                           '  多半是音源/MIDI 坏了，或磁盘写不动' % (limit, os.path.basename(sf2)))
    if r.returncode != 0:
        raise RuntimeError('fluidsynth 失败: %s' % (r.stderr or '')[-400:])
    x, sr = sf.read(raw, dtype='float64', always_2d=True)
    if trim:                                  # 去掉末尾的"死气"（见 trim_tail）
        x = trim_tail(x, sr)
    if verbose:
        measure(x, sr, '原始渲染')
    # 四个频域滤波合并成一次 FFT（数学等价：LTI 级联 = 响应相乘）。
    # 原实现分四次调用、各自做一遍全曲 rfft/irfft，实测占整条渲染链的 1/3。
    x = apply_chain_np(x, sr, shelf_db=shelf_db, mid_db=mid_db, low_db=low_db, hp_hz=hp_hz)

    # 响度目标与峰值上限会互相打架：加宽会抬高峰值，天花板于是把整体拉小、
    # 响度就掉下来了（实测可差 4dB）。真母带的做法是**用限幅压峰值**，而不是整体降增益。
    #
    # ⚠ **归一化的量必须与验收口径同量**（坑 105 的同类）：`metrics.profile`／scorecard／
    # 自检量的都是**单声道(mid)** 的 RMS（`metrics.load` 取 `x.mean(axis=1)`）。
    # 以前这里用**双声道** 2D RMS 归一化，加宽后 side 被放大 → 双声道 RMS 虚高 →
    # 成品单声道响度比目标低（宽度 2.2 实测 −18.99 vs 目标 −16.9，宽度 1.0 只差 0.25）——
    # 于是自动调参**永远追不上响度目标**，每轮重设同一个值、白烧 6 轮渲染。
    base = x
    target = 10 ** (rms_db / 20.0)
    drive_now = max(1.0, drive)
    y = None
    pk = 0.0
    for _ in range(4):
        y = soft_limit(base, drive_now)
        mono = (y[:, 0] + y[:, 1]) / 2
        r = float(np.sqrt((mono ** 2).mean()))
        if r > 0:
            y = y * (target / r)
        mid = (y[:, 0] + y[:, 1]) / 2
        side = (y[:, 0] - y[:, 1]) / 2 * width
        y = np.stack([mid + side, mid - side], axis=1)
        pk = float(np.abs(y).max())
        if pk <= 0.97:
            break
        drive_now *= 1.35                      # 限幅更狠一点，用峰值换响度
    peak_limited = pk > 0.97
    if peak_limited:                           # 到极限还不达标：只能整体降，并说明
        y *= 0.97 / pk
        if verbose:
            print('  注意：峰值仍超上限，响度被压低（内容动态过大）')
    x = y
    mono_out = (x[:, 0] + x[:, 1]) / 2
    got_db = round(float(20 * np.log10(max(1e-9, np.sqrt((mono_out ** 2).mean())))), 2)
    # **留痕给自动调参**：响度是不是被峰值上限挡住的、差多少 —— 调参据此停手并报"到顶"，
    # 而不是每轮重设同一个值把 6 轮渲染烧光（实测就是这么烧的）。
    LAST.update({'target_db': round(float(rms_db), 2), 'achieved_db': got_db,
                 'shortfall_db': round(got_db - float(rms_db), 2),
                 'peak': round(pk, 4), 'peak_limited': bool(peak_limited),
                 'width': round(float(width), 3), 'drive': round(drive_now, 3),
                 'norm': NORM})
    wav = out_base + '.wav'
    sf.write(wav, x, sr, subtype='PCM_16')
    if not keep_raw:
        os.remove(raw)
    if verbose:
        measure(x, sr, '成品')
    if not ogg:
        return wav, None
    return wav, encode_ogg(out_base)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        return 1
    mid = args[0]
    if len(args) > 1:
        base = args[1]
        if not os.path.isabs(base):
            # 只给名字 → 产物跟 MIDI 放一起（songs/<曲名>/）
            base = os.path.join(os.path.dirname(os.path.abspath(mid)), base)
    else:
        base = os.path.join(os.path.dirname(os.path.abspath(mid)),
                            os.path.splitext(os.path.basename(mid))[0])
    # 默认值必须与 render() 的默认一致，否则命令行不给参数时会被这里覆盖
    width = 2.2
    rms = -16.9
    shelf = 3.0
    hp = 38.0
    low = 0.0
    drive = 1.6
    mid_db = 0.0
    if '--width' in sys.argv:
        width = float(sys.argv[sys.argv.index('--width') + 1])
    if '--rms' in sys.argv:
        rms = float(sys.argv[sys.argv.index('--rms') + 1])
    if '--shelf' in sys.argv:
        shelf = float(sys.argv[sys.argv.index('--shelf') + 1])
    if '--hp' in sys.argv:
        hp = float(sys.argv[sys.argv.index('--hp') + 1])
    if '--low' in sys.argv:
        low = float(sys.argv[sys.argv.index('--low') + 1])
    if '--drive' in sys.argv:
        drive = float(sys.argv[sys.argv.index('--drive') + 1])
    if '--mid' in sys.argv:
        mid_db = float(sys.argv[sys.argv.index('--mid') + 1])
    # 人手抖动（opt-in）：不给 = 关；给 `--humanize` = 用默认 25ms；`--humanize 12` 可调
    hum = None
    if '--humanize' in sys.argv:
        i = sys.argv.index('--humanize') + 1
        hum = float(sys.argv[i]) if (i < len(sys.argv) and not sys.argv[i].startswith('--')) else 25.0
    print('== %s → %s (宽度×%.2f, RMS %+.1f, 搁架%+.1f, 中频%+.1f, 高通%.0f, 低调%+.1f, 限幅%.1f%s) =='
          % (mid, base, width, rms, shelf, mid_db, hp, low, drive,
             '' if not hum else ', 抖动±%.0fms' % hum))
    render(mid, base, rms, width, shelf, hp, low, drive, mid_db, humanize=hum)
    return 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    sys.exit(main())
