#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""vote_views.py —— **一条命令跑完"多视图 → 族票装配"**（应用层：不训练、不下载、不改权重）

为什么有它：`vote_apply.py` 只管**装配**（给它 base + 视图，它按族票收新音），而"视图从哪来"
一直散在一次性 shell 里（`D:\test\_tmp\resid` 的 `r6_*` / `r8_*` 脚本）。本工具把那条链固定成
**仓库里可复现的一步**：

    音频 → Demucs 分轨(htdemucs_6s) → 拼 3 个 stem 混音（nodrums / harm / novox）
         → 视图：YMT3 ×2 + BP ×4（默认 cheap）
         → base = YMT3(全混音) ∪ BP(全混音)      ← 与留出集台账的 `best/<tag>_v0.mid` 同口径
         → vote_apply --family-min 1（族票）→ voted.mid（**音色按骨架还原**，坑 277）

**为什么默认 cheap（6 视图）**：10 首留出集实测 —— 9 视图 ΔF1 **+0.008317**、
**6 视图（2 YMT3 + 4 BP）+0.007509**（保住 90% 的收益，少跑 1 次 YMT3 + 2 次 BP）；
4 视图只剩 +0.004857、2 视图 +0.002021（精度高但收得极少）。
另有**免费**的一条：`bp_novox_on0.7` 去掉反而略涨（+0.008322）⇒ 它不在任何档里。
⚠ 这些数字是在 **htdemucs_6s** 的 stem 混音上标的：`--stems 4` 的 `harm` 只有 `other`
（没有 guitar/piano），**实测值不能直接套**，当未标定口径用。

用法：
  python scripts\vote_views.py <音频> <工作目录> [--base B.mid] [--out O.mid]
      [--profile cheap|full] [--stems 6s|4] [--s1/--no-s1] [--dry-run]

