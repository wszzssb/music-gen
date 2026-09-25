#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""stem_export.py —— **按轨（stem）导出 MIDI**：每条轨一个文件，**带 tempo/拍号**，并做四重自证。

## 为什么（2026-09-25，对标 `mason369/music-to-midi` 的 `midi_stem_export.py` + `midi_tempo.py`）

① **我们的导出器本来会丢 tempo**：`midi_file.export_midi` 写 Type 1 时是
   `with_meta_head=(i == 0)` —— tempo/拍号**只写在第一条轨**上。所以"把某一条轨抽出来单独存"
   一旦保留原轨号就会丢 tempo，DAW 里**回落 120 BPM**（小节线、网格全错）。
   上游为此专门把 tempo map **复制到每条含音符的轨**（为兼容 MuseScore）。
② **导出后不验 = 不知道有没有生效**（本仓库的"ok≠生效"纪律）。上游 `midi_editor.py:466-523`
   用**四重校验**：tempo 条数/值 → 音符指纹 → 透传事件指纹 → 发布后字节比对。

## 四重自证（每导一条轨都跑）

| # | 验什么 | 判据 |
|---|---|---|
| ① | **tempo / 拍号** | 导出文件读回的 bpm 与拍号 == 源（允许 1e-3 相对误差） |
| ② | **音符指纹** | 逐音 (起点, 时值, 音高, 力度) 与源轨**完全相同** |
| ③ | **透传事件指纹** | CC / program_changes / markers 逐条相同（顺序也一致） |
| ④ | **幂等（发布后字节比对）** | 把导出文件再导出一遍，**逐字节相同** |

## 用法

