#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""preflight.py —— **交付前体检（关系型判据）**：抓"逐音级读数全对、交付却是错的"那一类。

## 为什么需要它（这份工具重建自 `docs/HANDOFF-TRANSCRIBE.md` §9）

2026-09 那一轮真实商业录音交付出的**四个错**，逐音级尺子（F1 / 召回 / 支持度 / 质心）**一个都看不见**：

| 交付出的错 | 用户的话 | 逐音级读数 | 这儿用哪条判据抓 |
|---|---|---|---|
| 编配被压平（12 轨 → 1 轨，1893 个音全变钢琴） | "响得有点奇怪" | 全对 | ① 轨结构 |
| 同一音高被写 2–5 份（320 份） | "25 秒左右有点问题" | 全对 | ② 新增音重复 |
| 该连长音处补成短音 | "1 分 11 秒左右也是" | 密度看着正常 | ③ 密度双向（逐秒） |
| 鼓轨无中生有的连击（22 下 / 原曲 10） | "1 分 42 左右的锣有点密了" | 音数合法 | ⑤ 鼓连击支撑率 |

⇒ **六条"总和型"尺子 + 一条"单边型"，都抓不住这些"关系型"性质**。这份工具专抓它们。

⚠ **本工具丢失过一次**：原实现（交付目录 `pop_transcribe_audit_交付\tools\preflight.py`，2026-09/10 两轮标定）
**从未进 git**，随中间产物清理一起消失（`git log --all` 0 提交）。本文件是**按文档校准值重建**的版本，
判据口径照 `HANDOFF-TRANSCRIBE.md` §9 / §10.2，**校准值必须是"坏件响、好件不响"**（`--selftest` 守着）。

## 判据（三态：PASS / WARN / FAIL / UNKNOWN —— **测不到就报 UNKNOWN，不静默降级**）

| # | 判据 | 三态 | 标定时的读数（文档 §9/§10.2） |
|---|---|---|---|
| ①a | **压平**：骨架 ≥2 条有音音高轨、成品只剩 1 条 | **FAIL** | 坏 12 轨→1 轨 |
| ①b | **疑似丢轨**：骨架某条有音的 (channel, program) 在成品里连"同 program 的轨"都没有 | WARN（`--strict-tracks` ⇒ FAIL） | 真实 douzo 上报 `(ch1 prog8) 17 音 · (ch2 prog16) 5 音`，而这两层其实在（引擎换了音色）⇒ 引擎编配版的既有性质，**不能当门** |
| ② | **新增音重复**：新增音里落在"同音高 ≤60ms 重复组"的占比 | **FAIL** >2% | 坏 320 份 / 63%；好 0 份 / 0% |
| ③ | **密度双向**：逐秒「我们渲染的起音数 vs 原曲起音数」（**音频 vs 音频**） | WARN | 好/坏都各 8–9 秒越界（**同一把尺子，分辨力有限**） |
| ④ | **覆盖**：a) 分轨有能量的段、我方整段无音 b) 我方放音而 `other` 轨极静 | WARN | 坏 440 处（最长 5.80s）；⚠ **b) 侧要前置判适用**：douzo 实测 `other` 只有 5% 的窗有能量 ⇒ 该侧恒真，报"未量"而不是报问题 |
| ⑤ | **鼓连击**：密集连击串里，每一下的 **3–8kHz 能量抬升 >6dB** 才算"有冲击"，支撑率 <0.6 | WARN | 坏 `100.79–102.31s 键38 22 下 支撑 0.45`；好 0 个 |
| ⑥ | **动态包络**：逐秒 RMS 差 − 整体差（§10.2 标定**有效**） | WARN | 坏 63 秒越界 → 修后 32 秒；douzo 实测 33 秒（= 这条线的基线） |

**已废弃、本工具不再实现**（文档 §10.2，别再加回来）：⑦ 频带赤字（坏/好都报 11 秒，分辨力不足）·
⑧ 逐层空洞（能量门触发 68%；起音事件数版坏/好都 476 个 = 噪声）。

## 用法

```powershell
$py = "<工具链>\.venv-ml\Scripts\python.exe"
& $py scripts\preflight.py <成品.mid> --ref <原曲音频> --stems <demucs六轨目录> `
      --skeleton <源转录.mid> [--base <补音前.mid>] [--mine-wav <我们渲染.wav>] `
      [--json 报告.json] [--out 报告目录]
& $py scripts\preflight.py --selftest        # 尺子自检：坏件必须响、好件必须不响
```

**退出码**：`0` = 无 FAIL（可交付）· `1` = 有 FAIL（不许生成成品）· `2` = 缺关键输入。

⚠ **纪律**（四条都来自实测）：
1. **`--selftest` 先过**：这一轮它曾当场抓到 ② 判据本身的 bug（"贴着基准音的重复份"被判成基准音 → 假 PASS）
   —— 所以 ② 的"新增"判定窗口（`SAME_SEC=20ms`）**故意比重复窗口（`DUP_SEC=60ms`）紧**，并配了反例夹具。
