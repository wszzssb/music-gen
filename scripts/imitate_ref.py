# -*- coding: utf-8 -*-
"""**参考曲模仿流水线**：给一首参考音频，一条命令跑出"仿写 + 混音 + 体检"全套。

为什么要有它：BGM35 的还原链是**十几个脚本手工串起来**的（拆轨 → 多模型转录 →
集成 → 低音专项 → 分轨渲染 → 能量比混音 → band_match → 宽度 → RMS → ogg → 体检），
每一步的"为什么这么设"都散在 PITFALLS / FINDINGS 里 —— 换一首曲子就要重新拼一遍。
本脚本把 BGM35/BGM29 上**验证过**的那条路固化成一条命令，并**分阶段缓存**（可续跑）。

各阶段与依据（实测数字见 docs/CASE-BGM35-FINDINGS.md）：

| 阶段 | 做什么 | 依据 |
|---|---|---|
| stems | htdemucs + htdemucs_6s 分轨 | 6s 的 piano/guitar 与 4s 的 other/bass 错误互不相关 |
| ymt3 | YourMT3+ 整曲转录（**batch 与分组自动推算**，见 `ML.md`「长曲必须分组推理」） | 2.3× 快、产物与官方一致；多乐器一次出 13 通道 |
| ymt3bass | **对 bass 分轨再跑一次** | 全混音里低音被盖住：279 音 vs 分轨 818 音（BGM29 实测） |
| bp | 6 条分轨跑 Basic Pitch | 与 YMT3 错误不相关 → 集成才有增益 |
| bass | 低音三源交叉验证 + 低八度 sub 层 | 平均相对带差 2.61 → **0.95 dB** |
| render | render_midi（**hp 0**：不切 20-40Hz） | 高通三阶在 20-40 带削 3~5 dB，而参考曲那里有能量 |
| match | band_match **两遍** | 第二遍再压 0.1~0.2 dB（max-gain 放大没用，到头了） |
| finish | 宽度对齐参考 + RMS 归一 + ogg | 宽度差是听感"窄/糊"的主因 |
| report | band_grade（**统一 44.1kHz** 再比） | 不统一采样率时低频差会虚高 1~2.6 dB（PITFALLS 174） |

用法：
    python scripts/imitate_ref.py <参考音频> -o <项目目录> [--jobs 5] [--from render]
        [--rms -16.1] [--width-from-ref/--width 0.52] [--thr 0.90] [--no-sub] [--force stems]

环境（三个 venv，脚本会自己找；缺哪个就跳过对应阶段并**明确报出来**，不静默）：
    <root>/.venv        渲染 / band_match / band_grade
    <root>/.venv-ml     分轨 / YourMT3 / 低音集成      （torch + demucs + librosa）
    BP_PY 或 D:\\test\\bp-venv       Basic Pitch
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY_MAIN = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')
PY_ML = os.path.join(ROOT, '.venv-ml', 'Scripts', 'python.exe')
PY_BP = os.environ.get('BP_PY', r'D:\test\bp-venv\Scripts\python.exe')

# 各分轨的合理音域（音高号）—— 与 BGM35/BGM29 用过的一致
RANGES = {'piano': (24, 96), 'guitar': (40, 96), 'other': (40, 96), 'bass': (24, 60)}

STAGES = ['stems', 'ymt3', 'ymt3bass', 'bp', 'bass', 'render', 'match', 'finish', 'report']


def sh(cmd, tag):
    """跑一条子命令；失败就抛（不吞错 —— 吞错是"拿旧产物当真结果"的根源）"""
    print('  $ %s' % ' '.join(cmd[:6] + ['…'] if len(cmd) > 6 else cmd), flush=True)
    t = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        raise SystemExit('[%s] 失败（exit %d）：\n%s\n%s'
                         % (tag, r.returncode, (r.stdout or '')[-1500:], (r.stderr or '')[-1500:]))
    tail = [ln for ln in (r.stdout or '').strip().splitlines() if ln.strip()][-4:]
    for ln in tail:
        print('    %s' % ln)
    print('    （%.1fs）' % (time.time() - t), flush=True)


def have(path):
    return os.path.exists(path)


def low_strategy(ref):
    """按**参考曲自己的低频含量**决定 (高通频率, 是否加 sub 层, 依据读数)。

    为什么必须自适应（实测翻车）：不同曲子的 20-40Hz 含量能差 **18 dB** ——
        BGM29 参考：20-40 比 80-160 只低 **7.9 dB**（有 sub，低音是"面"）
        BGM35 参考：低 **18.2 dB**（母带基本把 sub 切了）
    用 BGM29 调出来的"hp 0 + 加 sub"直接套到 BGM35 上 → 20-40 相对参考 **+10.95 dB**
    （平均相对带差 0.82 → **6.46 dB**，全线崩）。所以这套参数**必须从参考曲推**。

    判据用"20-40 相对本曲 80-160 的 dB"（与采样率无关的相对量）。

    ⚠ **别用硬分档**：第一版写的是
        `rel < −15 → hp36 · < −10 → hp28 · < −6 → hp18 · 否则 hp0+sub`，
        把 BGM29 的 **−7.9 dB** 判进了"hp18 且不加 sub"——
        它自己的实测最优是 "hp0 + 加 sub"（0.82 dB），照这个档位跑出来 **2.71 dB**
        （20-40 塌 5.8 dB、630Hz 以上全线掉 2.5~3 dB）。
        当时只顾着"BGM35 从 6.46 回到 1.60"，**没回头复验 BGM29**，于是把好曲子改坏了。

    现在只有两个**实测锚点**，就老老实实线性插值（别假装有更多知识）：
        BGM29  rel = **−7.9 dB** → hp 0   + 加 sub   → 实测 0.82 dB
        BGM35  rel = **−18.2 dB** → hp 36 + 不加 sub → 实测 1.60 dB
        hp = 0 if rel ≥ −8 else min(36, (−rel − 8) × 36/7)；sub 只在 rel ≥ −10 时开。
    **第三首曲子必须复验这两个锚点**（只有两点定的线，外推没有证据）。
    """
    import band_grade as G
    b, _mono, _x, _rms = G.bands44(ref)
    piv = b[G.NAMES.index('80-160')]
    rel = b[G.NAMES.index('20-40')] - piv
    hp = 0.0 if rel >= -8.0 else min(36.0, (-rel - 8.0) * 36.0 / 7.0)
    sub = rel >= -10.0
    return hp, sub, rel


def needs_redo(path, force_tags, tag, up=()):
    """产物是否要重做 —— **真的比时间戳**，不是"文件在不在"。

    ⚠ 2026-09-19 修（这条是"改了却听不到"的静默陷阱）：原实现只有
    `return tag in force or not have(path)` —— **名字叫 stale 却不看时间**。
    实测后果：`--force bass` 重跑集成、`song.mid` 已改写，而第 6/7/8 段照样打印
    "已存在，跳过" → 命令 `exit=0`、日志写"== 完成 =="，**成品音频还是上一版**
    （三首 18 秒跑完就是这么来的）。用户听到旧音频，只会得出"改了没用"。
    现在：任一上游产物比它新 → 重做（`up` = 该阶段的上游列表）。

    抽成**模块级**函数是为了能被单测（原来它是 `main()` 里的闭包，只能靠读源码猜）。
    """
    if tag in (force_tags or ()) or not os.path.exists(path):
        return True
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return True
    return any(os.path.exists(u) and os.path.getmtime(u) > mt for u in (up or ()))


def main():
    ap = argparse.ArgumentParser(description='参考曲模仿流水线')
    ap.add_argument('ref', help='参考音频（ogg/wav/flac/mp3）')
    ap.add_argument('-o', '--out', required=True, help='项目目录')
    ap.add_argument('--name', default=None, help='曲名（默认取参考文件名）')
    ap.add_argument('--jobs', type=int, default=0, help='保留参数（当前各阶段内部已并行）')
    ap.add_argument('--from', dest='from_stage', default='stems', choices=STAGES,
                    help='从哪个阶段开始（之前的阶段仍会检查产物是否存在）')
    ap.add_argument('--force', action='append', default=[], help='强制重跑某阶段，可重复')
    ap.add_argument('--rms', type=float, default=-16.10, help='成品 RMS 目标')
    ap.add_argument('--width', type=float, default=None, help='成品宽度（默认对齐参考曲）')
    ap.add_argument('--thr', type=float, default=0.90, help='低音集成阈值（历史链，别乱动）')
    ap.add_argument('--merge-thr', type=float, default=0.30,
                    help='非低音声部的集成阈值（`--merge` 合并式）。'
                         '实测 BGM35：0.90 → Piano 391 音；**0.30 → 3896**（认可版 v5 是 4143）')
    ap.add_argument('--no-sub', action='store_true', help='强制不做低八度 sub 层')
    ap.add_argument('--sub', action='store_true', help='强制做 sub 层（覆盖自适应判断）')
    ap.add_argument('--hp', type=float, default=None,
                    help='渲染高通频率（缺省 = 按参考曲低频含量自适应）')
    ap.add_argument('--bsz', type=int, default=0,
                    help='YourMT3 推理 batch；**0 = auto**（按显存自动挑，8GB 卡 → 24）')
    ap.add_argument('--dur-floor', type=float, default=0.55,
                    help='时值下限（拍）——对**每条旋律轨**生效（鼓/打击轨不动）。'
                         '实测依据见下（用户"太杂乱、不流畅"那次的量化）；0 = 关')
    ap.add_argument('--absorb', default='Synth Pad,Organ,Synth Lead,Chromatic Percussion',
                    help='并进 Acoustic Piano 的轨名（逗号分隔）——YMT3 的合成器/键盘通道。'
                         '**给空串 = 不并**（保留原轨数）')
    a = ap.parse_args()
    # ⚠ 2026-09-19：原来 default=32 且**显式传给子进程** —— 于是 `transcribe_ymt3.pick_bsz`
    #   的 8GB 档自动下调（32→24）**根本走不到**。实测 8GB 卡上 bsz=32 吃 7.68/8.15GB（94%）
    #   → WDDM 分页到共享显存 → 同一首 282s 曲子转录 >7.5 分钟未完（ML.md 基准 ≈100s）。
    #   0 = 不传 `--bsz`，交给子脚本按显存自动挑（换机器也对）。
    _BS = ['--bsz', str(a.bsz)] if a.bsz > 0 else []

    ref = os.path.abspath(a.ref)
    if not os.path.isfile(ref):
        raise SystemExit('找不到参考音频：%s' % ref)
    # 面板守卫（硬形式）：没在跑就先拉起来 —— 见 scripts/studio_guard.py 顶部那段。
    # ⚠ 2026-09-19 补：这条流水线**本来是缺口** —— `new_song`/`make_song`/`melody_gen`
    #   早就接了守卫，而"仿写/还原"这条主路没接，于是仿写时面板依然是"等用户开口才接上"。
    #   面板是**辅助通道**：起不来只警告不中断（stdout/stderr 也照样跑得完）。
    import studio_guard
    studio_guard.ensure_panel()
    name = a.name or os.path.splitext(os.path.basename(ref))[0]
    # ⚠ 分轨目录名来自**音频文件名**（demucs 自己按输入文件命名），而转录产物名来自 --name ——
    #    两者可能大小写/拼写不同（BGM29.ogg → stems/htdemucs/BGM29，但转录叫 bgm29_ymt3）。
    stem_name = os.path.splitext(os.path.basename(ref))[0]
    P = os.path.abspath(a.out)
    os.makedirs(P, exist_ok=True)
    stems = os.path.join(P, 'stems')
    ymt = os.path.join(P, 'ymt3')
    bpdir = os.path.join(P, 'midi')
    for d in (stems, ymt, bpdir):
        os.makedirs(d, exist_ok=True)

    print('== 参考 %s（曲名 %s）→ %s ==' % (os.path.basename(ref), name, P))
    print('   venv：主 %s | ml %s | bp %s' % (have(PY_MAIN), have(PY_ML), have(PY_BP)))
    started = STAGES.index(a.from_stage)

    def stage(i, tag):
        return i >= started or tag in a.force

    def stale(path, tag, up=()):
        """→ `needs_redo`（模块级，可被单测；语义见那里的 docstring）"""
        return needs_redo(path, a.force, tag, up)

    # ── 1 分轨 ──────────────────────────────────────────────────────────────
    s4 = os.path.join(stems, 'htdemucs', stem_name)
    s6 = os.path.join(stems, 'htdemucs_6s', stem_name)
    if stage(0, 'stems') and (stale(os.path.join(s6, 'bass.wav'), 'stems', [ref])
                              or stale(os.path.join(s4, 'bass.wav'), 'stems', [ref])):
        print('\n[1/9] 分轨（htdemucs + htdemucs_6s）')
        sh([PY_ML, os.path.join(HERE, 'stem_split.py'), ref, '-o', stems, '-m', 'both'], 'stems')
    else:
        print('\n[1/9] 分轨 —— 已存在，跳过')

    # ── 2 整曲转录 ──────────────────────────────────────────────────────────
    ymt_mid = os.path.join(ymt, '%s_ymt3.mid' % name)
    if stage(1, 'ymt3') and stale(ymt_mid, 'ymt3', [ref]):
        print('\n[2/9] YourMT3 整曲转录')
        sh([PY_ML, os.path.join(HERE, 'transcribe_ymt3.py'), ref, '-o', ymt,
            '--name', '%s_ymt3' % name] + _BS, 'ymt3')
    else:
        print('\n[2/9] 整曲转录 —— 已存在，跳过')

    # ── 3 bass 分轨单独转录（低音补强的关键一步）────────────────────────────
    yb_mid = os.path.join(ymt, '%s_bass_ymt3.mid' % name)
    bass_wav = os.path.join(s4, 'bass.wav')
    if stage(2, 'ymt3bass') and stale(yb_mid, 'ymt3bass', [bass_wav]):
        print('\n[3/9] YourMT3 对 bass 分轨单独转录')
        sh([PY_ML, os.path.join(HERE, 'transcribe_ymt3.py'), bass_wav, '-o', ymt,
            '--name', '%s_bass_ymt3' % name] + _BS, 'ymt3bass')
    else:
        print('\n[3/9] bass 分轨转录 —— 已存在，跳过')

    # ── 4 Basic Pitch 6 源 ──────────────────────────────────────────────────
    if not have(PY_BP):
        print('\n[4/9] Basic Pitch —— 跳过（没有 %s，可用环境变量 BP_PY 指定）' % PY_BP)
    else:
        jobs = [('piano6', os.path.join(s6, 'piano.wav'), 'piano'),
                ('guitar6', os.path.join(s6, 'guitar.wav'), 'guitar'),
                ('other6', os.path.join(s6, 'other.wav'), 'other'),
                ('bass6', os.path.join(s6, 'bass.wav'), 'bass'),
                ('other4', os.path.join(s4, 'other.wav'), 'other'),
                ('bass4', os.path.join(s4, 'bass.wav'), 'bass')]
        todo = [(t, w, k) for (t, w, k) in jobs
                if stage(3, 'bp') and os.path.isfile(w)
                and stale(os.path.join(bpdir, 'bp_%s.mid' % t), 'bp', [w])]
        if todo:
            print('\n[4/9] Basic Pitch（%d 条分轨）' % len(todo))
            for tag, wav, kind in todo:
                lo, hi = RANGES[kind]
                f = lambda p: '%.1f' % (440.0 * 2 ** ((p - 69) / 12.0))       # noqa: E731
                sh([PY_BP, os.path.join(HERE, 'bp_transcribe.py'), wav,
                    os.path.join(bpdir, 'bp_%s.mid' % tag),
                    '--fmin', f(lo), '--fmax', f(hi)], 'bp-%s' % tag)
        else:
            print('\n[4/9] Basic Pitch —— 已存在，跳过')

    # ── 5 低音专项集成（+ sub 层）───────────────────────────────────────────
    hp_hz, sub_on, rel2040 = low_strategy(ref)
    if a.hp is not None:
        hp_hz = a.hp
    if a.no_sub:
        sub_on = False
    if a.sub:
        sub_on = True
    print('\n  低频策略（按参考曲推）：参考 20-40 比 80-160 低 %.1f dB → 高通 %.0fHz · sub 层 %s'
          % (rel2040, hp_hz, '开' if sub_on else '关'))

    song = os.path.join(P, 'song.mid')
    # ⚠ 集成的上游是三条转录产物；**参数（--dur-floor / --absorb / --merge-thr）变了它看不出**
    #   —— 改这些参数时用 `--force bass`（本次就是这么重跑的）。
    if stage(4, 'bass') and stale(song, 'bass', [ymt_mid, yb_mid]):
        print('\n[5/9] 多声部集成（Bass / Piano / Guitar / Strings × 多来源%s）'
              % (' + sub 层' if sub_on else ''))
        # ── 2026-09-19 扩：原来只集成 **Bass 一条**，而第 4 段跑的 6 条 BP 分轨转录里
        #    另外 4 条（piano/guitar/other×2）**跑完没人用** → Piano 一直是 YMT3 单通道原样。
        #    实测（BGM35，对照用户认可版 v5_source.mid 的 Piano 4143 / 全曲 10413）：
        #      替换式集成 thr=0.90 → Piano 391 · 全曲 6685（比不集成还差）
        #      **--merge + thr=0.30 → Piano 3896 · 全曲 10190**（追到 v5 的 94% / 98%）
        #    所以：非低音声部一律 `--merge`（合并而非替换）+ 低阈值，低音那条走它自己的历史链。

        def _bp(tag):
            return os.path.join(bpdir, 'bp_%s.mid' % tag)

        def _ens(layer, srcs, cur_in, out, thr, merge=False, extra=None, mb=0.0):
            cmd = [PY_ML, os.path.join(HERE, 'bass_ensemble.py'),
                   '--base', cur_in, '--out', out, '--layer', layer, '--thr', str(thr)]
            for nm, p, lo, hi in srcs:
                if have(p):
                    cmd += ['--source', '%s=%s|%d|%d' % (nm, p, lo, hi)]
            # 时值下限（`--dur-floor`）：**只给旋律层** —— 鼓/打击轨本来就该是碎音
            # （认可版 v5 的 Perc 轨碎音也是 100%，时值 0.03 拍），拉长鼓会糊成一团。
            if mb > 0:
                cmd += ['--min-beats', '%g' % mb]
            if merge:
                cmd += ['--merge']
            if extra:
                cmd += extra
            sh(cmd, 'ens-%s' % layer.split()[0])
            return out

        cur = ymt_mid
        tmp = lambda i: os.path.join(P, 'ens_%d.mid' % i)                     # noqa: E731
        # 时值下限：**只给旋律层**（鼓/打击轨走 YMT3 原样 —— 认可版 v5 的 Perc 也是
        # 0.03 拍 / 碎音 100%，拉长鼓只会糊成一团）。数值依据（2026-09-19 逐轨量）：
        #   我的 BGM16 → Piano 0.38 拍/碎音 30.4% · Bass 0.35/22.1% · Guitar 0.20/92.5%
        #   认可版 v5   → Piano 0.66 拍/碎音  2.7% · Bass 0.58/ 2.7% · Guitar 0.86/ 3.9%
        # 单轨验证过：Piano 加 `--min-beats 1.0` → 碎音 30.4% → **0.0%**（flag 确实生效）。
        _DF = a.dur_floor
        # ① 低音：三源交叉 + 八度校正 + 可选 sub。**也走 merge + 低阈值** ——
        #    实测走历史链的 thr=0.90 只剩 386 音（v5 是 1677），而 0.30+merge 能追回来。
        cur = _ens('Bass',
                   [('ymt3b', yb_mid, 24, 60), ('bass4', _bp('bass4'), 24, 60),
                    ('bass6', _bp('bass6'), 24, 60)],
                   cur, tmp(0), a.merge_thr, merge=True, mb=_DF,
                   extra=(['--sub'] if sub_on else []) +
                         (['--octave-ref', bass_wav] if os.path.isfile(bass_wav) else []))
        # ② 钢琴（YMT3 的 Acoustic Piano 轨由 `--merge` 并入来源）
        cur = _ens('Acoustic Piano',
                   [('bp_piano6', _bp('piano6'), 21, 108),
                    ('bp_other6', _bp('other6'), 21, 108)],
                   cur, tmp(1), a.merge_thr, merge=True, mb=_DF)
        # ③ 吉他：**只用 6s 模型的 guitar 轨** —— 曾误加 `bp_other4`（4s 的 other 是
        #    钢琴/弦乐/其它混在一起），实测把 Guitar 顶到 2253 音（v5 只 254，多 8.9 倍）。
        cur = _ens('Guitar (clean)',
                   [('bp_guitar6', _bp('guitar6'), 40, 88)],
                   cur, tmp(2), a.merge_thr, merge=True, mb=_DF)
        # ④ 弦乐
        cur = _ens('Strings',
                   [('bp_other6', _bp('other6'), 36, 96)],
                   cur, song, a.merge_thr, merge=True, mb=_DF)
        # ⑤ 并轨（2026-09-19 新增）：把 YMT3 的**合成器/键盘通道**并进 Piano。
        #    实测病根：集成后仍有 9 条轨 —— Synth Pad 637 音（碎音 68%）、Organ 294（81%）、
        #    Chromatic Percussion 315（84%）、Synth Lead 159（21%），**而认可版 v5 只有 5 条轨**
        #    （Piano / Perc / Bass / Strings / Guitar），这些音是被并进 Piano 的。
        #    并完正好 5 轨（我的 Drums 2004 + Piano 3084 + Bass 910 + Guitar 268 + Strings 193）。
        #    放在**最后**：合成器轨没经过 ①~④ 的集成，只有并进来才一起吃到 `--min-beats`。
        _abs = [s.strip() for s in (a.absorb or '').split(',') if s.strip()]
        if _abs:
            sh([PY_MAIN, os.path.join(HERE, 'merge_tracks.py'), song,
                '--into', 'Acoustic Piano', '--from', ','.join(_abs),
                '--min-beats', '%g' % _DF] if _DF > 0 else
               [PY_MAIN, os.path.join(HERE, 'merge_tracks.py'), song,
                '--into', 'Acoustic Piano', '--from', ','.join(_abs)], 'absorb')
    else:
        print('\n[5/9] 多声部集成 —— 已存在，跳过')

    # ── 6 渲染（高通频率由参考曲低频含量决定）──────────────────────────────
    raw = os.path.join(P, 'render.wav')
    if stage(5, 'render') and stale(raw, 'render', [song]):
        print('\n[6/9] 渲染（高通 %.0fHz）' % hp_hz)
        sh([PY_MAIN, os.path.join(HERE, 'render_midi.py'), song, os.path.join(P, 'render'),
            '--width', '1.4', '--rms', '-16.0', '--shelf', '6.0', '--hp', '%g' % hp_hz,
            '--low', '2.0', '--drive', '1.2', '--mid', '4.0'], 'render')
    else:
        print('\n[6/9] 渲染 —— 已存在，跳过')

    # ── 7 band_match 两遍 ───────────────────────────────────────────────────
    bm2 = os.path.join(P, 'render_bm2.wav')
    if stage(6, 'match') and stale(bm2, 'match', [raw]):
        print('\n[7/9] band_match ×2')
        bm1 = os.path.join(P, 'render_bm.wav')
        sh([PY_MAIN, os.path.join(HERE, 'band_match.py'), raw, ref, bm1, '--max-gain', '6'], 'match1')
        sh([PY_MAIN, os.path.join(HERE, 'band_match.py'), bm1, ref, bm2, '--max-gain', '6'], 'match2')
    else:
        print('\n[7/9] band_match —— 已存在，跳过')

    # ── 8 收尾：宽度 → RMS → ogg ────────────────────────────────────────────
    out_ogg = os.path.join(P, '%s.ogg' % name)
    # ⚠ **自我覆盖拦截**（2026-09-19 实测踩到，代价是全项目数据被污染）：
    #   成品名默认 = 曲名 = 参考文件名，`-o` 目录若也放着参考音频（很自然的做法：
    #   把原曲拷进项目目录），收尾这一步就会**用成品覆盖参考**。
    #   实测后果链：`BGM16.ogg`（原曲 5,274,147 B）被写成成品（9,360,725 B）→
    #   下一次跑拿它当 ref → band_match 把成品对齐到"上一版成品"而不是原曲；
    #   更糟的是**当时看不出任何异常**（exit=0、日志"== 完成 =="）。
    #   拦在这里而不是改名：参考**不该**躺在输出目录里（用绝对路径引用原曲即可）。
    if os.path.abspath(out_ogg) == ref:
        raise SystemExit(
            '成品路径与参考音频是同一个文件：%s\n'
            '  收尾会**覆盖参考原曲**（自我覆盖）。请二选一：\n'
            '   · 参考用原曲的绝对路径（推荐，输出目录里只放成品）\n'
            '   · 或给 `--name` 换个成品名' % out_ogg)
    if stage(7, 'finish') and stale(out_ogg, 'finish', [bm2]):
        print('\n[8/9] 收尾（宽度 → RMS → ogg）')
        sh([PY_ML, os.path.join(HERE, 'master_finish.py'), bm2, os.path.join(P, '%s.wav' % name),
            ref, '--rms', str(a.rms)] + ([] if a.width is None else ['--width', str(a.width)]),
           'finish')
    else:
        print('\n[8/9] 收尾 —— 已存在，跳过')

    # ── 9 体检：**先报识别精度，再报混音平衡**（用户 2026-09-17 定的规矩）────────
    # 顺序不能反：识别错一个八度时，带差/EQ 这些指标照样能"变好"，
    # 于是会一路认真地加工一个错东西（BGM29 实测：低音补到 1416 音后带差 2.61→0.83dB，
    # 而那 1416 个音里有 25% 是错八度）。
    print('\n[9/9] ① 识别体检（转录 vs 参考分轨，先看这个）')
    if os.path.isfile(bass_wav):
        sh([PY_ML, os.path.join(HERE, 'transcribe_audit.py'), bass_wav, song,
            '--tracks', 'Bass', '--fmin', '40', '--fmax', '300'], 'audit')
    else:
        print('   （没有 bass 分轨，跳过识别体检）')
    print('\n[9/9] ② 逐带体检（统一 44.1kHz）')
    sh([PY_ML, os.path.join(HERE, 'band_grade.py'), ref, os.path.join(P, '%s.wav' % name)], 'report')
    print('\n== 完成 ==\n   成品 %s\n   面板：python studio/server.py --port 8765 --lib <项目父目录>'
          % out_ogg)
    return 0


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    sys.exit(main())
