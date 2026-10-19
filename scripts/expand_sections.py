#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""expand_sections.py —— 把主题包生成的骨架**扩成多段大曲式**（12 段 / 100 小节 / 3 分钟那种）。

## 何时用

`new_song.py --theme` 出来的骨架 = 主题包 `form.plan` 的段数（cheerful/battle/neon 那类
常见 **10 段 / 72 小节 ≈ 2:11** @132BPM）。要写 3 分钟的曲子就得扩段。
**手改 `song.json` 在这里会连环踩三个坑**（都进了 `PITFALLS.md`）：

  ① **`sections[i].chords` 的数量必须等于 `bars`**（坑 242）—— 12 小节的尾声只给 8 个和弦，
     会让 `song_json_buildable` / `density_dynamic_range` / `track_balance` / `compose_dry_run`
     等**十几条守卫一起 `IndexError`**，报错点离真因很远（看起来像引擎崩了）。
  ② 段数 ≠ 主题包 `form.plan` 时，`basis` 里要**同时**有 `kind='theme_pack'`（守卫
     `theme_basis_whitelist` 管）与 `structure_source='theme_pack-plan:<说明>'`
     （守卫 `t_imitate_path_marked` 管）—— 只写一个仍会 FAIL（坑 243）。
  ③ 写盘必须走 `json_io.save`（坑 244）—— `json.dump(indent=1)` 会把每个音符拆一行，
     `song_json_canonical` 判不合格（实测一首 100 小节的歌 5680 行 vs 应 2076 行）。

本工具把这三条**变成代码里的硬校验**，顺手还生成 `patterns.drum_grid.per_bar`
（**逐小节**鼓型 —— 引擎 Perc 音数的主力，不是 `perc_style`，见 `extract_drum_grid.py`）。

## 与另外两条路径的边界

依据仍是**主题模板包**（不是单首参考曲）→ 留痕用 `theme_pack-plan:`；
要按**单首参考曲**的结构重排段落请用 `scripts\imitate_plan.py`（留痕 `imitate:`）。

## 用法

    python scripts\expand_sections.py <曲目> --plan <结构表.json> [--dry-run]
    python scripts\expand_sections.py 60_carnival_days --plan songs\60_carnival_days\expand_plan.json

    --dry-run       只校验 + 打印段表/鼓型统计，**不写盘**（改结构表时反复用这条）
    --bpm N         覆盖结构表里的 bpm
    --new-melody    允许段落引用 `song.json` 里**还不存在**的旋律名
                    （之后**必须先**跑 `melody_gen.py` 把它填上，否则 compose 会缺旋律）

## 让模型来设计（`--brief` / `--json-errors` / `--fix`，2026-10-19）

    python scripts\expand_sections.py --brief gorgeous            # 设计说明书（JSON，喂给模型）
    python scripts\expand_sections.py 130_x --plan p.json --dry-run --json-errors
    python scripts\expand_sections.py 130_x --plan p.json --fix   # 先修结构错，再照常写入

* `--brief <主题>`：一次性给出**合法空间**——可用和弦词表（给曲目时取该曲 `chords` 表）、
  主题包的进行候选、段长惯例、`arr` 合法键、鼓件与 16 格、以及一张**能直接跑**的 `example_plan`。
  模型从它改起，不要从空白写。
* `--json-errors`：校验失败输出 `{"ok":false,"errors":[{"msg":…}]}`、退出码 2（默认是中文 `SystemExit`）。
* `--fix`：写回并修四类硬错（和弦数≠小节 · 未知和弦 → 就近替换 · 非法 `arr` 键 → 丢弃 ·
  `drums.per_bar` 长度 · 缺 `structure_source`）。**只修结构，不改音乐内容**。

## 结构表（`--plan`）字段

```jsonc
{
 "bpm": 133.3,                                     // 可选
 "structure_source": "theme_pack-plan:cheerful-12sec-96bar",   // 可选，缺省自动拼
 "arr_by_role": false,                             // 缺省 false（见下）
 "sections": [
   {"name": "Intro", "bars": 8, "melody": "intro",
    "chords": ["C7","E7","G7","Cmaj7","C7","E7","G7","Cmaj7"],   // **长度必须 == bars**
    "arr": {"piano": true, "perc": 1, "perc_in": 5, "density": 0, "melody_prog": 8},
    "mode": "major"}                                // mode 可选
 ],
 "drums": {
   "patterns": {"chorus": {"kick": [[0,118],[4,108],[8,118],[12,108]],
                           "snare": [[4,108],[12,114]],
                           "hat": [[2,84],[6,76],[10,84],[14,78]],
                           "open": [[14,80]]}},
   "per_bar": ["intro","intro",  null, "chorus", ...]   // 长度必须 == 总小节数
 }
}
```

* `drums.per_bar` 每一项 = **patterns 里的名字**、或 `null`/`{}`（该小节不出鼓）、或直接一个字型 dict。
* 鼓件只认四档：`kick` / `snare` / `hat` / `open`；每项是 `[十六分格 0–15, 力度 1–127]`。
  （不想自己排鼓型就整块省略 `drums` —— 引擎会退回 `perc_style` 的固定套路。）

