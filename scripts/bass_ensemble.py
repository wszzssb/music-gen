# -*- coding: utf-8 -*-
"""低音专项：把整曲里某条轨换成"分轨多来源交叉验证"的版本（+ 可选低八度 sub 层）。

为什么需要单独立一个工具（BGM29 实测，见 docs/CASE-BGM35-FINDINGS.md 第 37 条）：
  · YourMT3 在**全混音**上只给出 279 个低音（均音高 42.7 ≈ 93Hz）；
  · 同一个模型在 **demucs 的 bass 分轨**上给出 818 个（均 39.0）；
    Basic Pitch 在两条 bass 分轨上给出 1307 / 1384 个（均 ~37 ≈ 74Hz）。
  → 低音是"面"不是"点"，全混音里 279 个音根本铺不满：成品 20-80Hz 比参考低 6~8 dB，
    而 160-315Hz 反而高 4.5 dB。换成集成后的低音轨（1416 音）+ 低八度 sub 层，
    统一口径平均相对带差从 **2.16 → 0.95 dB**。

规则（沿用 ensemble_transcribe 的实测教训）：
  · 同 0.1s 格 + 同音高合并，按跨来源支持率软评分，score ≥ thr 才留；
  · **不做碎片合并**（那些是真实重复起音，合并会让 F1 从 0.481 掉到 0.283）；
  · 时值取各来源**最大值**（低音要连奏，短促会丢掉低频能量），
    截断到"同音高下一音之前"与 --max-dur，避免同音高重叠（重叠会被音源吞音）。

用法：
    python scripts/bass_ensemble.py --base 全曲.mid --out 输出.mid \
        --source "ymt3b=bass分轨转录.mid|24|60" \
        --source "bp4=bp_bass4.mid|24|60" --source "bp6=bp_bass6.mid|24|60" \
        --layer Bass [--program 38] [--sub]

⚠ 字段分隔符用 `|`（Windows 路径自带 `D:`，用 `:` 会把盘符切断 —— 见 PITFALLS 170）。
"""
import argparse
import os
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import midi_file  # noqa: E402

GRID = 0.1


def load_notes(path, lo, hi):
    """→ [(秒, 音高, 时值秒, 力度)]，跳过第 10 通道（鼓）"""
    m = midi_file.import_midi(path)
    spb = 60.0 / float(m.get('bpm') or 120.0)
    out = []
    for tr in m.get('tracks', []):
        if tr.get('drum') or tr.get('channel') == 9:
            continue
        for (st, du, p, v) in tr.get('notes', []):
            if lo <= p <= hi:
                out.append((float(st) * spb, int(p), float(du) * spb, int(v)))
    out.sort()
    return out


def parse_source(spec):
    name, rest = spec.split('=', 1)
    parts = rest.split('|')
    lo = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    hi = int(parts[2]) if len(parts) > 2 and parts[2] else 127
    return name, parts[0], lo, hi


