#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""lib_timeline_audit.py —— **跨曲目扫"时间轴/时长"这一族缺陷**（一条命令覆盖全库）。

## 为什么要有它（用户 2026-10-02 口径："**不要只针对单一音乐，主要是要推广到大部分音乐**"）

BGM35 上挖到的两个缺陷，本质都是**"整轨被静默缩放"**，而它们**不是那一首曲子的特例**：

  · 转录产物的 beat 是按**它自己声明的 bpm** 记的，写进 `song.json` 时若没换算，
    整轨就被乘 `曲子bpm / 转录bpm`（BGM35：120 → 149.8 ⇒ 秒数被压 0.801×）
  · MIDI 里没有 `set_tempo` 时，渲染器按 120BPM 算 ⇒ 与谱面 bpm 差一个比值

**这两类都不会报错**：文件都在、渲染成功、体检只报"某些秒密度过少"。
所以必须有一条**跨曲、只看"时间对不对"**的判据，把它们一次扫出来。

## 判据（都不需要真值，也不需要分轨）

| # | 判据 | 抓什么 | 判 FAIL 的条件 |
|---|---|---|---|
| ① | **MIDI 末音秒 vs 曲长** | 整轨被缩放 | 末音 < 0.9 × 曲长（曲长取"音频时长"或"小节数×每小节秒数"） |
| ② | **渲染音频时长 vs 谱面时长** | 渲染按了别的 bpm | 相对差 > 2% |
| ③ | **notes_extra 里的 bpm 口径痕迹** | 写回时没换算 | 各轨"绝对拍跨度"的最大值彼此差 > 20%（同一首曲子里两条轨的口径不同） |
| ④ | **reference 音频时长 vs 谱面时长** | 曲长/小节数/bpm 三者不自洽 | 相对差 > 2% |

③ 是这次最能推广的一条：**同一份 `song.json` 里，所有轨的 beat 必须共用同一个 bpm 口径** ——
只要有一条轨是别的口径（BGM35 的 Bass 就是），它的"绝对拍跨度"就会与别人差一个比值。

## 用法

```bash
python scripts/lib_timeline_audit.py [--lib <曲库目录>] [--only <曲名>] [--json 出.json]
python scripts/lib_timeline_audit.py --selftest
```
退出码：0 = 全部通过；1 = 有 FAIL。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

LIBS = ('songs', 'songs_direct')
# ③ 的口径容差：同一首曲子里各轨绝对拍跨度最大/最小之比超过它 ⇒ 口径不一致
SPAN_RATIO_TOL = 1.2
# ① 末音 vs 曲长
TAIL_TOL = 0.90
# ②④ 时长相对差
DUR_TOL = 0.02


def song_dirs(lib=None, only=None):
    """→ 曲目目录列表（**按 realpath 去重** —— 同 `lib_defect_scan.song_dirs`）。

    ⚠ 2026-10-02：`LIBS` 默认 `('songs', 'songs_direct')`，而本机 **`songs` 是指向
    `songs_direct` 的符号链接** ⇒ 同一首被扫两遍、输出条数翻倍（16 → 32），
    跨曲统计会虚高一倍。去重必须用 `realpath`（`abspath` 不解析符号链接）。
    """
    out, seen = [], set()
    bases = [lib] if lib else [os.path.join(ROOT, b) for b in LIBS]
    for base in bases:
        if not os.path.isdir(base):
            continue
        for d in sorted(os.listdir(base)):
            p = os.path.join(base, d)
            if os.path.isfile(os.path.join(p, 'song.json')):
                if only and d != only:
                    continue
                rp = os.path.realpath(p)
                if rp in seen:
                    continue
                seen.add(rp)
                out.append(p)
    return out


