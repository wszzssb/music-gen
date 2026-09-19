#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**全维度体检**：一次把"要注意的元素"全量列出来（对参考曲 vs 我的成品）。

为什么要有这个工具（用户 2026-09-16 的要求）：
  "能不能让最开始就发现所有要注意的元素" —— 之前是**踩一个补一个**：
  先是"乱"（密度/鼓/泛音），再是"旋律轨静音"（小节号口径），
  再是"MIDI 不像"（力度全 84）……每一条都是**用户先听出来**才发现。
  根因不是我漏看了某个指标，而是**没有一份"该看哪些维度"的清单**：
  每次只盯着手上那一个轴，其余维度处在"没人查"的状态。

这个工具把清单做成**可执行**的：逐维度量出"原曲 vs 我"的数，给出
**达标 / 偏差 / 测不了**三态，并明确标出**哪些维度工具测不到、必须靠人耳**。
（"测不了"必须显式写出来 —— 否则会误以为"报告全绿 = 听着像"，
 而 `similarity.py` 六个轴全是时间平均统计量、看不见力度/演奏法，这是踩过的。）

用法：
  python scripts\\audit.py <参考音频或目录> <我的音频> [--bpm N]
                           [--mine-json <song.json>] [--ref-chords <txt>]
                           [--midi <我的.mid>] [--ref-midi <参考.mid>]
                           [--json]
