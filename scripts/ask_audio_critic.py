#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""ask_audio_critic.py —— 让**本地 HF 音频大模型**逐段听曲子，输出带时间戳的问题清单 / 版本对照。

定位（先说清，免得当成验收门）：**它是"线索生成器"，不是判据**。实测边界见下，
完整数字见 `PITFALLS.md` 232/233。

═══ 四条硬约束（不遵守就白跑，全部实测过）═══
  ① **单次只吃 30 秒**：processor 是 `WhisperFeatureExtractor`（16k / 480000 样本），
     多喂的部分会被**静默截断** —— 模型以为听了整段。本模块的 `segment_bounds()`
     把每段夹在 `MAX_SEC` 内，`selftest` 有断言守着（曾用 `--segments 1` 把 223 秒当一段喂进去）。
  ② **参数名是 `audio=`（单数）**：`transformers 5.17` 的 `audios=` 被**静默忽略**，
     模型没听到音频却照样评价（坑 227）。本模块内置断言。
  ③ **必须 `greedy` 才可复现**：模型的 `generation_config.json` 是
     `do_sample: true / temperature 0.7 / top_p 0.5` —— 什么都不传时**每次答案都不同**
     （同段三次给出三个不同位置，坑 232）。本工具**默认 `do_sample=False`**，
     要多样性才加 `--sample`（那时请多次取多数，别用单次读数）。
  ④ **判定会"恒真"且对微扰敏感**：base 的 8 段**全部**被判"有问题"；改动别处引起的
     0.2dB 增益差就能让答案挪位置（坑 233）。→ 它能回答"哪一段可疑"，
     **判不了"这版比那版好吗"（价值判断）**；**但 `--compare` 同段同问能对比
     "原版 vs 当前版"的差异**（实测与用户原话、分轨能量三方一致，见 `AUDIO-CRITIC.md` §6.0）。
     判"更好"的判据是样本级音频差异（`diff_audio_ab.py`）+ 用户耳朵。
  ⑤ **模型报的时间是「整曲秒」，不是段内相对秒**：`Q_TMPL` 把整曲起止写进了 prompt，模型就用
     那个坐标系回答 —— 40 段真实输出里，7 个"两域不重叠"的段 **96/96 条**都落在整曲域、
     段内域 **0** 条（§8-3 原假设"要加回段起点"因此被推翻）。越界/没给时间的**单列、不硬映射**
     （`clock` = `abs`/`rel`/`ambiguous`/`out`/`notime`）。

═══ 环境（重依赖在函数内延迟 import，所以本模块能被无 torch 的自检 import）═══
  主 venv **没有** torch → 用 `.venv-ml`：
    <根>\.venv-ml\Scripts\python.exe scripts\ask_audio_critic.py <音频> --segments 8 --greedy
  CPU 上 decode 是**内存带宽**瓶颈（465ms/token ≈ 33GB/s）：加线程/缩短生成都没用，
  fp32 直接装不下。**要快就上 GPU 4bit**（实测 52.7s → 3.0s/段）：
    pip install --no-deps accelerate bitsandbytes && pip install --no-deps psutil
    ... --load-4bit      # 8GB 卡：4bit LM 进显存，音频编码器留 CPU
  ⚠ 量化会**改变判定** → 做 A/B 时全部版本必须同配置。
  ✅ **默认离线**（2026-09-25 用户要求，已落成代码）：脚本自己设 `HF_HUB_OFFLINE=1 /
     TRANSFORMERS_OFFLINE=1`，**照下面命令直接跑即可**；要联网加 `--online`
     （本机连不上 huggingface.co，会一路 ConnectTimeout 重试）。见 `_default_offline()`。

用法:
  python scripts\ask_audio_critic.py <音频> --start 55 --dur 28      # 单段
  python scripts\ask_audio_critic.py <音频> --segments 8 --json out.json
  python scripts\ask_audio_critic.py --compare a.ogg b.ogg c.ogg --segments 8 --json cmp.json
      # 同段同问扫描多个版本 → 输出"段 × 版本"矩阵，用于定位"改动落在哪、它有没有反应"
  python scripts\ask_audio_critic.py <音频> --segments 8 --load-4bit  # GPU 4bit
  python scripts\ask_audio_critic.py <音频> --segments 8 --load-4bit --sample --repeat 5
      # 同段采样 5 次 → **只留稳定复现的线索**（实测：74 条独立线索里只有 **7 条(9%)**
      #   在 5 次里出现 ≥3 次，而 55 条只出现过一次 = 噪声）。
      #   桶宽 `--band`（默认 4 秒）· 门槛 `--min-hits`（默认过半）
  # 扫描结束打印两样：**整曲时间轴上的指控清单**（可直接跳到问题点）+ **稳定线索表**
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import cli_utf8 as _cu
    _cu.setup()
except Exception:                                     # noqa: BLE001
    pass


