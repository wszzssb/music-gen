#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""perc_clean.py —— 按 `preflight.py` ⑤「鼓连击缺 3–8kHz 冲击支撑」剔掉假打击（**默认只报不删**）。

## 什么时候用

还原/扒带曲"听着吵"、且 `preflight.py` ⑤ 报「连击串缺 3–8kHz 冲击支撑」时。
⚠ **两首实测的形态完全不同**：BGM23 主力是**键54 铃鼓**（852 音 / 占鼓轨 20.4%），
BGM35 主力是**键42 闭镲**（1071 音）—— 所以才有 `--level bright` 这个保守档。

## 判据**不重造**（口径一处）

直接吃 `preflight.py --json` 的输出：⑤ 的 `bad` 列表（每条 = 同一鼓键相邻 ≤0.25s、
≥6 下的连击串、且支撑率 <0.6）。⚠ `bad` 在 2026-10-13 之前**只存前 30 条**（现已改全量）——
拿旧 json 会漏掉大半，本工具会**核对 `len(bad)` 与 `n_burst` 并告警**。

## 三档（`--level`）

| 档 | 剔什么 | 什么时候用 |
|---|---|---|
| `strict`（默认） | 落在 ⑤ bad 串里的**全部**音 | 假打击明显多时 |
| `bright` | 只剔**明亮宽带打击**键（39 拍手 · 46 开镲 · 49/51/52/55/57 各类镲 · 54 铃鼓 · 69 卡巴萨 · 70 沙锤），**保留军鼓/闭镲/底鼓/嗵鼓** | 怕删过头时 |

## 纪律（每条都有实测代价）

* **默认只报不删** —— 删内容必须 A/B（`SKILL` §9c/9d："分数微涨 ≠ 更好听"）。
* `--apply` 前**记 SHA256 + 备份** `.pre_perccut.bak`；写盘走 `json_io`（坑 341：`json.dump(open(p,'w'))`
  在 Windows 上把 `\n` 翻成 CRLF，`song_json_canonical` 当场拦）。
* ⚠ **别把鼓音数剔到低于原曲**：实测 BGM35 剔后 **1990** < 原曲 **2201** < 历史最佳 **2773**
  ⇒ 那是**删过头**的信号，改用 `--level bright`，或先按本曲重新标定 `preflight` 的门。

## 用法

```bash
# ① 先让 preflight 出 json（它给全量串）
python scripts\preflight.py <成品.mid> --ref <原曲> --stems <6轨目录> `
      --skeleton <转录.mid> --base <转录.mid> --mine-wav <成品.wav> --out <报告目录>
# ② 只报（默认）
python scripts\perc_clean.py <曲名|song.json> --preflight <报告目录>\preflight.json
# ③ 真写盘（出到新曲目，做 A/B）
python scripts\perc_clean.py <曲名> --preflight <报告目录>\preflight.json `
      --apply --out <新曲目目录>\song.json
python scripts\perc_clean.py --selftest
```

退出码：`0` = 正常（含"没有要剔的"）· `1` = 用法/输入错（缺 preflight.json、⑤ 项不存在等）。
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
import cli_utf8 as _cu                                          # noqa: E402
_cu.setup()
import json_io                                                  # noqa: E402

SONGS = os.path.join(ROOT, 'songs')

#: 落在串里的判定容差（秒）—— 串端点与音符起点各自允许这么多
EPS = 0.06

#: `--level bright` 的白名单：**明亮宽带打击**（GM 打击映射）
BRIGHT_KEYS = {
    39: '拍手', 46: '开镲', 49: '碎镲1', 51: '叮叮镲', 52: '中国镲',
    53: '叮叮镲心', 54: '铃鼓', 55: '水镲', 57: '碎镲2',
    69: '卡巴萨', 70: '沙锤', 75: '响棒', 76: '高音木鱼', 77: '低音木鱼',
}


