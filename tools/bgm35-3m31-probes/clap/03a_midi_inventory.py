# -*- coding: utf-8 -*-
"""MIDI 清点：BGM35_r21_补脉冲.mid 里有哪些轨/program、213-216.5s 有哪些音

不加载 CLAP，纯读盘。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clap_lib as C  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MID = r"D:\test\BGM35_提取\BGM35_r21_补脉冲.mid"
W0, W1 = 213.0, 216.5

GM = {8: "Celesta", 9: "Glockenspiel", 10: "Music Box", 11: "Vibraphone",
      12: "Marimba", 13: "Xylophone", 24: "Nylon Guitar", 25: "Steel Guitar",
      48: "String Ensemble 1", 50: "SynthStrings 1", 80: "Lead1 square",
      81: "Lead2 sawtooth", 88: "Pad2 warm", 0: "Acoustic Grand", 4: "EPiano1",
      32: "Acoustic Bass", 33: "Electric Bass finger", 89: "Pad3 polysynth"}


def main():
    log = C.start_log(os.path.join(HERE, "03a_midi_inventory.log"))
    try:
        import pretty_midi
        print("# pretty_midi %s" % pretty_midi.__version__)
        pm = pretty_midi.PrettyMIDI(MID)
        print("# 文件 %s" % MID)
        print("# 时长 %.2fs  分辨率 %d ppq  初始 bpm %.3f"
              % (pm.get_end_time(), pm.resolution,
                 (60.0 / (pm.get_tempo_changes()[1][0] / 1e6)) if len(pm.get_tempo_changes()[1]) else -1))
        try:
            tc_t, tc_v = pm.get_tempo_changes()
            print("# tempo 变更 %d 处：%s" % (len(tc_t), list(zip(
                [round(float(t), 2) for t in tc_t[:12]], [round(float(v), 2) for v in tc_v[:12]]))))
        except Exception as e:
            print("# tempo 读取失败 %s" % e)

        inv = []
        print("\n# 轨清点（共 %d 个 Instrument 对象）" % len(pm.instruments))
        print("#  %-4s %-6s %-5s %-6s %-6s %-8s %-22s %s"
              % ("idx", "prog", "drum", "n全部", "n窗口", "窗口时值", "GM名", "窗口音高"))
        for i, ins in enumerate(pm.instruments):
            inw = [n for n in ins.notes if n.start < W1 and n.end > W0]
            pitches = sorted(set(n.pitch for n in inw))
            prange = "%d-%d" % (pitches[0], pitches[-1]) if pitches else "-"
            dur = sum(n.end - n.start for n in inw)
            print("#  %-4d %-6d %-5s %-6d %-6d %-8.2f %-22s %s"
                  % (i, ins.program, ins.is_drum, len(ins.notes), len(inw), dur,
                     GM.get(ins.program, "?"), prange))
            inv.append(dict(idx=i, program=int(ins.program), is_drum=bool(ins.is_drum),
                            n_all=len(ins.notes), n_win=len(inw),
                            win_dur=round(float(dur), 3),
                            gm=GM.get(ins.program, "?"), pitches=pitches))

        print("\n# 窗口 %s-%ss 内的音（前 40 条，按时间）" % (W0, W1))
        rows = []
        for i, ins in enumerate(pm.instruments):
            for n in ins.notes:
                if n.start < W1 and n.end > W0:
                    rows.append((round(float(n.start), 4), i, int(ins.program),
                                 int(n.pitch), round(float(n.end - n.start), 4),
                                 int(n.velocity)))
        rows.sort()
        for r in rows[:40]:
            print("#   t=%.4f trk=%d prog=%d pitch=%d(%.4f) dur=%.4f vel=%d"
                  % (r[0], r[1], r[2], r[3], 0, r[4], r[5]))
        print("# 窗口内音符总数 %d" % len(rows))

        # 脉冲层特征：onset 间隔
        onsets = sorted(set(r[0] for r in rows))
        if len(onsets) > 2:
            d = [round(onsets[k + 1] - onsets[k], 4) for k in range(len(onsets) - 1)]
            print("# 窗口内唯一 onset 数 %d；间隔中位 %.4fs 最小 %.4f 最大 %.4f"
                  % (len(onsets), sorted(d)[len(d) // 2], min(d), max(d)))

        C.save_json(os.path.join(HERE, "03a_midi_inventory.json"),
                    dict(mid=MID, end_time=pm.get_end_time(),
                         window=[W0, W1], instruments=inv, notes_win=rows))
    finally:
        log.close()


if __name__ == "__main__":
    main()
