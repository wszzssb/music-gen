#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""filter_song_by_evidence.py —— **按逐音能量证据剔"没有音频支撑"的音**（假音治理 A 的动手端）

与 `filter_song_by_stem.py` 的分工：那个按**分轨 RMS / 逐段**判（粗），本工具按
**逐音 × 逐分轨音高带能量**判（`stem_note_evidence.py` 出的读数），并且**逐音表**可核对。

## 红线（全部来自实测，不许绕过）

1. **默认 `--dry`**：不加 `--apply` 只打印逐轨拟剔表，不写盘。
2. **必须 `--apply` + 记 SHA256**：写盘前自动备份成 `<song.json>.pre_evcut.bak`，
   并在 stdout 打 **改前 SHA256 / 改后 SHA256**（"ok ≠ 生效"，要能独立复核）。
3. **必须逐轨列明**：动手前打印 `轨 / 拟剔 / 保留 / 该轨占比`，不许静默删；
   `--tracks` 可限定只动某几轨（**一次只改一个维度**）。
4. **保护名单默认开**：`--protect` 默认 `Melody`（主奏/旋律线的音即使证据弱也别一刀切，
   同族教训 `PITFALLS` 325/334：那条线本来就不是能量主导的）。
5. 判据只用**该音的 `sup`**（= 6 条分轨里最大的音高带能量占比）与**窗总能量**：
   `sup < --drop` **且** 窗总能量 ≥ `--silent-db` 才剔（窗里根本没乐器在响的"留白音"
   另有含义，单列不剔，见 `stem_note_evidence` 的 `静音窗内` 一栏）。

## 用法

```powershell
$py = "<工具链>\.venv-ml\Scripts\python.exe"
& $py scripts\filter_song_by_evidence.py --song-json songs\<曲>\song.json `
      --evidence D:\test\_tmp\fake-note\ev_<曲>.json --drop 0.05 [--tracks Drums,Glock] `
      [--protect Melody] [--out <新 song.json 路径>] [--apply]
& $py scripts\filter_song_by_evidence.py --selftest
```

⚠ `--apply` 会**原地改歌**：要保留原版请用 `--out` 写到别处（推荐：复制成 `<曲>_evcut` 新曲目）。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()          # noqa: E402

