#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""midi_to_song.py —— **从任意标准 MIDI 开始做音乐**（MIDI → song.json → 引擎）。

## 这个工具补的是哪条路

引擎原本只有两条入口：
  · **生成**：`new_song.py --theme` → 只有和弦 + 段落，声部由引擎展开；
  · **转录**：`transcribe_ymt3.py` → `transcribe_to_song.py`（音频 → MIDI → song.json）。
一条**现成的 `.mid`** 没有入口 —— 编辑器的会话在 `%TEMP%`、不进曲库，
用户在 `/editor` 里改完只能"导出 .mid"，回不到引擎（不能渲染/体检/继续编配）。

本工具把 `.mid` 直接落成 `songs/<名>/song.json`：
  · 每个源轨映射到引擎轨（Melody/Hook/Piano/Arp/Pad/Strings/Bass/Glock/Drums）；
  · 音符**全量**写进 `notes_extra`（带第 5 位力度，不做抽样）；
  · 和弦按**每小节实际音级**识别（`midi_chords.match`），名字必须能被
    `selftest.parse_chord` 解析（守卫 `chord_names_match_notes` 是硬门）；
  · 段落按 `--bars-per-section` 等分（默认 8 小节），一个段一个键；
  · 生成层由引擎的 `apply_restore_no_gen` 自动关掉（有 `notes_extra` = 还原曲）——
    所以**不会**凭空多出伴奏/鼓（实测过：不关会多出 2293 个鼓点，见 PITFALLS 313/314 同族）。

## 口径（都实测过）

1. **小节号是全局 0 基**、`notes_extra` 的拍是**四分之一音符**（0..bar_beats-1）、
   时值单位也是拍。`melody` 才用段内小节号 —— 本工具不写 melody（避免与
   `notes_extra` 同一轨重复发声；实测 `siren_end2` 174/176 音重复，见
   `transcribe_to_song.py` 的 `_drop_mel` 注释）。
2. **时值下限 0.25 拍**（与 `transcribe_to_song.py` 同口径）：更短的音会被抬到 0.25。
   实测 BGM36 有 11 个音 <0.25 拍（Piano 2 / Strings 7 / Drums 2），回读时值差 0.002~0.1
   全部来自这一条 —— 音高/力度/起音**零差异**。
3. **音域**：引擎的 `TR_RANGE` 会对越界轨整轨移八度或丢音（`song_engine.build_events`）。
   本工具**不预先改音高**，但会把越界音打印出来（不静默）。
4. **和弦名**：`b000` 这种占位名过不了 `chord_names_match_notes`；本工具用
   `midi_chords.match` 识别后按 `QUALITY` 标准音集构造，静音小节填 `C`（不发声，
   生成层已关，只影响体检口径）。

## 用法

```powershell
# 写进曲库：songs/<midi 文件名>/song.json + .mid
& $py scripts\midi_to_song.py <路径>\foo.mid

# 指定名字 / 段落长度 / 只出数据不出 MIDI
& $py scripts\midi_to_song.py foo.mid --name my_song --bars-per-section 4 --no-compose

# 输出到曲库外（测试/临时）
& $py scripts\midi_to_song.py foo.mid --out D:\test\_tmp\x\song.json
```

## 验收（本机实测，2026-10-17）

`BGM36.mid`（79 小节 · 154.1 BPM · 7 轨）：
  · 导入 → 引擎事件数逐轨一致：Hook 9 · Piano 562 · Pad 60 · Strings 342 ·
    Bass 15 · Glock 5 · Drums 13（合计 1006）；
  · 回读 `.mid` 逐音比对：**音高 0 差 · 力度 0 差 · 起音 0 差**；时值只差 <0.25 拍那 11 个；
  · `check_song --all --fast` 17 项全过（含 `chord_names_match_notes`）；
  · `render_midi.py` 渲染 125.6 秒、RMS −16.2 dBFS、削波 0。
