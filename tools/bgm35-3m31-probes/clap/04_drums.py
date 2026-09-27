# -*- coding: utf-8 -*-
"""④ Q1 独立证据：鼓层真假 —— drums 216-220 vs 对照窗 205-208

外加 other 216-220（"纯旋律无鼓"参照）。同时打印各窗 RMS。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import clap_lib as C  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STEM = r"D:\test\_tmp\b35\stems\htdemucs_6s\BGM35"


def main():
    log = C.start_log(os.path.join(HERE, "04_drums.log"))
    try:
        cases = [
            ("drums_216.0-220.0", "drums.wav", 216.0, 220.0),
            ("drums_205.0-208.0", "drums.wav", 205.0, 208.0),
            ("other_216.0-220.0", "other.wav", 216.0, 220.0),
            ("other_205.0-208.0", "other.wav", 205.0, 208.0),
            ("bass_216.0-220.0", "bass.wav", 216.0, 220.0),
        ]
        waves, metas = [], []
        for name, fn, a, b in cases:
            w, m = C.read_mono48(os.path.join(STEM, fn), a, b)
            m["name"] = name
            waves.append(w)
            metas.append(m)
            print("# %-22s %.4fs  RMS=%+.2f dBFS  峰值=%+.2f dBFS"
                  % (name, m["dur"], m["rms_dbfs"], m["peak_dbfs"]))

        print("\n# RMS 对照：drums(216-220) - other(216-220) = %+.2f dB"
              % (metas[0]["rms_dbfs"] - metas[2]["rms_dbfs"]))
        print("# RMS 对照：drums(216-220) - drums(205-208) = %+.2f dB"
              % (metas[0]["rms_dbfs"] - metas[1]["rms_dbfs"]))

        # 电平对照：drums 216-220 只有 -41.7dBFS（比对照窗低 21dB）→ 归一到 -20dBFS 看标签是否漂
        wd = waves[0].astype(np.float64)
        r = np.sqrt((wd ** 2).mean())
        waves.append((wd * (10 ** (-20.0 / 20.0) / r)).astype(np.float32))
        metas.append(dict(metas[0], name="drums_216.0-220.0_norm-20dB", rms_dbfs=-20.0))
        print("# 电平对照：drums(216-220) 归一到 -20dBFS 再打一次")

        model, proc, device, ls_a = C.load_model()
        A = C.embed_audio(model, proc, waves, device, tag="q1")
        T = C.embed_text(model, proc, C.LABELS, device)
        cos = C.cos_to_labels(A, T)

        out = {}
        for i, m in enumerate(metas):
            full = C.rank_row(cos[i], C.LABELS, ls_a)
            core = C.rank_row(cos[i], C.LABELS, ls_a, idx=C.CORE7_IDX)
            print("\n=== %s ===" % m["name"])
            C.print_rank("全 33 标签 top-8", full, top=8)
            C.print_rank("必带 7 标签 top-7", core, top=7)
            out[m["name"]] = dict(meta=m, top8=full[:8], core7=core)

        AA = np.asarray(A) @ np.asarray(A).T          # (n_audio, n_audio)
        names = [m["name"] for m in metas]
        print("\n=== 音频嵌入两两余弦 A·A^T ===")
        print(" " * 24 + "".join("%-22s" % n[:21] for n in names))
        for i, n in enumerate(names):
            print("%-24s" % n[:23] + "".join("%-22.4f" % AA[i, j]
                                             for j in range(len(names))))
        out["_sim_matrix"] = {names[i]: {names[j]: round(float(AA[i, j]), 4)
                                         for j in range(len(names))}
                              for i in range(len(names))}
        out["_labels"] = C.LABELS
        out["_core7"] = C.CORE7
        C.save_json(os.path.join(HERE, "04_drums.json"), out)
    finally:
        log.close()


if __name__ == "__main__":
    main()