SONGS = os.path.join(ROOT, 'songs')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def plan(song_json, evidence, drop_thr=0.05, silent_db=-70.0, tracks=None, protect=('Melody',),
         mode='energy', rise_db=6.0):
    """→ (song 数据, {轨: 逐音 flag}, 逐轨汇总, notes_extra)。

    `mode='energy'`（A）：用 `stem_note_evidence` 的读数 —— `sup < drop_thr` **且**窗里有能量 ⇒ 剔。
    `mode='onset'`（B）：用 `stem_onset_evidence` 的读数 —— `rise` 缺省或 `≤ rise_db`
    **且**没有够凸的局部峰 ⇒ 剔（"这一下没有冲击/起音支撑"）。
    flag ∈ `keep` / `drop` / `hold`（限定轨外或保护轨，只报不动）/ `silent`（留白窗，不剔）。
    计数与列表**分开放**：本函数早期版本把 `keep` 同时当计数与列表用，自检当场抓到。
    """
    d = json.load(open(song_json, encoding='utf-8'))
    ev = json.load(open(evidence, encoding='utf-8'))
    rows = ev['rows']
    # 证据行按 (轨, 顺序) 与 song.json 对齐：evidence 是按 notes_extra 顺序逐音生成的
    by_track = {}
    for r in rows:
        by_track.setdefault(r['track'], []).append(r)
    ne = d.get('notes_extra') or {}
    out, summ = {}, []
    for tr, v in ne.items():
        lst = (v.get('notes') if isinstance(v, dict) else v) or []
        evs = by_track.get(tr, [])
        if len(evs) != len(lst):
            raise SystemExit('证据与 song.json 对不上：轨 %s 有 %d 音、证据 %d 条'
                             '（证据必须由同一份 song.json 生成）' % (tr, len(lst), len(evs)))
        act = tr not in (protect or ())                      # 保护轨：只报不动
        if tracks and tr not in tracks:
            act = False
        keep, drop, drop_silent = 0, 0, 0        # ⚠ 这里只**计数**：`keep` 是 int，
        flags = []                               #   写成 list 会与 `len(keep)` 打架（本轮自检当场抓到）
        for e in evs:
            if mode == 'onset':
                weak = (e.get('rise') is None or e['rise'] <= rise_db) and not e.get('peak')
            else:
                weak = e['sup'] < drop_thr
            if weak:
                if mode != 'onset' and e['db'] < silent_db:
                    drop_silent += 1
                    keep += 1
                    flags.append('silent')
                elif act:
                    drop += 1
                    flags.append('drop')
                else:
                    keep += 1
                    flags.append('hold')
            else:
                keep += 1
                flags.append('keep')
        out[tr] = flags
        summ.append(dict(track=tr, n=len(lst), drop=drop, silent=drop_silent,
                         keep=keep, act=bool(act),
                         pct=100.0 * drop / max(1, len(lst))))
    return d, out, summ, ne


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('--song-json')
    ap.add_argument('--evidence', help='`stem_note_evidence.py --json` 的输出')
    ap.add_argument('--mode', default='energy', choices=('energy', 'onset'),
                    help='energy = A（音高带能量，读 sup）· onset = B（冲击/起音，读 rise/peak）')
    ap.add_argument('--rise-db', type=float, default=6.0, help='onset 模式的抬升门（dB）')
    ap.add_argument('--drop', type=float, default=0.05, help='拟剔门（sup < 这个）')
    ap.add_argument('--silent-db', type=float, default=-70.0, help='窗总能量低于这个 ⇒ 留白，不剔')
    ap.add_argument('--tracks', default='', help='只动这些轨（逗号分隔；默认全部非保护轨）')
    ap.add_argument('--protect', default='Melody', help='保护轨（只报不动；默认 Melody）')
    ap.add_argument('--out', default=None, help='写到别处（推荐：新曲目的 song.json）')
    ap.add_argument('--apply', action='store_true', help='真写盘（默认只报）')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not (a.song_json and a.evidence):
        print(__doc__)
        return 1
    tracks = tuple(x.strip() for x in a.tracks.split(',') if x.strip()) or None
    protect = tuple(x.strip() for x in a.protect.split(',') if x.strip())
    d, out, summ, ne = plan(a.song_json, a.evidence, a.drop, a.silent_db, tracks, protect,
                            a.mode, a.rise_db)
    print('=' * 74)
    print('按%s证据剔假音 · %s' % ('起音/冲击' if a.mode == 'onset' else '音高带能量', a.song_json))
    print('  判据 %s · 留白门 %.0fdBFS · 限定轨 %s · 保护轨 %s'
          % (('抬升门 %.1fdB 且无局部峰' % a.rise_db) if a.mode == 'onset'
             else ('sup<%.2f' % a.drop),
             a.silent_db, ','.join(tracks) if tracks else '（全部非保护轨）',
             ','.join(protect) or '（无）'))
    print('  %-9s %6s %8s %10s %7s %s' % ('轨', '音数', '拟剔', '留白(不剔)', '保留', '动作'))
    tot = 0
    for s in summ:
        tot += s['drop']
        print('  %-9s %6d %7d(%5.1f%%) %8d %7d %s'
              % (s['track'], s['n'], s['drop'], s['pct'], s['silent'], s['keep'],
                 '动手' if s['act'] else '只报不动'))
    print('  %-9s %6s %7d' % ('合计', '', tot))
    if not a.apply:
        print('\n（**只报不删** —— 要真写盘加 `--apply`；建议先 `--out` 出一份新曲目做 A/B）')
        return 0
    dst = a.out or a.song_json
    print('\n改前 SHA256 %s' % sha256(a.song_json))
    if dst == a.song_json:
        bak = a.song_json + '.pre_evcut.bak'
        shutil.copy2(a.song_json, bak)
        print('已备份 %s' % bak)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    for tr, flags in out.items():
        v = ne[tr]
        if isinstance(v, dict):
            v['notes'] = [x for x, f in zip((v.get('notes') or []), flags) if f != 'drop']
        else:
            ne[tr] = [x for x, f in zip(v or [], flags) if f != 'drop']
    # ⚠ **必须走 `json_io.save`**（`newline='\n'`）：用 `json.dump(open(p,'w'))` 在 Windows 上
    #   会把 `\n` 翻译成 CRLF ⇒ 每行 +1 字节，`selftest` 的 `song_json_canonical` 当场拦
    #   （本轮建 A/B 变体时踩到）。`json_io` 同时保证"按小节分行"的规范排版。
    import json_io
    json_io.save(dst, d)
    print('已写 %s' % dst)
    print('改后 SHA256 %s' % sha256(dst))
    n0 = sum(len((v.get('notes') if isinstance(v, dict) else v) or []) for v in
             (json.load(open(a.song_json, encoding='utf-8')).get('notes_extra') or {}).values())
    n1 = sum(len((v.get('notes') if isinstance(v, dict) else v) or []) for v in ne.values())
    print('音符总数 %d → %d（-%d）' % (n0, n1, n0 - n1))
    return 0


