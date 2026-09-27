# -*- coding: utf-8 -*-
"""(3-1) 复核：把 BGM35_r21_补脉冲.mid 原样重渲一遍，与给定 wav 在 213-216.5s 对比

不比字节：量 RMS / 峰值 / 频带能量，看是否同量级。
产物：q3/render_full/BGM35_r21_补脉冲.wav（不碰 D:\\test\\BGM35_提取\\）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = r"D:\software\skill\music-gen\scripts"
sys.path.insert(0, SCRIPTS)
os.environ["BGM_NO_OGG"] = "1"

MID = r"D:\test\BGM35_提取\BGM35_r21_补脉冲.mid"
GIVEN = r"D:\test\BGM35_提取\BGM35_r21_补脉冲.wav"
OUTDIR = os.path.join(HERE, "render_full")
W0, W1 = 213.0, 216.5


def band_energy(x, sr, edges=(0, 200, 500, 1000, 2000, 4000, 6000, 8000, 12000, 16000, 22050)):
    n = 1 << 16
    acc = np.zeros(n // 2 + 1)
    cnt = 0
    for i in range(0, max(1, len(x) - n), n):
        acc += np.abs(np.fft.rfft(x[i:i + n] * np.hanning(n))) ** 2
        cnt += 1
    acc /= max(1, cnt)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    out = {}
    for a, b in zip(edges[:-1], edges[1:]):
        m = (f >= a) & (f < b)
        out["%d-%d" % (a, b)] = round(float(10 * np.log10(max(acc[m].sum(), 1e-20))), 2)
    return out


def load_mono(path, a=None, b=None):
    info = sf.info(path)
    s = 0 if a is None else int(round(a * info.samplerate))
    n = -1 if b is None else int(round((b - a) * info.samplerate))
    x, sr = sf.read(path, start=s, frames=n, dtype="float64", always_2d=True)
    return x.mean(axis=1), sr


def main():
    import clap_lib as C
    log = C.start_log(os.path.join(HERE, "03b_render_verify.log"))
    try:
        os.makedirs(OUTDIR, exist_ok=True)
        import render_midi
        print("# fluidsynth = %s" % render_midi.find_exe())
        print("# sf2        = %s" % render_midi.find_sf2())
        out_base = os.path.join(OUTDIR, "BGM35_r21_render_check")
        t0 = __import__("time").time()
        wav, _ = render_midi.render(MID, out_base, verbose=True, ogg=False)
        print("# 渲染完成 %.1fs → %s" % (__import__("time").time() - t0, wav))

        res = {}
        for tag, path in (("given", GIVEN), ("rerender", wav)):
            for seg, (a, b) in (("seg213-216.5", (W0, W1)), ("full", (None, None))):
                x, sr = load_mono(path, a, b)
                rms = 20 * np.log10(max(np.sqrt((x ** 2).mean()), 1e-12))
                pk = 20 * np.log10(max(np.abs(x).max(), 1e-12))
                d = dict(sr=sr, dur=round(len(x) / sr, 3), rms_dbfs=round(float(rms), 2),
                         peak_dbfs=round(float(pk), 2),
                         bands=band_energy(x, sr) if seg != "full" else {})
                res["%s_%s" % (tag, seg)] = d
                print("# %-10s %-14s sr=%d dur=%.3fs RMS=%+.2f dBFS 峰值=%+.2f dBFS"
                      % (tag, seg, sr, len(x) / sr, rms, pk))
                if seg != "full":
                    print("#   bands: %s" % d["bands"])

        g = res["given_seg213-216.5"]
        r = res["rerender_seg213-216.5"]
        print("\n# 213-216.5s 差异：RMS %+.2f dB   峰值 %+.2f dB"
              % (r["rms_dbfs"] - g["rms_dbfs"], r["peak_dbfs"] - g["peak_dbfs"]))
        print("# 逐带差（rerender - given, dB）：")
        for k in g["bands"]:
            print("#   %-10s %+6.2f" % (k, r["bands"][k] - g["bands"][k]))
        C.save_json(os.path.join(HERE, "03b_render_verify.json"),
                    dict(mid=MID, given=GIVEN, rerender=wav, window=[W0, W1], res=res))
    finally:
        log.close()


if __name__ == "__main__":
    main()
