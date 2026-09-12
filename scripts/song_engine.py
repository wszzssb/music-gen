#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""曲目引擎：读 songs/<曲名>/song.json（**纯数据**）→ 展开编配 → 输出 MIDI + 可选合成器试听

设计目标：**新歌只写一个 JSON，不写代码**。编配风格用字符串选：
  patterns.bass_style: offbeat | eighth | sixteenth | simple
  patterns.perc_style: light | dance | none
  patterns.arpeggio  : 和弦音序号序列（默认 [0,2,3,4,3,2,4]）

song.json 结构见 songs/05_d135_cheerful/song.json；字段缺省会自动补默认值。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bgm_synth as bs          # noqa: E402

DEFAULT_PROGRAMS = {
    'Melody': (81, 0), 'Hook': (4, 1), 'Piano': (0, 2), 'Arp': (87, 3),
    'Pad': (89, 4), 'Strings': (48, 5), 'Bass': (38, 6), 'Glock': (9, 7),
    'Perc': (None, 9),
}
DEFAULT_MIX = {
    'Melody': (76, 100), 'Hook': (48, 80), 'Piano': (86, 76), 'Arp': (92, 58),
    'Pad': (64, 68), 'Strings': (52, 70), 'Bass': (64, 90), 'Glock': (100, 74),
    'Perc': (64, 80),
}

# ---------------------------------------------------------------- 风格预设
# song.json 写 "style": "<名字>" 即可套用；song.json 里显式写的 programs/mix/patterns 会覆盖预设。
# 每套的音量配比都是**实际渲染调过之后的经验值**（尤其 gorgeous 那套：低中频要收，否则糊）。
# 通道一律用下面这张**固定表**，预设只改音色不改通道 —— 否则会有两轨抢同一通道、
# program change 互相覆盖（踩过：4 套预设都撞了通道）。
CH = {'Melody': 0, 'Hook': 1, 'Piano': 2, 'Arp': 3, 'Pad': 4, 'Strings': 5,
      'Bass': 6, 'Glock': 7, 'Perc': 9}


def _progs(**over):
    """按固定通道表生成 programs；只传要改的音色号"""
    base = {'Melody': 81, 'Hook': 4, 'Piano': 0, 'Arp': 87, 'Pad': 89,
            'Strings': 48, 'Bass': 38, 'Glock': 9, 'Perc': None}
    base.update(over)
    return {k: (v, CH[k]) for k, v in base.items()}


STYLES = {
    # 原声小编制（夏日/明快）：钢弦吉他分解 + 钢琴 + 弦乐
    'acoustic': {
        'desc': '原声小编制：钢弦吉他分解 + 钢琴 + 弦乐 + 沙锤',
        'programs': _progs(Melody=0, Hook=25, Piano=0, Strings=48, Bass=32,
                           Glock=9),
        'mix': {'Melody': (76, 104), 'Hook': (42, 78), 'Piano': (88, 76),
                'Arp': (92, 50), 'Pad': (64, 66), 'Strings': (52, 70),
                'Bass': (64, 92), 'Glock': (100, 76), 'Perc': (64, 72)},
        'patterns': {'bass_style': 'simple', 'perc_style': 'light'},
    },
    # 日常/抒情：钢琴主奏 + 吉他 + 电钢琴切分，柔和
    'daily': {
        'desc': '日常抒情：钢琴主奏 + 钢弦吉他 + 电钢琴切分 + 沙锤',
        'programs': _progs(Melody=0, Hook=25, Piano=4, Pad=89, Strings=48,
                           Bass=32, Glock=9),
        'mix': {'Melody': (76, 104), 'Hook': (42, 80), 'Piano': (88, 78),
                'Arp': (92, 50), 'Pad': (64, 66), 'Strings': (52, 70),
                'Bass': (64, 88), 'Glock': (100, 84), 'Perc': (64, 78)},
        'patterns': {'bass_style': 'simple', 'perc_style': 'light'},
    },
    # 华丽/盛大：竖琴 + 弦乐 + 人声合唱垫 + 钢片琴 + 定音鼓
    'gorgeous': {
        'desc': '华丽盛大：竖琴分解 + 弦乐 + 人声合唱垫 + 钢片琴 + 定音鼓/三角铁',
        'programs': _progs(Melody=0, Hook=46, Piano=0, Arp=8, Pad=52,
                           Strings=48, Bass=43, Glock=9),
        # 教训：华丽乐器全在中低频，配比必须比"直觉"更瘦、更亮
        'mix': {'Melody': (76, 104), 'Hook': (40, 62), 'Piano': (88, 56),
                'Arp': (96, 76), 'Pad': (64, 48), 'Strings': (50, 60),
                'Bass': (64, 104), 'Glock': (102, 94), 'Perc': (64, 86)},
        'patterns': {'bass_style': 'simple', 'perc_style': 'orchestral',
                     'sub_gain': 1.8, 'sub_dur': 1.2, 'voicing_shift': 0},
    },
    # 舞曲：合成主奏 + 电钢琴 + 琶音 + 合成贝斯（四踩底鼓）
    'dance': {
        'desc': '舞曲：合成主奏 + 电钢琴 + 琶音 + 合成贝斯 + 四踩底鼓',
        'programs': _progs(Melody=81, Hook=4, Piano=4, Pad=89, Strings=48,
                           Bass=38, Glock=9),
        'mix': {'Melody': (72, 100), 'Hook': (48, 78), 'Piano': (88, 74),
                'Arp': (92, 58), 'Pad': (64, 66), 'Strings': (56, 62),
                'Bass': (64, 96), 'Glock': (104, 58), 'Perc': (64, 84)},
        'patterns': {'bass_style': 'sixteenth', 'perc_style': 'dance'},
    },
    # 民谣/叙事：尼龙吉他 + 钢琴 + 弦乐，温和
    'ballad': {
        'desc': '民谣叙事：尼龙吉他 + 钢琴 + 弦乐 + 轻沙锤',
        'programs': _progs(Melody=0, Hook=24, Piano=0, Pad=89, Strings=49,
                           Bass=32, Glock=9),
        'mix': {'Melody': (76, 100), 'Hook': (46, 76), 'Piano': (86, 74),
                'Arp': (92, 48), 'Pad': (64, 68), 'Strings': (52, 74),
                'Bass': (64, 90), 'Glock': (100, 70), 'Perc': (64, 70)},
        'patterns': {'bass_style': 'simple', 'perc_style': 'light'},
    },
}
ARR_KEYS = ('uku', 'piano', 'ep', 'strings', 'glock', 'bass', 'pad', 'arp',
            'perc', 'harmony', 'shimmer', 'mix')


