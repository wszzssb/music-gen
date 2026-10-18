# -*- coding: utf-8 -*-
r"""octave_corrector.py —— **低音八度校正器**（学习型判别器 v1.0，2026-10-18 新建）

## 一句话结论（先看这个）

**在这把尺子上，"学习"追不上"换聚合方式"**：现有 pyin 硬规则（0.1s 桶中位）与帧级众数
在 1325 个有参考音上只差 **27 个（2.0%）**，而 pyin 判改的 650 个音里众数 **100% 同意**
—— 也就是说规则已经基本等于帧级最优。本工具量出来的真实增益是：

| 臂 | 做法 | 相对 pyin（四首留一曲 + BGM29 集成，双尺子） |
|---|---|---|
| `pyin` | 桶中位、只改正好 ±12/±24 | 基线 |
| `mode` | **帧级众数**（改用帧直方图，不用桶中位） | **每首、每把尺子 +0.0~+0.9 点**（集成 +0.8/+0.9） |
| `learned` | 5 类小 MLP（52 特征），标签=pyin 规则 | ≈ pyin（96% 位移准确）；集成上 +0.5，BGM35 上 −1.1 |
| `hybrid` | **规则优先，模型只补规则弃权的近八度**（且须与众数一致） | **≥ pyin，每首都不亏**；假修正率 0.00% |

学习模型的两个硬边界（实测，别归因给调参）：
① **`+12` 学不出来**：全库 8 个 `+12` 有 6 个在 BGM35，按曲留出时训练集只剩 2 个正例
   ⇒ BGM35 的 13 个漏改全是它。② 用帧级众数当标签训练，模型就**逐点复刻 mode**
   （BGM29 1.000 / BGM35 0.997），没有任何增量 —— 说明它的上限由标签上限决定。

## 这工具干什么

给低音候选音（时间 t、音高 p、所在 bass 分轨）判 `0 / ±12 / ±24`，输出仍是 MIDI；
五条臂 `base / pyin / mode / learned / hybrid` 可在**同一批留出曲**上做三组对照。
现有硬规则在 `bass_ensemble.py --octave-ref`；本工具 `--arm pyin` 与它**逐音等价**
（BGM29 集成实测：408 改 / 1122 音 / 去重后 973 音，与原工具完全同数）。

## 纪律（写死在这里）

1. **按曲划分**：`train` 只做 leave-one-song-out，绝不按音随机分（同曲相邻音高度相关）。
2. **标签与验收不同源**：标签=pyin 音符级规则；验收=帧级一致率（`transcribe_audit` 同口径，
   自检与它逐项对数）+ **CREPE 独立尺子**（`--f0 crepe`）。两把尺子都要看。
3. **只数改了多少音不算数**：看一致率、假修正率（pyin 判"本来就对"里被改的比例）。
4. **不原地改**：`apply` 只写新文件。

## 两个口径坑

- `_extract` 里 **BGM01zz 与 BGM01 的 MIDI 逐字节相同**（md5 一致）⇒ 按曲划分会泄漏，
  默认只用 `BGM01`。**BGM36 的 bass 分轨几乎没内容**（pyin 有效帧 3.4%，15 个音 0 参考）。
- 用 `.venv-ml` 跑（librosa/pyin + CREPE 的 torch；`.venv` 是 3.14，ABI 不兼容）。

## 命令（照抄）

```powershell
$ml = 'D:\software\skill\music-gen\.venv-ml\Scripts\python.exe'
& $ml scripts\octave_corrector.py prepare --data D:\test\_tmp\octave\data      # ① 建集（pyin 缓存）
& $ml scripts\octave_corrector.py stats   --data D:\test\_tmp\octave\data      # ② 标签分布/可改空间
& $ml scripts\octave_corrector.py train   --data D:\test\_tmp\octave\data --out m.npz --cv-apply cv
& $ml scripts\octave_corrector.py arms    --dir cv --data D:\test\_tmp\octave\data   # 四/五臂双尺子
& $ml scripts\octave_corrector.py apply   --mid in.mid --stem bass.wav --arm hybrid --model m.npz --out out.mid
& $ml scripts\octave_corrector.py diff    --mid in.mid --stem bass.wav --model m.npz  # 逐音假修正率
& $ml scripts\octave_corrector.py audit   --stem bass.wav --mid out.mid --f0 crepe    # 独立尺子
```
"""
import argparse
import glob
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import midi_file  # noqa: E402

SR = 22050
HOP = 512
GRID = 0.1
FMIN, FMAX = 40.0, 300.0
FRAME_LENGTH = 2048
MIN_PROB = 0.20          # 与 bass_ensemble --min-prob 默认一致
SHIFT_LO, SHIFT_HI = 12, 108   # 与 bass_ensemble 的 `12 <= p - dd <= 108` 一致

DEFAULT_SONGS = [
    # id, bass 分轨, 低音转录（基准 = 要校正的那一份）
    ('BGM01', r'_extract\BGM01\stems\htdemucs_6s\source\bass.wav',
     r'_extract\BGM01\ymt3\BGM01_stems\BGM01_Bass.mid'),
    ('BGM29', r'_extract\BGM29\stems\htdemucs_6s\source\bass.wav',
     r'_extract\BGM29\ymt3\BGM29_stems\BGM29_Bass.mid'),
    ('BGM35', r'_extract\BGM35\stems\htdemucs_6s\source\bass.wav',
     r'_extract\BGM35\ymt3\BGM35_stems\BGM35_Bass.mid'),
    ('BGM36', r'_extract\BGM36\stems\htdemucs_6s\source\bass.wav',
     r'_extract\BGM36\ymt3\BGM36_stems\BGM36_Bass.mid'),
    ('已至', r'_extract\已至\stems\htdemucs_6s\source\bass.wav',
     r'_extract\已至\ymt3\已至_stems\已至_Bass.mid'),
]
# 交接文档点名的重复组：BGM01zz 与 BGM01 的 midi md5 相同（本工具只留 BGM01）
DUP_GROUP = [('BGM01', 'BGM01zz')]

# ── 特征名（顺序即 X 的列序）────────────────────────────────────────────────
FEATURES = [
    'pitch', 'dur_s', 'vel', 't_frac',
    'dd_pyin', 'has_ref', 'n_buckets', 'n_buckets_ref', 'n_frames', 'n_valid', 'frac_valid',
    'frac_eq_0', 'frac_eq_m12', 'frac_eq_p12', 'frac_eq_m24', 'frac_eq_p24', 'frac_eq_other',
    'mode_shift', 'mode_frac',
    'med_rel', 'std_rel', 'min_rel', 'max_rel', 'frac_abs_lt_half', 'frac_abs_lt_1p5',
    'vprob_mean', 'vprob_min', 'frac_vprob_gt50',
    'first_rel', 'last_rel',
    'bucket_vote_m12', 'bucket_vote_0', 'bucket_vote_p12', 'bucket_vote_other', 'bucket_std',
    'prev_diff', 'next_diff', 'gap_prev', 'gap_next',
    'a_logE_p', 'a_logE_phalf', 'a_logE_p2', 'a_r_phalf', 'a_r_p2',
    'a_hs_m12', 'a_hs_p12', 'a_hs_ratio_m12', 'a_hs_ratio_p12',
    'a_odd_even', 'a_flatness', 'a_low_ratio', 'a_rms_db',
]


# ── 基础读取 ────────────────────────────────────────────────────────────────
def song_paths(sid):
    for a, b, c in DEFAULT_SONGS:
        if a == sid:
            return os.path.join(REPO, b), os.path.join(REPO, c)
    raise SystemExit('未知曲目：%s（可选：%s）' % (sid, [s[0] for s in DEFAULT_SONGS]))