def _default_offline():
    """**默认离线**（用户 2026-09-25："千问调成默认离线"）。

    为什么必须落成代码而不是写进文档：本机连不上 `huggingface.co`，而
    `AutoProcessor.from_pretrained` / `from_pretrained` 会**先联网 HEAD 每个文件**，
    每个文件重试 5 次 × 2 轮 → 实测一路 `httpx.ConnectTimeout [WinError 10060]`
    直到抛异常，**现象是"模型加载崩了"**（本轮真踩：照 §4 的命令直接跑就中）。
    文档里早就写着"离线跑"，但**照文档跑就是联网** —— 这类"文档写了、代码没接"必须落码。

    ⚠ 必须在 `import transformers` **之前**设置（它在 import 时读这两个变量）；
      而且本模块的重依赖是**函数内延迟 import**，所以放模块级就够早。
    要临时联网：`--online`，或显式 `set HF_HUB_OFFLINE=0`（`setdefault` 不覆盖你设的值）。
    """
    if os.environ.get('DSH_AUDIO_CRITIC_ONLINE') == '1':
        return
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')


_default_offline()

MODEL_ID = 'Qwen/Qwen2-Audio-7B-Instruct'
MAX_SEC = 30.0                     # WhisperFeatureExtractor 的硬上限（多喂 = 静默截断，坑 227）

Q_TMPL = (
    '请非常仔细地听这段音乐（第 {a:.0f} 秒到第 {b:.0f} 秒）。'
    '列出你听到的**所有**问题，越详细越好。每个问题请给三点：'
    '① 问题是什么（例如：音符被不自然地延长 / 和弦过渡生硬 / 某声部被盖住 / '
    '节奏不稳 / 音色发假 / 旋律突然跳）；'
    '② 问题大约出现在这段的第几秒；'
    '③ 涉及哪个声部或乐器（钢琴 / 低音 / 打击乐 / 旋律 / 伴奏 / 其他）。'
    '只列问题，不要评价优点。若这段确实没问题，就回答"没问题"。'
)

# 问题类别 → 正则（用于把回答归成可比对的类别）
CATS = {
    '延长': r'延长|拖长|时值过长|长音',
    '生硬': r'生硬|突兀|不自然.*(过渡|连接)|过渡',
    # ⚠ 2026-09-21 扩口径：模型高频用"模糊/压抑"说低音被盖，原正则（盖住|被掩|淹没|遮）抽不到
    '盖住': r'盖住|被掩|淹没|遮|模糊|压抑|听不清|覆盖',
    '跳': r'突然(下降|跳|上升)|大跳|跳进',
    '节奏': r'节奏(不稳|混乱|突变)|忽快忽慢|停止|加速',
    '音色': r'音色(发假|怪异)|听起来假|电子|尖锐|刺耳',
    '噪音': r'噪音|杂音|底噪',
    '音量': r'音量(不稳|忽大忽小)|忽大忽小|太响|太轻',
    # ⚠ 2026-09-21 新增：模型真会报"薄弱/无趣/缺乏层次"，原来落到"其它"= 白丢信息
    '薄弱': r'薄弱|无趣|单调|平淡|空洞|缺乏(层次|情感|变化)',
    # ⚠ 2026-09-21 新增：模型高频用"不连贯/时断时续"说旋律（10/125 条原本落"其它"）
    '断续': r'不连贯|时断时续|断断续续|不连续|断裂',
}

# 声部 → 正则（回答里"涉及哪个声部"那一栏）
PARTS = {    '低音': r'低音|贝斯|bass',
    '钢琴': r'钢琴|piano',
    '打击乐': r'打击|鼓|perc',
    '旋律': r'旋律|主奏|lead',
    '伴奏': r'伴奏|和声|背景|垫',
}


# 模型说"没问题"的几种说法。⚠ 自定义问法（如"有没有卡顿"）下它会答"流畅，没有卡顿"——
# 只认"没问题"三个字会把这种回答判成"其它"（看着像没结论）。
NO_PROBLEM = re.compile(r'没问题|流畅[，,]\s*没有卡顿|没有(明显)?问题|未发现(明显)?问题')


def problem_cats(ans):
    """从回答里抽问题类别（返回集合）。"""
    return {k for k, pat in CATS.items() if re.search(pat, ans or '')}


