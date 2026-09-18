# -*- coding: utf-8 -*-
"""转录结果 → `song.json`：**还原/扒带的正道入口**。

## 何时用

扒完一首参考曲（Demucs 分轨 + YMT3 / Basic Pitch 转录）之后，把结果变成一份
**能被引擎编配**的 `song.json` —— 之后交给 `compose.py` / `make_song.py` 就行。

**为什么不手工拼 MIDI**（PITFALLS 185，实测最大的一条教训）：
引擎的编配、音色分配、按 `arr.density` 抽样服从段落结构、混音、调参全是现成的；
手工拼轨 = 自己重造它一半功能还做不全。同一批转录音符，走本工具后
**5000–10000Hz 的缺口从 −12.6dB 收到 −3.8dB**。

## 用法

```bash
python scripts/transcribe_to_song.py <曲名> \
    --bpm 75 --key-mode minor \
    --chords-log <analyze_chords.py --bpm 75 的输出文件> \
    --boundaries 179.2,209.5,221.0,323.5,331.9 \
    --sec-names A,B,C,D,Ending \
    --mid Piano=.../piano.mid --mid Hook=.../guitar.mid \
    --mid Bass=.../bass.mid --mid Strings=.../other.mid --mid Drums=.../drums.mid
```

生成 `songs/<曲名>/song.json`（含 `chords` / `sections` / `melody` / `notes_extra`）。
随后：`cp` 一份 `compose.py`（照 `songs/01_daily_morning/compose.py` 那 14 行）→
`make_song.py <曲名>`。

## 关键约定（都是踩出来的，别改）

1. **小节号两套口径**：`melody` 用**段内**小节号，`notes_extra` 用**全局**小节号。
   写反了前者会静默不发声（引擎现在会拦，见 PITFALLS 182）。
2. **每段的 `chords` 个数必须等于 `bars`** —— 否则 `chords[i+1]` 越界，
   自检里 8 条一起 IndexError。
3. **`sections[].melody` 必须给出**，且一个段一个键；让短段复用长段的旋律键会被
   "小节号超段长"拦下。
4. **轨名只能用引擎认识的**（`Piano/Bass/Hook/Strings/Pad/Arp/Glock/Drums/Melody`）；
   吉他对应 **`Hook`**。鼓的音高是 GM 鼓键，直接进 `Drums`。
5. `--full` 会写 `patterns.notes_extra_full`（**不抽样**）。默认抽样是为"服从段落结构"
   设计的，还原要的是忠实 —— 这个取舍交给人。
6. 段边界按**秒**给（`analyze_sections.py` 的输出），本工具按 `--bpm` 换算成小节。
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import json_io                                                # noqa: E402
import midi_file                                              # noqa: E402
import song_engine as se                                      # noqa: E402

# 引擎认识的轨名（吉他 → Hook）
TRACKS = ('Piano', 'Bass', 'Hook', 'Strings', 'Pad', 'Arp', 'Glock', 'Drums', 'Melody')
# 一个段最多放多少音进 melody（防止把整轨钢琴都塞进旋律）
MEL_MAX_PER_BAR = 2


def read_notes(path):
    """→ [(起始秒, 结束秒, 音高)]。

    ⚠ **不用 `mido`**：主 venv 里没有它（只有 `.venv-ml` 有），顶层 import 会让
      `selftest` 的 `import_all` 直接 FAIL（PITFALLS 173 同族）。
      改用工具链自带的 `midi_file`（纯标准库）。
    ⚠ `midi_file` 的 `notes` 起始时间是**拍**（四分音符），乘 `60/bpm` 才是秒 ——
      曾当秒用，结果全曲音挤进第 0 格（PITFALLS 184①）。
    """
    d = midi_file.import_midi(str(path))
    spb = 60.0 / max(1e-9, float(d.get('bpm') or 120.0))
    out = []
    for tr in d.get('tracks') or []:
        for it in tr.get('notes') or []:
            if len(it) < 4:
                continue
            st = float(it[0]) * spb
            out.append((st, st + float(it[1]) * spb, int(it[2])))
    out.sort()
    return out


def parse_chords(path):
    """解析 `analyze_chords.py` 的输出 → [(小节号, 和弦名, 起音数)]。
    行格式：`  12 | Fsus4     | Vsus4    |    61  |   -15.3`"""
    rows = []
    pat = re.compile(r'^\s*(\d+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\d+)')
    with open(path, encoding='utf-8') as f:
        for line in f:
            m = pat.match(line)
            if m:
                rows.append((int(m.group(1)), m.group(2), int(m.group(4))))
    return rows


def main():
    import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
    ap = argparse.ArgumentParser(description='转录结果 → song.json')
    ap.add_argument('name', help='曲目名（会成为 songs/<name>/）')
    ap.add_argument('--bpm', type=float, required=True)
    ap.add_argument('--key-mode', default='minor', choices=('major', 'minor'))
    ap.add_argument('--style', default='ballad',
                    help='引擎风格预设（见 new_song.py --list-styles）')
    ap.add_argument('--chords-log', required=True,
                    help='analyze_chords.py 的输出文件（本工具从中解析小节级和弦）')
    ap.add_argument('--boundaries', required=True,
                    help='段边界（秒，逗号分隔，含 0 与总时长），如 0,179.2,209.5,331.9')
    ap.add_argument('--sec-names', required=True,
                    help='段名（逗号分隔），个数 = 边界数 − 1，如 A,B,C,Ending')
    ap.add_argument('--mid', action='append', default=[],
                    help='轨=文件，可多次；轨名见 --help 的约定 4')
    ap.add_argument('--melody-from', default='Piano', help='从哪条轨抽旋律')
    ap.add_argument('--full', action='store_true', help='写 patterns.notes_extra_full')
    ap.add_argument('--out', default=None, help='输出 song.json 路径（默认 songs/<name>/）')
    a = ap.parse_args()

    bar_sec = 4 * 60.0 / a.bpm
    bounds = [float(x) for x in a.boundaries.split(',') if x.strip()]
    names = [x.strip() for x in a.sec_names.split(',') if x.strip()]
    if len(names) != len(bounds) - 1:
        raise SystemExit('段名 %d 个 ≠ 边界 %d 个 − 1' % (len(names), len(bounds)))

    # —— 和弦表 ——
    rows = parse_chords(a.chords_log)
    if len(rows) < 8:
        raise SystemExit('从 %s 只解析出 %d 行小节和弦 —— 检查是不是 analyze_chords 的输出'
                         % (a.chords_log, len(rows)))
    chord_names = [r[1] for r in rows]
    onsets = {r[0]: r[2] for r in rows}

    def tones(name):
        m = re.match(r'^([A-G]#?)(.*)$', name)
        if not m:
            return None
        pc = {'C': 0, 'C#': 1, 'D': 2, 'D#': 3, 'E': 4, 'F': 5, 'F#': 6,
              'G': 7, 'G#': 8, 'A': 9, 'A#': 10, 'B': 11}[m.group(1)]
        steps = {'m7': [0, 3, 7, 10], '7': [0, 4, 7, 10], 'sus4': [0, 5, 7],
                 'm': [0, 3, 7], '': [0, 4, 7]}.get(m.group(2), [0, 4, 7])
        # ⚠ 音级必须落在 **C 的整数倍**上（60 = C4）；写成 64 会整体偏 4
        return 34, [60 + pc + s for s in steps]

    chords = {}
    for cn in chord_names:
        if cn not in chords:
            t = tones(cn)
            if t:
                chords[cn] = [t[0], t[1]]

    # —— 段落 ——
    starts = [int(round(b / bar_sec)) for b in bounds]
    secs, mel = [], {}
    for i, nm in enumerate(names):
        lo, hi = starts[i], starts[i + 1]
        nb = max(1, hi - lo)
        seg = chord_names[lo:hi]
        while len(seg) < nb:                        # 契约 2：和弦数 == 小节数
            seg.append(seg[-1] if seg else 'C')
        seg = seg[:nb]
        dens = sorted(0 if onsets.get(lo + k, 0) <= 6 else
                      1 if onsets.get(lo + k, 0) <= 20 else
                      2 if onsets.get(lo + k, 0) <= 45 else 3
                      for k in range(nb))
        mid_d = dens[len(dens) // 2]
        secs.append({
            'name': nm, 'bars': nb, 'chords': seg,
            'melody': nm,                           # 契约 3：一段一键
            'arr': {'bass': True, 'piano': True, 'pad': True,
                    'uku': nm in ('A', 'B'),
                    'arp': nm not in ('C', 'Ending'),
                    'strings': nm not in ('C', 'Ending'),
                    'glock': nm not in ('C', 'Ending'),
                    'ep': False,
                    'shimmer': nm not in ('C', 'Ending'),
                    'perc': 0 if nm in ('C', 'Ending') else (1 if nm == 'A' else 2),
                    'density': 0 if nm == 'C' else (1 if nm == 'Ending' else mid_d)},
            'mode': a.key_mode,
        })
        if nm == 'Ending':
            secs[-1]['arr']['ending_fade'] = 3      # 实测收尾 3 小节力度趋 0
        mel[nm] = []

    # —— 逐轨音符 ——
    ne, mel_src = {}, None
    for spec in a.mid:
        if '=' not in spec:
            raise SystemExit('--mid 要写成 轨=文件，收到 %r' % spec)
        tr, path = spec.split('=', 1)
        if tr not in TRACKS:
            raise SystemExit('轨名 %r 不在引擎认识的 %s 里（吉他请用 Hook）' % (tr, TRACKS))
        if not os.path.exists(path):
            raise SystemExit('找不到 %s' % path)
        notes = read_notes(path)
        ne[tr] = [[int(st / bar_sec), round((st - int(st / bar_sec) * bar_sec) / (bar_sec / 4), 2),
                   max(0.25, round((en - st) / (bar_sec / 4), 2)), p]
                  for (st, en, p) in notes]
        if tr == a.melody_from:
            mel_src = notes
        print('  %-8s %5d 音 · 覆盖 %d 小节' % (tr, len(notes), len({int(s / bar_sec) for s, _e, _p in notes})))

    # —— 抽旋律（从 --melody-from 那条轨取每小节的高音区）——
    if mel_src:
        bar0 = 0
        for sec in secs:
            rows_m = []
            for b in range(bar0, bar0 + sec['bars']):
                ns = sorted([(s, p) for (s, _e, p) in mel_src if int(s / bar_sec) == b],
                            key=lambda x: x[1])
                if len(ns) < 4:
                    continue
                for s, p in ns[int(len(ns) * 0.70):][:MEL_MAX_PER_BAR]:
                    rows_m.append((b - bar0, (s - b * bar_sec) / (bar_sec / 4), p))
            out = []
            for j, (bb, beat, p) in enumerate(rows_m):
                if j + 1 < len(rows_m) and rows_m[j + 1][0] == bb:
                    dur = max(0.5, round(rows_m[j + 1][1] - beat, 2))
                else:
                    dur = max(0.5, round(4.0 - beat, 2))
                out.append([bb, round(beat, 2), dur, p])
            mel[sec['name']] = out
            bar0 += sec['bars']

    d = {
        'name': a.name, 'bpm': float(a.bpm), 'meter': [4, 4],
        'desc': '由 transcribe_to_song.py 从转录生成（%g BPM / %d 小节）'
                % (a.bpm, sum(s['bars'] for s in secs)),
        'style': a.style,
        'patterns': {'bass_style': 'simple', 'perc_style': 'light',
                     'melody_dyn': True, 'arr_by_role': False,
                     'section_gap': 3.0, 'seg_fade': True,
                     'range_fix': True, 'legato_trim': True, 'dyn_vel': 8.0},
        'chords': chords, 'sections': secs, 'melody': mel,
        'notes_extra': ne,
    }
    if a.full:
        d['patterns']['notes_extra_full'] = True

    out = a.out or os.path.join(ROOT, 'songs', a.name, 'song.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json_io.save(out, d)
    print('写 %s（%d 段 / %d 小节 / %d 和弦种 / notes_extra %d 轨 %d 音）'
          % (out, len(secs), sum(s['bars'] for s in secs), len(chords),
             len(ne), sum(len(v) for v in ne.values())))
    print('下一步：放一份 compose.py 到同目录 → make_song.py %s' % a.name)
    return 0


if __name__ == '__main__':
    sys.exit(main())
