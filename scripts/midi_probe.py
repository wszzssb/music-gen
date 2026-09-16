#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MIDI 体检：解析 SMF，输出轨名/乐器/速度/拍号/音域/音符数/小节数
用法: python midi_probe.py <file.mid> [...]
"""
import struct
import sys

NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
GM_FAMILY = {
    0: '钢琴', 6: '羽管键琴', 8: '钢片琴/钟琴', 11: '颤音琴', 13: '木琴',
    16: '击弦风琴', 20: '簧风琴', 24: '尼龙弦吉他', 25: '钢弦吉他',
    32: '原声贝斯', 33: '电贝斯(指)', 42: '大提琴', 43: '低音提琴',
    48: '弦乐合奏', 49: '弦乐合奏2', 52: '人声合唱', 56: '小号', 65: '中音萨克斯',
    68: '双簧管', 71: '单簧管', 73: '长笛', 75: '排箫', 88: '合成Pad',
    89: '合成Pad(暖)', 104: '西塔琴', 112: '八音盒',
}


def note_name(n):
    return '%s%d' % (NAMES[n % 12], n // 12 - 1)


def _read_vlq(body, i):
    """变长数量：返回 (值, 新下标)。损坏数据（跑到轨尾）返回 (None, i)。"""
    d = 0
    while i < len(body):
        b = body[i]
        i += 1
        d = (d << 7) | (b & 0x7F)
        if not b & 0x80:
            return d, i
    return None, i


def parse_full(path):
    """**事件级**解析 SMF（给编辑器用：要能原样导出，所以力度/拍号/速度/CC 一个都不能丢）。

    与 `parse()` 的分工：`parse()` 是**统计口径**（theme_pack 拿它算画像、`note(v)` 一律返回 0），
    本函数是**编辑口径** —— 保留每条事件的原始载荷，`import→export` 往返后能被外部 DAW 正常读取。

    → {format, division, bpm, timesig, end_tick, tempo_map, tracks:[
         {index, name, channel, program, notes:[(start_tick, dur_tick, note, vel)],
          ccs:[(tick, cc, val)], program_changes:[(tick, prog)],
          pitch_bends:[(tick, value)], markers:[(tick, text)]}]}
    只做**记录**，不做任何"修正/量化/补齐"—— 修正是编辑器的活。
    """
    data = open(path, 'rb').read()
    if data[:4] != b'MThd':
        raise SystemExit('不是标准 MIDI 文件（缺 MThd）：%s' % path)
    fmt, ntrk, div = struct.unpack('>HHH', data[8:14])
    pos = 14
    res = {'format': fmt, 'division': div or 480, 'bpm': None, 'timesig': None,
           'end_tick': 0, 'tempo_map': [], 'tracks': []}
    for t in range(ntrk):
        if data[pos:pos + 4] != b'MTrk':
            raise SystemExit('第 %d 轨缺 MTrk 头（文件被截断或不是 SMF）' % t)
        ln = struct.unpack('>I', data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + ln]
        pos += 8 + ln
        i, tick = 0, 0
        last_st = None
        name = ''
        program = None
        channel = 0
        notes, ccs, progs, bends, markers = [], [], [], [], []
        active = {}                      # (通道, 音高) → [start_tick...]（重叠音按先入先出配对）
        while i < len(body):
            d, i = _read_vlq(body, i)
            if d is None:
                break
            tick += d
            if i >= len(body):
                break
            st = body[i]
            if st < 0x80 and last_st is not None:
                st = last_st              # running status
            else:
                i += 1
                last_st = st
            if st == 0xFF:
                meta = body[i]
                i += 1
                l, i = _read_vlq(body, i)
                if l is None:
                    break
                payload = body[i:i + l]
                i += l
                if meta == 0x51 and l == 3:
                    res['tempo_map'].append((tick, int.from_bytes(payload, 'big')))
                elif meta == 0x58 and l >= 2:
                    ts = (payload[0], 2 ** payload[1])
                    if res['timesig'] is None:
                        res['timesig'] = ts
                    res.setdefault('timesig_map', []).append((tick, list(ts)))
                elif meta == 0x03:
                    name = payload.decode('utf-8', 'replace')
                elif meta in (0x06, 0x01):   # marker / text
                    markers.append((tick, payload.decode('utf-8', 'replace')))
                elif meta == 0x2F:
                    break
            elif st in (0xF0, 0xF7):
                l, i = _read_vlq(body, i)
                if l is None:
                    break
                i += l
            else:
                hi, ch = st & 0xF0, st & 0x0F
                channel = ch
                if hi in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                    if i + 1 >= len(body):
                        break
                    p1, p2 = body[i], body[i + 1]
                    i += 2
                    if hi == 0x90 and p2 > 0:
                        active.setdefault((ch, p1), []).append((tick, p2))
                    elif hi == 0x80 or (hi == 0x90 and p2 == 0):
                        q = active.get((ch, p1))
                        if q:
                            s, vel = q.pop(0)
                            # ⚠ 力度取**按键事件**的 vel。松键事件（0x80）的第二个字节通常是 0，
                            # 而 running status 下 0x90+vel0 也算松键 —— 拿松键的 vel 当力度
                            # 会把整首曲子压成"力度 0~96"（实测外部 MIDI 全被写成 96）。
                            notes.append([s, tick - s, p1, vel])
                    elif hi == 0xB0:
                        ccs.append((tick, p1, p2))
                    elif hi == 0xE0:
                        bends.append((tick, ((p2 << 7) | p1) - 8192))
                elif hi == 0xC0:
                    if i >= len(body):
                        break
                    program = body[i]
                    progs.append((tick, program))
                    i += 1
                elif hi == 0xD0:
                    i += 1
        # 没松键的音：按轨尾收（否则导入后丢音）
        tail = max([tick] + [n[0] + n[1] for n in notes] + [ccs[-1][0] if ccs else 0])
        for (_ch, p), q in sorted(active.items()):
            for (s, vel) in q:
                notes.append([s, max(1, tail - s), p, vel])
        notes.sort()
        if notes:
            res['end_tick'] = max(res['end_tick'], max(n[0] + n[1] for n in notes))
        res['tracks'].append({'index': t, 'name': name, 'channel': channel,
                              'program': program, 'notes': notes, 'ccs': ccs,
                              'program_changes': progs, 'pitch_bends': bends,
                              'markers': markers})
    if res['tempo_map']:
        res['bpm'] = 60000000.0 / res['tempo_map'][0][1]
    else:
        res['bpm'] = 120.0               # SMF 规范：缺省 120
    if res['timesig'] is None:
        res['timesig'] = (4, 4)
    return res


def raw_events(path):
    """→ [(track, tick, status, d1, d2)]：**严格保留文件内事件顺序**的原始事件流。

    为什么单独开一个口子：`parse_full` 交出来的是**配对后的音符表**，一旦配上对，
    "这个 tick 上先写的谁"就没了 —— 而有些判据只能看原始顺序。已经被坑过一次：
    `midi_file.export_midi` 把同一 tick 上的 note-on 排在 note-off 之前时，
    音源的处理是"先起音、紧接着被同 tick 的 off 关掉"（MIDI 的 note-off 只带音高、
    不带 id，关的是该音高上所有正在响的 voice）→ **同音高的接续长音整段被吞**
    （见 PITFALLS 161 与自检 `midi_export_noteoff_first`）。

    `d2` 对 2 字节消息（0x8n/0x9n/0xAn/0xBn/0xEn）是第二个数据字节，1 字节消息
    （0xCn/0xDn）为 None；meta 事件的 `status=0xFF`、`d1=`meta 类型、`d2=None`。
    """
    data = open(path, 'rb').read()
    if data[:4] != b'MThd':
        raise SystemExit('不是标准 MIDI 文件（缺 MThd）：%s' % path)
    _fmt, ntrk, _div = struct.unpack('>HHH', data[8:14])
    pos = 14
    out = []
    for t in range(ntrk):
        if data[pos:pos + 4] != b'MTrk':
            break
        ln = struct.unpack('>I', data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + ln]
        pos += 8 + ln
        i, tick, last_st = 0, 0, None
        while i < len(body):
            d, i = _read_vlq(body, i)
            if d is None:
                break
            tick += d
            if i >= len(body):
                break
            st = body[i]
            if st < 0x80 and last_st is not None:
                st = last_st              # running status
            else:
                i += 1
                last_st = st
            hi = st & 0xF0
            if st == 0xFF:
                meta = body[i]
                i += 1
                l, i = _read_vlq(body, i)
                if l is None:
                    break
                i += l
                out.append((t, tick, 0xFF, meta, None))
            elif st in (0xF0, 0xF7):
                l, i = _read_vlq(body, i)
                if l is None:
                    break
                i += l
                out.append((t, tick, st, None, None))
            elif hi in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                if i + 1 >= len(body):
                    break
                out.append((t, tick, st, body[i], body[i + 1]))
                i += 2
            elif hi in (0xC0, 0xD0):
                if i >= len(body):
                    break
                out.append((t, tick, st, body[i], None))
                i += 1
            else:
                break
    return out


def noteoff_first_violations(path):
    """→ [(轨, tick, 音高)]：**同一 tick 上 note-on 排在同音高 note-off 之前**的位置。

    这是"吞音"的充要文件层特征（见 `raw_events` 的说明）。空列表 = 这个文件干净。
    """
    bad = []
    by = {}
    for (ti, tk, st, d1, d2) in raw_events(path):
        hi = st & 0xF0
        if hi == 0x80 or (hi == 0x90 and not d2):
            kind = 'off'
        elif hi == 0x90:
            kind = 'on'
        else:
            continue
        by.setdefault((ti, tk), []).append((kind, d1))
    for (ti, tk), evs in sorted(by.items()):
        started = set()
        for kind, p in evs:
            if kind == 'on':
                started.add(p)
            elif p in started:            # 该音高在本 tick 已经先起音了 → 会被这个 off 关掉
                bad.append((ti, tk, p))
    return bad


def parse(path, quiet=False):
    """解析 SMF。返回结构化结果（轨/音符/元事件），quiet=True 时不打印。
    结构: {format, division, bpm, timesig, tracks:[{name, channel, program,
          notes:[(start_tick, dur_tick, note, vel)], ...}]}"""
    data = open(path, 'rb').read()
    assert data[:4] == b'MThd', 'not a MIDI file'
    fmt, ntrk, div = struct.unpack('>HHH', data[8:14])
    pos = 14
    res = {'format': fmt, 'division': div, 'tracks': [], 'bpm': None,
           'timesig': None}
    say = (lambda *a: None) if quiet else print
    say('=' * 74)
    say('%s   format=%d  tracks=%d  division=%d tick/四分音符' %
        (path.split('\\')[-1], fmt, ntrk, div))
    tempo = None
    timesig = None          # **必须在轨道循环外**：拍号只写在第一轨，写在循环内会被后一轨抹成 None
    total_notes = 0
    all_notes = []
    for t in range(ntrk):
        assert data[pos:pos + 4] == b'MTrk'
        ln = struct.unpack('>I', data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + ln]
        pos += 8 + ln
        i = 0
        tick = 0
        name = ''
        program = None
        channel = 0
        notes = []
        ccs = []                # [(tick, CC号, 值)]：用于验证段落级混音自动化
        active = {}
        last_st = None
        while i < len(body):
            d = 0
            while True:
                b = body[i]
                i += 1
                d = (d << 7) | (b & 0x7F)
                if not b & 0x80:
                    break
            tick += d
            if i >= len(body):
                break
            st = body[i]
            if st < 0x80 and last_st is not None:
                st = last_st            # running status
            else:
                i += 1
                last_st = st
            if st == 0xFF:
                meta = body[i]
                i += 1
                l = 0
                while True:
                    b = body[i]
                    i += 1
                    l = (l << 7) | (b & 0x7F)
                    if not b & 0x80:
                        break
                payload = body[i:i + l]
                i += l
                if meta == 0x51 and l == 3:
                    tempo = int.from_bytes(payload, 'big')
                elif meta == 0x58:
                    timesig = (payload[0], 2 ** payload[1])
                elif meta == 0x03:
                    name = payload.decode('utf-8', 'replace')
                elif meta == 0x2F:
                    break               # end of track
            elif st in (0xF0, 0xF7):
                l = 0
                while True:
                    b = body[i]
                    i += 1
                    l = (l << 7) | (b & 0x7F)
                    if not b & 0x80:
                        break
                i += l
            else:
                hi = st & 0xF0
                ch = st & 0x0F
                channel = ch
                if hi in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                    p1, p2 = body[i], body[i + 1]
                    i += 2
                    if hi == 0xB0:                 # CC：记录（段落级混音自动化靠它验证）
                        ccs.append((tick, p1, p2))
                    if hi == 0xC0:
                        pass
                    if hi == 0x90 and p2 > 0:
                        active.setdefault(p1, []).append(tick)
                    elif hi == 0x80 or (hi == 0x90 and p2 == 0):
                        if active.get(p1):
                            start = active[p1].pop()
                            notes.append((start, tick - start, p1, p2))
                elif hi == 0xC0:
                    program = body[i]
                    i += 1
                elif hi == 0xD0:
                    i += 1
        if program is None and channel == 9:
            inst = '鼓组(ch10)'
        elif program is None:
            inst = '—'
        else:
            inst = 'GM%d %s' % (program, GM_FAMILY.get(program, ''))
        if notes:
            pitches = [n[2] for n in notes]
            say('  轨%d %-10s ch%-2d %-14s 音符%3d  音域 %s-%s  tick %d-%d'
                % (t, name or '(未命名)', channel + 1, inst, len(notes),
                   note_name(min(pitches)), note_name(max(pitches)),
                   min(n[0] for n in notes),
                   max(n[0] + n[1] for n in notes)))
        else:
            say('  轨%d %-10s %-14s (无音符/仅元事件)' % (t, name or '(未命名)', inst))
        res['tracks'].append({'index': t, 'name': name, 'channel': channel,
                              'program': program, 'notes': notes, 'ccs': ccs})
        total_notes += len(notes)
        all_notes += notes
    bpm = 60000000.0 / tempo if tempo else None
    res['bpm'] = bpm
    res['timesig'] = timesig
    res['note_count'] = total_notes
    say('  速度 %s  拍号 %s  音符总计 %d' %
        ('%.1f BPM' % bpm if bpm else '默认120', timesig or '4/4', total_notes))
    if all_notes:
        end = max(n[0] + n[1] for n in all_notes) / div
        num, den = (timesig or (4, 4))
        bb = num * 4.0 / den              # 一小节的四分音符数（3/4 → 3；6/8 → 3）
        res['end_tick'] = max(n[0] + n[1] for n in all_notes)
        say('  长度 %.1f 小节 (%d ticks)  约 %.1fs' %
            (end / bb, max(n[0] + n[1] for n in all_notes),
             end * 60 / (bpm or 120)))
    return res


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    for p in sys.argv[1:]:
        parse(p)
