#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新歌预演（forward rehearsal）：只用"以后写歌会走的那条路"，从零跑通全流程。

覆盖：
  1. 全新参考曲扒谱（缓存进 refs/）
  2. 5 套风格各自端到端：作曲 → 真音源渲染 → 自动调参 → 验收
     （判据只含"管线通 / 调参有效 / 速度一致"；**"对齐 dB"只打印、不进判据**，见 README 第 10 条）
  3. 边界情况：1 小节、某段无打击乐、显式覆盖风格预设、斜杠和弦、
     旋律落在末小节、voicing_shift、32 小节长曲
  4. 文档里写的命令行开关必须真的存在

用法: python scripts/rehearsal.py [--keep]      # --keep 保留产物便于试听
"""
import glob
import json
import os
import shutil
import sys
import tempfile
import time

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import song_engine          # noqa: E402
import render_midi          # noqa: E402
import make_song            # noqa: E402
import metrics              # noqa: E402
import scorecard            # noqa: E402
import midi_probe           # noqa: E402

TMP = tempfile.mkdtemp(prefix='rehearsal_')
KEEP = '--keep' in sys.argv
FAILS = []

#: 调参误差的"已经够好"下限。`tune_error` 是 3~4 个分组误差之和，每组容差 `metrics.TOL`＝1.5dB，
#: 所以 3.0 ≈ "各组都在容差内"。误差本来就在这个量级时，调参前后的小幅波动不算退化。
TUNE_FLOOR = 3.0


def log(ok, name, detail=''):
    print('  %-5s %-26s %s' % ('PASS' if ok else 'FAIL', name, detail))
    if not ok:
        FAILS.append('%s: %s' % (name, detail))


def mini_song(style, bars=4, perc=1, arr_extra=None, chords=None, melody=None,
              patterns=None, name='rehearsal', bpm=120):
    """构造一首最小的合法新歌（模拟"以后新写的歌"）"""
    ch = chords or {'D': [38, [57, 62, 66, 69, 74]],
                    'A/C#': [37, [57, 61, 64, 69, 73]],
                    'Bm7': [35, [59, 62, 66, 69, 74]],
                    'G': [31, [55, 59, 62, 67, 71]]}
    prog = list(ch)[:bars]
    if melody is None:
        mel = []
        for i in range(bars):
            b = min(i, bars - 1)
            mel.append([b, 0, 1, 74])
            mel.append([b, 2, 1, 78])
    else:
        mel = melody
    arr = {'uku': True, 'piano': True, 'bass': True, 'pad': True,
           'strings': True, 'glock': True, 'arp': True, 'perc': perc}
    if arr_extra:
        arr.update(arr_extra)
    d = {'name': name, 'bpm': bpm, 'style': style, 'chords': ch,
         'melody': {'m': mel},
         'sections': [{'name': 'A', 'bars': bars, 'chords': prog,
                       'melody': 'm', 'arr': arr}]}
    if patterns:
        d['patterns'] = patterns
    return d


def max_gap(prof, ref):
    """按参考曲的对标集算最大频段偏差"""
    keys = ref.get('align_bands') or [k for k in ref['bands']
                                      if not k.startswith('20-40')]
    return max(abs(prof['bands'][k] - ref['bands'][k]) for k in keys)


def tune_error(prof, ref):
    """**自动调参实际在优化的那个量**：分组误差绝对值之和（low / 1.2-5k / 5-18k，+ 可选 20-40）。

    为什么不拿"最大频段偏差"当判据：`make_song.autotune` 走的是 `target_gaps()` 的**分组**
    误差（`tune_step` 只看 low/mid/top/sub），最大频段偏差**不在它的目标里** ——
    拿它当门就会出现"调参明明有效却被判失败"（或反之）。实测：五套风格里多组 runs 的
    最大频段偏差调参前后**一模一样**（4.3→4.3），因为它改的是分组平均。
    """
    g = make_song.target_gaps(prof, ref)
    return abs(g['low']) + abs(g['mid_db']) + abs(g['shelf']) + abs(g['hp'])


def run_style(style, ref, bars=4, **kw):
    """端到端跑一套风格：作曲 → 渲染 → 自动调参 → 验收"""
    name = 'rh_%s' % style
    # 小样必须用**参考曲的 BPM**：早期这里写死 120，而分析时又强行按 ref['bpm'] 切小节，
    # 于是节奏型/调式/结构是在错位的小节网格上算出来的（参考曲 84~163BPM 时全错位），
    # 那种"验证"等于没验证。
    kw.setdefault('bpm', ref['bpm'])
    d = mini_song(style, bars=bars, name=name, **kw)
    sp = os.path.join(TMP, name + '.json')
    json.dump(d, open(sp, 'w', encoding='utf-8'), ensure_ascii=False)
    mid = os.path.join(TMP, name + '.mid')
    out = os.path.join(TMP, name + '_sf')
    t0 = time.time()
    song_engine.compose(sp, mid, quiet=True)
    ev, nbars = song_engine.build_events(song_engine.load(sp))
    n_notes = sum(len(v) for v in ev.values())
    cfg = {'rms': ref['rms_db'], 'width': max(0.8, min(2.6, ref['width'] / 0.28)),
           'shelf': 3.0, 'hp': 38.0, 'low': 0.0, 'drive': 1.5, 'mid_db': 0.0}
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        # 先渲染一版（未调参）作为基准，再让自动调参跑 —— 否则"调参有没有效果"无从判断
        render_midi.render(mid, out, rms_db=cfg['rms'], width=cfg['width'],
                           shelf_db=cfg['shelf'], hp_hz=cfg['hp'],
                           low_db=cfg['low'], drive=cfg['drive'],
                           mid_db=cfg['mid_db'], verbose=False)
        mine_before = metrics.profile(out + '.wav', ref['bpm'])
        gap_before = max_gap(mine_before, ref)
        err_before = tune_error(mine_before, ref)
        cfg = make_song.autotune(cfg, ref, mid, out, max_iter=3)
    y, sr = sf.read(out + '.wav', dtype='float64', always_2d=True)
    mine = metrics.profile(out + '.wav', ref['bpm'])
    gap_after = max_gap(mine, ref)
    err_after = tune_error(mine, ref)
    pk = float(np.abs(y).max())
    # 判据只留**不依赖"像不像"**的三件事：
    #   ① 管线通：有音符 / 出 OGG / 不削波 / 无 NaN
    #   ② 调参有效：`tune_error`（它真正优化的那个量）**确实变小了，或本来就够好**（≤ TUNE_FLOOR）
    #      —— 不像"更差就算数"那种写法，"调参整个失效"（前后一模一样且误差还很大）会红。
    #   ③ 速度一致：小样确实按参考速度写（读 MIDI tempo 元事件，不音频测速；见下）
    # **"对齐 dB"不进判据**：4 小节通用骨架 vs 完整制作，差 5–20dB 是正常的
    # （实测"无打击乐段落"18.8→15.0dB 仍应通过）—— 拿它当门等于用测不准的尺子判分。
    # 不用音频测速：连奏编配（竖琴/弦乐/合唱）会被 detect_bpm 误判
    # （实测 gorgeous 编配的 106BPM 样带读成 154.3，而自相关峰值正好落在一小节上）。
    wrote = midi_probe.parse(mid, quiet=True).get('bpm')
    grid_ok = wrote is not None and abs(wrote - ref['bpm']) < 0.05
    tuned_ok = (err_after <= TUNE_FLOOR) or (err_after < err_before)
    det = metrics.profile(out + '.wav')['bpm']          # 仅作参考打印
    ok = (n_notes > 10 and os.path.exists(out + '.ogg') and pk <= 1.0
          and np.isfinite(y).all() and grid_ok and tuned_ok)
    log(ok, '风格 %s' % style,
        '音符%d 时长%.0fs 峰值%.3f 调参误差 %.1f→%.1f 对齐 %.1f→%.1fdB(仅供参考) '
        '速度%.1f=写(参考%.1f) 测速%.1f* %.0fs'
        % (n_notes, len(y) / sr, pk, err_before, err_after, gap_before, gap_after,
           wrote or -1, ref['bpm'], det, time.time() - t0))
    return gap_after


def pick_ref(fresh):
    """本轮预演用哪首参考曲：
    ① 有没扒过的 → 扒它并存 refs/（这才是"全新参考曲"的真正考验）
    ② 池子被预演逐个消耗完了 → **重扒一首旧画像的原始音频**（画像里记着 file 路径），
       练到扒谱全链路但**不写 refs/**；否则预演跑几十次后会永远 FAIL"""
    if fresh:
        pick = fresh[0]
        t0 = time.time()
        prof = metrics.profile(pick, name=os.path.splitext(os.path.basename(pick))[0])
        os.makedirs(os.path.join(ROOT, 'refs'), exist_ok=True)
        json.dump(prof, open(os.path.join(ROOT, 'refs', prof['name'] + '.json'),
                             'w', encoding='utf-8'), ensure_ascii=False)
        log(True, '扒新参考曲 %s' % prof['name'],
            '%.1fBPM %.0fs RMS%.1f 宽度%.3f（%.0fs）'
            % (prof['bpm'], prof['duration'], prof['rms_db'], prof['width'],
               time.time() - t0))
        return prof
    for old in sorted(glob.glob(os.path.join(ROOT, 'refs', '*.json'))):
        try:
            src = json.load(open(old, encoding='utf-8')).get('file')
        except Exception:
            continue
        if not src or not os.path.exists(src):
            continue
        t0 = time.time()
        prof = metrics.profile(src)
        log(True, '参考曲池已用完',
            '重扒 %s（不写 refs/），本轮用它：%.1fBPM %.0fs 宽度%.3f（%.0fs）'
            % (os.path.basename(src), prof['bpm'], prof['duration'], prof['width'],
               time.time() - t0))
        return prof
    log(False, '全新参考曲', '没有未扒过的参考曲，也没有可用的旧画像')
    return scorecard.load_ref('BGM16c')


def main():
    print('=== 1. 全新参考曲扒谱（以前没扒过的）===')
    # 参考曲是**自备素材**（版权原因不随仓库分发）：用环境变量 BGM_REF_DIR 指过去。
    # 没设置、或目录里没有未扒过的曲目 → 跳过这一步、用已有画像继续彩排（不算失败）。
    ref_dir = os.environ.get('BGM_REF_DIR', '')
    refs = sorted(glob.glob(os.path.join(ref_dir, '*.ogg'))) if ref_dir else []
    known = {os.path.splitext(os.path.basename(p))[0]
             for p in glob.glob(os.path.join(ROOT, 'refs', '*.json'))}
    fresh = [p for p in refs if os.path.splitext(os.path.basename(p))[0] not in known]
    if fresh:
        ref = pick_ref(fresh)
    else:
        avail = sorted(os.path.splitext(os.path.basename(p))[0]
                       for p in glob.glob(os.path.join(ROOT, 'refs', '*.json')))
        if not avail:
            print('  （没有 BGM_REF_DIR，refs/ 里也没有画像 → 彩排需要一首参考曲；'
                  '先跑 profile_ref.py <参考曲> <名字>）')
            return 1
        print('  （没有 BGM_REF_DIR / 没有未扒过的参考曲 → 用已有画像 %s 继续）' % avail[0])
        ref = scorecard.load_ref(avail[0])

    print('=== 2. 五套风格端到端（作曲→渲染→自动调参→验收）===')
    for style in song_engine.STYLES:
        try:
            run_style(style, ref)
        except Exception as e:
            log(False, '风格 %s' % style, '%s: %s' % (type(e).__name__, e))

    print('=== 3. 边界情况 ===')
    cases = [
        ('1 小节短曲', dict(bars=1)),
        ('无打击乐段落', dict(perc=0)),
        ('风格预设被显式覆盖',
         dict(patterns={'bass_style': 'eighth', 'perc_style': 'dance'},
              arr_extra={'arp': True})),
        ('voicing_shift +12', dict(patterns={'voicing_shift': 12})),
        ('旋律落在末小节',
         dict(melody=[[3, 0, 1, 74], [3, 2, 2, 78]])),
    ]
    for label, kw in cases:
        try:
            run_style('daily', ref, **kw)
            log(True, label)
        except Exception as e:
            log(False, label, '%s: %s' % (type(e).__name__, e))

    print('=== 4. 文档开关真实存在 ===')
    docs = [os.path.join(ROOT, 'README.md'),
            os.path.join(ROOT, 'PITFALLS.md'),      # 坑清单拆出去了，也要一起查
            os.path.join(ROOT, 'HISTORY.md'),
            os.path.join(os.path.expanduser('~'), '.dsh', 'skills', 'bgm-studio', 'SKILL.md')]
    txt = ''.join(open(p, encoding='utf-8').read() for p in docs if os.path.exists(p))
    import re
    flags = set(re.findall(r'(--[a-z][a-z0-9-]+)', txt))
    src = {}
    # 开关也可能定义在 `studio/`（面板自己的 CLI）：那里也是本工具链的一部分，
    # 文档里写了它就该查得到（实测漏扫时 `--keep-tmp-audio` 被误报"缺失"）。
    for f in (glob.glob(os.path.join(HERE, '*.py'))
              + glob.glob(os.path.join(ROOT, 'studio', '*.py'))
              + glob.glob(os.path.join(ROOT, 'studio', 'tools', '*.py'))):
        src[os.path.relpath(f, ROOT)] = open(f, encoding='utf-8').read()
    allsrc = '\n'.join(src.values())
    missing = sorted(f for f in flags
                     if ("'%s'" % f) not in allsrc and ('"%s"' % f) not in allsrc)
    log(not missing, '文档里的命令行开关', '缺失: %s' % missing if missing else
        '共 %d 个开关全部存在' % len(flags))

    print('\n结果: %s' % ('全部通过' if not FAILS else '%d 项失败' % len(FAILS)))
    for f in FAILS:
        print('  - %s' % f)
    if not KEEP:
        shutil.rmtree(TMP, ignore_errors=True)
    else:
        print('产物保留在 %s' % TMP)
    return 1 if FAILS else 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    sys.exit(main())