def _norm_meter(v):
    """拍号 `[拍数, 音符单位]`，缺省 `[4,4]`。

    引擎内部的"拍"**一律是四分音符**（`bpm` 也是四分音符速度），所以拍号只影响三件事：
    ① 一小节有多少个四分音符（`[6,8]` → 3 个）；② 写进 MIDI 的拍号元事件；③ 强拍位置。
    **不支持的写法当场报错**，不许静默按 4/4 处理（那正是这套工具最怕的"静默给错答案"）。
    """
    if v is None:
        return [4, 4]
    if not (isinstance(v, (list, tuple)) and len(v) == 2):
        raise SystemExit('meter 应写成 [拍数, 音符单位]，如 [3,4] / [6,8]；收到 %r' % (v,))
    try:
        beats, unit = int(v[0]), int(v[1])
    except (TypeError, ValueError):
        raise SystemExit('meter 的两项必须是整数：%r' % (v,))
    if not (2 <= beats <= 12):
        raise SystemExit('meter 的拍数应在 2–12：%r' % (v,))
    if unit not in (4, 8):
        raise SystemExit('meter 的音符单位目前只支持 4 与 8（如 [3,4] / [6,8]）：%r' % (v,))
    return [beats, unit]


def bar_beats(d_or_meter):
    """一小节 = 几个**四分音符**（引擎内部时间单位）。[4,4]→4；[3,4]→3；[6,8]→3。"""
    m = d_or_meter.get('meter') if isinstance(d_or_meter, dict) else d_or_meter
    if not m:
        m = [4, 4]
    return float(m[0]) * (4.0 / float(m[1]))