def read_stem(path):
    import soundfile as sf
    import librosa
    y, sr = sf.read(path, dtype='float32', always_2d=True)
    y = y.mean(axis=1)
    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    return np.ascontiguousarray(y, dtype=np.float32)


def pyin_f0(y):
    import librosa
    f0, _vf, vprob = librosa.pyin(y, fmin=FMIN, fmax=FMAX, sr=SR,
                                  frame_length=FRAME_LENGTH, hop_length=HOP)
    return f0, vprob


def f0_to_pitch(f0):
    """连续 f0 → 最近半音（float）。无效帧为 NaN。"""
    with np.errstate(divide='ignore', invalid='ignore'):
        return 69.0 + 12.0 * np.log2(np.asarray(f0, dtype=np.float64) / 440.0)


def load_notes(mid_path, track='Bass'):
    m = midi_file.import_midi(mid_path)
    spb = 60.0 / float(m.get('bpm') or 120.0)
    out = []
    names = [tr.get('name') for tr in m['tracks']]
    if track not in names:
        raise SystemExit('MIDI 里没有轨 %r，实际轨名：%s' % (track, names))
    for tr in m['tracks']:
        if tr.get('name') != track:
            continue
        for (st, du, p, v) in tr['notes']:
            out.append([float(st) * spb, float(du) * spb, int(p), int(v)])
    out.sort(key=lambda x: (x[0], x[2]))
    return out, spb, mid_path


# ── pyin 参考表（**逐字复刻 bass_ensemble --octave-ref**）──────────────────
class RefCtx:
    def __init__(self, f0, vprob, min_prob=MIN_PROB):
        self.f0 = np.asarray(f0, dtype=np.float64)
        self.vprob = np.asarray(vprob, dtype=np.float64)
        n = len(self.f0)
        self.n_frames = n
        self.valid = np.isfinite(self.f0) & (self.vprob >= min_prob)
        with np.errstate(divide='ignore', invalid='ignore'):
            self.pitch = 69.0 + 12.0 * np.log2(np.where(self.f0 > 0, self.f0, np.nan) / 440.0)
        self.frame_pitch = np.where(np.isfinite(self.pitch), np.round(self.pitch), np.nan)
        # 帧 → 0.1s 桶（与 bass_ensemble 的 int(i*512/22050/GRID) 完全一致）
        idx = np.arange(n)
        self.bucket_of = (idx.astype(np.float64) * HOP / SR / GRID).astype(np.int64)
        self.buckets = defaultdict(list)
        for i in range(n):
            if self.valid[i]:
                self.buckets[int(self.bucket_of[i])].append(int(self.frame_pitch[i]))
        # 逐桶中位（int(np.median(...)) 的截断语义也要一致）
        self.ref_pitch = {g: int(np.median(v)) for g, v in self.buckets.items() if len(v) >= 1}
        self.bucket_frames = defaultdict(list)
        for i in range(n):
            self.bucket_frames[int(self.bucket_of[i])].append(i)

    def rule(self, t, dur, p):
        """→ (ref_median or None, cand 列表)。复刻 bass_ensemble 的边界写法。"""
        g0 = int(t / GRID)
        g1 = int((t + max(dur, 0.05)) / GRID)
        cand = [self.ref_pitch[g] for g in range(g0, g1 + 1) if g in self.ref_pitch]
        if not cand:
            return None, []
        return int(np.median(cand)), cand


# ── 音频特征 ────────────────────────────────────────────────────────────────
def band_max(mag, freqs, f, rel=0.03):
    if not np.isfinite(f) or f < 30.0 or f > SR / 2.0 - 100:
        return 0.0
    lo, hi = f * (1.0 - rel), f * (1.0 + rel)
    a, b = np.searchsorted(freqs, lo), np.searchsorted(freqs, hi)
    if b <= a:
        return 0.0
    return float(mag[a:b].max())


def harmonic_sum(mag, freqs, f, nh=6):
    if not np.isfinite(f) or f < 30.0:
        return 0.0
    s = 0.0
    for h in range(1, nh + 1):
        s += band_max(mag, freqs, f * h) / h
    return s


def audio_features(y, t, dur, p, total_rms):
    n = 4096
    center = t * SR + min(max(dur * 0.5, 0.03), 0.15) * SR
    i0 = int(round(center - n / 2.0))
    seg = np.zeros(n, dtype=np.float64)
    a, b = max(0, i0), min(len(y), i0 + n)
    if b > a:
        seg[a - i0:b - i0] = y[a:b]
    w = np.hanning(n)
    mag = np.abs(np.fft.rfft(seg * w, 8192)) + 1e-12
    freqs = np.fft.rfftfreq(8192, 1.0 / SR)
    fp = 440.0 * 2.0 ** ((p - 69.0) / 12.0)
    eps = 1e-12
    e_p = max(band_max(mag, freqs, fp), eps)
    e_half = max(band_max(mag, freqs, fp / 2.0), eps)
    e_2p = max(band_max(mag, freqs, fp * 2.0), eps)
    hs_m12 = max(harmonic_sum(mag, freqs, fp / 2.0), eps)
    hs_p12 = max(harmonic_sum(mag, freqs, fp * 2.0), eps)
    hs_p = max(harmonic_sum(mag, freqs, fp), eps)
    # 奇偶谐波比（在候选音高 p 上）
    odd = sum(band_max(mag, freqs, fp * h) for h in (1, 3, 5))
    even = sum(band_max(mag, freqs, fp * h) for h in (2, 4, 6))
    # 谱平坦度（20–2000Hz）
    band = mag[(freqs >= 20) & (freqs <= 2000)]
    flat = float(np.exp(np.mean(np.log(band + 1e-12))) / (np.mean(band) + 1e-12))
    low = float(np.sum(mag[freqs < 120] ** 2) / (np.sum(mag[freqs <= 2000] ** 2) + 1e-12))
    rms = float(np.sqrt(np.mean(seg ** 2)) + 1e-12)
    return [
        np.log10(e_p), np.log10(e_half), np.log10(e_2p),
        np.log10(e_p / e_half), np.log10(e_p / e_2p),
        np.log10(hs_m12), np.log10(hs_p12),
        np.log10(hs_p / hs_m12), np.log10(hs_p / hs_p12),
        np.log10((odd + eps) / (even + eps)),
        flat, low, 20 * np.log10(rms / max(total_rms, 1e-9)),
    ]


