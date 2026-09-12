#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成绩单：我的成品 vs 参考曲画像（refs/*.json）→ 一屏表格 + **可直接粘贴的调参建议**

这是把「渲染→测→调→再渲染」的多轮循环压成一轮的关键：一次输出差距 + 该改哪个参数。

用法:
  python scorecard.py <我的文件.wav|ogg> --ref refs/bgm01c.json
  python scorecard.py <我的文件> --ref bgm01c --render songs/02_dn128_drive/drive_pop.mid
"""
import json
import os
import sys

import metrics

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REFS = os.path.join(ROOT, 'refs')


def load_ref(spec):
    p = spec if os.path.isabs(spec) else os.path.join(REFS, spec)
    if not p.endswith('.json'):
        p += '.json'
    if not os.path.exists(p):
        have = [f[:-5] for f in sorted(os.listdir(REFS)) if f.endswith('.json')] \
            if os.path.isdir(REFS) else []
        raise SystemExit('找不到参考曲画像 %s\n  已有: %s\n'
                         '  新建: scripts\\profile_ref.py <参考曲> <名字>'
                         % (p, ', '.join(have) or '（无）'))
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def _limit(key):
    """渲染参数的可行范围（与 make_song.LIMITS 同源；惰性导入避免循环依赖）"""
    try:
        import make_song
        return make_song.LIMITS[key]
    except Exception:
        return {'low': (-4.0, 9.0), 'mid_db': (-4.0, 10.0), 'shelf': (-3.0, 10.0),
                'hp': (18.0, 60.0), 'width': (0.8, 2.6)}.get(key, (-99.0, 99.0))


def _eq_target(cfg, key, direction, step):
    """(建议写入的绝对参数值, 是否已到顶)。

    成绩单是在**成品**上跑的，此时 EQ 已经生效过一轮 —— 若按"从零起步"给绝对值，
    已到限的参数会得到**反方向**的建议（实测：shelf 已到 -3 下限时，它建议改成
    -2.8，等于让成品更亮）。所以有 render.json 时按"当前值再走一步"算；
    走不动了（夹到限位）就交回调用方，改给编配建议。"""
    if not cfg or key not in cfg:
        return direction * step, False
    lo, hi = _limit(key)
    cur = float(cfg[key])
    new = round(max(lo, min(hi, cur + direction * step)), 2)
    return new, abs(new - cur) < 1e-9


def _arr_tip(band, need_more, data):
    """EQ 到顶时改指编配（复用 make_song 的按状态提示，保证两处口径一致）"""
    try:
        import make_song
        prog, mix, arr = data if data else ({}, {}, set())
        return make_song._tip(make_song._tip_group(band), need_more, prog, mix, arr)
    except Exception:
        return '改 song.json 编配（调对应乐器轨音量）'


def suggest(mine, ref, cfg=None, data=None):
    """根据差距给出建议（只给真正需要的项）。

    两条纪律：
    · **只对参与对标的频段出主意** —— 人声主导的参考曲，sub/顶频是母带切出来的
      （实测 13 首真实 BGM 的 20-40Hz 都在 -9.9~-34.4dB，人声曲却是 -68.7dB），
      照它调只会把配器调得比所有真实 BGM 都薄。早期这里无视 align_bands，
      一边打印"已跳过 20-40"，一边建议"提高高通" —— 自相矛盾。
    · 参数到顶就说"到顶+改编配"，不开空头支票。"""
    s = []
    rb = ref['bands']
    al = set(metrics.aligned_bands(ref))      # 口径唯一来源（与调参同一份）
    g = metrics.judge_gaps(mine, ref)         # 分组偏差也来自同一份判据
    TOL = metrics.TOL

    def label(keys, default):
        """组名要如实反映**实际参与对标**的频段（人声曲只对齐到 5-10kHz）"""
        use = [k for k in keys if k in al]
        return default if len(use) == len(keys) else '+'.join(use)

    def eq(key, direction, step, what, gap, band, need_more):
        val, capped = _eq_target(cfg, key, direction, step)
        if capped:
            s.append('%s %+.1fdB → %s 已到%s(%.2f)，靠编配：%s'
                     % (what, gap, key, '上限' if direction > 0 else '下限',
                        float(cfg[key]), _arr_tip(band, need_more, data)))
        else:
            s.append('%s %+.1fdB → --%s %.2f' % (what, gap, key, val))

    # 阈值统一用 metrics.TOL：以前成绩单用 -2/+2.5、调参用 ±1.5，同一件事两套口径。
    low, sub, top, presence = g['low'], g['sub'], g['top'], g['presence']
    if sub is not None and sub > 6:
        eq('hp', +1, 8.0, '20-40Hz', sub, '20-40', False)
    if low is not None and low < -TOL:
        eq('low', +1, min(5.0, -low + 0.5), label(['40-80', '80-160'], '40-160Hz'),
           low, '40-80', True)
    elif low is not None and low > TOL:
        eq('low', -1, min(3.0, low - 1), label(['40-80', '80-160'], '40-160Hz'),
           low, '40-80', False)
    if top is not None and top > TOL:
        eq('shelf', -1, min(3.0, (top - 1) / 2),
           label(['5000-10000', '10000-18000'], '5-18kHz'), top, '5000-10000', False)
    elif top is not None and top < -TOL:
        eq('shelf', +1, min(4.0, (-top - 1) / 2),
           label(['5000-10000', '10000-18000'], '5-18kHz'), top, '5000-10000', True)
    if presence is not None and presence < -TOL:
        s.append('315-1250Hz 少 %.1fdB → 编配里加中频乐器/提高该轨 CC7' % (-presence))
    dw = -g['width']
    if dw > 0.06:
        eq('width', +1, min(1.0, dw * 4), '宽度窄', dw, '315-630', True)
    elif dw < -0.1:
        eq('width', -1, min(1.0, -dw * 3), '宽度宽', dw, '315-630', False)
    dr = -g['rms']
    if abs(dr) > 1.2:
        s.append('响度差 %+.1fdB → --rms %.1f' % (dr, ref['rms_db']))
    if mine['rhythm_low'] != ref['rhythm_low']:
        s.append('低频节奏型不一致（见下表）→ 调底鼓/贝斯力度与时值')
    return s


def _song_ctx(path):
    """读成品旁边的 render.json / song.json → (渲染参数, 编配上下文)；读不到给 None"""
    folder = os.path.dirname(os.path.abspath(path))
    cfg = data = None
    try:
        with open(os.path.join(folder, 'render.json'), encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception:
        cfg = None
    try:
        with open(os.path.join(folder, 'song.json'), encoding='utf-8') as f:
            d = json.load(f)
        mix = {k: v[1] for k, v in (d.get('mix') or {}).items() if isinstance(v, list)}
        arr = set()
        for sec in d.get('sections') or []:
            arr |= {k for k, v in (sec.get('arr') or {}).items() if v}
        data = (d.get('programs') or {}, mix, arr)
    except Exception:
        data = None
    return cfg, data


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        return 1
    mine_path = args[0]
    ref_spec = 'bgm01c'
    render_ctx = None
    if '--ref' in sys.argv:
        ref_spec = sys.argv[sys.argv.index('--ref') + 1]
    if '--render' in sys.argv:
        render_ctx = sys.argv[sys.argv.index('--render') + 1]
    ref = load_ref(ref_spec)
    bpm_arg = None
    if '--bpm' in sys.argv:
        try:
            bpm_arg = float(sys.argv[sys.argv.index('--bpm') + 1])
        except ValueError:
            print('!! --bpm 后面要跟数字')
            return 1
    # 我的文件测速：知道真实速度（MIDI tempo / --bpm）就直接用 —— 连奏编配靠音频测速
    # 会误判（gorgeous 编配的 106BPM 实测被读成 154.3），速度判错则节奏型/调式/结构
    # 全在错位的小节网格上算。不知道速度时才自动测速，并按参考速度做倍频吸附。
    mine = metrics.profile(mine_path, bpm_arg) if bpm_arg else metrics.profile(mine_path)
    mine['name'] = os.path.basename(mine_path)
    if bpm_arg is None:
        for k in (2, 3, 4):
            for cand in (ref['bpm'] * k, ref['bpm'] / k):
                if abs(mine['bpm'] - cand) < max(3.0, cand * 0.04):
                    print('(测速 %.1f 吸附到 %.1f，与参考一致)'
                          % (mine['bpm'], ref['bpm']))
                    mine = metrics.profile(mine_path, ref['bpm'])
                    mine['name'] = os.path.basename(mine_path)
                    break
            else:
                continue
            break
    if abs(mine['bpm'] - ref['bpm']) > 1.0:
        tip = '' if bpm_arg else '（音频测速在连奏编配上会误判，知道真实速度就加 --bpm）'
        print('!! 速度不一致：本曲 %.1f vs 参考 %.1f —— 先改作曲脚本的 BPM 再谈其它%s'
              % (mine['bpm'], ref['bpm'], tip))

    print('== %s  vs  %s ==' % (mine['name'], ref['name']))
    print('%-14s %10s %10s %8s' % ('指标', '本曲', '参考', '差'))
    rows = [('响度 RMS', mine['rms_db'], ref['rms_db'], '%.1f'),
            ('立体声宽度', mine['width'], ref['width'], '%.3f'),
            ('频谱质心Hz', mine['centroid'], ref['centroid'], '%d'),
            ('速度 BPM', mine['bpm'], ref['bpm'], '%.1f')]
    for label, a, b, fmt in rows:
        print('%-14s %10s %10s %8s' % (label, fmt % a, fmt % b, fmt % (a - b)))
    print('%-14s %10s %10s %8s' % ('倍频程(最响段=0dB)', '', '', ''))
    for k in ref['bands']:
        print('  %-12s %10.1f %10.1f %8.1f' % (k, mine['bands'][k], ref['bands'][k],
                                               mine['bands'][k] - ref['bands'][k]))
    print('%-14s %s' % ('低频节奏型', mine['rhythm_low']))
    print('%-14s %s' % ('  (参考)', ref['rhythm_low']))
    print('%-14s %s' % ('高频节奏型', mine['rhythm_high']))
    print('%-14s %s' % ('  (参考)', ref['rhythm_high']))
    ch = ' '.join('%s(%.2f)' % (k, v) for k, v in list(mine['quiet_chroma'].items())[:5])
    print('%-14s %s' % ('安静段调式', ch))
    if ref.get('character') == 'vocal_forward':
        skipped = [k for k in ref['bands'] if k not in (ref.get('align_bands') or [])]
        print('注意            参考曲是**人声主导**（最强段 315-630Hz、几乎无 sub/顶频）')
        print('                逐频段对齐不适用，已跳过 %s；按"性格"对齐（调性/速度/宽度/中频重心）'
              % ', '.join(skipped))
        if '20-40' in skipped and ref['bands'].get('20-40', 0.0) < -45:
            print('                该参考曲低频是母带切掉的（20-40Hz %.1fdB，而 13 首真实 BGM 都在'
                  % ref['bands']['20-40'])
            print('                -9.9~-34.4dB）—— 那是它的制作选择，别据此加/减 sub')
        # 同理：有些**人声混音**的高频被压得极狠（去齿音 + 母带）。拿它当 5-10kHz 的标尺，
        # 会把器乐压到比游戏里任何 BGM 都闷。这里只**如实说明**，不动判据。
        if (ref['bands'].get('5000-10000', 0.0) < -40
                and '5000-10000' in (ref.get('align_bands') or [])):
            print('                该参考曲 5-10kHz 也极暗（%.1fdB，比游戏里最暗的器乐 BGM'
                  % ref['bands']['5000-10000'])
            print('                −29.5dB 还低 17dB）：这一段的"差距"是人声混音的母带特征，')
            print('                器乐只要不暗过真实 BGM 就够 —— 别为它把高频压到发闷')

    cfg, data = _song_ctx(mine_path)
    tips = suggest(mine, ref, cfg, data)
    print('\n== 建议 ==')
    if not tips:
        print('  已对齐（各项都在容差内）')
    for t in tips:
        print('  - %s' % t)
    if render_ctx:
        import re
        flags = []
        for t in tips:
            flags += re.findall(r'--[a-z]+ [+-]?[\d.]+', t)
        if flags:
            print('\n重跑命令： python scripts\\render_midi.py %s <输出名> %s'
                  % (render_ctx, ' '.join(flags)))
    return 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    sys.exit(main())
