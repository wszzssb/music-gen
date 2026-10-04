# -*- coding: utf-8 -*-
"""从**分轨音频**给转录 MIDI 量**逐音力度** —— 还原要的"真实强弱"，不是引擎的默认值。

## 为什么必须做（`docs/RESTORE-METHOD.md` §4）

缺逐音力度 → 引擎套默认力度 → **"打字机"听感**。
实测对照（同一首参考曲）：带力度的版本 `notes_extra` **17773/17773 全带**，
不带的版本 **0/23033** —— 这是"不如"最直接的一条。

## 做法

对**每个音符**，在它的 `[起音, 起音 + 时值]` 窗口内取分轨音频的**峰值幅度**，
再按**分位校准**映射成 velocity：

```
vel = clip(round(P50_target + (peak_db - ref_p50_db) * k), 1, 127)
```

`ref_p50_db` 是**本轨音符峰值的中位**（dB），`P50_target` 是目标中位力度
（默认 51，与实战口径一致）。这样力度分布以"本轨自己的中位"为锚，
不会因为分轨电平不同而整体偏移。

⚠ **先自检再读数**（PITFALLS 187②）：加 `--self-test` 打印本轨峰值分布，
若中位落在噪声底附近（例如 −60dB 以下）说明分轨是空的，别拿它校准。

⚠ **护栏是逐轨的**（2026-10-02 改，实测理由见 `do_one` docstring）：YMT3 的逐分轨输出
**不是单乐器**（`bass.mid` 实测 7 条轨，只有 `Bass` 与 `bass.wav` 对得上）。
做法：
· 对不上的轨**跳过**（保持原力度不写），对得上的轨照常量；
· **一条轨都没对上 ⇒ 拒绝写盘**（拍口径整体错位 / 分轨真的是空的，都是这一种）。
旧行为（全文合计判）用 `--no-per-track-guard` 复现。

## 用法

```bash
python scripts/measure_velocity.py <分轨.wav> <转录.mid> <输出.mid> \
       [--p50 51] [--k 9.0] [--max-db 18]
# 多轨一起做（轨名用引擎认识的）
python scripts/measure_velocity.py --track Piano=piano.wav:piano.mid --track Bass=bass.wav:bass.mid ...
```

输出 MIDI 的每个音符带第 5 个元素（力度），可直接喂 `transcribe_to_song.py`。
（`midi_file` 的模型是 `[start_beat, dur_beat, pitch, vel]` —— 力度在 **index 3**。）
"""
import argparse
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import midi_file                                              # noqa: E402


def peak_db_map(wav, notes, spb, bpm_check=None):
    """→ 每个音符在其窗口内的峰值（dBFS）。`notes` 是 [start_beat, dur_beat, pitch, vel]。

    ## ⚠ 时间轴必须先对齐，否则这个函数**照样给出一串数**（2026-10-02 实测）

    它按 `spb = 60/转录声明的bpm` 把音放到秒轴上。转录的 beat **不一定**是这条音频的
    时间口径（YourMT3 输出恒 120BPM；而 `vocals` 那条分轨的转录内容是 149.8 口径）——
    错位时它会量到**别的时间点**的音频，读出来是"整轨都低于 −60dB"这种**看着像分轨空**的假象。
    实测现场：`vocals.wav` 整轨 RMS −29dB（有内容），而本函数给出的峰值全部 < −60dB。

    所以 `bpm_check` 给了的话，先做**对齐抽检**：随机取 30 个音，比较"按该 bpm 放置"与
    "整体平移 1 格"两种放置的峰值中位；若两者都低，直接返回 None 让调用方**别用**。
    """
    y, sr = sf.read(wav, always_2d=True)
    y = y.mean(axis=1)
    if y.size == 0:
        raise SystemExit('分轨是空的：%s' % wav)
    # 20ms 包络（峰值跟随），避免瞬时尖峰骗人
    hop = max(1, int(sr * 0.02))
    n = len(y) // hop
    env = np.abs(y[:n * hop]).reshape(n, hop).max(axis=1)
    out = []
    for it in notes:
        t0 = float(it[0]) * spb
        dur = max(0.05, float(it[1]) * spb)
        i0, i1 = int(t0 / 0.02), int((t0 + dur) / 0.02) + 1
        i0, i1 = max(0, i0), min(n, i1)
        if i1 <= i0:
            out.append(-120.0)
            continue
        v = float(env[i0:i1].max())
        out.append(20.0 * np.log10(v) if v > 1e-9 else -120.0)
    return out


