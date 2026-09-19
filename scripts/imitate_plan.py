#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""imitate_plan.py —— **模仿写歌路径**：按某一首参考曲的实测结构重写 song.json 的段落层。

为什么要独立成一个工具（用户 2026-09-19："把模仿写歌和直接作曲的功能和文档分开，防止错用"）：

  · **直接作曲**（`new_song.py --theme`）的段落来自**主题模板包**的 `form.plan`；
  · **模仿写歌**要按**单首参考曲**的实测结构（每 8 小节块的起音数曲线 / 调式 / 段落数）重排段落。
    这一步以前是**手写临时脚本**干的（一个任务一份、散在仓库外、没有校验）——
    实测在这个环节撞到两类**静默走样**，都是"命令 ok、成品不像"：
      ① 段名写 `Rise` / `Peak` / `Quiet` → `song_engine.role_of_section` 取"段名里第一个 a–e 字母"，
         它们**全落进角色 `E`** → 守卫报"同名段落 E 用了 5 支不同旋律"；
      ② 同角色的段用了**两条不同进行** → 复用处强拍不合弦（`melody_gen` 报"复用段冲突"，
         强拍贴合率掉到 93~96%）。
    本工具把这两条连同段数/和弦数/参数范围写成**硬校验**：不过就退出、不写盘。

与另外两条路径的边界（**别混**）：
  · 依据永远是**主题模板包**（≥8 首同主题 MIDI）—— `--ref` 只是**混音目标**，不是结构依据；
  · 抄音符是**还原**（`docs/RESTORE-METHOD.md`），不是模仿：模仿的旋律由 `melody_gen` 新写。

用法:
  python scripts\imitate_plan.py <曲目> --plan <结构表.json> [--dry-run]

  --dry-run  只校验 + 打印段表，不写盘（改结构表时反复用这条）

结构表（`--plan`）字段:
  ref            参考曲画像名（写进 `ref` 与留痕）
  ref_file       参考音频路径（留痕用，可省）
  ref_duration   参考时长秒（校验总小节用，可省）
  bpm            参考曲**钉死**的 BPM 层（两层都成立时必须显式给）
  meter          拍号，缺省 [4,4]（分析侧只支持 4/4）
  bars_expected  参考曲总小节数（容差 ±2）
  density_curve  每 8 小节块的起音数（**证据**，原样写进留痕）
  chords         {"和弦名": [bass音高, [和弦音…]], …}
  progressions   {"P_A": ["和弦名" × N], …}（每条长度 ≥ 用到它的段长）
  sections       [{"name","bars","prog","density","perc","perc_target","arr":[…]",
                   "melody_prog","extra":{…}}, …]
  patterns/programs/mix   可选，整体覆盖 song.json 的对应键
  desc           可选

输出（写盘时）:
  · `sections` / `chords` / `bpm` / `meter`（+ patterns/programs/mix）
  · `melody` 清空（**必须**重跑 melody_gen）
  · `basis.structure_source = 'imitate:<ref>'` + `basis.imitate = {…}`（守卫 `t_imitate_path_marked` 照它判）
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cli_utf8 as _cu                                              # noqa: E402
_cu.setup()

import json_io                                                      # noqa: E402
import song_engine as SE                                            # noqa: E402

ROOT = os.path.dirname(HERE)
SONGS = os.path.join(ROOT, 'songs')
ARR_KEYS = ('bass', 'piano', 'uku', 'strings', 'pad', 'arp', 'glock', 'ep', 'shimmer')
IMITATE_PREFIX = 'imitate:'          # 留痕前缀（变异用例注入这一条来验检查坏得起来）


def die(msg):
    raise SystemExit('imitate_plan: %s' % msg)


def role_hits_name(name):
    """段名里必须**出现** A–E 里的字母。

    为什么单列一条：`role_of_section` 在找不到 a–e 字母时**兜底返回 'A'**（静默），
    `Rise`/`Peak`/`Quiet` 这类名字则会命中**名字里的 `e`** → 全归 `E`。
    两种都会让"同角色共用旋律"的机制算错，所以命名先卡死。
    """
    s = str(name or '').strip().lower()
    if 'intro' in s or 'outro' in s or s.startswith('out'):
        return True
    head = s.split('(')[0].split('_')[0].strip()
    return any('a' <= c <= 'e' for c in head)