# ── 单曲数据集 ──────────────────────────────────────────────────────────────
def note_features(ctx, y, notes, total_rms, min_prob=MIN_PROB):
    X, meta, ys, extra = [], [], [], []
    n_notes = len(notes)
    total_t = max([n[0] + n[1] for n in notes] or [1.0])
    for k, (t, dur, p, vel) in enumerate(notes):
        ref, cand = ctx.rule(t, dur, p)
        dd = (p - ref) if ref is not None else 0
        g0 = int(t / GRID)
        g1 = int((t + max(dur, 0.05)) / GRID)
        sheet = []
        for g in range(g0, g1 + 1):
            sheet.extend(ctx.bucket_frames.get(g, []))
        frames = np.asarray(sheet, dtype=np.int64)
        valid = frames[ctx.valid[frames]] if len(frames) else np.asarray([], dtype=np.int64)
        n_valid = int(len(valid))
        rel_pitch = np.round(ctx.pitch[valid]) - p if n_valid else np.asarray([])
        rel_cont = (ctx.pitch[valid] - p) if n_valid else np.asarray([])
        cnt = Counter(rel_pitch.astype(int).tolist()) if n_valid else Counter()
        frac = (lambda k: cnt.get(k, 0) / max(1, n_valid))
        other = 1.0 - sum(frac(s) for s in (0, -12, 12, -24, 24))
        if n_valid:
            official = [0, -12, 12, -24, 24]
            mode_shift = max(official, key=lambda s: (cnt.get(s, 0), -abs(s)))
            mode_frac = cnt.get(mode_shift, 0) / n_valid
            std_rel = float(np.std(rel_cont))
            med_rel = float(np.median(rel_cont))
            min_rel, max_rel = float(rel_cont.min()), float(rel_cont.max())
            frac_half = float(np.mean(np.abs(rel_cont) < 0.5))
            frac_1p5 = float(np.mean(np.abs(rel_cont) < 1.5))
            vp = ctx.vprob[valid]
            vprob_mean, vprob_min = float(np.mean(vp)), float(np.min(vp))
            frac_vp = float(np.mean(vp > 0.5))
            first_rel = int(rel_pitch[0])
            last_rel = int(rel_pitch[-1])
        else:
            mode_shift, mode_frac, std_rel, med_rel = 0, 0.0, 99.0, 99.0
            min_rel = max_rel = 99.0
            frac_half = frac_1p5 = 0.0
            vprob_mean = vprob_min = frac_vp = 0.0
            first_rel = last_rel = 0
        # 桶级投票（规则用的就是这些桶的中位）
        bvotes = Counter()
        for g in range(g0, g1 + 1):
            if g in ctx.ref_pitch:
                bvotes[ctx.ref_pitch[g] - p] += 1
        n_b = sum(bvotes.values())
        bucket_std = float(np.std([k for k, c in bvotes.items() for _ in range(c)])) if n_b else 99.0
        prev = notes[k - 1] if k > 0 else None
        nxt = notes[k + 1] if k + 1 < n_notes else None
        prev_diff = float(p - prev[2]) if prev else 0.0
        next_diff = float(nxt[2] - p) if nxt else 0.0
        gap_prev = float(t - (prev[0] + prev[1])) if prev else 99.0
        gap_next = float(nxt[0] - (t + dur)) if nxt else 99.0
        af = audio_features(y, t, dur, p, total_rms)
        row = [
            p, dur, vel, t / total_t,
            dd, 1.0 if ref is not None else 0.0,
            g1 - g0 + 1, n_b, len(frames), n_valid,
            n_valid / max(1, len(frames)),
            frac(0), frac(-12), frac(12), frac(-24), frac(24), other,
            mode_shift, mode_frac,
            med_rel, std_rel, min_rel, max_rel, frac_half, frac_1p5,
            vprob_mean, vprob_min, frac_vp,
            first_rel, last_rel,
            bvotes.get(-12, 0), bvotes.get(0, 0), bvotes.get(12, 0),
            n_b - sum(v for k, v in bvotes.items() if k in (0, -12, 12, 24, -24)), bucket_std,
            prev_diff, next_diff, gap_prev, gap_next,
        ] + af
        X.append(row)
        meta.append([t, dur, p, vel, ref if ref is not None else -1])
        # 标签：pyin 规则会施加的位移（0 也代表"本来就对"与"非八度差异"）
        ys.append(-dd if (ref is not None and dd in (12, -12, 24, -24)
                          and SHIFT_LO <= p - dd <= SHIFT_HI) else 0)
        # 参考：帧级众数（用于诊断"pyin 中位规则本身错在哪"，不作训练标签）
        extra.append(mode_shift if ref is not None else 0)
    return (np.asarray(X, dtype=np.float32), np.asarray(ys, dtype=np.int16),
            np.asarray(meta, dtype=np.float32), np.asarray(extra, dtype=np.int16))


def cmd_prepare(a):
    data_dir = os.path.abspath(a.data)
    os.makedirs(data_dir, exist_ok=True)
    cache_dir = os.path.join(data_dir, 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    songs = a.only.split(',') if a.only else [s[0] for s in DEFAULT_SONGS]
    for sid in songs:
        stem, mid = song_paths(sid)
        if not os.path.isfile(stem) or not os.path.isfile(mid):
            print('[跳过] %s 缺文件：%s / %s' % (sid, stem, mid))
            continue
        st = os.stat(stem)
        key = hashlib.md5(('%s|%d|%d|%s' % (stem, st.st_size, int(st.st_mtime),
                                           (FMIN, FMAX, SR, FRAME_LENGTH, HOP))).encode()).hexdigest()[:16]
        cpath = os.path.join(cache_dir, '%s_%s.npz' % (sid, key))
        # ⚠ 音频必须**每次都读**：缓存分支若复用上一首的 `y`，特征会串到别的曲子上
        #   （这种错不会被任何断言抓住，只会让读数"看起来正常"）。
        y = read_stem(stem)
        if os.path.exists(cpath) and not a.force:
            d = np.load(cpath)
            f0, vprob = d['f0'], d['vprob']
            print('[缓存] %s f0 %d 帧' % (sid, len(f0)))
        else:
            print('[pyin] %s …' % sid, flush=True)
            f0, vprob = pyin_f0(y)
            np.savez_compressed(cpath, f0=f0, vprob=vprob)
            print('       f0 %d 帧（有效 %.1f%%）'
                  % (len(f0), 100 * np.mean(np.isfinite(f0) & (vprob >= MIN_PROB))), flush=True)
        total_rms = float(np.sqrt(np.mean(y ** 2)) + 1e-12)
        notes, spb, _ = load_notes(mid)
        ctx = RefCtx(f0, vprob)
        X, ys, meta, extra = note_features(ctx, y, notes, total_rms)
        out = os.path.join(data_dir, '%s.npz' % sid)
        np.savez_compressed(out, X=X, y=ys, meta=meta, mode=extra,
                            feat=np.asarray(FEATURES), song=np.asarray([sid]),
                            midi=np.asarray([mid]), stem=np.asarray([stem]), bpm=np.asarray([spb]))
        print('[数据] %s → %s：%d 音 · 有参考 %d · 规则改 %d'
              % (sid, out, len(ys), int((meta[:, 4] >= 0).sum()), int((ys != 0).sum())), flush=True)


# ── 统计 ────────────────────────────────────────────────────────────────────
def cmd_stats(a):
    rows = load_data(a.data)
    all_y, all_mode, all_ref = [], [], []
    print('%-8s %5s %6s %6s %6s %6s %6s %6s' % (
        '曲', '音数', '有参考', '规则改', '众数改', '两规则异', 'pyin漏改', '±24'))
    for r in rows:
        m = r['meta']
        has = m[:, 4] >= 0
        y, mode = r['y'], r['mode']
        diff = has & (y != mode)
        print('%-8s %5d %6d %6d %6d %6d %6d %6d'
              % (r['song'], len(y), int(has.sum()), int(((y != 0) & has).sum()),
                 int(((mode != 0) & has).sum()), int(diff.sum()),
                 int((diff & (y == 0)).sum()),
                 int((np.abs(y) == 24).sum())))
        all_y.append(y[has]); all_mode.append(mode[has]); all_ref.append(has)
    y = np.concatenate(all_y) if all_y else np.asarray([])
    mode = np.concatenate(all_mode) if all_mode else np.asarray([])
    print('-' * 78)
    print('合计有参考 %d 音' % len(y))
    print('  pyin 规则位移分布：%s' % dict(Counter(y.tolist())))
    print('  帧级众数位移分布：%s' % dict(Counter(mode.tolist())))
    d = (y != mode)
    print('  两条规则不一致 %d 音（%.1f%%）'
          % (int(d.sum()), 100.0 * d.sum() / max(1, len(y))))
    # 只在"pyin 判改"的音里，众数说"别改"的比例 —— 假修正的上界
    chg = (y != 0)
    if chg.sum():
        print('  pyin 判改的 %d 音里：众数同意 %d（%.1f%%）· 众数说保持 %d（%.1f%%）· 众数改别的 %d'
              % (int(chg.sum()), int((chg & (y == mode)).sum()),
                 100.0 * (chg & (y == mode)).sum() / chg.sum(),
                 int((chg & (mode == 0)).sum()),
                 100.0 * (chg & (mode == 0)).sum() / chg.sum(),
                 int((chg & (mode != 0) & (mode != y)).sum())))


def load_data(data_dir):
    rows = []
    for fn in sorted(os.listdir(data_dir)):
        if not fn.endswith('.npz'):
            continue
        d = np.load(os.path.join(data_dir, fn), allow_pickle=False)
        rows.append({'song': str(d['song'][0]), 'X': d['X'], 'y': d['y'],
                     'meta': d['meta'], 'mode': d['mode'], 'midi': str(d['midi'][0]),
                     'stem': str(d['stem'][0]), 'bpm': float(d['bpm'][0])})
    if not rows:
        raise SystemExit('数据集为空：%s（先跑 prepare）' % data_dir)
    return rows


# ══════════════════════════════════════════════════════════════════════════
# 学习型判别器（纯 numpy 小 MLP；不依赖 torch/sklearn，主 venv 也能推理）
# ══════════════════════════════════════════════════════════════════════════
CLASSES = np.asarray([-24, -12, 0, 12, 24], dtype=np.int16)


def to_class(y):
    """位移 → 类别下标。超出 ±24 的（不该有）归 0。"""
    idx = np.full(len(y), 2, dtype=np.int64)
    for k, s in enumerate(CLASSES):
        idx[np.asarray(y) == s] = k
    return idx


class MLP:
    def __init__(self, n_in, n_hidden, n_out, seed=0):
        rng = np.random.RandomState(seed)
        self.W1 = (rng.randn(n_in, n_hidden) * np.sqrt(2.0 / n_in)).astype(np.float64)
        self.b1 = np.zeros(n_hidden)
        self.W2 = (rng.randn(n_hidden, n_out) * np.sqrt(2.0 / n_hidden)).astype(np.float64)
        self.b2 = np.zeros(n_out)

    def forward(self, X):
        h = np.tanh(X @ self.W1 + self.b1)
        return h, h @ self.W2 + self.b2

    def probs(self, X):
        _, z = self.forward(X)
        z = z - z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=1, keepdims=True)

    def state(self):
        return {'W1': self.W1, 'b1': self.b1, 'W2': self.W2, 'b2': self.b2}


