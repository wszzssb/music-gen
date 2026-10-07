# -*- coding: utf-8 -*-
"""把中文口语点歌要求解析成音乐生成引擎的结构化参数（本地规则，离线）。

什么时候用
----------
用户用大白话说"来一首欢快的钢琴曲，90秒，不要太吵""做一首悲伤的慢歌，弦乐"这类要求时，
由本工具把它翻成引擎能直接吃的字段（主题 key、乐器、时长、能量倍率、随机种子）。
纯标准库 + 本地词表，**不联网、不调用任何模型**：同一句话在同一版本下给同样的结果
（只有一处按 `random` 取值 —— 用户说"换一版/随机"时的 `seed`）。

怎么用
------
    python scripts/ask_parse.py --text "来一首欢快的钢琴曲，90秒，不要太吵"
    python scripts/ask_parse.py "做一首悲伤的慢歌，用弦乐，两分钟"      # 位置参数同样可用
    python scripts/ask_parse.py --selftest                            # 内置自检，全过打印 OK

`--text` 与位置参数都接受文本；两者同时给出时以 `--text` 为准。
正常输出是 stdout 上的**一行 JSON**（`ensure_ascii=False`）。

输出契约
--------
`parse(text)` 返回下面这个 dict，键名与键的个数按契约固定：

    {
      "theme": "cheerful",        # 15 个主题 key 之一，认不出时为 null
      "theme_label": "欢快",       # 主题中文名；theme 为 null 时也是 null
      "seed": null,               # int 或 null；说"换一版/随机"时给一个随机 int
      "energy_gain": 1.1,         # float，夹到 [0.8, 1.3]，默认 1.0
      "instrument": null,         # "piano" / "strings" / "ep" / null
      "seconds": 90,              # 用户提到的时长（秒，int）或 null
      "bars_hint": null,          # 由时长与速度推出的建议小节数（int）或 null
      "matched": [{"word": "欢快", "kind": "theme", "value": "cheerful"}],
      "notes": ["要「欢快」→ 主题 cheerful（模板风格 pop/latin/rock，引擎预设 dance）"],
      "unknown": ["不要太吵"]
    }

`matched[].kind` 只取 theme / instrument / tempo / length / energy / seed 六个值。
命中规则的取舍：**词更长/更具体优先**（所以「欢快」里的「快」不会被单独再算一次），
同一个词可以同时带两种身份（「欢快」既是主题 cheerful，也是能量上调）。

主题表以本文件顶部的 `THEMES` + `THEME_WORDS` 为单一真源（15 个 key，与主题画像包一一对应）。
`bars_hint` 用的假定速度来自主题的引擎预设（见 `PRESET_BPM`），用户若明确说了 BPM 则以用户为准。
"""

import argparse
import json
import random
import re
import sys
import traceback

__all__ = ['parse']

# ---------------------------------------------------------------------------
# 主题表（15 个 key）：中文名 / midi2 模板风格集合 / 引擎预设
# ---------------------------------------------------------------------------
THEMES = {
    'battle':    {'label': '战斗',     'styles': ['rock', 'game16', 'game32'], 'preset': 'dance'},
    'cheerful':  {'label': '欢快',     'styles': ['pop', 'latin', 'rock'], 'preset': 'dance'},
    'classic':   {'label': '古典庄重', 'styles': ['classical', 'baroque', 'public_domain'], 'preset': 'gorgeous'},
    'daily':     {'label': '日常',     'styles': ['pop', 'folk', 'anime'], 'preset': 'daily'},
    'folk_tale': {'label': '民谣叙事', 'styles': ['folk', 'blues', 'ballad'], 'preset': 'ballad'},
    'gorgeous':  {'label': '华丽',     'styles': ['film', 'romantic', 'baroque'], 'preset': 'gorgeous'},
    'lounge':    {'label': '酒馆爵士', 'styles': ['jazz', 'blues', 'pop'], 'preset': 'acoustic'},
    'mystery':   {'label': '神秘',     'styles': ['film', 'newage', 'classical'], 'preset': 'gorgeous'},
    'neon':      {'label': '霓虹电子', 'styles': ['electronic', 'chiptune', 'game32'], 'preset': 'dance'},
    'night':     {'label': '夜晚',     'styles': ['newage', 'jazz', 'electronic'], 'preset': 'daily'},
    'retro':     {'label': '复古游戏', 'styles': ['chiptune', 'game16', 'game32'], 'preset': 'dance'},
    'seaside':   {'label': '海边',     'styles': ['newage', 'folk', 'pop'], 'preset': 'acoustic'},
    'sorrow':    {'label': '悲伤',     'styles': ['ballad', 'romantic', 'classical'], 'preset': 'ballad'},
    'tender':    {'label': '温柔抒情', 'styles': ['ballad', 'romantic', 'pop'], 'preset': 'ballad'},
    'waltz':     {'label': '三拍圆舞', 'styles': ['classical', 'baroque', 'romantic', 'folk', 'public_domain'],
                  'preset': 'gorgeous'},
}

