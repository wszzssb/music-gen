# -*- coding: utf-8 -*-
"""restore_gap_fill.py —— **分段补漏**：原曲有音乐、我们的 MIDI 却没响的地方，按**小节**补回来。

## 何时用

还原/扒谱交付前，或用户说"**有的地方不像 / 有音乐的地方没声音**"时。
先用它**定位**（`--dry` 只列表不改），再决定补。

## 为什么不整曲一刀切（用户 2026-09-25 定的规矩）

> "以后除了我明确说，**不要直接全曲处理，都要分段落处理**"

整曲替换/整曲关层会把**没问题的段一起改掉**（实测上一轮把 25 段的 `arr` 一刀切全关，
用户随即反馈"有的地方不像"）。本工具**逐小节**判定、**只补缺的那些小节**，并打印
**逐段×逐小节改动表**供核对（`SKILL.md` §9b 的硬要求）。

## 判据（两条，都对着"没响"这个现象）

某小节满足任一条就该补：
  ① 该小节**我们一个音都没有**（最典型的"音乐没了"）—— 实测 `siren_end` 小节 17、
     98–99（原曲 RMS −17~−22dB，成品 −27~−61dB）
  ② 我们的音数 **< 分轨转录的 `--ratio` 倍**（默认 0.6）—— 明显偏空

补的**来源**是 Demucs 分轨各自转录出来的音符（`transcribe_ymt3.py --no-song` 的产物）——
它们是**音频里的真实证据**，不是引擎按和弦生成的（那种"凭空补音"已由 `PITFALLS` 255 封掉）。

## 用法

```bash
# ① 先看（不改文件）：哪些小节缺、分轨里有多少音
python scripts/restore_gap_fill.py <曲目名> --stems-midi <目录> [--stems-midi <目录2>] --dry
# ② 确认后补（会写 song.json，并把改动表打印出来）
python scripts/restore_gap_fill.py <曲目名> --stems-midi <目录> [--ratio 0.6] [--apply]

# 分轨转录怎么来（`.venv-ml`）：
#   python scripts/transcribe_ymt3.py <demucs 输出目录> -o <输出目录> --no-song
```

⚠ 补进来的是**引擎已知的轨**（`Drums/Bass/Piano/Hook/Strings/Pad/Glock/Melody`），
按分轨来源映射（见 `TRACK_MAP`）；同小节同拍同音高的音**不重复加**。
"""
import argparse
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import json_io                                                 # noqa: E402
import midi_file                                               # noqa: E402
import song_engine                                             # noqa: E402  # TR_RANGE

# 分轨/通道名 → 引擎轨名（`transcribe_ymt3` 的 13 通道名在左边）
TRACK_MAP = {
    'drums': 'Drums', 'Drums': 'Drums',
    'bass': 'Bass', 'Bass': 'Bass',
    'piano': 'Piano', 'Acoustic Piano': 'Piano',
    'guitar': 'Hook', 'Guitar': 'Hook', 'Guitar (clean)': 'Hook',
    'other': 'Strings', 'Strings': 'Strings',
    'vocals': 'Melody', 'Synth Lead': 'Melody', 'Singing Voice': 'Melody',
    'Synth Pad': 'Pad', 'Pad': 'Pad',
    'Chromatic Percussion': 'Glock',
    'Brass': 'Strings', 'Reed': 'Strings',
}


def _src_name(path):
    """从文件名/轨名猜来源（`bar17_htdemucs_6s_piano.wav` → piano）。"""
    b = os.path.basename(path).lower()
    for k in ('drums', 'bass', 'piano', 'guitar', 'other', 'vocals'):
        if k in b:
            return k
    return None


