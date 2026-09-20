#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""midi_file.py —— **标准 MIDI 文件的导入 / 导出**（编辑器的地基：任意 .mid 可编辑、可原样导出）

和 `song_engine.write_midi` 的分工：那个写的是"我们的曲子"（song.json → 固定 7 轨、程序号由
风格预设给）；本模块处理**任意外部 .mid**（Type 0/1、任意轨数、任意通道、力度/CC/拍号都要保住），
因为编辑器的契约是"导入什么、编辑完导出还是那个文件"。

数据模型（**只存事实，不做修正**；量化/移调/力度都是编辑器的活）：
  {
    "format": 1, "division": 480, "bpm": 120.0, "timesig": [4, 4],
    "end_beat": 32.0, "title": "...",
    "tracks": [
      {"index": 0, "name": "Melody", "channel": 0, "program": 0,
       "drum": false, "mute": false, "solo": false, "hidden": false,
       "notes": [[start_beat, dur_beat, pitch, vel], ...],   # 拍 = 四分音符
       "ccs": [[beat, cc, val], ...],
       "program_changes": [[beat, prog], ...],
       "markers": [[beat, text], ...]}
    ]
  }

用法（也当 CLI 用）:
  python scripts\midi_file.py info <file.mid>              # 打印结构摘要
  python scripts\midi_file.py roundtrip <file.mid> [out]   # 导入 → 导出（默认 Type 1）→ 校验往返一致