# 每个主题的中文触发词（含同义词、口语词）+ 少量常见英文词
THEME_WORDS = {
    'battle':    ['战斗', '激昂', '热血', '决战', '打斗', '对抗', '开战', '战斗曲', 'battle', 'fight'],
    'cheerful':  ['欢快', '活泼', '开心', '愉快', '明朗', '阳光', '喜庆', '轻快', 'happy', 'cheerful', 'upbeat'],
    'classic':   ['古典', '庄重', '巴洛克', '庄严', '古典乐', '教堂', '管风琴', '圣咏', 'classical', 'baroque'],
    'daily':     ['日常', '轻松', '悠闲', '惬意', '生活气息', '随性', '平淡', '小日子', 'daily', 'everyday'],
    'folk_tale': ['民谣', '叙事', '故事', '传说', '说书', '乡谣', '民歌', '叙事曲', 'folk', 'tale'],
    'gorgeous':  ['华丽', '宏大', '史诗', '电影感', '交响', '磅礴', '辉煌', '大气', 'gorgeous', 'epic', 'cinematic'],
    'lounge':    ['爵士', '酒馆', '慵懒', '蓝调', '酒吧', '沙哑', '小酒馆', '爵士乐', 'jazz', 'lounge', 'blues'],
    'mystery':   ['神秘', '悬疑', '诡异', '幽深', '未知', '探索', '谜题', '神秘感', 'mystery', 'mysterious'],
    'neon':      ['霓虹', '电子', '赛博', '合成器', '电音', '未来感', '夜店', '电子乐', 'neon', 'synthwave', 'techno'],
    'night':     ['夜晚', '深夜', '夜色', '午夜', '星空', '晚安', '夜里', '夜幕', 'night', 'midnight'],
    'retro':     ['复古', '像素', '红白机', '芯片音乐', '老游戏', '怀旧游戏', '街机', '8bit', 'retro', 'chiptune'],
    'seaside':   ['海边', '大海', '海风', '沙滩', '夏天', '夏日', '海岸', '海浪', 'seaside', 'beach', 'summer'],
    'sorrow':    ['悲伤', '忧伤', '难过', '伤感', '哀伤', '催泪', '凄凉', '悲凉', 'sad', 'sorrow'],
    'tender':    ['温柔', '抒情', '治愈', '温暖', '细腻', '深情', '柔和', '抒情曲', 'tender', 'gentle'],
    'waltz':     ['圆舞曲', '圆舞', '华尔兹', '三拍', '小步舞曲', '宫廷', '三拍子', '维也纳', 'waltz'],
}

# 乐器：值只能是这三种
INSTRUMENTS = {
    'piano':   {'label': '钢琴', 'words': ['钢琴曲', '纯钢琴', '钢琴', '三角钢琴', 'piano']},
    'strings': {'label': '弦乐组', 'words': ['弦乐组', '弦乐', '小提琴', '大提琴', '提琴', 'strings']},
    'ep':      {'label': '电钢琴', 'words': ['电钢琴', '电钢', 'rhodes', 'ep']},
}

# 速度/能量：方向词 -> energy_gain。上调用 1.15~1.2，下调用 0.85~0.9。
ENERGY_UP = {
    '快节奏': 1.2, '激烈': 1.2, '燃': 1.2, '带劲': 1.2, '动感': 1.2, '热烈': 1.2, '强劲': 1.2,
    '欢快': 1.15, '轻快': 1.15, '快歌': 1.15, '兴奋': 1.15, '快': 1.15,
}
ENERGY_DOWN = {
    '不要太吵': 0.85, '别太吵': 0.85, '舒缓': 0.85, '安静': 0.85, '轻柔': 0.85, '小声': 0.85,
    '轻一点': 0.9, '慢歌': 0.9, '慢一点': 0.9, '慢': 0.9,
}

# 换一版 / 随机
SEED_WORDS = ['换个版本', '换一版', '换一首', '再来一首', '再来一个', '重来一首',
              '重新生成', '随机种子', '随机的', '随机']

# 切句与"口水词"（做 unknown 时先把命中的词挖掉，再去掉这些词）
FRAGMENT_SPLIT = re.compile(r'[^，。、；：！？,.!?;:\s和与跟]+')
FILLERS = ['来一首', '来一个', '再来点', '做一首', '做一个', '写一首', '写一个', '生成一首', '生成一个',
           '背景音乐', '背景', '曲子', '歌曲', '音乐', '一首', '一个', '一段', '一条', '那种', '感觉',
           '风格', '大概', '左右', '大约', '稍微', '有点', '比较', '给我', '帮我', '我要', '我想',
           '想要', '谢谢', '麻烦', '一点', '一些', '还有', '然后', '换成', '来个', '话', '点',
           '的', '吧', '了', '呀', '啊', '呢', '嘛', '哦', '和', '与', '跟', '用', '来', '做', '写',
           '要', '想', '请', '把', '个', '首', '曲', '歌', '是', '很', '就', '在']

