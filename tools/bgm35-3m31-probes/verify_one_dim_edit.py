#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r25_check.py —— 独立度量：r25 那 14 个音**真的落地了吗**（不听、只看数）

同一把尺子（q2c 的 energy_share）量三份音频在 213.0-216.5s 的八度能量份额：
  原曲 other（目标）· r21（改前）· r25（改后）
预期：r25 的"高八度组份额"应当**朝原曲方向下降**；若一字未变 = 没生效。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import q2c_final as Q  # noqa: E402

B = r"D:\test\BGM35_提取"
OTHER = os.path.join(Q.STEMS, "other.wav")

JOBS = [
    ("原曲 other 213.0-216.5", OTHER),
    ("r21 改前 213.0-216.5", os.path.join(B, "BGM35_r21_补脉冲.wav")),
    ("r25 改后 213.0-216.5", os.path.join(B, "BGM35_r25_主奏降八度.wav")),
]

out = {}
print("%-26s %10s %10s %10s %8s" % ("对象", "低八度%", "高八度%", "p89%", "p92%"))
for tag, path in JOBS:
    x, fs = Q.load_mono(path, 213.0, 216.5)
    xb = Q.bandpass(x, fs, 600.0, 2600.0)
    es = Q.energy_share(xb, fs, [77, 80, 82, 84, 89, 92, 94, 96, 97])
    lo = sum(es[p] for p in (77, 80, 82, 84))
    up = sum(es[p] for p in (89, 92, 94, 96))
    out[tag] = {"low": round(lo, 4), "up": round(up, 4),
                "p89": round(es[89], 4), "p92": round(es[92], 4),
                "p77": round(es[77], 4), "p80": round(es[80], 4)}
    print("%-26s %9.1f%% %9.1f%% %9.1f%% %7.1f%%"
          % (tag, 100 * lo, 100 * up, 100 * es[89], 100 * es[92]))

json.dump(out, open(r"D:\test\_tmp\b35-models\q2\r25_check.json", "w",
                    encoding="utf-8"), ensure_ascii=False, indent=1)