```bash
python scripts\stem_export.py <file.mid> --out <目录>        # 每轨一个 .mid + 清单
python scripts\stem_export.py <file.mid> --out <目录> --zip  # 另打一个 zip（原子发布 + testzip）
python scripts\stem_export.py --selftest                     # 尺子自检
```
命名：`NN-<轨名>-<BPM>BPM.mid`（NN 从 01 起，与上游 `NN-标签-BPM.mid` 同形）。
鼓轨（channel 9）也照导 —— DAW 里要靠它还原打击乐。
"""
import argparse
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()

import midi_file as mf

VERIFY_TOL = 1e-3


def _key(n):
    return (round(float(n[0]), 6), round(float(n[1]), 6), int(n[2]), int(n[3]))


def single_track_model(doc, track):
    """把一条轨包成**独立文档**（index 强制 0 → `export_midi` 会为它写 tempo/拍号）。"""
    t = dict(track)
    t['index'] = 0
    return {
        'format': 1, 'division': doc['division'], 'bpm': doc['bpm'],
        'timesig': list(doc['timesig']), 'end_beat': doc['end_beat'],
        'title': '%s-%s' % (doc.get('title') or 'stem', t.get('name') or 'track'),
        'tracks': [t],
    }


def verify_stem(src_track, path, doc):
    """四重自证 → `(ok, [失败项])`"""
    bad = []
    back = mf.import_midi(path)
    # ① tempo / 拍号
    if abs(float(back['bpm'] or 0) - float(doc['bpm'] or 0)) > VERIFY_TOL * max(1.0, doc['bpm'] or 1):
        bad.append('① tempo 丢了/变了：源 %s → 导出 %s（**单轨播放会回落 120BPM**）'
                   % (doc['bpm'], back['bpm']))
    if list(back['timesig']) != list(doc['timesig']):
        bad.append('① 拍号变了：%s → %s' % (doc['timesig'], back['timesig']))
    # ② 音符指纹
    b = back['tracks'][0] if back['tracks'] else {'notes': []}
    if sorted(map(_key, b['notes'])) != sorted(map(_key, src_track['notes'])):
        bad.append('② 音符指纹不一致：源 %d 音 → 导出 %d 音'
                   % (len(src_track['notes']), len(b['notes'])))
    # ③ 透传事件指纹
    for field in ('ccs', 'program_changes', 'markers'):
        s = [list(x) for x in (src_track.get(field) or [])]
        d = [list(x) for x in (b.get(field) or [])]
        if s != d:
            bad.append('③ %s 不一致：源 %d 条 → 导出 %d 条' % (field, len(s), len(d)))
    # ④ 幂等（再导出一遍 → 逐字节相同）
    tmp = path + '.again'
    mf.export_midi(mf.import_midi(path), tmp, fmt=1)
    with open(path, 'rb') as f1, open(tmp, 'rb') as f2:
        if f1.read() != f2.read():
            bad.append('④ 不幂等：导出文件再导出一遍**字节不同**（发布不可复现）')
    os.remove(tmp)
    return (not bad), bad


def run(src, out_dir, do_zip=False, verbose=True):
    doc = mf.import_midi(src)
    os.makedirs(out_dir, exist_ok=True)
    bpm = float(doc['bpm'] or 120.0)
    rows, allbad = [], []
    n = 0
    for t in doc['tracks']:
        if not t['notes']:
            continue
        n += 1
        name = (t.get('name') or 'track').replace('/', '_').replace('\\', '_')
        fn = '%02d-%s-%.1fBPM.mid' % (n, name, bpm)
        path = os.path.join(out_dir, fn)
        mf.export_midi(single_track_model(doc, t), path, fmt=1)
        ok, bad = verify_stem(t, path, doc)
        rows.append({'file': fn, 'track': t['name'], 'notes': len(t['notes']),
                     'channel': t.get('channel'), 'program': t.get('program'),
                     'bytes': os.path.getsize(path), 'ok': ok, 'bad': bad})
        allbad += ['%s: %s' % (fn, x) for x in bad]
    manifest = os.path.join(out_dir, 'stems.json')
    with open(manifest, 'w', encoding='utf-8') as fh:
        json.dump({'source': os.path.abspath(src), 'bpm': bpm,
                   'timesig': doc['timesig'], 'stems': rows}, fh,
                  ensure_ascii=False, indent=1)
    zpath = None
    if do_zip:
        zpath = os.path.join(out_dir, 'stems.zip')
        tmpz = zpath + '.tmp'
        with zipfile.ZipFile(tmpz, 'w', zipfile.ZIP_DEFLATED) as z:
            for r in rows:
                z.write(os.path.join(out_dir, r['file']), r['file'])
            z.write(manifest, 'stems.json')
        os.replace(tmpz, zpath)                      # 原子发布
        with zipfile.ZipFile(zpath) as z:
            if z.testzip() is not None:              # 发布后必须能读
                allbad.append('zip 校验失败（testzip 报损坏）')
    if verbose:
        print('源: %s（bpm %.2f · %s）' % (os.path.basename(src), bpm, doc['timesig']))
        print('导出 %d 条轨 → %s%s\n' % (len(rows), out_dir, ('  + %s' % zpath) if zpath else ''))
        print('  %-34s %6s %6s %8s  %s' % ('文件', '音符', '通道', '字节', '四重自证'))
        for r in rows:
            print('  %-34s %6d %6s %8d  %s'
                  % (r['file'][:34], r['notes'], r['channel'], r['bytes'],
                     'PASS' if r['ok'] else '**FAIL**'))
        if allbad:
            print('\n失败项:')
            for x in allbad:
                print('  ✗ %s' % x)
        else:
            print('\n四重自证全过（① tempo/拍号 ② 音符指纹 ③ 透传事件 ④ 幂等）')
    return rows, allbad, zpath


def selftest(verbose=True):
    """尺子自检：造一个"只有第 0 轨带 tempo"的多轨文件，验证
    ① 抽出来的轨**带着 tempo**（这正是本工具存在的理由）；
    ② 音符/CC 指纹能比对；
    ③ 幂等成立。"""
    import tempfile
    d = tempfile.mkdtemp(prefix='stem_export_st')
    src = os.path.join(d, 'probe.mid')
    doc = {'format': 1, 'division': 480, 'bpm': 145.9605, 'timesig': [4, 4],
           'end_beat': 32.0, 'title': 'probe', 'tracks': [
               {'index': 0, 'name': 'A', 'channel': 0, 'program': 0, 'notes': [[0.0, 1.0, 60, 100]],
                'ccs': [[0.0, 7, 100]], 'program_changes': [], 'markers': []},
               {'index': 1, 'name': 'B', 'channel': 1, 'program': 32, 'notes': [[1.0, 2.0, 40, 90]],
                'ccs': [], 'program_changes': [], 'markers': []}]}
    mf.export_midi(doc, src, fmt=1)
    rows, bad, _z = run(src, os.path.join(d, 'out'), verbose=False)
    assert not bad, '自检失败（四重自证报错）：%s' % bad
    assert len(rows) == 2, '两条有声轨该导出 2 个文件，实得 %d' % len(rows)
    # 关键：第二条轨单独导出后**必须带 tempo**（丢了就是回落 120BPM 的坑）
    second = mf.import_midi(os.path.join(d, 'out', [r for r in rows if r['track'] == 'B'][0]['file']))
    assert abs(float(second['bpm']) - 145.9605) < 0.01, \
        '抽出来的轨丢了 tempo（读回 %s）—— 这正是本工具要防的坑' % second['bpm']
    assert list(second['timesig']) == [4, 4], '抽出来的轨丢了拍号：%s' % second['timesig']
    if verbose:
        print('stem_export 自检 PASS：2 轨导出 · 第 2 轨 tempo=%.2f 拍号=%s · 四重自证全过'
              % (second['bpm'], second['timesig']))
    return True


def main():
    ap = argparse.ArgumentParser(description='按轨导出 MIDI（带 tempo + 四重自证）')
    ap.add_argument('midi', nargs='?')
    ap.add_argument('--out', default=None, help='输出目录（默认 <midi 同目录>/stems）')
    ap.add_argument('--zip', action='store_true', help='另打一个 zip（原子发布 + testzip）')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not a.midi:
        ap.print_help()
        return 1
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.midi)), 'stems')
    _rows, bad, _z = run(a.midi, out, do_zip=a.zip)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