"""
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()           # noqa: E402  控制台编码兜底
import metrics                                 # noqa: E402

STATE_OK, STATE_OFF, STATE_NA = 'ok', 'off', 'na'


def _find_audio(p):
    """给目录就找里面的音频（取第一个）"""
    if os.path.isfile(p):
        return p
    for ext in ('.ogg', '.wav', '.flac', '.mp3', '.m4a'):
        for f in sorted(os.listdir(p)):
            if f.lower().endswith(ext):
                return os.path.join(p, f)
    return None


def _midi_info(path):
    """MIDI 的力度/时值/音符数画像（`notes_extra` 那条链的验收口径）"""
    import mido
    mid = mido.MidiFile(path)
    tp = mid.ticks_per_beat
    vel, dur, per_track = [], [], {}
    for tr in mid.tracks:
        nm, t, pend = None, 0, {}
        for msg in tr:
            t += msg.time
            if msg.type == 'track_name':
                nm = msg.name
            elif msg.type == 'note_on' and msg.velocity > 0:
                pend[msg.note] = (t, msg.velocity)
            elif msg.type in ('note_off',) or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in pend:
                    t0, v = pend.pop(msg.note)
                    vel.append(v)
                    dur.append((t - t0) / tp)
                    per_track.setdefault(nm or '?', []).append(v)
    return {'n': len(vel), 'vel': vel, 'dur': dur, 'per_track': per_track}


def _vel_line(vel, tag):
    if not vel:
        return '%s：无音符' % tag
    u = len(set(vel))
    q = np.percentile(vel, [5, 50, 95])
    return ('%s：%d 音 · 力度 %d 种取值 · 分位 5/50/95 = %d/%d/%d'
            % (tag, len(vel), u, q[0], q[1], q[2]))


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('ref', help='参考曲（音频文件或目录）')
    ap.add_argument('mine')
    ap.add_argument('--bpm', type=float, default=None)
    ap.add_argument('--mine-json', default=None)
    ap.add_argument('--ref-chords', default=None)
    ap.add_argument('--midi', default=None, help='我的 MIDI（查力度/时值维度）')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    # 面板守卫（硬形式）：没在跑就先拉起来 —— 见 scripts/studio_guard.py 顶部那段。
    try:
        import studio_guard
        studio_guard.ensure_panel()
    except Exception as _e:                                        # noqa: BLE001
        print('  （面板守卫跳过：%s）' % str(_e)[:80])

    ref = _find_audio(a.ref)
    mine = _find_audio(a.mine)
    if not ref or not mine:
        raise SystemExit('找不到音频：ref=%s mine=%s' % (a.ref, a.mine))
    bpm = a.bpm or 150.0
    rows = []

    # ---------- ① 结构层（工具能测）----------
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, 'similarity.py'),
                            ref, mine, '--bpm', str(bpm), '--json',
                            '--mine-json', a.mine_json or '',
                            '--ref-chords', a.ref_chords or ''],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace')
        sc = json.loads((r.stdout or '').strip().splitlines()[-1])
    except Exception as e:
        sc = {}
        rows.append(('结构层', 'similarity.py', STATE_NA, '跑不动：%s' % str(e)[:60]))
    for k, th in (('chord', 90), ('grid', 60), ('density', 65),
                  ('octave', 45), ('timbre', 65), ('variation', 55)):
        if k in sc:
            v = sc[k]
            rows.append(('结构层', '%s（参考线 %d）' % (k, th),
                         STATE_OK if v >= th else STATE_OFF, '%.1f' % v))
    if sc.get('total') is not None:
        rows.append(('结构层', '总分', STATE_OK, '%.1f / 100' % sc['total']))

    # ---------- ② 频谱/动态（工具能测）----------
    pr, pm = metrics.profile(ref), metrics.profile(mine)
    for tag, key, tol in (('质心', 'centroid', 0.25), ('宽度', 'width', 0.25),
                          ('RMS', 'rms_db', 3.0)):
        va, vb = pr[key], pm[key]
        # ⚠ RMS 是**绝对 dB**，"相对百分比"没意义：−17.8 vs −16.1 被算成"差 170%"
        #   （踩过）。dB 类量用**绝对差**，只有质心/宽度这种有量纲正数才用百分比。
        if key == 'rms_db':
            d_abs, ok = abs(vb - va), abs(vb - va) <= tol
            rows.append(('频谱', '%s（%s vs %s）' % (tag, round(va, 1), round(vb, 1)),
                         STATE_OK if ok else STATE_OFF, '差 %.1f dB' % d_abs))
            continue
        d = abs(vb - va) / max(1e-9, abs(va))
        rows.append(('频谱', '%s（%s vs %s）' % (tag, round(va, 1), round(vb, 1)),
                     STATE_OK if d <= tol else STATE_OFF, '差 %.0f%%' % (d * 100)))
    ks = list(pr['bands'].keys())
    dd = [abs(pm['bands'][k] - pr['bands'][k]) for k in ks]
    rows.append(('频谱', '倍频程平均带差（≤3dB 满分）', STATE_OK if np.mean(dd) <= 3 else STATE_OFF,
                 '%.2f dB' % np.mean(dd)))
    worst = int(np.argmax(dd))
    rows.append(('频谱', '最差频带 %s' % ks[worst],
                 STATE_OK if dd[worst] <= 3 else STATE_OFF, '%.1f dB' % dd[worst]))

    # ---------- ③ 演奏层（工具能测，但常被忽略）----------
    if a.midi and os.path.exists(a.midi):
        mi = _midi_info(a.midi)
        u = len(set(mi['vel'])) if mi['vel'] else 0
        rows.append(('演奏', 'MIDI 力度取值种数（**平 = 不像**）',
                     STATE_OK if u >= 8 else STATE_OFF, '%d 种' % u))
        for tr, vs in sorted(mi['per_track'].items()):
            if len(vs) >= 20:
                uu = len(set(vs))
                rows.append(('演奏', '  %s 力度种数' % tr,
                             STATE_OK if uu >= 5 else STATE_OFF, '%d 种' % uu))
        if mi['dur']:
            med = float(np.median(mi['dur']))
            frag = float(np.mean([1.0 if d < 0.12 else 0.0 for d in mi['dur']]))
            rows.append(('演奏', '音符时值中位', STATE_OK, '%.2f 拍' % med))
            rows.append(('演奏', '碎音比例（<0.12 拍）',
                         STATE_OK if frag <= 0.15 else STATE_OFF, '%.0f%%' % (frag * 100)))
    else:
        rows.append(('演奏', 'MIDI 力度/时值', STATE_NA, '没给 --midi（**这项测不了**）'))

    # ---------- ④ 工具测不到的（必须人耳）----------
    for x in ('起音时刻是否有摇摆/提前量（我只量化到 0.25 拍）',
              '和弦音该连的有没有连（转录可能把一个和弦拆成几个音）',
              '钢琴延音踏板（真实钢琴的延音全靠它，MIDI 里没有）',
              '演奏法/音色切换、滑音、颤音',
              '音色本身（GM 音源 vs 商业采样库）',
              '旋律好不好听 / 段间过渡顺不顺'):
        rows.append(('人耳', x, STATE_NA, '**工具测不到**'))

    if a.json:
        print(json.dumps([{'层': g, '项': n, '态': s, '值': v} for g, n, s, v in rows],
                         ensure_ascii=False, indent=1))
        return
    icon = {STATE_OK: ' ✓ ', STATE_OFF: ' ! ', STATE_NA: ' ? '}
    cur = None
    for g, n, s, v in rows:
        if g != cur:
            print('\n== %s ==' % g)
            cur = g
        print('%s%-46s %s' % (icon[s], n, v))
    off = [n for g, n, s, v in rows if s == STATE_OFF]
    print('\n--- 结论 ---')
    print('偏差项 %d 个%s' % (len(off), ('：' + '；'.join(off[:6])) if off else ''))
    print('⚠ "?" 的项**工具测不到**，必须人耳确认 —— 全绿 ≠ 听着像')


if __name__ == '__main__':
    main()
