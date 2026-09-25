#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变异测试：**验证自检自己会不会报警**。

做法：故意注入已知故障（改数据/改模块），跑对应的那条检查，期望它 FAIL。
如果注入故障后检查仍然 PASS —— 说明那条防线是坏的（比产品 bug 更危险：它会掩盖一切）。

用法: python scripts/mutation_check.py
"""
import glob
import io
import json
import os
import sys
import tempfile
import atexit
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import selftest as st          # noqa: E402
import check_song as cs        # noqa: E402  # 它的 strict_downbeats 是独立判据
import song_engine             # noqa: E402
import render_midi             # noqa: E402
import bgm_synth as bs         # noqa: E402
import make_song as ms         # noqa: E402
import metrics
import breath
import token_audit             # noqa: E402
import json_io                 # noqa: E402

TMP = tempfile.mkdtemp(prefix='mutation_')


def _cleanup_tmp():
    """退出时删掉临时目录（**它以前从不清理**，实测累积 600+ 个 / 上百 MB；
    `DSH_KEEP_TMP=1` 时保留，便于回看注入用例的中间产物）"""
    if os.environ.get('DSH_KEEP_TMP'):
        print('  (DSH_KEEP_TMP=1：保留临时目录 %s)' % TMP)
        return
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)


atexit.register(_cleanup_tmp)


GOOD_SONG = {'name': 'mut', 'bpm': 120, 'style': 'daily',
             'chords': {'D': [38, [57, 62, 66, 69, 74]]},
             'melody': {'m': [[0, 0, 1, 74]]},
             'sections': [{'name': 'A', 'bars': 1, 'chords': ['D'], 'melody': 'm',
                           'arr': {'uku': True, 'bass': True, 'perc': 1}}]}


def temp_song_dir(mutate=None):
    d = tempfile.mkdtemp(dir=TMP)
    data = json.loads(json.dumps(GOOD_SONG))
    if mutate:
        mutate(data)
    json.dump(data, open(os.path.join(d, 'song.json'), 'w', encoding='utf-8'))
    return d


_AUDIO_FIX = []


def audio_fixture():
    """**给音频类检查造一份真夹具**：一首 16 小节的小曲 + 真渲染出的 `_sf.wav` + render.json。

    为什么必须有它：仓库现在**只留 MIDI**（用户要求，见 .gitignore），所以 `songs/` 里没有
    任何音频 —— `audio_health` / `audio_semantics` / `alignment_vs_refs` 会**空转**
    （它们只遍历存在的音频，一个都没有就整条跳过）。空转的检查在变异测试里表现为"漏了"，
    而实际上故障注入根本没碰到东西。夹具让这三条检查**真有东西可查**，
    于是"注入故障 → 必须被抓"才有意义（这类"空集合假通过"坑 117 系列已踩过多次）。

    **长度必须够**：短夹具（1 小节 = 2s）配 `audio_semantics` 的"+8s 混响尾"宽松窗口，
    "时长只有一半"根本落不进窗口（实测漏了）；16 小节 = 32s，砍半 16s 必然越界。
    """
    import song_engine
    import render_midi
    if _AUDIO_FIX:
        return _AUDIO_FIX[0]
    d = tempfile.mkdtemp(dir=TMP)
    data = {'name': 'mutfix', 'bpm': 120, 'style': 'daily',
            'chords': {'C': [36, [55, 60, 64, 67, 72]]},
            'melody': {'m': [[b, 0, 2.0, 72] for b in range(16)]},
            'sections': [{'name': 'A', 'bars': 16, 'chords': ['C'] * 16, 'melody': 'm',
                          'arr': {'uku': True, 'piano': True, 'bass': True,
                                  'pad': True, 'glock': True, 'perc': 1}}]}
    json.dump(data, open(os.path.join(d, 'song.json'), 'w', encoding='utf-8'))
    mid = os.path.join(d, 'mutfix.mid')
    with redirect_stdout(io.StringIO()):
        song_engine.compose(os.path.join(d, 'song.json'), mid)
        render_midi.render(mid, os.path.join(d, 'mutfix_sf'), rms_db=-16.9, width=1.5,
                           shelf_db=3.0, hp_hz=38.0, low_db=0.0, drive=1.6,
                           mid_db=0.0, verbose=False, ogg=False)
    json.dump({'composer': 'compose.py', 'mid': 'mutfix.mid', 'out': 'mutfix_sf',
               'ref': 'BGM16c'},
              open(os.path.join(d, 'render.json'), 'w', encoding='utf-8'))
    _AUDIO_FIX.append(d)
    return d


def with_fixture(mutate):
    """把 `song_dirs()` 指向音频夹具，再叠上故障注入（两层上下文一起进）"""
    class _Both:
        def __enter__(self):
            self.m = mutate()
            self.fx = Mut(st, 'song_dirs', lambda **k: [audio_fixture()])
            self.fx.__enter__()
            self.m.__enter__()
            return self

        def __exit__(self, *a):
            self.m.__exit__(*a)
            return self.fx.__exit__(*a)
    return _Both


def run_check(name):
    """跑指定检查，返回 `(True=真拦下 / False=没抓到 / None=**检查自己崩了**)`。

    ⚠ **`None` 这一态是 2026-09-25 加的**：原来所有异常都算"抓到"，于是
    `IndexError` / `TypeError` 这种**夹具不满足前提**导致的崩溃，会被记成一次成功的
    拦截 —— mutation 全绿、防线其实早失效（实测：`midi_ops_semantics` 挑到"第 0 轨前 8 拍
    没音符"的夹具，直接 `IndexError`，却一直显示"抓到"）。**崩掉 ≠ 通过**（PITFALLS 251）。
    """
    fn = dict((f.__name__[2:], f) for f in st.CHECKS).get(name)
    if fn is None:
        return False, '找不到检查 %s' % name
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            fn()
        return False, '检查通过（没抓到注入的故障）'
    except (AssertionError, SystemExit) as e:
        return True, str(e)[:90]
    except Exception as e:
        return None, '%s: %s' % (type(e).__name__, str(e)[:70])


class Mut:
    """临时替换某对象的属性（退出时还原）"""
    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)

    def __exit__(self, *a):
        setattr(self.obj, self.name, self.old)


class MutMany:
    """一次替换多个属性（`Mut` 只能一个）—— 有些判据是"几个门居其一"，
    只关一个门证明不了它真在量那样东西。"""
    def __init__(self, pairs):
        self.pairs = pairs
        self.olds = []

    def __enter__(self):
        for obj, name, value in self.pairs:
            self.olds.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

    def __exit__(self, *a):
        for obj, name, old in self.olds:
            setattr(obj, name, old)


class SkipCase(Exception):
    """夹具不存在时**跳过**该变异用例（既不判漏、也不判过）。

    为什么需要它：有的用例要拿"某首真曲"当夹具（如"拆掉某曲的旋律复用豁免"），
    而曲目是会被删的（2026-09-15 删了 7 首旧欢快曲，含 43_joy_to_sorrow）——
    硬编码曲名会让整轮 mutation 直接 FileNotFoundError 崩掉，而不是给出可读的跳过。
    """


def case(label, check, mutate):
    try:
        with mutate():
            caught, why = run_check(check)
    except SkipCase as e:
        print('  %-5s %-34s → %s' % ('跳过', label, e))
        return True                      # 不算漏
    if caught is None:
        # **检查自己崩了**：不算抓到（PITFALLS 251 —— "崩掉 ≠ 通过"）。
        # 报"崩了"是为了让人去修**夹具或前提**，而不是误以为防线有效。
        print('  %-5s %-34s → %s' % ('**崩了**', label, why))
        return False
    ok = caught
    print('  %-5s %-34s → %s' % ('抓到' if ok else '**漏了**', label, why))
    return ok


def case_check_song(label, path, break_fn):
    """针对 check_song.py 里**独立于 selftest** 的那条判据做变异测试。

    check_song 复用 selftest 的 66 项判据（那部分由上面的 case 覆盖），但它自己多了一条
    **100% 严判据** `strict_downbeats` —— 自检里那条是"全库 ≥70%"的松门，单个错音抓不到，
    所以必须单独证明它坏得起来（否则就是个装饰性绿灯）。"""
    import shutil
    tmp = tempfile.mkdtemp(dir=TMP)
    dst = os.path.join(tmp, 'song.json')
    shutil.copy2(path, dst)
    break_fn(dst)                      # 就地把复制出来的 song.json 改坏
    bad = cs._strict_downbeats(dst)
    ok = bool(bad)
    print('  %-5s %-34s → %s' % ('抓到' if ok else '**漏了**', label,
                                 (bad[0] if bad else '严判据没报警（注入的故障漏了）')))
    return ok


def main():
    print('变异测试：注入故障，看自检会不会报警')
    results = []
    real_song_dirs = st.song_dirs          # 注入"部分曲目"用

    # 0. 模仿写歌没留痕（2026-09-19：模仿写歌 / 直接作曲分开后的"防错用"判据）
    #    判据内容是"段数 ≠ 主题包 form.plan 时必须有 basis.structure_source=imitate:<参考曲>"，
    #    所以夹具 = 一首**真的模仿路径曲目**（40_imitate_b35，26 段 vs night 包 6 段），
    #    注入 = 把留痕抹掉。没有这类曲目时跳过（曲目会被删，硬编码会崩整轮）。
    def _no_imitate_mark():
        import shutil
        p40 = os.path.join(ROOT, 'songs', '40_imitate_b35', 'song.json')
        if not os.path.exists(p40):
            raise SkipCase('没有模仿路径曲目（40_imitate_b35）当夹具')
        tmpd = os.path.join(TMP, 'imitate_unmarked')
        shutil.rmtree(tmpd, ignore_errors=True)
        os.makedirs(tmpd)
        j = json.load(open(p40, encoding='utf-8'))
        (j.get('basis') or {}).pop('structure_source', None)
        json.dump(j, open(os.path.join(tmpd, 'song.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        return Mut(st, 'song_dirs', lambda **k: [tmpd])
    results.append(case('模仿写歌没留痕（结构改过）', 'imitate_path_marked', _no_imitate_mark))

    # 0b. 识别交叉判读被换成"永远说一致"（2026-09-19：识别功能自己的守卫）
    def _always_agree():
        import identify_ref as IR

        def fake(stems, ymt3):
            return {'agree': ['全都一致'], 'conflict': [], 'ymt3_only': [],
                    'demucs_only': [], 'absent': []}
        return Mut(IR, 'cross_check', fake)
    results.append(case('识别交叉判读永远说一致', 'identify_cross_rules', _always_agree))

    # 0b2. YMT3 改回"整曲一次喂"（实测慢 16.6 倍）—— 防回退守卫必须红
    def _ymt3_regressed():
        return Mut(st, '_ymt3_grouped_ok', lambda src: True)
    results.append(case('YMT3 改回整曲一次喂', 'ymt3_grouped_inference', _ymt3_regressed))

    # 0b3. 引擎生成层退回"段名规则"（2026-09-25：`--auto` 的段名是 S01…S24，
    #      任何按 'A'/'B'/'C'/'Ending' 判断的规则对它们**恒为同一个值** → arp/pad/glock/shimmer
    #      段段全开；实测引子第 1–4 小节转录 0~1 音、引擎却生成 17 音 → 用户"前面有点乱"）
    def _arr_regressed():
        import transcribe_to_song as T
        return Mut(T, 'gen_layer_on', lambda c: True)
    results.append(case('引擎生成层退回段名规则', 'transcribe_arr_by_source', _arr_regressed))

    # 0c. 密度起伏被抹平（2026-09-19 修口径后补：判据改量逐小节，注入要能红）
    def _flat_density():
        src = os.path.join(ROOT, 'songs', '41_imitate_b16', 'song.json')
        if not os.path.exists(src):
            raise SkipCase('没有 41_imitate_b16 当夹具')
        td = os.path.join(TMP, 'flat_density')
        os.makedirs(td, exist_ok=True)
        j = json.load(open(src, encoding='utf-8'))
        for s in j['sections']:
            s.setdefault('arr', {})['density'] = 2      # 全段抹平
        json.dump(j, open(os.path.join(td, 'song.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        real_glob = st.glob.glob
        return Mut(st.glob, 'glob',
                   lambda pat, **kw: [os.path.join(td, 'song.json')]
                   if str(pat).endswith('song.json') else real_glob(pat, **kw))
    results.append(case('密度抹平（全段 density=2）', 'density_dynamic_range', _flat_density))

    # 1. 和弦音写错
    d = temp_song_dir(lambda x: x['chords'].__setitem__('D', [38, [57, 62, 66, 69, 75]]))
    results.append(case('和弦里混进不属于它的音',
                        'chord_names_match_notes',
                        lambda: Mut(st, 'song_dirs', lambda **k: [d])))

    # 2. 通道重复
    def dup_chan(x):
        x['programs'] = {'Melody': [0, 0], 'Hook': [25, 1], 'Bass': [32, 1]}
    d2 = temp_song_dir(dup_chan)
    results.append(case('两轨用同一 MIDI 通道', 'channels_and_programs',
                        lambda: Mut(st, 'song_dirs', lambda **k: [d2])))

    # 3. 风格预设通道冲突
    def break_style():
        old = song_engine.STYLES['daily']['programs']['Pad']
        song_engine.STYLES['daily']['programs']['Pad'] = (89, 3)   # 与 Arp 撞
        return old
    def restore_style(old):
        song_engine.STYLES['daily']['programs']['Pad'] = old

    class StyleMut:
        def __enter__(self):
            self.old = break_style()
        def __exit__(self, *a):
            restore_style(self.old)
    results.append(case('风格预设通道冲突', 'styles_channels_and_programs', StyleMut))

    # 4. 旋律小节越界
    d3 = temp_song_dir(lambda x: x['melody'].__setitem__('m', [[5, 0, 1, 74]]))
    results.append(case('旋律小节偏移越界', 'melody_within_sections',
                        lambda: Mut(st, 'song_dirs', lambda **k: [d3])))

    # 5. 段落和弦数与小节数不符
    d4 = temp_song_dir(lambda x: x['sections'][0]['chords'].append('D'))
    results.append(case('段落和弦数 ≠ 小节数', 'melody_within_sections',
                        lambda: Mut(st, 'song_dirs', lambda **k: [d4])))

    # 6. 限幅越界（回到修复前的实现）
    results.append(case('软限幅输出超过满刻度', 'dsp_clean',
                        lambda: Mut(render_midi, 'soft_limit',
                                    lambda x, drive=1.6:
                                    __import__('numpy').tanh(x * drive)
                                    / __import__('numpy').tanh(drive))))

    # 7. MIDI 写入丢音符（保持签名，只丢事件）
    real_write = bs.write_midi

    def lossy_write(path, tracks, ppq=480, *a, **kw):
        out = []
        for t in tracks:
            name, prog, chan, ev = t[:4]
            ccs = t[4] if len(t) > 4 else []
            out.append((name, prog, chan, ev[::3], ccs))
        return real_write(path, out, ppq)
    results.append(case('MIDI 写入丢掉 2/3 音符', 'midi_roundtrip',
                        lambda: Mut(bs, 'write_midi', lossy_write)))

    # 8. 参考画像缺字段（替换 glob 模块的 .glob，别把模块换掉）
    import types
    import glob as _glob
    bad_ref_dir = tempfile.mkdtemp(dir=TMP)
    bad_ref = os.path.join(bad_ref_dir, 'broken.json')
    json.dump({'name': 'broken'}, open(bad_ref, 'w', encoding='utf-8', newline='\n'))
    shim = types.SimpleNamespace(
        glob=lambda pat, **k: ([bad_ref] if os.sep + 'refs' in pat
                               else _glob.glob(pat, **k)))
    results.append(case('参考画像缺字段', 'refs_schema',
                        lambda: Mut(st, 'glob', shim)))

    # 9. voicing_shift 失效
    real_build = song_engine.build_events

    def ignore_shift(d):
        d['patterns']['voicing_shift'] = 0
        return real_build(d)
    results.append(case('voicing_shift 被忽略', 'voicing_shift',
                        lambda: Mut(song_engine, 'build_events', ignore_shift)))

    # 10. 响度契约失效（渲染完偷偷降 6dB）
    import soundfile as sf
    real_render = render_midi.render

    def quiet_render(mid, out_base, *a, **kw):
        w, o = real_render(mid, out_base, *a, **kw)
        y, sr = sf.read(w, dtype='float64', always_2d=True)
        sf.write(w, y * 0.5, sr, subtype='PCM_16')
        return w, o
    results.append(case('渲染后响度被降 6dB', 'render_rms_contract',
                        lambda: Mut(render_midi, 'render', quiet_render)))

    # ================= 第 2 组：空集合假通过（审计发现 11 条检查曾集体空转）=========
    # 注入方式统一：把 song_dirs() 换成空列表。以前这些检查会**照样 PASS**。
    VACUOUS = ['song_json_buildable', 'render_json_schema', 'chord_names_match_notes',
               'melody_within_sections', 'channels_and_programs', 'outputs_exist',
               'notes_present', 'determinism_and_bytes', 'audio_health',
               'audio_semantics', 'unused_chords_warn']
    for nm in VACUOUS:
        results.append(case('曲目列表为空（%s）' % nm, nm,
                            lambda: Mut(st, 'song_dirs', lambda **k: [])))

    # ================= 第 3 组：数据/引擎类 =================
    # 11. 越界音高（应报错而不是静默回绕）
    d5 = temp_song_dir(lambda x: x['melody'].__setitem__('m', [[0, 0, 1, 200]]))
    results.append(case('旋律音高越界(200)', 'song_json_buildable',
                        lambda: Mut(st, 'song_dirs', lambda **k: [d5])))

    # 12. 段落引用不存在的和弦
    d6 = temp_song_dir(lambda x: x['sections'][0].__setitem__('chords', ['Zzz']))
    results.append(case('段落引用未定义和弦', 'song_json_buildable',
                        lambda: Mut(st, 'song_dirs', lambda **k: [d6])))

    # 13. 非确定性编配（同一份数据两次结果不同）
    real_be = song_engine.build_events
    cnt = {'n': 0}

    def jitter_build(d):
        ev, nb = real_be(d)
        cnt['n'] += 1
        for tr in list(ev)[:1]:
            if ev[tr]:
                t, du, m, v = ev[tr][0]
                ev[tr][0] = (t, du, m, min(127, v + (1 if cnt['n'] % 2 else 0)))
        return ev, nb
    results.append(case('编配不确定（两次输出不同）', 'determinism_and_bytes',
                        lambda: Mut(song_engine, 'build_events', jitter_build)))

    # 14. ep 编配开关被忽略（死代码回归）
    def ignore_ep(d):
        for sec in d.get('sections', []):
            (sec.get('arr') or {}).pop('ep', None)
        return real_be(d)
    results.append(case('arr.ep 被忽略', 'ep_part_wired',
                        lambda: Mut(song_engine, 'build_events', ignore_ep)))

    # 15. 未知风格名被静默接受（应报错）
    real_load = song_engine.load
    results.append(case('未知风格被静默接受', 'style_unknown',
                        lambda: Mut(song_engine, 'load',
                                    lambda p: {'name': 'x', 'style': 'nope', 'bpm': 120,
                                               'chords': {}, 'melody': {},
                                               'sections': [], 'patterns': {},
                                               'programs': {}, 'mix': {}})))

    # 16. 拼错的编配开关被静默丢弃（应报警告）
    def silent_arr(p):
        import json as _j
        d = _j.load(open(p, encoding='utf-8'))
        for sec in d.get('sections', []):
            arr = sec.get('arr') or {}
            sec['arr'] = {k: v for k, v in arr.items()
                          if k in ('uku', 'piano', 'bass', 'pad', 'strings', 'glock',
                                   'arp', 'perc', 'ep', 'vel')}
        tmp2 = os.path.join(TMP, 'silent_arr.json')
        _j.dump(d, open(tmp2, 'w', encoding='utf-8', newline='\n'), ensure_ascii=False)
        return real_load(tmp2)
    results.append(case('拼错的编配开关被静默丢弃', 'bad_arr_key_warns',
                        lambda: Mut(song_engine, 'load', silent_arr)))

    # 17. 畸形输入被当成合法（load 不再报错）
    results.append(case('畸形 song.json 被接受', 'malformed_inputs',
                        lambda: Mut(song_engine, 'load',
                                    lambda p: {'name': 'x', 'bpm': 120, 'chords': {},
                                               'melody': {}, 'sections': [],
                                               'patterns': {}, 'programs': {}, 'mix': {}})))

    # 18. 风格说明与实际音色不符（复制粘贴串味）
    def break_desc():
        return song_engine.STYLES['daily']['desc']

    class DescMut:
        def __enter__(self):
            self.old = break_desc()
            song_engine.STYLES['daily']['desc'] = '舞曲：合成主奏 + 四踩底鼓'
        def __exit__(self, *a):
            song_engine.STYLES['daily']['desc'] = self.old
    results.append(case('风格说明与音色不符', 'style_desc_matches_programs', DescMut))

    # ================= 第 4 组：测量仪器的准确性 =================
    # 19. 质心算错（仪器坏了，后面所有分数都不可信）
    real_centroid = st.metrics.centroid
    results.append(case('质心测量算错 2 倍', 'metrics_instrument_accuracy',
                        lambda: Mut(st.metrics, 'centroid',
                                    lambda m, sr: real_centroid(m, sr) * 2)))
    # 20. 宽度测量整体偏 0.5
    real_width = st.metrics.width
    results.append(case('宽度测量偏 0.5', 'metrics_instrument_accuracy',
                        lambda: Mut(st.metrics, 'width', lambda x: real_width(x) + 0.5)))

    # 21~23. 音频故障：这两条检查**自己直接读音频**（sf.read），不走 metrics.load。
    # **必须自带夹具**：仓库只留 MIDI → songs/ 里没有音频，不注入夹具的话这三条
    # 会空转（变异测试里表现为"漏了"，其实是没东西可查）。
    real_sf_read = st.sf.read
    results.append(case('成品音频削波', 'audio_health',
                        with_fixture(lambda: Mut(st.sf, 'read',
                                                 lambda f, **k: (lambda t: (t[0] * 4.0, t[1]))(
                                                     real_sf_read(f, **k))))))
    results.append(case('成品时长只有一半', 'audio_semantics',
                        with_fixture(lambda: Mut(st.sf, 'read',
                                                 lambda f, **k: (lambda t: (t[0][:len(t[0]) // 2], t[1]))(
                                                     real_sf_read(f, **k))))))
    results.append(case('有声段落被静音', 'audio_semantics',
                        with_fixture(lambda: Mut(st.sf, 'read',
                                                 lambda f, **k: (lambda t: (t[0] * 1e-6, t[1]))(
                                                     real_sf_read(f, **k))))))

    # 24. 人声/器乐判定永远返回 instrumental（静默改对标靶子）
    results.append(case('人声判定永远 instrumental', 'vocal_classifier_sanity',
                        lambda: Mut(st.metrics, 'character_of',
                                    lambda bands: ('instrumental',
                                                   [k for k in bands if k != '20-40']))))

    # ================= 第 5 组：调参与建议口径 =================
    # 25. 调参符号反了（偏亮却往上调）
    real_tune = ms.tune_step
    results.append(case('调参方向反了', 'tune_step_signs',
                        lambda: Mut(ms, 'tune_step',
                                    lambda mine, ref, cfg: (
                                        {k: -v for k, v in real_tune(mine, ref, cfg)[0].items()},
                                        real_tune(mine, ref, cfg)[1]))))

    # 26. 参数无上限（夹紧失效）
    class NoLimit:
        def __enter__(self):
            self.old = dict(ms.LIMITS)
            for k in ms.LIMITS:
                ms.LIMITS[k] = (-99.0, 99.0)
        def __exit__(self, *a):
            ms.LIMITS.clear()
            ms.LIMITS.update(self.old)
    results.append(case('参数上限失效（不报"到顶"）', 'autotune_reports_caps', NoLimit))

    # 27. 宽度精确校准变成空操作
    results.append(case('宽度精确校准失效', 'width_exact_extremes',
                        lambda: Mut(st.render_midi, 'set_width_exact',
                                    lambda *a, **k: None)))

    # 28. 成绩单无视"参数已到限"（又给反方向建议）
    real_eq = st.scorecard._eq_target
    results.append(case('无视参数到限，给反方向建议', 'suggest_respects_alignment',
                        lambda: Mut(st.scorecard, '_eq_target',
                                    lambda cfg, key, d, step: (d * step, False))))

    # 29. 速度又靠音频测速猜（MIDI 里明明是 120）
    results.append(case('速度改用音频测速', 'bpm_from_midi_not_guess',
                        lambda: Mut(ms, 'midi_bpm',
                                    lambda mid, data=None: 154.3)))

    # ================= 第 6 组：对标豁免与文档契约 =================
    # 30. 参考画像整体偏移 20dB（对标检查必须咬住"真的漂了"）
    real_load_ref = st.scorecard.load_ref

    def shifted_ref(spec):
        r = dict(real_load_ref(spec))
        r['bands'] = {k: v + 20.0 for k, v in r['bands'].items()}
        return r
    class _StrictAndShift:
        """画像整体偏移 20dB **并且**给有音频的曲目临时声明 strict_align=true。

        `alignment_vs_refs` 改版后**默认只提示**（创作不该被参考画像绑架）；
        只有曲目声明 strict_align 才把 8dB 当门 —— 所以注入故障时必须同时声明，
        这个用例才测得到"这条检查仍咬得住真的漂了"。跑完原样恢复。

        ⚠ 需要**有音频的曲目**：仓库只留 MIDI 时它一个都找不到 → 整条空转（曾表现为"漏了"），
        所以外部套 `with_fixture` 给一份真渲染的夹具。
        """

        def __enter__(self):
            import glob as _g
            self.saved = {}
            for d in st.song_dirs():
                rp = os.path.join(d, 'render.json')
                if os.path.exists(rp) and _g.glob(os.path.join(d, '*_sf.wav')):
                    self.saved[rp] = open(rp, encoding='utf-8').read()
                    c = json.loads(self.saved[rp])
                    c['strict_align'] = True
                    json.dump(c, open(rp, 'w', encoding='utf-8', newline='\n'),
                              ensure_ascii=False, indent=1)
            self._ref = st.scorecard.load_ref
            st.scorecard.load_ref = shifted_ref
            return self

        def __exit__(self, *a):
            for rp, txt in self.saved.items():
                open(rp, 'w', encoding='utf-8', newline='\n').write(txt)
            st.scorecard.load_ref = self._ref

    results.append(case('画像整体偏移 20dB（声明 strict_align 后必须被抓）',
                        'alignment_vs_refs', with_fixture(_StrictAndShift)))

    # 31. 豁免理由留空也算数（等于检查可被一句空话绕过）
    results.append(case('空白理由被当成有效豁免', 'alignment_vs_refs',
                        lambda: Mut(st, '_exempt_bands',
                                    lambda cfg: (cfg or {}).get('align_exempt') or {})))

    # 32. 真实曲目被漏掉一半（发现机制坏了，检查却"全绿"）
    results.append(case('只发现一半曲目', 'outputs_exist',
                        lambda: Mut(st, 'song_dirs',
                                    lambda **k: real_song_dirs(**k)[:max(1, len(real_song_dirs(**k)) // 2)])))

    # 33. 文档引用了不存在的脚本
    results.append(case('文档引用不存在的脚本', 'docs_paths',
                        lambda: Mut(st, 're', __import__('types').SimpleNamespace(
                            findall=lambda pat, txt: (['ghost_tool.py']
                                                      if 'scripts' in pat else [])))))
    # 34. 文档体积超预算（文档膨胀 = 每次写歌都变贵）
    results.append(case('技能/文档超预算', 'docs_budget_and_skill_intact',
                        lambda: Mut(token_audit, 'est', lambda s: 10 ** 6)))
    # 35. 技能 frontmatter 丢失（技能会从可用列表消失）
    class FmMut:
        def __enter__(self):
            self.old = token_audit.DOCS['SKILL.md（音乐任务加载）']
            p = os.path.join(TMP, 'nofront.md')
            open(p, 'w', encoding='utf-8', newline='\n').write('# BGM Studio\n\n（没有 frontmatter）')
            token_audit.DOCS['SKILL.md（音乐任务加载）'] = p
        def __exit__(self, *a):
            token_audit.DOCS['SKILL.md（音乐任务加载）'] = self.old
    results.append(case('技能 frontmatter 丢失', 'docs_budget_and_skill_intact', FmMut))

    # 35b/35c. 技能指针（description）的 catalog 显示预算与顺序
    #   （catalog 是**砍尾**的：超预算时末尾内容直接消失，所以顺序本身就是正确性）
    class DescMut:
        """把 SKILL.md 换成 frontmatter 可控的假文件 —— 与真实文件结构解耦，
        这样用例只测守卫逻辑，不会因为以后改写 description 而失效。"""
        def __init__(self, desc):
            self.desc = desc
        def __enter__(self):
            self.old = token_audit.DOCS['SKILL.md（音乐任务加载）']
            p = os.path.join(TMP, 'desc-mut.md')
            open(p, 'w', encoding='utf-8', newline='\n').write(
                '---\nname: bgm-studio\ndescription: %s\n---\n\n# x\n' % self.desc)
            token_audit.DOCS['SKILL.md（音乐任务加载）'] = p
        def __exit__(self, *a):
            token_audit.DOCS['SKILL.md（音乐任务加载）'] = self.old

    results.append(case('技能指针超显示预算', 'docs_budget_and_skill_intact',
                        lambda: DescMut('音乐 四条铁律 触发词' + '填' * 600)))
    results.append(case('技能指针顺序倒置', 'docs_budget_and_skill_intact',
                        lambda: DescMut('音乐 触发词 四条铁律 和谐优先 改必须分段 '
                                        'song.json SHA256 8765 venv')))

    # 36. **文档地图过期 / 丢失**（2026-09-21）
    #     地图全是行号（`docs/DOC-MAP.md` 由 `scripts/doc_map.py` 生成）——
    #     改了文档或改了分类却不重新生成，行号就全漂，而**看的人不会知道**。
    #     ⚠ 注入点要挑**真的会改变输出**的：
    #       ① `GROUPS` 少一个域 = "分类改了/新文档没归类却没重生成"（走内容不一致）；
    #       ② `OUT` 指到不存在的路径 = "地图被删/改名"（必须报"不存在"，不许静默跳过）。
    #     反例（实测）：拿 `MIN_SEC_TOK` 注入**打不进去** —— 它只作用于"非巨型且节数 >20"
    #     的文档，而当前这类文档一个都没有（HISTORY / CASE-BGM35-FINDINGS 都是巨型，
    #     走 `HUGE_MINS`）→ 注入后输出一字不变，守卫"通过"其实是**假通过**。
    import doc_map as _dm
    results.append(case('文档地图过期（分类改了没重生成）', 'doc_map_fresh',
                        lambda: Mut(_dm, 'GROUPS', _dm.GROUPS[:-1])))
    results.append(case('文档地图被删/改名', 'doc_map_fresh',
                        lambda: Mut(_dm, 'OUT', os.path.join(ROOT, 'docs',
                                                             'no_such_doc_map.md'))))
    # 36c. **文档漏登记预算**（`LIMITS` 少一项 → 它写多少都不会报警）。
    #      实景：`HISTORY.md`（全库最大 38.4k）长期不在 `LIMITS` 里，直到 2026-09-21 查疏漏。
    results.append(case('文档漏登记预算（膨胀无人报警）', 'docs_budget_and_skill_intact',
                        lambda: Mut(token_audit, 'LIMITS',
                                    {k: v for k, v in token_audit.LIMITS.items()
                                     if not k.startswith('HISTORY.md')})))

    # 37. **宿主文档的仓库备份不同步**（改了宿主没同步 → 推送出去的是旧版）。
    #     实景（2026-09-21 查疏漏）：`AGENTS.md` 备份落后 10 行（用户 2026-09-20 定的三条规矩
    #     只在宿主里），而此前**没有任何守卫**会发现。
    #     ⚠ 必须**改磁盘**（守卫读的是磁盘，改内存会假通过）→ 用 try/finally 保证还原，
    #       否则中途崩掉就把仓库里的备份写坏了。
    _host_note = os.path.join(os.path.expanduser('~'), '.dsh', 'docs', 'SHELL-NOTES.md')
    if os.path.exists(_host_note):          # 换机器时宿主机没有这些文档 → 跳过该用例

        class HostSyncMut:
            def __enter__(self):
                self.p = os.path.join(ROOT, 'docs', 'HOST-DOCS', 'SHELL-NOTES.md')
                self.old = open(self.p, encoding='utf-8').read()
                try:
                    with open(self.p, 'w', encoding='utf-8', newline='\n') as fh:
                        fh.write(self.old + '\n（变异：比宿主多一行 → 备份已不同步）\n')
                except Exception:                             # noqa: BLE001
                    self.__exit__()

            def __exit__(self, *a):
                with open(self.p, 'w', encoding='utf-8', newline='\n') as fh:
                    fh.write(self.old)

        results.append(case('宿主文档备份不同步（推送旧版）', 'host_docs_synced', HostSyncMut))

    # 38. **工具从没被任何文档提到**（= 对使用者不存在）。
    #     实景（2026-09-21 横向扫描）：`octave_audit` / `probe_variety` / `block_eq` /
    #     `section_eq` / `pitfalls_archive` 五个工具能被调用、有实质案例，却不在任何文档里。
    #     ⚠ 注入方式 = 在 `scripts/` 下**真的新建一个**没被引用的脚本（守卫读磁盘，
    #       改内存会假通过）；`__exit__` 负责删掉。万一进程被杀留下残留：
    #       文件名带 `zz_` 前缀，一眼能认出来。
    class ToolUnlistedMut:
        def __enter__(self):
            self.p = os.path.join(os.path.dirname(os.path.abspath(st.__file__)),
                                  'zz_mutation_tool_probe.py')
            with open(self.p, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write('# 变异用：一个没被任何文档提到的工具\n')

        def __exit__(self, *a):
            if os.path.exists(self.p):
                os.remove(self.p)
    results.append(case('工具从没被文档提到（等于不存在）', 'docs_paths', ToolUnlistedMut))

    # 39. **路由表的体量数字漂了**（它是选"读哪份、多贵"的依据，漂了就误导）。
    #     实景（2026-09-21）：11 行里 7 行偏差 >30%，最大 `CHEATSHEET ≈0.7k` 实际 3.3k。
    #     ⚠ 改的是**宿主 SKILL.md 磁盘文件**（守卫读磁盘），`__exit__` 负责还原。
    _skill = os.path.join(os.path.expanduser('~'), '.dsh', 'skills', 'bgm-studio', 'SKILL.md')
    if os.path.exists(_skill):

        class RouteSizeMut:
            def __enter__(self):
                self.p = _skill
                self.old = open(self.p, encoding='utf-8').read()
                with open(self.p, 'w', encoding='utf-8', newline='\n') as fh:
                    fh.write(self.old.replace('`CHEATSHEET.md`', '`CHEATSHEET.md`')
                             .replace('| ≈3.3k |', '| ≈0.7k |', 1))

            def __exit__(self, *a):
                with open(self.p, 'w', encoding='utf-8', newline='\n') as fh:
                    fh.write(self.old)

        results.append(case('路由表体量数字漂了（选读哪份的依据失真）',
                            'skill_routes_resolve', RouteSizeMut))

    # 40. **尾音回绕失效**（退化成直接裁剪）：每循环一次丢掉一截尾音，而**不会报错**
    #     —— 实测 A 段循环点之后 2.5 秒内的尾音只比正片低 0.3dB。
    # 41. **小节边界口径被改坏**（拿"实测时长 ÷ 小节数"反推会把尾音算进去）→ 每次循环错位。
    import loop_export as _le
    results.append(case('尾音回绕失效（退化成直接裁剪）', 'loop_export_contracts',
                        lambda: Mut(_le, 'crop_wrap', lambda x, i0, i1, n: x[i0:i1].copy())))
    results.append(case('小节边界拿实测时长反推（把尾音算进去）', 'loop_export_contracts',
                        lambda: Mut(_le, 'bar_seconds', lambda s: 223.376 / 72.0)))
    # 45. **角色判据阈值被改坏**（谁都成 base / 谁都成 accent → 绑定清单失去区分度）。
    #     实景：第一版用"input 跨度"判，结果四轨全被判成 layered。
    results.append(case('角色阈值不自洽（base/accent 判据失效）', 'loop_export_contracts',
                        lambda: Mut(_le, 'ACCENT_RATIO', 2.0)))

    # 42. **单段 `--start` 被忽略**（永远从 0 开始）→ 你以为在问第 55 秒，实际问的是开头，
    #     而且**输出里看不出**（只会看到段区间是 0~dur）。实景：2026-09-21 修的真 bug。
    import ask_audio_critic as _ac2
    results.append(case('单段 --start 被忽略（问错地方却看不出来）', 'audio_critic_contracts',
                        lambda: Mut(_ac2, 'single_bounds',
                                    lambda total, start, dur, max_sec=None:
                                        [(0.0, min(float(dur), _ac2.MAX_SEC))])))

    # 43. **CLI 参数声明了却没被读取**（静默失效）。实景：`ask_audio_critic --start` ·
    #     `layer_exp --only` —— 同一天抓到两个。
    #     ⚠ 注入方式 = 在 `scripts/` 下**真的新建**一个带死参数的脚本（守卫读磁盘，改内存会假通过）；
    #       `__exit__` 负责删掉，名字带 `zz_` 前缀便于识别残留。
    class DeadArgMut:
        def __enter__(self):
            self.p = os.path.join(os.path.dirname(os.path.abspath(st.__file__)),
                                  'zz_dead_arg_probe.py')
            with open(self.p, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write("import argparse\n"
                         "p = argparse.ArgumentParser()\n"
                         "p.add_argument('--never-read')\n"
                         "a = p.parse_args()\n"
                         "print('ok')\n")

        def __exit__(self, *a):
            if os.path.exists(self.p):
                os.remove(self.p)
    results.append(case('CLI 参数声明了却没读（静默失效）', 'dead_cli_args', DeadArgMut))

    # 44. **notes.md 的「速度」行与 song.json 不一致**（交付文档写错，照它复现会得到另一个速度）。
    #     实景：`20_piano_rain/notes.md` 写 69 BPM / 250.4 s，而实际是 78 BPM / 223.4 s。
    #     ⚠ 改**磁盘**（守卫读磁盘）→ `__exit__` 还原。
    _nt = os.path.join(ROOT, 'songs', '20_piano_rain', 'notes.md')
    if os.path.exists(_nt):

        class NotesSpeedMut:
            def __enter__(self):
                self.p = _nt
                self.old = open(self.p, encoding='utf-8').read()
                # ⚠ **不许硬编码旧速度**：原来写死 `'**78 BPM**'`，而 `20_piano_rain`
                #   2026-09-22 重生成成 **94 BPM** 后那个串根本不存在 → `replace` 什么都没改
                #   → 用例报"漏了"，看着像防线坏了，其实是**注入没打进去**
                #   （本文件第 608-613 行警告过的同一类"假通过"）。改成从夹具现读。
                import re as _re
                m = _re.search(r'^\|\s*速度\s*\|(.+)$', self.old, _re.M)
                assert m, '夹具 notes.md 里没有 `| 速度 |` 行，注入无从下手'
                b = _re.search(r'(\d+(?:\.\d+)?)\s*BPM', m.group(1))
                assert b, '`| 速度 |` 行里没有 BPM：%r' % m.group(1)[:60]
                line_new = m.group(0).replace(
                    b.group(0), '%g BPM' % (float(b.group(1)) + 12), 1)
                new = self.old.replace(m.group(0), line_new, 1)
                assert new != self.old, '注入没生效（文本一字未变）'
                with open(self.p, 'w', encoding='utf-8', newline='\n') as fh:
                    fh.write(new)

            def __exit__(self, *a):
                with open(self.p, 'w', encoding='utf-8', newline='\n') as fh:
                    fh.write(self.old)

        results.append(case('notes 速度与 song.json 不一致（交付文档写错）',
                            'notes_speed_matches', NotesSpeedMut))

    # 36. song.json 又被写成"一个数字一行"
    results.append(case('song.json 被写胖', 'song_json_canonical',
                        lambda: Mut(json_io, 'dumps',
                                    lambda o, indent=1: json.dumps(
                                        o, ensure_ascii=False, indent=1) + '\n')))

    # 37. 入口脚本丢了编码兜底（静态契约；真进程那条在检查内部自带反向对照）
    class NoGuard:
        def __enter__(self):
            self.old = st.HERE
            d = tempfile.mkdtemp(dir=TMP)
            open(os.path.join(d, 'unguarded.py'), 'w', encoding='utf-8').write(
                'import sys\n\ndef main():\n    return 0\n\n\n'
                'if __name__ == "__main__":\n    sys.exit(main())\n')
            st.HERE = d
        def __exit__(self, *a):
            st.HERE = self.old
    results.append(case('入口脚本没有编码兜底', 'console_encoding_safe', NoGuard))

    # 36. 残留和弦提示变成装饰
    results.append(case('残留和弦提示不响', 'unused_chords_warn',
                        lambda: Mut(st, '_unused_chord_report', lambda dirs: [])))

    # 37. 交付物缺失（render.json 声明了产物，文件却不在）
    def missing_outputs():
        d = temp_song_dir()
        json.dump({'composer': None, 'mid': 'gone.mid', 'out': 'gone_sf',
                   'ref': 'BGM16c'},
                  open(os.path.join(d, 'render.json'), 'w', encoding='utf-8'))
        # 必须有**一个**产物：否则会被判成"还没跑过 make_song"而跳过；
        # "跑过了、却缺声明的 MIDI"才是这条检查要抓的状态不一致。
        with open(os.path.join(d, 'gone_sf.ogg'), 'wb') as f:
            f.write(b'\x00' * 2048)
        return d
    results.append(case('声明的产物缺失', 'outputs_exist',
                        lambda: Mut(st, 'song_dirs', lambda **k: [missing_outputs()])))
    # 38. notes.md 缺失（接手的人无从了解）
    results.append(case('曲目缺 notes.md', 'notes_present',
                        lambda: Mut(st, 'song_dirs',
                                    lambda **k: [temp_song_dir()])))
    # 39. 新写了一条"只打印、从不 FAIL"的装饰性检查
    class NoAssert:
        def __enter__(self):
            self.old = st.__file__
            p = os.path.join(TMP, 'fake_selftest.py')
            open(p, 'w', encoding='utf-8', newline='\n').write(
                'def t_decorative():\n'
                '    """只打印，没有断言"""\n'
                '    print("看起来检查过了")\n')
            st.__file__ = p
        def __exit__(self, *a):
            st.__file__ = self.old
    results.append(case('装饰性检查（没有断言）', 'checks_have_assertions', NoAssert))
    # 39. render.json 的 mid 写成了数字（撞名 bug 的防线）
    def bad_render():
        d = temp_song_dir()
        json.dump({'composer': None, 'mid': 12345, 'out': 'x_sf', 'ref': 'BGM16c'},
                  open(os.path.join(d, 'render.json'), 'w', encoding='utf-8'))
        return d
    results.append(case('render.json 的 mid 是数字', 'render_json_schema',
                        lambda: Mut(st, 'song_dirs', lambda **k: [bad_render()])))

    # 40. 频域 DSP 改写后最容易漏的一处：高通忘了按 order 次乘（3 阶变 1 阶）
    def hp_ignores_order(x, sr, fc=38.0, order=3):
        nflt = render_midi._pad_len(len(x))
        H = 1.0 - render_midi._lp_response(sr, fc, nflt)
        return render_midi._freq_filter(x, H, nflt)
    results.append(case('高通忽略 order（3 阶→1 阶）', 'dsp_fft_equivalent',
                        lambda: Mut(render_midi, 'highpass_np', hp_ignores_order)))

    # 41. 频域滤波不做零填充 → 循环卷积把冲激响应尾巴绕回开头
    #     （短信号上就是能听出来的错；长信号上看不见 —— 所以检查必须两种长度都测）
    results.append(case('频域滤波不做零填充（绕回）', 'dsp_fft_equivalent',
                        lambda: Mut(render_midi, '_pad_len',
                                    lambda n, guard=65536: int(n))))

    # 42. 旋律强拍不落在和弦音上（听感"发飘"的客观成因；11 号曲 v1 就是这样）
    def bad_melody():
        def mut(x):
            x['sections'][0]['bars'] = 8
            x['sections'][0]['chords'] = ['D'] * 8
            x['melody']['m'] = [[b, 0, 1, 75] for b in range(8)]   # 75 不在 D 和弦里
        return temp_song_dir(mut)
    results.append(case('旋律强拍不合弦', 'melody_chord_fit',
                        lambda: Mut(st, 'song_dirs', lambda **k: [bad_melody()])))

    # 43. 强拍出现根音上方半音（♭9）：整体贴合度仍够 70%，但个别音最刺耳
    def b9_melody():
        def mut(x):
            x['sections'][0]['bars'] = 24
            x['sections'][0]['chords'] = ['D'] * 24
            good = [[2 * i, 0, 1, 62] for i in range(9)]      # 62=D4 ✓ 和弦音
            evil = [[2 * i + 1, 0, 1, 63] for i in range(3)]  # 63=D#4 = ♭9
            x['melody']['m'] = sorted(good + evil)
        return temp_song_dir(mut)
    results.append(case('强拍出现 ♭9 冲突', 'melody_chord_fit',
                        lambda: Mut(st, 'song_dirs', lambda **k: [b9_melody()])))

    # 44. 新律动 pump16 被退回普通八分（律动型就不再匹配参考曲）
    real_bass = song_engine.bass_part
    results.append(case('pump16 律动被改回八分', 'pump_groove',
                        lambda: Mut(song_engine, 'bass_part',
                                    lambda ch, nxt, i, pat, *a, **kw:
                                    real_bass(ch, nxt, i, {**pat, 'bass_style': 'eighth'}))))

    # 45. 副旋律乱配三度（不查和弦 → 不协和）
    results.append(case('副旋律配成半音三度', 'harmony_layer',
                        lambda: Mut(song_engine, 'harmony_below',
                                    lambda tones, m: m - 3)))

    # 46. 段落级混音自动化被丢掉（"起伏"就没了）
    real_wm = song_engine.write_midi

    def wm_no_auto(d, ev, path):
        d2 = json.loads(json.dumps(d))
        for sec in d2.get('sections', []):
            (sec.get('arr') or {}).pop('mix', None)
        return real_wm(d2, ev, path)
    results.append(case('段落 CC7 自动化被丢掉', 'section_mix_automation',
                        lambda: Mut(song_engine, 'write_midi', wm_no_auto)))

    # 47. kick 垫层与底鼓错位（听感变成"两个鼓在打架"）
    real_perc = song_engine.perc_part

    def perc_misaligned(style, level, i, nbars, layers=None, kick_vel=None,
                        *a, **kw):
        out = real_perc(style, level, i, nbars, layers, kick_vel)
        if layers and style == 'pump':
            out = [e for e in out if not (e[2] in (41, 43) and e[0] % 1.0 == 0.75)]
        return out
    results.append(case('kick 垫层与底鼓错位', 'perc_layers',
                        lambda: Mut(song_engine, 'perc_part', perc_misaligned)))

    # 48. air 垫层漏掉一半十六分格（5–10kHz 又回到"点+空"：实测占用率 100%→84%）
    def perc_air_gap(style, level, i, nbars, layers=None, kick_vel=None,
                     *a, **kw):
        out = real_perc(style, level, i, nbars, layers, kick_vel)
        if layers and style == 'pump':
            out = [e for e in out
                   if not (e[2] == 44 and int(round(e[0] * 4)) % 4 == 2)]
        return out
    results.append(case('air 垫层漏掉一半十六分格', 'perc_layers',
                        lambda: Mut(song_engine, 'perc_part', perc_air_gap)))

    # 49. 去尾失效（floor 抬到 0dB = 认为全是静音 → 原样返回）：
    #     末尾 15.7 秒的死气就会留在交付物里
    real_trim = render_midi.trim_tail
    results.append(case('渲染去尾失效', 'trim_tail',
                        lambda: Mut(render_midi, 'trim_tail',
                                    lambda x, sr, floor_db=-60.0, keep=1.0:
                                    real_trim(x, sr, 0.0, keep))))

    # 50. offbeat 垫层只落在每拍的 "a"（漏掉 "e"）→ 低频律动型又变成
    #     `◇◇·★◇◇·★`（例曲是 ◇★◇★◇★◇★，每拍两个反拍格都是强格）
    def perc_offbeat_half(style, level, i, nbars, layers=None, kick_vel=None,
                          *a, **kw):
        if layers and layers.get('kick_pos') == 'offbeat':
            layers = dict(layers, kick_pos='all')
        return real_perc(style, level, i, nbars, layers, kick_vel)
    results.append(case('offbeat 垫层漏掉 e 位', 'perc_layers',
                        lambda: Mut(song_engine, 'perc_part', perc_offbeat_half)))

    # 51. check_song 的 100% 严判据：把某个强拍音改成该小节和弦里没有的音。
    #     自检的 melody_chord_fit 是全库 ≥70% 松门（单个错音抓不到），所以这条严判据必须
    #     自己证明有效 —— 否则 check_song 会给"渲染前放行"一个假绿灯。
    def break_one_downbeat(dst):
        d = json_io.load(dst)
        sec = d['sections'][0]
        arr = d['melody'][sec['melody']]
        tones = [t % 12 for t in d['chords'][sec['chords'][0]][1]]
        # 根音要用和弦符号解析（和弦表的 [0] 是**低音**，转位时不是根音）
        _cname = sec['chords'][0].split('/')[0]
        _root = st.parse_chord(_cname)[0]
        root = (int(_root) % 12) if _root is not None else (d['chords'][sec['chords'][0]][0] % 12)
        hit = False
        for it in arr:
            if it[1] in (0.0, 2.0):          # 任一强拍即可（夹具旋律未必从 bar0 拍0 起）
                # 必须挑**真错音**：9 度(+2)/13 度(+9) 是和弦扩展音，严判据已放行它们，
                # 挑到扩展音这个用例就注入失效（实测踩过两次：77→76）
                it[3] = next(m for m in range(48, 84)
                             if m % 12 not in tones and (m - root) % 12 not in (2, 9))
                hit = True
                break
        assert hit, '夹具曲目里找不到强拍音 —— 这个用例没注入任何东西'
        json_io.save(dst, d)

    # 夹具动态挑：需要**原本强拍全在弦内音**的曲目，改掉一处才看得出严判据确实在起作用
    import selftest as _st
    _fx = _st.fixture_song(need='strict_clean')
    good = os.path.join(_fx, 'song.json') if _fx else ''
    if os.path.exists(good):
        results.append(case_check_song('强拍音改成弦外音（严判据）', good, break_one_downbeat))

    # 52. 技能卡路由表指向不存在的文档（静默失效：agent 读不到 → 只好整篇读 README → 变贵）
    import re as _re2
    _sk = os.path.join(os.path.expanduser('~'), '.dsh', 'skills', 'bgm-studio', 'SKILL.md')
    if os.path.exists(_sk):
        def _break_route(_p=None):
            t = open(_sk, encoding='utf-8').read()
            t2 = _re2.sub(r'`docs/SONG-FORMAT\.md`', '`docs/NOPE.md`', t, count=1)
            if t2 == t:                      # 兼容：表里写的是别的路径就整体换一个
                t2 = t.replace('.md`', '.md`', 1).replace('| 写/改', '| 写/改', 1)
            return t2
        class _RouteMut:
            def __enter__(self):
                self.old = open(_sk, encoding='utf-8').read()
                new = _re2.sub(r'(\| 写/改[^|]*\| )`([^`]+\.md)`', r'\1`docs/NOPE.md`',
                               self.old, count=1)
                open(_sk, 'w', encoding='utf-8', newline='\n').write(new)
                return new
            def __exit__(self, *a):
                open(_sk, 'w', encoding='utf-8', newline='\n').write(self.old)
        results.append(case('技能卡路由表指向缺失文档', 'skill_routes_resolve',
                            lambda: _RouteMut()))

    # 53. 文档指针腐烂（搬走正文/改名却没改指针）—— 例如把 PITFALLS-ARCHIVE 改名后
    #     README 与 PITFALLS 仍指着旧名。这属于"静默失效"：agent 按指针去读会读空。
    _arch = os.path.join(ROOT, 'PITFALLS-ARCHIVE.md')
    if os.path.exists(_arch):
        class _RenameArch:
            def __enter__(self):
                os.rename(_arch, _arch + '.tmp')
                return _arch
            def __exit__(self, *a):
                if os.path.exists(_arch + '.tmp'):
                    os.rename(_arch + '.tmp', _arch)
        results.append(case('文档指针腐烂（归档改名）', 'docs_paths', lambda: _RenameArch()))

    # 54. 文档分类错标（仓库文件被当宿主级 → 缺失时静默跳过，守卫等于不存在）
    import token_audit as _ta
    class _BadClass:
        def __enter__(self):
            self.old = _ta.HOST_LEVEL
            _ta.HOST_LEVEL = tuple(self.old) + ('README.md（工具链总索引）',)
            return _ta.HOST_LEVEL
        def __exit__(self, *a):
            _ta.HOST_LEVEL = self.old
    results.append(case('仓库文件被错标宿主级', 'docs_host_classification', lambda: _BadClass()))

    # 55. build_song 的时值推导被改坏（把"到下一个音的间距"改成固定 1 拍）
    #     → 生成的歌旋律时值全错；往返检查必须抓到"旋律不一致"
    import build_song as _bs
    def _break_dur(events, bars, default_dur=None):
        ev = _bs.melody_from_spec.__wrapped__(events, bars, default_dur) if hasattr(_bs.melody_from_spec, '__wrapped__') else None
        return ev
    _orig_mfs = _bs.melody_from_spec
    def _bad_mfs(events, bars, default_dur=None):
        out = _orig_mfs(events, bars, default_dur)
        for it in out:                      # 时值全改成 1 拍
            it[2] = 1.0
        return out
    results.append(case('build_song 时值推导坏掉', 'build_song_spec_roundtrip',
                        lambda: Mut(_bs, 'melody_from_spec', _bad_mfs)))

    # 56. melody_profile 退化成"只取调内率最高"（丢掉与和声分析对齐）
    #     注意：这条自检查的是**源码里有没有那步判据**，所以注入必须改到源码文本上
    #     （第一版替换 stats() = 空转，被"69/70 漏掉"如实报出来了）。
    _mp_path = os.path.join(ROOT, 'scripts', 'melody_profile.py')
    class _StripAlign:
        def __enter__(self):
            self.old = open(_mp_path, encoding='utf-8').read()
            anchor = 'if NAMES.index(tn) == ref_pc and pct >= 80.0:'
            assert anchor in self.old, '注入锚点不在源码里（改了实现就要同步改这条用例）'
            new_txt = self.old.replace(anchor, 'if False:      # injected', 1)
            assert new_txt != self.old, '注入没生效'
            open(_mp_path, 'w', encoding='utf-8', newline='\n').write(new_txt)
            return new_txt
        def __exit__(self, *a):
            open(_mp_path, 'w', encoding='utf-8', newline='\n').write(self.old)
    results.append(case('旋律主音建议丢掉和声对齐', 'melody_profile_tonic_hint',
                        lambda: _StripAlign()))

    # 57. 音域越界（某轨整体多移一个八度 → 次声波 / 超高）必须被抓。
    #     ⚠ **注入点在 2026-09-25 改了**：原来注入 `d['chords']` 的低音 −24，但坑 249 之后
    #     **引擎在 `build_events` 出口无条件夹取音域** → 故障被引擎修好，
    #     `track_ranges_musical`（读的是**引擎输出**）当然抓不到（实测"漏了"）。
    #     数据侧现在由 `t_transcribe_range_within_instrument` 守（它**直接读 song.json**），
    #     所以注入点改成 `notes_extra` 的音高 —— 与"谁读数据、就由谁守"对齐（坑 248/253）。
    import selftest as _st
    _f57 = None
    for _cand in _st.song_dirs():            # 夹具要**真有 notes_extra 音符**的曲目
        try:                                 # （`fixture_song()` 默认给生成曲，它 notes_extra 是空的）
            _j = json.load(open(os.path.join(_cand, 'song.json'), encoding='utf-8'))
        except Exception:                                          # noqa: BLE001
            continue
        if any((v.get('notes') if isinstance(v, dict) else v)
               for v in (_j.get('notes_extra') or {}).values()):
            _f57 = _cand
            break
    _s20 = os.path.join(_f57, 'song.json') if _f57 else ''
    if os.path.exists(_s20):
        class _LowerBass:
            def __enter__(self):
                self.old = open(_s20, encoding='utf-8').read()
                d = json.loads(self.old)
                ne = d.get('notes_extra') or {}
                tr = None
                for k, v in ne.items():
                    ns = v.get('notes') if isinstance(v, dict) else v
                    if ns:
                        tr = k
                        break
                if tr is None:
                    raise SkipCase('夹具没有 notes_extra 音符')
                arr = ne[tr]['notes'] if isinstance(ne[tr], dict) else ne[tr]
                for n in arr:
                    n[3] = max(0, int(n[3]) - 24)          # 整轨降两个八度
                json.dump(d, open(_s20, 'w', encoding='utf-8', newline='\n'),
                          ensure_ascii=False, indent=1)
                return d

            def __exit__(self, *a):
                open(_s20, 'w', encoding='utf-8', newline='\n').write(self.old)
        results.append(case('某轨整体降两个八度（次声波）',
                            'transcribe_range_within_instrument', lambda: _LowerBass()))

    # 58. spec 漂移（手工改 spec 的时值列）必须被抓 —— 复现命令会失效
    import selftest as _st
    _f58 = _st.fixture_song(need='with_spec')
    _sp20 = os.path.join(_f58, 'spec.json') if _f58 else ''
    if os.path.exists(_sp20):
        class _DriftSpec:
            def __enter__(self):
                self.old = open(_sp20, encoding='utf-8').read()
                d = json.loads(self.old)
                for sec in d['sections']:
                    mel = sec.get('melody')
                    if isinstance(mel, dict):
                        for _nm, arr in mel.items():
                            for e in arr:
                                if len(e) >= 4:
                                    e[2] = e[2] + 7
                        break
                json.dump(d, open(_sp20, 'w', encoding='utf-8', newline='\n'), ensure_ascii=False, indent=1)
                return d
            def __exit__(self, *a):
                open(_sp20, 'w', encoding='utf-8', newline='\n').write(self.old)
        results.append(case('spec 与 song.json 漂移', 'song_spec_sync',
                            lambda: _DriftSpec()))

    # 59. 新工具（bands_abs / probe_timbre / probe_peaks）的判据坏不坏得起来
    #     —— 它们的"绝对口径""占用率""低音根音"正是我踩过大坑的那三处，
    #     所以必须证明检查会报警（不报警 = 白加）。
    import bands_abs as ba
    import probe_timbre as pt
    import probe_peaks as pp

    results.append(case('绝对口径被抹平（所有频带同值）', 'bands_abs_absolute',
                        lambda: Mut(ba, 'band_power', lambda S, f, lo, hi: 1.0)))
    results.append(case('占用率口径失效（恒为 0）', 'probe_timbre_measures_air',
                        lambda: Mut(metrics, 'occupancy', lambda env, floor_db=20.0: 0.0)))
    results.append(case('低音根音读成固定值', 'probe_peaks_reads_root',
                        lambda: Mut(pp, 'pick_root', lambda peaks: 0)))

    # 60. 面板/CLI 共用的"逐轨事件出口"丢轨（面板会看不见东西，渲染却照样出声）
    import song_events as se
    _real_dump = se.dump

    def _drop_track(path, track=None):
        r = _real_dump(path, track)
        if r['tracks']:
            r['tracks'].pop(sorted(r['tracks'])[-1])
        return r
    results.append(case('逐轨事件出口丢一轨', 'song_events_dump',
                        lambda: Mut(se, 'dump', _drop_track)))

    # 61. 改名后没同步 render.json.mid（compose 写新文件、render 读旧文件 → 静默渲染旧 MIDI）
    import song_engine as _se
    _real_dirs = st.song_dirs

    def _renamed_dirs(**kw):
        import tempfile
        d = tempfile.mkdtemp(dir=ROOT)
        os.makedirs(os.path.join(d, 'songs', 'zz_renamed'), exist_ok=True)
        sd = os.path.join(d, 'songs', 'zz_renamed')
        json.dump({'name': 'zz_renamed', 'bpm': 120, 'chords': {'C': [36, [55, 60, 64]]},
                   'melody': {}, 'sections': [{'name': 'A', 'bars': 1, 'chords': ['C'],
                                               'melody': '', 'arr': {}}]},
                  open(os.path.join(sd, 'song.json'), 'w', encoding='utf-8'))
        json.dump({'composer': 'compose.py', 'mid': 'old_name.mid', 'out': 'x_sf'},
                  open(os.path.join(sd, 'render.json'), 'w', encoding='utf-8'))
        st.ROOT = d
        return [sd]

    class _FakeRoot:
        def __enter__(self):
            self.old_root, self.old_dirs = st.ROOT, st.song_dirs
            st.song_dirs = _renamed_dirs
        def __exit__(self, *a):
            import shutil
            shutil.rmtree(st.ROOT, ignore_errors=True)
            st.ROOT, st.song_dirs = self.old_root, self.old_dirs
    results.append(case('render.json.mid 与曲名脱钩', 'render_json_mid_matches_name',
                        lambda: _FakeRoot()))

    # 窗口外层级的支持度阈值关掉 → <60BPM 又变回"静默折半"
    results.append(case('窗口外层级不再上报（<60BPM 静默折半）',
                        'bpm_out_of_window_reported',
                        lambda: Mut(metrics, 'WINDOW_ALT_RATIO', 9.9)))

    # 强拍口径退回写死的 4/4（第 1、3 拍）→ 3/4 的弱拍经过音会被误判成错音
    results.append(case('强拍口径退回写死 4/4（3/4 误报）',
                        'meter_34_68',
                        lambda: Mut(song_engine, 'strong_beats',
                                    lambda meter: [0.0, 2.0])))

    # 华尔兹的和弦退回 4/4 反拍写法（0.5 / 1.5）→ pah-pah 不在第 2、3 拍上
    results.append(case('华尔兹和弦退回 4/4 反拍写法',
                        'waltz_groove',
                        lambda: Mut(song_engine, 'piano_part',
                                    lambda ch, i, B=4.0, *a, **kw: [(0.5, 0.28, m, 60)
                                                          for m in ch[1][:3]])))

    # 换气判据算错（把"缝隙"忽略、全曲当成一段）→ 判据自证必须报警
    results.append(case('换气判据算错（无视缝隙）',
                        'melody_breathing',
                        lambda: Mut(st, '_breath_runs',
                                    lambda iv, gap=0.5: [(min(s for s, _e in iv),
                                                          max(e for _s, e in iv))])))

    # 临时目录卫生：把总量预算压到 0 → 必须按"有工具在漏文件"断言失败
    # （实测那 7.5GB 的 selftest_* 就是这么漏出来的：TMP 是模块级创建、import 即生成；
    #   >24 小时的残留走**自愈**清理，所以这里测的是预算那条判据）
    results.append(case('临时目录总量预算被改坏（0MB=一有就报）',
                        'tmp_hygiene',
                        lambda: Mut(st, 'TMP_MAX_MB', 0.0)))

    # 面板音频缓存清理失效（变成空操作）→ "过期项必删"必须断言失败
    # （它以前从不清理：实测涨到 0.93GB / 12 个 job 目录）
    # ⚠ 必须拿**检查项用的同一个模块对象**（`st.load_studio_server()` 会缓存到 sys.modules）
    _srv = st.load_studio_server()
    results.append(case('面板缓存清理变成空操作',
                        'studio_cache_prune',
                        lambda: Mut(_srv, 'prune_tmp_audio', lambda **k: (0, 0.0))))

    # 换气阈值被抬到天上（等于"永远不需要换气"）→ 修复工具不会出手，检查必须抓到
    results.append(case('换气阈值被关掉（工具不再出手）',
                        'breath_fix_works',
                        lambda: Mut(breath, 'BREATH_SEC', 1e9)))

    # 跨曲雷同的阈值被放到 0 → 任何一点共享都算超标，检查必须按阈值断言失败
    results.append(case('跨曲雷同阈值被改坏',
                        'melody_distinct',
                        lambda: Mut(st, 'MELODY_SIM_MAX', -1.0)))

    # "语言重合"的孪生判据被放到 0 → 任何一对都算孪生，检查必须按上限断言失败
    # （这条防的是"改了 melody_gen 又把 10 首写成一套口音"，见 probe_melody_lang）
    import probe_melody_lang as _pl
    results.append(case('旋律语言孪生判据被改坏',
                        'melody_lang_diverse',
                        lambda: Mut(_pl, 'TWIN', 0.0)))
    results.append(case('语言孪生上限被放成负数（永远不许有孪生）',
                        'melody_lang_diverse',
                        lambda: Mut(st, 'MELODY_LANG_TWIN_MAX', -1)))

    # "生成旋律必须像画像"的承接度下限被抬到不可能达到 → 必须按阈值断言失败。
    # （真注入回归：把 melody_gen 出口的 `max(0.25, e[2])` 改回 `min(e[2], SPB-e[1])`
    #   复现坑 114 的裁剪 → 该检查实测 FAIL，落点 53%/时值 66%）
    results.append(case('旋律-画像承接度下限被改坏',
                        'melody_matches_profile',
                        lambda: Mut(st, 'MELODY_ACCEPT_MIN', 1.5)))

    # 模板库目录被清空 → "索引与磁盘一致"这条检查必须报空转，而不是静默通过
    results.append(case('模板库目录被清空（检查会空转）',
                        'midi_lib_index_sync',
                        lambda: Mut(st, 'MIDI_LIB_DIRS', ())))

    # 旋律形态守卫：① 真注入一首"连续 6 个同音 + 密度 1.0"的病态曲目 → 必须被抓
    # ② 阈值被放到天上（守卫变瞎）
    import probe_melody_health as _mh
    _sick = dict(name='注入的病态曲', notes=10, dens=1.0, same=60.0, maxrun=6, chop=0.0,
                 grids=3, onbeat=90.0, fit=100.0, bpm=100.0, gen=None, bars=10)
    results.append(case('注入"连续 6 个同音 + 密度 1.0"的曲目',
                        'melody_health',
                        lambda: Mut(_mh, 'collect', lambda *a, **k: [dict(_sick)])))
    results.append(case('旋律形态阈值被改坏（上限 0）',
                        'melody_health',
                        lambda: Mut(_mh, 'MAX_RUN', 0)))
    # ③ **压平**（2026-09-20 补）：批量改音高把一段旋律写成同一个音高时，那些音散在各小节
    # → 串长只有 2~3，`maxrun` 抓不到，**同音率 100%** 才是它的真身。
    # 这条用例的现场就是本轮的 20_piano_rain（16 个音写死同一个 98，串长 2~3）。
    _flat = dict(name='注入的被压平曲子', notes=16, dens=2.0, same=100.0, maxrun=3, chop=0.0,
                 grids=4, onbeat=60.0, fit=100.0, bpm=69.0, gen=None, bars=8)
    results.append(case('注入"同音率 100% 但串长 3"的压平曲目（串长判据抓不到）',
                        'melody_health',
                        lambda: Mut(_mh, 'collect', lambda *a, **k: [dict(_flat)])))
    results.append(case('同音率阈值被改坏（上限 100）',
                        'melody_health',
                        lambda: Mut(_mh, 'MAX_SAME', 100.0)))
    # ① sub 层音高下限被拆掉（= 旧行为：`bass − 12` 无条件）→ 贝斯掉进次声波，必须被抓
    results.append(case('贝斯 sub 层掉进次声波（SUB_FLOOR 归零）',
                        'bass_register',
                        lambda: Mut(_se, 'SUB_FLOOR', 0)))
    # ② 落点分散门被压到 0 → 每段都会"破门"，必须被抓（证明判据真的量得到落点集中）
    results.append(case('旋律落点分散门归零',
                        'melody_onset_spread',
                        lambda: Mut(st, 'ONSET_TVD_MAX', 0.0)))
    # ④ `ARR_KEYS` 白名单与实现脱节（引擎写了白名单外的键）→ 必须被抓
    results.append(case('编配白名单漏掉引擎写的键',
                        'arr_role_variety',
                        lambda: Mut(_se, 'ARR_KEYS',
                                    tuple(k for k in _se.ARR_KEYS if k != 'perc_in'))))
    # ⑤ `section_probe` 的段落地图被换回"一份硬编码地图" → 必须被抓
    # （2026-09-20 加：实测它原来就是硬编码的，对任何别的曲子只打印"不适用"）
    import section_probe as _sp
    results.append(case('section_probe 段落地图退回硬编码',
                        'probe_guards',
                        lambda: Mut(_sp, 'build_map',
                                    lambda song_json=None, bar=None: (
                                        [('Intro', 0, 4), ('A', 4, 12)], 10060.0 / 40))))
    # ③ 引子渐入被绕过（`perc_part` 忽略 `inbars`）→ 前 2 小节又敲起来，必须被抓
    _perc_orig = _se.perc_part
    results.append(case('引子渐入被绕过（perc_in 失效）',
                        'intro_gradience',
                        lambda: Mut(_se, 'perc_part',
                                    lambda style, level, i, nbars, layers=None,
                                    kick_vel=None, B=4.0, inbars=0, *a, **kw:
                                    _perc_orig(style, level, i, nbars, layers,
                                               kick_vel, B, 0))))
    # **候选打分**（`--step-bias` 的落点）：① 公式被反向（级进越高反而分越高）
    # ② 偏好量级大到盖过去重（"与库里不像"才是主要目标，级进只是同分时的偏好）。
    # 打分已抽成 `melody_gen.cand_score`，所以能用 mutation 的"改内存"机制注入
    # —— 原先它写在 `main()` 里，只能靠 subprocess 端到端验，注入打不进去。
    import melody_gen as _mg
    # ⚠ lambda 必须跟着 `cand_score` 的**当前签名**走（`..., onset_dist=0.0, form_pen=0.0`）：
    #   2026-09-18 给打分加了 `form_pen`（形态罚）后，旧 lambda 只收 5 个位置参数，
    #   注入时直接 `TypeError` —— 变异用例**照样被判"抓到"**，但抓的是签名不匹配、
    #   不是"逻辑被反向"，等于**假通过**。这里补上后两个参数的默认值。
    results.append(case('候选打分被反向（级进越高分越高）', 'melody_step_bias',
                        lambda: Mut(_mg, 'cand_score',
                                    lambda a, b, c, d, e, f=0.0, g=0.0:
                                    a * 2.0 + b + c * 0.5 + e * d + f + g)))
    results.append(case('级进偏好量级过大（盖过去重）', 'melody_step_bias',
                        lambda: Mut(_mg, 'cand_score',
                                    lambda a, b, c, d, e, f=0.0, g=0.0:
                                    a * 2.0 + b + c * 0.5 - 100.0 * e * d + f + g)))
    # **形态罚**：⚠ **不能靠"把 `melody_gen.form_penalty` 归零"来测**（2026-09-18 实测报漏）——
    # 本检查量的是**磁盘上已生成的 `song.json`**，而归零只改**生成侧**的内存函数，
    # 已有曲目一个音都不变 → 必然通过，是**假通过**（与上面 `cand_score` 那次"签名不匹配也算
    # 抓到"同一类毛病）。改成打**检查自己的门**：`t_melody_form_rules` 里的"门本身要有护栏"
    # 断言必须失败。
    results.append(case('形态的门被改坏（空档门抬到 99）', 'melody_form_rules',
                        lambda: Mut(st, 'FORM_MAX_GAP_MED', 99.0)))
    results.append(case('末落点门被归零（判据变瞎）', 'melody_form_rules',
                        lambda: Mut(st, 'FORM_MIN_LAST8', 0.0)))

    # **还原链的三个新工具**（2026-09-18）：**没有配 mutation 用例**，原因是技术性的 ——
    # 这三条 selftest（`transcribe_to_song_contracts` / `analyze_structure_not_degenerate` /
    # `measure_velocity_not_constant`）都用 `subprocess` 起**子进程**跑 CLI（它们本来就是
    # 命令行工具），而本文件的 `Mut` 是**改当前进程的内存** → 子进程看不到 → 注入必然"漏了"。
    # 实测：加进去后 140/142，两条都报"检查通过（没抓到注入的故障）"。
    # 要真配上，得先把工具重构成"可 import 调用 + CLI 只是薄壳"——那是独立的一步。
    # ⚠ **宁缺不假配**：本文件已经栽过两次假通过（`cand_score` 的 lambda 签名不匹配被
    #   当成"抓到"、`form_penalty` 归零改的是生成侧而检查量的是磁盘）。
    # **句末收束门本身**：2026-09-15 从 0.55 降到 **0.25**（旧门会把 **33% 的真实模板**
    # 判成不合格 —— 用同一口径复算 218 首 `refs/midi2` 的实测结果；用户口径"现代音乐也符合"）。
    # 把门改到 0（守卫变瞎）必须被抓到：该检查里"注入旧形态必须破门"的自证会失败。
    results.append(case('旋律句末收束门被改坏（下限 0）', 'melody_motif_rules',
                        lambda: Mut(st, 'MOTIF_MIN_CADENCE', 0.0)))
    # "小步打转"（|iv|≤1 占 50%）—— 用户嘴里"d d d d ddd"的真身
    _stag = dict(name='注入的小步打转曲', notes=20, dens=2.0, same=30.0, maxrun=3, chop=0.0,
                 grids=6, onbeat=50.0, fit=100.0, bpm=100.0, gen=None, bars=10,
                 small=50.0, uniq=5, span=4)
    results.append(case('注入"小步打转"(|iv|≤1 占 50%)的曲目',
                        'melody_health',
                        lambda: Mut(_mh, 'collect', lambda *a, **k: [dict(_stag)])))

    # **"嘴替"（`selfcheck.py`，2026-09-21）**：它报的是"离群量 + 方向"，两个要害都能打：
    #   ① 百分位被算平/算反 → 清单变"人人都不离群"（用户于是以为没问题，**比没有更坏**）
    #   ② COLS / HEARD 与探针脱节 → 静默少报一维、或那一项永远没有听感映射
    # ⚠ 注入的是**模块内存**，所以 `t_selfcheck_outliers` 必须 import 调用；
    #   若它哪天改成 subprocess 跑 CLI，这三条会集体"漏"——那时不要改判据，改回调用方式。
    import selfcheck as _sc
    results.append(case('离群百分位被拍平（人人都在中位）', 'selfcheck_outliers',
                        lambda: Mut(_sc, '_pct_rank', lambda xs, v: 50.0)))
    results.append(case('离群清单维度与探针对不上（COLS 少一维）', 'selfcheck_outliers',
                        lambda: Mut(_sc, 'COLS', _sc.COLS[:-1])))
    results.append(case('听感映射被改名（那项永远报不出听感）', 'selfcheck_outliers',
                        lambda: Mut(_sc, 'HEARD',
                                    {k: v for k, v in _sc.HEARD.items() if k != '小步率'})))

    # **Bass 音色**（2026-09-21 用户定案："以后要用 bass 时就这样来"）：把"允许的低音乐器"
    # 集合改成只含 0（钢琴）→ 那些用 GM 32 的曲目必须被判违规 —— 证明这条检查真的在量音色。
    # ⚠ **不能**靠"改某首 song.json 的 programs"来注入：本检查读的是**磁盘**，
    #   改内存只会假通过（`form_penalty` 那次踩过，见下方注释）。
    results.append(case('Bass 允许音色被改小（检查变瞎）', 'bass_timbre_is_low',
                        lambda: Mut(st, 'BASS_LOW_PROGS', frozenset({0}))))

    # **音频大模型的契约**（2026-09-21）：① 单段上限被抬大 → 整曲会被静默截断（模型只听到前 30 秒）
    # ② 判据口径被改回 `'没问题' in answer` → **报了问题的段被判成"没问题"**（我踩过的坑）
    # ⚠ 用例①能打进去的前提是 `segment_bounds` 的默认参数写成 `None` 而不是 `=MAX_SEC`
    #   （默认参数定义时绑定，改 MAX_SEC 对调用方无效 → 会变成假通过）。
    import ask_audio_critic as _ac
    results.append(case('音频大模型单段上限被抬大（静默截断）', 'audio_critic_contracts',
                        lambda: Mut(_ac, 'MAX_SEC', 600.0)))
    results.append(case('"报了问题"的判据被改回字符串包含', 'audio_critic_contracts',
                        lambda: Mut(_ac, 'verdict',
                                    lambda a: '没问题' if '没问题' in (a or '') else '其它')))
    # ③ **时间口径**（2026-09-21 校正）：文档原假设是"模型报段内相对秒、要加回段起点"，
    #    实测**推翻**（40 段里 96/96 条落在整曲域）。若哪天退回旧假设 → 整曲秒会被**再加一次**
    #    段起点（70 秒 → 125.8 秒），清单上的位置全错，而工具不会报错 —— 必须抓。
    results.append(case('时间口径退回"段内相对秒"旧假设（位置全错）',
                        'audio_critic_contracts',
                        lambda: Mut(_ac, 'to_abs',
                                    lambda v, start, dur, tol=0.6: (start + v, 'rel'))))
    # ④ **多数表决的键丢掉时间维** → 不同时刻的同类指控被并成一条，
    #    "只留稳定复现的线索"就退化成了"只留稳定的类别"（位置信息全丢）。
    results.append(case('多数表决的键丢掉时间维（不同时刻并成一条）',
                        'audio_critic_contracts',
                        lambda: Mut(_ac, '_claim_key', lambda cl, band: (cl['cat'], None))))

    # 主题模板包（用户口径：一次生成依据"很多同主题模板"，来源只许 refs/midi2 或权威网络数据）
    import theme_pack as _tp
    # 57. **聚合节奏型退回"取第一名成员"**（2026-09-22 修的真 bug）：
    #     `aggregate_refs` 原来对 `rhythm_low/high` 写 `profs[0]` → 5 个主题字符级完全相同
    #     （它们的 `members[0]` 都是 `bgm01c`），而候选池里有 44 种不同取值。
    results.append(case('聚合节奏型退回"取第一名成员"', 'theme_pack_agg_pattern',
                        lambda: Mut(_tp, '_agg_pattern',
                                    lambda pats: (pats[0] if pats else ''))))

    # 58. **节奏型不随拍号**（写死 4 拍）：3/4 拍的曲子会被按 4 拍切小节（本库 2 首圆舞曲）。
    import metrics as _mtr
    _orig_rhythm = _mtr.rhythm

    def _fixed4(m, sr, bpm, loud_bars=16, level=4, beats_per_bar=4):
        return _orig_rhythm(m, sr, bpm, loud_bars, level, 4)   # 永远按 4 拍

    results.append(case('节奏型不随拍号（写死 4 拍）', 'metrics_meter_aware',
                        lambda: Mut(_mtr, 'rhythm', _fixed4)))

    # 59. **成绩单的拍号改回"从 `_song_ctx` 取"**（2026-09-22 真崩过的原形）：
    #     `_song_ctx()` 返回的第二项是 `(programs, mix, arr)` 三元组，不是 song.json 字典，
    #     `.get('meter')` 直接 `AttributeError: 'tuple' object has no attribute 'get'`
    #     → `make_song.py 20_piano_rain` 退出码 1，而当时 selftest 144/144 全绿。
    import scorecard as _sc

    def _buggy_meter(path):
        _cfg, data = _sc._song_ctx(path)
        return (data or {}).get('meter') or (4, 4)      # 原 bug 的形态

    results.append(case('成绩单拍号改回从 _song_ctx 取（原崩法）',
                        'scorecard_meter_source',
                        lambda: Mut(_sc, '_meter_of', _buggy_meter)))
    # ① 取值点写了却不生效：永远 4/4 ⇒ 3/4 的圆舞曲又回到错位网格 —— 必须抓
    results.append(case('成绩单拍号写死 4/4（丢拍号）', 'scorecard_meter_source',
                        lambda: Mut(_sc, '_meter_of', lambda path: (4, 4))))
    # ② 兜底被拆（无 meter 的老歌直接给 None）⇒ `metrics.rhythm` 会拿到非法拍号
    results.append(case('拍号兜底被拆（无 meter 给 None）', 'scorecard_meter_source',
                        lambda: Mut(_sc, '_meter_of', lambda path: None)))
    # ③ `_song_ctx` 的形状契约失效（变成字典）⇒ 以后又有人拿它当 song.json 用
    results.append(case('_song_ctx 被改成返回字典（形状失守）',
                        'scorecard_meter_source',
                        lambda: Mut(_sc, '_song_ctx',
                                    lambda path: (None, {'meter': [4, 4]}))))
    # ④ **整条命令跑不完**：只测函数不测命令，就是这个 bug 逃过 144 项自检的原因
    results.append(case('成绩单跑不完（main 抛异常）', 'scorecard_main_runs',
                        lambda: Mut(_sc, '_meter_of', _buggy_meter)))

    # ① 白名单被放宽成"随便什么站点都算权威" → 来源校验必须失效被抓
    results.append(case('主题包来源白名单被改坏（人人都是权威）',
                        'theme_pack_valid',
                        lambda: Mut(_tp, 'AUTHORITATIVE_HOSTS', ('example.com',))))
    # ② 模板库索引读不到（模板不在库里）→ "模板必须来自 refs/midi2"必须断言失败
    results.append(case('模板库索引被清空（模板不在库里）',
                        'theme_pack_valid',
                        lambda: Mut(_tp, 'lib_index', lambda *a, **k: [])))
    # ③ 主题表被清空 → 包里的主题认不出来，必须被抓
    results.append(case('主题表被清空（包里的主题认不出）',
                        'theme_pack_valid',
                        lambda: Mut(_tp, 'THEMES', {})))
    # ④ 主题包路径被指向不存在 → "声明了主题却找不到包"必须被抓
    #   （路径要在 ROOT 下：放 TMP 会跨盘 relpath 抛 ValueError，那样算"抓到"是假阳性）
    results.append(case('主题包路径被改坏（声明了却找不到）',
                        'theme_basis_whitelist',
                        lambda: Mut(_tp, 'pack_path',
                                    lambda theme, root=None: os.path.join(
                                        ROOT, 'refs', 'themes', 'nope_%s.json' % theme))))
    # ⑤ 歌曲里的主题名认不出来（改过 THEMES 表 / 手写主题名）→ 逐首核对必须断言失败
    results.append(case('歌曲声明的主题不在主题表里',
                        'theme_basis_whitelist',
                        lambda: Mut(_tp, 'THEMES', {})))

    # ⑥ 主题包的**混音目标**被改成不存在的画像 → 守卫必须抓
    #    （不然生成时 render.json 会指向空画像；用临时改写包文件的方式注入真实数据故障）
    class _BadMixTarget:
        def __enter__(self):
            self.p = os.path.join(ROOT, 'refs', 'themes', 'daily.json')
            self.txt = open(self.p, encoding='utf-8').read()
            j = json.loads(self.txt)
            j['mix_target'] = {'ref': 'no_such_portrait', 'score': 0.1}
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                json.dump(j, f, ensure_ascii=False, indent=1)

        def __exit__(self, *a):
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                f.write(self.txt)

    results.append(case('主题包混音目标指向不存在的画像',
                        'theme_pack_valid', _BadMixTarget))

    # ⑦ 主题包把**基础声部 bass** 按"角色缺失"关掉（低音区明明有内容）→ 守卫必须抓
    #    （实测教训：钢琴曲没有独立贝斯轨，照"缺失即关"处理 → 成品 40–80Hz 掉到 −33dB）
    class _KillBass:
        def __enter__(self):
            self.p = os.path.join(ROOT, 'refs', 'themes', 'classic.json')
            self.txt = open(self.p, encoding='utf-8').read()
            j = json.loads(self.txt)
            j['arrangement']['arr_off'] = sorted(set(
                (j['arrangement'].get('arr_off') or []) + ['bass']))
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                json.dump(j, f, ensure_ascii=False, indent=1)

        def __exit__(self, *a):
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                f.write(self.txt)

    results.append(case('主题包把基础声部 bass 关掉（低音区有内容）',
                        'theme_pack_valid', _KillBass))

    # 旋律结构层（动机/期待/终止式）：
    # ① 大跳阈值抬到 99 → 没有任何音程算大跳 → 期待规则无从检验（样本量为 0 必须被拦）
    # ② 大跳阈值压到 0 → 每个音程都算大跳 → 反向率掉到 ~50%（随机方向），必须断言失败
    import melody_gen as _mg
    results.append(case('旋律：大跳阈值抬到 99（期待规则空转）',
                        'melody_motif_rules',
                        lambda: Mut(_mg, 'LEAP_IV', 99)))
    results.append(case('旋律：大跳阈值压到 0（人人都是大跳）',
                        'melody_motif_rules',
                        lambda: Mut(_mg, 'LEAP_IV', 0)))

    # 旋律**形态层**（铺满小节 / 不许每小节复刻）—— 三条注入，各自对应一条新判据：
    # ③ 关掉**变体层**（`VARIANTS_ON=False` → 每小节复刻同一 figure）→
    #    `rhythm_repeat` 必须回升到上限之上（否则"呆板"这条判据是装饰性的）
    # ④ 关掉**"落点铺满小节"的整套机制**（覆盖下限 + 打分里的末落点偏好）→
    #    落点退回"只说前半句" → `last8`/`maxgap_med` 必须破门
    #    ⚠ 只把 `CELL_LAST_MIN` 设 0 **抓不到**：`_cell_fit` 里"末落点越靠后越好"
    #      那一档仍会把落点挑到小节末（实测漏了一次）—— 注入必须打在真机制上。
    # ⑤ 关掉**句内拱形**（权重 0）→ 高点位置随机 → `peak_pos` 必须破门
    class _NoFill:
        def __enter__(self):
            self.a, self.b = _mg.CELL_LAST_MIN, _mg.CELL_TAIL_W
            _mg.CELL_LAST_MIN, _mg.CELL_TAIL_W = 0, 0.0

        def __exit__(self, *a):
            _mg.CELL_LAST_MIN, _mg.CELL_TAIL_W = self.a, self.b
    results.append(case('旋律：关掉动机变体层（每小节复刻）',
                        'melody_motif_rules',
                        lambda: Mut(_mg, 'VARIANTS_ON', False)))
    results.append(case('旋律：关掉落点铺满机制（只说前半句）',
                        'melody_form_rules', _NoFill))
    results.append(case('旋律：关掉落点间隔上限（说一句停一下）',
                        'melody_form_rules',
                        lambda: Mut(_mg, 'CELL_GAP_MAX', 99)))
    results.append(case('旋律：关掉句内拱形（高点乱落）',
                        'melody_form_rules',
                        lambda: Mut(_mg, 'ARCH_W', 0.0)))
    # ⑥ 音域：把画像 range 两头收窄 8 个半音（旧版收窄 2/1 的放大版）→ 音域判据必须抓到
    results.append(case('旋律：音域收窄（用不足画像音域）',
                        'melody_form_rules',
                        lambda: Mut(_mg, 'SPAN_TRIM', 8)))

    # 和声收束（主题路径）与力度曲线（opt-in）：
    # ⑦ 关掉 `cadence_pair` → 段末回到"进行原样循环"，永远停在属和弦 → 收束判据必须抓到
    # ⑧ 关掉力度包络函数（恒等）→ 旋律力度掉回硬编码 2 档 → opt-in 判据必须抓到
    import new_song as _ns
    import song_engine as _se
    results.append(case('和声：关掉段末 V→I 收束',
                        'theme_cadence',
                        lambda: Mut(_ns, 'cadence_pair', lambda pack, progs: None)))
    results.append(case('力度：关掉乐句力度包络',
                        'melody_dyn_optin',
                        lambda: Mut(_se, 'mel_dyn_env', lambda *a: 1.0)))
    # ⑨ 把音区分工换回**非八度移调**（旧表）→ 伴奏整轨被移到和弦外 →
    #    和弦贴合 + 音区分离两条必须同时抓到（这是"旋律和伴奏配合不好"的根因）
    results.append(case('配合：音区分工换回非八度移调（伴奏跑调）',
                        'accompaniment_harmony',
                        lambda: Mut(_se, 'TR_SHIFT',
                                    {'Pad': -5, 'Hook': -5, 'Piano': 4,
                                     'Strings': -3, 'Arp': 3, 'Melody': 7})))
    # ⑩ 关掉"给旋律留空间"这一层（`patterns.space` 恒 False）→ 伴奏密度与"旋律起音处的
    #    伴奏音数"必须弹回去 → `melody_space` 的对照判据抓到
    results.append(case('留空间：关掉 patterns.space',
                        'melody_space',
                        lambda: Mut(_se, 'space_on', lambda pat: False)))
    # ⑪ 段落的编配层次换回"第几段"的机械轮换（`level = i % 3`）→ 与能量曲线的
    #    单调性/排序相关必须被抓（这一层是"副歌厚、主歌薄"的唯一依据）
    results.append(case('编配：换回机械轮换 level=i%3',
                        'theme_arrangement_dynamic',
                        lambda: Mut(_ns, 'arr_level', lambda eused, i, role=None: i % 3)))
    # ⑬ 主题音色：把"音色池 → 引擎轨"的映射表清空 → `prog_pool` 注入了却没接上线
    #    （正是本轮犯过的错：守卫写了却没加 `@check`、根本没进 `CHECKS`）→
    #    `theme_timbre_pool` 必须抓到 `theme_programs` 空转。
    #    ⚠ 第一版变异写成"清空 `NOT_PLUCK`"，**它触发不了** —— daily 的 `uku` 池是
    #      `[25, 111, 30]`，`pick=0` 取首位 25，111 在第 2 位，过滤掉不掉都不影响输出。
    #      变异用例必须选"改了一定会变形"的点，否则是假绿灯。
    results.append(case('音色：轨映射表清空（注入却没接线）',
                        'theme_timbre_pool',
                        lambda: Mut(_ns, 'POOL_TO_TRACK', ())))
    # ⑫ 让旋律**整个跟着低音走八度**（最极端的平行八度）→ 声部进行守卫必须抓到
    _real_be = _se.build_events

    def _be_octave(d):
        ev, n = _real_be(d)
        if ev.get('Bass') and ev.get('Melody'):
            ev['Melody'] = [(t, dd, m + 12, v) for (t, dd, m, v) in ev['Bass']]
        return ev, n
    results.append(case('声部进行：旋律跟着低音走八度',
                        'melody_voice_leading',
                        lambda: Mut(_se, 'build_events', _be_octave)))
    # ⑬ 把某个主题的**聚合混音画像**砍成单份（"多方参考"退化成"单一参考"）→ 守卫必须抓到
    class _SingleRef:
        def __enter__(self):
            self.p = os.path.join(ROOT, 'refs', 'mix_targets', 'cheerful_mix.json')
            self.txt = open(self.p, encoding='utf-8').read()
            j = json.loads(self.txt)
            j['members'] = j['members'][:1]
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                json.dump(j, f, ensure_ascii=False, indent=1)

        def __exit__(self, *a):
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                f.write(self.txt)
    results.append(case('混音：聚合目标被砍成单份参考',
                        'mix_target_aggregate', _SingleRef))
    # ⑭ 抹掉成员的来源（不可溯源）→ 守卫必须抓到
    class _NoSource:
        def __enter__(self):
            self.p = os.path.join(ROOT, 'refs', 'mix_targets', 'cheerful_mix.json')
            self.txt = open(self.p, encoding='utf-8').read()
            j = json.loads(self.txt)
            for m in j.get('members') or []:
                m.pop('source', None)
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                json.dump(j, f, ensure_ascii=False, indent=1)

        def __exit__(self, *a):
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                f.write(self.txt)
    results.append(case('混音：聚合成员抹掉来源（不可溯源）',
                        'mix_target_aggregate', _NoSource))
    # ⑭-b 把同一个成员**再塞一份**（同一份音频重复投票）→ 守卫的判据 ④ 必须抓到。
    #      现场：`cheerful_mix` 原来 6 份成员里 `BGM16c`/`BGM16c_v2`/`bgm16c_new`
    #      逐字段相同（都是 `BGM16c.ogg`）→ bpm 中位被拉到 150，成绩单长期报"速度不一致"。
    class _DupSource:
        """复制一份成员 → **同一份音频投两票**。

        ⚠ **必须让判据 ③（聚合值 = 成员中位数）仍然通过**，否则它会被 ③ 先抓走、
        ④ 永远得不到验证 —— 第一版就是这样：日志显示"抓到了"，但报的是
        "频段 160-315 的聚合值 ≠ 成员中位数"，证明不了 ④ 坏得起来（等于装饰性检查）。
        做法：复制成员后**按含重复成员的全体重算各频段中位数并写回** ——
        这正是"去重逻辑被摘掉"时 `aggregate_refs` 会写出来的东西，
        于是 ③ 通过、只有 ④ 能报。
        """
        def __enter__(self):
            import theme_pack as _tp
            self.p = os.path.join(ROOT, 'refs', 'mix_targets', 'cheerful_mix.json')
            self.txt = open(self.p, encoding='utf-8').read()
            j = json.loads(self.txt)
            mem = j.get('members') or []
            if mem:
                mem.append(dict(mem[0]))           # 同一份音频投两票
            bands = {}
            for k in (j.get('bands') or {}):
                vals = []
                for m in mem:
                    q = _tp.find_ref_file(str(m.get('ref')))
                    if q:
                        try:
                            vals.append(json.load(open(q, encoding='utf-8'))['bands'].get(k))
                        except Exception:              # noqa: BLE001
                            pass
                v = _tp._med(vals)
                if v is not None:
                    bands[k] = round(v, 2)
            j['bands'] = bands
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                json.dump(j, f, ensure_ascii=False, indent=1)

        def __exit__(self, *a):
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                f.write(self.txt)
    results.append(case('混音：同一份音频被重复计入（重复投票）',
                        'mix_target_aggregate', _DupSource))
    # ⑭-c **非主题画像**不得绕过"多方参考"门槛（2026-09-24 补的自检盲区）。
    #      现场：`quiet_piano_mix` 是 `aggregate(1 refs)`、`source_note` 却写着"**多份**
    #      真实录音画像的……中位数"（名不副实），而它是 `refs/mix_targets/` 下**不在
    #      `tp.THEMES` 里**的画像 → 旧检查只遍历主题表，**永远看不到它**。
    #      做法：把它标回 `aggregate: True`（成员仍只有 1 份）→ ⑤ 必须抓到。
    class _NonThemeAgg:
        def __enter__(self):
            self.p = os.path.join(ROOT, 'refs', 'mix_targets', 'quiet_piano_mix.json')
            self.txt = open(self.p, encoding='utf-8').read()
            j = json.loads(self.txt)
            j['aggregate'] = True
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                json.dump(j, f, ensure_ascii=False, indent=1)

        def __exit__(self, *a):
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                f.write(self.txt)
    results.append(case('混音：非主题画像标成聚合却只有 1 份成员',
                        'mix_target_aggregate', _NonThemeAgg))
    # ⑭-d **豁免必须有实质理由**（2026-09-24 新增的两处豁免分支：音域 / 落点 TVD）。
    #      画像重建后按旧画像生成的曲目会越界，允许"带理由"放行 —— 但**理由空白 = 没写 = 不放行**，
    #      否则一句空话就能绕过门（同 `t_melody_matches_profile` 的 `_exempt_dims` 口径）。
    #      ⚠ **2026-09-25 加夹具探测**：这两条原来硬编码 `02_wave_walk` / `14_pixel_quest`，
    #      而画像会重建、曲目会被改写 —— 某次之后该曲该维**本来就达标**，清空豁免也不会 FAIL，
    #      用例于是变成"漏了"（mutation 报红、而检查其实是好的 = **夹具漂移**）。
    #      现在先真跑一次探测：清空后不 FAIL 就**跳过**（同 `SkipCase` 口径：不算漏）。
    def _blank_exempt_case(label, path, key, check):
        if not os.path.exists(path):
            print('  %-5s %-34s → %s' % ('跳过', label, '夹具曲目不存在'))
            return True
        txt = open(path, encoding='utf-8').read()
        try:
            j = json.loads(txt)
            ex = (j.get('patterns') or {}).get('melody_exempt') or {}
            if key not in ex:
                print('  %-5s %-34s → %s' % ('跳过', label, '夹具没有该维豁免（已被改写）'))
                return True
            ex[key] = '   '
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(json.dumps(j, ensure_ascii=False, indent=1))
            caught, _why = run_check(check)
        finally:
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(txt)
        if not caught:
            print('  %-5s %-34s → %s' % ('跳过', label,
                                         '夹具已漂移：清空豁免后该维本来就不越界'))
            return True

        class _Blank:
            def __enter__(self):
                self.txt = open(path, encoding='utf-8').read()
                _j = json.loads(self.txt)
                _j['patterns']['melody_exempt'][key] = '   '
                with open(path, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(json.dumps(_j, ensure_ascii=False, indent=1))

            def __exit__(self, *a):
                with open(path, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(self.txt)

        return case(label, check, _Blank)

    results.append(_blank_exempt_case(
        '旋律：音域豁免的理由被清空（空话放行）',
        os.path.join(ROOT, 'songs', '02_wave_walk', 'song.json'),
        'span', 'melody_form_rules'))
    results.append(_blank_exempt_case(
        '旋律：落点豁免的理由被清空（空话放行）',
        os.path.join(ROOT, 'songs', '14_pixel_quest', 'song.json'),
        'onset_tvd', 'melody_onset_spread'))
    # ⑮ 段落旋律命名换回"每段一个新名字"（旧行为）→ 同名段落不再共用旋律 →
    #    `theme_melody_reuse` 的自证分支必须抓到（复用彻底消失）
    results.append(case('曲式：段落旋律换回"每段一支"',
                        'theme_melody_reuse',
                        lambda: Mut(_ns, 'role_melody_name',
                                    lambda name, i: 'm%d' % (i + 1))))
    # ⑮-b 拆掉某曲的**显式豁免**（`melody_reuse_exempt`）→ 它的 Intro/Intro2 就是
    #      "同角色两段、两支旋律" → 必须失败。证明豁免是**逐曲声明**的，而不是把判据关掉。
    # ⚠ 夹具**动态找**（不硬编码曲名：43_joy_to_sorrow 已被删，硬编码会让整轮崩掉）
    def _exempt_song():
        for _d in sorted(glob.glob(os.path.join(ROOT, 'songs', '*'))):
            _p = os.path.join(_d, 'song.json')
            if not os.path.exists(_p):
                continue
            try:
                if 'melody_reuse_exempt' in json.load(open(_p, encoding='utf-8')):
                    return _p
            except Exception:
                continue
        return None

    class _DropExempt:
        def __enter__(self):
            self.p = _exempt_song()
            if not self.p:
                raise SkipCase('没有带 melody_reuse_exempt 的曲目（唯一那首 43_joy_to_sorrow 已删）')
            self.txt = open(self.p, encoding='utf-8').read()
            j = json.loads(self.txt)
            j.pop('melody_reuse_exempt', None)
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                json.dump(j, f, ensure_ascii=False, indent=1)

        def __exit__(self, *a):
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                f.write(self.txt)
    results.append(case('曲式：拆掉旋律复用豁免',
                        'theme_melody_reuse', _DropExempt))
    # ⑮-c 豁免理由写成空白 → 视为没写（否则一句空话就能绕过判据）
    class _BlankExempt:
        def __enter__(self):
            self.p = _exempt_song()
            if not self.p:
                raise SkipCase('没有带 melody_reuse_exempt 的曲目')
            self.txt = open(self.p, encoding='utf-8').read()
            j = json.loads(self.txt)
            j['melody_reuse_exempt'] = '   '
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                json.dump(j, f, ensure_ascii=False, indent=1)

        def __exit__(self, *a):
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                f.write(self.txt)
    results.append(case('曲式：豁免理由写成空白',
                        'theme_melody_reuse', _BlankExempt))
    # ⑯ 段落编制换回"原样返回"（= 旧行为：能量曲线微调音量，段落间同一套乐器）→
    #    `arr_role_variety` 的端到端判据（段间 Jaccard）与自证分支必须抓到
    results.append(case('编配：段落编制不随角色变（旧行为）',
                        'arr_role_variety',
                        lambda: Mut(_se, 'arr_by_role',
                                    lambda base, roles, energy=None, tier=1, sparse=False:
                                    [dict(b or {}) for b in base])))
    # ⑱ 削薄（`sparse`）失效：原样返回 → `t_arr_role_variety` 的机制断言必须抓到
    results.append(case('编配：削薄失效（sparse 不关层）',
                        'arr_role_variety',
                        lambda: Mut(_se, 'arr_sparse', lambda arr: dict(arr or {}))))
    # ⑲ 削薄时**把 glock 也一起关**：段间亮色差异被抹平 —— 实测段间 Jaccard 从 0.67
    #    涨到 0.80 超过 0.78 的门（battle/cheerful/neon/retro 报红），必须被抓
    _sp_orig = _se.arr_sparse
    results.append(case('编配：削薄误关 glock（抹平段间差异）',
                        'arr_role_variety',
                        lambda: Mut(_se, 'arr_sparse',
                                    lambda arr: dict(_sp_orig(arr), glock=False))))
    # ⑳ 把伴奏音色换回"盖住旋律"的那个（dance 的 Hook 由电钢 4 换回钢弦吉他 25）→
    #    `t_track_balance` 必须抓到 —— 用户听感总结"欢快的音乐都有一个音轨和其它不平衡"，
    #    实测就是钢弦吉他（program 25）的拨弦泛音把旋律压住了。
    # ⚠ `Mut` 走 getattr/setattr，**只能换对象属性、改不了 dict 的键** ——
    #   所以这里深拷贝整张 `STYLES` 再替换模块属性（第一版写成
    #   `Mut(_se.STYLES['dance']['programs'], 'Hook', ...)`，当场 AttributeError）。
    class _BrightHook:
        """把**会被 `t_track_balance` 检查到**的那首曲目的 Hook 换成钢弦吉他（program 25）。

        ⚠ 这里原来改的是 `STYLES['dance']['programs']` —— 那是**生成时**的音色预设，
        而 `song.json` 里各轨的 `programs` 早在生成那一刻就固化了，改预设对"被检查的对象"
        毫无影响 → 这条用例长期抓不到东西（mutation 158 项里**唯一**一项"漏了"，
        `t_track_balance` 本身是好的）。现在直接改 `song.json`，用完还原。

        `t_track_balance` 只查 glob 排序后**前 2 首** `style ∈ (dance, daily)` 且有
        `theme` 的曲目（当前是 `01_morning_light` 与 `04_pulse_city`），所以这里按同一
        条件挑"第一首"来当夹具 —— 挑法变了它会自动跟着变。
        """
        def __enter__(self):
            self.p, self.txt = None, None
            for q in sorted(glob.glob(os.path.join(ROOT, 'songs', '*', 'song.json'))):
                try:
                    j0 = json.load(open(q, encoding='utf-8'))
                except Exception:                          # noqa: BLE001
                    continue
                if (j0.get('style') or '') in ('dance', 'daily') and j0.get('theme'):
                    self.p = q
                    break
            if not self.p:
                return
            self.txt = open(self.p, encoding='utf-8').read()
            j = json.loads(self.txt)
            j.setdefault('programs', {})['Hook'] = [25, 1]   # 钢弦吉他：拨弦泛音会盖住旋律
            for s in j.get('sections') or []:                # 还得真在这首曲子里响起来
                s.setdefault('arr', {})['uku'] = True
            with open(self.p, 'w', encoding='utf-8', newline='') as f:
                json.dump(j, f, ensure_ascii=False, indent=1)

        def __exit__(self, *a):
            if self.p and self.txt is not None:
                with open(self.p, 'w', encoding='utf-8', newline='') as f:
                    f.write(self.txt)

    results.append(case('平衡：伴奏音色被换亮（盖住旋律）',
                        'track_balance', _BrightHook))
    # ㉑ 把主奏换成"起音慢"的音色（dance 的 Melody 木琴 13 → 颤音琴 11，起音 42ms）→
    #    `t_lead_timbre_attack` 必须抓到 —— 用户听感"有一个乐器慢一点不太和谐"
    # ⚠ `import copy` 原本挂在上一条用例（track_balance）前面，只服务这一条 ——
    #   2026-09-21 改那条用例的注入方式时**误删了这个 import**，于是 mutation_check
    #   在这一行直接 NameError 崩掉（整套变异测试跑不完）。改用例时留意**相邻**的
    #   局部 import/变量属于谁。
    import copy as _copy2
    _ST2 = _copy2.deepcopy(_se.STYLES)
    _ST2['dance']['programs']['Melody'] = [11, 0]
    results.append(case('音色：主奏换成慢起音（听着慢半拍）',
                        'lead_timbre_attack',
                        lambda: Mut(_se, 'STYLES', _ST2)))
    # ㉒ 把"过渡/留白"的两个门一起抬死 → 每个段界都会被判硬切，
    #    `t_section_transition` 必须抓到（证明它真在量段界形态，而不是恒绿）
    results.append(case('段界：过渡/留白门被抬死',
                        'section_transition',
                        lambda: MutMany([(st, 'TRANSITION_FADE_MIN', 99.0),
                                         (st, 'TRANSITION_JUMP_MAX', -99.0)])))
    # ㉓ 段级密度被压平（`build_events` 忽略 `arr.density`）→ 带 density 的曲目会被
    #    `t_density_dynamic_range` 跳过，导致 `checked == 0` 断言失败 —— 必须被抓到。
    #    它守的是"BGM35 那种 66 倍起伏"（我们原来只有 1.5–2.8 倍）。
    _be3 = _se.build_events

    def _flat_density(d, *a, **kw):
        import copy as _c3
        d2 = _c3.deepcopy(d)
        for s in d2.get('sections') or []:
            (s.get('arr') or {}).pop('density', None)
        return _be3(d2, *a, **kw)
    results.append(case('段级密度：density 被忽略（压平）',
                        'density_dynamic_range',
                        lambda: Mut(_se, 'build_events', _flat_density)))
    # ⑰ 吉他换回"每小节同一个音型"（关掉相位轮换）→ 同和弦的小节逐音复读 →
    #    `guitar_variation` 必须抓到（用户听感"每首曲子的刚弦吉他都是这个节奏音调"）
    results.append(case('吉他：关掉音型轮换（逐小节复读）',
                        'guitar_variation',
                        lambda: Mut(_se, 'guitar_rot',
                                    lambda arp, sec_i=0, bar_i=0, vary=False:
                                    list(arp or [0]))))
    # ⑱ 吉他音型换回硬编码的单一值（跨主题没区别）→ 组合数判据必须抓到
    results.append(case('吉他：所有主题共用同一组音型',
                        'guitar_variation',
                        lambda: Mut(_ns, 'theme_guitar_arp',
                                    lambda pack: [0, 2, 3, 4, 3, 2, 4])))
    # ⑲ 和弦识别丢掉低音信息（转位失效）→ `midi_chords_detect` 的转位判据必须抓到。
    #    ⚠ 变异函数必须**捕获原始函数对象**（`lambda: _mc.match(...)` 会被后来的替换套娃，
    #       实测直接 RecursionError —— 那是"变异写错"而不是"检查抓到"）。
    import midi_chords as _mc
    _orig_match = _mc.match                  # 先抓住**原始**函数（闭包引用，不受替换影响）
    results.append(case('和弦：识别时丢掉低音（转位失效）',
                        'midi_chords_detect',
                        lambda: Mut(_mc, 'match',
                                    lambda pcs, bass_pc=None: _orig_match(pcs, None))))
    # ⑳ 和弦识别把"窗后的音"也借进来（相邻小节互相污染）→ 时间轴/合并判据必须抓到
    _old_slice = _mc._slice_notes

    def _slice_tail(model, t0, t1, track_idx=None, **kw):
        return _old_slice(model, t0, t1 * 2.0, track_idx, **kw)
    results.append(case('和弦：取样借用了下一小节的音',
                        'midi_chords_detect',
                        lambda: Mut(_mc, '_slice_notes', _slice_tail)))

    # ㉑ 导出的 MIDI 把 note-on 排在 note-off 之前（同一 tick 上同音高的接续音被音源吞掉）→
    #    `midi_export_noteoff_first` 必须抓到。变异只动**排序权重常量**（真实 bug 就是这两个
    #    值写反）：往返判据一条都抓不到它，它会一路混到渲染，表现为"整段逐次衰减到 −80dB"
    #    （见 PITFALLS 161）。
    import midi_file as _mfi
    results.append(case('导出把按键排在松键之前（吞接续音）',
                        'midi_export_noteoff_first',
                        lambda: Mut(_mfi, 'W_ON', 2)))

    # ㉒ / ㉔ 的公共夹具：把真 HERE 下**所有** .py 拷进临时目录，并把"面板接线"字样
    #    从除 `studio_guard.py` 以外的每个脚本里抹掉 —— 等价于"生成脚本没接面板"。
    #
    #    为什么不用"造几个空文件"的老写法（2026-09-19 实测被它坑过一次）：检查里的
    #    脚本清单**会增长** —— `panel_guard_wired` 原本只查 new_song / make_song /
    #    melody_gen，后来 `imitate_ref.py` 也接了守卫、清单跟着变长，而变异只造了 3 个
    #    文件 → 第 4 个不存在 → 以 `FileNotFoundError` 收场：**"抓到"了，但理由不是
    #    接线缺失**，等于这条用例在替另一件事报警。改成"拷全 + 抹字样"后就与清单长度无关。
    import contextlib

    @contextlib.contextmanager
    def _no_panel_wiring():
        import glob as _glob
        import shutil
        old, d = st.HERE, tempfile.mkdtemp(dir=TMP)
        for p in _glob.glob(os.path.join(old, '*.py')):
            nm = os.path.basename(p)
            if nm == 'studio_guard.py':          # 守卫模块本身留着（否则红的是"缺模块"）
                shutil.copy2(p, d)
                continue
            s = open(p, encoding='utf-8').read()
            keep = [ln for ln in s.splitlines()
                    if 'studio_guard' not in ln and 'ensure_panel' not in ln
                    and 'delegate_' not in ln]
            open(os.path.join(d, nm), 'w', encoding='utf-8').write('\n'.join(keep))
        st.HERE = d
        try:
            yield
        finally:
            st.HERE = old
    results.append(case('生成脚本绕开面板守卫', 'panel_guard_wired', _no_panel_wiring))

    # ㉔ 面板委托被拆掉（生成又回到"绕开面板"）→ `panel_is_only_entry` 必须抓到。
    #    夹具同上（拷全 + 抹掉 delegate_* 接线）。
    results.append(case('生成脚本绕开面板唯一入口', 'panel_is_only_entry', _no_panel_wiring))

    # 面板漏翻一条静态文案（把字典里的 `💾 保存` 抹掉）→ `i18n_ui_translated` 必须抓到。
    # 变异的是 i18n.js（不是 .py），所以夹具要连 `studio/web` 一起搬到临时目录，并按
    # i18n_check.py 的 `WEB = dirname(HERE)/studio/web` 布局摆成两层：
    #     <base>/scripts/*.py  +  <base>/studio/web/{index,ed}.html,i18n.js
    # 摆错层的症状是"抓到"但理由是"文件不存在"，等于这条用例在替另一件事报警
    # （`_no_panel_wiring` 的注释里记着同一类坑）。
    @contextlib.contextmanager
    def _i18n_missing_entry():
        import glob as _g
        import shutil as _sh
        old_here, old_root = st.HERE, st.ROOT
        base = tempfile.mkdtemp(dir=TMP)
        d = os.path.join(base, 'scripts')
        os.makedirs(d)
        for p in _g.glob(os.path.join(old_here, '*.py')):
            _sh.copy2(p, d)
        web = os.path.join(base, 'studio', 'web')
        os.makedirs(web)
        for nm in ('index.html', 'ed.html', 'i18n.js'):
            _sh.copy2(os.path.join(old_root, 'studio', 'web', nm), web)
        p = os.path.join(web, 'i18n.js')
        s = open(p, encoding='utf-8').read()
        victim = "'💾 保存': '💾 Save',"
        assert victim in s, '变异夹具失效：i18n.js 里找不到 %s' % victim
        open(p, 'w', encoding='utf-8', newline='\n').write(s.replace(victim, ''))
        st.HERE, st.ROOT = d, base
        try:
            yield
        finally:
            st.HERE, st.ROOT = old_here, old_root
    results.append(case('面板漏翻一条文案（切英文时仍是中文）',
                        'i18n_ui_translated', _i18n_missing_entry))

    # ㉓ 时值下限被关掉（`mb=_DF` → `mb=0`）→ `dur_floor_wired` 必须抓到。
    #    这条参数是"听感 = 杂乱 / 不流畅"那轮的产物（PITFALLS 206），最容易被
    #    "参数不该写死"退回默认关 —— 关掉后旋律轨时值中位会跌回 0.10~0.19s
    #    （认可版是 0.264~0.344s），而**所有音符级指标都看不出异常**。
    #    拆在临时目录的副本上（不动真文件）。
    if not os.path.isdir(TMP):
        os.makedirs(TMP, exist_ok=True)

    class _CopyWith:
        """把两个脚本拷进临时目录、按 pairs 改坏其中某个，再把 `st.HERE` 指过去"""
        def __init__(self, fname, pairs):
            self.fname, self.pairs = fname, pairs
        def __enter__(self):
            import shutil
            self.old = st.HERE
            d = tempfile.mkdtemp(dir=TMP)
            for nm in ('imitate_ref.py', 'merge_tracks.py'):
                shutil.copy2(os.path.join(self.old, nm), d)
            p = os.path.join(d, self.fname)
            s = open(p, encoding='utf-8').read()
            for a, b in self.pairs:
                assert a in s, '变异锚点没找到：%r' % a[:60]
                s = s.replace(a, b)
            open(p, 'w', encoding='utf-8', newline='\n').write(s)
            st.HERE = d
        def __exit__(self, *a):
            st.HERE = self.old

    results.append(case('时值下限被关（mb=_DF → 0）', 'dur_floor_wired',
                        lambda: _CopyWith('imitate_ref.py', [('mb=_DF', 'mb=0')])))

    # ㉔ 并轨步骤被摘掉 → 集成后又是 9 条轨（Synth Pad / Organ / Chromatic Percussion /
    #    Synth Lead 全留着，碎音 68~84%），而认可版只有 5 条轨 → 必须抓到
    results.append(case('并轨步骤被摘掉', 'dur_floor_wired',
                        lambda: _CopyWith('imitate_ref.py',
                                          [('merge_tracks.py', 'merge_DISABLED.py')])))

    # ㉔b 并轨目标从 Strings 改回 Piano（= 首版那个"一点都不像"的配置）→ 必须抓到。
    #     这条守的是**听感结论**：原曲主体（other 44%）是合成器/弦乐，并进 Piano
    #     等于用钢琴音色弹它 —— 音高全对、音色全错，而所有音符级指标都看不出来。
    # ㉔c 单来源层（Guitar / Strings）被改回共识阈值 → 整层被砍（原曲主体消失）→ 必须抓到
    # ㉔d help 字符串里的 `%%` 被改回裸 `%`（argparse 再做一次 %-format 会炸）→ 必须抓到。
    #     这条不是理论风险：2026-09-19 就因为这个让 `imitate_ref.py --help` 直接退出，
    #     而 traceback 里**看不到是哪个参数**。
    results.append(case('help 里的 %% 被改成裸 %', 'cli_help_renders',
                        lambda: _CopyWith('imitate_ref.py', [('44%%', '44%')])))
    results.append(case('单来源层改回共识阈值', 'single_source_layers_unfiltered',
                        lambda: _CopyWith('imitate_ref.py',
                                          [('a.thr_extra', 'a.merge_thr')])))
    results.append(case('并轨改回并进 Piano', 'dur_floor_wired',
                        lambda: _CopyWith('imitate_ref.py',
                                          [("default='Strings'", "default='Acoustic Piano'")])))

    # ㉕ `stale()` 退回"只看文件在不在" → "改了 MIDI 却渲染旧音频"（PITFALLS 207，
    #    三首一起跑 18 秒就"完成"了，用户听到的是上一版音频）→ 必须抓到。
    #    这里直接换掉**模块函数**（守卫里 `ir.needs_redo(...)` 调的正是它）。
    import imitate_ref as _ir
    results.append(case('stale 退回只看文件在不在', 'dur_floor_wired',
                        lambda: Mut(_ir, 'needs_redo', lambda *a, **k: False)))

    # ㉖ **"只响 0.几秒"判据坏掉** —— 用户 2026-09-22："让以后不出现这种情况，出现了也能
    #    很快检查到修好"。把钢琴的实测"掉 12dB 时间"改成"不掉"，判据对钢琴就永远不响，
    #    等于这道防线没了（`sustain_criteria` 的断言①必须抓到）。
    import harmony_check as _hcm
    results.append(case('"只响 0.几秒"判据坏掉（钢琴当持续型）', 'sustain_criteria',
                        lambda: Mut(_hcm, 'SUSTAIN_DB12',
                                    {**_hcm.SUSTAIN_DB12, 0: float('inf')})))
    # ㉖b **未实测音色又被当成"判得了"**（族兜底值复活）→ 管乐 73 会被误报成"只响 0.几秒"。
    #     这正是本轮第一版的错法（全库从 2 首误报成 8 首）；断言③专钉这个错法。
    _hcmdb = _hcm.db12
    results.append(case('未实测音色又用族估值（管乐误报）', 'sustain_criteria',
                        lambda: Mut(_hcm, 'db12',
                                    lambda prog, _o=_hcmdb: 0.30 if prog == 73 else _o(prog))))

    # ㉗ **`new_song` 的音区修正被摘掉**（改成 no-op）→ 新生成的曲子又会整片"旋律撞伴奏"。
    #    用户 2026-09-22 的原话就是"new_song 修一下"（实测那次 8/10 段违反）。
    import new_song as _ns
    results.append(case('new_song 音区修正被摘掉', 'melody_register_fix',
                        lambda: Mut(_ns, 'fix_melody_register', lambda *a, **k: [])))
    # ㉘ **主奏音色池序退回"模板音色排最前"** → 引子（独奏位）又会拿到模板特色音色
    #     （实测那次是 GM 80 方波，用户"前面部分非常奇怪"）。
    results.append(case('主奏音色池序退回"模板音色排最前"', 'melody_prog_pool_order',
                        lambda: Mut(_ns, 'melody_prog_pool',
                                    lambda t: tuple(dict.fromkeys(
                                        [p for p in (t, 0, 13, 8, 4, 24, 9)
                                         if p is not None])))))

    # ㉙ **"流畅度"与"突兀声"两个量法坏不坏得起来**（用户 2026-09-22 要求沉淀成守卫）。
    #     ① 突兀声的对齐窗口改窄到 0 → 正常音头（起音延迟 42~78ms）全被判成"没有起音的杂音"
    #        —— 实测那次 5/5 全是误报；② 流畅度的断开门放到 999 拍 → 断得再多也不报。
    import probe_sustain as _ps
    _orig_sud, _orig_flow = _ps.sudden_sounds, _ps.melody_flow
    results.append(case('突兀声对齐窗口改窄（起音延迟误报）', 'flow_and_sudden_contracts',
                        lambda: Mut(_ps, 'sudden_sounds',
                                    lambda db, onsets, **k: _orig_sud(
                                        db, onsets, **dict(k, align=0.0)))))
    results.append(case('流畅度断开门放宽（断得再多也不报）', 'flow_and_sudden_contracts',
                        lambda: Mut(_ps, 'melody_flow',
                                    lambda notes, spb, **k: _orig_flow(
                                        notes, spb, gap_beat=999.0))))

    # ㉚ **ffmpeg 路径退回联网那一步** → 本机无网时所有 wav→ogg 卡死（实测一次卡半小时）。
    #     ⚠ 注入**不能**真的去调 `imageio_ffmpeg.get_ffmpeg_exe()`（那会把变异测试本身卡住），
    #     所以注入成"返回一个不在 binaries 下的路径"，由断言①抓到。
    import to_ogg as _to
    results.append(case('ffmpeg 路径不再指向本地 binaries', 'ffmpeg_exe_is_local',
                        lambda: Mut(_to, '_ffmpeg_exe',
                                    lambda: 'C:\\Windows\\notepad.exe')))

    # ㉛ **"飘太高"那一侧不再被修**（把 register_top_gaps 变空）→ 开头冲到 A6 又没人管
    #     （用户 2026-09-22："感觉这个音有点高了"，实测比伴奏高 34 半音而判据判合规）。
    import harmony_check as _hc2
    results.append(case('飘太高不再降八度（最高音侧失守）', 'melody_register_fix',
                        lambda: Mut(_hc2, 'register_top_gaps', lambda song: [])))

    # ㉜ **落点判据坏掉**（TVD 恒 0）→ "落点挤在少数格子里"再也不报。
    #     该守卫自带判据自证（把 12 个音全塞进同一个格必须破门）→ 注入后自证会失败 ⇒ 被抓到。
    import melody_gen as _mg2
    results.append(case('落点判据坏掉（TVD 恒 0）', 'melody_onset_spread',
                        lambda: Mut(_mg2, 'onset_tvd', lambda *a, **k: 0.0)))

    # ㉝ **`metrics` 又改回直连取 ffmpeg 路径** → 本机无外网时那一行会卡死，
    #     实测自检最慢项卡 **>9 分钟**（全量自检从 2 分钟掉回 >20 分钟），
    #     而且并行时**每个 worker 各卡一次**（288× 退化的真凶）。
    #     ⚠ 这里**必须改磁盘源码**：守卫 `t_ffmpeg_exe_is_local` 是 **AST 扫源码**的，
    #     改内存（`Mut`）它看不见。`__exit__` 负责还原；`case()` 的 `with` 保证会调到。
    _mp = os.path.join(ROOT, 'scripts', 'metrics.py')

    class MetricsNetMut:
        def __enter__(self):
            self.p = _mp
            self.old = open(self.p, encoding='utf-8').read()
            new = self.old.replace(
                "        import to_ogg\n        exe = to_ogg._ffmpeg_exe()",
                "        import imageio_ffmpeg\n"
                "        exe = imageio_ffmpeg.get_ffmpeg_exe()")
            assert new != self.old, '注入锚点不在了（`metrics._ffmpeg_exe` 的实现改过？）'
            with open(self.p, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(new)

        def __exit__(self, *a):
            with open(self.p, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(self.old)

    results.append(case('metrics 又改回直连取 ffmpeg（会卡死）', 'ffmpeg_exe_is_local',
                        MetricsNetMut))

    # 61. `expand_sections` 的三条硬校验被摘掉（`validate_plan` → 放行一切）必须被抓
    #     （2026-09-24）。这三个坑是"手写 sections 扩段"必踩的：和弦数 ≠ 小节数会让
    #     十几条守卫连环 `IndexError`、basis 少写一个字段只 FAIL 一条、`json.dump` 写盘
    #     判不合格（见坑 242/243/244）。摘掉校验后 `t_expand_sections_contract` 的
    #     6 条反例全都不再抛 → 本项必 FAIL。
    import expand_sections as _exs
    results.append(case(
        'expand_sections 校验失效（和弦数≠小节数也放行）', 'expand_sections_contract',
        lambda: Mut(_exs, 'validate_plan',
                    lambda plan, song=None, allow_new_melody=False:
                    (list(plan.get('sections') or []),
                     sum(int(s.get('bars') or 0)
                         for s in (plan.get('sections') or []))))))

    # 62. `transcribe_to_song.chord_tones` 把低音**写死**（老 bug 的形态：所有和弦同一个
    #     低音）必须被抓（2026-09-25）。现场：`return 34, [...]`（34 = A#1）→ `check_song`
    #     报 `siren_end` **20 个和弦种全部**"低音与根音不符"。这条检查的第一部分
    #     （自证"不同根音必须给出不同低音"）就该当场炸。
    import transcribe_to_song as _ts
    results.append(case(
        '和弦低音写死成 A#1（老 bug 回归）', 'chord_bass_matches_root',
        lambda: Mut(_ts, 'chord_tones', lambda name: (34, [60, 64, 67]))))

    # 63. `patterns.melody_exempt` 的**理由写成空话**时不许放行（2026-09-25）——
    #     "理由空白 = 没写 = 不放行"这条口径必须有变异用例守着，否则豁免会变成
    #     一键绕过所有旋律形态判据的后门。夹具 = 库里密度低于
    #     `probe_melody_health.MIN_DENS` 的曲目（还原曲）；没有就跳过（曲目会被删，
    #     不硬编码曲名 —— 见 `SkipCase` 的由来）。
    import probe_melody_health as _mh
    _low63 = [r for r in _mh.collect() if r['dens'] < _mh.MIN_DENS]
    if not _low63:
        print('  %-5s %-34s → %s' % ('跳过', '豁免理由写成空话（密度维还想放行）',
                                     '库里没有密度低于下限的曲目'))
        results.append(True)                 # 与 `case` 的 SkipCase 同口径：不算漏
    else:
        _p63 = os.path.join(st.ROOT, 'songs', _low63[0]['name'], 'song.json')

        class _BlankExempt:
            def __enter__(self):
                self.old = open(_p63, encoding='utf-8').read()
                _d = json.loads(self.old)
                _d.setdefault('patterns', {})['melody_exempt'] = {'dens': '   '}
                json.dump(_d, open(_p63, 'w', encoding='utf-8', newline='\n'),
                          ensure_ascii=False, indent=1)
                return _d

            def __exit__(self, *a):
                open(_p63, 'w', encoding='utf-8', newline='\n').write(self.old)

        results.append(case('豁免理由写成空话（密度维还想放行）', 'melody_health',
                            lambda: _BlankExempt()))

    # 64. `range_fit` 退化成"**整轨**移八度"（引擎的老行为）必须被抓（2026-09-25）。
    #     现场：Strings 只 4% 越界，引擎整轨 +12 → 96% 本来正确的音被改掉。
    #     注入口径就是这个形态：不管越没越界，所有音一律 +12。
    results.append(case(
        '音域夹取退化成整轨移八度', 'transcribe_range_within_instrument',
        lambda: Mut(_ts, 'range_fit',
                    lambda notes, tr: [(st, en, p + 12, v)
                                       for (st, en, p, v) in notes])))

    # 65. `probe_bpm_layers` 的自相关层判据打桩（永远只报一个 BPM）必须被抓（2026-09-25）。
    #     它要是认不出"已知 120 BPM 的合成 click"，拿它定的速度层级就会让小节数翻倍/减半。
    import probe_bpm_layers as _pb
    results.append(case(
        'BPM 层级判据打桩（永远报 60）', 'bpm_layers_contract',
        lambda: Mut(_pb, 'autocorr_layers',
                    lambda env, fps, lo_bpm=40.0, hi_bpm=220.0, top=4: [(60.0, 1.0)])))

    # 66. `restore_gap_fill` 的能量门槛失效（`bar_rms` 恒返回 0dB → 分轨在静音处的残余
    #     会被当成音符补进来）必须被抓（2026-09-25 实测：结尾 139/140 原曲 −52/−70dB，
    #     补进去后成品 −9.5/−12.4dB = "该没有声音的地方出现了声音"）。
    import restore_gap_fill as _rg
    results.append(case(
        '补漏的能量门槛失效（静音处也补）', 'restore_gap_fill_contract',
        lambda: Mut(_rg, 'bar_rms',
                    lambda audio, bar_sec, nbars: {b: 0.0 for b in range(nbars)})))

    print('\n结果: %d/%d 个故障被抓到' % (sum(results), len(results)))
    if not all(results):
        print('漏掉的故障意味着对应的自检项是坏的 —— 必须先修检查，而不是继续写歌')
    return 0 if all(results) else 1


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    sys.exit(main())
