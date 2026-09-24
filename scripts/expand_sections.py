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
    ap.add_argument('song', help='曲目名（songs/<名>）或 song.json 路径')
    ap.add_argument('--plan', required=True, help='结构表 JSON')
    ap.add_argument('--dry-run', action='store_true', help='只校验 + 打印，不写盘')
    ap.add_argument('--bpm', type=float, default=None, help='覆盖结构表里的 bpm')
    ap.add_argument('--new-melody', action='store_true',
                    help='允许引用还不存在的旋律名（之后必须先跑 melody_gen）')
    a = ap.parse_args(argv)

    sp = song_path_of(a.song)
    if not os.path.exists(a.plan):
        die('找不到结构表 %s' % a.plan)
    plan = json.load(open(a.plan, encoding='utf-8'))
    if a.bpm is not None:
        plan['bpm'] = a.bpm
    song = json_io.load(sp)
    try:
        out, total = apply_plan(song, plan, allow_new_melody=a.new_melody)
    except PlanError as e:
        die('%s\n  （**未做任何修改**）' % e)

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
