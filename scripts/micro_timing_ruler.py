#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""③ 符号层微时序尺子：直接在 **MIDI 音符时刻**上量微时序，不经音频检测 ⇒ 零检测误差。

为什么需要它（本轮尺子自检的结论）：
  · `groove_probe` 在**理想/现实合成**上能测 10ms（误差 0.4~2.6ms），
    但在**真实分轨/混音**上有 ±15ms 噪声底（检测侧：软起音 + 复音 + 分离残留）。
  · ⇒ "模板到底有没有微时序、我们抄了没有"这个问题，**音频尺子答不了**，必须看符号层。

能量什么（三条，都是"每格偏移"的直接量）：
  ① **逐格起音时刻分布**：每个 16 分格位置的 `tick % grid` 分布 —— 是否**恒为 0**
     （= 作者把音符硬量化到格上）还是有真实抖动/系统性偏移
  ② **swing**：奇数格（反拍）相对偶数格的中位偏移差（ms）；判据同 `groove_probe`：>15ms 才算
  ③ **逐轨差异**：不同轨的偏移是否一致（真实编曲里鼓/贝斯常不同）

用法:
  python scripts\\micro_timing_ruler.py <midi 或目录> [--bpm N] [--json]
  python scripts\\micro_timing_ruler.py --templates      # 扫 refs/midi2 全库出汇总
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics as stx
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)


def _tempo_changes(mf):
    """→ (set_tempo 事件列表(绝对 tick, bpm), 是否多tempo)。

    ⚠ **前提检查**：这把尺子假设"固定格"（一个 BPM 贯穿全曲）。若 MIDI 里有**多个 tempo**
      （rubato / 变速），格距会变，那些**结构性的**时刻偏差会被误读成"微时序"
      —— 实测模板 `1943.mid` 报 swing 227ms / sd 409ms，那种量级只能是变速，不是微时序。
      所以必须先报出来、并只对**恒定 tempo**的文件下"有没有微时序"的结论。
    """
    hits = []
    for tr in mf.tracks:
        t = 0
        for m in tr:
            t += m.time
            if m.is_meta and m.type == 'set_tempo':
                hits.append((t, round(60_000_000.0 / m.tempo, 2)))
    hits.sort()
    uniq = sorted({round(b, 1) for _t, b in hits})
    return hits, len(uniq) > 1


def _grid_dev(path, bpm=None):
    """→ (逐轨数据, bpm, tpb, grid_t)。

    逐轨数据 = list of (slot, dev_ms)：`slot` = **16 分格位置 0..15**（用于分奇偶判 swing），
    `dev_ms` = 相对该格的偏移（ms，正 = 晚于格）。**不经音频检测 ⇒ 零检测误差。**
    """
    import mido
    mf = mido.MidiFile(path)
    tpb = mf.ticks_per_beat
    if not bpm:
        for tr in mf.tracks:                    # 用文件里的第一处 set_tempo
            hit = [m for m in tr if m.is_meta and m.type == 'set_tempo']
            if hit:
                bpm = 60_000_000.0 / hit[0].tempo
                break
    bpm = bpm or 120.0
    grid_t = tpb / 4.0                          # 16 分格，单位 tick
    # ⚠ **单位**：`(60/bpm)/tpb` 是**秒**/tick，要乘 1000 才是毫秒。
    #   第一版漏了 ×1000，所有偏移被缩小 1000 倍（显示成 0.00ms、swing 恒 0）——
    #   是自证脚本把它抓出来的（真 10ms 报 0.00）。
    ms_per_tick = ((60.0 / bpm) / tpb) * 1000.0
    half = grid_t / 2.0
    out = {}
    for i, tr in enumerate(mf.tracks):
        t = 0
        rows = []
        for m in tr:
            t += m.time
            if (not m.is_meta) and m.type == 'note_on' and m.velocity > 0:
                gi = int(round(t / grid_t))     # 最近的格
                slot = gi % 16
                # ⚠ **这里不需要"折进 ±半格"**（2026-10-07 实测删掉一行死代码）：
                #   `gi = round(t/grid_t)` 取的就是**最近**的格 ⇒ `t − gi·grid_t` 天然落在
                #   ±半格内。我一度以为"不折会让 swing 超半格时偏移变 0"，加了折叠；
                #   实测**折叠与不折叠在 0~125ms 全部一致**（fold_probe），那几行永远不生效。
                #   当时真正的 bug 是 `ms_per_tick` **漏 ×1000**（见下）。
                #   ⇒ 超过半格（120BPM/16 分格 = 62.5ms）的"swing"**任何实现都测不出**：
                #     它离另一格更近，是**节拍歧义**，不是尺子缺陷。
                dv = t - gi * grid_t
                rows.append((slot, dv * ms_per_tick))
        if rows:
            out[(tr.name or 'tr%d' % i)] = rows
    return out, bpm, tpb, grid_t


def _swing(rows):
    """奇数格（反拍）中位偏移 − 偶数格中位偏移（ms）。>15ms 才算 swing（同 `groove_probe`）。"""
    ev = [d for s, d in rows if s % 2 == 0]
    od = [d for s, d in rows if s % 2 == 1]
    if len(ev) < 4 or len(od) < 4:
        return None
    return stx.median(od) - stx.median(ev)