def load(path):
    try:
        with open(path, encoding='utf-8') as f:
            txt = f.read()
    except FileNotFoundError:
        raise SystemExit('找不到 song.json: %s' % path)
    try:
        d = json.loads(txt)
    except json.JSONDecodeError as e:
        raise SystemExit('song.json 不是合法 JSON（第 %d 行第 %d 列）: %s'
                         % (e.lineno, e.colno, e.msg))
    if not isinstance(d, dict):
        raise SystemExit('song.json 顶层必须是对象')
    for need in ('chords', 'melody', 'sections'):
        if need not in d:
            raise SystemExit('song.json 缺字段 "%s"（需要 chords / melody / sections）' % need)
    if not d['sections']:
        raise SystemExit('song.json 的 sections 是空的')
    d.setdefault('bpm', 120.0)
    d['meter'] = _norm_meter(d.get('meter'))
    d['bar_beats'] = bar_beats(d['meter'])         # 一小节几个四分音符（下游统一用它）
    # --- 风格预设打底，song.json 里显式写的覆盖
    style = d.get('style')
    preset = {}
    if style:
        if style not in STYLES:
            raise SystemExit('未知 style "%s"，可选: %s'
                             % (style, ', '.join(STYLES)))
        preset = STYLES[style]
        d['style_desc'] = preset.get('desc', '')
    pats = dict(preset.get('patterns', {}))
    pats.update(d.get('patterns', {}))
    d['patterns'] = pats
    d['patterns'].setdefault('bass_style', 'simple')
    d['patterns'].setdefault('perc_style', 'light')
    d['patterns'].setdefault('arpeggio', [0, 2, 3, 4, 3, 2, 4])
    d['patterns'].setdefault('voicing_shift', 0)
    progs = dict(DEFAULT_PROGRAMS)
    progs.update(preset.get('programs', {}))
    progs.update({k: tuple(v) for k, v in d.get('programs', {}).items()})
    d['programs'] = progs
    mix = dict(DEFAULT_MIX)
    mix.update(preset.get('mix', {}))
    mix.update({k: tuple(v) for k, v in d.get('mix', {}).items()})
    d['mix'] = mix
    # --- 校验：拼错的编配开关要报出来，否则会静默不生效
    bad = set()
    for sec in d.get('sections', []):
        bad |= set(sec.get('arr', {})) - set(ARR_KEYS) - {'vel', 'glock_all'}
    if bad:
        print('  !! song.json 里有无效的编配开关: %s（可用: %s）'
              % (', '.join(sorted(bad)), ', '.join(ARR_KEYS)))
    # --- 校验：**输入数据里的音高越界必须当场报错**。
    # 以前只在 build_events 末尾 `min(127, max(0, m))` 静默夹断：写错一个音（比如 200）
    # 声音变了、却没有任何工具抱怨（"静默出错"这一类）。派生声部（±12 八度加倍）
    # 越界不属于数据错误，那些在下游按"丢弃该声部"处理。
    def _rng(where, m):
        if not isinstance(m, (int, float)) or not (0 <= m <= 127):
            raise SystemExit('song.json %s 的音高 %r 越界（MIDI 合法范围 0-127）'
                             % (where, m))
    for cname, ch in d['chords'].items():
        if not (isinstance(ch, (list, tuple)) and len(ch) == 2):
            raise SystemExit('和弦 "%s" 应为 [根音, [音集…]]' % cname)
        _rng('和弦 %s 的根音' % cname, ch[0])
        for m in ch[1]:
            _rng('和弦 %s' % cname, m)
    for mname, mel in d['melody'].items():
        for it in mel:
            if not (isinstance(it, (list, tuple)) and len(it) == 4):
                raise SystemExit('旋律 "%s" 的音符应为 [小节, 拍, 时值, 音高]：%r'
                                 % (mname, it))
            _rng('旋律 %s 的音高' % mname, it[3])
    for sec in d['sections']:
        for it in (sec.get('melody_extra') or []):
            _rng('段落 %s 的 melody_extra' % sec.get('name', '?'), it[3])
    return d


def tone(tones, i):
    return tones[min(max(0, i), len(tones) - 1)]


# ---------------------------------------------------------------- 编配生成
def guitar_arpeggio(ch, i, arp, B=4.0):
    """吉他/尤克里里分解：第 1 拍必须是根音（否则和声含糊、扒谱都对不上）

    `B` = 一小节的四分音符数（默认 4 = 老行为，逐字节不变）。3/4 → 一小节 5 个八分位。
    """
    bass, tones = ch
    beats = [k * 0.5 for k in range(max(1, int(round(B * 2)) - 1))]
    out = []
    for k, b in enumerate(beats):
        m = tone(tones, arp[k % len(arp)])
        out.append((b, 0.45, m, 80 if k % 2 else 68))
    return out


def piano_part(ch, i, B=4.0):
    """钢琴：反拍和弦短音（含根音） + 高音持续音"""
    _, tones = ch
    out = []
    for b in (0.5, B - 1.5):
        for m in tones[:3]:
            out.append((b, 0.28, m, 60))
    out.append((0.0, 1.5, tone(tones, 3), 54))
    if i % 4 == 3:
        out.append((B - 0.5, 0.4, tone(tones, 2) + 12, 62))
    return out


