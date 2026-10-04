#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""bass_pair_collapse.py —— 折叠"同音高近距离重复对"里**没有音频支撑**的那一半。

## 为什么需要（2026-10-02，BGM35 新版实测）

`bass_ensemble.py` 的并集口径是"同 0.1s 格 + 同音高合并"，所以**同格不会出两份**；
但两个来源（YMT3 分轨 / Basic Pitch）沿 0.1s 格线交错时，会产出**相邻格**的一对音 ——
落到秒上是 20~60ms。`preflight` ② 的判据是"同轨同音高 ≤60ms"，于是这些对全被计成重复组
（新版实测 137 组，其中 Bass 占 125 组）。

**但"间隔小"不等于"错"**：BGM35 的 bass 真的有把同一个音连拨两下的写法。
所以本工具**不按间隔一刀切**，而是用 `bass_pair_audio.py`（自检过的独立音频判据）逐对判：

  · burst < 5 dB（H2：第二下没有自己的起音，只是两个模型把一次起音各检出一半）
    → **折叠**成一个音（起音取 A、时值取到 B 的结束、力度取两者较大）
  · burst ≥ 5 dB（H1：音频里确实有第二次起音）
    → **原样保留**（判据服从原曲，不许为过指标删真实音符）

⚠ 本工具**只改 `notes_extra[<轨>]`**，其它字段逐字节不动；改前记 md5、改后读回核对。
⚠ 只折叠"同一条轨内"的对 —— 跨轨齐奏是合法编配，不算重复。

## 用法

```bash
python scripts/bass_pair_collapse.py <曲目名> --stem-wav <该层分轨.wav> [--track Bass] \
       [--tol 0.06] [--dry] [--no-render]
#   <曲目名> 解析到 songs/<名>/song.json（同 make_song 的曲目定位规则）
python scripts/bass_pair_collapse.py --selftest
```
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

BAR_BEATS_DEFAULT = 4.0


def md5(p):
    return hashlib.md5(open(p, 'rb').read()).hexdigest()


def find_song(name):
    """曲目名 → song.json（先 songs/ 再 songs_direct/，与引擎一致）"""
    for base in ('songs', 'songs_direct'):
        p = os.path.join(ROOT, base, name, 'song.json')
        if os.path.isfile(p):
            return p
    raise SystemExit('找不到曲目 %s 的 song.json（试过 songs/ 与 songs_direct/）' % name)


def bar_beats_of(song):
    m = song.get('meter') or [4, 4]
    return float(m[0]) * 4.0 / float(m[1])


def load_track_notes(song, track):
    """→ [(小节, 拍内, 时值拍, 音高, 力度), ...]（保持原顺序）"""
    ne = song.get('notes_extra') or {}
    if track not in ne:
        raise SystemExit('song.json 的 notes_extra 里没有轨 %s（有：%s）' % (track, sorted(ne)))
    v = ne[track]
    return v['notes'] if isinstance(v, dict) else v


def store_track_notes(song, track, notes):
    v = song['notes_extra'][track]
    if isinstance(v, dict):
        v['notes'] = notes
    else:
        song['notes_extra'][track] = notes


def to_list(notes, bar_beats):
    return [dict(beat=float(b) * bar_beats + float(o), bar=int(b), off=float(o),
                 dur=float(d), p=int(p), v=int(v), raw=[b, o, d, p, v]) for (b, o, d, p, v) in notes]


