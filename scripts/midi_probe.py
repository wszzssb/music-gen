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