def report(path, bpm=None, quiet=False):
    try:
        tracks, bpm, tpb, grid_t = _grid_dev(path, bpm)
    except Exception as ex:
        return {'file': path, 'error': str(ex)[:80]}
    grid_ms = (grid_t * (60.0 / bpm) / tpb) * 1000.0
    rows = {}
    for k, data in tracks.items():
        devs = [d for _s, d in data]
        n = len(devs)
        zero = sum(1 for d in devs if abs(d) < 1e-6)
        # **格自洽检查**（2026-10-07 加）：这把尺子把"偏差"折进 ±半格。
        #   若真有音的偏差**超过半格**，说明"固定 16 分格"这个前提对该文件不成立
        #   （三连音 / 变速残留 / 记谱不是 16 分），此时 swing/sd **不可信**。
        #   判据：折叠加权 |偏差| 的中位若接近半格（>40% 半格），就标不可信。
        h = grid_ms / 2.0
        unc = bool(devs) and (stx.median([abs(d) for d in devs]) > 0.4 * h)
        rows[k] = dict(n=n, med=round(stx.median(devs), 3) if n else 0.0,
                       sd=round(stx.pstdev(devs), 3) if n > 1 else 0.0,
                       zero_pct=round(100.0 * zero / n, 1) if n else 0.0,
                       rng=round(max(devs) - min(devs), 3) if n else 0.0,
                       grid_uncertain=unc,
                       swing=(round(_swing(data), 2) if _swing(data) is not None else None))
    try:
        _rel = os.path.relpath(path, ROOT)
    except ValueError:                          # 跨盘符（临时文件在 C: 等）→ 用绝对路径
        _rel = path
    # tempo 前提检查（见 `_tempo_changes`）
    try:
        import mido as _md
        _hits, _multi = _tempo_changes(_md.MidiFile(path))
    except Exception:
        _hits, _multi = [], False
    return {'file': _rel, 'bpm': round(bpm, 1),
            'grid_ms': round(grid_ms, 2), 'tracks': rows,
            'tempo_events': len(_hits), 'tempo_multi': _multi,
            'tempo_bpms': sorted({b for _t, b in _hits})[:6]}


def main():
    import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印中文/✓ 会崩）
    ap = argparse.ArgumentParser()
    ap.add_argument('src', nargs='?')
    ap.add_argument('--bpm', type=float, default=None)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--templates', action='store_true',
                    help='扫 refs/midi2 全库，汇总"有多少模板带微时序"')
    a = ap.parse_args()

    if a.templates:
        files = sorted(glob.glob(os.path.join(ROOT, 'refs', 'midi2', '*', '*.mid')))
        n_grid = n_micro = n_fail = 0
        micro = []
        for f in files:
            r = report(f, a.bpm, quiet=True)
            if 'error' in r:
                n_fail += 1
                continue
            # 只要**任一轨**有非零格内偏移（>2ms）就算"带微时序"
            has = any(v['sd'] > 2.0 or abs(v['med']) > 2.0 for v in r['tracks'].values())
            if has:
                n_micro += 1
                micro.append((r['file'], max((v['sd'] for v in r['tracks'].values()), default=0.0)))
            else:
                n_grid += 1
        print('=== refs/midi2 全库符号层微时序（%d 首）===' % (n_grid + n_micro))
        print('  **纯量化**（每轨格内偏移都 ≤2ms）: %d 首' % n_grid)
        print('  **带微时序**（有轨 sd 或中位 >2ms）: %d 首' % n_micro)
        print('  读取失败: %d 首' % n_fail)
        if micro:
            micro.sort(key=lambda t: -t[1])
            print('\n  微时序最大的 8 首（按最大轨 sd，ms）：')
            for f, sd in micro[:8]:
                print('    %-58s sd %.2f ms' % (f[:58], sd))
        return 0

    if not a.src:
        print('用法: micro_timing_ruler.py <midi|目录> [--bpm N] [--json] | --templates')
        return 2
    paths = []
    if os.path.isdir(a.src):
        paths = sorted(glob.glob(os.path.join(a.src, '**', '*.mid'), recursive=True))
    else:
        paths = [a.src]
    for p in paths:
        r = report(p, a.bpm)
        if a.json:
            print(json.dumps(r, ensure_ascii=False))
            continue
        if 'error' in r:
            print('%-58s 读取失败: %s' % (os.path.basename(p)[:58], r['error']))
            continue
        print('=== %s（%.0f BPM · 16 分格 %.2f ms）%s'
              % (r['file'], r['bpm'], r['grid_ms'],
                 '  ⚠ 多 tempo ⇒ 格距会变、结论不可信' if r.get('tempo_multi') else ''))
        print('  %-16s %6s %10s %9s %10s %9s %s'
              % ('轨', '音数', '中位偏移', '标准差', '格内为零', 'swing', '范围'))
        for k, v in sorted(r['tracks'].items()):
            sw = '%.1fms' % v['swing'] if v['swing'] is not None else '-'
            print('  %-16s %6d %+9.3fms %8.3f %9.1f%% %9s %7.3fms%s'
                  % (k, v['n'], v['med'], v['sd'], v['zero_pct'], sw, v['rng'],
                     '  ⚠格不自洽' if v.get('grid_uncertain') else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