def pair_index(items, tol, spb):
    """在**同一份 notes** 上按"音高 + 排序位置"给对编号 —— 不靠秒↔拍的往返取整。

    返回 {(音高, i, i+1): (A, B)}；i 是该音高内按起音排序的位置。
    ⚠ 之所以不用"时间戳当键"：MIDI 的 tick 量化会让 `秒/spb` 回不到原来的拍值
    （实测 2.277 拍 → 2.275 拍），键对不上就会**静默漏判**。

    ## ⚠ 间隔必须与 `bass_pair_audio.close_pairs` **用同一个算式**（2026-10-03 实测）

    本函数与 `close_pairs` 数出的对数会被 `main()` 交叉核对，不等就**拒绝改数据**。
    两者数学等价，但 IEEE754 下**不等价**：

        g1 = b*spb − a*spb      （close_pairs 的写法）
        g2 = (b − a)*spb        （本函数原来的写法）

    实测 `bgm35_v2` 有一对（音高 33、间隔**恰好等于门限** 0.06 秒 = 0.15 拍 × 0.4 秒/拍）：
    `g1 = 0.060000000000002274`（> tol，不算对）而 `g2 = 0.059999999999990908`（< tol，算对）
    ⇒ 两套口径 105 vs 106，守卫拦住整条工序（**它没错** —— 那对确实压在门限上）。

    结论：**跟 `close_pairs` 用同一个写法**（本函数是被核对方，不是定义方）。
    代价：**恰好压在门限上的那一对不会被折叠**（漏 1 对）——
    这是保守方向，且与 `preflight` 的口径一致（交付体检用的就是它）。
    """
    byp = defaultdict(list)
    for it in items:
        byp[it['p']].append(it)
    out = {}
    for p, lst in byp.items():
        lst.sort(key=lambda x: x['beat'])
        for i in range(len(lst) - 1):
            a, b = lst[i], lst[i + 1]
            # ⚠ 写法必须与 `close_pairs` 逐字一致（见 docstring 的 ULP 实测）
            if (b['beat'] * spb) - (a['beat'] * spb) <= tol:
                out[(p, i, i + 1)] = (a, b)
    return out


def collapse_pairs(items, flags, bar_beats, tol):
    """按 flags（True=保留/两次起音）折叠 —— **逐对**判，不整曲一刀切。

    返回 (新 notes 列表, 处理表)。同音高 ≤tol 秒的一对：
      · 保留 → 两个音都不动
      · 折叠 → 起音取 A、时值取到 max(A 结束, B 结束)、力度取较大者
    """
    byp = defaultdict(list)
    for it in items:
        byp[it['p']].append(it)
    drop = set()          # 被折叠掉的（按 id）
    extend = {}           # 保留的那条 → 新时值（拍）
    newvel = {}           # 保留的那条 → 新力度
    log = []
    for p, lst in byp.items():
        lst.sort(key=lambda x: x['beat'])
        for i in range(len(lst) - 1):
            a, b = lst[i], lst[i + 1]
            gap = b['beat'] - a['beat']
            if gap > tol:
                continue
            keep_two = flags.get((p, i, i + 1))
            if keep_two:
                log.append(dict(动作='保留(两次起音)', p=p, a_beat=a['beat'], b_beat=b['beat'],
                                gap=round(gap, 6), a_dur=round(a['dur'], 6), b_dur=round(b['dur'], 6)))
                continue
            new_dur = max(a['dur'], (b['beat'] + b['dur']) - a['beat'])
            drop.add(id(b))
            extend[id(a)] = max(extend.get(id(a), a['dur']), new_dur)
            newvel[id(a)] = max(newvel.get(id(a), a['v']), b['v'])
            log.append(dict(动作='折叠', p=p, a_beat=a['beat'], b_beat=b['beat'], gap=round(gap, 6),
                            a_dur=round(a['dur'], 6), b_dur=round(b['dur'], 6),
                            new_dur=round(new_dur, 6), a_v=a['v'], b_v=b['v'],
                            new_v=max(a['v'], b['v'])))
    out = []
    for it in items:
        if id(it) in drop:
            continue
        out.append([it['bar'], it['off'], extend.get(id(it), it['dur']), it['p'],
                    newvel.get(id(it), it['v'])])
    return out, log