# 由引擎预设推的假定速度（只用来估 bars_hint；用户明说 BPM 时以用户为准）
PRESET_BPM = {'dance': 118, 'gorgeous': 76, 'daily': 100, 'ballad': 72, 'acoustic': 92}
DEFAULT_BPM = 100

KIND_ORDER = ('theme', 'instrument', 'tempo', 'length', 'energy', 'seed')

CONTRACT_KEYS = ('theme', 'theme_label', 'seed', 'energy_gain', 'instrument',
                 'seconds', 'bars_hint', 'matched', 'notes', 'unknown')

ENERGY_MIN, ENERGY_MAX = 0.8, 1.3


# ---------------------------------------------------------------------------
# 词表 -> 扫描索引
# ---------------------------------------------------------------------------
def _build_word_map():
    """把上面几张表压成 `词 -> [(kind, value), ...]`（同一个词可以带多种身份）。"""
    wmap = {}
    for key, words in THEME_WORDS.items():
        for w in words:
            wmap.setdefault(w.lower(), []).append(('theme', key))
    for value, meta in INSTRUMENTS.items():
        for w in meta['words']:
            wmap.setdefault(w.lower(), []).append(('instrument', value))
    for w, gain in ENERGY_UP.items():
        wmap.setdefault(w.lower(), []).append(('energy', ('up', gain)))
    for w, gain in ENERGY_DOWN.items():
        wmap.setdefault(w.lower(), []).append(('energy', ('down', gain)))
    for w in SEED_WORDS:
        wmap.setdefault(w.lower(), []).append(('seed', None))
    return wmap


WORD_MAP = _build_word_map()

# 纯 ASCII 的触发词（piano / ep / 8bit …）要卡词边界：免得 `ep` 从 `sleep`、`sad` 从 `saddle`
# 里被挖出来。中文触发词不受影响（中文没有西文那样的词边界）。
_ASCII_WORD = re.compile(r'[A-Za-z0-9]+$')
_ALNUM = re.compile(r'[A-Za-z0-9]')


def _boundary_ok(low, i, n):
    """纯 ASCII 触发词的前后都不接字母数字，才算独立一个词。"""
    if i > 0 and _ALNUM.match(low[i - 1]):
        return False
    j = i + n
    if j < len(low) and _ALNUM.match(low[j]):
        return False
    return True


# ---------------------------------------------------------------------------
# 时长与速度的取词（含中文数字）
# ---------------------------------------------------------------------------
_CN_DIGITS = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
              '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
_NUM = r'(\d+(?:\.\d+)?|[零〇一二三四五六七八九十两]+)'


def _num2float(s):
    """把 `90` / `1.5` / `一` / `两分`里的数字部分 / `三十` 转成 float；转不了给 None。"""
    if s is None or s == '':
        return None
    try:
        return float(s)
    except ValueError:
        pass
    if s == '十':
        return 10.0
    if '十' in s:
        head, _, tail = s.partition('十')
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        if head and head not in _CN_DIGITS:
            return None
        if tail and tail not in _CN_DIGITS:
            return None
        return float(tens * 10 + ones)
    digits = ''
    for ch in s:
        if ch not in _CN_DIGITS:
            return None
        digits += str(_CN_DIGITS[ch])
    return float(digits) if digits else None


# 时长：4 类写法各自一个正则，最后按"位置优先、同位置取更长"挑一条
_RE_LEN_MIN_SEC = re.compile(_NUM + r'\s*分(?:钟)?\s*(半|(?:\d{1,2}|[零〇一二三四五六七八九十]{1,3}))\s*秒')
_RE_LEN_MIN_HALF = re.compile(_NUM + r'\s*分\s*半(?:钟)?')
_RE_LEN_MINUTES = re.compile(_NUM + r'\s*分钟')
_RE_LEN_HALF_MIN = re.compile(r'半\s*分钟')
_RE_LEN_SECONDS = re.compile(_NUM + r'\s*(?:秒|s(?![A-Za-z0-9])|secs?(?![A-Za-z0-9])|seconds?(?![A-Za-z0-9]))',
                             re.IGNORECASE)
_RE_LEN_MINS_EN = re.compile(r'(\d+(?:\.\d+)?)\s*(?:mins?(?![A-Za-z0-9])|minutes?(?![A-Za-z0-9]))',
                             re.IGNORECASE)
_RE_LEN_HOURS = re.compile(_NUM + r'\s*(?:个?小时|hrs?(?![A-Za-z0-9])|hours?(?![A-Za-z0-9]))',
                           re.IGNORECASE)

# 速度：只认写明的 BPM
_RE_TEMPO_BPM = re.compile(r'(\d{2,3})\s*bpm', re.IGNORECASE)
_RE_TEMPO_CN = re.compile(r'速度\s*[:：]?\s*(\d{2,3})')
_RE_TEMPO_CN2 = re.compile(r'每分钟\s*(\d{2,3})\s*拍')