产物（全在工作目录下）：`log/*.log`（每步日志，别删）· `ymt3.mid`（**骨架**兼鼓来源）·
`bp.mid` · `base.mid` · `views/*.mid` · `voted.mid`。已存在的产物**不重跑**（幂等，可续跑）。
"""
import argparse
import os
import subprocess
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cli_utf8 as _cu          # noqa: E402
_cu.setup()

import truth_eval as TE         # noqa: E402
import vote_apply as VA         # noqa: E402

ML_PY = os.path.join(ROOT, '.venv-ml', 'Scripts', 'python.exe')     # Demucs / YMT3 都在这里

# stem 混音 = 哪些 stem 相加（名字与 r8 脚本一致；`--stems` 决定可用集合）
MIXES = {
    'nodrums': ('vocals', 'bass', 'other', 'guitar', 'piano'),      # 去鼓
    'harm': ('other', 'guitar', 'piano'),                           # 和声织体
    'novox': ('bass', 'other', 'guitar', 'piano'),                  # 去鼓去人声
}
STEM_SETS = {'6s': 'htdemucs_6s', '4': 'htdemucs'}

# 视图清单：('族', '视图名', '输入', 'BP onset') —— 输入是 'mix' 或 MIXES 的键
# cheap = 我的成本实验里"保住 90% 收益"的那 6 个；full = 第九/十三轮台账的 9 视图口径
PROFILES = {
    'cheap': [
        ('ymt3', 'ymt3_nodrums', 'nodrums', None),
        ('ymt3', 'ymt3_harm', 'harm', None),
        ('bp', 'bp_on70', 'mix', 0.7),
        ('bp', 'bp_nodrums_on70', 'nodrums', 0.7),
        ('bp', 'bp_novox_on0.5', 'novox', 0.5),
        ('bp', 'bp_harm_on0.5', 'harm', 0.5),
    ],
    'full': [
        ('ymt3', 'ymt3_nodrums', 'nodrums', None),
        ('ymt3', 'ymt3_harm', 'harm', None),
        ('ymt3', 'ymt3_novox', 'novox', None),
        ('bp', 'bp_on70', 'mix', 0.7),
        ('bp', 'bp_nodrums_on70', 'nodrums', 0.7),
        ('bp', 'bp_novox_on0.5', 'novox', 0.5),
        ('bp', 'bp_harm_on0.5', 'harm', 0.5),
        ('bp', 'bp_novox_on0.7', 'novox', 0.7),
        ('bp', 'bp_harm_on0.7', 'harm', 0.7),
    ],
}


def _need(path, what):
    if not os.path.exists(path):
        raise SystemExit('%s 不存在：%s' % (what, path))
    return path


def _product_sig(dst):
    """产物的"指纹"：文件看字节数，目录看里面 wav 的**个数+总字节**（递归，Demucs 会分子目录）。"""
    if os.path.isdir(dst):
        n, tot = 0, 0
        for root, _dirs, files in os.walk(dst):
            for f in files:
                if f.endswith('.wav'):
                    n += 1
                    tot += os.path.getsize(os.path.join(root, f))
        return (n, tot)
    return (1, os.path.getsize(dst)) if os.path.exists(dst) else (-1, -1)


def _wait_product(dst, log_path, timeout=3600):
    """等**产物真正落盘**再走下一步，返回等待秒数（超时返回 None）。

    ⚠ 为什么必须等：Windows 上 `os.execv`（`pyenv.ensure` 换解释器用的那条路）**不是真 exec**
    —— 它起一个新进程、原进程立刻退出，于是 `subprocess.call` 会在"模型还没开始跑"时就返回 0。
    实测：BP 那一步只报了 **0.2s**，而日志里它自己写着 `完成 1.7s · 音符 1762`（之前还得加载模型）。
    不管它，后面 `union`/装配就会**拿写了一半的 MIDI 算 base**（静默错，读数看不出异常）。
    判据：产物指纹与日志大小连续 3 秒不变（对 YMT3 与 BP 都适用，不依赖各自的措辞）。
    """
    t0 = time.time()
    last, stable = None, 0
    while time.time() - t0 < timeout:
        sig = (_product_sig(dst), os.path.getsize(log_path) if os.path.exists(log_path) else -1)
        if sig[0][0] > 0 and sig == last:
            stable += 1
            if stable >= 3:
                return time.time() - t0
        else:
            stable = 0
        last = sig
        time.sleep(1.0)
    return None


def _run(cmd, log_path, label, dst=None):
    """跑一步，日志落盘（别删日志），返回 (exit, 秒)。已存在的产物由调用方跳过。"""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    t0 = time.time()
    with open(log_path, 'w', encoding='utf-8') as fh:
        fh.write('$ %s\n\n' % ' '.join('"%s"' % c if ' ' in c else c for c in cmd))
        fh.flush()
        rc = subprocess.call(cmd, stdout=fh, stderr=subprocess.STDOUT)
    dt = time.time() - t0
    wait = None
    if dst is not None:
        wait = _wait_product(dst, log_path)
        if wait is None:
            raise SystemExit('等产物超时（>1h）：%s —— 看 %s' % (dst, log_path))
        dt += wait
    print('  %-22s exit=%d  %.1fs%s  → %s'
          % (label, rc, dt, ('（含等落盘 %.0fs）' % wait) if wait and wait > 3 else '',
             os.path.basename(log_path)))
    return rc, dt


def build_mixes(stem_dir, out_dir, stems, log_dir):
    """按 MIXES 把 stem 相加写成 `<out_dir>/<name>.wav`（缺的 stem 跳过，与 r8 脚本同口径）。"""
    import numpy as np
    import soundfile as sf
    avail = set(stems)
    made = []
    for name, want in MIXES.items():
        use = [s for s in want if s in avail]
        if not use:
            continue
        dst = os.path.join(out_dir, '%s.wav' % name)
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            made.append(dst)
            continue
        y = None
        sr = None
        for s in use:
            x, sr = sf.read(os.path.join(stem_dir, s + '.wav'), always_2d=True)
            y = x if y is None else y + x
        sf.write(dst, y.astype(np.float32), sr)
        print('  混音 %-8s = %-28s → %s' % (name, '+'.join(use), os.path.basename(dst)))
        made.append(dst)
    return made


def plan_steps(audio, work, profile, stems, base_mid, out_mid):
    """→ [(kind, 说明, 命令行 or None, 产物)]；`--dry-run` 只打印它（自检也读它）。"""
    views_dir = os.path.join(work, 'views')
    ymt3_mid = os.path.join(work, 'ymt3.mid')
    bp_mid = os.path.join(work, 'bp.mid')
    base = base_mid or os.path.join(work, 'base.mid')
    # ⚠ Demucs 的产物是**每首歌一个子目录**（`stems/<模型>/<曲名>/*.wav`）——
    #   指到模型那一层会永远等不到 wav（实测把驱动卡死在等产物上，白等 1 小时超时）。
    stem_dir = os.path.join(work, 'stems', STEM_SETS[stems],
                            os.path.splitext(os.path.basename(audio))[0])
    steps = [('demucs', 'Demucs %s 分轨' % STEM_SETS[stems],
              [ML_PY, '-m', 'demucs', '-n', STEM_SETS[stems], '-o', os.path.join(work, 'stems'),
               audio], stem_dir),
             ('mix', '拼 stem 混音（nodrums/harm/novox）', None, views_dir),
             ('ymt3', 'YMT3 全混音（骨架 + base 来源 + 鼓）',
              [ML_PY, os.path.join(HERE, 'transcribe_ymt3.py'), audio, '-o', work,
               '--name', 'ymt3', '--no-song'], ymt3_mid),
             ('bp', 'BP 全混音 onset 0.5（base 来源）',
              [sys.executable, os.path.join(HERE, 'bp_transcribe.py'), audio, bp_mid], bp_mid)]
    if not base_mid:
        steps.append(('union', 'base = YMT3 ∪ BP（对齐后按 0.1s 格并集）', None, base))
    for fam, name, src, onset in PROFILES[profile]:
        dst = os.path.join(views_dir, '%s.mid' % name)
        if fam == 'ymt3':
            src_audio = os.path.join(views_dir, '%s.wav' % src)
            cmd = [ML_PY, os.path.join(HERE, 'transcribe_ymt3.py'), src_audio, '-o', views_dir,
                   '--name', name, '--no-song']
        else:
            src_audio = audio if src == 'mix' else os.path.join(views_dir, '%s.wav' % src)
            cmd = [sys.executable, os.path.join(HERE, 'bp_transcribe.py'), src_audio, dst,
                   '--onset', str(onset)]
        steps.append(('view', '%s（%s）' % (name, fam), cmd, dst))
    views = [os.path.join(views_dir, '%s.mid' % n) for (_f, n, _s, _o) in PROFILES[profile]]
    steps.append(('vote', '族票装配（每族≥1 · 骨架=%s）' % os.path.basename(ymt3_mid),
                  None, out_mid))
    return steps, views, base, ymt3_mid, views_dir


def plan_problems(views):
    """计划自检：**族票的前提是"至少两个族、每族至少一个视图"** —— 返回问题清单（空 = 没问题）。

    为什么单列出来：这条链最容易犯的退化是"视图只剩一族"（比如只跑 BP）——
    那时族票**静默退化成单族过滤**，产品照样出、读数照样有，只是收益没了（PITFALLS 294 同族）。
    自检项直接调它，所以它是**能被注入故障证明会红**的判据，不是打印。
    """
    fams = defaultdict(int)
    for p in views:
        fams[VA.family_of(os.path.basename(p)[:-4])] += 1
    bad = []
    if len(views) < 2:
        bad.append('视图只有 %d 个' % len(views))
    if len(fams) < 2:
        bad.append('只有 %d 个族（%s）—— 族票要求至少两个族'
                   % (len(fams), ', '.join(sorted(fams)) or '无'))
    if any(v < 1 for v in fams.values()):
        bad.append('有族一个视图都没有')
    return bad


def vote_argv(base, out_mid, views_dir, names, skeleton, s1=True):
    """族票装配的命令行（**规则必须显式写死在这里**，别让它悄悄退回 `--k`）。"""
    argv = [base, base, out_mid, '--family-min', '1']
    if skeleton:
        argv += ['--skeleton', skeleton]
    if s1:
        argv.append('--s1')
    for n in names:
        argv += ['--view', '%s=%s' % (n, os.path.join(views_dir, '%s.mid' % n))]
    return argv


def union_base(out_mid, *srcs):
    """N 个来源**先对齐再并集**（鼓只取第一个来源）—— 与 `make_union.py` 同口径。"""
    acc, base_nd, drums = {}, None, []
    for i, p in enumerate(srcs):
        ns, _m = TE.load_notes(p, drop_drum=False)
        nd = [x for x in ns if x[4] != 'drums']
        off = 0.0
        if i == 0:
            base_nd, drums = nd, [x for x in ns if x[4] == 'drums']
        else:
            off = TE.best_dt(base_nd, nd)[0]
        for x in nd:
            acc.setdefault((int(round((x[0] + off) / VA.GRID)), x[2]),
                           (x[0] + off, x[1], x[2], x[3]))
    union = [acc[k] for k in sorted(acc)]
    VA.rebuild([('pitched', 1, 0, False, list(union)),
                ('drums', 9, 0, True, [(x[0], x[1], x[2], x[3]) for x in drums])], out_mid)
    print('  base 并集 %d 音（%d 来源 · 鼓 %d）→ %s'
          % (len(union), len(srcs), len(drums), os.path.basename(out_mid)))
    return out_mid


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='一条命令：音频 → 多视图 → 族票装配（默认 cheap 6 视图；不跑训练/不下模型）')
    ap.add_argument('audio', help='原曲音频（mp3/wav/flac 都行）')
    ap.add_argument('work', help='工作目录（产物与日志都写这里）')
    ap.add_argument('--base', help='已知 base MIDI（不给就现造 YMT3 ∪ BP）')
    ap.add_argument('--out', help='族票装配产物（默认 <工作目录>/voted.mid）')
    ap.add_argument('--profile', choices=sorted(PROFILES), default='cheap',
                    help='cheap=6 视图（实测 ΔF1 +0.0075）· full=9 视图（+0.0083）')
    ap.add_argument('--stems', choices=sorted(STEM_SETS), default='6s',
                    help='分轨模型：6s = htdemucs_6s（实测口径）· 4 = htdemucs')
    ap.add_argument('--no-s1', action='store_true', help='不打印 S1（默认打印）')
    ap.add_argument('--dry-run', action='store_true', help='只打印计划，不跑任何模型')
    a = ap.parse_args(argv)

    _need(a.audio, '音频')
    work = os.path.abspath(a.work)
    out_mid = a.out or os.path.join(work, 'voted.mid')
    base_mid = a.base
    if base_mid:
        _need(base_mid, 'base MIDI')
    steps, views, base, ymt3_mid, views_dir = plan_steps(
        a.audio, work, a.profile, a.stems, base_mid, out_mid)

    fams = sorted({VA.family_of(os.path.basename(p)[:-4]) for p in views})
    probs = plan_problems(views)
    print('=== vote_views · profile=%s（%d 视图 · 族：%s）· stems=%s'
          % (a.profile, len(views), ', '.join(fams), STEM_SETS[a.stems]))
    if probs:
        raise SystemExit('视图计划不成立：%s' % '；'.join(probs))
    if a.dry_run:
        for kind, label, cmd, dst in steps:
            print('  [%s] %s\n        %s' % (kind, label, ' '.join(cmd) if cmd else '(内部步骤)'))
        print('  装配规则：%s' % ' '.join(vote_argv(base, out_mid, views_dir,
                                                   [n for (_f, n, _s, _o) in
                                                    PROFILES[a.profile]], ymt3_mid)[3:9]))
        return 0
    os.makedirs(views_dir, exist_ok=True)
    log_dir = os.path.join(work, 'log')
    cost = {}
    stem_dir = os.path.join(work, 'stems', STEM_SETS[a.stems],
                            os.path.splitext(os.path.basename(a.audio))[0])
    for kind, label, cmd, dst in steps:
        # ⚠ 顺序是**有依赖**的：分轨 → 拼混音 → 视图（YMT3/BP 吃的是混音 wav）→ base 并集 → 装配。
        #   第一版把"拼混音"排在视图**之后**，于是 YMT3 视图对着不存在的 nodrums.wav 跑，
        #   报的是 libsndfile 的 `System error`（看着像音频坏了，其实是顺序错）。
        if kind == 'mix':
            avail = sorted(f[:-4] for f in os.listdir(stem_dir) if f.endswith('.wav')) \
                if os.path.isdir(stem_dir) else []
            if not avail:
                raise SystemExit('分轨目录里没有 wav：%s（看 %s/demucs.log）' % (stem_dir, log_dir))
            t0 = time.time()
            build_mixes(stem_dir, views_dir, avail, log_dir)
            cost['拼 stem 混音'] = time.time() - t0
            continue
        if kind == 'union':
            t0 = time.time()
            union_base(base, ymt3_mid, os.path.join(work, 'bp.mid'))
            cost['base 并集'] = time.time() - t0
            continue
        if kind == 'vote':
            continue
        if _product_sig(dst)[0] > 0:      # 产物已在（文件算 1，目录按里面的 wav 数）→ 跳过
            print('  %-22s 已存在，跳过' % label)
            continue
        rc, dt = _run(cmd, os.path.join(log_dir, '%s.log' % kind if kind != 'view'
                                        else '%s.log' % os.path.basename(dst)[:-4]), label, dst)
        cost[label] = dt
        if rc != 0:
            raise SystemExit('步骤失败（exit=%d）：%s —— 看 %s/log/'
                             % (rc, label, work))

    missing = [p for p in views if not (os.path.exists(p) and os.path.getsize(p) > 0)]
    if missing:
        raise SystemExit('视图没生成齐：%s' % ', '.join(os.path.basename(m) for m in missing))

    print('--- 族票装配（base=%s · 骨架=%s）' % (os.path.basename(base),
                                                os.path.basename(ymt3_mid)))
    t0 = time.time()
    rc = VA.main(vote_argv(base, out_mid, views_dir,
                           [n for (_f, n, _s, _o) in PROFILES[a.profile]],
                           ymt3_mid, s1=not a.no_s1))
    cost['族票装配'] = time.time() - t0
    if rc != 0:
        raise SystemExit('族票装配失败：exit=%d' % rc)

    print('--- 成本（本机实测，仅供估算）')
    for k, v in cost.items():
        print('  %-34s %.1fs' % (k, v))
    print('  合计 %.1fs' % sum(cost.values()))
    print('产物：%s（骨架音色已按 %s 还原）' % (out_mid, os.path.basename(ymt3_mid)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
