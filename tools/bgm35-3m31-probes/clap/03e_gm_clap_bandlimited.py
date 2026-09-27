# -*- coding: utf-8 -*-
"""(3-3b) 带限视图下的 GM 相似度：把"亮层"从"垫子层"里分出来

为什么加这一层：other 分轨 213-216.5s 是**混合物**（该窗口里 GM88 pad 有 18 个音、
GM80 主奏 23 个音、还有钢琴/尼龙吉他），宽带余弦会被 pad 的低频能量主导。
所以再出两个视图（两侧同滤波，公平比较）：
  V_hp6k  —— 6kHz 高通（任务指定的第二读数）
  V_hp700 —— 700Hz 高通（保住脉冲基频 700-2200Hz 及其谐波，砍掉 pad/bass 基频）

候选 = 9 个 GM 独奏（dry / chain）+ 我们现有渲染（基准）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import clap_lib as C  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OTHER = r"D:\test\_tmp\b35\stems\htdemucs_6s\BGM35\other.wav"
OURS = r"D:\test\BGM35_提取\BGM35_r21_补脉冲.wav"
MAN = os.path.join(HERE, "03c_gm_solo.json")
W0, W1 = 213.0, 216.5
TARGET_DB = -26.0
VIEWS = [("hp6k", 6000.0), ("hp700", 700.0)]


def norm_rms(x, target_db=TARGET_DB):
    r = float(np.sqrt((x ** 2).mean()))
    return (x * (10 ** (target_db / 20.0) / max(r, 1e-12))).astype(np.float32), r


def main():
    log = C.start_log(os.path.join(HERE, "03e_gm_clap_bandlimited.log"))
    try:
        man = json.load(open(MAN, encoding="utf-8"))
        rw0, rw1 = man["window_in_render"]
        model, proc, device, ls_a = C.load_model()
        out = {}
        for vname, fc in VIEWS:
            print("\n############ 视图 %s（%.0f Hz 零相位高通，两侧同滤波） ############"
                  % (vname, fc))
            items = [("原曲_other_213-216.5", "ref", OTHER, W0, W1),
                     ("我们现有渲染_213-216.5", "ours", OURS, W0, W1)]
            for p in man["patches"]:
                for kind in ("dry", "chain"):
                    items.append(("GM%03d_%s_%s" % (p["program"], p["slug"], kind),
                                  kind, p[kind], rw0, rw1))
            waves, metas = [], []
            for nm, kind, path, a, b in items:
                w, m = C.read_mono48(path, a, b, hp_hz=fc)
                y, r0 = norm_rms(w.astype(np.float64))
                waves.append(y)
                metas.append(dict(name=nm, kind=kind))
            A = np.asarray(C.embed_audio(model, proc, waves, device, tag=vname))
            names = [m["name"] for m in metas]
            idx = {n: i for i, n in enumerate(names)}
            AA = A @ A.T
            ref = "原曲_other_213-216.5"
            cands = [n for n in names if n not in (ref,)]
            rows = sorted(((n, float(AA[idx[ref], idx[n]])) for n in cands),
                          key=lambda t: -t[1])
            print("# 与「原曲 other 213-216.5（%s 滤波后）」的余弦相似度（降序）：" % vname)
            for i, (n, v) in enumerate(rows, 1):
                print("  %2d. %-32s cos=%+.4f" % (i, n, v))
            # 分辨率：9 个 dry 候选两两
            sel = [n for n in cands if n.endswith("_dry")]
            print("\n# 分辨率自检（%s, dry 9 色两两余弦）" % vname)
            print(" " * 26 + "".join("%-11s" % n.split("_")[0] for n in sel))
            for n in sel:
                print("%-26s" % n + "".join("%-11.4f" % AA[idx[n], idx[m]] for m in sel))
            vals = [AA[idx[sel[i]], idx[sel[j]]] for i in range(len(sel))
                    for j in range(i + 1, len(sel))]
            print("# dry 两两余弦范围 %.4f ~ %.4f（越散说明分辨力越好）"
                  % (min(vals), max(vals)))
            out[vname] = dict(fc=fc, ranking=[dict(name=n, cos=round(v, 4)) for n, v in rows],
                              dry_pair_min=round(float(min(vals)), 4),
                              dry_pair_max=round(float(max(vals)), 4))
        C.save_json(os.path.join(HERE, "03e_gm_clap_bandlimited.json"), out)
    finally:
        log.close()


if __name__ == "__main__":
    main()
