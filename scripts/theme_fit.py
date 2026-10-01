#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""theme_fit.py —— **"这首曲子像不像它的主题"直接检查**（不依赖任何音频大模型）。

## 为什么需要（2026-10-01）

用户："用本地的千问检测歌曲风格，我感觉描述不像 …… **能不能直接检查，不依靠千问**"。
千问（Qwen2-Audio）能出描述，但实测它**读错拍号**（把 3/4 的 `102_waltz_court` 说成 4/4）、
**BPM 乱报**（151 说成 78）、标签带训练集偏见（一堆"爵士"）→ **不能当判据**
（边界见 `docs/AUDIO-CRITIC.md` §7）。

而"像不像主题"**其实是可量的**：主题包里就是模板实测出来的画像 —— BPM 区间 / 拍号 /
音色池 / 段落结构 / 旋律形态。本工具把"曲子的实际值"与"它自己主题的画像"逐项对一遍。

## 它查什么

| 项 | 画像字段 | 判据 |
|---|---|---|
| 速度 | `bpm.p25~p75` | 落在区间 = 符合；超出 ±15% = 偏离 |
| 拍号 | `meter` | 必须相等（`waltz` 的 3/4 就是这么钉住的） |
| **主奏音色** | `arrangement.prog_pool` → `new_song.theme_programs` | 与"生成时会取到的那个"比，**并报它跟几个其它主题相同** |
| 各声部音色 | 同上 | 每个 track 是否在该主题的候选池里 |
| 段落结构 | `form.plan` / `total_bars` | 段数、总小节数 |
| 旋律形态 | `melody.notes_per_bar` / `stepwise_pct` / `range` | 密度、级进率、音域（实量后对比） |

## ⚠ 最有价值的一项：**主题间区分度**（`--themes`）

实测：**15 个主题只有 7 种主奏音色**，其中 **GM 73 长笛独占 8 个主题**
（daily / folk_tale / neon / retro / seaside / sorrow / tender / waltz）——
于是"霓虹电子 / 海边 / 悲伤 / 圆舞曲"生成出来**主奏是同一种长笛**，听感自然都像轻音乐。
**这不是 bug，是画像层面的区分度不足**（模板 MIDI 的主奏本来就多是笛类）。
→ 选主题前先跑 `--themes`，撞了就给主奏换池里的**第二/第三候选**（`new_song --theme` 的
`theme_programs(pick=N)`）或直接改 `programs.Melody`。

## 用法

```bash
python scripts\theme_fit.py <曲目>            # 单曲：逐项对比它自己的主题画像
python scripts\theme_fit.py --themes         # 主题间区分度总表（选主题前先看）
python scripts\theme_fit.py <曲目> --json
python scripts\theme_fit.py --selftest
```
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()          # noqa: E402
import new_song as ns                        # noqa: E402
import song_engine as se                     # noqa: E402

THEMES = os.path.join(ROOT, 'refs', 'themes')
SONGS = os.path.join(ROOT, 'songs')


def gm_name(p):
    try:
        from extract_theme_timbres import GM
        return GM.get(int(p), '?')
    except Exception:                                             # noqa: BLE001
        return '?'


def load_pack(theme):
    p = os.path.join(THEMES, theme + '.json')
    if not os.path.exists(p):
        raise SystemExit('没有主题包 %s（用 `theme_pack.py --list-themes` 看可用的）' % p)
    return json.load(open(p, encoding='utf-8'))


def theme_rows():
    """全部主题 → 画像摘要 + 生成时会用的音色（同一份口径：直接调 `new_song.theme_programs`）"""
    rows = []
    for f in sorted(os.listdir(THEMES)):
        if not f.endswith('.json') or f.endswith('_melody.json'):
            continue
        pack = json.load(open(os.path.join(THEMES, f), encoding='utf-8'))
        rows.append({'theme': f[:-5], 'label': pack.get('label'),
                     'engine': pack.get('engine_style'), 'bpm': pack.get('bpm') or {},
                     'meter': pack.get('meter'), 'styles': pack.get('styles') or [],
                     'progs': {k: v[0] for k, v in ns.theme_programs(pack).items()},
                     'pool': (pack.get('arrangement') or {}).get('prog_pool') or {},
                     'form': pack.get('form') or {}, 'melody': pack.get('melody') or {}})
    return rows


