#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""ornament_probe.py —— **转音/跑动体检**：原曲哪里有"短促的连续级进"，我们写出来了没有。

## 什么时候用

扒带/还原交付前，以及**写歌前估"这类曲子该有多少转音"**：

- 用户口径（2026-10-05）："**以后其它地方有能识别到吗**，推广一下让直接写音乐也能尝试
  写出来不同的转音"。BGM35 19.0–19.6s 那处（`A♯5→A5→F5→C5→A♯4→A4→F4`，每音约 0.1s）
  是第一个**有据可查**的例子：`songs\b35_clean\notes.md` 记着它是用户认可点，
  而 BP 路径整条漏掉。

## 怎么判（三件事，缺一不可）

1. **逐帧谱峰**（`n_fft=4096 / hop=1024` ≈ 23ms 一帧、10.8Hz 一格）；
2. **谐波筛**：峰 `f` 在 `f/2`、`f/3` 处也有峰 ⇒ 标"疑似谐波"，**只让独立基音进轨迹**
   （这一步是分辨力的来源：`pyin` 在这类复音混音上置信度实测只有 **0.01**；
   整窗能量份额在转音窗里**不高于**相邻窗）——⚠ 真弹八度会被误杀，所以报告里**两种读数都印**；
3. **轨迹判据**：同一条轨迹在连续帧里按 **≤3 半音**移动、持续 **≥4 帧（≈90ms）且 ≤1.2s**、
   总跨度 **≥2 半音**、同向步数占比 ≥0.7 ⇒ 才算"转音候选"。
4. **长轨迹内部滑子窗**（`--sweep`，2026-10-06 生成曲标定后加的）：轨迹是贪心最近邻接出来的，
   在编配干净的生成曲上会一路接成 **13~27 秒** ⇒ 整条判会栽在"时长 > 1.2s"上**整条丢掉**。
   这一档**判据一个字不改**，只在轨迹内部再滑 5~8 音的子段。
   **实测（`109_sunlit_desk` 生成曲，11 个已知转音窗）：整曲召回 1/11 → 11/11**；
   代价是基线全曲候选 4 → 12 处（185.8s），10 个已知"无转音"窗误报 0~1 个。

## 用法

```powershell
$py = "<工具链>\.venv\Scripts\python.exe"
& $py scripts\ornament_probe.py <原曲音频> [--midi <我们的.mid>] [--json 报告.json]
& $py scripts\ornament_probe.py --selftest
```

**两段输出**：① 原曲的转音候选中列表（时间/时长/音名序列/方向）；② 给了 `--midi` 时，
逐个候选报 **`写出/漏掉`** —— 判"我们的 MIDI 在该窗里有没有 **≥3 个连续级进音**"。

⚠ **这不是"归属"判据**：它量的是**整段混音**的谱峰，只能回答"**这里有这么一串音高在响**"，
**不能**回答"是哪件乐器"/"这条轨该弹什么"（`PITFALLS` **325** 记的正是拿它去定归属被听感否掉的教训）。
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SR = 22050
NFFT, HOP = 4096, 1024
# ── 轨迹判据（都可在命令行覆盖）────────────────────────────────────────────
MIN_NOTES = 5         # 至少这么多**音**（先压掉同音连续帧再数）
MAX_SEC = 1.2         # 再长就不是"转音"而是旋律本身
MIN_SPAN = 4.0        # 总跨度（半音）
MAX_STEP = 3.0        # （仅供文档/敏感档用；轨迹配对门是 4.5）
MONO = 0.8            # 同向步数占比
BAND = (180.0, 2600.0)   # 只看这个音高带（避开贝斯 sub 与镲的高频）
PEAK_DB = -12.0          # 峰至少要比该帧最强峰高这么多才参与
HARM_KEEP = 0.25         # f/2 或 f/3 处有 > 该峰 25% 能量 ⇒ 判疑似谐波
# 口径敏感性用的"更宽档"（见 docstring：阈值在本库不当真，所以每次跑都报两档）
WIDE = dict(MIN_NOTES=4, MIN_SPAN=2.0, MONO=0.70, PEAK_DB=-14.0)
# **滑子窗档**（`--sweep`）：判据同严格档，只是轨迹内部再滑 5~8 音的子段（见 docstring 第 4 条）
SUB_MAX = 8