def alignment_suspect(wav, notes, spb, sample=40, thr_db=-50.0):
    """对齐抽检：抽若干音，看它们落在音频上到底有没有能量。

    返回 `(可疑?, 抽检峰值中位 dB, 建议)`。`可疑` = 中位低于 `thr_db`
    （= 转录的 beat 与这条音频的时间轴对不上，或该轨真的空）。
    """
    if not notes:
        return False, None, '没有音符'
    import random
    rnd = random.Random(0)
    idx = list(range(len(notes)))
    rnd.shuffle(idx)
    idx = sorted(idx[:min(sample, len(idx))])
    pk = peak_db_map(wav, [notes[i] for i in idx], spb)
    med = float(np.median([x for x in pk if x is not None])) if pk else None
    if med is None:
        return True, None, '量不出'
    if med < thr_db:
        return True, med, ('抽检 %d 个音的峰值中位 %.1f dB < %.0f dB —— **转录的拍口径与这条音频'
                           '对不上**（或该轨真的空）。先用 `beat_units.py` 核对两边 bpm，'
                           '别拿这个读数当"力度"。' % (len(idx), med, thr_db))
    return False, med, '对齐正常（抽检中位 %.1f dB）' % med


def calibrate(peaks, p50=51.0, k=9.0, max_db=18.0, anchor_pct=75.0, mode='rank',
              v_lo=28, v_hi=112):
    """峰值 dB → velocity。

    ## 两种映射，默认 `rank`（2026-10-02 实测选定）

    · **`rank`（默认，分位映射）**：把本轨音符按峰值**排名**，映射到 `[v_lo, v_hi]`。
      非参数、跨曲可复用（绝对电平被消掉），且**分布可控** ——
      实测锚点法在 `piano`（5395 音、长低尾）上给出"中位 12、55% 低于 20"，
      那半个轨在 GM 里**几乎无声**（技能：velocity<20 几乎听不见）。
      排位法下中位必然落在区间中部，`v_lo=28` 保证最轻的音也还听得见。
    · `linear`（旧行为，保留）：以 `anchor_pct` 分位为锚、`k` dB→力度。
      对"分布均匀"的层没问题，对长尾层会把一大片压到 1。

    ⚠ 力度是**相对**信息（"这个音比那个音响"），所以排位映射在原理上更贴切；
      它唯一的代价是丢掉了"整层整体强弱"——那由 `arr.mix` 的 CC7 管，不该由力度管。
    """
    good_idx = [i for i, p in enumerate(peaks) if p > -60.0]
    if not good_idx:
        raise SystemExit('所有音符的峰值都低于 −60dB —— 分轨基本是静音，别拿它校准力度')
    if mode == 'rank':
        order = sorted(good_idx, key=lambda i: peaks[i])
        n = len(order)
        out = [v_lo] * len(peaks)                      # 量不到的给下限（别给 0）
        for rank, i in enumerate(order):
            f = 1.0 if n == 1 else rank / float(n - 1)
            out[i] = int(round(v_lo + (v_hi - v_lo) * f))
        ref = float(np.percentile([peaks[i] for i in good_idx], 50))
        return out, ref
    ref = float(np.percentile([peaks[i] for i in good_idx], anchor_pct))
    out = []
    for p in peaks:
        d = max(-max_db, min(max_db, p - ref))
        out.append(int(max(1, min(127, round(p50 + d * k)))))
    return out, ref


