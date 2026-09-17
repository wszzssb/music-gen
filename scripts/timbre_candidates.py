# -*- coding: utf-8 -*-
"""生成音色候选：同一份 MIDI、只改 program，用于 A/B 挑音色。

v5 当前配置（用户反馈"乐器用的有点奇怪"）：
    Piano=0 Acoustic Grand · Guitar=27 Electric clean · Strings=48 Ensemble1 · Bass=33 Finger

做四组，方向不同，方便一次听出偏好：
    A 原声系   Guitar=24 Nylon · Strings=49 Ensemble2 · Bass=34 Pick   （更柔和、原声）
    B 合成系   Piano=4 E.Piano · Strings=50 Synth Strings · Bass=38 Synth Bass1（游戏 BGM 味）
    C 明亮系   Piano=1 Bright · Guitar=25 Steel · Strings=48 · Bass=33 （更亮更清）
    D 低音厚   Bass=37 Slap / 36 Fretless 由外部再试（本脚本默认 34）

用法：python timbre_candidates.py <输入.mid> <输出目录>
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import midi_file

SCHEMES = {
    "A_原声系": {"Piano": 0, "Guitar": 24, "Strings": 49, "Bass": 34, "Perc": 0},
    "B_合成系": {"Piano": 4, "Guitar": 27, "Strings": 50, "Bass": 38, "Perc": 0},
    "C_明亮系": {"Piano": 1, "Guitar": 25, "Strings": 48, "Bass": 33, "Perc": 0},
    "D_厚贝斯": {"Piano": 0, "Guitar": 27, "Strings": 48, "Bass": 36, "Perc": 0},
    "Z_当前":   {"Piano": 0, "Guitar": 27, "Strings": 48, "Bass": 33, "Perc": 0},
}


def main():
    src = sys.argv[1]
    outdir = sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    base = midi_file.import_midi(src)
    print("源 %s · BPM %.1f · 轨：%s"
          % (src, base.get("bpm", 0), [t["name"] for t in base["tracks"]]))
    made = []
    for name, prog in SCHEMES.items():
        model = json.loads(json.dumps(base))      # 深拷贝
        for tr in model["tracks"]:
            if tr["name"] in prog:
                tr["program"] = prog[tr["name"]]
                # 同刻的 program_change 也要改，否则导出时以它为准
                tr["program_changes"] = [[0, prog[tr["name"]]]]
        dst = os.path.join(outdir, "%s.mid" % name)
        midi_file.export_midi(model, dst)
        made.append((name, dst))
        print("  %-8s %s" % (name, {k: v for k, v in prog.items() if k != "Perc"}))
    print("\n产物目录 %s" % outdir)
    with open(os.path.join(outdir, "schemes.json"), "w", encoding="utf-8") as f:
        json.dump(SCHEMES, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    main()
