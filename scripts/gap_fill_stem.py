#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""gap_fill_stem.py —— **按秒补"整层没响"的缺口**（补的是某条分轨的真实转录内容）。

## 为什么不是 `restore_gap_fill.py` 就够

`restore_gap_fill.py` 是**按小节**判"我们 0 音 / 比来源少"，对**复音层**太粗：
BGM35 的 `other` 分轨转录 **8702 音**（去重后 5807、每小节中位 42 音），
而我们 Strings 只有 702 音（3 音/小节）—— 按小节判"偏少"会 194/207 小节全中，
**一次加 8000+ 音**（≈现有全曲翻倍），那是把"每音虚高"当成了"该补多少"。

本工具改成**按秒**判（与 `preflight` ⑧ 同一粒度），并做四道过滤：

  ① **去重**：同音高 ≤`--merge` 合并、时值 < `--min-dur` 丢弃（帧边界碎片）
  ② **只补缺口秒**：该秒来源有音而**我们对应轨一个音都没有**（`--min-cov` 可放宽成"覆盖不足"）
  ③ **逐音夹音域**：越界的音按 ±12 夹进 `TR_RANGE`（**逐音夹，不是整轨移八度** ——
     整轨移会把本来没问题的音一起改掉，`PITFALLS` 253）
  ④ **不重复加**：同音高 ±`--dup-tol` 秒内已有我们的音就跳过

## 用法

```bash
python scripts/gap_fill_stem.py <曲目名> --stem-midi <分轨转录.mid> --track Strings \
       [--merge 0.10] [--min-dur 0.08] [--min-cov 0] [--dup-tol 0.05] [--dry|--apply]
python scripts/gap_fill_stem.py --selftest
```

输出（`--apply`）：写回 `song.json` 的 `notes_extra[<轨>]`，并打印**逐小节改动表** +
"补了多少秒 / 还剩多少缺口"。⚠ 补完**必须重渲染**再看体检（红线 5）。
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)


def load_notes(path):
    import midi_file
    m = midi_file.import_midi(path)
    spb = 60.0 / float(m.get('bpm') or 120.0)
    out = []
    for tr in m.get('tracks', []):
        if tr.get('drum') or tr.get('channel') == 9:
            continue
        for (st, du, p, v) in tr.get('notes', []):
            out.append((float(st) * spb, float(du) * spb, int(p), int(v)))
    out.sort()
    return out


def dedup(ns, merge=0.10, min_dur=0.08):
    """同音高 ≤merge 合并（时值取到最晚结束）、时值 <min_dur 丢掉。→ [(t, dur, p, v)]"""
    byp = defaultdict(list)
    for (t, d, p, v) in ns:
        if d >= min_dur:
            byp[p].append([t, d, v])
    out = []
    for p, lst in byp.items():
        lst.sort()
        cur = None          # [start, end, vel]
        for (t, d, v) in lst:
            if cur is not None and t - cur[0] <= merge:
                cur[1] = max(cur[1], t + d)          # ⚠ 合并的是"结束时刻"，不是"时值"
                cur[2] = max(cur[2], v)
            else:
                if cur is not None:
                    out.append((cur[0], cur[1] - cur[0], p, cur[2]))
                cur = [t, t + d, v]
        if cur is not None:
            out.append((cur[0], cur[1] - cur[0], p, cur[2]))
    out.sort()
    return out


def live_secs(ns, nsec):
    """→ [set(音高)]：逐秒有音在响的音高集合"""
    live = [set() for _ in range(nsec + 1)]
    for (t, d, p, _v) in ns:
        a, b = int(t), int(t + max(d, 0.05))
        for s in range(a, min(b + 1, nsec + 1)):
            live[s].add(p)
    return live


def clamp_pitch(p, lo, hi):
    """逐音夹进 [lo, hi]（±12 的倍数）；夹不进返回 None"""
    q = int(p)
    for _ in range(6):
        if q < lo:
            q += 12
        elif q > hi:
            q -= 12
        else:
            return q
    return None


