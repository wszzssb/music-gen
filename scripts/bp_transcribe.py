# -*- coding: utf-8 -*-
"""Basic Pitch 复音转录（Spotify 开源，ONNX 后端）—— 集成转录取一个"独立来源"。

为什么要它：神经模型学的是真实演奏，**时值与起音**比"CQT 峰值 + 固定阈值"那类
几何法更接近实际；而它与 YourMT3 的错误互不相关 —— 两个来源交叉验证才有戏
（实测 BGM35：单来源最优 0.333 → 多来源集成 **0.532**，见 docs/CASE-BGM35-FINDINGS.md）。

用法：
    python scripts/bp_transcribe.py <音频> <输出.mid> [起始秒] [时长秒]
        [--onset 0.5] [--frame 0.3] [--minlen 127.7] [--fmin 32.7] [--fmax 2093.0]
环境：**独立的 bp-venv**（默认 D:\\test\\bp-venv，可用环境变量 BP_PY 覆盖），
      不污染 .venv-ml 的 torch（Basic Pitch 依赖的 tf/onnx 版本与它冲突）。
"""
import argparse
import os
import sys
import time


def main():
    ap = argparse.ArgumentParser(description="Basic Pitch 复音转录（ONNX 后端）")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("t0", nargs="?", type=float, default=0.0, help="起始秒（默认 0）")
    ap.add_argument("dur", nargs="?", type=float, default=0.0, help="时长秒（默认全曲）")
    ap.add_argument("--onset", type=float, default=0.5)
    ap.add_argument("--frame", type=float, default=0.3)
    ap.add_argument("--minlen", type=float, default=127.70)
    ap.add_argument("--fmin", type=float, default=32.7)
    ap.add_argument("--fmax", type=float, default=2093.0)
    a = ap.parse_args()
    # 面板守卫（硬形式）：没在跑就先拉起来 —— 见 scripts/studio_guard.py 顶部那段。
    try:
        import studio_guard
        studio_guard.ensure_panel()
    except Exception as _e:                                        # noqa: BLE001
        print('  （面板守卫跳过：%s）' % str(_e)[:80])

    import numpy as np
    import soundfile as sf

    use = a.src
    tmp = None
    if a.t0 or a.dur:                     # 切片走临时 WAV（predict 只吃文件路径）
        y, sr = sf.read(a.src, dtype="float32", always_2d=True)
        if y.ndim > 1:
            y = y.mean(axis=1)
        s0 = int(a.t0 * sr)
        s1 = int((a.t0 + a.dur) * sr) if a.dur else len(y)
        y = y[s0:s1]
        tmp = os.path.join(os.path.dirname(os.path.abspath(a.dst)),
                           "_bp_slice_%d_%d.wav" % (int(a.t0), int(a.dur)))
        sf.write(tmp, y, sr)
        use = tmp
        print("切片 %.1fs–%.1fs（%.1fs）" % (a.t0, a.t0 + a.dur if a.dur else 0, len(y) / sr))

    from basic_pitch.inference import predict
    print("转录中 …（onset=%.2f frame=%.2f minlen=%.1fms fmin=%.1f fmax=%.1f）"
          % (a.onset, a.frame, a.minlen, a.fmin, a.fmax))
    t = time.time()
    _model_out, midi_data, note_events = predict(
        use,
        onset_threshold=a.onset,
        frame_threshold=a.frame,
        minimum_note_length=a.minlen,
        minimum_frequency=a.fmin,
        maximum_frequency=a.fmax,
        multiple_pitch_bends=False,
    )
    dt = time.time() - t
    midi_data.write(a.dst)
    n = len(note_events)
    print("完成 %.1fs · 音符 %d · 写 %s（%d 字节）"
          % (dt, n, a.dst, os.path.getsize(a.dst)))
    if n:
        st = np.array([e[0] for e in note_events])
        en = np.array([e[1] for e in note_events])
        pi = np.array([e[2] for e in note_events])
        am = np.array([e[3] for e in note_events])
        du = en - st
        print("  起音 %.2f–%.2fs · 时值 中位 %.3fs P10 %.3f P90 %.3f"
              % (st.min(), st.max(), np.median(du), np.percentile(du, 10), np.percentile(du, 90)))
        print("  音高 %d–%d（%d 种）· 力度 中位 %.0f"
              % (pi.min(), pi.max(), len(set(pi)), np.median(am)))
    if tmp and os.path.isfile(tmp):
        os.remove(tmp)


if __name__ == "__main__":
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    main()
