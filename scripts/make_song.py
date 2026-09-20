#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**一条命令出一首歌**：作曲 → 真音源渲染 → **自动调参** → 成绩单

自动调参（autotune）是省 token 的核心：把「渲染→测→调参数→再渲染」的循环
放进程序内部跑（最多 6 轮），不占用对话轮次。收敛后把参数写回 render.json。

用法:
  python make_song.py 02_dn128_drive                # 全流程（含自动调参）
  python make_song.py 02_dn128_drive --check        # **先 2 秒查数据**再渲染（推荐）
  python make_song.py 02_dn128_drive --no-tune      # 只渲染一轮 + 成绩单
  python make_song.py 02_dn128_drive --no-compose   # 跳过作曲

为什么要 --check：一轮 = 30~120 秒，而 `song.json` 是手写的 —— 和弦音集不符、
旋律强拍错音、轨名拼错、通道撞车这些**数据错**本来 2 秒就能查出来
（`check_song.py` 直接复用自检的 66 项判据）。实测一次血亏：写完 72 小节直接渲染 →
自检 FAIL → 改数据 → 再渲染，来回四轮才交付。
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SONGS = os.path.join(ROOT, 'songs')
sys.path.insert(0, HERE)

import metrics            # noqa: E402
import scorecard          # noqa: E402  复用成绩单
import render_midi        # noqa: E402
import song_engine        # noqa: E402

# 各参数的允许范围：**故意收得保守**。
# 教训：差距 >4dB 通常是编配缺能量，不是 EQ 不够；硬推 EQ 只会变刺耳。
LIMITS = {'low': (-4.0, 9.0), 'mid_db': (-4.0, 10.0), 'shelf': (-3.0, 10.0),
          # 宽度上限必须收在 2.6：超过后软限幅对 mid/side 的压缩差异变大，
          # 宽度对倍数变成非线性（实测 4.0→0.86、3.81→1.19，会自己跑飞）
          'width': (0.8, 2.6), 'hp': (18.0, 60.0)}
TOL = 1.5          # 频段差 < 1.5dB 视为达标
# 频段 → 该动哪一轨（EQ 到顶时给的建议）
BAND_HINT = [
    ((20, 400), 'Bass 轨：改 song.json 的 mix.Bass 音量，或把和弦贝斯音降低八度'),
    ((315, 1250), '钢琴/吉他轨：mix.Piano / mix.Hook 音量'),
    ((1250, 5000), '吉他音色换亮一点：programs.Hook 24(尼龙)→25(钢弦)，或加 arr.arp 琶音层'),
    ((5000, 18000), '钟琴/打击：mix.Glock / mix.Perc 音量，或 perc_style 提高一级'),
]


def _tip_group(band):
    """频段 → 该动哪一组（单频段提示用；按名字映射，别用区间判断——315-400 会重叠）"""
    return {'20-40': 20, '40-80': 20, '80-160': 20,
            '160-315': 315, '315-630': 315, '630-1250': 315,
            '1250-2500': 1250, '2500-5000': 1250,
            '5000-10000': 5000, '10000-18000': 5000}.get(band, 315)


def _tip(lo, up, prog, mix, arr_on):
    """某个频段偏薄/偏厚时该动哪一轨（按当前状态给，不出现无效建议）"""
    if lo == 20:
        return ('提高 mix.Bass（当前 %s）' % mix.get('Bass', '—')) if up else \
               ('降低 mix.Bass（当前 %s）、sub_gain 调小，或把和弦贝斯音提高八度'
                % mix.get('Bass', '—'))
    if lo == 315:
        return ('提高 mix.Piano/mix.Hook 音量（当前 %s/%s）'
                % (mix.get('Piano', '—'), mix.get('Hook', '—')) if up else
                '降低 mix.Piano/mix.Hook 音量，或把和弦声部上移（voicing_shift +12）')
    if lo == 1250:
        if up:
            hook = prog.get('Hook', [None])[0]
            bits = []
            if hook not in (25, 26):
                bits.append('programs.Hook %s→25(钢弦)' % hook)
            if 'arp' not in arr_on:
                bits.append('段落加 arr.arp')
            bits.append('提高 mix.Hook/mix.Arp（当前 %s/%s）'
                        % (mix.get('Hook', '—'), mix.get('Arp', '—')))
            return '；'.join(bits)
        return '降低 mix.Hook/mix.Arp 或换更暖的音色'
    return ('提高 mix.Glock（当前 %s）' % mix.get('Glock', '—')) if up else \
           '降低 mix.Glock/mix.Perc（当前 %s/%s）' % (mix.get('Glock', '—'),
                                                     mix.get('Perc', '—'))


