# -*- coding: utf-8 -*-
"""多来源集成转录：把多条转录结果**交叉验证**后合成一份 MIDI（本轮最有效的提升手段）。

原理（实测支撑，见 docs/CASE-BGM35-FINDINGS.md 第 31/32 条）：
  · 单条转录总有假音与漏音，而**不同分离模型/不同参数**的错误互不相关；
  · 把各来源在**同 0.1s 格同音高**上合并，记录"来源集合"，再按来源精度加权打分：
        score(格,音高) = Σ_{来源∈集合} w[来源]
    保留 score ≥ 阈值者 —— 软评分明显优于"至少 N 条来源"这类硬规则；
  · 实测（BGM35，参照=用户认可的扒谱模板）：单来源最优 0.333 → 本方法 **0.532**，
    音符数与参照同量级；recall 0.439 → 0.575。

踩过的坑（不要重犯）：
  1. **`other`/arp 类分轨是假音主源**：被过滤掉的音里六成只有它支撑 → 权重会自然压低它；
  2. **"碎片合并"（同音高间隔 ≤40ms 合并）会让 F1 从 0.481 掉到 0.283** ——
     那些"碎片"其实是**真实的重复起音**，不能合；
  3. **"补漏"要克制**：把单模型在集成覆盖之外的音全收进来（±1 格），
     实测 F1 从 0.417 掉到 0.369 —— 单模型精度低时"更全"≠"更像"；
  4. 权重可**无监督化**（跨来源支持率），排序与监督版一致 → 没有参照也能用。

用法：
    # 监督权重（有参照，权重 = 各来源对参照的音符级精度）
    python scripts/ensemble_transcribe.py --source piano6=bp_piano.mid \
        --source other4=bp_oth4.mid --source ymt3=ymt3_fast.mid \
        --ref 模板.mid --thr 0.50 --out merged.mid

    # 无监督权重（没有参照）
    python scripts/ensemble_transcribe.py --source a=a.mid --source b=b.mid \
        --weight unsup --thr 0.90 --out merged.mid

    # 来源可带音域与前缀：--source 名字=文件:低:高:类别（类别 pianoguitarbassother）
"""
import argparse
import os
from collections import defaultdict

import midi_file

GRID = 0.1          # 合并格（秒）
KIND_LAYER = {      # 来源类别 → 目标层
    "piano": "Piano", "guitar": "Guitar", "bass": "Bass",
    "strings": "Strings", "other": "Piano", "drum": "Perc",
}
LAYERS = [("Piano", 0, 0), ("Guitar", 1, 27), ("Strings", 2, 48), ("Bass", 3, 33), ("Perc", 9, 0)]


def notes_of(path, lo=0, hi=127, drop_drum=True):
    """→ [(秒, 音高, 力度)]（工具链自带解析器，零依赖）"""
    model = midi_file.import_midi(path)
    spb = 60.0 / float(model.get("bpm") or 120.0)
    out = []
    for tr in model.get("tracks", []):
        if drop_drum and (tr.get("drum") or tr.get("channel") == 9):
            continue
        for (st, _du, p, v) in tr.get("notes", []):
            if lo <= p <= hi:
                out.append((float(st) * spb, int(p), int(v)))
    out.sort()
    return out


def keymap(notes):
    d = defaultdict(int)
    for (t, p, _v) in notes:
        d[(int(round(t / GRID)), p)] += 1
    return d


def f1_of(ct, cm):
    if not ct or not cm:
        return 0.0, 0.0, 0.0
    m = sum(min(n, cm.get(k, 0)) for k, n in ct.items())
    nt, nm = sum(ct.values()), sum(cm.values())
    P, R = m / nm, m / nt
    return (2 * P * R / (P + R) if P + R else 0.0), P, R


def parse_source(spec):
    """名字=文件[|低|高|类别]

    ⚠ 字段分隔符用 `|` 而不是 `:` —— Windows 路径自带 `D:`，用冒号会把盘符切断
      （实测报 `invalid literal for int(): '\\test\\...'`）。
    """
    name, rest = spec.split("=", 1)
    parts = rest.split("|")
    path = parts[0]
    lo = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    hi = int(parts[2]) if len(parts) > 2 and parts[2] else 127
    kind = parts[3] if len(parts) > 3 and parts[3] else "piano"
    return name, path, lo, hi, kind