def lead_groups(rows):
    """主奏音色 → 哪些主题共用（区分度报告）"""
    g = {}
    for r in rows:
        g.setdefault(r['progs'].get('Melody'), []).append(r['theme'])
    return g


def _melody_stats(d):
    """实量该曲的旋律形态（与画像同一口径：密度 / 级进率 / 音域）"""
    notes = []
    pos = 0.0
    bars = 0
    for sec in d.get('sections') or []:
        nb = int(sec.get('bars') or 0)
        bars += nb
        for x in (d.get('melody') or {}).get(sec.get('melody') or '', []) or []:
            if 0 <= x[0] < nb:
                notes.append((pos + x[0] * 4.0 + x[1], x[2], int(x[3])))
        pos += nb * 4.0
    notes.sort()
    if not notes:
        return None
    ps = [p for (_t, _d2, p) in notes]
    steps = [abs(ps[i + 1] - ps[i]) for i in range(len(ps) - 1)]
    return {'notes': len(notes), 'bars': bars,
            'notes_per_bar': round(len(notes) / float(max(1, bars)), 2),
            'stepwise_pct': round(100.0 * sum(1 for s in steps if s <= 2) / max(1, len(steps))),
            'range': [min(ps), max(ps)],
            'range_span': max(ps) - min(ps)}


def fit(path, rows=None):
    """→ (报告 dict, 人话行 list)。`path` = 曲目名或 song.json 路径"""
    if path.endswith('.json'):
        sj = path
        name = os.path.basename(os.path.dirname(os.path.abspath(sj)))
    else:
        name = path
        sj = os.path.join(SONGS, path, 'song.json')
    if not os.path.exists(sj):
        raise SystemExit('找不到 %s' % sj)
    d = json.load(open(sj, encoding='utf-8'))
    theme = (d.get('theme') or {}).get('name')
    if not theme:
        return None, ['%s 没有 `theme` 标记（不是主题路径生成的）—— 本工具只查主题路径的曲子' % name]
    rows = rows or theme_rows()
    pack = next((r for r in rows if r['theme'] == theme), None)
    if pack is None:
        return None, ['主题包 %s 不存在' % theme]
    # ⚠ **`pack` 是"画像行"，不是主题包 JSON**（2026-10-01 踩到）：判据要查"音色在不在候选池里"
    #   就得读真正的包文件 —— 第一版拿画像行去调 `ns.track_candidates()`，池取不到 →
    #   静默退化成"等于首选"（**判据看着还是 ✓，其实没查**）。这里显式读包。
    pj = load_pack(theme)
    exp = pack['progs']
    got = {k: (v[0] if isinstance(v, (list, tuple)) else v)
           for k, v in (d.get('programs') or {}).items()}
    b = pack['bpm'] or {}
    rep = {'song': name, 'theme': theme, 'label': pack['label'], 'items': []}

    def add(item, ok, got_v, want, note=''):
        rep['items'].append({'item': item, 'ok': ok, 'got': got_v, 'want': want, 'note': note})

    # ① 速度
    bpm = float(d.get('bpm') or 0)
    lo, hi = float(b.get('p25') or 0), float(b.get('p75') or 0)
    med = float(b.get('median') or 0)
    add('速度 BPM', lo <= bpm <= hi, bpm, '%.1f~%.1f（中位 %.1f）' % (lo, hi, med),
        '' if lo <= bpm <= hi else ('超出区间 ±15%% 以内算可接受：%s'
                                    % ('可接受' if abs(bpm - med) <= 0.15 * max(1.0, med) else '偏离')))
    # ② 拍号
    mt = d.get('meter') or [4, 4]
    add('拍号', list(mt) == list(pack['meter'] or [4, 4]), '%s/%s' % (mt[0], mt[1]),
        '%s/%s' % tuple(pack['meter'] or [4, 4]))
    # ③ 主奏音色（且报与几个其它主题相同）
    #    ⚠ 判据 = **在该主题的主奏候选池里**（2026-10-01 改）：生成端现在会**按 seed 在
    #    候选池里挑**（`new_song.theme_programs(seed=…)` / 段级 `melody_prog` 序列），
    #    所以"必须等于票数第一那个"会误报。`programs.Melody` 本身设计上仍留画像首选。
    lead = got.get('Melody')
    same = [t for t in lead_groups(rows).get(lead, []) if t != theme]
    lead_pool = ns.track_candidates(pj, 'Melody') or [exp.get('Melody')]
    add('主奏音色', lead in lead_pool,
        '%s %s' % (lead, gm_name(lead)),
        '%s %s（池 %s）' % (exp.get('Melody'), gm_name(exp.get('Melody')),
                            '/'.join(str(p) for p in lead_pool)),
        ('⚠ 与 %d 个其它主题**同一个音色**：%s' % (len(same), ', '.join(same))) if same else '')
    # ④ 各声部音色是否**在该声部的候选池里**（池成员判定，不是"等于第一候选"）
    bad, off_pick = [], []
    for k, v in got.items():
        if k == 'Perc' or k not in exp:
            continue
        cands = ns.track_candidates(pj, k)
        if cands and v not in cands:
            bad.append('%s=%s 不在池 %s' % (k, v, '/'.join(str(p) for p in cands)))
        elif v != exp[k]:
            off_pick.append('%s=%s（首选 %s）' % (k, v, exp[k]))
    add('其它声部音色', not bad,
        ', '.join('%s=%s' % (k, got[k]) for k in sorted(got) if k != 'Perc'),
        ', '.join('%s=%s' % (k, exp[k]) for k in sorted(exp) if k != 'Perc'),
        ('**不在池里**：%s' % '；'.join(bad)) if bad else
        (('与画像首选不同但仍在池内：%s' % '、'.join(off_pick)) if off_pick else ''))
    # ⑤ 段落结构
    bars = sum(int(s.get('bars') or 0) for s in d.get('sections') or [])
    tot = (pack['form'] or {}).get('total_bars')
    add('段落 / 总小节', True, '%d 段 / %d 小节' % (len(d.get('sections') or []), bars),
        '画像模板中位 %s 小节' % (tot if tot else '—'))
    # ⑥ 旋律形态（实量 vs 画像）
    ms = _melody_stats(d)
    if ms:
        mw = pack['melody'] or {}
        add('旋律密度（音/小节）', True, ms['notes_per_bar'], '画像 %.2f' % (mw.get('notes_per_bar') or 0))
        add('旋律级进率', True, '%d%%' % ms['stepwise_pct'], '画像 %s%%' % mw.get('stepwise_pct'))
        add('旋律音域', True, '%d~%d（%d 半音）' % (ms['range'][0], ms['range'][1], ms['range_span']),
            '画像 %s' % (mw.get('range') or '—'))
    rep['melody'] = ms
    return rep, None