def main():
    ap = argparse.ArgumentParser(description='低音专项：分轨多来源交叉验证 + sub 层')
    ap.add_argument('--base', required=True, help='基准整曲 MIDI（保留其所有轨）')
    ap.add_argument('--out', required=True)
    ap.add_argument('--source', action='append', required=True,
                    help='名字=文件|音域低|音域高，可重复')
    ap.add_argument('--layer', default='Bass', help='被替换的轨名（默认 Bass）')
    ap.add_argument('--thr', type=float, default=0.90, help='跨来源支持率阈值')
    ap.add_argument('--max-dur', type=float, default=1.6, help='单音最长时值（秒）')
    ap.add_argument('--min-beats', type=float, default=0.0,
                    help='时值下限（**拍**）：<= 它的音拉到该值。0=不拉。'
                         '实测 v5 认可版时值中位 0.47 拍，而集成产物只有 0.14 → 听感「不流畅」')
    ap.add_argument('--program', type=int, default=None, help='替换后该轨的 GM 音色号')
    ap.add_argument('--sub', action='store_true', help='额外生成低八度 sub 层（补 20-40Hz）')
    ap.add_argument('--octave-ref', default=None,
                    help='参考**低音分轨音频**：用它跑 pyin 提基频，修正"差一个八度"的转录错误。'
                         '实测 BGM29 全曲 bass 只有 48.5%% 的帧音高一致、**25.3%% 差整八度**，'
                         '而这类系统性错误多模型集成修不掉（三个模型犯同一个错）。')
    ap.add_argument('--merge', action='store_true',
                    help='把 base 目标轨的音也当成一个来源（**合并**而非替换）。'
                         '钢琴/吉他/弦乐要用它；低音那条历史行为是替换，别开')
    ap.add_argument('--min-prob', type=float, default=0.20,
                    help='pyin 置信度下限。实测扫描（BGM29 bass 全曲一致率）：'
                         '0.35→64.9%% · **0.20→69.9%%** · 0.12→70.1%%（拐点在 0.20，再放宽没收益）')
    a = ap.parse_args()
    # 面板守卫（硬形式）：没在跑就先拉起来 —— 见 scripts/studio_guard.py 顶部那段。
    try:
        import studio_guard
        studio_guard.ensure_panel()
    except Exception as _e:                                        # noqa: BLE001
        print('  （面板守卫跳过：%s）' % str(_e)[:80])

    srcs = {}
    for spec in a.source:
        name, path, lo, hi = parse_source(spec)
        if not os.path.isfile(path):
            print('  跳过（文件不存在）：%s' % path)
            continue
        srcs[name] = load_notes(path, lo, hi)
        print('  来源 %-10s %5d 音（音域 %d-%d）' % (name, len(srcs[name]), lo, hi))
    # **把 base 里目标轨的音也当成一个来源**（opt-in `--merge`，2026-09-19 加）
    #
    # 为什么需要：本工具的语义一直是**替换**（用集成结果覆盖该轨），于是
    # 「原轨有、来源没有」的音会被**丢掉** —— 对低音那条历史链是对的（v5 也是替换 + sub），
    # 但对钢琴/吉他/弦乐就成了净损失：BGM35 实测 Piano 替换后 2396 音 ≈ 原样 2377，
    # 而用户认可版 `v5_source.mid` 的 Piano 是 **4143**（多来源**合并**的结果）。
    # 开 `--merge` 后，base 该轨的音与各来源一起进"跨来源支持率"评分：
    # 被多来源支持的留下、只被单方支持的按同一把尺子裁掉。
    if getattr(a, 'merge', False):
        _b = midi_file.import_midi(a.base)
        _spb = 60.0 / float(_b.get('bpm') or 120.0)
        _bn = []
        for _tr in _b.get('tracks', []):
            if _tr.get('name') == a.layer:
                for (st, du, p, v) in (_tr.get('notes') or []):
                    _bn.append((float(st) * _spb, int(p), float(du) * _spb, int(v)))
        if _bn:
            srcs['__base__'] = sorted(_bn)
            print('  并入 base 的 %s 轨：%d 音（--merge）' % (a.layer, len(_bn)))

    if not srcs:
        raise SystemExit('没有可用来源')

    # 跨来源支持率（无参照也能算）：某来源的音在 ±1 格内被别的来源支持的比例
    sets = {}
    for name, ns in srcs.items():
        s = set()
        for (t, p, _d, _v) in ns:
            g = int(round(t / GRID))
            for dg in (-1, 0, 1):
                s.add((g + dg, p))
        sets[name] = s
    w = {}
    for name in srcs:
        others = set()
        for n2 in srcs:
            if n2 != name:
                others |= sets[n2]
        w[name] = len(sets[name] & others) / max(1, len(sets[name]))
    print('  跨来源支持率：%s'
          % {k: round(v, 3) for k, v in sorted(w.items(), key=lambda z: -z[1])})

    merged = {}
    for name, ns in srcs.items():
        for (t, p, d, v) in ns:
            k = (int(round(t / GRID)), p)
            rec = merged.get(k)
            if rec is None:
                merged[k] = [(t, p, d, v), {name}]
            else:
                rec[1].add(name)
                best = rec[0]
                rec[0] = (best[0], best[1], max(d, best[2]), max(v, best[3]))
    print('  合并后 %d 个 (格,音高)' % len(merged))

    by_pitch = defaultdict(list)
    kept = 0
    # **归一化支持分**（2026-09-19 修 · 通用缺陷）：
    #   原判据 `sum(w[支持来源]) < thr → 砍` 隐含假设"各来源的跨来源支持率都接近 1"。
    #   BGM16 实测三个来源的 w 只有 **0.437 / 0.404 / 0.067** → 即使**三源全共识**
    #   也只有 0.908（勉强过 0.90），**两源共识 0.841 就被砍** ——
    #   合并后 1427 个候选只剩 **2 音**，整条 Bass 轨等于废掉（体检一致率 0.6%）。
    #   改成按"占全部来源权重之和的比例"归一：三源全共识 = 1.0、两源共识 ≈ 0.93、
    #   单源独有 ≈ 0.48 —— **语义仍是"要共识"**，但对材料质量自适应（BGM29 那种
    #   各来源一致的曲子上，归一化前后取值几乎相同，行为不变）。
    W_TOTAL = sum(w.values())
    dropped = 0
    for (note, s) in merged.values():
        if sum(w.get(n, 0.0) for n in s) / max(1e-9, W_TOTAL) < a.thr:
            dropped += 1
            continue
        t, p, d, v = note
        by_pitch[p].append([t, min(d, a.max_dur), v])
        kept += 1
    print('  阈值 %.2f（归一化，权重总和 %.3f）→ 保留 %d 音 · 砍 %d'
          % (a.thr, W_TOTAL, kept, dropped))

    # 截断下限（秒）：`--min-beats` 给了就按它，否则退回原来的 0.01 秒
    _FLOOR = (a.min_beats * (60.0 / float(midi_file.import_midi(a.base).get('bpm') or 120.0))
              if getattr(a, 'min_beats', 0) > 0 else 0.01)

    def dedup(byp):
        """同音高截断，避免重叠（重叠会被音源吞音：note-off 只带音高不带 id）"""
        for p, lst in byp.items():
            lst.sort()
            out = []
            for j, cur in enumerate(lst):
                nxt = lst[j + 1][0] if j + 1 < len(lst) else None
                if nxt is not None:
                    gap = nxt - cur[0]
                    # ⚠ 间隔 < 20ms 的同音高重复：**直接丢掉前一个**。
                    #   原来是 `d = max(0.05, gap - 0.01)` —— gap < 60ms 时下限 0.05 反而
                    #   **大于** gap，于是照样重叠（实测全曲 Bass 37 处、Sub 27 处）。
                    if gap < 0.02:
                        continue
                    if cur[1] > gap - 0.005:
                        # ⚠ 下限不能写死 0.01：同音高密集重复时 gap 极小，
                        #   长音会被截成 0.025 拍 —— 实测这抵消了 `--min-beats`
                        #   （打印"拉长 978 音"但产物时值中位一动不动）。
                        #   用 `_FLOOR`（由 --min-beats 定），宁可让相邻同音高重叠。
                        cur[1] = max(_FLOOR, gap - 0.005)
                out.append(cur)
            lst[:] = out

    dedup(by_pitch)

    # **时值下限**（opt-in `--min-beats`，2026-09-19）：<=
    #   实测（用户："16/23/29 太杂乱、不流畅"）：集成产物时值中位 0.14~0.18 拍、
    #   碎音 57~61%，而认可版 v5_source 是 **0.47 拍 / 37.9%** → 音符太碎。
    #   BP 的转录天然给短时值，`max()` 也拉不回来，只能显式给下限。
    #   按**拍**给（各曲 BPM 差一倍多：71 vs 150）。拉长后可能撞出新重叠 → 再 dedup。
    if a.min_beats > 0:
        # ⚠ 这里在 `base = import_midi(...)` **之前**，不能引用 base —— 直接读一次
        _spb2 = 60.0 / float(midi_file.import_midi(a.base).get('bpm') or 120.0)
        _min_s = a.min_beats * _spb2
        _n = 0
        for _p, _lst in by_pitch.items():
            for _it in _lst:
                if _it[1] < _min_s:
                    _it[1] = _min_s
                    _n += 1
        print('  时值下限 %.2f 拍 → 拉长 %d 音' % (a.min_beats, _n))
        dedup(by_pitch)

    # ── 八度校正（opt-in，--octave-ref 给了才做）────────────────────────────
    # 依据：pyin 在参考低音分轨上提的基频是**独立方法**，三方（YMT3/BP4/BP6）都错才会同时错；
    # 而实测"差一个八度"占 25.3% —— 这是集成的盲区（三个模型犯同一个错）。
    # 只改**正好 ±12/±24** 的音，别的音程一律不动（可能是和弦内音，或 pyin 自己错）。
    if a.octave_ref:
        import soundfile as sf
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.venv-ml', 'Lib', 'site-packages'))
        import librosa
        y, sr0 = sf.read(a.octave_ref, dtype='float32', always_2d=True)
        y = y.mean(axis=1)
        if sr0 != 22050:
            y = librosa.resample(y, orig_sr=sr0, target_sr=22050)
        f0, _vf, vprob = librosa.pyin(y, fmin=40.0, fmax=300.0, sr=22050,
                                      frame_length=2048, hop_length=512)
        buckets = defaultdict(list)
        for i, v in enumerate(f0):
            if np.isfinite(v) and vprob[i] >= a.min_prob:
                buckets[int(i * 512 / 22050 / GRID)].append(
                    int(round(69 + 12 * np.log2(v / 440.0))))
        # 桶里只要有 1 帧可靠就给参考（原来要求 ≥2 帧，实测把 717/1416 个音判成"无参考"，
        # 覆盖率不够 → 只修掉一半八度错误）。放宽后仍以**一致率**为判据复核，见 tr_check。
        ref = {k: int(np.median(v)) for k, v in buckets.items() if len(v) >= 1}
        stat = defaultdict(int)
        flat = []
        for p, lst in by_pitch.items():
            for (t, d, v) in lst:
                g0, g1 = int(t / GRID), int((t + max(d, 0.05)) / GRID)
                cand = [ref[g] for g in range(g0, g1 + 1) if g in ref]
                if not cand:
                    stat['无参考'] += 1
                    flat.append([t, d, p, v]); continue
                rp = int(np.median(cand))
                dd = p - rp
                if dd in (12, -12, 24, -24) and 12 <= p - dd <= 108:
                    flat.append([t, d, p - dd, v])
                    stat['修正八度'] += 1
                else:
                    flat.append([t, d, p, v])
                    stat['本来就对' if dd == 0 else '非八度差异'] += 1
        by_pitch = defaultdict(list)
        for (t, d, p, v) in flat:
            by_pitch[p].append([t, d, v])
        dedup(by_pitch)          # 移调后可能撞出新的同音高重叠，再截一次
        print('  八度校正：%s' % dict(stat))
        print('  可靠参考格 %d 个（pyin 置信度 ≥ %.2f）' % (len(ref), a.min_prob))

    base = midi_file.import_midi(a.base)
    spb = 60.0 / float(base.get('bpm') or 120.0)
    hit = False
    for tr in base['tracks']:
        if tr.get('name') == a.layer:
            tr['notes'] = sorted([round(t / spb, 6), round(d / spb, 6), int(p), int(v)]
                                 for (p, lst) in by_pitch.items() for (t, d, v) in lst)
            if a.program is not None:
                # ⚠ 只改 `program` 不够：`midi_file.export_midi` 在**有 program_changes 时
                # 优先用它、忽略 program**（实测：只改 program，渲染结果与原来逐位相同
                # —— SHA256 一模一样才发现）。两条都写。
                tr['program'] = int(a.program)
                tr['program_changes'] = [[0.0, int(a.program)]]
            hit = True
            print('  替换轨 %s → %d 音%s' % (a.layer, len(tr['notes']),
                                             '' if a.program is None else '（音色→%d）' % a.program))
    if not hit:
        raise SystemExit('基准 MIDI 里没有名为 %s 的轨：%s'
                         % (a.layer, [t.get('name') for t in base['tracks']]))

    if a.sub:
        subs = []
        for (p, lst) in by_pitch.items():
            # 低八度落在 24~45（C1~A2 = 32.7~110Hz）才要：
            # 再低掉到 20Hz 以下（听不见、只吃动态余量），再高就与主层打架。
            if not (24 <= p - 12 <= 45):
                continue
            for (t, d, v) in lst:
                subs.append([round(t / spb, 6), round(d / spb, 6), p - 12, max(30, int(v * 0.8))])
        subs.sort()
        base['tracks'].append({'index': len(base['tracks']), 'name': 'Sub', 'channel': 8,
                               'program': 38, 'drum': False, 'mute': False, 'solo': False,
                               'hidden': False, 'notes': subs, 'ccs': [],
                               'program_changes': [[0.0, 38]], 'markers': []})
        print('  sub 层：%d 音（低八度，Synth Bass 1）' % len(subs))

    base['end_beat'] = max([n[0] + n[1] for t in base['tracks'] for n in t['notes']] or [0.0])
    midi_file.export_midi(base, a.out)
    print('  写 %s（%d 字节）' % (a.out, os.path.getsize(a.out)))


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    main()