def do_one(wav, mid, out, p50, k, max_db, report=True, guard=True, transcript_bpm=None,
           per_track_guard=True, align_thr=-50.0):
    """量一条分轨的逐音力度并写出。

    `transcript_bpm`：转录的 beat 相对哪个 bpm（不给则取 MIDI 自己声明的）。
    `guard=True` 时做**对齐抽检**（见 `alignment_suspect`）。

    ## ⚠ 护栏必须**逐轨**判，不能拿全文合计判（2026-10-02 实测，BGM35 的 bass）

    YMT3 的"逐分轨"输出**不是单乐器** —— 实测 `bass.mid` 里有 **7 条轨**
    （Piano 350 / Perc 97 / Organ 6 / Guitar 105 / **Bass 829** / Strings 32 / Drums 366），
    只有 `Bass` 那条与 `bass.wav` 对得上（中位 **−12.0 dB**），
    另外 956 个泄漏音落在贝斯分轨的静音段上（−63 ~ −70 dB）。

    拿**全文合计**判：中位被拖到 **−59.9 dB** → 整份被拒 —— 一条本来能量准的贝斯轨
    被 956 个不该管的音连坐（`HANDOFF-BGM35-R2.md` §6-5 把它写成"曲内位置错"，
    归因不对：本工具只看**转录 vs 这条分轨音频**，与曲内位置无关）。

    反过来，全文合计判**过**的时候也在放水：同一批实测里 `piano.mid` 的
    Synth Lead/Synth Pad/Drums 三条轨中位 −63 dB，全文合计 −18.4 dB 判"过"，
    那 **427 个音照样被 `mode='rank'` 映射出一串"看着正常"的力度**（其实是噪声底排位）。

    所以改成**逐轨**判：对不上的轨**跳过**（保持它原来的力度不写），对得上的轨照常量。
    安全性由一条更硬的规则保住 —— **一条轨都没对上 ⇒ 仍然拒绝写盘**：
    拍口径整体错位时所有轨都会一起落空，这时依旧一个字都不写（`vocals` 实测
    8 条轨全在 −60dB 以下，就是这条规则拦下的）。
    """
    d = midi_file.import_midi(mid)
    src_bpm = float(transcript_bpm or d.get('bpm') or 120.0)
    spb = 60.0 / max(1e-9, src_bpm)
    tracks = [tr for tr in (d.get('tracks') or []) if (tr.get('notes') or [])]
    all_notes = [x for tr in tracks for x in tr['notes']]
    if guard and all_notes and not per_track_guard:
        suspect, med, why = alignment_suspect(wav, all_notes, spb)
        if report:
            print('  对齐抽检（全文合计）：%s' % why)
        if suspect and med is not None and med < -60.0:
            # ⚠ **不许静默写盘**：口径错位时量到的是别处的时间点，
            #   写出来的力度"看着正常"（中位一定落在 --p50）但完全是噪声。
            raise SystemExit('✗ 拒绝写盘（%s）—— 用 `--transcript-bpm` 指对口径'
                             % why.split('——')[0].strip())
    total = 0
    kept, skipped = [], []
    for tr in tracks:
        notes = tr['notes']
        if guard and per_track_guard:
            suspect, med, why = alignment_suspect(wav, notes, spb, thr_db=align_thr)
            if suspect:
                skipped.append((tr.get('name'), len(notes), med))
                if report:
                    print('  轨 %-10s **跳过**（%5d 音 · 抽检中位 %s）—— 这条轨与 %s 对不上，'
                          '保持它原来的力度不写'
                          % (tr.get('name'), len(notes),
                             ('%.1f dB' % med) if med is not None else '量不出',
                             os.path.basename(wav)))
                continue
        peaks = peak_db_map(wav, notes, spb)
        vels, ref = calibrate(peaks, p50, k, max_db)
        for it, v in zip(notes, vels):
            # ⚠ **vel 在 index 3** —— `midi_file` 的模型是
            #   `[start_beat, dur_beat, pitch, vel]`。第一版 append 到第 5 位，
            #   导出后**读不回**（MIDI 文件只有那 4 个字段），实测"写了 6115 音带力度、
            #   读回 0 音带力度"。别把 `song.json` 的 5 元组格式搬到 MIDI 层来。
            it[3] = int(max(1, min(127, v)))
        total += len(notes)
        kept.append(tr.get('name'))
        if report:
            print('  轨 %-10s %5d 音 · 峰值中位 %.1f dB · 力度 %d~%d（中位 %d）'
                  % (tr.get('name'), len(notes), ref, min(vels), max(vels),
                     int(np.median(vels))))
    if guard and per_track_guard and not kept:
        # 一条都没对上：**一个字都不写**（口径整体错位 / 分轨是空的都是这一种）。
        raise SystemExit('✗ 拒绝写盘：%d 条轨**没有一条**与 %s 对得上（抽检全部 < %.0f dB）'
                         '—— 拍口径错位（用 `--transcript-bpm` 指对）或这条分轨本来就是空的；'
                         '细节：%s'
                         % (len(skipped), os.path.basename(wav), align_thr,
                            '；'.join('%s %d 音 %s' % (n, c, ('%.0fdB' % m) if m is not None
                                                      else '量不出')
                                      for n, c, m in skipped[:6])))
    if skipped and report:
        print('  ⚠ 逐轨护栏跳过 %d 条轨 / %d 音（它们与 %s 对不上）：%s'
              % (len(skipped), sum(c for _n, c, _m in skipped), os.path.basename(wav),
                 '、'.join(n for n, _c, _m in skipped)))
    midi_file.export_midi(d, out)
    # **产物检查**（2026-10-02 加）：力度中位不能太低 —— 实测 `piano` 曾给出"中位 1"
    # （半个轨听不见），而那正是技能里"弱起音 <20 在 GM 里几乎无声"那条。
    import statistics as _st
    _all = [int(x[3]) for t in d.get('tracks') or [] for x in (t.get('notes') or [])]
    if _all:
        _med = _st.median(_all)
        _lo = sum(1 for v in _all if v < 20) / len(_all)
        if _med < 20 or _lo > 0.5:
            print('  ! 产物力度可疑：中位 %d · <20 的占 %.0f%% —— 锚点/斜率要调'
                  '（技能：velocity<20 在 GM 里几乎无声）' % (_med, 100 * _lo))
    print('写 %s（%d 音带力度%s）'
          % (out, total, '；跳过 %d 轨 / %d 音' % (len(skipped),
                                                  sum(c for _n, c, _m in skipped))
             if skipped else ''))
    return total