def band_hints(mine, ref, data=None):
    """EQ 已到顶但仍有差距 → 指出该改哪个乐器（别再推 EQ）。
    ① 单频段 |差| > 4dB 单独提示（分组平均会把"低频厚 6dB、中频薄 4dB"互相抵消掉）
    ② 分组（20-400 / 315-1250 / 1250-5000 / 5-18k）差 > 2.5dB 再给一次汇总提示
    传入 data（已载入的 song.json）时建议**按当前状态**给出。"""
    prog = (data or {}).get('programs', {})
    mix = {k: v[1] for k, v in (data or {}).get('mix', {}).items()}
    arr_on = set()
    for sec in (data or {}).get('sections', []):
        arr_on |= {k for k, v in sec.get('arr', {}).items() if v}
    out = []
    # ① 单频段
    for b, v in ref['bands'].items():
        if b not in aligned_bands(ref):
            continue
        gap = mine['bands'][b] - v
        if abs(gap) > 4.0:
            out.append('%sHz 差 %+.1fdB（单频段超 4dB）→ %s'
                       % (b, gap, _tip(_tip_group(b), gap < 0, prog, mix, arr_on)))
    # ② 分组汇总
    for lo, hi in ((20, 400), (315, 1250), (1250, 5000), (5000, 18000)):
        keys = [k for k in ref['bands']
                if lo <= int(k.split('-')[0]) < hi and k in aligned_bands(ref)]
        if not keys:
            continue
        gap = sum(mine['bands'][k] - ref['bands'][k] for k in keys) / len(keys)
        if abs(gap) > 2.5:
            out.append('%d-%dHz 差 %+.1fdB（组内平均）→ %s'
                       % (lo, hi, gap, _tip(lo, gap < 0, prog, mix, arr_on)))
    return out


def midi_bpm(mid_path, data=None):
    """MIDI 里的真实速度（tempo 元事件 = 确定值）。

    不用音频测速：连奏编配（竖琴/弦乐/合唱）没有明显起音，`detect_bpm` 会误判 ——
    实测 gorgeous 编配的 106BPM 样带被读成 154.3（而自相关峰值正好落在一小节上）。
    速度一旦判错，节奏型/调式/结构就全在**错位的小节网格**上算。"""
    try:
        import midi_probe
        b = midi_probe.parse(mid_path, quiet=True).get('bpm')
    except Exception:
        b = None
    return b or ((data or {}).get('bpm'))


def measure(wav, ref, bpm=None):
    """量我的成品（有真实速度就直接用；否则自动测速 + 倍频吸附到参考速度）"""
    mine = metrics.profile(wav, bpm) if bpm else metrics.profile(wav)
    if bpm or abs(mine['bpm'] - ref['bpm']) <= 1.0:
        return mine
    for k in (2, 3, 4):
        for cand in (ref['bpm'] * k, ref['bpm'] / k):
            if abs(mine['bpm'] - cand) < max(3.0, cand * 0.04):
                return metrics.profile(wav, ref['bpm'])
    return mine


def aligned_bands(ref):
    """该参考曲参与对标/调参的频段（**口径来自 metrics，全链唯一**）"""
    return metrics.aligned_bands(ref)