def print_fit(rep):
    print('== %s（主题 %s / %s）' % (rep['song'], rep['label'], rep['theme']))
    for it in rep['items']:
        print('   %s %-16s 实际 %-28s 画像 %s%s'
              % ('✓' if it['ok'] else '✗', it['item'], str(it['got'])[:28], it['want'],
                 ('   ' + it['note']) if it['note'] else ''))
    print('   ⚠ 判据只比"与主题画像的一致性"——它不判好听，也不判"主题名对不对'
          '（那是画像本身的问题，看 `--themes`）"')


def print_themes(rows):
    print('== 主题画像总表（%d 个）' % len(rows))
    print('%-10s %-6s %-9s %-16s %-6s %s'
          % ('主题', '标签', '引擎', 'BPM 区间(中位)', '拍号', '主奏音色'))
    for r in rows:
        b = r['bpm']
        print('%-10s %-6s %-9s %-16s %-6s %s %s'
              % (r['theme'], r['label'], r['engine'],
                 '%s-%s (%s)' % (b.get('p25'), b.get('p75'), b.get('median')),
                 '%s/%s' % tuple(r['meter'] or [4, 4]),
                 r['progs'].get('Melody'), gm_name(r['progs'].get('Melody'))))
    g = lead_groups(rows)
    dup = {k: v for k, v in g.items() if len(v) > 1}
    print('\n== 主奏音色区分度：%d 个主题 → %d 种主奏音色' % (len(rows), len(g)))
    for k, v in sorted(dup.items(), key=lambda kv: -len(kv[1])):
        print('   ⚠ GM %s %s ← **%d 个主题共用**：%s'
              % (k, gm_name(k), len(v), ', '.join(v)))
    solo = [v[0] for k, v in g.items() if len(v) == 1]
    if solo:
        print('   （独占音色的主题：%s）' % ', '.join(solo))


