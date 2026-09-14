#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""theme_pack.py —— **把「同一主题的多首 MIDI 模板」聚合成一份主题模板包画像**。

为什么需要它（用户口径）：**一次生成要依据很多个不同模板，而不是一首参考曲**；
模板来源只能是 `refs/midi2/`（网络多风格 MIDI 模板库）或**网络上带来源 URL 的权威数据**
—— 不许拿"自己生成的曲子"或随手一份音频当模板。单个模板有偶然性（某一首的怪和弦、
怪速度、扒谱误差），聚合 N 首同主题模板才能得到"这个主题通常怎么写"。

主题词 → 风格集合 → 模板：主题是**语义**（日常 / 夜晚 / 海边 / 战斗……），
库里是按**风格目录**存的，所以 `THEMES` 给出"主题 → midi2 风格集合 + 引擎风格预设"的映射；
选模板时在同主题的风格集合内做**风格轮转 + 速度分层**，保证多样（不是挑最像的 8 首）。

聚合出的东西（全部来自音符层，**精确**，不受音频扒谱误差影响）：
  · 速度/拍号 · 调式（Krumhansl 相关）· 和声：常用 4 小节进行（罗马级数）+ 和弦池
  · 节奏：低频/高频 16 分格共识型 + 密度 · 配器：GM 音色族 → 轨角色 + 音域
  · 曲式：段落长度与总量 · **旋律语言**（落点/时值/音程/句长，喂 `melody_gen.py`）

产物（两个文件都在 `refs/themes/`）：
  · `refs/themes/<主题>.json`          —— 主题模板包（含**模板清单 + 来源 URL**，可溯源）
  · `refs/themes/<主题>_melody.json`   —— 旋律语言子画像（`melody_gen.py` 直接吃）

用法:
  python scripts\theme_pack.py --list-themes                 # 有哪些主题
  python scripts\theme_pack.py daily                          # 建一个主题包
  python scripts\theme_pack.py daily --show                   # 建完再打印摘要
  python scripts\theme_pack.py --all                          # 建全部主题包
  python scripts\theme_pack.py daily --min 10                 # 提高模板数下限（默认 8）
  python scripts\theme_pack.py waltz --allow-fetch            # 同主题不足时联网抓（默认不联网）
  python scripts\theme_pack.py daily --calibrate              # 标定段间曲线（多 seed 测量，默认只报不写）
  python scripts\theme_pack.py daily --calibrate --seeds 5,11,17 --set-gain   # 要写逐主题系数就显式说
  python scripts\theme_pack.py --selftest                     # 聚合规则自测（不读模板库）
