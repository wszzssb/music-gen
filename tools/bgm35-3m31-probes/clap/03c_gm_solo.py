# -*- coding: utf-8 -*-
"""(3-2) 造单轨 MIDI + 用 9 种 GM 音色各渲一遍独奏

音源轨：BGM35_r21_补脉冲.mid 里 program=80(Lead1 square) 的那条（idx=5）。
窗口：原曲 213.0-216.5s（overlap 判定）→ 整体平移 -211.5s → 渲染件里对应 1.5-5.0s。

每色两份渲染：
  dry   —— fluidsynth 直出（reverb/chorus 全关）＝纯音色
  chain —— 走 render_midi.render() 交付链（+3dB@3kHz 搁架 / 38Hz 高通 / 软限幅 / 归一 -16.9 / 宽度 2.2）

不碰 D:\\test\\BGM35_提取\\ 下任何东西。
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"D:\software\skill\music-gen\scripts")
os.environ["BGM_NO_OGG"] = "1"
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SOld = os.path.join(HERE, "gm_solo")
MID = r"D:\test\BGM35_提取\BGM35_r21_补脉冲.mid"
W0, W1 = 213.0, 216.5
SHIFT = -211.5          # 渲染件里窗口 = 1.5-5.0s

PATCHES = [
    (8, "celesta", "钢片琴 GM8"),
    (9, "glockenspiel", "钟琴 GM9"),
    (10, "musicbox", "八音盒 GM10"),
    (11, "vibraphone", "颤音琴 GM11"),
    (24, "nylon_guitar", "尼龙吉他 GM24"),
    (50, "synth_strings", "合成弦乐 GM50"),
    (80, "lead_square", "方波主奏 GM80"),
    (81, "lead_saw", "锯齿主奏 GM81"),
    (88, "pad2_warm", "Pad2 warm GM88"),
    (0, "piano_ctrl", "钢琴 GM0（负对照）"),
]


def main():
    import clap_lib as C
    log = C.start_log(os.path.join(HERE, "03c_gm_solo.log"))
    try:
        os.makedirs(SOld, exist_ok=True)
        import pretty_midi
        import render_midi

        exe, sf2 = render_midi.find_exe(), render_midi.find_sf2()
        print("# fluidsynth = %s" % exe)
        print("# sf2        = %s" % sf2)

        pm = pretty_midi.PrettyMIDI(MID)
        lead = [ins for ins in pm.instruments if ins.program == 80 and not ins.is_drum]
        print("# program=80 的轨数 %d；各自音符数 %s"
              % (len(lead), [len(i.notes) for i in lead]))
        ins = lead[0]
        win = [n for n in ins.notes if n.start < W1 and n.end > W0]
        win.sort(key=lambda n: n.start)
        # 检查是否有"起点在窗口前、跨越窗口左沿"的音
        straddle = [n for n in win if n.start < W0]
        print("# 窗口 %s-%ss 内（overlap 判定）音符 %d 个；跨越左沿 %d 个"
              % (W0, W1, len(win), len(straddle)))
        ons = sorted(set(round(float(n.start), 4) for n in win))
        d = [round(ons[k + 1] - ons[k], 4) for k in range(len(ons) - 1)]
        print("# onset 数 %d；间隔：%s" % (len(ons), d))
        if d:
            print("# 间隔 中位 %.4fs 均值 %.4fs 最小 %.4f 最大 %.4f"
                  % (sorted(d)[len(d) // 2], sum(d) / len(d), min(d), max(d)))
        print("# 音高 %s；时值中位 %.4fs；力度 %s"
              % (sorted(set(int(n.pitch) for n in win)),
                 sorted([n.end - n.start for n in win])[len(win) // 2],
                 sorted(set(int(n.velocity) for n in win))))
        print("# 全轨音符数 %d（整曲）" % len(ins.notes))

        # 造单轨 MIDI（音高/时值/力度原样，仅整体平移）
        man = dict(mid=MID, source_track=dict(program=80, n_all=len(ins.notes),
                                              n_win=len(win)),
                   window_orig=[W0, W1], shift=SHIFT,
                   window_in_render=[round(W0 + SHIFT, 3), round(W1 + SHIFT, 3)],
                   notes=[dict(start=round(float(n.start + SHIFT), 5),
                               end=round(float(n.end + SHIFT), 5),
                               pitch=int(n.pitch), velocity=int(n.velocity))
                          for n in win],
                   onset_intervals=d, patches=[])
        for p, slug, cn in PATCHES:
            pm2 = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
            i2 = pretty_midi.Instrument(program=p, is_drum=False, name=slug)
            for n in win:
                i2.notes.append(pretty_midi.Note(
                    velocity=int(n.velocity), pitch=int(n.pitch),
                    start=float(n.start + SHIFT), end=float(n.end + SHIFT)))
            pm2.instruments.append(i2)
            midp = os.path.join(SOld, "solo_%03d_%s.mid" % (p, slug))
            pm2.write(midp)

            dryp = os.path.join(SOld, "solo_%03d_%s_dry.wav" % (p, slug))
            t0 = time.time()
            cmd = [exe, "-ni", "-g", "1.0", "-r", "44100",
                   "-o", "synth.reverb.active=0", "-o", "synth.chorus.active=0",
                   "-o", "synth.gain=1.0", "-F", dryp, sf2, midp]
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace", timeout=300)
            if r.returncode != 0:
                raise RuntimeError("fluidsynth(dry) 失败 %s: %s" % (midp, (r.stderr or '')[-300:]))
            x, sr = sf.read(dryp, dtype="float64", always_2d=True)
            print("#  dry  GM%-3d %-14s %5.2fs  dur=%.3fs 峰值=%.4f(%.1fdBFS)"
                  % (p, slug, time.time() - t0, len(x) / sr, np.abs(x).max(),
                     20 * np.log10(max(np.abs(x).max(), 1e-12))))

            base = os.path.join(SOld, "solo_%03d_%s_chain" % (p, slug))
            t0 = time.time()
            render_midi.render(midp, base, verbose=False, ogg=False)
            chp = base + ".wav"
            x2, sr2 = sf.read(chp, dtype="float64", always_2d=True)
            print("#  chain GM%-3d %-12s %5.2fs  dur=%.3fs 峰值=%.4f"
                  % (p, slug, time.time() - t0, len(x2) / sr2, np.abs(x2).max()))
            man["patches"].append(dict(program=p, slug=slug, cn=cn, mid=midp,
                                       dry=dryp, chain=chp, src_sr=int(sr)))
        C.save_json(os.path.join(HERE, "03c_gm_solo.json"), man)
    finally:
        log.close()


if __name__ == "__main__":
    main()