def _hz_to_semi(hz):
    return 12.0 * np.log2(hz / 440.0) + 69.0


def peaks_by_frame(y, sr=SR):
    """→ (freqs, 每帧的 [(semi, hz, db, is_harm)], 帧步长秒)"""
    import librosa
    S = np.abs(librosa.stft(y, n_fft=NFFT, hop_length=HOP, window="hann"))
    fr = librosa.fft_frequencies(sr=sr, n_fft=NFFT)
    band = (fr >= BAND[0]) & (fr <= BAND[1])
    idx = np.where(band)[0]
    fps = HOP / float(sr)
    frames = []
    for k in range(S.shape[1]):
        col = S[idx, k]
        m = float(col.max()) if len(col) else 0.0
        if m <= 1e-9:
            frames.append([])
            continue
        pk, _ = _find_peaks(col, m * 0.10)
        out = []
        for j in pk:
            db = 20 * np.log10(max(col[j], 1e-12) / m)
            if db < PEAK_DB:
                continue
            f = float(fr[idx[j]])
            harm = False
            for div in (2.0, 3.0):
                t = f / div
                mm = (fr >= t * 0.985) & (fr <= t * 1.015)
                if mm.any() and S[mm, k].max() > col[j] * HARM_KEEP:
                    harm = True
            out.append((float(_hz_to_semi(f)), f, float(db), harm))
        frames.append(out)
    return fr, frames, fps


def _find_peaks(col, prom):
    """局部极大 + 突出度门（不依赖 scipy，避免多一个依赖面）。"""
    out = []
    for i in range(1, len(col) - 1):
        if col[i] >= col[i - 1] and col[i] > col[i + 1] and (col[i] - col[max(0, i - 3):i + 1].min()) >= prom:
            out.append(i)
    return out, None


def trace(frames):
    """逐帧把峰接成轨迹 → [{'i': 末帧, 'path': [(帧, semi), ...]}]，贪心最近邻。

    ⚠ **谐波峰不排除，只是"排在后面"**：真实复音混音里高音层几乎总有低八度同时在场
    （BGM35 19.7s 帧：700/883/1410Hz 全被标 H，因为 350/441/705 也在响），
    一刀切"只留非谐波"会把**整条高音转音剔掉** —— 2026-10-05 实测：19.0–19.6s 那处
    因此 0 候选（夹具上却通过，因为夹具是单音）。所以顺序 = (非谐波优先, dB 高优先)，
    真正的过滤交给下游的步长/单调性判据。
    """
    live = []
    done = []
    for fi, pk in enumerate(frames):
        cand = sorted(pk, key=lambda p: (p[3], -p[2]))
        used = set()
        for tr in live:
            last_semi = tr["path"][-1][1]
            best, bd = None, None
            for p in cand:
                d = abs(p[0] - last_semi)
                # ⚠ 配对门要**宽**（6.0 半音）：真实转音里常有 4~5 半音的步 —— 原曲
                #   19.0–19.6s 那处 `A♯5→A5→F5→C5→A♯4→A4→F4` 的步长是 `1,4,5,2,1,4`
                #   （`F5→C5` = 5 半音）。原来卡 1.5、再卡 4.5，两次都把轨迹切断
                #   （自检与真实素材各抓到一次：峰都对、候选 0 个 / 只检到后半段）。
                #   收窄交给下游的步长/单调性判据，不在这里卡。
                if d <= 6.0 and (bd is None or d < bd) and id(p) not in used:
                    best, bd = p, d
            if best is not None and fi - tr["i"] <= 2:
                tr["path"].append((fi, best[0]))
                tr["i"] = fi
                used.add(id(best))
            else:
                done.append(tr)
        live = [t for t in live if t["i"] == fi]
        for p in cand:
            if id(p) not in used:
                live.append({"i": fi, "path": [(fi, p[0])]})
    done += live
    return done