# ═══════════════════════════════════════════════════════════════════════════
# 指控解析层（§8-3 时间映射 / §8-2 多数表决 的公共底座）
# ═══════════════════════════════════════════════════════════════════════════
#
# **时间口径：文档原来写的是"段内相对秒"，实测是错的**（2026-09-21 量）：
# 拿 40 段真实输出（`_tmp/music-critic/qwen_compare_scan_4bit.json`）逐条判落点 ——
# 段 3 的 prompt 给的是"第 55.8 秒到第 83.8 秒"，模型报的是 `56~84`，
# 7 个"两域不重叠"的段（段 2~8，段起点 27.9 秒 > 段长 27.9 秒）里
# **96/96 条全部落在整曲域，段内域 0 条**。
# 原因也清楚：`Q_TMPL` 把整曲起止秒写进了 prompt，模型就用了那个坐标系。
# → 所以 `to_abs()` 的**第 1 判据是"落在段区间内 = 整曲秒"**，
#   "段内相对秒"只作为兜底分支留着（换个 prompt / 换模型就可能变，不能删）。
#
# ⚠ 这层只做**解析与映射**，不设任何阈值 —— 报出来的数字全是模型自己说的。
CLOCK_TOL = 0.6                    # 段边界容差：模型会把 83.8 的段末报成 84
_CLAIM_SPLIT = re.compile(r'[\n；;。！？!?]')
RE_RANGE = re.compile(
    r'第?\s*(\d+(?:\.\d+)?)\s*秒?\s*(?:至|到|~|～|—|–|-)\s*第?\s*(\d+(?:\.\d+)?)\s*秒')
RE_POINT = re.compile(r'第?\s*(\d+(?:\.\d+)?)\s*秒')
WORD_HEAD = re.compile(r'开头|起始|一开始|最初|前几秒')
WORD_TAIL = re.compile(r'结尾|末尾|最后|结束前')
# 模型的**元评论 / 免责声明** —— 不是音乐问题，但会被当成指控混进清单去计票
# （实测原话：`*注：由于音频文件时长不足，无法确定是否存在问题1、2在第112秒至第140秒之间`）。
META = re.compile(r'无法确定|时长不足|仅供参考|并非详尽|不是详尽|'
                  r'仅(是|为)(根据|提供)|以上(是|为)(分析|对)|希望对你有帮助|这仅是')


def to_abs(v, start, dur, tol=CLOCK_TOL):
    """把模型报的一个时间值映射到**整曲秒**。返回 `(绝对秒 | None, 口径标签)`。

    口径标签（`clock`）：
      · `abs`       —— 报的就是整曲秒（实测主口径）
      · `rel`       —— 落在"段内相对秒"域、却落不进段区间 → 加回段起点
      · `ambiguous` —— 两个域都容得下（段起点 ≈ 0 或段很短时会有 1 秒级的模糊带）
      · `out`       —— 两个域都不落（越界）→ **不硬映射**，返回 None（可疑，如实标出来）
    """
    end = start + dur
    in_abs = start - tol <= v <= end + tol
    in_rel = 0.0 <= v <= dur + tol
    if in_abs and in_rel:
        # 段起点≈0 时两种口径给出同一个绝对秒，没有歧义；否则带内确实分不清
        return v, ('abs' if start <= tol else 'ambiguous')
    if in_abs:
        return v, 'abs'
    if in_rel:
        return start + v, 'rel'
    return None, 'out'


def _piece_times(text):
    """抽一个分句里的时间表达 → [(a, b)]（区间取两端，单点 a==b）。"""
    out = []
    for m in RE_RANGE.finditer(text):
        a, b = float(m.group(1)), float(m.group(2))
        out.append((min(a, b), max(a, b)))
    if out:
        return out                       # 有区间就不再看单点（否则区间两端会被各抽一次）
    return [(float(m.group(1)), float(m.group(1))) for m in RE_POINT.finditer(text)]


def parse_claims(ans, start, end):
    """把一段回答拆成**指控**列表（每条 = 一个可分句 + 类别 + 声部 + 整曲时间）。

    为什么要拆：`--compare` 的判定矩阵把整段归成一个标签，用户拿不到"哪一秒出了什么"。
    分句按 `换行 / ；/ ;` 切 —— 模型两种写法都出现过（一行一条 / 一行多条用；分隔）。

    返回 `[{cat, cats, parts, t, clock, raw_span, text}, ...]`：
      · `t` = 整曲绝对秒（`None` = 模型没给时间或越界）
      · `clock` = `abs` / `rel` / `ambiguous` / `out` / `notime`
    既没有类别、也没有时间的分句**不算指控**（"以下是详细信息""有问题：6个"这类表头）。
    """
    out = []
    for piece in _CLAIM_SPLIT.split(ans or ''):
        s = piece.strip().strip('。.').strip()
        if not s or re.fullmatch(r'没问题', s):
            continue
        if META.search(s):
            continue                              # 元评论/免责声明，不是音乐问题
        cats = sorted(k for k, pat in CATS.items() if re.search(pat, s))
        parts = sorted(k for k, pat in PARTS.items() if re.search(pat, s))
        times = _piece_times(s)
        if not cats and not times:
            continue
        dur = max(0.0, float(end) - float(start))
        if times:
            a, b = times[0]
            t, clock = to_abs(a, start, dur)
            raw_span = (a, b)
        elif WORD_HEAD.search(s):
            t, clock, raw_span = float(start), 'word', None
        elif WORD_TAIL.search(s):
            t, clock, raw_span = float(end), 'word', None
        else:
            t, clock, raw_span = None, 'notime', None
        out.append({'cat': '/'.join(cats) if cats else '其它', 'cats': cats,
                    'parts': parts, 't': t, 'clock': clock,
                    'raw_span': raw_span, 'text': s})
    return out