def tune_step(mine, ref, cfg):
    """算一轮参数修正量；返回 (改动字典, 未达标说明)"""
    g = metrics.judge_gaps(mine, ref)     # 分组与容差的唯一口径在 metrics 里
    low, mid, top = g['low'] or 0.0, g['mid'] or 0.0, g['top'] or 0.0
    sub = g['sub'] or 0.0
    dw, dr = -g['width'], -g['rms']       # ref - mine（保持原方向约定）
    TOL = metrics.TOL
    delta, notes = {}, {}
    # 两个方向都用同一个容差：早期"偏厚"用了 TOL+1 的宽判据，
    # 结果 +2.5~3dB 的低频过厚永远不会被修正（自动调参假装达标）。
    if low < -TOL:
        delta['low'] = -low * 0.8
        notes['low'] = '40-160Hz 少 %.1f' % -low
    elif low > TOL:
        delta['low'] = -low * 0.8
        notes['low'] = '40-160Hz 多 %.1f' % low
    if mid < -TOL:
        delta['mid_db'] = -mid * 0.8
        notes['mid_db'] = '1.2-5kHz 薄 %.1f' % -mid
    elif mid > TOL:
        delta['mid_db'] = -mid * 0.8
        notes['mid_db'] = '1.2-5kHz 厚 %.1f' % mid
    if top < -TOL:
        delta['shelf'] = -top * 0.8
        notes['shelf'] = '5-18kHz 暗 %.1f' % -top
    elif top > TOL:
        delta['shelf'] = -top * 0.8
        notes['shelf'] = '5-18kHz 亮 %.1f' % top
    if sub > 5:
        delta['hp'] = 5.0
        notes['hp'] = '20-40Hz 多 %.1f' % sub
    elif sub < -7:
        delta['hp'] = -6.0
        notes['hp'] = '20-40Hz 少 %.1f' % -sub
    if abs(dw) > 0.04:
        # 宽度不在这里迭代：加宽在整条链最后，前面 EQ 每轮在变会让比例系数漂移，
        # 一律留给 autotune 收尾的 set_width_exact() 精确校准
        delta['width'] = 0.0
        notes['width'] = '宽度差 %+.3f（收尾精确校准）' % -dw
    if abs(dr) > 1.0:
        delta['rms'] = ref['rms_db']
        notes['rms'] = '响度差 %+.1f' % dr
    # 注意：这里**不做夹紧**。夹紧与"到顶"报告统一由 autotune 负责 ——
    # 两层夹紧会让"想要 −8.24、被夹成 0"这个信息丢失，日志就会谎报达标。
    return delta, notes

def target_gaps(mine, ref):
    """每个参数对应的"目标误差"（用于震荡检测）；只统计参与对标的频段"""
    al = aligned_bands(ref)
    b = {k: mine['bands'][k] - ref['bands'][k] for k in ref['bands']}

    def grp(keys):
        vals = [b[k] for k in keys if k in al]
        return sum(vals) / len(vals) if vals else 0.0
    return {
        'low': grp(['40-80', '80-160']),
        'mid_db': grp(['1250-2500', '2500-5000']),
        'shelf': grp(['5000-10000', '10000-18000']),
        'hp': b['20-40'] if '20-40' in al else 0.0,
        'width': mine['width'] - ref['width'],
        'rms': mine['rms_db'] - ref['rms_db'],
    }


