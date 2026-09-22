#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""loop_export.py —— 把线性 BGM 导出成**可无缝循环**的素材（含客观判据）。

═══ 为什么做这个（这是"音频大模型结构上给不了"的那一块）═══
端到端模型（Music 3 / ACE-Step）产出的是**一首成品歌**：没有小节、没有循环点、
不能只换一轨。而 BGM 的典型消费场景（游戏 / 交互 / 循环播放）要的是
**可无缝循环的素材 + 可分层替换的轨**。你的链路天生就有小节与段落信息，
所以这件事只有你能做对。

═══ 关键事实（2026-09-21 实测，决定了实现方式）═══
`20_piano_rain`：`song.json` bpm=78 · 72 小节 → 按 bpm 推算 221.54 秒，
而**渲染 WAV 是 223.38 秒** —— 多出的 **1.84 秒是尾音衰减**
（渲染链 `trim_tail` 的 `min_tail=3.0` 保留的尾巴）。
⇒ **直接裁剪 [t0,t1] 会把尾音切掉 → 循环回跳时听起来"断"**。
⇒ 正解是**尾音回绕**（tail wrap）：把 t1 之后自然衰减的那段**叠加回片段开头**，
   于是播到结尾时，尾音正好在开头接上（这是 loop 素材的标准做法）。

⚠ 小节边界**不能用"实测每小节时长"反推**（那是把尾音算进去的假值）——
本工具按 `meter[0] * 60 / bpm` 算（与 MIDI 的 tempo map 一致），
并打印"循环区间落在哪几个小节 / 覆盖多少 MIDI 音符"供核对。

用法:
  python scripts\loop_export.py <song.json> --section A --tail 1.8
  python scripts\loop_export.py <song.json> --bars 5:13 --tail 1.8     # 1-based，B 不含
  python scripts\loop_export.py <song.json> --section A --no-wrap      # 对照：直接裁剪
  输出: <曲目>/loop/<名>_loop.wav（+.ogg）· loop.json · 控制台打判据