"""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()

import midi_probe as mp

BEAT = 1.0                       # 内部时间单位：四分音符（拍）
DRUM_CH = 9                      # GM 鼓组通道（0-based 的 10 号通道）

# **同一 tick 上的事件排序权重**（`chunk()` 按 (tick, 权重) 升序写出）：
# 元事件(0) → 标记(1) → program/CC(2) → **松键(3) → 按键(4)**。
# ⚠ `W_OFF < W_ON` 是硬要求，不是风格问题：同音高首尾相接的两个音（长音 drone 的常态）
# 会让 off 与 on 落在同一个 tick 上，on 若先写就会被紧随其后的 off 关掉 → **音被吞**
# （详见 `track_events` 的 docstring 与 PITFALLS 161）。
# 提成模块级常量是为了**可被变异测试注入**（`mutation_check` 把 W_ON 改小 → 自检必须报警）。
W_META, W_MARK, W_PROG, W_OFF, W_ON = 0, 1, 2, 3, 4


def _beat(tick, div, nd=6):
    """tick → 拍。取到 1e-6 拍（= 480ppq 下 1/2000 tick）——纯为显示与 JSON 可读，
    精度远高于 MIDI 自身分辨率，往返不会漂。"""
    return round(float(tick) / (div or 480), nd)


def _dur(a, b, div, nd=6):
    """时值 = **终点 − 起点**（都按 tick 取整后再相减）。

    为什么不写成 `round(dur/div)`：那样终点 = round(start+half) 会因两次独立取整漂
    半 tick（实测外部 MIDI 的 3.6771 拍导出回来变成 3.625）—— 音符尾会一点点变短。
    按"终点减起点"算，终点恒等于导出时的同一个 tick，往返严格成立。"""
    return round((int(b) - int(a)) / float(div or 480), nd)


def import_midi(path, title=None):
    """标准 .mid / .midi → 编辑器数据模型（时间单位统一换成**拍**）"""
    r = mp.parse_full(path)
    div = float(r['division'] or 480)
    num, den = r['timesig'] or (4, 4)
    out = {'format': r['format'], 'division': int(div), 'bpm': round(r['bpm'], 4),
           'timesig': [int(num), int(den)],
           'title': title or os.path.splitext(os.path.basename(path))[0],
           'end_beat': round(r['end_tick'] / div, 4),
           'source': os.path.abspath(path),
           'tracks': []}
    for t in r['tracks']:
        ch = t['channel']
        out['tracks'].append({
            'index': t['index'],
            'name': t['name'] or ('Track %d' % (t['index'] + 1)),
            'channel': ch,
            'program': t['program'],
            'drum': ch == DRUM_CH,
            'mute': False, 'solo': False, 'hidden': False,
            'notes': [[_beat(a, div), _dur(a, a + d, div), int(p), int(v)]
                      for (a, d, p, v) in t['notes']],
            'ccs': [[_beat(a, div), int(c), int(v)] for (a, c, v) in t['ccs']],
            'program_changes': [[_beat(a, div), int(p)] for (a, p) in t['program_changes']],
            'markers': [[_beat(a, div), s] for (a, s) in t['markers']],
        })
    return out


def _vlq(n):
    out = bytearray([int(n) & 0x7F])
    n = int(n) >> 7
    while n:
        out.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out)


def _meta(kind, payload):
    return b'\xff' + bytes([kind]) + _vlq(len(payload)) + payload


def export_midi(model, path, fmt=None):
    """编辑器数据模型 → 标准 MIDI 文件。

    `fmt=1`（默认）= 多轨（每轨 MTrk，轨 0 带速度/拍号）
    `fmt=0` = 单轨（所有轨合并进一条 MTrk，**每条轨分配独立通道**；鼓轨固定 9）
    返回实际写入的 format。
    """
    fmt = int(fmt if fmt is not None else (model.get('format') or 1))
    if fmt not in (0, 1):
        raise SystemExit('format 只支持 0 / 1，收到 %r' % fmt)
    div = int(model.get('division') or 480)
    bpm = float(model.get('bpm') or 120.0)
    num, den = (model.get('timesig') or [4, 4])
    # 拍号分母在 SMF 里存的是**指数**（0x58 的第二个字节）：2→1、4→2、8→3 ……
    # 只支持 4/8 会把外部 MIDI 常见的 2/2（alla breve）挡在门外（实测 refs/midi2 里就有）。
    den = int(den)
    if den < 1 or (den & (den - 1)) != 0:
        raise SystemExit('拍号分母必须是 2 的幂（2/4/8/16…）：%r' % (model.get('timesig'),))
    den_exp = max(0, min(7, den.bit_length() - 1))
    tracks = [t for t in (model.get('tracks') or []) if not t.get('hidden')]
    if not tracks:
        raise SystemExit('没有可导出的轨（都隐藏了？）')

    def tick(beat):
        return int(round(float(beat) * div))

    def track_events(t, ch, with_meta_head):
        """→ [(tick, 排序权重, payload)]（权重保证同 tick 上"元事件→CC→**松键→按键**"的顺序）

        ⚠ **松键（note-off）必须排在按键（note-on）前面** —— 这是 SMF 的通行惯例，也是
        本模块踩过的坑：同一轨里**同音高首尾相接**的音（前音终点 == 后音起点，长音 drone /
        持续低音很常见），两个事件落在同一个 tick 上。若按键先写，音源的处理是"先起音、
        紧接着被这个 tick 上的松键关掉" → **新音被吞**（MIDI 的 note-off 只带音高、不带 id，
        它关的是这个音高上**所有**正在响的 voice）。实测后果不是"少一个音"而是**整段逐次衰减**：
        e01_remake（每 2 小节一个同音高长音）经编辑器导出后渲染，段内接续音全被吞，
        只剩混响残响，逐段从 −17dB 掉到 −80dB（`raw` 渲染 RMS −27.6dBFS vs 正常 −22.5dBFS）。
        只在段界（音高改变，C↔Bb）不冲突，所以听感是"每段头两小节有声、后面没了"。
        """
        ev = []
        if with_meta_head:
            nm = (t.get('name') or '').encode('utf-8')[:120]
            ev.append((0, W_META, _meta(0x03, nm)))
            ev.append((0, W_META, _meta(0x51, int(60_000_000 / max(1.0, bpm)).to_bytes(3, 'big'))))
            clocks = max(1, int(round(24.0 * 4.0 / int(den))))
            ev.append((0, W_META, _meta(0x58, bytes([int(num) & 0xFF, den_exp, clocks, 8]))))
        elif t.get('name'):
            ev.append((0, W_META, _meta(0x03, (t.get('name') or '').encode('utf-8')[:120])))
        prog = t.get('program')
        pcs = t.get('program_changes') or []
        # ⚠ **program 会被 program_changes 覆盖 → 必须提示，绝不静默**（2026-09-20，PITFALLS 213）：
        #   原写法是"有 program_changes 就不写 program"，于是"改了 program 想换音色"会被轨上
        #   自带的旧 `[[0, 0]]` **静默盖掉** —— 命令 ok、文件变大、渲染也 ok，只有渲染统计与
        #   改动前**逐样本相同**才暴露（当天就是这么被骗过去的）。
        #   ⚠ **为什么不直接报错**：导入的**真实 MIDI** 里 `program`（解析器推断）与
        #   `program_changes[0]`（文件真值）不同是**常态** —— 实测一报错就误伤编辑器的往返
        #   自检（`midi_file_editor_roundtrip`：`ABBA.Name of the game K.mid` 是 39 vs 84）。
        #   这两者该留哪个，工具**猜不出来**（用户那条 `[[0, 0]]` 本来就会覆盖）。
        #   所以这里**只消除静默**：导出行为一字不改，只把"哪个才生效"印到输出上。
        if prog is not None:
            if pcs and int(pcs[0][1]) != int(prog):
                print('  ⚠ 轨 %r：program=%s **不生效** —— 被 program_changes[0]=%s 覆盖'
                      '（想固定音色就清空 program_changes；见 PITFALLS 213）'
                      % (t.get('name'), prog, pcs[0][1]), file=sys.stderr)
            if not pcs:
                ev.append((0, W_PROG, bytes([0xC0 | ch, int(prog) & 0x7F])))
        for (bt, p) in pcs:
            ev.append((tick(bt), W_PROG, bytes([0xC0 | ch, int(p) & 0x7F])))
        for (bt, cc, val) in (t.get('ccs') or []):
            ev.append((tick(bt), W_PROG, bytes([0xB0 | ch, int(cc) & 0x7F,
                                                max(0, min(127, int(val)))])))
        for (bt, txt) in (t.get('markers') or []):
            ev.append((tick(bt), W_MARK, _meta(0x06, str(txt).encode('utf-8')[:120])))
        for n in (t.get('notes') or []):
            a, d, p, v = (float(n[0]), float(n[1]), int(n[2]), int(n[3]))
            if not 0 <= p <= 127:
                raise SystemExit('音高越界：%s（轨 %s）—— 编辑器不该写出这种数据'
                                 % (p, t.get('name')))
            if not 1 <= v <= 127:
                raise SystemExit('力度越界：%s（轨 %s）' % (v, t.get('name')))
            if a < 0 or d <= 0:
                raise SystemExit('时值异常：start=%s dur=%s（轨 %s）' % (a, d, t.get('name')))
            ev.append((tick(a + d), W_OFF, bytes([0x80 | ch, p, 0])))
            ev.append((tick(a), W_ON, bytes([0x90 | ch, p, v])))
        return ev

    def chunk(ev):
        ev.sort(key=lambda x: (x[0], x[1]))
        data = bytearray()
        last = 0
        for tk, _w, payload in ev:
            data += _vlq(max(0, tk - last))
            data += payload
            last = tk
        data += _vlq(0) + b'\xff\x2f\x00'
        return b'MTrk' + struct.pack('>I', len(data)) + bytes(data)

    chunks = []
    if fmt == 1:
        for i, t in enumerate(tracks):
            ch = int(t.get('channel', 0)) & 0x0F
            chunks.append(chunk(track_events(t, ch, with_meta_head=(i == 0))))
    else:
        # Type 0：单轨。通道按轨分配（鼓轨固定 9），否则全部挤在通道 1 上会串音色
        used, merged = set(), []
        free = [c for c in range(16) if c != DRUM_CH]
        for i, t in enumerate(tracks):
            if t.get('drum'):
                ch = DRUM_CH
            else:
                ch = next((c for c in free if c not in used), free[i % len(free)])
                used.add(ch)
            merged += track_events(t, ch, with_meta_head=(i == 0))
        chunks.append(chunk(merged))
    header = b'MThd' + struct.pack('>IHHH', 6, fmt, len(chunks), div)
    with open(path, 'wb') as f:
        f.write(header + b''.join(chunks))
    return fmt


def summarize(model):
    """编辑器模型 → 一行行摘要（CLI 与面板共用）"""
    out = ['format=%s division=%s bpm=%.2f 拍号=%s 长度=%.1f 小节 (%.2f 拍)'
           % (model.get('format'), model.get('division'), model.get('bpm') or 0,
              model.get('timesig'), (model.get('end_beat') or 0) / 4.0,
              model.get('end_beat') or 0)]
    for t in model.get('tracks') or []:
        ns = t.get('notes') or []
        vs = [n[3] for n in ns] or [0]
        ps = [n[2] for n in ns] or [0]
        out.append('  轨%-2d %-14s ch%-2d prog=%-4s 音 %3d  力度 %3d~%3d  音域 %s~%s  CC %d'
                   % (t.get('index'), (t.get('name') or '')[:14], int(t.get('channel', 0)) + 1,
                      t.get('program'), len(ns), min(vs), max(vs),
                      mp.note_name(min(ps)) if ns else '-',
                      mp.note_name(max(ps)) if ns else '-',
                      len(t.get('ccs') or [])))
    return out


def _has_overlap(notes):
    """同一轨里是否有"同音高且时间重叠"的音 —— MIDI 对这种写法**无法唯一还原配对**
    （note-on/note-off 只有音高没有 id），所以往返判据要分两档。"""
    by = {}
    for n in notes:
        by.setdefault(n[2], []).append((n[0], n[0] + n[1]))
    for p, iv in by.items():
        iv.sort()
        for (a1, b1), (a2, b2) in zip(iv, iv[1:]):
            if a2 < b1 - 1e-6:
                return True
    return False


def _net_notes(notes):
    """同音高重叠音 → **网络等价**表示：每个音高在每个时间点的"按下段"集合（去重后排序）。

    判据用它是为了避开 MIDI 的固有歧义：`[A 1拍] + [B 1拍]`（重叠）与 `[A 2拍]`
    在事件流上等价（同一音高连着按着不放），听感也完全一致。"""
    out = []
    for p in sorted({n[2] for n in notes}):
        segs = sorted({(round(n[0], 6), round(n[0] + n[1], 6))
                       for n in notes if n[2] == p})
        out.append((p, tuple(segs)))
    return out


def _sung_points(notes):
    """→ {(音高, round(起点或终点, 3))}：所有"发声状态改变"的时刻（1ms 精度）。

    这是**听感层**的等价判据：同音高重叠时哪个 note-off 配哪个 note-on 无法从 MIDI 还原，
    但"这个音高在这些时刻起、在这些时刻停"是确定的。往返后这个集合必须一模一样。"""
    pts = set()
    for n in notes:
        pts.add((n[2], round(n[0], 3)))
        pts.add((n[2], round(n[0] + n[1], 3)))
    return pts


def roundtrip_report(path, out=None):
    """导入 → 导出 → 再导入，逐项核对。

    判据两档（**必须分档，否则会把 MIDI 的固有歧义当成我们的 bug**）：
      · 该轨没有同音高重叠 → **严格逐音一致**（起点/时值/音高/力度）
      · 有重叠（外部 MIDI 很常见，如钢琴踏板式的同音反复）→ **网络等价**：
        同音高的按下段集合一致 + 音数一致 + 力度一致
    另外核对 BPM / 拍号 / 轨数 / CC 条数。
    """
    m1 = import_midi(path)
    out = out or os.path.join(os.environ.get('TEMP', '.'), 'roundtrip_probe.mid')
    fmt = export_midi(m1, out, fmt=1)
    m2 = import_midi(out)
    bad, exact, net = [], 0, 0
    swapped = []
    if abs((m1['bpm'] or 0) - (m2['bpm'] or 0)) > 0.01:
        bad.append('BPM %s → %s' % (m1['bpm'], m2['bpm']))
    if list(m1['timesig']) != list(m2['timesig']):
        bad.append('拍号 %s → %s' % (m1['timesig'], m2['timesig']))
    t1 = [t for t in m1['tracks'] if t['notes']]
    t2 = [t for t in m2['tracks'] if t['notes']]
    if len(t1) != len(t2):
        bad.append('有声轨数 %d → %d' % (len(t1), len(t2)))
    for a, b in zip(t1, t2):
        na, nb = a['notes'], b['notes']
        if len(na) != len(nb):
            bad.append('轨 %s 音符数 %d → %d' % (a['name'], len(na), len(nb)))
            continue
        if sorted(n[3] for n in na) != sorted(n[3] for n in nb):
            bad.append('轨 %s 力度分布变了' % a['name'])
        if _has_overlap(na):
            net += 1
            if _net_notes(na) != _net_notes(nb):
                # 分档：先给"听感层"（发声状态改变的时刻集合）判一次 —— 同音高重叠时
                # 哪个 note-off 配哪个 note-on 无法从 MIDI 还原（格式固有歧义），
                # 但"哪些时刻在响"是确定的；只有连这个都对不上才是真丢东西。
                miss = _sung_points(na) - _sung_points(nb)
                extra = _sung_points(nb) - _sung_points(na)
                if miss or extra:
                    bad.append('轨 %s 发声时刻变了（缺 %d 处 / 多 %d 处，例：%s）'
                               % (a['name'], len(miss), len(extra),
                                  sorted(miss)[:2] or sorted(extra)[:2]))
                else:
                    swapped.append('轨 %s 同音重叠的配对有重排（听感等价）' % a['name'])
        else:
            exact += 1
            for x, y in zip(na, nb):
                if (abs(x[0] - y[0]) > 1e-6 or abs(x[1] - y[1]) > 1e-6
                        or x[2] != y[2] or x[3] != y[3]):
                    bad.append('轨 %s 有音符对不上：%s vs %s' % (a['name'], x, y))
                    break
        if len(a.get('ccs') or []) != len(b.get('ccs') or []):
            bad.append('轨 %s CC 数 %d → %d'
                       % (a['name'], len(a.get('ccs') or []), len(b.get('ccs') or [])))
        if len(a.get('markers') or []) != len(b.get('markers') or []):
            bad.append('轨 %s 标记数变了' % a['name'])
    return {'ok': not bad, 'bad': bad[:8], 'out': out, 'fmt': fmt,
            'exact': exact, 'net': net, 'swapped': swapped[:4],
            'notes': sum(len(t['notes']) for t in m2['tracks']),
            'tracks': len(m2['tracks'])}


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    cmd, path = sys.argv[1], sys.argv[2]
    if cmd == 'info':
        for line in summarize(import_midi(path)):
            print(line)
        return 0
    if cmd == 'roundtrip':
        r = roundtrip_report(path, sys.argv[3] if len(sys.argv) > 3 else None)
        print('往返 %s：format=%d 轨 %d 音符 %d（严格逐音 %d 轨 / 网络等价 %d 轨）→ %s'
              % ('一致' if r['ok'] else '**不一致**', r['fmt'], r['tracks'],
                 r['notes'], r['exact'], r['net'], r['out']))
        for b in r['bad']:
            print('  !! ' + b)
        for s in r.get('swapped') or []:
            print('  ~  ' + s)
        return 0 if r['ok'] else 1
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main())