SECONDS_MIN, SECONDS_MAX = 1, 3600
BPM_MIN, BPM_MAX = 30, 300


def _find_lengths(text):
    """返回 [(pos, 词面, 秒数), ...]（按位置排序，重复位置只留更长的那条）。"""
    found = []

    def add(m, seconds):
        if seconds is None:
            return
        seconds = int(round(seconds))
        if SECONDS_MIN <= seconds <= SECONDS_MAX:
            found.append((m.start(), m.group(0), seconds))

    for m in _RE_LEN_MIN_SEC.finditer(text):
        head = _num2float(m.group(1))
        tail = 30.0 if m.group(2) == '半' else _num2float(m.group(2))
        if head is not None and tail is not None:
            add(m, head * 60 + tail)
    for m in _RE_LEN_MIN_HALF.finditer(text):
        head = _num2float(m.group(1))
        if head is not None:
            add(m, head * 60 + 30)
    for m in _RE_LEN_MINUTES.finditer(text):
        head = _num2float(m.group(1))
        if head is not None:
            add(m, head * 60)
    for m in _RE_LEN_HALF_MIN.finditer(text):
        add(m, 30)
    for m in _RE_LEN_SECONDS.finditer(text):
        v = _num2float(m.group(1))
        if v is not None:
            add(m, v)
    for m in _RE_LEN_MINS_EN.finditer(text):
        v = _num2float(m.group(1))
        if v is not None:
            add(m, v * 60)
    for m in _RE_LEN_HOURS.finditer(text):
        v = _num2float(m.group(1))
        if v is not None:
            add(m, v * 3600)

    found.sort(key=lambda t: (t[0], -len(t[1])))
    out, seen = [], set()
    for pos, word, seconds in found:
        if any(i in seen for i in range(pos, pos + len(word))):
            continue
        seen.update(range(pos, pos + len(word)))
        out.append((pos, word, seconds))
    return out


def _find_tempos(text):
    """返回 [(pos, 词面, BPM), ...]。"""
    found = []

    def add(m):
        v = int(m.group(1))
        if BPM_MIN <= v <= BPM_MAX:
            found.append((m.start(), m.group(0), v))

    for rx in (_RE_TEMPO_BPM, _RE_TEMPO_CN, _RE_TEMPO_CN2):
        for m in rx.finditer(text):
            add(m)
    found.sort(key=lambda t: (t[0], -len(t[1])))
    out, seen = [], set()
    for pos, word, bpm in found:
        if any(i in seen for i in range(pos, pos + len(word))):
            continue
        seen.update(range(pos, pos + len(word)))
        out.append((pos, word, bpm))
    return out


# ---------------------------------------------------------------------------
# 扫描：词表 + 时长 + 速度
# ---------------------------------------------------------------------------
def _scan(text):
    """一次扫完所有触发词。

    返回 `(hits, consumed)`：`hits` 是按位置排好的命中项，`consumed` 记录哪些字符
    已经被某个更长的词吃掉（做 unknown 时要跳过它们）。
    **词更长优先**：先扫长词并占位，短词若落在已占位的区间里就跳过 ——
    于是「欢快」里的「快」、「钢琴曲」里的「钢琴」都不会被重复算一次。
    """
    low = text.lower()
    consumed = [False] * len(low)
    hits = []

    for word in sorted(WORD_MAP, key=lambda w: (-len(w), w)):
        if not word:
            continue
        ascii_word = bool(_ASCII_WORD.match(word))
        start = 0
        while True:
            i = low.find(word, start)
            if i < 0:
                break
            start = i + 1
            if ascii_word and not _boundary_ok(low, i, len(word)):
                continue
            if any(consumed[i:i + len(word)]):
                continue
            for j in range(i, i + len(word)):
                consumed[j] = True
            for kind, value in WORD_MAP[word]:
                hits.append({'pos': i, 'word': low[i:i + len(word)], 'kind': kind, 'value': value})

    for pos, word, seconds in _find_lengths(low):
        if any(consumed[pos:pos + len(word)]):
            continue
        for j in range(pos, pos + len(word)):
            consumed[j] = True
        hits.append({'pos': pos, 'word': word, 'kind': 'length', 'value': seconds})

    for pos, word, bpm in _find_tempos(low):
        if any(consumed[pos:pos + len(word)]):
            continue
        for j in range(pos, pos + len(word)):
            consumed[j] = True
        hits.append({'pos': pos, 'word': word, 'kind': 'tempo', 'value': bpm})

    hits.sort(key=lambda h: (h['pos'], KIND_ORDER.index(h['kind'])))
    return hits, consumed


def _pick_longest(items):
    """同一类命中里挑"更长的词优先，同长取先出现的"。"""
    return max(items, key=lambda h: (len(h['word']), -h['pos'])) if items else None