def drop_to_register(p, lo, hi, ceiling):
    """把音放到 range 内、且**不高于 ceiling**（= 同拍旋律音高）的八度上。

    为什么需要（2026-10-02 实测，这条是真回归）：补 `other`→Strings 时，候选音落在 67–79，
    而同拍的旋律（Melody/Hook）常只有 60 上下 ⇒ 补进来的层**盖到旋律头上**：
    同一条守卫 `accompaniment_harmony` 的"音区分离中位"从 **+2.5 掉到 −10 半音**、
    "旋律在下"从 30% 涨到 **59%**。逐音往下挪八度即可（音级不变、和声仍安全）。
    """
    if ceiling is None:
        return clamp_pitch(p, lo, hi)
    for k in (0, -12, -24, 12, 24):
        q = int(p) + k
        if lo <= q <= hi and q <= ceiling:
            return q
    return clamp_pitch(p, lo, hi)


def melody_ceiling_by_beat(song, spb, grid=0.5):
    """→ {拍格: 该格旋律最高音}（旋律轨 = Melody，缺则 Hook）。用于把补进来的层压在旋律下方。"""
    for tr in ('Melody', 'Hook'):
        v = (song.get('notes_extra') or {}).get(tr)
        if not v:
            continue
        arr = (v.get('notes') or []) if isinstance(v, dict) else v
        out = {}
        for x in arr:
            beat = float(x[0]) * 4.0 + float(x[1])
            p = int(x[3])
            k = int(beat / grid)
            if p > out.get(k, -1):
                out[k] = p
        if out:
            return out
    return {}


def vel_scale(notes, k, lo=30):
    """力度乘数（保底 lo，避免弱到听不见）"""
    if k == 1.0:
        return notes
    return [(t, d, p, max(lo, int(round(v * k)))) for (t, d, p, v) in notes]