⚠ **`arr` 的键必须是引擎认的**（`song_engine.ARR_KEYS`）—— 写错会报"无效的编配开关"，
  而且**只在 `dry_compose` 里露一次面**（踩过，见 `ARR_KEYS` 上方那段注释）。
⚠ 缺省把 `patterns.arr_by_role` 写成 `false`：段里的 `arr` 是**手写**的，
  开着 `arr_by_role` 会被"按角色差异化编制"**静默覆盖**（`new_song` 会打印一条警告，但很容易漏看）。
  想交给引擎先验就显式写 `"arr_by_role": true`。
⚠ 本工具**不生成旋律**。引用了新旋律名（如 `D`/`E`）时加 `--new-melody`，
  然后跑：`python scripts\melody_gen.py songs\<曲>\song.json refs\themes\<主题>_melody.json --rhythm-cells …`
⚠ 改完 `song.json` **必须重作曲再渲染**：`python scripts\make_song.py <曲目>`。
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cli_utf8 as _cu                                              # noqa: E402
_cu.setup()

import json_io                                                      # noqa: E402
import song_engine as SE                                            # noqa: E402

ROOT = os.path.dirname(HERE)
SONGS = os.path.join(ROOT, 'songs')

THEME_PLAN_PREFIX = 'theme_pack-plan:'      # 留痕前缀（变异用例注入这一条来验检查坏得起来）
LEGAL_SRC_RE = re.compile(r'^(?:imitate:|theme_pack-plan:)\S+$')
DRUM_BANDS = ('kick', 'snare', 'hat', 'open')

# ── 详细编排：**arr 的键名合法还不够，值也必须合法**（2026-10-19）──────────────
# 以前只查"键在不在 ARR_KEYS 里"，值写错（`density: 9` / `perc: "on"`）会被引擎
# 静默夹取或忽略 —— 对模型来说就是"我写了但没生效，而且没人告诉我"。
# 这张表只登记**值域确定**的键（不确定的不登记，避免误杀）。
ARR_VALUE_RULES = {
    'density': ('int', 0, 4),
    'perc': ('int', 0, 2),
    'harmony_add': ('int', 0, 2),
    'glock_from_bar': ('int', 0, 64),
    'ending_fade': ('int', 0, 32),
    'glock_oct': ('int', -24, 48),
    'perc_target': ('num', 0, 64),
    'melody_prog': ('int', 0, 127),
}

# 段级 `arr` 键 → 引擎产出的轨名（只登记**一一对应**的；`uku/glock_starved/…` 不猜）
ARR_TO_TRACK = {
    'piano': 'Piano', 'ep': 'Hook', 'strings': 'Strings', 'glock': 'Glock',
    'bass': 'Bass', 'pad': 'Pad', 'arp': 'Arp', 'perc': 'Perc',
}

TRACK_NAMES = ('Melody', 'Hook', 'Piano', 'Strings', 'Pad', 'Glock', 'Bass', 'Arp', 'Perc')

# `strict` 时**清掉**的"引擎启发式"——它们都会改音符/时值/力度，而 plan 没点名要它们。
# 依据（2026-10-19 实测这一首）：`joint_cadence` 让位 12 个起音 · `legato_fill` 补长 349 个音 ·
# `range_fix` 抬 130 个弱起音 · `seg_fade`/`mix_ramp` 改力度。编排方没写就不该发生。
# ⚠ 只清"编辑类"；`perc_style`/`bass_style`/`arpeggio`/`drum_grid`/`guitar_beats` 是**生成参数**，保留。
STRICT_CLEAR = (
    'joint_cadence', 'joint_phrase_bars', 'legato_fill', 'avoid_lead', 'phrase_vary',
    'seg_fade', 'section_gap', 'mix_ramp', 'range_fix', 'legato_trim',
    'melody_dyn', 'dyn_vel', 'space', 'guitar_vary', 'swing', 'swing_humanize',
    'melody_exempt', 'lead_prog_source',
)