def seg_claims(row):
    """一行扫描结果 → 指控列表（`row` 要有 `answer` / `start` / `end`）。"""
    return parse_claims(row.get('answer'), row.get('start', 0.0), row.get('end', 0.0))


def timeline(rows):
    """把一次扫描的所有段合成**整曲时间轴上的清单**（按绝对秒排序）。

    §8-3 的产出就是这个：用户听曲子时对着的是**整曲时间轴**，而工具原来只给
    "段N [55.8~83.8]" + 一段自然语言，还得自己找哪一秒。

    返回 `(located, unlocated)`：
      · `located`   = 有时间、且没越界的指控（按整曲秒排序，可直接跳过去）
      · `unlocated` = **没给时间 / 越界**的指控 —— 不许丢，也不许瞎安一个时间，
                      单独列出来并标原因（`notime` / `out`）
    """
    located, unlocated = [], []
    for r in rows:
        for cl in (r.get('claims') if r.get('claims') is not None else seg_claims(r)):
            item = dict(cl)
            item['seg'] = r.get('seg')
            item['seg_start'] = r.get('start')
            item['seg_end'] = r.get('end')
            (located if cl['t'] is not None else unlocated).append(item)
    located.sort(key=lambda x: (x['t'], x['seg'] or 0))
    return located, unlocated