def _find_unknown(text, consumed):
    """把命中的词挖掉、再去掉口水词，剩下的（≥2 字）算"没读懂的部分"。"""
    unknown = []
    for m in FRAGMENT_SPLIT.finditer(text):
        frag, base = m.group(0), m.start()
        left = ''.join(ch for i, ch in enumerate(frag)
                       if not (base + i < len(consumed) and consumed[base + i]))
        for f in sorted(FILLERS, key=len, reverse=True):
            left = left.replace(f, '')
        left = left.strip()
        if len(left) >= 2:
            unknown.append(left)
    return unknown


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def parse(text):
    """把一句中文口语要求解析成契约里的那个 dict（纯本地规则）。"""
    if text is None:
        text = ''
    elif not isinstance(text, str):
        text = str(text)
    text = text.strip()

    hits, consumed = _scan(text) if text else ([], [])

    notes = []

    # ---- 主题：词更长/更具体优先 ----
    theme_hits = [h for h in hits if h['kind'] == 'theme']
    theme, theme_hit = None, _pick_longest(theme_hits)
    if theme_hit:
        theme = theme_hit['value']
        others = [h for h in theme_hits if h['value'] != theme]
        notes.append('要「%s」→ 主题 %s（模板风格 %s，引擎预设 %s）'
                     % (theme_hit['word'], theme, '/'.join(THEMES[theme]['styles']),
                        THEMES[theme]['preset']))
        if others:
            notes.append('「%s」也像主题，按更长的「%s」取了 %s（另一条没有生效）'
                         % ('」「'.join(h['word'] for h in others), theme_hit['word'], theme))
    else:
        notes.append('文本里没有主题触发词 → theme 为 null（默认主题交给调用方定）')
    theme_label = THEMES[theme]['label'] if theme else None

    # ---- 乐器 ----
    inst_hits = [h for h in hits if h['kind'] == 'instrument']
    instrument, inst_hit = None, _pick_longest(inst_hits)
    if inst_hit:
        instrument = inst_hit['value']
        notes.append('「%s」→ 乐器 %s（%s）'
                     % (inst_hit['word'], instrument, INSTRUMENTS[instrument]['label']))
        others = [h for h in inst_hits if h['value'] != instrument]
        if others:
            notes.append('同时提到「%s」，乐器取更长的「%s」= %s'
                         % ('」「'.join(h['word'] for h in others), inst_hit['word'], instrument))

    # ---- 速度（写明的 BPM，只用于估小节数）----
    tempo_hits = sorted([h for h in hits if h['kind'] == 'tempo'], key=lambda h: h['pos'])
    bpm = None
    if tempo_hits:
        bpm = tempo_hits[0]['value']
        notes.append('「%s」→ 速度 %d BPM（用来估小节数）' % (tempo_hits[0]['word'], bpm))
        if len(tempo_hits) > 1:
            notes.append('文本里还有别处的速度说法「%s」，按先出现的取 %d BPM'
                         % (tempo_hits[1]['word'], bpm))

    # ---- 时长 ----
    len_hits = sorted([h for h in hits if h['kind'] == 'length'], key=lambda h: h['pos'])
    seconds = None
    if len_hits:
        seconds = len_hits[0]['value']
        notes.append('「%s」→ 时长 %d 秒' % (len_hits[0]['word'], seconds))
        if len(len_hits) > 1:
            notes.append('文本里还有别处的时长说法「%s」，按先出现的取 %d 秒'
                         % (len_hits[1]['word'], seconds))

    # ---- 能量方向：先出现的那个方向说了算 ----
    energy_hits = sorted([h for h in hits if h['kind'] == 'energy'], key=lambda h: h['pos'])
    energy_gain = 1.0
    if energy_hits:
        first = energy_hits[0]
        direction, gain = first['value']
        energy_gain = gain
        notes.append('「%s」→ 能量方向%s，energy_gain %.2f'
                     % (first['word'], '上调' if direction == 'up' else '下调', gain))
        opposite = [h for h in energy_hits if h['value'][0] != direction]
        if opposite:
            notes.append('「%s」与「%s」方向相反，按先出现的「%s」取%s（后一条没有生效）'
                         % (first['word'], '」「'.join(h['word'] for h in opposite), first['word'],
                            '上调' if direction == 'up' else '下调'))
    energy_gain = max(ENERGY_MIN, min(ENERGY_MAX, round(float(energy_gain), 3)))

    # ---- 换一版 / 随机种子 ----
    seed_hits = [h for h in hits if h['kind'] == 'seed']
    seed = None
    if seed_hits:
        seed = random.randint(1, 2 ** 31 - 1)
        for h in seed_hits:
            h['value'] = seed
        notes.append('「%s」→ 随机种子 seed=%d（换个版本）' % (seed_hits[0]['word'], seed))

    # ---- 建议小节数：时长 + 速度（用户没给 BPM 就用预设的假定速度）----
    bars_hint = None
    if seconds is not None:
        use_bpm = bpm or PRESET_BPM.get(THEMES[theme]['preset'] if theme else '', DEFAULT_BPM)
        beats = 3 if theme == 'waltz' else 4
        bars_hint = max(1, int(round(seconds * use_bpm / 60.0 / beats)))
        notes.append('%d 秒按 %d BPM、%d/4 拍算 → 建议约 %d 小节'
                     % (seconds, use_bpm, beats, bars_hint))

    matched = [{'word': h['word'], 'kind': h['kind'],
                # 能量项在 matched 里只报方向字符串，倍率放在 notes 里说明
                'value': h['value'][0] if h['kind'] == 'energy' else h['value']}
               for h in hits]
    unknown = _find_unknown(text, consumed) if text else []

    return {
        'theme': theme,
        'theme_label': theme_label,
        'seed': seed,
        'energy_gain': energy_gain,
        'instrument': instrument,
        'seconds': seconds,
        'bars_hint': bars_hint,
        'matched': matched,
        'notes': notes,
        'unknown': unknown,
    }


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def _try_parse(text):
    """跑一次 `parse` 并把异常也收下（自检用它，免得一个异常把整轮自检打断）。

    返回 `(结果, 错误说明)`：成功时错误说明为 None。
    """
    try:
        return parse(text), None
    except Exception as exc:            # 自检要报的是"哪条失败"，不是甩一个 traceback
        return None, '%s: %s' % (type(exc).__name__, exc)


