# -*- coding: utf-8 -*-
"""分轨混音：把若干条干声轨按目标能量比合成一条（可复用版）。

做法（BGM35 验证过）：
  ① 各轨先各自归一化到同一等响参考，**再**按目标能量份额缩放 ——
     直接对原始能量做最小二乘会算出 +13dB 这类离谱增益（那些轨的绝对能量尺度不同）；
  ② 目标份额来自**原曲分轨的实测能量比**（drums / bass / other），
     这样"谁该多响"有客观依据，不靠拍脑袋；
  ③ 求和后统一峰值保护。

用法：
    python scripts/mix_stems.py -o 输出.wav --track Piano:0.62 --track Bass:0.875 --track Perc:0.78 ...
    # 份额是**相对值**（内部会按各自 RMS 归一化后再乘 sqrt(份额)）
"""
import argparse
import os

import numpy as np
import soundfile as sf


def parse_track(spec):
    """名字|文件[|能量份额]

    ⚠ 分隔符用 `|` 而不是 `:` —— Windows 路径自带 `D:`，用冒号会把盘符切断
      （实测报 `could not convert string to float: '/test/...'`）。
      同一个坑在本仓库已犯两次（ensemble_transcribe.py 也踩过）。
    """
    parts = spec.split("|")
    name, path = parts[0], parts[1]
    share = float(parts[2]) if len(parts) > 2 else 1.0
    return name, path, share


def main():
    ap = argparse.ArgumentParser(description="按目标能量比混合干声轨")
    ap.add_argument("--track", action="append", required=True, help="名字|文件路径[|能量份额]")
    ap.add_argument("-o", "--out", required=True, help="输出 wav")
    ap.add_argument("--ref-db", type=float, default=-16.0, help="各轨归一化参考电平 dBFS")
    ap.add_argument("--peak", type=float, default=0.98, help="输出峰值上限")
    a = ap.parse_args()

    ref = 10 ** (a.ref_db / 20.0)
    sigs, gains = [], []
    print("① 各轨：原 RMS → 增益")
    for spec in a.track:
        name, path, share = parse_track(spec)
        if not os.path.isfile(path):
            print("  跳过（不存在）：%s" % path)
            continue
        y, sr = sf.read(path, dtype="float32", always_2d=True)
        rms = float(np.sqrt((y.astype(np.float64) ** 2).mean()))
        g = (ref / max(1e-12, rms)) * np.sqrt(share)
        print("  %-8s %.2f dBFS · 份额 %.3f → %+.2f dB" % (name, 20 * np.log10(max(1e-9, rms)), share,
                                                          20 * np.log10(g)))
        sigs.append((name, y, sr))
        gains.append(g)

    if not sigs:
        raise SystemExit("没有可用轨道")
    sr = sigs[0][2]
    n = max(len(y) for _n, y, _s in sigs)          # 取**最长**并补零（短轨不该截断全曲）
    print("\n② 统一长度 %d 样本（%.1fs）" % (n, n / sr))

    mix = np.zeros((n, sigs[0][1].shape[1]), dtype=np.float64)
    for (name, y, _s), g in zip(sigs, gains):
        pad = np.zeros((n - len(y), y.shape[1]), dtype=y.dtype)
        yy = np.concatenate([y, pad], axis=0) if n > len(y) else y
        mix += yy.astype(np.float64) * g
        print("  叠加 %-8s（%+.2f dB）" % (name, 20 * np.log10(g)))

    rms = float(np.sqrt((mix ** 2).mean()))
    pk = float(np.abs(mix).max())
    print("\n③ 混音 RMS %.2f dBFS · 峰值 %.3f" % (20 * np.log10(max(1e-9, rms)), pk))
    if pk > a.peak:
        mix *= a.peak / pk
        print("   峰值超限 → 缩放到 %.3f" % float(np.abs(mix).max()))
    sf.write(a.out, mix.astype(np.float32), sr, subtype="PCM_16")
    print("\n④ 写 %s（%.1f MB）" % (a.out, os.path.getsize(a.out) / 1e6))


if __name__ == "__main__":
    main()