def bass_part(ch, nxt, i, pat, B=4.0):
    """贝斯：四种风格（都由参考曲低频节奏型反推出来的）
    sub_gain / sub_dur 可调：sub 层必须用**短音**（默认 0.3 拍），
    长音会把低频节奏型糊成连续块；但 sub 太少会缺 20-40Hz 的能量。

    `B` = 一小节的四分音符数（默认 4 = 老行为）。各风格都按"拍"展开，因此 3/4 会自动缩短
    （`simple` 在第 2..B-1 拍各给一个短音，`offbeat`/`pump16` 的收束音按 `B-…` 定位）。
    """
    style = pat['bass_style']
    sub_gain = pat.get('sub_gain', 1.0)
    sub_dur = pat.get('sub_dur', 0.3)
    bass, tones = ch
    NB = max(1, int(round(B)))
    if style == 'offbeat':
        # 反拍驱动：重音在每拍第 2、4 个十六分（0.25/0.75 与 B-1.75/B-1.25/B-0.75）
        out = [(0.25, 0.35, bass, 96), (0.75, 0.3, bass, 80),
               (B - 1.75, 0.35, bass, 92), (B - 1.25, 0.3, bass, 76),
               (B - 0.75, 0.3, bass, 84)]
        out.append((0.25, sub_dur, bass - 12, int(72 * sub_gain)))
        out.append((B - 1.75, sub_dur, bass - 12, int(68 * sub_gain)))
    elif style == 'eighth':
        pc = [bass, bass, bass, bass + 12, bass, bass, bass, bass + 7]
        vc = [104, 84, 96, 80, 104, 84, 96, 82]
        out = [(k * 0.5, 0.22, pc[k % 8], vc[k % 8])
               for k in range(max(1, int(round(B * 2))))]
        if sub_gain:                       # sub 层：参考曲 20-40Hz 常有能量
            out.append((0.0, max(sub_dur, 0.8), bass - 12, int(70 * sub_gain)))
            out.append((B / 2.0, max(sub_dur, 0.8), bass - 12, int(64 * sub_gain)))
    elif style == 'sixteenth':
        f5, up = bass + 7, bass + 12
        # 每拍三条（正拍 + e + a）；四拍模板逐拍等价于原来的 12 个手写元组
        tpl = [((0.0, 0.18, bass, 106), (0.25, 0.18, bass, 104), (0.5, 0.18, bass, 80)),
               ((0.0, 0.18, bass, 100), (0.25, 0.18, bass, 100), (0.5, 0.18, up, 78)),
               ((0.0, 0.18, bass, 106), (0.25, 0.18, bass, 104), (0.5, 0.18, bass, 80)),
               ((0.0, 0.18, bass, 100), (0.25, 0.18, f5, 100), (0.5, 0.18, up, 82))]
        out = [(beat + p, dd, m, v) for beat in range(NB)
               for (p, dd, m, v) in tpl[beat % 4]]
    elif style == 'pump16':
        # 反拍推动（照 BGM33 的低频节奏型反推）：**每拍的"e/a"两个十六分都推**，
        # 正拍留给鼓 → 低频显著起音全落在反拍：◇★◇★◇★◇★·★◇★◇★◇★
        # ⚠ 逐声部实测：例曲 bass 占用率 ~60%、**动态 48~52dB（有颗粒、有起伏）**。
        #   所以音长要**短**（0.28 拍 = 断开），力度要拉开（重音 108 / 弱音 66），
        #   绝不能是一整小节的长音（那会变成 98% 占用 / 13dB 动态的"嗡"）。
        f5 = bass + 7
        # `bass_vel`（opt-in）：直接给 e/a 位置的力度。默认那套是**故意有起伏**的
        # （逐声部实测：例曲 bass 动态 48~52dB = 有颗粒）；但**整曲低频的 16 分律动型**
        # 要求每个 e/a 都是强格（`◇★◇★◇★◇★`）—— 两个指标会打架，
        # 想要"律动型逐格对齐"就把它拉平（差 ≤20），想要"颗粒感"就用默认。
        d8 = pat.get('bass_vel') or (106, 110, 62, 88, 102, 108, 58, 92)
        out = []
        for beat in range(NB):
            out.append((beat + 0.25, 0.22, bass, d8[(2 * beat) % 8]))
            out.append((beat + 0.75, 0.24,
                        f5 if (2 * beat + 1) % 8 == 5 else bass, d8[(2 * beat + 1) % 8]))
        out.append((0.0, 0.18, bass, 60))             # 正拍只给短促弱音（有颗粒、不断层）
        if sub_gain:
            out.append((0.75, sub_dur, bass - 12, int(72 * sub_gain)))
            out.append((B - 1.25, sub_dur, bass - 12, int(68 * sub_gain)))
    else:                                       # simple
        out = [(0.0, 1.4, bass, 96)]
        for k in range(2, NB):                  # 4/4 → 第 2、3 拍（与老行为一致）
            out.append((float(k), 0.9, bass, 74 if k == NB - 1 else 80))
        if i % 2 == 1:
            out.append((B - 1.5, 0.45, bass + 7 if bass + 7 <= 47 else bass - 5, 72))
        # sub 层（20-40Hz）：参考曲这一段常有能量，主贝斯落在 40-80 时补不上
        if sub_gain:
            out.append((0.0, max(sub_dur, 1.2), bass - 12, int(70 * sub_gain)))
    if i % 4 == 3 and nxt:                       # 句尾半音引导
        nb = nxt[0]
        out.append((B - 0.25, 0.3, nb + (1 if nb > bass else -1), 78))
    return out


def ep_part(ch, i, B=4.0):
    """电钢琴：反拍切分和弦（走 Hook 轨）"""
    _, tones = ch
    acc = B / 2.0 + 0.5                        # 4/4 → 2.5（原来的重音位）
    out = []
    for b in [k + 0.5 for k in range(max(1, int(round(B))))]:
        for m in tones[1:4]:
            out.append((b, 0.22, m, 62 if b == acc else 54))
    if i % 4 == 3:
        out.append((B - 0.75, 0.2, tone(tones, 4), 66))
    return out


def pad_part(ch, B=4.0):
    _, tones = ch
    return [(0.0, B + 0.1, m, 52) for m in tones[:2]]