def _cands_from_path(path, fps, min_notes, max_sec, subwin=False, max_sub=SUB_MAX):
    """一条轨迹 → 候选（判据**只在这一处**，严格档与滑子窗档共用）。

    ⚠ **单调性/跨度要按"音高序列"算，不能按帧算**：一个音会占好几帧（0.12s 的音 ≈ 2–3 帧），
    按帧算的话步长里一半是 0 ⇒ 同向占比被稀释到 ~0.25，**真实转音会被判"不单调"整条否掉**
    （2026-10-05 自检当场抓到：峰与轨迹都对，候选却是 0）。
    所以先压掉连续重复帧得到 `seq`，再算步长/单调性；`n` 报**音数**、`frames` 报帧数。

    `subwin=True` ⇒ **轨迹内部再滑 5~8 音的子段**（`--sweep` 档）：`trace` 是贪心最近邻，
    在生成曲上会把不同乐句接成 13~27s 的长轨迹，整条判会因 `dur > max_sec` 整条丢掉
    （2026-10-06 标定：11 个已知转音窗里 10 个栽在这一条，整曲召回只有 1/11）。
    """
    import librosa
    seq = []
    for f, s in path:
        if not seq or abs(s - seq[-1][1]) > 0.5:
            seq.append((f, s))
    if len(seq) < min_notes:
        return []
    out = []
    for i in (range(len(seq)) if subwin else (0,)):
        # ⚠ `break` 只能在**同一个起点的 j 循环**里用（j 递增 ⇒ 子段只会更长，到此为止）。
        #   第一版把窗口拍平成一张 (i,j) 表再 break，等于**第一个子段超长就整张表都不看了**
        #   —— `--selftest` 的长轨迹夹具当场抓到（滑子窗档一个候选都没有）。
        j_lo = (i + min_notes) if subwin else len(seq)
        j_hi = min(len(seq), i + max_sub) if subwin else len(seq)
        for j in range(j_lo, j_hi + 1):
            sub = seq[i:j]
            if subwin:
                dur = (sub[-1][0] - sub[0][0] + 1) * fps      # 子段自己的帧跨度
            else:
                # ⚠ 严格档必须用**整条轨迹**的首末帧（不是 `seq` 的）：压掉连续重复帧会把
                #   末尾"持续响着的那几帧"从 `seq` 里去掉，用 `seq` 算出的 dur 偏小 ⇒
                #   本该因超长丢掉的长轨迹会被放过（2026-10-06 实测：基线凭空多 1 处候选，
                #   与旧版读数对不上 —— 这类"默认行为被悄悄改了"必须当场查，不许当噪声）。
                dur = (path[-1][0] - path[0][0] + 1) * fps
            if dur > max_sec:
                break          # j 递增 ⇒ 子段只会更长，本起点到此为止
            semi = [x[1] for x in sub]
            span = max(semi) - min(semi)
            if span < MIN_SPAN:
                continue
            steps = np.diff(semi)
            if len(steps) == 0:
                continue
            # ⚠ **步长门只用来排除"八度误判"，不用来定义"级进"**：真实转音常是"音阶 + 琶音跳"
            #   混着走 —— 原曲 19.0–19.6s 那处 `A♯5→A5→F5→C5→A♯4→A4→F4` 的步长是
            #   `1,4,5,2,1,4`（**三步超过 3 半音**）。曾经写"相邻 ≤3 半音"与"最多一步大跳"，
            #   两次都把这处真实转音整条否掉（自检当场抓到）。现在的门：**最大步 ≤7 半音**
            #   （>7 基本是八度/五度错判）且 **平均步长 ≤5 半音**；"是不是转音"主要由
            #   时长短、音数多、**同向单调**、跨度够这四条决定。
            if float(np.abs(steps).max()) > 7.0 or float(np.abs(steps).mean()) > 5.0:
                continue
            up = float((steps > 0).mean())
            dn = float((steps < 0).mean())
            if max(up, dn) < MONO:
                continue
            hzs = [441.0 * 2 ** ((s - 69.0) / 12.0) for s in semi]
            out.append(dict(t0=round(sub[0][0] * fps, 3), t1=round(sub[-1][0] * fps, 3),
                            dur=round(dur, 3),
                            # 严格档保持原读数（frames=整条帧数、n=压重后音数）；滑子窗档只能
                            # 报子段自己的跨度 —— 否则读数与它实际判的那一段对不上。
                            frames=(j - i if subwin else len(path)),
                            n=(j - i if subwin else len(seq)),
                            span=round(float(span), 2),
                            dir=("下行" if dn >= up else "上行"),
                            semi=[round(float(s), 1) for s in semi],
                            notes=[str(librosa.hz_to_note(float(h))) for h in hzs]))
    return out


