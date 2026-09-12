#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""melody_gen.py —— **按参考曲的"旋律语言"生成旋律**，写进 song.json。

为什么需要它：手写旋律总带着"我自己的习惯"（这次实测：级进只占 14%、正拍 73%、
每小节 4.6 个音），而例曲的语言是**级进为主（59%）、切分明显（正拍仅 34%）、
句子碎、时值长短相间**。用画像驱动生成，才能系统性地贴近，而不是靠手感。

算法（确定性，同种子同结果）：
  1. 时值/落点：从画像的 `dur16_hist` / `onbeat_pct` 抽样（16 分格为最小单位）
  2. 音高：随机游走 —— 以 `stepwise_pct` 的概率走 ±1~2 半音（级进），否则跳进 3~5 半音
  3. **强拍（第 1、3 拍）落点强制吸附到该小节和弦音**（我们自己的纪律，不能破）
  4. 音域限制在画像的 `range` 内；越界就折返
  5. 生成后自检：和弦贴合率、级进率、正拍率、每小节音符数 —— 与画像对比打印

用法:
  python scripts\\melody_gen.py songs\\14_d75_pulse\\song.json refs\\melody\\BGM33_melody.json [--seed 7]
"""

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）

import json_io      # noqa: E402
import song_engine  # noqa: E402

SCALE_MINOR = [0, 2, 3, 5, 7, 8, 10]          # 自然小调（画像主调 G 小调）
SCALE_DORIAN = [0, 2, 3, 5, 7, 9, 10]         # 多利亚（B 段色彩）


def gen_section(sec, chords, prof, rng, mode_scale, tonic):
    """给一个段落生成旋律：返回 [(bar, beat, dur, pitch)]

    `tonic` 是**主音的音级**（0-11），由调用方按该曲的和弦推断 ——
    以前这里硬编码 `tonic = 7`（G）。对 G 小调的曲子碰巧对，换成 D 小调就会
    把多利亚判定整错（D 多利亚该含 B，自然小调该含 Bb）。"""
    lo, hi = prof['range']
    lo, hi = min(lo + 2, 74), max(hi - 1, 80)          # 别贴着画像的极值
    dur_pool = []
    for k, v in prof['dur16_hist'].items():
        dur_pool += [int(k) / 4.0] * v                 # 16 分 → 拍
    if not dur_pool:
        dur_pool = [0.5, 1.0, 2.0]
    onbeat = prof.get('onbeat_pct', 50) / 100.0
    stepwise = prof.get('stepwise_pct', 50) / 100.0
    out = []
    prev = None
    for b in range(sec['bars']):
        cname = sec['chords'][b]
        root, tones = chords[cname]
        tset = sorted({t % 12 for t in tones})
        beat = 0.0
        while beat < 3.99:
            # 落点：按画像的正拍比例决定落在拍上还是反拍
            if rng.random() < onbeat:
                on = min(round(beat * 2) / 2, 3.5)
            else:
                on = min(beat + rng.choice([0.25, 0.5, 0.75]), 3.5)
            dur = rng.choice(dur_pool)
            dur = min(dur, 4.0 - on)
            # 音高：级进为主；强拍吸附到和弦音
            strong = abs(on - round(on)) < 1e-6 and int(round(on)) % 2 == 0
            if prev is None:
                p = rng.choice([t for t in (root % 12 + 60 + d for d in SCALE_MINOR)
                                if lo <= t <= hi] or [76])
            elif strong:
                cand = [t for t in range(prev - 3, prev + 4)      # 窗口 ±3：别为了凑弦内音跳四五度
                        if t % 12 in tset and lo <= t <= hi]
                p = rng.choice(cand) if cand else prev
            else:
                if rng.random() < max(0.62, stepwise):      # 级进下限 62%（实测人手写是 42~59%）
                    step = rng.choice([-2, -1, 1, 2])
                else:
                    step = rng.choice([-4, -3, 3, 4])      # 去掉 ±5（四五度跳进会显得"东跳西跳"）
                p = prev + step
                if p < lo or p > hi:                       # 越界折返
                    p = prev - step
                if mode_scale is not None and p % 12 not in [
                         (tonic + d) % 12 for d in mode_scale]:
                    p += 1 if rng.random() < 0.5 else -1
                p = max(lo, min(hi, p))
            # **弱拍也要避免半音冲突**（实测教训）：原先只保证强拍落和弦音，
            # 弱拍随机游走会经常落到"与某弦内音差 1 个半音"的音上（♭9/♮7，最刺耳的
            # 和声关系）。实测 20 号有 **42% 的音**是这样的 → 用户听感"不协调、难受"。
            def _clash(t):
                return any(min((t - x) % 12, (x - t) % 12) == 1 for x in tset)
            p = int(p)
            if _clash(p):
                fixed = None
                for cand in (p - 1, p + 1, p - 2, p + 2):      # 先原地微调（保轮廓）
                    if lo <= cand <= hi and not _clash(cand) and (
                            mode_scale is None or cand % 12 in
                            [(tonic + dd) % 12 for dd in mode_scale]):
                        fixed = cand
                        break
                if fixed is None:                              # 再退到最近的弦内音
                    cs = [t for t in range(lo, hi + 1) if t % 12 in tset]
                    if cs:
                        fixed = min(cs, key=lambda t: abs(t - p))
                if fixed is not None:
                    p = fixed
            out.append([b, round(on, 2), round(dur, 2), int(p)])
            prev = int(p)
            beat = on + dur
    # **网格化**（实测教训：画像的时值直方图含三连音值 0.75/1.25/1.75 拍，
    # 抽样后会产生"卡卡的"旋律；且相邻音之间常出现 >0.5 拍的空白间隙 → 听着断续）。
    # 实测 20/21 号：时值 7~9 种、40+ 处大间隙；手写的 17 号只有 3 种时值、0 间隙。
    GRID = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0)
    def snap(v):
        return min(GRID, key=lambda g: (abs(g - v), g > v))
    # **落点吸附到八分网格**（实测教训：生成器把落点撒在 16 个十六分格上，
    # 而伴奏/打击在"拍/八分"网格 → 听感"跟伴奏错位、节奏跳来跳去"。
    # 对照：手写的 17 号**只在格 0/4/8/12（拍与八分）**上。）
    for e in out:
        tot = round((e[0] * 4 + e[1]) * 2) / 2
        e[0], e[1] = int(tot // 4), round(tot % 4, 4)
    dedup = {}
    for e in out:
        dedup[(e[0], round(e[1], 4))] = e
    out = sorted(dedup.values(), key=lambda e: (e[0], e[1]))
    # **强拍兜底**（网格化会把音从强拍挪走 → 不再落弦内音）：把落在第 1、3 拍上的音
    # 重新吸附到该小节和弦音（±3 内），保证"强拍落弦内音"这条纪律不被网格化破坏。
    for e in out:
        if e[1] not in (0.0, 2.0):
            continue
        cname = (sec.get('chords') or [])[min(e[0], len(sec.get('chords') or []) - 1)] \
            if sec.get('chords') else None
        entry = chords.get(cname) if cname else None
        tones = []
        for t in (entry[1] if entry else []):
            while t < lo:
                t += 12
            while t > hi:
                t -= 12
            if lo <= t <= hi:
                tones.append(t)
        if not tones:
            continue
        tset = [t % 12 for t in tones]
        if e[3] % 12 in tset:
            continue
        cands = [t for t in tones if tset and lo <= t <= hi]
        if cands:
            e[3] = min(cands, key=lambda t: (abs(t - e[3]), t < e[3]))
    for e in out:
        e[2] = snap(min(e[2], 4.0))
    for i, e in enumerate(out):
        start = e[0] * 4 + e[1]
        nxt = (out[i + 1][0] * 4 + out[i + 1][1]) if i + 1 < len(out) else 16 * 4
        want = nxt - start
        if want < 0.5:
            want = 0.5
        e[2] = snap(min(want, 4.0))
    return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    song, prof_path = sys.argv[1], sys.argv[2]
    seed = int(sys.argv[sys.argv.index('--seed') + 1]) if '--seed' in sys.argv else 7
    prof = json.load(open(prof_path, encoding='utf-8'))
    d = json.load(open(song, encoding='utf-8'))
    # **拍号守卫**：这个生成器的落点/网格/拱形全是按"一小节 4 拍、16 个十六分格"写的 ——
    # 非 4/4 时它会静默把音撒到小节外。宁可拒绝，也不给错旋律（曲线救国留给以后再说）。
    meter = song_engine._norm_meter(d.get('meter'))
    if meter != [4, 4]:
        print('melody_gen 目前只支持 4/4（本曲 meter=%s）。'
              '引擎侧已支持非 4/4 编配，但**旋律生成器还没适配** —— '
              '请手写 melody，或先把 meter 改成 [4,4]。' % meter)
        return 1
    chords = d['chords']
    rng = random.Random(seed)
    names = list(d['melody'].keys())
    # 主音：命令行 --tonic 优先；否则取**第一段第一小节和弦的根音**（通常是主和弦）。
    # 别硬编码调性：画像里 tonic 可能只是扒谱时的假设（BGM33 曾被当成 G 小调，实际是 D 小调）。
    if '--tonic' in sys.argv:
        tonic = int(sys.argv[sys.argv.index('--tonic') + 1]) % 12
    else:
        try:
            tonic = chords[d['sections'][0]['chords'][0]][0] % 12
        except Exception:
            tonic = 0
    for i, sec in enumerate(d['sections']):
        key = sec['melody']
        # 多利亚必须**显式声明**（`"mode": "dorian"`）：以前按段落名首字母判，
        # 于是 "Bridge" 也被当成 B 段 → 旋律里混进不属于本调的升六度。
        scale = SCALE_DORIAN if sec.get('mode') == 'dorian' else SCALE_MINOR
        d['melody'][key] = gen_section(sec, chords, prof, rng, scale, tonic)
        sec.pop('melody_extra', None)              # 生成旋律已含细节，清掉旧加花
    json_io.save(song, d)
    # 自检 + 与画像对比
    tot = fit = 0
    ons, iv, npb = [], [], []
    for sec in d['sections']:
        mel = d['melody'].get(sec['melody'], [])
        npb.append(len(mel) / max(1, sec['bars']))
        prev = None
        for (b, beat, _dur, m) in mel:
            ons.append(int(round(beat * 4)) % 16)
            if prev is not None:
                iv.append(m - prev)
            prev = m
            if beat in (0.0, 2.0) and b < len(sec['chords']):
                tones = [t % 12 for t in chords[sec['chords'][b]][1]]
                tot += 1
                fit += 1 if m % 12 in tones else 0
    step = 100.0 * sum(1 for x in iv if abs(x) <= 2) / max(1, len(iv))
    onb = 100.0 * sum(1 for k in ons if k % 4 == 0) / max(1, len(ons))
    print('生成完毕（seed=%d）：%d 个段落旋律段' % (seed, len(names)))
    print('%-14s %8s %8s' % ('指标', '画像', '生成'))
    print('%-14s %8.2f %8.2f' % ('每小节音符', prof['notes_per_bar'],
                                 sum(npb) / max(1, len(npb))))
    print('%-14s %8.0f %8.0f' % ('级进占比%', prof['stepwise_pct'], step))
    print('%-14s %8.0f %8.0f' % ('正拍占比%', prof['onbeat_pct'], onb))
    print('%-14s %8s %8s' % ('音域', '%d-%d' % tuple(prof['range']),
                             '%d-%d' % (min(m for v in d['melody'].values() for *_x, m in v),
                                        max(m for v in d['melody'].values() for *_x, m in v))))
    print('强拍和弦贴合：%d/%d = %.0f%%' % (fit, tot, 100.0 * fit / max(1, tot)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