def train_mlp(X, y, w, n_out=5, hidden=32, epochs=600, lr=0.01, l2=1e-4, seed=0):
    """加权交叉熵 + Adam。X 已标准化。"""
    m = MLP(X.shape[1], hidden, n_out, seed=seed)
    m1 = [np.zeros_like(p) for p in (m.W1, m.b1, m.W2, m.b2)]
    m2 = [np.zeros_like(p) for p in (m.W1, m.b1, m.W2, m.b2)]
    b1, b2, eps = 0.9, 0.999, 1e-8
    Y = np.eye(n_out)[y]
    w = np.asarray(w, dtype=np.float64)
    w = w / w.sum()
    for ep in range(1, epochs + 1):
        h, z = m.forward(X)
        z = z - z.max(axis=1, keepdims=True)
        e = np.exp(z)
        p = e / e.sum(axis=1, keepdims=True)
        dz = (p - Y) * w[:, None]
        gW2 = h.T @ dz + l2 * m.W2
        gb2 = dz.sum(axis=0)
        dh = (dz @ m.W2.T) * (1.0 - h ** 2)
        gW1 = X.T @ dh + l2 * m.W1
        gb1 = dh.sum(axis=0)
        for k, g in enumerate((gW1, gb1, gW2, gb2)):
            m1[k] = b1 * m1[k] + (1 - b1) * g
            m2[k] = b2 * m2[k] + (1 - b2) * g * g
            ghat = (m1[k] / (1 - b1 ** ep)) / (np.sqrt(m2[k] / (1 - b2 ** ep)) + eps)
            if k == 0:
                m.W1 -= lr * ghat
            elif k == 1:
                m.b1 -= lr * ghat
            elif k == 2:
                m.W2 -= lr * ghat
            else:
                m.b2 -= lr * ghat
    return m


def class_weights(y_idx, n_out=5, power=0.5):
    cnt = np.bincount(y_idx, minlength=n_out).astype(np.float64)
    w = np.power(np.maximum(cnt, 1.0), -power)
    w = w / w.mean()
    return w[y_idx]


def fit_model(X, y, w, hidden=32, epochs=600, seed=0):
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd < 1e-6] = 1.0
    Xs = np.clip((X - mu) / sd, -8, 8)
    yi = to_class(y)
    ww = np.asarray(w, dtype=np.float64) * class_weights(yi)
    m = train_mlp(Xs, yi, ww, n_out=5, hidden=hidden, epochs=epochs, seed=seed)
    return {'model': m, 'mu': mu, 'sd': sd}


def predict_shift(bundle, X):
    m, mu, sd = bundle['model'], bundle['mu'], bundle['sd']
    Xs = np.clip((X - mu) / sd, -8, 8)
    p = m.probs(Xs)
    idx = p.argmax(axis=1)
    return CLASSES[idx], p