def strings_part(ch, B=4.0):
    _, tones = ch
    return [(0.0, B + 0.1, m + 12, 50) for m in tones[:3]]


def glock_part(ch, i, B=4.0):
    _, tones = ch
    if i % 4 == 2:
        return [(B - 2.5, 0.4, tone(tones, 3) + 24, 58),
                (B - 1.0, 0.4, tone(tones, 2) + 24, 54)]
    if i % 4 == 3:
        return [(0.0, 0.4, tone(tones, 4) + 24, 60)]
    return []


def perc_part(style, level, i, nbars, layers=None, kick_vel=None, B=4.0):
    """打击：light = 沙锤+轻底鼓（抒情向）；dance = 四踩+反拍踩镲（舞曲向）

    `B` = 一小节的四分音符数（默认 4 = 老行为，逐字节不变）；十六分格数 = B*4。
    `layers`（opt-in，默认 None = 输出与以前逐字节一致）：给底鼓位置/十六分网格
    额外叠"垫层"，用来补**时间连续性**（占用率），而不是补能量——
    逐声部实测发现我们与例曲差的不是频段能量（EQ 早已对齐），而是
    "低频/高频有没有一直响着"：例曲 5–10kHz 占用 96~100%，我们只有 78~79%。
    格式 {'kick': [[note, vel, 拍长], ...], 'air': [[note, vel, 拍长], ...]}"""
    if style == 'none' or level == 0:
        return []
    NB = max(1, int(round(B)))
    S = NB * 4                                     # 一小节的十六分格数（4/4 → 16）
    out = []
    if style == 'dance':
        for b in range(NB):
            out.append((b, 0.1, 36, 100 if b % 2 == 0 else 94))
            if level >= 2:
                out.append((b + 0.25, 0.1, 36, 84))       # 双踩
        for b in range(1, NB, 2):                         # 军鼓 2、4（4/4 → 1、3）
            out.append((b, 0.1, 38, 96))
        for b in range(NB):
            out.append((b + 0.5, 0.1, 42, 98))            # 只放反拍
            if level >= 3:
                out.append((b + 0.25, 0.1, 42, 20))
        if level >= 3:
            out.append((B - 0.5, 0.1, 46, 72))
    elif style == 'pump':
        # 照 BGM33 的高频节奏型反推 + **逐声部实测目标**：
        #   例曲 drums 占用率 69/57/33/21/15/12/55/84、动态 29~49dB
        #   （我们要的是"密集 + 均匀 + 被压过"，所以：**每个十六分都有东西**、
        #     力度收在 62~104 的窄带里、正拍与反拍差距压小）
        for k in range(S):                                # 十六分踩镲：全程铺满
            out.append((k * 0.25, 0.25, 42, 84 if k % 4 == 0 else (74 if k % 2 == 0 else 68)))
        for k in range(NB * 2):                           # 八分 ride：长延音铺 2.5–10kHz
            out.append((k * 0.5, 0.7, 51, 72 if k % 2 == 0 else 64))
        for k in range(NB * 2):                           # 常驻十六分沙锤（补最上端）
            out.append((k * 0.5 + 0.25, 0.2, 82, 60))
        for b in range(NB):                               # 反拍铃鼓
            out.append((b + 0.5, 0.25, 54, 66))
        if level >= 2:
            for b in range(NB):                           # 十六分幽灵小鼓（密度感）
                out.append((b + 0.25, 0.1, 40, 52))
                out.append((b + 0.75, 0.1, 40, 46))
        for b in range(1, NB, 2):
            out.append((float(b), 0.14, 38, 100))         # 军鼓 2、4
        # `kick_vel`（opt-in，默认 [94, 98] = 老行为）分开给"正拍"和"a 位"的力度：
        # 例曲的低频律动型是 `◇★◇★◇★◇★` —— **正拍是弱格**（低频重心在反拍推动上）。
        # 我们原来是 `★◇·★★··★`（正拍最强），因为底鼓+垫层都压在正拍上。
        # `layers.kick_pos == "offbeat"`（opt-in）：把**整套低频骨架**（底鼓 + 垫层）
        # 移到每拍的 e/a 两个十六分 —— 这才是例曲那种"反拍推动"的低频。
        kv = kick_vel or (94, 98)
        off_pos = None
        if layers and layers.get('kick_pos') == 'offbeat':
            off_pos = [k * 0.25 for k in range(1, S, 2)]
            kicks = [(p, 0.45, kv[1]) for p in off_pos]
            if kv[0]:        # 正拍留一个**很弱**的底鼓 = 例曲里的 ◇ 格（弱，但不能没有）
                kicks += [(float(b), 0.3, kv[0]) for b in range(NB)]
        else:
            kicks = [(float(b), 0.55, kv[0]) for b in range(NB)] + \
                    [(b + 0.75, 0.5, kv[1]) for b in range(NB)]
        for (kb, kd, kkv) in kicks:
            out.append((kb, kd, 36, kkv))
        # 垫层（opt-in）：位置**由上面的 kicks 派生**，保证永远与底鼓对齐
        # （GM 的底鼓采样只有 0.13~0.18 秒，尾巴垫不满 0.8 秒的一拍）
        # offbeat 模式下垫层只跟反拍格（正拍那个弱底鼓不该被垫厚）
        if layers:
            pos = off_pos if off_pos else [kb for (kb, _kd, _kv) in kicks]
            for (mn, mv, md) in layers.get('kick', []):
                for kb in pos:
                    out.append((kb, md, mn, mv))
            # air 层铺**十六分**网格：连续性取决于"采样长度 vs 网格间隔"，不是 MIDI 时值。
            # 实测（`stem_compare.py`，主歌段 5000Hz 占用率，例曲 99%）：
            #   每十六分 + 0.4 拍 → 100%（但见下）／每八分 + 0.7 拍 → 84% ✗
            #
            # ⚠ 但 0.4 拍 > 0.25 格距 = **同音高重叠** → FluidSynth 把 note-off 配错 voice，
            #   留下永不关闭的悬空 voice（镲采样带 loop）→ 整首歌**多渲染 21 秒真声音**。
            #   而时值缩到 0.24 拍（不重叠）声音又接不上（开镲 release 很短，实测占用掉回 78%）。
            # 解法：**多个条目交替占用子网格**——n 个条目 → 每个音高的间隔变成 n×0.25 拍，
            #   时值只要 < n×0.25 就不重叠，而 "时值 > 0.25" 的连续性照样拿得到。
            airs = layers.get('air', [])
            for si, (mn, mv, md) in enumerate(airs):
                for k in range(si, S, max(1, len(airs))):
                    out.append((k * 0.25, md, mn, mv))
        if level >= 3:
            out.append((B - 0.5, 0.3, 46, 74))            # 开镲收句
        if i % 4 == 3:                                    # 每 4 小节的十六分过门
            fill = [(1.75, 38, 74), (2.0, 48, 84), (2.25, 48, 74),
                    (2.5, 47, 88), (2.75, 47, 78), (3.0, 50, 92),
                    (3.25, 50, 82), (3.5, 45, 96), (3.75, 45, 86)]
            sh = 4.0 - B                                  # 4/4 → 0（逐字节不变）
            for (b, m, v) in fill:
                if b - sh >= 0:
                    out.append((b - sh, 0.1, m, v))
        if i % 8 == 7:                                    # 8 小节加一次大过门
            out.append((3.875 - (4.0 - B), 0.1, 49, 88))  # 吊镲（不冲太高，保持均匀）
    elif style == 'orchestral':
        # 定音鼓 + 三角铁微光 + 吊镲：华丽/盛大向，不用鼓组
        out.append((0.0, 0.35, 47, 96))                   # 低定音鼓（正拍）
        if level >= 2:
            out.append((B / 2.0, 0.35, 47, 82))           # 第 3 拍（4/4 → 2.0）
            out.append((B - 0.5, 0.35, 48, 72))           # 高定音鼓推进
        for b in range(NB):                               # 三角铁反拍微光（补 5-18kHz）
            out.append((b + 0.5, 0.25, 81, 54))
            if level >= 2:
                out.append((b + 0.25, 0.2, 81, 34))
        if level >= 3:
            out.append((B - 0.25, 0.3, 81, 62))
    else:                                                 # light
        for k in range(NB * 2):
            out.append((k * 0.5, 0.2, 82, 46 if k % 2 else 38))
        if level >= 2:
            out.append((0.0, 0.1, 36, 68))
            out.append((B / 2.0, 0.1, 36, 60))
            for b in range(1, NB, 2):                     # 侧棒（4/4 → 1、3）
                out.append((float(b), 0.1, 37, 52))
    if i == 0:
        out.append((0.0, 0.1, 49, 88))                    # 段首吊镲
    if i == nbars - 1:
        if style == 'dance':
            out.append((B - 0.5, 0.1, 48, 88))
            out.append((B - 0.25, 0.1, 45, 92))
        elif level >= 2:
            out.append((B - 0.5, 0.1, 39, 58))            # 轻过门
    return out