"""
import collections
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()          # noqa: E402  控制台编码兜底

import json_io                               # noqa: E402
import midi_probe as mp                      # noqa: E402  唯一的 SMF 解析口径
import midi_ref as mr                        # noqa: E402  唯一的和弦识别口径
import selftest as st                        # noqa: E402  唯一的和弦符号口径（parse_chord）
import song_engine                           # noqa: E402  引擎风格预设（engine_style 校验）

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIB = os.path.join(ROOT, 'refs', 'midi2')     # 模板库（2 号库：网络多风格 MIDI）
OUT = os.path.join(ROOT, 'refs', 'themes')    # 主题模板包
NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

MIN_TEMPLATES = 8            # 用户口径：同主题**至少 8 首**不同模板
DEF_TEMPLATES = 10           # 默认取 10 首（8 是下限，10 让统计稳一点）

# 模板来源白名单：**只允许** refs/midi2 本地库 + 网络权威站点抓来的（带 URL 可溯源）。
# 之所以按 **主机名** 而不是"有 URL 就算"：URL 谁都能写，"权威"必须落到具体站点。
AUTHORITATIVE_HOSTS = ('bitmidi.com', 'vgmusic.com', 'mutopiaproject.org',
                       'imslp.org', 'musescore.com')

# ---------------------------------------------------------------- 主题词表
# styles = midi2 的风格目录；engine = song_engine.STYLES 里的编配预设（生成时打底）。
# 一个主题给 3 个风格：既能凑够 ≥8 首模板，又不至于风格漂移（"同主题"要真的同主题）。
THEMES = {
    'daily':    {'label': '日常', 'styles': ['pop', 'folk', 'anime'], 'engine': 'daily'},
    'cheerful': {'label': '欢快', 'styles': ['pop', 'latin', 'rock'], 'engine': 'daily'},
    'tender':   {'label': '温柔抒情', 'styles': ['ballad', 'romantic', 'pop'], 'engine': 'ballad'},
    'night':    {'label': '夜晚', 'styles': ['newage', 'jazz', 'electronic'], 'engine': 'daily'},
    'seaside':  {'label': '海边', 'styles': ['newage', 'folk', 'pop'], 'engine': 'acoustic'},
    'sorrow':   {'label': '悲伤', 'styles': ['ballad', 'romantic', 'classical'], 'engine': 'ballad'},
    'battle':   {'label': '战斗', 'styles': ['rock', 'game16', 'game32'], 'engine': 'dance'},
    'mystery':  {'label': '神秘', 'styles': ['film', 'newage', 'classical'], 'engine': 'gorgeous'},
    'gorgeous': {'label': '华丽', 'styles': ['film', 'romantic', 'baroque'], 'engine': 'gorgeous'},
    'neon':     {'label': '霓虹电子', 'styles': ['electronic', 'chiptune', 'game32'], 'engine': 'dance'},
    'retro':    {'label': '复古游戏', 'styles': ['chiptune', 'game16', 'game32'], 'engine': 'dance'},
    'lounge':   {'label': '酒馆爵士', 'styles': ['jazz', 'blues', 'pop'], 'engine': 'acoustic'},
    'folk_tale': {'label': '民谣叙事', 'styles': ['folk', 'blues', 'ballad'], 'engine': 'ballad'},
    'classic':  {'label': '古典庄重', 'styles': ['classical', 'baroque', 'public_domain'],
                 'engine': 'gorgeous'},
    # 3/4 的模板全库只有 26 首（古典/巴洛克/民谣/公有领域为主）→ 风格集合必须放宽，
    # 否则凑不满 8 首（实测 classical/baroque/romantic 只有 7 首）
    'waltz':    {'label': '三拍圆舞', 'styles': ['classical', 'baroque', 'romantic', 'folk',
                                                 'public_domain'],
                 'engine': 'gorgeous', 'meter': [3, 4]},
}

# 和弦后缀归一：库里符号五花八门，先收敛到 `selftest.parse_chord` 认得的写法
SUF_ALIAS = {'': '', 'maj': '', 'M': '', 'min': 'm', 'm': 'm', 'madd9': 'm',
             '7': '7', 'dom7': '7', 'maj7': 'maj7', 'M7': 'maj7', 'm7': 'm7',
             'min7': 'm7', 'm6': 'm6', 'm7b5': 'm7b5', 'dim': 'dim', 'dim7': 'dim',
             'sus4': 'sus4', 'sus': 'sus4', 'sus2': 'sus4', '6': '6', 'add9': '',
             '9': '7', 'm9': 'm7', '11': '7', '13': '7', 'aug': '', '+': ''}
DEG_ROMAN = ['I', 'bII', 'II', 'bIII', 'III', 'IV', 'bV', 'V', 'bVI', 'VI',
             'bVII', 'VII']
# Krumhansl-Kessler 调性剖面（音级直方图相关法；只用它定"主题级调式"，逐首仍各算各的）
KK_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
KK_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]
MINOR_SCALE = [0, 2, 3, 5, 7, 8, 10]
# GM program → 轨角色（0-based program；鼓在 channel 10）。聚合"这个主题用哪些乐器"。
ROLE_BY_PROGRAM = (
    (0, 7, 'piano'), (8, 15, 'glock'), (16, 23, 'organ'), (24, 31, 'guitar'),
    (32, 39, 'bass'), (40, 47, 'strings'), (48, 55, 'strings'),
    (56, 63, 'brass'), (64, 71, 'reed'), (72, 79, 'pipe'),
    (80, 87, 'lead'), (88, 95, 'pad'), (96, 103, 'fx'), (104, 111, 'guitar'),
    (112, 119, 'perc'), (120, 127, 'fx'))
ROLE_TO_ARR = {'bass': 'bass', 'piano': 'piano', 'guitar': 'uku', 'strings': 'strings',
               'pad': 'pad', 'glock': 'glock', 'lead': 'ep', 'brass': 'strings',
               'reed': 'ep', 'pipe': 'ep', 'perc': 'perc'}
# 风格 → 引擎打击/贝斯音型（模板的音符密度只用来兜底，风格名是更稳的先验）
PERC_BY_STYLE = {'rock': 'dance', 'game16': 'dance', 'game32': 'dance',
                 'electronic': 'dance', 'chiptune': 'dance', 'latin': 'dance',
                 'blues': 'light', 'jazz': 'light', 'pop': 'light', 'folk': 'light',
                 'anime': 'light', 'newage': 'light', 'ballad': 'light',
                 'film': 'orchestral', 'classical': 'orchestral',
                 'romantic': 'orchestral', 'baroque': 'orchestral',
                 'public_domain': 'orchestral'}


# ---------------------------------------------------------------- 库与来源
def lib_index(path=None):
    """模板库索引（唯一台账：`refs/midi2/_index.json`）"""
    p = path or os.path.join(LIB, '_index.json')
    with open(p, encoding='utf-8-sig') as f:
        return json.load(f)


def lib_sources(path=None):
    """模板来源表（`<相对路径>: <URL>`）—— 白名单校验就靠它"""
    p = path or os.path.join(LIB, '_sources.json')
    with open(p, encoding='utf-8-sig') as f:
        return json.load(f)


def source_of(rel, sources):
    """模板相对路径 → 来源 URL。**没有来源 = 不许当模板**（不许静默放行）。"""
    if rel in sources:
        return sources[rel]
    base = os.path.basename(rel)
    hit = [v for k, v in sources.items() if os.path.basename(k) == base]
    return hit[0] if len(hit) == 1 else None


def source_kind(url):
    """URL → 站点名（`bitmidi` / `vgmusic` / `mutopia` …）；不在白名单返回 None"""
    if not url:
        return None
    m = re.match(r'https?://([^/]+)', url.strip())
    if not m:
        return None
    host = m.group(1).lower()
    for h in AUTHORITATIVE_HOSTS:
        if host == h or host.endswith('.' + h):
            return h.split('.')[0] if h.count('.') == 1 else h.split('.')[-2]
    return None


def whitelist_problem(rel, url):
    """模板来源是否合规（返回 None = 合规，否则返回原因字符串）"""
    if not os.path.isabs(rel) and ('/' in rel or '\\' in rel):
        inside = os.path.normpath(os.path.join(LIB, rel))
        if not inside.startswith(os.path.normpath(LIB)):
            return '模板 %s 不在模板库 %s 内' % (rel, os.path.relpath(LIB, ROOT))
    if source_kind(url) is None:
        return ('模板 %s 的来源不在权威白名单（%s）：%s'
                % (rel, '/'.join(AUTHORITATIVE_HOSTS), url or '（无来源 URL）'))
    return None


# ---------------------------------------------------------------- 选模板
def candidates(theme, index=None):
    """主题 → 候选模板（同主题风格集合内；**拍号必须与主题一致**，剔除坏数据）

    为什么要卡拍号：和声窗口（4 小节）、16 分格节奏型、段落长度**全都按拍号切**，
    把 [5,4]/[7,4]/[3,8] 混进 4/4 的主题统计，得到的是各首的并集垃圾
    （实测第一版：共识节奏型 0.1 音/小节、进行全是 `Gsus4 Gsus4 Gsus4 Gsus4`）。
    """
    th = THEMES[theme]
    meter = list(th.get('meter') or [4, 4])
    rows, seen = [], set()
    for r in (index if index is not None else lib_index()):
        if r.get('style') not in th['styles']:
            continue
        bpm, bars = r.get('bpm') or 0, r.get('bars') or 0
        if not (40 <= bpm <= 220) or bars < 8 or (r.get('note_count') or 0) < 40:
            continue                      # 坏数据/速成练习曲/超长炫技曲不进主题统计
        if list(r.get('timesig') or []) != meter:
            continue
        dens = (r.get('note_count') or 0) / max(1.0, bars)
        if not (0.5 <= dens <= 120):
            continue                      # 音密度离谱（扒谱碎片 / 黑 MIDI 数据堆）
        tdens = max([(t.get('notes') or 0) for t in (r.get('tracks') or [])] or [0]) \
            / max(1.0, bars)
        if tdens > 40:
            continue                      # 单轨就 40 音/小节 = 数据堆，不是编配
        key = r.get('md5') or r['file']
        if key in seen:
            continue
        seen.add(key)
        rows.append(r)
    return rows


def pick_templates(theme, min_n=MIN_TEMPLATES, target=DEF_TEMPLATES, index=None):
    """候选 → **多样性抽样**的模板清单（确定性：同库同参数 → 同结果）。

    做法：① 按风格分配额（每风格至少 1 首，余量轮转分配）② 每个风格内按 (bpm, 文件名)
    排序后**均匀间隔取**（首尾都取到 → 覆盖该风格的速度带）。这样保证"每风格都有代表 +
    速度覆盖"，而不是"挑最像的几首" —— 聚合画像才不会被单首的怪速度/怪和弦带偏。
    """
    rows = candidates(theme, index)
    if len(rows) < min_n:
        raise SystemExit(
            '主题 %s（%s）在 %s 里只找到 %d 首模板，少于下限 %d 首。\n'
            '  可选：① --allow-fetch 联网抓（fetch_midi_lib.py）② 调整 THEMES 里的 styles\n'
            '  ③ 明说 --min %d（**不许**把"没达标"说成达标）'
            % (theme, '/'.join(THEMES[theme]['styles']), os.path.relpath(LIB, ROOT),
               len(rows), min_n, len(rows)))
    groups = collections.OrderedDict()
    for r in rows:
        groups.setdefault(r['style'], []).append(r)
    for k in groups:
        groups[k].sort(key=lambda r: (r['bpm'], r['file']))
    want = max(min_n, min(target, len(rows)))
    # ① 风格配额：每风格先 1 首，余量按"可用数多的风格优先"轮转
    quota = {k: 0 for k in groups}
    order = sorted(groups, key=lambda k: (-len(groups[k]), k))
    i = 0
    while sum(quota.values()) < want and i < want * len(order) + len(order):
        k = order[i % len(order)]
        if quota[k] < len(groups[k]):
            quota[k] += 1
        i += 1
    # ② 风格内均匀间隔取（首尾都取到）
    out = []
    for k, arr in groups.items():
        q = min(quota[k], len(arr))
        if q <= 0:
            continue
        idxs = {int(round(i * (len(arr) - 1) / float(q - 1))) for i in range(q)} if q > 1 \
            else {len(arr) // 2}
        for extra in range(len(arr)):            # 去重后不够 q 首就就近补
            if len(idxs) >= q:
                break
            idxs.add(extra)
        out += [arr[j] for j in sorted(idxs)[:q]]
    out.sort(key=lambda r: (r['style'], r['bpm'], r['file']))
    return out


# ---------------------------------------------------------------- 单模板分析
def _corr(v, prof, tonic):
    """零均值归一化相关（v 与"主音移到 tonic 的调性剖面"）"""
    p = [prof[(i - tonic) % 12] for i in range(12)]
    mv, mp_ = sum(v) / 12.0, sum(p) / 12.0
    a = [x - mv for x in v]
    b = [x - mp_ for x in p]
    den = (sum(x * x for x in a) ** 0.5) * (sum(x * x for x in b) ** 0.5)
    return sum(x * y for x, y in zip(a, b)) / den if den else 0.0


def _key_of(pc_weight):
    """音级权重（12 维）→ (tonic_pc, mode, confidence)（Krumhansl 相关法）

    confidence = 最优 - 次优：两个候选接近 = 调性模糊，"主题调"不该被当成确定值。
    """
    tot = sum(pc_weight) or 1.0
    v = [x / tot for x in pc_weight]
    scored = []
    for mode, prof in (('major', KK_MAJOR), ('minor', KK_MINOR)):
        for tonic in range(12):
            scored.append((_corr(v, prof, tonic), tonic, mode))
    scored.sort(key=lambda z: -z[0])
    best = scored[0]
    return best[1], best[2], round(best[0] - (scored[1][0] if len(scored) > 1 else 0.0), 3)


def split_symbol(sym):
    """和弦符号 → (根音音级, 归一后缀)；认不出来返回 (None, None)"""
    m = re.match(r'^([A-G][#b]?)(.*)$', (sym or '').strip())
    if not m:
        return None, None
    root, suf = m.group(1), m.group(2)
    pc = NAMES.index(root) if root in NAMES else (
        (NAMES.index(root[0]) - 1) % 12 if root.endswith('b') else None)
    if pc is None:
        return None, None
    suf = SUF_ALIAS.get(suf, None) if suf else ''
    if suf is None:
        return None, None
    return pc, suf


def roman_token(pc, suf, tonic_pc):
    """(根音, 后缀, 主音) → 罗马级数记号（如 `i` / `VI7` / `bIIImaj7`）

    约定：**大小写表示三和弦性质**，所以小三和弦的 'm' 不再重复写进记号
    （`Am` 在 a 小调 = `i`，不是 `im`）；后缀只保留"性质之外"的信息（7 / 6 / maj7 / m7b5）。
    """
    deg = (pc - tonic_pc) % 12
    base = DEG_ROMAN[deg]
    s = suf or ''
    minor = ((s.startswith('m') and not s.startswith('maj')) or s == 'dim')
    if minor:
        if s == 'm':
            s = ''
        elif s in ('m7', 'm6'):
            s = s[1:]                      # m7 → 7（小七由小写级数表达）
        # m7b5 / dim / maj7 原样保留（表里一一对应，不许丢信息）
    return (base.lower() if minor else base) + s


def _polyphony(notes):
    """**平均同时发声数**（判"这条轨是不是单声部旋律"）。

    用时间加权，不用"音数除以轨长" —— 后者在长音+密集经过音的轨上会给出假高/假低。
    """
    ev = []
    for (s, d, _n, _v) in notes:
        ev.append((s, 1))
        ev.append((s + max(1, d), -1))
    ev.sort()
    cur, area, prev = 0, 0.0, (ev[0][0] if ev else 0)
    for t, dv in ev:
        area += cur * (t - prev)
        prev = t
        cur += dv
    span = (ev[-1][0] - ev[0][0]) if ev else 0
    return area / span if span > 0 else 1.0


def _melody_track(res, bars=None):
    """挑"旋律轨"：音区高 + 近似单声部 + **不要太密**（**启发式**，结果记进模板清单可复核）

    判据：跳过鼓通道与打击音色；音数 ≥ 12；平均音高最高；每多一层同时发声扣 6 个半音；
    每小节超过 3 个音再扣（一条真旋律通常 1.5~3 音/小节，16 分的钢琴织体不是旋律）。
    """
    div = res['division'] or 480
    num, den = res['timesig'] or (4, 4)
    bar_ticks = max(1, int(round(div * num * 4.0 / den)))
    best = None
    for t in res['tracks']:
        notes = t['notes']
        if len(notes) < 12 or t['channel'] == 9:
            continue
        if role_of_program(t['program']) == 'perc':
            continue
        ps = [n[2] for n in notes]
        mean = sum(ps) / len(ps)
        poly = _polyphony(notes)
        tb = (max(s + d for (s, d, _n, _v) in notes) / float(bar_ticks)) or 1.0
        dens = len(notes) / max(1.0, tb)
        score = mean - 6.0 * max(0.0, poly - 1.15) - 2.0 * max(0.0, dens - 3.0)
        if best is None or score > best[0]:
            best = (score, t, round(poly, 2), round(mean, 1), round(dens, 2))
    return best


def _melody_notes(res):
    """旋律轨 → [(起始拍, 结束拍, 音高)]（拍 = 四分音符，与画像/引擎同口径）

    做**最高声部单音化**（top-voice reduction）：同一起点只留最高音，并且把与下一个音
    重叠的时值裁到下一个音起始。不这样做的话，"旋律轨"常常是钢琴织体 —— 实测
    `notes_per_bar` 会到 5.3（真实旋律 1.5~3），画出来的"旋律语言"其实是伴奏语言。
    """
    hit = _melody_track(res)
    if not hit:
        return [], None
    div = res['division'] or 480
    t = hit[1]
    notes = sorted(t['notes'], key=lambda n: (n[0], -n[2]))
    mono, last_start = [], None
    for (s, d, p, _v) in notes:
        if s == last_start:               # 同一起点只留最高音
            continue
        last_start = s
        a, b = s / float(div), (s + d) / float(div)
        mono.append((a, min(b, a + 4.0), p))      # 长按音截到 4 拍（超长音不是旋律原型）
    out = []
    for i, (a, b, p) in enumerate(mono):
        nxt = mono[i + 1][0] if i + 1 < len(mono) else None
        if nxt is not None and nxt > a and nxt < b:
            b = nxt                            # 与下一个音重叠 → 裁到下一个音起始
        if b - a < 0.05:
            continue
        out.append((a, b, p))
    return out, {'index': t['index'], 'name': (t['name'] or '(未命名)')[:40],
                 'program': t['program'], 'channel': t['channel'] + 1,
                 'poly': hit[2], 'mean_pitch': hit[3], 'density': hit[4]}


def analyze_template(row, path=None, sources=None):
    """一个模板 → 特征字典（和声/节奏/旋律/配器/曲式），**全部音符层精确值**"""
    rel = row['file']
    src = source_of(rel, sources if sources is not None else lib_sources())
    p = path or os.path.join(LIB, *rel.split('/'))
    rep = mr.analyze(p)                    # 和弦/低音/节奏型/曲式（口径唯一）
    res = mp.parse(p, quiet=True)
    div = res['division'] or 480
    num, den = res['timesig'] or (4, 4)
    beats_per_bar = num * 4.0 / den

    # 调式：全轨音级加权 + 低音线加权（口径见 `_pc_weight`）→ 和弦转级数记号靠它
    pcw = _pc_weight(res)
    tonic, mode, conf = _key_of(pcw)

    chords, romans = [], []
    for b in rep['bar_detail']:
        sym = b['chord']
        chords.append(sym)
        pc, suf = split_symbol(sym)
        romans.append(roman_token(pc, suf, tonic) if pc is not None else None)

    mel, mtrack = _melody_notes(res)
    mstat = mprof_stats(mel, row.get('bpm') or 120.0, tonic,
                        MINOR_SCALE if mode == 'minor' else MAJOR_SCALE) if mel else {}
    if mstat:
        mstat['pitches'] = [p for (_a, _b, p) in mel]     # 供聚合时取音域分位数

    roles = collections.Counter()
    rng = collections.defaultdict(list)
    for t in res['tracks']:
        if not t['notes'] or t['channel'] == 9:
            continue
        role = role_of_program(t['program'])
        roles[role] += 1
        ps = [n[2] for n in t['notes']]
        rng[role] += [min(ps), max(ps)]
    low = _grid(res)
    return {
        'file': rel, 'style': row['style'], 'md5': row.get('md5'),
        'bpm': row.get('bpm'), 'timesig': list(row.get('timesig') or [4, 4]),
        'bars': rep['bars'], 'seconds': round(rep['seconds'], 1),
        'note_count': row.get('note_count'),
        'source': src, 'kind': source_kind(src),
        'tonic': NAMES[tonic], 'mode': mode, 'key_conf': conf,
        'chords': chords, 'romans': romans,
        'chord_per_bar': round(sum(1 for c in chords if c not in ('?', '-'))
                               / max(1, len(chords)), 2),
        'rhythm_low': low['low_pattern'], 'rhythm_high': low['high_pattern'],
        'grid': low,
        'low_density': low['low_density'], 'high_density': low['high_density'],
        'melody': mstat, 'melody_track': mtrack,
        'roles': dict(roles), 'role_range': {k: [min(v), max(v)] for k, v in rng.items()},
        'track_count': len([t for t in res['tracks'] if t['notes']]),
        'beats_per_bar': beats_per_bar,
        'key_pc_weight': [round(x, 2) for x in pcw],
    }


def mprof_stats(notes, bpm, tonic_pc, scale):
    """旋律语言统计 —— **口径复用 `melody_profile.stats`**（扒谱与模板同一套判据）"""
    import melody_profile as mpf
    return mpf.stats(notes, bpm, tonic_pc, scale)


def role_of_program(prog):
    """GM program（None = 没写 program change，按钢琴算）→ 轨角色"""
    prog = int(prog or 0)
    for lo, hi, role in ROLE_BY_PROGRAM:
        if lo <= prog <= hi:
            return role
    return 'other'


# ---------------------------------------------------------------- 聚合
def _pc_weight(res):
    """音级权重：按音长加权 + **低音线加权 3 倍**。

    为什么必须加低音：Krumhansl 相关只看音级分布时，**关系大小调会互串**
    （A 自然小调与 C 大调音级集合相同，实测把 a 小调判成 C 大调）。低音是调性最
    强的线索 —— 每拍取最低音（和声根音位置）额外加权，关系大小调就分得开了。
    """
    w = [0.0] * 12
    bass = {}
    div = res['division'] or 480
    for t in res['tracks']:
        if t['channel'] == 9:
            continue
        for (s, d, n, v) in t['notes']:
            w[n % 12] += d * (v / 100.0)
            slot = int(s / (div / 4.0)) if div else 0
            if slot not in bass or n < bass[slot][0]:
                bass[slot] = (n, d)
    for (n, d) in bass.values():
        w[n % 12] += 3.0 * d
    return w


def _consensus(patterns, slots):
    """多条 16 分格节奏型 → (共识型, 每格出现率)。**超过一半**模板有音 → 记为 ★

    用"严格过半"而不是"≥50%"：偶数模板平票（2 首里 1 首有音）时不该算共识 ——
    共识型的意义是"这个主题通常这么打"，掺进平票格就成了各首的并集。
    """
    cnt = [0] * slots
    for pat in patterns:
        for i, ch in enumerate(pat[:slots]):
            if ch == '★':
                cnt[i] += 1
    n = max(1, len(patterns))
    share = [round(c / float(n), 2) for c in cnt]
    return ''.join('★' if s > 0.5 else '·' for s in share), share


def deg_of_roman(tok):
    """罗马记号 → 音级（0-11）；认不出返回 None"""
    m = re.match(r'^([b#]?[IViv]+)', tok or '')
    if not m:
        return None
    base = m.group(1)
    return next((i for i, rb in enumerate(DEG_ROMAN) if rb.lower() == base.lower()), None)


def _top_windows(feats, span=4, top=5, min_templates=2):
    """跨模板统计常用和声进行（`span` 小节窗口，**转调不变**，按音级序列计数）

    三条过滤，都是实测踩出来的：
      · 按**音级序列**（不是和弦符号）计数与要求多样性：`I6 → Imaj7` 只是同一个和弦换了
        延伸音，不该算"两个和弦的进行"（实测 2 小节窗口第一版被 `I6 I` 占满 50%）
      · 窗口里至少 min(3, span) 个不同音级 —— 防"一个和弦撑满 4 小节"夺冠
      · 至少 `min_templates` 首模板共有 —— 单首的怪进行不算"这个主题的进行"
    """
    need = min(3, span)
    cnt, per = collections.Counter(), collections.defaultdict(set)
    forms = collections.defaultdict(collections.Counter)
    for f in feats:
        r = [x for x in f['romans'] if x]
        for i in range(0, max(0, len(r) - span + 1)):
            toks = tuple(r[i:i + span])
            dkey = tuple(deg_of_roman(t) for t in toks)
            if any(d is None for d in dkey) or len(set(dkey)) < need:
                continue
            cnt[dkey] += 1
            per[dkey].add(f['file'])
            forms[dkey][toks] += 1
    out = []
    for dkey, c in cnt.most_common(top * 6):
        share = round(len(per[dkey]) / max(1, len(feats)), 2)
        if len(per[dkey]) < min_templates and len(out) >= 1:
            continue
        out.append({'degrees': list(dkey),
                    'romans': list(forms[dkey].most_common(1)[0][0]),
                    'count': c, 'templates': sorted(per[dkey]),
                    'template_share': share})
        if len(out) >= top:
            break
    out.sort(key=lambda d: (-d['template_share'], -d['count'], d['romans']))
    return out


def _chain_progression(feats, ton, qdeg, length=4):
    """**和弦池兜底进行**：按模板里"级数 → 下一级数"的实际转移统计串成一条进行。

    为什么不用"和弦池前四名直接排一排"：那只说明"这几个和弦常见"，排出来的顺序是任意的
    （实测给出 `Imaj7 IV6 Vsus4 vii7`，听感上不成立）。转移统计来自模板里真实出现过的
    相邻关系，串出来的才是能用的进行。
    """
    pair = collections.defaultdict(set)          # (a, b) -> 模板集合
    for f in feats:
        r = [x for x in f['romans'] if x]
        for a, b in zip(r, r[1:]):
            if a != b:
                pair[(a, b)].add(f['file'])
    if not pair:
        return []
    good = {k: v for k, v in pair.items() if len(v) >= 2} or pair
    outdeg = collections.defaultdict(int)
    for (a, _b), s in good.items():
        outdeg[a] += len(s)
    # 起点优先主和弦（I / i），否则取"出去最多"的那个
    start = next((t for t in ('I', 'i', 'Imaj7', 'i7') if t in outdeg), None) \
        or max(outdeg, key=lambda k: outdeg[k])
    chain, cur = [start], start
    while len(chain) < length:
        nxt = sorted(((len(s), b) for (a, b), s in good.items() if a == cur and b != cur),
                     reverse=True)
        if not nxt:
            break
        cur = nxt[0][1]
        chain.append(cur)
    return chain if len(chain) >= 2 else []


def _chord_pool(feats):
    """和弦池：级数 + 最常用后缀 + 出现模板数（生成时据此拼和弦表）"""
    suf = collections.defaultdict(collections.Counter)
    tpls = collections.defaultdict(set)
    for f in feats:
        tpc = NAMES.index(f['tonic'])
        for sym in f['chords']:
            pc, s = split_symbol(sym)
            if pc is None:
                continue
            deg = (pc - tpc) % 12
            suf[deg][s] += 1
            tpls[deg].add(f['file'])
    out = []
    for deg, c in sorted(suf.items(), key=lambda kv: -sum(kv[1].values())):
        out.append({'degree': deg, 'roman': DEG_ROMAN[deg],
                    'suffix': c.most_common(1)[0][0], 'count': sum(c.values()),
                    'templates': len(tpls[deg])})
    return out


# 罗马级数 → 绝对和弦符号时，小写（小三和弦）与后缀的对应（保证过 parse_chord）
# 键 = roman_token 可能产生的后缀（split_symbol 归一后的闭集）
SUF_MINOR = {'': 'm', '7': 'm7', '6': 'm6', 'm7': 'm7', 'm6': 'm6',
             'maj7': 'maj7', 'm7b5': 'm7b5', 'dim': 'dim', 'sus4': 'm'}
SUF_MAJOR = {'': '', '7': '7', '6': '6', 'm': 'm', 'm7': 'm7', 'm6': 'm6',
             'maj7': 'maj7', 'm7b5': 'm7b5', 'dim': 'dim', 'sus4': 'sus4'}


def _abs_progression(romans, tonic_pc, quality_by_deg):
    """罗马级数进行 → 主题调上的绝对和弦符号（**过不了 `st.parse_chord` 就整体放弃**）"""
    out = []
    for tok in romans:
        m = re.match(r'^([b#]?[IViv]+)(.*)$', tok)
        if not m:
            return None
        base, suf = m.group(1), m.group(2)
        deg = next((i for i, rb in enumerate(DEG_ROMAN) if rb.lower() == base.lower()), None)
        if deg is None:
            return None
        minor = base.islower()            # 罗马记号的大小写 = 三和弦性质（口径来自 roman_token）
        table = SUF_MINOR if minor else SUF_MAJOR
        if not suf:                       # 记号没带后缀 → 用和弦池里该级数的常用后缀
            suf = quality_by_deg.get(deg, '')
        suf = table.get(suf, table[''])
        sym = NAMES[(tonic_pc + deg) % 12] + (suf or '')
        try:
            root, _, want = st.parse_chord(sym)
        except Exception:                                  # noqa: BLE001
            return None
        if root is None or want is None:
            return None
        out.append(sym)
    return out


def _grid(res):
    """节奏画像：一小节的 16 分格**占用率** + **分角色密度**（低/高区 + 贝斯 + 打击）。

    为什么不用 `midi_ref.analyze` 的 `rhythm_low/high`：那是**全曲合并**的"该格有没有出现过"
    —— 长曲子几乎每格都出现过，于是共识型变成 `★★★★★★★★` 而密度算出 0.1 音/小节
    （实测第一版就是这个结果，看着像数据、其实没有信息）。这里按**每小节**统计。

    密度**按轨角色分别算**（`bass_density` / `perc_density`）：按"音高 < 60"的粗分区
    会把钢琴左手八度算成贝斯、把琶音算成打击，据此选出的 bass_style/perc_style 必然偏
    （实测：民谣/流行主题被推成 `bass=sixteenth perc=dance`）。
    """
    div = res['division'] or 480
    num, den = res['timesig'] or (4, 4)
    bar_ticks = max(1, int(round(div * num * 4.0 / den)))
    slots = max(1, int(round(num * 16.0 / den)))
    occ = {'low': [0] * slots, 'high': [0] * slots}
    on = {'low': 0, 'high': 0, 'bass': 0, 'perc': 0}
    bars = set()
    for t in res['tracks']:
        role = 'perc' if t['channel'] == 9 else role_of_program(t['program'])
        for (s, d, n, _v) in t['notes']:
            zone = 'low' if n < 60 else 'high'
            slot = int(round((s % bar_ticks) / (bar_ticks / float(slots)))) % slots
            occ[zone][slot] += 1
            on[zone] += 1
            if role == 'bass':
                on['bass'] += 1
            if role == 'perc':
                on['perc'] += 1
            bars.add(s // bar_ticks)
    nbars = max(1, len(bars))
    return {'low_pattern': ''.join('★' if c / float(nbars) >= 0.5 else '·'
                                   for c in occ['low']),
            'high_pattern': ''.join('★' if c / float(nbars) >= 0.5 else '·'
                                    for c in occ['high']),
            'low_occ': [round(c / float(nbars), 3) for c in occ['low']],
            'high_occ': [round(c / float(nbars), 3) for c in occ['high']],
            'low_density': round(on['low'] / float(nbars), 2),
            'high_density': round(on['high'] / float(nbars), 2),
            'bass_density': round(on['bass'] / float(nbars), 2),
            'perc_density': round(on['perc'] / float(nbars), 2),
            'slots': slots}


def aggregate(theme, rows, feats, min_n=MIN_TEMPLATES):
    """模板清单 + 逐首特征 → **主题模板包**（生成时整体依据它）"""
    th = THEMES[theme]
    if len(feats) < min_n:
        raise SystemExit('主题 %s 只有 %d 首模板可用，少于下限 %d' % (theme, len(feats), min_n))
    bpms = sorted(f['bpm'] for f in feats)
    med_bpm = bpms[len(bpms) // 2]
    # 主题调式：各模板音级权重按**自己主音**折成级数后求和（跨调可比）
    deg_w = [0.0] * 12
    for f in feats:
        t = NAMES.index(f['tonic'])
        for i, w in enumerate(f['key_pc_weight']):
            deg_w[(i - t) % 12] += w
    ton, mode, conf = _key_of(deg_w)
    pool = _chord_pool(feats)
    qdeg = {p['degree']: p['suffix'] for p in pool}
    # 和声进行：4 小节窗口优先，不够就退到 2 小节窗口；再不够就用"和弦池前四名"兜底。
    # **每一层都记进包里**（template_share 如实写），生成时按可用性降级 —— 不许拿
    # "只有一首模板有"的进行冒充"这个主题的进行"。
    progs4 = _top_windows(feats, span=4, top=5)
    progs2 = _top_windows(feats, span=2, top=6, min_templates=3)
    # 兜底进行：模板里真实的"级数 → 下一级数"转移链（不是"把常见和弦排一排"）
    pool_prog = _chain_progression(feats, ton, qdeg, length=4)
    for lst in (progs4, progs2):
        for p in lst:
            p['symbols'] = _abs_progression(p['romans'], ton, qdeg)
            p['usable'] = bool(p['symbols'])
    progs4 = [p for p in progs4 if p['usable']]
    progs2 = [p for p in progs2 if p['usable']]
    pool_syms = _abs_progression(pool_prog, ton, qdeg) if pool_prog else None
    # **主进行**：按证据强度降级选取（4 小节窗口 分享≥20% → 2 小节窗口 分享≥30% → 转移链）。
    # 选定结果与来源写进包，生成时直接用 —— 免得"每次生成挑哪条"变成一个说不清的决定。
    primary, primary_src = None, None
    if progs4 and progs4[0]['template_share'] >= 0.2:
        primary, primary_src = progs4[0]['symbols'], 'window4'
    elif progs2 and progs2[0]['template_share'] >= 0.3:
        primary, primary_src = progs2[0]['symbols'] * 2, 'window2'
    elif pool_syms:
        primary, primary_src = pool_syms, 'chain'

    slots_low = int(round(list(th.get('meter') or [4, 4])[0] * 16.0
                          / list(th.get('meter') or [4, 4])[1]))
    # 节奏共识：逐模板的**每小节占用率**（`_grid`）→ 跨模板取"过半模板每小节都用"的格
    grids = [f['grid'] for f in feats]
    lo_pat = [g['low_pattern'] for g in grids]
    hi_pat = [g['high_pattern'] for g in grids]
    con_low, share_low = _consensus(lo_pat, slots_low)
    con_high, share_high = _consensus(hi_pat, slots_low)

    # 配器：角色出现率（有该角色的模板占比）+ 音域（各模板 min/max 的中位）
    role_share, role_rng = {}, {}
    for role in sorted({r for f in feats for r in f['roles']}):
        have = [f for f in feats if role in f['roles']]
        role_share[role] = round(len(have) / len(feats), 2)
        los = sorted(f['role_range'][role][0] for f in have)
        his = sorted(f['role_range'][role][1] for f in have)
        role_rng[role] = [los[len(los) // 2], his[len(his) // 2]]
    arr_share = {v: role_share.get(k, 0.0) for k, v in ROLE_TO_ARR.items()}
    # 三档结论（生成时按档处理，别让"证据不足"变成"关掉"）：
    #   on    = ≥50% 模板有该乐器 → 开
    #   off   = **0%**（真的一首都没有）→ 关；中间档一律交给引擎风格预设
    #   maybe = 0 < share < 50% → 无结论
    # ⚠ **基础声部（bass/piano）不许按"角色缺失"关掉**：那说的是"模板里没有独立的贝斯**轨**"，
    #   不等于"音乐里没有低音"——钢琴/古钢琴曲的低音在左手（实测 classic 主题：
    #   角色 'bass' 占比 0%，但低音区起音 **8.8 个/小节**）。照"缺失即关"处理会生成
    #   40–80Hz 只有 −33dB 的成品（实测，整条低频没了）。基础声部一律交给风格预设，
    #   要关得靠"低音区确实没内容"的证据（`validate_pack` 里守着这条）。
    FOUNDATION = ('bass', 'piano')
    arr_on = sorted(k for k, sh in arr_share.items() if sh >= 0.5)
    arr_off = sorted(k for k, sh in arr_share.items() if sh <= 0.0 and k not in FOUNDATION)
    arr_maybe = sorted(k for k, sh in arr_share.items() if 0.0 < sh < 0.5)
    styles = collections.Counter(f['style'] for f in feats)
    perc_votes = collections.Counter(PERC_BY_STYLE.get(s, 'light')
                                     for s, c in styles.items() for _ in range(c))
    perc_style = perc_votes.most_common(1)[0][0]
    high_dens = sum(g['high_density'] for g in grids) / len(grids)
    low_dens = sum(g['low_density'] for g in grids) / len(grids)
    bass_dens = sum(g['bass_density'] for g in grids) / len(grids)
    perc_dens = sum(g['perc_density'] for g in grids) / len(grids)
    # 音型先看**风格先验**，只在证据很强时才升级/降级（阈值写在这里，别藏在别处）：
    #   · 贝斯：真实贝斯轨每小节 ≥3 个起音 = 八分及更密 → sixteenth
    #   · 打击：真实鼓轨每小节 ≥3 个起音（或风格先验已是 dance）才算有鼓组；
    #     没有鼓轨的主题用 light（沙锤），免得 5-18kHz 全靠编配硬撑
    bass_style = 'sixteenth' if bass_dens >= 3.0 else 'simple'
    if perc_dens < 0.5 and perc_style == 'dance':
        perc_style = 'light'
    if th.get('meter') == [3, 4]:
        perc_style = bass_style = 'waltz'

    # 旋律语言：直方图求和（模板多 → 统计稳），标量取均值
    mel = _merge_melody([f['melody'] for f in feats if f.get('melody')])
    if not mel:
        raise SystemExit('主题 %s 的模板都没能挑出旋律轨（%s）—— 换模板或补库'
                         % (theme, '；'.join(
                             '%s: %s' % (f['file'], (f.get('melody_track') or {}).get('name'))
                             for f in feats[:3])))
    mel.pop('_dur_total', None)

    # 曲式：段落长度取"常用进行长度 × 2"与模板总量的中位，铺满整数个段落
    progs = progs4 or progs2
    span = len(progs[0]['romans']) if progs else 4
    sec_bars = max(4, min(16, span * 2))
    tot_med = sorted(f['bars'] for f in feats)[len(feats) // 2]
    nsec = max(2, min(8, int(round(tot_med / float(sec_bars)))))
    total = sec_bars * nsec                    # **总小节 = 段落数 × 段长**（对得上，不许各算各的）
    plan = []
    for i in range(nsec):
        nm = ['A', 'A2', 'B', 'A3', 'C', 'A4', 'B2', 'A5'][i % 8]
        prog_i = 0 if nm.startswith('A') else (1 if nm.startswith('B') else 2)
        plan.append({'name': nm, 'bars': sec_bars,
                     'prog': min(prog_i, max(0, len(progs) - 1))})

    pack = {
        'theme': theme, 'label': th['label'], 'desc': '%s（主题模板包：%d 首同主题模板聚合）'
        % (th['label'], len(feats)),
        'styles': list(th['styles']), 'engine_style': th['engine'],
        'meter': list(th.get('meter') or [4, 4]),
        'min_templates': min_n, 'template_count': len(feats),
        'bpm': {'median': med_bpm, 'p25': bpms[len(bpms) // 4],
                'p75': bpms[(3 * len(bpms)) // 4]},
        'key': {'tonic': NAMES[ton], 'pc': ton, 'mode': mode, 'confidence': conf,
                'scale_degree_weight': [round(x, 2) for x in deg_w]},
        'templates': [{k: f[k] for k in ('file', 'style', 'md5', 'bpm', 'timesig',
                                         'bars', 'seconds', 'source', 'kind')}
                      for f in feats],
        'source_kinds': dict(collections.Counter(f['kind'] or 'unknown' for f in feats)),
        'harmony': {'primary': primary, 'primary_source': primary_src,
                    'progressions': progs, 'progressions_4': progs4,
                    'progressions_2': progs2, 'pool_progression': pool_syms,
                    'pool_progression_romans': pool_prog, 'chord_pool': pool[:16],
                    'chord_per_bar': round(sum(f['chord_per_bar'] for f in feats)
                                           / len(feats), 2)},
        'rhythm': {'low16': con_low, 'high16': con_high,
                   'low_slot_share': share_low, 'high_slot_share': share_high,
                   'low_density': round(low_dens, 2), 'high_density': round(high_dens, 2),
                   'bass_density': round(bass_dens, 2), 'perc_density': round(perc_dens, 2),
                   'slots': slots_low,
                   'bass_style': bass_style, 'perc_style': perc_style},
        'arrangement': {'roles': role_share, 'role_range': role_rng,
                        'arr_share': arr_share, 'arr_on': arr_on, 'arr_off': arr_off,
                        'arr_maybe': arr_maybe,
                        'track_count': round(sum(f['track_count'] for f in feats)
                                             / len(feats), 1),
                        'style_mix': dict(styles)},
        'form': {'section_bars': sec_bars, 'total_bars': total, 'plan': plan},
        'melody': mel,
        'notes': [('共 %d 首模板，来源：%s' % (len(feats), ', '.join(
            '%s×%d' % (k, v) for k, v in
            collections.Counter(f['kind'] or 'unknown' for f in feats).items()))),
            ('旋律轨为启发式挑选（音区最高 + 近似单声部），逐首记录见 '
             'refs/themes/%s_tracks.json' % theme)],
        'tool': 'theme_pack.py',
    }
    # **混音目标层**：从项目真实音频画像里挑（不是 MIDI 渲染，见 mix_target 的 docstring）。
    # 放在最后算：它要拿 bpm / perc_style / 调式 当判据。
    pack['mix_target'] = mix_target(pack)
    return pack


def _merge_melody(stats):
    """多首模板的旋律语言统计 → 一份聚合画像。

    两条口径（都用实测教训定的）：
      · **每首模板等权**：直方图先归一化（各模板 1000 份）再相加 —— 直接加原始计数会让
        一首 300 小节的曲子压过其它 9 首（"主题语言"变成"那一首的语言"）
      · **音域取分位数**（p10~p90）而不是各模板 min/max 的中位：后者被单首的极端音带飞
        （实测第一版给出 46-88，等于把整个钢琴音域当成旋律音域）
    """
    if not stats:
        return {}
    iv, dur, ons = (collections.Counter() for _ in range(3))
    pitches, npb, step, onb, dia, mean_p, phrases, bars = [], [], [], [], [], [], [], []
    for s in stats:
        for hist, ctr in ((s.get('interval_hist'), iv), (s.get('dur16_hist'), dur),
                          (s.get('onset16_hist'), ons)):
            tot = float(sum((hist or {}).values()) or 1)
            for k, v in (hist or {}).items():
                ctr[int(k)] += v / tot * 1000.0        # 每首等权
        pitches += list(s.get('pitches') or [])
        npb.append(s.get('notes_per_bar') or 0)
        step.append(s.get('stepwise_pct') or 0)
        onb.append(s.get('onbeat_pct') or 0)
        dia.append(s.get('diatonic_pct') or 0)
        mean_p.append(s.get('mean_pitch') or 0)
        phrases += list(s.get('phrase_bars') or [])
        bars.append(s.get('bars') or 0)
    n = len(stats)

    def avg(xs):
        return round(sum(xs) / max(1, len(xs)), 2)

    def pct(xs, q):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(len(xs) * q))] if xs else 0
    notes = sum(s.get('notes') or 0 for s in stats)
    tot_bars = sum(bars)
    return {
        'notes': notes,
        'range': [pct(pitches, 0.10) or pct([s['range'][0] for s in stats], 0.5),
                  pct(pitches, 0.90) or pct([s['range'][1] for s in stats], 0.5)],
        'mean_pitch': avg(mean_p), 'notes_per_bar': round(notes / max(1.0, tot_bars), 2),
        'stepwise_pct': avg(step), 'onbeat_pct': avg(onb), 'diatonic_pct': avg(dia),
        'interval_hist': {str(k): int(round(v)) for k, v in sorted(iv.items())},
        'dur16_hist': {str(k): int(round(v)) for k, v in sorted(dur.items())},
        'onset16_hist': {str(k): int(round(v)) for k, v in sorted(ons.items())},
        'phrases': len(phrases), 'phrase_bars': phrases[:60],
        'bars': round(tot_bars / float(n), 1),
        'templates': n,
    }


def maybe_fetch(styles, min_n=MIN_TEMPLATES, per_style=12):
    """同主题模板不足 → 联网补库（**用户口径：不足才抓**）。

    抓的是 `fetch_midi_lib.py`（BitMidi / VGMusic / Mutopia 三个公开站点，带来源 URL，
    抓完自动重建索引）—— 这就是"网络上的权威数据"那条通道。返回是否真抓了。
    """
    cmd = [sys.executable, os.path.join(HERE, 'fetch_midi_lib.py'),
           os.path.relpath(LIB, ROOT).replace('\\', '/'),
           '--per-style', str(per_style), '--styles', ','.join(styles)]
    print('  模板不足 → 联网补库：%s' % ' '.join(cmd[1:]))
    import subprocess
    r = subprocess.run(cmd, cwd=ROOT)
    return r.returncode == 0


def build(theme, min_n=MIN_TEMPLATES, target=DEF_TEMPLATES, allow_fetch=False,
          index=None, sources=None):
    """建一个主题包 → (pack, melody_profile)；同时落盘两个 JSON"""
    if theme not in THEMES:
        raise SystemExit('未知主题 %s；可选: %s' % (theme, ', '.join(sorted(THEMES))))
    th = THEMES[theme]
    idx = index if index is not None else lib_index()
    if allow_fetch and len(candidates(theme, idx)) < min_n:
        if maybe_fetch(th['styles'], min_n=min_n):
            idx = lib_index()
    rows = pick_templates(theme, min_n=min_n, target=target, index=idx)
    feats, skipped = [], []
    src = sources if sources is not None else lib_sources()
    for r in rows:
        p = os.path.join(LIB, *r['file'].split('/'))
        prob = whitelist_problem(r['file'], source_of(r['file'], src))
        if prob:
            skipped.append(prob)
            continue
        if not os.path.isfile(p):
            skipped.append('模板 %s 在磁盘上不存在' % r['file'])
            continue
        feats.append(analyze_template(r, path=p, sources=src))
    if len(feats) < min_n:
        raise SystemExit('主题 %s 可用模板只剩 %d 首（要求 ≥%d）：%s'
                         % (theme, len(feats), min_n, '；'.join(skipped[:3])))
    pack = aggregate(theme, rows, feats, min_n=min_n)
    pack['skipped'] = skipped
    melp = dict(pack['melody'])
    melp.update({'name': theme, 'file': 'theme:%s' % theme,
                 'bpm': pack['bpm']['median'], 'tonic': pack['key']['tonic'],
                 'mode': pack['key']['mode'], 'source': 'theme_pack.py',
                 'templates': [t['file'] for t in pack['templates']]})
    return pack, melp


def save(pack, melp, out_dir=None):
    """落盘：主题包 + 旋律子画像（+ 逐首模板明细，便于复核选了哪几首）

    `newline=''`：仓库统一 LF（`.gitattributes` 的 `* text=auto eol=lf`），
    否则 Windows 上 `json.dump` 会写 CRLF → 每次工具改写都变成"整文件 diff"。
    """
    d = out_dir or OUT
    os.makedirs(d, exist_ok=True)
    pp = os.path.join(d, pack['theme'] + '.json')
    with open(pp, 'w', encoding='utf-8', newline='') as f:
        json.dump(pack, f, ensure_ascii=False, indent=1)
    mpp = os.path.join(d, pack['theme'] + '_melody.json')
    melp = {k: v for k, v in melp.items() if not k.startswith('_')}
    # **旋律画像的来源留痕**（口径对齐 `refs/*.json` 的 `source`）：它从哪几首模板来、
    # 各自来源 URL 是什么。原来只有一个 `source: 'theme_pack.py'` —— 那只说明"谁生成的"，
    # **不说明"依据了哪几首"**（同名字段两种语义，最容易把人绕进去）。
    melp['provenance'] = {
        'generated_by': 'theme_pack.py',
        'aggregate_of': len(pack.get('templates') or []),
        'templates': [{'file': t.get('file'), 'source': t.get('source'),
                       'bpm': t.get('bpm')} for t in (pack.get('templates') or [])],
        'note': ('旋律语言画像 = 上面这些模板的**旋律轨聚合**（落点/时值/音程/句长方言）。'
                 '它是**作曲依据层**（管"旋律怎么说"），与混音目标 '
                 '（`refs/mix_targets/<主题>_mix.json`，管"混成什么样"）是两层，别混。')}
    with open(mpp, 'w', encoding='utf-8', newline='') as f:
        json.dump(melp, f, ensure_ascii=False, indent=1)
    # **聚合混音画像落盘**（`refs/mix_targets/<主题>.json`）：render.json 的 ref 指向它
    write_agg_ref(pack)
    return pp, mpp


def pack_path(theme, root=None):
    return os.path.join(root or ROOT, 'refs', 'themes', theme + '.json')


def melody_path(theme, root=None):
    return os.path.join(root or ROOT, 'refs', 'themes', theme + '_melody.json')


def load_pack(theme, root=None):
    p = pack_path(theme, root)
    if not os.path.isfile(p):
        raise SystemExit('没有主题包 %s —— 先跑 python scripts\\theme_pack.py %s'
                         % (os.path.relpath(p, root or ROOT), theme))
    with open(p, encoding='utf-8') as f:
        return json.load(f)


# 主题 perc_style → "打击感"目标值（0~1）。混音目标的挑选拿它和画像的性格比。
PERC_TARGET = {'dance': 0.75, 'pump': 0.80, 'light': 0.35, 'orchestral': 0.45,
               'waltz': 0.40, 'none': 0.20}
MIX_W = {'bpm': 0.55, 'perc': 0.30, 'mode': 0.15}   # 权重写在一处，结果里逐项记分


def portrait_perc(prof):
    """**真实音频画像**的"打击感"（0~1）：高频段相对电平 0.6 + 高频 16 分格起音占比 0.4。

    注意两边量纲不同：画像来自**真实混音**（含鼓组/沙锤），主题侧来自**音符层**（含钢琴
    左手）——所以不直接比密度，而是各自折成 0~1 的性格值再比。
    """
    b = prof.get('bands') or {}
    if not b:
        return None
    top = max(b.values())
    hi = (b.get('5000-10000', top - 30.0) + b.get('10000-18000', top - 40.0)) / 2.0
    lvl = max(0.0, min(1.0, (hi - (top - 22.0)) / 14.0))   # 比最响段低 8dB→1.0，低 22dB→0.0
    rh = prof.get('rhythm_high') or ''
    ons = sum(1 for ch in rh if ch in '★◇●◆')
    dens = (ons / float(len(rh))) if rh else 0.5
    return round(0.6 * lvl + 0.4 * dens, 3)


def portrait_mode(prof):
    """画像安静段音级 → 大/小调（Krumhansl，与逐模板判调**同一口径**）"""
    qc = prof.get('quiet_chroma') or {}
    w = [0.0] * 12
    hit = False
    for k, v in qc.items():
        if k in NAMES:
            w[NAMES.index(k)] = float(v)
            hit = True
    if not hit or sum(w) <= 0:
        return None
    _t, mode, _c = _key_of(w)
    return mode


MIX_MIN_MEMBERS = 3      # 混音目标**至少聚合几份**参考（用户口径："也要多方参考"）
MIX_MEMBER_REL = 0.6     # 成员门槛：分数 ≥ 最高分 × 这个比例（不把不相关的参考拉进来）
AGG_DIR = 'mix_targets'  # 聚合画像的落盘目录（`refs/mix_targets/<主题>.json`）


def agg_ref_path(theme, root=None):
    """聚合画像的路径。**文件名必须与 `mix_target.ref` 同名**（`<主题>_mix.json`）——
    第一版写成了 `<主题>.json`，而 `ref` 是 `<主题>_mix` → `new_song` 按 ref 名去找会扑空。"""
    return os.path.join(root or ROOT, 'refs', AGG_DIR, '%s_mix.json' % theme)


def find_ref_file(name, root=None):
    """按名字找画像文件：`refs/<名字>.json` → `refs/mix_targets/<名字>.json`（聚合画像）"""
    r = root or ROOT
    for p in (os.path.join(r, 'refs', '%s.json' % name),
              os.path.join(r, 'refs', AGG_DIR, '%s.json' % name)):
        if os.path.exists(p):
            return p
    return None


def write_agg_ref(pack, root=None):
    """把**聚合混音画像**落盘（`refs/mix_targets/<主题>.json`）—— 供 `render.json` 的 ref 引用。

    为什么要落盘而不是"用时现算"：成绩单 / 自动调参 / 预览面板都要**反复读**这份参考，
    而且要在别的目录、别的进程里读得到 —— 落成文件是唯一稳的做法（与 `refs/*.json` 同口径）。
    """
    theme = pack.get('theme')
    mt = pack.get('mix_target') or {}
    if not theme or not mt.get('members'):
        return None
    agg = aggregate_refs(theme, mt['members'], root=root)
    if not agg:
        return None
    d = os.path.join(root or ROOT, 'refs', AGG_DIR)
    os.makedirs(d, exist_ok=True)
    with open(agg_ref_path(theme, root), 'w', encoding='utf-8', newline='') as f:
        json.dump(agg, f, ensure_ascii=False, indent=1)
    return agg


# 参考音频的**出处**（**从画像自己的 `file` 字段里读出来的原始路径，不是猜的**）。
# 5 份画像记的是完整路径，指向两部 galgame 的 Bgm 目录 —— 于是这批参考可以如实写成
# "两部游戏的真实商业 BGM"，并附上**权威页面 URL**（维基百科 / VNDB / 萌娘百科）。
# ⚠ **厂牌与发行日期以那些页面为准，本次没有逐项核对** —— 所以字段里不写死年份/厂牌，
# 免得把没核实过的信息当事实（"写了权威却不给依据"比不写更糟）。
REF_PROV = (
    {'match': r'D:\game\end\仰望夜空星辰 FINE DAYS\Bgm',
     'work': '仰望夜空的星辰（FINE DAYS）',
     'url': 'https://zh.wikipedia.org/zh-cn/%E4%BB%B0%E6%9C%9B%E5%A4%9C%E7%A9%BA%E7%9A%84%E6%98%9F%E8%BE%B0',
     'url_alt': ['https://mzh.moegirl.org.cn/%E4%BB%B0%E6%9C%9B%E5%A4%9C%E7%A9%BA%E7%9A%84%E6%98%9F%E8%BE%B0'],
     'kind': 'game_bgm'},
    {'match': r'D:\test\galgame\ピュアソングガーデン！解包\Bgm',
     'work': 'Pure Song Garden！（ピュアソングガーデン！）',
     'url': 'https://zh.wikipedia.org/zh-hant/Pure_Song_Garden%EF%BC%81',
     'url_alt': ['https://vndb.org/r161991', 'https://vndb.org/r145443'],
     'kind': 'game_bgm'},
)


def tag_ref_sources(root=None):
    """给 `refs/*.json` 补 `source` 字段（**幂等**，已写的跳过）—— 混音参考要**可溯源**。

    出处**按可考证程度分级**（诚实优先：写"权威"两个字而不给出处，比不写更糟）：
      · `file` 里带**完整原始路径**的 → 写明是哪部作品的 Bgm 目录（`origin_path`）
        + **权威页面 URL**（`url`/`url_alt`：维基百科 / VNDB / 萌娘百科）
      · 只记了文件名的 → 写明"能溯源到哪个文件，但作品级出处未记录"（同批的其它画像
        指向 galgame 商业 BGM，可交叉印证，但不能替它下结论）
    以后接入**网络权威源**时写 `source.kind='web'` + `url` + `license` 即可 ——
    守卫只要求"每份成员都有可读的 source"，不限定形式。
    """
    root = root or ROOT
    n = 0
    for p in sorted(glob.glob(os.path.join(root, 'refs', '*.json'))):
        try:
            j = json.load(open(p, encoding='utf-8'))
        except Exception:                                  # noqa: BLE001
            continue
        if j.get('source'):
            continue
        fl = str(j.get('file') or '')
        prov = next((d for d in REF_PROV if d['match'] in fl), None)
        j['source'] = {
            'kind': prov['kind'] if prov else 'project_audio',
            'detail': ('%s 的 Bgm 目录（真实商业混音）' % prov['work']) if prov else
                      '项目素材包 Bgm（同批提取的 galgame 商业 BGM）',
            'file': fl,
            'origin_path': prov['match'] if prov else None,
            'url': prov['url'] if prov else None,
            'url_alt': list(prov.get('url_alt') or []) if prov else [],
            'note': ('真实商业混音；出处 = 作品名 + 原始路径 + 权威页面 URL。'
                     '厂牌/发行日期以 `url` 页面为准（**本次未逐项核对，故不写死**）。'
                     if prov else
                     '该文件在画像里**只记了文件名**、没有完整路径 —— 能溯源到"哪个文件"，'
                     '但作品级出处未记录（同批的其它画像指向 galgame 商业 BGM）。')}
        with open(p, 'w', encoding='utf-8', newline='') as f:
            json.dump(j, f, ensure_ascii=False, indent=1)
        n += 1
    return n


def _med(xs):
    v = sorted(x for x in xs if isinstance(x, (int, float)))
    return v[len(v) // 2] if v else None


def aggregate_refs(theme, members, root=None):
    """多份真实画像 → **一份聚合画像**（逐维度取中位数）。

    为什么（用户口径："混音要参考权威音源，**也要多方参考**"）：原来是"从 46 份里挑一份最像的"，
    单份画像的**个性**（某首曲子偏亮/偏厚）会整体带进成品。实测：`tender`（ballad 编配，
    钢琴+尼龙吉他+弦乐，中频天生厚）挑到了 **BGM04**（315–1250Hz 在 −10~−14dB 的**亮薄**参考）
    → 成品**中频厚 9.6dB**，成绩单直接报"先改 BPM 再谈其它"。
    多份取中位数能削掉单份的极端个性，也让"依据"从"一首"变成"一组"（可复核）。

    返回的 dict 与普通画像**同构**（`bands`/`structure`/`rms_db`/`width`/`centroid`/`bpm`/
    `character`）—— 于是 `render_midi` / `scorecard` / `make_song` 一行都不用改。
    额外的 `members` 记录每份成员 + 它的 `source`（可溯源）与评分。
    """
    root = root or ROOT
    profs = []
    for m in members:
        p = os.path.join(root, 'refs', str(m['ref']) + '.json')
        if not os.path.isfile(p):
            continue
        try:
            j = json.load(open(p, encoding='utf-8'))
        except Exception:                                  # noqa: BLE001
            continue
        if j.get('bands'):
            profs.append((m, j))
    if not profs:
        return None
    bands = {}
    keys = sorted({k for _m, j in profs for k in j['bands']})
    for k in keys:
        v = _med([j['bands'].get(k) for _m, j in profs])
        if v is not None:
            bands[k] = round(v, 2)
    # 段间曲线：**逐块取中位**（块数按最短的对齐 —— 不同长度的块不能硬凑）
    structs = [[float(x) for x in (j.get('structure') or []) if isinstance(x, (int, float))]
               for _m, j in profs]
    structs = [s for s in structs if len(s) >= 3]
    structure = []
    if structs:
        n = min(len(s) for s in structs)
        structure = [round(_med([s[i] for s in structs]), 1) for i in range(n)]
    return {
        'name': '%s_mix' % theme,
        'file': 'aggregate(%d refs)' % len(profs),
        'aggregate': True,
        'theme': theme,
        'bpm': _med([j.get('bpm') for _m, j in profs]),
        'rms_db': _med([j.get('rms_db') for _m, j in profs]),
        'width': _med([j.get('width') for _m, j in profs]),
        'centroid': _med([j.get('centroid') for _m, j in profs]),
        'duration': _med([j.get('duration') for _m, j in profs]),
        'character': 'instrumental',
        'bands': bands,
        'structure': structure,
        # 离散字段（节奏型 / 音级分布）**不取中位**（它们是某一首的特征，不是可平均的量）——
        # 取分数最高那份成员的值（`profs` 已按 members 顺序 = 分数降序）。
        'rhythm_low': profs[0][1].get('rhythm_low') or '',
        'rhythm_high': profs[0][1].get('rhythm_high') or '',
        'quiet_chroma': profs[0][1].get('quiet_chroma') or {},
        'periodicity': _med([j.get('periodicity') for _m, j in profs]),
        # **逐份留痕**：谁参与了聚合、评分多少、来源是什么
        'members': [{'ref': m['ref'], 'score': m.get('score'),
                     'bpm': j.get('bpm'), 'centroid': j.get('centroid'),
                     'rms_db': j.get('rms_db'), 'width': j.get('width'),
                     'source': j.get('source') or {}} for m, j in profs],
        'source_note': ('多份真实录音画像的**逐维度中位数**（成员见 members，各自带 source）'
                        '—— 单份画像的个性（偏亮/偏厚）不整体带进成品'),
    }


def mix_target(pack, root=None, top=3):
    """主题包 → **混音目标**（从项目真实音频画像 `refs/*.json` 里挑，连同理由写进包）

    为什么必须是"真实音频画像"而不是 MIDI 渲染：技能里定死了 —— **MIDI 渲染出来的音频和
    真实录音是两回事**，拿它当频谱目标 = 把成品往"裸 GM 音色"上对齐。项目里真正的目标是
    游戏自己的 BGM 画像（`profile_ref.py` 从真实录音扒的频谱/宽度/响度）。
    ⚠ **混音目标不是模板依据**：模板只来自 `refs/midi2/` 或网络权威数据；这里选的是
    "对齐到哪个混音"，两者分工不同（一个管怎么写，一个管混成什么样）。

    判据（权重在 `MIX_W`，逐项记分写进包里，可复核）：
      · **速度 0.55**：差得多的目标会让成绩单一路报"速度不一致"，对齐也没意义
      · **打击感 0.30**：主题 `perc_style` 与画像"高频段电平 + 高频起音占比"要同类
      · **调式 0.15**：大调主题别配小调目标（情绪会拧）
    **人声主导画像直接排除**（`character != 'instrumental'`）：人声混音的母带特征不是
    我们的目标（技能 §3.4：该对齐的是 160–10kHz 那一层，别照抄母带特征）。
    """
    root = root or ROOT
    med = (pack.get('bpm') or {}).get('median') or 120.0
    want_perc = PERC_TARGET.get((pack.get('rhythm') or {}).get('perc_style', 'light'), 0.5)
    want_mode = (pack.get('key') or {}).get('mode')
    rows = []
    for p in sorted(glob.glob(os.path.join(root, 'refs', '*.json'))):
        try:
            j = json.load(open(p, encoding='utf-8'))
        except Exception:                                  # noqa: BLE001
            continue
        if not j.get('bands') or not j.get('bpm'):
            continue
        name = j.get('name') or os.path.basename(p)[:-5]
        if (j.get('character') or 'instrumental') != 'instrumental':
            continue                                        # 人声主导：不是我们的目标
        pp = portrait_perc(j)
        pm = j.get('mode') or portrait_mode(j)
        dbpm = abs((j['bpm'] or 0) - med)
        s_bpm = max(0.0, 1.0 - dbpm / 30.0)                 # 差 30BPM 得 0 分
        s_perc = max(0.0, 1.0 - abs((pp if pp is not None else 0.5) - want_perc) / 0.5)
        s_mode = 1.0 if (not pm or not want_mode or pm == want_mode) else 0.0
        score = (MIX_W['bpm'] * s_bpm + MIX_W['perc'] * s_perc + MIX_W['mode'] * s_mode)
        why = 'BPM %.1f（差 %.1f，%.2f）· 打击感 %.2f vs 目标 %.2f（%.2f）· 调式 %s%s（%.2f）' % (
            j['bpm'], dbpm, s_bpm, pp if pp is not None else -1, want_perc, s_perc,
            pm or '?', '' if pm == want_mode else '≠主题', s_mode)
        rows.append({'ref': name, 'score': round(score, 3), 'bpm': j['bpm'],
                     'rms_db': j.get('rms_db'), 'width': j.get('width'),
                     'centroid': j.get('centroid'), 'perc': pp, 'mode': pm,
                     'structure': j.get('structure') or [],
                     'parts': {'bpm': round(s_bpm, 3), 'perc': round(s_perc, 3),
                               'mode': round(s_mode, 3)},
                     'why': why})
    rows.sort(key=lambda r: (-r['score'], r['ref']))
    if not rows:
        return {}
    best = rows[0]
    # **多方聚合**（用户口径："混音要参考权威音源，**也要多方参考**"）：
    # 合格的前 N 份一起当目标，逐维度取中位数 —— 单份画像的个性（偏亮/偏薄）不整体带进成品。
    # 门槛两条：绝对分 ≥ 0.45，且 ≥ 最高分 × `MIX_MEMBER_REL`（免得把"速度差 25BPM"
    # 这种勉强及格的参考也拉进来）；合格的不足 `MIX_MIN_MEMBERS` 份时退回"按分数取前 N"。
    members = [r for r in rows
               if r['score'] >= 0.45 and r['score'] >= best['score'] * MIX_MEMBER_REL][:6]
    if len(members) < MIX_MIN_MEMBERS:
        members = rows[:MIX_MIN_MEMBERS]
    agg = aggregate_refs(pack.get('theme') or 'theme', members, root=root)
    best = dict(best)
    best['structure'] = (agg or {}).get('structure') or best.get('structure') or []
    # **段间能量曲线**：目标画像的 `structure` = **每 8 小节一块**的响度（dB，`metrics.structure`
    # 口径）。技能里定死："像不像"主要来自**段间对比**，全曲一条直线 = 亮却闷。
    # 这里折成"相对均值"的起伏（均值为 0 → 不改变整体响度，只给段落对比）；
    # **平坦的曲线不给**（省得写一堆 0 偏移的假变化）。
    curve, struct_src = [], []
    st = [float(v) for v in (best.get('structure') or []) if isinstance(v, (int, float))]
    if len(st) >= 3:
        # **原始 structure 无论给不给曲线都要留**（它是诊断依据：能看出"目标本来就平"）。
        # 第一版把它和曲线一起放在门槛里 → 平坦目标的 structure 也丢了，报告里全变成 0。
        struct_src = [round(v, 1) for v in st]
        mean = sum(st) / len(st)
        dev = [round(v - mean, 2) for v in st]
        # 起伏门槛 **1.5dB**（不是"非零"）：目标本身只有 1dB 起伏时，我们的编配自带走 1dB
        # 起伏，再叠加曲线会过冲到 2dB（实测 gorgeous：目标 1.0 → 成品 2.0）——
        # "目标本来就没对比"的主题，**不写曲线才是对的**（别硬造）。
        if round(max(dev) - min(dev), 2) >= 1.5:
            curve = dev
    return {'ref': (agg or {}).get('name') or best['ref'],
            'score': best['score'], 'why': best['why'],
            'aggregate': bool(agg), 'members': (agg or {}).get('members') or [],
            'agg_profile': (agg or {}).get('name'),
            'weights': dict(MIX_W), 'perc_style_target': want_perc,
            'energy_curve_db': curve, 'structure_db': struct_src,
            'curve_note': ('段间响度起伏（相对均值，每块 8 小节，取自**多份参考聚合后**的 '
                           'structure 中位数）；空 = 目标本身平坦，别硬造对比'),
            'candidates': rows[:top],
            'not_templates': ('频谱对齐目标（真实录音画像，**多份聚合**）；模板依据见 templates '
                              '—— 两者分工不同，别混用')}


def win_db(wav, bpm, group=8):
    """每 `group` 小节的**浮点** dB（与画像 `structure` 同口径，但不取整）。

    为什么不直接用 `metrics.structure`：它 `round()` 成整数 dB —— 而段间起伏本身只有
    1~6dB，整数化以后 1dB 的差异就是 20% 的分辨率误差（标定第一版吃过这个亏）。
    """
    import numpy as np
    import metrics
    m, sr, _x = metrics.load(wav)
    n = int(group * (4 * 60.0 / bpm) * sr)
    out = []
    for i in range(0, max(0, len(m) - n + 1), n):
        seg = m[i:i + n]
        out.append(20 * np.log10(max(1e-12, float(np.sqrt((seg ** 2).mean())))))
    return out


def _spread(v):
    return round(max(v) - min(v), 2) if len(v) > 1 else 0.0


def _resample(curve, n):
    out = []
    for i in range(n):
        pos = (i / float(n - 1)) * (len(curve) - 1) if n > 1 else 0.0
        lo = int(pos)
        hi = min(len(curve) - 1, lo + 1)
        out.append(curve[lo] + (curve[hi] - curve[lo]) * (pos - lo))
    return out


def _corr_seq(a, b):
    """两个序列的相关系数（标定用；**别和 `_key_of` 的 `_corr(v, prof, tonic)` 混名** ——
    同名会静默覆盖，`--selftest` 当场炸（本轮就踩了））"""
    if len(a) < 3 or len(a) != len(b):
        return 0.0
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    da, db = [x - ma for x in a], [y - mb for y in b]
    na = sum(x * x for x in da) ** 0.5
    nb = sum(y * y for y in db) ** 0.5
    return round(sum(x * y for x, y in zip(da, db)) / (na * nb), 3) if na and nb else 0.0


def calibrate(theme, seeds=(5, 11), set_gain=False, keep=False):
    """标定段间曲线的阻尼系数：**多 seed 测量 + 默认只报不写**（可复现、不拿噪声当信号）。

    方法：每个 seed 出两首探针曲（`--energy-gain 0` / `1`，写在 `songs/_cal_<主题>_*`，
    `_` 开头不进曲库）→ `make_song --no-tune` 各渲染一次（**不开自动调参**：否则两版 EQ
    不同，A/B 不干净）→ 用 `win_db`（每 8 小节浮点 dB，与画像同口径）量成品段间起伏
    r0 / r1 → `k* = (目标 − r0)/(r1 − r0)`。

    ⚠ **为什么不自动写 `energy_gain`**（实测教训）：单 seed 标出的逐主题 k* 在换 seed 后
    **完全翻转**（mystery：seed5 逐主题误差 0.84dB / 全局 1.0 误差 0.14dB，seed11 正好相反）。
    多 seed 平均后结论是**全局 `ENERGY_GAIN = 1.0` 更贴**（平均 |误差| 0.28dB vs 逐主题 0.50dB）——
    逐主题系数是在拟合单次生成的随机性。所以这里默认**只写证据**（`mix_target.calibration`），
    要用逐主题系数得显式 `--set-gain`（且请先看 evidence 里各 seed 的一致性）。
    跑完删掉探针曲（`keep=True` 留着看）。
    """
    import subprocess
    import shutil
    if theme not in THEMES:
        raise SystemExit('未知主题 %s' % theme)
    pack = load_pack(theme)
    mt = pack.get('mix_target') or {}
    tgt = [float(v) for v in (mt.get('structure_db') or [])]
    if len(tgt) < 3:
        raise SystemExit('主题 %s 的目标画像没有 structure，无法标定' % theme)
    t_spread = _spread(tgt)
    rows = []
    for seed in seeds:
        res = {}
        for gain, tag in ((0.0, 'k0'), (1.0, 'k1')):
            name = '_cal_%s_s%d_%s' % (theme, seed, tag)
            d = os.path.join(ROOT, 'songs', name)
            subprocess.run([sys.executable, os.path.join(HERE, 'new_song.py'), name,
                            '--theme', theme, '--seed', str(seed), '--energy-gain', str(gain)],
                           cwd=ROOT)
            subprocess.run([sys.executable, os.path.join(HERE, 'make_song.py'), name,
                            '--no-tune'], cwd=ROOT)
            cfg = json.load(open(os.path.join(d, 'render.json'), encoding='utf-8'))
            j = json.load(open(os.path.join(d, 'song.json'), encoding='utf-8'))
            import scorecard
            import metrics as _mx
            ref = scorecard.load_ref(cfg['ref'])
            wav = os.path.join(d, cfg['out'] + '.wav')
            prof = _mx.profile(wav, j['bpm'])
            res[tag] = {'blocks': win_db(wav, j['bpm']),
                        'worst': round(max(abs(prof['bands'][k] - ref['bands'][k])
                                           for k in ref['bands']
                                           if not k.startswith('20-40')), 2)}
            if not keep:
                shutil.rmtree(d, ignore_errors=True)
        r0, r1 = _spread(res['k0']['blocks']), _spread(res['k1']['blocks'])
        span = r1 - r0
        k = round((t_spread - r0) / span, 2) if span > 0.3 else None
        b = res['k1']['blocks']
        n = len(b) or 1
        ours = [x - sum(b) / n for x in b]
        tdev = _resample([x - sum(tgt) / len(tgt) for x in tgt], n)
        rows.append({'seed': seed, 'r0_no_curve_db': r0, 'r1_full_curve_db': r1,
                     'k_star': k, 'shape_corr': _corr_seq(ours, tdev),
                     'worst_band_k0': res['k0']['worst'],
                     'worst_band_k1': res['k1']['worst']})
        print('  %s seed=%-3s 目标 %.2f | k0 %.2f → k1 %.2f | k*=%-5s | 形状相关 %-5s'
              ' | 最差频段 %.1f→%.1f'
              % (theme, seed, t_spread, r0, r1, k, rows[-1]['shape_corr'],
                 rows[-1]['worst_band_k0'], rows[-1]['worst_band_k1']))
    ks = [r['k_star'] for r in rows if r['k_star'] is not None]
    mean_k = round(sum(ks) / len(ks), 2) if ks else None
    spread_of_k = round(max(ks) - min(ks), 2) if len(ks) > 1 else 0.0
    mt['calibration'] = {
        'metric': 'win_db（每 8 小节的浮点 dB，与画像 structure 同口径）',
        'seeds': list(seeds), 'target_spread_db': t_spread, 'per_seed': rows,
        'k_star_mean': mean_k, 'k_star_spread': spread_of_k,
        'verdict': ('k* 跨 seed 不稳（跨度 %.2f）→ 不写逐主题系数，用全局 ENERGY_GAIN=1.0'
                    % spread_of_k) if (spread_of_k > 0.15 or mean_k is None or
                                       abs((mean_k or 1.0) - 1.0) < 0.15) else
                   ('k*=%.2f 跨 seed 稳定（跨度 %.2f）→ 可用 --set-gain 写入'
                    % (mean_k, spread_of_k)),
        'applied_gain': (mt.get('energy_gain') if not set_gain else round(mean_k or 1.0, 2)),
        'note': ('判据：目标起伏 vs 成品起伏（同口径）；k* 若跨 seed 不稳就是噪声，别写进包 —— '
                 '实测逐主题系数平均误差 0.50dB，全局 1.0 只有 0.28dB'),
        'tool': 'theme_pack.py --calibrate'}
    if set_gain:
        if mean_k is None:
            raise SystemExit('k* 测不出来（曲线没起作用），拒绝写 energy_gain')
        mt['energy_gain'] = round(max(0.6, min(1.0, mean_k)), 2)
    pack['mix_target'] = mt
    save(pack, dict(pack['melody']), out_dir=OUT)
    print('  %s：k* 均值 %s（跨 seed 跨度 %.2f）→ %s'
          % (theme, mean_k, spread_of_k, mt['calibration']['verdict']))
    return mt['calibration']


def validate_pack(pack, root=None):
    """主题包合规性校验 → 问题清单（空 = 合规）。**守卫与生成共用这一份判据**。

    查的都是"静默失效"类问题：模板数不够（拿一首当很多首）、来源不在白名单、
    模板不在模板库索引里、模板风格不属于该主题、画像本身缺字段/数值坏。
    """
    root = root or ROOT
    bad = []
    theme = pack.get('theme')
    if not theme:
        return ['主题包缺 theme 字段']
    if theme not in THEMES:
        bad.append('主题 %s 不在 THEMES 表里' % theme)
    th = THEMES.get(theme, {})
    tpls = pack.get('templates') or []
    min_n = int(pack.get('min_templates') or MIN_TEMPLATES)
    if len(tpls) < min_n:
        bad.append('模板只有 %d 首（主题 %s 要求 ≥%d）' % (len(tpls), theme, min_n))
    idx = {r['file']: r for r in lib_index(os.path.join(root, 'refs', 'midi2', '_index.json'))}
    src = lib_sources(os.path.join(root, 'refs', 'midi2', '_sources.json'))
    seen = set()
    for t in tpls:
        f = t.get('file')
        if not f:
            bad.append('模板项缺 file 字段')
            continue
        if f in seen:
            bad.append('模板 %s 重复出现（同一首不能顶两首）' % f)
        seen.add(f)
        # ① 来源先查：它**不依赖索引**（"文件在不在库里"与"来源权不权威"是两件事，
        #    先查索引会让非白名单来源被 `continue` 跳过 —— 判据自证实测踩到）
        prob = whitelist_problem(f, t.get('source') or source_of(f, src))
        if prob:
            bad.append(prob)
        row = idx.get(f)
        if row is None:
            bad.append('模板 %s 不在模板库索引 refs/midi2/_index.json 里' % f)
            continue
        # ② 索引一致性
        if t.get('md5') and row.get('md5') and t['md5'] != row['md5']:
            bad.append('模板 %s 的 md5 与库索引不一致（被替换过）' % f)
        if th.get('styles') and t.get('style') not in th['styles']:
            bad.append('模板 %s 的风格 %s 不属于主题 %s 的风格集合 %s'
                       % (f, t.get('style'), theme, '/'.join(th['styles'])))
    if th.get('engine') and pack.get('engine_style') not in song_engine.STYLES:
        bad.append('engine_style=%r 不是引擎预设（可选 %s）'
                   % (pack.get('engine_style'), '/'.join(song_engine.STYLES)))
    if not (0 < (pack.get('bpm') or {}).get('median', 0) <= 400):
        bad.append('bpm.median 不合理：%r' % (pack.get('bpm') or {}).get('median'))
    key = pack.get('key') or {}
    if key.get('tonic') not in NAMES:
        bad.append('key.tonic 不是音名：%r' % key.get('tonic'))
    if key.get('mode') not in ('major', 'minor'):
        bad.append('key.mode 不是 major/minor：%r' % key.get('mode'))
    harm = pack.get('harmony') or {}
    progs = harm.get('progressions') or []
    if not progs:
        bad.append('没有可用的和声进行（生成时无从下手）')
    for p in progs:
        if not p.get('symbols'):
            bad.append('进行 %s 没有绝对和弦符号（转调失败）' % p.get('romans'))
    mel = pack.get('melody') or {}
    for k in ('onset16_hist', 'dur16_hist', 'interval_hist', 'range', 'notes_per_bar'):
        if k not in mel:
            bad.append('旋律画像缺字段 %s' % k)
    if (mel.get('notes') or 0) < 40:
        bad.append('旋律画像只有 %s 个音，统计不可靠（换模板或补库）' % mel.get('notes'))
    # 混音目标层：必须是**真实音频画像**里存在的一首、且不是人声主导
    mt = pack.get('mix_target') or {}
    if not mt.get('ref'):
        bad.append('缺混音目标（mix_target.ref）—— 生成时无从决定"对齐到哪个混音"')
    else:
        rp = find_ref_file(str(mt['ref']), root=root)
        if not rp:
            bad.append('混音目标 %s 找不到（`refs/` 与 `refs/%s/` 都查过；画像被改名/删除 → '
                       '重跑 theme_pack.py）' % (mt['ref'], AGG_DIR))
        else:
            rj = json.load(open(rp, encoding='utf-8'))
            # **多方聚合**的目标：成员 ≥ `MIX_MIN_MEMBERS` 份、每份都有 `source`（可溯源）
            if rj.get('aggregate'):
                mem = rj.get('members') or []
                if len(mem) < MIX_MIN_MEMBERS:
                    bad.append('混音目标 %s 只聚合了 %d 份参考（要求 ≥%d —— "多方参考"）'
                               % (mt['ref'], len(mem), MIX_MIN_MEMBERS))
                nose = [m.get('ref') for m in mem if not (m.get('source') or {})]
                if nose:
                    bad.append('混音目标 %s 的成员缺 source（不可溯源）：%s'
                               % (mt['ref'], ', '.join(nose[:4])))
            if not rj.get('bands'):
                bad.append('混音目标 %s 缺 bands（不是频谱画像）' % mt['ref'])
            if (rj.get('character') or 'instrumental') != 'instrumental':
                bad.append('混音目标 %s 是人声主导画像（人声混音的母带特征不是我们的目标）'
                           % mt['ref'])
            med = (pack.get('bpm') or {}).get('median') or 0
            if rj.get('bpm') and med and abs(rj['bpm'] - med) > 30:
                bad.append('混音目标 %s 的速度 %.1f 与主题 %.1f 差 >30BPM（对齐没意义）'
                           % (mt['ref'], rj['bpm'], med))
    # 段间曲线的**按主题阻尼系数**（标定产物）：必须在合理区间，且写明来路
    eg = mt.get('energy_gain')
    if eg is not None:
        if not isinstance(eg, (int, float)) or isinstance(eg, bool) or not (0.0 <= eg <= 1.5):
            bad.append('mix_target.energy_gain=%r 不合理（应在 0~1.5）' % eg)
        elif not (mt.get('calibration') or {}).get('k_star'):
            bad.append('写了 energy_gain 却没有 calibration 依据（标定值必须可溯源）')
    if not (pack.get('rhythm') or {}).get('low16'):
        bad.append('节奏共识型缺失')
    # **基础声部不许被"角色缺失"关掉**：低音区有内容（起音 ≥2/小节）却把 bass 判成"关"，
    # 说明用的是"有没有独立贝斯轨"这种错证据 —— 那样的成品会整条低频塌掉
    # （实测 classic 主题：40–80Hz −33dB，而低音区起音 8.8/小节）。
    arr_off = set((pack.get('arrangement') or {}).get('arr_off') or [])
    low_dens = (pack.get('rhythm') or {}).get('low_density') or 0.0
    if 'bass' in arr_off and low_dens >= 2.0:
        bad.append('把基础声部 bass 关掉了，但低音区有内容（%.1f 起音/小节）—— '
                   '"没有贝斯轨"不等于"没有低音"' % low_dens)
    if not (pack.get('form') or {}).get('plan'):
        bad.append('曲式计划缺失')
    return bad


# ---------------------------------------------------------------- 摘要
def show(pack):
    print('=' * 74)
    print('主题包 %s（%s）  %d 首模板 · 源 %s'
          % (pack['theme'], pack.get('label', ''), pack['template_count'],
             ', '.join('%s×%d' % (k, v)
                       for k, v in (pack.get('source_kinds') or {}).items())))
    print('  风格 %s → 引擎预设 %s · 拍号 %s'
          % ('/'.join(pack['styles']), pack['engine_style'], pack['meter']))
    print('  速度 %.0f BPM（%.0f~%.0f） · 调 %s %s（置信 %.2f）'
          % (pack['bpm']['median'], pack['bpm']['p25'], pack['bpm']['p75'],
             pack['key']['tonic'],
             '大调' if pack['key']['mode'] == 'major' else '小调',
             pack['key']['confidence']))
    print('  ── 模板（%d 首：风格配额 + 风格内按速度均匀间隔）──' % len(pack['templates']))
    for t in pack['templates']:
        print('    %-46s %-12s %5.0fBPM %s' % (t['file'][-46:], t['style'],
                                               t['bpm'], t['timesig']))
    print('  ── 和声进行（跨模板统计，级数用大调参照系）──')
    print('    主进行（%s）：%s'
          % (pack['harmony'].get('primary_source') or '无',
             ' '.join(pack['harmony'].get('primary') or ['（无）'])))
    for p in (pack['harmony']['progressions'] or [])[:4]:
        print('    %-34s %d 次 / %d 首(%.0f%%)  → %s'
              % (' '.join(p['romans']), p['count'], len(p['templates']),
                 p['template_share'] * 100, ' '.join(p.get('symbols') or [])))
    if pack['harmony'].get('pool_progression'):
        print('    （转移链兜底：%s → %s）'
              % (' '.join(pack['harmony']['pool_progression_romans']),
                 ' '.join(pack['harmony']['pool_progression'])))
    print('  ── 节奏共识（16 分格 · ★ = 过半模板"每小节"该格有音）──')
    print('    低 %s   %.1f 音/小节' % (pack['rhythm']['low16'], pack['rhythm']['low_density']))
    print('    高 %s   %.1f 音/小节' % (pack['rhythm']['high16'], pack['rhythm']['high_density']))
    print('    音型 bass=%s（贝斯轨 %.1f 音/小节） perc=%s（鼓轨 %.1f 音/小节）'
          % (pack['rhythm']['bass_style'], pack['rhythm']['bass_density'],
             pack['rhythm']['perc_style'], pack['rhythm']['perc_density']))
    print('  ── 配器（角色 → 模板占比 / 音域）──')
    for r, sh in sorted(pack['arrangement']['roles'].items(), key=lambda kv: -kv[1]):
        rng = pack['arrangement']['role_range'].get(r) or ['-', '-']
        print('    %-8s %3.0f%%  音域 %s-%s' % (r, sh * 100, rng[0], rng[1]))
    mr_ = pack['melody']
    rng = mr_.get('range') or ['-', '-']
    print('  ── 旋律语言（%d 音，%d 首模板）── 音域 %s-%s · %.2f 音/小节 · 级进 %.0f%% · 正拍 %.0f%%'
          % (mr_.get('notes', 0), mr_.get('templates', 0), rng[0], rng[1],
             mr_.get('notes_per_bar', 0), mr_.get('stepwise_pct', 0),
             mr_.get('onbeat_pct', 0)))
    mt = pack.get('mix_target') or {}
    if mt.get('ref'):
        print('  ── 混音目标（对齐到哪个真实混音；**不是模板依据**）──')
        print('    %s（评分 %.2f）%s' % (mt['ref'], mt.get('score') or 0, mt.get('why') or ''))
        for c in (mt.get('candidates') or [])[1:3]:
            print('    备选 %s（%.2f）' % (c['ref'], c['score']))
    print('    → refs/themes/%s.json + %s_melody.json' % (pack['theme'], pack['theme']))


def pop_sources(pack):
    """主题包的来源构成（`{站点: 首数}`）—— 打印/复用时用"""
    return pack.get('source_kinds') or {}


# ---------------------------------------------------------------- 自测
def selftest():
    """聚合规则自测（不读模板库、不联网）"""
    ok = 0

    def eq(got, want, label):
        nonlocal ok
        if got != want:
            raise AssertionError('%s: %r != %r' % (label, got, want))
        ok += 1

    # ① 来源白名单：权威站点放行，其它一律拦下（"有 URL 就算"是漏的）
    eq(source_kind('https://bitmidi.com/uploads/1.mid'), 'bitmidi', 'bitmidi 域名')
    eq(source_kind('https://www.mutopiaproject.org/ftp/x.mid'), 'mutopiaproject',
       '含 www 的权威站点')
    eq(source_kind('http://evil.example.com/a.mid'), None, '非白名单站点必须判 None')
    eq(source_kind(''), None, '空来源必须判 None')
    eq(whitelist_problem('jazz/a.mid', 'https://bitmidi.com/uploads/1.mid'), None,
       '库内 + 权威来源 = 合规')
    if not whitelist_problem('jazz/a.mid', 'http://evil.example.com/a.mid'):
        raise AssertionError('非白名单来源必须报问题')
    if not whitelist_problem('../outside.mid', 'https://bitmidi.com/uploads/1.mid'):
        raise AssertionError('库外路径必须报问题')
    ok += 2

    # ② 和弦符号 → 罗马级数（转调不变）
    eq(roman_token(9, 'm', 9), 'i', 'a 小调主和弦 = i')
    # 级数记号用**大调参照系**（bIII/bVI/bVII）—— 它是"音级 → 记号"的一一映射，
    # 反解级数时不会因"大调还是小调"产生歧义（小调相对写法会让 bIII 与 III 撞车）
    eq(roman_token(5, '', 9), 'bVI', 'F 在 a 小调 = bVI（大调参照系）')
    eq(roman_token(0, '', 9), 'bIII', 'C 在 a 小调 = bIII')
    eq(roman_token(7, '7', 0), 'V7', 'G7 在 C 大调 = V7')
    eq(roman_token(9, 'm7', 9), 'i7', 'Am7 在 a 小调 = i7（m 不重复写）')
    eq(roman_token(11, 'm7b5', 0), 'viim7b5', 'Bm7b5 在 C 大调保留 b5 信息')
    eq(split_symbol('F#m7b5'), (6, 'm7b5'), 'F#m7b5 解析')
    eq(split_symbol('Bb'), (10, ''), '降号根音解析')
    eq(split_symbol('?'), (None, None), '认不出的符号返回 None')

    # ③ 进行窗口 → 主题调上的绝对符号（必须能被 parse_chord 认）
    sym = _abs_progression(['i', 'bVI', 'bIII', 'bVII'], 9, {9: 'm', 8: '', 3: '', 10: ''})
    eq(sym, ['Am', 'F', 'C', 'G'], '罗马级数 → 绝对符号（a 小调）')
    for s in (sym or []):
        r, _, want = st.parse_chord(s)
        if r is None or want is None:
            raise AssertionError('生成的符号 %s 过不了 parse_chord' % s)
    ok += 1
    eq(_abs_progression(['i', 'zzz'], 9, {}), None, '坏记号必须整体放弃（不半途拼）')
    eq(_abs_progression(['i7'], 9, {}), ['Am7'], '小写级数 + 7 = 小七和弦（别拼成 A7）')

    # ④ 节奏共识：过半模板有音才算 ★
    eq(_consensus(['★···★···', '★···★···', '·★··★···'], 8)[0], '★···★···',
       '共识型 = 过半')
    eq(_consensus(['★···', '···★'], 4)[0], '····', '平票不算共识')

    # ⑤ 调式相关法：音级分布要能判出调性（**关系大小调靠低音加权分开**）
    w = [0.0] * 12
    for d in MAJOR_SCALE:
        w[d] = 10.0
    w[0] += 30.0                       # 主音被强调（真实音乐里低音反复落在主音）
    t, m, c = _key_of(w)
    eq((NAMES[t], m), ('C', 'major'), 'Krumhansl 判 C 大调')
    eq(c > 0, True, '置信度应为正（最优明显优于次优）')
    w2 = [0.0] * 12                    # A 自然小调与 C 大调同音级集合 → 必须靠低音分开
    for d in MINOR_SCALE:
        w2[(d + 9) % 12] = 10.0
    w2[9] += 30.0
    t2, m2, _c2 = _key_of(w2)
    eq((NAMES[t2], m2), ('A', 'minor'), 'Krumhansl 判 a 小调（关系大小调不许互串）')

    # ⑥ 模板选取是否确定性 + 多样性（用假索引，不碰真库）
    fake = []
    for stl in THEMES['daily']['styles']:
        for i in range(6):
            fake.append({'file': '%s/x%d.mid' % (stl, i), 'style': stl, 'md5': '%s%d' % (stl, i),
                         'bpm': 60 + i * 12, 'timesig': [4, 4], 'bars': 32,
                         'note_count': 500})
    a = pick_templates('daily', min_n=8, target=9, index=fake)
    b = pick_templates('daily', min_n=8, target=9, index=fake)
    eq([r['file'] for r in a], [r['file'] for r in b], '同参数选取结果一致（确定性）')
    eq(len(a), 9, '取够目标首数')
    eq(sorted({r['style'] for r in a}), sorted(THEMES['daily']['styles']),
       '每个风格都要有代表（不是挑最像的）')
    for stl in THEMES['daily']['styles']:
        bs = [r['bpm'] for r in a if r['style'] == stl]
        eq(min(bs) <= 72 and max(bs) >= 108, True,
           '%s 的速度要铺开（实测取到 %s，首尾都该取到）' % (stl, sorted(bs)))

    # ⑦ 模板数不足必须报错（不许静默凑数）
    try:
        pick_templates('daily', min_n=8, target=9, index=fake[:4])
        raise AssertionError('模板不足时必须 SystemExit')
    except SystemExit:
        ok += 1

    # ⑧ 校验器抓得住坏包（守卫本身要坏得起来）
    good = {'theme': 'daily', 'min_templates': 8,
            'templates': [], 'bpm': {'median': 100}, 'key': {'tonic': 'C', 'mode': 'minor'},
            'harmony': {'progressions': [{'romans': ['i'], 'symbols': ['Cm']}]},
            'rhythm': {'low16': '★···'}, 'form': {'plan': [{'name': 'A'}]},
            'melody': {'onset16_hist': {}, 'dur16_hist': {}, 'interval_hist': {},
                       'range': [60, 80], 'notes_per_bar': 2, 'notes': 100},
            'engine_style': 'daily'}
    probs = validate_pack(good, root=ROOT)
    if not any('模板只有' in p for p in probs):
        raise AssertionError('模板数不足必须被抓：%s' % probs)
    ok += 1
    print('theme_pack 自测: %d 项通过' % ok)
    return 0


# ---------------------------------------------------------------- 入口
def main():
    argv = sys.argv[1:]
    if '--selftest' in argv:
        return selftest()
    if '--list-themes' in argv:
        print('可用主题（主题 → midi2 风格集合 → 引擎预设）：')
        for k in sorted(THEMES):
            th = THEMES[k]
            print('  %-10s %-6s %-34s %s'
                  % (k, th['label'], '/'.join(th['styles']), th['engine']))
        return 0
    themes = [a for a in argv if not a.startswith('--') and not a[0].isdigit()]
    min_n = int(argv[argv.index('--min') + 1]) if '--min' in argv else MIN_TEMPLATES
    target = int(argv[argv.index('--target') + 1]) if '--target' in argv else DEF_TEMPLATES
    seed = int(argv[argv.index('--seed') + 1]) if '--seed' in argv else 5
    if '--all' in argv:
        themes = sorted(THEMES)
    if not themes:
        print(__doc__)
        return 1
    # `--calibrate`：标定（多 seed 测量 → 只写证据；`--set-gain` 才写逐主题系数）
    if '--calibrate' in argv:
        seeds = tuple(int(x) for x in
                      (argv[argv.index('--seeds') + 1].split(',')
                       if '--seeds' in argv else ('5', '11')))
        for t in themes:
            calibrate(t, seeds=seeds, set_gain='--set-gain' in argv,
                      keep='--keep' in argv)
        return 0
    bad = 0
    for t in themes:
        if t not in THEMES:
            print('未知主题 %s（--list-themes 看可用主题）' % t)
            bad += 1
            continue
        pack, melp = build(t, min_n=min_n, target=target,
                           allow_fetch='--allow-fetch' in argv)
        probs = validate_pack(pack)
        pp, mpp = save(pack, melp)
        print('✓ %s（%d 首模板）→ %s'
              % (t, pack['template_count'], os.path.relpath(pp, ROOT)))
        if probs:
            print('  !! 校验问题 %d 条：%s' % (len(probs), '；'.join(probs[:3])))
            bad += 1
        if '--show' in argv:
            show(pack)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