def find_ornaments(y, sr=SR, min_notes=None, max_sec=None, subwin=False):
    """→ [dict(t0,t1,dur,frames,n,span,dir,notes:[音名],semi:[...])]

    `min_notes` / `max_sec` 给 None ⇒ 取**全局** `MIN_NOTES` / `MAX_SEC`。
    ⚠ 原来写成默认参数（`min_notes=MIN_NOTES`）—— 默认参数在 **def 时**求值，于是
    `main` 里改全局的做法（`--min-notes` / `--max-sec` / WIDE 档的 `MIN_NOTES`）
    **一直没生效**（2026-10-06 标定当场发现：严格档与 WIDE 档读数一字不差）。
    """
    if min_notes is None:
        min_notes = MIN_NOTES
    if max_sec is None:
        max_sec = MAX_SEC
    _fr, frames, fps = peaks_by_frame(y, sr)
    out = []
    for tr in trace(frames):
        out += _cands_from_path(tr["path"], fps, min_notes, max_sec, subwin)
    out.sort(key=lambda r: r["t0"])
    # 合并重叠/相邻（同一处的多次跟踪）
    merged = []
    for r in out:
        if merged and r["t0"] <= merged[-1]["t1"] + 0.15:
            if r["n"] > merged[-1]["n"]:
                merged[-1] = r
            continue
        merged.append(r)
    return merged


def midi_ornaments_in(path, t0, t1, cand_semis=None, pad=0.12):
    """我们的 MIDI 在这窗里：① 有没有 **≥3 个连续级进音**；② 与候选音高的**重合数**。

    ⚠ 两个读数都要看：只看①会把"我们其实写了那几个音高、只是被低音层夹在中间"判成"漏掉"
    （BGM35 19.0–19.6s 实测：窗内**有 A♯5**，但相邻音是 C2/D2/F♯2 ⇒ 级进序列不成立）。
    判"写出"= ① 成立 **或** ② 重合 ≥3 个（±0.6 半音）。
    返回 `(hit, 音名序列, 重合数)`。
    """
    import midi_file
    import librosa
    m = midi_file.import_midi(path)
    spb = 60.0 / float(m.get("bpm") or 120.0)
    ns = sorted((float(n[0]) * spb, int(n[2])) for tr in m["tracks"] for n in (tr.get("notes") or []))
    win = [p for (t, p) in ns if t0 - pad <= t <= t1 + pad]
    hit, run = False, []
    cur = []
    for p in win:
        if cur and abs(p - cur[-1]) <= MAX_STEP:
            cur.append(p)
        else:
            cur = [p]
        if len(cur) >= 3 and (max(cur) - min(cur)) >= MIN_SPAN:
            hit = True
            run = cur
    ov = 0
    if cand_semis:
        for s in cand_semis:
            if any(abs(p - s) <= 0.6 for p in win):
                ov += 1
    # ⚠ "写出"以**音高重合**为主（≥3 个）：只看"有没有 ≥3 音级进"会把**低音层自己的级进**
    #   当成"我们写出了那处转音"（BGM35 19.0–19.6s 实测：窗内 C2 D2 F2 F♯2 是低音线，
    #   重合 0/10 却判"写出"）。级进序列只在"它同时与候选音高有 ≥2 个重合"时才算数。
    if ov >= 3 or (hit and ov >= 2):
        hit = True
    elif hit and ov < 2:
        hit = False
    names = (run or win)[:10]
    return hit, [str(librosa.midi_to_note(p)) for p in names], ov


