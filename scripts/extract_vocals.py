# -*- coding: utf-8 -*-
"""人声提取 + **只修"段间过门"的杂音**（保住和声）。

两个来源：

  ① **差分法**（推荐，细节最真实）：若已知 `A = B + 人声`（同一首曲子的带人声版与器乐版），
     则 `人声 = A − g·B`，g 由逐通道最小二乘拟合。器乐在相减时基本抵消，人声细节完整。
  ② **AI 分离**（htdemucs 系列）：用 transcribe 侧的分离结果，间隙天然干净，但会**削掉和声**
     （实测在 3:00–3:30 比差分低 1–2.7 dB）。

**只修段间杂音**（本脚本的核心，是踩了五次坑才定下来的）：
  · 差分法在"人声整段停止的过门/间奏"处会残留去人声处理的痕迹，听感是"沙沙的杂音"；
  · 但差分信号里**"段间过门残留"与"低音量和声"的能量几乎一样**（实测 0.0472 vs 0.0478），
    单靠能量**分不开** —— 拿能量阈值去压，必然误伤和声（实测误压处掉 3.2 dB，用户一听就否）；
  · 可用的判据是**持续时长**：段间过门是**成段**的人声空缺（≥1.5s），
    而乐句换气是短间隙（<1s）→ **只压长段、短间隙一律不碰**；
  · 再给差分能量设**上限**：能量太高说明那里有实际内容（和声/弱唱）→ 保护。

实测（BGM34/BGM35，331.9s）：
    段间过门杂音 **−8.8 dB**，而 12 处人声/和声位置全部 ±0.03 dB（60–70s 那种弱唱段 0.00 dB）。

用法：
    python scripts/extract_vocals.py --a 带人声.ogg --b 器乐.ogg --out out.wav
                                   [--gap-min 1.5] [--gap-max-energy 0.15] [--depth 8.8]
                                   [--vad-from AI人声.wav] [--gate-frames auto|ai]
说明：
  · 输出 WAV（libsndfile 直接写 OGG 在千万级样本上会栈溢出，需要 ogg 时再用 scripts/to_ogg.py）；
  · 采样率沿用输入，不做重采样（工具链 venv 没有 scipy）。
"""
import argparse
import os

import numpy as np
import soundfile as sf

FL = 0.02           # 包络帧长（秒）


def load_mono_or_stereo(path):
    y, sr = sf.read(path, dtype="float32", always_2d=True)
    return y, sr