def _check_arr_values(tag, arr):
    """段级 `arr` 的**值**校验（键名合法性在 `validate_plan` 里查）。

    模型最容易犯的不是"键写错"，而是"键对、值离谱"（`density: 9` / `perc: "on"` /
    `prog` 用轨名以外的字符串）—— 这类错在引擎里会**静默夹取或忽略**，所以必须在这里拦。
    """
    for k, v in arr.items():
        rule = ARR_VALUE_RULES.get(k)
        if rule:
            kind, lo, hi = rule
            if k == 'perc' and isinstance(v, bool):
                continue                      # 老写法 `perc: true/false` 仍收
            num_ok = (isinstance(v, int) and not isinstance(v, bool)) if kind == 'int' \
                else (isinstance(v, (int, float)) and not isinstance(v, bool))
            if not num_ok or not (lo <= float(v) <= hi):
                raise PlanError('%s 的 arr.%s=%r 不合法：要 %s，范围 %s~%s'
                                % (tag, k, v, kind, lo, hi))
        if k == 'prog':
            if not isinstance(v, dict):
                raise PlanError('%s 的 arr.prog 必须是 {轨名: GM 音色号}' % tag)
            for t, pr in v.items():
                if t not in TRACK_NAMES:
                    raise PlanError('%s 的 arr.prog 键 %r 不是合法轨名（%s）'
                                    % (tag, t, ', '.join(TRACK_NAMES)))
                if isinstance(pr, bool) or not isinstance(pr, int) or not (0 <= pr <= 127):
                    raise PlanError('%s 的 arr.prog[%r]=%r 必须是 0~127 的整数'
                                    % (tag, t, pr))
        if k == 'shift':
            if not isinstance(v, dict):
                raise PlanError('%s 的 arr.shift 必须是 {轨名: 半音数}' % tag)
            for t, sh in v.items():
                if t not in TRACK_NAMES:
                    raise PlanError('%s 的 arr.shift 键 %r 不是合法轨名（%s）'
                                    % (tag, t, ', '.join(TRACK_NAMES)))
                if isinstance(sh, bool) or not isinstance(sh, int) or sh % 12:
                    raise PlanError('%s 的 arr.shift[%r]=%r 必须是 12 的倍数（整八度）'
                                    % (tag, t, sh))


class PlanError(ValueError):
    """结构表不合法 —— **不写盘**（与 imitate_plan 同口径：不过就退出）。"""


def die(msg):
    raise SystemExit('expand_sections: %s' % msg)


# ── 校验 ────────────────────────────────────────────────────────────────────
def validate_plan(plan, song=None, allow_new_melody=False):
    """结构表硬校验。**不过就抛 `PlanError`，调用方不许写盘。**

    返回 `(sections, total_bars)`。`song` 给了就再查"和弦名/旋律名是否存在"。

    ⚠ 这里是**本工具唯一的判据出处**：`selftest.t_expand_sections_contract` 直接调它，
    `mutation_check` 把它换成 no-op 来验"检查真会报警"（换掉后反例不再抛 → 自检必 FAIL）。
    """
    if not isinstance(plan, dict):
        raise PlanError('结构表必须是 JSON 对象')
    secs = plan.get('sections')
    if not isinstance(secs, list) or not secs:
        raise PlanError('sections 必须是非空数组')
    arr_keys = set(getattr(SE, 'ARR_KEYS', ())) | set(getattr(SE, 'ARR_KEYS_EXTRA', ()))
    known_chords = set((song or {}).get('chords') or {})
    known_mel = set((song or {}).get('melody') or {})
    total, seen_names = 0, set()
    for i, s in enumerate(secs):
        tag = '第 %d 段' % (i + 1)
        if not isinstance(s, dict):
            raise PlanError('%s 不是对象' % tag)
        name = str(s.get('name') or '').strip()
        if not name:
            raise PlanError('%s 缺 name' % tag)
        tag = '%s(%s)' % (tag, name)
        if name in seen_names:
            raise PlanError('%s 段名重复 —— `melody_gen` 按"段名→角色"取旋律，重名会串' % tag)
        seen_names.add(name)
        bars = s.get('bars')
        if not isinstance(bars, int) or bars < 1:
            raise PlanError('%s 的 bars 必须是 ≥1 的整数（现在 %r）' % (tag, bars))
        chords = s.get('chords')
        if not isinstance(chords, list) or not chords:
            raise PlanError('%s 缺 chords（每小节一个，长度必须等于 bars=%d）' % (tag, bars))
        # ★ 坑 242：数量不等 → 十几条守卫连环 IndexError
        if len(chords) != bars:
            raise PlanError(
                '%s：和弦 %d 个 ≠ 小节 %d 个 —— **每个小节都要一个和弦**'
                '（少给会让十几条守卫连环 IndexError，见 PITFALLS 242）'
                % (tag, len(chords), bars))
        if known_chords:
            miss = [c for c in chords if c not in known_chords]
            if miss:
                raise PlanError('%s 用了 song.json 的 chords 表里没有的和弦：%s'
                                '（先在 chords 里定义，或改成本曲已有和弦）'
                                % (tag, sorted(set(miss))))
        mel = str(s.get('melody') or '').strip()
        if not mel:
            raise PlanError('%s 缺 melody（填旋律槽名，如 "A"/"B"/"intro"/"outro"）' % tag)
        if known_mel and mel not in known_mel and not allow_new_melody:
            raise PlanError('%s 引用新旋律名 %r —— 加 `--new-melody`（之后必须先跑 melody_gen）'
                            % (tag, mel))
        arr = s.get('arr')
        if arr is not None:
            if not isinstance(arr, dict):
                raise PlanError('%s 的 arr 必须是对象' % tag)
            if arr_keys:
                bad = sorted(set(arr) - arr_keys)
                if bad:
                    raise PlanError('%s 的 arr 有引擎不认的键 %s（合法键 = song_engine.ARR_KEYS'
                                    ' + ARR_KEYS_EXTRA：%s）'
                                    % (tag, bad, ', '.join(sorted(arr_keys))))
            _check_arr_values(tag, arr)
        total += bars
    # ── 鼓型 ──
    drums = plan.get('drums')
    if drums is not None:
        if not isinstance(drums, dict):
            raise PlanError('drums 必须是对象')
        pats = drums.get('patterns') or {}
        if not isinstance(pats, dict):
            raise PlanError('drums.patterns 必须是对象')
        for pname, pat in pats.items():
            if not isinstance(pat, dict):
                raise PlanError('鼓型 %r 必须是对象' % pname)
            bad = sorted(set(pat) - set(DRUM_BANDS))
            if bad:
                raise PlanError('鼓型 %r 有引擎不认的鼓件 %s（只认 %s）'
                                % (pname, bad, '/'.join(DRUM_BANDS)))
            for band, seq in pat.items():
                if not isinstance(seq, list):
                    raise PlanError('鼓型 %r 的 %s 必须是 [[格,力度], …]' % (pname, band))
                for it in seq:
                    if (not isinstance(it, (list, tuple)) or len(it) != 2
                            or not (0 <= int(it[0]) <= 15) or not (1 <= int(it[1]) <= 127)):
                        raise PlanError('鼓型 %r 的 %s 有非法项 %r'
                                        '（要求 [十六分格 0–15, 力度 1–127]）' % (pname, band, it))
        pbars = drums.get('per_bar')
        if pbars is not None:
            if not isinstance(pbars, list):
                raise PlanError('drums.per_bar 必须是数组（每小节一项）')
            if len(pbars) != total:
                raise PlanError('drums.per_bar %d 项 ≠ 总小节 %d 项'
                                '（尾奏改长/改短后最容易漏改这里）' % (len(pbars), total))
            for i, it in enumerate(pbars):
                if it is None or it == {}:
                    continue
                if isinstance(it, str):
                    if it not in pats:
                        raise PlanError('drums.per_bar[%d]=%r 不在 drums.patterns 里' % (i, it))
                elif not isinstance(it, dict):
                    raise PlanError('drums.per_bar[%d] 只能是鼓型名 / null / 字型对象' % i)
    # ── 留痕 ──
    src = plan.get('structure_source')
    if src is not None and not LEGAL_SRC_RE.match(str(src)):
        raise PlanError('structure_source=%r 不合规：必须匹配 %s'
                        '（守卫 t_imitate_path_marked 只认这两个前缀）'
                        % (src, LEGAL_SRC_RE.pattern))
    return secs, total


