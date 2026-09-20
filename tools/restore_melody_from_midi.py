# -*- coding: utf-8 -*-
"""从旧 MIDI 恢复 `song.json` 的旋律 —— `melody_gen.py` 覆盖写盘后的抢救。

事故（2026-09-20）：我把 `melody_gen.py` 当**只读诊断**跑（想复现 `melody_step_bias` 的
非零退出），它是**写盘**工具 —— 直接把这个曲子的 5 支旋律全换成了 seed=7 的新旋律。
`git` 未追踪该曲（`?? songs/20_piano_rain/`），没有版本副本；唯一字节真源是**覆盖前
渲染出的 `piano_rain.mid`**（Melody 轨 194 个音，含我后补的引子落点音）。

恢复口径：
  · 起音 = 把绝对 tick 四舍五入到 **0.25 拍网格**再拆成 (小节, 拍) —— 引擎写 MIDI 是
    确定式的，量化回网格能逐音还原（浮点误差不会漏音）
  · 时值 = MIDI 里的 `dur*0.96` → 用 0.25 倍网格找回**原始时值**
  · 只从每支旋律**首次出现**的段落取音（同一支旋律会在多段复用）
"""
import io
import json
import os
import sys

import mido

ROOT = r'D:\software\skill\music-gen'
SJ = os.path.join(ROOT, 'songs', '20_piano_rain', 'song.json')
MID = os.path.join(ROOT, 'songs', '20_piano_rain', 'piano_rain.mid')
PPQ = 480
GRID = 0.25

d = json.load(io.open(SJ, encoding='utf-8'))
B = float(d['meter'][0]) * 4.0 / float(d['meter'][1])          # 一小节几拍（4/4 → 4）

# ---- 读 Melody 轨：(绝对拍, 音高, 弹奏时值) ----
tr = [t for t in mido.MidiFile(MID).tracks if t.name == 'Melody'][0]
tm, on, notes = 0, {}, []
for msg in tr:
    tm += msg.time
    if msg.type == 'note_on' and msg.velocity > 0:
        on[msg.note] = tm / float(PPQ)
    elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
        t0 = on.pop(msg.note, None)
        if t0 is not None:
            notes.append((t0, msg.note, tm / float(PPQ) - t0))
notes.sort()
print('MIDI Melody 音符 %d 个' % len(notes))

# ⚠ **先剔掉引擎派生的"低八度加厚层"**（`patterns.mel_octave`，默认 0.15：只给 ≥1 拍的长音
# 按哈希比例加一个 −12 的音）。第一版没剔，恢复出 86 个音而 MIDI 有 194 个 —— 断言当场拦下。
# 判据：**同一起音时刻上同时存在 p 与 p−12** 的那个低音就是派生层（原始数据里不会有这种重叠）。
_by_t = {}
for (t, p, dur) in notes:
    _by_t.setdefault(round(t, 4), []).append((t, p, dur))
kept = []
for _t, lst in _by_t.items():
    pcs = {p for (_t2, p, _d) in lst}
    for (t, p, dur) in lst:
        if len(lst) > 1 and (p + 12) in pcs:
            continue                       # 派生层：丢掉
        kept.append((t, p, dur))
kept.sort()
print('  剔除低八度加厚层后：%d 个' % len(kept))
notes = kept

# ---- 段落小节起点（按出现顺序）----
marks, b0, seen = [], 0, {}
for si, sec in enumerate(d['sections']):
    marks.append((si, sec, b0))
    b0 += int(sec['bars'])

ORIG = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0, 4.0)


def q_dur(played):
    """弹奏时值 → 原始时值（引擎写的是 `原始*0.96`，反推后吸到 0.25 网格）。"""
    raw = played / 0.96
    return min(ORIG, key=lambda x: abs(x - raw))


mel_new = {}
first = {}
for si, sec, b0 in marks:
    nm = sec['melody']
    if nm in first:                       # 复用段：只取第一次出现的
        continue
    first[nm] = si
    t0 = b0 * B
    t1 = (b0 + int(sec['bars'])) * B
    _in = [(t, p, du) for (t, p, du) in notes if t0 - 1e-6 <= t < t1 + 1e-6]
    out = []
    for (t, p, dur) in _in:
        rel = round((t - t0) / GRID) * GRID          # 吸到 16 分网格（消浮点误差）
        bar = int(rel // B)
        beat = round(rel - bar * B, 4)
        out.append([bar, beat, q_dur(dur), p])
    out.sort(key=lambda x: (x[0], x[1]))
    mel_new[nm] = out
    print('  %-6s 段%d 区间 [%6.1f, %6.1f) 拍到 %d 个音（去重后待写 %d 个）'
          % (nm, si, t0, t1, len(_in), len(out)))

bad = [k for k, v in mel_new.items() if not v]
assert not bad, '有旋律段没恢复出音：%s' % bad
# ⚠ **不能拿"恢复总数 == MIDI 总数"当断言**：MIDI 里同一支旋律会在多段复用，总数必然更多
# （本例恢复 86 支旋律音，渲染后 MIDI 有 194 个音 = 各段复用后的总数）。
# 真正的验收是**恢复后重渲，MIDI 与当前这份逐音符一致**（`--verify` 做这件事）。
print('\n各支旋律恢复 %d 音（渲染后 MIDI 会因段落复用变多，这是正常的）'
      % sum(len(v) for v in mel_new.values()))

d['melody'] = mel_new
d['melody_gen'] = {'profile': 'tender', 'seed': 20, 'dens': 2.6, 'candidates': 4,
                   'mode': 'motif', 'variants': True, 'step_bias': 1.0}
json.dump(d, io.open(SJ, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('\n已恢复并写回（melody_gen.seed 也改回 20）')
