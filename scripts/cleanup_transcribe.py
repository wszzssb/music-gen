# -*- coding: utf-8 -*-
r"""转录产物清理：**残片删除 + 抖动合并** —— 判据全部来自音频，两条都带尺子自检。

## 何时用

扒带链走到「内容修正」阶段（在 `merged_vel*` 上、`transcribe_to_song.py` 之前）。
对标 `mason369/music-to-midi` 的乐器后处理，但**判据比它硬**（实测差距见下）：

| 它的做法（`src/core/midi_generator.py`） | 问题（我们实测） | 本工具 |
|---|---|---|
| `_smooth_vibrato`：间隔 ≤100ms 且 \|Δp\|=1 → **无条件**统一到主音高 | 不看音频；钢琴**没有揉弦**，前提不成立 —— 该材料 61 处里 **39 处两个音都真实在响** | 只按音频能量判，弱的才动 |
| `_merge_close_notes`：gap ≤10ms → **无条件**合并 | 会把真实踏板重弹并掉 —— 实测 137 处里 **113 处是两个 ≥60ms 的真实重叠** | 双长音重叠（≥60ms）**一律不动** |

## 三档判据（缺一不动）

- **A 残片**：时长 <60ms，且被同音高「≥3 倍长、覆盖 ≥80%」的音覆盖 → 删（物理必然是残片）
- **B 抖动**：起点差 <50ms 且至少一个 <60ms → 量第二个起点处的 **RMS 包络跃升**
  - 跃升 <2.0（无新起音）→ 同一次发声被切碎 → 合并
  - 跃升 ≥2.0（真有起音）→ 真实重弹 → **保留**
- **C 双长音重叠**（都 ≥60ms）→ **一律不动**

⚠ 跃升尺子两条边界（都写在这里，别再踩）：
  ① RMS 包络**不区分音高** —— 它只答"这一刻有没有新能量起音"，同刻别的乐器在响会污染；
  ② 判据用「峰窗 ÷ 前背景窗」，不是「峰 ÷ 整窗中位」—— 后者会把上一音的衰减尾巴算进背景
     （合成信号实测只读出 1.92，自检当场 FAIL）。

## 用法

```bash
py = <工具链根>/.venv/Scripts/python.exe
& $py scripts\cleanup_transcribe.py <输入目录> \
    --audio-dir <分轨目录> \
    --audio-map "Piano=h6_piano.wav,Bass=h6_bass.wav,Pad=h6_other.wav,Strings=h6_other.wav" \
    [--out <输出目录>] [--selftest]
```
不给 `--out` 只出报告、不写文件；没配到音频的轨只做 A 档（A 档不需要音频）。
`--selftest` 用合成信号验证跃升尺子后可单独退出。
"""
import argparse
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓/⚠ 会崩）
import midi_file  # noqa: E402

SR = 22050
SHORT, RATIO_DUR, COVER = 0.060, 3.0, 0.80
F0, RATIO_ONSET = 0.050, 2.0
TRACKS = ("Piano", "Pad", "Strings", "Hook", "Bass", "Arp", "Glock", "Melody")


def onset_ratio(y, sr, t):
    """t 处的 RMS 包络跃升比 = 峰窗最大 / 前背景窗中位。

    峰窗 [t-5ms, t+35ms] · 背景窗 [t-70ms, t-20ms]。背景近静音时返回 99（= 确有起音）。
    """
    a, b = int(max(0, t - 0.10) * sr), int(min(len(y) / sr, t + 0.05) * sr)
    if b - a < 512:
        return -1.0
    seg = y[a:b]
    n, hop = 256, 64
    rms = np.array([np.sqrt(np.mean(seg[i:i + n] ** 2))
                    for i in range(0, len(seg) - n, hop)])
    if len(rms) < 8:
        return -1.0
    times = a / sr + (np.arange(len(rms)) * hop + n * 0.5) / sr
    bgm = (times >= t - 0.070) & (times < t - 0.020)
    pkm = (times >= t - 0.005) & (times < t + 0.035)
    if not bgm.any() or not pkm.any():
        return -1.0
    bg = float(np.median(rms[bgm]))
    pk = float(rms[pkm].max())
    return pk / bg if bg > 1e-7 else 99.0


def load_audio(path, sr_out=SR):
    w, sr = sf.read(path, always_2d=True)
    y = w.mean(axis=1).astype(np.float32)
    if sr != sr_out:
        idx = (np.arange(int(len(y) * sr_out / sr)) * sr / sr_out).astype(int)
        y = y[np.clip(idx, 0, len(y) - 1)]
    return y