def build_per_bar(drums, total_bars):
    """`drums.per_bar` → 引擎要的 `patterns.drum_grid.per_bar`（扁平逐小节列表）。"""
    if not drums:
        return None
    pats = drums.get('patterns') or {}
    pbars = drums.get('per_bar')
    if pbars is None:
        return None
    out = []
    for it in pbars:
        if it is None or it == {}:
            out.append({})
        elif isinstance(it, str):
            out.append({b: [list(x) for x in v] for b, v in (pats.get(it) or {}).items()})
        else:
            out.append({b: [list(x) for x in v] for b, v in it.items()})
    if len(out) != total_bars:
        raise PlanError('鼓型 %d 小节 ≠ 总小节 %d' % (len(out), total_bars))
    return out


def apply_plan(song, plan, allow_new_melody=False):
    """**纯函数**：返回改好的 song dict（不改入参、不碰磁盘）—— 便于自检直接调用。"""
    secs, total = validate_plan(plan, song=song, allow_new_melody=allow_new_melody)
    out = json.loads(json.dumps(song))              # 深拷贝（结构表可能很大，不走 copy.deepcopy 的坑）
    if plan.get('bpm') is not None:
        out['bpm'] = float(plan['bpm'])
    new_secs = []
    for s in secs:
        # 键序照 `new_song` 的惯例（name/bars/chords/melody/arr/mode）——
        # `json_io` 按插入序输出，顺序变了会让"同一份数据"的字节不同（哈希对不上，
        # 排查"工具到底改没改东西"时很费劲；实测就是这么发现的）。
        sec = {'name': s['name'], 'bars': int(s['bars']),
               'chords': list(s['chords']), 'melody': s['melody'],
               'arr': dict(s.get('arr') or {})}
        if s.get('mode'):
            sec['mode'] = s['mode']
        for k in ('melody_extra',):                 # 允许透传的段级扩展字段
            if s.get(k):
                sec[k] = s[k]
        new_secs.append(sec)
    out['sections'] = new_secs
    out.setdefault('patterns', {})
    # 透传：`programs` / `patterns` / `mix` / `desc`（口径同 `imitate_plan` 的同名字段）
    for key in ('programs', 'mix'):
        if plan.get(key):
            out[key] = dict(out.get(key) or {})
            out[key].update(plan[key])
    if plan.get('patterns'):
        out['patterns'].update({k: v for k, v in plan['patterns'].items()
                                if k not in ('arr_by_role', 'drum_grid')})
    if plan.get('desc'):
        out['desc'] = plan['desc']
    # ★ `arr_by_role` 缺省关掉：段里的 arr 是手写的，开着会被"按角色差异化编制"静默覆盖
    out['patterns']['arr_by_role'] = bool(plan.get('arr_by_role', False))
    grid = build_per_bar(plan.get('drums'), total)
    if grid is not None:
        out['patterns']['drum_grid'] = {'per_bar': grid}
    # ★ 坑 243：两个字段各被一条守卫盯着，缺一个就 FAIL
    src = plan.get('structure_source')
    if not src:
        theme = (out.get('theme') or {}).get('name') or 'theme'
        src = '%s%s-%dsec-%dbar' % (THEME_PLAN_PREFIX, theme, len(new_secs), total)
    out['basis'] = dict(out.get('basis') or {})
    out['basis']['kind'] = 'theme_pack'
    out['basis']['structure_source'] = str(src)
    return out, total