def autotune(cfg, ref, mid_path, out_base, max_iter=6, data=None):
    """内部闭环调参：不占对话轮次。
    两道保险：① 参数到上限就停手并给改编配的建议；
              ② 某参数调完反而更差 → 回退并冻结它（防止越调越偏/震荡）。"""
    for k, (lo, hi) in LIMITS.items():
        if k in cfg:
            cfg[k] = round(max(lo, min(hi, float(cfg[k]))), 2)
    true_bpm = midi_bpm(mid_path, data)
    frozen = set()
    prev = {}            # 参数 -> (上一轮的值, 上一轮该参数的误差)
    last = None
    baseline = cfg.get('last_bands') or {}     # 上一次运行的实测频段（跨次比较用）
    regress_warned = False
    for it in range(max_iter):
        render_midi.LAST.clear()      # **只信本次渲染自报的状态**：打桩/异常时不许吃上一次的旧值
        render_midi.render(mid_path, out_base,
                           rms_db=cfg['rms'], width=cfg['width'],
                           shelf_db=cfg['shelf'], hp_hz=cfg['hp'],
                           low_db=cfg['low'], drive=cfg['drive'],
                           mid_db=cfg.get('mid_db', 0.0), verbose=False,
                           ogg=False,                  # 中间轮次不编码 OGG（马上会被覆盖）
                           reverb=cfg.get('reverb'))   # 混响可选覆盖（opt-in，默认不变）
        # **响度到顶要当场认下来**：峰值上限（0.97）挡住响度目标时，重设多少次同一个
        # rms 都不会变（实测：6 轮里 5 轮都在"调 响度差 +1.3"，白烧 ~150 秒渲染）。
        # 判据来自 render_midi.LAST（渲染器自报的"峰值受限 + 差多少"），不是猜的。
        _last = render_midi.LAST or {}
        if (_last.get('peak_limited') and 'rms' not in frozen
                and abs(_last.get('shortfall_db') or 0.0) > metrics.TOL):
            frozen.add('rms')
            print('  ! 响度到顶：峰值上限 0.97 挡住了响度目标（实得 %+.1fdB）→ 冻结 rms，'
                  '不再每轮重设（剩下的差距靠编配或降宽度）'
                  % (_last.get('shortfall_db') or 0.0))
        mine = measure(out_base + '.wav', ref, true_bpm)
        # **跨次回归检测**（实测踩过）：改了 song.json（试编配/音色）之后，若某频段比
        # **上次运行**更差，多半不是"改得不够"，而是被自动调参反制了 —— 例如把 Strings
        # 提 22 反而让 1250–2500 更薄（它为了压别处把整体拉低）。这时该收冲突源，不是继续加。
        if baseline and not regress_warned:
            al = aligned_bands(ref)
            worse = [(k, mine['bands'][k] - baseline[k]) for k in al
                     if k in baseline and mine['bands'][k] - baseline[k] < -metrics.TOL]
            if worse:
                print('  ! 与上次相比变差（>%.1f dB）：%s'
                      % (metrics.TOL, '; '.join('%s %+.1f' % (k, dv) for k, dv in sorted(worse))))
                print('    多半是自动调参反制了你的编配改动 —— 优先**收小冲突源**（如打击/亮音色），'
                      '而不是继续加大别的轨')
            regress_warned = True
        last = mine
        # **生成期间的和谐体检**（用户 2026-09-21："以后生成音乐最后检查是否和谐" +
        #   "生成期间也要注意"）。放在**每轮渲染之后**：调参动的是混音参数，但
        #   "调参把某轨推得盖住旋律 / 编配本身音区撞车"这类不和谐，只有在这里才当场看得见。
        #   判据收在 `scripts/harmony_check.py`（与 `check_song` 共用一份，别抄）。
        if data and (it == 0 or it == max_iter - 1):
            try:
                import harmony_check as _hc
                _hz = _hc.check(data)
            except Exception as _e:                              # noqa: BLE001
                _hz, _ = ['（和谐体检跑不起来：%s）' % type(_e).__name__], None
            if _hz:
                print('  ⚠ 和谐体检（第%d轮）：%d 个问题' % (it + 1, len(_hz)))
                for _x in _hz[:4]:
                    print('      · %s' % _x)
                if it == max_iter - 1:
                    print('      （生成链收尾仍不和谐 → 改 `song.json` 的编配/音区，'
                          '细节见 `SKILL.md` §3 第 15 条）')
            elif it == 0:
                print('  ✓ 和谐体检：三项都过（音区间距 / 撞音 / 长音层）')
        gaps = target_gaps(mine, ref)
        # 震荡保护：上一轮调过的参数，若目标误差反而变大 → 回退+冻结
        # **没进展也冻结**：误差几乎不动（<0.1dB）说明这个方向已经到底（或到顶），
        # 再调就是每轮白烧一次渲染（实测 rms 曾这样烧掉 5 轮）
        for k, (old_v, old_gap) in list(prev.items()):
            if k in frozen:
                continue
            if abs(gaps[k]) > abs(old_gap) + 0.3:
                cfg[k] = old_v
                frozen.add(k)
            elif abs(gaps[k]) > abs(old_gap) - 0.1:
                frozen.add(k)
        prev = {}
        delta, notes = tune_step(mine, ref, cfg)
        capped = []
        for k in list(delta):
            if k == 'rms':
                continue
            lo, hi = LIMITS[k]
            cur = cfg.get(k, 0.0)
            want = delta[k]
            delta[k] = max(lo - cur, min(hi - cur, delta[k]))
            # 两种"到顶"都要报：① 方向被上限挡住 ② 已被夹成 0（参数本就在边界）。
            # 早期只判了 ①，于是 ② 会静默变成 0 → 日志报"✓ 达标"，实际还差 10dB
            # （这是"假装收敛"这一类 bug 的第三次出现）。
            if want and delta[k] == 0:
                delta.pop(k)
                capped.append(k)
        for k in frozen:
            delta.pop(k, None)
        delta.pop('width', None)         # 宽度收尾精确校准，循环里不动
        applied = {k: v for k, v in delta.items() if v}
        shown = [notes[k] for k in applied if k in notes]
        line = '  第%d轮: 质心%.0f 宽度%.3f(w=%.2f)%s' % (
            it + 1, mine['centroid'], mine['width'], cfg.get('width', 0),
            ('  → 调 ' + ', '.join(shown)) if shown else '  ✓ 达标')
        extra = []
        if capped:
            extra.append('到顶: %s' % '/'.join(capped))
        if frozen:
            extra.append('已冻结: %s' % '/'.join(sorted(frozen)))
        if extra:
            line += '（%s）' % '；'.join(extra)
        print(line, flush=True)
        if not applied:
            break
        for k, v in applied.items():
            prev[k] = (cfg.get(k, 0.0), gaps[k])      # 记录"改动前"的值与误差（含 rms）
            if k == 'rms':
                cfg['rms'] = v
            else:
                lo, hi = LIMITS[k]
                cfg[k] = round(max(lo, min(hi, cfg.get(k, 0.0) + v)), 2)

    # 收尾：用成品反推，把宽度**精确**校到参考曲（不受前面 EQ 变化影响）；
    # 然后**只编码一次** OGG（循环里每轮都编的话，5 分钟的歌白烧 ~8s × 轮数）
    if last is not None:
        if abs(last['width'] - ref['width']) > 0.03:
            w = render_midi.set_width_exact(out_base + '.wav', ref['width'])
            if w is not None:
                print('  收尾: 宽度精确校准 → %.3f（目标 %.3f）' % (w, ref['width']))
        render_midi.encode_ogg(out_base)      # 全程只编码这一次
        hints = band_hints(last, ref, data)
        if hints:
            print('  ⚠ EQ 到头了，剩下的差距要靠编配（改 song.json）：')
            for h in hints:
                print('    - %s' % h)
    return cfg


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        return 1
    # 面板守卫（硬形式）：没在跑就先拉起来 —— 见 scripts/studio_guard.py 顶部那段
    import studio_guard
    studio_guard.ensure_panel()
    # A′（2026-09-19 用户拍板）：**默认把渲染委托给面板** —— 手敲 CLI 就等于在面板里建任务，
    # GUI 全程可见、产物立刻能听；面板内部调用（BGM_STUDIO_INNER）和批量（BGM_CLI_DIRECT）
    # 走原生实现。带 `--check`/`--no-compose` 等面板无等价任务的开关时不委托（见 studio_guard）。
    _rc = studio_guard.delegate_make_song(sys.argv)
    if _rc is not None:
        return _rc
    song = args[0]
    folder = os.path.join(SONGS, song)
    if not os.path.isdir(folder):
        print('找不到 %s，可选: %s' % (folder, ', '.join(sorted(os.listdir(SONGS)))))
        return 1
    cfg_path = os.path.join(folder, 'render.json')
    cfg = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = json.load(f)
    composer = cfg.get('composer')
    # ⚠ **首次跑必然没有 `render.json`**（它由本次渲染写出）→ `composer` 恒为空 →
    #   打印「[1/3] 跳过作曲」→ 接着报「找不到 MIDI」。而那句提示
    #   「第一次跑不要加 --no-compose（要先生成 MIDI）」是**误导**：根本没加那个开关。
    #   实测（2026-09-18 BGM35 还原）：在这上面连卡 4 轮，最后靠手动跑 compose.py 才过。
    #   修法：没有 `render.json` 时，曲目目录里有 `compose.py` 就默认用它 ——
    #   与 `songs/*/compose.py` 的既有约定一致（该文件只负责调用引擎）。
    if not composer and os.path.exists(os.path.join(folder, 'compose.py')):
        composer = 'compose.py'
    mid = os.path.join(folder, cfg.get('mid', song + '.mid'))
    out = os.path.join(folder, cfg.get('out', song + '_sf'))
    ref_name = cfg.get('ref', 'bgm01c')
    # 必须在这里先定义：第 331 行「改过 song.json 却跳过作曲」那条检查要用它。
    # 原先只在更下面（原 341 行）赋值 → 走到那条分支就 UnboundLocalError 崩掉。
    song_json = os.path.join(folder, 'song.json')

    # [0/3] 数据把关（--check）：2 秒查完数据判据，把那 30~120 秒的渲染留给**对的数据**
    if '--check' in sys.argv:
        print('[0/3] 数据体检: check_song.py %s' % song)
        try:
            r = subprocess.run([sys.executable, os.path.join(HERE, 'check_song.py'), song],
                               cwd=ROOT, timeout=180)
        except subprocess.TimeoutExpired:
            print('  ! 数据体检超时（>180s）—— 跳过体检继续渲染；'
                  '若反复超时请单独跑 check_song.py 查是哪一项卡住')
            r = None
        if r is not None and r.returncode != 0:
            print('\n  ✗ 数据契约没过，**先别渲染**：')
            print('    · 一键修可自动修的（和弦音集/强拍）: '
                  'python scripts\\check_song.py %s --fix' % song)
            print('    · 再看上面的"数据契约"条目逐条改 song.json')
            return 1

    if composer and '--no-compose' not in sys.argv:
        if '/' in composer or '\\' in composer:
            script, extra = os.path.join(ROOT, composer), [folder]
        else:
            script, extra = os.path.join(folder, composer), []
        print('[1/3] 作曲: %s' % composer)
        try:
            r = subprocess.run([sys.executable, script] + extra, cwd=ROOT, timeout=180)
        except subprocess.TimeoutExpired:
            print('作曲超时（>180s）—— 曲目是否大到异常？（200 小节也不到 5 秒）')
            return 1
        if r.returncode != 0:
            print('作曲失败')
            return 1
    else:
        print('[1/3] 跳过作曲')

    if not os.path.exists(mid):
        print('找不到 MIDI: %s\n  第一次跑不要加 --no-compose（要先生成 MIDI）' % mid)
        return 1
    # **改过 song.json 却跳过作曲** = 这个坑实测白烧过一整轮（≈2 分钟 + 一次成绩单）：
    # 引擎读的是 song.json，但渲染的是 MIDI —— MIDI 不重生成，改动就一点都不会体现。
    if os.path.exists(song_json) and os.path.getmtime(song_json) > os.path.getmtime(mid) + 1:
        print('  !! song.json 比 MIDI 新（%.0f 秒）—— 这次渲染**不含**你的改动。'
              % (os.path.getmtime(song_json) - os.path.getmtime(mid)))
        print('     去掉 --no-compose 重跑（或先跑曲目目录里的 compose.py）再渲染。')
    for k, v in (('rms', -16.9), ('width', 2.2), ('shelf', 3.0), ('hp', 38.0),
                 ('low', 0.0), ('drive', 1.6), ('mid_db', 0.0)):
        cfg.setdefault(k, v)

    ref = scorecard.load_ref(ref_name)
    data = None
    if os.path.exists(song_json):
        try:
            data = song_engine.load(song_json)      # 供"按当前状态给建议"用
        except Exception:
            data = None
    # **口径标记**：`norm` 记录这份 render.json 是哪个响度口径下调出来的。
    # 旧配置（无标记 = 双声道 RMS 时代）重渲染会**变响**（更贴近参考，最多 ~2.1dB），
    # 让自动调参重新收敛即可；但 `--no-tune` 的 A/B 探针会被这 +2dB 误导，所以先提示。
    if cfg.get('norm') != render_midi.NORM:
        print('  ! render.json 的响度口径 = %s（当前 %s）：重渲染会变响（更贴近参考，'
              '最多 ~2.1dB）—— 别用 --no-tune 做 A/B，让它跑自动调参重新收敛。'
              % (cfg.get('norm') or '未标记（旧：双声道 RMS）', render_midi.NORM))
    if '--no-render' not in sys.argv and '--no-tune' not in sys.argv:
        print('[2/3] 渲染 + 自动调参（最多 6 轮，内部闭环）')
        cfg = autotune(cfg, ref, mid, out, data=data)
        cfg['norm'] = render_midi.NORM          # 记下"这份参数是哪个口径调出来的"
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        print('  已把调好的参数写回 render.json')
        # 最后一次实测的频段落盘：供**下次运行**做"与上次相比变差"的检测
        # （改了编配却变差时，自动调参可能反制了你的改动 —— 实测踩过）
        try:
            _last = measure(out + '.wav', ref, midi_bpm(mid, data))
            cfg['last_bands'] = dict(_last['bands'])
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=1)
        except Exception:
            pass
    elif '--no-render' not in sys.argv:
        print('[2/3] 渲染（不调参）')
        render_midi.render(mid, out, rms_db=cfg['rms'], width=cfg['width'],
                           shelf_db=cfg['shelf'], hp_hz=cfg['hp'],
                           low_db=cfg['low'], drive=cfg['drive'],
                           mid_db=cfg['mid_db'], verbose=False,
                           reverb=cfg.get('reverb'))
    else:
        print('[2/3] 跳过渲染')

    print('[3/3] 成绩单')
    wav = out + '.wav'
    # **音频与 MIDI 的同步检查**（2026-09-20 加，实测踩过）：这是上面那条
    # 「song.json 比 MIDI 新」的**姊妹坑** —— 收尾只要动过 song.json（例如跑
    # `json_io.py` 规范化、或某个改数据的小脚本），MIDI 会重生成而**音频还停在旧版**，
    # 于是"文件都在、时间戳也对"，交付出去的却是旧声音。现场：piano_rain.mid 比
    # `_sf.ogg` 新 4 分钟，是人工比对 mtime 才发现的。
    if os.path.exists(wav) and os.path.getmtime(mid) > os.path.getmtime(wav) + 1:
        print('  !! **音频比 MIDI 旧**（%.0f 秒）：%s 不含最新数据 ——'
              % (os.path.getmtime(mid) - os.path.getmtime(wav), os.path.basename(wav)))
        print('     交付前必须重渲染：把 song.json 的改动落回音频（去掉 --no-compose 重跑）。')
    if os.path.exists(wav):
        argv = ['scorecard.py', wav, '--ref', ref_name,
                '--render', os.path.relpath(mid, ROOT)]
        tb = midi_bpm(mid, data)          # 用 MIDI 的真实速度，别让音频测速把网格判错
        if tb:
            argv += ['--bpm', '%.3f' % tb]
        sys.argv = argv
        scorecard.main()
    else:
        print('  还没渲染出 %s' % wav)
    # **落盘本次实测频段**：供下次运行做"与上次相比变差"的检测（改了编配却变差时，
    # 往往是自动调参反制了你的改动 —— 实测踩过，当时日志毫无提示）。
    try:
        _wav = out + '.wav'
        if os.path.exists(_wav):
            _m = measure(_wav, scorecard.load_ref(ref_name), midi_bpm(mid, data))
            cfg['last_bands'] = dict(_m['bands'])
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=1)
            print('  已记录本次频段（供下次比较）')
    except Exception as _e:                                     # noqa: BLE001
        print('  (频段未记录: %s)' % type(_e).__name__)

    return 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    sys.exit(main())
