#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""新歌脚手架（song.json 方案）：**新歌只写一个 JSON，不写代码**

用法:
  # ① 主题路径（**推荐 / 唯一合规**）：依据 = 同主题多首 MIDI 模板聚合出的主题模板包
  python new_song.py 35_seaside --theme seaside [--ref BGM16c] [--seed 7] [--energy-gain 1.0]
  # ② 复现/改歌：从现成曲目复制骨架（**不算模板依据**，会被 check_song 标记为非白名单）
  python new_song.py 06_morning --from 05_d135_cheerful [--style gorgeous]
  python new_song.py --list-styles        # 引擎风格预设
  python new_song.py --list-themes        # 主题模板包（跑 theme_pack.py 生成/查看）

「模板」的规矩（用户口径）：**一次生成要依据很多个不同模板**（同主题、≥8 首），
模板来源只能是 `refs/midi2/`（网络多风格 MIDI 模板库）或网络上带来源 URL 的权威数据。
所以主题路径会：① 读 `refs/themes/<主题>.json`（模板包）② 用它的**和声进行 / 速度 /
调式 / 节奏音型 / 配器 / 曲式**搭出 song.json ③ 用它的**旋律语言画像**跑 melody_gen
④ 把"依据了哪几首模板"写进 song.json（`theme` 字段），供 check_song 与自检核对。

做的事（主题路径）:
  1. songs/<NN_名字>/ 建目录
  2. 写 song.json：段落/和弦/编制来自模板包（**不是复制某首歌**）
  3. 跑 melody_gen（画像 = refs/themes/<主题>_melody.json）
  4. 写 render.json / compose.py / notes.md（notes 里列出模板依据与来源）