# ── 训练 / 留一曲交叉验证 ──────────────────────────────────────────────────
def cmd_train(a):
    rows = load_data(a.data)
    for r in rows:
        r['has'] = r['meta'][:, 4] >= 0
        r['w'] = np.maximum(1.0, r['meta'][:, 1] * SR / HOP)       # 帧数当权重
    lab_key = 'y' if a.label == 'pyin' else 'mode'
    for r in rows:
        r['lab'] = r[lab_key]
    songs = [r['song'] for r in rows]
    print('=' * 96)
    print('按曲留一（leave-one-song-out）：%d 首 %s' % (len(rows), songs))
    print('标签=%s；只训练"有参考"的音；权重=该音覆盖的帧数' % a.label)
    print('-' * 96)
    print('%-8s %5s %6s | %7s %7s %7s | %8s %8s'
          % ('留出曲', '样本', '类别0', '总准确', '保持率', '改对率', '假修正率', '帧权重准'))
    all_pred = {}
    for i, r in enumerate(rows):
        if not r['has'].any():
            print('%-8s %5d %6d | （无参考样本：不参与留一曲）' % (r['song'], 0, 0))
            continue
        train = [q for j, q in enumerate(rows) if j != i and q['has'].any()]
        Xtr = np.concatenate([q['X'][q['has']] for q in train])
        ytr = np.concatenate([q['lab'][q['has']] for q in train])
        wtr = np.concatenate([q['w'][q['has']] for q in train])
        b = fit_model(Xtr, ytr, wtr, hidden=a.hidden, epochs=a.epochs, seed=a.seed)
        Xte = r['X'][r['has']]
        yte = r['lab'][r['has']]
        wte = r['w'][r['has']]
        pred, _ = predict_shift(b, Xte)
        all_pred[r['song']] = pred
        acc = float(np.mean(pred == yte))
        keep = yte == 0
        chg = ~keep
        keep_rate = float(np.mean(pred[keep] == 0)) if keep.any() else float('nan')
        chg_rate = float(np.mean(pred[chg] == yte[chg])) if chg.any() else float('nan')
        false_fix = float(np.mean(pred[keep] != 0)) if keep.any() else float('nan')
        facc = float(np.sum(wte * (pred == yte)) / np.sum(wte)) if wte.sum() else float('nan')
        print('%-8s %5d %6d | %7.3f %7.3f %7.3f | %8.3f %8.3f'
              % (r['song'], len(yte), int(keep.sum()), acc, keep_rate, chg_rate,
                 false_fix, facc))
        # 留出曲 → 四个臂（base / pyin / mode / learned）写成 MIDI，供 audit 用
        if a.cv_apply:
            outd = os.path.join(os.path.abspath(a.cv_apply), r['song'])
            write_arms(r, pred, outd)
        if a.save_folds:
            os.makedirs(os.path.abspath(a.save_folds), exist_ok=True)
            save_bundle(b, os.path.join(os.path.abspath(a.save_folds), '%s_model.npz' % r['song']))
    # 汇总
    print('-' * 96)
    tot_n = tot_ok = 0
    for r in rows:
        if r['song'] not in all_pred:
            continue
        y = r['lab'][r['has']]
        pr = all_pred[r['song']]
        tot_n += len(y)
        tot_ok += int((pr == y).sum())
    print('留一曲合计：%d 音 · 位移准确 %.3f' % (tot_n, tot_ok / max(1, tot_n)))

    # 全量训练 → 部署模型
    X = np.concatenate([r['X'][r['has']] for r in rows])
    y = np.concatenate([r['lab'][r['has']] for r in rows])
    w = np.concatenate([r['w'][r['has']] for r in rows])
    b = fit_model(X, y, w, hidden=a.hidden, epochs=a.epochs, seed=a.seed)
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    save_bundle(b, out, songs=[r['song'] for r in rows])
    print('部署模型（全部 %d 首训练）→ %s' % (len(rows), out))


def save_bundle(b, out, songs=None):
    m = b['model']
    np.savez_compressed(out, W1=m.W1, b1=m.b1, W2=m.W2, b2=m.b2, mu=b['mu'], sd=b['sd'],
                        feat=np.asarray(FEATURES), classes=CLASSES,
                        songs=np.asarray(songs if songs is not None else []))


def write_arms(r, pred, out_dir):
    """把一曲的四个对照臂写成 MIDI（只换 Bass 轨；时间轴按原 MIDI 的 bpm）。"""
    os.makedirs(out_dir, exist_ok=True)
    notes = [[float(x[0]), float(x[1]), int(x[2]), int(x[3])] for x in r['meta']]
    base = os.path.basename(r['midi'])
    stem = os.path.splitext(base)[0]
    arms = {'base': [0] * len(notes),
            'pyin': [int(s) for s in r['y']],
            'mode': [int(s) for s in r['mode']]}
    if pred is not None:
        full = np.zeros(len(notes), dtype=np.int64)
        has = r['has']
        full[has] = pred
        arms['learned'] = [int(s) for s in full]
        arms['hybrid'] = combine_hybrid([int(s) for s in r['y']],
                                        [int(s) for s in r['mode']], full, has)
    for name, shifts in arms.items():
        moved = dedup_by_pitch([[t, d, p + s, v] for (t, d, p, v), s in zip(notes, shifts)])
        out = os.path.join(out_dir, '%s_%s.mid' % (stem, name))
        write_track(r['midi'], out, moved)
        n_chg = sum(1 for s in shifts if s)
        print('        臂 %-7s 改 %3d / %3d → %s' % (name, n_chg, len(notes), out))


# ── 应用 ────────────────────────────────────────────────────────────────────
def dedup_by_pitch(items):
    """复刻 bass_ensemble.dedup：同音高截断、<20ms 重复丢前一个。items=[t,dur,p,vel] 秒。"""
    by = defaultdict(list)
    for (t, d, p, v) in items:
        by[int(p)].append([t, float(d), v])
    out = []
    for p, lst in by.items():
        lst.sort()
        keep = []
        for j, cur in enumerate(lst):
            nxt = lst[j + 1][0] if j + 1 < len(lst) else None
            if nxt is not None:
                gap = nxt - cur[0]
                if gap < 0.02:
                    continue
                if cur[1] > gap - 0.005:
                    cur[1] = max(max(0.01, gap - 0.005), 0.01)
            keep.append(cur)
        for (t, d, v) in keep:
            out.append([t, d, p, v])
    out.sort(key=lambda x: (x[0], x[2]))
    return out


def write_track(mid_path, out_path, notes, track='Bass'):
    d = midi_file.import_midi(mid_path)
    spb = 60.0 / float(d.get('bpm') or 120.0)
    hit = False
    for tr in d['tracks']:
        if tr.get('name') == track:
            tr['notes'] = sorted([round(t / spb, 6), round(d / spb, 6), int(p), int(v)]
                                 for (t, d, p, v) in notes)
            hit = True
    if not hit:
        raise SystemExit('MIDI 里没有轨 %r' % track)
    d['end_beat'] = max([n[0] + n[1] for tr in d['tracks'] for n in tr['notes']] or [0.0])
    midi_file.export_midi(d, out_path)


def combine_hybrid(pyin_shifts, mode_shifts, pred, has):
    """可部署形态：**规则优先，模型只补规则弃权处**，且补的值必须与帧级众数一致。

    为什么这样组合（实测）：
      · 规则（桶中位）在"正好 ±12/±24"上已经把 650 个修正做对了，模型没有理由覆盖它；
      · 规则的盲区是 `dd=±10..14` 的**近八度**（它按定义弃权），而帧级众数在这类音上
        与模型一致 —— BGM29 集成上 10/10、BGM35 上 4/4 都是真修正；
      · 要求 `pred == mode` 这一条把 BGM01 那 25 个"模型独自想改、众数说保持"的音挡掉。
    """
    out = []
    for ps, ms, pr, h in zip(pyin_shifts, mode_shifts, pred, has):
        if ps != 0:
            out.append(int(ps))
        elif h and pr != 0 and pr == ms:
            out.append(int(pr))
        else:
            out.append(0)
    return out


def compute_shifts(mid_path, stem, arm, model=None, min_prob=MIN_PROB, track='Bass', cache=None):
    notes, spb, _ = load_notes(mid_path, track)
    if arm == 'none':
        return notes, [0] * len(notes), {}
    f0, vprob, _sr, _hop = get_f0(stem, 'pyin', cache)
    ctx = RefCtx(f0, vprob, min_prob)
    y = read_stem(stem)
    total_rms = float(np.sqrt(np.mean(y ** 2)) + 1e-12)
    pyin_shifts, mode_shifts = [], []
    for (t, dur, p, vel) in notes:
        ref, cand = ctx.rule(t, dur, p)
        s = 0
        if ref is not None:
            dd = p - ref
            if dd in (12, -12, 24, -24) and SHIFT_LO <= p - dd <= SHIFT_HI:
                s = -dd
        pyin_shifts.append(int(s))
        g0, g1 = int(t / GRID), int((t + max(dur, 0.05)) / GRID)
        sheet = []
        for g in range(g0, g1 + 1):
            sheet.extend(ctx.bucket_frames.get(g, []))
        frames = np.asarray(sheet, dtype=np.int64)
        valid = frames[ctx.valid[frames]] if len(frames) else frames
        if len(valid):
            rp = np.round(ctx.pitch[valid]) - p
            cnt = Counter(rp.astype(int).tolist())
            ms = max([0, -12, 12, -24, 24], key=lambda k: (cnt.get(k, 0), -abs(k)))
        else:
            ms = 0
        mode_shifts.append(int(ms))
    X, _ys, meta, _mode = note_features(ctx, y, notes, total_rms, min_prob)
    has = meta[:, 4] >= 0
    if arm == 'pyin':
        shifts = pyin_shifts
    elif arm == 'mode':
        shifts = mode_shifts
    elif arm == 'learned':
        b = load_model(model)
        pred, _p = predict_shift(b, X)
        shifts = [int(s) if h else 0 for s, h in zip(pred, has)]
    elif arm == 'hybrid':
        b = load_model(model)
        pred, _p = predict_shift(b, X)
        shifts = combine_hybrid(pyin_shifts, mode_shifts, pred, has)
    else:
        raise SystemExit('未知臂：%s' % arm)
    # 越界保护：与 bass_ensemble 一致（12 <= 新音高 <= 108）
    shifts = [s if SHIFT_LO <= p + s <= SHIFT_HI else 0 for s, (t, d, p, v) in zip(shifts, notes)]
    stat = Counter('改' if s else '保持' for s in shifts)
    stat['有参考'] = sum(1 for s in shifts)
    return notes, shifts, stat