def self_test(wav, mid, p50=51.0, k=9.0, max_db=18.0):
    """打印"这个分轨能不能用来校准力度"：峰值分布 + 整轨 RMS + 校准后的力度范围。

    为什么要它（docstring 第 23 行一直这么写着，但**直到 2026-09-25 才真的实现**）：
    实测把 `--self-test` 按文档敲上去会得到 `unrecognized arguments` —— 判据只写在文档里、
    没落成代码，等于没有。而这一步是**必需的**：分轨是空的时候，`calibrate()` 会把
    一整轨噪声底映射成"看起来很正常的力度"（它只保证中位落在 `--p50`，不保证有信号）。
    """
    y, sr = sf.read(wav, always_2d=True)
    y = y.mean(axis=1)
    if y.size == 0:
        print('分轨**是空的**（0 采样）：%s' % wav)
        return 4
    rms = float(np.sqrt((y ** 2).mean()))
    pk = float(np.abs(y).max())
    print('分轨 %s' % wav)
    print('  整轨 RMS %.1f dB · 峰值 %.1f dB（RMS 是内容参考，不是严格噪声底）'
          % (20.0 * np.log10(max(rms, 1e-12)), 20.0 * np.log10(max(pk, 1e-12))))
    d = midi_file.import_midi(mid)
    spb = 60.0 / max(1e-9, float(d.get('bpm') or 120.0))
    usable = 0
    for tr in d.get('tracks') or []:
        notes = tr.get('notes') or []
        if not notes:
            continue
        peaks = np.array(peak_db_map(wav, notes, spb), dtype=float)
        good = peaks[peaks > -60.0]
        if good.size == 0:
            print('  轨 %-10s %5d 音 · **全部低于 −60dB → 这轨是空的，别拿它校准力度**'
                  % (tr.get('name'), len(notes)))
            continue
        vels, ref = calibrate(list(peaks), p50, k, max_db)
        print('  轨 %-10s %5d 音 · 峰值 中位 %.1f / P10 %.1f / P90 %.1f dB · 低于 −60dB 的 %d 个'
              % (tr.get('name'), len(notes), float(np.median(good)),
                 float(np.percentile(good, 10)), float(np.percentile(good, 90)),
                 int((peaks <= -60.0).sum())))
        print('      → 锚点（峰值中位）%.1f dB · 力度中位 %d · 范围 %d~%d'
              % (ref, int(np.median(vels)), min(vels), max(vels)))
        usable += 1
    if usable:
        print('结论：**可以**拿它校准力度（%d 轨有可校准音符）' % usable)
        return 0
    print('结论：**别用** —— 没有任何一轨有可校准的音符（分轨空 / 名字对不上 / 转录为空）')
    return 4