def downsample_avg(x, k):
    """k 点移动平均（廉价抗混叠），长度取整到 k 的倍数。"""
    if k < 2:
        return x
    n = (len(x) // k) * k
    if n == 0:
        return x
    if x.ndim > 1:
        return x[:n].reshape(-1, k, x.shape[1]).mean(axis=1).astype(np.float32)
    return x[:n].reshape(-1, k).mean(axis=1).astype(np.float32)


def resample_linear(x, sr_in, sr_out):
    """线性插值重采样（只用于**包络/VAD**，不用于输出音频）。

    ⚠ 为什么需要它：若 VAD 信号与主信号采样率不同（例如 AI 人声是 44.1k、原始曲是 96k），
      用同一个"帧长样本数"去分帧会让两条时间轴差 2 倍以上 —— 实测表现为
      "候选段 177 个但没一个够长"，压制因此完全失效。工具链 venv 没有 scipy，
      所以这里用 numpy 线性插值（对包络足够）。
    """
    if sr_in == sr_out:
        return x
    n_out = int(round(len(x) * sr_out / float(sr_in)))
    idx = np.linspace(0.0, len(x) - 1.0, n_out)
    i0 = np.floor(idx).astype(np.int64)
    i1 = np.minimum(i0 + 1, len(x) - 1)
    f = (idx - i0).astype(np.float32)
    if x.ndim > 1:
        f = f[:, None]
    return (x[i0] * (1.0 - f) + x[i1] * f).astype(np.float32)


def envelope(x, fl):
    m = x.mean(axis=1) if x.ndim > 1 else x
    n = (len(m) // fl) * fl
    if n == 0:
        return np.zeros(1, dtype=np.float64)
    return np.sqrt((m[:n].reshape(-1, fl) ** 2).mean(axis=1))


def median_filter(a, k):
    """numpy 版中值滤波（工具链 venv 没有 scipy）。k 为奇数。"""
    if k < 3:
        return a
    pad = k // 2
    ap = np.pad(a, pad, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(ap, k)
    return np.median(win, axis=1)


def runs(mask):
    """连续 True 段 → [(起, 止)]（帧索引）"""
    out = []
    i = 0
    n = len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def main():
    ap = argparse.ArgumentParser(description="人声提取 + 只修段间过门杂音")
    ap.add_argument("--a", required=True, help="带人声的版本（如 BGM34.ogg）")
    ap.add_argument("--b", help="器乐版本（如 BGM35.ogg）；给了就走差分法")
    ap.add_argument("--ai", help="AI 分离出的人声轨（可选，用于更准的 VAD 基准）")
    ap.add_argument("--out", required=True, help="输出 WAV")
    ap.add_argument("--gap-min", type=float, default=1.5,
                    help="只压时长 ≥ 此值的人声空缺（秒，默认 1.5 = 段间过门）")
    ap.add_argument("--gap-max-energy", type=float, default=0.15,
                    help="差分相对能量上限（超过视为有实际内容，保护）")
    ap.add_argument("--depth", type=float, default=8.8, help="压制深度 dB（默认 8.8）")
    ap.add_argument("--smooth", type=float, default=0.2, help="增益平滑系数")
    ap.add_argument("--vad-thr", type=float, default=0.025,
                    help="AI 人声包络（**中值滤波后**）低于此值才算『无人声』（默认 0.025）")
    ap.add_argument("--med-window", type=int, default=11,
                    help="VAD 包络的中值滤波窗（帧，默认 11 ≈ 220ms；抹掉短促凹陷）")
    ap.add_argument("--noise-min", type=float, default=0.03, help="差分归一化包络下限（低于视为无残留）")
    ap.add_argument("--noise-max", type=float, default=0.15, help="差分归一化包络上限（高于视为有内容）")
    ap.add_argument("--sr", type=int, default=44100,
                    help="统一处理采样率（默认 44100；0 = 保持原样）。"
                         "⚠ 阈值是在 44.1k 下校准的，换成别的采样率需用 --stats 重调")
    ap.add_argument("--stats", action="store_true", help="打印包络分位数，便于校准阈值")
    a = ap.parse_args()

    if not a.b and not a.ai:
        raise SystemExit("至少要给 --b（差分法）或 --ai（AI 分离人声）")

    print("① 载入")
    A, sr = load_mono_or_stereo(a.a)
    print("   %s: %d 样本 @ %d Hz（%.1fs）" % (os.path.basename(a.a), len(A), sr, len(A) / sr))
    # 统一采样率：阈值是在 44.1k 下校准的，采样率不同会让包络统计整体漂移
    # （实测 96k 下"空缺 ≥1.5s"的段一个都凑不出来，压制完全失效）
    if a.sr and sr != a.sr:
        k = max(1, int(round(sr / float(a.sr))))
        A = resample_linear(downsample_avg(A, k), sr / k, a.sr)
        print("   降采样 %d → %d Hz（先 %d 点均值抗混叠）" % (sr, a.sr, k))
        sr = a.sr
    fl = int(FL * sr)

    if a.b:
        B, sr2 = load_mono_or_stereo(a.b)
        print("   %s: %d 样本 @ %d Hz" % (os.path.basename(a.b), len(B), sr2))
        if a.sr and sr2 != a.sr:
            k2 = max(1, int(round(sr2 / float(a.sr))))
            B = resample_linear(downsample_avg(B, k2), sr2 / k2, a.sr)
            sr2 = a.sr
        if sr2 != sr:
            raise SystemExit("两个文件采样率不同（%d vs %d），请先统一" % (sr, sr2))
        n = min(len(A), len(B))
        A, B = A[:n], B[:n]
        # 逐通道最小二乘增益（左右分别拟合更准）
        gL = float((A[:, 0].astype(np.float64) * B[:, 0]).sum() / (B[:, 0].astype(np.float64) ** 2).sum())
        gR = float((A[:, 1].astype(np.float64) * B[:, 1]).sum() / (B[:, 1].astype(np.float64) ** 2).sum()) \
            if A.shape[1] > 1 else gL
        print("\n② 差分：增益 L %.4f · R %.4f" % (gL, gR))
        voc = (A - np.stack([gL * B[:, 0], gR * B[:, 1]], axis=1)).astype(np.float32)
    else:
        # 没有 --b：--a 本身就是人声信号（例如别处已经算好的差分，或 AI 分离结果）
        voc = A
        n = len(voc)
        print("\n② 使用 --a 作为人声信号（未做差分）")

    # VAD 基准（**必须先统一采样率**，否则两条时间轴会差 2 倍以上）
    vad_src = None
    if a.ai and os.path.isfile(a.ai):
        V, sr_v = load_mono_or_stereo(a.ai)
        if sr_v != sr:
            print("   VAD 信号重采样 %d → %d Hz（仅用于包络）" % (sr_v, sr))
            V = resample_linear(V, sr_v, sr)
        vad_src = V[:len(voc)]
        print("   VAD 基准：AI 人声")
    else:
        vad_src = voc
        print("   VAD 基准：差分信号自身")

    e_vad = envelope(vad_src, fl)
    e_vad_n = e_vad / (e_vad.max() + 1e-9)
    e_voc = envelope(voc, fl)
    e_voc_n = e_voc / (e_voc.max() + 1e-9)
    k = min(len(e_vad_n), len(e_voc_n))
    e_vad_n, e_voc_n = e_vad_n[:k], e_voc_n[:k]

    # 候选：AI 判定"持续无人声"，且差分确有能量（但不至于高到像有内容）
    #
    # ⚠ **必须先中值滤波**：AI 人声的瞬时包络在过门处是**断续**的（有零星残留），
    #   直接拿瞬时值配"段长 ≥1.5s"会一段都凑不出来 —— 实测候选 407 段却只保留 0s。
    #   中值滤波（约 220ms）会抹掉短促凹陷，只有"滤波后仍然低"才算人声空缺，
    #   这同时保证**和声不被误判**（和声能量低但持续，滤波前后都高于阈值）。
    e_med = median_filter(e_vad_n, a.med_window)
    if a.stats:
        for tag, e in (("AI 人声", e_vad_n), ("AI(滤波)", e_med), ("差分", e_voc_n)):
            qs = [1, 5, 10, 25, 50, 75, 90, 99]
            print("   %-10s 分位：%s" % (tag, " ".join("P%d=%.4f" % (q, np.percentile(e, q)) for q in qs)))
    cand = ((e_med < a.vad_thr) & (e_voc_n > a.noise_min) & (e_voc_n < a.noise_max))
    # 只保留"成段"的空缺（≥ gap-min 秒）；短间隙（乐句换气）一律不碰
    min_frames = max(1, int(a.gap_min / FL))
    keep = np.zeros_like(cand)
    segs = runs(cand)
    kept_s = dropped_s = 0.0
    kept_list = []
    for (i, j) in segs:
        if (j - i) >= min_frames:
            keep[i:j] = True
            kept_s += (j - i) * FL
            kept_list.append((i * FL, j * FL))
        else:
            dropped_s += (j - i) * FL
    print("\n③ 段间过门判据（空缺 ≥ %.1fs，能量 0.03–%.2f）" % (a.gap_min, a.gap_max_energy))
    print("   候选段 %d 个 → 保留 %.0fs（%d 段）· 丢弃短间隙 %.0fs"
          % (len(segs), kept_s, len(kept_list), dropped_s))
    if kept_list:
        print("   段间位置（前 8 个）：%s"
              % " ".join("%.0f–%.0fs" % s for s in kept_list[:8]))

    # 平滑门控
    gmin = 10 ** (-a.depth / 20.0)
    gf = np.where(keep, gmin, 1.0)
    gs = np.empty_like(gf)
    cur = 1.0
    for i in range(len(gf)):
        cur += a.smooth * (gf[i] - cur)
        gs[i] = cur
    gs = np.clip(gs, gmin, 1.0)
    idx = np.repeat(gs, fl)
    L = min(len(idx), len(voc))
    out = (voc[:L] * (idx[:L, None] if voc.ndim > 1 else idx[:L])).astype(np.float32)

    # 报告：目标段压了多少 / 其余段有没有被误伤
    def rms(x, m):
        e = envelope(x, fl)
        kk = min(len(e), len(m))
        return float(np.sqrt((e[:kk][m[:kk]] ** 2).mean())) if m[:kk].sum() else float("nan")

    a_noise, a_rest = rms(voc, keep), rms(voc, ~keep)
    b_noise, b_rest = rms(out, keep), rms(out, ~keep)
    print("\n④ 效果：段间杂音 %+.2f dB · 其余（人声/和声）%+.2f dB"
          % (20 * np.log10(max(1e-9, b_noise) / max(1e-9, a_noise)),
             20 * np.log10(max(1e-9, b_rest) / max(1e-9, a_rest))))
    print("   注意：若『其余』低于 -1 dB，说明压到了人声 —— 请调小 --depth 或调大 --gap-min")

    peak = float(np.abs(out).max())
    if peak > 0.99:
        out = out * (0.99 / peak)
    sf.write(a.out, out, sr, subtype="PCM_16")
    print("\n⑤ 写 %s（%.1f MB）" % (a.out, os.path.getsize(a.out) / 1e6))
    print("   需要 ogg 时：python scripts/to_ogg.py %s" % a.out)


if __name__ == "__main__":
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    main()