# ── 给 LLM 用：设计说明书 / 机器可读报错 / 自动修复 ────────────────────────
PACKS = os.path.join(ROOT, 'refs', 'themes')


def _pack_of(theme):
    """主题包 → (theme_pack 模块, 包 dict)。**只读**，不改任何状态。"""
    import theme_pack as tp
    p = os.path.join(PACKS, '%s.json' % theme)
    if not os.path.exists(p):
        die('找不到主题包 %s —— 可用主题见 `theme_pack.py --list-themes`' % p)
    return tp, json.load(open(p, encoding='utf-8'))


def design_brief(theme, song=None):
    """**给 LLM 的设计说明书**（`--brief <主题>` 输出 JSON）。

    为什么要它（2026-10-19）：让模型"设计"曲子时，它必须先知道**合法空间**——
    可用和弦词表、段长惯例、`arr` 合法键、鼓件与格数 —— 否则产出必然在
    `validate_plan` 上撞墙（撞一次就废一轮）。这里把"能写什么"一次给全。

    `legal_chords` 的口径：**给曲目就取该曲 `song.json` 的 chords 表**（那才是
    `validate_plan` 认的唯一集合）；不给曲目就取主题包 `chord_pool`（= 建曲时的词表）。
    """
    tp, pack = _pack_of(theme)
    pc = int(((pack.get('key') or {}).get('pc')) or 0)
    h = pack.get('harmony') or {}
    pool = []
    for c in (h.get('chord_pool') or []):
        sym = tp.NAMES[(pc + int(c.get('degree') or 0)) % 12] + (c.get('suffix') or '')
        pool.append({'symbol': sym, 'roman': c.get('roman'),
                     'degree': c.get('degree'), 'count': c.get('count')})
    legal, seen = [], set()
    for c in pool:
        if c['symbol'] not in seen:
            seen.add(c['symbol'])
            legal.append(c['symbol'])
    # 进行里真实用到的和弦也必须算合法 —— 建曲时 `song.json` 的 chords 表就是
    # 「pool ∪ 进行里出现的符号」，只给 pool 会让示例结构表用上词表外的名字（实测踩到）
    for p in (h.get('progressions_4') or []) + (h.get('progressions_2') or []):
        for sym in (p.get('symbols') or []):
            if sym not in seen:
                seen.add(sym)
                legal.append(sym)
    if song and song.get('chords'):
        legal = sorted(song['chords'])                    # 曲目已有和弦表 = 唯一可用集
    primary = list(h.get('primary') or [])
    return {
        'usage': '把 example_plan 改成你的设计，存成 .json 后给 `expand_sections.py <曲> --plan <文件>`',
        'theme': theme,
        'meter': pack.get('meter') or [4, 4],
        'bpm': pack.get('bpm') or {},
        'key': pack.get('key') or {},
        'legal_chords': legal,
        'chords_detail': pool,
        'progressions': [
            {'symbols': p.get('symbols'), 'romans': p.get('romans'),
             'count': p.get('count'), 'share': p.get('template_share')}
            for p in (h.get('progressions_4') or []) if p.get('usable') is not False],
        'primary_progression': primary,
        'section_bars_typical': (pack.get('form') or {}).get('section_bars'),
        'form_example': (pack.get('form') or {}).get('plan') or [],
        'arr_keys': list(getattr(SE, 'ARR_KEYS', ())) + list(getattr(SE, 'ARR_KEYS_EXTRA', ())),
        'section_knobs': {
            'bool_keys': list(getattr(SE, 'ARR_KEYS', ())),
            'ranges': {k: '%s %s~%s' % v for k, v in ARR_VALUE_RULES.items()},
            'maps': {
                'prog': '{轨名: GM 音色号 0~127} —— 段级换音色，轨名 ∈ %s' % (TRACK_NAMES,),
                'shift': '{轨名: ±12 的倍数} —— 段级整八度移调（保音级）',
            },
            'tracks': list(TRACK_NAMES),
        },
        'arr_share': (pack.get('arrangement') or {}).get('arr_share') or {},
        'drum_bands': list(DRUM_BANDS),
        'drum_slots': 16,
        'rules': [
            '每段 chords 的长度必须 == bars（坑 242）',
            '和弦只能用 legal_chords 里的名字（不要自造）',
            'arr 的键只能用 arr_keys',
            '段落名不许重复；旋律槽名 = 想复用的动机名（同名 = 共用旋律）',
            'structure_source 用 theme_pack-plan:llm-<主题>-<总小节>bar',
            'drums 可整块省略（引擎退回 perc_style）',
        ],
        'example_plan': _example_plan(pack, theme, primary, legal),
    }


