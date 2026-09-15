#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""曲目引擎：读 songs/<曲名>/song.json（**纯数据**）→ 展开编配 → 输出 MIDI + 可选合成器试听

设计目标：**新歌只写一个 JSON，不写代码**。编配风格用字符串选：
  patterns.bass_style: offbeat | eighth | sixteenth | simple | pump16 | waltz（3/4 华尔兹）
  patterns.perc_style: light | dance | orchestral | pump | none | waltz（3/4 华尔兹）
  patterns.arpeggio  : 和弦音序号序列（默认 [0,2,3,4,3,2,4]）

**拍号**：`meter: [3,4]` / `[6,8]`（缺省 `[4,4]`）。引擎内部的"拍"一律是四分音符；
奇数拍号（3/4）下钢琴/电钢自动改走华尔兹的 pah-pah（和弦落在第 2、3 拍），4/4 输出逐字节不变。

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
# **音区分工**：只允许**纯八度**（12 的倍数）。
# ⚠ 2026-09-14 修**真 bug**：原来这里是 `Pad −5 → Hook −5 → Piano +4 → Strings −3 →
# Arp +3 → Melody +7`，注释写着"只改 MIDI 音高、不动和声：整轨同移不改变和弦内的音程关系"
# —— **那句话是错的推理**：轨内音程关系确实不变，但**与和弦的关系全变了**。实测
# （音的 pc 是否落在当小节和弦音集里）：Bass 98.6%（无移调）·
# **Hook 15.1% · Arp 31.6% · Piano 37.5% · Strings 43.2%**
# 也就是说**伴奏有 60~85% 的音是和弦外音** —— 用户听完 38 号说的"主旋律和伴奏没有很好
# 配合"就是这个：旋律（有强拍贴和弦纪律）踩在 A 和弦上，伴奏却在弹移调后的另一组音。
# 探针量到的后果：旋律与同拍伴奏的**半音冲突 45%**（真实模板 6%）。
# 现在：**伴奏整体降八度让位、旋律升八度独占最高**（各轨仍分居不同八度，分工不丢）。
# 个别曲子的旋律本来就高（实测 17/28 号升八度后到 104/105，超出 Melody 上界 103）——
# 那些曲子由下面的**边界保护**自动退回不移调（整轨统一，不许轨内八度跳变）。
# ⚠ 2026-09-15 **撤销旋律的 +12**（用户口径："8~11 秒有点奇怪"）。
# 依据：**模板旋律画像的 `range` 才是"旋律该在哪儿"的权威**（cheerful [64,81]、
# tender [59,93]，mean_pitch 76.2）。实测全库 9 首带旋律的曲子，song.json 的原始音域
# **全部落在各自画像内**，渲染后却被 +12 顶出画像之外：43 号 66~81(A#4~A5) → 78~93(F#5~A6)，
# 中位从 D5 升到 D6 —— 比画像均值高 10 个半音，听感就是"旋律飞在顶上、发尖"。
# 伴奏的 −12 **保留**：旋律回到原音区后依旧是最高的非打击轨（Hook 最高 67 < 旋律 70），
# 纵向配合不变，变的只是旋律不再高一个八度。
# 自证：`track_ranges_musical` + `melody_register_vs_profile`（新增）必须同时通过。
TR_SHIFT = {'Pad': -12, 'Hook': -12, 'Piano': -12, 'Strings': -12, 'Arp': -12,
            'Melody': 0}