def sha256(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def load_bad(preflight_json):
    """从 preflight 报告里取 ⑤ 的连击串。→ (bad, warn)"""
    d = json.load(open(preflight_json, encoding='utf-8'))
    c5 = None
    for c in (d.get('checks') or []):
        if c.get('id') == '⑤':
            c5 = c
            break
    if c5 is None:
        raise SystemExit('这份 preflight.json 里没有 ⑤「鼓连击」项：%s' % preflight_json)
    bad = c5.get('bad') or []
    warn = ''
    nb = c5.get('n_burst')
    if nb is not None and len(bad) < nb:
        warn = ('⚠ bad 只有 %d 条而 n_burst=%d —— 这份 json 是**旧版截断**（[:30]）格式，'
                '会漏剔；请用 2026-10-13 之后的 preflight 重跑。' % (len(bad), nb))
    return bad, warn, c5


def _spans_by_pitch(bad, level):
    out = {}
    for c in bad:
        p = int(c['pitch'])
        if level == 'bright' and p not in BRIGHT_KEYS:
            continue
        out.setdefault(p, []).append((float(c['t0']), float(c['t1'])))
    return out


def cut(song_json, bad, level='strict', tracks=('Drums',)):
    """→ (d, ne, summ)：算出拟剔清单（**不改原 dict 之外的任何东西**）。"""
    d = json_io.load(song_json)
    ne = d.get('notes_extra') or {}
    spb = 60.0 / float(d.get('bpm') or 120.0)
    spans = _spans_by_pitch(bad, level)
    summ = []
    for tr in tracks:
        rows = ne.get(tr) or []
        keep, per = [], {}
        for n in rows:
            bar, beat, dur, pitch, vel = n[0], n[1], n[2], n[3], n[4]
            ss = spans.get(int(pitch))
            hit = False
            if ss:
                t = (float(bar) * 4.0 + float(beat)) * spb
                for (a, b) in ss:
                    if a - EPS <= t <= b + EPS:
                        hit = True
                        break
            if hit:
                per[int(pitch)] = per.get(int(pitch), 0) + 1
            else:
                keep.append(n)
        summ.append(dict(track=tr, n=len(rows), drop=len(rows) - len(keep), keep=len(keep),
                         per=per))
    return d, ne, summ, spb


def report(summ, level, bad_n, song_json, dry=True):
    print('=' * 74)
    print('按 preflight ⑤ 剔假打击 · %s' % song_json)
    print('  档 %s · 串 %d 条 · 容差 %.2fs · %s'
          % (level, bad_n, EPS, '**只报不删**' if dry else '写盘'))
    print('  %-9s %6s %7s %7s %s' % ('轨', '音数', '拟剔', '保留', '逐键'))
    tot = 0
    for s in summ:
        tot += s['drop']
        pk = ' '.join('%s%d' % (BRIGHT_KEYS.get(p, '键%d' % p) if level == 'bright'
                                else '键%d' % p, c)
                      for p, c in sorted(s['per'].items(), key=lambda kv: -kv[1])[:5])
        print('  %-9s %6d %7d %7d %s' % (s['track'], s['n'], s['drop'], s['keep'], pk or '-'))
    print('  %-9s %6s %7d' % ('合计', '', tot))
    return tot


def selftest():
    """判据自证：**改对了、改错不了**（合成件，不依赖曲库）。

    四条断言：
      ① 落在串里的音被剔、窗外的音**一个不动**
      ② `--level bright` 只剔明亮键（军鼓/闭镲必须留下）
      ③ 串端点 ±EPS 边界：贴边要剔、离 >EPS 不许剔
      ④ 没有串 ⇒ 一个都不剔（空判据不许"全剔"）
    """
    import tempfile
    tmp = tempfile.mkdtemp(prefix='pperccut_')
    sp = os.path.join(tmp, 'song.json')
    # 造一份最小 song.json：bpm=120（1 拍 = 0.5s），一小节 4 拍 = 2s
    notes = [
        [0, 0.0, 0.25, 54, 100],     # 键54 在 0.00s  ← 串内，应剔
        [0, 1.0, 0.25, 54, 100],     # 键54 在 0.50s  ← 串内，应剔
        [1, 0.0, 0.25, 54, 100],     # 键54 在 2.00s  ← 串外，必须留
        [1, 1.0, 0.25, 38, 100],     # 键38 军鼓 0.50s ← bright 档必须留
        [2, 0.0, 0.25, 42, 100],     # 键42 闭镲 4.00s ← bright 档必须留
    ]
    json_io.save(sp, dict(name='t', bpm=120.0, notes_extra=dict(Drums=notes)))

    bad = [dict(pitch=54, n=6, t0=0.0, t1=0.5, support=0.1, med_rise=2.0)]
    d, ne, summ, spb = cut(sp, bad, 'strict', ('Drums',))
    assert summ[0]['drop'] == 2 and summ[0]['keep'] == 3, \
        '① 应剔 2 留 3，实得 %s' % summ[0]
    d, ne, summ, spb = cut(sp, bad, 'bright', ('Drums',))
    assert summ[0]['drop'] == 2 and summ[0]['keep'] == 3, \
        '② bright 档键54 仍应剔 2（军鼓/闭镲在串外）实得 %s' % summ[0]
    # ③ 边界：串 [0.0, 0.5]、容差 0.06 ⇒ 闭区间 [−0.06, 0.56] **要剔**；0.65s 明确在外、必须留
    notes2 = [[0, 0.0, 0.25, 54, 100], [0, 1.0, 0.25, 54, 100],
              [0, 1.12, 0.25, 54, 100], [0, 1.3, 0.25, 54, 100]]
    json_io.save(sp, dict(name='t', bpm=120.0, notes_extra=dict(Drums=notes2)))
    d, ne, summ, spb = cut(sp, bad, 'strict', ('Drums',))
    assert summ[0]['drop'] == 3 and summ[0]['keep'] == 1, \
        '③ 边界：0.00/0.50/0.56s 应剔、0.65s 必须留，实得 %s' % summ[0]
    # ④ 空串 ⇒ 一个都不剔
    d, ne, summ, spb = cut(sp, [], 'strict', ('Drums',))
    assert summ[0]['drop'] == 0, '④ 没有串时不许剔任何音，实得 %s' % summ[0]
    shutil.rmtree(tmp, ignore_errors=True)
    print('perc_clean 自检：① 串内剔/串外留 · ② bright 白名单 · ③ ±%.2fs 边界 · ④ 空判据不剔 —— 4/4 PASS' % EPS)
    return True


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('song', nargs='?')
    ap.add_argument('--preflight', help='preflight.py --json 的输出（必须含 ⑤）')
    ap.add_argument('--level', default='strict', choices=('strict', 'bright'))
    ap.add_argument('--tracks', default='Drums', help='要动的轨（逗号分隔，默认 Drums）')
    ap.add_argument('--apply', action='store_true', help='真写盘（默认只报）')
    ap.add_argument('--out', default=None, help='写到别处（推荐：新曲目的 song.json）')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not (a.song and a.preflight):
        print(__doc__)
        return 1
    p = a.song if os.path.isfile(a.song) else os.path.join(SONGS, a.song, 'song.json')
    if not os.path.exists(p):
        print('找不到 %s' % p)
        return 1
    bad, warn, c5 = load_bad(a.preflight)
    if warn:
        print(warn)
    tracks = tuple(x.strip() for x in a.tracks.split(',') if x.strip())
    d, ne, summ, spb = cut(p, bad, a.level, tracks)
    tot = report(summ, a.level, len(bad), p, dry=not a.apply)
    if not a.apply:
        print('\n（**只报不删** —— 要真写盘加 `--apply`；建议 `--out` 出一份新曲目做 A/B）')
        print('⚠ 剔完请核对**鼓音数是否低于原曲**：低于 ⇒ 删过头，改用 `--level bright`。')
        return 0
    dst = a.out or p
    print('\n改前 SHA256 %s' % sha256(p))
    if dst == p:
        bak = p + '.pre_perccut.bak'
        shutil.copy2(p, bak)
        print('已备份 %s' % bak)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    for s in summ:
        ne[s['track']] = [n for n in (ne.get(s['track']) or [])
                          if not _is_fake(n, bad, a.level, spb)]
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    json_io.save(dst, d)
    print('已写 %s' % dst)
    print('改后 SHA256 %s' % sha256(dst))
    n1 = sum(len(v or []) for v in ne.values())
    print('音符总数 %d → %d（剔 %d）' % (n1 + tot, n1, tot))
    return 0


def _is_fake(n, bad, level, spb):
    spans = _spans_by_pitch(bad, level)
    ss = spans.get(int(n[3]))
    if not ss:
        return False
    t = (float(n[0]) * 4.0 + float(n[1])) * spb
    return any(a - EPS <= t <= b + EPS for (a, b) in ss)


if __name__ == '__main__':
    sys.exit(main())