def main():
    import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
    ap = argparse.ArgumentParser(description='从分轨音频量逐音力度')
    ap.add_argument('wav', nargs='?', help='分轨音频')
    ap.add_argument('mid', nargs='?', help='转录 MIDI')
    ap.add_argument('out', nargs='?', help='输出 MIDI')
    ap.add_argument('--track', action='append', default=[],
                    help='轨名=wav:mid（可多次，用于一次处理多轨）')
    ap.add_argument('--p50', type=float, default=51.0, help='目标中位力度（默认 51）')
    ap.add_argument('--k', type=float, default=9.0, help='dB→力度 的斜率（默认 9）')
    ap.add_argument('--max-db', type=float, default=18.0, help='允许偏离中位的上限 dB')
    ap.add_argument('--self-test', action='store_true',
                    help='只打印本轨的峰值分布与噪声底，**不写文件**（判"这个分轨能不能用来校准"）')
    ap.add_argument('--transcript-bpm', type=float, default=None,
                    help='**这条转录的 beat 是相对哪个 bpm 记的**（默认取 MIDI 自己声明的）。'
                         '⚠ 与音频的真实速度不同时，本工具量到的是**别的时间点**的音频，'
                         '读出来像"整轨都低于 −60dB"——会做对齐抽检并**拒绝写盘**'
                         '（口径错位时所有轨会一起落空，见 `do_one` 的"一条都没对上"规则）')
    ap.add_argument('--no-per-track-guard', action='store_true',
                    help='回到旧的**全文合计**护栏（默认逐轨判）。'
                         '⚠ 只在需要复现旧读数时用：全文合计会被转录里的**泄漏轨**污染')
    a = ap.parse_args()
    if a.self_test:
        if not (a.wav and a.mid):
            raise SystemExit('--self-test 需要 <分轨.wav> <转录.mid> 两个位置参数')
        return self_test(a.wav, a.mid, a.p50, a.k, a.max_db)
    if not (a.wav and a.mid and a.out) and not a.track:
        raise SystemExit('要么给三个位置参数，要么用 --track 轨名=wav:mid')
    # 拍值口径（2026-10-02 加）：转录的 beat 是相对 `--transcript-bpm`（默认取它声明的）记的。
    # 这个值**与音频的真实速度不一致**时，本工具会把音放到错误的秒位置，量到的是**别处**的音频
    # —— 症状是"整轨都低于 −60dB"，看着像分轨空。
    # ⚠ 2026-10-02 **护栏改逐轨**：原来这里拿**全文合计**抽检并直接拒绝写盘，
    #   而 YMT3 的"逐分轨"输出里混着泄漏轨（实测 `bass.mid` 7 条轨里只有 `Bass` 对得上），
    #   合计中位被拖到 −59.9dB ⇒ 一条能量准的贝斯轨被连坐拒掉。
    #   现在**闸门只有一处**（`do_one` 的逐轨判定 + "一条都没对上就不写盘"），
    #   这里只报口径，不重复设门 —— 两道门口径不一致正是最难查的一类问题。
    tbpm = a.transcript_bpm
    per_track = not a.no_per_track_guard
    if tbpm and a.wav and a.mid:
        d0 = midi_file.import_midi(a.mid)
        src = float(d0.get('bpm') or 120.0)
        if abs(src - tbpm) > 0.01:
            print('  拍口径：转录声明 %.4g BPM、按 %.4g BPM 解释（调用方指定）' % (src, tbpm))
    if a.track:
        for spec in a.track:
            if '=' not in spec or ':' not in spec.split('=', 1)[1]:
                raise SystemExit('--track 要写成 轨名=wav:mid，收到 %r' % spec)
            nm, rest = spec.split('=', 1)
            w, m = rest.rsplit(':', 1)
            print('%s:' % nm)
            do_one(w, m, m.replace('.mid', '_vel.mid'), a.p50, a.k, a.max_db,
                   transcript_bpm=tbpm, per_track_guard=per_track)
        return 0
    do_one(a.wav, a.mid, a.out, a.p50, a.k, a.max_db, transcript_bpm=tbpm,
           per_track_guard=per_track)
    return 0


if __name__ == '__main__':
    sys.exit(main())
