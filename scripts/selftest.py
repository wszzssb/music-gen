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
from contextlib import redirect_stdout

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix='selftest_')
FAST = '--fast' in sys.argv

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
        assert c.get('composer'), '%s: 缺 composer' % name
        comp = c['composer']
        cp = os.path.join(ROOT, comp) if ('/' in comp or '\\' in comp) \
            else os.path.join(d, comp)
        assert os.path.exists(cp), '%s: composer 不存在 %s' % (name, cp)
        refp = os.path.join(ROOT, 'refs', c.get('ref', '') + '.json')
        assert os.path.exists(refp), '%s: 参考画像不存在 %s' % (name, c.get('ref'))


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
              os.path.join(ROOT, 'docs', 'SONG-FORMAT.md'), files[-1]]
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


@check
def t_melody_breathing():
    """旋律**呼吸度**提示：过满的曲子听起来累（每拍都有新音、无长音、无静音）。

    为什么单列一条：实测一首 90 小节、每小节填 4 个音的曲子，时值被自动推成整齐的
    1 拍 —— 全曲最长音只有 2 拍、音符覆盖率 91%，用户听完反馈"旋律中间一直没有停顿，
    听起来好累"。这条**只提示不判错**（库里早于本约定写的曲子，重写要动旋律），
    打印清单供人工处置。新歌请遵守：每 4 小节的句尾留长音（>=3 拍）或留 2 拍静音。
    """
    thin, checked = [], 0
    for d in song_dirs():
        j2 = json.load(open(os.path.join(d, 'song.json'), encoding='utf-8'))
        tot = lng = dur = 0.0
        n = 0
        for sec in j2['sections']:
            bars = sec['bars']
            tot += bars * 4.0
            arr = [x for x in (j2['melody'].get(sec['melody']) or [])
                   if 0 <= x[0] < bars]
            n += len(arr)
            for x in arr:
                lng = max(lng, x[2])
                dur += x[2]
        if tot <= 0:
            continue
        checked += 1
        if n / tot * 4 > 3.5 and lng <= 2.0 and (1 - dur / tot) < 0.08:
            thin.append('%s(%d音/小节,最长%.0f拍,静音%.0f%%)'
                        % (os.path.basename(d), n / tot * 4, lng, (1 - dur / tot) * 100))
    assert checked > 0, '没有可检查的曲目（songs/ 路径或 glob 坏了）'
    if thin:
        print('        （旋律过满提示：%s —— 每拍都有新音且无长音/静音，听感会累）'
              % '; '.join(thin))



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
def t_style_desc_matches_programs():
    """风格预设的文字说明要与实际音色一致（防复制粘贴串味）"""
    want = {'gorgeous': ('竖琴', 'Hook', 46), 'ballad': ('尼龙', 'Hook', 24),
            'acoustic': ('钢弦', 'Hook', 25), 'daily': ('钢弦', 'Hook', 25),
            'dance': ('合成主奏', 'Melody', 81)}
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
        bar = 4 * 60.0 / data['bpm']
        y, sr = sf.read(wav, dtype='float64', always_2d=True)
        dur = len(y) / sr
        expect = nbars * bar
        assert expect - 0.6 <= dur <= expect + 8.0, \
            '%s 时长 %.1fs 与谱面 %.1fs 不符（多出的是混响尾，正常 ≤8s）' % (name, dur, expect)
        mono = y.mean(axis=1)
        bar0 = 0
        for sec in data['sections']:
            n = sec['bars']
            t0, t1 = bar0 * bar, (bar0 + n) * bar
            seg = mono[int(t0 * sr):int(min(t1, dur) * sr)]
            notes = sum(len(v) for tr, v in ev.items()
                        for (t, _dd, _m, _v) in v if bar0 * 4 <= t < (bar0 + n) * 4)
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
    （漂移意味着每次重出成品都会变，是静默的不确定性）"""
    if FAST:
        return
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
    quiet(make_song.autotune, c1, ref, mid, out, 6)
    gaps1 = make_song.target_gaps(make_song.measure(out + '.wav', ref), ref)
    w1 = open(out + '.wav', 'rb').read()
    c2 = dict(c1)
    quiet(make_song.autotune, c2, ref, mid, out, 6)
    gaps2 = make_song.target_gaps(make_song.measure(out + '.wav', ref), ref)
    drift = max(abs(c1[k] - c2[k]) for k in c1 if k != 'rms')
    assert drift <= 0.25, '第二次调参把参数改了 %.2f（不幂等）: %s → %s' % (
        drift, c1, c2)
    worst = max(abs(gaps2[k]) for k in ('low', 'mid_db', 'shelf', 'width'))
    assert worst <= 2.5, '二次运行后误差反而大: %s' % gaps2


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

    判据：强拍（每小节第 1、3 拍）和弦音占比 ≥ 70%；根音上方半音（♭9，最刺耳）≤ 2 处。
    弱拍不做限制 —— 经过音/倚音本来就该在弱拍。"""
    rows = []
    for d in songs_or_fail():
        name = os.path.basename(d)
        data = song_engine.load(os.path.join(d, 'song.json'))
        chords = data['chords']
        tot = fit = b9 = 0
        for sec in data['sections']:
            mel = data['melody'].get(sec['melody'], []) + (sec.get('melody_extra') or [])
            for (b, beat, _dur, m) in mel:
                if beat not in (0.0, 2.0) or b >= len(sec['chords']):
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
                     + ' —— 改 song.json 的 melody：每小节第 1、3 拍用该小节和弦的音')
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
         'melody': {'m': [[0, 0, 2, 76], [0, 2, 2, 79]]},      # E5 / G5
         'sections': [{'name': 'A', 'bars': 2, 'chords': ['C', 'C'], 'melody': 'm',
                       'arr': {'piano': True, 'strings': True, 'harmony': True}}]}
    sp = os.path.join(TMP, 'harm.json')
    json.dump(d, open(sp, 'w', encoding='utf-8'))
    ev, _nb = build(quiet(song_engine.load, sp)[0])
    tones = [48, 52, 55, 60, 64]
    harm = [m for (_t, _d, m, _v) in ev['Hook'] + ev['Strings'] if m in
            [t - 3 for t in tones] + [t - 4 for t in tones]]
    assert harm, 'harmony 没有产出和弦内低三度'
    for (_t, _d, m, _v) in ev['Melody']:
        pass
    for tr in ('Hook', 'Strings'):
        for (_t, _d, m, _v) in ev[tr]:
            if 65 <= m <= 76:                     # 副旋律音区
                assert m % 12 in [t % 12 for t in tones], \
                    '副旋律音 %d 不是和弦音（会不协和）' % m


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
    # 轨名: (最低, 最高)。原区间 = 库内 17 首实测包络；现上下各**外扩 7 半音**
    # （一个纯五度）—— 实测它原来会拦住正常的音区探索（如把主歌旋律下移八度到 F2，
    # 钢琴完全可行却报超界）。外扩后仍能抓住它真正要防的事故：**整体移一两个八度**
    # （差 12 半音 > 7）。Bass 下界保持 16：次声波是真实事故，不放。
    RANGE = {
        'Arp': (44, 111),
        'Bass': (16, 71),
        'Glock': (63, 115),
        'Hook': (32, 91),
        'Melody': (43, 103),
        'Pad': (29, 83),
        'Piano': (29, 97),
        'Strings': (41, 99),
    }
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
