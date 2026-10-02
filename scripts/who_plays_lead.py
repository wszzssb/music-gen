#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""who_plays_lead.py —— **"参考曲里这条旋律到底是谁在弹？"**（按音高判，不是按响度）。

## 为什么需要（2026-10-02，BGM35 复刻那一轮）

用户口径是"**像不像 / 哪里不像**"。而"不像"最常见的根因就是**主奏音色用错**：
我们拿钢琴弹了参考曲里合成器（或吉他）弹的那条线。
`timbre_audit.py` 量的是"**这一段整体哪条分轨最响**"（编配层），
而**主奏旋律**往往不是最响的那条 —— 所以它抓不到这一类。

## 判据（为什么不能按响度）

第一版用"起音窗口内 RMS 最大者"当赢家 → **被低音和鼓骗了**：参考曲上打出
`bass 36% · drums 32%` 当"主奏"（主奏不可能在低频）。改成**按音高**：
对每个旋律音算它的**基频 + 前两个谐波**（`440·2^((p−69)/12)`），
在这些频率附近量每条分轨的谱幅度 —— **低频乐器在旋律音高上没有能量，自然出局**。
同一份素材重测：`other 49% · guitar 34% · piano 9%`（与用户听感、`.mid` 音色三方一致）。

## 用法

```powershell
$py = "<根>\.venv\Scripts\python.exe"

# ① 主奏在独立轨上（最常见）：直接拿该轨的音符当旋律线
& $py scripts\who_plays_lead.py <demucs分轨目录> <song.json> --track Melody --by-sec 30

# ② 旋律混在伴奏轨里（前半段 `Melody` 轨是空的）：用"每小节最高音"当旋律线代理
& $py scripts\who_plays_lead.py <分轨目录> <song.json> --track Piano --melody-proxy --by-section

# ③ 只看前半段 / 换判定为"主奏"的分轨族
& $py scripts\who_plays_lead.py <分轨目录> <song.json> --end 135 --lead-stems other,guitar
```

输出：6 条分轨（drums/bass/other/vocals/guitar/piano）在各段/各时间桶里的**占比**。
`--lead-stems` 给的是"哪些分轨算旋律乐器族"（用于 `--json` 里的 `lead_share` 汇总）。