def build(plan):
    """结构表 → (sections, 错误清单)"""
    errs = []
    chords_tab = plan.get('chords') or {}
    progs = plan.get('progressions') or {}
    raw = plan.get('sections') or []
    if len(raw) < 4:
        errs.append('段落数 %d < 4 —— 这个长度谈不上曲式（守卫也要 ≥2 个角色）' % len(raw))
    out = []
    for i, s in enumerate(raw):
        nm = s.get('name')
        if not nm:
            errs.append('第 %d 段没有 name' % (i + 1))
            continue
        if not role_hits_name(nm):
            errs.append('第 %d 段名 %r 里没有 A–E 字母 → role_of_section 会走兜底，'
                        '同角色共用旋律的机制算错（改成 A/A2/B/C… 或 Intro/Outro）' % (i + 1, nm))
        bars = int(s.get('bars') or 0)
        if bars <= 0:
            errs.append('第 %d 段 %s: bars=%r 非法' % (i + 1, nm, s.get('bars')))
            continue
        prog = s.get('prog')
        seq = progs.get(prog)
        if not seq:
            errs.append('第 %d 段 %s: 进行 %r 不在 progressions 里' % (i + 1, nm, prog))
            continue
        if len(seq) < bars:
            errs.append('第 %d 段 %s: 进行 %s 只有 %d 个和弦 < %d 小节'
                        % (i + 1, nm, prog, len(seq), bars))
            continue
        bad = [c for c in seq[:bars] if c not in chords_tab]
        if bad:
            errs.append('第 %d 段 %s: 和弦 %s 不在 chords 表里' % (i + 1, nm, sorted(set(bad))))
            continue
        dens = s.get('density')
        if dens is None or not (0 <= int(dens) <= 4):
            errs.append('第 %d 段 %s: density=%r 越界（0–4，见 docs/IMITATE-PATH.md §2）'
                        % (i + 1, nm, dens))
            continue
        perc = int(s.get('perc') or 0)
        if not (0 <= perc <= 2):
            errs.append('第 %d 段 %s: perc=%r 越界（0–2）' % (i + 1, nm, perc))
        arr = s.get('arr') or []
        unk = [k for k in arr if k not in ARR_KEYS]
        if unk:
            errs.append('第 %d 段 %s: 编配键 %s 不认识（可选 %s）'
                        % (i + 1, nm, unk, list(ARR_KEYS)))
        prog_no = s.get('melody_prog')
        if prog_no is not None and not (0 <= int(prog_no) <= 127):
            errs.append('第 %d 段 %s: melody_prog=%r 不是合法 GM 音色号' % (i + 1, nm, prog_no))
        a = {k: (k in arr) for k in ARR_KEYS}
        a['perc'] = perc
        a['density'] = int(dens)
        if prog_no is not None:
            a['melody_prog'] = int(prog_no)
        if s.get('perc_target') is not None:
            a['perc_target'] = s['perc_target']
        a.update(s.get('extra') or {})
        role = ''.join(c for c in nm if not c.isdigit())
        out.append({'name': nm, 'bars': bars, 'chords': list(seq[:bars]), 'melody': role,
                    'arr': a, 'mode': s.get('mode') or plan.get('mode') or 'minor'})

    # ② 同角色：同一条进行 + 同一支旋律（否则复用段强拍不合弦）
    by_role = {}
    for s in out:
        by_role.setdefault(SE.role_of_section(s['name']), set()).add(
            (s['melody'], tuple(s['chords'])))
    for role, keys in sorted(by_role.items()):
        mel = {k[0] for k in keys}
        prg = {k[1] for k in keys}
        if len(mel) > 1:
            errs.append('角色 %s 用了 %d 支不同旋律 %s —— 同名段落必须共用一支'
                        % (role, len(mel), sorted(mel)))
        if len(prg) > 1:
            errs.append('角色 %s 用了 %d 条不同进行 —— 复用段的强拍会不合弦'
                        % (role, len(prg)))
    if len({s['melody'] for s in out}) < 2:
        errs.append('全曲只有一支旋律 —— 没有曲式可言（至少要两个角色）')

    # ④ 总小节
    total = sum(s['bars'] for s in out)
    exp = plan.get('bars_expected')
    if exp and abs(total - int(exp)) > 2:
        errs.append('总小节 %d 与参考曲 %s 相差 %d（容差 ±2）'
                    % (total, exp, abs(total - int(exp))))
    return out, total, errs