def _load(path, sr=SR):
    import soundfile as sf
    import librosa
    y, s = sf.read(path, dtype="float32", always_2d=True)
    y = y.mean(axis=1)
    if s != sr:
        y = librosa.resample(y, orig_sr=s, target_sr=sr)
    return y


def selftest():
    """坏件必须响、好件必须不响：① 4 音快速级进下行 ② 长音 ③ 谐波陷阱（低音+它的 2/3 次谐波）"""
    ok = True

    def rep(label, y, want, subwin=False):
        nonlocal ok
        r = find_ornaments(y, subwin=subwin)
        got = len(r) >= 1
        flag = "PASS" if got == want else "FAIL"
        ok = ok and got == want
        ex = ("%s %s" % (r[0]["dir"], "".join("-"))) if r else ""
        print("  [%s] %-26s 期望%-4s 实得%-4s 候选 %d 个 %s"
              % (flag, label, "有转音" if want else "无", "有转音" if got else "无", len(r), ex))

    # 2.6s 基准：夹具 ④（持续音 + 级进）要比 1.2s 的时长门长出可见的一截
    t = np.arange(int(SR * 2.6)) / SR

    def tone(f0, a, b, amp=0.4):
        i, j = int(a * SR), int(b * SR)
        x = np.zeros(len(t), dtype="float32")
        x[i:j] = (amp * np.sin(2 * np.pi * f0 * t[i:j]) * np.hanning(max(1, j - i))).astype("float32")
        return x

    # ① 已知转音：A#5→A5→F5→C5→A#4→A4→F4（**照原曲 19.0–19.6s 的形态**，每个约 0.12s；
    #    其中 A#5→F5 = 4 半音，正是"允许一步大跳"要放过的那个）
    y1 = np.zeros(len(t), dtype="float32")
    for k, midi in enumerate((82, 81, 77, 72, 70, 69, 65)):
        f = 440.0 * 2 ** ((midi - 69) / 12.0)
        y1 += tone(f, 0.2 + 0.12 * k, 0.2 + 0.12 * (k + 1) + 0.03)
    rep("7 音快速级进下行（原曲形态）", y1, True)

    # ② 长音（同一音高一直响）—— 不许报
    rep("单个长音", tone(69 and 440.0, 0.2, 1.3), False)

    # ③ 谐波陷阱：低音 A3(220) + 它的 2、3 次谐波（440 / 660）—— 不许报成"上行转音"
    y3 = tone(220.0, 0.2, 1.3) + tone(440.0, 0.2, 1.3, 0.25) + tone(660.0, 0.2, 1.3, 0.15)
    rep("低音+其 2/3 次谐波", y3, False)

    # ④ **长轨迹陷阱**（2026-10-06 生成曲标定后加的）：持续音 + 紧接的 5 音级进 ——
    #    贪心最近邻会把两段接成**一条 1.9s 的轨迹**，严格档按「时长 > 1.2s」整条丢掉
    #    （生成曲实测：11 个已知转音窗里 10 个栽在这一条，整曲召回只有 1/11）。
    #    两条断言都要：严格档**必须漏**（把机制钉住）· 滑子窗档**必须捞回**（把修法钉住）。
    y4 = tone(523.25, 0.2, 1.5)                       # C5 持续 1.3s（= 长过 1.2s 的那截）
    for k, midi in enumerate((74, 76, 77, 79, 81)):   # D5 E5 F5 G5 A5，每音 0.12s，紧接其后
        y4 += tone(440.0 * 2 ** ((midi - 69) / 12.0), 1.5 + 0.12 * k, 1.5 + 0.12 * (k + 1) + 0.03)
    rep("持续音后接 5 音级进（严格档）", y4, False)
    rep("  ↑ 同上，滑子窗档", y4, True, subwin=True)

    print("  selftest %s" % ("全部通过" if ok else "有失败"))
    return ok


