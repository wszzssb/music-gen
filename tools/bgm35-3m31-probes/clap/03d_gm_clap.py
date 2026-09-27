# -*- coding: utf-8 -*-
"""(3-3) CLAP 音频嵌入余弦：各 GM 独奏 vs 原曲 other 213-216.5（及整曲同段）

口径：
  · 全部片段重采样到 48kHz 单声道后**统一归一到 -26 dBFS RMS**（去掉电平这一混淆项）
  · 参考 = 原曲 other 分轨 213.0-216.5s；第二参考 = 整曲 BGM35.flac 213.0-216.5s
  · 基准 = 我们现有渲染 BGM35_r21_补脉冲.wav 同段
  · 另出 10×10 候选两两余弦（判"这套嵌入到底分不分得开音色"）
  · 对齐自检：solo_080 dry 与现有渲染同段的包络互相关最佳 lag（应≈0）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import clap_lib as C  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OTHER = r"D:\test\_tmp\b35\stems\htdemucs_6s\BGM35\other.wav"
FULL = r"D:\test\_tmp\b35\BGM35.flac"
OURS = r"D:\test\BGM35_提取\BGM35_r21_补脉冲.wav"
RECHK = os.path.join(HERE, "render_full", "BGM35_r21_render_check.wav")
MAN = os.path.join(HERE, "03c_gm_solo.json")
W0, W1 = 213.0, 216.5
TARGET_DB = -26.0
BAND = (700.0, 2200.0)          # 主奏脉冲所在带，用于对齐自检


def norm_rms(x, target_db=TARGET_DB):
    r = float(np.sqrt((x ** 2).mean()))
    y = x * (10 ** (target_db / 20.0) / max(r, 1e-12))
    pk = float(np.abs(y).max())
    hard = pk > 0.99
    if hard:
        y = y * (0.99 / pk)
    return y.astype(np.float32), r, pk, hard


def bandpass(x, sr, lo, hi):
    n = len(x)
    nf = 1 << max(8, (n - 1).bit_length())
    f = np.fft.rfftfreq(nf, 1.0 / sr)
    g = ((f >= lo) & (f <= hi)).astype(np.float64)
    # 边缘各 1/3 八度做余弦斜坡，避免硬切
    lo2, hi2 = lo * 0.794, hi * 1.26
    ramp_lo = (f > lo2) & (f < lo)
    ramp_hi = (f > hi) & (f < hi2)
    g[ramp_lo] = 0.5 - 0.5 * np.cos(np.pi * (f[ramp_lo] - lo2) / (lo - lo2))
    g[ramp_hi] = 0.5 + 0.5 * np.cos(np.pi * (f[ramp_hi] - hi) / (hi2 - hi))
    return np.fft.irfft(np.fft.rfft(x, n=nf) * g, n=nf)[:n]


def envelope(x, sr, win_ms=10.0):
    h = max(1, int(sr * win_ms / 1000.0))
    n = len(x) // h
    return np.sqrt((x[:n * h].reshape(n, h) ** 2).mean(axis=1) + 1e-20)


def main():
    log = C.start_log(os.path.join(HERE, "03d_gm_clap.log"))
    try:
        import json
        man = json.load(open(MAN, encoding="utf-8"))
        rw0, rw1 = man["window_in_render"]
        print("# 单轨 MIDI 清单 %s" % MAN)
        print("# 原曲窗口 %s-%ss → 渲染件窗口 %s-%ss（平移 %+.1fs）"
              % (W0, W1, rw0, rw1, man["shift"]))
        print("# 源轨 notes=%d（整曲 %d）onset 间隔 中位 %.4fs"
              % (man["source_track"]["n_win"], man["source_track"]["n_all"],
                 sorted(man["onset_intervals"])[len(man["onset_intervals"]) // 2]))

        # ---- 参考与候选
        items = []      # (name, kind, wave)
        for nm, path, a, b in (("原曲_other_213-216.5", OTHER, W0, W1),
                               ("原曲_整曲_213-216.5", FULL, W0, W1),
                               ("我们现有渲染_213-216.5", OURS, W0, W1),
                               ("原midi重渲_213-216.5", RECHK, W0, W1)):
            if not os.path.exists(path):
                print("# 缺文件，跳过 %s" % path)
                continue
            w, m = C.read_mono48(path, a, b)
            items.append((nm, "ref", w, m))
        for p in man["patches"]:
            for kind in ("dry", "chain"):
                w, m = C.read_mono48(p[kind], rw0, rw1)
                items.append(("GM%03d_%s_%s" % (p["program"], p["slug"], kind),
                              kind, w, m))

        waves, metas = [], []
        print("\n# 电平统一（目标 %+.0f dBFS RMS）" % TARGET_DB)
        for nm, kind, w, m in items:
            y, r0, pk, hard = norm_rms(w.astype(np.float64))
            print("#  %-32s 原始RMS=%+.2f dBFS → %+.2f dBFS  峰值%.3f%s"
                  % (nm, m["rms_dbfs"], 20 * np.log10(np.sqrt((y ** 2).mean())),
                     np.abs(y).max(), "  [峰值触顶已压]" if hard else ""))
            waves.append(y.astype(np.float32))
            metas.append(dict(name=nm, kind=kind, path=m["path"], rms_src=m["rms_dbfs"]))

        model, proc, device, ls_a = C.load_model()
        A = np.asarray(C.embed_audio(model, proc, waves, device, tag="gm"))
        names = [m["name"] for m in metas]
        idx = {n: i for i, n in enumerate(names)}
        AA = A @ A.T

        refs = [n for n, m in zip(names, metas) if m["kind"] == "ref"]
        cands = [n for n, m in zip(names, metas) if m["kind"] in ("dry", "chain")]

        # ---- 对齐自检（包络互相关）
        print("\n=== 对齐自检：solo_080_dry 的 700-2200Hz 包络 vs 现有渲染同段 ===")
        for other_name in ("我们现有渲染_213-216.5", "原曲_other_213-216.5"):
            if other_name not in idx:
                continue
            a = bandpass(waves[idx["GM080_lead_square_dry"]].astype(np.float64), C.SR, *BAND)
            b = bandpass(waves[idx[other_name]].astype(np.float64), C.SR, *BAND)
            ea, eb = envelope(a, C.SR), envelope(b, C.SR)
            n = min(len(ea), len(eb))
            ea, eb = ea[:n] - ea[:n].mean(), eb[:n] - eb[:n].mean()
            cc = np.correlate(ea, eb, mode="full") / (np.linalg.norm(ea) * np.linalg.norm(eb))
            lags = (np.arange(len(cc)) - (n - 1)) * 0.010
            k = int(np.argmax(cc))
            print("#  vs %-28s 最佳 lag=%+.3fs  相关=%.3f（0 lag 处 %.3f）"
                  % (other_name, lags[k], cc[k], cc[n - 1]))

        # ---- 相似度表
        for ref in refs:
            print("\n=== 与「%s」的余弦相似度（降序） ===" % ref)
            rows = sorted(((n, float(AA[idx[ref], idx[n]])) for n in cands),
                          key=lambda t: -t[1])
            for i, (n, v) in enumerate(rows, 1):
                print("  %2d. %-32s cos=%+.4f" % (i, n, v))
            base = [n for n in cands if n.startswith("GM080_")]
            print("  [基准] 现有渲染自身 vs 原音源独奏：", end="")
            for n in base:
                print("%s cos=%+.4f  " % (n, AA[idx[ref], idx[n]]), end="")
            print()

        print("\n=== 基准：现有渲染 vs 原曲 ===")
        for ref in refs:
            print("#  %-24s vs %-28s cos=%+.4f"
                  % ("我们现有渲染_213-216.5", ref, AA[idx["我们现有渲染_213-216.5"], idx[ref]]))

        # ---- 分辨率自检：候选两两余弦
        print("\n=== 分辨率自检：候选音频嵌入两两余弦（dry 优先，chain 同） ===")
        sel = [n for n in cands if n.endswith("_dry")]
        print(" " * 26 + "".join("%-12s" % n.split("_")[0] for n in sel))
        for n in sel:
            print("%-26s" % n + "".join("%-12.4f" % AA[idx[n], idx[m]] for m in sel))

        C.save_json(os.path.join(HERE, "03d_gm_clap.json"),
                    dict(window_orig=[W0, W1], window_render=[rw0, rw1],
                         target_rms_db=TARGET_DB, names=names,
                         cos_to_refs={r: {n: round(float(AA[idx[r], idx[n]]), 4)
                                          for n in cands} for r in refs},
                         all_pairs={n: {m: round(float(AA[idx[n], idx[m]]), 4)
                                        for m in names} for n in names},
                         align_check="见日志"))
    finally:
        log.close()


if __name__ == "__main__":
    main()
