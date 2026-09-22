#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""层次实验：给**已成品的 WAV** 叠一层候选音色，直接看指标变化（不重渲染整首歌）

为什么需要：改引擎 → 重渲染整首（4~5 分钟的歌约 80~120s）→ 测 → 不满意再改，
一轮很贵。而"这一层到底有没有用"往往取决于**位置和音色**，与其它轨道无关。
所以：从源 MIDI 里把某个音的事件照抄到另一个音（例如底鼓 36 → 低音嗵鼓 41），
只渲染这一层（几秒），加到成品 WAV 上测量。指标变好了再落回引擎。

用法:
  # 把底鼓位置都换成 note 41（力度 52），叠到成品上，看 40/80Hz 占用率
  python scripts/layer_exp.py songs/14_d75_pulse/pulse_sf.wav songs/14_d75_pulse/pulse.mid \
         --from 36 --to 41 --vel 52 --chan 9
  # 不叠加，只测候选层本身
  python scripts/layer_exp.py ... --only
"""
import argparse
import os
import struct
import subprocess
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import render_midi as RM     # noqa: E402
import stem_compare as SC    # noqa: E402


def _vlq(buf, i):
    n = 0
    while True:
        b = buf[i]
        i += 1
        n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            return n, i


def _pack_vlq(n):
    out = bytearray([n & 0x7F])
    n >>= 7
    while n:
        out.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out)


def parse_smf(path):
    """极简 SMF 解析（够用即可）：返回 (division, [(tick, status, d1, d2)], tempo 列表)"""
    buf = open(path, 'rb').read()
    if buf[:4] != b'MThd':
        raise ValueError('不是 MIDI: %s' % path)
    _, ntrk, div = struct.unpack('>HHH', buf[8:14])
    off, tracks = 14, []
    for _ in range(ntrk):
        ln = struct.unpack('>I', buf[off + 4:off + 8])[0]
        tracks.append(buf[off + 8:off + 8 + ln])
        off += 8 + ln
    ev, tempos = [], []
    for tr in tracks:
        i, t, status = 0, 0, None
        while i < len(tr):
            d, i = _vlq(tr, i)
            t += d
            if i >= len(tr):
                break
            b = tr[i]
            if b & 0x80:
                status = b
                i += 1
            if status == 0xFF:
                mt = tr[i]
                i += 1
                ln, i = _vlq(tr, i)
                data = tr[i:i + ln]
                i += ln
                if mt == 0x51 and len(data) == 3:
                    tempos.append((t, struct.unpack('>I', b'\x00' + data)[0]))
            elif status in (0xF0, 0xF7):
                ln, i = _vlq(tr, i)
                i += ln
            else:
                hi = status >> 4
                n = 1 if hi in (0xC, 0xD) else 2
                d1 = tr[i] if n >= 1 else 0
                d2 = tr[i + 1] if n >= 2 else 0
                i += n
                ev.append((t, status, d1, d2))
    return div, ev, tempos


def write_smf(path, div, notes, tempos, chan):
    """notes: [(tick, note, vel, dur_ticks)]；tempos: [(tick, us)]"""
    evs = []
    for tk, us in tempos:
        evs.append((tk, 0, bytes([0xFF, 0x51, 0x03]) + struct.pack('>I', us)[1:]))
    for tk, note, vel, dur in notes:
        evs.append((tk, 1, bytes([0x90 | chan, note, vel])))
        evs.append((tk + dur, 1, bytes([0x80 | chan, note, 0])))
    evs.sort(key=lambda e: (e[0], e[1]))
    trk, last = b'', 0
    for tk, _, data in evs:
        trk += _pack_vlq(tk - last) + data
        last = tk
    trk += _pack_vlq(0) + b'\xFF\x2F\x00'
    data = b'MThd' + struct.pack('>IHHH', 6, 0, 1, div) + \
           b'MTrk' + struct.pack('>I', len(trk)) + trk
    open(path, 'wb').write(data)


def render_layer(mid, wav, reverb, sf2, exe):
    opts = []
    skip = {'synth.reverb.%s' % k for k in (reverb or {})}
    it = iter(RM.FS_OPTS)
    for a in it:
        b = next(it)
        if a == '-o' and b.split('=')[0] in skip:
            continue
        opts += [a, b]
    for k, v in (reverb or {}).items():
        opts += ['-o', 'synth.reverb.%s=%s' % (k, v)]
    cmd = [exe, '-ni', '-g', '1.0', '-r', '44100'] + opts + ['-F', wav, sf2, mid]
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-300:])


def band_line(tag, occ, dyn):
    return '%-22s %s | %s' % (tag, ' '.join('%4.0f' % v for v in occ),
                              ' '.join('%3.0f' % v for v in dyn))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('wav', help='成品 WAV')
    ap.add_argument('mid', help='源 MIDI（照抄它的事件位置）')
    ap.add_argument('--from', dest='frm', type=int, default=36)
    ap.add_argument('--to', dest='to', type=int, required=True)
    ap.add_argument('--vel', type=float, default=52)
    ap.add_argument('--vel-from', dest='velfrom', action='store_true',
                    help='力度照抄源音（乘 --vel/100）')
    ap.add_argument('--dur', type=float, default=0.4, help='音长（拍）')
    ap.add_argument('--chan', type=int, default=9)
    ap.add_argument('--only', action='store_true', help='只测这一层，不叠加')
    ap.add_argument('--secs', type=float, default=40.0)
    a = ap.parse_args()

    div, ev, tempos = parse_smf(a.mid)
    hits = [(t, d2, d1) for (t, st, d1, d2) in ev
            if (st & 0xF0) == 0x90 and (st & 0x0F) == a.chan and d1 == a.frm and d2 > 0]
    if not hits:
        print('源 MIDI 里没找到 channel %d 的 note %d' % (a.chan, a.frm))
        return 1
    print('照抄 %d 个 note %d → note %d' % (len(hits), a.frm, a.to))
    dur = int(round(a.dur * div))
    notes = [(t, a.to, int(round(a.vel if not a.velfrom else v * a.vel / 100.0)), dur)
             for (t, v, _) in hits]

    tmp = os.path.join(os.environ.get('TEMP', '.'), 'layerexp')
    os.makedirs(tmp, exist_ok=True)
    mid2 = os.path.join(tmp, 'layer.mid')
    wav2 = os.path.join(tmp, 'layer.wav')
    write_smf(mid2, div, notes, tempos, a.chan)
    # 混响跟成品一致（成品目录里的 render.json），否则这一层的"尾巴"不可比
    reverb = None
    rj = os.path.join(os.path.dirname(os.path.abspath(a.wav)), 'render.json')
    if os.path.exists(rj):
        import json
        reverb = json.load(open(rj, encoding='utf-8')).get('reverb')
    render_layer(mid2, wav2, reverb, RM.find_sf2(), RM.find_exe())

    base, sr = sf.read(a.wav, dtype='float64', always_2d=True)
    lay, sr2 = sf.read(wav2, dtype='float64', always_2d=True)
    # 先截断再取单声道（口径必须与 stem_compare 一致：**先切窗口再算**，
    # 否则整首歌的稀疏段落会把占用率拉低，看起来"改坏了"）
    n = min(int(a.secs * sr), len(base), len(lay))
    base, lay = base[:n], lay[:n]
    print('口径：占用率/动态（前 %.0fs），见 stem_compare.py' % a.secs)
    print('%-22s %s | %s' % ('频带(Hz)', ' '.join('%4s' % u for u in SC.UNITS), '动态(dB)'))
    o, d = SC.metrics(base[:n].mean(axis=1), sr)
    print(band_line('成品', o, d))
    o2, d2 = SC.metrics(lay[:n].mean(axis=1), sr2)
    print(band_line('候选层(%d)' % a.to, o2, d2))
    # ⚠ 2026-09-21 修的静默 bug：`--only`（help：'只测这一层，不叠加'）**声明了却从没被读** ——
    #   于是"叠加后 / 差"这两行**永远会打**，docstring 里写的那个用法（L15）等于没有。
    #   这类"参数收下了但没用"的错不报错，只让你拿到**比你要的更多**的输出
    #   （同族：`ask_audio_critic.py --start` 那次是拿到的**更少/错位**）。
    if not a.only:
        py = base[:n] + lay[:n]
        pk = float(np.abs(py).max())
        if pk > 0.985:
            py *= 0.985 / pk
        o3, d3 = SC.metrics(py.mean(axis=1), sr)
        print(band_line('叠加后', o3, d3))
        print('%-22s %s' % ('差（叠加−成品）', ' '.join('%+4.0f' % v for v in (o3 - o))))
    else:
        print('（--only：只报这一层本身，跳过"叠加后 / 差"）')
    return 0


import cli_utf8 as _cu; _cu.setup()
if __name__ == '__main__':
    sys.exit(main())