def collect_sources(mid_paths, bar_sec):
    """→ `{引擎轨: {(bar, beat16, pitch): (dur, vel)}}`（从分轨转录的 MIDI 收集）。"""
    out = {}
    for p in mid_paths:
        try:
            m = midi_file.import_midi(p)
        except Exception:                                      # noqa: BLE001
            continue
        spb = 60.0 / max(1e-9, float(m.get('bpm') or 120.0))
        hint = _src_name(p)
        for tr in (m.get('tracks') or []):
            nm = tr.get('name') or ''
            eng = TRACK_MAP.get(nm) or TRACK_MAP.get(hint or '')
            if not eng:
                continue
            bucket = out.setdefault(eng, {})
            for it in (tr.get('notes') or []):
                if len(it) < 4:
                    continue
                st = float(it[0]) * spb
                bar = int(st / bar_sec)
                beat = (st - bar * bar_sec) / (bar_sec / 4.0)
                key = (bar, int(round(beat * 4)), int(it[2]))
                bucket[key] = (max(0.25, float(it[1]) * spb / (bar_sec / 4.0)),
                               int(it[3]) if it[3] else 84)
    return out


def existing_keys(d, bar_sec):
    """→ `(每小节现有音数, 现有 (bar,beat16,pitch) 集合)`。"""
    cnt, keys = {}, set()
    for _tr, v in (d.get('notes_extra') or {}).items():
        ns = v.get('notes') if isinstance(v, dict) else v
        for n in (ns or []):
            b = int(n[0])
            cnt[b] = cnt.get(b, 0) + 1
            keys.add((b, int(round(float(n[1]) * 4)), int(n[3])))
    grid = ((d.get('patterns') or {}).get('drum_grid') or {}).get('per_bar') or []
    for b, cell in enumerate(grid):
        c = sum(len(v) for v in cell.values()) if isinstance(cell, dict) else len(cell)
        if c:
            cnt[b] = cnt.get(b, 0) + c
    return cnt, keys


def bar_rms(audio, bar_sec, nbars):
    """→ `{小节: RMS dBFS}`（**原曲**逐小节能量）。

    ⚠ 这道门槛是必需的（2026-09-25 实测踩到）：Demucs 分轨在**近乎静音**处会留下残余，
    被转录器当成音符 —— `siren_end` 结尾两小节原曲是 **−52 / −70dB**（等于没声音），
    分轨却"转出" 12 个 Bass 音，补进去之后成品变成 **−9.5 / −12.4dB**，
    正好又犯了"该没有声音的地方出现了声音"。
    """
    import soundfile as sf
    y, sr = sf.read(audio, always_2d=True)
    y = y.mean(axis=1)
    out = {}
    for b in range(nbars):
        i0, i1 = int(b * bar_sec * sr), int((b + 1) * bar_sec * sr)
        if i1 <= i0 or i1 > len(y):
            out[b] = -99.0
            continue
        out[b] = 20.0 * math.log10(float(np.sqrt(np.mean(y[i0:i1] ** 2))) + 1e-9)
    return out


def bimodal_gate(vals, gap_min=12.0, floor_off=35.0):
    """逐小节能量 → 门限，由分布**双峰**标定（与 `filter_song_by_stem` 同口径）。

    **空档 < `gap_min` 就退回 `max - floor_off` 兜底**（不硬筛）—— 硬挑门限就是拍数字。
    返回 `(门限dB 或 None, 空档dB)`。
    """
    v = sorted(x for x in vals if x > -90.0)
    if len(v) < 4:
        return None, 0.0
    gap, at = 0.0, None
    for i in range(1, len(v)):
        g = v[i] - v[i - 1]
        if g > gap and 0.1 <= i / float(len(v)) <= 0.9:
            gap, at = g, i
    if at is not None and gap >= gap_min:
        return (v[at - 1] + v[at]) / 2.0, gap
    return max(v) - floor_off, gap


# 引擎轨 → 分轨名（`--stems-audio` 的逐分轨能量门用）
STEM_OF = {'Bass': 'bass', 'Drums': 'drums', 'Piano': 'piano', 'Hook': 'guitar',
           'Strings': 'other', 'Pad': 'other', 'Melody': 'vocals', 'Glock': 'other'}