def show(plan, secs, total):
    print('参考曲 %s · %s BPM · 参考 %s 小节 → 本曲 %d 段 / %d 小节 ≈ %.1fs'
          % (plan.get('ref'), plan.get('bpm'), plan.get('bars_expected'), len(secs), total,
             total * 4 * 60.0 / float(plan.get('bpm') or 120)))
    print('%-8s %5s %-9s %7s %5s %-26s %s'
          % ('段', '小节', '进行', 'density', 'perc', '编配', '主奏音色'))
    for s in secs:
        a = s['arr']
        on = ','.join(k for k in ARR_KEYS if a.get(k))
        print('%-8s %5d %-9s %7d %5d %-26s %s'
              % (s['name'], s['bars'], s['chords'][0] + '…', a['density'], a['perc'],
                 on[:26], a.get('melody_prog', '-')))


def main():
    ap = argparse.ArgumentParser(description='模仿写歌：按参考曲结构重写段落层（硬校验）')
    ap.add_argument('song', help='曲目（songs/ 下的目录名）')
    ap.add_argument('--plan', required=True, help='结构表 JSON（格式见本文件 __doc__）')
    ap.add_argument('--dry-run', action='store_true', help='只校验 + 打印，不写盘')
    a = ap.parse_args()

    sp = os.path.join(SONGS, a.song, 'song.json')
    if not os.path.isfile(sp):
        die('找不到 %s（先跑 new_song.py --theme 出骨架）' % sp)
    if not os.path.isfile(a.plan):
        die('找不到结构表 %s' % a.plan)
    plan = json.load(open(a.plan, encoding='utf-8'))
    d = json.load(open(sp, encoding='utf-8'))

    secs, total, errs = build(plan)
    show(plan, secs, total)
    if errs:
        print('\n!! 校验不通过（%d 条）—— 未写盘：' % len(errs))
        for e in errs:
            print('   · ' + e)
        return 1
    if a.dry_run:
        print('\n--dry-run：校验通过，未写盘。')
        return 0

    d['bpm'] = float(plan['bpm'])
    d['meter'] = list(plan.get('meter') or [4, 4])
    d['chords'] = dict(plan['chords'])
    # **逐段和弦序列没变就别动旋律**（清了就得重跑 melody_gen + 重渲染一轮）。
    # 变了则必须清空 —— 旋律是按旧和弦写的，留着会强拍不合弦（走 check_song 也照样难查）。
    keep_mel = bool(d.get('melody')) and (
        [s.get('chords') for s in (d.get('sections') or [])] == [s['chords'] for s in secs])
    d['sections'] = secs
    if not keep_mel:
        d['melody'] = {}
    if plan.get('desc'):
        d['desc'] = plan['desc']
    for k in ('patterns', 'programs', 'mix'):
        if plan.get(k):
            d[k] = plan[k]
    b = d.get('basis') or {}
    # ⚠ **依据字段必须留着**：模仿只换**段落层**，模板依据仍然是主题模板包 ——
    #   只写 structure_source 会让 `theme_basis_whitelist` 读到 `basis.kind=None`
    #   判成"依据不是白名单"（本轮实测被自检抓到）。
    b['kind'] = 'theme_pack'
    b['structure_source'] = IMITATE_PREFIX + str(plan['ref'])
    b['imitate'] = {
        'ref': plan['ref'],
        'plan': os.path.basename(a.plan),
        'ref_file': plan.get('ref_file'),
        'ref_duration': plan.get('ref_duration'),
        'bars': total,
        'sections': len(secs),
        'density_curve': plan.get('density_curve') or [],
        'note': '结构来自参考曲实测（每 8 小节块起音数曲线）；模板依据仍是 theme 包的模板',
    }
    d['basis'] = b
    open(sp, 'w', encoding='utf-8').write(json_io.dumps(d))
    print('\n已写回 %s' % sp)
    print('留痕 basis.structure_source=%s' % b['structure_source'])
    if keep_mel:
        print('逐段和弦与写盘前一致 → **旋律保留**（不用重跑 melody_gen）')
    else:
        print('和弦序列变了 → **melody 已清空**：必须重跑 melody_gen，再 check_song / make_song')
    return 0


if __name__ == '__main__':
    sys.exit(main())