def harmony_below(tones, m):
    """副旋律取音：从和弦音里挑一个比旋律音低 3~6 半音的音（和弦内低三度）。
    抽成独立函数是为了能被注入用例直接打到（变异测试要能把它换掉）。"""
    below = [t for t in tones if 3 <= m - t <= 6]
    return max(below) if below else None


def build_events(d):
    """展开成 {轨名: [(起始拍, 时值拍, 音高, 力度)]}"""
    ch_all = {k: (v[0], v[1]) for k, v in d['chords'].items()}
    mel_all = d['melody']
    pat = d['patterns']
    shift = pat.get('voicing_shift', 0)      # 和弦声部整体上/下移（华丽太厚时 +12 更清亮）
    B = float(d.get('bar_beats') or 4.0)     # 一小节几个四分音符（拍号；默认 4 = 老行为）

    def voicing(ch):
        return (ch[0], [m + shift for m in ch[1]]) if shift else ch

    ev = {k: [] for k in d['programs']}
    # 断奏因子（opt-in，默认 1.0）：把伴奏音变短 = **在鼓点之间腾出空间**。
    # 参考曲的 20ms 短窗电平起伏 σ≈22dB（鼓点之间掉得下去），我们原来只有 ~10dB（一直在糊）
    sc = float(pat.get('staccato', 1.0))
    bar0 = 0
    for sec in d['sections']:
        nbars = sec['bars']
        arr = sec.get('arr', {})
        vs = arr.get('vel', 1.0)
        mel = list(mel_all.get(sec.get('melody', ''), []))
        mel += sec.get('melody_extra', [])
        bucket = {k: [] for k in d['programs']}
        for i in range(nbars):
            cn = sec['chords'][i]
            if cn not in ch_all:
                raise SystemExit('段落 %s 第 %d 小节引用了未定义的和弦 "%s"'
                                 % (sec.get('name', '?'), i + 1, cn))
            ch = voicing(ch_all[cn])
            nxt = None
            if i + 1 < nbars:
                nn = sec['chords'][i + 1]
                if nn not in ch_all:
                    raise SystemExit('段落 %s 第 %d 小节引用了未定义的和弦 "%s"'
                                     % (sec.get('name', '?'), i + 2, nn))
                nxt = voicing(ch_all[nn])
            t0 = (bar0 + i) * B
            if arr.get('bass'):
                for (b, dd, m, v) in bass_part(ch, nxt, i, pat, B):
                    bucket['Bass'].append((t0 + b, dd, m, v))
            if arr.get('uku'):
                for (b, dd, m, v) in guitar_arpeggio(ch, i, pat['arpeggio'], B):
                    bucket['Hook'].append((t0 + b, dd * sc, m, v))
            if arr.get('ep'):                      # 电钢琴反拍切分（Hook 轨）
                for (b, dd, m, v) in ep_part(ch, i, B):
                    bucket['Hook'].append((t0 + b, dd * sc, m, v))
            if arr.get('piano'):
                # 钢琴轨缺失时依次退到 Hook / Arp，避免落到音色不对的轨道
                tr = next((k for k in ('Piano', 'Hook', 'Arp') if k in bucket), None)
                if tr:
                    for (b, dd, m, v) in piano_part(ch, i, B):
                        bucket[tr].append((t0 + b, dd * sc, m, v))
            if arr.get('pad'):
                for (b, dd, m, v) in pad_part(ch, B):
                    bucket['Pad'].append((t0 + b, dd, m, v))
            if arr.get('strings'):
                for (b, dd, m, v) in strings_part(ch, B):
                    bucket['Strings'].append((t0 + b, dd, m, v))
            if arr.get('glock'):
                for (b, dd, m, v) in glock_part(ch, i, B):
                    bucket['Glock'].append((t0 + b, dd, m, v))
            if arr.get('arp'):
                for k in range(max(1, int(round(B * 2)))):
                    seq = [tone(ch[1], 0), tone(ch[1], 2), tone(ch[1], 4), tone(ch[1], 2)]
                    bucket['Arp'].append((t0 + k * 0.5, 0.28 * sc, seq[k % 4] + 12,
                                          42 + (8 if k % 2 == 0 else 0)))
            # 持续微光层（opt-in）：整小节长音的高八度和弦音，走 Arp 轨（音色可覆盖成
            # 颤音琴/竖琴这类**有延音的亮音色**）。用途：例曲 2.5–10kHz 的占用率是 87~90%
            # （连续），而我们只有短促打击点 → 高频出现空洞，听感"薄、空、不像成品"。
            if arr.get('shimmer'):
                tones = voicing(ch_all[cn])[1]
                # 两个八度同时铺（+24 进 630–1250、+36 进 1.2–4kHz），音色要选**有延音**的
                # （颤音琴/音乐盒），否则高频只剩打击点 → "点+空"，例曲是连续的墙
                for m in [t + 24 for t in tones if t + 24 <= 104][:3]:
                    bucket['Arp'].append((t0, B - 0.1, m, 58))
                for m in [t + 36 for t in tones if t + 36 <= 108][:3]:
                    bucket['Arp'].append((t0, B - 0.1, m, 72))
            if arr.get('perc'):
                for (b, dd, m, v) in perc_part(pat['perc_style'], arr['perc'], i, nbars,
                                               pat.get('perc_layers'),
                                               pat.get('kick_vel'), B):
                    bucket['Perc'].append((t0 + b, dd, m, v))
        for (b, beat, dur, m) in mel:
            t = (bar0 + b) * B + beat
            # 低八度加厚：越界就**不加这一层**（以前是夹到 0/127 —— 会变成另一个音）
            # `patterns.mel_vel`（opt-in，默认 1.0）：旋律力度缩放。
            # 为什么需要：旋律力度原先是**硬编码**的，而 `mix.Melody` 的 CC7 会被
            # 渲染端的响度归一化吃掉（实测 60→127 只差 0.23dB）→ 想让旋律"浮在伴奏上"
            # 没有任何可用旋钮。实测 17 号（听感融合好）旋律比伴奏 +1.1dB，而 20/21 是 −0.3dB。
            mv = float(pat.get('mel_vel', 1.0))
            bucket['Melody'].append((t, dur * 0.96, m, max(1, min(127, int(round(96 * mv))))))
            if 0 <= m - 12 <= 127:
                bucket['Melody'].append((t, dur * 0.9, m - 12,
                                         max(1, min(127, int(round(62 * mv))))))
            # 高八度钟琴：同理，越界丢弃而不是夹断
            if (arr.get('glock') and (arr.get('glock_all') or b % 2 == 0)
                    and m + 12 <= 127):
                bucket['Glock'].append((t, dur * 0.9, m + 12, 54))
        # 副旋律/加厚层（opt-in）：给旋律音配一个**和弦内的低三度**（保证协和），
        # 走 Strings 轨（没有就退到 Hook/Piano）。这是"听起来做得很满"最省的一招。
        if arr.get('harmony'):
            ht = next((k for k in ('Strings', 'Hook', 'Piano') if k in bucket), None)
            if ht:
                for (b, beat, dur, m) in mel:
                    if b >= len(sec['chords']):
                        continue
                    cn2 = sec['chords'][b]
                    if cn2 not in ch_all:
                        continue
                    tones = voicing(ch_all[cn2])[1]
                    hm = harmony_below(tones, m)
                    if hm is not None:
                        t = (bar0 + b) * B + beat
                        bucket[ht].append((t, dur * 0.9, hm, 50))
        for k in bucket:
            for (t, dd, m, v) in bucket[k]:
                # 走到这里的音高都已在合法范围内（数据越界在 load() 就报错了，
                # 派生声部越界在上游被丢弃）；这里只处理时间/时值/力度
                assert 0 <= m <= 127, '%s 出现了越界音高 %s（派生声部漏了过滤）' % (k, m)
                ev[k].append((max(0.0, t), max(0.05, dd), int(m),
                              max(1, min(127, int(round(v * vs))))))
        bar0 += nbars
    for k in ev:
        ev[k].sort()
    return ev, bar0