def stem_active_bars(stems_audio, eng, bar_sec, nbars, gap_min=12.0):
    """该引擎轨对应的**分轨**逐小节在不在响 → `(set(小节) 或 None, 门限, 空档)`。

    ## 为什么必须有这道门（2026-09-26 实测，`dear_good_friends`）
    整混音的门（`--min-rms`）**挡不住"某条分轨自己没响"**：那首曲 S04/S07 的
    `h6_guitar` 是 **−29 dB（在响）**、而 S01 的 `h6_bass` 是 **−86 dB（等于没有）**，
    可 `h6_bass` 照样转录出 **204 个音** —— 拿整混音门放行，就会把这些幻觉音补进谱面。
    逐分轨量才分得开：**在场 −0~−18dB / 缺席 −25~−65dB**（双峰空档 31.8dB）。
    """
    stem = STEM_OF.get(eng)
    if not stem or not stems_audio or not os.path.isdir(stems_audio):
        return None, None, 0.0
    path = None
    for f in sorted(os.listdir(stems_audio)):
        if stem in f.lower() and f.lower().endswith(('.wav', '.flac')):
            path = os.path.join(stems_audio, f)
            break
    if not path:
        return None, None, 0.0
    rm = bar_rms(path, bar_sec, nbars)
    thr, gap = bimodal_gate(list(rm.values()), gap_min=gap_min)
    if thr is None:
        return None, None, gap
    return {b for b, v in rm.items() if v >= thr}, thr, gap


def prune_quiet(d, bar_sec, nbars, audio, min_rms):
    """**删掉"原曲静音"的小节里我们却有的音**（与补音对称的能力：符合原曲）。

    实测（2026-09-25）：结尾 139–142 小节原曲淡出到 **−52 → −99 dBFS**（等于没声音），
    而我们的成品还有 **−30 → −59 dB**（转录音符 + 渲染尾巴）—— 用户听到的就是
    "该没有声音的地方出现了声音"。补音侧由 `--min-rms` 挡，删音侧由这里收。
    """
    rm = bar_rms(audio, bar_sec, nbars)
    quiet = {b for b in range(nbars) if rm.get(b, -99.0) < min_rms}
    removed = {}
    for tr, v in (d.get('notes_extra') or {}).items():
        is_dict = isinstance(v, dict)
        arr = (v.get('notes') or []) if is_dict else v
        if not arr:
            continue
        keep = [n for n in arr if int(n[0]) not in quiet]
        if len(keep) != len(arr):
            removed[tr] = len(arr) - len(keep)
            if is_dict:
                v['notes'] = keep
            else:
                d['notes_extra'][tr] = keep
    return removed, sorted(quiet)


