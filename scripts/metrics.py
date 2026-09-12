#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共用度量内核（被 profile_ref.py / scorecard.py import）

一次算出对齐参考曲所需的全部客观指标：倍频程平衡、质心、宽度、响度、
16 分节奏型、安静段 chroma、结构曲线。**只输出数据，不打印**。

也是**全工具链唯一的音频读取入口**（`read_audio`）：libsndfile 读不了就用自带的
ffmpeg 转码兜底，所以 m4a/mp4/aac/wma/ape/视频容器都能直接喂进来。
"""
import math
import os

import numpy as np
import soundfile as sf

NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
BANDS = [(20, 40), (40, 80), (80, 160), (160, 315), (315, 630), (630, 1250),
         (1250, 2500), (2500, 5000), (5000, 10000), (10000, 18000)]

# ---------------------------------------------------------------- 音频读取（全链路口径）
# libsndfile 只认 26 种容器（WAV/FLAC/OGG/MP3/AIFF/CAF/W64/…）——碰上 m4a / mp4 / aac /
# wma / ape / wv / 视频容器就直接开不了。本机 imageio-ffmpeg 自带 ffmpeg 7.1，
# 所以 libsndfile 失败时用 ffmpeg 转成 32bit float WAV 再读，**上层脚本无需感知**。
AUDIO_EXTS = ('.wav', '.flac', '.ogg', '.oga', '.opus', '.mp3', '.aiff', '.aif',
              '.aifc', '.au', '.snd', '.caf', '.w64', '.rf64', '.mpc', '.wv',
              '.m4a', '.mp4', '.m4b', '.aac', '.wma', '.ape', '.alac', '.webm',
              '.mkv', '.mov', '.flv', '.3gp', '.amr', '.ac3', '.dts', '.mka')

# ffmpeg 转码超时（秒）：正常一首 5 分钟的歌解码只要 1~2 秒，给 300 秒足够任何
# 大文件；**没有这个上限时，坏文件/网络路径会让工具永久挂住**（实测过）。
FFMPEG_TIMEOUT = 300


def _ffmpeg_exe():
    """本机 ffmpeg 路径；没有就返回 None（不抛异常，让调用方给友好提示）"""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        return exe if exe and os.path.exists(exe) else None
    except Exception:
        return None


def read_audio(path, dtype='float32', mono=False):
    """读任意常见音频/视频容器 → (数据, 采样率)。

    ① 先试 soundfile（快、无临时文件）；② 失败则 ffmpeg 转 WAV 到临时目录再读
    （读完即删，不留垃圾）。返回 (N,) 单声道或 (N, 声道) 数组。
    **全工具链只有这一个读入口**——以前每个脚本各自 sf.read，格式支持各自为政。
    """
    if not os.path.exists(path):
        raise SystemExit('找不到音频文件: %s' % path)
    try:
        x, sr = sf.read(path, dtype=dtype, always_2d=True)
        return (x.mean(axis=1) if mono else x), sr
    except Exception as e1:
        exe = _ffmpeg_exe()
        if exe is None:
            raise SystemExit('读不了 %s：%s\n  （libsndfile 不支持该格式，且本机没有 ffmpeg 兜底）'
                             % (os.path.basename(path), e1))
        import shutil
        import subprocess
        import tempfile
        tmpd = tempfile.mkdtemp(prefix='mg_fmt_')
        wav = os.path.join(tmpd, 'a.wav')
        try:
            # -vn 丢视频轨；pcm_f32le 保证精度不丢。
            # **必须有 timeout**：坏文件/网络路径/坏盘会让 ffmpeg 永久阻塞，
            # 而这个函数在分析工具的关键路径上（没超时 = 整条链子挂住）。
            try:
                r = subprocess.run([exe, '-y', '-hide_banner', '-loglevel', 'error',
                                    '-i', path, '-vn', '-f', 'wav', '-c:a', 'pcm_f32le', wav],
                                   capture_output=True, timeout=FFMPEG_TIMEOUT)
            except subprocess.TimeoutExpired:
                raise SystemExit('ffmpeg 解码超时（>%ds）：%s\n'
                                 '  文件可能损坏、或在慢速/网络盘上'
                                 % (FFMPEG_TIMEOUT, os.path.basename(path)))
            if r.returncode != 0 or not os.path.exists(wav):
                msg = (r.stderr or b'').decode('utf-8', 'replace').strip()[-300:]
                raise SystemExit('ffmpeg 也解不开 %s：%s' % (os.path.basename(path), msg))
            x, sr = sf.read(wav, dtype=dtype, always_2d=True)
            return (x.mean(axis=1) if mono else x), sr
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)


def probe_format(path):
    """→ (引擎, 采样率, 声道, 时长秒, 说明)。供 check_audio.py 做格式体检。

    注意：imageio-ffmpeg 只带 `ffmpeg`，**没有 ffprobe**，所以不能走 `-show_streams`
    那套参数（实测直接报 Unrecognized option）。改成解析 ffmpeg 自己的 stderr 摘要行，
    它对任何容器都稳定输出 `Stream #0:0: Audio: aac (LC), 44100 Hz, stereo, fltp`。
    """
    try:
        info = sf.info(path)
        return ('libsndfile', info.samplerate, info.channels, round(info.duration, 2),
                '%s / %s' % (info.format, info.subtype))
    except Exception:
        pass
    exe = _ffmpeg_exe()
    if exe is None:
        return ('打不开', 0, 0, 0.0, 'libsndfile 不支持，且本机没有 ffmpeg')
    import re
    import subprocess
    # 不给输出文件 → ffmpeg 报 "At least one output file must be specified"(exit 1)，
    # 但 stderr 里已经把输入流信息打印全了，所以 returncode 不看、只看文本。
    # 同样必须带 timeout：探测"不是音频的怪文件"时 ffmpeg 会尝试解析很久。
    try:
        r = subprocess.run([exe, '-hide_banner', '-i', path], capture_output=True,
                           timeout=FFMPEG_TIMEOUT)
    except subprocess.TimeoutExpired:
        return ('超时', 0, 0, 0.0, 'ffmpeg 探测超过 %ds（文件损坏或在慢速盘上）'
                % FFMPEG_TIMEOUT)
    txt = (r.stderr or b'').decode('utf-8', 'replace')
    # 实测行形如（[0x1] 与 (und) 段可有可无）：
    #   Stream #0:0[0x1](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, stereo, fltp, 127 kb/s
    m = re.search(r'Audio:\s*([A-Za-z0-9_]+)'
                  r'(?:\s*\(([^)]*)\))?'
                  r'(?:\s*\([^)]*\))?'
                  r'\s*,\s*(\d+)\s*Hz\s*,\s*([^,\n]+)', txt)
    if not m:
        return ('打不开', 0, 0, 0.0, 'ffmpeg 无法解析（可能不是音频）')
    codec, prof, sr, ch = m.group(1), m.group(2), int(m.group(3)), m.group(4).strip()
    d = re.search(r'Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)', txt)
    dur = 0.0
    if d:
        dur = round(int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)), 2)
    chn = 2 if 'stereo' in ch else 1 if 'mono' in ch else 0
    return ('ffmpeg', sr, chn, dur,
            '%s%s / %s' % (codec, ' (%s)' % prof if prof else '', ch))



def load(path):
    """→ (单声道 float32, 采样率, 原始多声道数组)。老调用方靠这个三元组，别改签名。"""
    x, sr = read_audio(path, dtype='float32')
    return x.mean(axis=1), sr, x


def _count_frames(total, n, step):
    """`range(0, total - n, step)` 的窗数（total <= n 时是 0）"""
    return 0 if total <= n else (total - n + step - 1) // step


def _frames(m, n, step, count, chunk=4096):
    """按 step 取 count 个 n 长窗（分块产出，峰值内存受控）。

    **与逐窗 for 循环逐值等价**，只是把 Python 层循环换成一次批量 rfft：
    5 分钟的歌有 5.4 万个窗，逐窗调 numpy 时**函数调用开销**本身就是主要成本
    （实测 env() 两次 3.4s、其中 rfft 只有 2.6s）。"""
    idx = np.arange(n)
    j = 0
    while j < count:
        k = min(chunk, count - j)
        yield m[(j + np.arange(k))[:, None] * step + idx[None, :]]
        j += k


def _avg_spec(m, sr, n=8192):
    win = np.hanning(n)
    step = n * 2
    if len(m) < n:                    # 原来是崩（窗比信号长 → rfft 长度不够、广播失败）
        m = np.pad(m, (0, n - len(m)))
    cnt = max(1, _count_frames(len(m), n, step))
    acc = np.zeros(n // 2 + 1)
    for blk in _frames(m, n, step, cnt):
        acc += np.abs(np.fft.rfft(blk * win, axis=1)).sum(axis=0)
    return acc / cnt, np.fft.rfftfreq(n, 1 / sr)


def _band_env(m, sr, n, step, lo, hi, chunk=4096):
    """每 step 个采样取一窗，量 [lo,hi] 频段的均方根（批量版，值同逐窗推导）"""
    f = np.fft.rfftfreq(n, 1 / sr)
    k = (f >= lo) & (f <= hi)
    win = np.hanning(n)
    cnt = _count_frames(len(m), n, step)
    out = np.empty(cnt)
    pos = 0
    for blk in _frames(m, n, step, cnt, chunk):
        P = np.abs(np.fft.rfft(blk * win, axis=1))
        out[pos:pos + len(P)] = np.sqrt((P[:, k] ** 2).mean(axis=1))
        pos += len(P)
    return out


def octave_bands(m, sr, S=None, f=None):
    """各倍频程**总能量**（相对最强频段 = 0dB）—— 与 analyze_ref2.py 同口径

    S/f 可由调用方传入：`profile()` 里倍频程与质心共用同一次 `_avg_spec`，
    省掉一次全曲窗 FFT（值不变，`_avg_spec` 是确定性的）。"""
    if S is None:
        S, f = _avg_spec(m, sr)
    out = {}
    vals = []
    for lo, hi in BANDS:
        k = (f >= lo) & (f < hi)
        vals.append(float((S[k] ** 2).sum()))
    ref = max(vals) or 1.0
    for (lo, hi), v in zip(BANDS, vals):
        out['%d-%d' % (lo, hi)] = round(10 * math.log10(max(1e-20, v / ref)), 1)
    return out


def centroid(m, sr, S=None, f=None):
    if S is None:
        S, f = _avg_spec(m, sr)
    return round(float((S * f).sum() / max(1e-9, S.sum())))


def width(x):
    mid = (x[:, 0] + x[:, 1]) / 2
    side = (x[:, 0] - x[:, 1]) / 2
    return round(float(np.sqrt((side ** 2).mean())
                       / max(1e-9, np.sqrt((mid ** 2).mean()))), 3)


def rms_db(m):
    return round(float(20 * np.log10(max(1e-9, np.sqrt((m ** 2).mean())))), 1)


def occupancy(env, floor_db=20.0):
    """**连续性**指标：帧能量 env（线性 RMS 序列）里"一直响着"的帧占比（%）。

    为什么放进 metrics（而不是每个工具各写一份）：这是独立于**电平**的一层 ——
    参考曲 10-18k 的占用率 70~88%（"连续的墙"），点状音色只有 25~45%，
    两者的频段**电平可以完全一样**（21_g150_velvet 第一版：10-18k 电平差 0.2dB、占用率差 26 点）。
    阈值取"本带 99 分位 - floor_db"：用分位而不是最大值，避免单个瞬态把门槛抬爆。"""
    e = 20 * np.log10(np.maximum(env, 1e-12))
    if not len(e):
        return 0.0
    return round(100.0 * float((e > np.percentile(e, 99.0) - floor_db).mean()), 1)


def _bars_of(m, sr, bar):
    """按小节长度切开；**文件短于一小节时返回空列表**（调用方必须能处理）"""
    nbar = int(len(m) / (bar * sr))
    return [np.sqrt((m[int(b * bar * sr):int((b + 1) * bar * sr)] ** 2).mean())
            for b in range(nbar)], nbar


def rhythm(m, sr, bpm, loud_bars=16, level=4):
    """16 分格节奏型（每格 = 1/4 拍）：取最响的若干小节做平均模板。
    文件比一小节还短时返回中性值（原来是直接崩：numpy "a cannot be empty"）。"""
    beat = 60.0 / bpm
    bar = 4 * beat
    if len(m) < bar * sr:
        return '·' * (4 * level), '·' * (4 * level)
    hop = 256
    n = 1024

    def env(lo, hi):
        return _band_env(m, sr, n, hop, lo, hi), hop / sr

    lo_e, dt = env(40, 120)
    hi_e, _ = env(7000, 12000)
    bars, nbar = _bars_of(m, sr, bar)
    loud_bars = max(1, min(loud_bars, nbar))
    k = int(np.argmax(np.convolve(bars, np.ones(loud_bars) / loud_bars, 'valid')))
    step = max(1, int(beat / level / dt))
    out = []
    for e in (lo_e, hi_e):
        prof = []
        for i in range(4 * level):
            s = int((k * bar + i * beat / level) / dt)
            prof.append(float(e[s:s + step].max()) if s < len(e) else 0.0)
        mx = max(prof) or 1.0
        out.append(''.join('★' if v / mx > 0.66 else ('◇' if v / mx > 0.33 else '·')
                           for v in prof))
    return out[0], out[1]


def quiet_chroma(m, sr, bar, min_bars=8):
    """最安静段的音级分布（乐器最少 → 最干净，用来判断调式）；短文件用整段。"""
    bars, nbar = _bars_of(m, sr, bar)
    if nbar == 0:
        seg = m
    else:
        q = max(1, min(min_bars, nbar))
        k = int(np.argmin(np.convolve(bars, np.ones(q) / q, 'valid')))
        seg = m[int(k * bar * sr):int((k + q) * bar * sr)]
    n = 8192
    if len(seg) < n:
        seg = np.pad(seg, (0, n - len(seg)))
    S = np.abs(np.fft.rfft(seg[:n] * np.hanning(n)))
    f = np.fft.rfftfreq(n, 1 / sr)
    ch = np.zeros(12)
    for h in range(1, 7):
        kk = (f >= 55 * h) & (f <= 2500 * h)
        for j in np.where(kk)[0]:
            fh = f[j] / h
            if fh < 55:
                continue
            ch[int(round(12 * math.log2(fh / 261.6256))) % 12] += S[j] / h
    ch /= max(1e-9, ch.max())
    order = sorted(range(12), key=lambda i: -ch[i])
    return {NAMES[i]: round(float(ch[i]), 2) for i in order[:6]}


def structure(m, sr, bar, group=8):
    bars, nbar = _bars_of(m, sr, bar)
    out = []
    for i in range(0, nbar - group + 1, group):
        seg = m[int(i * bar * sr):int((i + group) * bar * sr)]
        out.append(round(float(20 * np.log10(max(1e-9, np.sqrt((seg ** 2).mean()))))))
    return out


def onset_density(m, sr):
    """每秒有几个"明确起音"（谱通量峰，mean+2.5σ，无最小间隔）。

    用途：给速度层级定层（见 `detect_bpm` 的 `note_rate`）。**为什么需要它**：
    自相关只说"这个周期存在"，不说"哪个周期是**拍**"—— 一首 72.8BPM 的民谣
    （吉他走八分）与 145.6BPM 的快歌，在自相关上都能解释得通；但"每拍几个音"
    这个物理量能把两种解释分开：民谣 ≈1.5~2.5 个/拍，打击乐型快歌 <1.2 个/拍。
    实测 `(17) ひとりごはんのよる.flac`：2.27 个起音/秒 → 145.6BPM 层 = 0.94 个/拍
    （不合理）而 72.8BPM 层 = 1.87 个/拍（合理）。
    """
    if len(m) < 4096:
        return 0.0
    n, hop = 1024, 256
    frames = np.lib.stride_tricks.sliding_window_view(m, n)[::hop]
    X = np.abs(np.fft.rfft(frames * np.hanning(n), axis=1))
    flux = np.maximum(X[1:] - X[:-1], 0).sum(axis=1)
    thr = flux.mean() + 2.5 * flux.std()
    k = 0
    for i in range(1, len(flux) - 1):
        if flux[i] > thr and flux[i] >= flux[i - 1] and flux[i] > flux[i + 1]:
            k += 1
    return k / max(1e-9, len(m) / sr)


def pick_level(bpm, support, density):
    """**只报告，不改判**（实测教训：这一层不能被自动决定）。

    第一版拿"每拍音数落在 1.0~3.0（八分/十六分）"当判据自动定层，结果：
      · `ひとりごはん` 145.6BPM 层的 0.94 个/拍被改成 72.8 ✔（这次对了）
      · 但 BGM16c 的 150BPM 层是 3.35 个/拍（十六分音型），被判成 75 ✘
      · BGM33 的 75BPM 层是 5.73 个/拍，又被判成 150 ✘
    根因：**"每拍音数"区分不了"慢歌走八分"与"快歌走十六分"**（都是 ~3 个/拍）。
    所以这里只给出信息与建议，最终由**人**用 `--bpm` 钉死一层 —— 工具不猜。
    """
    rate = density * 60 / max(1e-9, bpm) if density > 0 else 0.0
    note = ('自相关取 %.1f BPM（该层 %.2f 起音/拍）' % (bpm, rate)) if rate else \
           '自相关取 %.1f BPM（无密度数据）' % bpm
    if rate and rate < 1.0:
        note += '；**每拍不足一个音**，可能是"最慢层+八分"的解释，可用 --bpm 试 %.1f' % (bpm * 0.5)
    elif rate > 8.0:
        note += '；**每拍 >8 个音**，可能把十六分当成了拍，可用 --bpm 试 %.1f' % (bpm * 2)
    return bpm, note


#: 候选速度的折叠窗口。折叠的目的是把八度相关的候选并到**同一层**比较，
#: 它**不是**"速度不可能超出这个范围"的断言。原来窗口外的曲子被折上来之后不留任何痕迹 ——
#: 实测 55BPM 的底鼓+踩镲被报成 110.5（+100.9%），属于静默给错答案。
#: 现在窗口本身不动（读数行为零变更），但**窗口外同样成立的层级会显式报出来**（见 detect_bpm）。
BPM_FOLD = (60.0, 180.0)

#: 窗口外层级要多高的支持度才值得报（相对于本层）。1.0 = 只报更强的那层。
WINDOW_ALT_RATIO = 0.85


def _fold_bpm(bpm):
    """把候选速度按八度折进 `BPM_FOLD` 窗口（独立成函数是为了能被变异测试注入）。"""
    lo, hi = BPM_FOLD
    while bpm < lo:
        bpm *= 2
    while bpm > hi:
        bpm /= 2
    return bpm


def detect_bpm(m, sr):
    """低频脉冲周期性定速度（比起音包络稳）。

    返回 `(bpm, periodicity, info)`。

    **关于半/倍速（坑 66）**：60/75/80 与 120/150/160 的自相关峰位是八度关系，两者都是
    "对同一个音频成立的读法"——BGM19.ogg 实测 ac(80BPM 峰)=0.197 / ac(151BPM 峰)=0.182，
    本管线自己渲染的 150BPM 曲目反而是 ac(75BPM)=0.448 > ac(150BPM)=0.165。**谁高谁低
    由配器决定（低音落在每拍还是每半拍），不能当"速度"的唯一真相**。

    所以这里：① 报**支持度最高**的那个；② 若低八度邻级支持度 ≥85%，在 info 里给出
    `low_level` —— 让上层（脚手架填 BPM / 成绩单比速度）**显式选一层并写进画像**，
    而不是两边各选一层、（旧代码就这样）拿 150 的谱面对 75 的成品说"速度差 100%"。
    """
    hop = 256
    n = 1024
    e = _band_env(m, sr, n, hop, 40, 130)
    e = np.maximum(np.diff(e), 0)
    e -= e.mean()
    fps = sr / hop
    # 自相关只算用得到的前几个 lag：原来 `np.correlate(e, e, 'full')` 把
    # 5.4万 × 5.4万 对全算了一遍（5 分钟的歌实测 **19.1s**，占 detect_bpm 的 93%），
    # 而下面只取 lag < 2.6*fps、最多到它的 4 倍 → 前 4*int(2.6*fps)+1 个逐位相同。
    need = min(len(e), 8 * int(2.6 * fps) + 1)   # ×8：低八度层级要多看两档倍数
    ac = np.array([float(np.dot(e[:len(e) - lag], e[lag:])) for lag in range(need)])
    ac /= max(1e-9, ac[0])

    def score_of(bpm):
        """该速度在自相关上的支持度（周期自身 + 各整数倍周期，权重递减）"""
        lag = int(round(60.0 / bpm * fps))
        if lag < 1 or lag >= len(ac):
            return -9.0
        s = ac[lag]
        for mult in (2, 3, 4):
            j = lag * mult
            if j < len(ac):
                s += ac[j] / (mult + 1.0)
        return s

    cands = set()
    for lag in range(int(0.35 * fps), min(int(2.6 * fps), len(ac))):
        bpm = 60.0 / (lag / fps)
        cands.add(round(_fold_bpm(bpm), 1))
    ranked = sorted(((score_of(b), b) for b in cands), reverse=True)
    best_s, best_bpm = (ranked[0] if ranked else (-9.0, 120.0))

    info = {}
    # 低八度邻级：best/2；它的两倍正好回到 best，所以上层只认"选了哪一层"
    low = best_bpm / 2
    s_low = float(score_of(low))
    info['level_hi'] = round(float(best_bpm), 1)
    info['level_hi_score'] = round(float(best_s), 3)
    info['level_lo'] = round(low, 1)
    info['level_lo_score'] = round(s_low, 3)
    if s_low >= 0.85 * best_s:
        info['low_level'] = round(low, 1)      # 低八度层级几乎同样成立
    if best_bpm < 100 and score_of(best_bpm * 2) >= 0.85 * best_s:
        info['high_level'] = round(best_bpm * 2, 1)

    # 自相关说"周期存在"，不保证"这就是拍" → 用**起音密度**在八度层级里定层（见 pick_level）
    dens = onset_density(m, sr)
    chosen, why = pick_level(best_bpm, float(best_s), dens)
    info['onset_density'] = round(dens, 2)
    info['level_note'] = why
    if abs(chosen - best_bpm) > 1e-9:
        info['level_raw'] = round(float(best_bpm), 1)
        best_bpm, best_s = chosen, float(score_of(chosen))
        info['level_hi'] = round(float(best_bpm), 1)
        info['level_hi_score'] = round(float(best_s), 3)
        info['level_lo'] = round(best_bpm / 2, 1)
        info['level_lo_score'] = round(float(score_of(best_bpm / 2)), 3)

    # 窗口外层级：折叠窗口 [60,180] 之外的曲子会被折上来（55→110、230→115），
    # 而折叠本身不留痕迹。这里把"窗口外同样成立、甚至更成立的层级"显式报出来，
    # 让上层（check_audio / profile_ref / 成绩单）能提示**用 --bpm 钉死**，
    # 而不是让人拿着一个折半/加倍的值去写画像。判据与 low_level 同源（支持度比值）。
    alts = [(round(float(score_of(v)), 3), round(v, 1))
            for v in (best_bpm / 4.0, best_bpm / 3.0, best_bpm / 2.0,
                      best_bpm * 2.0, best_bpm * 3.0, best_bpm * 4.0)
            if 40.0 <= v <= 240.0 and not (BPM_FOLD[0] <= v <= BPM_FOLD[1])]
    if alts:
        s_alt, v_alt = max(alts)
        if s_alt >= WINDOW_ALT_RATIO * best_s:
            info['window_alt'] = v_alt
            info['window_alt_score'] = s_alt
            info['level_note'] += ('；**窗口外层级同样成立：%.1f BPM（支持度 %.3f vs %.3f）**'
                                   '→ 超出自动定层窗口 %.0f–%.0f，请用 `--bpm %.1f` 核对'
                                   % (v_alt, s_alt, best_s, BPM_FOLD[0], BPM_FOLD[1], v_alt))
    return round(float(best_bpm), 1), round(float(best_s), 3), info


def character_of(bands):
    """从频段分布判定"人声主导 / 器乐"，并给出参与对标的频段。
    两件事都**只依赖 bands**，所以老画像可以就地补齐，不必重扒音频。"""
    vocal = (bands.get('20-40', 0) < -45 and bands.get('315-630', -99) >= -1.0
             and bands.get('10000-18000', 0) < -30)
    if vocal:
        align = [k for k in bands if 160 <= int(k.split('-')[0]) < 10000]
    else:
        align = [k for k in bands if k != '20-40']
    return ('vocal_forward' if vocal else 'instrumental'), align


# ---------------------------------------------------------------- 判据（唯一口径）
def aligned_bands(ref):
    """该参考曲参与对标/调参的频段。

    **全工具链唯一的口径来源**：以前 `make_song` 和 `scorecard` 各写了一份同样的
    表达式，两处一旦漂移就会出现"一边说已跳过 20-40、一边建议动 20-40"这种自相矛盾。"""
    return ref.get('align_bands') or [k for k in ref['bands']
                                      if not k.startswith('20-40')]


# 频段分组：调参与成绩单**共用**（曾经两处对 "mid" 的定义都不一样：
# make_song 指 1250-5000，scorecard 指 315-1250 —— 同一句话在两个工具里含义不同）
GROUPS = (('sub', ('20-40',)),
          ('low', ('40-80', '80-160')),
          ('presence', ('315-630', '630-1250')),
          ('mid', ('1250-2500', '2500-5000')),
          ('top', ('5000-10000', '10000-18000')))

TOL = 1.5          # 单组容差(dB)：调参与成绩单共用同一个数，不再各用各的


def judge_gaps(mine, ref):
    """各组偏差（组内只统计参与对标的频段）；组里没有可对标频段 → None。

    返回 {'sub','low','presence','mid','top','width','rms'}，单位为 dB /
    宽度为差值。**调参、成绩单、编配提示都从这里取数**，避免三套口径。"""
    al = set(aligned_bands(ref))
    d = {k: mine['bands'][k] - ref['bands'][k]
         for k in ref['bands'] if k in mine.get('bands', {})}
    out = {}
    for name, keys in GROUPS:
        vals = [d[k] for k in keys if k in al and k in d]
        out[name] = (sum(vals) / len(vals)) if vals else None
    out['width'] = mine['width'] - ref['width']
    out['rms'] = mine['rms_db'] - ref['rms_db']
    return out


def profile(path, bpm=None, name=None):
    m, sr, x = load(path)
    tempo = None
    if bpm is None:
        bpm, periodicity, tempo = detect_bpm(m, sr)
    else:
        periodicity = None
    bar = 4 * 60.0 / bpm
    lo, hi = rhythm(m, sr, bpm)
    spec, freqs = _avg_spec(m, sr)          # 只算一次：倍频程与质心共用
    bands = octave_bands(m, sr, spec, freqs)
    char, align = character_of(bands)
    p = {
        'name': name or path.split('\\')[-1].rsplit('.', 1)[0],
        'file': path,
        'duration': round(len(m) / sr, 1),
        'bpm': bpm,
        'bar': round(bar, 3),
        'rms_db': rms_db(m),
        'width': width(x),
        'centroid': centroid(m, sr, spec, freqs),
        'bands': bands,
        'character': char,
        'align_bands': align,
        'rhythm_low': lo,
        'rhythm_high': hi,
        'quiet_chroma': quiet_chroma(m, sr, bar),
        'structure': structure(m, sr, bar),
    }
    if periodicity is not None:
        p['periodicity'] = periodicity
    if tempo:
        # 半/倍速层级（坑 66）：后续命令（new_song 填 BPM / scorecard 比速度）读这两个
        # 字段，**显式选一层**，避免"两边各选一层 → 速度差 100% 假报警"
        for k in ('low_level', 'high_level'):
            if tempo.get(k):
                p[k] = tempo[k]
        p['level_scores'] = {'hi': tempo.get('level_hi_score'),
                             'lo': tempo.get('level_lo_score')}
    return p
