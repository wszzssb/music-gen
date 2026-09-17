# -*- coding: utf-8 -*-
"""转录评估：被评 MIDI 对参照 MIDI 的**音符级 F1**（逐段列出，不取全曲平均）。

指标定义（同 16 分格 + 同音高，一对一计数）：
    matched[(格, 音高)] = min(参照个数, 被评个数)
    P = matched / 被评音符数   ·   R = matched / 参照音符数   ·   F1 = 2PR/(P+R)

判读要点（都是踩过的坑）：
  1. **先自检**：`--self-test` 把参照自己当被评跑一遍，F1 应 ≈ 1.0。
     若量化格用 `floor(t/0.1 + eps)`，恒等输入也可能只有 0.75–0.94 —— 那是 16 分格
     边界抖动，不是"不像"。**报数必须说明尺子的天花板**，否则 0.30 会被误读成"很差"。
  2. **逐段看，绝不看全曲平均**：同一首曲子里各段节奏本就不同，平均值会把差异抹平，
     而听感反馈总指向局部（"开头乱"、"这段不像"）。
  3. P 低 = 多弹了参照没有的音（糊）；R 低 = 漏了参照有的音（空）。两者要分开读。
  4. 判断"是否只是错位"用 `--shift`：逐段另搜 ±2 秒，若大幅提升说明是时间轴没对齐，
     而不是内容不同。

用法：
    python scripts/eval_transcription.py <被评.mid> --ref <参照.mid> [--seg 12.8]
    python scripts/eval_transcription.py --self-test --ref <参照.mid>        # 量尺子天花板
    python scripts/eval_transcription.py <被评.mid> --ref <参照.mid> --shift # 搜时间偏移
"""
import argparse
from collections import defaultdict

import midi_file

GRID = 0.1          # 16 分格（秒）


def notes_sec(path, drop_drum=True):
    """→ [(起始秒, 音高)]。用工具链自带解析器（零依赖），时间由 拍 × 60/BPM 换算。"""
    model = midi_file.import_midi(path)
    spb = 60.0 / float(model.get("bpm") or 120.0)
    out = []
    for tr in model.get("tracks", []):
        if drop_drum and tr.get("drum"):
            continue
        if tr.get("channel") == 9:
            continue
        for (st, _du, p, _v) in tr.get("notes", []):
            out.append((float(st) * spb, int(p)))
    out.sort()
    return out


def keymap(notes):
    d = defaultdict(int)
    for (t, p) in notes:
        d[(int(round(t / GRID)), p)] += 1
    return d


def f1_of(ct, cm):
    if not ct or not cm:
        return 0.0, 0.0, 0.0, 0
    m = sum(min(n, cm.get(k, 0)) for k, n in ct.items())
    nt, nm = sum(ct.values()), sum(cm.values())
    P, R = m / nm, m / nt
    return (2 * P * R / (P + R) if P + R else 0.0), P, R, m


def main():
    ap = argparse.ArgumentParser(description="转录评估（音符级 F1，逐段）")
    ap.add_argument("mine", nargs="?", help="被评 MIDI")
    ap.add_argument("--ref", required=True, help="参照 MIDI")
    ap.add_argument("--seg", type=float, default=12.8, help="段长秒（默认 12.8 = 8 小节 @150BPM）")
    ap.add_argument("--self-test", action="store_true", help="参照自己当被评，量天花板")
    ap.add_argument("--shift", action="store_true", help="逐段搜 ±2s 时间偏移")
    ap.add_argument("--keep-drum", action="store_true", help="保留打击乐通道（默认剔除）")
    a = ap.parse_args()

    ref = notes_sec(a.ref, drop_drum=not a.keep_drum)
    mine = ref if a.self_test else notes_sec(a.mine, drop_drum=not a.keep_drum)
    ref_map, mine_map = keymap(ref), keymap(mine)

    dur = max([t for (t, _p) in ref + mine] or [0.0])
    nseg = int(dur // a.seg) + 1
    print("参照 %s（%d 音）· 被评 %s（%d 音）· 段长 %.1fs"
          % (a.ref, len(ref), "（自检：参照自己）" if a.self_test else a.mine, len(mine), a.seg))
    print()
    hdr = "%-4s %8s | %6s %6s | %8s %8s %6s" % ("段", "秒", "参照音", "被评音", "P", "R", "F1")
    if a.shift:
        hdr += " %9s %8s" % ("最佳dt", "F1(最佳)")
    print(hdr)
    print("-" * len(hdr))

    rows = []
    for i in range(nseg):
        lo, hi = i * a.seg, (i + 1) * a.seg
        st = {k: v for k, v in ref_map.items() if lo <= k[0] * GRID < hi}
        sm = {k: v for k, v in mine_map.items() if lo <= k[0] * GRID < hi}
        nt, nm = sum(st.values()), sum(sm.values())
        if not nt and not nm:
            continue
        F, P, R, _m = f1_of(st, sm)
        line = "%-4d %8.1f | %6d %6d | %8.3f %8.3f %6.3f" % (i, lo, nt, nm, P, R, F)
        if a.shift:
            best = (F, 0.0)
            for dti in range(-20, 21):
                dt = dti * 0.1
                sm2 = {k: v for k, v in mine_map.items() if lo + dt <= k[0] * GRID < hi + dt}
                cand = f1_of(st, sm2)[0]
                if cand > best[0]:
                    best = (cand, dt)
            line += " %+8.1fs %8.3f" % (best[1], best[0])
        print(line)
        rows.append((i, F, P, R))

    print("-" * len(hdr))
    if rows:
        fs = sorted(r[1] for r in rows)
        print("逐段 F1：最低 %.3f · 中位 %.3f · 最高 %.3f（**判断按上表逐段**）"
              % (fs[0], fs[len(fs) // 2], fs[-1]))
    totF, totP, totR, _ = f1_of(ref_map, mine_map)
    print("全曲参考（仅供尺度感）：P %.3f · R %.3f · F1 %.3f" % (totP, totR, totF))
    if a.self_test:
        print("\n自检：F1 应 ≈ 1.0；明显低于 1.0 说明量化实现有边界抖动，")
        print("      此后所有分数都要相对这个天花板来读。")


if __name__ == "__main__":
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    main()
