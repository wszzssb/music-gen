#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新歌脚手架（song.json 方案）：**新歌只写一个 JSON，不写代码**

用法:
  python new_song.py 06_morning --from 05_d135_cheerful --ref BGM16c [--style gorgeous]
  python new_song.py --list-styles

做的事:
  1. songs/<NN_名字>/ 建目录
  2. 复制模板的 song.json（改 name / 前缀），**并按参考曲画像自动填 BPM**
  3. 写 compose.py（10 行固定桩，调引擎）
  4. 写 render.json：**按参考曲画像自动推算渲染参数**（响度/宽度/搁架），省掉一轮试错
  5. 写 notes.md：把参考曲的关键指标直接填进去
不复制任何音频产物。
"""
import json
import os
import shutil
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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', 'scripts'))
import song_engine

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == '__main__':
    song_engine.compose(os.path.join(HERE, 'song.json'))
'''


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
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args or '--from' not in sys.argv:
        print(__doc__)
        return 1
    new = args[0]
    src_name = sys.argv[sys.argv.index('--from') + 1]
    ref_name = sys.argv[sys.argv.index('--ref') + 1] if '--ref' in sys.argv else None
    style = sys.argv[sys.argv.index('--style') + 1] if '--style' in sys.argv else None
    sec_name = (sys.argv[sys.argv.index('--from-sections') + 1]
                if '--from-sections' in sys.argv else None)
    src_dir = os.path.join(SONGS, src_name)
    if not os.path.isdir(src_dir):
        print('模板不存在: %s（可选: %s）'
              % (src_dir, ', '.join(sorted(os.listdir(SONGS)))))
        return 1
    short = new.split('_', 1)[1] if '_' in new else new
    dst = os.path.join(SONGS, new)
    if os.path.exists(dst):
        print('已存在: %s' % dst)
        return 1
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
                '$py = "D:\\software\\skill\\.venv\\Scripts\\python.exe"\n'
                'cd D:\\software\\skill\n'
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