def load_model(path):
    d = np.load(path, allow_pickle=False)
    m = MLP(len(d['mu']), d['W1'].shape[1], d['W2'].shape[1])
    m.W1, m.b1, m.W2, m.b2 = d['W1'], d['b1'], d['W2'], d['b2']
    return {'model': m, 'mu': d['mu'], 'sd': d['sd']}


def cmd_apply(a):
    if a.arm in ('learned', 'hybrid') and not a.model:
        raise SystemExit('--arm %s 需要 --model <训练产物.npz>' % a.arm)
    notes, shifts, stat = compute_shifts(a.mid, a.stem, a.arm, a.model, a.min_prob,
                                         a.track, a.cache)
    moved = []
    for (t, d, p, v), s in zip(notes, shifts):
        moved.append([t, d, p + s, v])
        if s:
            pass
    moved = dedup_by_pitch(moved)
    write_track(a.mid, a.out, moved, a.track)
    n_chg = sum(1 for s in shifts if s)
    print('臂=%s · %d 音 · 改动 %d（%.1f%%）· 去重后 %d 音'
          % (a.arm, len(notes), n_chg, 100.0 * n_chg / max(1, len(notes)), len(moved)))
    print('位移分布：%s' % dict(Counter(shifts)))
    if a.verbose:
        notes2 = dedup_by_pitch([list(n) for n in notes])
        print('（未改动的原始音 %d）' % len(notes2))
    print('→ %s' % os.path.abspath(a.out))


# ── 帧级验收（与 transcribe_audit.py 完全同口径；另有 CREPE 独立尺子）───────
CREPE_HOP = 160      # 10ms @16k
CREPE_SR = 16000


def _f0_key(stem, kind):
    st = os.stat(stem)
    return hashlib.md5(('%s|%d|%d|%s|%s' % (stem, st.st_size, int(st.st_mtime), kind,
                                            (FMIN, FMAX))).encode()).hexdigest()[:16]


def get_f0(stem, kind='pyin', cache_dir=None):
    """→ (f0, quality, sr, hop)。pyin: quality=vprob @22050/512；crepe: periodicity @16k/160。"""
    cache = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache = os.path.join(cache_dir, '%s_%s.npz' % (_f0_key(stem, kind), kind))
        if os.path.exists(cache):
            d = np.load(cache)
            return d['f0'], d['q'], int(d['sr']), int(d['hop'])
    if kind == 'pyin':
        import librosa
        y = read_stem(stem)
        f0, _vf, q = librosa.pyin(y, fmin=FMIN, fmax=FMAX, sr=SR,
                                  frame_length=FRAME_LENGTH, hop_length=HOP)
        sr, hop = SR, HOP
    elif kind == 'crepe':
        import librosa
        import torch
        import torchcrepe
        y = read_stem(stem)
        y16 = librosa.resample(y, orig_sr=SR, target_sr=CREPE_SR)
        f0, q = torchcrepe.predict(torch.from_numpy(np.ascontiguousarray(y16)).unsqueeze(0),
                                   CREPE_SR, hop_length=CREPE_HOP, fmin=FMIN, fmax=FMAX,
                                   model='full', batch_size=1024, device='cpu',
                                   return_periodicity=True)
        f0, q = f0.squeeze(0).numpy(), q.squeeze(0).numpy()
        sr, hop = CREPE_SR, CREPE_HOP
    else:
        raise SystemExit('未知 f0 尺子：%s' % kind)
    if cache:
        np.savez_compressed(cache, f0=f0, q=q, sr=sr, hop=hop)
    return f0, q, sr, hop


def audit_with(f0, q, sr, hop, mid_path, track='Bass', t0=0.0, t1=0.0, qthr=0.3):
    """给定 f0 尺子，算帧级五数（与 transcribe_audit 同口径）。"""
    i0 = int(t0 * sr)
    i1 = int(t1 * sr) if t1 > 0 else len(f0)
    f0 = f0[i0:i1]
    q = q[i0:i1]
    ref = np.full(len(f0), -1, dtype=np.int64)
    ok = np.isfinite(f0) & (f0 > 0)
    if qthr is not None:
        ok &= (q >= qthr)
    ref[ok] = np.round(69 + 12 * np.log2(f0[ok] / 440.0)).astype(np.int64)
    m = midi_file.import_midi(mid_path)
    spb = 60.0 / float(m.get('bpm') or 120.0)
    mine = np.full(len(f0), -1, dtype=np.int64)
    names = [tr.get('name') for tr in m['tracks']]
    if track not in names:
        raise SystemExit('MIDI 里没有轨 %r：%s' % (track, names))
    for tr in m['tracks']:
        if tr.get('name') != track:
            continue
        for n in tr['notes']:
            ts = n[0] * spb
            j0 = max(0, int((ts - t0) * sr / hop))
            j1 = min(len(mine), int(np.ceil((ts + n[1] * spb - t0) * sr / hop)))
            for j in range(j0, j1):
                if mine[j] == -1:
                    mine[j] = int(n[2])
    both = (ref >= 0) & (mine >= 0)
    same = both & (ref == mine)
    oct8 = both & (np.abs(ref - mine) == 12)
    diff = both & (ref != mine) & (np.abs(ref - mine) != 12)
    miss = (ref >= 0) & (mine < 0)
    fake = (ref < 0) & (mine >= 0)
    tot = max(1, int((ref >= 0).sum()))
    tot_mine = max(1, int((mine >= 0).sum()))
    return {'agree': 100.0 * same.sum() / tot, 'oct8': 100.0 * oct8.sum() / tot,
            'diff': 100.0 * diff.sum() / tot, 'miss': 100.0 * miss.sum() / tot,
            'fake': 100.0 * fake.sum() / tot_mine, 'tot': tot, 'tot_mine': tot_mine,
            'same_n': int(same.sum()), 'miss_n': int(miss.sum()), 'fake_n': int(fake.sum()),
            'oct8_n': int(oct8.sum())}


