# -*- coding: utf-8 -*-
"""① 尺子自检：三段合成信号 → CLAP 零样本全标签排序

信号（2 秒 / 48kHz / 单声道 / 各自 RMS 归一到 -20 dBFS 以排除"响度差"这一混淆项）：
  A 锯齿波 220Hz       —— 合成器音色原型
  B 白噪声爆发         —— 每 0.25s 一次，10ms attack / 80ms 指数衰减（镲/踩镲原型）
  C 纯正弦 220Hz

判据：A 的 synth 类排第一、B 的镲/打击类排第一。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import clap_lib as C  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WAVDIR = os.path.join(HERE, "selftest_wav")
os.makedirs(WAVDIR, exist_ok=True)

TARGET_RMS_DB = -20.0


def norm(x):
    r = np.sqrt((x ** 2).mean())
    return x * (10 ** (TARGET_RMS_DB / 20.0) / r)


def make_saw(f=220.0, dur=2.0, sr=C.SR):
    t = np.arange(int(dur * sr)) / sr
    return 2.0 * (t * f - np.floor(0.5 + t * f))


def make_sine(f=220.0, dur=2.0, sr=C.SR):
    t = np.arange(int(dur * sr)) / sr
    return np.sin(2 * np.pi * f * t)


def make_noise_bursts(period=0.25, attack=0.010, decay=0.080, dur=2.0, sr=C.SR, seed=0):
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    noise = rng.standard_normal(n)
    env = np.zeros(n)
    tau = decay / np.log(1000.0)          # -60dB 处正好等于 decay
    for k in range(int(dur / period)):
        s = int(round(k * period * sr))
        L = int(min(n - s, 0.20 * sr))
        if L <= 0:
            continue
        tt = np.arange(L) / sr
        a = np.minimum(1.0, tt / attack)
        env[s:s + L] = np.maximum(env[s:s + L], a * np.exp(-tt / tau))
    return noise * env


def main():
    log = C.start_log(os.path.join(HERE, "01_selftest.log"))
    try:
        sigs = {}
        sigs["A_sawtooth220"] = norm(make_saw())
        sigs["B_noiseburst"] = norm(make_noise_bursts())
        sigs["C_sine220"] = norm(make_sine())

        paths, waves, metas = [], [], []
        for name, x in sigs.items():
            p = os.path.join(WAVDIR, name + ".wav")
            sf.write(p, x.astype(np.float32), C.SR, subtype="PCM_16")
            y, sr = sf.read(p, dtype="float64")
            print("# 写 %s  sr=%d  n=%d  RMS=%.2f dBFS  峰值=%.2f dBFS  时长=%.3fs"
                  % (p, sr, len(y), 20 * np.log10(np.sqrt((y ** 2).mean())),
                     20 * np.log10(np.abs(y).max()), len(y) / sr))
            paths.append(p)
            waves.append(y.astype(np.float32))
            metas.append(dict(name=name, wav=p, rms_dbfs=round(
                float(20 * np.log10(np.sqrt((y ** 2).mean()))), 2)))

        model, proc, device, ls_a = C.load_model()

        print("\n# 标签集（%d 条；前 %d 条为任务指定的必带项）" % (len(C.LABELS), len(C.CORE7)))
        for i, l in enumerate(C.LABELS):
            print("#   [%02d]%s %s" % (i, " *" if i < len(C.CORE7) else "  ", l))

        A = C.embed_audio(model, proc, waves, device, tag="selftest")
        T = C.embed_text(model, proc, C.LABELS, device)
        cos = C.cos_to_labels(A, T)

        results = {}
        for i, m in enumerate(metas):
            full = C.rank_row(cos[i], C.LABELS, ls_a)
            core = C.rank_row(cos[i], C.LABELS, ls_a, idx=C.CORE7_IDX)
            print("\n=== %s ===" % m["name"])
            C.print_rank("全 %d 标签排序" % len(C.LABELS), full)
            C.print_rank("限定必带 7 标签（子集重归一化）", core)
            results[m["name"]] = dict(meta=m, full=full, core7=core)

        print("\n=== 判据核对 ===")
        for name, key in (("A_sawtooth220", "synth"), ("B_noiseburst", "镲/打击")):
            top = results[name]["core7"][0]
            print("  %-16s 必带7标签 top1 = %-42s cos=%+.4f p=%.4f"
                  % (name, top["label"], top["cosine"], top["prob"]))
        for name in results:
            top = results[name]["full"][0]
            topc = results[name]["core7"][0]
            print("  %-16s 全标签 top1 = %-42s cos=%+.4f p=%.4f | 必带7 top1 = %s"
                  % (name, top["label"], top["cosine"], top["prob"], topc["label"]))

        C.save_json(os.path.join(HERE, "01_selftest.json"),
                    dict(labels=C.LABELS, core7=C.CORE7, logit_scale_a=ls_a,
                         target_rms_db=TARGET_RMS_DB, results=results))
    finally:
        log.close()


if __name__ == "__main__":
    main()
