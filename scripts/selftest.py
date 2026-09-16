#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全链路自检：一条命令检查所有脚本、数据、管线的不变量。

用法:
  python scripts/selftest.py            # 全部检查（含一次极小的真音源渲染）
  python scripts/selftest.py --fast     # 跳过渲染类检查（秒级）

退出码 0 = 全过；非 0 = 有 FAIL（会打印具体项）。
"""
import glob
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import atexit
from contextlib import redirect_stdout

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix='selftest_')
FAST = '--fast' in sys.argv


def _cleanup_tmp():
    """**退出时删掉自己的临时目录**。

    为什么必须做（实测）：`TMP` 是**模块级**创建的，而 `selftest` 被一堆工具 import
    （`check_song` / `build_song` / `midi_ref` / `theme_pack` / `mutation_check`）——
    于是**每一次 `new_song.py` / `check_song.py` / `theme_pack.py` 运行都会在
    `%TEMP%` 里留下一个 selftest_* 目录**，而它们**从来不清理**。累积结果：
    实测系统临时目录里有 **1601 个 selftest_* 目录 / 7.5GB**（渲染出来的 WAV/OGG），
    直接把系统盘吃紧（用户报"C 盘怎么变小了"就是这么来的）。
    例外：`DSH_KEEP_TMP=1` 时保留（排查失败用例时要看里面的文件）。
    """
    if os.environ.get('DSH_KEEP_TMP'):
        print('  (DSH_KEEP_TMP=1：保留临时目录 %s)' % TMP)
        return
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)


atexit.register(_cleanup_tmp)

import metrics            # noqa: E402
import song_engine        # noqa: E402
import scorecard          # noqa: E402
import render_midi        # noqa: E402
import to_ogg             # noqa: E402

CHECKS = []
FAILS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def song_dirs(with_json=True):
    out = []
    for d in sorted(glob.glob(os.path.join(ROOT, 'songs', '*'))):
        if not os.path.isdir(d):
            continue
        if os.path.basename(d).startswith('_'):
            continue      # `_` 开头 = 临时/归档（`_archive/`、`_lint_*`），不是正式曲目
        if with_json and not os.path.exists(os.path.join(d, 'song.json')):
            continue
        out.append(d)
    return out


def songs_or_fail(**kw):
    """取曲目列表，但**先证明它非空**（真实曲目时再核对数量）。

    为什么必须这样：审计发现 13 条"与曲目有关"的检查里有 11 条在
    `song_dirs()` 返回空列表时**照样 PASS**（`for d in []` 什么都不做）——
    目录名一改、glob 一坏，它们就集体变成空转的绿灯，你还以为"全绿"。
    这类"空集合假通过"是检查层最隐蔽的 bug，比产品 bug 更危险。

    注意：**变异测试会注入临时曲目目录**，那种情况下跳过"数量核对"
    （否则注入的故障会因为"数量不对"被抓，而不是因为那条检查本身有效 ——
    那样变异测试就变成了自欺）。"""
    got = song_dirs(**kw)
    assert got, 'song_dirs() 返回空 —— 这些检查会空转（songs/ 路径或 glob 坏了）'
    real_root = os.path.abspath(os.path.join(ROOT, 'songs'))
    injected = any(not os.path.abspath(d).startswith(real_root) for d in got)
    if not injected:
        if kw.get('with_json', True):
            disk = glob.glob(os.path.join(ROOT, 'songs', '*', 'song.json'))
        else:
            disk = [p for p in glob.glob(os.path.join(ROOT, 'songs', '*'))
                    if os.path.isdir(p) and not os.path.basename(p).startswith('_')]
        assert len(got) == len(disk), \
            '只发现 %d 个曲目，磁盘上有 %d 个（发现机制漏掉了曲目）' % (
                len(got), len(disk))
    return got


def fixture_song(need=None):
    """挑一首现成曲目当**夹具** —— 不再硬编码 `16_d150_bright_day` 这种具体曲名。

    为什么：仓库可以只带一两首示例曲（甚至只带一份 spec）。硬编码曲名会让"删示例曲"
    直接等于"自检变红"，可这些检查与"到底是哪首曲"毫无关系。挑不到返回 None，
    由调用方决定是跳过还是报错。

    `need='strict_clean'`：只挑**强拍全落在弦内音**的曲目（旧库普遍有 7~15 处强拍
    经过音，挑到那种会让"内容断言"误报成检查坏了）。
    `need='with_spec'`：只挑带 `spec.json` 的（spec→song.json 往返用它才确定可行）。
    """
    import check_song as _cs
    for d in song_dirs():
        if need == 'with_spec' and not os.path.exists(os.path.join(d, 'spec.json')):
            continue
        if need == 'strict_clean' and _cs._strict_downbeats(os.path.join(d, 'song.json')):
            continue
        return d
    return None


def quiet(fn, *a, **kw):
    """吞掉被测函数的打印，只取返回值"""
    buf = io.StringIO()
    with redirect_stdout(buf):
        r = fn(*a, **kw)
    return r, buf.getvalue()


# ---------------------------------------------------------------- 1. 静态
@check
def t_import_all():
    """所有脚本都能 import（语法/顶层错误）"""
    bad = []
    for p in sorted(glob.glob(os.path.join(HERE, '*.py'))):
        name = os.path.basename(p)[:-3]
        if name in ('play_midi',):          # 依赖 winmm，单独检查
            continue
        try:
            __import__(name)
        except Exception as e:
            bad.append('%s: %s' % (name, e))
    assert not bad, 'import 失败: ' + '; '.join(bad)


@check
def t_py_compile():
    """所有 .py（含未 import 的）都能编译"""
    bad = []
    for p in glob.glob(os.path.join(ROOT, '**', '*.py'), recursive=True):
        if '.venv' in p or 'vendor' in p or '__pycache__' in p:
            continue
        r = subprocess.run([sys.executable, '-m', 'py_compile', p],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace')
        if r.returncode != 0:
            bad.append('%s: %s' % (os.path.basename(p), r.stderr.strip()[-120:]))
    assert not bad, '编译失败: ' + '; '.join(bad)


@check
def t_vendor():
    """真音源环境在位"""
    exe, sf2 = render_midi.find_exe(), render_midi.find_sf2()
    assert os.path.exists(exe) and os.path.getsize(exe) > 1e5, 'fluidsynth 缺失'
    assert os.path.getsize(sf2) > 1e6, 'sf2 音源缺失'


# ---------------------------------------------------------------- 2. 数据
@check
def t_refs_schema():
    """refs/*.json 字段齐全"""
    need = ('name', 'bpm', 'bar', 'rms_db', 'width', 'centroid', 'bands',
            'rhythm_low', 'rhythm_high', 'quiet_chroma')
    refs = glob.glob(os.path.join(ROOT, 'refs', '*.json'))
    assert refs, '一个参考曲画像都没有'
    for p in refs:
        d = json.load(open(p, encoding='utf-8'))
        miss = [k for k in need if k not in d]
        assert not miss, '%s 缺字段 %s' % (os.path.basename(p), miss)
        assert len(d['bands']) == 10, '%s bands 不是 10 段' % os.path.basename(p)
        assert 20 < d['bpm'] < 300, '%s BPM 异常 %s' % (os.path.basename(p), d['bpm'])


@check
def t_render_json_schema():
    """render.json 字段类型正确（mid 必须是文件名，不能是数字 —— 撞名 bug 的防线）"""
    for d in songs_or_fail(with_json=False):
        p = os.path.join(d, 'render.json')
        if not os.path.exists(p):
            continue
        c = json.load(open(p, encoding='utf-8'))
        name = os.path.basename(d)
        assert isinstance(c.get('mid'), str), '%s: render.json 的 mid 必须是字符串' % name
        assert isinstance(c.get('out'), str), '%s: out 必须是字符串' % name
        for k in ('rms', 'width', 'shelf', 'hp', 'low', 'drive', 'mid_db'):
            if k in c:
                assert isinstance(c[k], (int, float)) and not isinstance(c[k], bool), \
                    '%s: %s 必须是数字' % (name, k)
        # **响度口径标记**：写了就必须与当前实现一致（写错会让"旧配置变响"的提示失灵）。
        # 不强制要求存在 —— 历史配置没有这个标记，重渲染时由 make_song 提示重跑调参。
        if 'norm' in c:
            assert c['norm'] == render_midi.NORM, \
                '%s: render.json 的 norm=%r 不是当前口径 %r（口径写错 = 提示失灵）' % (
                    name, c['norm'], render_midi.NORM)
        assert c.get('composer'), '%s: 缺 composer' % name
        comp = c['composer']
        cp = os.path.join(ROOT, comp) if ('/' in comp or '\\' in comp) \
            else os.path.join(d, comp)
        assert os.path.exists(cp), '%s: composer 不存在 %s' % (name, cp)
        import scorecard as _sc
        refp = _sc.ref_path(c.get('ref', ''))
        assert os.path.exists(refp), \
            ('%s: 参考画像不存在 %s（单份在 refs/、聚合在 refs/mix_targets/）'
             % (name, c.get('ref')))


@check
def t_song_json_buildable():
    """每首歌的 song.json 都能编配出合法事件（音符/力度/时值范围）"""
    for d in songs_or_fail():
        name = os.path.basename(d)
        data = song_engine.load(os.path.join(d, 'song.json'))
        ev, nbars = quiet(song_engine.build_events, data)[0]
        assert nbars > 0, '%s: 小节数为 0' % name
        total = 0
        for tr, notes in ev.items():
            for (t, dur, m, v) in notes:
                assert 0 <= m <= 127, '%s/%s 音高越界 %s' % (name, tr, m)
                assert 1 <= v <= 127, '%s/%s 力度越界 %s' % (name, tr, v)
                assert t >= 0 and dur > 0, '%s/%s 时值异常 t=%s d=%s' % (name, tr, t, dur)
            total += len(notes)
        assert total > 20, '%s: 音符太少(%d)' % (name, total)
        # 段落引用的和弦必须都在和弦库里
        for sec in data['sections']:
            for c in sec['chords']:
                assert c in data['chords'], '%s: 段落 %s 引用了未定义和弦 %s' % (
                    name, sec['name'], c)
            for ref in re.findall(r'[A-Ga-g][#b]?', sec.get('melody', '')):
                pass


def build(dd):
    """build_events 返回 (ev, nbars)，这里统一解包"""
    (ev, nbars), _ = quiet(song_engine.build_events, dd)
    return ev, nbars


@check
def t_styles():
    """5 套风格都能载入，且音色/节奏与说明一致"""
    for s in song_engine.STYLES:
        d = {'name': 't', 'bpm': 120, 'style': s,
             'chords': {'C': [36, [55, 60, 64, 67, 72]]}, 'melody': {},
             'sections': [{'name': 'A', 'bars': 2, 'chords': ['C', 'C'],
                           'melody': '', 'arr': {'uku': True, 'piano': True,
                                                 'bass': True, 'perc': 1}}]}
        p = os.path.join(TMP, 'style_%s.json' % s)
        json.dump(d, open(p, 'w', encoding='utf-8'))
        dd, _ = quiet(song_engine.load, p)
        for k in ('Melody', 'Hook', 'Piano', 'Bass', 'Glock', 'Perc'):
            assert k in dd['programs'] and k in dd['mix'], '%s 缺轨 %s' % (s, k)
        ev, _n = build(dd)
        assert sum(len(v) for v in ev.values()) > 10, '%s 没编出音符' % s


@check
def t_style_unknown():
    """未知风格名必须报错，不能静默用默认值"""
    p = os.path.join(TMP, 'bad_style.json')
    json.dump({'name': 'x', 'style': 'nope', 'chords': {}, 'melody': {},
               'sections': []}, open(p, 'w', encoding='utf-8'))
    try:
        quiet(song_engine.load, p)
    except SystemExit:
        return
    raise AssertionError('未知 style 没有报错')


@check
def t_bad_arr_key_warns():
    """拼错的编配开关要报警告"""
    p = os.path.join(TMP, 'bad_arr.json')
    json.dump({'name': 'x', 'bpm': 100, 'chords': {'C': [36, [55, 60, 64]]},
               'melody': {}, 'sections': [{'name': 'A', 'bars': 1,
                                           'chords': ['C'], 'melody': '',
                                           'arr': {'pinao': True}}]},
              open(p, 'w', encoding='utf-8'))
    _, out = quiet(song_engine.load, p)
    assert 'pinao' in out, '拼错的开关没被报出来'


@check
def t_voicing_shift():
    """voicing_shift 真的移动了和弦音（不动贝斯）"""
    base = {'name': 'x', 'bpm': 100, 'chords': {'C': [36, [55, 60, 64]]},
            'melody': {}, 'sections': [{'name': 'A', 'bars': 1, 'chords': ['C'],
                                        'melody': '',
                                        'arr': {'uku': True, 'bass': True}}]}
    outs = []
    for shift in (0, 12):
        d = json.loads(json.dumps(base))
        d['patterns'] = {'voicing_shift': shift}
        p = os.path.join(TMP, 'vs_%d.json' % shift)
        json.dump(d, open(p, 'w', encoding='utf-8'))
        dd, _ = quiet(song_engine.load, p)
        ev, _n = build(dd)
        outs.append((min(m for (_, _, m, _) in ev['Hook']),
                     min(m for (_, _, m, _) in ev['Bass'])))
    assert outs[1][0] == outs[0][0] + 12, 'voicing_shift 没作用在 Hook'
    assert outs[1][1] == outs[0][1], 'voicing_shift 不该动 Bass'


# ---------------------------------------------------------------- 3. 数学/DSP
@check
def t_tune_step_signs():
    """自动调参的符号方向：本曲偏亮 → shelf 必须往下调"""
    import make_song
    ref = {'bands': {'20-40': -14, '40-80': 0, '80-160': -4, '160-315': -6,
                     '315-630': -10, '630-1250': -14, '1250-2500': -20,
                     '2500-5000': -25, '5000-10000': -25, '10000-18000': -30},
           'width': 0.5, 'rms_db': -14.0}
    bright = dict(ref)
    bright = {'bands': dict(ref['bands']), 'width': 0.3, 'rms_db': -17.0}
    for k in ('2500-5000', '5000-10000', '10000-18000'):
        bright['bands'][k] = ref['bands'][k] + 8      # 本曲偏亮 8dB
    delta, _ = make_song.tune_step(bright, ref, {})
    assert delta.get('shelf', 0) < 0, '本曲偏亮时 shelf 应下调，实际 %s' % delta.get('shelf')
    dark = json.loads(json.dumps(bright))
    for k in ('2500-5000', '5000-10000', '10000-18000'):
        dark['bands'][k] = ref['bands'][k] - 8
    delta2, _ = make_song.tune_step(dark, ref, {})
    assert delta2.get('shelf', 0) > 0, '本曲偏暗时 shelf 应上调'
    assert delta2.get('mid_db', 0) > 0, '本曲中频薄时应上调 mid_db'


@check
def t_tune_limits_clamp():
    """① 小差距只能给小建议（防止 width(lo=0.8)/hp(lo=18) 的小变化量被抬到下限）
       ② 夹紧与"到顶"报告只在 autotune 一层负责（tune_step 返回原始建议）"""
    import make_song
    ref = {'bands': {k: 0.0 for k in
                     ('20-40', '40-80', '80-160', '160-315', '315-630',
                      '630-1250', '1250-2500', '2500-5000', '5000-10000',
                      '10000-18000')}, 'width': 0.5, 'rms_db': -14.0}
    near = {'bands': {k: ref['bands'][k] + 1.6 for k in ref['bands']},
            'width': 0.45, 'rms_db': -14.0}
    cfg2 = {'low': 0.0, 'mid_db': 0.0, 'shelf': 0.0, 'width': 2.0, 'hp': 38.0}
    d2, _ = make_song.tune_step(near, ref, cfg2)
    for k in ('width', 'hp'):
        if k in d2:
            assert abs(d2[k]) <= 1.0, '%s 的小差距给出了大建议 %s' % (k, d2[k])
    # 原始建议必须如实反映差距（不在这里夹紧）
    mine = {'bands': {k: ref['bands'][k] + 10.0 for k in ref['bands']},
            'width': 0.5, 'rms_db': -14.0}
    cfg3 = {'low': -4.0, 'mid_db': 0.0, 'shelf': -3.0, 'width': 2.0, 'hp': 60.0}
    d3, _ = make_song.tune_step(mine, ref, cfg3)
    assert d3.get('shelf', 0) < -4.0, \
        'tune_step 不该预先夹紧（会丢掉"到顶"信息），实得 %s' % d3.get('shelf')


@check
def t_dsp_clean():
    """DSP 不产生 NaN/Inf，输出不越界"""
    rng = np.random.default_rng(7)
    x = (rng.standard_normal((44100, 2)) * 0.3).astype(np.float64)
    y = render_midi.high_shelf_np(x.copy(), 44100, 3000, 6)
    y = render_midi.mid_boost_np(y, 44100, 6)
    y = render_midi.low_shelf_np(y, 44100, 150, 6)
    y = render_midi.highpass_np(y, 44100, 40, 3)
    y = render_midi.soft_limit(y, 2.0)
    assert np.isfinite(y).all(), 'DSP 出现 NaN/Inf'
    assert np.abs(y).max() <= 1.0001, '软限幅后仍越界 %.4f' % np.abs(y).max()


@check
def t_dsp_direction():
    """各 EQ 的方向正确（+6dB 真的抬该频段）"""
    sr = 44100
    t = np.arange(sr * 3) / sr
    hi = np.sin(2 * np.pi * 8000 * t)
    lo = np.sin(2 * np.pi * 60 * t)
    midf = np.sin(2 * np.pi * 3000 * t)
    for sig, fn, args, lo_b, hi_b, label in (
            (hi, render_midi.high_shelf_np, (3000, 6), 6000, 12000, 'high_shelf'),
            (lo, render_midi.low_shelf_np, (150, 6), 40, 90, 'low_shelf'),
            (midf, render_midi.mid_boost_np, (6,), 1500, 5000, 'mid_boost')):
        x = np.stack([sig, sig], axis=1)
        y = fn(x.copy(), sr, *args)
        assert np.sqrt((y ** 2).mean()) > np.sqrt((x ** 2).mean()) * 1.3, \
            '%s 没有提升该频段' % label
    # 高通必须削掉极低频
    x = np.stack([lo, lo], axis=1)
    y = render_midi.highpass_np(x.copy(), sr, 60, 3)
    assert np.sqrt((y ** 2).mean()) < np.sqrt((x ** 2).mean()) * 0.6, '高通没起作用'


@check
def t_dsp_fft_equivalent():
    """频域 DSP 必须与**逐样本时域递推**等价（不是"方向对"，是逐样本）。

    为什么需要这条：把 63s/轮的逐样本循环换成频域乘法是一次**行为等价改写**，
    它的风险不是"崩"，而是"听着差不多但响应偏了"——`dsp_direction`（+6dB 抬该频段）
    对这类偏差完全不敏感（阶数写错、极点系数写错、忘了零填充都照样过）。

    基准是这里**独立写的慢速时域递推**（故意不复用 render_midi 的实现 ——
    用被测实现当判据等于没测）。两种长度都要测：长信号（2 万样本）查响应，
    短信号（400 样本）查**零填充**——不填充时循环卷积会把尾巴绕回开头，
    长信号上看不出来（极点^20000 ≈ 0），短信号上就是明显错误。
    变异测试里有两条用例守着它：高通忽略 order、以及不做零填充。
    """
    sr = 44100
    rng = np.random.default_rng(11)

    def ref_lp(ch, a):
        out = np.empty_like(ch)
        acc = 0.0
        for i in range(len(ch)):
            acc += a * (ch[i] - acc)
            out[i] = acc
        return out

    def ref_shelf(x, fc, gain_db):
        g = 10.0 ** (gain_db / 20.0) - 1.0
        a = 1.0 - np.exp(-2 * np.pi * fc / sr)
        y = x.copy()
        for c in range(y.shape[1]):
            y[:, c] = y[:, c] + g * (y[:, c] - ref_lp(y[:, c], a))
        return y

    def ref_lowshelf(x, fc, gain_db):
        g = 10.0 ** (gain_db / 20.0) - 1.0
        a = 1.0 - np.exp(-2 * np.pi * fc / sr)
        y = x.copy()
        for c in range(y.shape[1]):
            y[:, c] = y[:, c] + g * ref_lp(y[:, c], a)
        return y

    def ref_mid(x, gain_db, f_lo, f_hi):
        g = 10.0 ** (gain_db / 20.0) - 1.0
        a_hi = 1.0 - np.exp(-2 * np.pi * f_hi / sr)
        a_lo = 1.0 - np.exp(-2 * np.pi * f_lo / sr)
        y = x.copy()
        for c in range(y.shape[1]):
            ch = y[:, c].copy()
            y[:, c] = ch + g * (ref_lp(ch, a_hi) - ref_lp(ch, a_lo))
        return y

    def ref_hp(x, fc, order):
        a = 1.0 - np.exp(-2 * np.pi * fc / sr)
        y = x.copy()
        for c in range(y.shape[1]):
            ch = y[:, c].copy()
            for _ in range(max(1, order)):
                ch = ch - ref_lp(ch, a)
            y[:, c] = ch
        return y

    cases = (
        ('high_shelf', lambda s: render_midi.high_shelf_np(s, sr, 3000, 3.5),
         lambda s: ref_shelf(s, 3000, 3.5)),
        ('low_shelf', lambda s: render_midi.low_shelf_np(s, sr, 150, 6),
         lambda s: ref_lowshelf(s, 150, 6)),
        ('mid_boost', lambda s: render_midi.mid_boost_np(s, sr, 6),
         lambda s: ref_mid(s, 6, 1200, 6000)),
        ('highpass×3', lambda s: render_midi.highpass_np(s, sr, 38, 3),
         lambda s: ref_hp(s, 38, 3)),
    )
    for n in (20000, 400):        # 长信号查响应，短信号查零填充（绕回)
        x = (rng.standard_normal((n, 2)) * 0.3).astype(np.float64)
        for label, got_fn, want_fn in cases:
            err = float(np.abs(got_fn(x.copy()) - want_fn(x.copy())).max())
            assert err < 1e-9, \
                '%d 样本的 %s：频域实现与时域递推不等价，最大逐样本差 %.3g' % (
                    n, label, err)


@check
def t_metrics_bpm_and_schema():
    """合成 120BPM 打点 → 测速命中（允许倍频误差，scorecard 会吸附）"""
    sr = 22050
    n = sr * 12
    x = np.zeros(n)
    step = int(sr * 0.25)          # 每 0.25s 一击 = 240BPM 的十六分 / 120BPM 的八分
    for i in range(0, n - 200, step):
        x[i:i + 200] += np.hanning(200) * 0.8
    m = x.astype(np.float32)
    bpm = quiet(metrics.detect_bpm, m, sr)[0][0]   # detect_bpm 返回 (bpm, 峰, info)
    ok = any(abs(bpm - c) < 6 for c in (60, 120, 240))
    assert ok, '测速失败: %.1f' % bpm
    p = os.path.join(TMP, 'click.wav')
    sf.write(p, np.stack([m, m], axis=1), sr)
    prof = metrics.profile(p, 120.0)
    assert len(prof['bands']) == 10 and prof['width'] >= 0


@check
def t_scorecard_missing_ref():
    """参考画像缺失时必须友好退出"""
    try:
        scorecard.load_ref('绝对不存在的画像名')
    except SystemExit as e:
        assert 'profile_ref' in str(e), '报错信息没告诉怎么建画像'
        return
    raise AssertionError('缺画像时没有报错')


# ---------------------------------------------------------------- 4. 端到端
@check
def t_tiny_render_and_ogg():
    """极小 MIDI → 真音源渲染 → WAV+OGG（含中频/高通参数）"""
    if FAST:
        return
    d = {'name': 'tiny', 'bpm': 120, 'style': 'daily',
         'chords': {'C': [36, [55, 60, 64, 67, 72]],
                    'G': [31, [55, 59, 62, 67, 71]]},
         'melody': {'m': [[0, 0, 1, 72], [0, 2, 1, 76]]},
         'sections': [{'name': 'A', 'bars': 2, 'chords': ['C', 'G'],
                       'melody': 'm', 'arr': {'uku': True, 'piano': True,
                                              'bass': True, 'pad': True,
                                              'glock': True, 'perc': 1}}]}
    sp = os.path.join(TMP, 'tiny.json')
    json.dump(d, open(sp, 'w', encoding='utf-8'))
    mid = os.path.join(TMP, 'tiny.mid')
    quiet(song_engine.compose, sp, mid)
    wav, ogg = quiet(render_midi.render, mid, os.path.join(TMP, 'tiny_sf'),
                     -16.9, 2.0, 3.0, 38.0, 0.0, 1.6, 5.0, False, False)[0]
    for f in (wav, ogg):
        assert os.path.exists(f) and os.path.getsize(f) > 1000, '产物缺失 %s' % f
    y, sr = sf.read(wav, dtype='float64', always_2d=True)
    assert np.isfinite(y).all(), '渲染结果含 NaN'
    assert np.abs(y).max() <= 1.0, '渲染结果削波'


@check
def t_midi_probe_all():
    """所有曲目的 MIDI 都能被解析"""
    import midi_probe
    n = 0
    for d in song_dirs(with_json=False):
        for f in glob.glob(os.path.join(d, '*.mid')):
            quiet(midi_probe.parse, f)
            n += 1
    assert n >= 3, 'MIDI 数量太少(%d)' % n


@check
def t_probe_guards():
    """section_probe 对过短文件要提示而不是给错数据"""
    import section_probe
    short = os.path.join(TMP, 'short.wav')
    sf.write(short, np.zeros((44100, 2), dtype=np.float32), 44100)
    _, out = quiet(section_probe.main, short, 1.6)
    assert '段落地图不适用' in out or 'SECS' in out, '过短文件没提示'
    # 夹具音频：任意一首带成品的曲目都行（仓库可能不带音频 → 有就查，没有就跳过）
    wavs = sorted(glob.glob(os.path.join(ROOT, 'songs', '*', '*_sf.wav')))
    if wavs:
        _, out2 = quiet(section_probe.main, wavs[0], 1.6)
        assert 'SECS' in out2, '长文件缺少段落地图提示'


@check
def t_make_song_missing_midi():
    """--no-compose 且无 MIDI 时要友好退出"""
    import make_song
    d = os.path.join(ROOT, 'songs', '_selftest_nomidi')
    os.makedirs(d, exist_ok=True)
    json.dump({'composer': 'compose.py', 'mid': 'none.mid', 'out': 'none_sf',
               'ref': 'BGM16c'}, open(os.path.join(d, 'render.json'), 'w'))
    old = sys.argv
    try:
        sys.argv = ['make_song.py', '_selftest_nomidi', '--no-compose']
        rc = make_song.main()
        assert rc == 1, '缺 MIDI 时应返回 1，实际 %s' % rc
    finally:
        sys.argv = old
        import shutil
        shutil.rmtree(d, ignore_errors=True)


@check
def t_docs_paths():
    """README / 技能 里提到的 scripts\\X.py 都要存在"""
    files = [os.path.join(ROOT, 'README.md'),
             os.path.join(ROOT, 'CHEATSHEET.md'),
             os.path.join(ROOT, 'docs', 'SONG-FORMAT.md'),
             os.path.join(ROOT, 'docs', 'THEME-PACK.md'),
             os.path.join(ROOT, 'docs', 'CONVENTION.md'),
             os.path.join(ROOT, 'studio', 'README.md'),
             os.path.join(os.path.expanduser('~'), '.dsh', 'skills',
                          'bgm-studio', 'SKILL.md')]
    missing = []
    for p in files:
        if not os.path.exists(p):
            continue
        txt = open(p, encoding='utf-8').read()
        for m in set(re.findall(r'(?:scripts[\\/])([A-Za-z_0-9]+\.py)', txt)):
            if not os.path.exists(os.path.join(HERE, m)):
                missing.append('%s → %s' % (os.path.basename(p), m))
    assert not missing, '文档引用了不存在的脚本: ' + '; '.join(missing)

    # 文档之间的 `.md` 指针也要能走通（实测：搬走一节后 README 还指着旧位置，
    # 表现为"agent 按指针去读、发现是空的"）。只查**项目内**的文档名；
    # notes.md / SKILL.md 这类"按输入生成/系统级"的名字在白名单里。
    OK_GENERIC = {'notes.md', 'SKILL.md', 'README.md'}
    ptr = re.compile(r'`([A-Za-z0-9/_.\-]+\.md)`')
    dokeys = [os.path.join(ROOT, 'README.md'), os.path.join(ROOT, 'CHEATSHEET.md'),
              os.path.join(ROOT, 'PITFALLS.md'), os.path.join(ROOT, 'PITFALLS-ARCHIVE.md'),
              os.path.join(ROOT, 'docs', 'SONG-FORMAT.md'),
              os.path.join(ROOT, 'docs', 'THEME-PACK.md'), files[-1]]
    dead = []
    for p in dokeys:
        if not os.path.exists(p):
            continue
        txt = open(p, encoding='utf-8').read()
        for m in sorted(set(ptr.findall(txt))):
            if os.path.basename(m) in OK_GENERIC:
                continue
            if not os.path.exists(os.path.join(ROOT, m)):
                dead.append('%s → %s' % (os.path.basename(p), m))
    assert not dead, '文档指针腐烂（搬走了正文却没改指针）: ' + '; '.join(dead)


@check
def t_outputs_exist():
    """每首歌声明的产物：**跑过 make_song 的必须有 MIDI**；**音频（WAV/OGG）可以整首不带**。

    合法状态：① 还没跑过 make_song（跳过）；② 只带 MIDI；③ MIDI + ogg（仓库交付态）；
    ④ MIDI + ogg + wav 全套。**非法**：跑过 make_song 却没有 MIDI（谱面丢了）。
    音频的 ogg / wav 各自可选 —— 仓库只带成品 ogg，wav 母版靠 make_song.py 重生成。
    """
    MIN = {'.mid': 200, '.wav': 1000, '.ogg': 1000}
    bad, unrendered = [], []
    for d in songs_or_fail(with_json=False):
        p = os.path.join(d, 'render.json')
        if not os.path.exists(p):
            continue
        c = json.load(open(p, encoding='utf-8'))
        name = os.path.basename(d)
        mid = c.get('mid') or ''
        rels = [mid, (c.get('out', '') + '.wav'), (c.get('out', '') + '.ogg')]
        present = [r for r in rels if r and os.path.exists(os.path.join(d, r))]
        if not present:
            unrendered.append(name)
            continue
        # 跑过 make_song（有任何产物）就必须有 MIDI；
        # 音频则 **ogg / wav 各自可选**：仓库只带成品 ogg，wav 母版体积大、可重生成。
        if mid not in present or os.path.getsize(os.path.join(d, mid)) < MIN['.mid']:
            bad.append('%s/%s（跑过 make_song 就必须有谱面 MIDI）' % (name, mid or '(未声明)'))
        for rel in present:
            if os.path.getsize(os.path.join(d, rel)) < MIN.get(
                    os.path.splitext(rel)[1].lower(), 1000):
                bad.append('%s/%s 过小' % (name, rel))
    assert not bad, '产物缺失或过小（重新跑 make_song.py <曲目>）: ' + ', '.join(bad)
    if unrendered:
        print('        （%d 首还没跑过 make_song，已跳过）' % len(unrendered))


def _breath_runs(iv, gap=0.5):
    """把音符区间合并成"不间断段"（唯一口径在 `breath.py`；这里保留薄封装给自证用）。"""
    import breath
    return breath.runs(iv, gap)


@check
def t_melody_breathing():
    """旋律**换气**提示：**连续太长**才需要停顿，短句写满是正常的（用户校准后的口径）。

    起因（实测）：一首 90 小节、每小节填 4 个音的曲子，时值被自动推成整齐的 1 拍 ——
    全曲最长音只有 2 拍、覆盖率 91%，用户听完反馈"旋律中间一直没有停顿，听起来好累"。
    **用户随后又校准了口径**："不一定每一段都要停顿，而是**太长的话**要停顿换气" ——
    所以这条改成量"**最长不间断段**"（把相邻音之间 < 0.5 拍的缝隙视为连着），
    超过 `BREATH_SEC` 秒还没换气就提示；短句写满不提示。

    只提示不判错（库里早于本约定写的曲子，重写要动旋律），打印清单供人工处置。
    判据口径与修复工具共用 `breath.py`（避免"检查一个口径、修复另一个口径"）。
    """
    import breath
    # **判据自证**：提示类检查没有断言，判据坏了没人知道（实测过同类：检查本身写错、
    # 空转假绿）。这里先证明"缝隙怎么算"是对的，再拿它去量曲子。
    assert _breath_runs([(0.0, 1.0), (1.2, 2.0)], breath.BREATH_GAP) == [(0.0, 2.0)], \
        '缝隙 0.2 拍应视为连着（连奏不断句）'
    assert _breath_runs([(0.0, 1.0), (1.5, 2.0)], breath.BREATH_GAP) == [(0.0, 1.0), (1.5, 2.0)], \
        '缝隙 0.5 拍应视为换气（断开）'
    assert _breath_runs([(0.0, 2.0), (1.0, 3.0)], breath.BREATH_GAP) == [(0.0, 3.0)], \
        '重叠的音应合并成一个不间断段'
    thin, checked = [], 0
    for d in song_dirs():
        j2 = json.load(open(os.path.join(d, 'song.json'), encoding='utf-8'))
        iv, bb, spb = breath.intervals(j2)
        if not iv:
            continue
        checked += 1
        lng = max(x[2] for sec in j2['sections']
                  for x in (j2['melody'].get(sec['melody']) or []) if 0 <= x[0] < sec['bars'])
        lb = max(e - s for (s, e) in breath.runs(iv, breath.BREATH_GAP))
        if lb * spb > breath.BREATH_SEC:
            thin.append('%s(%.0f秒/%.1f小节没换气,占空比%.0f%%,最长音%.0f拍)'
                        % (os.path.basename(d), lb * spb, lb / bb,
                           100 * sum(e - s for s, e in iv) / (bb * sum(
                               sec['bars'] for sec in j2['sections'])), lng))
    assert checked > 0, '没有可检查的曲目（songs/ 路径或 glob 坏了）'
    if thin:
        head = '; '.join(thin[:6])
        more = '' if len(thin) <= 6 else ' … 共 %d 首' % len(thin)
        print('        （换气提示：%s%s —— 连续太久没停顿，听感会累；'
              '短句写满是正常的，只在长句里留停顿/长音即可）' % (head, more, ))



def t_ref_audio_still_there():
    """refs/*.json 里写的是**绝对路径**时，校验那首参考曲还在（防止搬迁后画像失效）。

    仓库里的画像一律只存**文件名**（`"file": "BGM01.ogg"`）—— 既不带本机目录，
    也不暴露参考曲来源；这种情况没什么可校验的，直接跳过。本机开发时用绝对路径
    生成的画像，这条检查照样生效。
    """
    bad = []
    for p in glob.glob(os.path.join(ROOT, 'refs', '*.json')):
        d = json.load(open(p, encoding='utf-8'))
        f = d.get('file')
        if f and os.path.isabs(f) and not os.path.exists(f):
            bad.append('%s → %s' % (os.path.basename(p), f))
    assert not bad, '参考曲文件已不在: ' + '; '.join(bad)


@check
def t_notes_present():
    """每首歌都有 notes.md（交给下一个人/下个对话时能接手）"""
    miss = [os.path.basename(d) for d in songs_or_fail()
            if not os.path.exists(os.path.join(d, 'notes.md'))]
    assert not miss, '缺 notes.md: ' + ', '.join(miss)


@check
def t_determinism_and_bytes():
    """同一 song.json 编两次字节完全一致（无隐藏状态），且与已交付的 MIDI 一致"""
    for d in songs_or_fail():
        name = os.path.basename(d)
        sp = os.path.join(d, 'song.json')
        a = os.path.join(TMP, name + '_a.mid')
        b = os.path.join(TMP, name + '_b.mid')
        quiet(song_engine.compose, sp, a)[0]
        quiet(song_engine.compose, sp, b)[0]
        ha = open(a, 'rb').read()
        hb = open(b, 'rb').read()
        assert ha == hb, '%s: 两次编配结果不一致（存在隐藏状态/随机性）' % name
        shipped = glob.glob(os.path.join(d, '*.mid'))
        if shipped:
            hs = open(shipped[0], 'rb').read()
            assert ha == hs, ('%s: 已交付的 %s 与当前引擎的输出不一致 → 需要重跑 make_song.py'
                              % (name, os.path.basename(shipped[0])))


@check
def t_audio_health():
    """所有成品音频：无削波、无直流、无 NaN、时长与 MIDI 相符"""
    bad = []
    for d in songs_or_fail(with_json=False):
        for f in glob.glob(os.path.join(d, '*_sf.ogg')) + \
                glob.glob(os.path.join(d, '*_sf.wav')):
            try:
                y, sr = sf.read(f, dtype='float64', always_2d=True)
            except Exception as e:
                bad.append('%s 读不出: %s' % (os.path.basename(f), e))
                continue
            if len(y) == 0:
                bad.append('%s 是空文件' % os.path.basename(f))
                continue
            pk = float(np.abs(y).max())
            dc = float(abs(y.mean()))
            if not np.isfinite(y).all():
                bad.append('%s 含 NaN' % os.path.basename(f))
            elif pk >= 1.0:
                bad.append('%s 削波 %.3f' % (os.path.basename(f), pk))
            elif dc > 0.01:
                bad.append('%s 直流偏置 %.4f' % (os.path.basename(f), dc))
    assert not bad, '; '.join(bad)


def _exempt_bands(cfg):
    """render.json 里声明的对标豁免：**理由为空/全空白视为没写**
    （否则一句空话就能把任何偏离放行 —— 那等于没有检查）"""
    return {k: v for k, v in ((cfg or {}).get('align_exempt') or {}).items()
            if isinstance(v, str) and v.strip()}


@check
def t_alignment_vs_refs():
    """有参考画像的曲目，倍频程最大偏差不超过 8dB（超出说明参数漂了）。
    20-40Hz 不参与判据：那是 sub 低频，各家音源/生产差异极大，多数回放设备也听不到；
    它只作提示打印，要收紧请看 scorecard 的输出。

    **豁免必须显式声明**：参考曲是人声混音时，某些频段是它的母带特征（不是我们的目标）——
    照抄会把器乐压得比游戏里任何 BGM 都闷。这种情况要在该曲 `render.json` 里写
    `"align_exempt": {"5000-10000": "理由…"}`，**理由为空则视为没写**（不许静默放水）。
    这样"检查仍然默认严格"，放行一条就得留下可审计的文字。"""
    rows, exempted = [], []
    # 先自证契约：豁免必须有实质理由（空/空白 = 没写），否则这条检查会被人用空话绕过
    assert _exempt_bands({'align_exempt': {'a': '  ', 'b': ''}}) == {}, \
        '空白理由被当成有效豁免（检查可被空话绕过）'
    assert _exempt_bands({'align_exempt': {'a': '理由'}}) == {'a': '理由'}, \
        '正常豁免被误判为无效'
    assert _exempt_bands(None) == {}, '缺 render.json 时应视为无豁免'
    for d in song_dirs(with_json=False):
        p = os.path.join(d, 'render.json')
        if not os.path.exists(p):
            continue
        c = json.load(open(p, encoding='utf-8'))
        if c.get('legacy'):
            continue                      # 明确标注的遗留测试品不参与对标
        wav = os.path.join(d, (c.get('out') or '') + '.wav')
        if not os.path.exists(wav):
            continue
        ref = scorecard.load_ref(c['ref'])
        mine = metrics.profile(wav, ref['bpm'])
        ex = _exempt_bands(c)
        keys = [k for k in (ref.get('align_bands') or [k for k in ref['bands']
                                                      if not k.startswith('20-40')])
                if k not in ex]
        for k, why in ex.items():
            exempted.append((os.path.basename(d), k, why))
        worst = max(abs(mine['bands'][k] - ref['bands'][k]) for k in keys)
        sub = mine['bands']['20-40'] - ref['bands']['20-40']
        rows.append((os.path.basename(d), worst, sub, bool(c.get('strict_align'))))
    assert isinstance(rows, list), '内部错误：rows 不是列表'
    if not rows:
        # 没有带 wav 母版的曲目 = **正常的仓库交付态**（仓库只带成品 ogg，wav 体积大、
        # 由 make_song.py 重生成）→ 对标无从谈起，**跳过**而不是判错。
        # 实测：从 GitHub clone 下来跑 selftest 会在这里红（77/78），但那是仓库策略使然，
        # 不是使用者的问题；跑一次 make_song.py <曲目> 生成 wav 后这条检查即生效。
        print('        （没有带 wav 母版的曲目（仓库只带成品 ogg）→ 对标检查跳过；'
              '本地跑 `make_song.py <曲目>` 生成 wav 后即生效）')
        return
    for n, w, s, _st in rows:
        print('        %-24s 最大偏差 %.1fdB（20-40Hz 差 %+.1fdB）%s'
              % (n, w, s, '  [strict_align]' if _st else ''))
    for n, k, why in exempted:
        print('        %-24s 已声明豁免 %s：%s' % (n, k, why[:70]))
    # **默认只提示**：创作不该被参考画像绑架（原创曲尤其 —— 参考只是工程基线，
    # 不是作品该长成的样子）。只有曲目在 render.json 里显式写 "strict_align": true
    # （仿写曲：目标就是贴近参考）才把 8dB 当门。
    over = ['%s 差%.1fdB' % (n, w) for n, w, _s, _st in rows if w > 8.0]
    gate = ['%s 差%.1fdB' % (n, w) for n, w, _s, st in rows if w > 8.0 and st]
    assert not gate, ('对标偏差过大且本曲声明了 strict_align=true（先跑 make_song.py 收敛；'
                      '若是参考曲本身的母带特征，就在 render.json 写 align_exempt + 理由）: '
                      + ', '.join(gate))
    if over:
        print('        （对标偏差提示（**未当门**）：%s —— 原创曲可忽略；'
              '仿写曲要当门请在 render.json 写 "strict_align": true）' % ', '.join(over))


@check
def t_ep_part_wired():
    """arr.ep（电钢琴反拍切分）真的能出音符（此前是死代码）"""
    d = {'name': 'ep', 'bpm': 120, 'chords': {'C': [36, [55, 60, 64, 67, 72]]},
         'melody': {}, 'sections': [{'name': 'A', 'bars': 1, 'chords': ['C'],
                                     'melody': '', 'arr': {'ep': True}}]}
    p = os.path.join(TMP, 'ep.json')
    json.dump(d, open(p, 'w', encoding='utf-8'))
    dd, _ = quiet(song_engine.load, p)
    ev, _n = build(dd)
    assert len(ev.get('Hook', [])) > 4, 'arr.ep 没产出音符'


NOTE_PC = {'C': 0, 'C#': 1, 'Db': 1, 'D': 2, 'D#': 3, 'Eb': 3, 'E': 4, 'F': 5,
           'F#': 6, 'Gb': 6, 'G': 7, 'G#': 8, 'Ab': 8, 'A': 9, 'A#': 10,
           'Bb': 10, 'B': 11}
QUALITY = {'': (0, 4, 7), 'm': (0, 3, 7), '7': (0, 4, 7, 10), 'maj7': (0, 4, 7, 11),
           'maj9': (0, 4, 7, 11, 2), 'm7': (0, 3, 7, 10), '6': (0, 4, 7, 9),
           'm6': (0, 3, 7, 9), '5': (0, 7),          # 5 = 强力和弦（无三度）
           'sus4': (0, 5, 7), '7sus4': (0, 5, 7, 10), 'sus2': (0, 2, 7),
           'dim': (0, 3, 6), 'm7b5': (0, 3, 6, 10), 'aug': (0, 4, 8),
           'add9': (0, 2, 4, 7), 'm9': (0, 3, 7, 10, 2), '9': (0, 4, 7, 10, 2)}


def parse_chord(name):
    """和弦名 → (根音音级, 斜杠贝斯音级或 None, 期望音级集合)
    注意 '6/9' 是加音和弦（六九和弦），不是斜杠和弦 —— 必须先剥掉再处理斜杠。"""
    base = name
    slash = None
    if base.endswith('6/9'):
        root = base[:-3]
        if root in NOTE_PC:
            r = NOTE_PC[root]
            return r, None, {(r + i) % 12 for i in (0, 4, 7, 9, 2)}
    if '/' in base:
        base, slash_s = base.split('/', 1)
        slash = NOTE_PC.get(slash_s)
    for q in sorted(QUALITY, key=len, reverse=True):
        if q and base.endswith(q):
            root = base[:-len(q)]
            if root in NOTE_PC:
                r = NOTE_PC[root]
                return r, slash, {(r + i) % 12 for i in QUALITY[q]}
    if base in NOTE_PC:
        r = NOTE_PC[base]
        return r, slash, {(r + i) % 12 for i in QUALITY['']}
    return None, slash, None


@check
def t_styles_channels_and_programs():
    """**每一套风格预设**都不能有通道冲突/非法音色（踩过：4 套预设都撞了通道，
    导致两轨抢同一通道、program change 互相覆盖 → 音色错乱）"""
    bad = []
    for s, preset in song_engine.STYLES.items():
        seen = {}
        for tr, (prog, chan) in preset['programs'].items():
            if chan in seen:
                bad.append('%s: %s 与 %s 都用通道 %d' % (s, tr, seen[chan], chan))
            seen[chan] = tr
            if not (0 <= chan <= 15):
                bad.append('%s/%s 通道非法 %d' % (s, tr, chan))
            if prog is None:
                if chan != 9:
                    bad.append('%s/%s 只有鼓组通道能用 None' % (s, tr))
            elif not (0 <= prog <= 127):
                bad.append('%s/%s 音色号非法 %s' % (s, tr, prog))
        for tr in preset['programs']:
            if tr not in preset['mix']:
                bad.append('%s: %s 没有 mix 条目' % (s, tr))
    assert not bad, '; '.join(bad)


@check
def t_channels_and_programs():
    """每首歌的 MIDI 通道不重复（否则后一个 program change 会盖掉前一个）、program 合法"""
    for d in songs_or_fail():
        name = os.path.basename(d)
        data = song_engine.load(os.path.join(d, 'song.json'))
        seen = {}
        for tr, (prog, chan) in data['programs'].items():
            assert 0 <= chan <= 15, '%s/%s 通道非法 %s' % (name, tr, chan)
            if chan in seen:
                raise AssertionError('%s: %s 与 %s 都用通道 %d（音色会互相覆盖）'
                                     % (name, tr, seen[chan], chan))
            seen[chan] = tr
            if prog is not None:
                assert 0 <= prog <= 127, '%s/%s program 非法 %s' % (name, tr, prog)
            else:
                assert chan == 9, '%s/%s 只有鼓组通道能用 None program' % (name, tr)


def chords_from_py(path):
    """从代码式作曲脚本里安全提取 CHORDS 字面量（01/02 这类没走 song.json 的曲目）"""
    import ast
    tree = ast.parse(open(path, encoding='utf-8').read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, 'id', '') == 'CHORDS' for t in node.targets):
            d = ast.literal_eval(node.value)
            return {k: (v[0], list(v[1])) for k, v in d.items()}
    return {}


def validate_chords(name, chords, bad):
    for cname, (bass, tones) in chords.items():
        root, slash, want = parse_chord(cname)
        if want is None:
            bad.append('%s: 无法解析和弦名 %s' % (name, cname))
            continue
        got = {m % 12 for m in tones}
        if not got <= want:
            bad.append('%s: %s 含不属于该和弦的音级 %s（期望 %s）'
                       % (name, cname, sorted(got - want), sorted(want)))
        if (bass % 12) != (slash if slash is not None else root):
            bad.append('%s: %s 的低音 %d 与根音/斜杠音不符' % (name, cname, bass))


@check
def t_chord_names_match_notes():
    """和弦名与音集必须自洽（防手写和弦表打错音）—— song.json 与代码式脚本都查"""
    bad = []
    for d in songs_or_fail():
        name = os.path.basename(d)
        data = song_engine.load(os.path.join(d, 'song.json'))
        validate_chords(name, data['chords'], bad)
    # 代码式作曲脚本（render.json 指向曲目文件夹内的 .py）
    for d in songs_or_fail(with_json=False):
        p = os.path.join(d, 'render.json')
        if not os.path.exists(p):
            continue
        c = json.load(open(p, encoding='utf-8'))
        comp = c.get('composer', '')
        if '/' in comp or '\\' in comp or not comp.endswith('.py'):
            continue
        fp = os.path.join(d, comp)
        if not os.path.exists(fp):
            continue
        try:
            ch = chords_from_py(fp)
        except Exception as e:
            bad.append('%s: 解析 CHORDS 失败 %s' % (os.path.basename(d), e))
            continue
        if ch:
            validate_chords(os.path.basename(d) + '(code)', ch, bad)
    assert not bad, '; '.join(bad)


@check
def t_melody_within_sections():
    """旋律事件的"小节偏移"必须落在该段范围内（越界会悄悄串到别的段）"""
    bad = []
    for d in songs_or_fail():
        name = os.path.basename(d)
        data = song_engine.load(os.path.join(d, 'song.json'))
        for sec in data['sections']:
            bars = sec['bars']
            for key in ([sec.get('melody')] if sec.get('melody') else []):
                for ev in data['melody'].get(key, []):
                    b = ev[0]
                    if not (0 <= b < bars):
                        bad.append('%s/%s: 旋律 %s 的小节 %s 超出该段 %d 小节'
                                   % (name, sec['name'], key, b, bars))
            for ev in sec.get('melody_extra', []):
                if not (0 <= ev[0] < bars):
                    bad.append('%s/%s: melody_extra 小节 %s 越界' % (name, sec['name'], ev[0]))
            if len(sec['chords']) != bars:
                bad.append('%s/%s: 和弦 %d 个 ≠ 小节 %d（多出的会被忽略）'
                           % (name, sec['name'], len(sec['chords']), bars))
    assert not bad, '; '.join(bad)


@check
def t_track_balance():
    """**旋律不许被伴奏盖住** —— 用户的听感总结："欢快的音乐都有一个音轨和其它不平衡"。

    用 `probe_timbre.solo_song` 逐轨量**未归一化 raw** 的 2.5–5kHz 电平：
    响度归一化会吃掉 `arr.mix` 的差异（CC7 60→127 只差 0.23dB），**只有 raw 反映真实比例**。

    实测（47_cheer_pop）：
      修前（cheerful 用 `daily` 引擎、Hook = 钢弦吉他 program 25）：
        Hook **38.2dB** / Melody 22.2dB → 吉他比旋律高 **16dB**，把旋律盖住
      修后（`dance` 引擎 + 主奏颤音琴 11）：
        Hook **0.3dB** / Melody 20.8dB → 正常

    判据：**伴奏轨（Hook/Piano/Arp/Strings/Pad/Bass）里最响的那条，
    不得比 Melody 高 6dB 以上**。打击（Perc）不参与 —— 它是节奏层，
    在 2.5–5kHz 天然比旋律高（实测各曲都 ~33dB），不是"伴奏压主奏"。
    """
    import probe_timbre as pt
    bad, checked = [], 0
    for p in sorted(glob.glob(os.path.join(ROOT, 'songs', '*', 'song.json'))):
        if checked >= 2:                 # 逐轨 solo 要渲染 N 次，全库跑太慢
            break
        try:
            d = song_engine.load(p)
        except Exception:
            continue
        if (d.get('style') or '') not in ('dance', 'daily'):
            continue
        if not d.get('theme'):
            # 只查**有主题依据**的曲目。早期无主题曲（11_dn75_neon / 12_d75_warm 等）
            # 的旋律是手写的、音区本身偏低（实测 Melody 只有 6.0dB），量到的是
            # "这首曲子的音区"而不是"轨间平衡" —— 拿它判平衡会误报。
            continue
        rows = pt.solo_song(p)
        by = {r['name']: r['abs']['2500-5000'] for r in rows}
        if 'Melody' not in by:
            continue
        mel = by['Melody']
        # 只算**中高频伴奏层**：Hook（吉他）/ Arp / Strings / Pad / Piano。
        # 排除 Perc（节奏层，2.5–5kHz 天然 ~33dB）与 Bass（低音层 —— 它在那里的能量
        # 是泛音，实测 47 号的 Bass 有 22.4dB 但听感上并不"盖住旋律"，拿它判会误报）。
        rest = {k: v for k, v in by.items()
                if k in ('Hook', 'Arp', 'Strings', 'Pad', 'Piano')}
        if not rest:
            continue
        checked += 1
        top_k = max(rest, key=lambda k: rest[k])
        if rest[top_k] > mel + 6.0:
            bad.append('%s: %s %.1fdB 比 Melody %.1fdB 高 %.1fdB' % (
                os.path.basename(os.path.dirname(p)), top_k, rest[top_k],
                mel, rest[top_k] - mel))
    assert checked >= 1, '没有可查的曲目（夹具太少，这条检查会空转）'
    assert not bad, '伴奏盖住旋律（"一轨和其它不平衡"）：%s' % '；'.join(bad)
    print('        %d 首：伴奏轨都未盖过旋律（阈值 +6dB，用未归一化 raw 量）' % checked)


@check
def t_lead_timbre_attack():
    """**主奏音色的起音必须够快** —— 用户听感："有一个乐器慢一点不太和谐"。

    那条"慢"的乐器就是主旋律。渲染固定乐句量音头（10%→90% 峰值）实测：
      电钢 4ms · 钟琴 4ms · 钢琴 8ms · **木琴 12ms** · 颤音琴 42ms · 合成主奏 282ms
    打击（踩镲/底鼓）的起音是 1–5ms —— 主奏慢一个数量级，在 132BPM 的 8/16 分
    伴奏里就显"慢半拍"（用户就是这么听出来的，当时主奏是颤音琴）。
    判据 **≤ 20ms**：木琴 12 有余量，颤音琴 42 会被拦住。
    """
    import tempfile
    import probe_timbre as pt
    import numpy as np
    bad = []
    out = os.path.join(tempfile.gettempdir(), 'mg_lead_atk')
    os.makedirs(out, exist_ok=True)
    for st, cfg in sorted(song_engine.STYLES.items()):
        prog = (cfg.get('programs') or {}).get('Melody')
        if not prog:
            continue
        mid = os.path.join(out, '%s.mid' % st)
        base = os.path.join(out, st)
        pt.phrase_midi(mid, prog[0], (84,), 1, 120.0)
        pt.render_bare(mid, base)
        w = base + '.raw.wav'
        if not os.path.exists(w):
            w = base + '.wav'
        assert os.path.exists(w), '主奏起音检查渲染失败：%s' % st
        import soundfile as _sf
        y, sr = _sf.read(w, dtype='float64')
        m = y.mean(axis=1) if y.ndim > 1 else y
        hop = max(1, int(sr * 0.002))
        env = np.array([np.sqrt((m[i:i + hop] ** 2).mean())
                        for i in range(0, max(1, len(m) - hop), hop)])
        ref = env.max()
        i90 = next((i for i, v in enumerate(env) if v >= ref * 0.9), 0)
        atk = i90 * 2
        if atk > 20:
            bad.append('%s(Melody prog %d) 起音 %dms' % (st, prog[0], atk))
    assert not bad, '主奏起音太慢（会听着"慢半拍"）：%s' % '；'.join(bad)
    print('        各 STYLES 的主奏起音都 ≤ 20ms（渲染固定乐句量音头）')


@check
def t_section_transition():
    """**段与段之间要有过渡或留白** —— 用户："有转变可以，但要过渡自然或中间有空白作为间隔"。

    量每个段边界：边界处（±0.15s）的短时 RMS 中位 vs 两侧（0.3~0.9s）的中位。
      · **谷深 ≥ 6dB** → 边界处明显低 = 有留白（`patterns.section_gap`）✓
      · **边界跳变 < 3dB** → 两侧本来就接近 = 有渐变（编配/能量曲线平滑）✓
      · 两者都不是 → **硬切** ✗ —— 就是"突兀"

    背景（2026-09-15 实测）：加留白之前 **34 首里 27 首是硬切**（谷深 −4~+5.7dB、
    跳变中位 5.5dB）；47_cheer_pop 加 `section_gap: 1.0` 后谷深 −3.9 → **+9.2dB**，
    从"硬切"变"有留白"。只查**主题路径**曲目（早期无主题曲的段界形态是历史包袱，
    重做才有意义）。
    """
    import json as _json
    import numpy as _np
    bad, checked = [], 0
    for p in sorted(glob.glob(os.path.join(ROOT, 'songs', '*', 'song.json'))):
        sid = os.path.basename(os.path.dirname(p))
        try:
            d = song_engine.load(p)
        except Exception:
            continue
        if not d.get('theme'):
            continue
        hit = glob.glob(os.path.join(ROOT, 'songs', sid, '*_sf.wav'))
        if not hit:
            continue
        bpm = d.get('bpm') or 120.0
        m, sr, x = metrics.load(hit[0])
        mono = x.mean(axis=1) if x.ndim > 1 else x
        hop = max(1, int(sr * 0.05))
        env = 20 * _np.log10(_np.maximum(_np.array(
            [_np.sqrt((mono[i:i + hop] ** 2).mean())
             for i in range(0, max(1, len(mono) - hop), hop)]), 1e-9))
        bar_s = 4 * 60.0 / float(bpm)
        t, ok_all, worst = 0.0, True, None
        for s in (d.get('sections') or [])[:-1]:
            t += int(s.get('bars') or 0) * bar_s
            b = int(t / 0.05)
            pre = env[max(0, b - 18):b - 6]        # 边界前 0.3~0.9s（前段主体）
            post = env[b + 6:b + 18]               # 边界后 0.3~0.9s（后段主体）
            if len(pre) < 4 or len(post) < 4:
                continue
            # ⚠ 早先拿"边界 ±0.15s"当 mid 是错的：那个窗**跨了边界**，
            #   前半是渐弱尾、后半是新段头，一平均就看不出留白（实测 dip 只有 −2~−3dB）。
            #   正确做法是分别看两侧：
            #     · 段末**渐弱**：边界前 0.15s 明显低于前段主体
            #     · 段首**渐入**：边界后 0.15s 明显低于后段主体
            #     · 或边界处有**留白**：两侧主体都比边界附近高（旧口径，保留）
            p_edge = env[max(0, b - 3):b]
            q_edge = env[b:b + 3]
            fade_out = (float(_np.median(pre)) - float(_np.median(p_edge))
                        if len(p_edge) >= 1 else 0.0)
            fade_in = (float(_np.median(post)) - float(_np.median(q_edge))
                       if len(q_edge) >= 1 else 0.0)
            jump = abs(float(_np.median(post)) - float(_np.median(pre)))
            ok = (fade_out >= TRANSITION_FADE_MIN) or (fade_in >= TRANSITION_FADE_MIN) \
                or (jump < TRANSITION_JUMP_MAX)
            if not ok:
                ok_all = False
                if worst is None or jump > worst[0]:
                    worst = (jump, s.get('name'), max(fade_out, fade_in))
        checked += 1
        if not ok_all and worst:
            bad.append('%s: 边界「%s」跳 %.1fdB、两端渐弱/渐入只有 %.1fdB（硬切）'
                       % (sid, worst[1], worst[0], worst[2]))
    assert checked >= 3, '主题路径曲目太少（%d），这条检查会空转' % checked
    assert not bad, ('段界硬切（要"过渡自然或中间留白"）：%s' % '；'.join(bad[:4]))
    print('        %d 首主题路径曲目：段界都有留白或渐变' % checked)


@check
def t_density_dynamic_range():
    """**段级密度要有大起大落** —— 用户指定案例 BGM35 实测"逐小节起音数 0→66，变化 66 倍"，
    结构是 3 个高潮 + 3 个呼吸口；而我们原来只有 **1.5–2.8 倍**（全程一条平线）。

    口径：**直接数 MIDI 音符**（不渲染、不受音源质量影响 —— 用户："只需要 midi 一样就行"）。
    判据：每小节音符数的 **max/min ≥ 4 倍**。
    只查**声明了 `arr.density`** 的曲目 —— 早期曲没有这一档，量的是历史包袱；
    新曲（`new_song` 生成的）都带 `density`（见 `song_engine.build_events` 的说明）。
    """
    checked, bad = 0, []
    for p in sorted(glob.glob(os.path.join(ROOT, 'songs', '*', 'song.json'))):
        try:
            d = song_engine.load(p)
        except Exception:
            continue
        secs = d.get('sections') or []
        if not any((s.get('arr') or {}).get('density') is not None for s in secs):
            continue                      # 没声明 density 的曲不查（历史曲目）
        ev, _nb = song_engine.build_events(d)
        per, bar = [], 0
        for s in secs:
            n = int(s.get('bars') or 0)
            lo, hi = bar * 4.0, (bar + n) * 4.0
            cnt = sum(1 for k in ev for (t, _d, _m, _v) in ev[k] if lo <= t < hi)
            per.append(cnt / max(1.0, n))
            bar += n
        if len(per) < 3 or min(per) <= 0:
            continue
        checked += 1
        ratio = max(per) / min(per)
        if ratio < 4.0:
            bad.append('%s: %.1f 倍（min %.1f / max %.1f 音每小节）'
                       % (os.path.basename(os.path.dirname(p)), ratio, min(per), max(per)))
    assert checked >= 1, '没有声明 arr.density 的曲目（这条检查会空转）'
    assert not bad, ('段级密度太平（要 ≥4 倍起伏，BGM35 是 66 倍）：%s' % '；'.join(bad[:4]))
    print('        %d 首带 density 的曲目：段级密度起伏都 ≥4 倍' % checked)


@check
def t_style_desc_matches_programs():
    """风格预设的文字说明要与实际音色一致（防复制粘贴串味）"""
    want = {'gorgeous': ('竖琴', 'Hook', 46), 'ballad': ('尼龙', 'Hook', 24),
            'acoustic': ('钢弦', 'Hook', 25),
            # daily 的 Hook 2026-09-15 由钢弦 25 改尼龙 24（钢弦的拨弦泛音在 2.5–10k
            # 比电钢高 39dB、把旋律盖住 —— 见 song_engine.STYLES 的注释），desc 同步改
            'daily': ('尼龙', 'Hook', 24),
            # dance 的主奏 2026-09-15：合成主奏 81 → 颤音琴 11 → **木琴 13**
            # （合成主奏电子味重、happy 只 0.323；颤音琴 attack 42ms 听着"慢半拍"；
            #  木琴 attack 12ms、最响、不拖 —— 见 song_engine.STYLES 的实测表）
            'dance': ('木琴', 'Melody', 13)}
    for s, (kw, tr, prog) in want.items():
        preset = song_engine.STYLES[s]
        desc = preset.get('desc', '')
        assert kw in desc, '%s 说明里没有关键词 %s' % (s, kw)
        assert preset['programs'][tr][0] == prog, \
            '%s 说明写 %s 但 %s 是 GM%s' % (s, kw, tr, preset['programs'][tr][0])


@check
def t_legacy_composer_outdir():
    """遗留作曲脚本（bgm_synth/bgm_acoustic）必须把产物写进命令行给的目录"""
    outdir = os.path.join(TMP, 'legacyout')
    os.makedirs(outdir, exist_ok=True)
    for script, mid in (('bgm_synth.py', 'pure_garden_theme.mid'),
                        ('bgm_acoustic.py', 'garden_warm_test.mid')):
        r = subprocess.run([sys.executable, os.path.join(HERE, script), outdir],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', cwd=ROOT)
        assert r.returncode == 0, '%s 运行失败: %s' % (script, r.stderr[-200:])
        f = os.path.join(outdir, mid)
        assert os.path.exists(f), '%s 没把 %s 写进指定目录（argv 契约失效）' % (script, mid)
        assert not os.path.exists(os.path.join(HERE, mid)), \
            '%s 把产物写到了 scripts/ 里' % script


@check
def t_play_midi_safe():
    """play_midi 能在无播放任务时安全停止（不真的出声）"""
    import play_midi
    buf = io.StringIO()
    with redirect_stdout(buf):
        play_midi.stop()
    assert '停止' in buf.getvalue() or 'MCI' in buf.getvalue()


@check
def t_audio_semantics():
    """渲染结果的时长与结构要对得上：总长≈谱面长度；有音符的段落不能是静音"""
    for d in songs_or_fail():
        name = os.path.basename(d)
        p = os.path.join(d, 'render.json')
        if not os.path.exists(p):
            continue
        c = json.load(open(p, encoding='utf-8'))
        wav = os.path.join(d, (c.get('out') or '') + '.wav')
        if not os.path.exists(wav):
            continue
        data = song_engine.load(os.path.join(d, 'song.json'))
        ev, nbars = build(data)
        # 一小节几个四分音符**由拍号定**（4/4 → 4；3/4 → 3；6/8 → 3）。
        # 写死 4 的话，3/4 的曲子谱面会被算长 1/3 → 渲染时长"对不上"，全是误报。
        bar = song_engine.bar_beats(data) * 60.0 / data['bpm']
        y, sr = sf.read(wav, dtype='float64', always_2d=True)
        dur = len(y) / sr
        expect = nbars * bar
        assert expect - 0.6 <= dur <= expect + 8.0, \
            '%s 时长 %.1fs 与谱面 %.1fs 不符（多出的是混响尾，正常 ≤8s）' % (name, dur, expect)
        mono = y.mean(axis=1)
        bar0 = 0
        B = song_engine.bar_beats(data)
        for sec in data['sections']:
            n = sec['bars']
            t0, t1 = bar0 * bar, (bar0 + n) * bar
            seg = mono[int(t0 * sr):int(min(t1, dur) * sr)]
            notes = sum(len(v) for tr, v in ev.items()
                        for (t, _dd, _m, _v) in v
                        if bar0 * B <= t < (bar0 + n) * B)
            if notes >= 5 and len(seg) > sr // 2:
                rms = 20 * np.log10(max(1e-9, np.sqrt((seg ** 2).mean())))
                assert rms > -45, '%s/%s 有 %d 个音符却是静音(%.1fdB)' % (
                    name, sec['name'], notes, rms)
            bar0 += n


def _unused_chord_report(dirs):
    """返回"定义了但没被任何段落用到"的和弦提示行（纯报告，不是失败项）"""
    out = []
    for d in dirs:
        name = os.path.basename(d)
        data = song_engine.load(os.path.join(d, 'song.json'))
        used = {c for sec in data['sections'] for c in sec['chords']}
        unused = sorted(set(data['chords']) - used)
        if unused:
            out.append('%s 未使用的和弦: %s' % (name, ', '.join(unused)))
    return out


@check
def t_unused_chords_warn():
    """残留和弦的**提示机制**必须真的会响 —— 两个方向都验：
    ① 有残留 → 必须点名（否则"提示"是装饰：本项以前只打印、从不 FAIL = 空转的绿灯）
    ② 无残留 → 必须闭嘴（否则是"永远报警"的假检查）
    真实曲目的残留只报告、不失败（和弦库留备用和弦是合理的）。"""
    d1 = tempfile.mkdtemp(dir=TMP)
    json.dump({'name': 'u1', 'bpm': 120,
               'chords': {'D': [38, [57, 62, 66, 69, 74]], 'G7': [31, [55, 59, 62, 65]]},
               'melody': {}, 'sections': [{'name': 'A', 'bars': 1, 'chords': ['D'],
                                           'melody': '', 'arr': {'uku': True}}]},
              open(os.path.join(d1, 'song.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    got = _unused_chord_report([d1])
    assert got and 'G7' in got[0], '有残留和弦却没提示（提示机制是装饰）: %r' % got

    d2 = tempfile.mkdtemp(dir=TMP)
    json.dump({'name': 'u2', 'bpm': 120, 'chords': {'D': [38, [57, 62, 66, 69, 74]]},
               'melody': {}, 'sections': [{'name': 'A', 'bars': 1, 'chords': ['D'],
                                           'melody': '', 'arr': {'uku': True}}]},
              open(os.path.join(d2, 'song.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    assert not _unused_chord_report([d2]), '没有残留和弦却报警了（假检查）'

    for line in _unused_chord_report(songs_or_fail()):
        print('        ' + line)


@check
def t_metrics_instrument_accuracy():
    """**验证测量仪器本身**：合成的已知信号 → 指标必须给出可解析的正确答案。
    （前面所有检查都建立在"分数是准的"这个假设上，这条把它证明掉。）"""
    sr = 44100
    t = np.arange(sr * 2) / sr
    def wav(freq, amp=0.5, hard_left=False, identical=False, gain=1.0):
        sig = amp * gain * np.sin(2 * np.pi * freq * t)
        if hard_left:
            x = np.stack([sig, np.zeros_like(sig)], axis=1)
        else:
            x = np.stack([sig, sig], axis=1)
        p = os.path.join(TMP, 'sine_%d_%s%s.wav' % (freq, hard_left, identical))
        sf.write(p, x.astype(np.float32), sr)
        return p
    # 频段定位
    b60 = metrics.octave_bands(*metrics.load(wav(60))[:2])
    assert b60['40-80'] == 0.0, '60Hz 正弦的 40-80 频段不是最强: %s' % b60['40-80']
    assert b60['10000-18000'] < -40, '60Hz 正弦在 10-18k 不该有能量'
    b12k = metrics.octave_bands(*metrics.load(wav(12000))[:2])
    assert b12k['10000-18000'] == 0.0, '12kHz 正弦的顶频段不是最强'
    # 响度
    m, s, _x = metrics.load(wav(1000, 0.5))
    rms = metrics.rms_db(m)
    assert abs(rms - (-9.03)) < 0.4, '0.5 幅值正弦的 RMS 应为 −9.03dB，实得 %.2f' % rms
    # 立体声宽度
    assert metrics.width(metrics.load(wav(500, 0.5, identical=True))[2]) < 0.02, \
        '左右完全相同的信号宽度应为 0'
    assert metrics.width(metrics.load(wav(500, 0.5, hard_left=True))[2]) > 0.95, \
        '硬左信号宽度应接近 1'
    # 质心：必须给出**接近真值**的数。
    # 以前只测"低频很低、高频很高"（<500 / >8000），把质心整体 ×2 也照样通过
    # —— 变异测试抓到了这个洞（系统性偏差是最危险的仪器故障）。
    for f in (60, 1000, 12000):
        c = metrics.centroid(*metrics.load(wav(f))[:2])
        assert abs(c - f) <= f * 0.2, \
            '%dHz 正弦的质心应≈%d，实得 %.0f（仪器有系统性偏差）' % (f, f, c)


@check
def t_midi_roundtrip():
    """MIDI 往返：引擎产出的事件必须**一个不少**地出现在写出的文件里
    （音高/力度/时间量化到 tick 后一致）"""
    import midi_probe
    d = mini = {'name': 'rt', 'bpm': 100, 'style': 'daily',
                'chords': {'C': [36, [55, 60, 64, 67, 72]],
                           'G': [31, [55, 59, 62, 67, 71]]},
                'melody': {'m': [[0, 0, 1, 72], [0, 2, 1, 76], [1, 0, 2, 74]]},
                'sections': [{'name': 'A', 'bars': 2, 'chords': ['C', 'G'],
                              'melody': 'm',
                              'arr': {'uku': True, 'piano': True, 'bass': True,
                                      'pad': True, 'glock': True, 'perc': 1}}]}
    sp = os.path.join(TMP, 'rt.json')
    json.dump(d, open(sp, 'w', encoding='utf-8'))
    dd, _ = quiet(song_engine.load, sp)
    ev, _n = build(dd)
    mid = os.path.join(TMP, 'rt.mid')
    song_engine.write_midi(dd, ev, mid)
    res = midi_probe.parse(mid, quiet=True)
    got = {}
    for tr in res['tracks']:
        for (start, dur, note, vel) in tr['notes']:
            got.setdefault((tr['channel'], start), []).append((note, vel))
    ppq = res['division']
    miss = 0
    for tr, notes in ev.items():
        chan = dd['programs'][tr][1]
        for (t, _dur, m, v) in notes:
            key = (chan, int(t * ppq))
            if key not in got or not any(n == m for (n, _vv) in got[key]):
                miss += 1
    assert miss == 0, '往返后有 %d 个事件对不上（丢失/音高错）' % miss
    assert res['note_count'] == sum(len(v) for v in ev.values()), \
        'MIDI 音符数 %d ≠ 引擎事件数 %d' % (res['note_count'],
                                          sum(len(v) for v in ev.values()))


@check
def t_autotune_convergence():
    """自动调参的算法行为（用假的测量函数，不真渲染）：
    ① 必须收敛到容差内 ② 每轮误差不许变大（不震荡）③ 不能超上限"""
    import make_song as ms
    ref = {'bands': {k: 0.0 for k in
                     ('20-40', '40-80', '80-160', '160-315', '315-630',
                      '630-1250', '1250-2500', '2500-5000', '5000-10000',
                      '10000-18000')}, 'width': 0.50, 'rms_db': -15.0}
    # 真编一个小 MIDI：autotune 要从它读出真实速度并传给 measure（不是测速猜的）
    sp = os.path.join(TMP, 'conv.json')
    json.dump({'name': 'conv', 'bpm': 120, 'style': 'daily',
               'chords': {'C': [36, [55, 60, 64, 67, 72]]},
               'melody': {'m': [[0, 0, 1, 72], [0, 2, 1, 76]]},
               'sections': [{'name': 'A', 'bars': 2, 'chords': ['C', 'C'], 'melody': 'm',
                             'arr': {'uku': True, 'piano': True, 'bass': True,
                                     'pad': True, 'glock': True, 'perc': 1}}]},
              open(sp, 'w', encoding='utf-8'), ensure_ascii=False)
    conv_mid = song_engine.compose(sp, os.path.join(TMP, 'conv.mid'), quiet=True)
    # 模拟：每个 EQ 参数以 1:1 影响对应频段（linear），宽度与倍数成正比
    real_render, real_measure, real_width = (ms.render_midi.render, ms.measure,
                                             ms.render_midi.set_width_exact)
    real_ogg = ms.render_midi.encode_ogg      # autotune 定稿要编码 OGG，这里一并打桩
    errs = []
    def fake_render(*a, **kw):
        return None
    def fake_measure(wav, r, bpm=None):
        cfg = fake_measure.cfg
        # 速度必须由 MIDI 传进来（不是测速猜的）—— 顺带守住这个契约
        fake_measure.bpm = bpm
        # 模拟模型必须"可解"：偏差要落在 LIMITS 允许的范围内（否则不收敛是正确行为）
        b = {}
        b['20-40'] = -1.0 - cfg['hp'] * 0.05 + cfg['low'] * 0.2
        b['40-80'] = 3.0 + cfg['low'] * 1.0
        b['80-160'] = 2.0 + cfg['low'] * 1.0
        b['160-315'] = 1.0 + cfg['low'] * 0.3
        b['315-630'] = 1.5
        b['630-1250'] = 1.0 + cfg['mid_db'] * 0.3
        b['1250-2500'] = 4.0 + cfg['mid_db'] * 1.0
        b['2500-5000'] = 3.0 + cfg['mid_db'] * 1.0
        b['5000-10000'] = 3.0 + cfg['shelf'] * 1.0
        b['10000-18000'] = 4.0 + cfg['shelf'] * 1.0
        mine = {'bands': b, 'width': 0.30 * cfg['width'], 'centroid': 2500,
                'rms_db': cfg['rms'], 'bpm': 120, 'bar': 2.0}
        # 误差只看自动调参真正负责的频段（20-40Hz 是提示性的，不参与收敛判据）
        errs.append(max(abs(b[k] - r['bands'][k]) for k in r['bands']
                        if not k.startswith('20-40')))
        fake_measure.last_bands = {k: round(v, 1) for k, v in b.items()}
        return mine
    try:
        ms.render_midi.render = fake_render
        ms.measure = fake_measure
        ms.render_midi.set_width_exact = lambda *a, **k: None
        ms.render_midi.encode_ogg = lambda *a, **k: None   # 本检查只测调参算法，不编码 OGG
        cfg = {'rms': -15.0, 'width': 1.6, 'shelf': 0.0, 'hp': 38.0,
               'low': 0.0, 'drive': 1.5, 'mid_db': 0.0}
        fake_measure.cfg = cfg
        out = ms.autotune(cfg, ref, conv_mid, os.path.join(TMP, 'x'), max_iter=6)
    finally:
        ms.render_midi.render = real_render
        ms.measure = real_measure
        ms.render_midi.set_width_exact = real_width
        ms.render_midi.encode_ogg = real_ogg
    assert errs and errs[-1] <= 2.0, \
        '没收敛：末轮最大误差 %.1f（每轮 %s；末轮 cfg %s；末轮频段 %s）' % (
            errs[-1] if errs else -1, [round(e, 1) for e in errs],
            {k: round(v, 2) for k, v in out.items()
             if k in ('low', 'mid_db', 'shelf', 'hp', 'width')},
            getattr(fake_measure, 'last_bands', None))
    worst = max(errs[i] - errs[i - 1] for i in range(1, len(errs))) if len(errs) > 1 else 0
    assert worst <= 0.6, '误差在中途变大（震荡）: %s' % [round(e, 1) for e in errs]
    for k, (lo, hi) in ms.LIMITS.items():
        assert lo - 1e-6 <= out.get(k, 0) <= hi + 1e-6, '%s 越界 %s' % (k, out.get(k))
    assert fake_measure.bpm == 120.0, \
        'measure 没收到 MIDI 里的真实速度（拿到 %s）' % fake_measure.bpm


@check
def t_render_rms_contract():
    """渲染契约：成品响度应接近请求的 target（峰值上限可能拉低一点，但不该差太多）"""
    if FAST:
        return
    d = {'name': 'rms', 'bpm': 120, 'style': 'daily',
         'chords': {'C': [36, [55, 60, 64, 67, 72]]},
         'melody': {'m': [[0, 0, 1, 72]]},
         'sections': [{'name': 'A', 'bars': 2, 'chords': ['C', 'C'],
                       'melody': 'm', 'arr': {'uku': True, 'bass': True,
                                              'perc': 1}}]}
    sp = os.path.join(TMP, 'rms.json')
    json.dump(d, open(sp, 'w', encoding='utf-8'))
    mid = os.path.join(TMP, 'rms.mid')
    quiet(song_engine.compose, sp, mid)
    out = os.path.join(TMP, 'rms_sf')
    quiet(render_midi.render, mid, out, -18.0, 1.5, 0.0, 38.0, 0.0, 1.5, 0.0,
          False, False)
    y, sr = sf.read(out + '.wav', dtype='float64', always_2d=True)
    rms = 20 * np.log10(np.sqrt((y.mean(axis=1) ** 2).mean()))
    assert abs(rms - (-18.0)) < 1.6, '请求 −18dBFS，实得 %.1f（契约失效）' % rms
    assert float(np.abs(y).max()) <= 1.0


@check
def t_malformed_inputs():
    """畸形 song.json 必须给出**可读的错误**（不是裸 traceback、更不能静默出坏 MIDI）"""
    cases = {
        'notjson.json': '{ 这不是 JSON',
        'nosections.json': '{"chords": {}, "melody": {}}',
        'badchord.json': json.dumps({'name': 'x', 'bpm': 100,
                                     'chords': {'C': [36, [55, 60, 64]]},
                                     'melody': {},
                                     'sections': [{'name': 'A', 'bars': 1,
                                                   'chords': ['Am'],
                                                   'melody': '', 'arr': {}}]}),
    }
    for fn, txt in cases.items():
        p = os.path.join(TMP, fn)
        open(p, 'w', encoding='utf-8').write(txt)
        try:
            quiet(song_engine.compose, p, os.path.join(TMP, fn + '.mid'))
        except SystemExit as e:
            assert str(e).strip(), '%s 报了空错误' % fn
            continue
        except Exception as e:
            raise AssertionError('%s 抛出了裸异常 %s: %s' % (fn, type(e).__name__, e))
        raise AssertionError('%s 没有被拦下（会静默产出坏 MIDI）' % fn)


@check
def t_hygiene_no_leftovers():
    """仓库卫生：不留**真正的**临时产物（*.raw.wav、_selftest_* 等测试目录）。

    `__pycache__` 是 Python 正常行为，不算残留；
    `songs/_archive/` 是**有意的归档**（质量分级为"不好"的曲目移到那里，`.gitignore` 已整目录排除），
    也不算残留。"""
    bad = []
    for p in glob.glob(os.path.join(ROOT, '**', '*.raw.wav'), recursive=True):
        if '.venv' not in p:
            bad.append(os.path.relpath(p, ROOT))
    for p in glob.glob(os.path.join(ROOT, 'songs', '_*')):
        if os.path.basename(p) == '_archive':
            continue
        bad.append(os.path.relpath(p, ROOT))
    for p in glob.glob(os.path.join(ROOT, '*.log')):
        bad.append(os.path.relpath(p, ROOT))
    assert not bad, '残留文件: ' + ', '.join(bad)


@check
def t_paths_with_spaces_and_cjk():
    """带空格和中文的路径也要能用（参考曲目录就是中文名）"""
    d = os.path.join(TMP, '音乐 test 目录')
    os.makedirs(d, exist_ok=True)
    wav = os.path.join(d, '测试.wav')
    sr = 22050
    t = np.arange(sr) / sr
    sig = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    sf.write(wav, np.stack([sig, sig], axis=1), sr)
    prof = metrics.profile(wav, 120.0)
    assert prof['bands'] and prof['width'] >= 0
    sp = os.path.join(d, '歌.json')
    json.dump({'name': 'cn', 'bpm': 120, 'style': 'daily',
               'chords': {'C': [36, [55, 60, 64, 67, 72]]},
               'melody': {'m': [[0, 0, 1, 72]]},
               'sections': [{'name': 'A', 'bars': 1, 'chords': ['C'],
                             'melody': 'm', 'arr': {'uku': True, 'bass': True}}]},
              open(sp, 'w', encoding='utf-8'))
    mid = song_engine.compose(sp, os.path.join(d, '歌.mid'), quiet=True)
    assert os.path.exists(mid) and os.path.getsize(mid) > 100


@check
def t_autotune_reports_caps():
    """**"不给假达标"不变量**：若某频段仍超容差，日志必须说明原因（到顶/冻结/编配建议），
    不能只打"✓ 达标"。（这条 bug 出现过三次：夹紧当变化量、宽阈值、夹成 0 静默）"""
    import make_song as ms
    ref = {'bands': {k: 0.0 for k in
                     ('20-40', '40-80', '80-160', '160-315', '315-630',
                      '630-1250', '1250-2500', '2500-5000', '5000-10000',
                      '10000-18000')}, 'width': 0.5, 'rms_db': -15.0}
    real_render, real_measure, real_width = (ms.render_midi.render, ms.measure,
                                             ms.render_midi.set_width_exact)
    real_ogg = ms.render_midi.encode_ogg      # autotune 定稿要编码 OGG，这里一并打桩
    # 顶频需要 −20dB 才达标，但 shelf 下限是 −3 → 必须被报成"到顶"
    def fake_measure(wav, r, bpm=None):
        c = fake_measure.cfg
        b = {k: 0.0 for k in r['bands']}
        b['5000-10000'] = 20.0 + c['shelf']
        b['10000-18000'] = 20.0 + c['shelf']
        return {'bands': b, 'width': 0.5, 'centroid': 2000, 'rms_db': c['rms'],
                'bpm': 120, 'bar': 2.0}
    buf = io.StringIO()
    try:
        ms.render_midi.render = lambda *a, **k: None
        ms.measure = fake_measure
        ms.render_midi.set_width_exact = lambda *a, **k: None
        ms.render_midi.encode_ogg = lambda *a, **k: None   # 本检查只测调参算法，不编码 OGG
        cfg = {'rms': -15.0, 'width': 1.7, 'shelf': -3.0, 'hp': 38.0, 'low': 0.0,
               'drive': 1.5, 'mid_db': 0.0}       # shelf 已在边界
        fake_measure.cfg = cfg
        with redirect_stdout(buf):
            ms.autotune(cfg, ref, 'x.mid', os.path.join(TMP, 'x'), max_iter=3)
    finally:
        ms.render_midi.render = real_render
        ms.measure = real_measure
        ms.render_midi.set_width_exact = real_width
        ms.render_midi.encode_ogg = real_ogg
    out = buf.getvalue()
    assert '到顶' in out or '到头' in out or 'EQ 到头' in out, \
        '参数到顶且仍超容差时，日志没有说明原因（假达标）:\n%s' % out


@check
def t_vocal_classifier_sanity():
    """人声/器乐判定不能误判（误判会静默改变对标靶子）：
    已知器乐参考曲（游戏 BGM，低频主导）必须判成 instrumental；
    已知人声歌（しみじみゅどうふ.flac）必须判成 vocal_forward。

    **两个方向都要真测逻辑**：以前这条只读 `refs/*.json` 里**已经存好**的 character 字段，
    等于在检查"上次写进去的字"，分类逻辑坏了它照样绿（变异测试抓到了这个洞）。
    现在 ① 用合成画像直接测分类器本身；② 已存画像必须与**当前逻辑**算出的一致。"""
    # ① 分类器本身：人声型 / 器乐型 两边都要判对
    vocalish = {'20-40': -68.7, '40-80': -33.1, '80-160': -8.8, '160-315': -3.8,
                '315-630': 0.0, '630-1250': -2.4, '1250-2500': -6.7, '2500-5000': -15.8,
                '5000-10000': -28.8, '10000-18000': -41.7}
    instr = {'20-40': -13.3, '40-80': -11.8, '80-160': -4.7, '160-315': -3.0,
             '315-630': 0.0, '630-1250': -1.0, '1250-2500': -6.0, '2500-5000': -18.0,
             '5000-10000': -20.7, '10000-18000': -35.0}
    got_v, al_v = metrics.character_of(vocalish)
    got_i, al_i = metrics.character_of(instr)
    assert got_v == 'vocal_forward', '人声型画像被判成 %s' % got_v
    assert got_i == 'instrumental', '器乐型画像被判成 %s' % got_i
    assert '20-40' not in al_v, '人声画像不该对齐 20-40Hz（那是它的母带特征）'
    assert '20-40' not in al_i, '20-40Hz 是 sub 提示项，器乐画像也不该拿它当判据（见坑 33）'
    assert '80-160' not in al_v and '160-315' in al_v, '人声画像只对齐 160-10kHz'
    assert '10000-18000' not in al_v, '人声画像不该对齐 10-18kHz'
    assert '10000-18000' in al_i, '器乐画像应当对齐到 10-18kHz'

    cases = []
    for nm, want in (('BGM16c', 'instrumental'), ('bgm01c', 'instrumental'),
                     ('BGM04', 'instrumental'), ('BGM18s', 'instrumental'),
                     ('BGM18b', 'instrumental'),
                     ('shimijimi', 'vocal_forward')):
        p = os.path.join(ROOT, 'refs', nm + '.json')
        if os.path.exists(p):
            d = json.load(open(p, encoding='utf-8'))
            cases.append((nm, d.get('character'), want, d.get('bands'),
                          d.get('align_bands')))
    for nm, got, want, bands, align in cases:
        assert got == want, '%s 判定为 %s，期望 %s' % (nm, got, want)
        now, now_align = metrics.character_of(bands)
        assert now == got, '%s 画像 character=%s，当前逻辑算出 %s（逻辑改了没重算）' % (
            nm, got, now)
        assert (align or []) == now_align, \
            '%s 画像里的 align_bands 与当前逻辑不一致（该重算画像）' % nm
    assert len(cases) >= 4, '样本太少(%d)' % len(cases)


@check
def t_width_exact_extremes():
    """宽度精确校准在极端目标下也要准（它会直接改写成品）"""
    sr = 22050
    t = np.arange(sr) / sr
    mid_sig = 0.3 * np.sin(2 * np.pi * 300 * t)
    side_sig = 0.15 * np.sin(2 * np.pi * 700 * t)
    wav = os.path.join(TMP, 'wide.wav')
    sf.write(wav, np.stack([mid_sig + side_sig, mid_sig - side_sig], axis=1), sr)
    before_mid = sf.read(wav, dtype='float64', always_2d=True)[0].mean(axis=1)
    for target in (0.05, 0.3, 0.6, 0.95):
        got = render_midi.set_width_exact(wav, target)
        y, sr2 = sf.read(wav, dtype='float64', always_2d=True)
        mid = y.mean(axis=1)
        side = (y[:, 0] - y[:, 1]) / 2
        ratio = float(np.sqrt((side ** 2).mean()) / np.sqrt((mid ** 2).mean()))
        assert abs(ratio - target) < 0.03, \
            '目标 %.2f 实得 %.3f' % (target, ratio)
        assert np.abs(y).max() <= 1.0, '加宽后削波'
        # mid（单声道内容）必须保持不变
        n = min(len(mid), len(before_mid))
        d = float(np.abs(mid[:n] - before_mid[:n]).max())
        assert d < 1e-3, 'mid 被改动（差异 %.4f）' % d


@check
def t_autotune_idempotent():
    """重复运行必须稳定：同一首歌连跑两次自动调参，参数与结果都不该漂移
    （漂移意味着每次重出成品都会变，是静默的不确定性）。

    收尾判据两段，**都不许静默**：
      ① 幂等：两次运行的参数漂移 ≤0.25（rms 是绝对目标值，不计入）
      ② 收敛：每组的残差 ≤2.5dB；**达不到就必须留下可审计的证据** —— 该组对应的参数
         要么顶在 `LIMITS` 边界，要么被"到顶/冻结"机制**点名**（输出里有名有姓）。
    为什么不是"一律要求 ≤2.5dB"（实测教训）：这个夹具的 1.2–5kHz 比参考薄 9.7dB，
    而 EQ 的 `mid_db` 上限 10 是**故意收得保守**的（"差距 >4dB 通常是编配缺能量"）——
    顶到 9.84 后再推反而让整条谱被峰值上限压回来（实测 −2.2 → −2.85），于是机制冻结它并报
    "已冻结: mid_db"。把这种**如实报告的到顶**判成失败，等于逼实现去假装收敛（坑 105/117 的同一课）。
    响度同理：峰值上限 0.97 挡住目标时，`render_midi.LAST` 会自报"峰值受限 + 差多少"，
    自动调参照此冻结 rms（否则每轮重设同一个值，白烧 ~30s/轮）。
    """
    if FAST:
        return
    import re
    import make_song
    d = {'name': 'idem', 'bpm': 120, 'style': 'daily',
         'chords': {'C': [36, [55, 60, 64, 67, 72]]},
         'melody': {'m': [[0, 0, 1, 72], [0, 2, 1, 76]]},
         'sections': [{'name': 'A', 'bars': 4, 'chords': ['C'] * 4, 'melody': 'm',
                       'arr': {'uku': True, 'piano': True, 'bass': True,
                               'pad': True, 'glock': True, 'perc': 1}}]}
    sp = os.path.join(TMP, 'idem.json')
    json.dump(d, open(sp, 'w', encoding='utf-8'))
    mid = os.path.join(TMP, 'idem.mid')
    quiet(song_engine.compose, sp, mid)
    ref = scorecard.load_ref('BGM16c')
    base = {'rms': -16.9, 'width': 2.0, 'shelf': 3.0, 'hp': 38.0, 'low': 0.0,
            'drive': 1.6, 'mid_db': 0.0}
    out = os.path.join(TMP, 'idem_sf')
    c1 = dict(base)
    buf1 = io.StringIO()
    with redirect_stdout(buf1):
        make_song.autotune(c1, ref, mid, out, 6)
    # **防"一律冻结"的假通过**：第一轮要么真的调过参数，要么本来就已在容差内 ——
    # 否则"什么都不做"也能让上面的两段判据全绿（检查就成了摆设）
    import metrics as _mx
    tuned = '→ 调' in buf1.getvalue()
    g0 = make_song.target_gaps(make_song.measure(out + '.wav', ref), ref)
    assert tuned or all(abs(g0[k]) <= _mx.TOL for k in ('low', 'mid_db', 'shelf')
                        if g0.get(k) is not None), \
        '第一轮既没调过任何参数、也没在容差内（"一律冻结"会让这条检查失去意义）'
    gaps1 = g0
    w1 = open(out + '.wav', 'rb').read()
    c2 = dict(c1)
    buf = io.StringIO()
    with redirect_stdout(buf):
        make_song.autotune(c2, ref, mid, out, 6)
    log2 = buf.getvalue()
    gaps2 = make_song.target_gaps(make_song.measure(out + '.wav', ref), ref)
    # ① 幂等：**顶在 `LIMITS` 边界的参数单独算**。
    # 为什么（2026-09-14 实测）：`tune_step` 的步长是"误差 × 0.8"（可达数 dB），被上限夹住时
    # 落点取决于**起点** —— 第一次从 0 出发，推到 9.6 后"再推更差"（峰值上限把整条谱压回来，
    # 见本函数 docstring）于是自适应冻结；第二次从 9.6 出发又推一步、被 `mid_db` 上限夹到 10.0。
    # 两者都在上限附近、成品差 ≤0.4dB（听不出来），**判据必须与参数的夹取粒度匹配**，
    # 否则"差一步"永远超标（坑 105 的同类：判据口径与被优化的量不一致）。非边界参数仍严判 0.25。
    edge = {k for k in c1 if k != 'rms' and k in make_song.LIMITS
            and any(min(abs(c1[k] - b), abs(c2[k] - b)) < 1e-6
                    for b in make_song.LIMITS[k])}
    drift = max(abs(c1[k] - c2[k]) for k in c1 if k != 'rms')
    loose = max((abs(c1[k] - c2[k]) for k in c1 if k != 'rms' and k not in edge),
                default=0.0)
    assert loose <= 0.25, '第二次调参把参数改了 %.2f（不幂等）: %s → %s' % (
        loose, c1, c2)
    if edge and drift > 0.25:
        print('        （%s 顶在 LIMITS 边界：%s，允许一步夹取差 %.2f）'
              % ('/'.join(sorted(edge)), ' → '.join('%.2f' % c1[k] for k in sorted(edge)),
                 drift))
    # ② 收敛 or 如实报告的"到顶/冻结"
    named = set()
    for m in re.finditer(r'(?:到顶|已冻结): ([^）\n]*)', log2):
        named |= {x.strip() for x in m.group(1).split('/') if x.strip()}
    worst = {k: g for k, g in gaps2.items()
             if k in ('low', 'mid_db', 'shelf', 'width') and abs(g) > 2.5}
    for k, g in worst.items():
        lo, hi = make_song.LIMITS[k]
        pinned = min(abs(c2[k] - lo), abs(c2[k] - hi)) < 1e-6
        assert k in named or pinned, (
            '第 %s 组差 %+.2fdB 超过 2.5dB，但既没顶到 LIMITS 边界、输出里也没有点名'
            '（"到顶/已冻结"）—— 不许把到不了静默当成达标；参数 %s=%s，输出尾部：%s'
            % (k, g, k, c2[k], log2.strip().splitlines()[-1:]))
    if worst:
        print('        （%s 到不了 2.5dB，但已如实点名：%s）'
              % ('/'.join(sorted(worst)), ', '.join(sorted(named))))
    # ③ 成品字节不应该随运行漂（幂等的物理含义）：**成品不同必须能用"参数漂了"解释** ——
    # 参数一模一样却字节不同 = 渲染里有隐藏随机性（那才是 bug）。
    same = open(out + '.wav', 'rb').read() == w1
    assert same or drift > 0, \
        '两次运行的成品不同，但参数完全没变（%s）→ 渲染里有隐藏随机性' % c1


@check
def t_analyzers_smoke():
    """所有分析/体检脚本都能跑通（重构过度量内核，别把老工具跑坏）"""
    sr = 22050
    n = sr * 8
    x = np.zeros(n, dtype=np.float32)
    for i in range(0, n - 400, int(sr * 0.5)):        # 120BPM 打点
        x[i:i + 400] += (np.hanning(400) * 0.6).astype(np.float32)
    x += (np.random.default_rng(3).standard_normal(n) * 0.01).astype(np.float32)
    wav = os.path.join(TMP, 'click.wav')
    sf.write(wav, np.stack([x, x], axis=1), sr)
    # 夹具曲目/MIDI 都改成动态挑：仓库可以只带少量示例曲（甚至不带音频）
    _fix = fixture_song()
    song = os.path.join(_fix, 'song.json') if _fix else ''
    _mids = sorted(glob.glob(os.path.join(ROOT, 'songs', '*', '*.mid')))
    mid = _mids[0] if _mids else ''
    jobs = [('analyze_ref.py', [wav]), ('analyze_ref2.py', [wav]),
            ('analyze_chords.py', [wav, '--bpm', '120']),
            ('analyze_prog.py', [wav, '--bpm', '120']),
            ('analyze_bass.py', [wav, '--bpm', '120']),
            ('probe_style.py', [wav, '--bpm', '120']),
            ('section_probe.py', [wav, '1.0']),
            ('noise_probe.py', [wav])]
    if os.path.exists(song):
        jobs.append(('arrange_probe.py', [song]))
    if os.path.exists(mid):
        jobs.append(('midi_probe.py', [mid]))
    _mids2 = sorted(glob.glob(os.path.join(ROOT, 'songs', '*', '*.mid')))
    if _mids2:
        jobs.append(('midi_ref.py', [_mids2[0]]))
    bad = []
    for script, argv in jobs:
        r = subprocess.run([sys.executable, os.path.join(HERE, script)] + argv,
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', cwd=ROOT)
        if r.returncode != 0 or not (r.stdout or '').strip():
            bad.append('%s(rc=%d) %s' % (script, r.returncode,
                                         (r.stderr or '').strip()[-80:]))
    assert not bad, '跑不通: ' + '; '.join(bad)


@check
def t_long_song_compose():
    """200 小节的长曲也要能编出来（性能与数据规模）"""
    import time as _t
    bars = 200
    chords = {'C': [36, [55, 60, 64, 67, 72]], 'G': [31, [55, 59, 62, 67, 71]],
              'Am': [33, [55, 60, 64, 69, 72]], 'F': [29, [53, 57, 60, 65, 69]]}
    prog = list(chords) * (bars // 4)
    mel = []
    for b in range(bars):
        mel.append([b, 0, 1, 72])
        mel.append([b, 2, 1, 76])
    d = {'name': 'long', 'bpm': 120, 'style': 'daily', 'chords': chords,
         'melody': {'m': mel},
         'sections': [{'name': 'A', 'bars': bars, 'chords': prog[:bars],
                       'melody': 'm',
                       'arr': {'uku': True, 'piano': True, 'bass': True,
                               'pad': True, 'strings': True, 'glock': True,
                               'arp': True, 'perc': 2}}]}
    sp = os.path.join(TMP, 'long.json')
    json.dump(d, open(sp, 'w', encoding='utf-8'))
    t0 = _t.time()
    mid = song_engine.compose(sp, os.path.join(TMP, 'long.mid'), quiet=True)
    dt = _t.time() - t0
    res = quiet(__import__('midi_probe').parse, mid, True)[0]
    assert res['note_count'] > 5000, '长曲音符太少 %d' % res['note_count']
    assert dt < 30, '编配 200 小节用了 %.1fs（过慢）' % dt


@check
def t_suggest_respects_alignment():
    """成绩单的建议不能跟自己的对标口径打架（用合成画像，不依赖参考曲文件）：
    ① 不参与对标的频段**不许出建议** —— 早期一边打印"已跳过 20-40"，
       一边建议"20-40Hz 多 29.8dB → 提高高通"（照做会让配器比所有真实 BGM 都薄）
    ② 参数已到限时**不许再给该参数的旗标** —— 成绩单跑在**成品**上，此时 EQ 已生效
       过一轮，按"从零起步"给绝对值会出现反方向建议（shelf 已 -3 下限却建议 -2.8）"""
    al = ['160-315', '315-630', '630-1250', '1250-2500', '2500-5000', '5000-10000']
    ref = {'name': 'fake', 'character': 'vocal_forward', 'align_bands': al,
           'bpm': 129.2, 'width': 0.735, 'rms_db': -22.5, 'centroid': 1552,
           'rhythm_low': '····', 'rhythm_high': '····',
           'bands': {'20-40': -68.7, '40-80': -33.1, '80-160': -8.8, '160-315': -3.8,
                     '315-630': 0.0, '630-1250': -2.4, '1250-2500': -6.7,
                     '2500-5000': -15.8, '5000-10000': -28.8, '10000-18000': -41.7}}
    mine = {'bands': {'20-40': -38.9, '40-80': -14.5, '80-160': -7.5, '160-315': -5.7,
                      '315-630': -1.1, '630-1250': 0.0, '1250-2500': -8.0,
                      '2500-5000': -15.0, '5000-10000': -24.1, '10000-18000': -33.1},
            'width': 0.736, 'rms_db': -23.2, 'bpm': 129.2, 'centroid': 1864,
            'rhythm_low': '★◇◇◇', 'rhythm_high': '★★★◇'}
    tips = scorecard.suggest(mine, ref)
    for t in tips:
        for b in ('20-40', '40-80', '80-160', '10000-18000'):
            assert b not in t, '对不参与对标的 %s 频段出了建议: %s' % (b, t)
    assert not [t for t in tips if '--hp' in t], 'sub 不参与对标却建议动 hp: %s' % tips
    # ② shelf 已到 -3 下限（高频仍偏亮 4.7dB）→ 只能给编配建议
    tips2 = scorecard.suggest(mine, ref, {'shelf': -3.0, 'low': 0.0, 'hp': 38.0})
    assert not [t for t in tips2 if '--shelf' in t], 'shelf 已到下限还建议调 shelf: %s' % tips2
    assert [t for t in tips2 if 'shelf 已到下限' in t and '编配' in t], \
        '到限了却没说清只能改编配: %s' % tips2
    # ③ shelf 未到限 → 给可粘贴旗标，且方向必须是**更暗**
    t3 = [t for t in scorecard.suggest(mine, ref, {'shelf': 0.0, 'low': 0.0, 'hp': 38.0})
          if '--shelf' in t]
    assert t3 and float(t3[0].split('--shelf')[1].split()[0]) < 0, \
        '高频偏亮却给出不变暗的 shelf: %s' % t3


@check
def t_bpm_from_midi_not_guess():
    """速度必须取自 MIDI 的 tempo 元事件（确定值），不能靠音频测速猜：
    连奏编配没有明显起音，`detect_bpm` 会误判 —— 实测 gorgeous 编配的 106BPM 样带
    被读成 154.3，而它的自相关峰值正好落在**一小节**上（即音频确实是 106BPM）。
    速度判错 → 节奏型/调式/结构全在错位的小节网格上算（早期预演小样写死 120BPM
    却按参考速度切小节，就是这个坑）。"""
    import make_song
    import midi_probe
    want = 106.0
    d = {'name': 'bpmcheck', 'bpm': want, 'style': 'gorgeous',
         'chords': {'D': [38, [57, 62, 66, 69, 74]], 'G': [31, [55, 59, 62, 67, 71]]},
         'melody': {'m': [[0, 0, 1, 74], [0, 2, 1, 78], [1, 0, 1, 74], [1, 2, 1, 78]]},
         'sections': [{'name': 'A', 'bars': 2, 'chords': ['D', 'G'], 'melody': 'm',
                       'arr': {'uku': True, 'piano': True, 'bass': True, 'pad': True,
                               'strings': True, 'glock': True, 'arp': True, 'perc': 1}}]}
    sp = os.path.join(TMP, 'bpmcheck.json')
    json.dump(d, open(sp, 'w', encoding='utf-8'), ensure_ascii=False)
    mid = song_engine.compose(sp, os.path.join(TMP, 'bpmcheck.mid'), quiet=True)
    got = midi_probe.parse(mid, quiet=True)['bpm']
    assert got and abs(got - want) < 0.01, \
        'MIDI 里写的速度是 %.3f，要求 %.1f' % (got or -1, want)
    assert abs(make_song.midi_bpm(mid) - want) < 0.01, \
        'midi_bpm 读出来是 %s' % make_song.midi_bpm(mid)
    # 传了真实速度时 measure 不该再去测速/吸附；成绩单要有显式 --bpm 开关
    src = open(os.path.join(HERE, 'make_song.py'), encoding='utf-8').read()
    assert 'def measure(wav, ref, bpm=None)' in src, 'measure 丢了 bpm 参数（会退回测速）'
    assert "'--bpm'" in open(os.path.join(HERE, 'scorecard.py'),
                             encoding='utf-8').read(), '成绩单少了 --bpm 开关'


@check
def t_console_encoding_safe():
    """换台机器/换个对话就崩的那类问题：Windows 默认控制台是 GBK，脚本打印 `✓` 会
    `UnicodeEncodeError: 'gbk' codec can't encode`，**在自动调参中途直接崩**。
    以前本地都先设了 `chcp 65001`/`PYTHONIOENCODING=utf-8`，所以一直没暴露。
    ① 起真进程（PYTHONIOENCODING=gbk）验证 cli_utf8.setup() 确实救得回来
    ② 反向对照：不加固必须崩（否则这条检查是空转）
    ③ 每个入口脚本都必须调用它"""
    env = dict(os.environ, PYTHONIOENCODING='gbk')
    code = ('import sys; sys.path.insert(0, %r); import cli_utf8 as c; c.setup();'
            'print("\\u2713 \\u2717 \\u2192")' % HERE)
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', env=env, cwd=TMP)
    assert r.returncode == 0, 'GBK 控制台下打印 ✓ 仍崩: %s' % (r.stderr or '')[-200:]
    assert '\u2713' in (r.stdout or ''), '字符没正常输出: %r' % (r.stdout or '')
    r2 = subprocess.run([sys.executable, '-c', 'print("\\u2713")'], capture_output=True,
                        text=True, encoding='utf-8', errors='replace', env=env, cwd=TMP)
    assert r2.returncode != 0, 'GBK 下不加固也不崩？这条检查是空转（没在测东西）'
    missing = []
    for p in sorted(glob.glob(os.path.join(HERE, '*.py'))):
        nm = os.path.basename(p)
        if nm == 'cli_utf8.py':
            continue
        src = open(p, encoding='utf-8').read()
        if 'if __name__' in src and '_cu.setup()' not in src:
            missing.append(nm)
    assert not missing, '这些入口脚本没做编码兜底: %s' % ', '.join(missing)


@check
def t_melody_chord_fit():
    """**旋律强拍必须落在和弦音上**（听感"搭不搭"的客观判据）。

    起因：11_dn75_neon v1 被用户评价"不好听"。解剖发现——强拍只有 **54%** 落在和弦音上
    （早期曲目是 78~87%），同一时期写的 10_b146_night_meal 也是 55%。
    也就是说：越写越自由、不再检查"这个音在这小节的和弦里成不成立"，旋律就会一直悬着、
    听感发飘发脏。**这不是口味问题，是能查出来的。**

    判据：**强拍**（位置由拍号定，`song_engine.strong_beats`：4/4 → 第 1、3 拍；
    3/4 → 只有第 1 拍；6/8 → 第 1 拍与第 4 个八分）和弦音占比 ≥ 70%；
    根音上方半音（♭9，最刺耳）≤ 2 处。弱拍不做限制 —— 经过音/倚音本来就该在弱拍。"""
    rows = []
    for d in songs_or_fail():
        name = os.path.basename(d)
        data = song_engine.load(os.path.join(d, 'song.json'))
        chords = data['chords']
        strong = song_engine.strong_beats(data.get('meter'))
        tot = fit = b9 = 0
        for sec in data['sections']:
            mel = data['melody'].get(sec['melody'], []) + (sec.get('melody_extra') or [])
            for (b, beat, _dur, m) in mel:
                if beat not in strong or b >= len(sec['chords']):
                    continue
                cname = sec['chords'][b]
                tones = [t % 12 for t in chords[cname][1]]
                root = chords[cname][0] % 12
                tot += 1
                if m % 12 in tones:
                    fit += 1
                elif (m - root) % 12 == 1:
                    b9 += 1
        assert tot >= 8, '%s: 强拍样本太少(%d)，这条检查会空转' % (name, tot)
        rows.append((name, tot, 100.0 * fit / tot, b9))
    for n, t, r, c in rows:
        print('        %-20s 强拍%3d 个：弦内音 %3.0f%%  ♭9 冲突 %d' % (n, t, r, c))
    bad = ['%s 只 %.0f%%' % (n, r) for n, _t, r, _c in rows if r < 70.0]
    assert not bad, ('旋律强拍没落在和弦音上（经过音该放弱拍）: ' + ', '.join(bad)
                     + ' —— 改 song.json 的 melody：每小节强拍用该小节和弦的音')
    bad9 = ['%s %d 处' % (n, c) for n, _t, _r, c in rows if c > 2]
    assert not bad9, '强拍出现根音上方半音（♭9，最刺耳的不协和）: ' + ', '.join(bad9)


@check
def t_pump_groove():
    """新增的两套律动必须真的照参考曲反推出来（防以后改引擎改跑）：
    · `bass_style: pump16` → 贝斯落点**全在每拍的 e/a 两个十六分**上
      （参考曲低频型 `◇★◇★◇★◇★·★◇★◇★◇★`，正拍留空给鼓）
    · `perc_style: pump` → 八分踩镲 + 军鼓 2/4 + 底鼓只踩"a" + **每 4 小节十六分过门**"""
    d = {'name': 'pg', 'bpm': 75, 'style': 'daily',
         'chords': {'Gm': [31, [43, 46, 50, 55, 58]], 'Eb': [39, [51, 55, 58, 63, 67]]},
         'melody': {'m': [[0, 0, 2, 74], [0, 2, 2, 79]]},
         'sections': [{'name': 'A', 'bars': 8,
                       'chords': ['Gm', 'Gm', 'Eb', 'Eb', 'Gm', 'Gm', 'Eb', 'Eb'],
                       'melody': 'm',
                       'arr': {'bass': True, 'perc': 3, 'piano': True}}],
         'patterns': {'bass_style': 'pump16', 'perc_style': 'pump', 'sub_gain': 0.15}}
    sp = os.path.join(TMP, 'pump.json')
    json.dump(d, open(sp, 'w', encoding='utf-8'))
    ev, _nb = build(quiet(song_engine.load, sp)[0])

    def slots(track, pred=None):
        out = set()
        for (t, _dd, m, _v) in ev[track]:
            if pred and not pred(m):
                continue
            out.add(int(round((t % 4.0) * 4)) % 16)
        return out

    # ① 位置：每拍的 e/a 两个十六分**都要有音**（反拍推动的骨架）
    off_slots = {1, 3, 5, 7, 9, 11, 13, 15}
    bslots = {int(round((t % 4.0) * 4)) % 16 for (t, _d, _m, _v) in ev['Bass']}
    assert off_slots <= bslots, \
        'pump16 的贝斯应覆盖每拍 e/a 两个十六分，实得 %s' % sorted(bslots)
    # ② 动态：例曲 bass 动态 48~52dB（有颗粒有起伏）→ 力度必须拉开，
    #    且**正拍不能是重音**（正拍留给鼓）。以前用"力度≥90 才算重音"判，太死板：
    #    逐声部实测后的设计是"反拍为主 + 力度起伏"，就该按"位置 + 动态"验。
    accent = [v for (t, _d, _m, v) in ev['Bass']
              if int(round((t % 4.0) * 4)) % 16 in off_slots]
    downbeat = [v for (t, _d, _m, v) in ev['Bass']
                if int(round((t % 4.0) * 4)) % 16 in (0, 4, 8, 12)]
    assert max(accent) - min(accent) >= 20, \
        'pump16 的贝斯力度太死板（动态只有 %d）：例曲 bass 动态 48~52dB' % (
            max(accent) - min(accent))
    assert downbeat and max(downbeat) < max(accent), \
        '正拍不该是重音（正拍留给鼓）：%s vs 反拍 %s' % (sorted(downbeat), sorted(accent))
    hats = slots('Perc', lambda m: m == 42)
    assert {0, 2, 4, 6, 8, 10, 12, 14} <= hats, 'pump 应有八分踩镲，实得 %s' % sorted(hats)
    snare = slots('Perc', lambda m: m == 38)
    assert {4, 12} <= snare, 'pump 的军鼓应在 2、4 拍，实得 %s' % sorted(snare)
    kick = slots('Perc', lambda m: m == 36)
    assert {3, 7, 11, 15} <= kick, 'pump 的底鼓应踩每拍"a"，实得 %s' % sorted(kick)
    toms = [t for (t, _d, m, _v) in ev['Perc'] if m in (45, 47, 48, 50)]
    assert toms, 'pump 缺少过门鼓（45/47/48/50）'
    fill_bar = [t for t in toms if 15.0 <= (t % 16.0) < 16.0]
    assert len(fill_bar) >= 3, '过门应落在每 4 小节乐句的末小节，实得 %d 个' % len(fill_bar)


@check
def t_waltz_groove():
    """3/4 要的是**地道华尔兹**，不是"把 4/4 的落点按拍缩放一遍"（本轮实测踩到）：

    · `bass_style: waltz` → 贝斯只踩**第 1 拍**（"oom"）—— 不再是"第 1 拍 + 第 3 拍"那种缩放结果
    · 钢琴/电钢的奇数拍分支 → 和弦落在**第 2、3 拍**（"pah-pah"）
    · `perc_style: waltz` → 底鼓只踩第 1 拍，侧棒点第 2、3 拍，八分沙锤铺连续性
    · 全部落在**一小节内**（3 拍）
    """
    d = {'name': 'wz', 'bpm': 150, 'meter': [3, 4], 'style': 'ballad',
         'chords': {'Dm': [38, [57, 62, 65, 69, 74]]},
         'melody': {'m': [[0, 0, 1, 74]]},
         'sections': [{'name': 'A', 'bars': 1, 'chords': ['Dm'], 'melody': 'm',
                       'arr': {'bass': True, 'piano': True, 'perc': 2}}],
         'patterns': {'bass_style': 'waltz', 'perc_style': 'waltz'}}
    sp = os.path.join(TMP, 'waltz.json')
    json.dump(d, open(sp, 'w', encoding='utf-8'))
    ev, _nb = build(quiet(song_engine.load, sp)[0])

    def pos(track, pred=None):
        return sorted({round(t % 3.0, 2) for (t, _dd, m, _v) in ev.get(track, [])
                       if pred is None or pred(m)})

    assert pos('Bass') == [0.0, 2.0], \
        'waltz 贝斯应只踩第 1 拍（+ 第 3 拍轻五度），实得 %s' % pos('Bass')
    assert pos('Piano') == [1.0, 2.0], \
        'waltz 和弦应在第 2、3 拍（pah-pah），实得 %s' % pos('Piano')
    assert pos('Perc', lambda m: m == 36) == [0.0], \
        'waltz 底鼓只踩第 1 拍，实得 %s' % pos('Perc', lambda m: m == 36)
    assert pos('Perc', lambda m: m == 37) == [1.0, 2.0], \
        'waltz 侧棒应在第 2、3 拍，实得 %s' % pos('Perc', lambda m: m == 37)


@check
def t_render_duration_matches_midi():
    """成品 WAV 的时长必须 ≈ MIDI 时长 + 混响尾巴（允许多 10 秒）。

    教训：air 垫层用 16 分网格但时值 0.4 拍（> 0.25 格距 = 同音高重叠）时，
    FluidSynth 会把 note-off 配到错的 voice 上，留下**永不关闭的悬空 voice**
    （镲片采样带 loop → 一直响），于是 4:42 的歌渲染成 5:03 ——**多出来的 21 秒
    是真声音**，而当时没有任何检查抱怨（"静默出错"这一类）。"""
    import glob as _glob
    import layer_exp as LE
    bad = []
    for mid in sorted(_glob.glob(os.path.join(ROOT, 'songs', '*', '*.mid'))):
        wav = os.path.splitext(mid)[0] + '_sf.wav'
        if not os.path.exists(wav):
            continue
        div, ev, tempos = LE.parse_smf(mid)
        if not ev:
            continue
        us = tempos[0][1] if tempos else 500000
        mid_s = max(t for (t, _s, _a, _b) in ev) / div * (us / 1e6)
        dur = sf.info(wav).duration
        if dur - mid_s > 10.0 or mid_s - dur > 2.0:
            bad.append('%s: MIDI %.1fs / 成品 %.1fs' % (os.path.basename(mid), mid_s, dur))
    assert not bad, '成品时长与 MIDI 不符（渲染多了或少了内容）: ' + '; '.join(bad)


@check
def t_perc_layers():
    """打击垫层（`patterns.perc_layers`，opt-in）：用来补**时间连续性**而不是能量。
    ① 不配 layers 时不能凭空多出垫层音（opt-in 纪律：老歌必须逐字节不变）
    ② kick 垫层必须与底鼓**逐点对齐**（错位就变成"两个鼓在打架"，听感立刻垮）
    ③ air 垫层必须铺满 **16 个十六分格**（连续性取决于"采样长度 vs 网格间隔"：
       实测每十六分 → 例曲级 5000Hz 占用率 100%，改成每八分直接掉到 84%）"""
    d = {'name': 'pl', 'bpm': 75, 'style': 'daily',
         'chords': {'Gm': [31, [43, 46, 50, 55, 58]]},
         'melody': {'m': [[0, 0, 2, 74], [0, 2, 2, 79]]},
         'sections': [{'name': 'A', 'bars': 4, 'chords': ['Gm'] * 4, 'melody': 'm',
                       'arr': {'bass': True, 'perc': 3, 'piano': True}}],
         'patterns': {'bass_style': 'pump16', 'perc_style': 'pump', 'sub_gain': 0.15}}
    sp0 = os.path.join(TMP, 'pl0.json')
    json.dump(d, open(sp0, 'w', encoding='utf-8'))
    ev0, _nb = build(quiet(song_engine.load, sp0)[0])
    stray = [m for (_t, _d, m, _v) in ev0['Perc'] if m in (41, 43, 44, 69)]
    assert not stray, '没配 perc_layers 时不该有垫层音，实得 %s' % sorted(set(stray))
    d['patterns']['perc_layers'] = {'kick': [[41, 66, 0.7], [43, 72, 0.7]],
                                    'air': [[44, 40, 0.4], [69, 46, 0.4]]}
    sp1 = os.path.join(TMP, 'pl1.json')
    json.dump(d, open(sp1, 'w', encoding='utf-8'))
    ev, _nb = build(quiet(song_engine.load, sp1)[0])

    def times(note):
        return sorted(round(t, 4) for (t, _d, m, _v) in ev['Perc'] if m == note)
    kick = times(36)
    assert kick, 'pump 应该有底鼓'
    for note in (41, 43):
        assert times(note) == kick, \
            'note %d 垫层必须与底鼓逐点对齐（%d vs %d 个点）' % (note, len(times(note)),
                                                                 len(kick))
    # air：十六分网格被多个音色**交替**铺满（用 44/69 做探针：基础 pump 不用这两个音）
    # ⚠ 解包时别用 `d` 当变量名 —— 外层 `d` 是歌曲数据，被遮蔽后下面就 "float 不可下标"
    air = {}
    for (t, dur, m, _v) in ev['Perc']:
        if m in (44, 69):
            air.setdefault(m, []).append((t % 4.0, dur))
    assert set(air) == {44, 69}, 'air 两个条目都应该出现，实得 %s' % sorted(air)
    slots = set()
    for m, items in air.items():
        slots |= {int(round(bt * 4)) % 16 for (bt, _d) in items}
    assert slots == set(range(16)), 'air 合起来要铺满 16 个十六分格，实得 %s' % sorted(slots)
    # **同一个音高的相邻音不得重叠**这条**不再是硬约束**：实测重叠（0.4 拍 > 0.25 格距）
    # 并不影响听感，只是让 FluidSynth 多渲染 15.7 秒的 −72dBFS 死气 —— 那由
    # `render_midi.trim_tail()` 收尾（自检 `trim_tail` + `no_long_silent_tail` 守着）。
    # 这里只验"交替铺满"：n 个条目按 k % n == si 占格，合起来 16 格不缺。
    expect = {44: {0, 2, 4, 6, 8, 10, 12, 14}, 69: {1, 3, 5, 7, 9, 11, 13, 15}}
    for m, ks in expect.items():
        got = sorted({int(round(bt * 4)) % 16 for (bt, _d) in air[m]})
        assert got == sorted(ks), 'note %d 的 air 应占 %s 格，实得 %s' % (m, sorted(ks), got)

    # `kick_pos: "offbeat"`：**整套低频骨架**（底鼓 + 垫层）改落每拍的 e/a
    # —— 例曲低频律动 `◇★◇★◇★◇★` 的反拍推动；正拍只留"弱格"
    d['patterns']['perc_layers']['kick_pos'] = 'offbeat'
    sp2 = os.path.join(TMP, 'pl2.json')
    json.dump(d, open(sp2, 'w', encoding='utf-8'))
    ev2, _nb = build(quiet(song_engine.load, sp2)[0])
    want = {0.25, 0.75, 1.25, 1.75, 2.25, 2.75, 3.25, 3.75}
    for note in (36, 41):
        got = {round(t % 4.0, 2) for (t, _dur, m, _v) in ev2['Perc'] if m == note}
        assert want <= got, 'offbeat 模式下 note %d 必须落每拍 e/a，缺 %s' % (
            note, sorted(want - got))
    # 垫层**不能**跟到正拍（正拍那个弱底鼓不该被垫厚，否则 ◇ 格又变 ★）
    got41 = {round(t % 4.0, 2) for (t, _dur, m, _v) in ev2['Perc'] if m == 41}
    assert 0.0 not in got41, 'offbeat 模式的垫层不该落在正拍，实得 %s' % sorted(got41)


@check
def t_trim_tail():
    """去尾 `render_midi.trim_tail`：切掉**过长的**尾部死气，但不能碰正常尾巴。
    （背景：FluidSynth 会渲染到所有 voice 停止，重叠镲会让 4:42 的歌多出 15.7 秒
    的 −72dBFS 死气；而既有 13 首歌的正常混响尾巴只有 2.0~3.7 秒。）"""
    sr = 44100
    # ① 长死气（末尾 8 秒静音）必须被裁到"最后一声 + 1 秒"
    x = np.zeros((sr * 12, 2))
    x[:sr * 2] = 0.5                          # 前 2 秒内容（−6dB）
    x[sr * 3] = 0.002                         # 第 3 秒一个 −54dB 的微弱残留
    y = render_midi.trim_tail(x, sr)
    assert 3.9 * sr <= len(y) <= int(4.1 * sr), \
        '长死气应裁到约 4.0 秒（最后有声 + 1 秒），实得 %.2f 秒' % (len(y) / sr)
    # ② 正常尾巴（末尾静音 2 秒）必须**原样返回**（否则既有交付物与小样带会漂）
    z = np.zeros((sr * 4, 2))
    z[:sr * 2] = 0.5
    assert len(render_midi.trim_tail(z, sr)) == len(z), '正常尾巴不该被动'
    # ③ 全静音输入不能崩、不能切没
    w = np.zeros((sr * 3, 2))
    assert len(render_midi.trim_tail(w, sr)) == len(w), '全静音输入不该被切'


@check
def t_no_long_silent_tail():
    """所有成品 WAV 的末尾不得有 **> 6 秒**的静音（−60dB 以下）。
    这一条是"渲染层留下死气"的兜底：实测既有 13 首歌的正常混响尾巴是 2.0~3.7 秒，
    而重叠 air 触发的死气是 **15.7 秒**（−72dBFS，听不见但真实存在）。"""
    import glob as _glob
    bad = []
    for w in sorted(_glob.glob(os.path.join(ROOT, 'songs', '*', '*_sf.wav'))):
        info = sf.info(w)
        if info.duration < 5:
            continue
        x, sr = sf.read(w, always_2d=True)
        m = np.abs(x).max(axis=1)
        nz = np.where(m > 1e-3)[0]
        if not nz.size:
            continue
        tail = (len(x) - nz[-1]) / sr
        if tail > 6.0:
            bad.append('%s 尾巴 %.1fs 静音' % (os.path.basename(w), tail))
    assert not bad, '成品末尾有死气（去尾没生效？）: ' + '; '.join(bad)


@check
def t_harmony_layer():
    """副旋律层（`arr.harmony`）：给旋律配的音必须是**该小节和弦内的低三度**
    （3~6 半音之下、且是和弦音）——乱配三度会直接毁掉协和度。"""
    d = {'name': 'hm', 'bpm': 120, 'style': 'daily',
         'chords': {'C': [36, [48, 52, 55, 60, 64]]},          # C 大三和弦
         # ⚠ 旋律音必须落在"和弦音上方 3~6 半音"，否则 `harmony_below` 返回 None、
         # **根本不产副旋律**（原夹具是 76/79，而和弦最高才 64 —— 相差一个八度）。
         # 旧断言 `assert harm` 之所以过，是因为 Hook 的普通伴奏音恰好撞上了
         # `[t-3 for t in tones]` 里的 52（**假通过**）。现在改成端到端：期望的音必须
         # 真的出现在 Strings 轨里。
         'melody': {'m': [[0, 0, 2, 64], [0, 2, 2, 59]]},     # E4 / B3
         'sections': [{'name': 'A', 'bars': 2, 'chords': ['C', 'C'], 'melody': 'm',
                       'arr': {'piano': True, 'strings': True, 'harmony': True}}]}
    sp = os.path.join(TMP, 'harm.json')
    json.dump(d, open(sp, 'w', encoding='utf-8'))
    tones = [48, 52, 55, 60, 64]
    # ① **机制级**（不受音区分工影响）：`harmony_below` 给出"和弦内的低三度"
    for m in (64, 59):
        hm = song_engine.harmony_below(tones, m)
        assert hm is not None, 'harmony_below(%s, %d) 返回 None' % (tones, m)
        assert hm % 12 in [x % 12 for x in tones], \
            '副旋律音 %d 不是和弦音（会不协和）' % hm
        assert 3 <= m - hm <= 6, '副旋律音 %d 不在旋律 %d 下方 3~6 半音' % (hm, m)
    # ② **端到端**：`arr.harmony` 真的把那两个音写进了 Strings 轨（配置写了要生效）
    ev, _nb = build(quiet(song_engine.load, sp)[0])
    exp = {song_engine.harmony_below(tones, m) + song_engine.TR_SHIFT.get('Strings', 0)
           for m in (64, 59)}
    got = {m for (_t, _d, m, _v) in (ev.get('Strings') or [])}
    assert exp & got, \
        ('harmony 层没有写进 Strings 轨（期望含 %s，实际 %s）—— 配置写了没生效'
         % (sorted(exp), sorted(got)[:8]))


@check
def t_section_mix_automation():
    """段落级混音自动化：`sections[i].arr.mix={"Strings":80}` 必须在**该段起点**
    写出 CC7（这是做"起伏"最直接的手段）。跑真写入 → 用 midi_probe 读回验证。"""
    import midi_probe
    d = {'name': 'mx', 'bpm': 120, 'style': 'daily',
         'chords': {'C': [36, [48, 52, 55, 60, 64]]},
         'melody': {'m': [[0, 0, 2, 72]]},
         'sections': [
             {'name': 'A', 'bars': 4, 'chords': ['C'] * 4, 'melody': 'm',
              'arr': {'piano': True, 'strings': True, 'mix': {'Strings': 90}}},
             {'name': 'B', 'bars': 4, 'chords': ['C'] * 4, 'melody': 'm',
              'arr': {'piano': True, 'strings': True, 'mix': {'Strings': 40}}}]}
    sp = os.path.join(TMP, 'mx.json')
    json.dump(d, open(sp, 'w', encoding='utf-8'))
    mid = song_engine.compose(sp, os.path.join(TMP, 'mx.mid'), quiet=True)
    tr = [t for t in midi_probe.parse(mid, quiet=True)['tracks'] if t['name'] == 'Strings']
    assert tr, '没写出 Strings 轨'
    cc7 = [(tk, v) for (tk, cc, v) in tr[0]['ccs'] if cc == 7]
    assert len(cc7) >= 3, 'CC7 自动化没写进 MIDI：%s' % cc7
    # B 段起点 = 第 4 小节 = tick 4*4*480 = 7680，值应为 40
    at_b = [v for (tk, v) in cc7 if tk == 7680]
    assert at_b and at_b[0] == 40, 'B 段起点的 CC7 应为 40，实得 %s（全部 CC7: %s）' % (at_b, cc7)


@check
def t_checks_have_assertions():
    """每条自检都必须至少有一个断言（`assert` 或 `raise AssertionError`）。

    踩过：`unused_chords_warn` 只打印、从不 FAIL —— 装饰性绿灯，比没有检查更糟
    （它让"55 项通过"这个数字变成假话）。变异测试只能覆盖**已存在**的检查，
    新写出来的空转检查得靠这条静态守卫拦住。"""
    import ast
    tree = ast.parse(open(__file__, encoding='utf-8').read())
    bad = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith('t_'):
            continue
        has = any(isinstance(n, ast.Assert) for n in ast.walk(node)) or any(
            isinstance(n, ast.Raise) and n.exc is not None
            and 'AssertionError' in ast.dump(n.exc) for n in ast.walk(node))
        if not has:
            bad.append(node.name)
    assert not bad, '这些检查没有任何断言（永远不会 FAIL，是装饰性绿灯）: %s' % ', '.join(bad)


@check
def t_song_json_canonical():
    """song.json 必须是**规范格式**（`json_io` 的按小节分行）且数据自洽。
    踩过：`json.dump(..., indent=1)` 把每个音符拆成一行 → 一首 40 小节的歌 868 行、
    读一遍 ≈2k token，而写歌流程里"读一遍 song.json"是最普通的一步。
    这条同时守住"某个工具又把文件写胖"（重新格式化后必须与原文逐字节相同）。"""
    import json_io
    bad_fmt, bad_eq = [], []
    for p in sorted(glob.glob(os.path.join(ROOT, 'songs', '*', 'song.json'))):
        raw = open(p, encoding='utf-8').read()
        data = json.loads(raw)
        canon = json_io.dumps(data)
        if json.loads(canon) != data:
            bad_eq.append(os.path.basename(os.path.dirname(p)))
        elif canon != raw:
            bad_fmt.append('%s(%d 行 → 应 %d 行)'
                           % (os.path.basename(os.path.dirname(p)),
                              raw.count('\n') + 1, canon.count('\n') + 1))
    assert not bad_eq, '规范化后数据不等价（绝不能为了排版动数据）: %s' % bad_eq
    assert not bad_fmt, ('这些 song.json 不是规范格式（重跑 `scripts\\json_io.py`）: %s'
                         % ', '.join(bad_fmt))


@check
def t_docs_budget_and_skill_intact():
    """文档预算 + 技能文件完整性（"写歌为什么变贵"要有守卫，不能靠自觉）：
    ① SKILL.md 是**每个音乐任务都加载**的，超预算就会让每次写歌都变贵；
    ② SKILL.md 开头必须有 YAML frontmatter（name/description）——
       踩过：整篇重写技能时把 frontmatter 覆盖掉，技能**直接从可用列表消失**。"""
    import token_audit
    host_missing = []
    for label, p in token_audit.DOCS.items():
        if not os.path.exists(p):
            # 宿主级（技能）文档缺失 ≠ 本仓库坏了：换机器/换用户时它本就不在，
            # 记下来提示即可（想强制要求存在 → 环境变量 DSH_REQUIRE_SKILL=1）
            if label in getattr(token_audit, 'HOST_LEVEL', ()):
                host_missing.append(label)
            continue
        tok = token_audit.est(open(p, encoding='utf-8').read())
        lim = token_audit.LIMITS.get(label)
        if lim and tok > lim:
            raise AssertionError('%s ≈%d tok，超预算 %d（该拆文档/改格式了）'
                                 % (label, tok, lim))
    if host_missing:
        print('        （宿主级文档未提供，跳过其校验: %s；本机用 DSH_REQUIRE_SKILL=1 强制要求）'
              % ', '.join(host_missing))
    sk = token_audit.DOCS['SKILL.md（音乐任务加载）']
    if not os.path.exists(sk):
        if os.environ.get('DSH_REQUIRE_SKILL'):
            raise AssertionError('要求技能文件存在（DSH_REQUIRE_SKILL=1）但没有: %s' % sk)
        return
    head = open(sk, encoding='utf-8').read()[:400]
    assert head.startswith('---'), 'SKILL.md 丢了 frontmatter 开头（技能会被加载器忽略）'
    for field in ('name:', 'description:'):
        assert field in head, 'SKILL.md frontmatter 缺 %s' % field
    assert 'name: bgm-studio' in head, 'SKILL.md 的 name 必须与目录名一致'


@check
def t_read_audio_format_fallback():
    """音频读取要能吃**多种容器**（用户要求"能识别大部分音乐格式"）。

    libsndfile 只认 26 种容器，m4a/mp4/aac/wma/ape/视频容器都开不了 → `metrics.read_audio`
    在它失败时用本机 ffmpeg 转 WAV 兜底。这条检查**真的造一个 m4a** 再读回来，
    而不是只断言"函数存在"（否则就是装饰性检查）。没有 ffmpeg 时跳过并说明。"""
    import subprocess
    exe = metrics._ffmpeg_exe()
    if exe is None:
        print('        （本机没有 ffmpeg，跳过 m4a 兜底验证）')
        return
    sr = 8000
    t = np.arange(sr * 2, dtype=np.float32) / sr
    tone = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
    wav = os.path.join(TMP, 'fmt.wav')
    m4a = os.path.join(TMP, 'fmt.m4a')
    sf.write(wav, np.stack([tone, tone], axis=1), sr)
    r = subprocess.run([exe, '-y', '-hide_banner', '-loglevel', 'error', '-i', wav,
                        '-c:a', 'aac', '-b:a', '96k', m4a], capture_output=True)
    assert r.returncode == 0 and os.path.exists(m4a), '造 m4a 失败（ffmpeg 有问题）'
    try:
        sf.read(m4a)
        print('        （本机 libsndfile 已能读 m4a，兜底路径无从验证）')
        return
    except Exception:
        pass                                    # 预期：libsndfile 读不了 → 才走兜底
    x, got_sr = metrics.read_audio(m4a, dtype='float32')
    assert abs(got_sr - sr) < 1, 'm4a 采样率不对: %s' % got_sr
    assert x.shape[0] > 0.5 * sr * 2, 'm4a 读出来太短: %s' % (x.shape,)
    assert np.abs(x).max() > 0.05, 'm4a 读出来是静音（兜底路径坏了）'
    eng, _esr, ech, _edur, _efmt = metrics.probe_format(m4a)
    assert eng == 'ffmpeg', 'ffmpeg 容器的引擎判定应为 ffmpeg，得到 %s' % eng
    assert ech == 2, '声道数判定错: %s' % ech


@check
def t_check_song_sandbox():
    """`check_song.py` 的"单曲沙箱"机制必须真的把判据限制在这一首曲上。

    它把 selftest 的**全部**检查项跑在 `songs/_lint_<曲>` 上（真曲目用符号链接），所以：
    ① 必须是**真跑**（不是返回空列表）；② 沙箱必须清理干净（不留垃圾目录）；
    ③ `st.ROOT` 要能还原（否则污染同进程后续检查）。"""
    import check_song as cs
    import selftest as _st
    # 夹具动态挑（不再绑死 16_d150_bright_day）：下面两条断言要求"和弦音集自洽 + 强拍全在
    # 弦内音"，所以只挑**确实满足**的曲目 —— 旧库普遍有 7~15 处强拍经过音，
    # 随便挑一首会让内容断言误报成"检查坏了"
    _fix = fixture_song(need='strict_clean')
    if not _fix:
        raise AssertionError('songs/ 里没有"强拍全在弦内音"的曲目可当夹具：'
                             '放一首示例曲，或用 build_song 从 spec 生成一首')
    name = os.path.basename(_fix)
    real_root = _st.ROOT
    tmp = cs.lint_dirs([name])
    try:
        assert os.path.isdir(os.path.join(tmp, 'songs', name)), '沙箱没建出来'
        res, _fails = cs.run_checks_on(tmp, only=cs.DATA_CHECKS)
        expect = len([f for f in _st.CHECKS
                      if f.__name__[2:] in cs.DATA_CHECKS])
        assert len(res) == expect, \
            '只跑了 %d 项，应跑 %d 项（漏跑=假通过）' % (len(res), expect)
        got = dict(res)
        assert got.get('chord_names_match_notes') is None, \
            '%s 和弦音集应自洽: %s' % (name, got.get('chord_names_match_notes'))
        assert got.get('strict_downbeats') is None, \
            '%s 强拍应全在弦内音上: %s' % (name, got.get('strict_downbeats'))
    finally:
        cs.cleanup(tmp)
    assert _st.ROOT == real_root, 'ROOT 没还原（会污染后续检查）'
    assert not os.path.exists(tmp), '沙箱目录没清理'




@check
def t_skill_routes_resolve():
    """技能卡顶部"要什么 → 读哪份"路由表里指的每份文档都必须真实存在。

    为什么单独一条：路由表是**每次音乐任务都加载**的入口，一旦有人改了文档名而没改它，
    表现就是"agent 读不到那份文档、只好整篇读 README"（写歌重新变贵），而且**静默发生**。
    """
    import re as _re
    sk = os.path.join(os.path.expanduser('~'), '.dsh', 'skills', 'bgm-studio', 'SKILL.md')
    if not os.path.exists(sk):
        if os.environ.get('DSH_REQUIRE_SKILL'):
            raise AssertionError('要求技能文件存在（DSH_REQUIRE_SKILL=1）但没有: %s' % sk)
        print('        （技能文件由宿主提供，本机没有 → 跳过路由表校验）')
        return
    txt = open(sk, encoding='utf-8').read()
    assert '要什么 → 读哪份' in txt, '路由表被删了（技能卡失去"读哪份"的入口）'
    # 只看路由表那一段，避免把正文里的散文引用也算进来
    i = txt.index('要什么 → 读哪份')
    j = txt.find('\n## ', i)
    seg = txt[i:j if j > i else len(txt)]
    targets = set(_re.findall(r'`([^`]*\.md)`', seg))
    assert targets, '路由表里没解析出任何文档路径（表结构被改坏了？）'
    miss = [t for t in sorted(targets) if not os.path.exists(os.path.join(ROOT, t))]
    assert not miss, '路由表指向不存在的文档: %s' % ', '.join(miss)


@check
def t_docs_host_classification():
    """`token_audit.HOST_LEVEL` 的分类必须与**它指向的路径**自洽。

    为什么要这条：宿主级/仓库级的区分决定"缺失时是 FAIL 还是跳过"。一旦把
    仓库文件误标成宿主级，它缺失时就**静默跳过**（守卫形同不存在）；反过来把用户
    目录的文件标成仓库级，换机器就崩。实测就标错过一次（AGENTS.md 被算成仓库级）。
    """
    import token_audit
    home = os.path.expanduser('~')
    bad = []
    for label, p in token_audit.DOCS.items():
        in_repo = os.path.abspath(p).startswith(os.path.abspath(ROOT))
        host = label in getattr(token_audit, 'HOST_LEVEL', ())
        if in_repo and host:
            bad.append('%s 在仓库内却被标成宿主级（缺失会被静默跳过）' % label)
        if (not in_repo) and (not host):
            bad.append('%s 在仓库外却没标宿主级（换机器会崩）' % label)
    assert not bad, '文档分类与路径不自洽: ' + '; '.join(bad)


@check
def t_build_song_spec_roundtrip():
    """`build_song` 的 spec→song.json 推导必须**真能跑通**，且**往返保真**。

    这条检查的意义：spec 是"省字"的入口，一旦推导规则坏了（时值列错、和弦排列音级不符、
    旋律丢失），生成出来的歌会静默变样。所以这里做真往返：拿一首现成曲目导出 spec、
    再生成，要求**旋律逐音一致 + 强拍严判据 0 条**。
    """
    import build_song as bs
    import check_song as cs
    # 夹具动态挑：优先带 `spec.json` 的曲目（这类曲目 spec→song.json 往返确定可行），
    # 不再绑死 17_b73_slow_evening
    _fix = fixture_song(need='with_spec')
    if not _fix:
        raise AssertionError('songs/ 里没有带 spec.json 的曲目可当夹具（这条检查会空转）；'
                             '放一首由 spec 生成的曲目即可')
    src = os.path.join(_fix, 'song.json')
    song = json.load(open(src, encoding='utf-8'))
    spec = bs.to_spec(song)
    again = bs.build(spec)
    miss = [k for k in song['melody'] if k not in again['melody']]
    assert not miss, '往返丢了旋律: %s' % miss
    for k, v in song['melody'].items():
        assert again['melody'][k] == v, '旋律 %s 往返不一致' % k
    assert len(again['sections']) == len(song['sections']), '往返段落数不一致'
    for n, (a, b) in enumerate(zip(song['sections'], again['sections'])):
        assert a['chords'] == b['chords'], '第 %d 段和弦走向不一致' % (n + 1)
    # 和弦表：用到的都在，且音级与符号自洽
    for cname, (bass, tones) in again['chords'].items():
        r, slash, want = parse_chord(cname)      # 本模块内的唯一口径
        assert want is not None, '生成的和弦符号解析不了: %s' % cname
        assert set(t % 12 for t in tones) == want, '%s 排列音级不符' % cname


@check
def t_melody_profile_tonic_hint():
    """`melody_profile` 在"调内率低"时必须能区分**扒错了**与**主音给错了**。

    实测教训：BGM16c 用 `--tonic C#` 得 9.6%（看着像扒错），换 F 得 90.4%（其实没扒错）——
    我因此白判了一次"旋律扒取失败"。判据的关键：**不能只取调内率最高的主音**
    （那组音级被多个调 100% 覆盖），必须与和声分析（`refs/*.json` 的 `quiet_chroma`）对齐。
    这里用构造数据验证判据，并**把"哪些主音会并列"这一事实钉住**（别再用我手算的结论）。
    """
    import melody_profile as mp
    NAMES, MAJOR = mp.NAMES, mp.MAJOR
    notes = [(0.0, 0.5, 65), (1.0, 0.5, 67), (2.0, 0.5, 69),
             (3.0, 0.5, 70), (4.0, 0.5, 72)]           # 音级 F G A A# C

    def pct_for(tonic_name):
        i = NAMES.index(tonic_name)
        hit = sum(1 for (_a, _b, p) in notes if ((p - i) % 12) in MAJOR)
        return round(100.0 * hit / len(notes), 1)

    # ① 用错的主音 → 低到触发"扒错"警告
    assert pct_for('C#') < 80.0, 'C# 作主音时应判低（实测 %s%%）' % pct_for('C#')
    # ② 用对的主音 → 达标
    assert pct_for('F') >= 80.0, 'F 作主音时应达标（实测 %s%%）' % pct_for('F')
    # ③ **并列事实**：这组音级被多个大调 100% 覆盖 → "最高分"不足以定主音
    tops = [n for n in NAMES if pct_for(n) == 100.0]
    assert len(tops) >= 2, '这组音级应被多个主音 100%% 覆盖（实际 %s）→ 需与和声对齐' % tops
    assert 'F' in tops, 'F 应在 100%% 候选里（实际 %s）' % tops
    # ④ 与和声对齐这一步必须存在：源码里要能读到 quiet_chroma 的引用
    # **断言要咬住逻辑、不能只查子串**：第一版写 `'quiet_chroma' in src`，
    # 结果把变量名改成 quiet_chroma_XXX 仍能通过（子串还在）——典型的装饰性检查。
    mp_src = open(os.path.join(HERE, 'melody_profile.py'), encoding='utf-8').read()
    assert "rp.get('quiet_chroma')" in mp_src, \
        'melody_profile 丢了"读取和声画像的 quiet_chroma"这一步（会给出错主音建议）'
    assert 'ref_pc' in mp_src and 'NAMES.index(tn) == ref_pc' in mp_src, \
        'melody_profile 丢了"候选主音必须与和声分析一致"的判据'



@check
def t_track_ranges_musical():
    """每轨音域必须落在**乐器合理区间**内（不只是 MIDI 0~127 合法）。

    为什么需要（实测踩过）：我把和弦整体移低 12 半音时**误把低音多移了一次** →
    Bass 落到 **F-1–D2（22~73Hz）**，成为次声波轰鸣，表现为"低频厚 +9dB、听着乱"，
    而当时所有既有检查都是绿灯（MIDI 值合法、和弦音级也合法 —— 只是**音区不合法**）。

    **区间按库里 17 首成品校准**（不是拍脑袋）：取各轨实际音域的包络 + 小余量，
    专门抓"整体大了一/两个八度"这类事故。
    """
    # 轨名: (最低, 最高)。**表在引擎里**（`song_engine.TR_RANGE`）—— 引擎的
    # "自适应八度边界保护"用同一张表，两处各写一份必然打架（引擎认为合法、自检说超界，
    # 或者反过来：引擎保护失灵而自检才报）。
    # 原区间 = 库内 17 首实测包络；现上下各**外扩 7 半音**（一个纯五度）——
    # 实测它原来会拦住正常的音区探索（如把主歌旋律下移八度到 F2，钢琴完全可行却报超界）。
    # 外扩后仍能抓住它真正要防的事故：**整体移一两个八度**（差 12 半音 > 7）。
    # Bass 下界保持 16：次声波是真实事故，不放。
    RANGE = song_engine.TR_RANGE
    bad = []
    for d in songs_or_fail():
        name = os.path.basename(d)
        try:
            data = song_engine.load(os.path.join(d, 'song.json'))
        except SystemExit:
            continue
        ev = song_engine.build_events(data)
        if not hasattr(ev, 'items'):
            ev = ev[0]
        for tr, (lo, hi) in RANGE.items():
            notes = [n[2] for n in ev.get(tr, []) if n[3] > 0]
            if not notes:
                continue
            if min(notes) < lo or max(notes) > hi:
                bad.append('%s/%s 实际 %d-%d（合理 %d-%d）→ 可能整体移了一/两个八度'
                           % (name, tr, min(notes), max(notes), lo, hi))
    assert not bad, '有轨超出乐器合理音域: ' + '; '.join(bad)



@check
def t_song_spec_sync():
    """有 `spec.json` 的曲目，其 spec **必须能重建出同一份 song.json**（旋律逐音 + 和弦音级）。

    为什么需要（实测踩过两次）：手工编辑 spec 时把 4 元 `[小节,拍,时值,音高]` 当成 3 元处理，
    **把时值 +7 了**；另一次 spec 与成品差了整八度。两者都让"复现"失效，而且**静默**。
    """
    import build_song as bs
    bad = []
    for d in songs_or_fail():
        name = os.path.basename(d)
        sp = os.path.join(d, 'spec.json')
        if not os.path.exists(sp):
            continue
        song = json.load(open(os.path.join(d, 'song.json'), encoding='utf-8'))
        spec = json.load(open(sp, encoding='utf-8'))
        try:
            again = bs.build(spec)
        except Exception as e:                       # noqa: BLE001
            bad.append('%s: spec 重建失败 %s: %s' % (name, type(e).__name__, e))
            continue
        for k, v in song['melody'].items():
            if again['melody'].get(k) != v:
                bad.append('%s: 旋律 %s 与 spec 重建不一致' % (name, k))
                break
        for c in {x for s in song['sections'] for x in s['chords']}:
            if c not in again['chords']:
                bad.append('%s: 和弦 %s 未在 spec 里生成' % (name, c))
                break
    assert not bad, 'spec 与 song.json 漂移（复现会失效）: ' + '; '.join(bad)
@check
def t_bands_abs_absolute():
    """bands_abs：**绝对口径**要看得见"差在哪"，**占用率**要看得见"墙还是点"。

    为什么需要（实测踩过，见 PITFALLS 60）：成绩单的倍频程是**相对本曲最响频段**归一的，
    低频一厚，10 个数字整体平移 —— 21_g150_velvet 连改 5 版编配，那张表上"看不出变化"；
    而 10-18k 的**连续性**（"墙/点"）原口径里根本没有这一列。
    三段人造音频把两件事钉死：
      甲 = 200Hz 长音 + **点状** 11kHz（每 0.5s 只响 60ms）
      乙 = 200Hz 长音 + **连续** 11kHz（平均功率与甲相近）
      丙 = 200Hz 长音 + 极弱 11kHz
    断言：① 乙比丙的 10-18k 绝对电平高 ≥12dB（绝对值口径有效）
          ② 甲/乙的**时域响度相同**（RMS 差 <1dB）→ 旧口径看不出差别
          ③ 乙占用 ≥80%、甲 ≤60%、差 ≥20 点（连续性口径有效）
    """
    import bands_abs as ba
    sr = 44100
    t = np.arange(int(sr * 3.0)) / sr
    lo = 0.3 * np.sin(2 * np.pi * 200.0 * t)
    hi = np.sin(2 * np.pi * 11000.0 * t)
    gate = np.zeros(len(t))
    for k in range(0, len(t), sr // 2):
        gate[k:k + int(0.06 * sr)] = 1.0                 # 占空比 12%
    sig = {'dot': lo + 0.25 * hi * gate,
           'wall': lo + 0.25 * hi * np.sqrt(0.12),       # 同平均功率，但一直在响
           'weak': lo + 0.01 * hi}
    tab = {}
    for k, v in sig.items():
        p = os.path.join(TMP, 'ba_%s.wav' % k)
        sf.write(p, np.stack([v, v], axis=1), sr)
        tab[k] = ba.table(p)
    key = '10000-18000'
    assert tab['wall']['abs'][key] - tab['weak']['abs'][key] >= 12.0, \
        '绝对口径读不出电平差: %s' % {k: v['abs'][key] for k, v in tab.items()}
    assert abs(tab['dot']['rms'] - tab['wall']['rms']) < 1.0, \
        '甲/乙的时域响度本该一样（这条检查才说明"电平看不出、占用率看得出"）: %.1f vs %.1f' % (
            tab['dot']['rms'], tab['wall']['rms'])
    od, ow = tab['dot']['occ'][key], tab['wall']['occ'][key]
    assert ow >= 80.0 and od <= 60.0 and ow - od >= 20.0, \
        '占用率没区分"墙/点": 点 %.0f%% 墙 %.0f%%' % (od, ow)


@check
def t_probe_timbre_measures_air():
    """probe_timbre：占用率能区分"连续的墙 / 点状"——选空气层音色唯一的客观判据。

    实测：参考曲 10-18k 占用 88%，Glock/Celesta 这类"点"只有 21%/19%，
    可它们的 10-18k **电平**在等响度下并不差（Glock 20.0dB vs Pad Sweep 15.9dB）——
    只看电平会选错音色。这条检查保证 `band_stats` 两列都真的在算。
    """
    import probe_timbre as pt
    sr = 44100
    t = np.arange(int(sr * 2.0)) / sr
    rng = np.random.default_rng(3)
    noise = rng.standard_normal(len(t))
    mask = np.zeros(len(t))
    for k in range(0, len(t), sr // 4):                  # 每 0.25s 响 50ms
        mask[k:k + int(0.05 * sr)] = 1.0
    paths = {}
    for k, v in (('dot', noise * mask), ('wall', noise * np.sqrt(0.2))):
        p = os.path.join(TMP, 'pt_%s.wav' % k)
        sf.write(p, np.stack([v, v], axis=1) * 0.3, sr)
        paths[k] = p
    st = {k: pt.band_stats(p) for k, p in paths.items()}
    key = '10000-18000'
    dw, dd = st['wall']['occ'][key], st['dot']['occ'][key]
    assert dw - dd >= 20.0, '占用率没区分墙/点: 点 %.0f%% 墙 %.0f%%' % (dd, dw)
    assert abs(st['wall']['abs'][key] - st['dot']['abs'][key]) < 6.0, \
        '两段的电平本该接近（这条检查说明"电平看不出、占用率看得出"）: %.1f vs %.1f' % (
            st['wall']['abs'][key], st['dot']['abs'][key])


@check
def t_probe_peaks_reads_root():
    """probe_peaks：**低音根音**必须读对（和声表里根错一格，整首歌就都错了）。

    实测：按"bin→音级"直接归属时，96kHz 下 11.7Hz 的 bin 会把整首歌读成 C/C#/E/G#/A；
    换谱峰法 + 长窗低音峰才读出 Bb / C / Dm7 / C#maj7 那条真进行。
    检查：造 150BPM 两小节 —— A2(110Hz)、C3(130.8Hz)，各带 4 个谐波。
    """
    import probe_peaks as pp
    sr = 44100
    bar = 4 * 60.0 / 150.0
    t = np.arange(int(sr * bar * 2)) / sr
    sig = np.zeros(len(t))
    for f0, off in ((110.0, 0.0), (130.81, bar)):
        k = (t >= off) & (t < off + bar)
        for h in range(1, 5):
            sig[k] += (0.3 / h) * np.sin(2 * np.pi * f0 * h * (t[k] - off))
    p = os.path.join(TMP, 'pp_roots.wav')
    sf.write(p, np.stack([sig, sig], axis=1), sr)
    rows = pp.analyze(p, 150.0)
    assert [r['root'] for r in rows[:2]] == [9, 0], \
        '根音读错: %s' % [r['root_name'] for r in rows[:2]]
    assert abs(rows[0]['low'][0][0] - 110) <= 3 and abs(rows[1]['low'][0][0] - 130.8) <= 3, \
        '低音峰频率不对: %s' % [r['low'] for r in rows[:2]]
    assert rows[0]['chroma'][0][0] == 'A' and rows[1]['chroma'][0][0] == 'C', \
        '音级占比第一不是根音: %s' % [r['chroma'][:2] for r in rows[:2]]





@check
def t_song_events_dump():
    """`song_events.py` 的逐轨事件出口必须和引擎一致（可视化面板的钢琴卷帘直接吃它）。

    为什么需要：这是面板唯一的数据源。它要是悄悄少了一轨/少了音，**面板上就是"看不见的东西"**，
    而渲染照样出声 —— 典型的静默不一致（panel 改回来还会把少掉的音写进 song.json）。
    判据：① 轨集合与音符数 == `song_engine.build_events()`；② 字段/范围合法；
          ③ `--track` 过滤只剩一轨。
    """
    import song_events as se
    song = {'name': 'ev', 'bpm': 120, 'chords': {'C': [36, [55, 60, 64, 67, 72]]},
            'melody': {'m': [[0, 0, 1, 72], [1, 2, 0.5, 74]]},
            'sections': [{'name': 'A', 'bars': 2, 'chords': ['C', 'C'], 'melody': 'm',
                          'arr': {'uku': True, 'bass': True, 'pad': True, 'perc': 1}}]}
    p = os.path.join(TMP, 'ev_song.json')
    json.dump(song, open(p, 'w', encoding='utf-8'))
    res = se.dump(p)
    ev, _nb = song_engine.build_events(song_engine.load(p))
    want = {k: len(v) for k, v in ev.items() if v}
    assert set(res['tracks']) == set(want), '轨集合不一致: %s vs %s' % (
        sorted(res['tracks']), sorted(want))
    for tr, n in want.items():
        assert res['tracks'][tr]['n'] == n, '%s 音符数不一致: %s vs %s' % (
            tr, res['tracks'][tr]['n'], n)
    for tr, d in res['tracks'].items():
        for (t, dur, m, vel) in d['notes']:
            assert t >= 0 and dur > 0, '%s 起始/时值非法: %s %s' % (tr, t, dur)
            assert 0 <= m <= 127 and 1 <= vel <= 127, '%s 音高/力度非法: %s %s' % (tr, m, vel)
    one = se.dump(p, 'Bass')
    assert list(one['tracks']) == ['Bass'], '--track 过滤失效: %s' % list(one['tracks'])
    assert one['tracks']['Bass']['n'] > 0, '过滤后没有音符'


@check
def t_render_json_mid_matches_name():
    """`render.json` 的 `mid` 必须与 `song.json` 的 `name` 对得上（**静默渲染旧 MIDI** 的坑）。

    实测踩到：把一首歌复制/改名为 `25_mixfit_demo` 后，`compose.py` 按 `song.json.name` 写出了
    `cheerful2.mid`（新），而 `render_midi` 仍按 `render.json.mid` 读**旧文件** → 改了 CC7/音符
    却渲染出旧音频，**成绩单一动不动**（我为此白跑了一轮实验）。
    判据：composer 是标准 `compose.py`（或没写）时，`render.json.mid` 必须 == `<name>.mid`；
          自定义 composer（老曲目）不查。
    """
    bad = []
    for d in songs_or_fail():
        name = os.path.basename(d)
        rp = os.path.join(d, 'render.json')
        if not os.path.isfile(rp):
            continue
        cfg = json.load(open(rp, encoding='utf-8'))
        comp = (cfg.get('composer') or 'compose.py')
        if '/' in comp or '\\' in comp or comp != 'compose.py':
            continue
        song = json.load(open(os.path.join(d, 'song.json'), encoding='utf-8'))
        want = (song.get('name') or name) + '.mid'
        got = os.path.basename(cfg.get('mid') or want)
        if got != want:
            bad.append('%s: render.json.mid=%s 但 song.json.name=%s（compose 会写 %s）'
                       % (name, got, song.get('name'), want))
    assert not bad, '改过名字却没同步 render.json（渲染的是旧 MIDI）: ' + '; '.join(bad)


@check
def t_bpm_out_of_window_reported():
    """<60 BPM 的曲子**不许被静默折半**：窗口外层必须显式报出来。

    实测（修复前）：55BPM 的"底鼓每拍 + 踩镲每半拍"被报成 111.1，而 info 里没有任何
    "这已经超出自动定层窗口"的说法 —— 扒新参考曲的人会直接把 111.1 写进画像。
    （同类：45BPM→90.7、180BPM→60.4，参考池里 64BPM 的曲子同样读成 110。）

    判据（双向契约）：
      · 45 / 55 BPM：报出来的值、或 `window_alt`，必须命中真值（±5%）；
      · 120 BPM（窗内）：**不许**冒出 `window_alt`（不该响不许响）。
    **已知边界（不算通过）**：>180 BPM 的信号（210/230 实测）自相关峰落在三拍关系上，
      窗口外提示**不**触发 —— 那一档仍然只能靠 `--bpm` 人工钉死，本用例不替它担保。
    """
    def train(bpm, sr=22050, seconds=20.0):
        """底鼓(60Hz)每拍 + 踩镲(噪声)每半拍。

        **不能用等幅脉冲串**：那样所有整数倍周期等强，自相关分不出"拍"与"4 拍"。
        """
        n = int(sr * seconds)
        x = np.zeros(n)
        beat = sr * 60.0 / bpm
        rng = np.random.default_rng(7)
        t = np.arange(int(sr * 0.12)) / sr
        k = np.sin(2 * np.pi * 60 * t) * np.exp(-t * 30.0)
        th = np.arange(int(sr * 0.03)) / sr
        h = rng.standard_normal(len(th)) * np.exp(-th * 120.0) * 0.35
        for i in range(int(n / beat) + 1):
            p = int(i * beat)
            if p + len(k) < n:
                x[p:p + len(k)] += k
            q = p + int(beat / 2)
            if q + len(h) < n:
                x[q:q + len(h)] += h
        return x.astype(np.float32), sr

    for true in (45.0, 55.0):
        m, sr = train(true)
        bpm, _s, info = quiet(metrics.detect_bpm, m, sr)[0]
        got = [v for v in (bpm, info.get('window_alt')) if v]
        assert any(abs(v - true) / true <= 0.05 for v in got), \
            '%.0fBPM 既没报对也没报窗口外层：bpm=%.1f info=%s' % (true, bpm, info)
        # "不许静默折半"：要么报了窗口外层，要么报了折上来的值（level_folded）
        assert info.get('window_alt') or info.get('level_folded'), \
            '%.0fBPM 超出窗口却没给任何提示（静默折半）：%s' % (true, info)
    m, sr = train(120.0)
    _b, _s, info = quiet(metrics.detect_bpm, m, sr)[0]
    assert not info.get('window_alt'), '120BPM 是窗内速度，不该报 window_alt：%s' % info


@check
def t_meter_34_68():
    """**3/4 与 6/8 真的走通了**（不只是"引擎里没写死 4.0"）：

    · MIDI 拍号元事件写对（否则 DAW 里小节线全错，内部再对也没用）
    · 每个音都落在**本小节内**（还在按 4 拍排的话，3/4 的曲子第 4 拍会溢出到下一小节）
    · 强拍口径跟着拍号（3/4 的第 2 拍是**弱拍** —— 那里放经过音是合法的，
      拿 4/4 的"第 1、3 拍"去判会误报；注入用例就是打这一条）
    """
    def probe(meter, melody):
        import midi_probe
        name = 'meter%d%d' % tuple(meter)
        d = {'name': name, 'bpm': 150, 'style': 'daily', 'meter': list(meter),
             'chords': {'D': [38, [57, 62, 66, 69, 74]],
                        'A': [33, [57, 61, 64, 69, 73]]},
             'melody': {'m': melody},
             'sections': [{'name': 'A', 'bars': 4, 'chords': ['D', 'A'] * 2,
                           'melody': 'm',
                           'arr': {'piano': True, 'bass': True, 'perc': 1}}]}
        p = os.path.join(TMP, name + '.json')
        json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
        mid = os.path.join(TMP, name + '.mid')
        print('        %s 的编配输出：' % name)          # compose 内部会打印，缩进一下免得刷屏
        song_engine.compose(p, mid, quiet=True)
        data = song_engine.load(p)
        ev, nbars = song_engine.build_events(data)
        B = song_engine.bar_beats(data['meter'])
        res = quiet(midi_probe.parse, mid, True)[0]
        assert tuple(res['timesig'] or ()) == tuple(meter), \
            '%s 的 MIDI 拍号写成 %s（应为 %s）' % (name, res['timesig'], tuple(meter))
        for k, notes in ev.items():
            for (t, dd, m, _v) in notes:
                bar = int(t // B)
                assert t >= -1e-6 and t < bar * B + B + 1e-6, \
                    '%s 的 %s 有音落在小节外：起始拍 %.2f（一小节 %s 拍）' % (name, k, t, B)
                # Pad/Strings 的尾音故意比小节长 0.1 拍（老行为），其余不许溢出
                lim = B + (0.15 if k in ('Pad', 'Strings') else 0.02)
                assert t - bar * B + dd <= lim, \
                    '%s 的 %s 时值溢出小节：%.2f 拍 > %.2f' % (name, k, t - bar * B + dd, lim)
        strong = song_engine.strong_beats(data['meter'])
        for (b, beat, _dd, m) in melody:
            if beat in strong:
                tones = [x % 12 for x in data['chords'][['D', 'A'][b % 2]][1]]
                assert m % 12 in tones, \
                    '%s 的强拍 %.1f（%s）放了弦外音 %d' % (name, beat, strong, m)
        return res

    # 3/4：第 2 拍放**经过音 63**（在 D 和弦小节里是弦外音）—— 那在 3/4 里是弱拍，合法
    res = probe([3, 4], [[0, 0, 1, 62], [0, 2, 1, 63], [1, 0, 1, 61], [1, 2, 1, 62],
                         [2, 0, 1, 62], [2, 2, 1, 63], [3, 0, 1, 61], [3, 2, 1, 62]])
    assert res['bpm'] and abs(res['bpm'] - 150.0) < 0.01, '3/4 的速度写错: %s' % res['bpm']
    # 6/8：强拍在 0 与 1.5（每小节两个附点四分脉冲），第 1 拍放经过音
    probe([6, 8], [[0, 0, 1, 62], [0, 1.0, 1, 63], [0, 1.5, 1, 66],
                   [1, 0, 1, 61], [1, 1.0, 1, 62], [1, 1.5, 1, 64],
                   [2, 0, 1, 62], [2, 1.5, 1, 69]])


@check
def t_breath_fix_works():
    """换气修复工具（`fix_breathing.py`）真的能修，而且**只改时值**：

    · 40 小节连奏（60BPM = 160 秒不断）必须被判需要换气并给出改法
    · 修完必须达标（不再有 >20 秒不间断）
    · **落点/音高一个都不许动**（只收短时值）—— 否则等于偷偷改了旋律
    · 幂等：再跑一次不该再改
    """
    import breath
    import fix_breathing as fb
    bars = 40
    mel = [[b, float(k), 1.0, 72 + (k % 2)] for b in range(bars) for k in range(4)]
    d = {'name': 'breathfix', 'bpm': 60, 'meter': [4, 4], 'style': 'daily',
         'chords': {'C': [36, [55, 60, 64, 67, 72]]},
         'melody': {'m': mel},
         'sections': [{'name': 'A', 'bars': bars, 'chords': ['C'] * bars, 'melody': 'm',
                       'arr': {'piano': True, 'bass': True}}]}
    p = os.path.join(TMP, 'breathfix.json')
    json.dump(d, open(p, 'w', encoding='utf-8'))
    assert breath.long_runs(d)[0], '夹具本身就该是"长段"，否则这条检查会空转'
    changes, _run, _step = fb.fix_song(p, dry=True)
    assert changes, '40 小节连奏（160 秒）应当被判需要换气并给出改法'
    fb.fix_song(p, dry=False)
    after = json.load(open(p, encoding='utf-8'))
    assert not breath.long_runs(after)[0], \
        '修完仍有 >20 秒不换气：%s' % breath.long_runs(after)[0]
    assert [(x[0], x[1], x[3]) for x in after['melody']['m']] == \
        [(x[0], x[1], x[3]) for x in mel], '修复工具不许动落点/音高（只许改时值）'
    again, _r, _s = fb.fix_song(p, dry=True)
    assert not again, '修复应当幂等（第二次不该再改）'


MELODY_WIN = 8          # 旋律窗口：连续 8 个音（≈2–3 小节）
MELODY_SIM_MAX = 0.05   # 允许的"跨曲共享窗口"比例上限
MELODY_LANG_TWIN_MAX = 2   # 允许的"孪生对"数（语言重合 ≥85% = 同一种说话方式）
MELODY_ACCEPT_MIN = 0.40   # 生成旋律与画像的逐维承接度下限（落点/时值）
MELODY_ACCEPT_SPARSE = 0.40   # 画像本身很稀疏（<80 个旋律音）时的下限：直方图是稀疏采样
MIDI_LIB_DIRS = ('refs/midi', 'refs/midi2')   # 模板库（音符层参考素材）目录
# 本项目会往系统临时目录写东西的前缀 + 卫生阈值（`t_tmp_hygiene` 用）
TMP_PREFIXES = ('selftest_', 'mutation_', 'rehearsal_')   # 瞬态目录：必须自己清干净
TMP_CACHE_DIRS = ('bgm-studio-audio',)   # 面板的音频缓存：只报体积，不算失败
TMP_MAX_AGE_H = 24        # 超过这个小时数还留着 = 清理失效
TMP_MAX_MB = 512          # 这些目录累计超过这个量 = 有工具在漏
# 旋律"音乐性"结构层的判据（`t_melody_motif_rules`）。
# ⚠ 2026-09-14 改：**重复率从下限改成上限**。旧值 0.55 是拍的（当时只有"旧版 19% vs
# 动机版 70%"两个自家样本）；量了真实模板旋律 150 首（`refs/midi2/`，
# `theme_pack._melody_notes` 提取，与生成端同一套定义）之后真相是：
# **小节节奏签名重复率中位只有 23%、均值 33%** —— "每小节复刻同一 figure"（旧版 66~70%）
# 正是用户说的"呆板"。下限门留着就会把"不呆板"判成不合格。
MOTIF_MAX_REPEAT = 0.45      # 上限：节奏动机重复率（真实中位 23% / 均值 33%）
MOTIF_MIN_REVERSE = 0.60     # 大跳后反向率（真实中位 66% / 均值 62%）
MOTIF_MIN_FILL = 0.50        # 反向里"回填"的比例（真实中位 60%）
# 句末收束率下限。**2026-09-15 从 0.55 降到 0.25**：用**同一口径**（逐 4 小节窗取句末音，
# 时值 ≥1.0 拍且音级落在主/属和弦音；主音取旋律音级直方图最高音级）复算 **218 首真实模板
# （`refs/midi2/`）**：10% 分位 = 0.25、25% = 0.50、中位 = 0.67。
# → **旧门 0.55 会把 33% 的真实写法判成不合格**（实测），而"开放句尾"是现代/悬留型
# 编曲的常见写法、不是缺陷（用户口径："给标准降低一点，现代音乐也符合标准"）。
# 新门取真实模板的 **10% 分位**：只有比 90% 的真实写法更不闭合才算问题。
MOTIF_MIN_CADENCE = 0.25
# 旋律"形态层"的判据（`t_melody_form_rules`）—— 对照值全部来自真实模板（同上 150 首）：
# 小节末落点 ≥8 格的小节占比中位 90%（cheerful 主题 79%）、小节内最大空档中位 1.03 拍
# （cheerful 1.40）、格 0 落点占比中位 12.9%（cheerful 14.8%）、密度 cheerful 2.63。
FORM_MIN_LAST8 = 0.65        # 末落点 ≥8 格（跨过第 2 拍）的小节占比下限
FORM_MAX_GAP_MED = 1.70      # 小节内最大空档中位上限（拍）
FORM_DENS = (1.8, 2.9)       # 密度区间（用户口径 2.0~2.6，留生成随机性的余量）
FORM_MAX_G0 = 0.22           # 格 0（小节第 1 拍）落点占比上限
# 句内高点位置（**三音滑动平均的轮廓**，见 `melody_gen.form_stats`）：旋律写作的拱形是
# "起 → 高点（约 2/3 处）→ 落"。区间取宽（证明"高点不在句首、也不在句末"）——
# 实测：加拱形前中位 **0.225**（句句都在往下掉）、加拱形后中位 **0.667**。
FORM_PEAK = (0.45, 0.85)
# 音域：**对着画像判**，不是拍绝对下限。旧版 `persona` 把画像 range 两头各砍一点
# （`lo+2 / hi-1`）→ 实测 37 号只用了 13 个半音（画像 17），用户口径是"音域用足"。
FORM_SPAN_RATIO = 0.45
# 落盘曲目的**音域合理下限**（半音）：一个八度 —— 旋律的常识下限。
# ⚠ 别拿"画像 range × 比例"当单曲下限：画像是**同主题多首模板的并集**（tender 34 半音），
# 单曲自然更窄（39 号 18 半音 = 53%，完全正常）。
FORM_SPAN_MIN = 8
# **上面三条门的真值依据**（2026-09-15 用模板重新校准时发现原值过严）：
#   时值交叠（MIDI note 时值 vs 画像 `dur16_hist`）：cheerful min 0.42 / 中位 0.52；
#     sorrow min 0.49 / 中位 0.70 —— 原门 0.55 比真实音乐还严（cheerful 误伤 6/9 首）。
#     口径差异是根因：画像那张表是 **F0 跟踪的"发声时长"**，与 MIDI 的 note-off 不是一回事
#     （见 `melody_gen._make_cell` 的注释），所以本就不该要求高交叠。
#   span/画像：cheerful min 0.47 / 中位 1.12；sorrow min 0.45 / 中位 0.76 ——
#     **单曲音域比聚合画像窄是常态**，原门 0.85 误伤 4/9 与 6/10 首。
#   模板最小 span = 8（Disco Citizens - Footprint）→ 原下限 12 误伤 3 首。
# 夹具只有 16 小节，**音域本来就撑不满**（实测 4 个 seed 合并 19/24 = 79%）——
# 短样本用这个门；落盘曲目（64 小节）用上面的 0.85。
FORM_SPAN_RATIO_SHORT = 0.70


def _melody_windows(notes, w=MELODY_WIN):
    """一条旋律的窗口形状集合：音程序列 + 相对时值（**转调/变速不变**）。

    只比形状不比绝对音高 —— 同一个动机换个调、换速度，听起来还是同一句。
    """
    out = set()
    for i in range(max(0, len(notes) - w)):
        seg = notes[i:i + w]
        out.add((tuple(seg[k + 1][2] - seg[k][2] for k in range(w - 1)),
                 tuple(round(seg[k + 1][0] - seg[k][0], 2) for k in range(w - 1))))
    return out


@check
def t_melody_distinct():
    """**跨曲主旋律不许雷同**（用户反馈："怎么这么多歌的主旋律都是一样的"）。

    量的是"窗口形状"（连续 8 个音的音程 + 相对时值，转调/变速不变）在**别的曲子里**出现的比例。
    实测（本轮）：初版全库 **1%**、其中 12/13 是唯一一对句子级重复（各 16/181 个窗口，第 40–50 拍
    共用一个乐句）→ 已改写 13 那一句 → **0.5%**。

    ⚠ 注意它**抓不到**"同一套旋律语言"（落点/时值分布 90% 重合那一类）—— 那是画像复用的结果，
    见 HISTORY：10 首歌共用 2 份旋律画像（BGM33 ×5、BGM16c ×5）。这条只防"照抄/退化"。
    """
    rows, checked = [], 0
    for d in song_dirs():
        j2 = json.load(open(os.path.join(d, 'song.json'), encoding='utf-8'))
        pos, notes = 0.0, []
        for sec in j2['sections']:
            for x in (j2['melody'].get(sec['melody']) or []):
                if 0 <= x[0] < sec['bars']:
                    notes.append((pos + x[0] * 4.0 + x[1], x[2], x[3]))
            pos += sec['bars'] * 4.0
        if len(notes) > MELODY_WIN:
            rows.append((os.path.basename(d), notes))
            checked += 1
    assert checked >= 2, '带旋律的曲目太少（%d），这条检查会空转' % checked
    wins = {n: _melody_windows(ns) for n, ns in rows}
    # 判据自证：整体移调 = 同形；换节奏 = 不同形
    a = [(i * 1.0, 1.0, 60 + i) for i in range(9)]
    b = [(i * 1.0, 1.0, 72 + i) for i in range(9)]
    c = [(i * 2.0, 1.0, 60 + i) for i in range(9)]
    assert _melody_windows(a) & _melody_windows(b), '整体移调的同一句必须判为同形'
    assert not (_melody_windows(a) & _melody_windows(c)), '节奏不同的句子不该判为同形'
    tot = shared = 0
    bad = []
    for name, ws in wins.items():
        tot += len(ws)
        s = sum(1 for sh in ws if any(sh in wins[o] for o in wins if o != name))
        shared += s
        if len(ws) and s / len(ws) > MELODY_SIM_MAX * 2:
            bad.append('%s %d/%d 个窗口' % (name, s, len(ws)))
    ratio = shared / max(1, tot)
    print('        跨曲共享旋律窗口 %d/%d = %.1f%%（上限 %.0f%%）%s'
          % (shared, tot, ratio * 100, MELODY_SIM_MAX * 100,
             ('；单曲超限: ' + ', '.join(bad)) if bad else ''))
    assert ratio <= MELODY_SIM_MAX, \
        ('跨曲主旋律雷同：%d/%d = %.1f%%（上限 %.0f%%）；单曲超限: %s'
         % (shared, tot, ratio * 100, MELODY_SIM_MAX * 100, ', '.join(bad)))


@check
def t_melody_lang_diverse():
    """**每首歌要有自己的说话方式**（用户原话："怎么这么多歌的主旋律都是一样的"）。

    量的是**分布级**重合：落点(16 分格)/时值/音程/句长/拱形 5 组直方图的平均交叠率，
    和 `melody_distinct`（形状级，防照抄/退化）不是一回事 —— 这条防"一套口音"。
    实测（2026-09-13）：修 `melody_gen` 前，全库**跨曲共享片段只有 0.5%**（不是照抄），
    但最像的一对 87%（12~13）、落点维度两两平均 58% —— 听感"每首都像"来自这里。
    修好后（真读画像的句长/落点/音程/时值 + 每首一份画像 + 生成去重筛选）：孪生对 0、
    落点维度平均 41%。工具 `probe_melody_lang.py`。
    """
    import probe_melody_lang as PL
    # 判据自证：同一句整体移调 = 100% 重合；节奏和走向都换掉 = 明显更低
    a = [(i * 1.0, 0.5 + (i % 3) * 0.25, 60 + (i % 5)) for i in range(24)]
    b = [(i * 1.0, d, p + 7) for (i, d, p) in a]
    c = [(i * 2.0, 1.0, 60 + (i % 7)) for i in range(24)]
    assert PL.sim(PL.feats(a), PL.feats(b)) > 0.999, '整体移调的同一句必须 100% 重合'
    assert PL.sim(PL.feats(a), PL.feats(c)) < 0.9, '节奏/走向都换掉的句子不该是同一种说话方式'
    r = PL.report()
    assert len(r['items']) >= 3, '带旋律的曲目太少（%d），这条检查会空转' % len(r['items'])
    n = len(r['twin'])
    print('        孪生对 %d 对（上限 %d）；落点维度两两平均 %.0f%%、最高 %.0f%%'
          % (n, MELODY_LANG_TWIN_MAX, r['dims']['onset'][0] * 100,
             r['dims']['onset'][1] * 100))
    assert n <= MELODY_LANG_TWIN_MAX, \
        ('旋律语言雷同：%d 对孪生（上限 %d）→ %s'
         % (n, MELODY_LANG_TWIN_MAX,
            '；'.join('%.0f%% %s~%s' % (cc * 100, x, y) for cc, x, y in r['twin'])))


@check
def t_melody_matches_profile():
    """**生成出来的旋律必须像它的画像**（统计层守卫）。

    防的是坑 114/115 那一类：旋律在生成过程里看着对，落盘却因为**出口裁剪 / 概率过滤**
    而偏离画像 —— 实测那首 31 号：0.25 拍碎音 11%（画像 0%）、2 拍长音 0%（画像 48%）、
    正拍被长尾过滤滤光…… 而**频段类守卫一个都抓不到**（频谱完全正常）。

    只查带 `melody_gen` 元数据的曲子（= 旋律确实由画像生成；手写旋律没有"该像谁"这回事）。
    判据：**落点、时值**两维的直方图交叠率 ≥ `MELODY_ACCEPT_MIN`。
    """
    import probe_melody_lang as PL
    import melody_profile as MP
    rows = []
    for d in song_dirs():
        p = os.path.join(d, 'song.json')
        j2 = json.load(open(p, encoding='utf-8'))
        pname = (j2.get('melody_gen') or {}).get('profile')
        if not pname:
            continue
        # 画像解析走 `melody_profile.find_profile`（refs/melody → refs/themes）：
        # 主题模板包产出的画像在 refs/themes/，硬拼 refs/melody 会让主题曲**静默跳过**这条守卫
        pp = MP.find_profile(pname)
        if not pp:
            continue
        notes, is44 = PL.notes_of(p)
        f = PL.feats(notes, is44)
        prof = json.load(open(pp, encoding='utf-8'))
        pf = PL.prof_feats(prof)
        if not f or not pf:
            continue
        dims = {k: PL.sim({k: f[k]}, {k: pf[k]})
                for k in ('onset', 'dur') if k in f and k in pf}
        # 画像自己的样本量决定判据松紧：<80 个旋律音的画像（实测 BGM16 只有约 50 个）
        # 本身就是稀疏采样，落点直方图不可靠 → 用 MELODY_ACCEPT_SPARSE。
        pnotes = prof.get('notes') or sum((prof.get('dur16_hist') or {}).values())
        if dims:
            rows.append((os.path.basename(d), pname, dims, pnotes))
    assert rows, '没有带 melody_gen 元数据的曲子（%d），这条检查会空转' % len(rows)
    # 判据自证：同一分布自比 = 100%；全碎音的旋律，时值维必须明显掉下来
    long_n = [(i * 2.0, 2.0, 60) for i in range(16)]
    chop_n = [(i * 0.25, 0.25, 60) for i in range(16)]
    fl, fc = PL.feats(long_n), PL.feats(chop_n)
    assert PL.sim({'dur': fl['dur']}, {'dur': fl['dur']}) > 0.999, '同一分布自比必须 100%'
    assert PL.sim({'dur': fl['dur']}, {'dur': fc['dur']}) < 0.5, '全碎音不该判成与长音分布相似'
    for n, pn, dims, pnotes in rows:
        print('        %-22s 画像 %-18s 落点 %3.0f%%  时值 %3.0f%%   (画像音数 %d%s)'
              % (n, pn, dims.get('onset', 0) * 100, dims.get('dur', 0) * 100, pnotes,
                 '，稀疏档' if pnotes < 80 else ''))
    bad = []
    for n, pn, dims, pnotes in rows:
        thr = MELODY_ACCEPT_MIN if pnotes >= 80 else MELODY_ACCEPT_SPARSE
        if min(dims.values()) < thr:
            bad.append('%s（画像 %s）落点 %.0f%%/时值 %.0f%%（下限 %.0f%%）'
                       % (n, pn, dims.get('onset', 0) * 100, dims.get('dur', 0) * 100, thr * 100))
    assert not bad, ('生成旋律离画像太远：%s —— 先查 melody_gen 的出口裁剪/落点过滤'
                     '（坑 114/115），别去调画像' % '；'.join(bad))


@check
def t_midi_lib_index_sync():
    """**模板库的索引必须与目录里的文件对得上**（`refs/midi`（1 号）、`refs/midi2`（2 号））。

    防的是"库越用越乱"：手工删/加了 .mid 而索引没更新、抓来的重复文件混进来、
    索引里的 bpm/小节 是坏的 —— 这些都会让"从库里挑参考曲"这一步**静默选到不存在或
    不可用的文件**。三条：① 索引里的文件都真实存在 ② 目录里的文件都在索引里
    ③ 特征字段齐全且数值合理。**不联网**（只查本地库）。
    """
    libs = [os.path.join(ROOT, *p.split('/')) for p in MIDI_LIB_DIRS]
    have = [p for p in libs if os.path.isfile(os.path.join(p, '_index.json'))]
    assert have, '没有找到任何模板库（%s），这条检查会空转' % ', '.join(MIDI_LIB_DIRS)
    tot = 0
    for root in have:
        rows = json.load(open(os.path.join(root, '_index.json'), encoding='utf-8-sig'))
        assert rows, '%s 的索引是空的' % root
        on_disk = set()
        for dp, dn, fns in os.walk(root):
            dn[:] = [x for x in dn if not x.startswith('_')]   # _broken 等内部目录不算库内容
            for f in fns:
                if f.lower().endswith(('.mid', '.midi')):
                    on_disk.add(os.path.relpath(os.path.join(dp, f), root).replace('\\', '/'))
        idx_paths, idx_base = set(), set()
        for r in rows:
            f = r.get('file')
            assert f, '%s 的索引项缺 file 字段' % root
            idx_paths.add(f)
            idx_base.add(os.path.basename(f))
        # ① 索引 → 磁盘（1 号库的 file 只有文件名，按 basename 兜一层）
        missing = [f for f in idx_paths
                   if f not in on_disk and os.path.basename(f) not in {os.path.basename(x) for x in on_disk}]
        assert not missing, ('索引里有 %d 个文件在磁盘上不存在：%s —— 删文件后要重跑 '
                             'fetch_midi_lib.py 重建索引' % (len(missing), ', '.join(sorted(missing)[:4])))
        # ② 磁盘 → 索引
        unindexed = sorted(p for p in on_disk if os.path.basename(p) not in idx_base)
        assert not unindexed, ('磁盘上有 %d 个 .mid 不在索引里：%s —— 重跑 fetch_midi_lib.py'
                               % (len(unindexed), ', '.join(unindexed[:4])))
        # ③ 特征合理
        bad = []
        for r in rows:
            bpm = r.get('bpm') or 0
            if not (0 < bpm <= 400):
                bad.append('%s bpm=%s' % (r['file'], bpm))
            if not (r.get('bars') or 0) > 0:
                bad.append('%s bars=%s' % (r['file'], r.get('bars')))
            for t in (r.get('tracks') or []):
                lo, hi = t.get('lo'), t.get('hi')
                if lo is not None and hi is not None and not (0 <= lo <= hi <= 127):
                    bad.append('%s 音域 %s-%s' % (r['file'], lo, hi))
        assert not bad, '索引里的特征不合理（%d 条）：%s' % (len(bad), '；'.join(bad[:4]))
        tot += len(rows)
        print('        %-28s %4d 首（索引与磁盘一致）'
              % (os.path.relpath(root, ROOT), len(rows)))
    assert tot >= 10, '模板库总共只有 %d 首，太少了' % tot


@check
def t_melody_health():
    """**旋律形态守卫** —— 补上"频段/响度/结构类守卫看不见"的那一层。

    起因：这一轮用户连报三次听感问题（"镫镫地卡着不规律"、"d d d d ddd"），而当时
    84 项自检**全绿**。查出来的是：最长连续同音 6 个、密度低到 1.0 音/小节、
    长音被截成 0.25 拍 —— 全是旋律**形态**的事，跟频谱无关。
    判据（含"碎音对照画像"的口径）统一收在 `probe_melody_health.py`，工具与守卫共用一套。
    """
    import probe_melody_health as MH
    rows = MH.collect()
    assert rows, '没有可体检的曲目，这条检查会空转'
    # 判据自证：连续 6 个同音的旋律必须被判为问题；干净的必须不被判
    def fake(**kw):
        base = dict(name='x', notes=10, dens=2.0, same=10.0, maxrun=2, chop=0.0,
                    grids=6, onbeat=50.0, fit=100.0, bpm=100.0, gen=None, bars=8)
        base.update(kw)
        return base
    assert MH.issues(fake(maxrun=6)), '连续 6 个同音必须判为问题'
    assert MH.issues(fake(dens=1.0)), '密度 1.0 音/小节必须判为问题'
    assert not MH.issues(fake()), '干净的旋律不该被判为问题'
    bad = ['%s: %s' % (r['name'], '、'.join(MH.issues(r)))
           for r in rows if MH.issues(r)]
    print('        最长同音串 %d（上限 %d）· 密度下限 %.1f · %d/%d 首有形态问题'
          % (max(r['maxrun'] for r in rows), MH.MAX_RUN, MH.MIN_DENS, len(bad), len(rows)))
    assert not bad, ('旋律形态问题（用户口径："一串同音"/"音太少"/"卡卡的"）：%s —— '
                     '跑 probe_melody_health.py 看细节，重跑 melody_gen 修'
                     % '；'.join(bad[:6]))


@check
def t_theme_pack_valid():
    """**主题模板包必须是"多个同主题模板聚合 + 白名单来源"**（用户口径的守卫）。

    口径（用户明确要求）：一次生成要依据**很多不同的相同主题模板**，模板只能来自
    `refs/midi2/`（网络多风格 MIDI 库）或网络上带来源 URL 的权威数据 ——
    不许拿"自己生成的曲子"或某一份音频当模板。判据全部收在 `theme_pack.validate_pack`
    （生成路径也用同一份 → 检查与生成不会各说各话）：
      · 模板数 ≥ 下限（默认 8 首），且**不重复**（同一首顶两首 = 凑数）
      · 每首都在 `refs/midi2/_index.json` 里（md5 对得上 = 没被替换过）
      · 每首的风格属于该主题的风格集合（"同主题"不是随便凑）
      · 每首都有来源 URL 且站点在权威白名单里
      · 画像字段齐全（速度/调式/和声进行/节奏/旋律），旋律画像音数够（统计才可信）
    """
    import theme_pack as tp
    packs = sorted(glob.glob(os.path.join(ROOT, 'refs', 'themes', '*.json')))
    packs = [p for p in packs if not os.path.basename(p).endswith('_melody.json')]
    assert packs, ('没有主题模板包（refs/themes/*.json）—— 这条检查会空转。'
                   '生成新歌前先跑 python scripts\\theme_pack.py --all')
    # 判据自证：① 模板不足必须被抓 ② 非白名单来源必须被抓 ③ 混音目标指向不存在的画像
    fake = {'theme': 'daily', 'min_templates': 8, 'templates': [], 'engine_style': 'daily',
            'bpm': {'median': 100}, 'key': {'tonic': 'C', 'mode': 'minor'},
            'harmony': {'progressions': [{'romans': ['i'], 'symbols': ['Cm']}]},
            'rhythm': {'low16': '★···'}, 'form': {'plan': [{'name': 'A'}]},
            'mix_target': {'ref': 'bgm01c', 'score': 0.5, 'why': 'x'},
            'melody': {'onset16_hist': {}, 'dur16_hist': {}, 'interval_hist': {},
                       'range': [60, 80], 'notes_per_bar': 2, 'notes': 100}}
    probs = tp.validate_pack(fake, root=ROOT)
    assert any('模板只有' in p for p in probs), '模板数不足必须被判为问题（判据自证）'
    fake2 = dict(fake)
    fake2['templates'] = [{'file': 'pop/x.mid', 'style': 'pop', 'md5': 'x',
                           'source': 'http://evil.example.com/x.mid'}] * 8
    probs2 = tp.validate_pack(fake2, root=ROOT)
    assert any('白名单' in p for p in probs2), '非白名单来源必须被判为问题（判据自证）'
    fake3 = dict(fake)
    fake3['mix_target'] = {'ref': 'no_such_portrait'}
    probs3 = tp.validate_pack(fake3, root=ROOT)
    assert any('混音目标' in p for p in probs3), '混音目标指向不存在的画像必须被抓（判据自证）'
    bad = []
    for p in packs:
        pack = json.load(open(p, encoding='utf-8'))
        probs = tp.validate_pack(pack, root=ROOT)
        name = os.path.basename(p)[:-5]
        mp_ = os.path.join(ROOT, 'refs', 'themes', name + '_melody.json')
        if not os.path.isfile(mp_):
            probs.append('缺旋律子画像 %s_melody.json（melody_gen 没有画像可用）' % name)
        else:
            m = json.load(open(mp_, encoding='utf-8'))
            for k in ('onset16_hist', 'dur16_hist', 'interval_hist', 'range',
                      'notes_per_bar', 'notes'):
                if k not in m:
                    probs.append('旋律子画像缺字段 %s' % k)
        if probs:
            bad.append('%s: %s' % (name, '；'.join(probs[:3])))
    tot = sum(len(json.load(open(p, encoding='utf-8')).get('templates') or []) for p in packs)
    print('        %d 个主题包 · 共 %d 首模板（每个 ≥%d 首，来源白名单 + 索引可溯）'
          % (len(packs), tot, tp.MIN_TEMPLATES))
    assert not bad, ('主题模板包不合规：%s —— 重跑 python scripts\\theme_pack.py <主题>'
                     '（模板不足时加 --allow-fetch 联网抓）' % '；'.join(bad[:4]))


@check
def t_theme_basis_whitelist():
    """**新歌声明的"模板依据"必须是主题模板包**（不许拿自己做的曲子当模板）。

    判据：song.json 里
      · 写了 `theme`（主题路径）→ 该主题包必须存在、名单里的模板必须与包**逐首一致**、
        数量与包一致（少写几首 = 隐藏真实依据）；`basis.kind` 只认 `theme_pack`
      · 写了 `basis.kind != theme_pack`（老 `--from` 路径的留痕）→ **FAIL**，并给出改用
        `--theme` 的指令（用户口径：模板只能是 refs/midi2 或网络权威数据）
      · 两样都没写的旧曲目（历史产物）→ 跳过并计数，不追溯
    """
    import theme_pack as tp
    checked, legacy, bad = 0, 0, []
    for d in song_dirs():
        j = json.load(open(os.path.join(d, 'song.json'), encoding='utf-8'))
        name = os.path.basename(d)
        basis = j.get('basis') or {}
        th = j.get('theme') or {}
        if basis and basis.get('kind') != 'theme_pack':
            bad.append('%s: basis.kind=%s（依据不是白名单模板 —— 改用 new_song.py --theme <主题>）'
                       % (name, basis.get('kind')))
            continue
        if not th:
            legacy += 1
            continue
        checked += 1
        theme = th.get('name')
        if theme not in tp.THEMES:
            bad.append('%s: theme.name=%r 不在主题表里' % (name, theme))
            continue
        pp = tp.pack_path(theme, root=ROOT)
        if not os.path.isfile(pp):
            bad.append('%s: 主题包不存在 %s' % (name, os.path.relpath(pp, ROOT)))
            continue
        pack = json.load(open(pp, encoding='utf-8'))
        probs = tp.validate_pack(pack, root=ROOT)
        if probs:
            bad.append('%s: 主题包不合规（%s）' % (name, probs[0]))
            continue
        want = [t['file'] for t in pack['templates']]
        got = list(th.get('templates') or [])
        if sorted(got) != sorted(want):
            bad.append('%s: theme.templates 与包不一致（声明 %d 首 / 包里 %d 首%s）'
                       % (name, len(got), len(want),
                          '' if not (set(want) - set(got)) else
                          '；漏了 %s' % (sorted(set(want) - set(got))[:2])))
        if int(th.get('template_count') or 0) != len(want):
            bad.append('%s: theme.template_count=%s ≠ 包里的 %d 首'
                       % (name, th.get('template_count'), len(want)))
        if len(want) < tp.MIN_TEMPLATES:
            bad.append('%s: 依据的模板只有 %d 首（要求 ≥%d）'
                       % (name, len(want), tp.MIN_TEMPLATES))
        mp_ = tp.melody_path(theme, root=ROOT)
        if th.get('melody_profile') and not os.path.isfile(mp_):
            bad.append('%s: theme.melody_profile 指向的 %s 不存在' % (name, th['melody_profile']))
    print('        主题路径曲目 %d 首（逐首核对模板名单）· 历史曲目 %d 首（跳过）'
          % (checked, legacy))
    assert not bad, ('模板依据不合规：%s' % '；'.join(bad[:4]))


@check
def t_tmp_hygiene():
    """**别把系统临时目录当垃圾场**：本项目自己的临时目录必须在退出时清掉。

    实测教训（用户报"C 盘怎么变小了"）：`selftest` 的 TMP 是**模块级**创建的，而它被
    `check_song` / `build_song` / `midi_ref` / `theme_pack` / `mutation_check` 到处 import
    —— 于是每一次这类工具运行都会在 `%TEMP%` 留一个 `selftest_*` 目录（里面是渲染出来的
    WAV/OGG），而它**从来不清理**：实测累积 **1601 个 / 7.5GB**，直接把系统盘吃紧。
    现在两处都注册了 `atexit` 清理（`DSH_KEEP_TMP=1` 可保留），这条守卫防复发：
      ① 本项目前缀的临时目录**存在超过 `TMP_MAX_AGE_H` 小时** → 报问题（清理失效/进程被杀）
      ② 这些目录的总量超过 `TMP_MAX_MB` → 报问题（防"每天漏一点、一年几十 GB"）
    判据自证：伪造一个"3 天前"的目录必须被抓；空集合不许报警。
    """
    root = os.environ.get('TEMP') or os.environ.get('TMP') or tempfile.gettempdir()
    now = time.time()

    def scan():
        out = []
        for p in TMP_PREFIXES:
            for d in glob.glob(os.path.join(root, p + '*')):
                try:
                    age_h = (now - os.path.getmtime(d)) / 3600.0
                    sz = sum(f.stat().st_size for f in
                             (os.scandir(d) if os.path.isdir(d) else [])
                             if f.is_file())
                except OSError:
                    continue
                out.append((os.path.basename(d), round(age_h, 1), sz))
        return out
    # 判据自证：伪造一个"3 天前"的临时目录 → 必须被抓
    fake = os.path.join(root, 'selftest_zz_probe_%d' % os.getpid())
    os.makedirs(fake, exist_ok=True)
    open(os.path.join(fake, 'x.wav'), 'wb').write(b'0' * 1024)
    old = now - 3 * 24 * 3600
    os.utime(fake, (old, old))
    try:
        stale = [r for r in scan() if r[1] > TMP_MAX_AGE_H]
        assert any(r[0].startswith('selftest_zz_probe') for r in stale), \
            '伪造的"3 天前"临时目录没被抓（这条守卫是坏的）'
    finally:
        import shutil
        shutil.rmtree(fake, ignore_errors=True)
    # **清理机制必须真的生效**：开个子进程 import selftest（它会在导入时建 TMP），
    # 子进程退出后那个目录必须消失 —— 比"读 atexit 内部结构"结实得多。
    if not os.environ.get('DSH_KEEP_TMP'):
        code = ('import sys; sys.path.insert(0, %r); import selftest; print(selftest.TMP)'
                % HERE)
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        leaked = (r.stdout or '').strip().splitlines()[-1:] or ['']
        assert not os.path.exists(leaked[0]), \
            ('子进程退出后临时目录还在（atexit 清理失效）：%s' % leaked[0])
    rows = scan()
    stale, total = [], 0
    for name, age_h, sz in rows:
        total += sz
        if age_h > TMP_MAX_AGE_H:
            stale.append('%s（%.0f 小时前 / %.1fMB）' % (name, age_h, sz / 1e6))
    cache = 0
    for c in TMP_CACHE_DIRS:                     # 递归算（缓存是按 job 分子目录放的）
        cd = os.path.join(root, c)
        if os.path.isdir(cd):
            for dp, _dn, fns in os.walk(cd):
                cache += sum(os.path.getsize(os.path.join(dp, f)) for f in fns
                             if os.path.isfile(os.path.join(dp, f)))
    mb = total / 1e6
    print('        瞬态临时目录 %d 个 / %.1fMB（阈值 %dMB、%.0f 小时）· 面板音频缓存 %.0fMB'
          % (len(rows), mb, TMP_MAX_MB, TMP_MAX_AGE_H, cache / 1e6))
    # **自愈**：>24 小时的残留（多半是上次进程被杀，atexit 没跑到）当场清掉并说明 ——
    # 只报警不清理会让"昨天被杀一次、今天开始一直红"。真正的失败留给预算那条（有工具在漏）。
    if stale:
        import shutil
        for name, age_h, _sz in [(r[0], r[1], r[2]) for r in rows if r[1] > TMP_MAX_AGE_H]:
            shutil.rmtree(os.path.join(root, name), ignore_errors=True)
        print('        （清掉 %d 个上次残留：%s —— 进程被杀时 atexit 跑不到）'
              % (len(stale), '；'.join(stale[:3])))
    assert mb < TMP_MAX_MB, ('本项目临时目录累计 %.0fMB（阈值 %dMB）—— 有工具在漏文件'
                             % (mb, TMP_MAX_MB))


def load_studio_server():
    """import `studio/server.py` 成模块对象，**缓存到 `sys.modules['studio_server']`**。

    为什么要共用这一份：检查项与变异用例必须拿到**同一个模块对象**，否则
    `Mut(srv, 'prune_tmp_audio', …)` 打的是另一个副本 —— 检查照样通过，
    变异测试报"漏了"（本轮实测踩到：检查用 'studio_server_probe'、变异用 'studio_server_mut'）。
    """
    import importlib.util
    if 'studio_server' in sys.modules:
        return sys.modules['studio_server']
    p = os.path.join(ROOT, 'studio', 'server.py')
    if not os.path.isfile(p):
        return None
    spec = importlib.util.spec_from_file_location('studio_server', p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['studio_server'] = mod
    spec.loader.exec_module(mod)
    return mod


@check
def t_studio_cache_prune():
    """**面板音频缓存必须有界**：`studio` 每次试听/搜索/配平/分轨都往
    `%TEMP%\\bgm-studio-audio` 丢文件，而它**以前从不清理**（实测涨到 0.93GB / 12 个 job 目录，
    与"自检临时目录泄漏 7.5GB"是同一类毛病）。现在启动时按预算清（`prune_tmp_audio`）。
    判据（用临时目录做**功能测试**，不启动服务）：
      ① 过期项（最新写入超过 keep_days）必删；② 预算内 + 新鲜的小项**必须留下**（防"一律删光"）；
      ③ 仍超预算时**最旧先删**；④ 容器目录（`stems`/`preview`/`mixfit`）本身不删。
    """
    import shutil
    srv = load_studio_server()
    if srv is None:
        print('        （没有 studio/server.py，跳过）')
        return
    d = tempfile.mkdtemp(dir=TMP, prefix='studio_prune_')
    try:
        def mk(rel, mb, age_d):
            fp = os.path.join(d, rel)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, 'wb') as f:
                f.write(b'0' * int(mb * 1e6))
            t = time.time() - age_d * 86400
            os.utime(fp, (t, t))
        mk(os.path.join('stems', 's1', 'a.ogg'), 3, 10)      # 过期 → 必删
        mk(os.path.join('preview', 'p1', 'b.ogg'), 3, 1)     # 新鲜 + 预算内 → 必留
        mk(os.path.join('search_x', 'c.ogg'), 3, 2)          # 新鲜，但超预算 → 最旧先删
        srv.prune_tmp_audio(root=d, keep_mb=4, keep_days=7, verbose=False)
        left = {os.path.relpath(os.path.join(dp, f), d)
                for dp, _dn, fs in os.walk(d) for f in fs}
        assert not any('stems' in x for x in left), '过期项没删：%s' % left
        assert any('preview' in x for x in left), '新鲜且在预算内的小项被删了（过度清理）：%s' % left
        assert os.path.isdir(os.path.join(d, 'stems')), '容器目录 stems 被删了'
        total = sum(os.path.getsize(os.path.join(d, x)) for x in left) / 1e6
        assert total <= 4.5, '清理后仍超预算：%.1fMB' % total
        print('        缓存清理：过期删 / 新鲜留 / 超预算最旧先删 / 容器不删 —— 4 项判据全过')
    finally:
        shutil.rmtree(d, ignore_errors=True)


@check
def t_melody_motif_rules():
    """**旋律的"音乐性"三层必须真的发生**（动机 / 期待 / 终止式）—— 结构层判据。

    为什么加（用户原话："这个不是很好听"）：旧版逐音从画像直方图抽样 → 每个音都"合理"，
    但整条旋律**没有动机**（听完记不住）、**不满足期待规则**（大跳后继续往同方向跑 = 悬空）、
    **没有终止式**（句尾不落根音/主音 = 没有句读）。这三层是"像人写的"与"像随机采样"的分界，
    而且**频谱类守卫一个都看不见**（频段/响度/宽度可以完全达标）。

    判据（就地生成 8 小节夹具，不渲染不落盘；四个维度都给下限）：
      ① 段内节奏动机重复率 **≤ `MOTIF_MAX_REPEAT`**（**上限**：不许每小节复刻同一 figure
         —— 真实模板中位只有 23%，复刻就是"呆板"）
      ② 大跳后反向率 ≥ `MOTIF_MIN_REVERSE`（Narmour：大跳后要反向）
      ③ 反向里回填率 ≥ `MOTIF_MIN_FILL`（落回跳进区间内）
      ④ 句末收束率 ≥ `MOTIF_MIN_CADENCE`（短语末音是长音且落在该小节和弦音上）
    **判据自证**（两条）：① 把整个动机层关掉（`motif=None`，逐音直方图版）→ ④ 必须掉到
    门以下；② 只关掉**变体层**（`motif['variants'] = [motif]` = 旧版"每小节复刻"）
    → ① 必须回升到门以上。证明这两条判据真能区分"有结构"与"呆板"。
    """
    import melody_gen as M
    import random as _rnd
    chords = {'C': [36, [55, 60, 64, 67, 72]], 'G': [31, [55, 59, 62, 67, 71]],
              'Am': [33, [57, 60, 64, 69, 72]], 'F': [29, [53, 57, 60, 65, 69]]}
    prof = {'range': [60, 84], 'notes_per_bar': 3.2, 'stepwise_pct': 58,
            'onbeat_pct': 45, 'dur16_hist': {'2': 6, '4': 8, '8': 5},
            'onset16_hist': {str(k): v for k, v in
                             ((0, 9), (2, 3), (4, 7), (6, 4), (8, 8), (10, 3),
                              (12, 6), (14, 2))},
            'interval_hist': {'-2': 8, '2': 7, '-1': 3, '1': 3, '0': 2, '3': 2,
                              '-5': 2, '5': 2, '4': 1, '-4': 1},
            'phrase_bars': [4.0, 4.0, 2.0]}
    # 夹具 16 小节（4 个短语）：段末收束率是按"每 4 小节窗口"算的，8 小节只有 2 个样本
    # → 分辨率只有 0/50/100%，判据会被粒度卡住（实测 50% 卡在 55% 门上）。
    prog = ['C', 'G', 'Am', 'F'] * 4
    sec = {'name': 'A', 'bars': 16, 'melody': 'm', 'chords': prog, 'arr': {}}

    # **门本身要有护栏**：这四个门都有真实模板对照值（见常量处的注释），被改成 0 或 0.9
    # 都会让判据变成瞎的；而"门被改了却没人报警"正是变异测试该抓的 —— 实测：删掉自证①
    # 之后，把 `MOTIF_MIN_CADENCE` 改成 0 **已经没有任何断言会失败**（mutation 122/123 报漏），
    # 所以补这一道。区间只围"有依据"的范围，不是紧箍咒。
    for _n, _v, _lo, _hi in (('MOTIF_MIN_REVERSE', MOTIF_MIN_REVERSE, 0.30, 0.90),
                             ('MOTIF_MIN_FILL', MOTIF_MIN_FILL, 0.20, 0.90),
                             ('MOTIF_MIN_CADENCE', MOTIF_MIN_CADENCE, 0.10, 0.60),
                             ('MOTIF_MAX_REPEAT', MOTIF_MAX_REPEAT, 0.20, 0.80)):
        assert _lo <= _v <= _hi, \
            '%s = %.2f 落在有依据的区间 [%.2f, %.2f] 之外（门被改坏了？）' % (_n, _v, _lo, _hi)

    def run(use_motif, seed=11, frozen=False):
        rng = _rnd.Random(seed)
        per = M.persona(prof, rng)
        mf = M._motif_cell(per, rng, bars=1) if use_motif else None
        if frozen and mf is not None:
            mf['variants'] = [mf]          # 关掉变体层 = 每小节复刻同一个 figure（旧形态）
        mel = M.gen_section(sec, chords, prof, rng, M.SCALE_MAJOR, 0, per, 3.0, motif=mf)
        return {'m': mel}
    # 夹具要**含足够多的大跳**才谈得上"期待规则"，而且**单条样本粒度太粗**：
    # 16 小节只有 4~6 个大跳 → 反向率只能取 0/25/50/75/100%，判据会被粒度卡住（实测卡在 50%）。
    # 所以跑 4 个 seed、**取平均**（并把大跳数累加做样本量断言）。
    def avg_metrics(use_motif, seeds=(11, 3, 7, 23), frozen=False):
        rows = [M.motif_stats(run(use_motif, s, frozen), [sec], chords, 0) for s in seeds]
        rows = [r for r in rows if r]
        out = {}
        for k in ('rhythm_repeat', 'leap_reverse_rate', 'gap_fill_rate', 'cadence_rate'):
            out[k] = sum(r.get(k, 0) for r in rows) / max(1, len(rows))
        for k in ('leap_after', 'leap_reverse', 'gap_fill'):      # 计数要**累加**（样本量）
            out[k] = sum(int(r.get(k, 0)) for r in rows)
        return out
    good = avg_metrics(True)
    weak = avg_metrics(False)
    frozen = avg_metrics(True, frozen=True)
    assert good['leap_after'] >= 6, \
        ('夹具里的大跳太少（%d 个）—— 期待规则无从检验，这条检查会空转' % good['leap_after'])
    # 判据自证①：**原写法是错的假设，已改为只打印对照**（2026-09-15 实测）。
    # 原断言是"关掉动机层后 cadence 必须破门"。cadence 门从 0.55 降到有真实模板依据的 0.25
    # 后它立刻失败；改成"四维里至少一维破门"后**仍然失败** —— 实测关掉动机层（逐音直方图版）：
    #   跳后反向 85% / 回填 82% / 收束 38% / 重复 19%**全部达标**。
    # 原因：这四维来自**音程与节奏的分布**，而逐音直方图版同样按画像抽样，自然也有这些性质
    # —— 它们与"动机层有没有工作"**无关**。所以这里只打印对照值，不再断言；
    # 真正能区分"有无结构"的是自证②（关掉变体层 → 重复率必须回升到上限之上）。
    print('        [对照] 关动机版：跳后反向 %.0f%% · 回填 %.0f%% · 收束 %.0f%% · 重复 %.0f%%'
          % (weak['leap_reverse_rate'] * 100, weak['gap_fill_rate'] * 100,
             weak['cadence_rate'] * 100, weak['rhythm_repeat'] * 100))
    # 判据自证②：关掉**变体层**（每小节复刻同一 figure）→ 重复率必须回升到上限之上
    assert frozen['rhythm_repeat'] > MOTIF_MAX_REPEAT, \
        '判据自证失败：每小节复刻同一 figure 的旧形态重复率只有 %.0f%%（上限 %.0f%%）—— ' \
        '说明"复刻"这条判据抓不住呆板' % (frozen['rhythm_repeat'] * 100, MOTIF_MAX_REPEAT * 100)
    bad = []
    for k, lim, label, how in (('leap_reverse_rate', MOTIF_MIN_REVERSE, '跳后反向', 'min'),
                               ('gap_fill_rate', MOTIF_MIN_FILL, '回填', 'min'),
                               ('cadence_rate', MOTIF_MIN_CADENCE, '句末收束', 'min'),
                               ('rhythm_repeat', MOTIF_MAX_REPEAT, '节奏动机重复', 'max')):
        okk = good[k] >= lim if how == 'min' else good[k] <= lim
        if not okk:
            bad.append('%s %.0f%% %s %.0f%%' % (label, good[k] * 100,
                                                '低于' if how == 'min' else '高于', lim * 100))
    print('        动机重复 %.0f%%（复刻版 %.0f%%）· 跳后反向 %.0f%% · 回填 %.0f%% · 收束 %.0f%%（旧版 %.0f%%）'
          % (good['rhythm_repeat'] * 100, frozen['rhythm_repeat'] * 100,
             good['leap_reverse_rate'] * 100, good['gap_fill_rate'] * 100,
             good['cadence_rate'] * 100, weak['cadence_rate'] * 100))
    assert not bad, ('旋律结构层不达标：%s —— 动机/期待/终止式这三层要真的发生'
                     '（melody_gen 的 `--motif` 默认开，别关）' % '；'.join(bad))
    # 已落盘的**动机模式**曲子也一起核（旧曲 mode 不是 motif，不追溯）
    checked = []
    for d in song_dirs():
        p = os.path.join(d, 'song.json')
        try:
            j2 = json.load(open(p, encoding='utf-8'))
        except Exception:                                   # noqa: BLE001
            continue
        mg = j2.get('melody_gen') or {}
        if mg.get('mode') != 'motif':
            continue
        st = M.motif_stats(j2['melody'], j2['sections'], j2['chords'], None)
        if not st:
            continue
        checked.append(os.path.basename(d))
        if st['leap_after'] >= 3 and st['leap_reverse_rate'] < MOTIF_MIN_REVERSE - 0.1:
            bad.append('%s 跳后反向 %.0f%%' % (os.path.basename(d),
                                              st['leap_reverse_rate'] * 100))
        if st['cadence_rate'] < MOTIF_MIN_CADENCE - 0.15:
            bad.append('%s 句末收束 %.0f%%' % (os.path.basename(d), st['cadence_rate'] * 100))
        # **重复率上限只对带变体层的曲目生效**（`melody_gen.variants`）：37 号及更早的
        # motif 曲目是"每小节复刻"的旧形态（实测 59~66%），那是历史数据，不追溯 ——
        # 但形态判据由 `t_melody_form_rules` 分别守（同样带 variants 过滤）。
        if mg.get('variants') and st['rhythm_repeat'] > MOTIF_MAX_REPEAT + 0.10:
            bad.append('%s 节奏动机重复 %.0f%%' % (os.path.basename(d),
                                                  st['rhythm_repeat'] * 100))
    assert not bad, '落盘的动机模式曲目不达标：%s' % '；'.join(bad[:4])
    if checked:
        print('        落盘动机模式曲目 %d 首已核对' % len(checked))


@check
def t_melody_step_bias():
    """`--step-bias`：关掉时＝旧挑法，打开时**选中的必须是候选里级进最高的那条**。

    用户实测（2026-09-14）：同一骨架的 4 条候选"级进 17% → 52% **越来越顺**，202 之后
    两条都比原版好"；而旧挑法 `score = 形状共享×2 + 语言重合 + 复用冲突×0.5` **完全不看
    听感维度** → 同一份画像下会随机挑到跳进多的那条（根因还有 `persona` 里
    `leap = uniform(0.70, 1.40)` 的两倍范围）。

    **为什么判据只能是相对的**：画像的 `stepwise_pct` 是 F0 跟踪的产物（实测 45~89%），
    手写曲实际旋律只有 6~15% —— 两个口径不可比，设绝对门槛会把正常旋律判成不合格
    （我为此连推翻过三次自己的诊断）。所以这里验的是"打开偏好时挑中的是不是候选里
    级进最高的那条"，而不是"级进必须 ≥ 某值"。

    不带 `--avoid` 时去重两项为 0，但 2026-09-15 起 `score` **还含「落点偏离画像」一项**
    （`onset_tvd`，治 43 号 B/Outro 落点集中在 3~4 个格的问题）→ 不再保证挑中"级进最高"那条，
    所以断言改成**单调性**：打开偏好后选中的级进率不得低于关闭时。
    """
    import melody_gen as M
    import subprocess, tempfile
    prof = {'range': [60, 84], 'notes_per_bar': 2.6, 'stepwise_pct': 55, 'onbeat_pct': 45,
            'dur16_hist': {'2': 6, '4': 8, '8': 5},
            'onset16_hist': {str(k): v for k, v in ((0, 9), (4, 7), (8, 8), (12, 6))},
            'interval_hist': {'-2': 8, '2': 7, '-1': 3, '1': 3, '0': 2, '3': 2, '5': 2},
            'phrase_bars': [4.0, 4.0, 2.0]}
    song = {'name': 'sb', 'bpm': 120, 'meter': [4, 4], 'style': 'ballad',
            'chords': {'C': [36, [55, 60, 64, 67]], 'G': [31, [55, 59, 62, 67]],
                       'Am': [33, [57, 60, 64, 69]], 'F': [29, [53, 57, 60, 65]]},
            'melody': {'m': [[0, 0, 1, 60], [0, 2, 1, 62]]},
            'sections': [{'name': 'A', 'bars': 16, 'melody': 'm',
                          'chords': ['C', 'G', 'Am', 'F'] * 4, 'arr': {}}]}
    with tempfile.TemporaryDirectory() as td:
        pf, sf = os.path.join(td, 'p.json'), os.path.join(td, 's.json')
        io.open(pf, 'w', encoding='utf-8').write(json.dumps(prof))
        sw_of = M.stepwise_pct

        def run(bias):
            io.open(sf, 'w', encoding='utf-8').write(json.dumps(song))
            r = subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'melody_gen.py'),
                                sf, pf, '--seed', '11', '--candidates', '4',
                                '--dens', '2.5', '--step-bias', '%.2f' % bias],
                               capture_output=True, text=True, encoding='utf-8',
                               errors='replace', cwd=ROOT)
            assert r.returncode == 0, 'melody_gen 非零退出：%s' % (r.stdout or '')[-300:]
            cands = [int(x) for x in re.findall(r'级进 (\d+)%', r.stdout or '')]
            d = json.load(io.open(sf, encoding='utf-8'))
            return sw_of(d['melody']), cands, d.get('melody_gen') or {}

        off, _c0, _m0 = run(0.0)
        on, cands, meta = run(1.0)
    assert off > 0 and on > 0, 'stepwise_pct 没算出来（off=%.3f on=%.3f）' % (off, on)
    assert cands, 'CLI 没有逐条报候选级进（无法核对"挑的是不是最高那条"）'
    assert on >= off - 0.005, \
        ('打开级进偏好后选中的反而更跳：关 %.0f%% → 开 %.0f%%（候选级进 %s）'
         % (off * 100, on * 100, cands))
    assert abs(meta.get('step_bias', 0) - 1.0) < 1e-9, '生成元数据没留 step_bias 痕迹'

    # **打分公式本身**（`cand_score` 抽出来就是为了这一条能被注入验证）：关掉时与旧式逐字一致，
    # 打开时对"级进更高"的候选给出更低分；且偏好量级不盖过去重（同分候选才会被它改变选择）。
    base = M.cand_score(0.10, 0.80, 1, 0.40, 0.0)
    # 落点项：偏离画像越多 → 分越高（`cand_score` 越小越好）；量级与级进项同级
    assert M.cand_score(0.10, 0.80, 1, 0.40, 0.0, 0.30) > M.cand_score(0.10, 0.80, 1, 0.40, 0.0, 0.10), \
        '落点偏离没有影响打分（`onset_dist` 项失效）'
    # `onset_tvd` 本身：全挤在一个格 → 距离大；四格均匀 → 距离小；空画像 → 0（不误伤）
    _P = {'onset16_hist': {'0': 25, '4': 25, '8': 25, '12': 25}}
    _even = [[0, 0.0, 1.0, 60], [0, 1.0, 1.0, 64], [0, 2.0, 1.0, 62], [0, 3.0, 1.0, 65]]
    _one = [[0, 0.0, 1.0, 60] for _ in range(4)]
    assert M.onset_tvd({'_': _one}, _P) > M.onset_tvd({'_': _even}, _P), 'onset_tvd 方向反了'
    assert M.onset_tvd({'_': _one}, {'onset16_hist': {}}) == 0.0, '空画像应返回 0（不误伤）'
    assert abs(base - (0.10 * 2 + 0.80 + 0.5)) < 1e-12, 'step_bias=0 时打分与旧式不一致'
    hi = M.cand_score(0.10, 0.80, 1, 0.68, 1.0)
    lo = M.cand_score(0.10, 0.80, 1, 0.44, 1.0)
    assert hi < lo, '打开偏好后"级进高"的候选分没有更低（%.3f vs %.3f）' % (hi, lo)
    dedup = M.cand_score(0.90, 0.90, 0, 0.95, 1.0)
    assert dedup > hi, '级进偏好盖过了去重（不该：去重是主要目标）'
    return ('step_bias 0→级进 %.0f%%、1.0→%.0f%%（候选 %s，挑中最高那条）'
            % (off * 100, on * 100, cands))


@check
def t_melody_form_rules():
    """**旋律的节奏形态**：音要铺满小节、每小节不许复刻同一 figure（结构层第二组判据）。

    为什么单开一条（用户反馈："好了一点，但还是不如普通的曲子"）：`melody_motif_rules`
    守的是"有没有动机/期待/终止式"，那四条全绿之下 37 号仍然不好听 —— 实测它的形态是
    小节落点 `0 / 0.5 / 1.5 / 2.0` 拍（**三个音挤在前 2 拍**、之后空 1.5~2 拍），于是
    **每小节都被切成一句**（断句 74 处 / 64 小节），听感"呆板 + 说一句停一下"。

    四个指标的对照值全部来自真实模板旋律 150 首（`refs/midi2/`，口径见
    `melody_gen.form_stats`，**不是拍的**）：

    | 判据 | 真实模板 | 旧版 37 号 | 门 |
    |---|---|---|---|
    | 末落点 ≥8 格的小节占比 | 中位 90% / cheerful 79% | 61% | ≥65% |
    | 小节内最大空档中位 | 1.03 / 1.40 拍 | 2.00 拍 | ≤1.70 |
    | 格 0（第 1 拍）落点占比 | 12.9% / 14.8% | 25.7% | ≤22% |
    | 密度（音/小节） | cheerful 2.63 | 3.41 | 1.8~2.9 |

    **判据自证**：夹具里注入"旧形态"（落点 `(0,2,6)` 挤在前半 + 不带变体层 = 每小节复刻）
    → 至少两条必须破门；否则说明这四条量的是别的东西。
    """
    import melody_gen as M
    import random as _rnd
    chords = {'C': [36, [55, 60, 64, 67, 72]], 'G': [31, [55, 59, 62, 67, 71]],
              'Am': [33, [57, 60, 64, 69, 72]], 'F': [29, [53, 57, 60, 65, 69]]}
    prof = {'range': [60, 84], 'notes_per_bar': 3.2, 'stepwise_pct': 58,
            'onbeat_pct': 45, 'dur16_hist': {'2': 6, '4': 8, '8': 5},
            'onset16_hist': {str(k): v for k, v in
                             ((0, 9), (2, 3), (4, 7), (6, 4), (8, 8), (10, 3),
                              (12, 6), (14, 2))},
            'interval_hist': {'-2': 8, '2': 7, '-1': 3, '1': 3, '0': 2, '3': 2,
                              '-5': 2, '5': 2, '4': 1, '-4': 1},
            'phrase_bars': [4.0, 4.0, 2.0]}
    sec = {'name': 'A', 'bars': 16, 'melody': 'm', 'chords': ['C', 'G', 'Am', 'F'] * 4,
           'arr': {}}
    seeds = (11, 3, 7, 23)

    def gen(seed, cell=None):
        rng = _rnd.Random(seed)
        per = M.persona(prof, rng)
        if cell is not None:
            mf = {'bars': 1, 'onsets': list(cell),
                  'durs': [0.5] * len(cell), 'ivs': [2, -2] * len(cell)}
        else:
            mf = M._motif_cell(per, rng, bars=1)
        mel = {'m': M.gen_section(sec, chords, prof, rng, M.SCALE_MAJOR, 0, per, 2.6,
                                  motif=mf)}
        return M.form_stats(mel, [sec]), [p for (_b, _bt, _d, p) in mel['m']]

    def avg(cell=None):
        pairs = [gen(s, cell) for s in seeds]
        rows = [r for r, _ps in pairs if r]
        assert len(rows) >= 3, '夹具样本太少（%d）—— 这条检查会空转' % len(rows)
        out = {}
        for k in ('last8', 'maxgap_med', 'g0', 'dens', 'peak_pos'):
            v = [r[k] for r in rows if r.get(k) is not None]
            out[k] = (sum(v) / len(v)) if v else None
        # **音域**（半音）取**跨 seed 合并**：音域本来就是"整首曲子用到多宽"（集合性质），
        # 16 小节单样本撑不开（实测单 seed 8~19、4 seed 合并 19），合并才与 64 小节的
        # 真实曲目同口径（38 号单曲 17/17 = 100%）。
        ps = [p for _r, pss in pairs for p in pss]
        out['span_merged'] = (max(ps) - min(ps)) if ps else 0
        return out

    good = avg()
    # **判据自证**：旧形态（三音挤前 2 拍 + 每小节复刻同一 figure）必须被抓
    old = avg(cell=(0, 2, 6))
    broke = []
    if not old['last8'] >= FORM_MIN_LAST8:
        broke.append('末落点')
    if not old['maxgap_med'] <= FORM_MAX_GAP_MED:
        broke.append('空档')
    if not old['g0'] <= FORM_MAX_G0:
        broke.append('格0')
    if not (FORM_DENS[0] <= old['dens'] <= FORM_DENS[1]):
        broke.append('密度')
    if old['peak_pos'] is not None and not (FORM_PEAK[0] <= old['peak_pos'] <= FORM_PEAK[1]):
        broke.append('高点位置')
    assert len(broke) >= 2, \
        ('判据自证失败：旧形态（三音挤前半 + 每小节复刻）只破了 %s —— 这条检查量不到'
         '"铺满小节"这件事；旧形态实测 last8 %.0f%%、空档 %.2f 拍、密度 %.2f'
         % (', '.join(broke) or '0 条', old['last8'] * 100, old['maxgap_med'], old['dens']))
    bad = []
    if good['last8'] < FORM_MIN_LAST8:
        bad.append('末落点≥8 格的小节只有 %.0f%%（门 %.0f%%）'
                   % (good['last8'] * 100, FORM_MIN_LAST8 * 100))
    if good['maxgap_med'] > FORM_MAX_GAP_MED:
        bad.append('小节内最大空档中位 %.2f 拍（门 %.2f）'
                   % (good['maxgap_med'], FORM_MAX_GAP_MED))
    if good['g0'] > FORM_MAX_G0:
        bad.append('格 0 落点占比 %.0f%%（门 %.0f%%）' % (good['g0'] * 100, FORM_MAX_G0 * 100))
    if not (FORM_DENS[0] <= good['dens'] <= FORM_DENS[1]):
        bad.append('密度 %.2f 音/小节（区间 %.1f~%.1f）' % (good['dens'], *FORM_DENS))
    if good['peak_pos'] is not None and \
            not (FORM_PEAK[0] <= good['peak_pos'] <= FORM_PEAK[1]):
        bad.append('句内高点位置 %.2f（应落在 %.2f~%.2f：句子要有"起→高点(2/3)→落"的形状）'
                   % (good['peak_pos'], *FORM_PEAK))
    # **音域**：判据对着画像判（不是拍绝对下限）。夹具 prof 的 range [60,84] = 24 半音，
    # 而 16 小节短样本撑不到 100%（实测 4 seed 合并 79%）→ 夹具用 0.70 门、
    # **落盘曲目用 0.85 门**（64 小节，38 号实测 100%）。旧版收窄 range 后只到 76%。
    want_fix = prof['range'][1] - prof['range'][0]
    if good['span_merged'] < want_fix * FORM_SPAN_RATIO_SHORT:
        bad.append('音域只有 %d 半音（4 seed 合并；夹具画像 %d，门 %.0f%%）'
                   % (good['span_merged'], want_fix, FORM_SPAN_RATIO_SHORT * 100))
    print('        末落点≥8 %.0f%%（旧形态 %.0f%%）· 空档中位 %.2f 拍（旧 %.2f）· '
          '格0 %.0f%%（旧 %.0f%%）· 密度 %.2f · 高处 %.2f（旧 %.2f、目标 0.67）· 音域 %d 半音'
          % (good['last8'] * 100, old['last8'] * 100, good['maxgap_med'], old['maxgap_med'],
             good['g0'] * 100, old['g0'] * 100, good['dens'],
             good['peak_pos'] if good['peak_pos'] is not None else -1,
             old['peak_pos'] if old['peak_pos'] is not None else -1,
             good['span_merged']))
    assert not bad, ('旋律形态不达标：%s —— 音要铺满小节（真实模板末落点≥8 格占 79~90%%）'
                     % '；'.join(bad))
    # 已落盘、**带变体层**的曲目一起核（旧曲没有 variants 标记 = 历史形态，不追溯）
    import melody_profile as MP
    chk = 0
    for d in song_dirs():
        try:
            j3 = json.load(open(os.path.join(d, 'song.json'), encoding='utf-8'))
        except Exception:                                   # noqa: BLE001
            continue
        mg3 = j3.get('melody_gen') or {}
        if not mg3.get('variants'):
            continue
        fs = M.form_stats(j3['melody'], j3['sections'])
        if not fs:
            continue
        chk += 1
        nm = os.path.basename(d)
        if fs['last8'] < FORM_MIN_LAST8 - 0.10:
            bad.append('%s 末落点≥8 只有 %.0f%%' % (nm, fs['last8'] * 100))
        if fs['maxgap_med'] > FORM_MAX_GAP_MED + 0.3:
            bad.append('%s 小节内空档 %.2f 拍' % (nm, fs['maxgap_med']))
        if fs['g0'] > FORM_MAX_G0 + 0.06:
            bad.append('%s 格 0 占比 %.0f%%' % (nm, fs['g0'] * 100))
        if not (FORM_DENS[0] - 0.2 <= fs['dens'] <= FORM_DENS[1] + 0.3):
            bad.append('%s 密度 %.2f' % (nm, fs['dens']))
        # **音域**：对着该曲的画像 range 判（`span ≥ 画像 span × FORM_SPAN_RATIO`）——
        # 旧版收窄 range 后 37 号只用了 13 个半音（画像 17 = 76%），要抓得住。
        want = None
        try:
            _pp = MP.find_profile(mg3.get('profile'))
            _rg = json.load(open(_pp, encoding='utf-8')).get('range') if _pp else None
            if _rg and len(_rg) == 2:
                want = int(_rg[1]) - int(_rg[0])
        except Exception:                                   # noqa: BLE001
            want = None
        if want and want > 0:
            # **音域判据的口径修正**（2026-09-14）：画像的 `range` 是**同主题多首模板的并集**
            # （tender 34 半音），而**单曲**的音域自然更窄 —— 拿"画像 × 0.85"当单曲下限，
            # 会把正常的曲子判红（实测 39 号旋律复用后只剩 3 支旋律、合计 18 半音 = 画像的 53%，
            # 而 18 半音对一个主题完全正常）。现在只要求落在**合理区间**：
            # `FORM_SPAN_MIN`（一个八度，旋律的常识下限）≤ span ≤ 画像 range。
            if fs['span'] < FORM_SPAN_MIN or fs['span'] > want:
                bad.append('%s 音域 %d 半音（合理区间 %d~画像 %d）'
                           % (nm, fs['span'], FORM_SPAN_MIN, want))
    # **判据自证（音域）**：手搓一条只有 2 个半音的旋律 → 必须低于门
    # （旧版 76% 与新版 100% 都在这一条上见分晓）
    _narrow = {'m': [[b, 0.0, 1.0, 70 + (b % 3)] for b in range(16)]}
    _fsn = M.form_stats(_narrow, [sec])
    assert _fsn and _fsn['span'] < 24 * FORM_SPAN_RATIO, \
        ('判据自证失败：只有 %s 个半音的旋律竟然通过了音域判据（画像 24 半音、门 %.0f%%）'
         % ((_fsn or {}).get('span'), FORM_SPAN_RATIO * 100))
    assert not bad, '落盘曲目的旋律形态不达标：%s' % '；'.join(bad[:4])
    if chk:
        print('        落盘带变体层的曲目 %d 首已核对' % chk)


@check
def t_theme_cadence():
    """**主题路径的曲子每段末尾要收束**（属 → 主），不是永远悬在属和弦上。

    依据：旧版把主题包的 4 和弦进行**原样循环整段** → A 段 8 小节停在 `B7`（属功能），
    整段悬着不落地；真实曲式里每 8 小节（乐段）是要合的（用户口径："和声必须收束"）。
    材料来源必须是**模板里的和弦**（用户硬口径：不许自己造）—— `new_song.cadence_pair`
    三层退让（进行里真实的 V→I 相邻对 → 同一进行里的属+主 → `chord_pool` 的 degree 7/0，
    `_stable` 挡掉 sus/dim），三层都拿不到就返回 None（**宁可不收束，也不硬造**）。

    判据（就地 `build_from_theme`，不落盘不渲染）：
      ① 每个主题包**都能拿到收束对**（拿不到 = 这一层对那个主题没生效，要报出来）
      ② 每段最后 2 小节的根音级数 = 主音（0）与属（主音 +7）
      ③ 收束用到的和弦都在该曲 `chords` 字典里（否则渲染时找不到音高）
    **判据自证**：把段末换回"进行原样循环"（旧行为）→ ② 必须判失败。
    """
    import new_song as ns
    import theme_pack as tp
    bad, checked = [], 0

    def _tail_ok(sec):
        ch = sec.get('chords') or []
        if len(ch) < 4:
            return True, ''
        d2, d1 = ns._deg_of(ch[-2], tonic), ns._deg_of(ch[-1], tonic)
        if d1 != 0:
            return False, '末小节 %s 的级数 %s ≠ 主音（没落地）' % (ch[-1], d1)
        if d2 != 7:
            return False, '倒数第 2 小节 %s 的级数 %s ≠ 属（主音+7）' % (ch[-2], d2)
        return True, ''

    themes = sorted(tp.THEMES)
    for th in themes:
        pack = tp.load_pack(th)
        if not pack:
            bad.append('%s: 包读不出来' % th)
            continue
        if not ns.cadence_pair(pack, ns.theme_progressions(pack)):
            bad.append('%s: 拿不到收束对（cadence_pair 返回 None）' % th)
            continue
        d = ns.build_from_theme(pack, 'cad_probe', seed=1, ncand=1)
        tonic = ((pack.get('key') or {}).get('pc') or 0) % 12
        for sec in d['sections']:
            checked += 1
            okk, why = _tail_ok(sec)
            if not okk:
                bad.append('%s/%s: %s' % (th, sec['name'], why))
        miss = [c for sec in d['sections'] for c in sec['chords'] if c not in d['chords']]
        if miss:
            bad.append('%s: 收束和弦没有音高定义 %s' % (th, sorted(set(miss))))
    # 顺序要紧：**先报"哪个主题没收束"，再报"夹具空转"** —— 反过来的话，注入
    # "关掉 cadence_pair"时先撞空转断言，信息变成"夹具太少（0 段）"，指不到真原因。
    assert not bad, '主题曲目没有收束：%s' % '；'.join(bad[:4])
    assert checked >= 30, '夹具太少（%d 段）—— 这条检查会空转' % checked
    # **判据自证**：旧行为（进行原样循环、段末停在属和弦）必须被判为"没收束"
    pack = tp.load_pack('cheerful')
    base = ns.theme_progressions(pack)[0]
    tonic = ((pack.get('key') or {}).get('pc') or 0) % 12
    old_sec = {'name': 'A', 'bars': 8, 'chords': [base[j % len(base)] for j in range(8)]}
    okk, _why = _tail_ok(old_sec)
    assert not okk, \
        ('判据自证失败：旧行为（进行原样循环、段末停在 %s）竟然判为已收束'
         % old_sec['chords'][-1])
    print('        %d 个主题包 / %d 个段落：段末全部属→主收束' % (len(themes), checked))


@check
def t_melody_dyn_optin():
    """**旋律力度曲线**：opt-in、真生效、关着时老曲逐字节不变（`patterns.melody_dyn`）。

    依据（用户口径："旋律力度只有 62/96 两档，要加乐句级力度曲线（渐强/句末收）"）：
    旋律力度原先是**硬编码两档**（主层 96 / 低八度加厚层 62），整条旋律一个力度
    → 没有"唱"的表情。但**必须 opt-in**：老曲的 `song.json` 里没有这个键，引擎一旦
    默认开就会改变所有老曲的 MIDI 字节（全库都得重渲染）。

    判据（就地编配 `build_events`，不渲染不落盘）：
      ① **缺省 = 老行为**：不含该键时 Melody 轨只有 1 个力度值（96）——
         `mel_octave` 缺省 **0**（不加低八度层；实测真实模板 cheerful 10 首里 7 首叠加率为 0），
         显式 `mel_octave: 1.0` 时才多出 62 那一档（低八度加厚层仍在，只是要显式开）
      ② 显式 `false` 与缺省**逐字节相同**，且两次编配结果相同（opt-in 语义 + 无隐藏随机）
      ③ **打开 = 有曲线**：力度取值 ≥ 6 档
    **判据自证**：把 `mel_dyn_env` 换成恒返回 1.0 → ③ 必须掉回缺省的 1 档（判据抓得到）。
    """
    import song_engine as SE
    base = {
        'name': 'dyn_probe', 'bpm': 120.0, 'meter': [4, 4], 'style': 'daily',
        'chords': {'C': [36, [55, 60, 64, 67]], 'G': [31, [55, 59, 62, 67]],
                   'Am': [33, [57, 60, 64, 69]], 'F': [29, [53, 57, 60, 65]]},
        # 夹具要点：包络按"句内位置 prog"取值，采样点越多档数越多。
        # 每小节只有一个 beat 0 的音时，4 小节的 prog 只取 0/0.25/0.5/0.75 四个点，
        # `int(round(96*mv))` 后只落 3 档 —— 量不出判据 ③ 要的"乐句级曲线"。
        # 所以用 **4 小节 × 每拍一个音**（16 个采样点）。
        'melody': {'m': [[b, bt, 1.0, 72]
                         for b in range(4) for bt in (0.0, 1.0, 2.0, 3.0)]},
        'sections': [{'name': 'A', 'bars': 4, 'chords': ['C', 'G', 'Am', 'F'],
                      'melody': 'm', 'arr': {'bass': True, 'piano': True, 'perc': 1}}],
    }
    tmp = os.path.join(TMP, 'dyn_probe.json')

    def vels(marker, extra=None):
        d = json.loads(json.dumps(base))          # 深拷贝（build_events 会填 programs/mix）
        pat = dict(extra or {})
        if marker is not None:
            pat['melody_dyn'] = marker
        if pat:
            d['patterns'] = pat
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f)
        ev, _bars = SE.build_events(SE.load(tmp))
        return [v for (_t, _dd, _m, v) in ev['Melody']]

    off = vels(None)
    assert len(off) >= 8, '夹具没编出旋律（%d 个音）—— 这条检查会空转' % len(off)
    off2, off3, on = vels(False), vels(None), vels(True)
    assert set(off) == {96}, \
        ('缺省（melody_dyn 关）时旋律力度应只有 1 档 96（`mel_octave` 缺省 0 = 不加低八度层），'
         '实测 %d 档：%s' % (len(set(off)), sorted(set(off))))
    oct_on = vels(None, {'mel_octave': 1.0})
    assert set(oct_on) == {96, 62}, \
        ('显式 mel_octave=1.0 时应有 2 档（96 主层 / 62 低八度加厚层），实测 %s'
         % sorted(set(oct_on)))
    assert off == off2, '显式 melody_dyn=false 与缺省必须逐字节相同（opt-in 语义）'
    assert off == off3, '两次编配结果不同（存在隐藏状态/随机性）'
    nv = len(set(on))
    assert nv >= 6, '开了 melody_dyn 也只有 %d 档力度（应有乐句级曲线）' % nv
    # **判据自证**：关掉包络函数 → 必须掉回 2 档
    _old = SE.mel_dyn_env
    try:
        SE.mel_dyn_env = lambda *a: 1.0
        killed = sorted(set(vels(True)))
    finally:
        SE.mel_dyn_env = _old
    assert len(killed) == 1, \
        ('判据自证失败：把 mel_dyn_env 换成恒等函数后力度仍有 %d 档 —— 这条判据量不到曲线'
         % len(killed))
    print('        旋律力度档数：缺省 %d → 显式叠低八度 %d → 打开 %d（%d~%d）'
          % (len(set(off)), len(set(oct_on)), nv, min(on), max(on)))


@check
def t_accompaniment_harmony():
    """**伴奏必须弹和弦音**（`TR_SHIFT` 只许纯八度）+ 旋律与伴奏的**纵向配合**。

    为什么加（用户听完 38 号："主旋律和伴奏没有很好配合"）：`song_engine.TR_SHIFT` 原来是
    `Pad −5 / Hook −5 / Piano +4 / Strings −3 / Arp +3 / Melody +7`，注释写着
    "只改 MIDI 音高、不动和声：整轨同移不改变和弦内的音程关系" —— **那是错的推理**：
    轨内音程关系确实不变，但**与和弦的关系全变了**。实测各轨"音的 pc 落在当小节和弦音集里"
    的比例：Bass **98.6%**（无移调，作对照）· Hook **15.1%** · Arp 31.6% · Piano 37.5% ·
    Strings 43.2% —— 也就是**伴奏有 60~85% 的音是和弦外音**。
    后果（同口径探针，真实模板 80 首作对照）：旋律与同拍伴奏的**半音冲突 45%**（真实 6%）、
    **旋律音区反被伴奏盖住**（"旋律在下"59%，真实 1%）。用户听出来的"配合不好"就是这两条。

    判据（就地编配 `build_events`，不渲染不落盘）：
      ① `TR_SHIFT` 的每一项**必须是 12 的倍数**（纯八度；非八度 = 改音级）
      ② 伴奏轨（Hook/Piano/Arp/Strings/Pad）的**和弦贴合率 ≥ 95%**
      ③ 旋律与同拍伴奏最高音的**音区分离中位 ≥ 6 半音**（真实模板 +12）
    **判据自证**：把 `TR_SHIFT` 换回旧的半音偏移 → ①②③ 必须同时失败。
    """
    import bisect
    import song_engine as SE
    off = {k: v for k, v in SE.TR_SHIFT.items() if v % 12}
    assert not off, ('TR_SHIFT 只允许纯八度（12 的倍数）—— 非八度移调会改变音级、'
                     '把整条伴奏轨移到和弦外：%s' % off)
    ACC = ('Hook', 'Piano', 'Arp', 'Strings', 'Pad')
    fit, sep, checked = [], [], 0
    for d in songs_or_fail():
        try:
            data = SE.load(os.path.join(d, 'song.json'))
        except SystemExit:
            continue
        ev = SE.build_events(data)
        if not hasattr(ev, 'items'):
            ev = ev[0]
        bar_ch = []
        for sec in data['sections']:
            bar_ch += list(sec['chords'])
        if not bar_ch:
            continue
        checked += 1
        B = SE.bar_beats(data)          # ⚠ 3/4 曲目的一小节是 **3 拍**，不能写死 4
                                        # （写死时 29_meter34_waltz 的贴合率被算成 49%）

        def tset(bar):
            cn = bar_ch[min(int(bar) % len(bar_ch), len(bar_ch) - 1)]
            e = data['chords'].get(cn)
            return {x % 12 for x in e[1]} if e else set()
        for tr in ACC:                                     # ② 伴奏和弦贴合
            notes = [n for n in ev.get(tr, []) if n[3] > 0]
            if len(notes) < 40:
                continue
            ok = sum(1 for (t, _dd, m, _v) in notes if m % 12 in tset(t // B))
            fit.append((os.path.basename(d), tr, ok / len(notes)))
        # ③ 音区分离：旋律音 − 同拍（±0.125 拍）伴奏最高音
        acc, _m = [], {}
        for tr in ACC:
            for (t, _dd, m, _v) in ev.get(tr, []):
                if _v > 0:
                    acc.append((round(t, 4), m))
        acc.sort()
        aks = [x[0] for x in acc]
        for (t, _dd, m, _v) in ev.get('Melody', []):
            i = bisect.bisect_left(aks, t - 0.125)
            hi = None
            while i < len(aks) and aks[i] <= t + 0.125:
                hi = acc[i][1] if hi is None else max(hi, acc[i][1])
                i += 1
            if hi is not None:
                sep.append(m - hi)
    assert checked >= 5, '带和弦的曲目太少（%d）—— 这条检查会空转' % checked
    assert len(sep) >= 200, '音区分离的样本太少（%d）—— 这条检查会空转' % len(sep)
    fmin = min(f for _n, _t, f in fit) if fit else 1.0
    bad = []
    for (nm, tr, f) in fit:
        if f < 0.95:
            bad.append('%s/%s 和弦贴合只有 %.0f%%' % (nm, tr, f * 100))
    sep.sort()
    sep_med = sep[len(sep) // 2]
    if sep_med < 6:
        bad.append('音区分离中位 %+d 半音（门 +6；真实模板 +12）—— 旋律被伴奏盖住' % sep_med)
    low = sum(1 for x in sep if x < 0) / len(sep)
    if low > 0.15:
        bad.append('旋律有 %.0f%% 的音落在伴奏最高音之下（真实 1%%）' % (low * 100))
    print('        伴奏和弦贴合最低 %.0f%%（%d 轨）· 音区分离中位 %+d 半音 · 旋律在下 %.0f%%'
          % (fmin * 100, len(fit), sep_med, low * 100))
    # **判据自证**：换回旧的半音偏移 → 贴合率必须崩（旧表实测 15~43%）
    _old = SE.TR_SHIFT
    try:
        SE.TR_SHIFT = {'Pad': -5, 'Hook': -5, 'Piano': 4, 'Strings': -3, 'Arp': 3,
                       'Melody': 7}
        old_min = 1.0
        for d in songs_or_fail()[:3]:
            try:
                data = SE.load(os.path.join(d, 'song.json'))
            except SystemExit:
                continue
            ev = SE.build_events(data)
            if not hasattr(ev, 'items'):
                ev = ev[0]
            bar_ch = []
            for sec in data['sections']:
                bar_ch += list(sec['chords'])
            _B = SE.bar_beats(data)
            for tr in ACC:
                notes = [n for n in ev.get(tr, []) if n[3] > 0]
                if len(notes) < 40 or not bar_ch:
                    continue
                ok = 0
                for (t, _dd, m, _v) in notes:
                    cn = bar_ch[min(int(t // _B), len(bar_ch) - 1)]
                    e = data['chords'].get(cn)
                    ok += bool(e) and (m % 12 in {x % 12 for x in e[1]})
                old_min = min(old_min, ok / len(notes))
    finally:
        SE.TR_SHIFT = _old
    assert old_min < 0.95, \
        ('判据自证失败：换回旧的半音偏移表后，伴奏和弦贴合仍有 %.0f%% —— 这条判据量不到'
         '"移调破坏和声"' % (old_min * 100))
    assert not bad, '旋律与伴奏的配合不达标：%s' % '；'.join(bad[:4])


@check
def t_melody_space():
    """**给旋律留空间**（`patterns.space`，opt-in）：伴奏减薄、旋律"独唱率"回升。

    为什么加（用户："不好听，主旋律和伴奏没有很好配合"）：网上编曲手法的第一条就是
    "creating space for a melody"（伴奏在旋律陈述时减薄、在长音/休止时填充）。同口径探针
    （真实侧 = `refs/midi2/` 的 80 首模板）量出**伴奏起音密度**：真实 **19.8 音/小节**，
    我们 **45.0**（2.3 倍）—— 真实模板非鼓轨每轨中位只有 3.6 音/小节，而我们是
    Hook 14.7 / Bass 12.1 / Arp 8.0 / Piano 7.2。后果：旋律的**"独唱率"只有 15%**
    （真实 **43%**）—— 旋律一开口伴奏永远在同时响，听感"糊、分不出主次"。

    判据（就地编配**同一份夹具的开关两版**，不渲染不落盘）：
      ① 开 `space` 后伴奏起音密度 ≤ 关时的 **85%**（确实减薄了，不是配置写了没生效）
      ② 开 `space` 后旋律独唱率（落点处没有伴奏起音的比例）**高于**关时
      ③ **无鼓段落（`perc: 0`）不减薄** —— 那里伴奏本来就稀，再减撑不住织体
        （实测 rehearsal 的"无打击乐段落"夹具调参误差卡在 3.5、EQ 补不回）
    **判据自证**：把 `space_on` 换成恒 False（= 这一层失效）→ ①② 的差异必须消失。
    """
    import bisect
    import song_engine as SE
    base = {'name': 'sp', 'bpm': 120.0, 'meter': [4, 4], 'style': 'daily',
            'chords': {'C': [36, [55, 60, 64, 67, 72]]},
            'melody': {'m': [x for b in range(8)
                             for x in ([b, 0.0, 1.0, 72], [b, 2.0, 1.0, 76])]},
            'sections': [{'name': 'A', 'bars': 8, 'chords': ['C'] * 8, 'melody': 'm',
                          'arr': {'uku': True, 'piano': True, 'ep': True, 'arp': True,
                                  'bass': True, 'strings': True, 'pad': True, 'perc': 1}}]}
    tmp = os.path.join(TMP, 'space_probe.json')

    def stat(space, perc=1):
        d = json.loads(json.dumps(base))
        if space:
            d['patterns'] = {'space': True}
        if perc == 0:
            for s in d['sections']:
                s['arr'] = dict(s['arr'], perc=0)
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f)
        ev, _n = SE.build_events(SE.load(tmp))
        bars = float(sum(s['bars'] for s in d['sections']))
        acc = [(t, m) for k, v in ev.items() if k not in ('Melody', 'Perc')
               for (t, _dd, m, _vv) in v]
        mel = [(t, m) for (t, _dd, m, _vv) in ev.get('Melody', [])]
        assert len(mel) >= 8, '夹具没编出旋律（%d 个音）—— 这条检查会空转' % len(mel)
        starts = sorted({round(t, 4) for (t, _m) in acc})
        cnt, solo = [], 0
        for (t, _m) in mel:
            i = bisect.bisect_left(starts, t - 0.125)
            c = 0
            while i < len(starts) and starts[i] <= t + 0.125:
                c += sum(1 for (tt, _mm) in acc if abs(tt - starts[i]) < 1e-9)
                i += 1
            cnt.append(c)
            if c == 0:
                solo += 1
        return len(acc) / bars, (sum(cnt) / len(cnt)), solo / len(mel)

    d_off, a_off, s_off = stat(False)
    d_on, a_on, s_on = stat(True)
    d_noperc_on, _, _ = stat(True, perc=0)
    d_noperc_off, _, _ = stat(False, perc=0)
    bad = []
    if not (d_on <= d_off * 0.85):
        bad.append('开 space 后伴奏密度 %.1f 没有明显低于关时 %.1f（配置写了没生效？）'
                   % (d_on, d_off))
    # ② **旋律起音处的伴奏音数**（这才是"留空间"的直接度量）：**独唱率**不用作判据 ——
    # 它要求伴奏放弃整个八分网格（真实模板靠"旋律节奏自由"实现），在 16 分格全覆盖的
    # 编配里恒为 0，会变成一个永远红灯的假判据。
    if not (a_on < a_off):
        bad.append('开 space 后旋律起音处的伴奏音数 %.2f 没有低于关时 %.2f' % (a_on, a_off))
    if not (d_noperc_on >= d_noperc_off * 0.95):
        bad.append('无鼓段落不该减薄（开 %.1f vs 关 %.1f）—— 那里本来就稀，减了撑不住织体'
                   % (d_noperc_on, d_noperc_off))
    # **判据自证**：把开关函数换成恒 False → 上面两条差异必须消失
    _old = SE.space_on
    try:
        SE.space_on = lambda pat: False
        d_kill, a_kill, _s = stat(True)
    finally:
        SE.space_on = _old
    assert abs(d_kill - d_off) < 1e-6 and abs(a_kill - a_off) < 1e-6, \
        ('判据自证失败：`space_on` 失效后密度/同起音数仍与关时不同（%.1f/%.2f vs %.1f/%.2f）'
         % (d_kill, a_kill, d_off, a_off))
    print('        伴奏密度 %.1f → %.1f 音/小节（%.0f%%；真实模板 19.8）· '
          '旋律起音处伴奏音数 %.2f → %.2f（独唱率 %.0f%%，真实 43%% 但需伴奏放弃八分网格）'
          % (d_off, d_on, 100.0 * d_on / d_off, a_off, a_on, s_on * 100))
    assert not bad, '给旋律留空间这一层不达标：%s' % '；'.join(bad)


@check
def t_theme_arrangement_dynamic():
    """**段落编配要跟张力走**，不是按"第几段"机械轮换（替换旧的 `level = i % 3`）。

    旧行为：`level = i % 3` —— 哪一段厚由"它在第几段"决定，与曲式无关（主歌第 3 段会比
    副歌第 2 段厚，纯属位置巧合）。

    真值来源（**试了两条，只留成立的那条**）：
      · ✅ **混音目标画像的段间能量块**（`mix_target.energy_curve_db`，来自**真实音频**的
        每 8 小节响度起伏）：高于均值的段开第二梯队（strings/pad/glock/ep/arp）。
      · ❌ **MIDI 模板的编配密度曲线**：实测真实模板"每 8 小节密度"的**相对起伏中位 0.00**
        （四分位 0.00~0.57，即一半以上完全平）—— MIDI 模板库在编配层是**扁平的**
        （多是钢琴/小编制），拿它当"张力"的真值不成立。**参照系选错，比没有参照更糟。**

    判据（就地 `build_from_theme`，不落盘）：
      ① 每个主题包：段落的编配层次（`arr` 里第二梯队的开启情况）与**能量曲线的排序同向**
         （高能量段开、低能量段关），相关系数 > 0.5 或"曲线太平 → 全部关闭"
      ② 曲线起伏 <1dB 时**不许**造出层次差异
    **判据自证**：把 `arr_level` 换回 `i % 3` → ① 必须失败（机械轮换与能量不相关）。
    """
    import new_song as ns
    import theme_pack as tp

    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos
        return r

    def rho(a, b):
        """排序相关（Spearman）；n 很小时分辨率低，但用来区分 1.0 与 0.3 足够。"""
        n = len(a)
        if n < 3:
            return None
        ra, rb = rank(a), rank(b)
        d2 = sum((ra[i] - rb[i]) ** 2 for i in range(n))
        return 1.0 - 6.0 * d2 / (n * (n * n - 1))

    bad, checked, rhos = [], 0, []
    TIER = ('strings', 'pad', 'glock', 'ep', 'arp')
    for th in sorted(tp.THEMES):
        pack = tp.load_pack(th)
        if not pack:
            continue
        try:
            d = ns.build_from_theme(pack, 'arr_probe', seed=1, ncand=1)
        except SystemExit:
            continue
        eused = (d.get('theme') or {}).get('energy_curve_db') or []
        secs = d['sections']
        if not eused:
            # **曲线太平 → 整首不写**（`energy_mix` 的 ENERGY_MIN_DB 口径：目标起伏 <0.5dB
            # 就不硬造对比）—— 这是设计，不是错；那几个主题本来就没有段间张力可言。
            continue
        if len(eused) != len(secs):
            bad.append('%s: 能量曲线长度 %d ≠ 段数 %d' % (th, len(eused), len(secs)))
            continue
        checked += 1
        thick = [sum(1 for k in TIER if s['arr'].get(k)) for s in secs]
        spread = max(eused) - min(eused)
        if spread < 1.0:
            # 曲线平坦 → `arr_level` 走**曲式角色兜底**（非 A 段厚、A 段薄）。
            # ⚠ 旧版这里要求"全平"（不造假变化），但实测那会让 `tender` 的 **8 段编配
            # 一模一样** —— 而它正是用户说"怎么感觉你写的好多部分都是一样的"的那首。
            if max(thick) == min(thick) and max(thick) < len(TIER):
                bad.append('%s: 目标曲线平坦（%.1fdB）时编配也**全平** %s —— 整首 8 段一个样，'
                           '既没跟能量走、也没跟曲式走' % (th, spread, thick))
            continue
        if max(thick) == min(thick):
            # 全都一样：**只有当"还有调节余地"时才算出错** —— 若每段都已经把第二梯队全开，
            # `arr_level` 本来就无处发力（实测 gorgeous：主题包的 `arr_on` 要求全开 →
            # `[5]*8`，这不是"没跟张力走"，是"没有更厚的档可加"）。
            if max(thick) >= len(TIER):
                continue
            bad.append('%s: 能量起伏 %.1fdB，但每段编配层次完全一样 %s（没跟张力走）'
                       % (th, spread, thick))
            continue
        r = rho(eused, thick)
        if r is not None:
            rhos.append(r)
    # 门取 5：15 个主题里约一半的**聚合目标曲线本身平坦**（`ENERGY_MIN_DB` 的设计 ——
    # 目标没对比就不硬造），能判的只有 8 个左右。门太高会让这条检查在"聚合后曲线更平"
    # 时误报"空转"。
    assert checked >= 5, '夹具太少（%d 个主题）—— 这条检查会空转' % checked
    assert rhos, '没有"能量有起伏"的主题包 —— 这条检查会空转'
    avg_rho = sum(rhos) / len(rhos)
    # ① **机制级**（主判据）：`arr_level` 必须是能量的**单调不减**函数、且曲线平缓时全 0。
    # ⚠ 别只用"相关系数"当判据：`i % 3` 与**周期性的能量曲线**（8 段循环）会撞出
    # **伪相关 0.64**（实测），判别力不够；单调性测试对机械轮换是**立刻失败**的
    # （`i%3` 在 [-3,-1,0,1,3] 上给出 [0,1,2,0,1] —— 根本不单调）。
    seq = [-3.0, -1.0, 0.0, 1.0, 3.0]
    lv = [ns.arr_level(seq, i) for i in range(len(seq))]
    assert lv == sorted(lv), '`arr_level` 不是能量的单调不减函数：%s ← %s' % (lv, seq)
    assert lv[0] == 0 and lv[-1] == 1, '能量最低/最高的段必须分别关/开第二梯队：%s' % lv
    assert ns.arr_level([0.2, 0.3, 0.25, 0.1], 1) == 0, \
        '能量曲线只有 0.2dB 起伏时不许造出梯队差异（不造假变化）'
    flat = [ns.arr_level([0.0] * 8, i) for i in range(8)]
    assert set(flat) == {0}, '全平的曲线必须全是基础编制：%s' % flat
    # ② 端到端（诊断）：段落厚度 vs 能量的排序相关；机械轮换版作对照
    _old = ns.arr_level
    try:
        ns.arr_level = lambda eused, i, role=None: i % 3
        kill = []
        for th in sorted(tp.THEMES):
            pack = tp.load_pack(th)
            if not pack:
                continue
            d = ns.build_from_theme(pack, 'arr_probe', seed=1, ncand=1)
            eused = (d.get('theme') or {}).get('energy_curve_db') or []
            secs = d['sections']
            if len(eused) != len(secs) or max(eused) - min(eused) < 1.0:
                continue
            thick = [sum(1 for k in TIER if s['arr'].get(k)) for s in secs]
            r = rho(eused, thick)
            if r is not None:
                kill.append(r)
    finally:
        ns.arr_level = _old
    kill_rho = (sum(kill) / len(kill)) if kill else 0.0
    print('        %d 个主题包：编配厚度 vs 能量曲线排序相关 %.2f（机械轮换版 %.2f）'
          % (checked, avg_rho, kill_rho))
    assert not bad, '编配没有跟张力走：%s' % '；'.join(bad[:4])


# 段间**编配同质化**的上限（用户听感"好多部分都是一样的" → 量出来的真值）：
#   本库旧版段落间乐器组合 Jaccard **中位 0.86**（36~39 号全是 0.86，18/20/24/25/27 号是 1.00
#   = 整曲同一套乐器），bass 100% / piano 98% / perc 97% 段落全在场。
#   对照 13 首真实商业 BGM 的分段画像（`refs/sections/*.json`，以 160–315Hz 为基准的相对谱）：
#   高频 5–10k 段间起伏中位 **7.8dB**、10–18k **9.1dB**、中频 7.8dB；我们只有 0.5/0.5/3.8dB。
# 门取 0.78：角色化编制实测 0.60~0.76（旧值 0.86）—— 与旧行为留出 0.08 的判别余量；
# 0.76 那档是 waltz（它的段落结构 A/B 交替最密，且曲线把最后两个 A 段抬了一档）。
ARR_JACCARD_MAX = 0.78
ROLE_ALWAYS = ('bass', 'piano')      # 基础层（低频唯一来源 + 主奏音色）必须每段都在


@check
def t_arr_role_variety():
    """**段落角色 → 编制**：段间不许"同一套乐器从头铺到尾"。

    用户听感"怎么感觉你写的好多部分都是一样的"——量下来两头都反了：旋律那侧**过头**
    （每段一支新旋律、没有记忆点，已由 `role_melody_name` 修），编配这侧**不足**：
    231 个段落里 bass 100% / piano 98% / perc 97% 在场，段间 Jaccard 中位 0.86。

    判据（机制级 + 端到端，两层）：
      ① `arr_by_role` 的性质：BASE 每段都在 · perc 在引子/尾声为 0 · 同角色段落编制相同 ·
         全曲至少一段有 perc（兜底，否则 5–18kHz 塌）· 角色不同的段编制**不相等**
      ② 端到端：15 个主题包各建一次 → 每首的段间 Jaccard 中位 ≤ `ARR_JACCARD_MAX`，
         且 `strings`（中高频厚度）**不是**每段都在（旧版 100% 在场）
    变异：把 `arr_by_role` 换成"原样返回"（= 旧行为）→ ② 必须失败。
    """
    import new_song as ns
    import song_engine as se

    INSTR = ('uku', 'piano', 'ep', 'strings', 'glock', 'bass', 'pad', 'arp',
             'perc', 'harmony', 'shimmer')

    def median(v):
        v = sorted(v)
        n = len(v)
        return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0

    def sig(a):
        return frozenset(k for k in INSTR if a.get(k))

    def jac_med(secs):
        ss = [sig(s.get('arr') or {}) for s in secs]
        v = [len(a & b) / len(a | b) for i, a in enumerate(ss) for b in ss[i + 1:]]
        return (median(v) if v else 1.0), ss

    # ① 机制级
    names = ['Intro', 'A', 'A2', 'B', 'A3', 'C', 'B2', 'Outro']
    roles = [se.role_of_section(x) for x in names]
    assert roles == ['intro', 'A', 'A', 'B', 'A', 'C', 'B', 'outro'], \
        '段落名 → 角色映射错：%s' % list(zip(names, roles))
    base = [{'bass': True, 'piano': True, 'uku': True, 'perc': 1}] * len(names)
    out = se.arr_by_role(base, roles, energy=None, tier=1)
    for i, a in enumerate(out):
        assert all(a.get(k) for k in ROLE_ALWAYS), \
            '第 %d 段（%s）缺基础层（低频/主奏会空）：%s' % (i, names[i], a)
    # **引子可以有打击，但必须渐入**（2026-09-15 按真值改：cheerful 7/10 首引子有鼓，
    # b1–b2 静、b3–b4 进来）；**尾声保持 0**（sorrow 池鼓点中位 0）。
    assert out[0]['perc'] == 1 and out[0].get('perc_in') == 2, \
        ('引子应 perc=1 且带 perc_in=2（渐入）：%s' % out[0])
    assert out[-1]['perc'] == 0, \
        '尾声不许上打击（"留白收尾"）：%s' % out[-1].get('perc')
    assert any(a.get('perc') for a in out), '全曲没有任何一段有打击 → 5–18kHz 会塌'
    assert sig(out[1]) == sig(out[2]) == sig(out[4]), \
        '同角色（A/A2/A3）必须拿到同一套编制：%s' % [sorted(sig(a)) for a in out]
    assert sig(out[3]) != sig(out[6]), \
        '两次副歌的编制不许一模一样（副歌按次序升级）：%s / %s' % (sorted(sig(out[3])), sorted(sig(out[6])))
    assert sig(out[1]) != sig(out[3]) and sig(out[1]) != sig(out[5]), \
        '主歌与副歌/桥段的编制不许相同 —— 那正是"段落换了却听不出来"'
    # **判据自证**：引子的 perc 必须是**被强制**成 1 的（档 0 自己是 0）——
    # 否则那条断言只是碰巧成立，量不到"引子渐入"这件事。
    _p0 = se.ARR_PACKS[se.arr_pack_idx('intro')].get('perc')
    assert _p0 == 0 and out[0]['perc'] == 1, \
        ('判据自证失败：档 0 的 perc=%s、引子 perc=%s —— 引子没有走"强制 1 + perc_in"这条'
         % (_p0, out[0]['perc']))
    # **引擎写进 arr 的键必须在 `ARR_KEYS` 里**（白名单与实现脱节的守卫，
    # 见 `ARR_KEYS` 上方注释：加 `perc_in` 时漏过一次）
    _extra = set()
    for _a in out:
        _extra |= set(_a) - set(se.ARR_KEYS) - {'vel', 'glock_all'}
    assert not _extra, \
        'arr_by_role 写了 ARR_KEYS 之外的键：%s —— 请同步 ARR_KEYS' % sorted(_extra)
    solo = se.arr_by_role([{'bass': True, 'piano': True}] * 3,
                          ['intro', 'intro', 'outro'], energy=None, tier=1)
    assert any(a.get('perc') for a in solo), '引子/尾声为主的夹具下兜底没生效（全曲无打击）'
    # **削薄（`sparse`，2026-09-15 新增）**：欢快/舞曲类主题用它 —— 实测 CLAP happy +72%
    # （见 `song_engine.arr_sparse` 的实测记录）。断言三件事：
    #   ① 真的关掉 pad/strings/ep；② **glock 必须留着**（它承载段间亮色差异 ——
    #   一起关掉会让段间 Jaccard 从 0.67 涨到 0.80，超 0.78 的门，battle/cheerful/
    #   neon/retro 四首当场报红）；③ 基础层不能被动。
    sp = se.arr_by_role(base, roles, energy=None, tier=1, sparse=True)
    for _i, _a in enumerate(sp):
        assert not any(_a.get(k) for k in ('pad', 'strings', 'ep')), \
            'sparse 没关掉 pad/strings/ep（第 %d 段）：%s' % (_i, _a)
        assert all(_a.get(k) for k in ROLE_ALWAYS), \
            'sparse 把基础层也削了（第 %d 段）：%s' % (_i, _a)
    assert any(a.get('glock') for a in sp), \
        'sparse 把 glock 也关了 —— 那会连带抹平段间编制差异（实测 Jaccard 0.67→0.80 超门）'
    # **判据自证**：不传 sparse 时必须仍有段开 pad/strings —— 否则上面那条断言
    # 只是"本来就没有"，量不到"削薄"这件事。
    _plain = se.arr_by_role(base, roles, energy=None, tier=1)
    assert any(a.get('strings') or a.get('pad') for a in _plain), \
        '不传 sparse 时也没有任何段开 strings/pad —— 上一条断言量不到"削薄"'

    # ② 端到端
    import theme_pack as tp
    bad, checked, meds = [], 0, []
    for th in sorted(tp.THEMES):
        pack = tp.load_pack(th)
        if not pack:
            continue
        try:
            d = ns.build_from_theme(pack, 'arr_role_probe', seed=1, ncand=1)
        except SystemExit:
            continue
        secs = d.get('sections') or []
        if len(secs) < 4:
            continue
        checked += 1
        m, ss = jac_med(secs)
        meds.append(m)
        if m > ARR_JACCARD_MAX:
            bad.append('%s: 段间编配 Jaccard 中位 %.2f > %.2f（段落换了编制没换）'
                       % (th, m, ARR_JACCARD_MAX))
        if all('strings' in x for x in ss):
            bad.append('%s: strings 每段都在（中高频厚度没有起伏）' % th)
        if not any((s.get('arr') or {}).get('perc') for s in secs):
            bad.append('%s: 全曲没有任何一段开打击 → 5–18kHz 会塌' % th)
    assert checked >= 8, '夹具太少（%d 个主题包）—— 这条检查会空转' % checked
    print('        %d 个主题包：段间编配 Jaccard 中位 %.2f（上限 %.2f）'
          % (checked, median(meds), ARR_JACCARD_MAX))
    assert not bad, '段落编制还是"一套乐器铺到底"：%s' % '；'.join(bad[:4])

    # 变异自证：还原成旧行为（原样返回）→ 必须失败
    _old = se.arr_by_role
    try:
        se.arr_by_role = lambda base, roles, energy=None, tier=1, sparse=False: \
            [dict(b) for b in base]
        pack = tp.load_pack('cheerful')
        d = ns.build_from_theme(pack, 'arr_role_probe', seed=1, ncand=1)
        m, _ss = jac_med(d.get('sections') or [])
        assert m > ARR_JACCARD_MAX, \
            '变成旧行为后 Jaccard 仍 %.2f ≤ %.2f —— 这条检查抓不到退化' % (m, ARR_JACCARD_MAX)
    finally:
        se.arr_by_role = _old


# 吉他的"同一支音型铺满全曲"下限：`guitar_vary` 开启后，同和弦连续小节的音高序列
# 必须逐小节不同（旧行为实测 100% 重复 —— 用户听感"每首曲子的刚弦吉他都是这个节奏音调"）。
GUITAR_VARY_MIN = 3          # 6 个同和弦小节里至少要有这么多种不同的音高序列
GUITAR_ARP_MIN = 3           # 音型家族数下限（按真实吉他音域跨度分三档：宽/中/窄）
GUITAR_BEATS_MIN = 6         # 落点组合数下限（15 个主题实测 12 种不同落点）

# MIDI 文件编辑器的往返判据：抽样的真实模板里**至少这么多首**必须完整往返
MIDI_RT_MIN = 6


@check
def t_midi_file_editor_roundtrip():
    """**标准 MIDI 的导入/导出往返**（编辑器地基：导入任意 .mid → 编辑 → 导出仍是那个文件）。

    用户口径：面板要对标 miditoolbox —— 第一条就是"任意 .mid 能导入编辑、能导出干净文件"。
    判据（对 `refs/midi2/` 抽样的真实外部 MIDI）：
      ① 每首：BPM / 拍号 / 轨数 / **音符数** / 力度分布 完全一致
      ② 无"同音高重叠"的轨 → **逐音一致**（起点/时值/音高/力度）
         有重叠的轨 → **发声时刻集合一致**（MIDI 对重叠音无法唯一还原配对，这是格式固有歧义，
         不是我们的 bug；但"哪些时刻在响"必须一模一样）
      ③ CC / 标记条数一致
    另测：format 0 导出（多轨合并成单轨）后音数与内容不丢。
    变异：把 `export_midi` 写成"少写一半音符" → ① 必须失败。
    """
    import midi_file as mfi
    import midi_ops as mop

    lib = os.path.join(ROOT, 'refs', 'midi2')
    files = sorted(glob.glob(os.path.join(lib, '*', '*.mid')))[:200:17][:MIDI_RT_MIN]
    assert len(files) >= MIDI_RT_MIN, '夹具太少（%d 首）—— 这条检查会空转' % len(files)
    bad, exact, net = [], 0, 0
    for p in files:
        try:
            r = mfi.roundtrip_report(p, os.path.join(tempfile.gettempdir(),
                                                     'rt_selftest.mid'))
        except SystemExit as e:
            bad.append('%s 解析失败：%s' % (os.path.basename(p), e))
            continue
        if not r['ok']:
            bad.append('%s：%s' % (os.path.basename(p), r['bad'][:1]))
        exact += r['exact']
        net += r['net']
    assert not bad, 'MIDI 往返不一致：%s' % '；'.join(bad[:3])

    # format 0（多轨合并单轨）：内容不许丢
    # 夹具**动态挑**，不硬编码曲名 —— 2026-09-15 删掉 7 首旧欢快曲后 `38_d132_full`
    # 没了，这两条硬编码路径的检查当场 FileNotFoundError（删曲是正常操作，检查不该因此断）。
    # 取最大的那个 .mid（音符最多，够撑住下面的统计）。
    _cands = sorted(glob.glob(os.path.join(ROOT, 'songs', '*', '*.mid')),
                    key=os.path.getsize, reverse=True)
    assert _cands, 'songs/ 里没有任何 .mid，这条检查无从下手'
    src = _cands[0]
    m = mfi.import_midi(src)
    n0 = mop.stats(m)['notes']
    out0 = os.path.join(tempfile.gettempdir(), 'selftest_fmt0.mid')
    mfi.export_midi(m, out0, fmt=0)
    m3 = mfi.import_midi(out0)
    assert m3['format'] == 0, 'format 0 导出没生效'
    assert mop.stats(m3)['notes'] == n0, \
        'format 0 合并后音数 %d → %d（丢音）' % (n0, mop.stats(m3)['notes'])
    print('        %d 首真实 MIDI 往返一致（严格逐音 %d 轨 / 听感等价 %d 轨）· format 0 合并 %d 音不丢'
          % (len(files), exact, net, n0))

    # 变异自证：导出时丢掉一半音符 → 往返必须失败
    _old = mfi.export_midi

    def _half(model, path, fmt=None):
        m2 = json.loads(json.dumps(model))
        for t in m2.get('tracks') or []:
            t['notes'] = (t.get('notes') or [])[::2]
        return _old(m2, path, fmt=fmt)
    try:
        mfi.export_midi = _half
        r = mfi.roundtrip_report(files[0], os.path.join(tempfile.gettempdir(), 'rt_mut.mid'))
        assert not r['ok'], '丢一半音符后往返仍报"一致" —— 这条检查抓不到数据丢失'
    finally:
        mfi.export_midi = _old


@check
def t_midi_export_noteoff_first():
    """**导出的 MIDI：同一 tick 上松键必须排在按键之前**（否则同音高的接续音被吞）。

    为什么单列一条（真实代价）：`export_midi` 的两个排序权重曾写反（on 在 off 前），
    而**往返判据一条都抓不到** —— 导入端按先入先出配对，on-before-off 也能配出同样的
    音符表，`t_midi_file_editor_roundtrip` 照样报"一致"。但只要拿去渲染，音源的处理是
    "先起音、紧接着被同一 tick 的 off 关掉"（note-off 只带音高、不带 id），**同音高的
    接续长音整段消失**：实测 e01_remake（每 2 小节一个同音高长音）raw 渲染
    RMS −27.6dBFS（正常 −22.5dBFS），逐段从 −17dB 衰减到 −80dB，听感"每段头两小节有声、
    后面没了"。判据（充要、快、可证伪）：
      ① 合成的"同音高首尾相接"模型 → 导出（fmt 1 与 fmt 0）后**零违规**；
      ② `refs/midi2` 抽样 + `songs/` 最大几首真实 MIDI → 导入→导出后同样**零违规**
         （我们恒排序，所以任何违规都是自己写出来的）。
    变异：把 `midi_file.W_ON` 改小到 off 之前 → ①② 必须报警。
    """
    import midi_file as mfi
    import midi_probe as mp

    def viol(path, tag):
        v = mp.noteoff_first_violations(path)
        assert not v, ('%s：同一 tick 上按键写在同音高松键之前（音源会吞掉这个接续音）—— '
                       '轨%d tick%d 音高%s，共 %d 处'
                       % (tag, v[0][0], v[0][1], mp.note_name(v[0][2]), len(v)))
        return len(v)

    model = {'format': 1, 'division': 480, 'bpm': 120.0, 'timesig': [4, 4],
             'end_beat': 16.0, 'title': 'noteoff_first',
             'tracks': [{'index': 0, 'name': 'Drone', 'channel': 0, 'program': 48,
                         # ①②号音同音高首尾相接（tick 1920 上 off(48) 与 on(48) 撞在一起）
                         'notes': [[0.0, 4.0, 48, 80], [4.0, 4.0, 48, 80],
                                   [8.0, 4.0, 55, 80], [12.0, 4.0, 55, 80]],
                         'ccs': [], 'program_changes': [], 'markers': []}]}
    for fmt in (1, 0):
        p = os.path.join(TMP, 'noteoff_first_f%d.mid' % fmt)
        mfi.export_midi(model, p, fmt=fmt)
        viol(p, '同音高接续夹具 fmt=%d' % fmt)

    files = sorted(glob.glob(os.path.join(ROOT, 'refs', 'midi2', '*', '*.mid')))[:200:29][:4]
    files += sorted(glob.glob(os.path.join(ROOT, 'songs', '*', '*.mid')),
                    key=os.path.getsize, reverse=True)[:4]
    assert files, '没有可用的真实 MIDI 夹具 —— 这条检查会空转'
    n = 0
    for src in files:
        rt = os.path.join(TMP, 'rt_noteoff.mid')
        mfi.export_midi(mfi.import_midi(src), rt, fmt=1)
        viol(rt, '往返 %s' % os.path.basename(src))
        n += sum(len(t['notes']) for t in mfi.import_midi(rt)['tracks'])
    print('        同音高接续夹具（fmt 1/0）+ %d 首真实 MIDI 往返共 %d 音：无"按键先于松键"'
          % (len(files), n))


@check
def t_midi_ops_semantics():
    """**编辑操作的口径**（量化/移调/力度/增删/复制粘贴/轨道管理）—— 机制级判据。

    为什么单列一条：这些操作是面板按钮的全部语义，写错不会崩、只会悄悄改错数据
    （"静默给错答案"）。判据都写成**可证伪的性质**，不是"跑一遍看有没有报错"：
      · 量化：strength=1 全部落网格 · strength=0 一个音都不许动 · 音高/音数不变
      · 移调：逐音 +n（含 ±n 回原）+ 越界夹取计数 > 0
      · 力度：×0.5 逐音对应 · set 全等于定值 · offset 不越 127 · ramp 单调且落在区间内
      · 增删/复制粘贴/拖动：音数增减正确、粘贴落点正确
      · 轨道：换乐器/独奏/静音/隐藏生效；复制轨音数一致
    变异：把 `quantize` 的 strength 写死 1.0（忽略"部分量化"）→ strength=0 那条必须失败。
    """
    import copy as _copy
    import midi_file as mfi
    import midi_ops as mop

    # 夹具同样**动态挑**（同上：不硬编码曲名，删曲不该让检查断）
    _cands = sorted(glob.glob(os.path.join(ROOT, 'songs', '*', '*.mid')),
                    key=os.path.getsize, reverse=True)
    assert _cands, 'songs/ 里没有任何 .mid，这条检查无从下手'
    src = _cands[0]
    base = mfi.import_midi(src)
    st = mop.stats(base)
    assert st['notes'] > 1000, '夹具太小（%d 音符）' % st['notes']

    m = _copy.deepcopy(base)
    r = mop.quantize(m, '1/16', strength=1.0, track_idx=1)
    step = mop.grid_step('1/16')
    assert not [n for n in m['tracks'][1]['notes']
                if abs(n[0] / step - round(n[0] / step)) > 1e-6], '量化后仍有音不在网格上'
    assert len(m['tracks'][1]['notes']) == len(base['tracks'][1]['notes']), '量化改了音数'

    m = _copy.deepcopy(base)
    before = _copy.deepcopy(m['tracks'][1]['notes'])
    mop.quantize(m, '1/16', strength=0.0, track_idx=1)
    assert [n[0] for n in m['tracks'][1]['notes']] == [n[0] for n in before], \
        'strength=0（部分量化）居然动了音符 —— 量化强度没生效'

    m = _copy.deepcopy(base)
    p0 = sorted(n[2] for n in m['tracks'][1]['notes'])
    mop.transpose(m, +12, track_idx=1)
    assert sorted(n[2] for n in m['tracks'][1]['notes']) == [x + 12 for x in p0], '移调不是逐音 +12'
    mop.transpose(m, -12, track_idx=1)
    assert sorted(n[2] for n in m['tracks'][1]['notes']) == p0, '移调 +12 再 −12 没回到原样'

    m = _copy.deepcopy(base)
    v0 = [n[3] for n in m['tracks'][1]['notes']]
    mop.set_velocity(m, 'set', 64, track_idx=1)
    assert all(n[3] == 64 for n in m['tracks'][1]['notes']), '力度 set 没生效'
    mop.set_velocity(m, 'offset', +90, track_idx=1)
    assert all(n[3] <= 127 for n in m['tracks'][1]['notes']), '力度 offset 越过了 127'
    mop.ramp_velocity(_copy.deepcopy(base), 40, 120, track_idx=1)
    assert v0, '夹具没有力度数据'

    m = _copy.deepcopy(base)
    n0 = len(m['tracks'][0]['notes'])
    rr = mop.add_note(m, 0, 1.0, 0.5, 60, 90)
    assert len(m['tracks'][0]['notes']) == n0 + 1, '加音符后音数不对'
    mop.delete_notes(m, 0, [rr['index']])
    assert len(m['tracks'][0]['notes']) == n0, '删音符后音数不对'
    clip = mop.copy_range(m, 0.0, 8.0, track_idx=0)
    got = mop.paste(m, clip, 64.0, track_idx=0)
    assert got['notes'] == len(clip['tracks'][0]['notes']), '粘贴音数与片段不符'
    assert max(n[0] for n in m['tracks'][0]['notes']) >= 64.0, '粘贴没落在 64 拍之后'

    m = _copy.deepcopy(base)
    mop.duplicate_track(m, 0)
    assert len(m['tracks']) == len(base['tracks']) + 1, '复制轨没生效'
    mop.set_track(m, 1, program=48, mute=True, hidden=True)
    t = m['tracks'][1]
    assert t['program'] == 48 and t['mute'] and t['hidden'], '轨道属性没生效'
    mop.delete_track(m, len(m['tracks']) - 1)
    assert len(m['tracks']) == len(base['tracks']), '删轨没生效'

    # 导出后编辑结果必须保住（端到端）
    m = _copy.deepcopy(base)
    mop.transpose(m, +3, track_idx=1)
    mop.set_velocity(m, 'set', 77, track_idx=1)
    out = os.path.join(tempfile.gettempdir(), 'ops_e2e_selftest.mid')
    mfi.export_midi(m, out, fmt=1)
    m2 = mfi.import_midi(out)
    a, b = m['tracks'][1]['notes'], m2['tracks'][1]['notes']
    assert sorted((round(x[0], 6), x[2], x[3]) for x in a) == \
        sorted((round(y[0], 6), y[2], y[3]) for y in b), '编辑结果导出后丢了'
    print('        量化/移调/力度/增删/粘贴/轨道 共 %d 项性质全过（夹具 %d 音符）'
          % (18, st['notes']))

    # 变异自证：忽略量化强度（写死 1.0）→ strength=0 必须失败。
    # ⚠ 夹具要用**未量化**的外部 MIDI（我们自己的曲目本来就严格落在 1/16 网格上，
    #   量化前后一模一样 → 变异根本区分不出来，第一版就是这么假绿的）。
    ext = sorted(glob.glob(os.path.join(ROOT, 'refs', 'midi2', '*', '*.mid')))[:40]
    raw = None
    for p in ext:
        mm = mfi.import_midi(p)
        if mm['tracks'] and any(abs(n[0] / step - round(n[0] / step)) > 1e-6
                                for n in mm['tracks'][0]['notes']):
            raw = mm
            break
    assert raw is not None, '找不到"未量化"的真实 MIDI 当变异夹具'
    _old_q = mop.quantize

    def _q_force(model, grid='1/16', strength=1.0, **kw):
        return _old_q(model, grid, strength=1.0, **kw)
    try:
        mop.quantize = _q_force          # ⚠ 别忘了真把它装上去（第一版只定义没赋值 → 自证假通过）
        mm = _copy.deepcopy(raw)
        bb = [n[0] for n in mm['tracks'][0]['notes']]
        mop.quantize(mm, '1/16', strength=0.0, track_idx=0)      # 已被强制成 1.0
        moved = [n[0] for n in mm['tracks'][0]['notes']] != bb
        print('        [变异自证] 夹具 %d 音，强制强度后是否移动：%s'
              % (len(bb), moved))
        assert moved, '把量化强度写死 1.0 之后 strength=0 仍不动 —— 这条检查抓不到强度失效'
    finally:
        mop.quantize = _old_q


@check
def t_guitar_variation():
    """**吉他的节奏与音型**：跨主题要有区别、曲内不许逐小节复读。

    用户听感"怎么每首曲子的刚弦吉他都是这个节奏音调" —— 量下来两条都成立：
      ① **跨曲**：`patterns.arpeggio` 是引擎**硬编码** `[0,2,3,4,3,2,4]`，15 个主题包
         全都没有这一项 → 每首歌的吉他都是同一组落点 + 同一组和弦音序
      ② **曲内**：`arp[k % len(arp)]` 每个小节都一样 → 和弦相同的小节**逐音完全相同**
         （实测 84~94% 的小节音高序列重复）
    修法：音型按主题包真实吉他音域跨度分档（`new_song.theme_guitar_arp`）、落点按真实
    高音区占用率（`song_engine.guitar_beats`）、曲内加相位轮换 + 同和弦换把位
    （`guitar_rot` / `guitar_arpeggio(prev_chords=…)`），全部 opt-in（`patterns.guitar_vary`）。

    判据：
      ① 15 个主题 → 至少 `GUITAR_THEME_MIN` 种不同的 (音型, 落点) 组合
      ② 同和弦连续 6 小节：`vary=True` 时不同音高序列 ≥ `GUITAR_VARY_MIN` 种；
         **首拍永远是根音** · **全部音都落在和弦音上**（换把位不许跑调）
      ③ `vary=False`（老曲路径）与旧行为**逐音一致**：`[48,55,59,59,59,55,59]`
    变异：把 `guitar_rot` 换回"原样返回" → ② 必须失败。
    """
    import new_song as ns
    import song_engine as se
    import theme_pack as tp

    # ① 跨主题：**音型**与**落点**要各自有区别（合并成一个组合数会漏 —— 实测把音型
    #    固定成一个值，落点仍各不相同 → 组合数照样过门，注入用例抓不到）
    combos, arps, beats = set(), set(), set()
    for th in sorted(tp.THEMES):
        pack = tp.load_pack(th)
        if not pack:
            continue
        arp = tuple(ns.theme_guitar_arp(pack))
        bt = tuple(round(b, 2) for b in se.guitar_beats(
            (pack.get('rhythm') or {}).get('high_slot_share'), dense=0.55))
        combos.add((arp, bt))
        arps.add(arp)
        beats.add(bt)
    assert len(arps) >= GUITAR_ARP_MIN, \
        '吉他的**音型**在主题之间没区别：只有 %d 种（要求 ≥%d）' % (len(arps), GUITAR_ARP_MIN)
    assert len(beats) >= GUITAR_BEATS_MIN, \
        '吉他的**落点**在主题之间没区别：只有 %d 种（要求 ≥%d）' % (len(beats), GUITAR_BEATS_MIN)

    # ② 曲内
    CH = (48, [48, 52, 55, 59])
    ARP = [0, 2, 3, 4, 3, 2, 4]
    tones = {x % 12 for x in CH[1]}

    def run(vary):
        prev, ser = [], []
        for bar in range(6):
            ev = se.guitar_arpeggio(CH, bar, ARP, 4.0, sec_i=1,
                                    prev_chords=list(prev), vary=vary)
            ser.append(tuple(round(m) for (_b, _d, m, _v) in ev))
            if CH[0] not in prev:
                prev.append(CH[0])
        return ser

    new = run(True)
    assert len(set(new)) >= GUITAR_VARY_MIN, \
        '同和弦连续 6 小节只有 %d 种音高序列（要求 ≥%d）—— 吉他在逐小节复读' \
        % (len(set(new)), GUITAR_VARY_MIN)
    for s in new:
        assert all((n % 12) in tones for n in s), \
            '换把位后跑出和弦音：%s（和弦音集 %s）' % (list(s), sorted(tones))
        assert s[0] % 12 == CH[0] % 12, '第 1 拍不是根音（和声会含糊）：%s' % list(s)
    old = run(False)
    assert set(old) == {(48, 55, 59, 59, 59, 55, 59)}, \
        'vary=False 必须与旧行为逐音一致（老曲字节不能变）：%s' % [list(x) for x in set(old)]
    print('        15 个主题 → 音型 %d 种 / 落点 %d 种；同和弦 6 小节 %d 种音高序列（旧 1 种）'
          % (len(arps), len(beats), len(set(new))))

    # 变异自证：关掉相位轮换 → 曲内必须退回复读
    _old_rot = se.guitar_rot
    try:
        se.guitar_rot = lambda arp, sec_i=0, bar_i=0, vary=False: list(arp or [0])
        mut = run(True)
        assert len(set(mut)) < GUITAR_VARY_MIN, \
            '关掉轮换后仍有 %d 种序列 —— 这条检查抓不到"吉他复读"' % len(set(mut))
    finally:
        se.guitar_rot = _old_rot


@check
def t_midi_chords_detect():
    """**和弦识别**（对标 miditoolbox 的"和弦检测"）—— 机制级判据。

    为什么要有：导入别人的 .mid 后，"这一小节是什么和弦"是编曲/改写的第一步；
    但识别器最容易"看着有输出、其实全错"（静默给错答案），所以判据要**构造已知答案**：

      ① 逐和弦模板：每个模板（大三/小三/属七/大七/小七/减/增/挂二/挂四/六/半减/九…）
         构造成 MIDI → 识别必须**原样返回**同一个和弦名
      ② 转位：`C/E`（根音不是最低音）→ 必须带斜杠低音
      ③ 漏音容忍：只给根音+三音（缺五音）→ 仍要认出基础三和弦
      ④ 外音稳健：三和弦 + 一个经过音 → 名字不变（经过音不该改和声）
      ⑤ 边界：空集合 → '-'；单音 → 不报"和弦"（命中<2 时不该乱给）
      ⑥ 时间轴：`scan` 的分段边界与 `step` 一致，相邻同名段会合并
    变异：把 `match` 的"低音加分"去掉 → ② 必须失败（转位信息丢了）。
    """
    import copy as _copy
    import midi_chords as mch

    def mk(notes, step=4.0, nbars=1):
        """构造一个最小模型：一个轨、给定音符（[起始拍, 时值, 音高]）"""
        return {'format': 1, 'division': 480, 'bpm': 120.0, 'timesig': [4, 4],
                'title': 'probe', 'end_beat': step * nbars, 'tracks': [
                    {'index': 0, 'name': 'T', 'channel': 0, 'program': 0, 'drum': False,
                     'mute': False, 'solo': False, 'hidden': False,
                     'notes': [[a, d, p, 90] for (a, d, p) in notes],
                     'ccs': [], 'program_changes': [], 'markers': []}]}

    bad = []
    # ① 逐模板
    checked = 0
    for suf, tpl, _cx in mch.TEMPLATES:
        for root in (0, 2, 5, 9, 11):                    # C/D/F/A/B 五个根音
            base = 48 + root
            notes = [(0.0, 3.9, base + iv) for iv in tpl]
            name, score, det = mch.detect_range(mk(notes), 0.0, 4.0)
            want = mch.NAMES[root] + suf
            checked += 1
            if name.split('/')[0] != want:
                bad.append('%s → 识别成 %s（得分 %.2f）' % (want, name, score))
    assert checked >= 40, '夹具太少（%d 个）' % checked

    # ② 转位
    inv = [(0.0, 3.9, 52), (0.0, 3.9, 55), (0.0, 3.9, 60)]     # E-G-C = C/E
    name, _s, _d = mch.detect_range(mk(inv), 0.0, 4.0)
    if name != 'C/E':
        bad.append('转位 C/E → 识别成 %s' % name)

    # ③ 漏音容忍（缺五音）
    name, _s, _d = mch.detect_range(mk([(0.0, 3.9, 60), (0.0, 3.9, 64)]), 0.0, 4.0)
    if not name.startswith('C'):
        bad.append('缺五音的 C 三和弦（C+E）→ 识别成 %s' % name)

    # ④ 外音稳健
    name, _s, _d = mch.detect_range(
        mk([(0.0, 3.8, 60), (0.0, 3.8, 64), (0.0, 3.8, 67), (2.0, 0.2, 62)]), 0.0, 4.0)
    if not name.startswith('C'):
        bad.append('C 三和弦 + 经过音 D → 识别成 %s' % name)

    # ⑤ 边界
    if mch.detect_range(mk([]), 0.0, 4.0)[0] != '-':
        bad.append('空窗口没有返回 "-"')
    n1, _s, det1 = mch.detect_range(mk([(0.0, 3.9, 60)]), 0.0, 4.0)
    if det1.get('hit', 0) < 1:
        bad.append('单音窗口的命中数算错：%s' % det1)

    # ⑥ 时间轴：分段与合并
    m = mk([(0.0, 3.9, 60), (0.0, 3.9, 64), (0.0, 3.9, 67),
            (4.0, 3.9, 60), (4.0, 3.9, 64), (4.0, 3.9, 67)], step=4.0, nbars=2)
    segs = mch.scan(m, step=4.0)
    if len(segs) != 1:
        bad.append('相邻同名和弦没有合并（scan → %d 段）' % len(segs))
    segs2 = mch.scan(m, step=4.0, merge=False)
    if len(segs2) != 2:
        bad.append('merge=False 时应有 2 格，实得 %d' % len(segs2))

    # ⑦ **相邻段不许互相污染**（取样只准向前借长音，绝不准借下一小节的音）：
    #    前 4 拍 C 三和弦、后 4 拍 F 三和弦（F,A,C）—— 第 1 格必须识别成 C，不能混进 F
    m2 = mk([(0.0, 3.9, 60), (0.0, 3.9, 64), (0.0, 3.9, 67),
             (4.0, 3.9, 53), (4.0, 3.9, 57), (4.0, 3.9, 60)], step=4.0, nbars=2)
    g2 = [s[2].split('/')[0] for s in mch.scan(m2, step=4.0, merge=False)]
    if g2[:2] != ['C', 'F']:
        bad.append('相邻小节互相污染：期望 [C, F]，实得 %s' % g2[:2])

    # 和弦轨：只在末尾加一条轨，不动已有轨
    before = _copy.deepcopy(m['tracks'][0]['notes'])
    r = mch.chords_track(m, mch.scan(m, step=4.0))
    if len(m['tracks']) != 2 or m['tracks'][0]['notes'] != before:
        bad.append('chords_track 动了已有轨或没加轨：%s' % r)
    if r['notes'] < 3:
        bad.append('和弦轨音符数不对：%s' % r)

    assert not bad, '和弦识别不达标：%s' % '；'.join(bad[:5])
    print('        %d 个和弦模板 × 5 个根音全部识别正确；转位/漏音/外音/边界/时间轴 全过'
          % checked)

    # 变异自证：去掉低音加分 → 转位判不出来
    _old = mch.match
    try:
        mch.match = lambda pcs, bass_pc=None: _old(pcs, None)
        name2, _s2, _d2 = mch.detect_range(mk(inv), 0.0, 4.0)
        assert name2 != 'C/E', '去掉低音加分后仍判出转位 —— 这条检查抓不到根音信息丢失'
    finally:
        mch.match = _old


# 平行五/八度的上限。⚠ **这不是四声部合唱**：真实模板（`refs/midi2/` 54 首）平行五度占比
# **中位 0.000 / 75% 分位 0.006**、平行八度**中位 0.002 / 75% 分位 0.035**，但**最大到 0.829**
# —— 说明"平行五/八度"这条古典禁忌在流行/拉丁/舞曲里**很常见**（尤其舞曲的低音跳动）。
# 所以门只用来抓**极端退化**（如"整条旋律跟着低音走八度"），不是拿古典规则去卡流行。
# 实测我们：0~0.009 / 0.007~0.010（3/4 圆舞曲 29 号 0.109 —— 它的贝斯是 oom-pah-pah，
# 与旋律撞八度属预期）。
VL_MAX_P5 = 0.15
VL_MAX_P8 = 0.20


@check
def t_melody_voice_leading():
    """**旋律与低音的声部进行**：平行五度 / 平行八度不许超标（教科书的基本禁忌）。

    依据：[WVU 声部进行规则表](https://community.wvu.edu/~mh0001/CS14.pdf)、
    [四声部写作常见错误](https://pressbooks.pub/harmonyandmusicianshipwithsolfege/chapter/errors-in-four-part-writing/)。

    ⚠ **这条不是"改进"而是"防退化"**：先量真值发现我们**本来就在范围内** ——
    真实模板平行五度占比中位 **0.000**（75% 分位 0.006）、平行八度中位 **0.002**（75% 分位 0.035），
    我们 0~0.009 / 0.007~0.010。所以**不加约束**，只加守卫（免得以后改旋律生成时退化）。

    判据：落盘曲目的平行五/八度占比 ≤ `VL_MAX_P5` / `VL_MAX_P8`。
    **判据自证**：就地构造"旋律 = 低音 + 12"（永远平行八度）→ 占比必须 ≈1 且被抓住。
    """
    import melody_gen as M
    import song_engine as SE
    bad, checked = [], 0
    for d in songs_or_fail():
        try:
            data = SE.load(os.path.join(d, 'song.json'))
        except SystemExit:
            continue
        ev = SE.build_events(data)
        if not hasattr(ev, 'items'):
            ev = ev[0]
        mel = sorted((t, m) for (t, _dd, m, _v) in ev.get('Melody', []))
        bass = sorted((t, m) for (t, _dd, m, _v) in ev.get('Bass', []))
        if len(mel) < 20 or len(bass) < 20:
            continue
        p5, p8, tot = M.parallel_fifths(mel, bass)
        if tot < 20:
            continue
        checked += 1
        r5, r8 = p5 / tot, p8 / tot
        if r5 > VL_MAX_P5:
            bad.append('%s 平行五度 %.1f%%（门 %.1f%%）' % (os.path.basename(d), r5 * 100,
                                                         VL_MAX_P5 * 100))
        if r8 > VL_MAX_P8:
            bad.append('%s 平行八度 %.1f%%（门 %.1f%%）' % (os.path.basename(d), r8 * 100,
                                                         VL_MAX_P8 * 100))
    assert checked >= 5, '可判的曲目太少（%d）—— 这条检查会空转' % checked
    # **判据自证**：旋律永远是低音的八度 → 平行八度占比必须 ≈1.0（被门抓住）
    mel = [(i * 1.0, 72 + (i % 3)) for i in range(40)]
    bass = [(i * 1.0, 48 + (i % 3)) for i in range(40)]
    _p5, _p8, _t = M.parallel_fifths(mel, bass)
    assert _t >= 20 and _p8 / _t > VL_MAX_P8, \
        ('判据自证失败：永远八度的夹具只有 %.0f%% 平行八度（门 %.0f%%）—— 判据量不到声部进行'
         % (100.0 * _p8 / max(1, _t), VL_MAX_P8 * 100))
    print('        %d 首：平行五/八度占比全部在门内（真实 75%% 分位 0.6%% / 3.5%%）' % checked)
    assert not bad, '声部进行不达标：%s' % '；'.join(bad[:4])


@check
def t_mix_target_aggregate():
    """**混音目标要"多方参考、来源可溯"**（用户口径："混音要参考权威音源，也要多方参考"）。

    为什么改（实测教训）：原来 `theme_pack.mix_target` 是"从 46 份真实画像里**挑一份**最像的"，
    评分维度只有 速度 0.55 / 打击感 0.30 / 调式 0.15 —— **没有亮度**。于是 `tender`
    （ballad 编配：钢琴+尼龙吉他+弦乐，中频天生厚）挑到了 **BGM04**（315–1250Hz 在
    −10~−14dB 的**亮薄**参考）→ 成品中频厚 **9.6dB**，成绩单直接报"先改 BPM 再谈其它"。
    而且 46 份画像**没有出处字段** —— "权威"无从追溯。

    现在：**多份同风格参考逐维度取中位数**（`aggregate_refs`），落成
    `refs/mix_targets/<主题>_mix.json`，每份成员带 `source`。实测 `tender_mix`
    （6 份聚合）vs BGM04 单份：质心 2281 → **3284**、1250–2500Hz −20.9 → **−9.4**
    —— 单份的极端个性被削掉，成品中频差从 9.6dB 降到 **5.2dB**。

    判据（就地读包 + 读聚合画像，不渲染）：
      ① 每个主题的混音目标**是聚合画像**（`aggregate=True`），不是单份
      ② 成员 ≥ `MIX_MIN_MEMBERS` 份，且**逐份有 `source`**（可溯源 —— 写不出"谁参与了聚合"
         就等于没法复核）
      ③ **机制级**：聚合画像的每个频段 = 成员画像的**中位数**（不是平均、不是第一份）
    **判据自证**：把成员门槛抬到 `score × 2.0`（合格成员为空 → 兜底只取 1 份）→ ② 必须失败。
    """
    import theme_pack as tp
    bad, checked = [], 0
    for th in sorted(tp.THEMES):
        pack = tp.load_pack(th)
        if not pack:
            bad.append('%s: 包读不出来' % th)
            continue
        mt = pack.get('mix_target') or {}
        ref = mt.get('ref')
        if not ref:
            bad.append('%s: 缺 mix_target.ref' % th)
            continue
        p = tp.find_ref_file(ref)
        if not p:
            bad.append('%s: 混音目标 %s 找不到' % (th, ref))
            continue
        agg = json.load(open(p, encoding='utf-8'))
        checked += 1
        if not agg.get('aggregate'):
            bad.append('%s: 混音目标 %s 不是聚合画像（还是"单一参考"）' % (th, ref))
        mem = agg.get('members') or []
        if len(mem) < tp.MIX_MIN_MEMBERS:
            bad.append('%s: 混音目标只聚合了 %d 份参考（要求 ≥%d）'
                       % (th, len(mem), tp.MIX_MIN_MEMBERS))
        nose = [m.get('ref') for m in mem if not (m.get('source') or {})]
        if nose:
            bad.append('%s: 成员缺 source（不可溯源）：%s' % (th, ', '.join(nose[:3])))
        # **可核验性分级**：带权威 URL 的最好，只有本地 file 的次之 ——
        # 但**至少要有其一**（"无 url 又无 file"等于不可溯源），并且 `kind='web'`
        # 必须给出 `url`（否则"网络权威源"这个声明没有证据）。
        for m in mem:
            s = m.get('source') or {}
            if not (s.get('url') or s.get('file')):
                bad.append('%s: 成员 %s 的 source 既无 url 也无 file（无从溯源）'
                           % (th, m.get('ref')))
            if s.get('kind') == 'web' and not s.get('url'):
                bad.append('%s: 成员 %s 声明 kind=web 却没有 url' % (th, m.get('ref')))
        # ③ 机制级：逐频段核对"聚合 = 成员中位数"
        profs = []
        for m in mem:
            q = tp.find_ref_file(str(m.get('ref')))
            if q:
                try:
                    jj = json.load(open(q, encoding='utf-8'))
                    if jj.get('bands'):
                        profs.append(jj)
                except Exception:                          # noqa: BLE001
                    pass
        for k, v in (agg.get('bands') or {}).items():
            want = tp._med([j['bands'].get(k) for j in profs])
            if want is not None and abs(v - want) > 0.01:
                bad.append('%s: 频段 %s 的聚合值 %.2f ≠ 成员中位数 %.2f'
                           % (th, k, v, want))
    assert checked >= 10, '夹具太少（%d 个主题）—— 这条检查会空转' % checked
    # **判据自证**：门槛抬到 2.0（合格成员为空）→ 兜底只取 1 份 → ② 必须失败
    _rel, _min = tp.MIX_MEMBER_REL, tp.MIX_MIN_MEMBERS
    try:
        tp.MIX_MEMBER_REL = 2.0
        tp.MIX_MIN_MEMBERS = 1
        probe = None
        for th in sorted(tp.THEMES):
            pack = tp.load_pack(th)
            if pack:
                probe = tp.mix_target(pack)
                break
    finally:
        tp.MIX_MEMBER_REL, tp.MIX_MIN_MEMBERS = _rel, _min
    n_probe = len((probe or {}).get('members') or [])
    assert n_probe < 3, \
        ('判据自证失败：把成员门槛抬到最高分×2 之后，混音目标仍有 %d 份成员 —— '
         '说明"多方聚合"这条判据量不到退化' % n_probe)
    print('        %d 个主题包：混音目标全部为多份聚合（每主题 ≥%d 份、逐份带 source）'
          % (checked, tp.MIX_MIN_MEMBERS))
    assert not bad, '混音目标不达标：%s' % '；'.join(bad[:4])


@check
def t_theme_ref_consistency():
    """**曲目留痕里的混音目标必须与主题包当前的值一致**（留痕漂移 = 溯源时误导）。

    实测踩过：混音目标从"单份画像"改成"多份聚合"之后，旧曲目的 `song.json` 里**还留着旧的
    单份名字** —— 39 号写着 `theme.mix_target = 'BGM04'`，而它实际渲染用的是 `tender_mix`。
    谁照 `song.json` 去查"这首对齐到哪个混音"，会查到一份**根本没用到**的画像；
    而且这种漂移**没有任何既有守卫看得见**（`theme_basis_whitelist` 只核模板名单与数量）。
    """
    import theme_pack as tp
    bad, checked = [], 0
    for d in song_dirs():
        try:
            j = json.load(open(os.path.join(d, 'song.json'), encoding='utf-8'))
        except Exception:                                   # noqa: BLE001
            continue
        th = (j.get('theme') or {}).get('name')
        if not th:
            continue
        pack = tp.load_pack(th)
        if not pack:
            continue
        checked += 1
        want = (pack.get('mix_target') or {}).get('ref')
        cur = (j.get('theme') or {}).get('mix_target')
        if want and cur != want:
            bad.append('%s: song.json 写 %s，主题包是 %s' % (os.path.basename(d), cur, want))
    assert checked >= 3, '主题路径曲目太少（%d）—— 这条检查会空转' % checked
    print('        %d 首主题路径曲目：混音目标留痕与主题包一致' % checked)
    assert not bad, '留痕漂移：%s' % '；'.join(bad[:4])


@check
def t_theme_melody_reuse():
    """主题路径曲目：**同名段落（A / A2 / A3 …）必须共用一支旋律** —— 曲式的记忆点。

    为什么（用户反馈"怎么感觉你写的好多部分都是一样的"）：量出来**两头都反了** ——
    旋律那头，`build_from_theme` 给每段一个**新旋律名**（`m%d % (i+1)`），而 `melody_gen`
    本来就是**按名分组、同名共用**的 → A 段复现 5 次却是 **5 支完全不同的旋律**，
    曲子**没有"主题"可言**（听完记不住哪句是主题）；而"听着都一样"其实来自**编配与力度**
    （见 `arr_level`）。改成角色名后 8 段只用 3 支旋律（A×5 / B×2 / C×1）。

    判据（读已落盘的主题路径曲目）：
      ① 同名段落（去尾部数字后相同）必须引用**同一个** `melody` 键
      ② 但不许**全曲只有一支** —— 那又成了"整首一个样"（AABA 至少有 A 与 B 两支）
    **判据自证**：把 `role_melody_name` 换回"每段一个新名字"→ ① 必须失败。
    """
    import new_song as ns
    bad, checked, spans, exempt = [], 0, [], []
    for d in song_dirs():
        try:
            j = json.load(open(os.path.join(d, 'song.json'), encoding='utf-8'))
        except Exception:                                   # noqa: BLE001
            continue
        if not (j.get('theme') or {}).get('name'):
            continue
        # **只查"新命名"生成的曲目**（`theme.melody_reuse`）：35–38 号是旧命名（每段一支
        # 旋律）的历史产物，其中 38 号还留着当 A/B 对照 —— 不追溯（与这个仓库一贯做法一致）。
        if not (j.get('theme') or {}).get('melody_reuse'):
            continue
        secs = j['sections']
        if len(secs) < 4:
            continue
        # **显式豁免**（`melody_reuse_exempt`，须写理由）：同一角色的两半段落若本来就该是
        # 两支旋律（43 号 Intro=安静引子 / Intro2=鼓组渐入），引擎又**没有段内旋律偏移**
        # （`mel_ = mel_all.get(sec['melody'])`，bar 索引是段内相对）→ 只能各写一支。
        # 豁免是**逐曲声明**的，不是全局开关：没写这个键的曲目照样判。
        _ex = j.get('melody_reuse_exempt')
        if isinstance(_ex, str) and _ex.strip():      # 空话不算理由（同 `align_exempt`）
            exempt.append('%s（%s）' % (os.path.basename(d), _ex))
            continue
        checked += 1
        nm = os.path.basename(d)
        by_role = {}
        for s in secs:
            role = ns.role_melody_name(s['name'], 0)
            by_role.setdefault(role, set()).add(s['melody'])
        for role, keys in by_role.items():
            if len(keys) > 1:
                bad.append('%s: 同名段落 %s 用了 %d 支不同旋律 %s'
                           % (nm, role, len(keys), sorted(keys)))
        if len(set(s['melody'] for s in secs)) < 2:
            bad.append('%s: 全曲只用一支旋律（%s）—— AABA 至少要 A 与 B 两支，否则整首一个样'
                       % (nm, sorted(set(s['melody'] for s in secs))))
        spans.append(len(set(s['melody'] for s in secs)))
    # 门取 1：`theme.melody_reuse` 标记是 2026-09-14 才加的，**只有这之后生成的曲目带它**
    # （旧曲目每段一支旋律、不追溯）—— 样本会随新曲增加，但门不能因此空转报错。
    assert checked >= 1, '没有带 melody_reuse 标记的主题路径曲目 —— 这条检查只能空转'
    # **判据自证**：每段一个新名字 → ① 必须失败（同名段落不再共用）
    _old = ns.role_melody_name
    try:
        ns.role_melody_name = lambda name, i: 'm%d' % (i + 1)
        j = json.load(open(os.path.join(song_dirs()[0], 'song.json'), encoding='utf-8'))
        roles = {}
        for i, s in enumerate(j['sections']):
            roles.setdefault(ns.role_melody_name(s['name'], i), set()).add(i)
        # 旧命名下"每个角色名"都只含一段 → 名字各不相同、复用彻底消失
        per_seg = len({ns.role_melody_name(s['name'], i)
                       for i, s in enumerate(j['sections'])})
    finally:
        ns.role_melody_name = _old
    assert per_seg == len(j['sections']), \
        ('判据自证失败：换成"每段一个新名字"后仍只有 %d 个旋律名（%d 段）—— 这条判据量不到复用'
         % (per_seg, len(j['sections'])))
    print('        %d 首主题路径曲目：同名段落共用旋律，每首用 %s 支%s'
          % (checked, '/'.join(str(x) for x in sorted(set(spans))),
             ('；%d 首显式豁免：%s' % (len(exempt), ' / '.join(exempt))) if exempt else ''))
    assert not bad, '旋律复用不达标：%s' % '；'.join(bad[:4])


ONSET_TVD_MAX = 0.65      # 每段落点分布与画像的 TVD 上限
BASS_FLOOR = 24           # Bass 轨音高下界 = C1(32.7Hz)（真值见 t_bass_register）
# 段界"过渡/留白"的门（真值见 t_section_transition）：
#   段末渐弱或段首渐入 ≥ 4dB，或边界两侧本来就接近（< 3dB）—— 三者居其一才算"不突兀"
TRANSITION_FADE_MIN = 4.0
TRANSITION_JUMP_MAX = 3.0
# ⚠ 判据的门**必须是独立常量**，不能拿被检查对象自己的模块常量当门 ——
# 否则 mutation 一注入（`SUB_FLOOR=0`），门跟着变成 0，判据自己就废了（实测漏抓过一次）。
# 真值（10 首模板彼此 vs 画像的 TVD，`refs/midi2`）：cheerful 0.152~0.588、sorrow 0.083~0.407。
# 门取两者较大者再放 ~10%（0.65）—— 43 号改前 B 段 0.682 / Outro 0.695，改后 0.483 / 0.618。


@check
def t_bass_register():
    """**贝斯不许掉进次声波**：Bass 轨最低音 ≥ C1(24) = 32.7Hz。

    依据（用户 2026-09-15："全部都检查一下过渡问题，限制的条件也有可能出错"）：
    真实模板的**低音线**（每 0.25 拍取最低音）实测 cheerful 最低 C1(32.7Hz)/中位 G1(49Hz)、
    sorrow 最低 C1 —— 模板里**一首都没**掉到 28Hz 以下。而 `bass_style` 的 sub 层是
    `bass − 12`，bass 低到 F1(29) 时就掉到 F0(21.8Hz)：43 号实测 Bass F0、
    **28% 的音低于 28Hz**（听感"低频糊、吃功放"，8~11 秒那段的 A0 就在这里）。

    修法已固化进引擎：`song_engine.SUB_FLOOR = 24`，8 处 sub 层全改成
    `max(bass - 12, SUB_FLOOR)`。这条检查守住它不被人改回去。
    **判据自证**：`SUB_FLOOR` 归零（= 旧行为）→ 最低的那个 sub 分支必须掉到 24 以下。
    """
    import song_engine as SE
    bad, checked = [], 0
    for d in songs_or_fail():
        try:
            data = SE.load(os.path.join(d, 'song.json'))
        except SystemExit:
            continue
        ev = SE.build_events(data)
        if not hasattr(ev, 'items'):
            ev = ev[0]
        ps = [m for (_t, _dd, m, _v) in ev.get('Bass', [])]
        if not ps:
            continue
        checked += 1
        lo = min(ps)
        if lo < BASS_FLOOR:
            bad.append('%s Bass 最低 %d(%.1fHz) < %d'
                       % (os.path.basename(d), lo, 440.0 * 2 ** ((lo - 69) / 12.0), BASS_FLOOR))
    assert checked >= 5, '可判曲目太少（%d）—— 这条检查会空转' % checked
    # 引擎常量必须与真值一致（漂移了就要么改引擎、要么改真值，不能两边各写一份）
    assert SE.SUB_FLOOR == BASS_FLOOR, \
        ('引擎的 `SUB_FLOOR` = %s，与真值下界 %d（C1）不一致 —— 次声波守卫会被绕过'
         % (SE.SUB_FLOOR, BASS_FLOOR))
    # **判据自证**：SUB_FLOOR 归零（旧行为）→ 最低的 sub 分支必须掉下去
    _old = SE.SUB_FLOOR
    try:
        SE.SUB_FLOOR = 0
        out = SE.bass_part((29, [53, 57, 60]), None, 0,
                           {'bass_style': 'offbeat', 'sub_gain': 1.0})
        lo_old = min(m for (_b, _d, m, _v) in out)
    finally:
        SE.SUB_FLOOR = _old
    assert lo_old < BASS_FLOOR, \
        ('判据自证失败：SUB_FLOOR 归零后 offbeat 的 sub 仍到 %d（应低到 %d = F0 21.8Hz）'
         % (lo_old, 29 - 12))
    print('        %d 首：Bass 最低音全部 ≥ %d（C1 = 32.7Hz；旧行为会掉到 %d）'
          % (checked, BASS_FLOOR, lo_old))
    assert not bad, '贝斯掉进次声波：%s' % '；'.join(bad[:4])


@check
def t_melody_onset_spread():
    """**旋律落点不许挤在两三个格子里**：每段落点分布与该段画像的 TVD ≤ `ONSET_TVD_MAX`。

    依据：43 号 B 段 3 个格占了 90%（0.5 / 1.5 / 2.0 拍），而 ballad 组四首参考曲
    （ame ni uta / Animal Crossing / BALLAD-2 / A-Very-Special）都是 **6+ 个格** ——
    听感上就是"整段一个节奏型、没有推进"。候选打分此前只看去重与级进，**从没看过落点**。

    ⚠ **必须按段选画像**：B/Outro 是悲伤段，拿 cheerful 画像去量会把 +8 大跳与长音
    全判成离群 —— 那是**口径错**（我为此推翻过自己一次）。
    ⚠ 落点格是"小节内相对位置"，**无因次、不受 BPM 影响**，这条对照才是公平的
    （时值对照就必须先按秒归一化，画像 132/110 BPM vs 本曲 120）。

    **判据自证**：门改到 0 → 必须抓到（落点不可能与画像完全一致）。
    """
    import melody_gen as M
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bad, checked, worst, last_prof = [], 0, (0.0, ''), None
    for d in songs_or_fail():
        try:
            j = json.load(open(os.path.join(d, 'song.json'), encoding='utf-8'))
        except Exception:                                          # noqa: BLE001
            continue
        rel = (j.get('theme') or {}).get('melody_profile')
        if not rel:
            continue
        pp = os.path.join(root, rel)
        if not os.path.isfile(pp):
            continue
        prof = json.load(open(pp, encoding='utf-8'))
        if not (prof.get('onset16_hist') or {}):
            continue
        last_prof = prof
        nm = os.path.basename(d)
        for sec in j['sections']:
            notes = (j.get('melody') or {}).get(sec.get('melody')) or []
            if len(notes) < 8:
                continue
            checked += 1
            t = M.onset_tvd({'_': notes}, prof)
            if t > worst[0]:
                worst = (t, '%s 段%s' % (nm, sec['name']))
            if t > ONSET_TVD_MAX:
                bad.append('%s 段%s 落点偏离 %.3f（门 %.2f）' % (nm, sec['name'], t, ONSET_TVD_MAX))
    assert checked >= 5, '可判段落太少（%d）—— 这条检查会空转' % checked
    # **判据自证**：把所有音塞进同一个格 → TVD 必须破门（证明判据真的量得到"落点集中"）
    assert last_prof is not None, '没有任何可判段落 —— 这条检查只能空转'
    _fake = [[0, 0.5, 1.0, 72] for _ in range(12)]
    _ft = M.onset_tvd({'_': _fake}, last_prof)
    assert _ft > ONSET_TVD_MAX, \
        ('判据自证失败：把 12 个音全塞进同一个格，TVD 只有 %.3f（门 %.2f）—— 判据量不到落点集中'
         % (_ft, ONSET_TVD_MAX))
    print('        %d 个段落：落点偏离最大 %.3f（%s），门 %.2f' % (checked, worst[0], worst[1],
                                                              ONSET_TVD_MAX))
    assert not bad, '落点过于集中：%s' % '；'.join(bad[:4])


@check
def t_intro_gradience():
    """**引子渐入**（`arr.perc_in`）：段内前 N 小节不敲 —— 对齐真实模板的进法。

    依据：cheerful 10 首模板里 7 首前 4 小节有鼓，模式是 **b1–b2 安静、b3–b4 鼓组进来**
    （合计中位 18 点；单看 b1 多数是 0）。43 号原先引子 4 小节全静、第 5 小节一次性全开
    → 逐小节频谱质心 788 → 4907Hz，听感就是"第 8 秒突然变亮"。
    引擎侧实现：`perc_part(..., inbars=…)` 读段的 `arr.perc_in`。

    ⚠ **现状**：15 个主题包的 `form.plan` 全是 `A/A2/B/A3/C/A4/B2/A5`，**没有 intro** ——
    所以这条机制当前只在**带引子的曲目**（手写、或 43 号那种拆段写法）上生效。
    写这条守卫是为了让它别在无人知晓的情况下坏掉（也记录"新歌没有引子"这个事实）。

    **判据自证**：把 `perc_part` 包一层忽略 `inbars` → 必须抓到。
    """
    import song_engine as SE
    base = {
        'name': 'intro_probe', 'bpm': 120.0, 'meter': [4, 4], 'style': 'daily',
        'patterns': {'bass_style': 'simple', 'perc_style': 'dance'},
        'chords': {'C': [36, [55, 60, 64, 67]], 'G': [31, [55, 59, 62, 67]]},
        'melody': {'m': [[0, 0.0, 1.0, 72]]},
        'sections': [
            {'name': 'Intro', 'bars': 4, 'chords': ['C', 'C', 'G', 'G'], 'melody': 'm',
             'arr': {'bass': True, 'perc': 1, 'perc_in': 2}},
            {'name': 'A', 'bars': 2, 'chords': ['C', 'G'], 'melody': 'm',
             'arr': {'bass': True, 'perc': 1}},
        ],
    }
    tmp = os.path.join(TMP, 'intro_probe.json')

    def perc_beats(strip=False):
        d = json.loads(json.dumps(base))
        if strip:
            d['sections'][0]['arr'].pop('perc_in', None)
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f)
        ev = SE.build_events(SE.load(tmp))
        if not hasattr(ev, 'items'):
            ev = ev[0]
        return sorted(t for (t, _d, _m, _v) in ev.get('Perc', []))

    on = perc_beats()
    off = perc_beats(strip=True)
    assert off, '夹具没编出打击乐 —— 这条检查会空转'
    assert len(on) < len(off), \
        'perc_in 没有减少打击乐（带 %d 个 vs 不带 %d 个）—— 引子渐入失效' % (len(on), len(off))
    early = [t for t in on if t < 8.0]
    assert not early, '引子前 2 小节（拍 0~8）仍在敲打击乐：%s' % early[:5]
    assert [t for t in on if 8.0 <= t < 16.0], '引子第 3~4 小节没有打击乐（渐入没进来）'
    assert [t for t in on if t >= 16.0], 'A 段（拍 16 起）没有打击乐 —— `perc_in` 越界生效了'
    # **判据自证**：把 perc_part 包一层忽略 inbars → 必须抓到
    _orig = SE.perc_part
    try:
        SE.perc_part = lambda style, level, i, nbars, layers=None, kick_vel=None, \
            B=4.0, inbars=0: _orig(style, level, i, nbars, layers, kick_vel, B, 0)
        _bad = perc_beats()
    finally:
        SE.perc_part = _orig
    assert _bad and min(_bad) < 8.0, \
        ('判据自证失败：忽略 inbars 后引子前 2 小节仍是 %s —— 这条判据量不到渐入'
         % (sorted(_bad)[:5] if _bad else '空'))
    print('        引子渐入：带 perc_in %d 个鼓点 < 不带 %d 个；前 2 小节 %d 个（旧行为会敲）'
          % (len(on), len(off), 0))


def main():
    print('自检 %d 项 %s' % (len(CHECKS), '(--fast，跳过渲染)' if FAST else ''))
    for fn in CHECKS:
        name = fn.__name__[2:]
        try:
            fn()
            print('  PASS  %s' % name)
        except AssertionError as e:
            FAILS.append((name, str(e)))
            print('  FAIL  %s → %s' % (name, e))
        except Exception as e:
            FAILS.append((name, '%s: %s' % (type(e).__name__, e)))
            print('  ERR   %s → %s: %s' % (name, type(e).__name__, e))
    print('\n结果: %d/%d 通过' % (len(CHECKS) - len(FAILS), len(CHECKS)))
    if FAILS:
        print('失败项:')
        for n, m in FAILS:
            print('  - %s: %s' % (n, m))
    return 1 if FAILS else 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    sys.exit(main())