def cmd_audit(a):
    f0, q, sr, hop = get_f0(a.stem, a.f0, a.cache)
    qthr = None if a.f0 == 'pyin' else a.perc
    r = audit_with(f0, q, sr, hop, a.mid, a.track, a.t0, a.t1, qthr)
    print('== 帧级体检（%s 尺子）：%s vs %s'
          % (a.f0, os.path.basename(a.stem), os.path.basename(a.mid)))
    print('   基准有声帧 %d · 我方有声帧 %d' % (r['tot'], r['tot_mine']))
    print('   ✓ 一致 %.1f%% · ~ 差八度 %.1f%% · ✗ 音高不同 %.1f%% · ✗ 漏检 %.1f%% · ✗ 假音 %.1f%%'
          % (r['agree'], r['oct8'], r['diff'], r['miss'], r['fake']))


def cmd_arms(a):
    """对一个 CV 目录里的四个臂做双尺子体检（一把 pyin、一把 CREPE）。"""
    rows = load_data(a.data)
    info = {r['song']: r for r in rows}
    dirs = [d for d in sorted(os.listdir(a.dir)) if os.path.isdir(os.path.join(a.dir, d))]
    print('=' * 104)
    print('三组对照（同一批留出曲、同一把尺子）· 目录 %s' % a.dir)
    print('-' * 104)
    print('%-8s %-8s | %8s %8s %8s %8s | %8s %8s %8s %8s'
          % ('曲', '臂', 'pyin一致', '~八度', '漏检', '假音', 'crepe一致', '~八度', '漏检', '假音'))
    f0cache = a.cache or os.path.join(a.dir, 'f0cache')
    for sd in dirs:
        r = info.get(sd)
        if r is None:
            continue
        print('-' * 104)
        py = get_f0(r['stem'], 'pyin', f0cache)
        cr = get_f0(r['stem'], 'crepe', f0cache)
        for arm in ('base', 'pyin', 'mode', 'learned', 'hybrid'):
            hits = sorted(glob.glob(os.path.join(a.dir, sd, '*_%s.mid' % arm)))
            if not hits:
                continue
            p = hits[0]
            rp = audit_with(*py, p, a.track, qthr=None)
            rc = audit_with(*cr, p, a.track, qthr=a.perc)
            print('%-8s %-8s | %7.1f%% %7.1f%% %7.1f%% %7.1f%% | %7.1f%% %7.1f%% %7.1f%% %7.1f%%'
                  % (sd, arm, rp['agree'], rp['oct8'], rp['miss'], rp['fake'],
                     rc['agree'], rc['oct8'], rc['miss'], rc['fake']))


def cmd_diff(a):
    """逐音对比 pyin 规则 vs 学习模型的判定（假修正率 / 互补项 / 无参考项）。"""
    notes, spb, _ = load_notes(a.mid, a.track)
    f0, vprob, _sr, _hop = get_f0(a.stem, 'pyin', a.cache)
    ctx = RefCtx(f0, vprob, a.min_prob)
    y = read_stem(a.stem)
    total_rms = float(np.sqrt(np.mean(y ** 2)) + 1e-12)
    X, ys, meta, mode = note_features(ctx, y, notes, total_rms, a.min_prob)
    b = load_model(a.model)
    pred, prob = predict_shift(b, X)
    has = meta[:, 4] >= 0
    dd = (meta[:, 2] - meta[:, 4]).astype(int)
    conf = prob.max(axis=1)
    n = len(notes)
    print('=' * 90)
    print('逐音对比：%s（%d 音 · 有参考 %d）' % (os.path.basename(a.mid), n, int(has.sum())))
    print('-' * 90)
    same = ys == pred
    print('  pyin 改 %d · 模型改 %d · 两判定相同 %d（%.1f%%）'
          % (int((ys != 0).sum()), int((pred != 0).sum()), int(same.sum()),
             100.0 * same.sum() / max(1, n)))
    # 假修正：pyin 说保持（dd==0，本来就对）→ 模型改了
    keep_true = has & (dd == 0)
    ff = keep_true & (pred != 0)
    print('  假修正（pyin 判"本来就对" %d 音里模型改）%d 音 = %.2f%%'
          % (int(keep_true.sum()), int(ff.sum()), 100.0 * ff.sum() / max(1, keep_true.sum())))
    # 非八度差异（pyin 弃权，dd 不在 ±12/±24）→ 模型改
    amb = has & (dd != 0) & ~np.isin(dd, [12, -12, 24, -24])
    print('  非八度差异项 %d 音里模型改 %d' % (int(amb.sum()), int((amb & (pred != 0)).sum())))
    # 无参考 → 模型改（默认应被屏蔽）
    print('  无参考项 %d 音里模型改 %d' % (int((~has).sum()), int(((~has) & (pred != 0)).sum())))
    # pyin 漏、模型补：模型改的方向与帧级众数一致的有多少
    fix = has & (ys == 0) & (pred != 0)
    print('  模型补 pyin 没改的 %d 音，其中与帧级众数一致 %d'
          % (int(fix.sum()), int((fix & (mode == pred)).sum())))
    # pyin 改、模型不同意
    brk = has & (ys != 0) & (pred != ys)
    print('  模型不同意 pyin 的改动 %d 音（其中模型改 0：%d · 改别的：%d）'
          % (int(brk.sum()), int((brk & (pred == 0)).sum()),
             int((brk & (pred != 0)).sum())))
    print('  假修正的模型置信度：中位 %.2f（最小 %.2f）'
          % (float(np.median(conf[ff])) if ff.any() else -1,
             float(conf[ff].min()) if ff.any() else -1))
    # hybrid：规则优先 + 模型补（要求与帧级众数一致）
    hyb = np.asarray(combine_hybrid([int(x) for x in ys], [int(x) for x in mode],
                                    [int(x) for x in pred], has))
    hff = keep_true & (hyb != 0)
    print('  hybrid 改 %d（比 pyin 多 %d · 少 %d）· 假修正 %d = %.2f%%'
          % (int((hyb != 0).sum()),
             int(((hyb != 0) & (ys == 0)).sum()), int(((hyb == 0) & (ys != 0)).sum()),
             int(hff.sum()), 100.0 * hff.sum() / max(1, keep_true.sum())))
    if a.list:
        print('-' * 90)
        print('  逐条列出有分歧的音（前 %d）：t · p · dd · pyin · 模型 · 众数 · hybrid · 置信'
              % a.list)
        idx = np.where(has & (ys != pred))[0][:a.list]
        for i in idx:
            print('    %7.3f  p%-3d dd=%+3d  pyin=%+3d  model=%+3d  mode=%+3d  hyb=%+3d  conf=%.2f'
                  % (meta[i, 0], int(meta[i, 2]), int(dd[i]), int(ys[i]), int(pred[i]),
                     int(mode[i]), int(hyb[i]), conf[i]))
    if a.json:
        out = [{'t': float(meta[i, 0]), 'dur': float(meta[i, 1]), 'pitch': int(meta[i, 2]),
                'has_ref': bool(has[i]), 'dd': int(dd[i]), 'pyin': int(ys[i]),
                'model': int(pred[i]), 'mode': int(mode[i]), 'hybrid': int(hyb[i]),
                'conf': float(conf[i])}
               for i in range(n)]
        with open(a.json, 'w', encoding='utf-8') as fh:
            json.dump(out, fh, ensure_ascii=False)
        print('  → %s' % a.json)


# ── 第二把尺子：频率域（谐波显著性 / 最低显著峰），不依赖自相关+HMM ────────
def spectrum_at(y, t, dur, n=4096):
    center = t * SR + min(max(dur * 0.5, 0.03), 0.15) * SR
    i0 = int(round(center - n / 2.0))
    seg = np.zeros(n, dtype=np.float64)
    a, b = max(0, i0), min(len(y), i0 + n)
    if b > a:
        seg[a - i0:b - i0] = y[a:b]
    mag = np.abs(np.fft.rfft(seg * np.hanning(n), 8192))
    freqs = np.fft.rfftfreq(8192, 1.0 / SR)
    return mag, freqs


