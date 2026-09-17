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
| ymt3 | YourMT3+ 整曲转录（bsz=32） | 2.3× 快、产物与官方一致；多乐器一次出 13 通道 |
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
    """
    import band_grade as G
    b, _mono, _x, _rms = G.bands44(ref)
    piv = b[G.NAMES.index('80-160')]
    rel = b[G.NAMES.index('20-40')] - piv
    if rel < -15:
        hp, sub = 36.0, False
    elif rel < -10:
        hp, sub = 28.0, False
    elif rel < -6:
        hp, sub = 18.0, False
    else:
        hp, sub = 0.0, True
    return hp, sub, rel


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
    ap.add_argument('--thr', type=float, default=0.90, help='低音集成阈值')
    ap.add_argument('--no-sub', action='store_true', help='强制不做低八度 sub 层')
    ap.add_argument('--sub', action='store_true', help='强制做 sub 层（覆盖自适应判断）')
    ap.add_argument('--hp', type=float, default=None,
                    help='渲染高通频率（缺省 = 按参考曲低频含量自适应）')
    ap.add_argument('--bsz', type=int, default=32)
    a = ap.parse_args()

    ref = os.path.abspath(a.ref)
    if not os.path.isfile(ref):
        raise SystemExit('找不到参考音频：%s' % ref)
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

    def stale(path, tag):
        return tag in a.force or not have(path)

    # ── 1 分轨 ──────────────────────────────────────────────────────────────
    s4 = os.path.join(stems, 'htdemucs', stem_name)
    s6 = os.path.join(stems, 'htdemucs_6s', stem_name)
    if stage(0, 'stems') and (stale(os.path.join(s6, 'bass.wav'), 'stems')
                              or stale(os.path.join(s4, 'bass.wav'), 'stems')):
        print('\n[1/9] 分轨（htdemucs + htdemucs_6s）')
        sh([PY_ML, os.path.join(HERE, 'stem_split.py'), ref, '-o', stems, '-m', 'both'], 'stems')
    else:
        print('\n[1/9] 分轨 —— 已存在，跳过')

    # ── 2 整曲转录 ──────────────────────────────────────────────────────────
    ymt_mid = os.path.join(ymt, '%s_ymt3.mid' % name)
    if stage(1, 'ymt3') and stale(ymt_mid, 'ymt3'):
        print('\n[2/9] YourMT3 整曲转录')
        sh([PY_ML, os.path.join(HERE, 'transcribe_ymt3.py'), ref, '-o', ymt,
            '--name', '%s_ymt3' % name, '--bsz', str(a.bsz)], 'ymt3')
    else:
        print('\n[2/9] 整曲转录 —— 已存在，跳过')

    # ── 3 bass 分轨单独转录（低音补强的关键一步）────────────────────────────
    yb_mid = os.path.join(ymt, '%s_bass_ymt3.mid' % name)
    bass_wav = os.path.join(s4, 'bass.wav')
    if stage(2, 'ymt3bass') and stale(yb_mid, 'ymt3bass'):
        print('\n[3/9] YourMT3 对 bass 分轨单独转录')
        sh([PY_ML, os.path.join(HERE, 'transcribe_ymt3.py'), bass_wav, '-o', ymt,
            '--name', '%s_bass_ymt3' % name, '--bsz', str(a.bsz)], 'ymt3bass')
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
                if stage(3, 'bp') and os.path.isfile(w) and stale(os.path.join(bpdir, 'bp_%s.mid' % t), 'bp')]
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
    if stage(4, 'bass') and stale(song, 'bass'):
        print('\n[5/9] 低音专项集成（三源交叉验证%s）' % (' + sub 层' if sub_on else '，不加 sub'))
        cmd = [PY_ML, os.path.join(HERE, 'bass_ensemble.py'),
               '--base', ymt_mid, '--out', song, '--layer', 'Bass', '--thr', str(a.thr)]
        if have(yb_mid):
            cmd += ['--source', 'ymt3b=%s|24|60' % yb_mid]
        for tag in ('bass4', 'bass6'):
            p = os.path.join(bpdir, 'bp_%s.mid' % tag)
            if have(p):
                cmd += ['--source', '%s=%s|24|60' % (tag, p)]
        if sub_on:
            cmd += ['--sub']
        sh(cmd, 'bass')
    else:
        print('\n[5/9] 低音集成 —— 已存在，跳过')

    # ── 6 渲染（高通频率由参考曲低频含量决定）──────────────────────────────
    raw = os.path.join(P, 'render.wav')
    if stage(5, 'render') and stale(raw, 'render'):
        print('\n[6/9] 渲染（高通 %.0fHz）' % hp_hz)
        sh([PY_MAIN, os.path.join(HERE, 'render_midi.py'), song, os.path.join(P, 'render'),
            '--width', '1.4', '--rms', '-16.0', '--shelf', '6.0', '--hp', '%g' % hp_hz,
            '--low', '2.0', '--drive', '1.2', '--mid', '4.0'], 'render')
    else:
        print('\n[6/9] 渲染 —— 已存在，跳过')

    # ── 7 band_match 两遍 ───────────────────────────────────────────────────
    bm2 = os.path.join(P, 'render_bm2.wav')
    if stage(6, 'match') and stale(bm2, 'match'):
        print('\n[7/9] band_match ×2')
        bm1 = os.path.join(P, 'render_bm.wav')
        sh([PY_MAIN, os.path.join(HERE, 'band_match.py'), raw, ref, bm1, '--max-gain', '6'], 'match1')
        sh([PY_MAIN, os.path.join(HERE, 'band_match.py'), bm1, ref, bm2, '--max-gain', '6'], 'match2')
    else:
        print('\n[7/9] band_match —— 已存在，跳过')

    # ── 8 收尾：宽度 → RMS → ogg ────────────────────────────────────────────
    out_ogg = os.path.join(P, '%s.ogg' % name)
    if stage(7, 'finish') and stale(out_ogg, 'finish'):
        print('\n[8/9] 收尾（宽度 → RMS → ogg）')
        sh([PY_ML, os.path.join(HERE, 'master_finish.py'), bm2, os.path.join(P, '%s.wav' % name),
            ref, '--rms', str(a.rms)] + ([] if a.width is None else ['--width', str(a.width)]),
           'finish')
    else:
        print('\n[8/9] 收尾 —— 已存在，跳过')

    # ── 9 体检 ──────────────────────────────────────────────────────────────
    print('\n[9/9] 逐带体检（统一 44.1kHz）')
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