def _example_plan(pack, theme, primary, legal):
    """按主题包自己的段式铺一张**能直接跑**的结构表（LLM 从它改起，而不是从空白写）。"""
    form = (pack.get('form') or {}).get('plan') or []
    prog = primary or legal[:4] or ['C']
    secs, total, seen = [], 0, {}
    for i, p in enumerate(form):
        name = str(p.get('name') or ('S%d' % (i + 1)))
        bars = int(p.get('bars') or 8)
        base = re.sub(r'\d+$', '', name) or name          # A2 → A
        # **段名必须唯一**（`validate_plan` 会拦），但旋律槽要复用：
        # 第 2 次出现叫 `A2`、旋律仍是 `A` —— 这正是"同名角色共用一支旋律"的口径
        seen[base] = seen.get(base, 0) + 1
        sec_name = base if seen[base] == 1 else '%s%d' % (base, seen[base])
        chords = [prog[(j + i) % len(prog)] for j in range(bars)]
        secs.append({'name': sec_name, 'bars': bars, 'melody': base, 'chords': chords})
        total += bars
    return {'structure_source': 'theme_pack-plan:llm-%s-%dbar' % (theme, total),
            'sections': secs}


def fix_plan(plan, song=None):
    """**自动修复** LLM 最常见的四类硬错（坑 242 类），返回 `(新 plan, 修复说明列表)`。

    只修**结构错误**，不动音乐内容：和弦数量对齐、未知和弦就近替换、未知 arr 键丢弃、
    `drums.per_bar` 对齐小节数、补 `structure_source`。修不了的（如段名重复）留给报错。
    """
    import copy
    out = copy.deepcopy(plan)
    fixes = []
    secs = out.get('sections')
    if not isinstance(secs, list):
        return out, fixes
    legal = sorted((song or {}).get('chords') or {})
    legal_pcs = []
    if legal:
        try:
            import theme_pack as tp
            for s in legal:
                _pc, _suf = tp.split_symbol(s)
                if _pc is not None:
                    legal_pcs.append((_pc % 12, s))
        except Exception:
            legal_pcs = []
    arr_keys = set(getattr(SE, 'ARR_KEYS', ())) | set(getattr(SE, 'ARR_KEYS_EXTRA', ()))
    total = 0
    for i, s in enumerate(secs):
        if not isinstance(s, dict):
            continue
        bars = s.get('bars')
        bars = bars if isinstance(bars, int) and bars >= 1 else 8
        s['bars'] = bars
        total += bars
        chords = s.get('chords') or []
        if not isinstance(chords, list):
            chords = []
        had = len(chords)
        if had < bars:
            fill = chords[-1] if chords else (legal[0] if legal else 'C')
            chords = chords + [fill] * (bars - had)
            fixes.append('%s: 和弦补齐 %d → %d' % (s.get('name'), had, bars))
        elif had > bars:
            fixes.append('%s: 和弦截断 %d → %d' % (s.get('name'), had, bars))
            chords = chords[:bars]
        if legal_pcs:
            fixed = []
            for c in chords:
                if c in legal:
                    fixed.append(c)
                    continue
                try:
                    import theme_pack as tp
                    cp, _sf = tp.split_symbol(str(c))
                except Exception:
                    cp = None
                if cp is None:
                    fixed.append(legal[0])
                    fixes.append('%s: 认不出的和弦 %r → %s' % (s.get('name'), c, legal[0]))
                    continue
                best = min(legal_pcs, key=lambda t: min((cp - t[0]) % 12, (t[0] - cp) % 12))[1]
                fixed.append(best)
                fixes.append('%s: 和弦 %s 不在词表 → 就近替换 %s' % (s.get('name'), c, best))
            chords = fixed
        s['chords'] = chords
        arr = s.get('arr')
        if isinstance(arr, dict):
            bad = [k for k in arr if k not in arr_keys]
            for k in bad:
                arr.pop(k, None)
            if bad:
                fixes.append('%s: 丢弃非法 arr 键 %s' % (s.get('name'), bad))
    drums = out.get('drums')
    if isinstance(drums, dict) and isinstance(drums.get('per_bar'), list):
        pats = drums.get('patterns') if isinstance(drums.get('patterns'), dict) else {}
        for _pname, _pat in list(pats.items()):
            if isinstance(_pat, dict):
                for _b in [b for b in _pat if b not in DRUM_BANDS]:
                    _pat.pop(_b, None)
                    fixes.append('鼓型 %r: 丢弃非法鼓件 %r' % (_pname, _b))
        pb = drums['per_bar']
        # 先修"引用了不存在的鼓型名"——否则长度对齐了照样过不了校验
        for _i, _it in enumerate(pb):
            if isinstance(_it, str) and _it not in pats:
                pb[_i] = None
                fixes.append('drums.per_bar[%d]: 鼓型 %r 未定义 → null' % (_i, _it))
        if len(pb) < total:
            fill = next((x for x in reversed(pb) if x), None)
            pb += [fill] * (total - len(pb))
            fixes.append('drums.per_bar 补齐 → %d 项' % total)
        elif len(pb) > total:
            del pb[total:]
            fixes.append('drums.per_bar 截断 → %d 项' % total)
    if not out.get('structure_source'):
        out['structure_source'] = 'theme_pack-plan:llm-%dbar' % total
        fixes.append('补 structure_source = %s' % out['structure_source'])
    return out, fixes