2. **⑤ 每次跑都打印反例（`ctrl`）**：Demucs 鼓分轨的"支撑率"会给真鼓花 **0.00**（会误删）
   ⇒ 判据只用**全混音 3–8kHz**，且把最强的那一串无论好坏都印出来当对照。
   ⚠ 原实现想过用 `onset_detect` 数冲击 —— 自检当场证伪：它的 `normalize=True`（默认）把静音区噪声
   归一化到 1.0，**"ref 里没有冲击"的坏件照样被判"有支撑"**。现在直接量每一下的频带能量抬升。
3. **只有 ①② 是门**：③④⑤⑥ 是 WARN 级 —— 它们的"越界"在**好件上也出现**（是这条线的基线），
   只当线索，**必须配人耳 A/B**。
4. **判据要带"适用性前置"**：④b 侧在"`other` 轨本来就没内容"的曲子上**恒真**（实测 douzo 5%），
   这时报 **"未量"** 而不是报问题 —— 同族纪律见 `SKILL.md` §16（触发率 ≈100% = 噪声）。
"""
import argparse
import bisect
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SR, HOP = 22050, 512

# ② 重复与"与基准同一个音"的两个窗口 —— 故意不等（见 docstring 纪律 1）
DUP_SEC = 0.06          # 同音高、起音间隔 ≤ 这个 ⇒ 算"重复份"
SAME_SEC = 0.02         # 与 base 的音 ≤ 这个 ⇒ 算"同一个音"（不算新增）
DUP_RATIO_MAX = 0.02    # 新增音里落进重复组的占比 > 2% ⇒ FAIL

# ⑤ 鼓连击
BURST_GAP = 0.25        # 同一鼓键相邻两下的最大间隔（串内）
BURST_MIN = 6           # 串长（下数）≥ 这个才查
BURST_SUPPORT_MIN = 0.6  # 支撑率低于这个 ⇒ WARN
BURST_RISE_DB = 6.0     # 每一下的 3–8kHz 抬升 > 这个 dB 算"有冲击"
BAND_LO, BAND_HI = 3000.0, 8000.0   # 全混音冲击带（Demucs 鼓分轨只当旁证）

# ③ 密度 / ⑥ 包络
DENS_ABS, DENS_REL = 3, 0.6
ENV_DB = 6.0
COVER_APPLY_MIN = 0.10   # ④b：`other` 轨有能量的窗占比低于这个 ⇒ 该侧**不适用**（报"未量"）

STEM_OF = {'Drums': 'drums', 'Bass': 'bass', 'Piano': 'piano', 'Hook': 'guitar',
           'Strings': 'other', 'Pad': 'other', 'Melody': 'vocals', 'Glock': 'other'}
NOTES = ('drums', 'bass', 'other', 'vocals', 'guitar', 'piano')

FAIL, WARN, PASS, UNK = 'FAIL', 'WARN', 'PASS', 'UNKNOWN'


# ---------------------------------------------------------------- MIDI 读入
def load_midi(path):
    """→ {path, bpm, tracks:[{name, ch, prog, drum, notes:[(t_sec, dur_sec, pitch, vel)]}]}

    ⚠ 音高轨按"通道 9 = 鼓"归鼓（GM `program` 在鼓通道上无效，坑 270）。
    """
    import midi_file
    m = midi_file.import_midi(path)
    bpm = float(m.get('bpm') or 120.0)
    spb = 60.0 / bpm
    tracks = []
    for tr in m['tracks']:
        ch = tr.get('channel')
        notes = [(float(n[0]) * spb, float(n[1]) * spb, int(n[2]), int(n[3]))
                 for n in (tr.get('notes') or [])]
        tracks.append(dict(name=tr.get('name') or '?', ch=ch, prog=tr.get('program'),
                           drum=bool(tr.get('drum') or ch == 9), notes=notes))
    return dict(path=path, bpm=bpm, tracks=tracks)


def all_notes(m):
    """→ [(t, dur, pitch, vel, track, is_drum)]，按时间排序"""
    out = []
    for tr in m['tracks']:
        for (t, d, p, v) in tr['notes']:
            out.append((t, d, p, v, tr['name'], tr['drum']))
    out.sort(key=lambda x: x[0])
    return out


# ---------------------------------------------------------------- ① 轨结构
def is_flattened(ks_keys, km_keys):
    """①a 压平：骨架 ≥2 条有音音高轨、成品只剩 1 条（**抽成函数是为了能被变异注入**）"""
    return len(ks_keys) >= 2 and len(km_keys) == 1


def check_structure(skel, mine, strict=False):
    """①a **压平**（骨架 ≥2 条有音音高轨、成品只剩 1 条）⇒ FAIL。
    ①b **丢轨**（骨架某条有音的 (ch,prog) 在成品里连同 program 的轨都没有）⇒ 默认 **WARN**。

    ⚠ ①b 为什么默认不是门：**引擎编配版按角色自己分配音色**（`HANDOFF-BGM35-R2.md` §7-5 实测），
    骨架里的 (channel, program) 本来就**不会**原样保留 —— 真实素材 douzo 上实测它会报
    `(ch1 prog8) 17 音 · (ch2 prog16) 5 音` 而这两层其实在（引擎换了音色）。
    而"重建时丢了音色骨架"那次的形态是 **12 轨 → 1 轨** ⇒ ①a 抓得住。
    要把 ①b 也当门：`--strict-tracks`。
    """
    def keyed(m):
        d = defaultdict(int)
        for tr in m['tracks']:
            if tr['drum'] or not tr['notes']:
                continue
            d[(tr['ch'], tr['prog'])] += len(tr['notes'])
        return d
    ks, km = keyed(skel), keyed(mine)
    progs = set(k[1] for k in km)
    lost = sorted(k for k in ks if k not in km and k[1] not in progs)
    added = sorted(k for k in km if k not in ks)
    flat = is_flattened(ks, km)
    if flat:
        st = FAIL
    elif lost:
        st = FAIL if strict else WARN
    else:
        st = PASS
    why = []
    if flat:
        why.append('压平：骨架 %d 条有音音高轨 → 成品只有 1 条' % len(ks))
    for i, k in enumerate(lost):
        note = '' if (strict or i) else '（引擎编配版既有性质；要当门加 --strict-tracks）'
        why.append('%s：(ch%s prog%s) 骨架 %d 音%s'
                   % ('丢轨' if strict else '疑似丢轨', k[0], k[1], ks[k], note))
    return dict(id='①', name='轨结构', state=st, why=why, flat=flat,
                skel_keys={str(k): v for k, v in sorted(ks.items())},
                mine_keys={str(k): v for k, v in sorted(km.items())},
                lost=[str(k) for k in lost], added=[str(k) for k in added],
                strict=bool(strict))


# ---------------------------------------------------------------- ② 新增音重复
def dup_flags(notes, win=DUP_SEC):
    """同音高、起音间隔 ≤win 的**链式**聚簇；返回"重复份"的下标集合（每组保留第一份）"""
    by = defaultdict(list)
    for i, n in enumerate(notes):
        by[n[2]].append((n[0], i))
    dup = set()
    for p, lst in by.items():
        lst.sort()
        prev = None
        for t, i in lst:
            if prev is not None and t - prev <= win:
                dup.add(i)
            prev = t
    return dup


def check_dup(mine, base):
    M = all_notes(mine)
    B = all_notes(base) if base else []
    bby = defaultdict(list)
    for n in B:
        bby[n[2]].append(n[0])
    for p in bby:
        bby[p].sort()

    def is_new(t, p):
        arr = bby.get(p)
        if not arr:
            return True
        j = bisect.bisect_left(arr, t)
        for k in (j - 1, j):
            if 0 <= k < len(arr) and abs(arr[k] - t) <= SAME_SEC:
                return False
        return True

    dup = dup_flags(M)
    new_idx = set(i for i in range(len(M)) if is_new(M[i][0], M[i][2]))
    new_dup = sorted(dup & new_idx)
    n_new = len(new_idx)
    ratio = len(new_dup) / float(max(1, n_new))
    # 对照：基准自己的重复份（"增量"口径）
    n_dup_base = len(dup_flags(B)) if B else None
    st = FAIL if ratio > DUP_RATIO_MAX else PASS
    why = []
    if st == FAIL:
        ex = ['%.2fs 音高%d' % (M[i][0], M[i][2]) for i in new_dup[:5]]
        why.append('新增音里 %d/%d（%.1f%%）落在同音高 ≤%dms 重复组：%s'
                   % (len(new_dup), n_new, ratio * 100, int(DUP_SEC * 1000), '、'.join(ex)))
    return dict(id='②', name='新增音重复', state=st, why=why, new=n_new,
                dup_new=len(new_dup), ratio=round(ratio, 4),
                dup_mine=len(dup), dup_base=n_dup_base,
                baseline_used=('--base' if base else 'skeleton'))


# ---------------------------------------------------------------- ③ 密度双向
def onset_times(path):
    import numpy as np
    import soundfile as sf
    import librosa
    y, sr = sf.read(path, dtype='float32', always_2d=True)
    y = y.mean(axis=1)
    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    return np.asarray(librosa.onset.onset_detect(
        y=y, sr=SR, hop_length=HOP, units='time', pre_max=3, post_max=3,
        pre_avg=8, post_avg=8, delta=0.05, wait=2)), y


def sec_hist(times, dur):
    import numpy as np
    n = int(np.ceil(dur)) + 1
    h = np.zeros(n, dtype=int)
    for t in times:
        i = int(t)
        if 0 <= i < n:
            h[i] += 1
    return h


def check_density(mine_wav, ref):
    if not (mine_wav and ref):
        return dict(id='③', name='密度双向', state=UNK,
                    why=['缺 --mine-wav 或 --ref（音频 vs 音频才能比，不许拿音符数代替）'])
    import numpy as np
    a, ya = onset_times(mine_wav)
    b, yb = onset_times(ref)
    dur = max(len(ya), len(yb)) / float(SR)
    ha, hb = sec_hist(a, dur), sec_hist(b, dur)
    n = min(len(ha), len(hb))
    ha, hb = ha[:n], hb[:n]
    bad = [i for i in range(n)
           if abs(int(ha[i]) - int(hb[i])) > max(DENS_ABS, DENS_REL * max(ha[i], hb[i]))]
    st = WARN if bad else PASS
    why = []
    if bad:
        ex = ['%ds 我方%d/原曲%d' % (i, ha[i], hb[i]) for i in bad[:6]]
        why.append('%d 秒越界（好件上也会出现，属这条线的基线）：%s' % (len(bad), '、'.join(ex)))
    return dict(id='③', name='密度双向', state=st, why=why, bad_sec=len(bad),
                total_sec=n, mine_onsets=int(ha.sum()), ref_onsets=int(hb.sum()),
                first_bad=bad[:40])


# ---------------------------------------------------------------- ④ 覆盖
def _overlap(spans, t0, win=0.5):
    """我方某个音与窗 `[t0, t0+win)` 是否**真的**重叠。

    ⚠ 必须是严格不等：写成 `s0 <= t0+win` 会把"起音正好落在窗末"的音算进**前一个**窗
    （合成自检当场抓到：0.5s 的窗被判成"我方有音"，于是好件报 3 秒凭空音）。
    """
    return any(s0 < t0 + win and s1 > t0 for (s0, s1) in spans)


def _win_rms(mono, sr, win=0.5):
    import numpy as np
    n = int(win * sr)
    out = []
    for i in range(0, max(1, len(mono) - n + 1), n):
        seg = mono[i:i + n]
        out.append((i / float(sr), 20 * np.log10(max(1e-9, float(np.sqrt((seg ** 2).mean()))))))
    return out


def check_cover(mine, stems_dir, ref=None, top=25):
    """a) 分轨有能量的段、我方整段无音；b) 我方放音而 `other` 轨极静。

    ⚠ **"该层在响"必须是绝对门**：`ref` 逐 0.5s RMS 的 **p75 −30dB**（不低于 −60dBFS）。
    两个坑都是自检当场抓到的：
      · 用"该层**自身中位** −6dB"这种相对门 ⇒ 全零分轨的噪声底（−180dB）也被当成"在响"
        （同族坑：`HANDOFF-TRANSCRIBE.md` §10.4 第③条）；
      · 用 `ref` 的**中位** −30dB ⇒ 在"大部分时间静音"的素材上中位本身就是静音底（合成件实测 −180dB）
        ⇒ 门低到 −210dB，静音窗全部入选。**要取 p75**（"在响"的那一档），再压一个绝对下限。
    """
    if not stems_dir:
        return dict(id='④', name='覆盖', state=UNK, why=['缺 --stems（要有分轨才能当参照）'])
    import numpy as np
    import soundfile as sf
    import audit_stems as AS
    stems, sr, is6 = AS.load_stems(stems_dir)
    gate = -60.0
    if ref:
        y = sf.read(ref, dtype='float32', always_2d=True)[0].mean(axis=1)
        wr = _win_rms(y, sr)
        if wr:
            v = np.array([x[1] for x in wr])
            gate = max(float(np.percentile(v, 75)) - 30.0, -60.0)
    notes = [n for n in all_notes(mine) if not n[5]]
    spans = [(n[0], n[0] + max(0.05, n[1])) for n in notes]
    missing, quiet, notapp = [], [], []
    win = {}
    for nm, mono in sorted(stems.items()):
        w = _win_rms(mono, sr)
        if not w:
            continue
        win[nm] = w
        idx = [i for i in range(len(w)) if w[i][1] > gate]        # **绝对门**
        idx = sorted(sorted(idx, key=lambda i: -w[i][1])[:top])
        for i in idx:
            t0 = w[i][0]
            if not _overlap(spans, t0):
                missing.append((nm, round(t0, 2), round(w[i][1], 1)))
    # (b) 我方放音而**全部分轨**都极静 = 凭空音。
    #     ⚠ 只看 `other` 一条会在"other 本来就没内容"的曲子上**恒真**（真实 douzo 实测 other 只有 4–5% 的窗
    #     有能量；合成件上同理）—— 触发率≈100% 的判据等于没查（`SKILL.md` §16）。
    if win:
        keys = [k for k in win if k != 'drums'] or list(win)
        n = min(len(win[k]) for k in keys)
        act = [any(win[k][i][1] > gate for k in keys) for i in range(n)]
        frac = sum(act) / float(max(1, n))
        if frac < COVER_APPLY_MIN:
            notapp.append('（b）侧未量：非鼓分轨全曲只有 %.0f%% 的窗有能量 ⇒ '
                          '该判据在本题材上恒真（触发率≈100%% = 噪声，不当结论）' % (100 * frac))
        else:
            for i in range(n):
                if act[i]:
                    continue
                t0 = win[keys[0]][i][0]
                if _overlap(spans, t0):
                    quiet.append(round(t0, 1))
    st = WARN if (missing or quiet) else PASS
    why = list(notapp)
    if missing:
        ex = ['%s@%.1fs(%.1fdB)' % m for m in missing[:6]]
        why.append('分轨有能量、我方整段无音 %d 处：%s' % (len(missing), '、'.join(ex)))
    if quiet:
        why.append('我方放音而非鼓分轨全静 %d 秒（前几个 %.1fs）' % (len(quiet), quiet[0]))
    return dict(id='④', name='覆盖', state=st, why=why, is6=is6, gate_db=round(gate, 1),
                missing=missing[:60], quiet=quiet[:60],
                unmeasured='音高匹配（同音高没被盖住）未量 —— 要基频估计，见 audit_stems')


# ---------------------------------------------------------------- ⑤ 鼓连击
def pack_bursts(drum_notes):
    """同一鼓键、相邻间隔 ≤BURST_GAP 的串（≥BURST_MIN 下）→ [(pitch, [t,...])]"""
    by = defaultdict(list)
    for (t, d, p, v, nm, drum) in drum_notes:
        by[p].append(t)
    out = []
    for p, ts in by.items():
        ts.sort()
        cur = [ts[0]]
        for t in ts[1:]:
            if t - cur[-1] <= BURST_GAP:
                cur.append(t)
            else:
                if len(cur) >= BURST_MIN:
                    out.append((p, cur))
                cur = [t]
        if len(cur) >= BURST_MIN:
            out.append((p, cur))
    return out


def check_drums(mine, ref):
    """**每一下**在 3–8kHz 的抬升（不靠 onset 检测器）。

    ⚠ 原实现想过用 `librosa.onset.onset_detect` —— **自检当场证伪**：它的 `normalize=True`（默认）
    会把静音区的浮点噪声归一化到 1.0，于是"ref 里根本没有冲击"的坏件照样被判成"有支撑"（假 PASS）。
    现在直接量每一下的**频带能量抬升**：`post(起音后 0–46ms 均值) / pre(起音前 186ms 中位)` 的 dB 值
    > `BURST_RISE_DB` 算这一下"有冲击"。静音段 pre≈post ⇒ 抬升≈0 ⇒ 不支撑（这正是坏件的形态）。
    """
    if not ref:
        return dict(id='⑤', name='鼓连击', state=UNK, why=['缺 --ref（要用全混音 3–8kHz 当对照）'])
    import numpy as np
    import soundfile as sf
    import librosa
    y, sr = sf.read(ref, dtype='float32', always_2d=True)
    y = y.mean(axis=1)
    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=HOP))
    fr = librosa.fft_frequencies(sr=SR, n_fft=1024)
    band = S[(fr >= BAND_LO) & (fr <= BAND_HI)].sum(axis=0)
    fps = HOP / float(SR)                      # 每帧 ≈23.2ms
    npost, npre = 2, 3                         # 起音后 46ms 均值 / 起音前 70ms **最小值** 当本底

    def rise(t):
        """本底用**起音前 70ms 的最小值**（不是中位）：密集连击时中位会被前一下的尾巴抬起来
        —— 自检里"真鼓反被报 WARN"就是这么来的。最小值落在两下之间的缝隙上，才是真本底。"""
        i = int(t / fps)
        post = band[i:i + npost]
        pre = band[max(0, i - npre):i]
        if not len(post) or not len(pre):
            return None
        p = float(post.mean())
        if p <= 1e-9:                          # 静音里没有冲击可谈（不靠浮点噪声凑比值）
            return None
        return 20 * np.log10(p / max(float(pre.min()), 1e-12))

    drums = [n for n in all_notes(mine) if n[5]]
    bursts = pack_bursts(drums)
    rows = []
    for p, ts in bursts:
        rs = [rise(t) for t in ts]
        good = [r for r in rs if r is not None and r > BURST_RISE_DB]
        sup = len(good) / float(max(1, len(rs)))
        rows.append(dict(pitch=int(p), n=len(ts), t0=round(ts[0], 2), t1=round(ts[-1], 2),
                         support=round(sup, 3),
                         med_rise=round(float(np.median([r for r in rs if r is not None] or [0.0])), 1)))
    rows.sort(key=lambda r: -r['n'])
    bad = [r for r in rows if r['support'] < BURST_SUPPORT_MIN]
    st = WARN if bad else PASS
    why = []
    if bad:
        ex = ['%.2f–%.2fs 键%d %d 下 支撑 %.2f（抬升中位 %.1fdB）'
              % (r['t0'], r['t1'], r['pitch'], r['n'], r['support'], r['med_rise'])
              for r in bad[:5]]
        why.append('连击串缺 3–8kHz 冲击支撑 %d 串：%s' % (len(bad), '、'.join(ex)))
    ctrl = rows[0] if rows else None
    return dict(id='⑤', name='鼓连击', state=st, why=why, n_burst=len(rows),
                bad=bad[:30], ctrl=ctrl, rise_db=BURST_RISE_DB,
                n_drum_notes=len(drums), fps=round(fps, 4))


# ---------------------------------------------------------------- ⑥ 动态包络
def check_envelope(mine_wav, ref):
    if not (mine_wav and ref):
        return dict(id='⑥', name='动态包络', state=UNK, why=['缺 --mine-wav 或 --ref'])
    import numpy as np
    import soundfile as sf
    a = sf.read(mine_wav, dtype='float32', always_2d=True)[0].mean(axis=1)
    b = sf.read(ref, dtype='float32', always_2d=True)[0].mean(axis=1)
    n = int(min(len(a), len(b)) / float(SR))

    def per_sec(x):
        return np.array([20 * np.log10(max(1e-9, float(np.sqrt((x[int(i * SR):int((i + 1) * SR)] ** 2).mean()))))
                         for i in range(n)])
    da, db = per_sec(a), per_sec(b)
    dev = (da - db) - float(np.median(da - db))
    bad = [int(i) for i in np.where(np.abs(dev) > ENV_DB)[0]]
    st = WARN if bad else PASS
    why = []
    if bad:
        why.append('%d 秒相对包络偏差 >%.0fdB（起始 %.1fs，最大 %.1fdB）'
                   % (len(bad), ENV_DB, bad[0], float(np.max(np.abs(dev)))))
    return dict(id='⑥', name='动态包络', state=st, why=why, bad_sec=len(bad),
                total_sec=n, max_dev_db=round(float(np.max(np.abs(dev))), 2))


# ---------------------------------------------------------------- 汇总
def run(mid, ref=None, stems=None, skeleton=None, base=None, mine_wav=None, strict=False):
    mine = load_midi(mid)
    skel = load_midi(skeleton) if skeleton else None
    basem = load_midi(base) if base else None
    checks = []
    if skel is None:
        checks.append(dict(id='①', name='轨结构', state=UNK, why=['缺 --skeleton（没有骨架就判不了压平/丢轨）']))
    else:
        checks.append(check_structure(skel, mine, strict))
    checks.append(check_dup(mine, basem or skel))
    checks.append(check_density(mine_wav, ref))
    checks.append(check_cover(mine, stems, ref))
    checks.append(check_drums(mine, ref))
    checks.append(check_envelope(mine_wav, ref))
    n_fail = sum(1 for c in checks if c['state'] == FAIL)
    n_warn = sum(1 for c in checks if c['state'] == WARN)
    n_unk = sum(1 for c in checks if c['state'] == UNK)
    return dict(mid=mid, ref=ref, stems=stems, skeleton=skeleton, base=base,
                mine_wav=mine_wav, checks=checks, n_fail=n_fail, n_warn=n_warn,
                n_unknown=n_unk, rc=(1 if n_fail else 0))


def report(rep):
    print('=' * 78)
    print('交付前体检（关系型判据）· %s' % os.path.basename(rep['mid']))
    print('  ref=%s' % (os.path.basename(rep['ref']) if rep['ref'] else '（缺）'))
    print('  stems=%s' % (rep['stems'] or '（缺）'))
    print('  skeleton=%s · base=%s · mine-wav=%s'
          % (os.path.basename(rep['skeleton']) if rep['skeleton'] else '（缺）',
             os.path.basename(rep['base']) if rep['base'] else '（缺）',
             os.path.basename(rep['mine_wav']) if rep['mine_wav'] else '（缺）'))
    print('-' * 78)
    for c in rep['checks']:
        print('%s %-6s %-8s %s' % (c['id'], c['state'], c['name'],
                                   ('；'.join(c.get('why') or [])) or '（无异常）'))
    d = [c for c in rep['checks'] if c['id'] == '⑤'][0]
    if d.get('ctrl'):
        r = d['ctrl']
        print('  ⑤ 对照（最强串，无论好坏都印）：%.2f–%.2fs 键%d %d 下 支撑 %.2f'
              % (r['t0'], r['t1'], r['pitch'], r['n'], r['support']))
    print('-' * 78)
    print('汇总：FAIL %d · WARN %d · UNKNOWN %d ⇒ %s'
          % (rep['n_fail'], rep['n_warn'], rep['n_unknown'],
             '有 FAIL，不许生成成品' if rep['n_fail'] else '无 FAIL（WARN 项仍需人耳 A/B）'))
    print('  ⚠ 没量到的：%s' % '；'.join(
        [c['unmeasured'] for c in rep['checks'] if c.get('unmeasured')] or ['（无）']))
    print('  ⚠ 全曲 8 条"测不到"（起音摇摆/连奏/踏板/演奏法/音色/好听度/过渡/整体）本工具一条也不管'
          ' → docs/UNMEASURABLE-SOLUTIONS.md')
    return rep['rc']


# ---------------------------------------------------------------- 自检
def selftest():
    """坏件必须响、好件必须不响 —— 三组已知答案（含 ② 的反例与负控）。

    bpm 一律 60 ⇒ **1 拍 = 1 秒**，音符的起音拍直接当秒数用（夹具可读）。
    """
    import tempfile
    import numpy as np
    import soundfile as sf
    import midi_file
    d = tempfile.mkdtemp(prefix='pf_selftest_')
    ok = True

    def mk_mid(path, tracks):
        m = dict(bpm=60.0, division=480, tracks=[
            dict(index=i, name=nm, channel=ch, program=pg, drum=(ch == 9), mute=False,
                 solo=False, hidden=False, ccs=[], program_changes=[[0.0, pg]], markers=[],
                 notes=[[float(t), 0.4, int(p), 90] for (t, p) in ns])
            for i, (nm, ch, pg, ns) in enumerate(tracks)])
        midi_file.export_midi(m, path)
        return path

    def tone(f0, dur, sr=SR):
        t = np.arange(int(dur * sr)) / sr
        return (0.4 * np.sin(2 * np.pi * f0 * t) * np.hanning(len(t))).astype('float32')

    def mix(path, events, noise=None, dur=10.0):
        y = np.zeros(int(dur * SR), dtype='float32')
        for (t0, f0, du) in events:
            x = tone(f0, du)
            i = int(t0 * SR)
            y[i:i + len(x)] += x[:max(0, len(y) - i)]
        for t0 in (noise or []):
            i, k = int(t0 * SR), int(0.06 * SR)
            y[i:i + k] += (np.random.RandomState(1).randn(k) * np.hanning(k) * 0.35).astype('float32')
        sf.write(path, y, SR)
        return path

    # ⚠ 音长 0.45s（略长于 MIDI 音的 0.4s）：tone 全长 1.2s 会拖进下一个 0.5s 窗，
    #   让 ④ 的 (a) 侧报"分轨有能量、我方整段无音"—— 那是夹具不严，不是判据错（本轮已踩）。
    ev = [(1.0, 65.4, 0.45), (3.0, 65.4, 0.45), (5.0, 65.4, 0.45),
          (1.5, 440.0, 0.45), (3.5, 493.9, 0.45), (5.5, 523.3, 0.45)]
    ref = mix(os.path.join(d, 'ref.wav'), ev)
    mine_wav = mix(os.path.join(d, 'mine.wav'), ev)
    # 分轨：bass/piano 有内容，other 极静（④ 的 (b) 侧要能对得上）
    mix(os.path.join(d, 'bass.wav'), [(1.0, 65.4, 0.45), (3.0, 65.4, 0.45), (5.0, 65.4, 0.45)])
    mix(os.path.join(d, 'piano.wav'), [(1.5, 440.0, 0.45), (3.5, 493.9, 0.45), (5.5, 523.3, 0.45)])
    mix(os.path.join(d, 'other.wav'), [(1.5, 440.0, 0.45), (3.5, 493.9, 0.45), (5.5, 523.3, 0.45)])
    for nm in ('drums', 'guitar'):
        sf.write(os.path.join(d, nm + '.wav'), np.zeros(int(10 * SR), dtype='float32'), SR)
    sf.write(os.path.join(d, 'vocals.wav'), np.zeros(int(10 * SR), dtype='float32'), SR)

    skel = mk_mid(os.path.join(d, 'skel.mid'), [
        ('Bass', 0, 32, [(1, 36), (3, 36), (5, 36)]),
        ('Piano', 1, 0, [(1.5, 69), (3.5, 71), (5.5, 72)])])
    good = mk_mid(os.path.join(d, 'good.mid'), [
        ('Bass', 0, 32, [(1, 36), (3, 36), (5, 36)]),
        ('Piano', 1, 0, [(1.5, 69), (3.5, 71), (5.5, 72)])])
    flat = mk_mid(os.path.join(d, 'flat.mid'), [
        ('Piano', 1, 0, [(1, 36), (1.5, 69), (3, 36), (3.5, 71), (5, 36), (5.5, 72)])])
    lost = mk_mid(os.path.join(d, 'lost.mid'), [
        ('Bass', 0, 32, [(1, 36), (3, 36), (5, 36)]),
        ('Guitar', 2, 24, [(1.5, 69), (3.5, 71), (5.5, 72)])])
    # ② 坏件：新增音两两相距 30ms（成重复组），且离 base 音 > 20ms
    dup = mk_mid(os.path.join(d, 'dup.mid'), [
        ('Bass', 0, 32, [(1, 36), (3, 36), (5, 36)]),
        ('Piano', 1, 0, [(1.5, 69), (3.5, 71), (5.5, 72),
                        (7.0, 76), (7.03, 76), (7.5, 77), (7.53, 77)])])
    # ② 反例（旧实现会假 PASS）：新增音只离 base 同音高音 **40ms** ⇒ 必须仍算"新增重复份"
    near = mk_mid(os.path.join(d, 'near.mid'), [
        ('Bass', 0, 32, [(1, 36), (3, 36), (5, 36)]),
        ('Piano', 1, 0, [(1.5, 69), (3.5, 71), (5.5, 72), (1.54, 69)])])
    # ② 负控：新增音离 base 同音高音 **10ms** ⇒ 算同一个音，不算新增，不许报
    same = mk_mid(os.path.join(d, 'same.mid'), [
        ('Bass', 0, 32, [(1, 36), (3, 36), (5, 36)]),
        ('Piano', 1, 0, [(1.5, 69), (3.5, 71), (5.5, 72), (1.51, 69)])])
    # ⑤ 坏件：鼓 8 下连击，ref 音频里没有 3–8kHz 冲击
    drums_bad = mk_mid(os.path.join(d, 'drums_bad.mid'), [
        ('Bass', 0, 32, [(1, 36), (3, 36), (5, 36)]),
        ('Piano', 1, 0, [(1.5, 69), (3.5, 71), (5.5, 72)]),
        ('Drums', 9, 0, [(6.0 + 0.1 * i, 38) for i in range(8)])])
    ref_nd = mix(os.path.join(d, 'ref_nd.wav'), ev)          # 无冲击
    ref_d = mix(os.path.join(d, 'ref_d.wav'), ev, noise=[6.0 + 0.1 * i for i in range(8)])

    def go(name, **kw):
        return run(os.path.join(d, name), ref=ref, stems=d, skeleton=skel, **kw)

    cases = [
        ('好件全绿', lambda: go('good.mid', base=good, mine_wav=mine_wav),
         {'①': PASS, '②': PASS, '③': PASS, '④': PASS, '⑤': PASS, '⑥': PASS}),
        ('压平 2 轨→1 轨', lambda: go('flat.mid', base=flat, mine_wav=mine_wav), {'①': FAIL}),
        ('丢轨（Piano→Guitar）默认只报线索',
         lambda: go('lost.mid', base=lost, mine_wav=mine_wav), {'①': WARN}),
        ('丢轨 + --strict-tracks ⇒ 当门',
         lambda: go('lost.mid', base=lost, mine_wav=mine_wav, strict=True), {'①': FAIL}),
        ('新增音重复', lambda: go('dup.mid', base=good, mine_wav=mine_wav), {'②': FAIL}),
        ('反例：新增音贴 base 40ms', lambda: go('near.mid', base=good, mine_wav=mine_wav), {'②': FAIL}),
        ('负控：新增音贴 base 10ms', lambda: go('same.mid', base=good, mine_wav=mine_wav), {'②': PASS}),
        ('鼓连击无支撑（ref 无冲击）',
         lambda: run(os.path.join(d, 'drums_bad.mid'), ref=ref_nd, stems=d, skeleton=skel,
                     base=drums_bad, mine_wav=mine_wav), {'⑤': WARN}),
        ('好鼓：ref 有冲击 ⇒ 不许报',
         lambda: run(os.path.join(d, 'drums_bad.mid'), ref=ref_d, stems=d, skeleton=skel,
                     base=drums_bad, mine_wav=mine_wav), {'⑤': PASS}),
    ]
    for label, fn, want in cases:
        rep = fn()
        got = {c['id']: c['state'] for c in rep['checks']}
        for cid, w in want.items():
            g = got.get(cid)
            flag = 'PASS' if g == w else 'FAIL'
            ok = ok and g == w
            extra = ''
            if flag == 'FAIL':
                c = [x for x in rep['checks'] if x['id'] == cid][0]
                extra = '  ← %s' % '；'.join(c.get('why') or [])[:110]
            print('  [%s] %-24s %s 期望 %-7s 实得 %s%s' % (flag, label, cid, w, g, extra))

    # ④ 的**适用性前置**：非鼓分轨全静（这份素材没有分轨内容）时必须报"未量"，
    #    而不是拿一条恒真的判据报问题。
    d2 = os.path.join(d, 'stems_quiet')
    os.makedirs(d2, exist_ok=True)
    for nm in NOTES:
        sf.write(os.path.join(d2, nm + '.wav'), np.zeros(int(10 * SR), dtype='float32'), SR)
    rep = run(os.path.join(d, 'good.mid'), ref=ref, stems=d2, skeleton=skel,
              base=good, mine_wav=mine_wav)
    c4 = [c for c in rep['checks'] if c['id'] == '④'][0]
    good4 = (c4['state'] == PASS and any('未量' in w for w in c4['why']))
    ok = ok and good4
    print('  [%s] %-24s ④ 期望 PASS+未量  实得 %s / %s'
          % ('PASS' if good4 else 'FAIL', '分轨全静 ⇒ 判据不适用', c4['state'],
             '；'.join(c4['why'])[:56]))
    print('  selftest %s' % ('全部通过' if ok else '有失败'))
    return ok


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description='交付前体检：抓"逐音级读数全对、交付却是错的"那一类')
    ap.add_argument('mid', nargs='?', help='成品 MIDI')
    ap.add_argument('--ref', help='原曲音频（③⑤⑥ 要用）')
    ap.add_argument('--stems', help='demucs 六轨目录（④ 要用）')
    ap.add_argument('--skeleton', help='源转录 MIDI（骨架：① 要用）')
    ap.add_argument('--base', help='补音前的 MIDI（② 的基准；缺省用 skeleton）')
    ap.add_argument('--mine-wav', dest='mine_wav', help='我们渲染的 wav（③⑥ 要用）')
    ap.add_argument('--json', default=None, help='把读数写成 JSON')
    ap.add_argument('--out', default=None, help='报告目录（写 preflight.json）')
    ap.add_argument('--strict-tracks', dest='strict_tracks', action='store_true',
                    help='把 ①b「疑似丢轨」升级成 FAIL（默认 WARN：引擎编配版按角色换音色，'
                         '骨架的 (ch,prog) 本来就留不住）')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not (a.mid and a.ref and a.stems):
        ap.print_help()
        print('\n✗ 缺关键输入：<成品.mid> --ref --stems 是必需的（缺了不许静默降级）')
        return 2
    for p in (a.mid, a.ref, a.skeleton, a.base, a.mine_wav):
        if p and not os.path.exists(p):
            print('✗ 文件不存在：%s' % p)
            return 2
    if not os.path.isdir(a.stems):
        print('✗ --stems 不是目录：%s' % a.stems)
        return 2
    try:
        import pyenv
        pyenv.ensure('librosa', '.venv-ml', '本工具要 librosa/soundfile')
    except Exception:                                          # noqa: BLE001
        pass
    rep = run(a.mid, a.ref, a.stems, a.skeleton, a.base, a.mine_wav,
              strict=a.strict_tracks)
    rc = report(rep)
    dst = a.json or (os.path.join(a.out, 'preflight.json') if a.out else None)
    if dst:
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        json.dump(rep, open(dst, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('  → %s' % dst)
    return rc


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                          # noqa: BLE001
        pass
    sys.exit(main())