def selftest():
    ok = True

    def chk(lab, got, want):
        nonlocal ok
        f = 'PASS' if got == want else 'FAIL'
        ok = ok and got == want
        print('  [%s] %-46s 期望 %-4s 实得 %-4s' % (f, lab, want, got))

    bb = 4.0
    spb = 0.4
    tb = 0.06 / spb          # 容差（拍）：0.06 秒 ÷ 每拍秒数
    # 用例 1：一次起音被重复检出（gap 0.05 拍）→ 折叠成一个音
    items = to_list([[0, 0.0, 0.5, 40, 100], [0, 0.05, 1.0, 40, 60], [0, 2.0, 0.5, 43, 80]], bb)
    flags = {(40, 0, 1): False}
    out, log = collapse_pairs(items, flags, bb, tb)
    chk('折叠：音数 3 → 2', len(out), 2)
    chk('折叠：起音不动（仍是 0.0）', out[0][1], 0.0)
    chk('折叠：时值延伸到 B 的结束（1.05 拍）', round(out[0][2], 6), 1.05)
    chk('折叠：力度取较大（100）', out[0][4], 100)
    # 用例 2：两次真实起音 → 两个音都留
    items = to_list([[0, 0.0, 0.02, 40, 100], [0, 0.05, 0.5, 40, 90]], bb)
    flags = {(40, 0, 1): True}
    out, _ = collapse_pairs(items, flags, bb, tb)
    chk('保留：音数仍 2', len(out), 2)
    chk('保留：时值未被改', (round(out[0][2], 6), round(out[1][2], 6)), (0.02, 0.5))
    # 用例 3：间隔超容差 → 不动（0.2 拍 = 80ms > 60ms）
    items = to_list([[0, 0.0, 0.5, 40, 100], [0, 0.2, 0.5, 40, 90]], bb)
    out, _ = collapse_pairs(items, {}, bb, tb)
    chk('超容差不折叠', len(out), 2)
    # 用例 4：不同音高不算对
    items = to_list([[0, 0.0, 0.5, 40, 100], [0, 0.01, 0.5, 41, 90]], bb)
    out, _ = collapse_pairs(items, {}, bb, tb)
    chk('跨音高不折叠', len(out), 2)
    # 用例 5：边界恰好 = tol → 算对（与 preflight 的 `<= tol` 同口径）
    items = to_list([[0, 0.0, 0.5, 40, 100], [0, 0.15, 0.5, 40, 90]], bb)   # 0.15 拍 = 60ms
    out, _ = collapse_pairs(items, {(40, 0, 1): False}, bb, tb)
    chk('边界 = tol 折叠', len(out), 1)
    # 用例 6：pair_index 与 close_pairs 的**配对顺序一致**（否则 flags 会错位挂钩）
    items = to_list([[0, 0.0, 0.5, 40, 100], [0, 0.05, 0.4, 40, 90],
                     [0, 1.0, 0.5, 43, 80], [1, 0.0, 0.5, 43, 70]], bb)
    pi = pair_index(items, 0.06, spb)
    chk('pair_index 数出一对（另一对间隔 2 拍不算）', sorted(pi.keys()), [(40, 0, 1)])
    a, b = pi[(40, 0, 1)]
    chk('pair_index 指向的音正确', (round(a['beat'], 3), round(b['beat'], 3)), (0.0, 0.05))
    print('  selftest %s' % ('全部通过' if ok else '有失败'))
    return ok