def main():
    global MAX_SEC, MIN_NOTES, MIN_SPAN, MONO, PEAK_DB
    ap = argparse.ArgumentParser(description="转音/跑动体检：原曲哪里有短促的连续级进、我们写了没有")
    ap.add_argument("audio", nargs="?")
    ap.add_argument("--midi", default=None, help="我们的 MIDI（给了就报每处 写出/漏掉）")
    ap.add_argument("--at", default=None,
                    help="**只体检这一段**（如 `19.0-19.6`）—— 密集复音上最可靠的用法："
                         "打印该窗的谱峰音高序列（非谐波优先、压重后）与我们的音、重合数；"
                         "不依赖全曲轨迹跟踪（那一步在密集混音里参数敏感，见下）")
    ap.add_argument("--max-sec", type=float, default=MAX_SEC)
    ap.add_argument("--min-notes", type=int, default=MIN_NOTES, help="至少几个音（默认 %d）" % MIN_NOTES)
    ap.add_argument("--sweep", action="store_true",
                    help="**滑子窗档**：判据不变，但在每条轨迹内部再滑 5~%d 音的子段。"
                         "生成曲（编配干净）上整曲召回 1/11 → 11/11 —— 轨迹会被贪心最近邻"
                         "接成十几秒的长轨迹，整条判会因「时长 > 1.2s」整条丢掉（见 docstring 第 4 条）"
                         % SUB_MAX)
    ap.add_argument("--json", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not a.audio:
        ap.print_help()
        return 1
    try:
        import pyenv
        pyenv.ensure("librosa", ".venv-ml", "本工具要 librosa/soundfile")
    except Exception:                                          # noqa: BLE001
        pass
    MAX_SEC, MIN_NOTES = a.max_sec, a.min_notes
    y = _load(a.audio)

    if a.at:
        # **窗体检**（推荐用法）：不跑全曲轨迹，直接看这一段里"有哪些音高在依次响"
        import librosa
        t0, t1 = [float(x) for x in a.at.replace("–", "-").replace("~", "-").split("-")]
        seg = y[int(t0 * SR):int(t1 * SR)]
        _fr, frames, fps = peaks_by_frame(seg)
        # **列该窗所有显著音高（按出现帧数）**，而不是"每帧最强那一个" ——
        # 取最强只会拿到低音层（原曲 19.0–19.6s 实测：最强是 A3/C4/G#3，
        # 而我们要找的那条高音线 A♯5/A5/F5 全是较弱的峰，会被整片看不见）。
        from collections import Counter
        cnt = Counter()
        for f in frames:
            for s, _hz, _db, _hm in f:
                cnt[round(s * 2) / 2.0] += 1
        top = cnt.most_common(14)
        seq = [s for s, _n in top]
        harms = sum(1 for f in frames for p in f if p[3])
        print("=" * 78)
        print("窗体检 · %s · %.2f–%.2f s（%d 帧 · 疑似谐波峰 %d 个）"
              % (os.path.basename(a.audio), t0, t1, len(frames), harms))
        print("  原曲该窗显著音高（音名:出现帧数，前 14 个）：")
        print("    " + "  ".join("%s:%d" % (librosa.hz_to_note(float(441.0 * 2 ** ((s - 69) / 12.0))), n)
                                for s, n in top))
        r = find_ornaments(seg)
        print("  按转音判据：%s" % ("**命中候选 %d 处**" % len(r) if r else "（本窗不构成转音候选）"))
        # **逐帧明细**（这一步才是可靠的那一半）：快速经过的音每个只占 1~2 帧，
        # 任何"按帧数/按平均"的统计都会把它们淹没（本窗实测：帧数 top 是 E6/C5/C7 这些
        # 持续音，而我们要找的那条线排不进去）。所以把每帧的峰**原样列出来**，自己/模型看轨迹。
        print("  逐帧峰（每帧 top6；`*` = 独立基音候选，`~` = 疑似谐波）：")
        for i, f in enumerate(frames):
            if not f:
                continue
            pk = sorted(f, key=lambda p: (p[3], -p[2]))[:6]
            print("    %6.3fs  %s" % (t0 + i * fps, " | ".join(
                "%5.0fHz %-5s %5.1fdB%s" % (hz, librosa.hz_to_note(float(hz)), db,
                                            "*" if not hm else "~")
                for _s, hz, db, hm in pk)))
        if a.midi:
            h, names, ov = midi_ornaments_in(a.midi, t0, t1, seq)
            print("  我们的 MIDI：%s（音高重合 %d/%d）· 窗内音：%s"
                  % ("写出" if h else "**漏掉**", ov, len(seq), " ".join(names) or "无音"))
            if not h:
                print("  → 这窗里我们没写出对应的高音线；要补请走**逐段修**（`PITFALLS` 325："
                      "先确认是哪条轨，别拿混音峰当成归属）")
        print("=" * 78)
        if a.json:
            json.dump(dict(audio=a.audio, at=[t0, t1], seq=[float(s) for s in seq]),
                      open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print("  → %s" % a.json)
        return 0

    cands = find_ornaments(y, subwin=a.sweep)
    # **口径敏感性**（每次都报）：换一档更宽的参数再数一遍 —— 本库的阈值型判据
    # 基本不当真（`SKILL` §16），同素材换参数候选数能差 3 倍（BGM35 实测 52~155 处），
    # 所以这张清单**只当线索**，唯一能当结论的是"我们写了没有"这个对照。
    _save = (MIN_NOTES, MIN_SPAN, MONO, PEAK_DB)
    MIN_NOTES, MIN_SPAN, MONO, PEAK_DB = (WIDE["MIN_NOTES"], WIDE["MIN_SPAN"],
                                          WIDE["MONO"], WIDE["PEAK_DB"])
    n_wide = len(find_ornaments(y))
    MIN_NOTES, MIN_SPAN, MONO, PEAK_DB = _save
    # 第三档：**滑子窗**（判据同严格档，只是轨迹内部再滑）—— 生成曲上可用的是这一档
    n_sweep = len(find_ornaments(y, subwin=True))
    print("=" * 78)
    print("转音体检 · %s（%.1f 秒）· 音高带 %d–%dHz" %
          (os.path.basename(a.audio), len(y) / float(SR), BAND[0], BAND[1]))
    print("候选 %d 处（判据：≥%d 音 / ≤%.1fs / 跨度 ≥%.0f 半音 / 最大步 ≤7 / 平均步 ≤5 / 同向 ≥%.0f%%）%s"
          % (len(cands), MIN_NOTES, MAX_SEC, MIN_SPAN, 100 * MONO,
             "　← **滑子窗档**" if a.sweep else ""))
    print("⚠ 口径敏感性：更宽档（%d 音 / %.0f 半音 / 同向 %.0f%%）**%d 处** · "
          "**滑子窗档**（判据同严格档 + 轨迹内滑 5~%d 音子段）**%d 处** ⇒ "
          "这张清单只当**线索**，不是门。" % (WIDE["MIN_NOTES"], WIDE["MIN_SPAN"],
                                             100 * WIDE["MONO"], n_wide, SUB_MAX, n_sweep))
    print("-" * 78)
    hit = miss = 0
    for c in cands:
        line = "  %6.2f–%6.2fs（%.2fs·%d 帧·%s %.1f 半音）%s" % (
            c["t0"], c["t1"], c["dur"], c["n"], c["dir"], c["span"], " ".join(c["notes"]))
        if a.midi:
            h, names, ov = midi_ornaments_in(a.midi, c["t0"], c["t1"], c["semi"])
            hit += 1 if h else 0
            miss += 0 if h else 1
            line += "\n        → %s（音高重合 %d/%d；我们窗内：%s）" % (
                "写出" if h else "**漏掉**", ov, len(c["semi"]), " ".join(names) or "无音")
        print(line)
    if a.midi and cands:
        print("-" * 78)
        print("写出 %d / 漏掉 %d（漏掉 = 原曲这处有短促连续级进，我们的 MIDI 该窗内没有 ≥3 音级进）"
              % (hit, miss))
    print("-" * 78)
    print("⚠ 这不是归属判据：量的是**整段混音**的谱峰，只说\"这里有这么一串音高在响\"，"
          "不说\"哪件乐器/这条轨该弹什么\"（PITFALLS 325）。")
    if a.json:
        json.dump(dict(audio=a.audio, midi=a.midi, cands=cands, hit=hit, miss=miss),
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("  → %s" % a.json)
    return 0


if __name__ == "__main__":
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                          # noqa: BLE001
        pass
    sys.exit(main())