def run(song_p, stem_midi, track, merge, min_dur, min_cov, dup_tol, apply_, velk=1.0, place=False):
    import song_engine
    d = json.load(open(song_p, encoding='utf-8'))
    bpm = float(d['bpm'])
    spb = 60.0 / bpm
    meter = d.get('meter') or [4, 4]
    bb = float(meter[0]) * 4.0 / float(meter[1])
    nbars = sum(int(s.get('bars') or 0) for s in (d.get('sections') or []))
    nsec = int(nbars * bb * spb) + 2

    raw = load_notes(stem_midi)
    src = dedup(raw, merge, min_dur)
    print('== %s ← %s ==' % (os.path.basename(os.path.dirname(song_p)), os.path.basename(stem_midi)))
    print('   来源原始 %d 音 → 去重后 **%d** 音（同音高 ≤%.0fms 合并、时值 <%.0fms 丢）'
          % (len(raw), len(src), merge * 1000, min_dur * 1000))

    v = d['notes_extra'].get(track)
    if v is None:
        raise SystemExit('song.json 的 notes_extra 里没有轨 %s' % track)
    is_dict = isinstance(v, dict)
    arr = (v.get('notes') or []) if is_dict else v
    mine = [(float(x[0]) * bb + float(x[1])) * spb for x in arr]
    mine_n = [((float(x[0]) * bb + float(x[1])) * spb, float(x[2]) * spb, int(x[3])) for x in arr]
    print('   我们 %s 现有 %d 音' % (track, len(mine_n)))

    live_s = live_secs([(t, d, p, v) for (t, d, p, v) in src], nsec)
    live_m = live_secs([(t, d, p, 80) for (t, d, p) in mine_n], nsec)
    # 缺口秒：来源有音，我们该秒 "一个音都没有"（min_cov=0）或 "覆盖 < min_cov"
    gap = []
    for s in range(nsec):
        if not live_s[s]:
            continue
        cov = len(live_s[s] & live_m[s]) / max(1, len(live_s[s]))
        if cov <= min_cov:
            gap.append(s)
    gset = set(gap)
    print('   逐秒：来源有音 %d 秒 · 我们覆盖 ≤%.0f%% 的秒 **%d**'
          % (sum(1 for s in live_s if s), min_cov * 100, len(gap)))

    rng = song_engine.TR_RANGE.get(track)
    ceiling = melody_ceiling_by_beat(d, spb) if place else {}
    cand, dropped_rng = [], 0
    for (t, dur, p, vel) in src:
        if int(t) not in gset:
            continue
        q = p
        if rng:
            if place:
                q = drop_to_register(p, rng[0], rng[1], ceiling.get(int((t / spb) / 0.5), None))
            else:
                q = clamp_pitch(p, rng[0], rng[1])
            if q is None:
                dropped_rng += 1
                continue
        # 不重复加：同音高 ±dup_tol 内已有我们的音
        if any(pp == q and abs(tt - t) <= dup_tol for (tt, _dd, pp) in mine_n):
            continue
        cand.append((t, dur, q, vel))
    cand = vel_scale(cand, velk)
    print('   候选补音（落在缺口秒、夹过音域、去掉重复）**%d** 个%s'
          % (len(cand), ('（另有 %d 个夹不进音域被丢）' % dropped_rng) if dropped_rng else ''))
    byp = defaultdict(int)
    for (_t, _d, p, _v) in cand:
        byp[p] += 1
    if byp:
        top = sorted(byp.items(), key=lambda kv: -kv[1])[:8]
        print('   音高分布（前 8）：%s' % ' '.join('%d×%d' % (p, c) for p, c in top))

    per_bar = defaultdict(int)
    for (t, _d, _p, _v) in cand:
        per_bar[int(t / (bb * spb))] += 1

    if not apply_:
        print('\n   逐小节改动表（前 25 个小节）：')
        for b in sorted(per_bar)[:25]:
            print('     小节 %3d：+%d 音' % (b, per_bar[b]))
        print('   … 共 %d 个小节有改动' % len(per_bar))
        print('\n   （--dry：没有改任何文件。确认后加 `--apply`）')
        print('   ⚠ 这条工序是**整曲级**的（按秒补缺口）—— 按用户 2026-09-25 的口径，'
              '整曲处理需要用户明确同意；本工具用于"整层没响"这类整曲级缺陷，'
              '逐小节/逐段的小改动请用 `restore_gap_fill.py`')
        return 0

    for (t, dur, p, vel) in cand:
        ab = t / spb
        bar = int(ab // bb)
        off = ab - bar * bb
        arr.append([bar, round(off, 4), round(dur / spb, 4), int(p), int(vel)])
    arr.sort(key=lambda n: (n[0], n[1], n[3]))
    if is_dict:
        v['notes'] = arr
    else:
        d['notes_extra'][track] = arr
    # ⚠ 必须用 `json_io.save`：它写 LF **且**是"紧凑可读"规范格式。用 `json.dump(indent=1)`
    #   会把每个数字拆一行（实测 16606 行 → 91444 行），守卫 `song_json_canonical` 直接 FAIL
    import json_io
    json_io.save(song_p, d)
    print('\n   已写回 %s：%s %d → **%d** 音' % (song_p, track, len(mine_n), len(arr)))
    # 读回 + 复查缺口
    back = json.load(open(song_p, encoding='utf-8'))
    bv = back['notes_extra'][track]
    ba = (bv.get('notes') or []) if isinstance(bv, dict) else bv
    bn = [((float(x[0]) * bb + float(x[1])) * spb, float(x[2]) * spb, int(x[3])) for x in ba]
    live_b = live_secs([(t, d, p, 80) for (t, d, p) in bn], nsec)
    rest = [s for s in gap if not (live_s[s] & live_b[s])]
    print('   读回 %d 音 · 复查：原缺口 %d 秒 → 仍未覆盖 **%d** 秒'
          % (len(bn), len(gap), len(rest)))
    if rest:
        print('     （仍未覆盖的秒：%s%s）'
              % (rest[:12], ' …' if len(rest) > 12 else ''))
    return 0


def selftest():
    """契约自检：去重方向、缺口判定、逐音夹音域、不重复加。"""
    import tempfile
    ok = True

    def chk(lab, got, want):
        nonlocal ok
        f = 'PASS' if got == want else 'FAIL'
        ok = ok and got == want
        print('  [%s] %-44s 期望 %-10s 实得 %-10s' % (f, lab, want, got))

    # ① 去重：同音高 30ms 两条 → 一条；时值 <80ms 丢掉
    ns = [(1.00, 0.30, 60, 90), (1.03, 0.20, 60, 80), (2.00, 0.02, 62, 90), (3.00, 0.5, 64, 90)]
    dd = dedup(ns, 0.10, 0.08)
    chk('去重：4 条 → 2 条（合并 + 丢碎片）', len(dd), 2)
    # 最晚结束 = max(1.00+0.30, 1.03+0.20) = 1.30 ⇒ 时值 0.30（不是 0.33 —— 别把起音差算进时值）
    chk('去重：时值 = 最晚结束 − 起音（0.30）', round(dd[0][1], 3), 0.30)
    chk('去重：力度取较大', dd[0][3], 90)
    # 真能抓到"把起音差算进时值"的用例：后一条明显更长
    dd2 = dedup([(1.00, 0.10, 60, 90), (1.05, 0.50, 60, 80)], 0.10, 0.08)
    chk('去重：后一条更长时取它的结束（0.55）', round(dd2[0][1], 3), 0.55)
    # ② 逐音夹音域
    chk('夹音域：30 → 42（+12 进 41..99）', clamp_pitch(30, 41, 99), 42)
    chk('夹音域：103 → 91（−12）', clamp_pitch(103, 41, 99), 91)
    chk('夹音域：已在范围内不动', clamp_pitch(70, 41, 99), 70)
    # ④ 放到旋律下方：70 而旋律最高 62 ⇒ 降到 58（−12，仍在 41..99）
    chk('音区放置：70/旋律62 → 58', drop_to_register(70, 41, 99, 62), 58)
    chk('音区放置：已在下方的音不动（55/旋律62）', drop_to_register(55, 41, 99, 62), 55)
    chk('音区放置：旋律未知 → 退回只夹音域', drop_to_register(70, 41, 99, None), 70)
    # ③ 逐秒 live 覆盖
    lv = live_secs([(1.0, 0.5, 60, 90)], 5)
    chk('live_secs：第 1 秒有音高 60', 60 in lv[1], True)
    chk('live_secs：第 3 秒没有', len(lv[3]), 0)
    print('  selftest %s' % ('全部通过' if ok else '有失败'))
    return ok


def main():
    ap = argparse.ArgumentParser(description='按秒补"整层没响"的缺口（补分轨的真实转录内容）')
    ap.add_argument('song', nargs='?', help='曲目名或 song.json 路径')
    ap.add_argument('--stem-midi', help='该层的分轨转录 MIDI')
    ap.add_argument('--track', default='Strings', help='要补进哪条引擎轨')
    ap.add_argument('--merge', type=float, default=0.10, help='同音高合并窗（秒）')
    ap.add_argument('--min-dur', type=float, default=0.08, help='时值下限（秒），低于它丢弃')
    ap.add_argument('--min-cov', type=float, default=0.0,
                    help='该秒覆盖低于此值才算缺口（0=该秒一个音都没有）')
    ap.add_argument('--dup-tol', type=float, default=0.05, help='同音高 ±该秒内已有音则不重复加')
    ap.add_argument('--vel-scale', type=float, default=1.0,
                    help='补进来的音**力度乘数**（<1 = 让它垫在下面）。BGM35 实测：1.0 时'
                         '预听层把钢琴盖住（渲染总起音 809→682），0.7 时不盖')
    ap.add_argument('--place-under-melody', action='store_true',
                    help='把补进来的音放到**同拍旋律音高之下**的八度（音级不变）。'
                         '⚠ 补复音层时建议开：BGM35 实测不开会把"旋律与伴奏音区分离中位"'
                         '从 +2.5 半音压到 **−10**（旋律被盖住），开了只掉到 −2.5')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not (a.song and a.stem_midi):
        ap.print_help()
        return 2
    song_p = a.song
    if not song_p.endswith('.json'):
        song_p = os.path.join(ROOT, 'songs', a.song, 'song.json')
    if not os.path.isfile(song_p):
        raise SystemExit('找不到 %s' % song_p)
    if not os.path.isfile(a.stem_midi):
        raise SystemExit('找不到分轨转录 %s' % a.stem_midi)
    return run(song_p, a.stem_midi, a.track, a.merge, a.min_dur, a.min_cov, a.dup_tol,
               a.apply and not a.dry, a.vel_scale, a.place_under_melody)


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                          # noqa: BLE001
        pass
    sys.exit(main())