"""
import argparse
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import json_io        # noqa: E402
import midi_chords as mc  # noqa: E402
import midi_file      # noqa: E402
import song_engine    # noqa: E402

TRACKS = ('Melody', 'Hook', 'Piano', 'Arp', 'Pad', 'Strings', 'Bass', 'Glock', 'Drums')

# 轨名关键词 → 引擎轨（小写匹配，先到先得）
NAME2TRACK = (
    ('melody', 'Melody'), ('lead', 'Melody'), ('vocal', 'Melody'), ('voice', 'Melody'),
    ('sing', 'Melody'), ('hook', 'Hook'), ('guitar', 'Hook'), ('gt', 'Hook'),
    ('pluck', 'Hook'), ('comp', 'Hook'), ('piano', 'Piano'), ('keys', 'Piano'),
    ('keyboard', 'Piano'), ('rhodes', 'Piano'), ('arp', 'Arp'), ('pad', 'Pad'),
    ('strings', 'Strings'), ('string', 'Strings'), ('bass', 'Bass'),
    ('glock', 'Glock'), ('bell', 'Glock'), ('celesta', 'Glock'),
    ('drum', 'Drums'), ('perc', 'Drums'),
)
# GM program 段 → 引擎轨（音色族，按 General MIDI 分组）
PROG2TRACK = (
    (0, 7, 'Piano'), (8, 15, 'Glock'), (24, 31, 'Hook'), (32, 39, 'Bass'),
    (40, 47, 'Strings'), (48, 51, 'Strings'), (52, 55, 'Pad'),
    (56, 63, 'Hook'), (64, 71, 'Hook'), (72, 79, 'Melody'),
    (80, 87, 'Melody'), (88, 95, 'Pad'), (96, 103, 'Arp'),
    (104, 111, 'Hook'), (112, 119, 'Glock'), (120, 127, 'Arp'),
)

# 与 `selftest.QUALITY` / `NOTE_PC` 同一套（守卫 `midi_to_song_chord_tables` 比对）
QUALITY = {'': (0, 4, 7), 'm': (0, 3, 7), '7': (0, 4, 7, 10), 'maj7': (0, 4, 7, 11),
           'maj9': (0, 4, 7, 11, 2), 'm7': (0, 3, 7, 10), '6': (0, 4, 7, 9),
           'm6': (0, 3, 7, 9), '5': (0, 7), 'sus4': (0, 5, 7), '7sus4': (0, 5, 7, 10),
           'sus2': (0, 2, 7), 'dim': (0, 3, 6), 'm7b5': (0, 3, 6, 10),
           'aug': (0, 4, 8), 'add9': (0, 2, 4, 7), 'm9': (0, 3, 7, 10, 2),
           '9': (0, 4, 7, 10, 2)}
NOTE_PC = {'C': 0, 'C#': 1, 'Db': 1, 'D': 2, 'D#': 3, 'Eb': 3, 'E': 4, 'F': 5,
           'F#': 6, 'Gb': 6, 'G': 7, 'G#': 8, 'Ab': 8, 'A': 9, 'A#': 10,
           'Bb': 10, 'B': 11}

MIN_DUR = 0.25          # 与 transcribe_to_song.py 同口径


def parse_chord(name):
    """和弦名 → (根音音级, 斜杠音级|None, 音程集合|None)。与 selftest.parse_chord 同口径。"""
    base, slash = name, None
    if '/' in base:
        base, s = base.split('/', 1)
        slash = NOTE_PC.get(s)
    for q in sorted(QUALITY, key=len, reverse=True):
        if q and base.endswith(q):
            root = NOTE_PC.get(base[:-len(q)])
            if root is not None:
                return root, slash, QUALITY[q]
    root = NOTE_PC.get(base)
    return (root, slash, QUALITY['']) if root is not None else (None, slash, None)


def chord_for_bar(pcs, bass_pc):
    """一小节的音级集合 → (可解析的和弦名, [bass_midi, tones])"""
    if not pcs:
        return 'C', [48, [60, 64, 67]]
    name, _score, det = mc.match(pcs, bass_pc)
    if name == '-':
        return 'C', [48, [60, 64, 67]]
    root, slash, ivs = parse_chord(name)
    if ivs is None:                       # 识别出的后缀不在 QUALITY 里 → 退到同类简单和弦
        root = det.get('root')
        name = mc.NAMES[root]
        slash, ivs = None, QUALITY['']
    bass = (slash if slash is not None else root) + 36
    tones = [root + 48 + i for i in ivs]
    return name, [bass, tones]


def track_by_name(name):
    """轨名关键词 → 引擎轨（没有命中返回 None）"""
    low = (name or '').strip().lower()
    for kw, tr in NAME2TRACK:
        if kw in low:
            return tr
    return None


def track_by_program(program):
    """GM 音色族 → 引擎轨（没有命中返回 None）"""
    if program is None:
        return None
    for lo, hi, tr in PROG2TRACK:
        if lo <= int(program) <= hi:
            return tr
    return None


def pick_track(name, channel, program):
    """源轨 → 引擎轨：鼓通道 > 轨名关键词 > GM 音色族 > 兜底 Piano"""
    if int(channel) == 9:
        return 'Drums'
    return track_by_name(name) or track_by_program(program) or 'Piano'


def channel_ccs(tr, ch):
    """Type 0 拆通道时，该通道的 CC 副本（`ccs_by_ch`）。"""
    return [list(x) for x in ((tr.get('ccs_by_ch') or {}).get(ch) or [])]


def channel_bends(tr, ch):
    """Type 0 拆通道时，该通道的弯音副本（`bends_by_ch`）。"""
    return [list(x) for x in ((tr.get('bends_by_ch') or {}).get(ch) or [])]


def expand_channels(tracks):
    """单轨多通道（SMF Type 0 的常态）→ 按通道拆成虚拟轨。

    ⚠ 不拆会**静默毁曲**：实测 Type 0 的钢琴/贝斯/鼓被并成一条轨，
      又因轨名像 Drums 而整条用鼓音色发声（7 个音全变鼓）。
    虚拟轨的名字故意用 `chN`（不带原轨名的关键词），让映射走**音色族**而不是继承原轨名。
    音符、CC、弯音都按通道带走（表情不能全塞给最后一条通道）。
    """
    out = []
    for tr in tracks:
        nbc = tr.get('notes_by_ch') or {}
        live = [c for c, ns in nbc.items() if ns]
        if len(live) <= 1:
            out.append(tr)
            continue
        pbc = tr.get('progs_by_ch') or {}
        for c in sorted(live):
            pcs = pbc.get(c) or []
            out.append({
                'index': tr.get('index'),
                'name': 'ch%d' % c,
                'channel': c,
                'program': (pcs[-1][1] if pcs else tr.get('program')),
                'notes': nbc[c],
                'ccs': channel_ccs(tr, c),
                'pitch_bends': channel_bends(tr, c),
            })
    return out


def beat_in_bar(beat, bar, bar_beats):
    """全局拍 → 小节内拍（**0 基、四分之一音符单位**）。

    抽成模块级函数是为了让守卫能**行为注入**：小节号两套口径
    （`melody` 段内 / `notes_extra` 全局）是这个仓库最贵的坑之一 ——
    写错时音符数一个不少、只是位置整体错位，数量型判据抓不到。
    """
    return round(beat - bar * bar_beats, 4)


def has_tempo_change(tempo_map):
    """tempo_map 里有 ≥2 个**不同** tempo 才算"变速"（重复同值不算）。

    抽成模块级函数是为了让变异测试能注入"检测失效"。
    """
    return len({int(m) for _b, m in (tempo_map or [])}) > 1


def sec_at(beat, tempo_map, default_mpqn=500000):
    """源拍 → 秒（按 tempo_map 分段积分；map 空/单条时是线性换算）。"""
    tmap = sorted((float(b), int(m)) for b, m in (tempo_map or []))
    if not tmap:
        return float(beat) * default_mpqn / 1e6
    sec, prev_b, prev_m = 0.0, 0.0, int(default_mpqn)
    for (b, m) in tmap:
        if b >= beat:
            break
        sec += (b - prev_b) * prev_m / 1e6
        prev_b, prev_m = b, m
    return sec + (float(beat) - prev_b) * prev_m / 1e6


def count_automation(m):
    """源模型里的 (CC 条数, 弯音条数)。

    引擎是**音符模型**：`song.json` 没有 CC/弯音的位置，导入时只能丢弃。
    丢弃本身可以接受，**静默**不可以 —— 所以要计数、打印、进 info（面板也显示）。
    """
    cc = sum(len(t.get('ccs') or []) for t in (m.get('tracks') or []))
    bd = sum(len(t.get('pitch_bends') or []) for t in (m.get('tracks') or []))
    return cc, bd


def collect_automation(tracks, map_beat, overrides=None):
    """源轨 → 引擎 `automation`（`{轨: {ccs, pitch_bends}}`）。

    `map_beat`：源拍 → **目标全局拍**（单 tempo 按比例 / 多 tempo 按秒重排）。
    ⚠ 引擎的 `write_midi` 只认**全局拍**（与 `notes_extra` 的小节内拍不是同一口径）。
    ⚠ Type 0（`notes_by_ch` 多通道）的 CC/弯音**不在这里搬**：`parse_full` 还没给
      `ccs_by_ch`/`bends_by_ch`，硬搬会全塞进最后一条通道 —— 调用方要**明确报出来**。
    """
    ov = {str(k).strip().lower(): v for k, v in (overrides or {}).items()}
    out = {}
    for tr in tracks:
        ccs = tr.get('ccs') or []
        bends = tr.get('pitch_bends') or []
        if not ccs and not bends:
            continue
        key = (tr.get('name') or '').strip().lower()
        dst = ov.get(key) or pick_track(tr.get('name'), tr.get('channel'),
                                        tr.get('program'))
        slot = out.setdefault(dst, {'ccs': [], 'pitch_bends': []})
        for (b, cc, val) in ccs:
            slot['ccs'].append([map_beat(float(b)), int(cc), int(val)])
        for (b, val) in bends:
            slot['pitch_bends'].append([map_beat(float(b)), int(val)])
    return out


def safe_name(s):
    """曲目目录名：保留中文/字母/数字/空格/下划线/连字符，其余换 `_`"""
    s = os.path.splitext(os.path.basename(s))[0]
    s = re.sub(r'[^\w\u4e00-\u9fff\- ]+', '_', s, flags=re.UNICODE).strip(' _')
    return s or 'imported'


def build_from_model(m, name=None, bpm=None, bars_per_section=8, verbose=True,
                     overrides=None, allow_tempo_change=False, tempo_map=None,
                     source_label=None):
    """`midi_file` 模型 → song.json 的 dict + 诊断信息（不写盘）。

    `overrides`：{源轨名（小写）: 引擎轨}，`--map` 用；命中时不再猜。
    `allow_tempo_change`：多 tempo 时默认**拒绝**（防静默错时间轴）；显式允许后
    按**秒**重排到统一 BPM（小节线会变、原曲小节号不再对应）。
    `tempo_map`：[[拍, mpqn], ...]；模型本身不带它（`import_midi` 故意不暴露），
    文件版由 `build()` 从解析器取；编辑器内存模型不经过文件，传空 = 单 tempo。
    """
    src_bpm = float(m.get('bpm') or 120.0)
    bpm = float(bpm or src_bpm)
    bar_beats = float(song_engine.bar_beats(m.get('timesig') or [4, 4]))
    cc_count, bend_count = count_automation(m)
    # `.kar` 歌词（meta 0x05）：只统计、只报告 —— **不变成音符、不进 song.json**
    # （歌词不是可演奏内容；但"看见 N 行被忽略"是必须的，否则用户以为歌词跟着进来了）。
    lyric_lines = sum(len(t.get('lyrics') or []) for t in (m.get('tracks') or []))
    tmap = tempo_map or []
    first_mpqn = int(tmap[0][1]) if tmap else int(round(60_000_000 / max(1.0, src_bpm)))
    tempo_multi = has_tempo_change(tmap)
    if tempo_multi:
        desc = '、'.join('%g BPM@第%.2f拍' % (60_000_000.0 / int(mpqn), float(b))
                        for b, mpqn in tmap[:8])
        if not allow_tempo_change:
            raise SystemExit(
                '这个 MIDI 有 %d 个 tempo 变化（%s）。\n'
                '引擎是**单 tempo** 模型：直接导入会按首个 tempo 换算整条时间轴，'
                '后半段会静默错位。\n'
                '  · 按**秒**重排到统一 BPM（小节线会变、原曲小节号不再对应）：'
                '加 --allow-tempo-change\n'
                '  · 想保留原曲小节线：先在 DAW / 本仓库编辑器里转成单 tempo 再导'
                % (len(tmap), desc))
        if verbose:
            print('  ! 多 tempo（%d 段：%s）→ 按**秒**重排到统一 %.2f BPM；'
                  '小节线不再对应原曲' % (len(tmap), desc, bpm))
    # 目标时间轴上的总长（`--bpm` 与源不同 / 多 tempo 都要换算，否则小节数会算错）
    if tempo_multi:
        _end_new = sec_at(float(m.get('end_beat') or 0.0), tmap, first_mpqn) * bpm / 60.0
    else:
        _end_new = float(m.get('end_beat') or 0.0) * src_bpm / bpm
    nbars = max(1, int(math.ceil(_end_new / bar_beats)))
    overrides = {str(k).strip().lower(): v for k, v in (overrides or {}).items()}
    _split = any(
        len([c for c, ns in (t.get('notes_by_ch') or {}).items() if ns]) > 1
        for t in (m.get('tracks') or []))
    if _split and verbose:
        print('  [Type 0] 单轨混多通道 → 按通道拆成虚拟轨'
              '（音符 + CC/弯音；program 取该通道最后一个）')

    def _map_beat(b):
        if tempo_multi:
            return round(sec_at(float(b), tmap, first_mpqn) * bpm / 60.0, 4)
        return round(float(b) * src_bpm / bpm, 4)

    _src = expand_channels(m.get('tracks') or [])
    automation = collect_automation(_src, _map_beat, overrides)

    merged = {t: [] for t in TRACKS}
    mapping, short_dur = [], 0
    for tr in _src:
        ns = tr.get('notes') or []
        if not ns:
            continue
        ch, prog = tr.get('channel'), tr.get('program')
        by_name = track_by_name(tr.get('name'))
        by_prog = track_by_program(prog)
        key = (tr.get('name') or '').strip().lower()
        dst = overrides.get(key) or pick_track(tr.get('name'), ch, prog)
        if verbose and not overrides.get(key) and by_name and by_prog and by_name != by_prog:
            print('  ! %s：轨名像 %s、音色像 %s → 按轨名走'
                  '（要按音色：--map "%s=%s"）'
                  % (tr.get('name'), by_name, by_prog, tr.get('name'), by_prog))
        mapping.append((tr.get('name'), ch, prog, dst, len(ns)))
        for (b, d, p, v) in ns:
            if b is None or d is None or p is None:
                continue
            if tempo_multi:
                _s0 = sec_at(float(b), tmap, first_mpqn)
                _s1 = sec_at(float(b) + float(d), tmap, first_mpqn)
                beat = _s0 * bpm / 60.0
                dur = max(MIN_DUR, (_s1 - _s0) * bpm / 60.0)
            else:
                beat = float(b) * src_bpm / bpm
                dur = float(d) * src_bpm / bpm
            if dur < MIN_DUR:
                dur, short_dur = MIN_DUR, short_dur + 1
            bar = int(beat // bar_beats)
            clipped = False
            if bar >= nbars:
                bar, beat, clipped = nbars - 1, 0.0, True
            inbar = beat_in_bar(beat, bar, bar_beats)
            merged[dst].append([bar, inbar, round(dur, 4), int(p),
                                int(v) if v is not None else 100, clipped])

    # 超出曲尾的音：夹到最后一小节、时值收到小节内（不删内容）
    clipped = 0
    for tr, arr in merged.items():
        for it in arr:
            if it[5]:
                clipped += 1
                it[2] = max(MIN_DUR, round(bar_beats - it[1], 4))
        merged[tr] = [it[:5] for it in arr]

    # 和弦：每小节按实际音级识别
    sounding = [(it[0], it[3]) for t in TRACKS for it in merged[t] if t != 'Drums']
    chords, bar_chord = {}, []
    for bar in range(nbars):
        ps = [p for (b, p) in sounding if b == bar]
        nm, pair = chord_for_bar({p % 12 for p in ps}, min(ps) if ps else None)
        chords.setdefault(nm, pair)
        bar_chord.append(nm)

    sections = []
    for i in range(int(math.ceil(nbars / float(bars_per_section)))):
        b0 = i * bars_per_section
        n = min(bars_per_section, nbars - b0)
        sections.append({'name': 'S%02d' % (i + 1), 'bars': n,
                         'chords': bar_chord[b0:b0 + n], 'melody': 'a',
                         'arr': {'perc': 0}})

    programs = {}
    for tr in TRACKS:
        if not merged.get(tr):
            continue
        src = next((x for x in mapping if x[3] == tr), None)
        programs[tr] = [src[2] if src and src[2] is not None else None,
                        song_engine.CH[tr]]

    d = {
        'name': name or safe_name(str(source_label or 'imported')),
        'bpm': round(bpm, 4),
        'meter': list(m.get('timesig') or [4, 4]),
        'desc': '由 midi_to_song.py 从 %s 导入（%d 小节 · %d 段）'
                % (os.path.basename(str(source_label or '内存模型')), nbars, len(sections)),
        'chords': chords, 'sections': sections,
        'melody': {'a': []},
        'notes_extra': {t: merged[t] for t in TRACKS if merged[t]},
        'programs': programs,
        'patterns': {'notes_extra_full': True},
    }
    if automation:
        # 自动化轨（CC / 弯音）：`song_engine.write_midi` 直接写进 MIDI，渲染时生效
        d['automation'] = automation
    _kcc = sum(len(v['ccs']) for v in automation.values())
    _kbd = sum(len(v['pitch_bends']) for v in automation.values())
    info = {'src_bpm': src_bpm, 'bpm': bpm, 'bar_beats': bar_beats,
            'nbars': nbars, 'mapping': mapping, 'short_dur': short_dur,
            'clipped': clipped, 'cc_count': cc_count, 'bend_count': bend_count,
            'kept_cc': _kcc, 'kept_bends': _kbd, 'lyric_lines': lyric_lines,
            'counts': {t: len(merged[t]) for t in TRACKS if merged[t]}}
    if verbose:
        print('源 %.4f BPM → 曲 %.4f BPM · 一小节 %.2f 拍 · %d 小节 · %d 段'
              % (src_bpm, bpm, bar_beats, nbars, len(sections)))
        print('%-12s %3s %5s %6s  → %s' % ('源轨', 'ch', 'prog', '音符', '引擎轨'))
        for (nm, ch, pr, dst, n) in mapping:
            print('%-12s %3d %5s %6d  → %s' % (nm[:12], ch, pr, n, dst))
        for t in TRACKS:
            if merged.get(t):
                ps = [it[3] for it in merged[t]]
                print('  %-8s %5d 音 · 音域 %3d-%3d' % (t, len(merged[t]), min(ps), max(ps)))
        if short_dur:
            print('  ! %d 个音的时值 <%.2f 拍 → 抬到 %.2f（与 transcribe_to_song 同口径）'
                  % (short_dur, MIN_DUR, MIN_DUR))
        if clipped:
            print('  ! %d 个音超出曲尾 → 夹到最后一小节（不删内容）' % clipped)
        if cc_count or bend_count:
            if _split:
                print('  ✓ Type 0：CC/弯音也按通道拆（保留 %d 条）' % (_kcc + _kbd))
            else:
                print('  ✓ 保留 %d 条 CC / %d 条弯音 → automation（渲染时生效）'
                      % (_kcc, _kbd))
        if lyric_lines:
            print('  ✓ .kar 歌词 %d 行：**已忽略**（歌词不是可演奏内容，不进 song.json）'
                  % lyric_lines)
    return d, info


def build(mid_path, name=None, bpm=None, bars_per_section=8, verbose=True,
          overrides=None, allow_tempo_change=False):
    """读 MIDI 文件 → `build_from_model()`（tempo_map 从解析器取）。"""
    m = midi_file.import_midi(str(mid_path))
    # tempo_map 直接从解析器取：`import_midi` **故意不暴露**它 —— 暴露了会让
    # `export_midi` 在"导入 → 改 bpm → 导出"时静默继承旧变速（truth_eval 尺子自检当场红）。
    import midi_probe as _mp
    _raw = _mp.parse_full(str(mid_path))
    _div = float(_raw.get('division') or 480)
    tmap = [[float(_t) / _div, int(_mm)] for (_t, _mm) in (_raw.get('tempo_map') or [])]
    return build_from_model(m, name=name, bpm=bpm,
                            bars_per_section=bars_per_section, verbose=verbose,
                            overrides=overrides,
                            allow_tempo_change=allow_tempo_change,
                            tempo_map=tmap, source_label=str(mid_path))


def _range_warn(d):
    """打印引擎会做的音域修正（不静默）"""
    for tr, arr in (d.get('notes_extra') or {}).items():
        rg = song_engine.TR_RANGE.get(tr)
        if not rg or not arr:
            continue
        ps = [it[3] for it in arr]
        lo, hi = rg
        if lo <= min(ps) and max(ps) <= hi:
            continue
        print('  ! %s 音域 %d-%d 超出引擎合理范围 %d-%d：引擎会整轨移八度或丢越界音'
              % (tr, min(ps), max(ps), lo, hi))


def main():
    ap = argparse.ArgumentParser(description='任意标准 MIDI → songs/<名>/song.json')
    ap.add_argument('mid', help='输入 .mid/.midi')
    ap.add_argument('--name', default=None, help='曲目名（默认取文件名）')
    ap.add_argument('--out', default=None, help='song.json 输出路径（默认 songs/<名>/song.json）')
    ap.add_argument('--bpm', type=float, default=None, help='目标 BPM（默认沿用 MIDI 的 tempo）')
    ap.add_argument('--bars-per-section', type=int, default=8, help='每段小节数（默认 8）')
    ap.add_argument('--no-compose', action='store_true', help='只写 song.json，不出 .mid')
    ap.add_argument('--force', action='store_true', help='覆盖已存在的曲目目录')
    ap.add_argument('--map', action='append', default=[],
                    help='强制映射：源轨名=引擎轨（可多次，如 "Lead=Melody"）')
    ap.add_argument('--allow-tempo-change', action='store_true',
                    help='多 tempo 时按秒重排到统一 BPM（默认拒绝：直接导会静默错时间轴）')
    a = ap.parse_args()

    mid = os.path.abspath(a.mid)
    if not os.path.exists(mid):
        raise SystemExit('找不到 %s' % mid)
    with open(mid, 'rb') as f:
        if f.read(4) != b'MThd':
            raise SystemExit('%s 不是标准 MIDI（缺 MThd 头）' % mid)

    name = a.name or safe_name(mid)
    out = a.out or os.path.join(ROOT, 'songs', name, 'song.json')
    if a.out is None and os.path.exists(out) and not a.force:
        raise SystemExit('曲目已存在：%s\n要覆盖加 --force（或换 --name）' % out)
    if a.bars_per_section < 1:
        raise SystemExit('--bars-per-section 必须 ≥1')

    overrides = {}
    for item in a.map:
        if '=' not in item:
            raise SystemExit('--map 要写成 源轨名=引擎轨，收到 %r' % item)
        k, v = item.split('=', 1)
        k, v = k.strip().lower(), v.strip()
        if v not in TRACKS:
            raise SystemExit('--map 的目标 %r 不认识（可用 %s）'
                             % (v, ', '.join(TRACKS)))
        overrides[k] = v

    d, info = build(mid, name=name, bpm=a.bpm,
                    bars_per_section=a.bars_per_section, overrides=overrides,
                    allow_tempo_change=a.allow_tempo_change)
    if not d['notes_extra']:
        raise SystemExit('这个 MIDI 里没有可导入的音符（全是空轨？）')

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    json_io.save(out, d)
    print('\n写出 %s（%d 轨 / %d 音）'
          % (out, len(d['notes_extra']), sum(info['counts'].values())))

    _range_warn(d)
    eng = song_engine.load(out)
    ev, nbars = song_engine.build_events(eng)
    print('\n引擎事件数：')
    for t in TRACKS:
        if ev.get(t):
            print('  %-8s %5d' % (t, len(ev[t])))

    if not a.no_compose:
        mid_out = os.path.join(os.path.dirname(os.path.abspath(out)), name + '.mid')
        song_engine.compose(out, mid_out, quiet=True)
        print('\n下一步：')
        print('  %s scripts\\make_song.py %s --no-tune   # 渲染（autotune 会把编配往回推）'
              % (os.path.join(ROOT, '.venv', 'Scripts', 'python.exe'), name))
    return 0


import cli_utf8 as _cu  # noqa: E402
_cu.setup()

if __name__ == '__main__':
    sys.exit(main())
