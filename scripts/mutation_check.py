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


def run_check(name):
    """跑指定检查，返回 (是否被拦下, 说明)"""
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
        return True, '%s: %s' % (type(e).__name__, str(e)[:70])


class Mut:
    """临时替换某对象的属性（退出时还原）"""
    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)

    def __exit__(self, *a):
        setattr(self.obj, self.name, self.old)


def case(label, check, mutate):
    with mutate():
        caught, why = run_check(check)
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

    def lossy_write(path, tracks, ppq=480):
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
    json.dump({'name': 'broken'}, open(bad_ref, 'w', encoding='utf-8'))
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
        _j.dump(d, open(tmp2, 'w', encoding='utf-8'), ensure_ascii=False)
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

    # 21~23. 音频故障：这两条检查**自己直接读音频**（sf.read），不走 metrics.load
    real_sf_read = st.sf.read
    results.append(case('成品音频削波', 'audio_health',
                        lambda: Mut(st.sf, 'read',
                                    lambda f, **k: (lambda t: (t[0] * 4.0, t[1]))(
                                        real_sf_read(f, **k)))))
    results.append(case('成品时长只有一半', 'audio_semantics',
                        lambda: Mut(st.sf, 'read',
                                    lambda f, **k: (lambda t: (t[0][:len(t[0]) // 2], t[1]))(
                                        real_sf_read(f, **k)))))
    results.append(case('有声段落被静音', 'audio_semantics',
                        lambda: Mut(st.sf, 'read',
                                    lambda f, **k: (lambda t: (t[0] * 1e-6, t[1]))(
                                        real_sf_read(f, **k)))))

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
                    json.dump(c, open(rp, 'w', encoding='utf-8'),
                              ensure_ascii=False, indent=1)
            self._ref = st.scorecard.load_ref
            st.scorecard.load_ref = shifted_ref
            return self

        def __exit__(self, *a):
            for rp, txt in self.saved.items():
                open(rp, 'w', encoding='utf-8').write(txt)
            st.scorecard.load_ref = self._ref

    results.append(case('画像整体偏移 20dB（声明 strict_align 后必须被抓）',
                        'alignment_vs_refs', lambda: _StrictAndShift()))

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
            open(p, 'w', encoding='utf-8').write('# BGM Studio\n\n（没有 frontmatter）')
            token_audit.DOCS['SKILL.md（音乐任务加载）'] = p
        def __exit__(self, *a):
            token_audit.DOCS['SKILL.md（音乐任务加载）'] = self.old
    results.append(case('技能 frontmatter 丢失', 'docs_budget_and_skill_intact', FmMut))

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
            open(p, 'w', encoding='utf-8').write(
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
                                    lambda ch, nxt, i, pat:
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

    def perc_misaligned(style, level, i, nbars, layers=None, kick_vel=None):
        out = real_perc(style, level, i, nbars, layers, kick_vel)
        if layers and style == 'pump':
            out = [e for e in out if not (e[2] in (41, 43) and e[0] % 1.0 == 0.75)]
        return out
    results.append(case('kick 垫层与底鼓错位', 'perc_layers',
                        lambda: Mut(song_engine, 'perc_part', perc_misaligned)))

    # 48. air 垫层漏掉一半十六分格（5–10kHz 又回到"点+空"：实测占用率 100%→84%）
    def perc_air_gap(style, level, i, nbars, layers=None, kick_vel=None):
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
    def perc_offbeat_half(style, level, i, nbars, layers=None, kick_vel=None):
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
                open(_sk, 'w', encoding='utf-8').write(new)
                return new
            def __exit__(self, *a):
                open(_sk, 'w', encoding='utf-8').write(self.old)
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
            open(_mp_path, 'w', encoding='utf-8').write(new_txt)
            return new_txt
        def __exit__(self, *a):
            open(_mp_path, 'w', encoding='utf-8').write(self.old)
    results.append(case('旋律主音建议丢掉和声对齐', 'melody_profile_tonic_hint',
                        lambda: _StripAlign()))

    # 57. 音域越界（低音整体多移一个八度 → 次声波）必须被抓
    import selftest as _st
    _f57 = _st.fixture_song()
    _s20 = os.path.join(_f57, 'song.json') if _f57 else ''
    if os.path.exists(_s20):
        class _LowerBass:
            def __enter__(self):
                self.old = open(_s20, encoding='utf-8').read()
                d = json.loads(self.old)
                d['chords'] = {k: [v[0] - 12, list(v[1])] for k, v in d['chords'].items()}
                json.dump(d, open(_s20, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
                return d
            def __exit__(self, *a):
                open(_s20, 'w', encoding='utf-8').write(self.old)
        results.append(case('低音整体再降八度（次声波）', 'track_ranges_musical',
                            lambda: _LowerBass()))

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
                json.dump(d, open(_sp20, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
                return d
            def __exit__(self, *a):
                open(_sp20, 'w', encoding='utf-8').write(self.old)
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
                                    lambda ch, i, B=4.0: [(0.5, 0.28, m, 60)
                                                          for m in ch[1][:3]])))

    # 换气判据算错（把"缝隙"忽略、全曲当成一段）→ 判据自证必须报警
    results.append(case('换气判据算错（无视缝隙）',
                        'melody_breathing',
                        lambda: Mut(st, '_breath_runs',
                                    lambda iv, gap=0.5: [(min(s for s, _e in iv),
                                                          max(e for _s, e in iv))])))

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

    print('\n结果: %d/%d 个故障被抓到' % (sum(results), len(results)))
    if not all(results):
        print('漏掉的故障意味着对应的自检项是坏的 —— 必须先修检查，而不是继续写歌')
    return 0 if all(results) else 1


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    sys.exit(main())