def _selftest():
    """内置自检：返回失败说明的列表（空列表 = 全过）。"""
    bad = []

    def check(ok, msg):
        if not ok:
            bad.append(msg)

    def P(phrase, where):
        """跑一次 `parse`；抛异常就记一条**具名**失败并返回 None（后面的用例照跑）。"""
        r, err = _try_parse(phrase)
        if err is not None:
            bad.append('%s：%r 抛异常：%s' % (where, phrase, err))
        return r

    # ① 主题表与触发词：每个词都要能命中自己的主题
    check(len(THEMES) == 15, '主题表应有 15 个 key，实际 %d 个' % len(THEMES))
    check(set(THEMES) == set(THEME_WORDS), 'THEMES 与 THEME_WORDS 的 key 不一致')
    for key, meta in THEMES.items():
        for field in ('label', 'styles', 'preset'):
            check(field in meta, '主题 %s 缺字段 %s' % (key, field))
        cn = [w for w in THEME_WORDS.get(key, []) if re.search(r'[\u4e00-\u9fff]', w)]
        check(len(cn) >= 4, '主题 %s 的中文触发词少于 4 个：%s' % (key, cn))
        check(len(cn) <= 8, '主题 %s 的中文触发词多于 8 个：%s' % (key, cn))
        for w in THEME_WORDS.get(key, []):
            r = P(w, '主题触发词')
            if r is None:
                continue
            if r['theme'] != key:
                bad.append('主题触发词自命中失败：%r 期望 %s，实际 %s' % (w, key, r['theme']))
            elif r['theme_label'] != meta['label']:
                bad.append('触发词 %r 的 theme_label 期望 %s，实际 %s'
                           % (w, meta['label'], r['theme_label']))

    # ② 时长解析
    for phrase, expect in [('90秒', 90), ('90s', 90), ('90 秒', 90), ('一秒', 1),
                           ('一分钟', 60), ('一分半', 90), ('1分半', 90), ('两分半', 150),
                           ('2分半', 150), ('三分钟', 180), ('2分钟', 120), ('1.5分钟', 90),
                           ('1分30秒', 90), ('一分三十秒', 90), ('半分钟', 30), ('2min', 120),
                           ('1小时', 3600), ('九十秒', 90), ('十分钟', 600)]:
        r, err = _try_parse(phrase)
        if err is not None:
            bad.append('时长解析：%r 抛异常：%s' % (phrase, err))
            continue
        got = r['seconds']
        check(got == expect, '时长解析：%r 期望 %s 秒，实际 %s' % (phrase, expect, got))
    for phrase in ('来一首歌', '随便写点什么'):
        r = P(phrase, '时长误报')
        check(r is not None and r['seconds'] is None, '不该从 %r 里认出时长' % phrase)

    # ③ 乐器识别
    for value, meta in INSTRUMENTS.items():
        for w in meta['words']:
            r = P(w, '乐器识别')
            got = None if r is None else r['instrument']
            check(got == value, '乐器识别：%r 期望 %s，实际 %s' % (w, value, got))
    r = P('来一首歌', '乐器误报')
    check(r is not None and r['instrument'] is None, '无关文本不该给出乐器')
    # 词更长优先：电钢琴 -> ep，而不是被「电子」（霓虹）或「钢琴」抢走
    r = P('来一段电钢琴', '乐器优先级')
    if r is not None:
        check(r['instrument'] == 'ep' and r['theme'] is None,
              '「电钢琴」应只给 ep，实际 instrument=%s theme=%s' % (r['instrument'], r['theme']))

    # ④ 能量方向与夹取范围
    r = P('快节奏的鼓点', '能量方向')
    check(r is not None and r['energy_gain'] > 1.0, '「快节奏」应上调 energy_gain')
    r = P('舒缓一点', '能量方向')
    check(r is not None and r['energy_gain'] < 1.0, '「舒缓」应下调 energy_gain')
    r = P('别太吵', '能量方向')
    check(r is not None and r['energy_gain'] < 1.0, '「别太吵」应下调 energy_gain')
    r = P('快，但是要慢一点', '能量方向')
    check(r is not None and r['energy_gain'] > 1.0, '两个方向都出现时应取先出现的（快）')
    r = P('慢一点，不过要快', '能量方向')
    check(r is not None and r['energy_gain'] < 1.0, '两个方向都出现时应取先出现的（慢）')
    for phrase in ('快节奏', '舒缓', '不要太吵', '轻柔'):
        r = P(phrase, '能量夹取')
        if r is None:
            continue
        g = r['energy_gain']
        check(ENERGY_MIN <= g <= ENERGY_MAX and isinstance(g, float),
              '%r 的 energy_gain 越界或不是 float：%r' % (phrase, g))
    r = P('来一首歌', '能量默认')
    check(r is not None and r['energy_gain'] == 1.0, '没提速度/能量时应是默认 1.0')

    # ⑤ 完全无关的文本不崩、theme 为 null
    for phrase in ('今天天气不错，随便写点什么', '', '   ', '???', '12345'):
        r = P(phrase, '无关文本')
        if r is None:
            continue
        check(r['theme'] is None and r['theme_label'] is None,
              '无关文本 %r 的 theme 应为 null，实际 %s' % (phrase, r['theme']))
        check(r['matched'] == [], '无关文本 %r 不该有命中项' % phrase)

    # ⑥ 输出键齐全 + 类型/取值范围
    battery = ['来一首欢快的钢琴曲，90秒，不要太吵',
               '做一首悲伤的慢歌，用弦乐，两分钟',
               '换成三拍圆舞曲，换一版',
               '来一首欢快的钢琴曲',
               '今天天气不错', '']
    for phrase in battery:
        r = P(phrase, '契约检查')
        if r is None:
            continue
        check(tuple(r.keys()) == CONTRACT_KEYS,
              '%r 的键不齐/顺序不符：%s' % (phrase, list(r.keys())))
        check(r['theme'] is None or r['theme'] in THEMES, '%r 的 theme 不在 15 个 key 里' % phrase)
        check(r['theme_label'] is None or isinstance(r['theme_label'], str),
              '%r 的 theme_label 类型不对' % phrase)
        check(r['seed'] is None or isinstance(r['seed'], int), '%r 的 seed 类型不对' % phrase)
        check(isinstance(r['energy_gain'], float) and ENERGY_MIN <= r['energy_gain'] <= ENERGY_MAX,
              '%r 的 energy_gain 类型/范围不对：%r' % (phrase, r['energy_gain']))
        check(r['instrument'] in (None, 'piano', 'strings', 'ep'),
              '%r 的 instrument 取值越界：%r' % (phrase, r['instrument']))
        check(r['seconds'] is None or isinstance(r['seconds'], int), '%r 的 seconds 类型不对' % phrase)
        check(r['bars_hint'] is None or isinstance(r['bars_hint'], int), '%r 的 bars_hint 类型不对' % phrase)
        check((r['bars_hint'] is None) == (r['seconds'] is None),
              '%r 的 bars_hint 与 seconds 应同时为 null 或同时有值' % phrase)
        check(isinstance(r['notes'], list) and all(isinstance(s, str) for s in r['notes']),
              '%r 的 notes 应为字符串列表' % phrase)
        check(isinstance(r['unknown'], list) and all(isinstance(s, str) for s in r['unknown']),
              '%r 的 unknown 应为字符串列表' % phrase)
        for item in r['matched']:
            check(tuple(item.keys()) == ('word', 'kind', 'value'),
                  '%r 的 matched 元素键不对：%s' % (phrase, list(item.keys())))
            check(item['kind'] in KIND_ORDER, '%r 的 matched.kind 越界：%r' % (phrase, item['kind']))
            check(bool(item['word']), '%r 的 matched.word 为空' % phrase)

    # 具体的期望值（几条有代表性的句子）
    r = P('来一首欢快的钢琴曲，90秒，不要太吵', '示例句①')
    if r is not None:
        check(r['theme'] == 'cheerful' and r['instrument'] == 'piano' and r['seconds'] == 90,
              '示例句①的关键字段不对：%s' % r)
        check(r['energy_gain'] > 1.0, '示例句①先出现「欢快」，能量应取上调')
    r = P('做一首悲伤的慢歌，用弦乐，两分钟', '示例句②')
    if r is not None:
        check(r['theme'] == 'sorrow', '示例句②的主题应为 sorrow')
        check(r['instrument'] == 'strings', '示例句②的乐器应为 strings')
        check(r['seconds'] == 120, '示例句②的时长应为 120 秒')
    r = P('换成三拍圆舞曲，换一版', '示例句③')
    if r is not None:
        check(r['theme'] == 'waltz', '示例句③的主题应为 waltz')
        check(isinstance(r['seed'], int), '示例句③应给出随机 seed')
        check(any(m['kind'] == 'seed' and m['value'] == r['seed'] for m in r['matched']),
              '示例句③的 matched 里应有 seed 项且值与 seed 字段一致')
    r = P('90秒的曲子', 'matched 的 length 项')
    check(r is not None and any(m['kind'] == 'length' for m in r['matched']),
          'matched 里应有 length 项')
    # bars_hint 与拍号：圆舞曲按 3/4 算
    r_w = P('90秒的三拍圆舞曲', '小节数-圆舞曲')
    r_c = P('90秒的欢快曲子', '小节数-4/4')
    if r_w is not None and r_c is not None:
        check(r_w['bars_hint'] != r_c['bars_hint'],
              '三拍圆舞与 4/4 主题的建议小节数应不同')

    # ⑦ 回归：**带时长的整句**。2026-10-07 实测踩过一次 —— `_find_lengths` 用的
    #    `_NUM` 当时写成了非捕获组，于是句子里一出现「90秒」「两分钟」就在 `m.group(1)`
    #    上抛 `IndexError: no such group`，而只测「90秒」这种片段抓不到它（调用方先撞上）。
    #    所以这里钉两类写法各一条：阿拉伯数字 + 中文数字，且要断言"不抛异常"。
    regress = [('来一首欢快的钢琴曲，90秒，不要太吵', 90),      # 阿拉伯数字
               ('来一段纯钢琴，一分半', 90),                    # 中文数字
               ('换成三拍圆舞曲，三分钟', 180),                 # 中文数字 + 主题
               ('做一首悲伤的慢歌，用弦乐，两分钟', 120),
               ('随便写点什么', None)]                          # 没提时长
    for phrase, expect in regress:
        rr, err = _try_parse(phrase)
        if err is not None:
            bad.append('时长回归：整句 %r 抛异常：%s' % (phrase, err))
            continue
        check(rr['seconds'] == expect, '时长回归：整句 %r 期望 %s 秒，实际 %s'
              % (phrase, expect, rr['seconds']))
    # 带时长的整句里，主题与乐器也要照旧认出来（时长那条路径不该把它们挤掉）
    rr, err = _try_parse('来一首欢快的钢琴曲，90秒，不要太吵')
    if err is not None:
        bad.append('时长回归：示例句①抛异常：%s' % err)
    else:
        check((rr['theme'], rr['instrument'], rr['seconds']) == ('cheerful', 'piano', 90),
              '时长回归：示例句①要 theme=cheerful / instrument=piano / seconds=90，实际 %s'
              % {k: rr[k] for k in ('theme', 'instrument', 'seconds')})

    # 两次调用互不干扰（返回的是新对象）
    a = P('欢快的钢琴曲', '对象隔离')
    b = P('欢快的钢琴曲', '对象隔离')
    if a is not None and b is not None:
        a['matched'].append({'word': 'x', 'kind': 'theme', 'value': 'cheerful'})
        check(len(b['matched']) == len(a['matched']) - 1,
              'parse 的返回值被共享了（第二次调用受了污染）')
    return bad