def audio_dur(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        import soundfile as sf
        return float(sf.info(path).duration)
    except Exception:                                          # noqa: BLE001
        return None


def rows_of(v):
    return (v.get('notes') or []) if isinstance(v, dict) else (v or [])


def tail_verdict(last_sec, score_sec, tol=TAIL_TOL):
    """① 的判据（纯函数，便于直接自检）：末音 vs 谱面时长 → ('PASS'/'FAIL', detail)"""
    if not score_sec:
        return 'UNKNOWN', '谱面时长为 0（bpm/小节数缺）'
    ratio = last_sec / score_sec
    if ratio < tol:
        return 'FAIL', ('MIDI 末音 %.1f 秒 < %.0f%% × 谱面 %.1f 秒（比值 %.3f）—— '
                        '整轨/部分轨被缩放，或有轨没写进去'
                        % (last_sec, 100 * tol, score_sec, ratio))
    return 'PASS', '末音 %.1f 秒 / 谱面 %.1f 秒（%.0f%%）' % (last_sec, score_sec, 100 * ratio)


def span_verdict(spans, tol=SPAN_RATIO_TOL):
    """③ 的判据（纯函数）：各轨绝对拍跨度 → ('PASS'/'FAIL', detail)"""
    if not spans:
        return 'UNKNOWN', '没有 notes_extra'
    lo, hi = min(spans.values()), max(spans.values())
    ratio = hi / max(lo, 1e-9)
    if ratio > tol:
        worst = min(spans, key=lambda k: spans[k])
        return 'FAIL', ('各轨绝对拍跨度差 %.2f×（最短 %s=%.1f 拍，最长 %.1f 拍）—— '
                        '**同一首曲子里有轨用了别的 bpm 口径**' % (ratio, worst, lo, hi))
    return 'PASS', '各轨跨度比 %.2f× ≤ %.2f' % (ratio, tol)


def dur_verdict(a_sec, b_sec, tag, tol=DUR_TOL):
    """②/④ 的判据（纯函数）：两个时长是否自洽"""
    if not (a_sec and b_sec):
        return 'UNKNOWN', '缺时长（%s）' % tag
    rel = abs(a_sec - b_sec) / b_sec
    st = 'FAIL' if rel > tol else 'PASS'
    return st, '%s %.1f 秒 vs %.1f 秒（差 %.1f%%）' % (tag, a_sec, b_sec, 100 * rel)


def audit_song(d):
    """→ dict(checks=[(id, state, detail)], ...)"""
    r = dict(name=os.path.basename(d), checks=[])
    song_p = os.path.join(d, 'song.json')
    try:
        s = json.load(open(song_p, encoding='utf-8'))
    except Exception as e:                                     # noqa: BLE001
        r['checks'].append(('song.json', 'FAIL', '读不了：%s' % e))
        return r
    bpm = float(s.get('bpm') or 0)
    meter = s.get('meter') or [4, 4]
    bar_beats = float(meter[0]) * 4.0 / float(meter[1])
    nbars = sum(int(x.get('bars') or 0) for x in (s.get('sections') or []))
    score_sec = nbars * bar_beats * 60.0 / bpm if bpm else 0.0
    r.update(bpm=bpm, nbars=nbars, score_sec=round(score_sec, 1))

    # ③ 各轨绝对拍跨度口径
    spans = {}
    for tr, v in (s.get('notes_extra') or {}).items():
        rows = rows_of(v)
        if not rows:
            continue
        beats = [float(x[0]) * bar_beats + float(x[1]) for x in rows if len(x) >= 2]
        if beats:
            spans[tr] = max(beats)
    if spans:
        st3, d3 = span_verdict(spans)
        r['checks'].append(('③轨间口径', st3, d3))
        r['spans'] = {k: round(v, 1) for k, v in sorted(spans.items())}

    # ① MIDI 末音
    mid = None
    rj = os.path.join(d, 'render.json')
    if os.path.isfile(rj):
        try:
            cfg = json.load(open(rj, encoding='utf-8'))
            mid = os.path.join(d, cfg.get('mid') or '')
            if not os.path.isfile(mid):
                mid = None
        except Exception:                                      # noqa: BLE001
            mid = None
    if mid is None:
        cand = [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith('.mid')]
        mid = cand[0] if cand else None
    if mid:
        try:
            import midi_file
            m = midi_file.import_midi(mid)
            mbpm = float(m.get('bpm') or 0)
            spb = 60.0 / mbpm if mbpm else 0.5
            last = 0.0
            for tr in m.get('tracks', []):
                for x in tr.get('notes', []):
                    last = max(last, (float(x[0]) + float(x[1])) * spb)
            r['mid'] = os.path.basename(mid)
            r['mid_last_sec'] = round(last, 1)
            r['mid_bpm'] = mbpm
            st1, d1 = tail_verdict(last, score_sec)
            r['checks'].append(('①末音vs曲长', st1, d1))
        except Exception as e:                                 # noqa: BLE001
            r['checks'].append(('①末音vs曲长', 'UNKNOWN', '读 MIDI 失败：%s' % e))

    # ② 渲染音频时长 vs 谱面
    wav = None
    if os.path.isfile(rj):
        try:
            cfg = json.load(open(rj, encoding='utf-8'))
            wav = os.path.join(d, (cfg.get('out') or '') + '.wav')
        except Exception:                                      # noqa: BLE001
            wav = None
    if not wav or not os.path.isfile(wav):
        cand = [os.path.join(d, f) for f in sorted(os.listdir(d))
                if f.endswith('.wav') or f.endswith('.ogg')]
        wav = cand[0] if cand else None
    ad = audio_dur(wav)
    r['audio'] = os.path.basename(wav) if wav else None
    r['audio_sec'] = round(ad, 1) if ad else None
    if ad and score_sec:
        st2, d2 = dur_verdict(ad, score_sec, '音频')
        r['checks'].append(('②音频vs谱面', st2, d2))

    # ④ 参考音频时长 vs 谱面（曲长自洽性）
    ref = None
    if os.path.isfile(rj):
        try:
            import scorecard as _sc
            cfg = json.load(open(rj, encoding='utf-8'))
            ref = _sc.ref_path(cfg.get('ref') or '')
        except Exception:                                      # noqa: BLE001
            ref = None
    rd = audio_dur(ref) if ref else None
    if rd and score_sec:
        r['ref_sec'] = round(rd, 1)
        st4, d4 = dur_verdict(rd, score_sec, '参考')
        r['checks'].append(('④参考vs谱面', st4, d4))
    return r


def selftest():
    """尺子自检：造三首合成曲 —— 正常 / 被缩放 0.8× / 某轨口径不同，方向必须对。"""
    import tempfile
    import midi_file
    ok = True

    def chk(lab, got, want):
        nonlocal ok
        f = 'PASS' if got == want else 'FAIL'
        ok = ok and got == want
        print('  [%s] %-52s 期望 %-8s 实得 %-8s' % (f, lab, want, got))

    def mk(d, bpm, span_beats, bad_track=False):
        os.makedirs(d, exist_ok=True)
        rows = [[0, 0.0, 1.0, 60, 90], [int(span_beats // 4), span_beats % 4, 1.0, 62, 90]]
        bad = [[0, 0.0, 1.0, 40, 90], [int(span_beats * 0.6 // 4), (span_beats * 0.6) % 4, 1.0, 41, 90]]
        s = dict(name=os.path.basename(d), bpm=bpm, meter=[4, 4],
                 sections=[dict(name='S01', bars=int(span_beats // 4), chords=[], melody='')],
                 chords={}, melody={},
                 notes_extra={'Piano': rows, 'Bass': (bad if bad_track else rows)})
        json.dump(s, open(os.path.join(d, 'song.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        midi_file.export_midi(dict(bpm=bpm, division=480, tracks=[dict(
            index=0, name='Piano', channel=0, program=0, drum=False, mute=False, solo=False,
            hidden=False, ccs=[], program_changes=[[0.0, 0]], markers=[],
            notes=[[float(x[0]) * 4 + float(x[1]), float(x[2]), int(x[3]), int(x[4])]
                   for x in rows])]), os.path.join(d, 'x.mid'))
        return d

    t = tempfile.mkdtemp(prefix='lta_selftest_')
    # ⚠ 直接测**判据函数**，不造 MIDI 夹具：`midi_file.export_midi` 会把音高夹到合法音域
    #   （夹具里用音高 40 写的"坏轨"被静默改掉），造夹具反而会把判据的真实行为遮住。
    chk('正常曲（末音=曲长）：① PASS', tail_verdict(240.0, 240.0)[0], 'PASS')
    chk('被缩放 0.8×：① FAIL', tail_verdict(192.0, 240.0)[0], 'FAIL')
    chk('刚过门（0.905×）：① PASS', tail_verdict(217.2, 240.0)[0], 'PASS')
    chk('刚好压线（0.895×）：① FAIL', tail_verdict(214.8, 240.0)[0], 'FAIL')
    chk('③ 同口径：PASS', span_verdict({'Piano': 600.0, 'Bass': 598.0})[0], 'PASS')
    chk('③ 一条轨差 1.25×：FAIL', span_verdict({'Piano': 600.0, 'Bass': 480.0})[0], 'FAIL')
    chk('③ 空：UNKNOWN', span_verdict({})[0], 'UNKNOWN')
    chk('② 时长一致：PASS', dur_verdict(332.9, 331.6, '音频')[0], 'PASS')
    chk('② 差 20%：FAIL', dur_verdict(280.0, 331.6, '音频')[0], 'FAIL')
    chk('② 缺一侧：UNKNOWN', dur_verdict(None, 331.6, '音频')[0], 'UNKNOWN')
    # 端到端：真的读一首合成曲（只验"能跑通 + ③ 抓得到"）
    d3 = mk(os.path.join(t, 'badunit'), 150.0, 600.0, bad_track=True)
    r3 = audit_song(d3)
    st = lambda r, key: next((s for (k, s, _d) in r['checks'] if k.startswith(key)), 'NA')
    chk('端到端：某轨口径不同 ⇒ ③ FAIL', st(r3, '③'), 'FAIL')
    print('  selftest %s' % ('全部通过' if ok else '有失败'))
    return ok


def main():
    ap = argparse.ArgumentParser(description='跨曲目扫"时间轴/时长"这一族缺陷')
    ap.add_argument('--lib', default=None, help='曲库目录（默认 songs + songs_direct）')
    ap.add_argument('--only', default=None, help='只查这一首')
    ap.add_argument('--json', default=None)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    dirs = song_dirs(a.lib, a.only)
    if not dirs:
        raise SystemExit('没找到曲目（--lib %s）' % (a.lib or LIBS))
    reps = [audit_song(d) for d in dirs]
    fails = 0
    print('%-22s %7s %8s %8s %8s  %s' % ('曲目', 'bpm', '谱面秒', '音频秒', '末音秒', '判'))
    print('-' * 92)
    for r in reps:
        bad = [c for c in r['checks'] if c[1] == 'FAIL']
        fails += 1 if bad else 0
        print('%-22s %7.1f %8s %8s %8s  %s'
              % (r['name'], r.get('bpm') or 0, r.get('score_sec'), r.get('audio_sec'),
                 r.get('mid_last_sec'), ('✗ ' + ' / '.join(c[0] for c in bad)) if bad else '✓'))
        for (k, s, d) in bad:
            print('      %s %s' % (k, d))
    print('\n合计 %d 首 · FAIL %d 首' % (len(reps), fails))
    if a.json:
        json.dump(reps, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('→ %s' % a.json)
    return 1 if fails else 0


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                          # noqa: BLE001
        pass
    sys.exit(main())