⚠ **它能回答与不能回答**：能回答"这条线**不是**钢琴弹的"；
**不能**回答"`other` 里面到底是合成器还是萨克斯"（Demucs 的 `other` 是个大杂烩，
`probe_instruments.py` 的文档也把这条列为"未验证"）。要更细只能用 `ask_music_critic.py` 问，
且它的乐器名本身也不可靠（见 `docs/AUDIO-CRITIC.md` §11）。
"""
import argparse
import json
import os
import sys
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()                                    # noqa: E402

STEMS = ('drums', 'bass', 'other', 'vocals', 'guitar', 'piano')
NFFT = 8192
WIN = 0.18            # 每个音取起音后 180ms
DEFAULT_LEAD = ('other', 'guitar', 'vocals')


def load_mono(path):
    """读成单声道 float32（分轨 wav；soundfile 支持 wav/flac/ogg）。"""
    import soundfile as sf
    x, sr = sf.read(path, dtype='float32', always_2d=True)
    return x.mean(axis=1), sr


def band_amp(sig, sr, t, f0, nfft=NFFT, win=WIN):
    """`t` 起 `win` 秒内，`f0` 与它的 2、3 次谐波附近的**平均谱峰幅度**。"""
    i0 = int(max(0.0, t) * sr)
    i1 = min(len(sig), i0 + int(win * sr))
    if i1 - i0 < 1024:
        return 0.0
    seg = sig[i0:i1] * np.hanning(i1 - i0)
    sp = np.abs(np.fft.rfft(seg, n=nfft))
    fr = np.fft.rfftfreq(nfft, 1.0 / sr)
    out = []
    for h in (1, 2, 3):
        f = f0 * h
        if f > sr * 0.45:
            break
        m = (fr >= f * 0.97) & (fr <= f * 1.03)
        if m.any():
            out.append(float(sp[m].max()))
    return float(np.mean(out)) if out else 0.0


def pick_winner(energies):
    """6 条分轨的能量 → **能量最大**的那条；空输入或全 ≤0 返回 None。

    ⚠ 判据是**该音高上的能量谁强**，与"整段谁响"无关 —— 这正是它比 RMS 口径对的地方。
    （早期版本这里还带了个"只保留最大值 30% 以上"的候选门槛，实测**不影响返回值**
    —— 赢家本来就是最大者 —— 属于装饰性参数，2026-10-02 删掉。）
    """
    if not energies:
        return None
    mx = max(energies.values())
    if mx <= 0:
        return None
    return max(energies, key=energies.get)


def load_stems(stem_dir):
    out = {}
    sr = None
    for st in STEMS:
        p = os.path.join(stem_dir, st + '.wav')
        if os.path.isfile(p):
            out[st], sr = load_mono(p)
    return out, sr


def song_timeline(song_json):
    """→ (bar_sec, [(段名, 起秒, 止秒), …])"""
    d = json.load(open(song_json, encoding='utf-8'))
    bpm = float(d.get('bpm') or 120)
    meter = d.get('meter') or [4, 4]
    bar_sec = (4.0 * 60.0 / bpm) * (meter[0] / 4.0)
    secs, t = [], 0.0
    for s in d.get('sections') or []:
        secs.append((s.get('name') or '?', t, t + int(s.get('bars') or 0) * bar_sec))
        t += int(s.get('bars') or 0) * bar_sec
    return bar_sec, secs


def melody_points(song_json, track='Melody', end=None, proxy=False):
    """→ [(秒, MIDI 音高), …]。`proxy=True` 时取**每小节最高音**（旋律线代理）。"""
    d = json.load(open(song_json, encoding='utf-8'))
    meter = d.get('meter') or [4, 4]
    bar_sec, _secs = song_timeline(song_json)
    rows = (d.get('notes_extra') or {}).get(track)
    if rows is None:
        return []
    ev = [((x[0] + x[1] / float(meter[0])) * bar_sec, int(x[3])) for x in rows]
    if end:
        ev = [(t, p) for (t, p) in ev if t < float(end)]
    if not proxy or not ev:
        return ev
    per_bar = {}
    for t, p in ev:
        b = int(t // bar_sec)
        per_bar[b] = max(per_bar.get(b, 0), p)
    return [(b * bar_sec + 0.01, p) for b, p in sorted(per_bar.items())]


def attribute(points, sigs, sr, buckets, lead_stems=DEFAULT_LEAD):
    """→ (Counter 全量赢家, Counter {(段名, 赢家): 音数}, 占比 dict)"""
    win, seg = Counter(), Counter()
    for t, pit in points:
        f0 = 440.0 * 2 ** ((pit - 69) / 12.0)
        e = {st: band_amp(v, sr, t, f0) for st, v in sigs.items()}
        w = pick_winner(e)
        if w is None:
            continue
        win[w] += 1
        for nm, a, b in buckets:
            if a <= t < b:
                seg[(nm, w)] += 1
                break
    n = sum(win.values()) or 1
    share = {st: win[st] / float(n) for st in STEMS if win[st]}
    lead = sum(win[st] for st in lead_stems if st in win) / float(n)
    return win, seg, {'share': share, 'lead_share': lead, 'notes': n}


def main():
    ap = argparse.ArgumentParser(description='"这条旋律在参考曲里是谁在弹"（按音高判）')
    ap.add_argument('stem_dir', help='demucs 分轨目录（含 drums/bass/other/vocals/guitar/piano.wav）')
    ap.add_argument('song_json', help='song.json（旋律音符从它的 notes_extra 读）')
    ap.add_argument('--track', default='Melody', help='取哪条轨的音符当旋律线（默认 Melody）')
    ap.add_argument('--end', type=float, help='只看这个秒数之前')
    ap.add_argument('--melody-proxy', action='store_true',
                    help='用"每小节最高音"当旋律线代理（旋律混在伴奏轨里时用）')
    ap.add_argument('--by-sec', type=float, default=0.0, help='按 N 秒分桶（默认 30）')
    ap.add_argument('--by-section', action='store_true', help='按 song.json 的段落汇总')
    ap.add_argument('--lead-stems', default=','.join(DEFAULT_LEAD),
                    help='哪些分轨算"旋律乐器族"（默认 other,guitar,vocals）')
    ap.add_argument('--json', help='结果落盘')
    a = ap.parse_args()

    sigs, sr = load_stems(a.stem_dir)
    if not sigs:
        raise SystemExit('分轨目录里没有 wav：%s' % a.stem_dir)
    bar_sec, secs = song_timeline(a.song_json)
    pts = melody_points(a.song_json, a.track, a.end, a.melody_proxy)
    if not pts:
        raise SystemExit('轨 %s 在给定范围里没有音符（换个 --track 或加 --melody-proxy）' % a.track)
    if a.by_section:
        buckets = secs
        label = '段'
    else:
        step = a.by_sec or 30.0
        hi = max(t for t, _ in pts)
        buckets = [('%d-%d' % (i * step, (i + 1) * step), i * step, (i + 1) * step)
                   for i in range(int(hi // step) + 1)]
        label = '%gs 桶' % step
    lead = tuple(x for x in a.lead_stems.split(',') if x)
    win, seg, agg = attribute(pts, sigs, sr, buckets, lead)

    print('%s 轨 %d 个音（%.1f~%.1f 秒）· 一小节 %.3f 秒 · 按%s汇总'
          % (a.track, len(pts), min(t for t, _ in pts), max(t for t, _ in pts),
             bar_sec, label))
    print('    %-12s' % label + ''.join('%8s' % st for st in STEMS))
    for nm, _a, _b in buckets:
        tot = sum(seg[(nm, st)] for st in STEMS)
        if not tot:
            continue
        print('    %-12s' % nm + ''.join('%7.0f%%' % (100.0 * seg[(nm, st)] / tot)
                                        for st in STEMS))
    print('  **全段合计**：' + ' · '.join('%s %.0f%%' % (st, 100.0 * win[st] / max(1, sum(win.values())))
                                        for st in STEMS if win[st]))
    print('  旋律乐器族（%s）占比：**%.0f%%**' % ('/'.join(lead), 100.0 * agg['lead_share']))
    if a.json:
        json.dump({'track': a.track, 'notes': agg['notes'], 'share': agg['share'],
                   'lead_share': agg['lead_share'],
                   'buckets': {'%s|%s' % (nm, st): seg[(nm, st)]
                               for nm, _x, _y in buckets for st in STEMS if seg[(nm, st)]}},
                  open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('[i] 写了 %s' % a.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