def selftest():
    """尺子自检：合成画像 + 合成曲目 —— 正例全过、三个反例各自被抓到。"""
    import copy
    rows = theme_rows()
    assert len(rows) >= 8, '主题包太少（%d）' % len(rows)
    lead_g = lead_groups(rows)
    assert len(lead_g) < len(rows), '所有主题的主奏音色都不同？区分度报告会空转'
    r = rows[0]
    base = {'name': 'fx', 'theme': {'name': r['theme']}, 'bpm': r['bpm'].get('median'),
            'meter': r['meter'], 'programs': {k: [v, se.CH[k]] for k, v in r['progs'].items()},
            'sections': [{'name': 'A', 'bars': 8, 'chords': ['D'], 'melody': 'A'}],
            'melody': {'A': [[0, 0, 1, 60], [1, 0, 1, 62], [2, 0, 1, 64], [3, 0, 1, 65]]},
            'desc': 'x'}
    import tempfile
    td = tempfile.mkdtemp(prefix='themefit_')
    p = os.path.join(td, 'song.json')

    def run(obj):
        with open(p, 'w', encoding='utf-8') as fh:
            json.dump(obj, fh, ensure_ascii=False)
        rep, msg = fit(p, rows)
        assert rep is not None, msg
        return {it['item']: it['ok'] for it in rep['items']}, rep

    ok, rep = run(copy.deepcopy(base))
    assert ok['速度 BPM'] and ok['拍号'] and ok['主奏音色'], '正例该全过：%r' % ok
    b2 = copy.deepcopy(base)
    b2['bpm'] = float(r['bpm'].get('median') or 120) * 2      # 速度翻倍
    ok2, _ = run(b2)
    assert not ok2['速度 BPM'], '速度翻倍必须报偏离'
    b3 = copy.deepcopy(base)
    b3['meter'] = [3, 4] if list(r['meter'] or [4, 4]) != [3, 4] else [4, 4]
    ok3, _ = run(b3)
    assert not ok3['拍号'], '拍号不符必须报'
    b4 = copy.deepcopy(base)
    lead = r['progs'].get('Melody')
    b4['programs']['Melody'] = [(lead + 1) % 96, se.CH['Melody']]
    ok4, _ = run(b4)
    assert not ok4['主奏音色'], '主奏音色不符必须报'
    # ⑤ **池外音色**必须报（2026-10-01 加）：判据从"等于首选"改成"在池里"之后，
    #    必须证明它仍然**有牙齿** —— 把 Strings 换成一个不在该主题候选池里的音色。
    pj = load_pack(r['theme'])
    cands_s = ns.track_candidates(pj, 'Strings')
    if cands_s:
        outside = next(p for p in range(96) if p not in cands_s
                       and p not in ns.SLOW_ATTACK)
        b5 = copy.deepcopy(base)
        b5['programs']['Strings'] = [outside, se.CH['Strings']]
        ok5, _ = run(b5)
        assert not ok5['其它声部音色'], \
            '池外音色（Strings=%d，池 %s）必须报' % (outside, cands_s)
    # ⑥ **池内但非首选**不该报（同一改动方向：多样性是允许的）
    if len(ns.track_candidates(pj, 'Strings')) > 1:
        b6 = copy.deepcopy(base)
        b6['programs']['Strings'] = [ns.track_candidates(pj, 'Strings')[1], se.CH['Strings']]
        ok6, _ = run(b6)
        assert ok6['其它声部音色'], '池内非首选不该报（多样性被误判）'
    import shutil
    shutil.rmtree(td, ignore_errors=True)
    print('theme_fit --selftest：正例全过 · 速度/拍号/主奏音色/池外音色 四个反例各自被抓到'
          '· 池内非首选不误报（%d 个主题 → %d 种主奏音色）' % (len(rows), len(lead_g)))
    return 0


def main():
    ap = argparse.ArgumentParser(description='曲子 vs 主题画像的直接检查（不依赖音频大模型）')
    ap.add_argument('song', nargs='?', help='曲目名或 song.json 路径')
    ap.add_argument('--themes', action='store_true', help='主题间区分度总表')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    rows = theme_rows()
    if a.themes or not a.song:
        if a.json:
            print(json.dumps([{k: v for k, v in r.items() if k != 'pool'} for r in rows],
                             ensure_ascii=False, indent=1))
        else:
            print_themes(rows)
        return 0
    rep, msg = fit(a.song, rows)
    if rep is None:
        for m in msg:
            print(m)
        return 1
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print_fit(rep)
    return 0


if __name__ == '__main__':
    sys.exit(main())
