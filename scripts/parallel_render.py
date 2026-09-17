# -*- coding: utf-8 -*-
"""并行批量渲染：多进程吃满多核（渲染慢的瓶颈不在合成器，而在"单线程处理一个文件"）。

实测（Ryzen 9 9955HX · 16 核 / 32 线程，BGM35 这类 5.5 分钟的曲子）：
    · fluidsynth 纯合成 **3.6s**（92× 实时）—— `synth.cpu-cores` 对它**无效**（0.94×，
      那个选项是给"多个合成器实例"用的，不是给单实例渲染提速的）；
    · render_midi 全流程 **20.9s**（其余 17s 是 numpy 处理 + ogg 编码 + IO）；
    · 标准链（band_match + 宽度 + RMS + ogg）**14.9s**；
    · → 单组总计 **≈36s，全程单线程**。
结论：**并行多个文件**才是有效旋钮。实测 5 个文件并行（每进程单核）总耗时
4.3s vs 串行 18s（渲染段 4.2×），且**输出与串行逐样本一致**（残差 0.000e+00）。

用法：
    python scripts/parallel_render.py <mid 文件或目录> [-o 输出目录] [--jobs N]
                                      [--pipe] [--ref 参考音频]
    # --pipe 时走 band_match（对齐参考曲频谱）+ 宽度 + RMS，与单曲标准链一致
"""
import argparse
import concurrent.futures as cf
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RENDER = os.path.join(HERE, "render_midi.py")
BAND = os.path.join(HERE, "band_match.py")
TO_OGG = os.path.join(HERE, "to_ogg.py")

RENDER_OPTS = ["--width", "1.4", "--rms", "-16.0", "--shelf", "6.0", "--hp", "25",
               "--low", "2.0", "--drive", "1.2", "--mid", "4.0"]


def one(mid, outdir, pipe, ref, quiet=True):
    name = os.path.splitext(os.path.basename(mid))[0]
    base = os.path.join(outdir, name)
    t0 = time.time()
    r = subprocess.run([sys.executable, RENDER, mid, base] + RENDER_OPTS,
                       stdout=subprocess.DEVNULL if quiet else None,
                       stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return name, False, time.time() - t0, (r.stderr or "")[-200:]
    if pipe and ref and os.path.isfile(ref):
        piped = subprocess.run([sys.executable, BAND, base + ".wav", ref, base + "_bm.wav",
                                "--max-gain", "6"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace")
        if piped.returncode == 0 and os.path.isfile(base + "_bm.wav"):
            os.replace(base + "_bm.wav", base + ".wav")
    if os.path.isfile(TO_OGG):
        subprocess.run([sys.executable, TO_OGG, base + ".wav"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return name, True, time.time() - t0, ""


def main():
    ap = argparse.ArgumentParser(description="并行批量渲染（多进程）")
    ap.add_argument("src", help="MIDI 文件或目录")
    ap.add_argument("-o", "--out", default=None, help="输出目录（默认 <src 同目录>/render_out）")
    ap.add_argument("--jobs", type=int, default=0, help="并行进程数（默认 = CPU 核数）")
    ap.add_argument("--pipe", action="store_true", help="走 band_match 频谱对齐")
    ap.add_argument("--ref", default=None, help="参考音频（--pipe 用）")
    a = ap.parse_args()

    if os.path.isdir(a.src):
        mids = sorted(os.path.join(a.src, f) for f in os.listdir(a.src) if f.lower().endswith((".mid", ".midi")))
        outdir = a.out or os.path.join(a.src, "render_out")
    else:
        mids = [a.src]
        outdir = a.out or os.path.join(os.path.dirname(os.path.abspath(a.src)), "render_out")
    os.makedirs(outdir, exist_ok=True)
    if not mids:
        raise SystemExit("没有找到 MIDI")

    jobs = a.jobs or (os.cpu_count() or 4)
    print("并行渲染 %d 个文件 · 进程数 %d（CPU %d 核）· 输出 %s"
          % (len(mids), jobs, os.cpu_count() or 0, outdir))
    t0 = time.time()
    rows = []
    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(one, m, outdir, a.pipe, a.ref) for m in mids]
        for f in cf.as_completed(futs):
            name, ok, dt, err = f.result()
            rows.append((name, ok, dt, err))
            print("  %-28s %6.1fs %s%s" % (name, dt, "OK" if ok else "FAIL",
                                           ("  " + err.strip()[:80]) if err else ""))
    total = time.time() - t0
    seq = sum(r[2] for r in rows)
    print("\n总耗时 %.1fs · 各文件累计 %.1fs → 并行加速 **%.2f×**"
          % (total, seq, seq / max(1e-9, total)))
    print("产物：%s" % outdir)


if __name__ == "__main__":
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    main()