def spec_shift(y, t, dur, p):
    """第二把尺子：谐波显著性 argmax（候选 p-12/p/p+12；再远不看）。"""
    mag, freqs = spectrum_at(y, t, dur)
    hs = {}
    for s in (0, -12, 12):
        f = 440.0 * 2.0 ** ((p + s - 69.0) / 12.0)
        hs[s] = harmonic_sum(mag, freqs, f)
    best = max((0, -12, 12), key=lambda s: hs[s])
    # 置信：最优 / 次优（对数）
    order = sorted(hs.values(), reverse=True)
    ratio = float(np.log10((order[0] + 1e-12) / (order[1] + 1e-12))) if len(order) > 1 else 0.0
    return best, ratio, hs


def lowest_peak_shift(y, t, dur, p):
    """第二把尺子 B：最低显著谱峰（30–500Hz）→ 估计基频 → 相对 p 的位移。"""
    mag, freqs = spectrum_at(y, t, dur)
    band = (freqs >= 30) & (freqs <= 500)
    m, f = mag[band], freqs[band]
    if len(m) < 3:
        return 0, 0.0
    thr = m.max() * 10 ** (-25 / 20.0)
    idx = [i for i in range(1, len(m) - 1)
           if m[i] >= m[i - 1] and m[i] >= m[i + 1] and m[i] >= thr]
    if not idx:
        return 0, 0.0
    f0 = f[idx[0]]
    # 抛物线插值
    i = idx[0]
    if 0 < i < len(m) - 1:
        denom = (m[i - 1] - 2 * m[i] + m[i + 1])
        if abs(denom) > 1e-12:
            d = 0.5 * (m[i - 1] - m[i + 1]) / denom
            f0 = f[i] + d * (f[1] - f[0])
    est = int(round(69 + 12 * np.log2(max(f0, 1e-3) / 440.0)))
    d = est - p
    s = 0
    if d in (12, -12, 24, -24):
        s = int(np.sign(d) * 12)
    ratio = float(np.log10((m[i] + 1e-12) / (np.median(m) + 1e-12)))
    return s, ratio


def cmd_calib(a):
    rows = load_data(a.data)
    print('=' * 96)
    print('第二把尺子标定：频率域方法 vs pyin 标签（只在"有参考"的音上）')
    print('  A = 谐波显著性 argmax（hs 在 p-12/p/p+12 中最大者）')
    print('  B = 最低显著谱峰（30–500Hz）→ 音高 → 位移')
    print('-' * 96)
    print('%-8s %6s | %-22s | %-22s' % ('曲', '有参考', 'A 与 pyin 一致/±12/其他', 'B 与 pyin 一致/±12/其他'))
    for r in rows:
        has = r['meta'][:, 4] >= 0
        notes = r['meta'][has]
        y = r['y'][has]
        if not len(y):
            continue
        y_audio = read_stem(r['stem'])
        A, B = [], []
        for (row, lab) in zip(notes, y):
            t, dur, p = float(row[0]), float(row[1]), int(row[2])
            sa, _r, _hs = spec_shift(y_audio, t, dur, p)
            sb, _r2 = lowest_peak_shift(y_audio, t, dur, p)
            A.append(sa)
            B.append(sb)
        A, B = np.asarray(A), np.asarray(B)
        def fmt(est, lab):
            same = np.mean(est == lab)
            oct12 = np.mean((est != lab) & (np.abs(est - lab) == 12))
            return '一致 %.2f · 12半音 %.2f · 其他 %.2f' % (same, oct12, 1 - same - oct12)
        print('%-8s %6d | %-22s | %-22s' % (r['song'], len(y), fmt(A, y), fmt(B, y)))
        # 只在 pyin 判改 / 判保持两个子集上看
        chg = y != 0
        if chg.any():
            print('         其中 pyin 判"改"的 %d 音：A 支持 %.2f · B 支持 %.2f'
                  % (int(chg.sum()), np.mean(A[chg] == y[chg]), np.mean(B[chg] == y[chg])))


def main():
    ap = argparse.ArgumentParser(description='低音八度校正器（学习型 v0.1）')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('prepare', help='对每首跑 pyin 并抽特征')
    p.add_argument('--data', required=True)
    p.add_argument('--only', default=None, help='只处理这些曲（逗号分隔）')
    p.add_argument('--force', action='store_true', help='忽略 pyin 缓存')
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser('stats', help='标签分布 / 规则可改空间')
    p.add_argument('--data', required=True)
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser('train', help='按曲留一训练 + 保存部署模型')
    p.add_argument('--data', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--hidden', type=int, default=32)
    p.add_argument('--epochs', type=int, default=600)
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--label', default='pyin', choices=['pyin', 'mode'],
                   help='训练标签来源：pyin 中位规则（默认）或帧级众数')
    p.add_argument('--cv-apply', default=None,
                   help='留一曲预测后，把 base/pyin/mode/learned 四臂写成 MIDI 到该目录')
    p.add_argument('--save-folds', default=None, help='把每个留一曲模型的 npz 存到该目录')
    p.set_defaults(func=cmd_train)

    p = sub.add_parser('apply', help='把某一臂作用到 MIDI')
    p.add_argument('--mid', required=True)
    p.add_argument('--stem', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--arm', default='pyin',
                   choices=['none', 'pyin', 'mode', 'learned', 'hybrid'],
                   help='默认 pyin（与现有 bass_ensemble 等价，不需要模型）；'
                        'learned/hybrid 需要 --model')
    p.add_argument('--model', default=None)
    p.add_argument('--track', default='Bass')
    p.add_argument('--min-prob', type=float, default=MIN_PROB)
    p.add_argument('--cache', default=None, help='f0 缓存目录（apply 也要跑 pyin）')
    p.add_argument('--verbose', action='store_true')
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser('audit', help='帧级体检（与 transcribe_audit 同口径）')
    p.add_argument('--stem', required=True)
    p.add_argument('--mid', required=True)
    p.add_argument('--track', default='Bass')
    p.add_argument('--f0', default='pyin', choices=['pyin', 'crepe'], help='参考尺子')
    p.add_argument('--perc', type=float, default=0.3, help='CREPE 周期置信度下限')
    p.add_argument('--cache', default=None)
    p.add_argument('--t0', type=float, default=0.0)
    p.add_argument('--t1', type=float, default=0.0)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser('arms', help='对 CV 目录的四个臂做双尺子体检')
    p.add_argument('--dir', required=True)
    p.add_argument('--data', required=True)
    p.add_argument('--track', default='Bass')
    p.add_argument('--perc', type=float, default=0.3)
    p.add_argument('--cache', default=None)
    p.set_defaults(func=cmd_arms)

    p = sub.add_parser('calib', help='第二把尺子（频率域）标定')
    p.add_argument('--data', required=True)
    p.set_defaults(func=cmd_calib)

    p = sub.add_parser('diff', help='逐音对比 pyin 规则 vs 学习模型（假修正率）')
    p.add_argument('--mid', required=True)
    p.add_argument('--stem', required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--track', default='Bass')
    p.add_argument('--min-prob', type=float, default=MIN_PROB)
    p.add_argument('--cache', default=None)
    p.add_argument('--list', type=int, default=30, help='列出多少条分歧')
    p.add_argument('--json', default=None)
    p.set_defaults(func=cmd_diff)

    a = ap.parse_args()
    a.func(a)


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                              # noqa: BLE001
        pass
    main()
