#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只给现有主题包**注入密度曲线**，不动其他任何字段。

为什么不直接 `theme_pack.py --all` 重建（踩过）：
重建会**连旋律画像一起重算**，实测 29 个包全漂移
（battle 的 `melody.notes` 1552 → 1508）—— 于是 `selftest` 的
`melody_matches_profile` 立刻报"生成旋律离画像太远：49_battle_rock"
（旧曲子是按**旧画像**生成的，新画像一来就不匹配了）。
**密度曲线是"加法"，不该顺带改别人的旋律画像。**

做法：读现有 `refs/themes/<theme>.json` → 从该主题的模板 MIDI 算密度曲线
→ 只写 `mix_target.density_curve_db` / `density_meta` → 原样写回。
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
os.chdir(ROOT)
import cli_utf8 as _cu; _cu.setup()          # noqa: E402
import song_density as SD                     # noqa: E402
import theme_pack as TP                       # noqa: E402


def main():
    force = '--force' in sys.argv
    done, skip = [], []
    for p in sorted(glob.glob(os.path.join(ROOT, 'refs', 'themes', '*.json'))):
        name = os.path.basename(p)[:-5]
        if name.endswith('_melody'):
            continue                              # 旋律画像包：不碰
        d = json.load(open(p, encoding='utf-8'))
        mt = d.get('mix_target')
        if not isinstance(mt, dict):
            skip.append(name + '(无 mix_target)')
            continue
        if mt.get('density_curve_db') and not force:
            skip.append(name + '(已有)')
            continue
        theme = d.get('theme') or name
        try:
            tpl = [t.get('file') for t in
                   TP.pick_templates(theme, min_n=TP.MIN_TEMPLATES,
                                     target=TP.DEF_TEMPLATES)]
        except Exception as e:
            skip.append('%s(选模板失败 %s)' % (name, str(e)[:30]))
            continue
        files = [os.path.join(ROOT, 'refs', 'midi2', f or '') for f in tpl]
        nsec = len(mt.get('structure_db') or []) or 10
        curve, meta = SD.curve_from_midis(files, nsec, root=ROOT)
        if len(curve) < 3:
            skip.append('%s(%s)' % (name, meta.get('note', '曲线不足')))
            continue
        mt['density_curve_db'] = SD.rel_curve(curve)
        mt['density_meta'] = dict(meta, abs_curve=curve,
                                  note='每 8 小节的音符数曲线（相对中位数 dB，'
                                       '取自同主题多份模板的中位数）；'
                                       '消费端 `new_song.density_curve_mix` → `arr.density`')
        d['mix_target'] = mt
        json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        done.append('%s(%d 点, %s)' % (name, len(mt['density_curve_db']),
                                       [round(x) for x in mt['density_curve_db']]))
    print('已注入 %d 个主题包：' % len(done))
    for x in done:
        print('   ' + x)
    if skip:
        print('跳过 %d 个：%s' % (len(skip), '；'.join(skip[:8])))


if __name__ == '__main__':
    main()
