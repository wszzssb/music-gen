#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mk_r25.py —— 由 r21（选项 A）生成 r25：**只把主奏在 213.70-217.00s 的 p>=89 的音降八度**

⚠ 不用 `resid_bp.rebuild(skeleton=…)`：它按 **(音高,时间) → 轨** 的索引把音放回原轨，
   一旦改了音高索引就落空、退回"按音高就近继承"→ **静默把编配压平**（坑 277 同类）。
   这里用 `midi_file` 直接改、再逐项读回核验。
改 1 个维度（音高），不动时值/力度/轨/通道/program/其它任何音。
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, r"D:\software\skill\music-gen\scripts")
import midi_file  # noqa: E402

B = r"D:\test\BGM35_提取"
SRC = os.path.join(B, "BGM35_r21_补脉冲.mid")
OUT = os.path.join(B, "BGM35_r25_主奏降八度.mid")
T0, T1, MINP, PROG = 213.70, 217.00, 89, 80


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def dump(m):
    return [(t.get("name"), t.get("channel"), t.get("program"), bool(t.get("drum")),
             len(t.get("notes", []))) for t in m["tracks"]]


def main():
    print("基准 r21 SHA256 = %s" % sha(SRC))
    m0 = midi_file.import_midi(SRC)
    before_tracks = dump(m0)
    before_notes = sum(x[4] for x in before_tracks)
    bpm = float(m0["bpm"])
    spb = 60.0 / bpm
    print("r21：%d 轨 / %d 音 / bpm=%s" % (len(before_tracks), before_notes, bpm))
    for x in before_tracks:
        print("   ", x)

    changed = []
    n_trk = 0
    # ⚠ **改之前先快照**：第一版在 m0 上原地改完再拿 m0 和读回的比 —— 那是**自比自**，
    #   必然报"0 处差异"，等于没验（"ok ≠ 生效"的翻版）。
    snapshot = [[[float(a[0]), float(a[1]), int(a[2]), int(a[3])] for a in t["notes"]]
                for t in m0["tracks"]]
    for tr in m0["tracks"]:
        if bool(tr.get("drum")) or tr.get("channel") == 9:
            continue
        if int(tr.get("program") or 0) != PROG:
            continue
        n_trk += 1
        for nt in tr["notes"]:
            t = float(nt[0]) * spb
            if T0 <= t <= T1 and int(nt[2]) >= MINP:
                changed.append({"t": round(t, 3), "from": int(nt[2]), "to": int(nt[2]) - 12,
                                "vel": int(nt[3])})
                nt[2] = int(nt[2]) - 12
    print("\n命中主奏轨 %d 条 · 改音高 %d 个：" % (n_trk, len(changed)))
    for c in sorted(changed, key=lambda z: z["t"]):
        print("   %7.3fs  p%-3d → p%-3d  (vel %d)" % (c["t"], c["from"], c["to"], c["vel"]))
    assert n_trk == 1, "主奏轨数不是 1（%d）—— 停手" % n_trk
    assert len(changed) == 14, "命中的音数不是 14（%d）—— 停手" % len(changed)

    midi_file.export_midi(m0, OUT, fmt=1)

    # ---------------- 读回核验（"工具不报错 ≠ 生效"）
    m1 = midi_file.import_midi(OUT)
    after_tracks = dump(m1)
    after_notes = sum(x[4] for x in after_tracks)
    print("\n读回 %s" % OUT)
    print("  轨构成 改前 == 改后 ? %s" % (before_tracks == after_tracks))
    print("  总音数 改前 %d == 改后 %d ? %s" % (before_notes, after_notes,
                                              before_notes == after_notes))
    diff = []
    for i, tr1 in enumerate(m1["tracks"]):
        n0, n1 = snapshot[i], tr1["notes"]
        assert len(n0) == len(n1)
        for a, b in zip(n0, n1):
            aft = [float(b[0]), float(b[1]), int(b[2]), int(b[3])]
            if a != aft:
                diff.append({"t": round(aft[0] * spb, 3), "before": a, "after": aft})
    print("  逐音差异 %d 处（应恰好 = 改的 %d 个，且只差音高）" % (len(diff), len(changed)))
    ok = len(diff) == len(changed)
    for d in diff:
        # ⚠ 比较必须用**全精度**：第一版把"显示用的 round(...,4)"也拿去比 → 0.160417 与
        #   0.1604 被判成"别的不该动"，**假 FAIL**（差异是我自己四舍五入出来的）。
        same = (abs(d["before"][0] - d["after"][0]) < 1e-9
                and abs(d["before"][1] - d["after"][1]) < 1e-9
                and d["before"][3] == d["after"][3]
                and d["after"][2] == d["before"][2] - 12)
        ok = ok and same
        print("    %7.3fs p%-3d→p%-3d vel %d 时值%.6f拍  %s"
              % (d["t"], d["before"][2], d["after"][2], d["after"][3], d["after"][1],
                 "OK（只差音高）" if same else "!! 别的不该动"))
    print("\n核验 %s" % ("PASS" if ok else "FAIL"))
    print("r25 SHA256 = %s" % sha(OUT))
    json.dump({"src": SRC, "src_sha256": sha(SRC), "out": OUT, "out_sha256": sha(OUT),
               "changed": changed, "verify": "PASS" if ok else "FAIL"},
              open(r"D:\test\_tmp\b35-models\q2\r25_build.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