# 各轨**乐器合理音域**（按库里成品实测包络，上下各外扩 7 半音；Bass 下界不放）。
# 放在**引擎**里而不是自检里：`TR_SHIFT` 的自适应八度要用它做**边界保护** ——
# 两边各写一份必然漂移（`track_ranges_musical` 会与引擎的判断打架）。
TR_RANGE = {
    'Arp': (44, 111), 'Bass': (16, 71), 'Glock': (63, 115), 'Hook': (32, 91),
    'Melody': (43, 103), 'Pad': (29, 83), 'Piano': (29, 97), 'Strings': (41, 99),
}

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
        'desc': '日常抒情：木琴主奏 + 尼龙吉他 + 电钢琴切分 + 沙锤',
        # ⚠ 2026-09-15：主奏 Melody 0（钢琴）→ 11（颤音琴）→ **13（木琴）**。钢琴在
        #   2.5–5kHz 只有 15.8dB，被 Hook/Strings 的 22–24dB 盖住（`t_track_balance`
        #   报 12_d75_warm 超 8.7dB）；颤音琴补上了亮度，但 attack 42ms 听着"慢半拍"；
        #   木琴 attack 12ms、最响、不拖 —— 见 STYLES['dance'] 的实测表。
        'programs': _progs(Melody=13, Hook=24, Piano=4, Pad=89, Strings=48,
                           Bass=32, Glock=9),
        'mix': {'Melody': (76, 98), 'Hook': (42, 80), 'Piano': (88, 78),
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
        'desc': '舞曲：木琴主奏 + 电钢琴 + 琶音 + 合成贝斯 + 四踩底鼓',
        # ⚠ 2026-09-15：主奏 Melody 81（合成主奏）→ 11（颤音琴）→ **13（木琴）**。
        #   合成主奏够亮、平衡好，但电子味重、CLAP happy 只有 0.323（tense 0.58）。
        #   换成颤音琴后 happy 0.498，可用户听出"有一个乐器慢一点不太和谐" ——
        #   逐项实测（`attack_probe.py`，渲染固定乐句量音头）：
        #     音色      attack   拖尾     首音峰值
        #     电钢 4      4ms    —        −9.9
        #     钟琴 9      4ms    1690ms   −9.8   ← 拖尾太长
        #     钢琴 0      8ms    —        −6.4
        #     木琴 13    12ms    460ms    −3.6   ← ✓ 最响、干脆
        #     颤音琴 11  42ms    310ms    −9.2   ← 比打击(~1–5ms)慢一个数量级 = "慢半拍"
        #     八音盒 10 758ms    1700ms   −5.6
        #   木琴（Xylophone）起音快、不拖、最响，适合 132BPM 的快节奏欢快曲。
        'programs': _progs(Melody=13, Hook=4, Piano=4, Pad=89, Strings=48,
                           Bass=38, Glock=9),
        # ⚠ Melody 的 CC7 上限 104 → 98：木琴采样本身最响（峰值 −3.6dB，其他候选
        #   −6~−10dB），归一化到目标 RMS 后峰值会过 1.0（实测 13_d75_rising 削波 1.001）。
        #   降 6（≈1dB）即可，不影响"旋律压住伴奏"（实测 Melody 仍 28.6dB vs Hook 0.1dB）。
        'mix': {'Melody': (72, 98), 'Hook': (48, 78), 'Piano': (88, 74),
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
# ⚠ **引擎写进 `arr` 的键必须全在这里**（自检 `arr_role_variety` 守着）：
# 2026-09-15 实测踩过 —— 加了 `arr.perc_in`（引子渐入）却忘了同步这张表，
# 结果新歌生成时报"无效的编配开关: perc_in"，而且只在 `dry_compose` 里露一次面。
ARR_KEYS = ('uku', 'piano', 'ep', 'strings', 'glock', 'bass', 'pad', 'arp',
            'perc', 'perc_in', 'harmony', 'shimmer', 'mix',
            # 段级密度（0–4，见 `build_events` 里的说明）与**段级主奏音色**
            # （`melody_prog`，见 `write_midi` 里的说明）—— 2026-09-15 加。
            # ⚠ **必须同步这张表**（上面那条教训就是加了 `perc_in` 忘了这里）。
            'density', 'melody_prog')

# ---------------------------------------------------------------------------
# 段落角色 → 编制（opt-in，`patterns.arr_by_role`）
#
# 为什么（用户反馈"怎么感觉你写的好多部分都是一样的" → 量出来的真值）：
#   本库 231 个段落里 bass 在场 100% / piano 98% / perc 97%（"万年在场"），段落间
#   乐器组合 Jaccard 中位 **0.86**，36~39 号全是 0.86。对照 13 首真实商业 BGM 的
#   分段画像（`refs/sections/*.json`）：高频 5–10k 段间起伏中位 **7.3dB**、10–18k 8.9dB、
#   低频 6.9dB；我们只有 4.5 / 5.0 / 1.5dB，最近 5 首（35~39）更是 0.4~2.3dB —— 整曲
#   一套乐器全在场，听感自然"哪里都一样"。
#
# 做法：**按曲式角色定编制**（不是按能量微调音量），角色名来自模板分析的 `form.plan`
#   A/A2/A3… 主歌 · B/B2… 副歌 · C… 桥段/间奏 · *(bridge) 桥段 · Intro/Outro 引子/尾声
# 三层：
#   BASE   每段都在（bass 是低频唯一来源、piano 是主奏音色 —— 抽掉整段会空，见坑 81 系）
#   COLOR  主歌不开、副歌全开（"亮色只在副歌出现"是真实编配的通用做法）
#   LIFT   中频加厚层，只在副歌/桥段
# 副歌按出现次序轮换亮色家族（第 1 次钟琴、第 2 次电钢琴、第 3 次等）→ 副歌之间也不同。
ROLE_BASE = ('bass', 'piano')
ROLE_COLOR = ('glock', 'uku', 'arp', 'ep')
ROLE_LIFT = ('strings', 'pad', 'shimmer')
# 编制档（**按频段差异设计**，不只是"多一件少一件"）：
#   0 极简：钢琴 + 贝斯 + 垫子        —— 只有低频/中频，5–18k 几乎为空
#   1 主歌：+ 吉他分解                —— 预置律动，中高频有拨弦
#   2 副歌：+ 钟琴/琶音/打击 + 弦乐    —— 高频亮色 + 中频厚度
#   3 变奏：+ 电钢（替钟琴）+ 打击     —— "亮但不一样"（电钢的亮在 1–5k，钟琴在 8k+）
#   4 大副歌：全开                     —— 最满
ARR_PACKS = (
    {'uku': False, 'arp': False, 'glock': False, 'ep': False, 'perc': 0,
     'pad': True, 'strings': False, 'shimmer': False},
    {'uku': True, 'arp': False, 'glock': False, 'ep': False, 'perc': 1,
     'pad': True, 'strings': False, 'shimmer': False},
    {'uku': True, 'arp': True, 'glock': True, 'ep': False, 'perc': 2,
     'pad': True, 'strings': True, 'shimmer': False},
    {'uku': True, 'arp': True, 'glock': False, 'ep': True, 'perc': 2,
     'pad': True, 'strings': False, 'shimmer': True},
    {'uku': True, 'arp': True, 'glock': True, 'ep': True, 'perc': 3,
     'pad': True, 'strings': True, 'shimmer': True},
)


def arr_pack_idx(role, nth=0, tier=1):
    """段落角色 + 第几次出现 → 编制档下标（见 `ARR_PACKS`）

    主歌恒定档 1（同一角色应当可预期）；副歌按出现次序 2 → 3 → 4 → 4（升级但不重复）；
    桥段固定档 3（换音色，做"这里不一样"）；引子/尾声档 0（只有钢琴+贝斯+垫子）。
    """
    if tier <= 0:
        return 1
    r = str(role)
    if r in ('intro', 'outro'):
        return 0
    if r == 'bridge':
        return 3
    if r == 'A':
        return 1
    return min(2 + nth, len(ARR_PACKS) - 1)


def role_of_section(name):
    """段落名 → 角色：'A2'→'A'、'B'→'B'、'b_bridge'→'bridge'、
    'Final (A minor)'→'A'（括号里是调式说明，不是段名）、'intro2'→'intro'。"""
    s = str(name or '').strip().lower()
    if 'intro' in s:
        return 'intro'
    if 'outro' in s or s.startswith('out'):
        return 'outro'
    if 'ridge' in s:
        return 'bridge'
    head = s.split('(')[0].split('_')[0].strip()
    for c in head:                      # 只认 A–E（段落主名；F 起是调式词的"F minor"）
        if 'a' <= c <= 'e':
            return c.upper()
    return 'A'


def arr_sparse(arr):
    """把一段编制**削薄**：关掉 pad / strings / ep 三层，只留节奏与和声骨架
    （uku / piano / arp / glock / bass / perc）。

    **实测依据（2026-09-15，`probe_aesthetic.py`）**：36 号把四层全关（9 轨 → 5 轨）后
      CLAP happy **0.207 → 0.356（+72%）**、tense **0.579 → 0.350（−40%）**，
      而 width / rms / 质心几乎没动（0.66 / −19.3 / 3902）。
    对照实验：同一首改 `perc_style`（dance→pump）、改宽度（0.66→0.40）、3kHz 提亮 3dB、
    换和声（→ C 大调 I-V-vi-IV）—— **四类"改声音"全部无效**，因为都没动结构。
    旁证：我们 happy 最高的 12_d75_warm(0.409) 正是每段 4–6 轨的小编制；
    权威 50 首商业 BGM 的 happy 中位 0.393，我们只有 0.281。

    ⚠ **`glock` 故意不关**：它是"亮色层"，只在副歌档出现 —— 关掉它会连带抹平
    **段间编制差异**，实测让 `arr_role_variety` 的段间 Jaccard 中位从 0.67 涨到
    0.80，超过 0.78 的门（battle/cheerful/neon/retro 四首当场报红）。
    留一层亮色，既有"副歌更亮"的对比，又保住"段落换了编制"。
    """
    out = dict(arr or {})
    for k in ('pad', 'strings', 'ep'):
        if k in out:
            out[k] = False
    return out


def arr_by_role(base, roles, energy=None, tier=1, sparse=False):
    """**按段落角色**改编制（opt-in；`patterns.arr_by_role` 或 `song.json.arr_by_role`）

    参数：
      base    list[dict] —— 每段的编制（`theme_arr` 的输出，含主题包 arr_on/off）
      roles   list[str]  —— 每段的**角色**（`role_of_section` 的结果）
      energy  list[float]|None —— 段间能量曲线（真值来自混音目标的 `structure`）；
              有起伏（≥0.5dB）时**抬/压**而非推翻角色表：高能量段额外开亮色与中频层
      tier    int        —— 主题包允许的编配厚度档（`arr_level` 的 0/1），
              0 = 保守（只保留 BASE + 一种亮色），用在模板证据薄的主题上
      sparse  bool       —— 削薄（走 `arr_sparse`）：关掉 pad/strings/glock/ep 四层。
              舞曲/欢快类主题（`perc_style` 为 dance/pump）用它 —— 实测 happy +72%。

    返回**新的** list（不改入参）。性质（自检 `arr_role_variety` 断言这些）：
      · BASE 每段都在（bass/piano 永不为假）
      · perc 只在主歌/副歌/桥段/大副歌开 —— 引子与尾声**不开**（"从简进入、留白收尾"）
      · 同角色的段落拿到**同一套**编制（曲式该有的可预期性）；副歌之间按次序升级
      · 任意两段的编制不相等（除非两段同角色）—— 这是"段间 Jaccard ≤0.75"的来源
      · **兜底**：若整首没有任何一段开 perc（全曲只有 intro/outro 类段落），
        至少给最先出现的非 intro 段开上 —— 否则 5–18kHz 会塌（见坑 81 系）。
    """
    n = len(base)
    out = [dict(b or {}) for b in base]
    if n == 0:
        return out
    hi = None
    if energy and len(energy) == n:
        span = max(energy) - min(energy)
        hi = (span >= 0.5)
        mid = sum(energy) / float(len(energy))
    seen = {'B': 0, 'bridge': 0}
    for i in range(n):
        role = roles[i] if i < len(roles) else 'A'
        a = out[i]
        a['glock_all'] = False               # 角色编制不继承段落级 glock_all（那会让亮色段过满）
        nth = seen.get(role, 0)
        _idx = arr_pack_idx(role, nth, tier)
        pack = ARR_PACKS[_idx]
        if role in ('B', 'bridge'):
            seen[role] = nth + 1
        for k in ROLE_COLOR + ROLE_LIFT:
            a[k] = bool(pack.get(k))
        for k in ROLE_BASE:                  # 基础层永在（bass 是低频唯一来源、piano 是主奏）
            a[k] = True
        a['perc'] = int(pack.get('perc') or 0)
        # **段级密度**（`arr.density` 0–4，见 `build_events` 里的说明）：按编制档映射 ——
        # 用户指定案例 BGM35 的"逐小节起音数 0→66（**66 倍**）"就是靠段间密度的大起大落，
        # 而我们原来只有 1.5–2.8 倍（全程一条平线）。引子/尾声给 0（只留骨架音），
        # 主歌 2，副歌随次序 3 → 4。
        a['density'] = (0 if role in ('intro', 'outro')
                        else (4 if _idx >= 4 else (3 if _idx >= 2 else 2)))
        if role == 'intro':
            # **引子渐入**（2026-09-15 按真值改）：真值里引子**不是**"不许上打击" ——
            # cheerful 10 首里 7 首前 4 小节有鼓，合计中位 18 点（主段约 22 点/小节），
            # 模式是 "b1–b2 安静、b3–b4 鼓组进来"。所以引子给 `perc=1`，
            # 并由 `perc_in: 2` 让前 2 小节不敲（`perc_part(inbars=…)`）。
            # 尾声仍保持档 0 的 `perc=0`（sorrow 池鼓点中位 0 = 真的不收打击）。
            a['perc'] = 1
            a['perc_in'] = 2
        if hi:                               # 能量曲线：只做**微调**，不推翻角色底色
            if energy[i] > mid:
                a['strings'] = True
            elif energy[i] < mid - 0.5 and role not in ('intro', 'outro'):
                a['glock'] = False
    if not any(a.get('perc') for a in out):   # 兜底：别让全曲没有高频来源
        cand = next((i for i, r in enumerate(roles) if r not in ('intro', 'outro')), 0)
        out[cand]['perc'] = 2
    if sparse:                                # 舞曲/欢快类：削薄（实测 happy +72%）
        out = [arr_sparse(a) for a in out]
    return out


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


def strong_beats(meter):
    """一 **小节内** 的强拍位置（单位：四分音符，与曲内时间轴同口径）。

    口径（唯一来源，`check_song` / `melody_gen` / `selftest` 都从这里取）：
    - 偶数拍号 → `[0, 半小节]`：`[4,4]`→`[0,2]`、`[6,8]`→`[0,1.5]`（两个附点四分脉冲）、`[2,4]`→`[0,1]`
    - 奇数拍号 → `[0]`：`[3,4]` 的第 2 拍是**弱拍**，把它当强拍判会误报（老代码写死第 1、3 拍）
    """
    m = meter if isinstance(meter, (list, tuple)) else (meter or {}).get('meter')
    num, den = (m or (4, 4))
    num, den = int(num), int(den)
    unit = 4.0 / den                       # 一拍 = 几个四分音符
    if num % 2:                            # 奇数拍：只有第 1 拍是强拍
        return [0.0]
    return [0.0, round(num / 2.0 * unit, 4)]


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
    d['patterns'].setdefault('guitar_beats', None)   # opt-in：主题包给的真实高音区落点
    d['patterns'].setdefault('guitar_vary', False)   # opt-in：吉他相位轮换 + 同和弦换把位
    d['patterns'].setdefault('voicing_shift', 0)
    progs = dict(DEFAULT_PROGRAMS)
    progs.update(preset.get('programs', {}))
    progs.update({k: tuple(v) for k, v in d.get('programs', {}).items()})
    d['programs'] = progs
    mix = dict(DEFAULT_MIX)
    mix.update(preset.get('mix', {}))
    mix.update({k: tuple(v) for k, v in d.get('mix', {}).items()})
    d['mix'] = mix
    # --- 校验：**声明了却不生效的项要报出来**，否则就是"配置写了、声音里没有"（实测踩过：
    # `perc_style: light` + `perc_layers` 静默无效，见坑 81 的姊妹问题）
    lay = d.get('patterns', {}).get('perc_layers')
    if lay and d['patterns'].get('perc_style') not in ('light', 'pump'):
        print('  !! patterns.perc_layers 在 perc_style=%s 下**不生效**（只支持 light / pump）'
              % d['patterns'].get('perc_style'))
    if lay:
        airs = lay.get('air') or []
        # 同音高重叠 → FluidSynth 配错 note-off、留下悬空 voice（整曲多渲染十几秒，坑 82）。
        # 规则：每条音高的重复间隔 = (条目数 × 0.25) 拍，时值必须小于它。
        if airs and len(airs) * 0.25 <= max(float(a[2]) for a in airs) + 1e-9:
            print('  !! perc_layers.air 的时值 %.2f 拍 ≥ 重复间隔 %.2f 拍（%d 条）'
                  '→ 同音高重叠，FluidSynth 会留悬空 voice；请减小时值或加条目交替'
                  % (max(float(a[2]) for a in airs), len(airs) * 0.25, len(airs)))
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


def _oct_pick(bar, beat, rate):
    """确定性挑选（同一位置永远同一结果）—— 不用随机数，免得改一处影响全曲。

    低八度加厚要按比例挑音，但引擎的输出必须**可复现**（交付 MIDI 与引擎一致由自检核），
    所以用位置哈希而不是 rng。
    """
    import zlib
    h = zlib.crc32(('%d:%.2f' % (bar, beat)).encode('utf-8'))
    return (h % 1000) / 1000.0 < rate


def tone(tones, i):
    return tones[min(max(0, i), len(tones) - 1)]


# ---------------------------------------------------------------- 编配生成
def guitar_beats(slot_share, B=4.0, dense=0.5):
    """真实模板的高音区落点占用率 → 吉他/尤克里里的**落点**（拍位）

    `slot_share` 是主题包里聚合出来的"每一格有多少比例的小节有音"（16 分格）。
    取 ≥`dense` 的格 → 拍位；同时**补上八分铺底**（律动必须可预期，见 `guitar_arpeggio`），
    再去掉和声含糊的风险：第 1 拍永远保留（根音落在强拍）。
    """
    n = max(1, int(round(B * 2)) - 1)
    eighth = [k * 0.5 for k in range(n)]
    if not slot_share:
        return eighth
    S = len(slot_share)
    got = []
    for j, v in enumerate(slot_share):
        if v >= dense:
            got.append(round(j * B / float(S), 3))
    if not got:                              # 模板高音区太稀 → 退回八分铺底
        return eighth
    keep = [b for b in eighth if b in got]   # 八分位里模板也认的那些
    extra = [b for b in got if b not in keep and b < B - 1e-6]
    if len(keep) < 2:                        # 八分位几乎都不认 → 用模板格（仍保证有音）
        return sorted(set([0.0] + extra))[:max(2, n)]
    return sorted(set([0.0] + keep + extra))


def guitar_rot(arp, sec_i=0, bar_i=0, vary=False):
    """吉他音型的**相位**：给分解和弦换"换弦/换把位"的层次。

    为什么（用户反馈"怎么每首曲子的刚弦吉他都是这个节奏音调"）：
      · **跨曲**：音型曾经是引擎硬编码的 `[0,2,3,4,3,2,4]`，15 个主题包都没有这一项
        → 每首歌的吉他都是同一组落点 + 同一组和弦音序（已由 `new_song.theme_guitar_arp`
        与 `guitar_beats` 按主题的真实音域跨度/高音区占用率修掉）。
      · **曲内**：`arp[k % len(arp)]` 对每个小节都一样 → **和弦相同的小节音高序列逐音相同**
        （实测 84~94% 的小节重复）。真实吉他手会换弦、换把位。
    这里给**相位**（整体轮转）：`[0,2,3,4,3,2,4]` → `[2,3,4,3,2,4,0]` → … 按段落 + 小节错开。
    落点（律动）不变 —— 遵循"音型要么稳定、要么各轨错开相位"的原则。
    音符层的另一层变化由 `guitar_arpeggio` 的**八度档**承担（同和弦换把位）。
    `vary=False`（缺省）= 原样返回，老曲与夹具逐字节不变。
    """
    arp = list(arp or [0])
    if not vary or not arp:
        return arp
    k = (int(sec_i) + int(bar_i)) % len(arp)
    return arp[k:] + arp[:k]


def guitar_arpeggio(ch, i, arp, B=4.0, beats=None, sec_i=0, prev_chords=(), vary=False):
    """吉他/尤克里里分解：第 1 拍必须是根音（否则和声含糊、扒谱都对不上）

    `B` = 一小节的四分音符数（默认 4 = 老行为，逐字节不变）。3/4 → 一小节 5 个八分位。
    `beats`（opt-in，默认 None = 逐字节不变）：**落点**改由主题包的真实高音区占用率给出
    （`guitar_beats`）—— 不传时是"八分铺底 + 每 4 小节末格轻切分"的老行为。
    `sec_i` / `prev_chords` / `vary`（opt-in，`patterns.guitar_vary`）：段落序号、
    本段已用过的和弦、以及总开关 —— 做相位轮换（`guitar_rot`）与**八度档递进**。
    """
    bass, tones = ch
    n = max(1, int(round(B * 2)) - 1)
    if beats:
        bl = sorted(set([0.0] + [b for b in beats if 0 <= b < B]))
    else:
        # **基本律动保持八分铺底**（律动必须可预期），只在每 4 小节的最后一小节把末格
        # 往前挪一点做轻切分；力度走一条平缓的 4 小节包络。
        # ⚠ 2026-09-13 教训：先试过"四种落点型逐小节轮换"（铺底/抽格/加十六分/留白），
        #   结果四条音型**同时**变密变疏 → 整首歌 4 小节一波动，用户听感"凌乱"。
        #   音型要么稳定、要么各轨错开相位；同相位的大幅轮换 = 乱。
        bl = [k * 0.5 for k in range(n)]
        if i % 4 == 3 and n >= 4:
            bl = bl[:-1] + [n * 0.5 - 0.75]
    env = (1.0, 0.96, 0.92, 0.96)[i % 4]
    # **八度档**：同一个和弦在本段里第 k 次出现 → 整体上移 k 个八度（夹在吉他音域内）。
    # 真实分解和弦就是这么做的（第二遍换高把位），它同时消掉"逐音重复"。
    shift = 0
    if vary and prev_chords:
        k = sum(1 for c in prev_chords if c == bass)
        shift = 12 * (k % 2) if k else 0
    seq = guitar_rot(arp, sec_i, i, vary=vary)
    out = []
    for k, b in enumerate(bl):
        # 第 1 拍永远是**根音**（和声不含糊，不参与相位轮换）；其余按轮换后的音型取音。
        if b <= 1e-6:
            m = tone(tones, arp[0])
        else:
            m = tone(tones, seq[k % len(seq)])
        m += shift
        if m > 96:                            # 换把位不许冲出吉他音域
            m -= 12
        out.append((b, 0.45, m, max(40, int((80 if k % 2 else 68) * env))))
    return out


def space_on(pat):
    """`patterns.space`（**给旋律留空间**，opt-in）是否开启。

    抽成独立函数是为了能被变异用例直接打到（同 `harmony_below`）—— 检查要能证明
    "关掉这一层，伴奏密度就会回升、旋律独唱率就会掉回去"。
    """
    return bool((pat or {}).get('space'))


def piano_part(ch, i, B=4.0, thin=False, dense=True):
    """钢琴：反拍和弦短音（含根音） + 高音持续音

    **奇数拍（3/4）走华尔兹写法**：和弦落在第 2、3 拍 = "oom-pah-pah" 的 pah（4/4 的反拍写法
    在三拍里会糊成一片，实测听感没有圆舞曲的推动感）。

    ⚠ 2026-09-14 **`thin`（opt-in，`patterns.space`）= 给旋律留空间**：反拍和弦取 2 个音
    而不是 3 个。依据：真实模板非鼓轨中位 **3.6 音/小节**，我们原来 45 音/小节（2.3 倍）
    → 旋律"独唱率"只有 15%（真实 43%）。**无鼓段落（`dense=False`）不减** ——
    减薄是为了给旋律**在鼓的挤压下**让位，没有鼓的段落本来就稀（少了 10 音/小节的打击），
    再减就撑不住织体（实测 rehearsal 的"无打击乐段落"夹具调参误差卡在 3.5、EQ 补不回）。
    """
    _, tones = ch
    if int(round(B)) % 2:
        return [(float(b), 0.42, m, 66 if b == 1 else 58)
                for b in range(1, int(round(B))) for m in tones[:2 if (thin and dense) else 3]]
    # **反拍和弦的位置按小节轮换**（原来每小节都固定在 0.5 / B-1.5 两处 → 也机械）
    # 前 3 小节保持固定，第 4 小节把第二个反拍往后挪半拍（轻变化，不换律动）
    ALT = ((0.5, B - 1.5),) * 3 + ((0.5, B - 1.0),)
    out = []
    for b in ALT[i % 4]:
        for m in tones[:2 if (thin and dense) else 3]:
            out.append((b, 0.28, m, 58 + (6 if i % 2 else 0)))
    out.append((0.0, 1.5, tone(tones, 3), 54))
    if i % 4 == 3:
        out.append((B - 0.5, 0.4, tone(tones, 2) + 12, 62))
    return out


def fifth_tone(bass, tones):
    """根音上方的"五度音"：**取和弦里真实存在的那个**。

    为什么不能硬写 `bass + 7`：m7b5 / dim 和弦的五度是**减五度**（C#m7b5 的 G# 不是和弦音）
    —— 实测 776 个贝斯音里有 11 个（1.4%）落在和弦外，`accompaniment_harmony` 的
    和弦贴合率因此不是 100%。次序：纯五度 → 减五度 → 增五度，都不在就退回 +7。
    """
    pcs = {t % 12 for t in tones}
    for d in (7, 6, 8):
        if (bass + d) % 12 in pcs:
            return bass + d
    return bass + 7


# **sub 层音高下限**（= C1，32.7Hz）：真实模板的低音线最低就到 C1
# （`refs/midi2` cheerful 最低 C1 / 中位 G1(49Hz)、sorrow 最低 C1）。
# sub 层是 `bass − 12`，bass 本身低到 F1(29) 时就会掉进 21.8Hz 的次声波 ——
# 实测 43 号 Bass 有 28% 的音低于 28Hz，而模板一首都没有。
SUB_FLOOR = 24


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
        if sub_gain:                       # ⚠ 2026-09-15 修：这里原来**无条件**加 sub 音
            # （`sub_gain: 0` 只把力度乘成 0，音高照样写进 MIDI → 留下 21.8Hz 的次声波音）。
            # 实测 43 号被 sub 层推到 Bass F0(21.8Hz)、28% 的音低于 28Hz；
            # 而真实模板低音线（每 0.25 拍最低音）cheerful 最低 C1(32.7Hz)/中位 G1(49Hz)、
            # sorrow 最低 C1/中位 F1(43.7Hz)。其他风格分支早就写了 `if sub_gain`，只有这里漏。
            out.append((0.25, sub_dur, max(bass - 12, SUB_FLOOR), int(72 * sub_gain)))
            out.append((B - 1.75, sub_dur, max(bass - 12, SUB_FLOOR), int(68 * sub_gain)))
    elif style == 'eighth':
        pc = [bass, bass, bass, bass + 12, bass, bass, bass, bass + 7]
        vc = [104, 84, 96, 80, 104, 84, 96, 82]
        out = [(k * 0.5, 0.22, pc[k % 8], vc[k % 8])
               for k in range(max(1, int(round(B * 2))))]
        if sub_gain:                       # sub 层：参考曲 20-40Hz 常有能量
            out.append((0.0, max(sub_dur, 0.8), max(bass - 12, SUB_FLOOR), int(70 * sub_gain)))
            out.append((B / 2.0, max(sub_dur, 0.8), max(bass - 12, SUB_FLOOR), int(64 * sub_gain)))
    elif style == 'sixteenth':
        f5, up = fifth_tone(bass, tones), bass + 12
        # 每拍**三条**（正拍 + e + a）；⚠ `patterns.space`（opt-in）时减到**两条**
        # （12 → 8 音/小节）：16 分三连的低频会把旋律的落点全糊住（坑 129：旋律独唱率
        # 15% vs 真实 43%）。缺省（老曲/夹具）保持原样。
        if space_on(pat):
            tpl = [((0.0, 0.18, bass, 106), (0.5, 0.18, bass, 80)),
                   ((0.0, 0.18, bass, 100), (0.5, 0.18, up, 78)),
                   ((0.0, 0.18, bass, 106), (0.5, 0.18, bass, 80)),
                   ((0.0, 0.18, bass, 100), (0.5, 0.18, f5, 100))]
        else:
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
        f5 = fifth_tone(bass, tones)
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
            out.append((0.75, sub_dur, max(bass - 12, SUB_FLOOR), int(72 * sub_gain)))
            out.append((B - 1.25, sub_dur, max(bass - 12, SUB_FLOOR), int(68 * sub_gain)))
    elif style == 'waltz':
        # 华尔兹的 "oom"：根音踩**第 1 拍**（长音铺住前两拍），第 3 拍给一个轻五度。
        # "pah-pah" 交给钢琴/电钢（`piano_part` / `ep_part` 的奇数拍分支）。
        out = [(0.0, 1.9, bass, 96)]
        if NB >= 3:
            out.append((float(NB - 1), 0.8,
                        bass + 7 if bass + 7 <= 47 else bass - 5, 70))
        if sub_gain:
            out.append((0.0, max(sub_dur, 1.2), max(bass - 12, SUB_FLOOR), int(70 * sub_gain)))
    else:                                       # simple
        out = [(0.0, 1.4, bass, 96)]
        for k in range(2, NB):                  # 4/4 → 第 2、3 拍（与老行为一致）
            out.append((float(k), 0.9, bass, 74 if k == NB - 1 else 80))
        if i % 2 == 1:
            out.append((B - 1.5, 0.45, bass + 7 if bass + 7 <= 47 else bass - 5, 72))
        # sub 层（20-40Hz）：参考曲这一段常有能量，主贝斯落在 40-80 时补不上
        if sub_gain:
            out.append((0.0, max(sub_dur, 1.2), max(bass - 12, SUB_FLOOR), int(70 * sub_gain)))
    if i % 4 == 3 and nxt:                       # 句尾半音引导
        nb = nxt[0]
        out.append((B - 0.25, 0.3, nb + (1 if nb > bass else -1), 78))
    return out


def ep_part(ch, i, B=4.0, thin=False, dense=True):
    """电钢琴：反拍切分和弦（走 Hook 轨）。奇数拍同样改成华尔兹的 pah-pah（见 `piano_part`）

    ⚠ 2026-09-14 **`thin`（opt-in，`patterns.space`）**：每拍 3 个和弦音（12 音/小节）
    → 2 个（8 音/小节）；无鼓段落保持 3 个（口径同 `piano_part`）。
    """
    _, tones = ch
    if int(round(B)) % 2:
        return [(float(b), 0.36, m, 62 if b == 1 else 54)
                for b in range(1, int(round(B))) for m in tones[1:3 if (thin and dense) else 4]]
    acc = B / 2.0 + 0.5                        # 4/4 → 2.5（原来的重音位）
    out = []
    for b in [k + 0.5 for k in range(max(1, int(round(B))))]:
        for m in tones[1:3 if (thin and dense) else 4]:
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

    def _hi(k, dur, vel):
        """取和弦音 +24；⚠ `tone(tones, k)` 在**索引越界时会退回最低的和弦音**
        （常在 C2 附近，如 36）—— +24 之后只有 60，低于钟琴合理下界 63
        （`TR_RANGE['Glock'] = (63, 115)`）。实测 `50_density_test` 的 Glock 掉到 60-96，
        被 `track_ranges_musical` 抓到。这里夹到 ≥63。"""
        m = tone(tones, k) + 24
        while m < 63:
            m += 12
        return m, dur, vel

    if i % 4 == 2:
        a, b = _hi(3, 0.4, 58), _hi(2, 0.4, 54)
        return [(B - 2.5, a[1], a[0], a[2]), (B - 1.0, b[1], b[0], b[2])]
    if i % 4 == 3:
        c = _hi(4, 0.4, 60)
        return [(0.0, c[1], c[0], c[2])]
    return []


def perc_part(style, level, i, nbars, layers=None, kick_vel=None, B=4.0, inbars=0):
    """打击：light = 沙锤+轻底鼓（抒情向）；dance = 四踩+反拍踩镲（舞曲向）

    `B` = 一小节的四分音符数（默认 4 = 老行为，逐字节不变）；十六分格数 = B*4。
    `layers`（opt-in，默认 None = 输出与以前逐字节一致）：给底鼓位置/十六分网格
    额外叠"垫层"，用来补**时间连续性**（占用率），而不是补能量——
    逐声部实测发现我们与例曲差的不是频段能量（EQ 早已对齐），而是
    "低频/高频有没有一直响着"：例曲 5–10kHz 占用 96~100%，我们只有 78~79%。
    格式 {'kick': [[note, vel, 拍长], ...], 'air': [[note, vel, 拍长], ...]}

    `inbars`（opt-in，默认 0 = 逐字节不变）：段内**前 N 小节不敲** —— 引子渐入。
    真实模板（cheerful 10 首）里 7 首前 4 小节有鼓，模式是 **b1–b2 安静、b3–b4 进来**；
    整段一次性全开会让段落切换处出现亮度突变（43 号实测质心 788 → 4907Hz）。"""
    if style == 'none' or level == 0:
        return []
    if inbars and i < inbars:
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
            # ⚠ 2026-09-15 修：踩镲 vel 98 → 66。实测 46/47 的 Perc 在 10–18k 有 28.8dB，
            #   而旋律（钢琴，音区中位 74 左右）在 5–10k 只有 1.5dB —— 高频打击把旋律盖住。
            #   降到 66 后 Perc 的 10–18k 掉到 19.8dB（−9dB），CLAP happy 0.438→0.449。
            out.append((b + 0.5, 0.1, 42, 66))            # 只放反拍
            if level >= 3:
                out.append((b + 0.25, 0.1, 42, 20))
        if level >= 3:
            out.append((B - 0.5, 0.1, 46, 72))
    elif style == 'pump':
        # 照 BGM33 的高频节奏型反推 + **逐声部实测目标**：
        #   例曲 drums 占用率 69/57/33/21/15/12/55/84、动态 29~49dB
        #   （我们要的是"密集 + 均匀 + 被压过"，所以：**每个十六分都有东西**、
        #     力度收在 62~104 的窄带里、正拍与反拍差距压小）
        for k in range(S):                                # 十六分踩镲：全程铺满（骨架，保留）
            out.append((k * 0.25, 0.25, 42, 84 if k % 4 == 0 else (74 if k % 2 == 0 else 68)))
        # ⚠ 2026-09-13 精简：原先这里还叠了 **八分 ride（8/小节）+ 常驻十六分沙锤（8/小节）
        #   + 幽灵小鼓（4/小节）**，三层全在 2.5–10kHz 同一个区、又与踩镲同拍 →
        #   实测打击轨 **70~90 音/小节**，比其余所有轨加起来还多，听感"杂乱"。
        #   高频连续性靠十六分踩镲已经足够（它本来就是每 0.25 拍一个）。
        for b in range(NB):                               # 反拍铃鼓（保留：给反拍一点质感）
            out.append((b + 0.5, 0.25, 54, 66))
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
    elif style == 'waltz':
        # 圆舞曲的打击：底鼓只踩第 1 拍（轻），第 2、3 拍用侧棒点一下，八分沙锤铺连续性。
        # 目的不是"更响"，而是让三拍的**层级**听得出来（1 强 2 弱 3 弱）。
        out.append((0.0, 0.12, 36, 68))
        for b in range(1, NB):
            out.append((float(b), 0.12, 37, 52 if b % 2 else 46))
        for k in range(NB * 2):
            out.append((k * 0.5, 0.2, 82, 34 if k % 2 else 44))
        if level >= 3:
            out.append((B - 0.25, 0.3, 81, 58))
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
        # 沙锤：**4 小节一个循环的落点型**。
        # ⚠ 原先这里是 `for k in range(NB*2): (k*0.5, 0.2, 82, 46 if k%2 else 38)` ——
        #   每 0.5 拍一个、力度只有两档、全曲一动不动。听感就是用户说的"d d d d ddd"，
        #   而且**所有用 light 的曲子都是同一条**（用户："怎么都是这个"）。
        #   现在按小节轮换四种落点：铺底 / 抽格+切分 / 加十六分 / 留白。
        #   每小节仍保 ≥7 个沙锤 → 5–18kHz 连续性不塌（那一档只有沙锤一个高频来源）。
        _ENV = (1.0, 0.85, 0.95, 0.82)                    # 每 4 小节的力度起伏
        _beats = [k * 0.5 for k in range(NB * 2)]
        if i % 4 == 3 and len(_beats) >= 4:                 # 每 4 小节末尾轻切分
            _beats = _beats[:-1] + [NB * 2 * 0.5 - 0.75]
        for bi, off in enumerate(_beats):
            if off < B:
                base = 46 if bi % 2 else 38
                out.append((off, 0.2, 82, max(24, int(base * _ENV[i % 4]))))
        if level >= 2:
            out.append((0.0, 0.1, 36, 68))
            out.append((B / 2.0, 0.1, 36, 60))
            for b in range(1, NB, 2):                     # 侧棒（4/4 → 1、3）
                out.append((float(b), 0.1, 37, 52))
        # 垫层（opt-in）：**light 才是最需要它的一档** —— 这一档只有沙锤一个高频来源，
        # 5–18kHz 的连续性全靠垫层（坑 81：例曲 5000Hz 占用 99%，我们 78%）。
        # ⚠ 以前这段只写在 `pump` 分支里 → `perc_style: light` 下声明的 `perc_layers`
        #   **静默不生效**（16/23 号就踩了这个：配置写了、声音里没有）。
        # ⚠ 条目要 ≥2 个（`n×0.25 > 时值`）：同音高重叠会让 FluidSynth 配错 note-off、
        #   留下永不关闭的悬空 voice（整曲多渲染十几秒，坑 82）。
        if layers:
            airs = layers.get('air', [])
            for si, (mn, mv, md) in enumerate(airs):
                for k in range(si, S, max(1, len(airs))):
                    out.append((k * 0.25, md, mn, mv))
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
    # **`patterns.space`（opt-in）：给旋律留空间** —— 伴奏减薄（见 `piano_part` / Arp / bass）。
    # 缺省关：老曲与 rehearsal 夹具的字节完全不变；主题路径的新歌默认开（`new_song`）。
    _thin = space_on(pat)
    bar0 = 0
    for sec_i, sec in enumerate(d['sections']):
        nbars = sec['bars']
        arr = sec.get('arr', {})
        vs = arr.get('vel', 1.0)
        mel = list(mel_all.get(sec.get('melody', ''), []))
        mel += sec.get('melody_extra', [])
        bucket = {k: [] for k in d['programs']}
        sec_chords = []              # 本段已出现过的和弦根音（吉他换把位档位，见 guitar_arpeggio）
        for i in range(nbars):
            cn = sec['chords'][i]
            if cn not in ch_all:
                raise SystemExit('段落 %s 第 %d 小节引用了未定义的和弦 "%s"'
                                 % (sec.get('name', '?'), i + 1, cn))
            ch = voicing(ch_all[cn])
            if ch[0] not in sec_chords:
                sec_chords.append(ch[0])
            nxt = None
            if i + 1 < nbars:
                nn = sec['chords'][i + 1]
                if nn not in ch_all:
                    raise SystemExit('段落 %s 第 %d 小节引用了未定义的和弦 "%s"'
                                     % (sec.get('name', '?'), i + 2, nn))
                nxt = voicing(ch_all[nn])
            t0 = (bar0 + i) * B
            # **段级密度**（opt-in `arr.density` 0–4；缺省 -1 = 逐字节保持现有行为）——
            # 依据（用户指定的最佳案例 BGM35 实测）：它"逐小节起音数 0→66，**变化 66 倍**"，
            # 结构是 3 个高潮 + 3 个呼吸口串起来的组曲；而我们只有 `perc` 开关与轨开关
            # 两种密度手段，钢琴/贝斯每段照常演奏 → 稀疏段起音数仍有 ~19，
            # 做不出"主体内部 6↔37 反复"（实测只做到总体 39 倍、主体内部 18~22 一条平线）。
            # 这一档同时影响**四条轨**：贝斯音型 / 吉他落点 / 钢琴反拍音数 / 琶音间隔。
            _dens = int(arr.get('density', -1))
            _bpat, _beats, _gap_step = pat, pat.get('guitar_beats'), None
            if _dens >= 0:
                if _dens <= 1:                    # 极简：贝斯退 simple、吉他只留正拍
                    _bpat = dict(pat, bass_style='simple')
                    _gb = [b for b in (pat.get('guitar_beats') or [0.0, 1.0, 2.0, 3.0])
                           if abs(b - round(b)) < 1e-6]
                    _beats = _gb or [0.0, 2.0]
                    _gap_step = 2.0
                elif _dens >= 3:                  # 加厚：吉他回到八分、琶音八分
                    _beats = [k * 0.5 for k in range(8)]
                    _gap_step = 0.5
                # _dens == 2：保持现有密度（不加不减）
            # `density == 0` 的**极端稀疏**：一小节只留骨架音（每轨第 1 个）——
            # 实测（BGM35）它最稀疏的段落起音只有 0~6/小节，而我们即使关掉打击、
            # 钢琴/贝斯仍各弹十几个音（稀疏段 14.2/小节），"呼吸口"根本呼吸不起来。
            _bare = (_dens == 0)
            if arr.get('bass'):
                _ev = list(bass_part(ch, nxt, i, _bpat, B))
                if _bare:
                    _ev = _ev[:1]
                for (b, dd, m, v) in _ev:
                    bucket['Bass'].append((t0 + b, dd, m, v))
            if arr.get('uku') and not _bare:
                for (b, dd, m, v) in guitar_arpeggio(ch, i, pat['arpeggio'], B,
                                                     beats=_beats,
                                                     sec_i=sec_i, prev_chords=sec_chords,
                                                     vary=bool(pat.get('guitar_vary'))):
                    bucket['Hook'].append((t0 + b, dd * sc, m, v))
            if arr.get('ep'):                      # 电钢琴反拍切分（Hook 轨）
                for (b, dd, m, v) in ep_part(ch, i, B, thin=_thin,
                                             dense=bool(arr.get('perc'))):
                    # **+12**：它与吉他分解共用 Hook 轨、落点都压在 0.5 拍、音高取自
                    # 同一个和弦音池 → 实测撞出 96 处"同轨同音高同时发声"
                    # （FluidSynth 会留悬空 voice）。移高八度即解。
                    mm = m + 12
                    if mm <= 127:
                        bucket['Hook'].append((t0 + b, dd * sc, mm, v))
            if arr.get('piano'):
                # 钢琴轨缺失时依次退到 Hook / Arp，避免落到音色不对的轨道
                tr = next((k for k in ('Piano', 'Hook', 'Arp') if k in bucket), None)
                if tr:
                    _pev = list(piano_part(
                            ch, i, B,
                            thin=(_thin or (_dens >= 0 and _dens <= 1)),
                            dense=bool(arr.get('perc'))))
                    if _bare:
                        _pev = _pev[:1]          # 极简：一小节只留一个钢琴长音
                    for (b, dd, m, v) in _pev:
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
                # **按小节轮换落点**（与 guitar_arpeggio(Hook) / perc_part 同一套）：
                # 原先是"每 0.5 拍一个 + seq[0,2,4,2] 循环 + 力度只有 42/50" —— 384 个音
                # 全曲八分平铺，和 Hook、沙锤同频叠加，是"d d d d ddd"的又一层。
                seq = [tone(ch[1], 0), tone(ch[1], 2), tone(ch[1], 4), tone(ch[1], 2)]
                # ⚠ 2026-09-14 **按需减薄**（口径同 `piano_part`）：**有鼓的段落**每拍一个
                # （4 音/小节，原为八分铺底 8 音/小节），无鼓段落保持八分 ——
                # 减薄是为了在鼓的挤压下给旋律让位；无鼓段落本来就稀，减了高频撑不住
                # （实测 rehearsal 的"无打击乐段落"边界夹具调参误差卡在 3.5、EQ 补不回）。
                # ⚠ **时值同时加长**（0.28 → 0.9 拍）：琶音轨在 2.5–10kHz 做"空气层"，
                # 靠"持续"而不是"靠音数"占高频 —— 与 `shimmer` 层同一课（高频要连续的墙，
                # 不是点+空）。实测音数减半后高频占用率因此不降。
                _dense = bool(arr.get('perc')) and _thin
                # 段级密度优先（见上面 `arr.density` 的说明）
                _step = _gap_step if _gap_step else (1.0 if _dense else 0.5)
                _n2 = max(1, int(round(B / _step)))
                _b = [k * _step for k in range(_n2)]
                if i % 4 == 3 and _n2 >= 4:
                    _b = _b[:-1] + [_n2 * _step - 0.75]
                _dur = min(B - 0.1, 0.9) if _dense else 0.28
                _env = (1.0, 0.96, 0.92, 0.96)[i % 4]
                for k, b in enumerate(_b):
                    # **音数减半 → 时值加长**（0.28 → 0.9 拍）：琶音轨在 2.5–10kHz 做"空气层"，
                    # 单纯砍音数会让高频能量掉下来（实测 rehearsal 的 daily 边界夹具调参误差
                    # 卡在 3.5、EQ 补不回）。**靠"持续"而不是"靠音数"占住高频** ——
                    # 也是 `shimmer` 层的同一课（高频要连续的墙，不是点+空）。
                    bucket['Arp'].append((t0 + b, _dur * sc, seq[k % 4] + 12,
                                          max(20, int(((_dense and 52 or 42) +
                                                       (10 if _dense else 8)
                                                       * (1 if k % 2 == 0 else 0)) * _env))))
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
            # **自定义鼓型**（opt-in `patterns.drum_grid`）—— 用户："没有韵律感"。
            # `perc_style: dance` 是"四踩 + 反拍踩镲"的固定套路，而原曲的律动是具体的
            # （`b35_drums.py` 从 Demucs 分离的 drums 轨逐 16 分格实测）：
            #   kick  每拍"正拍 + e 位"（格 0,1 / 4,5 / 8,9 / 12,13）
            #   snare 格 0,3,7,11,15（正拍 + a 位，2/4 拍加强）
            #   hat   8 分格为主、格 7/15 重音
            # 格式：{'kick': [[格, 力度], ...], 'snare': [...], 'hat': [...], 'open': [...]}
            # 给了它就**完全替代** `perc_part`（不再走固定套路）。
            _dg = pat.get('drum_grid')
            if arr.get('perc') and _dg:
                _lvl = 1.0 if int(arr['perc']) >= 2 else 0.78
                for _nm, _note in (('kick', 36), ('snare', 38),
                                   ('hat', 42), ('open', 46)):
                    for (_g, _v) in (_dg.get(_nm) or []):
                        bucket['Perc'].append(
                            (t0 + float(_g) * 0.25, 0.2, _note,
                             max(1, min(127, int(round(float(_v) * _lvl))))))
            elif arr.get('perc'):
                for (b, dd, m, v) in perc_part(pat['perc_style'], arr['perc'], i, nbars,
                                               pat.get('perc_layers'),
                                               pat.get('kick_vel'), B,
                                               int(arr.get('perc_in') or 0)):
                    bucket['Perc'].append((t0 + b, dd, m, v))
        for (b, beat, dur, m) in mel:
            t = (bar0 + b) * B + beat
            # 低八度加厚：越界就**不加这一层**（以前是夹到 0/127 —— 会变成另一个音）
            # `patterns.mel_vel`（opt-in，默认 1.0）：旋律力度缩放。
            # 为什么需要：旋律力度原先是**硬编码**的，而 `mix.Melody` 的 CC7 会被
            # 渲染端的响度归一化吃掉（实测 60→127 只差 0.23dB）→ 想让旋律"浮在伴奏上"
            # 没有任何可用旋钮。实测 17 号（听感融合好）旋律比伴奏 +1.1dB，而 20/21 是 −0.3dB。
            mv = float(pat.get('mel_vel', 1.0))
            # `patterns.melody_dyn`（opt-in，默认关）：**乐句级力度曲线** ——
            # 起 → 推（高点在句 2/3 处）→ 句末收。缺省时这一行是恒等变换（老曲字节不变）。
            if pat.get('melody_dyn'):
                mv *= mel_dyn_env(b, beat, dur, pat['melody_dyn'])
            bucket['Melody'].append((t, dur * 0.96, m, max(1, min(127, int(round(96 * mv))))))
            # **低八度加厚**（`patterns.mel_octave`，默认 **0.15**；`1.0` = 旧行为全叠）。
            # 实测（2026-09-15）：我们 Melody 轨 **100% 的旋律音**都被叠了低八度，而真实模板
            # （cheerful 10 首）叠加率**中位 0%**（7 首为 0，最高 32%）——无条件全叠会把旋律
            # 变成"双八度 synth lead"，听感"电子味 / 假"（用户："所有你生成的快乐的都有这个问题"，
            # 并在 MIDI 里指出 9s / 15s 处"不正常"就是这两层同起点相差 12 的音）。
            # 现在：**只给长音（≥1 拍）按 15% 的比例加**，越界仍不加。
            # **低八度加厚**（`patterns.mel_octave`，默认 **0 = 不加**；`1.0` = 旧行为全叠）。
            # 演进过程（都留痕，别重走）：
            #   ① 原行为：**每个旋律音**都叠低八度 → 实测叠加率 100%，而真实模板（cheerful
            #      10 首）**中位 0%**（7 首为 0，最高 32%）→ 旋律变成"双八度 synth lead"，
            #      听感"电子味/假"（用户："所有你生成的快乐的都有这个问题"）。
            #   ② 第一版修正：按 15% **逐音哈希挑** → 用户反馈"**不连贯**" —— 挑中的音有
            #      低八度、没挑中的没有，**相邻音忽厚忽薄**；旧行为虽"假"但厚度一致。
            #   ③ 现在：**默认不加**（最贴近真实：7/10 首是 0%）。要让旋律浮出来请用
            #      `mel_vel` / 编配平衡（`mix` 与乐器层数），别再靠"随机加厚"。
            mo = float(pat.get('mel_octave', 0.0))
            if mo > 0 and dur >= 1.0 and 0 <= m - 12 <= 127 and _oct_pick(b, beat, mo):
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
        if _thin:
            # **让位**（`patterns.space` 的第二半）：**旋律起音的同一刻，伴奏最多 3 条轨发声**，
            # 且每条轨在该刻最多留 1 个音 —— 旋律一开口，伴奏自动空出来。
            # 这是编曲里 "creating space for a melody" 的**运行时版本**：
            # 减薄（每轨少弹音）只是把"平均密度"降下来，而"旋律响的那一刻仍挤着 6 条轨"
            # 这件事只能靠让位解决（实测减薄后"旋律起音处的伴奏音数"一点没降：6.5 → 6.5）。
            # ⚠ 无鼓段落不让位（同减薄的口径：那里本来就稀）。
            if arr.get('perc'):
                _mel_t = {round((bar0 + b) * B + beat, 4) for (b, beat, _d, _m) in mel}
                _prio = ('Bass', 'Piano', 'Hook', 'Strings', 'Pad', 'Arp')
                _at = {}
                for _k in _prio:
                    for _e in bucket.get(_k, []):
                        _at.setdefault(round(_e[0], 4), []).append(_k)
                _drop = {}
                for _t, _ks in _at.items():
                    if _t not in _mel_t:
                        continue
                    for _k in _ks[3:]:             # 优先级低的轨：该刻整轨让位
                        _drop.setdefault(_k, set()).add(_t)
                _seen = set()
                for _k in _prio:
                    _keep = []
                    for _e in bucket.get(_k, []):
                        _t = round(_e[0], 4)
                        if _t in _drop.get(_k, ()):
                            continue
                        if _t in _mel_t:           # 该刻该轨最多留 1 个音
                            if (_k, _t) in _seen:
                                continue
                            _seen.add((_k, _t))
                        _keep.append(_e)
                    if _k in bucket:
                        bucket[_k] = _keep
        for k in bucket:
            _sh = TR_SHIFT.get(k, 0)      # 音区分工（**只允许纯八度**，见 TR_SHIFT 定义处）
            # **边界保护**：整轨移调后若越出乐器合理音域，**这一轨就不移** ——
            # 整轨统一，不许轨内八度跳变（那会变成"一个音突然跳八度"）。
            # 实测有的曲子 Hook 基准只有 41~67，−12 掉到 29（吉他下界 32），
            # 由 `track_ranges_musical` 守卫抓到；这里先按同一张表判。
            if _sh:
                _rg = TR_RANGE.get(k)
                if _rg:
                    _ps = [m for (_t, _d, m, _v) in bucket[k]]
                    if _ps and not (_rg[0] <= min(_ps) + _sh and max(_ps) + _sh <= _rg[1]):
                        _sh = 0
            # **段末留白 + 渐弱**（opt-in `patterns.section_gap`，单位=拍的倍数）——
            # 用户："有转变可以，但要过渡自然或中间有空白作为间隔"。
            # ⚠ 实现是"最后 `gap` 拍**渐弱到 0**"而**不是直接切掉**：第一版直接切，
            #   结果上一小节的余音（混响 + 采样尾巴）把空白填满了 —— 边界处并不比两侧低
            #   （实测谷深只有 0.3dB），检查照样判"硬切"。渐弱才真把能量收下去。
            # 依据：34 首里 27 首段界是硬切（谷深 −4~+5.7dB、跳变中位 5.5dB）。
            _gap = float((d.get('patterns') or {}).get('section_gap') or 0.0)
            # ⚠ `bucket` 里的事件时间是**全局拍**（见上面 `t0 = (bar0 + i) * B`），
            #   所以段的结束位置必须是 `(bar0 + nbars) * B` —— 第一版错写成 `nbars * B`
            #   （段内拍），于是 `_left` 对几乎所有音都是负数、被整段 `continue` 掉，
            #   成品时长从 118s 塌成 19s（`render_duration_matches_midi` 当场抓到）。
            _end = (bar0 + nbars) * B
            for (t, dd, m, v) in bucket[k]:
                if _gap > 0:
                    _left = _end - t
                    if _left <= 0:
                        continue                     # 越过段末：这一音不留
                    if _left < _gap:
                        v = v * (_left / _gap)       # 段末渐弱
                        dd = min(dd, max(0.1, _left))
                # 走到这里的音高都已在合法范围内（数据越界在 load() 就报错了，
                # 派生声部越界在上游被丢弃）；这里只处理时间/时值/力度
                assert 0 <= m <= 127, '%s 出现了越界音高 %s（派生声部漏了过滤）' % (k, m)
                m2 = m + _sh
                if not (0 <= m2 <= 127):  # 越界就整轨不移（宁可不移，也别夹断音程）
                    m2 = m
                ev[k].append((max(0.0, t), max(0.05, dd), int(m2),
                              max(1, min(127, int(round(v * vs))))))
        bar0 += nbars
    for k in ev:
        ev[k].sort()
    return ev, bar0


def mel_dyn_env(bar, beat, dur, opt):
    """**乐句级力度包络**（opt-in：`patterns.melody_dyn`）—— 起 → 推 → 落。

    为什么要有（用户口径）：旋律力度原先**硬编码两档**（主层 96 / 低八度加厚层 62），
    整条旋律一个力度 → 没有"唱"的表情。真实演奏里一个乐句是"渐强到高点、句末收下来"。

    实现口径（与旋律的拱形同一形状，只是错开 1/3）：
      · 句内位置 `prog ∈ [0,1)`，**高点在 2/3 处**（不是句末）—— 与 `melody_gen` 的
        拱形目标一致；高点之后回落，句末音（时值 ≥1.0 拍）再收一档。
      · 幅度默认 ±10%（0.93 → 1.06 → 0.88）：再大就不是"乐句表情"而是"忽大忽小"了。
    `opt` 可以是 `True`（默认参数）或 dict（`phrase` 乐句小节数 / `hi` 高点 / `lo` 谷底 /
    `tail` 句末收束系数）。

    ⚠ **必须 opt-in**：缺省（`patterns.melody_dyn` 不存在）时这条函数根本不被调用 ——
    否则老曲目的 MIDI 字节会变，全库都得重渲染（`piano_part`/`bass_part` 那些
    `bass_vel`/`kick_vel` 是同一条纪律）。
    """
    o = opt if isinstance(opt, dict) else {}
    phrase = max(1.0, float(o.get('phrase', 4)))
    hi = float(o.get('hi', 1.06))
    lo = float(o.get('lo', 0.94))
    tail = float(o.get('tail', 0.94))
    prog = min(1.0, max(0.0, ((bar % phrase) * 4.0 + beat) / (phrase * 4.0)))
    # 两段折线：句首 `lo` → 句 2/3 处 `hi` → 句末 `lo`。**取对称的 lo/hi**，
    # 于是整句的平均力度 = (lo+hi)/2 = **1.0** —— 只改句内形状，不改整体响度。
    # （第一版把句首写成 `1.0-(hi-1.0)*2`，均值掉到 0.97，整条旋律被压低 3%。）
    if prog < 2.0 / 3.0:
        env = lo + (hi - lo) * (prog / (2.0 / 3.0))
    else:
        env = hi + (lo - hi) * ((prog - 2.0 / 3.0) / (1.0 / 3.0))
    if dur >= 1.0:                       # 长音（多半是句末终止音）
        env *= tail
    return env


def write_midi(d, ev, path):
    tracks = []
    skipped = []
    # 段落级混音自动化（opt-in）：`sections[i].arr.mix = {"Strings": 74, ...}`
    # → 在该段起点写 CC7。这是"起伏"最直接的手段：不用改音符，光靠推子就能做出层次。
    auto = {}
    aprog = {}
    bar0 = 0
    B = float(d.get('bar_beats') or 4.0)
    for sec in d.get('sections', []):
        a = sec.get('arr') or {}
        amix = a.get('mix') or {}
        for name, vol in amix.items():
            auto.setdefault(name, []).append(((bar0) * B, 7, max(0, min(127, int(vol)))))
        # **段级主奏音色**（opt-in `sections[i].arr.melody_prog`）—— 用户："不同部分都有
        # 不同旋律音色，变化很大但是不突兀"。`programs.Melody` 只能给整轨一个音色，
        # 所以在段边界写 program change（`bgm_synth.write_midi` 里 `cc == 'prog'`）。
        if a.get('melody_prog') is not None:
            aprog.setdefault('Melody', []).append(
                (bar0 * B, 'prog', int(a['melody_prog'])))
        bar0 += sec['bars']
    for name, (prog, chan) in d['programs'].items():
        if not ev.get(name):
            skipped.append(name)           # 空轨不写进 MIDI（否则 DAW 里多一堆空轨）
            continue
        pan, vol = d['mix'][name]
        # CC 与 program change 分开拼：前者带 int 的 CC 号，后者是字符串 'prog'，
        # 混在一起排序会 TypeError（第一版就这么写的）。
        ccs = [(0.0, 10, pan), (0.0, 7, vol)] + sorted(auto.get(name, []))
        ccs += sorted(aprog.get(name, []), key=lambda z: z[0])
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