def main():
    ap = argparse.ArgumentParser(description='折叠"同音高 ≤tol 且无音频起音支撑"的重复对')
    ap.add_argument('song', nargs='?', help='曲目名（songs/<名>/song.json）')
    ap.add_argument('--stem-wav', help='该层的分轨音频（判据来源）')
    ap.add_argument('--track', default='Bass')
    ap.add_argument('--tol', type=float, default=0.06, help='同音高对的时间容差（秒，默认 0.06 与 preflight 同口径）')
    ap.add_argument('--dry', action='store_true', help='只出逐条表，不写任何文件')
    ap.add_argument('--no-render', action='store_true', help='只改 song.json，不重渲染（⚠ 不重渲染 = 音频停在旧版）')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not (a.song and a.stem_wav):
        ap.print_help()
        return 2
    if not os.path.isfile(a.stem_wav):
        raise SystemExit('找不到分轨音频 %s' % a.stem_wav)
    import bass_pair_audio as BPA

    song_p = find_song(a.song) if not a.song.endswith('.json') else a.song
    song = json.load(open(song_p, encoding='utf-8'))
    md5_before = md5(song_p)
    bpm = float(song['bpm'])
    spb = 60.0 / bpm
    bb = bar_beats_of(song)
    raw = load_track_notes(song, a.track)
    items = to_list(raw, bb)
    print('曲目 %s（song.json md5 %s）· BPM %.1f · 小节 = %.1f 拍' % (song_p, md5_before[:12], bpm, bb))
    print('%s 轨 %d 音' % (a.track, len(items)))

    # ── 判据：逐对量 burst（尺子先自检）
    print('\n尺子自检：')
    if not BPA.selftest():
        raise SystemExit('✗ 尺子自检不过，不许改数据')
    y = BPA.load_audio(a.stem_wav)
    pidx = pair_index(items, a.tol, spb)
    ns = len(BPA.close_pairs([(it['beat'] * spb, it['dur'] * spb, it['p'], it['v']) for it in items], a.tol))
    if ns != len(pidx):
        raise SystemExit('✗ 两套配对口径数出的对数不同（preflight 口径 %d vs 位置键 %d）—— 拒绝改数据'
                         % (ns, len(pidx)))
    print('两套配对口径一致：都是 %d 对（preflight 口径 vs 位置键）' % ns)
    # ⚠ 逐对的 burst 直接在**位置键顺序**上算 —— 不靠"另一个函数的返回顺序与我一致"
    ks = sorted(pidx.keys())
    rows = BPA.judge_pairs(y, [(k[0], (pidx[k][0]['beat'] * spb, pidx[k][0]['dur'] * spb,
                                       pidx[k][0]['p'], pidx[k][0]['v']),
                                (pidx[k][1]['beat'] * spb, pidx[k][1]['dur'] * spb,
                                 pidx[k][1]['p'], pidx[k][1]['v'])) for k in ks])
    flags = {}
    for (k, r) in zip(ks, rows):
        if r['two'] is None:
            raise SystemExit('✗ 有一对量不出 burst（音高 %d gap %.3f），拒绝在缺读数时改数据'
                             % (r['p'], r['gap']))
        flags[(k[0], k[1], k[2])] = r['two']
    n2 = sum(1 for r in rows if r['two'])
    print('\n同音高 ≤%.0fms 对 %d 个：两次真实起音 **%d**（保留）· 一次起音被重复检出 **%d**（折叠）'
          % (a.tol * 1000, len(rows), n2, len(rows) - n2))

    out, log = collapse_pairs(items, flags, bb, a.tol / spb)
    folds = [x for x in log if x['动作'] == '折叠']
    print('折叠后 %s 轨 %d → **%d** 音（删 %d）' % (a.track, len(items), len(out), len(items) - len(out)))

    # 逐条表（按小节），这是"改必须分段 + 打印逐段改动表"要求的形式
    bybar = defaultdict(list)
    for x in folds:
        bar = int(x['a_beat'] // bb)
        bybar[bar].append(x)
    print('\n逐小节改动表（只列有折叠的小节，共 %d 个小节）：' % len(bybar))
    for bar in sorted(bybar):
        xs = bybar[bar]
        ps = sorted(set(x['p'] for x in xs))
        gaps = [x['gap'] * spb * 1000 for x in xs]
        print('  小节 %3d：折叠 %d 对 · 音高 %s · 间隔 %s ms'
              % (bar, len(xs), ps, ['%.0f' % g for g in gaps[:6]] + (['…'] if len(gaps) > 6 else [])))

    if a.dry:
        print('\n（--dry：没有写任何文件）')
        return 0

    # ── 写回（只改这一条轨）
    store_track_notes(song, a.track, out)
    shutil.copy2(song_p, song_p + '.prepair.bak')
    # ⚠ 必须用 `json_io.save`：它写 LF **且**是"紧凑可读"规范格式（`json.dump(indent=1)`
    #   会把每个数字拆一行 —— 实测 16606 行 → 91444 行，`song_json_canonical` FAIL）
    import json_io
    json_io.save(song_p, song)
    md5_after = md5(song_p)
    print('\n写出 %s\n  备份 %s\n  md5 %s → %s' % (song_p, song_p + '.prepair.bak', md5_before[:12], md5_after[:12]))

    # ── 读回验证（工具不报错 ≠ 生效）
    back = json.load(open(song_p, encoding='utf-8'))
    rb = load_track_notes(back, a.track)
    print('  读回 %s 轨 %d 音（应 %d）· 其它轨音符数 %s'
          % (a.track, len(rb),
             len(out),
             {k: (len(v['notes']) if isinstance(v, dict) else len(v))
              for k, v in (back.get('notes_extra') or {}).items() if k != a.track}))
    if len(rb) != len(out):
        raise SystemExit('✗ 读回音数不符，回滚用 %s' % (song_p + '.prepair.bak'))

    if a.no_render:
        print('\n⚠ --no-render：song.json 已改但**没有重渲染** —— 音频仍停在旧版（红线 5）')
        return 0
    py = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')
    cmd = [py, os.path.join(HERE, 'make_song.py'), a.song, '--no-tune']
    print('\n重渲染：%s' % ' '.join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit('✗ 重渲染失败（rc=%d）；song.json 已改，回滚用 %s' % (r.returncode, song_p + '.prepair.bak'))
    return 0


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                              # noqa: BLE001
        pass
    sys.exit(main())