def process(notes, y=None):
    """notes: [(start_s, end_s, pitch, vel)] → (新列表, 删残片数, 合并数, 明细)"""
    by_pitch = {}
    for i, (s, e, p, v) in enumerate(notes):
        by_pitch.setdefault(p, []).append((s, e, v, i))

    kill, det_a = set(), []
    for p, lst in by_pitch.items():
        for (s, e, _v, i) in lst:
            d = e - s
            if d >= SHORT:
                continue
            for (s2, e2, _v2, j) in lst:
                if j == i or (e2 - s2) < d * RATIO_DUR:
                    continue
                if min(e, e2) - max(s, s2) >= COVER * d:
                    kill.add(i)
                    det_a.append((s, p, d))
                    break

    out, merged, det_b = [], 0, []
    for p, lst in by_pitch.items():
        lst.sort()
        cur = None
        for (s, e, v, i) in lst:
            if i in kill:
                continue
            if cur is None:
                cur = [s, e, v]
                continue
            near = (s - cur[1]) < F0 and (s - cur[0]) < F0
            shortish = (cur[1] - cur[0]) < SHORT or (e - s) < SHORT
            if near and shortish and y is not None:
                r = onset_ratio(y, SR, s)
                if 0 <= r < RATIO_ONSET:
                    cur[1] = max(cur[1], e)
                    cur[2] = max(cur[2], v)
                    merged += 1
                    det_b.append((s, p, r))
                    continue
            out.append((cur[0], cur[1], p, cur[2]))
            cur = [s, e, v]
        if cur is not None:
            out.append((cur[0], cur[1], p, cur[2]))
    out.sort()
    return out, len(kill), merged, det_a, det_b


def selftest():
    """跃升尺子自检（合成信号，已知答案）。返回 bool。"""
    sr = SR
    t = np.arange(int(sr * 0.5)) / sr
    tone = np.sin(2 * np.pi * 300 * t) * np.exp(-t * 12)
    two = np.concatenate([tone, np.zeros(int(sr * 0.04)), tone])
    t2 = np.arange(int(sr * 1.04)) / sr
    one = np.sin(2 * np.pi * 300 * t2) * np.exp(-t2 * 3)
    r_two, r_one = onset_ratio(two, sr, 0.54), onset_ratio(one, sr, 0.54)
    return r_two >= RATIO_ONSET and 0 <= r_one < RATIO_ONSET


def main():
    ap = argparse.ArgumentParser(description="转录产物清理（残片删除 + 抖动合并）")
    ap.add_argument("indir", nargs="?", help="输入目录（内含 <轨名>.mid）")
    ap.add_argument("--audio-dir", default=None, help="分轨音频目录（B 档判据用）")
    ap.add_argument("--audio-map", default="",
                    help='轨名→音频文件，如 "Piano=h6_piano.wav,Bass=h6_bass.wav"')
    ap.add_argument("--out", default=None, help="输出目录（不给则只出报告）")
    ap.add_argument("--selftest", action="store_true", help="只跑尺子自检")
    a = ap.parse_args()

    if a.selftest:
        ok = selftest()
        print("尺子自检：%s" % ("PASS" if ok else "FAIL"))
        return 0 if ok else 2
    if not a.indir:
        ap.error("需要输入目录")
    if not selftest():
        raise SystemExit("尺子自检 FAIL → 拒绝输出")

    amap = {}
    for item in a.audio_map.split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            amap[k.strip()] = v.strip()
    if a.out:
        os.makedirs(a.out, exist_ok=True)

    tb = ta = td = tm = 0
    for tr in TRACKS:
        p = os.path.join(a.indir, tr + ".mid")
        if not os.path.exists(p):
            continue
        y = None
        if a.audio_dir and tr in amap:
            ap_ = os.path.join(a.audio_dir, amap[tr])
            if os.path.exists(ap_):
                y = load_audio(ap_)
        m = midi_file.import_midi(p)
        spb = 60.0 / float(m.get("bpm") or 120.0)
        for t in m.get("tracks", []):
            ns = t.get("notes") or []
            if not ns:
                continue
            rec = [(float(x[0]) * spb, (float(x[0]) + float(x[1])) * spb,
                    int(x[2]), int(x[3])) for x in ns]
            new, ndel, nmerge, det_a, det_b = process(rec, y)
            print("%-9s %5d → %5d 音 | A档删残片 %2d · B档并 %2d%s"
                  % (tr, len(rec), len(new), ndel, nmerge,
                     "" if y is not None else "（无音频 → 只做 A 档）"))
            for s, pc, d in sorted(det_a)[:3]:
                print("      A  %.3fs p%-3d 残片 %.0fms" % (s, pc, d * 1000))
            for s, pc, r in sorted(det_b)[:3]:
                print("      B  %.3fs p%-3d 起音跃升 %.2f（<%.1f → 并）" % (s, pc, r, RATIO_ONSET))
            tb += len(rec)
            ta += len(new)
            td += ndel
            tm += nmerge
            if a.out:
                t["notes"] = [[round(s / spb, 6), round((e - s) / spb, 6), pc, v]
                              for (s, e, pc, v) in new]
                midi_file.export_midi(m, os.path.join(a.out, tr + ".mid"))
    print("\n合计 %d → %d 音（A档删 %d · B档并 %d，净减 %.2f%%）"
          % (tb, ta, td, tm, 100.0 * (tb - ta) / max(tb, 1)))
    print("（%s）" % ("已写出到 %s，原文件未动" % a.out if a.out else "只出报告，没写文件"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
