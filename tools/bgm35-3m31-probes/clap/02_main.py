# -*- coding: utf-8 -*-
"""② Q3 主量：原曲 other 分轨的亮音色层 → CLAP 零样本标签排序

读数：
  · other 213.0-216.5s（原样）
  · other 213.0-216.5s（6kHz 零相位高通版）
  · other 216.0-220.0s
  · 附：整曲 BGM35.flac 213.0-216.5s（做相似度的参考尺度）
  · 附：电平对照（把 213-216.5 归一到 -20dBFS 再打一次，看标签是否随电平漂移）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import clap_lib as C  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OTHER = r"D:\test\_tmp\b35\stems\htdemucs_6s\BGM35\other.wav"
DRUMS = r"D:\test\_tmp\b35\stems\htdemucs_6s\BGM35\drums.wav"
FULL = r"D:\test\_tmp\b35\BGM35.flac"


def main():
    log = C.start_log(os.path.join(HERE, "02_main.log"))
    try:
        cases = [
            ("other_213.0-216.5", OTHER, 213.0, 216.5, None),
            ("other_213.0-216.5_hp6k", OTHER, 213.0, 216.5, 6000.0),
            ("other_216.0-220.0", OTHER, 216.0, 220.0, None),
            ("full_213.0-216.5", FULL, 213.0, 216.5, None),
            ("other_205.0-208.0", OTHER, 205.0, 208.0, None),
            ("drums_216.0-220.0", DRUMS, 216.0, 220.0, None),
        ]
        waves, metas = [], []
        for name, path, a, b, hp in cases:
            w, m = C.read_mono48(path, a, b, hp_hz=hp)
            m["name"] = name
            waves.append(w)
            metas.append(m)
            print("# %-24s %.4fs  RMS=%+.2f dBFS  峰值=%+.2f dBFS  (源 %d Hz)"
                  % (name, m["dur"], m["rms_dbfs"], m["peak_dbfs"], m["src_sr"]))

        # 电平对照：把第一段归一到 -20dBFS
        w0 = waves[0].astype(np.float64)
        r = np.sqrt((w0 ** 2).mean())
        ctrl = (w0 * (10 ** (-20.0 / 20.0) / r)).astype(np.float32)
        waves.append(ctrl)
        metas.append(dict(metas[0], name="other_213.0-216.5_norm-20dB",
                          rms_dbfs=-20.0))

        model, proc, device, ls_a = C.load_model()
        A = C.embed_audio(model, proc, waves, device, tag="q3")
        T = C.embed_text(model, proc, C.LABELS, device)
        cos = C.cos_to_labels(A, T)

        out = {}
        for i, m in enumerate(metas):
            full = C.rank_row(cos[i], C.LABELS, ls_a)
            core = C.rank_row(cos[i], C.LABELS, ls_a, idx=C.CORE7_IDX)
            print("\n=== %s (%.2f-%.2fs%s) ==="
                  % (m["name"], m["start"], m["end"],
                     ", hp%gk" % (m["hp_hz"] / 1000) if m.get("hp_hz") else ""))
            C.print_rank("全 33 标签 top-8", full, top=8)
            C.print_rank("必带 7 标签 top-7", core, top=7)
            out[m["name"]] = dict(meta=m, top8=full[:8], core7=core)

        # 参考尺度：**音频嵌入两两**余弦（给"相似度"一个可比基线）
        AA = np.asarray(A) @ np.asarray(A).T          # (n_audio, n_audio)
        print("\n=== 参考尺度：音频嵌入两两余弦 A·A^T ===")
        names = [m["name"] for m in metas]
        print(" " * 24 + "".join("%-16s" % n[:15] for n in names))
        for i, n in enumerate(names):
            print("%-24s" % n[:23] + "".join("%-16.4f" % AA[i, j]
                                             for j in range(len(names))))
        sim = {names[i]: {names[j]: round(float(AA[i, j]), 4)
                          for j in range(len(names))} for i in range(len(names))}
        # 用文本嵌入的余弦做对照：同一段音频对 33 个标签的 cos 分布
        out["_sim_matrix"] = sim
        out["_labels"] = C.LABELS
        out["_core7"] = C.CORE7
        out["_logit_scale_a"] = ls_a
        C.save_json(os.path.join(HERE, "02_main.json"), out)
    finally:
        log.close()


if __name__ == "__main__":
    main()