def selftest():
    """判据自证：① 低于门且窗里有能量 ⇒ drop；② 留白窗 ⇒ 不剔；③ 保护轨 ⇒ 不动；④ 对不上就报错。"""
    import tempfile
    ok = []
    d = tempfile.mkdtemp(prefix='fse_')
    song = os.path.join(d, 'song.json')
    json.dump(dict(bpm=120, notes_extra={
        'Drums': [[0, 0, 0.25, 36, 100], [0, 1, 0.25, 36, 100], [0, 2, 0.25, 36, 100]],
        'Melody': [[0, 0, 1.0, 72, 100]],
    }), open(song, 'w', encoding='utf-8'))
    ev = os.path.join(d, 'ev.json')
    json.dump(dict(rows=[
        dict(track='Drums', sup=0.01, db=-20.0),      # 有能量、无支撑 ⇒ 剔
        dict(track='Drums', sup=0.01, db=-95.0),      # 留白窗 ⇒ 不剔
        dict(track='Drums', sup=0.90, db=-20.0),      # 有支撑 ⇒ 留
        dict(track='Melody', sup=0.01, db=-20.0),     # 保护轨 ⇒ 不动
    ]), open(ev, 'w', encoding='utf-8'))
    _d, out, summ, _ne = plan(song, ev, drop_thr=0.05, protect=('Melody',))
    s = {x['track']: x for x in summ}
    ok.append(('有能量无支撑 → 剔', s['Drums']['drop'] == 1))
    ok.append(('留白窗 → 不计入剔', s['Drums']['silent'] == 1))
    ok.append(('保护轨 → 只报不动', s['Melody']['drop'] == 0 and not s['Melody']['act']))
    ok.append(('有支撑 → 保留', s['Drums']['keep'] == 2))
    # ④ 证据条数对不上必须报错（防"拿错曲子的证据"）
    json.dump(dict(rows=[dict(track='Drums', sup=0.0, db=-20.0)]), open(ev, 'w', encoding='utf-8'))
    try:
        plan(song, ev)
        ok.append(('证据对不上 → 报错', False))
    except SystemExit:
        ok.append(('证据对不上 → 报错', True))
    # ⑤ onset 模式（B）：无抬升且无局部峰 ⇒ 剔；有局部峰 ⇒ 留
    json.dump(dict(rows=[
        dict(track='Drums', rise=1.0, peak=False),
        dict(track='Drums', rise=1.0, peak=True),
        dict(track='Drums', rise=20.0, peak=False),
        dict(track='Drums', rise=None, peak=False),
    ]), open(ev, 'w', encoding='utf-8'))
    json.dump(dict(bpm=120, notes_extra={
        'Drums': [[0, i, 0.25, 36, 100] for i in range(4)]}), open(song, 'w', encoding='utf-8'))
    _d2, _o2, s2, _n2 = plan(song, ev, mode='onset', rise_db=6.0, protect=())
    d2 = {x['track']: x for x in s2}['Drums']
    ok.append(('onset：无抬升且无峰 → 剔', d2['drop'] == 2))
    ok.append(('onset：有局部峰 / 抬升过门 → 留', d2['keep'] == 2))
    for nm, v in ok:
        print('  %-26s %s' % (nm, 'PASS' if v else 'FAIL'))
    print('filter_song_by_evidence 自检 ' + ('PASS' if all(v for _n, v in ok) else 'FAIL'))
    return all(v for _n, v in ok)


if __name__ == '__main__':
    sys.exit(main())