def _claim_key(cl, band):
    """指控 → 可计票的键：`(类别, 时间桶)`。

    ⚠ **越界/没给时间的指控不能全塞进同一个桶**（2026-09-21 用真采样数据踩到）：
    实测模型在同一段里会报**完全不同**的越界位置 —— 段 3（55.8~83.8 秒）在 5 次采样里
    分别报了 `29.46 秒`、`240–249 秒`（后者**超过整曲 223.4 秒**）。若都归 `None` 桶，
    这些互不相干的位置就会**合起来凑够票数 → 假稳定**（这正是本过滤器要防的东西）。
    所以：有整曲秒 → 按它分桶；越界但模型给了数 → **按它报的那个数**分桶（保留位置）；
    完全没给时间（`notime`）→ 才归 `None`。
    """
    if cl['t'] is not None:
        return (cl['cat'], int(cl['t'] // band))
    rs = cl.get('raw_span')
    if rs:
        return (cl['cat'], int(rs[0] // band))
    return (cl['cat'], None)


def vote(claim_lists, band=4.0, min_hits=None):
    """**多次采样取多数**（§8-2）：N 份指控列表 → 只留"稳定出现"的那些。

    为什么需要：模型默认采样是 `do_sample: true`（`generation_config` 里写死），
    单次读数就是噪声（坑 232）。`--sample` 跑 N 次后，同一处指控会在 N 次里出现几次 ——
    **只有稳定复现的才算线索**，这条既是过滤器，也顺带量出了它自身的不确定性。

    计票口径：
      · 键 = `(类别, 时间桶)`，桶宽 `band` 秒（模型报的时间粗：75/110 条是**整数秒**，
        跨采样还会漂几秒，所以必须按桶聚类，不能按精确值）。
      · **每次采样对同一键最多计 1 票**（模型会在一次回答里把同一处报两遍）。
      · 默认门槛 = 过半（`ceil(N/2)`）。

    返回 `(rows, min_hits)`，`rows` 每项带 `hits` / `n` / `stable`，按"稳定优先 + 时间"排序。
    """
    n = len(claim_lists)
    if n == 0:
        return [], 0
    # `--min-hits 0`（argparse 的默认值）与"不传"同义 = 过半 ——
    # ⚠ 踩过：直接 `int(min_hits)` 会把默认的 0 当成**门槛 0**，于是 1/5、2/5 全被判成"稳定"，
    #   而打印的表头还写着"门槛 3 票"（标题与行为不一致，等于过滤器失效）。
    min_hits = (n + 1) // 2 if not min_hits else int(min_hits)
    acc = {}
    for ci, cls in enumerate(claim_lists):
        for cl in cls:
            k = _claim_key(cl, band)
            e = acc.setdefault(k, {'cat': cl['cat'], 'bucket': k[1], 'runs': set(),
                                   'parts': set(), 'texts': [], 't': cl['t'],
                                   'clock': cl['clock']})
            e['runs'].add(ci + 1)                    # set → 同一次采样重复报只算 1 票
            e['parts'] |= set(cl['parts'])
            e['texts'].append(cl['text'])
    rows = []
    for e in acc.values():
        hits = len(e['runs'])
        rows.append({'cat': e['cat'], 'bucket': e['bucket'], 'hits': hits, 'n': n,
                     'stable': hits >= min_hits, 'parts': sorted(e['parts']),
                     'runs': sorted(e['runs']), 't': e['t'], 'clock': e['clock'],
                     'text': e['texts'][0]})
    rows.sort(key=lambda r: (not r['stable'],
                             r['bucket'] if r['bucket'] is not None else 10 ** 6,
                             -r['hits']))
    return rows, min_hits


def vote_all(rows, band=4.0, min_hits=None):
    """按**段**分别表决（同一段才可比），再拼成整曲清单。

    ⚠ 必须分段表决：不同段的绝对秒差着几十秒，混在一张票表里"时间桶"就没意义了。
    """
    out = []
    for r in rows:
        answers = r.get('answers') or [r.get('answer') or '']
        lists = [parse_claims(a, r.get('start', 0.0), r.get('end', 0.0)) for a in answers]
        voted, mh = vote(lists, band=band, min_hits=min_hits)
        for v in voted:
            v['seg'] = r.get('seg')
            v['seg_start'] = r.get('start')
            v['seg_end'] = r.get('end')
            out.append(v)
    # 稳定优先 → 整曲时间 → 段号
    out.sort(key=lambda v: (not v['stable'],
                            (v['bucket'] * band) if v['bucket'] is not None else 10 ** 6,
                            v['seg'] or 0))
    return out


def verdict(ans):
    """这一段算"报了问题"还是"没问题"。

    ⚠ **口径坑（踩过，`selftest` 有断言守着）**：不能用 `'没问题' not in answer` 判 ——
    模型常输出"**有问题**，以下是详细信息：…（末尾）没问题。"，
    于是**报了问题的段会被判成"没问题"**（第一版矩阵整张表因此失真）。
    正确顺序：**先抽类别**，抽到就是报问题；抽不到再看有没有"没问题"。

    ⚠ 2026-09-21 扩口径：自定义问法下模型会答"**流畅，没有卡顿**"——
    原来只认"没问题"三个字，这种回答会落到"其它"（看着像"没结论"）。
    """
    c = problem_cats(ans)
    if c:
        return '/'.join(sorted(c))
    return '没问题' if NO_PROBLEM.search(ans or '') else '其它'


def segment_bounds(total, n, max_sec=None):
    """把 `total` 秒均分成 `n` 段，**每段不超过 `max_sec`**（超出会被静默截断）。

    返回 [(start, dur), ...]。`n` 太小时段长会被夹到 `max_sec`（而不是喂整曲）——
    这正是坑 227 的现场：`--segments 1` 曾把 223 秒整曲喂进去，模型只"听"到前 30 秒。

    ⚠ `max_sec` 默认写 `None`、在函数里取 `MAX_SEC`（**不要写成 `max_sec=MAX_SEC`**）：
    默认参数在定义时绑定，那样改 `MAX_SEC` 对调用方无效 —— 变异测试注入"上限被抬大"时
    会**打不进去**（假通过）。
    """
    max_sec = MAX_SEC if max_sec is None else max_sec
    n = max(1, int(n))
    step = float(total) / n
    out = []
    for i in range(n):
        st = i * step
        du = min(step, float(total) - st, max_sec)
        if du > 1.0:
            out.append((round(st, 3), round(du, 3)))
    return out


def single_bounds(total, start, dur, max_sec=None):
    """**单段**模式的区间：从 `start` 秒起、长 `dur` 秒（两端都夹在合法范围内）。

    ⚠ 2026-09-21 修的一个真 bug：`--start` 原来**声明了却从没被用过** ——
    单段模式永远从 0 开始，只把长度夹到 `--dur`。于是文档 §4 里
    `--start 55 --dur 28` 这个用法**一直是失效的**：你以为在问第 55 秒，
    实际问的是**文件开头**（实测就是拿它问"循环素材的接缝"，问到的却是 0~4 秒）。
    这类"参数收下了但没用"的错**不报错**，只会让你对着**另一段音频**下结论。
    """
    max_sec = MAX_SEC if max_sec is None else max_sec
    total = float(total)
    st = max(0.0, min(float(start), max(0.0, total - 1.0)))
    du = min(float(dur), max_sec, total - st)
    return [(round(st, 3), round(du, 3))]


def load_audio(path, start=0.0, dur=MAX_SEC, sr_target=16000):
    """读成 16kHz 单声道 float32（重采样优先 librosa，缺失时用线性插值兜底）。"""
    import numpy as np
    import soundfile as sf

    info = sf.info(path)
    y, sr = sf.read(path, dtype='float32', always_2d=True,
                    start=int(start * info.samplerate),
                    stop=int(min(info.duration, start + dur) * info.samplerate))
    y = y.mean(axis=1)
    if sr != sr_target:
        try:
            import librosa
            y = librosa.resample(y, orig_sr=sr, target_sr=sr_target)
        except Exception:                             # noqa: BLE001
            n = int(round(len(y) * sr_target / float(sr)))
            idx = np.linspace(0.0, len(y) - 1.0, n)
            y = np.interp(idx, np.arange(len(y)), y).astype('float32')
    return np.ascontiguousarray(y, dtype='float32')


class Critic:
    """懒加载的音频大模型评委（torch/transformers 都在这里才 import）。

    `load_4bit=True`：bitsandbytes NF4 4bit + 自定义 `device_map`（音频编码器留 CPU、
    LM 主体放卡）。8GB 显存装不下 bf16 的 15.5GB 权重，4bit 约 4.9GB 可以；
    实测 **52.7s → 3.0s/段**。⚠ 量化会改变判定，A/B 必须同配置。
    """

    def __init__(self, device='cpu', dtype_name='bfloat16', load_4bit=False, model_id=MODEL_ID):
        import torch
        from transformers import AutoProcessor, Qwen2AudioForConditionalGeneration

        dtype = {'bfloat16': torch.bfloat16, 'float16': torch.float16,
                 'float32': torch.float32}[dtype_name]
        t0 = time.time()
        print('[i] 加载 processor …')
        self.proc = AutoProcessor.from_pretrained(model_id)
        print('[i] 加载模型（%s%s）…' % ('cuda 4bit' if load_4bit else device,
                                        '' if load_4bit else ' / ' + dtype_name))
        if load_4bit:
            from transformers import BitsAndBytesConfig
            bnb = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type='nf4',
                bnb_4bit_compute_dtype=dtype, bnb_4bit_use_double_quant=True,
                llm_int8_skip_modules=['audio_tower'],
                llm_int8_enable_fp32_cpu_offload=True)
            # 直接 device_map='auto' 会让 accelerate 把部分模块判给 CPU，bnb 随即拒绝；
            # 且 8GB 卡塞不下"4bit LM + 音频编码器"，所以编码器显式留 CPU（decode 才是瓶颈）。
            self.model = Qwen2AudioForConditionalGeneration.from_pretrained(
                model_id, quantization_config=bnb,
                device_map={'audio_tower': 'cpu', 'multi_modal_projector': 0, '': 0})
            self.device = 'cuda'
        else:
            self.model = Qwen2AudioForConditionalGeneration.from_pretrained(
                model_id, torch_dtype=dtype)
            self.model.to(device)
            self.device = device
        self.model.eval()
        print('[i] 就绪，耗时 %.1f 秒' % (time.time() - t0))

    def ask(self, wav, question, max_new_tokens=400, **gen_kwargs):
        """问一段音频。**默认 greedy**（`do_sample=False` 由调用方给，见 `main`）。"""
        import torch

        conversation = [{'role': 'user', 'content': [
            {'type': 'audio', 'audio_url': 'placeholder'},      # 真音频走 audio=（单数！）
            {'type': 'text', 'text': question}]}]
        text = self.proc.apply_chat_template(conversation, add_generation_prompt=True,
                                             tokenize=False)
        inputs = self.proc(text=text, audio=[wav], return_tensors='pt', padding=True)
        keys = [k for k in inputs if hasattr(inputs[k], 'shape')]
        if not any(('feature' in k) or ('audio' in k) for k in keys):
            raise RuntimeError(
                '音频没进 processor！拿到的键只有 %s —— 检查参数名（应为 audio= 单数）。'
                '绝不能在没听到音频的情况下让模型评价。' % keys)
        inputs = {k: (v.to(self.device) if hasattr(v, 'to') else v)
                  for k, v in inputs.items()}
        t0 = time.time()
        with torch.no_grad():
            out = self.model.generate(**inputs, max_new_tokens=max_new_tokens, **gen_kwargs)
        new = out[:, inputs['input_ids'].size(1):]
        ans = self.proc.batch_decode(new, skip_special_tokens=True,
                                     clean_up_tokenization_spaces=False)[0]
        return {'answer': ans.strip(), 'seconds': time.time() - t0}


def _scan_one(c, audio, bounds, greedy, max_new_tokens, repeat=1, band=4.0, q_tmpl=None):
    """逐段问一遍（`repeat>1` 时同段问 N 次，供 §8-2 的多数表决用）。

    ⚠ `repeat>1` 只在 `--sample`（`do_sample=True`）下才有意义：greedy 下 N 次**逐字一致**，
    表决结果恒等于单次（不报错，但也证明不了稳定性）。
    """
    repeat = max(1, int(repeat))
    rows = []
    for i, (st, du) in enumerate(bounds, 1):
        wav = load_audio(audio, st, du)              # 音频只读一次，N 次复用
        q = (q_tmpl or Q_TMPL).format(a=st, b=st + du)
        answers, secs = [], []
        for _k in range(repeat):
            try:
                r = c.ask(wav, q, max_new_tokens=max_new_tokens,
                          **({} if not greedy else {'do_sample': False}))
                ans, sec = r['answer'], r['seconds']
            except Exception as e:                                # noqa: BLE001
                ans, sec = '（推理失败：%s）' % str(e)[:150], 0.0
            answers.append(ans)
            secs.append(sec)
        ans = answers[0]
        end = round(st + du, 3)
        extra = ''
        if repeat > 1:
            voted, mh = vote([parse_claims(a, st, end) for a in answers], band=band)
            extra = ' ×%d→稳定%d/%d(门槛%d)' % (repeat,
                                              sum(1 for v in voted if v['stable']),
                                              len(voted), mh)
        print('  %-18s 段%d/%d [%.1f~%.1f 秒] %5.1fs%s | %s'
              % (os.path.basename(audio)[:18], i, len(bounds), st, st + du, sum(secs),
                 extra, ans.replace('\n', ' ')[:110]))
        rows.append({'seg': i, 'start': st, 'end': end, 'answer': ans,
                     'answers': answers, 'seconds': sum(secs), 'verdict': verdict(ans),
                     'claims': parse_claims(ans, st, end),
                     'repeat': repeat, 'band': band})
    return rows


def _print_timeline(key, rows, band, repeat, min_hits):
    """打印某份音频的**整曲时间轴清单**（§8-3）／**稳定线索**（§8-2）。"""
    print('\n' + '-' * 84)
    if repeat > 1:
        voted = vote_all(rows, band=band, min_hits=min_hits)
        mh = min_hits or (repeat + 1) // 2
        stable = [v for v in voted if v['stable']]
        weak = [v for v in voted if not v['stable']]
        print('【稳定线索】%s —— %d 次采样 · 门槛 %d 票 · 时间桶 %.0f 秒'
              % (key, repeat, mh, band))
        if not stable:
            print('  （没有一条指控在多数采样里复现）')
        for v in stable:
            lo = v['bucket'] * band if v['bucket'] is not None else None
            where = ('%6.1f~%.0fs' % (lo, lo + band)) if lo is not None else '  未定位  '
            print('  ✓ %d/%d  [%s] %-6s %s%s  ← 段%s'
                  % (v['hits'], v['n'], where, v['cat'],
                     ('·%s ' % '/'.join(v['parts'])) if v['parts'] else '',
                     v['text'][:56], v['seg']))
        if weak:
            print('  ── 不稳定（弃，%d 条）：%s'
                  % (len(weak), '；'.join('%s(段%s %d/%d)'
                                        % (v['cat'], v['seg'], v['hits'], v['n'])
                                        for v in weak[:10])))
        return
    located, unlocated = timeline(rows)
    print('【指控清单·整曲时间轴】%s（%d 条可定位 / %d 条不可定位）'
          % (key, len(located), len(unlocated)))
    for c in located:
        flag = '' if c['clock'] == 'abs' else ' ⚠%s' % c['clock']
        # ⚠ 这里原来漏了 `flag` 的占位符（格式串 5 个、实参 6 个）→ **只要这一版有
        #   可定位指控就 `TypeError: not all arguments converted`**，整个时间轴清单在
        #   最后一步崩掉。"有指控的那版才崩"正是不易被发现的原因（2026-09-22 连踩两次）。
        print('  [%7.1fs] %-6s %s%s  ← 段%d%s'
              % (c['t'], c['cat'],
                 ('·%s ' % '/'.join(c['parts'])) if c['parts'] else '',
                 c['text'][:56], c['seg'], flag))
    for c in unlocated:
        if c['clock'] == 'out':
            why = '模型报的时间越界(原报 %s~%s 秒)' % c['raw_span']
        else:
            why = '模型没给时间'
        print('  [  未定位 ] %-6s %s  ← 段%d（%s）'
              % (c['cat'], c['text'][:46], c['seg'], why))


def main():
    ap = argparse.ArgumentParser(description='HF 音频大模型逐段听 → 问题清单 / 版本对照')
    ap.add_argument('audio', nargs='?', help='音频文件（单曲模式）')
    ap.add_argument('--compare', nargs='+', metavar='AUDIO',
                    help='多份音频做**同段同问**对照（段边界按第一份的总时长切）。'
                         '典型用法 = **原版 vs 当前版**：读它各自描述的乐器/角色/进出差异，'
                         '⚠ 差异 ≠ 优劣（它判不了"哪版更好"）')
    ap.add_argument('--start', type=float, default=0.0)
    ap.add_argument('--dur', type=float, default=MAX_SEC)
    ap.add_argument('--segments', type=int, default=0, help='>1 时均分扫描（每段自动夹到 30 秒内）')
    ap.add_argument('--sample', action='store_true',
                    help='回到采样（**不可复现**）；默认 greedy')
    ap.add_argument('--repeat', type=int, default=1,
                    help='每段问 N 次（**配 --sample 才有差异**）→ 走多数表决只留稳定线索')
    ap.add_argument('--band', type=float, default=4.0,
                    help='多数表决的时间桶宽（秒），默认 4（模型报的时间精确到秒级会漂）')
    ap.add_argument('--min-hits', type=int, default=0, help='稳定门槛票数，默认过半 ceil(N/2)')
    ap.add_argument('--load-4bit', action='store_true', help='GPU NF4 4bit（快约 17×）')
    ap.add_argument('--dtype', default='bfloat16',
                    choices=['bfloat16', 'float16', 'float32'])
    ap.add_argument('--max-new-tokens', type=int, default=400)
    ap.add_argument('--ask', default='',
                    help='自定义问题（支持 {a}/{b} 两个占位符 = 这段的整曲起止秒）；'
                         '默认用内置的"列出所有问题"模板。'
                         '例：--ask "这段听起来流畅吗？有没有卡顿、断裂、突然中断的地方？"')
    ap.add_argument('--json', default='')
    ap.add_argument('--online', action='store_true',
                    help='**一次性联网**（默认离线，见 `_default_offline`）；'
                         '本机通常连不上 huggingface.co，加了会一路 ConnectTimeout')
    a = ap.parse_args()

    if a.online:                       # 必须在构造 Critic（= import transformers）之前解开
        os.environ.pop('HF_HUB_OFFLINE', None)
        os.environ.pop('TRANSFORMERS_OFFLINE', None)
        print('[i] --online：已解开离线限制（本机连不上 huggingface.co 时会卡在重试）')

    files = a.compare or ([a.audio] if a.audio else [])
    if not files:
        ap.error('给一个音频，或用 --compare 列多份')
    for f in files:
        if not os.path.exists(f):
            raise SystemExit('音频不存在: %s' % f)
    if a.repeat > 1 and not a.sample:
        print('!! --repeat %d 但没开 --sample：greedy 下 N 次**逐字一致**，'
              '表决退化成单次（要量稳定性必须 --sample）' % a.repeat)
    if a.repeat > 1:
        print('[i] 每段问 %d 次 → 总推理次数 ×%d（预计耗时 ×%d）' % (a.repeat, a.repeat, a.repeat))

    import soundfile as sf
    total = sf.info(files[0]).duration
    bounds = (segment_bounds(total, a.segments) if a.segments > 1
              else single_bounds(total, a.start, a.dur))
    if a.segments > 1 and total / a.segments > MAX_SEC:
        print('!! 每段 %.1f 秒 > 单次上限 %.0f 秒：会被静默截断，请把 --segments 调到 ≥ %d'
              % (total / a.segments, MAX_SEC, int(total / MAX_SEC) + 1))
    print('=' * 84)
    print('%d 份音频 × %d 段（单段上限 %.0f 秒%s）'
          % (len(files), len(bounds), MAX_SEC,
             '' if a.sample else '；greedy 可复现'))
    print('=' * 84)

    c = Critic(dtype_name=a.dtype, load_4bit=a.load_4bit)
    out = {'model': MODEL_ID, 'dtype': a.dtype, 'load_4bit': a.load_4bit,
           'greedy': not a.sample, 'repeat': a.repeat, 'band': a.band,
           'min_hits': a.min_hits or ((a.repeat + 1) // 2 if a.repeat > 1 else 0),
           'bounds': bounds, 'results': {}}
    for f in files:
        key = os.path.basename(f)
        print('\n### %s' % key)
        out['results'][key] = _scan_one(c, f, bounds, not a.sample, a.max_new_tokens,
                                       repeat=a.repeat, band=a.band,
                                       q_tmpl=(a.ask or None))
        if a.json:                                    # 增量落盘（长扫描禁不起中断）
            with open(a.json, 'w', encoding='utf-8') as fh:
                json.dump(out, fh, ensure_ascii=False, indent=1)

    print('\n' + '=' * 84)
    print('判定矩阵（先抽问题类别，抽不到才看"没问题"）')
    print('=' * 84)
    for key, rows in out['results'].items():
        print('  %-34s %s' % (key, ' '.join('%-10s' % r['verdict'] for r in rows)))

    for key, rows in out['results'].items():
        _print_timeline(key, rows, a.band, a.repeat, a.min_hits)

    print('\n⚠ **线索不是判据**：实测它 base 8 段全报问题（恒真）、0.2dB 微扰就能改判定 ——')
    print('  能用它定位"哪段可疑"· **能用 --compare 对比"原版 vs 当前版"的差异** —— '
          '但**判不了"哪版更好"**（价值判断，见 PITFALLS 232/233）。')
    if a.json:
        print('JSON: %s' % os.path.abspath(a.json))


if __name__ == '__main__':
    sys.exit(main())