不复制任何音频产物。
"""
import json
import os
import re
import shutil
import subprocess
import sys

import json_io

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SONGS = os.path.join(ROOT, 'songs')
REFS = os.path.join(ROOT, 'refs')

COMPOSE_STUB = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""%(name)s —— 编配数据在 song.json，本文件只负责调用引擎"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# ⚠ **面板是按"曲库路径"执行本文件的**（曲目用 `cmd /c mklink /J <曲库>\\\\<曲> <真身>\\\\<曲>`
# 挂进曲库），于是 `HERE/../../scripts` 会解析到**曲库的上级**、`import song_engine` 直接
# `ModuleNotFoundError` —— 2026-09-19 实测：面板「🎼 作曲」按钮对**所有**曲目都失败。
# 面板与它起的任务都会设 `BGM_STUDIO_ROOT` → 优先用它；没设（手工直跑）才回退相对路径。
_ROOT = os.environ.get('BGM_STUDIO_ROOT') or os.path.join(HERE, '..', '..')
sys.path.insert(0, os.path.join(_ROOT, 'scripts'))
import song_engine

if __name__ == '__main__':
    song_engine.compose(os.path.join(HERE, 'song.json'))
'''

# 段落层次用到的"第二梯队"编制（主题包说这个主题确实会用 → 副歌/桥段开出来做变化）
SECOND_TIER = ('strings', 'pad', 'glock', 'ep', 'arp')
ENERGY_CAP_DB = 4.0      # 段间电平偏移上限（再大就不是"段间对比"而是"忽大忽小"）
ENERGY_MIN_DB = 0.5      # 小于这个就当没有起伏，不写 mix（不制造假变化）
# **CC7 → 实际电平的换算系数**（实测，不是理论）：FluidSynth + GeneralUser GS 下
#     dB(cc, cc0) ≈ 38 · log10(cc / cc0)      →  幅度 ∝ cc^1.9（GM 的**凹曲线**）
# 测法：同一份 8 小节内容重复多段、每段只改 CC7，逐段量 RMS（同一渲染里放 CC7=100 参考段
# —— 渲染器会把整首归一到同一响度，跨渲染比绝对电平会串味，第一版就是这么测出鬼值的）：
#   CC7 127→+3.76 · 116→+2.37 · 108→+1.24 · 100→0 · 92→−1.35 · 84→−2.87 ·
#       74→−4.86 · 64→−7.30 · 56→−9.58 · 48→−12.23 · 40→−15.37 · 30→−20.58 (dB)
# ⚠ 别用"CC7 是线性幅度"（`cur × 10^(Δ/20)`）—— 那会**过冲一倍**：线性算 104→77 是 −2.6dB，
# 实测 −3.9dB（实测 battle 目标 6dB 起伏、成品 12dB 就是它造成的）。
CC7_DB_PER_DECADE = 38.0


def cc7_at(level, delta_db):
    """把 CC7 电平按 **实测曲线** 平移 delta_db（GM 的 CC7 是凹曲线，不是线性幅度）"""
    return max(0, min(127, int(round(level * 10 ** (delta_db / CC7_DB_PER_DECADE)))))
# **段间曲线的阻尼系数**（0 = 不写曲线，1 = 照抄目标起伏；`--energy-gain` 可覆盖）。
# 标定方法：6 个主题 × k=0 / k=1 两版（同 seed、`--no-tune` 固定渲染参数），
# 单主题最优 `k* = (目标起伏 − r0)/(r1 − r0)`，r0/r1 = k=0 / k=1 时成品的段间起伏：
#   battle(目标6.0) 0.0→6.0 k*=1.00 形状相关1.00 · neon(6.0) 0.0→6.0 k*=1.00 1.00
#   seaside(3.0) 0.0→3.0 k*=1.00 0.77 · daily(3.0) 0.0→4.0 k*=0.75 0.94
#   night(3.0) 2.0→2.0 无变化（编配自带起伏已到量级）· gorgeous(1.0) 1.0→2.0（目标太平）
# → **中位 k\*=1.0：系数最终就是 1.0**。先前那版 0.6 是在补偿 CC7 换算的口径错（见 cc7_at），
#   口径修对之后不该再留假阻尼。目标起伏 <1.5dB 的主题在包里**直接不给曲线**（别硬造对比）。
ENERGY_GAIN = 1.0


# 各 GM 音色在 **2.5–5kHz 的实测电平**（`probe_timbre.probe_programs` 量：150BPM、
# 两小节固定乐句、**raw 未归一化**）。跨度 **34dB**（电钢 15.5 → 失真吉他 49.8）——
# 这就是用户"有些配合还是不好"的物理来源：`arr.mix` 是按**轨名**写死的，换了音色不调
# 电平，轨间平衡就全乱。实测吻合：钢弦吉他 25(48.8) − 长笛 73(29.7) = **19.1dB**，
# 而守卫在 01/04 上量到 19.2 / 21.0dB（门限 +6dB）。
# 未列出的音色 → 不补偿（宁可不动，也不按猜测乱调）。
HF_LEVEL = {30: 49.8, 25: 48.8, 81: 45.2, 29: 44.5, 27: 41.0, 46: 40.9,
            71: 40.0, 80: 39.1, 75: 35.8, 24: 35.3, 26: 34.2, 73: 29.7,
            0: 23.1, 8: 22.1, 11: 22.1, 13: 21.8, 4: 15.5}


# **CC7 平移量 → 该频段实测 dB 的比例**（实测标定，2026-09-18）：给 Hook 写 −12dB 的
# CC7 平移（78→39）实测只让它 2.5–5kHz 电平降 **5.8dB**（34.8→29.0）= **0.49 倍**。
# `cc7_at` 走的是 GM 凹曲线，与"频段电平"不是同一尺度 —— 不换算就会每次都差一半。
HF_CC7_K = 0.49


def hf_balance(progs, gap=6.0, margin=5.0, cap=40.0):
    """按音色实测高频电平算**伴奏轨**该平移多少 dB（返回 {轨名: 负 dB}）

    为什么必须做（2026-09-18）：音色改成按主题模板取真值后，`track_balance` 立刻在
    01/04 上超标 19~21dB —— 模板把 Hook 给了**钢弦吉他**(48.8)，主奏是**长笛**(29.7)，
    两者 2.5–5kHz 差 19.1dB。**保留模板音色、补电平**，而不是把音色换回预设 ——
    换回去等于把"乐器选择像不像"的成果扔掉（用户那条判据要的正是模板音色）。

    目标：伴奏轨 ≤ `Melody + gap − margin`（留 3dB 余量，免得压着门限走）。
    **只降不抬**：抬高伴奏会把整曲推响，那是另一个问题。

    ⚠ **`cap` 为什么是 26 而不是 12**（实测标定）：CC7 平移的 dB 与"该轨 2.5–5kHz 实测
      电平"**不是同一尺度** —— 给 Hook 写 −12dB（CC7 78→39）实测只降了 **5.8dB**
      （34.8→29.0），约 **0.48 倍**。所以按需降量反推，cap 要放到两倍以上。
    """
    mel = progs.get('Melody')
    if not mel or mel[0] not in HF_LEVEL:
        return {}
    lim = HF_LEVEL[mel[0]] + gap - margin
    out = {}
    for tr in ('Hook', 'Arp', 'Strings', 'Pad', 'Piano'):
        v = progs.get(tr)
        if not v or v[0] not in HF_LEVEL:
            continue
        over = HF_LEVEL[v[0]] - lim
        if over > 0.5:                      # 半 dB 以内不值得写
            # 按 `HF_CC7_K` 反推写入量：`over` 是"该频段实际高出多少 dB"，
            # 而 mix 写的是 CC7 平移量，两者差 2 倍（见常量处的实测标定）。
            out[tr] = -min(over / HF_CC7_K, cap)
    return out


def apply_hf_balance(secs, progs, style):
    """把 `hf_balance` 的补偿**叠加**到每段的 `arr.mix`（值 = 单整数 CC7）

    ⚠ 只能调一次：它是在**当前值**上按 dB 平移，调两次会在已改过的值上再乘一遍。
    """
    import song_engine
    comp = hf_balance(progs)
    if not comp:
        return {}
    preset_mix = (song_engine.STYLES.get(style) or {}).get('mix') or {}
    for sec in secs:
        mix = sec['arr'].setdefault('mix', {})
        for tr, db in comp.items():
            ent = preset_mix.get(tr)
            base = mix[tr] if tr in mix else (int(ent[1]) if ent else 100)
            mix[tr] = cc7_at(int(base), db)
    return comp


def auto_render_params(ref):
    """按参考曲画像推算渲染参数（用户不用手调就能落在附近）"""
    bands = ref.get('bands', {})
    top = bands.get('10000-18000', -20.0)
    # 参考曲顶频越暗 → 搁架压得越低（经验拟合：BGM16c −19.6→3.0 / bgm01c −21.6→2.4 / BGM18b −30.3→−1.1）
    shelf = max(-3.0, min(4.0, 3.0 + (top + 20.0) * 0.4))
    # 裸渲染的宽度通常 0.28-0.33（实测 cheerful: 0.282 / summer: 0.30），
    # 取 0.28 反推偏保守一点，比调小更省一轮
    width = max(0.8, min(4.0, ref.get('width', 0.5) / 0.28))
    return {
        'rms': ref.get('rms_db', -16.9),
        'width': round(width, 2),
        'shelf': round(shelf, 1),
        'hp': 38,
        'low': 0.0,
        'drive': 1.5,
    }


# ---------------------------------------------------------------- 主题路径
def theme_progressions(pack):
    """主题包 → 可用的和声进行候选（主进行优先，去重保序）"""
    h = pack.get('harmony') or {}
    out = []

    def add(p):
        p = list(p or [])
        if p and p not in out:
            out.append(p)
    add(h.get('primary'))
    for p in (h.get('progressions_4') or [])[1:3]:
        add(p.get('symbols'))
    for p in (h.get('progressions_2') or [])[:1]:
        add(list(p.get('symbols') or []) * 2)
    add(h.get('pool_progression'))
    return out


def theme_arr(pack, style, level=0):
    """段落编制 = 引擎风格预设打底 + **主题包覆盖**（`arr_on` / `arr_off`）

    三档口径（写在 pack 里，见 theme_pack 的 `arrangement`）：
      · `arr_on`（≥50% 模板有）→ 开；`arr_off`（0% 模板有）→ 关；其余**交给风格预设**
        —— "证据不足"不等于"没有"，把预设的弦乐/垫子按低占比关掉会抽掉中频厚度。
      · `perc` **永远是 1**：`perc_style=light` + `perc=1` 是"无鼓组但保住 5-18kHz"的唯一
        正解（perc=none/perc=0 会让高频塌掉，见技能与坑 81 系列）。
      · `bass` **永远是 1**：低频的唯一来源；"模板里没有独立贝斯轨"（钢琴曲的低音在左手）
        不等于"没有低音" —— 实测按角色缺失关掉它会让 40–80Hz 掉到 −33dB。
      · `level ≥ 1`（副歌/桥段）把"中间档"的第二梯队打开做段落对比（依据仍是 `arr_share`）。
    """
    import song_engine
    preset = song_engine.STYLES.get(style) or {}
    progs = preset.get('programs') or {}
    arr = {'bass': True, 'perc': 1, 'piano': True, 'uku': True}
    for tr, key in (('Bass', 'bass'), ('Perc', 'perc'), ('Piano', 'piano'),
                    ('Hook', 'uku'), ('Arp', 'arp'), ('Pad', 'pad'),
                    ('Strings', 'strings'), ('Glock', 'glock'), ('EP', 'ep')):
        if tr in progs:
            arr[key] = 1 if key == 'perc' else True
    a = pack.get('arrangement') or {}
    share = a.get('arr_share') or {}
    for k in (a.get('arr_on') or []):
        arr[k] = True
    for k in (a.get('arr_off') or []):
        if k not in ('perc', 'bass'):
            arr[k] = False
    arr['perc'] = 1
    # **基础声部永远是"有"**：bass 是低频的唯一来源（关掉 = 整条 40–160Hz 塌掉，
    # 实测 classic 主题 −33dB）；"模板里没有独立贝斯轨"不等于"音乐里没有低音"。
    arr['bass'] = True
    if level >= 1:
        for k in SECOND_TIER:
            if k in (a.get('arr_maybe') or []) and share.get(k, 0) > 0:
                arr[k] = True
    return arr


# 主题模板音色池 → 引擎轨名（键与 `theme_pack.ROLE_TO_ARR` 的值域一致）
POOL_TO_TRACK = (('ep', 'Melody'), ('uku', 'Hook'), ('piano', 'Piano'),
                 ('strings', 'Strings'), ('pad', 'Pad'), ('glock', 'Glock'),
                 ('bass', 'Bass'))
# 主奏**不许用**的慢起音音色：用户听感"有一个乐器慢一点不太和谐"（selftest 的
# `t_lead_timbre_attack` 把上限定在 20ms，颤音琴 11 实测 42ms 被点名淘汰）。
# 族口径：管风琴 16-23（风箱起音）· 弦乐 40-51（弓弦 60~150ms）· 人声 52-55 · Pad 88-95。
# ⚠ 那个守卫只渲染 `STYLES` 预设来量，**管不到 song.json 里的实际值** —— 模板音色
#   从这一层进来，所以过滤必须写在这里，否则能绕过守卫。
SLOW_ATTACK = frozenset(list(range(16, 24)) + list(range(40, 52))
                        + list(range(52, 56)) + list(range(88, 96)) + [11])
# `uku`（Hook 轨 = 拨弦/分解和弦角色）不许用的音色：`theme_pack.ROLE_BY_PROGRAM`
# 把 ethnic 族 104-111 整体归给 guitar，但其中 **109 风笛 / 110 小提琴 / 111 唢呐
# 不是拨弦**（守卫第一次跑就抓到 daily 的 uku 池首位是 111）—— 弓弦/簧管塞进分解
# 和弦轨会明显不像。104-108（西塔/班卓/三味线/古筝/拇指琴）是拨弦，保留。
NOT_PLUCK = frozenset([109, 110, 111])
# Hook（分解和弦）轨**不用** 2.5–5kHz 极响的音色：吉他族的钢弦 25(48.8) / 清音 27(41.0)
# / 过载 29(44.5) / 失真 30(49.8) 与锯齿主音 81(45.2) 都比主奏（长笛 29.7）高 11~20dB
# —— 而 CC7 压到下限也只降约 12dB，压不平，只能把伴奏压没。
# 用户 2026-09-18 的选择：**换音色**，而不是"把伴奏压到听不见"。
HOOK_HF_MAX = 41.0
# Hook 池被筛空时往**同主题的其它声部池**找 —— 池里每个音色都是该主题模板里**真实出现过**
# 的，所以仍算"按模板选"，不是回落到 5 套风格预设（daily 的 uku 池只有 [25, 111, 30]，
# 筛完为空 → 落到同主题 piano 池的 0 钢琴）。
HOOK_POOLS = ('uku', 'piano', 'glock', 'ep', 'pad')

# **兜底音色**（2026-09-19 加）：主题池**缺某个角色**时，不要让它掉到
# `STYLES[engine_style].programs` —— 古典那套预设把 `Bass` 给成 **Contrabass(43)**、
# `Pad` 给成 **Choir Aahs(52)（人声"啊"）**，实测听感就是"**像打呼噜的声音**"
# （用户 2026-09-19："waltz 背景奇怪…像打呼噜的声音"、"classic 都不好听也是这个原因"）。
# 证据链：classic / waltz 的主题池恰好缺 `Bass` 与 `Pad` → 吃到预设；
#   而 battle / neon / daily 的池里有 `bass: 32` → 只有前两个主题犯病，与用户听感一致。
# 兜底值取**多个主题池实际给出过的**音色（有模板证据，不是自造）：
FALLBACK_PROG = {
    'Bass': 32,      # Acoustic Bass —— battle / neon / daily 的主题池都给这个
    # ⚠ **只有 Bass 需要兜底**（2026-09-19 实测定的）：其余轨的引擎预设本来就合适
    #   （Hook→Harp(46)、Glock→Celesta(9)、Piano/Melody→钢琴、Strings→48），
    #   而且 `Melody` 有**自己的慢起音过滤**、池空时**应该**回落到预设 ——
    #   守卫 `theme_timbre_pool` 的自证用例正是在守这条（上一版我把 Melody 也写进兜底，
    #   当场把那条守卫弄红了）。
    #   真正让人听着"**像打呼噜**"的只有 `Bass = Contrabass(43)`（古典族那套预设的拨弦低音）。
    #   ⚠ `Pad` 也**不**兜底：实测保留预设的 `Choir Aahs(52)（人声"啊"）` 比换掉更好听
    #   （用户："classic_a 也好了而且更好听"，而 a 只换了 Bass）。
}


def theme_programs(pack, pick=0, verbose=False):
    """主题模板的**实际音色** → `song.json` 的 `programs`（覆盖引擎风格预设）

    依据 = `arrangement.prog_pool`：`extract_theme_timbres.py --inject` 扫 8~10 首同主题
    模板、**每首一票**（取该声部音符最多的那条轨）选出的真实 GM 音色，按频次排序。
    `pick=0` ＝ 该主题**最常用**的那个音色（实测 classic＝钢琴/大键琴、battle＝原声贝斯、
    neon＝原声贝斯＋方波主音）；多候选留给"同主题换音色"的变体。

    ⚠ 为什么 `ep → Melody`：`ROLE_TO_ARR` 把 lead/reed/pipe 三族都折成 `ep`，这正是
      主奏族（排箫 75 / 单簧管 71 / 方波主音 80）—— 这才是"主奏该用什么音色"的模板证据。
    ⚠ **`Perc` 与 `Arp` 不接**：`perc` 池里是**音高打击乐**（钢鼓 114 / 反镲 119），
      不是鼓组（鼓组在 channel 10，不吃 program）；`Arp` 在模板角色里没有对应族。
    ⚠ 值必须是 `(program, channel)` 二元组：`song_engine` 用 `tuple(v)` 解包，
      写成裸 int 会 `TypeError: 'int' object is not iterable`。
    """
    import song_engine
    pool = (pack.get('arrangement') or {}).get('prog_pool') or {}
    out = {}
    for key, track in POOL_TO_TRACK:
        # Hook 的候选按 `HOOK_POOLS` 依次拼（uku 优先，空了往同主题的键盘/拨弦池找）
        keys = HOOK_POOLS if track == 'Hook' else (key,)
        cands = []
        for k in keys:
            for _v in (pool.get(k) or []):
                p = int(_v)
                if not (0 <= p <= 95) or p in cands:
                    continue
                if track == 'Melody' and p in SLOW_ATTACK:
                    continue                  # 主奏不许慢起音（见 SLOW_ATTACK）
                if track == 'Hook' and (p in NOT_PLUCK
                                        or HF_LEVEL.get(p, 30.0) > HOOK_HF_MAX):
                    continue                  # 分解和弦不许弓弦/簧管、不许极响吉他
                cands.append(p)
        if not cands:
            # ⚠ 缺角色时**不要**掉到 `STYLES[engine_style].programs`（2026-09-19）：
            #   古典预设的 `Bass=Contrabass(43)` / `Pad=Choir Aahs(52)` 正是用户听到的
            #   "像打呼噜的声音"。走**兜底表**（值都取自其它主题池的真实音色）。
            _fb = FALLBACK_PROG.get(track)
            if _fb is None:
                continue
            out[track] = (_fb, song_engine.CH[track])
            if verbose:
                print('  %s 缺主题音色 → 兜底 %d' % (track, _fb))
            continue
        out[track] = (cands[min(pick, len(cands) - 1)], song_engine.CH[track])
    if verbose:
        print('  音色依据（模板实际）：%s'
              % ' · '.join('%s=%d' % (k, v[0]) for k, v in sorted(out.items())))
    return out


def effective_gain(pack, gain=None):
    """这次实际用的阻尼系数：调用方指定 > 包里的逐主题标定值 > 全局默认

    逐主题标定的原因：成品段间起伏 = **编配自带**（各主题 0.3~1.7dB 不等）+ 曲线贡献，
    单值系数压不住这个差异（实测 k* 在 0.61~1.04 之间）。
    """
    if gain is not None:
        return float(gain)
    g = (pack.get('mix_target') or {}).get('energy_gain')
    return ENERGY_GAIN if g is None else float(g)


def energy_mix(pack, plan, style, gain=None):
    """**段间能量曲线** → 每段的 `arr.mix`（整段一起抬/压，做"段间对比"）

    依据：技能里定死 —— "像不像"主要来自**段间对比**，全曲一条直线 = 亮却闷。
    数据来源 = 混音目标画像的 `structure`（每 8 小节一块的响度）折成的相对起伏。

    换算口径（写在一处，方便核对）：
      · 目标曲线是**每 8 小节**一块，我们的段长默认也是 8 小节 → 按**相对位置**映射
        （段数不同就线性重采样，不硬凑）
      · dB → MIDI CC7 走 **实测曲线**（`cc7_at`：CC7 是凹曲线，幅度 ∝ cc^1.9，不是线性！
        按线性换算会过冲一倍 —— 实测 battle 目标 6dB、成品 12dB 就是它）
      · 偏移先乘**阻尼系数 `gain`**（默认 `ENERGY_GAIN`，6 个主题实测标定），再夹 ±4dB；
        曲线整体起伏 <0.5dB 就**整首不写**（不造假变化）；**只要写，就每段都写** ——
        CC7 写入后一直有效，跳过的段落会继承上一段电平（实测踩过：段间对比直接归零）
    返回 (每段 mix 或 None 的列表, 实际用的曲线)
    """
    mt = pack.get('mix_target') or {}
    k_curve = effective_gain(pack, gain)
    curve = [v * k_curve for v in (mt.get('energy_curve_db') or [])]
    if len(curve) < 3 or not plan:
        return [], []
    n = len(plan)
    used = []
    for i in range(n):                       # 线性重采样：第 i 段取曲线 i/(n-1) 处
        pos = (i / float(n - 1)) * (len(curve) - 1) if n > 1 else 0.0
        lo = int(pos)
        hi = min(len(curve) - 1, lo + 1)
        v = curve[lo] + (curve[hi] - curve[lo]) * (pos - lo)
        used.append(round(max(-ENERGY_CAP_DB, min(ENERGY_CAP_DB, v)), 2))
    if not used or max(used) - min(used) < ENERGY_MIN_DB:
        return [None] * n, used
    import song_engine
    preset_mix = (song_engine.STYLES.get(style) or {}).get('mix') or {}
    out = []
    for d in used:
        # ⚠ **每一段都要写 mix，哪怕偏移是 0**：CC7 是"写入后一直有效"的控制器，
        # 不写的段落会**继承上一段的电平**。第一版图省事把 |Δ|<0.5dB 的段跳过了，
        # 结果 seaside 只有第 1 段有偏移、第 2~8 段全继承了它 → 整首被压低、段间对比归零
        # （实测那个主题的"目标 3dB / 成品 2dB / 形状相关 −0.25"就是这么来的）。
        mix = {}
        for tr, ent in preset_mix.items():
            if not (isinstance(ent, (list, tuple)) and len(ent) > 1):
                continue
            # ⚠ `arr.mix` 的值是**单整数 CC7**（不是 [pan, level]！写成列表会在
            # `write_midi` 的 int(list) 上崩 —— 坑 92 就是这个，白跑一整轮渲染）
            mix[tr] = cc7_at(int(ent[1]), d)
        out.append(mix or None)
    return out, used


def density_curve_mix(pack, plan, verbose=False):
    """**段间密度曲线** → 每段的 `arr.density` 档（0–4）—— "按段对齐"的密度层。

    与 `energy_mix` 成对：那条管"段落整体响/轻"（写 CC7），这条管"段落整体疏/密"
    （开合贝斯音型/吉他落点/钢琴反拍/琶音间隔）。
    用户口径（还原曲反馈）：**"乐器有点乱，没有像原曲一样很好控制"** ——
    "控制得好"= 该疏的地方真的疏下去，而这件事只能从参考曲量出来
    （实测 BGM35 逐段起音 **0.4 ~ 42.4**，**66 倍**范围里反复）。

    数据来源 = 主题包 `mix_target.density_curve_db`（同主题多份模板按 8 小节量音符、
    重采样到同段数后取中位数，再折成相对中位数的 dB —— 见 `song_density.py`）。

    映射（**不硬造对比**）：
      · 以曲线中位数为锚 → **2 档**（`arr.density=0` 是"每小节只留 1 音"的近乎独奏档，
        整首偏 0 档听起来是"一直很空"，不是"跟着参考曲走"）
      · 偏离越大档位越极端，**但档位跨度不人为拉满** —— 实测同主题模板的段间密度
        起伏只有 **1.3~2.5 倍**（`night` 2.5、`battle` 1.3），若强行铺满 0–4 档，
        等于把"参考曲本来很平"编造成"大起大落"，那正是我们自己的听感、不是对齐。
      · 曲线取不到（模板不足）→ 返回空列表，调用方保持 `arr_by_role` 的结果。
    """
    curve = ((pack.get('mix_target') or {}).get('density_curve_db') or [])
    n = len(plan)
    if len(curve) < 3 or n < 2:
        return []
    import song_engine as _se
    order = sorted(range(len(curve)), key=lambda i: (curve[i], i))
    slot = {}
    for rank, i in enumerate(order):
        slot[i] = rank - (len(curve) - 1) / 2.0        # 中位数锚在 0
    half = max(1.0, (len(curve) - 1) / 2.0)
    out = []
    for i in range(n):
        pos = (i / float(n - 1)) * (len(curve) - 1)    # 段数不同就线性重采样
        lo = int(pos)
        hi = min(lo + 1, len(curve) - 1)
        f = pos - lo
        d = slot[lo] * (1 - f) + slot[hi] * f
        # ⚠ 档位跨度收到 **±1（1~3 档）**：`arr.density=0` 的语义是
        #   "每小节只留 1 个音"的**近乎独奏**档，`4` 是全开 —— 两者都是极端。
        #   实测踩过：同主题模板的段间密度起伏本来就小（1.3~2.5 倍），
        #   把它映到 0 档会把普通主歌削成极简，`melody_matches_profile` 立刻
        #   报"生成旋律离画像太远（时值 34%，下限 40%）"。
        #   参考曲的疏密该跟，但不该跟成"极简"，所以基线 2 档、只允许 ±1。
        k = int(round(2.0 + 1.0 * d / half))
        out.append(max(1, min(3, k)))
    if verbose:
        print('   密度曲线 → %s（%s）' % (out, {k: out.count(k) for k in range(5)}))
    return out


def _deg_of(sym, tonic_pc):
    """和弦符号 → 相对主音的半音级数（认不出来返回 None）"""
    import theme_pack as tp
    pc, _suf = tp.split_symbol(sym)
    return None if pc is None else (pc - tonic_pc) % 12


def cadence_pair(pack, progs):
    """**收束对 (V, I)** —— 每段（8 小节）末尾用它收尾。

    为什么必须收束：旧版把主题包的 4 和弦进行**原样循环整段**，A 段 8 小节停在 `B7`
    （属功能）→ 整段悬着不落地（用户口径："和声必须收束"）。真实曲式里每 8 小节
    （乐段）是要合的：属 → 主。

    材料来源（用户硬口径：**不许自己造和弦**，只能取模板里的）——三层，逐层退：
      ① 模板进行里**真实的"属→主"相邻对**（按音级判：属 = 主音 +7、主 = 主音）
      ② 同一个进行里**同时出现**的属与主，拼成 V→I
      ③ `harmony.chord_pool` 里按 degree 记的、**模板里真实用过**的属（degree 7，带自己的
         suffix）+ `harmony.pool_progression` 里的主和弦族（degree 0）
    三层都拿不到 → **返回 None**（不硬造：宁可这一段不收束，也不能破坏"依据可溯源"）。
    """
    import theme_pack as tp
    tonic = ((pack.get('key') or {}).get('pc') or 0) % 12
    h = pack.get('harmony') or {}
    seqs = []
    if h.get('primary'):
        seqs.append(list(h['primary']))
    for p in (h.get('progressions_4') or []):
        if p.get('symbols'):
            seqs.append(list(p['symbols']))
    for p in (h.get('progressions_2') or []):
        if p.get('symbols'):
            seqs.append(list(p['symbols']) * 2)
    if h.get('pool_progression'):
        seqs.append(list(h['pool_progression']))

    def _stable(sym):
        """是不是"落得下来"的和弦（挂四/减/增没有稳定三度，不适合当主）"""
        _pc, suf = tp.split_symbol(sym)
        return suf is not None and 'sus' not in suf and suf not in ('dim', 'aug', 'm7b5')

    for seq in seqs:                                    # ① 真实的 V→I 相邻对
        for a, b in zip(seq, seq[1:]):
            if _deg_of(a, tonic) == 7 and _deg_of(b, tonic) == 0 and _stable(b):
                return [a, b]
    for seq in seqs:                                    # ② 同一进行里的属 + 主
        v = next((s for s in seq if _deg_of(s, tonic) == 7), None)
        i = next((s for s in seq if _deg_of(s, tonic) == 0 and _stable(s)), None)
        if v and i:
            return [v, i]
    pool = h.get('chord_pool') or []
    # 第③层的后缀优先级：**属要有导音/三度才成其为属**，主要有三度才落得下来 ——
    # 不排序的话 `by_deg` 会按频次取到 `Gsus4`（无三度）当属、`Csus4` 当主，
    # 那就成了"挂和弦收束"（听感不落地）。次序 = 功能明确度：
    V_PREF = ('7', '', '6', 'maj7', 'm7', 'm', 'm6')
    I_PREF = ('maj7', '6', '', 'm7', 'm', '7', 'm6')

    def by_deg(deg, pref):                              # ③ chord_pool 的 degree 记录
        cs = [c for c in pool if int(c.get('degree', -1)) == deg]
        if not cs:
            return None
        for s in pref:
            if any((c.get('suffix') or '') == s for c in cs):
                return tp.NAMES[(tonic + deg) % 12] + s
        c = max(cs, key=lambda x: x.get('count') or 0)
        return tp.NAMES[(tonic + deg) % 12] + (c.get('suffix') or '')
    v = by_deg(7, V_PREF)
    # 主和弦**优先取 `pool_progression`**（包里给的主和弦族，如 cheerful 的 `Cmaj7`）；
    # `chord_pool` 的 degree 0 只有频次聚合，cheerful 那条只会给出 `C7`（属七性质的 I，
    # 作收束偏"没落地"）。`_stable` 把挂四/减/增挡在外面（实测 lounge 的族首是 `Csus4`）。
    i = next((s for s in (h.get('pool_progression') or [])
              if _deg_of(s, tonic) == 0 and _stable(s)), None) or by_deg(0, I_PREF)
    return [v, i] if (v and i) else None


def arr_level(eused, i, role=None):
    """段落 i 的**编配层次**（0 = 基础编制，1 = 开第二梯队）。两条依据，**能量曲线优先**。

    ① **段间能量曲线**（`mix_target.energy_curve_db`，来自**真实音频**的每 8 小节响度）：
       高于均值的段开第二梯队。这是有真值支撑的那条（实测跟它走，高/低能量段的编配厚度
       排序相关 0.87，而旧的 `i % 3` 机械轮换只有 0.64 且不单调）。
       ⚠ 试过拿 **MIDI 模板的编配密度曲线**当真值：真实模板"每 8 小节密度"的**相对起伏
       中位 0.00**（一半以上完全平）—— MIDI 模板库在编配层是**扁平**的，不成立。
    ② **曲式角色兜底**（曲线起伏 <1dB，即"目标本身没对比"时）：非 A 段（B/C，副歌/桥段）开、
       A 段（含 A2/A3…，主歌）关。
       为什么加（用户反馈"怎么感觉你写的好多部分都是一样的"）：39 号（tender）的目标画像
       本身平坦 → 曲线为空 → 旧逻辑**全 0** → **8 段编配一模一样**，而它恰恰就是被吐槽
       "都一样"的那首。曲式角色是**已有信息**（`form.plan` 的段落名就是分析模板得来的
       A/A2/B/A3/C…），用它当兜底比"全平"强，也不用编造数据。
    """
    if eused and i < len(eused) and (max(eused) - min(eused)) >= 1.0:
        return 1 if eused[i] >= sum(eused) / len(eused) else 0
    if role is not None:
        return 0 if str(role).startswith('A') else 1
    return 0


def role_melody_name(name, i):
    """段落名 → **旋律键**：同名段落（A / A2 / A3 / A4 / A5）**共用一支旋律**。

    为什么（用户反馈"怎么感觉你写的好多部分都是一样的" → 量完发现**两头都反了**）：
    `melody_gen` 本来就是**按旋律名分组生成、同名共用**的；而旧代码给每段一个**新名字**
    （`m%d % (i + 1)`）→ A 段复现 5 次却是 5 支完全不同的旋律，曲子**没有记忆点**
    （"主题"根本不存在）。改成按角色名（去掉尾部数字）后，8 段只用 3 支旋律
    （A×5 / B×2 / C×1）—— 这才是曲式（AABA）该有的样子。

    **2026-09 修正**：键改由 `song_engine.role_of_section` 给出 —— 旧写法只剥尾部数字，
    `Final (A minor)` 会被当成一支**新旋律**（17/13 号就是这么来的：`b` 与 `b_bridge`
    是两条独立旋律线却语言重合 93%）。现在 `Final (A minor)`→`A`、`b_bridge`→`bridge`，
    段落名里的调式说明不再另起一支。
    抽成独立函数是为了能被变异用例打到（同 `song_engine.space_on`）。
    """
    import song_engine
    return song_engine.role_of_section(name) if name else ('m%d' % (i + 1))


def theme_guitar_arp(pack):
    """主题包的真实吉他音域跨度 → **分解和弦的音型**（和弦音序号序列）

    为什么（用户反馈"怎么每首曲子的刚弦吉他都是这个节奏音调"）：
    引擎里 `patterns.arpeggio` 的默认值是**硬编码**的 `[0,2,3,4,3,2,4]`，而 15 个主题包
    全都没有这一项 → 每首歌的吉他都是"同一组落点 + 同一组和弦音序"，只有和弦不同。

    依据（量出来的）：主题包 `arrangement.role_range` 里吉他/主奏的真实音域跨度差很大 ——
    `retro` 31–77（跨度 46）、`lounge` 39–78（39）、`classic` 的 pipe 58–105（47）
    vs `night` 50–67（跨度 17）、`sorrow` 60–79（19）、`waltz` 53–74（21）。
    跨度大 = 真实曲里吉他在**跨八度地扫**（宽广琶音）；跨度小 = 挤在中音区（密集回旋）。
    所以按跨度分三档取音型，而不是所有主题一个样。

    抽成独立函数是为了能被自检与变异用例打到（同 `role_melody_name`）。
    """
    rr = (pack.get('arrangement') or {}).get('role_range') or {}
    rng = rr.get('guitar') or rr.get('lead') or rr.get('pipe') or rr.get('organ')
    span = (int(rng[1]) - int(rng[0])) if (rng and len(rng) == 2) else 30
    if span >= 36:
        return [0, 2, 4, 5, 4, 2, 3]          # 宽琶音（跨八度，含九度）
    if span >= 24:
        return [0, 2, 3, 4, 3, 2, 4]          # 中琶音（三度/五度阶梯；原默认档）
    return [0, 1, 3, 2]                       # 窄音型（密集邻音回旋，留在中音区）


def _bpm_from_pack(pack, seed):
    """按主题模板包的**真实 BPM 范围**取一个值（不再固定用中位数）。→ (bpm, 来源说明)

    ⚠ 为什么改（2026-09-20，用户："主题的 bpm 怎么是固定的"）：
      旧行为无条件取 `pack['bpm']['median']` → **同一主题生成的每首曲子 BPM 完全一样**
      （实测 battle 3 首全 139.0 · classic 3 首全 110.0 · daily 4 首全 128 · neon 2 首全 140）；
      而模板本身的跨度很宽（daily 88~161、battle 60~165）—— 中位数把这个信息**压成了一个点**。
      这正是 SKILL §0 那句"**真差距是「同质化」**"最直接的来源之一。
    ⚠ 为什么用 **p25~p75** 而不是 min~max：两端多是**记谱层级**问题（实测 battle 有 60、
      classic 有 40/190 —— 半速/倍速记谱），落进去会得到"速度层级错"的曲子（SKILL §2 第 6 条）。
    ⚠ 为什么按 seed 定点取：**同 seed 必须复现同一首**（自检 `determinism_and_bytes` 守这一点）。
      这里用 LCG 自算 [0,1)，**不 import random** —— 免得动到全局随机状态、影响 melody_gen
      等其它随机源（那是"改了 A 却动了 B"的经典坑）。
    """
    b = pack.get('bpm') or {}
    med = float(b.get('median') or 120.0)
    lo, hi = b.get('p25'), b.get('p75')
    if lo and hi and float(hi) == float(lo):
        return float(lo), '命令行 --bpm 指定'          # theme_mode 把范围塌缩成了一点
    if not lo or not hi or float(hi) < float(lo):
        return med, '主题中位数 %g（包内没有 p25/p75 范围）' % med
    x = (int(seed) * 1103515245 + 12345) & 0x7FFFFFFF
    v = int(round(float(lo) + (float(hi) - float(lo)) * (x / float(0x80000000))))
    return float(v), '主题模板范围 %g~%g（按 seed=%s 定点取）' % (float(lo), float(hi), seed)


def build_from_theme(pack, short, seed=7, ncand=4, energy_gain=None):
    """主题模板包 → song.json 数据（**作曲依据全在包里**）"""
    import build_song
    import song_engine
    arr_by_role = True          # 新歌默认按段落角色差异化编制（见下）
    gtr_arp = theme_guitar_arp(pack)
    gtr_beats = song_engine.guitar_beats((pack.get('rhythm') or {}).get('high_slot_share'),
                                         dense=0.55)
    plan = (pack.get('form') or {}).get('plan') or []
    progs = theme_progressions(pack)
    if not plan or not progs:
        raise SystemExit('主题包 %s 缺 form.plan / harmony（先重跑 theme_pack.py）'
                         % pack.get('theme'))
    tonic_pc = ((pack.get('key') or {}).get('pc') or 0)
    cad = cadence_pair(pack, progs)
    # **段间能量曲线**（来自混音目标画像的 structure）：整段抬/压，做"段间对比"。
    # 它同时决定**编配层次**（见下）—— 所以先算曲线、再造段落。
    emix, eused = energy_mix(pack, plan, pack.get('engine_style'), gain=energy_gain)
    chords, melody, secs = {}, {}, []
    for i, item in enumerate(plan):
        name = item.get('name') or 'S%d' % (i + 1)
        bars = int(item.get('bars') or 8)
        base = progs[min(int(item.get('prog') or 0), len(progs) - 1)]
        if not name.startswith('A'):
            base = base[1:] + base[:1]          # 非 A 段：同一和声家族换起点（有变化不跑题）
        clist = [base[j % len(base)] for j in range(bars)]
        # **段末收束（每 8 小节）**：最后 2 小节改成 V→I。旧版是"进行原样循环整段"，
        # A 段永远停在 B7（属）→ 一直悬着（用户口径："和声必须收束"）。
        # 和弦仍取自主题包（`cadence_pair` 三层退让），不是自己造的。
        if cad and bars >= 4:
            clist[-2], clist[-1] = cad[0], cad[1]
        for c in clist:
            if c not in chords:
                chords[c] = build_song.voicing(c)
        # **段落旋律按"角色"命名**：同名段落（A / A2 / A3 …）**共用一支旋律**。
        # 为什么（用户反馈："怎么感觉你写的好多部分都是一样的" → 量完发现**两头都反了**）：
        #   · 旋律这头**过头了** —— 原来每段给一个新名字（`m%d % (i+1)`），于是 A 段复现
        #     5 次却是 **5 支完全不同的旋律**，曲子**没有记忆点**（`melody_gen` 本来就是
        #     按旋律名分组生成、同名共用的，是这里的命名把复用掐掉了）。
        #   · 编配与力度那头**太少** —— 那才是"听着都一样"的来源（见 `arr_level`）。
        # 取段落名去掉结尾数字做角色名：A2/A3/A4/A5 → A；B2 → B；C → C。
        mname = role_melody_name(name, i)
        # 占位旋律：song.json 的旋律是**4 元** `[小节, 拍, 时值, 音高]`（melody_gen 会覆盖它）
        melody[mname] = [[0, 0, 4.0, min(83, 60 + tonic_pc)]]
        secs.append({'name': name, 'bars': bars, 'chords': clist, 'melody': mname,
                     'arr': theme_arr(pack, pack.get('engine_style'),
                                      level=arr_level(eused, i,
                                                      role=song_engine.role_of_section(name)))})
    # **按段落角色差异化编制**（opt-in：`patterns.arr_by_role`，见 `song_engine.arr_by_role`）
    # 为什么：只按能量曲线调音量的话，231 个段落里 bass 100% / piano 98% / perc 97% 在场，
    # 段间乐器组合 Jaccard 中位 0.86 —— 用户听感就是"好多部分都是一样的"。
    if arr_by_role:
        roles = [song_engine.role_of_section(s['name']) for s in secs]
        # 舞曲/欢快类主题**削薄**编配（`arr_sparse`）：实测 36 号关掉 pad/strings/
        # glock/ep 后 CLAP happy 0.207→0.356（+72%）、tense 0.579→0.350（−40%），
        # 而 width/rms/质心几乎没动 —— 见 `song_engine.arr_sparse` 的实测记录。
        _sparse = (pack.get('rhythm') or {}).get('perc_style') in ('dance', 'pump')
        arrs = song_engine.arr_by_role([s['arr'] for s in secs], roles,
                                       energy=(eused or None), tier=1, sparse=_sparse)
        for s, a in zip(secs, arrs):
            s['arr'] = a
    # **段间密度曲线**（"按段对齐"的密度层，`mix_target.density_curve_db`）：
    # 放在 `arr_by_role` **之后**覆盖它的 `arr.density` —— 角色只是"副歌比主歌厚"的通用先验，
    # 而这条曲线是**该主题参考曲实测的疏密走势**（谁该疏、谁该密），比先验更具体。
    # 取不到曲线时返回空列表 → 保持 arr_by_role 的结果（向后兼容）。
    dcurve = density_curve_mix(pack, plan)
    if dcurve:
        for s, k in zip(secs, dcurve):
            s['arr'] = dict(s.get('arr') or {}, density=k)
    for sec, mx in zip(secs, emix):
        if mx:
            sec['arr']['mix'] = mx

    # **段级调式**（2026-09-18 补）：`melody_gen` 的调式取 `sec.get('mode')`，段落没写就退回
    # **画像自己的 `base_scale`**（画像来自真实曲目、与主题包无关）→ 旋律与和弦各走各的调式。
    # 实测（`b29_melody_chord_fit.py`）：09_cheerful 调内率只有 67%、硬冲突 14%；13_night 67%/14%。
    # 写进段落即让两边同调式（引擎已支持段级 mode）。
    _mode = (pack.get('key') or {}).get('mode') or 'major'
    for s in secs:
        s['mode'] = _mode

    # **中段"呼吸口"极静段**（2026-09-18 补）：`docs/CASE-BGM35.md` 实测它的
    # **66 倍段级密度起伏**来自第 201-208 小节的极静段（−34.2dB）；我们的曲子只有 1.7~2.8 倍。
    # 消融实验（`b29_ablate_density.py`）把 `arr.density` / `arr_by_role` / `space` /
    # 编配定量四个嫌疑全排除了，定位到**根本没有低密度段**。挑"倒数第 3 个非引子/尾声段"
    # 当呼吸口（与 BGM35 的位置一致），并把旋律层一起压低（否则它撑住能量、静不下来）。
    # 挑法（**必须与落盘复核脚本 `b29_add_quiet.py` 的 `pick_quiet` 同规则**，否则新建的曲子
    # 会挑到 A 系主题段、把主题首现改成极静）：先砍 intro/outro → 再砍掉第一个候选
    # （主题首次陈述不动）→ 优先 **B/C 等对比段**（段名不以 A 开头）→ 取池子里**倒数第 2 个**
    # （留最后一个对比段在靠后处给劲儿；BGM35 的极静段也在中后段，不在收尾）。
    # ⚠ 复核脚本还会多做一步"逐候选试算、要求全曲峰值不掉"——生成阶段还没算 MIDI 事件，
    #   做不了；所以这里只保证"挑对比段"，落盘后由 `b29_quiet_ratio.py` 复核密度倍率。
    _cand = [i for i, s in enumerate(secs)
             if song_engine.role_of_section(s['name']) not in ('intro', 'outro')]
    _body = _cand[1:]
    _contra = [i for i in _body if not str(secs[i]['name']).upper().startswith('A')]
    _pool = _contra or _body
    if _pool:
        _q = secs[_pool[-2] if len(_pool) >= 2 else _pool[-1]]
        _qa = dict(_q.get('arr') or {})
        for _k in ('uku', 'arp', 'strings', 'glock', 'ep', 'shimmer', 'glock_all'):
            _qa[_k] = False
        _qa.update({'bass': True, 'piano': True, 'pad': True, 'perc': 0, 'density': 0})
        # ⚠ **不要在这里再压 `mix`**（2026-09-18 实测后去掉）：`RECIPE-BGM35.md:30` 里
        # 呼吸口 RMS −19.7 vs 主体 −15.8 —— **只降 3.9dB**，它靠**密度**降（起音 36→6.4），
        # **不是靠音量**。实测我们只做 density 时入口落差 −1.9~−3.6dB，与配方吻合；
        # 若再叠 `mix × 0.42`（≈ −7.5dB）就过冲，入口会变成硬切。
        _q['arr'] = _qa
    # **收尾逐小节渐弱**（2026-09-18 补，口径同 `CASE-BGM36.md:60` / `CASE-BGM35.md:129`）：
    # BGM36 最后 **7 小节**衰减到 −94.1dB、BGM35 是 5 小节到 −38dB —— 那才是"过渡自然"的样板；
    # 我们的 `section_gap` 只做段末几拍，实测收尾段 RMS 只到 −16.5dB（主体 −15.9）。
    # 取 **3 小节**（本曲 40~64 小节，约 5~7%），并从末尾往前**跳过极静段**
    # （极静段是呼吸口，不是收尾；叠加会双重衰减）。
    for _s in reversed(secs):
        if int((_s.get('arr') or {}).get('density') or 0) == 0:
            continue
        _s['arr']['ending_fade'] = 3
        break

    # **段级主奏音色**（`arr.melody_prog`，2026-09-18 补）—— 引擎早就支持
    # （`song_engine.write_midi` 里有 `melody_prog` 分支），注释里引的就是用户原话：
    #   **"不同部分都有不同旋律音色，变化很大但是不突兀"**。
    # 但 `new_song` **从来没生成过它** → 直接写出来的曲子**全曲一个主奏音色**，
    # 这是"乐器选择不像"的一条主因（用户 2026-09-18："乐器选择还是不像，**在 MIDI 里也一样**"
    # —— 即不是音源的锅，是 MIDI 层就没换）。
    #
    # 规则（两条都有实测依据）：
    #   ① **同角色的段落用同一个音色** —— 曲式该有的可预期性（与 `arr_by_role` 同口径）；
    #   ② 只用**起音 ≤20ms** 的音色：颤音琴(11) 42ms 实测"慢半拍"被用户点名淘汰
    #      （`t_lead_timbre_attack` 在守）。
    # 候选池按"与钢琴的距离"排：0 钢琴 → 13 木琴 → 8 钢片琴 → 4 电钢 → 24 尼龙吉他 → 9 钟琴。
    _mel_tpl = (theme_programs(pack).get('Melody') or (None,))[0]
    _MEL_PROGS = melody_prog_pool(_mel_tpl)
    _role_at = {}
    for _s in secs:
        _r = song_engine.role_of_section(_s['name'])
        if _r not in _role_at:
            _role_at[_r] = len(_role_at)
        _s['arr']['melody_prog'] = _MEL_PROGS[_role_at[_r] % len(_MEL_PROGS)]
    # **引子渐入**（`arr.perc_in` → `song_engine.perc_part(inbars=…)`）：真实模板里引子是
    # "b1–b2 安静、b3–b4 鼓组进来"（cheerful 10 首里 7 首前 4 小节有鼓、合计中位 18 点，
    # 而单看 b1 多数是 0）。整段一次性全开会在段落切换处造成亮度突变
    # （43 号实测逐小节频谱质心 788 → 4907Hz）。
    # 只在引子且段长 ≥4 时生效；`perc=0` 的段本来就不敲，加了也没有副作用。
    for _s in secs:
        if song_engine.role_of_section(_s['name']) == 'intro' and _s['bars'] >= 4:
            _s['arr']['perc_in'] = 2
    # **音色平衡补偿**：音色按模板真值取用后，轨间高频平衡必须跟着调（见 `hf_balance`）。
    # 放在 `d` 组装前、`energy_mix` 之后 —— 段间曲线先写，补偿再叠加，互不覆盖。
    _hb = apply_hf_balance(secs, theme_programs(pack), pack.get('engine_style'))
    if _hb:
        print('  音色平衡补偿（按 2.5-5kHz 实测）：%s'
              % ' · '.join('%s %+.1fdB' % (k, v) for k, v in sorted(_hb.items())))
    _bpm, _bpm_src = _bpm_from_pack(pack, seed)
    print('  BPM %g —— %s' % (_bpm, _bpm_src))
    d = {'name': short,
         'bpm': _bpm,
         'meter': list(pack.get('meter') or [4, 4]),
         'desc': '%s（依据主题模板包 %s：%d 首同主题模板）'
                 % (pack.get('label', ''), pack['theme'], pack.get('template_count', 0)),
         'style': pack.get('engine_style'),
         # **音色按主题模板来**（逐键覆盖 `STYLES[engine_style].programs`，见
         # `theme_programs`）：实测模板用的音色远超 5 套预设 —— classic 有管钟/双簧管/
         # 小提琴，battle 有排箫/钢弦吉他，neon 有方波主音/合成弦乐，lounge/night 有
         # 中音·次中音萨克斯。用户判据"乐器选择不像"就卡在这一层。
         # 只写"模板里有证据"的那几个轨，其余仍继承预设（`song_engine` 是逐键 update）。
         'programs': theme_programs(pack, verbose=True),
         'patterns': {'bass_style': (pack.get('rhythm') or {}).get('bass_style', 'simple'),
                      'perc_style': (pack.get('rhythm') or {}).get('perc_style', 'light'),
                      # **乐句级力度曲线**（opt-in，见 `song_engine.mel_dyn_env`）：
                      # 只有主题路径的新歌才开 —— 老曲目没有这个键，MIDI 字节不变，
                      # 不需要全库重渲染。
                      'melody_dyn': True,
                      # **按段落角色差异化编制**（opt-in，见 `song_engine.arr_by_role`）：
                      # 主歌/副歌/桥段/引子/尾声各用不同乐器组合，治"整曲一套乐器全在场"
                      # （实测段间编配 Jaccard 0.86）。只对新歌开。
                      'arr_by_role': True,
                      # **吉他的节奏与音型按主题来**（opt-in，见 `theme_guitar_arp` 与
                      # `song_engine.guitar_beats`）：落点来自主题包真实高音区占用率、
                      # 音型来自真实吉他音域跨度。老曲目没有这两个键 → 字节不变。
                      'arpeggio': gtr_arp,
                      'guitar_beats': gtr_beats,
                      # 吉他的**曲内变化**（相位轮换 + 同和弦换把位）：同和弦反复的小节
                      # 不再逐音重复（实测旧行为 84~94% 的小节音高序列完全相同）。
                      'guitar_vary': True,
                      # **给旋律留空间**（opt-in，见 `song_engine` 的 `patterns.space`）：
                      # 伴奏减薄到接近真实模板的密度（实测我们 45 音/小节 vs 真实 19.8，
                      # 旋律"独唱率"只有 15% vs 真实 43%）。同样只对新歌开。
                      'space': True,
                      # **伴奏避让主奏**（opt-in，见 `song_engine` 的 `avoid_lead`）：
                      # 把压在旋律同音高/同音区上的伴奏音移到**本和弦的另一个音级**。
                      # 依据（2026-09-19 新旧对照实测）：**同规则从零生成的 5 首新曲里有 3 首**
                      # 撞主奏 13~19 处（肇事轨 `Hook`/`Arp`），而 19 首**已开这个键**的旧曲
                      # 合计只有 15 处。原因不是新曲写得差 —— 是这个键原先只加在旧曲的
                      # `song.json` 里、没进生成器，新曲天生缺它。
                      'avoid_lead': 2,
                      # **段首极短渐入**（opt-in，见 `song_engine` 的 `seg_in`）。
                      # 依据同上：那批新曲里有 2 首（battle/neon）段界 `section_transition`
                      # FAIL（渐变只有 −3~4dB、跳 13~18dB），而 19 首已开它的曲子 0 FAIL。
                      # 0.75 拍 ≈ 0.32s：够盖住判据量的"边界后 0.15s"，又不至于让人听出"段首软"。
                      'seg_in': 0.75,
                      # **段末留白 / 渐弱**（见 `song_engine.build_events` 的 `section_gap`）。
                      # ⚠ 2026-09-19 **改回 3.0**（用户实测判据："**从 0 重新生成的质量没有
                      #   之前高**"）：与 19 首旧曲逐键 diff 后，**唯一实质差异就是这一项**
                      #   （旧曲 3.0 / 新曲 0.5；`programs` / `arr` / 段落结构 / 音色全同）
                      #   —— 听感差别就是"**段落之间有没有呼吸**"。
                      #   0.5 是 2026-09-18 为迁就 `section_transition` 的 `pre` 参考窗口选的
                      #   （留白长过 0.3s 时 `pre` 自己也落进留白、量不出渐变）。但那是
                      #   **为守卫口径牺牲听感**，而且是双输：新曲段界照样 FAIL 4 个，听感还掉了。
                      #   用户方针（2026-09-19）："**怎么样好听就怎么样，重要的是听感好**"。
                      # ⚠ 单位是**拍**，秒数随 bpm 变（3.0 拍 @139BPM ≈ 1.29s）。
                      'section_gap': 3.0,
                      # ↓↓↓ 2026-09-18 补：这三项 + dyn_vel 长期"默认关"，症状一直挂在守卫上
                      #     （用户："为什么不会自动打开？让之后的对话能自动识别打开"）。
                      #     引擎的规矩是"opt-in，默认关 = 老曲字节不变"，所以**开关必须写在
                      #     这里**（新歌默认带、老曲不受影响）—— 写进文档是拦不住的。
                      # **段界力度平滑**（opt-in `patterns.seg_fade`）：给段界处的力度做过渡，
                      # 治 `section_transition` 的"硬切"（实测边界跳 5.1~23.9dB、
                      # 两端渐弱只有 -0.7~3.8dB，门要求 ≥4dB）。
                      'seg_fade': True,
                      # **音区修正 + 弱起音修正**（opt-in `patterns.range_fix`）：旋律钻到伴奏
                      # 音区以下、或主奏起音拖沓时自动修（治"音域只 7 半音"、"听着慢半拍"）。
                      # 引擎里已保证它排在 `legato_trim` **之前**（先改音高会打乱分组）。
                      'range_fix': True,
                      # **去同音高重叠**（opt-in `patterns.legato_trim`）：同音高的相邻音提前
                      # 松键，避免音源把后一个音吞掉（听感"少音"，见坑 157 系列）。
                      'legato_trim': True,
                      # **长音层逐小节力度**（opt-in `patterns.dyn_vel`）：Pad/Strings/Glock
                      # 这些长音轨原本力度只有 **1 种**（实测最平轨 Pad:1，AUDIT 管这叫"打字机"）。
                      'dyn_vel': 8.0},
         'chords': chords, 'melody': melody, 'sections': secs,
         # **模板依据留痕**：check_song / 自检照这份核对"是不是白名单来源、够不够多"
         'theme': {'name': pack['theme'], 'label': pack.get('label'),
                   'pack': 'refs/themes/%s.json' % pack['theme'],
                   'template_count': pack.get('template_count'),
                   'styles': list(pack.get('styles') or []),
                   'source_kinds': dict(pack.get('source_kinds') or {}),
                   'primary_source': (pack.get('harmony') or {}).get('primary_source'),
                   # 混音目标层（对齐到哪个真实混音）—— **不是模板依据**，一并留痕
                   'mix_target': (pack.get('mix_target') or {}).get('ref'),
                   # 段间能量曲线（实际用在各段 arr.mix 上的 dB 偏移；均值为 0，不改整体响度）
                   'energy_curve_db': eused,
                   'energy_gain': effective_gain(pack, energy_gain),
                   'templates': [t['file'] for t in (pack.get('templates') or [])]}}
    return d


# **级进偏好**（`melody_gen --step-bias`）的默认值 —— 用户实测（2026-09-14）：
# 同一骨架的 4 条候选"级进 17% → 52% **越来越顺**，202 之后两条都比原版好"，
# 而旧挑法只看"与库里不像"、完全不看听感维度（根因还有 `persona` 里 `leap` 每首随机
# 0.70~1.40 的两倍范围）→ 同一份画像下会随机挑到跳进多的那条。
# 设 `BGM_STEP_BIAS=0` 可关掉（回到旧行为）；它是**候选之间的相对排序**，不是绝对门槛。
STEP_BIAS = float(os.environ.get('BGM_STEP_BIAS', '1.0'))


def run_melody_gen(song_json, pack, theme, seed, ncand, step_bias=None):
    """用**主题旋律画像**生成旋律（唯一入口 `melody_gen.py`）；失败就大声报错

    非 4/4（如三拍圆舞）时 `melody_gen` 拒绝工作（它按"一小节 16 格"写的）→ 这里**返回 False**
    让调用方以非零码退出：骨架会留在盘上，但**旋律是占位音**，绝不能让下游当成品渲染
    （"不许静默给错答案"）。

    **生成后当场体检（`probe_melody_health`），不合格就降密度重试**：主题画像取自模板，
    像 `battle`（chiptune/game16/32）这种模板本身是十六分跑句的主题，生成出来**碎音 35%**，
    连它自己的画像口径都判"卡卡的"（实测）。不体检的话，这个缺陷要等渲染完的成绩单才暴露，
    甚至只在用户耳朵里暴露。密度阶梯 1.0 → 0.75 → 0.6 → 0.5（下限 1.2 音/小节），
    取第一个"形态无问题"的；全都不行就保留最后一个并**如实报告**剩下的问题。
    """
    import theme_pack as tp
    meter = list(pack.get('meter') or [4, 4])
    if meter not in ([4, 4], [3, 4]):
        print('  !! meter=%s：melody_gen 只支持 4/4 与 3/4 → **旋律需手写**'
              '（song.json 骨架已生成，模板依据仍在 theme 字段里）' % meter)
        return False
    import probe_melody_health as MH
    import melody_gen as MG
    prof = tp.melody_path(theme)
    # **密度上限取 `melody_gen.DENS_MAX`（2.6 音/小节）**，不是画像的 `notes_per_bar`：
    # 后者是混音 F0 跟踪的**上界估计**（cheerful 3.23），照抄实测把密度顶到 3.4 ——
    # 音长被压短、每小节挤成一句（用户口径"密度压回 2.0~2.6"）。单一出处在 melody_gen。
    base = max(1.2, min(MG.DENS_MAX, ((pack.get('melody') or {}).get('notes_per_bar') or 2.0)))
    # 有级进偏好时**候选数至少 4**：只有 1~2 条就无所谓"挑"
    sb = STEP_BIAS if step_bias is None else float(step_bias)
    if sb > 0:
        ncand = max(int(ncand), 4)
    tried, last = [], None
    for k in (1.0, 0.75, 0.6, 0.5):
        dens = round(max(1.2, base * k), 2)
        cmd = [sys.executable, os.path.join(HERE, 'melody_gen.py'), song_json, prof,
               '--seed', str(seed), '--candidates', str(ncand),
               '--tonic', str((pack.get('key') or {}).get('pc') or 0),
               '--dens', '%.2f' % dens, '--avoid', 'songs',
               '--step-bias', '%.2f' % sb,
               # **时值偏好**（2026-09-18 起默认 1.0）：把"时值分布与画像的 TVD"接进候选
               # 打分（`melody_gen.dur_tvd`）。起因：守卫 `melody_matches_profile` 拿
               # **时值维**判"这条旋律像不像它的画像"，而候选打分原先只管落点（`onset_tvd`）
               # —— 实测 3 首生成曲的时值承接度 40%/31%/（05 的落点）卡在门上，接上这一项后
               # 分别升到 54% / 69%，05 的 Outro 落点 TVD 0.702 → 0.284。
               # 它**只在同批候选之间排序**、不改生成逻辑（同 `onset_tvd` / `form_pen`）。
               '--dur-bias', '1.0']
        print('  melody_gen：画像 %s（密度 %.2f 音/小节，%d 候选）'
              % (os.path.relpath(prof, ROOT), dens, ncand))
        r = subprocess.run(cmd, cwd=ROOT)
        if r.returncode != 0:
            raise SystemExit('melody_gen 失败（画像 %s）—— 旋律还是占位音，别往下渲染'
                             % os.path.relpath(prof, ROOT))
        row = MH.probe(song_json)
        iss = MH.issues(row) if row else ['测不到旋律']
        tried.append((dens, iss))
        last = row
        if not iss:
            if len(tried) > 1:
                print('  ✓ 旋律形态体检通过（密度 %.2f；前 %d 档不合格：%s）'
                      % (dens, len(tried) - 1,
                         '；'.join('%.2f→%s' % (d, '、'.join(i)) for d, i in tried[:-1])))
            return True
    print('  !! 旋律形态**仍不合格**（试过 %s）：%s —— 这条不达标，别当成"已修好"'
          % ('、'.join('%.2f' % d for d, _i in tried), '、'.join(tried[-1][1])))
    return True


def fix_melody_register(data, gap_min=None, gap_max=None, verbose=True):
    """**逐段把旋律挪到与和弦合宜的音区**（生成后修正）—— 量法与 `harmony_check` 同一份。

    为什么必须在**生成时**修：实测 `new_song.py ... --force` 直接生成的曲子，8 个段落全违反
    `harmony_check` 第 ① 项 —— A/A2/A3/A4/A5/Outro 的**旋律最低 52–57 落在和弦最高
    (渲染后 59) 之下**（与左手撞在一起），B/B2 的**旋律最低 93 比和弦最高 62 高 31 半音**
    （中间空掉）。而 `patterns.range_fix` **管不到它** —— 那只修"超出乐器合理音域
    `TR_RANGE`"的音（同一次实测里"移八度 0 个"）→ 于是每首新生成的曲子都要人工逐段修音区
    （`20_piano_rain` 原版就是"修前 A+21 / B+26 / C+34 → 修后 9–22"手工磨出来的）。

    修法：**整段移八度**（不改音级 / 和声 / 时值 / 力度 / 音符数 —— 与 `range_fix` 同手法），
    在 `{0, ±12, ±24}` 里挑让**最多段**落进 `[gap_min, gap_max]` 且位移最小的那个。
    返回 `[(旋律键, [(段名, 修前 gap, 修后 gap)], 位移)]`。
    """
    import harmony_check as HC
    lo_g = HC.GAP_MIN if gap_min is None else gap_min
    hi_g = HC.GAP_MAX if gap_max is None else gap_max
    # ---- ⓪ 先把**飘太高**的音降八度（2026-09-22 补：用户"感觉这个音有点高了"）----
    #   实测那次 Intro 开头是 93(A6)/88(E6)/90(F#6)，比该段和弦最高音（59=B3）高 **34 半音**
    #   （近 3 个八度），随后又掉到 76(E5)（17 半音大跳）——而 `harmony_check` 的
    #   "音区间距"只看**最低音**（74−59=15 → 判合规）⇒ 这一侧是盲区，靠耳朵才发现。
    #   手法与手工那次一致：只把**超过上限**（和弦最高 + GAP_MAX）的音降八度，
    #   不动音级 / 时值 / 力度 / 起音。
    # ⚠ 必须在 `bykey` 之前：它改了音高 ⇒ gap 得按**改后**的算。第一版写在 `bykey` 之后，
    #   后面那步又按"降八度之前的旧 gap"移了一次 → 过头（自检 `melody_register_fix`
    #   的 93 那组用例当场报"gap 实得 −12"）。
    n_low = 0
    for (nm, _hi, chi, tg) in HC.register_top_gaps(data):
        if tg <= hi_g:
            continue
        limit = chi + hi_g
        key = next((s.get('melody') for s in data.get('sections', []) if s['name'] == nm), None)
        if key is None:
            continue
        for note in data['melody'].get(key) or []:
            while int(note[3]) > limit:
                note[3] = int(note[3]) - 12
                n_low += 1
    if verbose and n_low:
        print('  音区修正：%d 个音**飘太高**（超和弦最高 +%d 半音）→ 降八度' % (n_low, hi_g))
    bykey = {}
    for (nm, _lo, _chi, gap) in HC.register_gaps(data):
        sec = next(s for s in data.get('sections', []) if s['name'] == nm)
        bykey.setdefault(sec.get('melody'), []).append((nm, gap))
    fixed = []
    # ---- ① 先压**音域跨度**（治本）：跨 3~4 个八度的旋律，任何"整段移八度"都无解 ——
    #   实测 Intro 是 **52–93 = 41 半音**：移 +12 越乐器音域（>103）、移 0 间距仍为负、
    #   移 −12 又低于下限 → 三个约束互相打架，一个都选不出来。而 `SKILL.md` §13 早把
    #   "音域跨 4 个八度"列为听感病根。这里以**中位音高**为中心把越界的音折回（±12 整数倍）。
    SPAN_MAX = 24
    folded = []
    for key in list(bykey):
        notes = data.get('melody', {}).get(key) or []
        ps = [int(n[3]) for n in notes]
        if not ps or max(ps) - min(ps) <= SPAN_MAX:
            continue
        mid = int(round((max(ps) + min(ps)) / 2.0))
        lo_t, hi_t = mid - SPAN_MAX // 2, mid + SPAN_MAX // 2
        nf = 0
        for note in notes:
            m = int(note[3])
            while m > hi_t:
                m -= 12
            while m < lo_t:
                m += 12
            if m != int(note[3]):
                note[3] = m
                nf += 1
        folded.append((key, nf))
    if verbose:
        for (key, nf) in folded:
            ps = [int(n[3]) for n in data['melody'][key]]
            print('  音域压缩：旋律 %s 折回 %d 个音 → 跨度 %d 半音（上限 %d）'
                  % (key, nf, max(ps) - min(ps), SPAN_MAX))
    for key, items in bykey.items():
        if key is None or not data.get('melody', {}).get(key):
            continue
        if all(lo_g <= g <= hi_g for (_n, g) in items):
            continue
        # ⚠ 移八度**必须同时不越出该轨的合理音域**（`song_engine.TR_RANGE`）——
        #   第一版只看"与和弦的间距"，把 Outro 移了 +24 后旋律最高冲到 **105**，
        #   当场被 `track_ranges_musical` 抓成 FAIL（Melody 合理上限 103）。
        #   两者冲突时**音域优先**（硬守卫），间距退而求其次
        #   （`harmony_check` 只在 gap > 22 或 gap < 0 时报，落在 0~22 之间的都不算违反）。
        import song_engine as SE
        rg = SE.TR_RANGE.get('Melody')
        ps = [int(n[3]) for n in data['melody'][key]]
        best, best_cost = 0, None
        for sh in (0, 12, -12, 24, -24):
            if rg and (min(ps) + sh < rg[0] or max(ps) + sh > rg[1]):
                continue
            n_out = sum(1 for (_n, g) in items if not (lo_g <= g + sh <= hi_g))
            cost = (n_out, abs(sh))
            if best_cost is None or cost < best_cost:
                best, best_cost = sh, cost
        if best == 0:
            continue
        for note in data['melody'][key]:
            note[3] = int(note[3]) + best
        fixed.append((key, [(n, g, g + best) for (n, g) in items], best))
    if verbose:
        for (key, items, sh) in fixed:
            print('  音区修正：旋律 %s 移 %+d 半音 —— %s'
                  % (key, sh, ' · '.join('%s %+d→%+d' % (n, a, b) for (n, a, b) in items)))
        if not fixed:
            print('  音区修正：各段旋律↔和弦间距都已落在 %d~%d 半音内' % (lo_g, hi_g))
    # ---- ⓪b 移完八度**再夹一次"飘太高"**（2026-09-22）----
    #   ⚠ 顺序坑：⓪ 在移八度**之前**做，而移八度（+12/+24）会把最高音又推上去 ——
    #   实测重生成后 A/A2/A3/A4/A5 五段"旋律最高 83 比和弦最高高 **24** 半音"（上限 22，
    #   只超 2 半音），就是 ⓪ 降完又被 +24 抬回来的结果。⇒ 移完必须再夹一次。
    n_low2 = 0
    for (nm, _hi, chi, tg) in HC.register_top_gaps(data):
        if tg <= hi_g:
            continue
        limit = chi + hi_g
        key = next((s.get('melody') for s in data.get('sections', []) if s['name'] == nm), None)
        if key is None:
            continue
        for note in data['melody'].get(key) or []:
            while int(note[3]) > limit:
                note[3] = int(note[3]) - 12
                n_low2 += 1
    if verbose and n_low2:
        print('  音区修正：移八度后又夹回 %d 个**飘太高**的音' % n_low2)
    return fixed


def melody_prog_pool(mel_tpl):
    """段级主奏音色的**候选池**（顺序 = 角色首次出现顺序拿到的音色）。

    池序 = **从保守到特色**：0 钢琴 → 13 木琴 → 8 钢片琴 → **模板音色** → 4 电钢 → 24 吉他 → 9 钟琴。

    ⚠ **模板音色不能排池首**（2026-09-22 改，用户："让以后不要发生"）：
    排最前时，**第一个出现的角色 —— 也就是 `intro`（独奏位）—— 会拿到模板音色**。
    实测 `20_piano_rain` 重生成时模板给的是 **GM 80（Lead 1 square 方波）**，
    于是引子成了"高音方波独奏"（首音 93 = A6 · 力度 91），用户原话
    "**前面部分非常奇怪**"；而这段池序注释自己写的就是"从保守到特色"，
    把最特色的音色放在最保守的位置上，是自相矛盾。

    原注释的顾虑"模板音色排最前，否则 `programs.Melody` 会被段级值立刻覆盖 = 白设"
    —— 那个顾虑只要求它**在池子里**，不要求排最前。插在**第 4 位**即可两面都满足：
    引子/主歌/副歌各拿保守音色，桥段或第二个副歌拿到模板音色。
    """
    return tuple(dict.fromkeys([p for p in (0, 13, 8, mel_tpl, 4, 24, 9)
                                if p is not None]))


def legato_melody(data, fill=0.9):
    """**把旋律的音长接到下一个音**（只改时值、起音一个不动）—— 用户 2026-09-22：
    "**重要是更流畅**，不要有突然的突兀杂音"。

    实测现场（`20_piano_rain` 重生成版）：旋律 190 音，**音间空隙中位 +0.332s**、
    **>0.25 拍的空隙占 69%**、**>0.5 拍占 58%、最长 2.27s** —— 听感"一句一句断开"；
    而真实模板（9 首 tender 旋律轨 4849 音）的空隙中位只有 **0.001s**、≥0.3s 仅 **3%**。
    上一版是**手工**修的（`legato_melody.py`：55 个音 0.75→1.35 拍，空隙中位
    0.601→0.157s，用户"流畅度还好"）—— 这里把它做成生成时的默认步骤，别再手工补。

    手法：每条旋律线按 `(小节, 拍)` 排序，**非末音**取
    `dur = max(dur, 到下一音的距离 × fill)`。`fill=0.9 < 1` ⇒ 不会与下一个音重叠；
    **起音一个不动** ⇒ 节奏/碎音率不变，只把"断开"补上（用户的诉求就是补这个）。

    ⚠ 与引擎的 `patterns.legato_trim`（去重叠 = **缩短**）方向相反，两者可共存：
    这里先补空隙，引擎导出时再按需去重叠。

    ⚠ 副作用（与上一版同）：旋律的**时值分布**会离画像更远，`melody_matches_profile`
    的"时值交叠率"可能掉到门槛 40% 以下 —— 那时按既有做法写 `patterns.melody_exempt.dur`
    （**必须写实质理由**，空理由自检会拦），因为听感（不断开）优先于该统计量。
    返回被改长的音数。
    """
    B = float((data.get('meter') or [4, 4])[0] or 4)
    n_fix = 0
    for key, notes in (data.get('melody') or {}).items():
        if not notes or len(notes) < 2:
            continue
        notes.sort(key=lambda z: (float(z[0]), float(z[1])))
        for i in range(len(notes) - 1):
            a, b = notes[i], notes[i + 1]
            gap = (float(b[0]) * B + float(b[1])) - (float(a[0]) * B + float(a[1]))
            if gap <= 0:
                continue
            want = gap * fill
            # ⚠ 判据要带容差、写回要 round 到 3 位：否则 `round(want, 4)` 的值可能仍 < want
            #   → 同一个音每次跑都被"改"一次（实测第二次跑又报"改了 1 个音"，不幂等，
            #   重复调用会逐次漂移）。
            if float(a[2]) < want - 5e-4:
                a[2] = round(want, 3)
                n_fix += 1
    if n_fix:
        # **自动写豁免**：连奏必然把旋律时值分布推离画像（tender 画像的时值中位只有 0.25 拍
        # = 十六分连奏走句），`melody_matches_profile` 的"时值交叠率"会掉到门槛 40% 以下。
        # 守卫自己给的出口就是这条（"确属画像如此就写清理由放行该维"），所以**由生成器写**，
        # 别让每首新曲都留一个 FAIL 等人手工补。理由必须写实（空理由自检会拦）。
        pat = data.setdefault('patterns', {})
        pat.setdefault('melody_exempt', {})['dur'] = (
            '【旋律连奏】按用户口径"重要是更流畅"（2026-09-22）：每条旋律线的**非末音**取 '
            '`dur = max(dur, 到下一音的距离 × 0.9)` —— **只改时值、起音一个不动** ⇒'
            '节奏与碎音率不变，只补掉"一句一句断开"（实测空隙中位 0.332s → 0.088s、'
            '断开 >0.25 拍 69% → 20%、断奏 3% → 0%）。'
            '代价：时值维交叠率掉到门槛 40% 以下 —— tender 画像的时值中位只有 0.25 拍'
            '（十六分连奏走句），而本曲旋律音数少、听感要的是长音。'
            '取舍依据：用户亲耳说"更流畅"是硬诉求，听感 > 该统计量；落点维不受影响。'
            '⚠ 与旧记录"`--dur-bias` 会加密起音、碎音 1%→12%"**不是同一手法**：'
            '那个动起音，这个不动。')
    return n_fix


def dry_compose(song_json):
    """干跑一次引擎（写到临时目录，不留垃圾）：能编配出合法 MIDI 吗？

    为什么生成阶段就要跑：`check_song` 的 `compose_dry_run` 只有**渲染前**才跑，
    而生成阶段写错的数据（如 `arr.mix` 的值写成列表）会一路走到 `make_song` 才崩 ——
    实测白跑一整轮（坑 92 的同一形态）。这里 1 秒内给出可读报错。
    """
    import tempfile
    import song_engine
    try:
        with tempfile.TemporaryDirectory(prefix='newsong_dry_') as td:
            song_engine.compose(song_json, out_mid=os.path.join(td, 'x.mid'))
        return True
    except Exception as e:                                  # noqa: BLE001
        print('  ✗ 编配干跑失败: %s: %s' % (type(e).__name__, e))
        return False


def theme_mode(new, theme, ref_name=None, seed=7, ncand=4, energy_gain=None,
                force=False, bpm=None):
    """`--theme` 路径：按主题模板包生成一首新歌"""
    import theme_pack as tp
    pack = tp.load_pack(theme)
    # ⚠ `--bpm` = **显式指定**：把主题的 BPM 范围"塌缩成一点"，`_bpm_from_pack` 自然取到它
    #   —— 不必把参数一路透传到 `build_from_theme`（少改一处就少一处出错的机会）。
    if bpm:
        # 这里**不打印** —— `build_from_theme` 会打印"BPM x —— 命令行 --bpm 指定"，
        # 两处都打就成了重复行（实测第一版就是这样）。
        pack['bpm'] = {'median': float(bpm), 'p25': float(bpm), 'p75': float(bpm)}
    probs = tp.validate_pack(pack)
    if probs:
        print('✗ 主题包 %s 不合规，先修再生成：' % theme)
        for p in probs[:6]:
            print('   - %s' % p)
        return 1
    short = new.split('_', 1)[1] if '_' in new else new
    dst = os.path.join(SONGS, new)
    if os.path.exists(dst):
        if not force:
            # ⚠ 这里**必须说清"什么都没做"**：旧版只打印一句"已存在"就退出，照字面读不出
            #   "引擎改了也不会进这首曲子" —— 2026-09-18 我加了音色平衡补偿后重跑，看输出
            #   以为生效了，实际 `song.json` 一字未动、`track_balance` 数值也一字未变
            #   （`make_song` 渲染的是旧 `song.json`）。
            print('已存在：%s' % dst)
            print('  ⚠ **未做任何修改** —— 引擎/参数改了也不会进这首曲子。')
            print('    按当前引擎重生成：python scripts\\new_song.py %s --theme %s --force'
                  % (new, theme))
            print('    `--force` 删旧重建；**改完还必须跑 make_song 重渲染**，'
                  '否则 .mid/.ogg 仍是旧的。')
            return 1
        print('  --force：删掉旧目录重建 %s' % new)
        shutil.rmtree(dst)
    os.makedirs(dst)
    data = build_from_theme(pack, short, seed=seed, ncand=ncand, energy_gain=energy_gain)
    song_json = os.path.join(dst, 'song.json')
    json_io.save(song_json, data)
    print('已创建 songs\\%s\\song.json' % new)
    print('  模板依据：%s（%s）· %d 首 · 源 %s'
          % (theme, '/'.join(pack.get('styles') or []), pack.get('template_count', 0),
             ', '.join('%s×%d' % (k, v)
                       for k, v in (pack.get('source_kinds') or {}).items())))
    # ⚠ 这里打印的是**主题包的速度中位数**，不是本曲生成的 BPM（那行由 `_bpm_from_pack` 打）。
    #   两处都叫"BPM"会让人以为"生成的速度=128 固定" —— 实测就是这么被绕进去的。
    print('  和声：%s（来源 %s）· 主题速度中位 %.0f BPM · 调 %s %s'
          % (' '.join((pack.get('harmony') or {}).get('primary') or []),
             (pack.get('harmony') or {}).get('primary_source'),
             (pack.get('bpm') or {}).get('median', 0),
             (pack.get('key') or {}).get('tonic'), (pack.get('key') or {}).get('mode')))
    _used = (data.get('theme') or {}).get('energy_curve_db') or []
    if any(abs(v) >= ENERGY_MIN_DB for v in _used):
        print('  段间能量曲线：k=%.2f · 各段偏移 %s dB（目标 %s 的起伏；均值为 0 → 不改整体响度）'
              % ((data.get('theme') or {}).get('energy_gain', ENERGY_GAIN),
                 _used, (pack.get('mix_target') or {}).get('ref')))
    else:
        print('  段间能量曲线：本次未写（目标画像本身平坦或曲线起伏 <%.1fdB）' % ENERGY_MIN_DB)
    # **干跑一次引擎**（和 `build_song.py` 同一招）：`arr.mix` 这类"数据检查全绿、渲染才崩"
    # 的错要当场发现 —— 坑 92 就是 `arr.mix` 写成 `[pan, level]` 而引擎要**单整数 CC7**，
    # 结果白跑一整轮渲染。这里只写 MIDI（不渲染），不到 1 秒。
    if not dry_compose(song_json):
        print('  → 骨架已生成（songs\\%s\\），但**编配跑不通**：先按上面的报错改 song.json'
              % new)
        return 1
    # 旋律：主题画像驱动
    if not run_melody_gen(song_json, pack, theme, seed, ncand):
        print('  → 骨架已生成（songs\\%s\\），但**旋律还是占位音**：手写 melody 后再 '
              'make_song.py %s --check' % (new, new))
        return 1
    data = json.load(open(song_json, encoding='utf-8'))
    # **旋律↔和弦的音区**（生成后修正；量法与 `harmony_check` 第 ① 项同一份）——
    # 不加这步，新生成的曲子在那一项上会整片 FAIL（实测 8/10 段）。
    fix_melody_register(data)
    # **旋律连奏**（用户 2026-09-22："重要是更流畅"）—— 只延时值、起音不动。
    _lm = legato_melody(data)
    if _lm:
        print('  旋律连奏：%d 个音的音长接到下一个音（空隙按真值补齐，起音一个不动）' % _lm)
    data['theme'] = data.get('theme') or {}
    data['theme']['melody_profile'] = 'refs/themes/%s_melody.json' % theme
    data['theme']['seed'] = seed
    # **标记：同名段落共用旋律**（见 `role_melody_name`）—— 新旧命名的分界，
    # 让守卫 `theme_melody_reuse` 只对"新命名"的曲目判（旧曲目每段一支旋律，不追溯）。
    data['theme']['melody_reuse'] = True
    json_io.save(song_json, data)

    # compose.py（固定桩）
    with open(os.path.join(dst, 'compose.py'), 'w', encoding='utf-8',
              newline='') as f:
        f.write(COMPOSE_STUB % {'name': short})
    # render.json：**频谱对齐画像 = 主题包的混音目标层**（不是模板依据 —— 模板只来自
    # midi2/权威网络数据；这里决定"混成什么样、对齐到哪个真实混音"）。`--ref` 可覆盖。
    cfg = {'composer': 'compose.py', 'mid': short + '.mid', 'out': short + '_sf'}
    ref = None
    mt = pack.get('mix_target') or {}
    ref_use = ref_name or mt.get('ref') or 'bgm01c'
    # **聚合混音画像**落在 `refs/mix_targets/<主题>.json`（多方参考的中位数），
    # 普通画像在 `refs/*.json` —— 两处都找（`--ref` 仍可指定任意一份人工挑的画像）。
    _stem = ref_use[:-5] if ref_use.endswith('.json') else ref_use
    _cands = [os.path.join(REFS, _stem + '.json'),
              os.path.join(REFS, 'mix_targets', _stem + '.json')]
    p = next((q for q in _cands if os.path.exists(q)), _cands[0])
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            ref = json.load(f)
        cfg['ref'] = ref['name']
        if ref_name:
            print('  频谱对齐画像：%s（--ref 指定）—— 它**不是模板依据**' % ref['name'])
        elif ref.get('aggregate'):
            print('  频谱对齐画像：%s（**%d 份真实录音的聚合中位数**，成员 %s）—— 它不是模板依据'
                  % (ref['name'], len(ref.get('members') or []),
                     ', '.join(m['ref'] for m in (ref.get('members') or [])[:4])))
        else:
            print('  频谱对齐画像：%s（主题包 %s 的混音目标，评分 %.2f）—— 它**不是模板依据**'
                  % (ref['name'], theme, mt.get('score') or 0))
        if mt.get('why'):
            print('    判据：%s' % mt['why'])
        alt = [c['ref'] for c in (mt.get('candidates') or [])[1:3]]
        if alt:
            print('    备选：%s（--ref 可换）' % ' / '.join(alt))
    else:
        print('  警告：找不到频谱画像 %s（render.json 未写 ref，成绩单会报"参考画像不存在"）'
              % p)
    cfg.update(auto_render_params(ref) if ref else {})
    with open(os.path.join(dst, 'render.json'), 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    write_notes(dst, new, data, pack, ref)
    print('  下一步: python scripts\\make_song.py %s --check' % new)
    return 0


def write_notes(dst, new, data, pack, ref):
    """notes.md：模板依据（可溯源）+ 复现命令"""
    tpl = pack.get('templates') or []
    lines = ['# %s（主题模板包：%s / %s）\n' % (new, pack['theme'], pack.get('label', '')),
             '| 项目 | 值 |', '|---|---|',
             '| 模板依据 | **%d 首同主题模板聚合**（`refs/themes/%s.json`） |'
             % (pack.get('template_count', 0), pack['theme']),
             '| 主题→风格 | %s（引擎预设 %s） |'
             % ('/'.join(pack.get('styles') or []), pack.get('engine_style')),
             # ⚠ **本曲速度必须单独一行、写成精确的 `| 速度 |`**（2026-09-22 补）：
             #   守卫 `selftest.t_notes_speed_matches` 只认这一种写法 —— 合并列
             #   `| 速度·调式 |` 记的是**模板依据**（按本曲参数判会把 4 首 imitate 曲误报，
             #   见该守卫的 docstring）。只写合并列 ⇒ **新生成的曲子根本不在那条守卫的
             #   覆盖范围内**；实测重生成后的 `20_piano_rain/notes.md` 就缺这一行，
             #   连变异用例的夹具都注入不进去（`replace('**78 BPM**')` 找不到目标）。
             '| 速度 | **%.0f BPM**（四分音符）· %s · %d 小节 |'
             % (float(data.get('bpm') or 0),
                '/'.join(str(x) for x in (data.get('meter') or [4, 4])),
                sum(int(s.get('bars') or 0) for s in (data.get('sections') or []))),
             '| 速度·调式 | 主题中位 %.0f BPM · %s %s（**本曲速度**见上一行；这行是模板依据） |'
             % ((pack.get('bpm') or {}).get('median', 0),
                (pack.get('key') or {}).get('tonic'), (pack.get('key') or {}).get('mode')),
             '| 和声 | %s（来源 %s） |'
             % (' '.join((pack.get('harmony') or {}).get('primary') or []),
                (pack.get('harmony') or {}).get('primary_source')),
             '| 曲式 | %d 段 × %d 小节 = %d 小节 |'
             % (len((pack.get('form') or {}).get('plan') or []),
                (pack.get('form') or {}).get('section_bars', 0),
                (pack.get('form') or {}).get('total_bars', 0)),
             '', '## 模板清单（来源可溯源；.mid 不进仓库，重建见 `fetch_midi_lib.py`）', '',
             '| 模板 | 风格 | 速度 | 来源 |', '|---|---|---|---|']
    for t in tpl:
        lines.append('| `%s` | %s | %.0f | %s |'
                     % (t['file'], t.get('style'), t.get('bpm') or 0, t.get('source') or '-'))
    mel = pack.get('melody') or {}
    lines += ['', '## 主题旋律语言（画像 %d 音）' % mel.get('notes', 0), '',
              '- 音域 %s · %.2f 音/小节 · 级进 %.0f%% · 正拍 %.0f%%'
              % (mel.get('range'), mel.get('notes_per_bar', 0),
                 mel.get('stepwise_pct', 0), mel.get('onbeat_pct', 0))]
    if ref:
        lines += ['', '## 频谱对齐画像（**不是模板**，只用于混音对标）', '']
        if ref.get('aggregate'):
            lines += ['- **%s**：%d 份真实录音画像的**逐维度中位数**（多方参考，见坑 132）'
                      % (ref['name'], len(ref.get('members') or [])),
                      '  —— 响度 %.1f dBFS · 宽度 %.3f · 质心 %dHz'
                      % (ref.get('rms_db') or 0, ref.get('width') or 0,
                         ref.get('centroid') or 0), '',
                      '| 成员参考 | 评分 | 质心 | 来源 |', '|---|---|---|---|']
            for m in (ref.get('members') or []):
                s = m.get('source') or {}
                lines.append('| `%s` | %.3f | %s | %s（%s） |'
                             % (m.get('ref'), m.get('score') or 0, m.get('centroid'),
                                s.get('detail') or s.get('kind') or '未标注',
                                s.get('file') or s.get('url') or '-'))
            lines += ['', "> 来源结构：`source` 支持 `kind='file'`（项目素材包，带文件名）与",
                      "> `kind='web'`（网络权威源，带 `url`/`license`）—— 混音参考**必须可溯源**，",
                      '> 守卫 `mix_target_aggregate` 会要求每份成员都有 `source`。']
        else:
            lines += ['- %s：响度 %.1f dBFS · 宽度 %.3f · 质心 %dHz'
                      % (ref.get('name'), ref.get('rms_db', 0), ref.get('width', 0),
                         ref.get('centroid', 0))]
    lines += ['', '## 复现', '', '```powershell',
              '$py = "<工具链根>/venv/python.exe"',
              'cd <工具链根>',
              '& $py scripts\\theme_pack.py %s        # 模板包（模板不足时 --allow-fetch 联网抓）'
              % pack['theme'],
              '& $py scripts\\new_song.py %s --theme %s --seed %d'
              % (new, pack['theme'], (data.get('theme') or {}).get('seed', 7)),
              '& $py scripts\\make_song.py %s --check' % new, '```', '',
              '## 还没验证什么', '', '- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）']
    with open(os.path.join(dst, 'notes.md'), 'w', encoding='utf-8', newline='') as f:
        f.write('\n'.join(lines) + '\n')


def _studio_lib():
    """studio 面板的曲库目录（`studio/.libpath` 里那一行）；没有 / 不存在就返回 None。"""
    p = os.path.join(HERE, '..', 'studio', '.libpath')
    try:
        with open(p, encoding='utf-8') as f:
            lib = f.read().strip()
    except OSError:
        return None
    return lib if lib and os.path.isdir(lib) else None


def link_to_studio_lib(name, src):
    """把新曲**挂进 studio 曲库**（junction）—— 否则面板看不到、也播不了。

    ⚠ 这是用户 2026-09-19 报的"**每次新曲都不会出现**"的根因：曲库里每首曲子是一条
      **独立 junction**（不是把整个 `songs/` 挂一次），所以新曲得自己挂一条；
      不挂的话面板 `/api/songs` 列不到它、`/api/audio?id=<曲>` 直接 404。
    ⚠ 必须用 `cmd /c mklink /J`：MSYS 的 `ln -s` 对目录是**复制**（实测白占 460MB）。
      失败只提示、不中断生成 —— 曲子在 `songs/` 里是好的，只是面板暂时看不到。
    """
    lib = _studio_lib()
    if not lib:
        return
    dst = os.path.join(lib, name)
    if os.path.exists(dst):
        print('  已挂面板曲库  ← %s' % dst)
        return
    try:
        r = subprocess.run(['cmd', '/c', 'mklink', '/J', dst, os.path.abspath(src)],
                           capture_output=True)
    except OSError as e:                                             # noqa: BLE001
        print('  !! 挂面板曲库失败（%s）—— 手工挂：cmd /c mklink /J "%s" "%s"'
              % (e, dst, os.path.abspath(src)))
        return
    if r.returncode == 0:
        print('  已挂面板曲库  ← %s（面板现在能看到它）' % dst)
    else:
        print('  !! 挂面板曲库失败：%s\n     手工挂：cmd /c mklink /J "%s" "%s"'
              % (r.stderr.decode('utf-8', 'replace').strip()[:150],
                 dst, os.path.abspath(src)))


def main():
    if '--list-styles' in sys.argv:
        sys.path.insert(0, HERE)
        import song_engine
        print('可用风格预设（song.json 里写 "style": "<名字>"）:')
        for k, v in song_engine.STYLES.items():
            p = v['patterns']
            print('  %-10s %s  [bass=%s perc=%s]'
                  % (k, v.get('desc', ''), p.get('bass_style'),
                     p.get('perc_style')))
        return 0
    if '--list-themes' in sys.argv:
        import theme_pack as tp
        print('可用主题模板包（依据 = 同主题多首 MIDI 模板聚合；跑 `theme_pack.py` 生成）:')
        for k in sorted(tp.THEMES):
            th = tp.THEMES[k]
            n = os.path.exists(tp.pack_path(k))
            print('  %-10s %-6s %-34s %s%s'
                  % (k, th['label'], '/'.join(th['styles']), th['engine'],
                     '' if n else '   （还没建包：theme_pack.py %s）' % k))
        return 0
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    theme = sys.argv[sys.argv.index('--theme') + 1] if '--theme' in sys.argv else None
    if theme:
        args = [a for a in args if a != theme]
    if not args or ('--from' not in sys.argv and not theme):
        print(__doc__)
        return 1
    # 面板守卫（硬形式）：没在跑就先拉起来 —— 见 scripts/studio_guard.py 顶部那段
    import studio_guard
    studio_guard.ensure_panel()
    # A′（2026-09-19 用户拍板）：**默认把生成委托给面板** —— 手敲 CLI 就等于在面板里建任务，
    # GUI 全程可见、产物立刻能听。面板内部调用（BGM_STUDIO_INNER）、批量（BGM_CLI_DIRECT）、
    # 以及面板无法等价表达的调用（没给 --ref / 带 --candidates 等）自动退回原生实现。
    _rc = studio_guard.delegate_new_song(sys.argv)
    if _rc is not None:
        return _rc
    new = args[0]
    ref_name = sys.argv[sys.argv.index('--ref') + 1] if '--ref' in sys.argv else None
    seed = int(sys.argv[sys.argv.index('--seed') + 1]) if '--seed' in sys.argv else 7
    ncand = int(sys.argv[sys.argv.index('--candidates') + 1]) \
        if '--candidates' in sys.argv else 4
    egain = float(sys.argv[sys.argv.index('--energy-gain') + 1]) \
        if '--energy-gain' in sys.argv else None
    # ⚠ `--bpm <值>`（2026-09-20 加）：**显式钉死速度**。不加时 BPM 在主题模板的
    #   **p25~p75 真实范围**里按 seed 取 —— 旧行为是固定用中位数，同一主题每首一样
    #   （实测 battle 3 首全 139、daily 4 首全 128），是"同质化"的直接来源。
    cli_bpm = float(sys.argv[sys.argv.index('--bpm') + 1]) \
        if '--bpm' in sys.argv else None
    force = '--force' in sys.argv
    if theme:
        rc = theme_mode(new, theme, ref_name=ref_name, seed=seed, ncand=ncand,
                        energy_gain=egain, force=force, bpm=cli_bpm)
        # ⚠ **挂曲库必须跟着 `theme_mode` 的出口**：main 末尾那处调用**走不到这里**
        #   —— `--theme` 路径在下面这样一行就 return 了。第一版我把调用写在 main 末尾，
        #   实测 3 首变体全是 404、日志里也没有"已挂面板曲库"（加了 ≠ 生效）。
        if rc == 0:
            link_to_studio_lib(new, os.path.join(SONGS, new))
        return rc
    style = sys.argv[sys.argv.index('--style') + 1] if '--style' in sys.argv else None
    sec_name = (sys.argv[sys.argv.index('--from-sections') + 1]
                if '--from-sections' in sys.argv else None)
    src_name = sys.argv[sys.argv.index('--from') + 1]
    src_dir = os.path.join(SONGS, src_name)
    if not os.path.isdir(src_dir):
        print('模板不存在: %s（可选: %s）'
              % (src_dir, ', '.join(sorted(os.listdir(SONGS)))))
        return 1
    print('  !! `--from` 是**复现/改歌**用的老路径：它复制的是"我们自己做过的曲子"，'
          '不是白名单模板。\n'
          '     按用户口径（模板只能是 refs/midi2 或网络权威数据，且要 ≥8 首同主题），'
          '新歌请用：\n'
          '       python scripts\\new_song.py %s --theme <主题>   （--list-themes 看主题）\n'
          '     本次仍会写入 basis=copied_song 的留痕，check_song 会据此提示不合规。'
          % new)
    short = new.split('_', 1)[1] if '_' in new else new
    dst = os.path.join(SONGS, new)
    if os.path.exists(dst):
        if not force:
            print('已存在：%s' % dst)
            print('  ⚠ **未做任何修改** —— 要重建加 `--force`（会删旧目录）；'
                  '改完还必须跑 make_song 重渲染。')
            return 1
        print('  --force：删掉旧目录重建 %s' % new)
        shutil.rmtree(dst)
    os.makedirs(dst)

    # --- 参考曲画像
    ref = None
    if ref_name:
        p = os.path.join(REFS, ref_name if ref_name.endswith('.json')
                         else ref_name + '.json')
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                ref = json.load(f)
        else:
            print('警告：找不到画像 %s，先用 profile_ref.py 生成' % p)

    # --- song.json（数据骨架）
    src_data = os.path.join(src_dir, 'song.json')
    if not os.path.exists(src_data):
        print('模板 %s 没有 song.json（01-04 号是旧的代码式曲目，不能当模板）。\n'
              '可用的模板: %s' % (src_name, ', '.join(
                  d for d in sorted(os.listdir(SONGS))
                  if os.path.exists(os.path.join(SONGS, d, 'song.json')))))
        return 1
    with open(src_data, encoding='utf-8') as f:
        data = json.load(f)
    data['name'] = short
    data['desc'] = '待填：风格与参考曲'
    if style:
        # 指定风格时，把模板里显式的 programs/mix/patterns 删掉，让预设生效
        data['style'] = style
        for k in ('programs', 'mix', 'patterns'):
            data.pop(k, None)
    if ref:
        data['bpm'] = ref['bpm']
        data['ref'] = ref['name']

    # --- 分段目标 → 自动填"每段编制 + 段落级混音曲线"
    if sec_name:
        _b = sec_name[:-5] if sec_name.endswith('.json') else sec_name
        # 名字容错：`--from-sections hitorigohan2` 与 `hitorigohan2_sections` 都接受；
        # 目录：refs/sections/（新）→ refs/（兼容）
        _cands = []
        # 没有 segments 字段）会先被取到 → 表现为"分段数 0 ≠ 曲目段数 N"（实测踩过）
        for _stem in (_b + '_sections', _b):
            for _d in (os.path.join(REFS, 'sections'), REFS):
                _cands.append(os.path.join(_d, _stem + '.json'))
        sp = next((q for q in _cands if os.path.exists(q)), _cands[0])
        ssecs = data.get('sections') or []
        if not os.path.exists(sp):
            print('警告：找不到分段画像 %s（先跑 analyze_sections.py）' % sp)
        else:
            with open(sp, encoding='utf-8') as f:
                sprof = json.load(f)
            segs = sprof.get('segments') or []
            if len(segs) == len(ssecs) and segs:
                base = data.get('mix') or {}
                for s, seg in zip(ssecs, segs):
                    dev10 = seg['dev'].get('5000-10000', 0.0)
                    dev5 = seg['dev'].get('2500-5000', 0.0)
                    # 系数偏大：实测"段间对比"是听感像不像的关键（参考曲 5-10k 段间
                    # 起伏 9~13dB；系数小则各段趋同、听感平）。上限 ±26 保护可听范围。
                    d10 = max(-26, min(26, round(dev10 * 2.0)))
                    d5 = max(-20, min(20, round(dev5 * 1.4)))
                    mix = dict(base)
                    for tr, delta in (('Glock', d10), ('Perc', (d10 + d5) // 2),
                                      ('Hook', d5 // 2), ('Strings', d5 // 3)):
                        cur = mix.get(tr)
                        lvl = cur[1] if isinstance(cur, (list, tuple)) and len(cur) > 1 else 64
                        pan = cur[0] if isinstance(cur, (list, tuple)) else 64
                        mix[tr] = [pan, max(0, min(127, int(lvl) + delta))]
                    s['arr']['mix'] = mix
                    if dev10 > 2.5:
                        s['arr']['glock'] = True
                    elif dev10 < -2.5:
                        s['arr']['glock'] = False
                print('  分段拟合已写入：%d 段编制+混音曲线（%s）'
                      % (len(segs), os.path.basename(sp)))
            else:
                print('  分段数（%d）≠ 曲目段数（%d）—— 自动拟合需要两边段数一致：\n'
                      '    参考曲 %d 段 → song.json 的 sections 也写成 %d 段'
                      % (len(segs), len(ssecs), len(segs), len(segs)))

    # 老路径的**依据留痕**：说明这是"复制自己做过的曲子"，不是白名单模板依据
    # （自检 `theme_basis_whitelist` 照这条判定；不许静默混过去）
    data['basis'] = {'kind': 'copied_song', 'from': src_name,
                     'note': '模板来源不在白名单（新歌请用 --theme <主题>）'}
    with open(os.path.join(dst, 'song.json'), 'w', encoding='utf-8') as f:
        f.write(json_io.dumps(data))     # 按小节分行：读一遍省 ~40% token

    # --- compose.py 固定桩
    with open(os.path.join(dst, 'compose.py'), 'w', encoding='utf-8',
              newline='') as f:
        f.write(COMPOSE_STUB % {'name': short})

    # --- render.json（自动推算参数）
    cfg = {'composer': 'compose.py', 'mid': short + '.mid', 'out': short + '_sf',
           'ref': ref['name'] if ref else (ref_name or '')}
    cfg.update(auto_render_params(ref) if ref else {})
    with open(os.path.join(dst, 'render.json'), 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)

    # --- notes.md
    if ref:
        bands = ' · '.join('%s %s' % (k, v) for k, v in list(ref['bands'].items())[:5])
        body = ('# %s（仿 %s）\n\n| 项目 | 值 |\n|---|---|\n'
                '| 调性·速度 | 待填（参考 **%.1f BPM**，小节 %.3fs） |\n'
                '| 长度 | 待填（参考 %.0f 秒） |\n| 结构 | 待填 |\n| 和声 | 待填 |\n'
                '| 编配 | 待填 |\n\n'
                '## 参考曲指标（同口径对比用）\n\n'
                '- 响度 **%.1f dBFS** · 宽度 **%.3f** · 质心 **%dHz**\n'
                '- 倍频程：%s\n- 低频节奏型 `%s`\n- 高频节奏型 `%s`\n'
                '- 安静段调式 %s\n\n'
                '## 复现\n\n```powershell\n'
                '$py = "<工具链根>/venv/python.exe"\n'
                'cd <工具链根>\n'
                '& $py scripts\\make_song.py %s\n```\n'
                % (short, ref['name'], ref['bpm'], ref['bar'], ref['duration'],
                   ref['rms_db'], ref['width'], ref['centroid'], bands,
                   ref['rhythm_low'], ref['rhythm_high'],
                   ' '.join('%s(%.2f)' % (k, v) for k, v in
                            list(ref['quiet_chroma'].items())[:4]), new))
    else:
        body = '# %s\n\n待填\n' % short
    with open(os.path.join(dst, 'notes.md'), 'w', encoding='utf-8',
              newline='') as f:
        f.write(body)

    print('已创建 songs\\%s\\' % new)
    # **挂进面板曲库**（否则面板看不到/播不了，见 `link_to_studio_lib` 的说明）
    link_to_studio_lib(new, dst)
    print('  song.json    ← 只改这个（chords / melody / sections）')
    if style:
        print('  风格预设     ← %s（song.json 里显式写的会覆盖预设）' % style)
    print('  render.json  ← 已按参考曲推算：%s'
          % ', '.join('%s=%s' % (k, v) for k, v in cfg.items()
                      if k in ('rms', 'width', 'shelf')))
    print('  下一步: & $py scripts\\make_song.py %s' % new)
    return 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
if __name__ == '__main__':
    sys.exit(main())