def run(song, stems_midi, ratio=0.6, min_src=2, apply_=False,
        audio=None, min_rms=-38.0, prune=False, stems_audio=None, stem_gap=12.0):
    d = json.load(open(song, encoding='utf-8'))
    bpm = float(d.get('bpm') or 120.0)
    meter = d.get('meter') or [4, 4]
    bar_beats = float(meter[0]) * 4.0 / float(meter[1])
    bar_sec = bar_beats * 60.0 / bpm
    nbars = sum(int(s.get('bars') or 0) for s in (d.get('sections') or []))
    mids = []
    for root in stems_midi:
        for dp, _dn, fn in os.walk(root):
            mids += [os.path.join(dp, f) for f in fn if f.lower().endswith('.mid')]
    if not mids:
        raise SystemExit('没找到任何分轨 MIDI（--stems-midi 目录里要有 *.mid）')
    src = collect_sources(mids, bar_sec)
    cnt, have = existing_keys(d, bar_sec)

    per_bar_src = {}
    for eng, bucket in src.items():
        for (b, _q, _p) in bucket:
            per_bar_src[b] = per_bar_src.get(b, 0) + 1

    # 段名（便于打印"逐段×逐小节"表）
    sec_of, s0, names = {}, 0, []
    for s in d['sections']:
        names.append((s['name'], s0, s0 + int(s['bars'])))
        for b in range(s0, s0 + int(s['bars'])):
            sec_of[b] = s['name']
        s0 += int(s['bars'])

    todo, add_total = [], 0
    for b in range(nbars):
        a, c = cnt.get(b, 0), per_bar_src.get(b, 0)
        if c < min_src:
            continue
        miss = (a == 0) or (a < c * ratio)
        if miss:
            todo.append((b, sec_of.get(b, '?'), a, c))

    # **原曲能量门槛**：原曲本来就是静音的小节**不许补**（分轨在静音处的残余是假音）
    quiet = []
    if audio:
        rm = bar_rms(audio, bar_sec, nbars)
        keep = []
        for t in todo:
            if rm.get(t[0], -99.0) >= min_rms:
                keep.append(t)
            else:
                quiet.append((t[0], rm.get(t[0], -99.0)))
        todo = keep
    # **逐分轨能量门**（opt-in `--stems-audio`）：某条分轨自己在该小节没响就不许从它补。
    # 整混音门（`--min-rms`）挡不住这个 —— 实测 `dear_good_friends`：S01 的 `h6_bass`
    # 是 −86dB（等于没有）却转出 204 个音，S04/S07 的 `h6_guitar` 是 −29dB（在响）却只补 4~6 个。
    allow, gate_info, blocked = {}, {}, {}
    if stems_audio:
        for eng in src:
            bars, thr, gap = stem_active_bars(stems_audio, eng, bar_sec, nbars,
                                              gap_min=stem_gap)
            allow[eng] = bars
            gate_info[eng] = (thr, gap)
        tset0 = {x[0] for x in todo}
        for eng, bucket in src.items():
            ok = allow.get(eng)
            if ok is None:
                continue
            blocked[eng] = sum(1 for (b, _q, _p) in bucket
                               if b in tset0 and (b, _q, _p) not in have and b not in ok)

    for eng, bucket in src.items():
        ok = allow.get(eng)
        for (b, _q, _p) in bucket:
            if b not in {x[0] for x in todo} or (b, _q, _p) in have:
                continue
            if ok is not None and b not in ok:
                continue
            add_total += 1

    print('== %s ==' % os.path.basename(os.path.dirname(song)))
    print('   分轨 MIDI %d 个 · 来源轨 %s' % (len(mids), '、'.join(sorted(src))))
    print('   需补小节 **%d / %d**（判据：我们 0 音，或 < 分轨的 %.0f%%）'
          % (len(todo), nbars, ratio * 100))
    if quiet:
        print('   ⚠ 因**原曲本来就是静音**（< %.0f dBFS）挡掉 %d 个小节：%s'
              % (min_rms, len(quiet),
                 '、'.join('%d(%.0fdB)' % (b, v) for b, v in quiet[:8])))
    if stems_audio:
        print('   **逐分轨能量门**（--stems-audio，空档门 %.0fdB）：' % stem_gap)
        for eng in sorted(gate_info):
            thr, gap = gate_info[eng]
            if thr is None:
                print('     %-9s 分轨缺失/样本不足 → **不设门**（会退回整混音门）' % eng)
            else:
                print('     %-9s 门限 %7.1fdB（空档 %4.1fdB）· 在场 %d 小节 · 挡掉 %d 音'
                      % (eng, thr, gap, len(allow[eng]), blocked.get(eng, 0)))
    if todo:
        print('\n   段     小节   我们  分轨')
        cur = None
        for b, nm, a, c in todo:
            if nm != cur:
                print('   ── %s' % nm)
                cur = nm
            print('   %-6s %4d  %5d %5d' % ('', b, a, c))
    print('\n   可补音符合计 **%d**（去重后）' % add_total)

    if not apply_:
        print('\n   （--dry 模式：没有改任何文件。确认后加 `--apply` 再跑）')
        return 0

    tset = {x[0] for x in todo}
    added, dropped = {}, 0
    for eng, bucket in src.items():
        arr = d.setdefault('notes_extra', {}).setdefault(eng, [])
        rng = song_engine.TR_RANGE.get(eng)
        for (b, q, p), (dur, vel) in sorted(bucket.items()):
            ok = allow.get(eng)
            if b not in tset or (b, q, p) in have:
                continue
            if ok is not None and b not in ok:      # 逐分轨能量门
                continue
            # ⚠ **逐音夹进乐器合理音域**（PITFALLS 253）：分轨转录会给"不属于该乐器"的音高，
            #   原样写进来会让引擎**整轨移八度**（把没问题的音一起改掉）。装不下的就不补。
            if rng:
                q2 = int(p)
                while q2 < rng[0]:
                    q2 += 12
                while q2 > rng[1]:
                    q2 -= 12
                if not (rng[0] <= q2 <= rng[1]):
                    dropped += 1
                    continue
                p = q2
            arr.append([b, round(q / 4.0, 2), round(float(dur), 2), int(p), int(vel)])
            added[eng] = added.get(eng, 0) + 1
    if dropped:
        print('   （%d 个音移八度也装不进该轨音域 → 没补，宁缺勿错）' % dropped)
    for eng in added:
        arr = d['notes_extra'][eng]
        if isinstance(arr, list):
            arr.sort(key=lambda n: (n[0], n[1], n[3]))
    # **对称的那一半**：原曲静音的小节里我们不该有音
    if prune and audio:
        rm2 = bar_rms(audio, bar_sec, nbars)
        rm_removed, quiet2 = prune_quiet(d, bar_sec, nbars, audio, min_rms)
        if rm_removed:
            print('   原曲静音处**删音**：%s（小节 %s）'
                  % ('、'.join('%s −%d' % (k, v) for k, v in sorted(rm_removed.items())),
                     '、'.join(str(b) for b in quiet2[:10])))
    json_io.save(song, d)
    print('\n   已写回 %s' % song)
    print('   补入：%s' % '、'.join('%s +%d' % (k, v) for k, v in sorted(added.items())))
    return 0