def arrangement_report(out):
    """**逐段 × 逐轨**音符数 + "指令未生效"清单（`--report`）。

    为什么要有它：模型写 `"arp": true` 之后，引擎可能因为该轨没内容而**整轨跳过**
    （只打印一行 `(跳过空轨: Arp)`）。对模型来说这是**静默失败**——它不知道自己的指令
    被吃掉了。这里把"每段每轨实际出了多少音"与"要了这层却 0 音"一次列清楚，
    让"详细编排"变成可核对的账，而不是写完就赌。
    """
    import copy
    dd = copy.deepcopy(out)
    # ⚠ `build_events` 的桶是按 `programs` 的键建的（`ev = {k: [] for k in d['programs']}`），
    #   而 `song.json` 里没有 Perc/Arp 这两个键（引擎在 `load()` 里补）——
    #   直接喂会 `KeyError: 'Perc'`（实测踩到）。这里补成与 `load()` 同口径。
    progs = dd.setdefault('programs', {})
    for _t in TRACK_NAMES:
        progs.setdefault(_t, [0, 0])
    dd.setdefault('bar_beats', 4.0)
    ev, _nbars = SE.build_events(dd)      # ⚠ 返回的是 (ev, nbars)，不是单个 dict
    B = 4.0
    rows, bar0 = [], 0
    for s in (out.get('sections') or []):
        nb = int(s.get('bars') or 0)
        t0, t1 = bar0 * B, (bar0 + nb) * B
        counts = {}
        for tr, notes in ev.items():
            n = sum(1 for it in notes if t0 - 1e-6 <= it[0] < t1 - 1e-6)
            if n:
                counts[tr] = n
        arr = s.get('arr') or {}
        dead = ['%s→%s' % (k, tr) for k, tr in ARR_TO_TRACK.items()
                if arr.get(k) and not counts.get(tr)]
        rows.append({'name': s.get('name'), 'bars': nb, 'notes': counts, 'dead': dead})
        bar0 += nb
    return rows