def main():
    ap = argparse.ArgumentParser(description="多来源集成转录（交叉验证 + 软评分）")
    ap.add_argument("--source", action="append", required=True,
                    help="名字=文件[|音域低|音域高|类别]，可重复；类别∈piano/guitar/bass/other")
    ap.add_argument("--ref", help="参照 MIDI（有则用监督权重 = 各来源对参照的精度）")
    ap.add_argument("--weight", choices=["sup", "unsup"], default=None,
                    help="权重方式；给 --ref 默认 sup，否则 unsup")
    ap.add_argument("--thr", type=float, default=None,
                    help="score 阈值（sup 建议 0.50；unsup 建议 0.90，即约等于 1 条来源）")
    ap.add_argument("--out", required=True, help="输出 MIDI")
    ap.add_argument("--bpm", type=float, default=150.0, help="输出 BPM（默认 150）")
    ap.add_argument("--bass-max", type=int, default=47, help="低于此音高且时值长者归 Bass")
    a = ap.parse_args()
    # 面板守卫（硬形式）：没在跑就先拉起来 —— 见 scripts/studio_guard.py 顶部那段。
    try:
        import studio_guard
        studio_guard.ensure_panel()
    except Exception as _e:                                        # noqa: BLE001
        print('  （面板守卫跳过：%s）' % str(_e)[:80])

    mode = a.weight or ("sup" if a.ref else "unsup")
    thr = a.thr if a.thr is not None else (0.50 if mode == "sup" else 0.90)

    srcs = {}
    kind = {}
    for spec in a.source:
        name, path, lo, hi, kd = parse_source(spec)
        if not os.path.isfile(path):
            print("  跳过（文件不存在）：%s" % path)
            continue
        srcs[name] = notes_of(path, lo, hi)
        kind[name] = kd
        print("  来源 %-10s %5d 音（%s）" % (name, len(srcs[name]), kd))

    # ── 合并：同格同音高，记录来源集合 ──
    merged = {}
    for name, ns in srcs.items():
        for (t, p, v) in ns:
            k = (int(round(t / GRID)), p)
            rec = merged.get(k)
            if rec is None:
                merged[k] = [(t, p, v), {name}]
            else:
                rec[1].add(name)
                if v > rec[0][2]:
                    rec[0] = (t, p, v)
    rows = list(merged.items())
    print("\n合并后 %d 个 (格,音高) · 来源数分布 %s"
          % (len(rows), dict(sorted(__import__("collections").Counter(len(s) for _k, (_n, s) in rows).items()))))

    # ── 权重 ──
    w = {}
    if mode == "sup":
        ref_notes = notes_of(a.ref)
        cm_ref = keymap(ref_notes)
        for name, ns in srcs.items():
            _F, P, _R = f1_of(cm_ref, keymap(ns))
            w[name] = P
        print("权重=各来源对参照的精度 P：%s"
              % {k: round(v, 3) for k, v in sorted(w.items(), key=lambda z: -z[1])})
    else:
        sets = {}
        for name, ns in srcs.items():
            s = set()
            for (t, p, _v) in ns:
                g = int(round(t / GRID))
                for dg in (-1, 0, 1):
                    s.add((g + dg, p))
            sets[name] = s
        for name in srcs:
            others = set()
            for n2 in srcs:
                if n2 != name:
                    others |= sets[n2]
            w[name] = len(sets[name] & others) / max(1, len(sets[name]))
        print("权重=跨来源支持率（无需参照）：%s"
              % {k: round(v, 3) for k, v in sorted(w.items(), key=lambda z: -z[1])})

    # ── 筛选 + 编配 ──
    tracks = defaultdict(list)
    kept = 0
    for k, (note, s) in rows:
        score = sum(w.get(n, 0.0) for n in s)
        if score < thr:
            continue
        kept += 1
        t, p, v = note
        # 该音的归属：取"贡献权重最大"的来源类别
        best = max(s, key=lambda n: w.get(n, 0.0))
        layer = KIND_LAYER.get(kind.get(best, "piano"), "Piano")
        if layer == "Perc":
            continue                      # 集成不处理打击乐（来源不同、键位语义不一）
        if p <= a.bass_max and layer == "Piano":
            layer = "Bass"
        tracks[layer].append((t, p, v))
    print("\n阈值 %.2f → 保留 %d / %d 音" % (thr, kept, len(rows)))
    print("  编配：%s" % {k: len(v) for k, v in sorted(tracks.items())})

    # ── 写出 ──
    div = 480
    spb = 60.0 / a.bpm
    model = {"format": 1, "division": div, "bpm": a.bpm, "timesig": [4, 4],
             "title": os.path.splitext(os.path.basename(a.out))[0],
             "end_beat": 0.0, "source": "", "tracks": []}
    for (name, ch, prog) in LAYERS:
        notes = tracks.get(name) or []
        if not notes:
            continue
        out_notes = []
        for (t, p, v) in sorted(notes):
            sb = round(t / spb, 6)
            out_notes.append([sb, 0.35, int(p), int(v)])     # 时值给固定 0.35 拍
        model["tracks"].append({"index": len(model["tracks"]), "name": name, "channel": ch,
                                "program": prog, "drum": ch == 9,
                                "mute": False, "solo": False, "hidden": False,
                                "notes": out_notes, "ccs": [], "program_changes": [], "markers": []})
        model["end_beat"] = max(model["end_beat"], max(n[0] + n[1] for n in out_notes))
    midi_file.export_midi(model, a.out)
    print("\n写 %s（%d 字节）· 轨：%s"
          % (a.out, os.path.getsize(a.out), [t["name"] for t in model["tracks"]]))


if __name__ == "__main__":
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    main()
