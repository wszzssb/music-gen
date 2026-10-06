#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""ymt3_engine_map.py —— 把**任意 YMT3 权重**的原始 13 通道产物转成引擎认的 9 轨布局。

## 为什么需要它

`transcribe_ymt3.py` 出来的 MIDI 是**引擎映射后**的布局（`Piano/Bass/Hook/Strings/Pad/Arp/Glock/Drums/Melody`），
但直接换权重（`ymt3_moe_transcribe.py`）出来的是 **YMT3 原始 GM 家族名**
（`Acoustic Piano`/`Strings`/`Bass`/`Guitar (clean)`…）。`transcribe_to_song` 只认前者 ⇒
**直接喂会写出 0 轨 0 音**（实测，且不报错）。

映射表**不在这里抄第二份**（`PITFALLS 331`：每份清单都会漂移）—— 从
`transcribe_ymt3._ENGINE_MAP` **现场导入**，保证与主人单一真源一致。

## 用法

    <py-ml> ymt3_engine_map.py <输入.mid> <输出.mid> [--bpm 149.8] [--per-track-dir <目录>]

`--per-track-dir` 会**额外**把每条引擎轨写成 `<目录>/<轨名>.mid` ——
`transcribe_to_song` 的每轨内容只认 `--mid <轨>=<文件>`（单文件多轨会被拍平、写不进任何轨，
实测直接写 0 轨 0 音），所以换权重时必须走逐轨文件。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()          # noqa: E402
import midi_file                             # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--bpm', type=float, default=None, help='覆盖速度（原始产物默认 120）')
    ap.add_argument('--per-track-dir', default=None, help='额外逐轨写出 <目录>/<轨名>.mid')
    a = ap.parse_args()
    import transcribe_ymt3 as TY
    emap = TY._ENGINE_MAP
    m = midi_file.import_midi(a.src)
    if a.bpm:
        m['bpm'] = float(a.bpm)
    # 按引擎轨名合并（同名的多条轨加起来）
    merged = {}
    dropped = {}
    for tr in m['tracks']:
        nm = str(tr.get('name') or '')
        eng = emap.get(nm)
        ns = tr.get('notes') or []
        if not ns:
            continue
        if not eng:
            dropped[nm] = dropped.get(nm, 0) + len(ns)
            continue
        merged.setdefault(eng, []).extend(ns)
    if dropped:
        print('  ⚠ **没映射上的轨**（会被丢）：%s' % dropped)
    tracks = []
    for i, (eng, ns) in enumerate(sorted(merged.items())):
        tracks.append(dict(index=i, name=eng, channel=(9 if eng == 'Drums' else i),
                           program=(None if eng == 'Drums' else 0),
                           drum=(eng == 'Drums'), mute=False, solo=False, hidden=False,
                           ccs=[], program_changes=[], markers=[], notes=ns))
    out = dict(bpm=m['bpm'], division=m.get('division') or 480, tracks=tracks)
    midi_file.export_midi(out, a.dst)
    n = sum(len(t['notes']) for t in tracks)
    print('  %s → %s · %d 轨 / %d 音（bpm %s）'
          % (os.path.basename(a.src), os.path.basename(a.dst), len(tracks), n, out['bpm']))
    for t in tracks:
        print('     %-9s %5d 音' % (t['name'], len(t['notes'])))
    if a.per_track_dir:
        os.makedirs(a.per_track_dir, exist_ok=True)
        for t in tracks:
            one = dict(bpm=out['bpm'], division=out['division'], tracks=[dict(t, index=0)])
            midi_file.export_midi(one, os.path.join(a.per_track_dir, '%s.mid' % t['name']))
        print('  逐轨写出 → %s（%d 个文件，供 `--mid <轨>=<文件>`）'
              % (a.per_track_dir, len(tracks)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