# ── 入口 ────────────────────────────────────────────────────────────────────
def song_path_of(arg):
    if arg.endswith('.json') and os.path.exists(arg):
        return os.path.abspath(arg)
    p = os.path.join(SONGS, arg, 'song.json')
    if not os.path.exists(p):
        die('找不到 %s（给曲目名或直接给 song.json 路径）' % p)
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='把主题包生成的骨架扩成多段大曲式（含逐小节鼓型）',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('song', nargs='?', help='曲目名（songs/<名>）或 song.json 路径')
    ap.add_argument('--plan', help='结构表 JSON')
    ap.add_argument('--dry-run', action='store_true', help='只校验 + 打印，不写盘')
    ap.add_argument('--bpm', type=float, default=None, help='覆盖结构表里的 bpm')
    ap.add_argument('--new-melody', action='store_true',
                    help='允许引用还不存在的旋律名（之后必须先跑 melody_gen）')
    # ── 面向 LLM 的三个开关（2026-10-19；只增不改，缺省行为逐字节不变）──
    ap.add_argument('--brief', metavar='主题',
                    help='打印**给模型的设计说明书**（合法和弦/段长/arr 键/鼓件/示例结构表，JSON）')
    ap.add_argument('--json-errors', action='store_true',
                    help='校验失败时输出机器可读 JSON（{"ok":false,"errors":[…]}），退出码 2')
    ap.add_argument('--fix', action='store_true',
                    help='**写回**结构表并自动修复常见硬错（和弦数≠小节、未知和弦、非法 arr 键、'
                         'drums.per_bar 长度、缺 structure_source）')
    ap.add_argument('--strict', action='store_true',
                    help='**严格服从编排**：引擎不再让步/移八度夹取/去重（写 patterns.strict），'
                         '只报告"本来会怎么动"。也可在结构表里写 "strict": true')
    ap.add_argument('--report', action='store_true',
                    help='打印**逐段×逐轨**音符数与"指令未生效"清单'
                         '（核对"写下的编排到底有没有落地"）')
    a = ap.parse_args(argv)

    if a.brief:
        song = json_io.load(song_path_of(a.song)) if a.song else None
        print(json.dumps(design_brief(a.brief, song), ensure_ascii=False, indent=1))
        return 0
    if not a.song or not a.plan:
        die('用法：expand_sections.py <曲目> --plan <结构表.json>'
            '（或 --brief <主题> 先拿设计说明书）')

    sp = song_path_of(a.song)
    if not os.path.exists(a.plan):
        die('找不到结构表 %s' % a.plan)
    plan = json.load(open(a.plan, encoding='utf-8'))
    if a.bpm is not None:
        plan['bpm'] = a.bpm
    song = json_io.load(sp)
    if a.fix:
        plan, fixes = fix_plan(plan, song=song)
        # ⚠ 结构表不是 song.json，不走 `json_io.save`（那是 song.json 的规范格式）
        with open(a.plan, 'w', encoding='utf-8') as f:
            json.dump(plan, f, ensure_ascii=False, indent=1)
        print('自动修复 %d 处（已写回 %s）：' % (len(fixes), a.plan))
        for x in fixes:
            print('  ·', x)
    try:
        out, total = apply_plan(song, plan, allow_new_melody=a.new_melody)
    except PlanError as e:
        if a.json_errors:
            print(json.dumps({'ok': False, 'errors': [{'msg': str(e)}]},
                             ensure_ascii=False, indent=1))
            return 2
        die('%s\n  （**未做任何修改**）' % e)

    if a.strict or plan.get('strict'):
        pat = out.setdefault('patterns', {})
        keep = dict(plan.get('patterns') or {})
        cleared = [k for k in STRICT_CLEAR if k in pat and k not in keep]
        for k in cleared:
            pat.pop(k, None)
        pat.update({k: v for k, v in keep.items() if k not in ('arr_by_role', 'drum_grid')})
        pat['strict'] = True
        print('strict：引擎**不再**让步 / 移八度夹取 / 去重 —— 只报告"本来会怎么动"')
        print('       已清掉 plan 没点名的引擎启发式 %d 项：%s'
              % (len(cleared), ', '.join(cleared) if cleared else '无'))
        print('       ⚠ 自动调参仍会调 EQ/宽度（不动音符与 mix）；要完全不动：make_song --no-tune')

    bpm = float(out.get('bpm') or 120.0)
    meter = out.get('meter') or [4, 4]
    beats = total * float(meter[0]) * (4.0 / float(meter[1]))
    dur = beats * 60.0 / bpm
    print('段数 %d · 小节 %d · %.1f BPM · 时长 %.1fs = %d:%04.1f'
          % (len(out['sections']), total, bpm, dur, int(dur // 60), dur % 60))
    print('旋律槽：', sorted({s['melody'] for s in out['sections']}))
    have = set((song.get('melody') or {}))
    miss = sorted({s['melody'] for s in out['sections']} - have)
    if miss:
        print('  !! 缺旋律：%s —— 写完必须跑 melody_gen 填上（否则 compose 缺旋律）'
              % ', '.join(miss))
    grid = (out.get('patterns', {}).get('drum_grid') or {}).get('per_bar')
    if grid is not None:
        nz = [sum(len(v) for v in b.values()) for b in grid]
        print('鼓型：%d 小节 · 逐小节鼓点 %d~%d（空小节 %d 个）'
              % (len(grid), min(nz), max(nz), sum(1 for x in nz if x == 0)))
    print('留痕：basis.kind=%s · structure_source=%s · arr_by_role=%s'
          % (out['basis']['kind'], out['basis']['structure_source'],
             out['patterns']['arr_by_role']))
    for s in out['sections']:
        arr = s['arr']
        on = [k for k in ('uku', 'piano', 'ep', 'strings', 'glock', 'pad', 'arp',
                          'shimmer', 'harmony') if arr.get(k)]
        print('   %-8s %2d 小节  mel=%-6s perc=%s dens=%-4s prog=%-4s %s'
              % (s['name'], s['bars'], s['melody'], arr.get('perc'),
                 arr.get('density'), arr.get('melody_prog'), ','.join(on)))
    if a.report:
        rows = arrangement_report(out)
        tracks = [t for t in TRACK_NAMES if any(r['notes'].get(t) for r in rows)]
        print('逐段 × 逐轨（音符数，来自引擎实际产出）：')
        print('  %-8s %3s  %s' % ('段', '小节', ' '.join('%6s' % t for t in tracks)))
        for r in rows:
            print('  %-8s %3d  %s' % (r['name'], r['bars'],
                  ' '.join('%6s' % (r['notes'].get(t) or '-') for t in tracks)))
        dead = [(r['name'], d) for r in rows for d in r['dead']]
        if dead:
            print('  !! 指令未生效（要了这层，但该段 0 音 —— 查音区/音色池/引擎跳过原因）：')
            for nm, d in dead:
                print('     · %s: %s' % (nm, d))
        else:
            print('  ✓ 所有"要了的层"都有音')
    if a.dry_run:
        print('（--dry-run：**未写盘**）')
        return 0
    json_io.save(sp, out)
    print('已写盘：%s（json_io 规范格式）' % sp)
    print('  下一步：① 缺旋律就先跑 melody_gen  ② `check_song.py %s`  ③ `make_song.py %s`'
          % (os.path.basename(os.path.dirname(sp)), os.path.basename(os.path.dirname(sp))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
