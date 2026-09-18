# -*- coding: utf-8 -*-
"""从鼓分轨 MIDI 提取 `patterns.drum_grid.per_bar` —— **逐小节鼓型**（引擎 Perc 的主力）。

## 为什么需要（`docs/RESTORE-METHOD.md`）

引擎的 Perc 音数**主要由 `drum_grid.per_bar` 决定**，不是 `perc_style` / `arr.perc`。
实测对照（同一首参考曲）：
· 它的最终成品 Perc **3776 音**，全部来自 **208 小节**的 `per_bar`；
· 我靠 `perc_style=pump` + `arr.perc=3~4` 只到 **3241**，而且**鼓型是引擎预设的**、
  不是原曲的 —— "鼓像不像"只能靠这个字段。
（踩过的现场：把 `arr.perc` 从 1/2 提到 3/4，Perc 只从 945 涨到 1170，数字几乎不动。）

## 格式（引擎 `song_engine.py:1323`）

```jsonc
"patterns": {"drum_grid": {"per_bar": [
  {},                                                   // 第 0 小节：空（安静开头）
  {"snare": [[12, 127]], "hat": [[6, 109], [9, 76]]},    // [十六分格 0–15, 力度]
  {"kick": [[4, 114]], "snare": [[0, 88]]}
]}}
```

## 用法

```bash
python scripts/extract_drum_grid.py <鼓分轨.mid> --bpm 150 --bars 208 [--out grid.json]
# 直接写进 song.json：
python scripts/extract_drum_grid.py <鼓分轨.mid> --bpm 150 --song songs/99_x/song.json
```

⚠ **GM 打击乐键 → 引擎只认的四档**（引擎内部固定 `kick=36 snare=38 hat=42 open=46`）：
`35/36→kick` · `38/40→snare` · `42/44→hat` · `46→open`；
其余（嗵鼓/吊镲/叮叮镲…）**并入最近的档**（嗵鼓→snare、镲类→open）——
**不要丢弃**：引擎只认四档，丢掉会让鼓明显变稀。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import midi_file                                              # noqa: E402

# GM 打击乐键 → 引擎四档
SLOT = {}
for _p in (35, 36):
    SLOT[_p] = 'kick'
for _p in (38, 39, 40):                       # 军鼓 / 拍手 / 电军鼓
    SLOT[_p] = 'snare'
for _p in (41, 43, 45, 47, 48, 50):           # 嗵鼓 → 并入 snare（同一个"击打"语义）
    SLOT[_p] = 'snare'
for _p in (42, 44):                           # 闭镲 / 踩镲
    SLOT[_p] = 'hat'
for _p in (46, 49, 51, 52, 53, 55, 57, 59):   # 开镲 / 各类镲 → open
    SLOT[_p] = 'open'


def bars_from_notes(notes, bars=None):
    """`midi_file` 的 notes（`[start_beat, dur, pitch, vel]`）→ `per_bar` 列表。"""
    out = {}
    for it in notes:
        if len(it) < 4:
            continue
        beat = float(it[0])
        pitch = int(it[2])
        vel = int(it[3]) if it[3] else 100
        slot = SLOT.get(pitch)
        if slot is None:
            # 未映射的键：按音高落到最近的档（高频镲类 → open，其余 → snare）
            slot = 'open' if pitch >= 49 else 'snare'
        bar = int(beat // 4.0)                     # 每小节 4 拍
        grid = int(round((beat - bar * 4.0) * 4.0)) % 16   # 十六分格 0–15
        rec = out.setdefault(bar, {})
        rec.setdefault(slot, []).append([grid, max(1, min(127, vel))])
    n = int(bars) if bars else (max(out) + 1 if out else 0)
    per_bar = []
    for b in range(n):
        rec = out.get(b) or {}
        per_bar.append({k: sorted(v) for k, v in rec.items()})
    return per_bar


def main():
    import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
    ap = argparse.ArgumentParser(description='从鼓分轨 MIDI 提取 drum_grid.per_bar')
    ap.add_argument('mid', help='鼓分轨 MIDI（Demucs drums 轨的转录）')
    ap.add_argument('--bpm', type=float, default=0.0,
                    help='参考曲 BPM（0 = 用 MIDI 自带的）')
    ap.add_argument('--bars', type=int, default=0, help='总小节数（0 = 按音符推）')
    ap.add_argument('--out', default=None, help='另存一份 grid.json')
    ap.add_argument('--song', default=None, help='直接写进这个 song.json 的 patterns')
    ap.add_argument('--song-bars', type=int, default=0,
                    help='配合 --song：以 song.json 的小节总数为准（更稳）')
    a = ap.parse_args()
    if not os.path.exists(a.mid):
        raise SystemExit('找不到 %s' % a.mid)

    d = midi_file.import_midi(a.mid)
    src_bpm = float(d.get('bpm') or 120.0)
    notes = [it for tr in (d.get('tracks') or []) for it in (tr.get('notes') or [])]
    if not notes:
        raise SystemExit('%s 里没有音符' % a.mid)

    # ⚠ **必须真的缩放**：转录工具的 MIDI 按**它自己的 bpm** 记"拍"，而目标曲目用 `--bpm`。
    #   同一段音频在两种 bpm 下**秒数相同、拍数不同** → 拍要乘 `目标bpm / 源bpm`。
    #   第一版**只 print 了提示、没做缩放** → 小节号整体偏大 1.6 倍，超出 `--song-bars`
    #   的鼓点被**静默丢掉**（实测 2776 音只剩 1935 点，Perc 因此差一倍）。
    k = (a.bpm / src_bpm) if (a.bpm and src_bpm) else 1.0
    if abs(k - 1.0) > 1e-6:
        print('  时间轴换算：源 %.1f → 目标 %.1f bpm，拍 × %.4f'
              % (src_bpm, a.bpm, k))
        notes = [[it[0] * k, it[1] * k, it[2], it[3]] for it in notes]

    bars = a.song_bars or a.bars
    per_bar = bars_from_notes(notes, bars)

    # 自检：太稀说明映射错了（引擎只认四档，丢档会让 Perc 明显变少）
    tot = sum(len(v) for r in per_bar for v in r.values())
    empty = sum(1 for r in per_bar if not r)
    print('  小节 %d · 鼓点 %d · 空小节 %d · 每小节均 %.2f'
          % (len(per_bar), tot, empty, tot / max(1, len(per_bar))))
    for s in ('kick', 'snare', 'hat', 'open'):
        c = sum(len(r.get(s) or []) for r in per_bar)
        print('    %-6s %5d 点' % (s, c))
    if tot and tot / max(1, len(per_bar)) < 2.0:
        print('  ! 每小节不到 2 个鼓点 —— 检查分轨是不是真的鼓，或映射表要不要补')

    if a.out:
        json.dump({'per_bar': per_bar}, open(a.out, 'w', encoding='utf-8'),
                  ensure_ascii=False)
        print('写 %s' % a.out)
    if a.song:
        s = json.load(open(a.song, encoding='utf-8'))
        s.setdefault('patterns', {})['drum_grid'] = {'per_bar': per_bar}
        json.dump(s, open(a.song, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('已写入 %s 的 patterns.drum_grid.per_bar' % a.song)
    if not (a.out or a.song):
        print(json.dumps({'per_bar': per_bar[:3]}, ensure_ascii=False)[:400])
    return 0


if __name__ == '__main__':
    sys.exit(main())