"""
import argparse
import json
import os
import sys

# "某轨在某段算不算活跃"的落差门（相对该轨最响的那一段）：高于它就算这一段该轨在场。
# 15dB 是"明显还在响"的量级 —— 比它低，人耳基本分不出那是不是该轨的贡献。
ACTIVE_DROP_DB = 15.0
# 判轨的角色用**它在整曲里活跃的段比例**（不是 input 跨度）：
#   ≥0.8 → base（几乎每段都在 ⇒ 常驻做底）· ≥0.5 → accent（点缀，适合参数驱动）· 其余 rare。
# ⚠ 一开始我用"input 区间跨度"判，结果**四轨全被判成 layered** ——
#   因为能量轴的最低点是 Outro（0.0），而没有一轨在 Outro 活跃，于是所有轨的下界都被抬高。
BASE_RATIO = 0.8
ACCENT_RATIO = 0.5

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import cli_utf8 as _cu
    _cu.setup()
except Exception:                                     # noqa: BLE001
    pass


def bar_seconds(song):
    """每小节秒数 = 拍数 × 60/BPM。**与 MIDI 的 tempo map 一致**（别用"实测时长/小节"反推）。"""
    m = song.get('meter') or [4, 4]
    return float(m[0]) * 60.0 / float(song['bpm'])


def section_span(song, name):
    """段落名 → (起始小节 0-based, 小节数)。段落名重复时取第一个匹配。"""
    k = 0
    for s in song.get('sections') or []:
        if s.get('name') == name:
            return k, int(s.get('bars') or 0)
        k += int(s.get('bars') or 0)
    raise SystemExit('段落不存在: %s（可用: %s）'
                     % (name, [s.get('name') for s in song.get('sections') or []]))


def total_bars(song):
    return sum(int(s.get('bars') or 0) for s in song.get('sections') or [])


def crop_wrap(x, i0, i1, wrap_n):
    """裁 [i0,i1) 并把 i1 之后的 `wrap_n` 个样本**叠加回开头**（尾音回绕）。

    没有这一步，循环回跳处会丢掉尾音（钢琴/镲片的衰减被硬切）→ 听感"断"。
    """
    seg = x[i0:i1].copy()
    if wrap_n > 0:
        tail = x[i1:i1 + wrap_n]
        n = int(min(len(tail), len(seg)))
        seg[:n] += tail[:n]
    return seg


def seam_report(seg, sr, src=None, i1=None, wrap_n=0):
    """循环接缝的判据。**三条一起看，缺一条就会被骗**（2026-09-21 实测的教训）。

    ① `jump` / `ratio`：接缝处 y[-1] → y[0] 的**单样本跳变**，与片段内部跳变分位比。
       ⚠ 光看它不够 —— 实测 B 段"回绕 0.5 秒"与"回绕 2.5 秒"给出**完全相同**的 0.00510
       （因为 wrap 只改开头那几个样本，而接缝跳变可能由末尾样本主导）。单样本跳变
       只反映"有没有爆音"，**反映不了"尾音被剁掉"**。
    ② `seam_db` / `seam_z`：接缝两侧各 50ms 的能量差。
       ⚠⚠ **这一条不是好坏判据**（2026-09-21 实测否定）：回绕版 26.5 dB **大于**直接裁剪的
       19.97 dB，而回绕明明是更对的（它接住了尾音）。原因是这段音乐**本来就"弱收强起"**
       （片段内部相邻 50ms 窗的 RMS 波动 σ=6.85 dB），**接缝处能量不相等是正常的**。
       → 留着它只作**描述**（看接缝处是不是"突兀地差了一大截"），**不许拿它判优劣**。
    ③ `tail_after_db` / `tail_rel_db`：**循环点之后那截尾音的电平**（相对片段电平）——
       这是**本源量、也是唯一能判定"要不要回绕"的量**：实测 A 段循环点之后还有 1.8 秒
       尾音、电平只比正片低 **0.8 dB** ⇒ 不回绕就是**每循环一次丢掉一截和正片一样响的内容**。
    ④ `beg_over_db`：回绕后**开头 50ms 相对片段能量中位的偏差** —— 防"绕过头的反向错"
       （叠太多 → 开头过响/发浑）。同样是描述量。
    """
    import numpy as np
    mono = seg if seg.ndim == 1 else seg[:, 0]
    W = max(1, int(0.05 * sr))
    d = np.abs(np.diff(mono))
    inner_p999 = float(np.percentile(d, 99.9)) if d.size else 0.0
    jump = float(abs(mono[-1] - mono[0]))
    end_r, beg_r = rms_db(mono[-W:]), rms_db(mono[:W])
    seam_db = abs(end_r - beg_r)
    n = len(mono) // W
    if n >= 4:
        wins = mono[:n * W].reshape(n, W).astype('float64')
        rr = np.sqrt((wins ** 2).mean(axis=1))
        win_db = 20.0 * np.log10(np.maximum(rr, 1e-9))
        inner_std = float(np.std(win_db))
        med_db = float(np.median(win_db))
    else:
        inner_std, med_db = float('nan'), float('nan')
    out = {'jump': jump, 'inner_p999': inner_p999,
           'ratio': (jump / inner_p999) if inner_p999 > 0 else float('inf'),
           'seam_db': seam_db, 'inner_win_std_db': inner_std,
           'seam_z': (seam_db / inner_std) if inner_std and inner_std > 0 else float('nan'),
           'beg_over_db': (beg_r - med_db) if med_db == med_db else float('nan'),
           'seg_db': rms_db(mono)}
    if src is not None and i1 is not None and wrap_n:
        tail = src[i1:i1 + wrap_n]
        tail = tail if tail.ndim == 1 else tail[:, 0]
        out['tail_after_db'] = rms_db(tail)
        out['tail_rel_db'] = out['tail_after_db'] - out['seg_db']
        out['tail_seconds'] = round(len(tail) / float(sr), 3)
    return out


def rms_db(x):
    import numpy as np
    if x.size == 0:
        return -120.0
    return float(20.0 * np.log10(max(1e-9, float(np.sqrt((x.astype('float64') ** 2).mean())))))


def section_bounds(song, spb):
    """段落 → `[(段名, 起秒, 止秒)]`（按小节累加；与 MIDI 时间轴一致）。"""
    out, b0 = [], 0.0
    for s in song.get('sections') or []:
        b1 = b0 + int(s.get('bars') or 0) * spb
        out.append((s.get('name'), b0, b1))
        b0 = b1
    return out


def stems_loop(song_json, spb, i0, i1, wrap_n, out_dir, bounds, log=print):
    """逐轨导出**同一区间**的循环素材（中间件要的是"每一轨都可循环"）。

    复用 `studio/bridge.py` 的 `song_tracks` + `render_track`（**别重造**）——
    后者 `keep_wav=True` 会留下 **WAV**，这对无缝很关键：
    OGG/MP3 的编码 padding 会破坏循环点（所以主交付用 WAV，`--ogg` 只是可选）。

    ⚠ 各轨的循环区间必须**逐样本同一时间区间**，否则中间件里对不齐。
    ⚠ 单轨渲染时长可能与主混音不同（实测 218.8 vs 223.4 秒）—— 起点对齐、末尾长短不同，
      所以**越界的那一轨直接跳过并报出来**，不硬裁。
    """
    import shutil
    import tempfile

    import numpy as np
    import soundfile as sf

    studio = os.path.join(os.path.dirname(HERE), 'studio')
    if studio not in sys.path:
        sys.path.insert(0, studio)
    import bridge                                            # 延迟 import（它依赖引擎）

    sdir = os.path.join(out_dir, 'stems')
    os.makedirs(sdir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='loopstems_')
    info = []
    try:
        for tr, notes, d in bridge.song_tracks(song_json):
            base = os.path.join(tmp, tr)
            bridge.render_track(d, tr, notes, base, keep_wav=True)
            wav = base + '.wav'
            x, sr = sf.read(wav, dtype='float32', always_2d=True)
            if i1 + wrap_n > len(x):
                log('  !! %-9s 比循环区间短（%.1f 秒）→ 跳过（不硬裁）'
                    % (tr, len(x) / float(sr)))
                continue
            seg = np.stack([crop_wrap(x[:, c], i0, i1, wrap_n)
                            for c in range(x.shape[1])], axis=1)
            op = os.path.join(sdir, tr + '.wav')
            sf.write(op, seg, sr, subtype='PCM_16')
            # ⚠ 逐段活跃度必须**趁整曲 WAV 还在**时算（`finally` 里就删了）——
            #   循环素材只覆盖一个段落，用它判"这轨在哪些段响"是错的。
            srm = section_rms(x, sr, bounds)
            mx = max([r for _n, r in srm] or [-120.0])
            active = [nm for nm, r in srm if r > mx - ACTIVE_DROP_DB]
            info.append({'name': tr, 'file': op,
                         'stem_seconds': round(len(x) / float(sr), 2),
                         'stem_rms_db': round(rms_db(x[:, 0]), 1),
                         'loop_rms_db': round(rms_db(seg[:, 0]), 1),
                         'sections_rms': [(nm, round(r, 1)) for nm, r in srm],
                         'active_sections': active})
            log('  %-9s 全曲 %.1f dBFS（%.1f 秒）→ 循环 %.1f dBFS · 活跃段 %s'
                % (tr, info[-1]['stem_rms_db'], info[-1]['stem_seconds'],
                   info[-1]['loop_rms_db'], '/'.join(active) or '（无）'))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return info


def section_rms(x, sr, bounds):
    """逐段 RMS（单声道量）→ `[(段名, dB)]`。"""
    out = []
    for nm, t0, t1 in bounds:
        a, b = int(t0 * sr), int(t1 * sr)
        out.append((nm, rms_db(x[a:b, 0]) if b > a else -120.0))
    return out


def build_bindings(song, spb, main_sec_rms, stems_info):
    """生成**参数绑定清单**（接 Wwise / kibaudio 那类音频中间件的接口）。

    ⚠ 这里**只做客观推导，不编游戏语义**：
      · 强度轴 = 按**各段主混音 RMS** 归一化到 [0,1]（0 = 最安静的段，1 = 最响的段）；
      · 每轨的 `suggest_input` = 它在能量轴上**活跃段**所覆盖的区间
        （活跃 = 该段 RMS 高于"该轨最响段 − `ACTIVE_DROP_DB`"，逐段数据在 `stems_loop` 里
        趁整曲 WAV 还在时算好）。
      参数名（`intensity`）是**占位**，游戏侧叫什么由你定（战斗强度 / 紧张度 / 情绪）。
      这套结构对应中间件的 RTPC / ParamBinding：**参数一推，对应轨淡入**。
    """
    vals = [r for _n, r in main_sec_rms]
    lo, hi = (min(vals), max(vals)) if vals else (0.0, 0.0)
    axis = {nm: (0.0 if hi <= lo else (r - lo) / (hi - lo)) for nm, r in main_sec_rms}

    stems_out, roles = {}, {'base': [], 'accent': [], 'rare': []}
    order = [nm for nm, _a, _b in section_bounds(song, spb)]
    for st in stems_info:
        act = [nm for nm in (st.get('active_sections') or []) if nm in axis]
        ax = [axis[nm] for nm in act]
        ratio = len(act) / float(max(1, len(order)))
        role = ('base' if ratio >= BASE_RATIO
                else ('accent' if ratio >= ACCENT_RATIO else 'rare'))
        roles[role].append(st['name'])
        stems_out[st['name']] = {
            'loop_file': os.path.basename(st['file']),
            # ⚠ 字段名必须写全：`song.json` 的 `mix[轨]` 是 **`[pan, vol]`**（见 `render_track`），
            #   写成 `song_mix` 会被误读成"音量曲线"（实测我第一版就这么写过）。
            'song_pan_vol': (song.get('mix') or {}).get(st['name']),
            'active_sections': act,
            'active_ratio': round(ratio, 2),
            'role': role,
            # ⚠ 这是**整曲分段**的参考区间，**单段循环用不到**（见 note）
            'section_input_ref': ([round(min(ax), 2), round(max(ax), 2)] if ax else None),
        }
    return {
        'note': ('两种用法别混：'
                 '① **单段循环 + 分层**（kibaudio 那种 stems 模式）—— 所有轨同时播，'
                 '参数控制各自音量；这时看 `role`：`base` 常驻做底、`accent` 适合被参数驱动淡入。'
                 '② **整曲分段切换**（按游戏状态换段）—— 才用得上 `section_axis`/`section_input_ref`，'
                 '它按「各段主混音 RMS」排序推导（0=最安静段，1=最响段），**与单段循环无关**。'
                 '参数名 `intensity` 是**占位**，按你的游戏状态改名。'),
        # ⚠ 这条是防"坏尺子"的：逐轨渲染是**归一化过**的（`render_track` 固定 rms=-16.9），
        #   所以各轨 `loop_rms_db` 的差异反映的是**内容稀疏度**，不是它在混音里的真实电平。
        #   要配平请用 `song_mix`（song.json 的 CC7）或跑 `bridge mixfit`。
        'level_warning': ('`loop_rms_db` 是**归一化后**的电平，**别拿它给轨配平**；'
                          '配平看 `song_pan_vol`（song.json 的 `mix[轨]` = **[pan, vol]**）'
                          '或 `bridge mixfit`。'),
        'axis_source': 'section RMS of the mixed master（仅用于"整曲分段切换"）',
        'active_drop_db': ACTIVE_DROP_DB,
        'section_axis': {nm: round(v, 3) for nm, v in axis.items()},
        'section_rms_db': [(nm, round(r, 1)) for nm, r in main_sec_rms],
        'roles': roles,
        'stems': stems_out,
    }


def alignment_lag(main_seg, stems_sum, sr, max_ms=200):
    """逐轨素材之和 与 主混音（同一区间）的互相关峰值位置 → `(lag 样本, 相关系数)`。

    为什么要它：中间件里**各轨与主混音必须同一起点**，否则你听到的"对上了"是假的。
    ⚠ 只判**位置**（峰值应在 0 附近），不判幅度 —— 逐轨渲染是**归一化过**的
    （`render_track` 固定 `rms_db=-16.9`），求和本来就不会等于主混音。
    """
    import numpy as np
    n = min(len(main_seg), len(stems_sum))
    ds = max(1, int(sr * 0.002))                       # 降采样步长 ≈2ms（加速）
    a = np.asarray(main_seg[:n:ds], dtype='float64')
    b = np.asarray(stems_sum[:n:ds], dtype='float64')
    m = int(max_ms / 1000.0 * sr / ds)
    best, bl = -2.0, 0
    for lag in range(-m, m + 1):
        if lag >= 0:
            x, y = a[lag:], b[:len(a) - lag]
        else:
            x, y = a[:len(a) + lag], b[-lag:]
        if len(x) < 50 or np.std(x) < 1e-9 or np.std(y) < 1e-9:
            continue
        c = float(np.corrcoef(x, y)[0, 1])
        if c > best:
            best, bl = c, lag
    return int(bl * ds), best


def main():
    import numpy as np
    import soundfile as sf

    ap = argparse.ArgumentParser(description='导出可无缝循环的 BGM 素材（尾音回绕 + 客观判据）')
    ap.add_argument('song_json')
    ap.add_argument('--section', help='按段落名取循环区间（如 A / B / C）')
    ap.add_argument('--bars', help='按小节区间取：起:止（1-based，止不含），如 5:13')
    ap.add_argument('--wav', help='音频文件（默认按 render.json 的 out 找 .wav）')
    ap.add_argument('--to', help='输出目录（默认 <曲目>/loop）')
    ap.add_argument('--tail', type=float, default=1.8, help='尾音回绕秒数（默认 1.8）')
    ap.add_argument('--no-wrap', action='store_true', help='**对照**：直接裁剪、不回绕')
    ap.add_argument('--ogg', action='store_true', help='额外导出 ogg（⚠ 有损编码可能引入 padding）')
    ap.add_argument('--stems', action='store_true',
                    help='同时逐轨导出**同一区间**的循环素材到 `stems/`（中间件要的形态），'
                         '并生成参数绑定清单 `loop_stems.json`。'
                         '⚠ 逐轨渲染是分钟级（实测每轨 ≈7 秒）')
    ap.add_argument('--repeat', type=int, default=0,
                    help='额外导出"循环 N 遍"的试听版（接缝会暴露 N−1 次 —— **最终判据是耳朵**）')
    a = ap.parse_args()

    song = json.load(open(a.song_json, encoding='utf-8'))
    d = os.path.dirname(os.path.abspath(a.song_json))
    spb = bar_seconds(song)
    nb = total_bars(song)

    if a.section:
        b0, n = section_span(song, a.section)
        b1 = b0 + n
        label = a.section
    elif a.bars:
        p = a.bars.split(':')
        b0, b1 = int(p[0]) - 1, int(p[1]) - 1
        label = 'bars%d-%d' % (b0 + 1, b1)
    else:
        raise SystemExit('给 --section 或 --bars')

    # 音频
    wav = a.wav
    if not wav:
        rj = os.path.join(d, 'render.json')
        out = json.load(open(rj, encoding='utf-8')).get('out') if os.path.exists(rj) else None
        wav = os.path.join(d, (out or song['name'] + '_sf') + '.wav')
    x, sr = sf.read(wav, dtype='float32', always_2d=True)
    n_ch = x.shape[1]

    i0, i1 = int(round(b0 * spb * sr)), int(round(b1 * spb * sr))
    if i1 > len(x):
        raise SystemExit('循环区间超出音频（%.2f 秒 > 音频 %.2f 秒）'
                         % (i1 / sr, len(x) / sr))
    wrap_n = 0 if a.no_wrap else int(round(a.tail * sr))

    seg = np.stack([crop_wrap(x[:, c], i0, i1, wrap_n) for c in range(n_ch)], axis=1)
    seg_len = seg.shape[0]
    # ⚠ 判据要用**原始音频的尾音**做参照：即"循环点之后本来还有多少声音"。
    #   回绕版已经把它叠回开头了，所以量尾音必须去 `x`（原音频）里量，不能量 seg。
    #   参照窗**固定**（两版同一个窗才可比；否则 no-wrap 版会显示成"只有 0.5 秒尾音"）。
    REF_SEC = 2.5
    rep = seam_report(seg, sr, src=x, i1=i1, wrap_n=int(round(REF_SEC * sr)))
    # ⚠ `tail_rel_db` 的基准必须是**裁剪段本身**（不含回绕）——
    #   用"回绕后的片段 RMS"当分母会让两版出现 +0.3 与 +0.7 的假差异
    #   （回绕把开头叠响 → 片段 RMS 从 -20.0 升到 -19.6）。判据的基准要跨版本可比。
    rep['seg_src_db'] = rms_db(x[i0:i1, 0])
    rep['tail_rel_db'] = rep['tail_after_db'] - rep['seg_src_db']

    to = os.path.abspath(a.to or os.path.join(d, 'loop'))
    os.makedirs(to, exist_ok=True)
    stem = '%s_loop%s' % (label, '' if not a.no_wrap else '_nowrap')
    wp = os.path.join(to, stem + '.wav')
    sf.write(wp, seg, sr, subtype='PCM_16')

    info = {'song': song['name'], 'label': label, 'source_wav': os.path.abspath(wav),
            'bpm': song['bpm'], 'meter': song.get('meter'), 'bar_seconds': round(spb, 6),
            'bars': [b0 + 1, b1], 'bars_count': b1 - b0,
            'start_sec': round(i0 / sr, 4), 'end_sec': round(i1 / sr, 4),
            'loop_seconds': round(seg_len / sr, 4), 'sample_rate': sr,
            'channels': n_ch, 'tail_wrap_sec': 0.0 if a.no_wrap else a.tail,
            'seam': {k: (round(v, 6) if v != float('inf') else None) for k, v in rep.items()},
            'out_wav': wp}
    if a.repeat and a.repeat > 1:
        xp = os.path.join(to, '%s_x%d.wav' % (stem, a.repeat))
        sf.write(xp, np.concatenate([seg] * a.repeat, axis=0), sr, subtype='PCM_16')
        info['out_repeat'] = xp
    if a.ogg:
        import subprocess
        og = os.path.join(to, stem + '.ogg')
        r = subprocess.run([sys.executable, os.path.join(HERE, 'to_ogg.py'), wp, og],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        info['out_ogg'] = og if r.returncode == 0 else None
        if r.returncode != 0:
            info['ogg_error'] = (r.stderr or r.stdout or '')[-160:]

    if a.stems:
        print('  ── 逐轨渲染中（每轨 ≈7 秒，请稍候）──')
        bounds_sec = section_bounds(song, spb)
        s_info = stems_loop(os.path.abspath(a.song_json), spb, i0, i1, wrap_n, to, bounds_sec)
        main_sec = section_rms(x, sr, bounds_sec)
        info['stems'] = [dict(st, file=os.path.relpath(st['file'], to)) for st in s_info]
        info['bindings'] = build_bindings(song, spb, main_sec, s_info)
        if s_info:
            ssum = None
            for st in s_info:
                y, _sr2 = sf.read(st['file'], dtype='float32', always_2d=True)
                ssum = y if ssum is None else ssum + y
            lag, cc = alignment_lag(seg[:, 0], ssum[:, 0], sr)
            info['stems_alignment'] = {'lag_samples': lag,
                                       'lag_ms': round(lag / sr * 1000.0, 1),
                                       'corr': round(cc, 3)}
            print('  逐轨求和 vs 主混音：互相关峰值在 %+d 样本（%+.1f ms）· 相关 %.3f'
                  % (lag, lag / sr * 1000.0, cc))
            print('    ↑ 峰值该在 0 附近；偏得多 = 逐轨素材与主混音**不是同一起点**')
            sp = os.path.join(to, 'loop_stems.json')
            with open(sp, 'w', encoding='utf-8') as fh:
                json.dump(info['bindings'], fh, ensure_ascii=False, indent=1)
            rl = info['bindings']['roles']
            print('  绑定清单：base(常驻底) %s · accent(可参数驱动) %s · rare %s'
                  % (rl['base'] or '（无）', rl['accent'] or '（无）', rl['rare'] or '（无）'))
            print('        → %s' % sp)

    jp = os.path.join(to, 'loop.json')
    with open(jp, 'w', encoding='utf-8') as fh:
        json.dump(info, fh, ensure_ascii=False, indent=1)

    # ── 控制台：人话 + 判据 ──────────────────────────────────────────────
    print('=' * 78)
    print('%s · 取 %s（小节 %d–%d，共 %d 小节）' % (song['name'], label, b0 + 1, b1, b1 - b0))
    print('  每小节 %.4f 秒（%d/%d @ %d BPM，与 MIDI tempo 一致）'
          % (spb, (song.get('meter') or [4, 4])[0], (song.get('meter') or [4, 4])[1], song['bpm']))
    print('  整曲 %d 小节 · 音频 %.2f 秒' % (nb, len(x) / sr))
    print('  循环区间 %.3f ~ %.3f 秒 → 素材 %.3f 秒' % (i0 / sr, i1 / sr, seg_len / sr))
    print('  尾音回绕 %s' % ('关（**对照版**：直接裁剪）' if a.no_wrap else '%.2f 秒' % a.tail))
    print('  ③ 循环点之后 %.1f 秒内仍有尾音 %.1f dBFS（相对**裁剪段** **%+.1f dB**）'
          % (rep['tail_seconds'], rep['tail_after_db'], rep['tail_rel_db']))
    print('     ↑ **本源量**：不回绕 = 每循环一次丢掉这一截（实测 A 段只低 0.8dB → 听感是"音尾被剁"）')
    print('  ① 单样本跳变 %.5f（片段内部 99.9%% 分位 %.5f → 比值 %.2f）'
          % (rep['jump'], rep['inner_p999'], rep['ratio']))
    print('  ② 接缝能量差 %.1f dB —— **描述量，不是判据**：这段本来就"弱收强起"，'
          % rep['seam_db'])
    print('     片段内部同量波动 σ=%.2f dB，接缝处能量不相等是正常的（回绕版此值反而更大）'
          % rep['inner_win_std_db'])
    print('  ④ 回绕后开头比片段中位 **%+.1f dB** —— 描述量，防"绕过头"（叠太多 → 开头过响/发浑）'
          % rep['beg_over_db'])
    print('  片段 RMS %.1f dBFS · 素材 %.3f 秒' % (rep['seg_db'], seg_len / sr))
    print('  写出: %s' % wp)
    if info.get('out_repeat'):
        print('        试听版（循环 %d 遍，接缝出现 %d 次，**用耳朵判**）: %s'
              % (a.repeat, a.repeat - 1, info['out_repeat']))
    print('        %s' % jp)
    print('=' * 78)
    return 0


if __name__ == '__main__':
    sys.exit(main())