def write_midi(d, ev, path):
    tracks = []
    skipped = []
    # 段落级混音自动化（opt-in）：`sections[i].arr.mix = {"Strings": 74, ...}`
    # → 在该段起点写 CC7。这是"起伏"最直接的手段：不用改音符，光靠推子就能做出层次。
    auto = {}
    bar0 = 0
    B = float(d.get('bar_beats') or 4.0)
    for sec in d.get('sections', []):
        amix = (sec.get('arr') or {}).get('mix') or {}
        for name, vol in amix.items():
            auto.setdefault(name, []).append(((bar0) * B, 7, max(0, min(127, int(vol)))))
        bar0 += sec['bars']
    for name, (prog, chan) in d['programs'].items():
        if not ev.get(name):
            skipped.append(name)           # 空轨不写进 MIDI（否则 DAW 里多一堆空轨）
            continue
        pan, vol = d['mix'][name]
        ccs = [(0.0, 10, pan), (0.0, 7, vol)] + sorted(auto.get(name, []))
        tracks.append((name, prog, chan, ev[name], ccs))
    if skipped:
        print('  (跳过空轨: %s)' % ', '.join(skipped))
    bs.BPM = d['bpm']
    bs.write_midi(path, tracks, ppq=480, meter=tuple(d.get('meter') or (4, 4)))