def main():
    ap = argparse.ArgumentParser(description='分段补漏：原曲有音、我们没响的小节，按小节补回来')
    ap.add_argument('song', help='曲目名（songs/<名>）或 song.json 路径')
    ap.add_argument('--stems-midi', action='append', required=True,
                    help='分轨转录 MIDI 的目录（可多次）')
    ap.add_argument('--ratio', type=float, default=0.6,
                    help='我们的音数 < 分轨×该值时判为"偏空"（默认 0.6）')
    ap.add_argument('--min-src', type=int, default=2, help='分轨至少这么多音才纳入判定（默认 2）')
    ap.add_argument('--audio', default=None,
                    help='原曲音频：用它做**能量门槛**（原曲本来就静音的小节不许补）')
    ap.add_argument('--min-rms', type=float, default=-38.0,
                    help='原曲该小节 RMS 低于此值就跳过（默认 -38 dBFS）')
    ap.add_argument('--dry', action='store_true', help='只看不改（默认）')
    ap.add_argument('--prune-quiet', action='store_true',
                    help='对称能力：**删掉**原曲静音（< --min-rms）小节里我们却有的音')
    ap.add_argument('--stems-audio', default=None,
                    help='Demucs 分轨 wav 目录：**逐分轨能量门** —— 某条分轨自己在该小节'
                         '没响就不许从它补（整混音门挡不住这个，见 stem_active_bars 的 docstring）')
    ap.add_argument('--stem-gap', type=float, default=12.0,
                    help='逐分轨门限的**双峰空档**下限（默认 12dB；小于它就退回 max-35dB 兜底）')
    ap.add_argument('--apply', action='store_true', help='真的写回 song.json')
    a = ap.parse_args()
    song = a.song
    if not song.endswith('.json'):
        song = os.path.join(ROOT, 'songs', song, 'song.json')
    if not os.path.isfile(song):
        raise SystemExit('找不到 %s' % song)
    return run(song, a.stems_midi, a.ratio, a.min_src, a.apply and not a.dry,
               a.audio, a.min_rms, a.prune_quiet, a.stems_audio, a.stem_gap)


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                          # noqa: BLE001
        pass
    sys.exit(main())