def _setup_stdout():
    """把 stdout/stderr 切到 UTF-8，免掉 Windows GBK 控制台打印中文时崩掉。"""
    for stream in (getattr(sys, 'stdout', None), getattr(sys, 'stderr', None)):
        try:
            enc = (getattr(stream, 'encoding', '') or '').lower().replace('-', '')
            if enc == 'utf8':
                continue
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def main(argv=None):
    """命令行入口：`--text` / 位置参数 / `--selftest`。"""
    _setup_stdout()
    ap = argparse.ArgumentParser(
        prog='ask_parse.py',
        description=(__doc__ or '').strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('text_pos', nargs='?', default=None, help='中文口语要求（与 --text 等价）')
    ap.add_argument('--text', default=None, help='中文口语要求')
    ap.add_argument('--selftest', action='store_true', help='跑内置自检，全过打印 OK')
    args = ap.parse_args(argv)

    if args.selftest:
        try:
            fails = _selftest()
        except Exception as exc:        # 自检自己崩了也要给"哪条失败"和退出码 1
            traceback.print_exc()
            print('FAIL: 自检自身抛异常（%s: %s）' % (type(exc).__name__, exc))
            return 1
        if fails:
            for line in fails:
                print('FAIL: %s' % line)
            print('FAIL：%d 条自检没过' % len(fails))
            return 1
        print('OK')
        return 0

    text = args.text if args.text is not None else args.text_pos
    if text is None:
        print('用法：python scripts/ask_parse.py --text "来一首欢快的钢琴曲"', file=sys.stderr)
        print('      python scripts/ask_parse.py "来一首欢快的钢琴曲"', file=sys.stderr)
        print('      python scripts/ask_parse.py --selftest', file=sys.stderr)
        return 2

    print(json.dumps(parse(text), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）—— 与其它入口脚本同一写法
    sys.exit(main())