def compose(song_json, out_mid=None, quiet=True):
    d = load(song_json)
    # 防呆：perc_style=none 且所有段落 perc=0 → 5-18kHz 会塌（沙锤是这类编配的唯一高频来源）
    if d['patterns']['perc_style'] == 'none' and \
            not any(s.get('arr', {}).get('perc') for s in d['sections']):
        print('  !! 警告：perc_style=none 且无段落启用打击乐 → 5-18kHz 会明显偏暗。'
              '想要"无鼓组"请用 perc_style=light + perc=1（只留沙锤）')
    ev, nbars = build_events(d)
    out_mid = out_mid or os.path.join(os.path.dirname(os.path.abspath(song_json)),
                                      d.get('name', 'song') + '.mid')
    write_midi(d, ev, out_mid)
    if not quiet:
        bar = d['bar_beats'] * 60.0 / d['bpm']
        counts = ', '.join('%s:%d' % (k, len(v)) for k, v in ev.items())
        print('  %d 小节 ≈ %.0f 秒 @%.1fBPM%s | %s'
              % (nbars, nbars * bar, d['bpm'],
                 '' if d['meter'] == [4, 4] else ' %d/%d' % tuple(d['meter']), counts))
    print('  MIDI: %s' % out_mid)
    return out_mid


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    compose(sys.argv[1], quiet='--brief' not in sys.argv)
    return 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    sys.exit(main())
