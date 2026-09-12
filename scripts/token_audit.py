"""上下文成本审计：写歌这件事到底吃多少 token。

动机：用户问过"为什么写歌消耗的 token 变多了"。答案不该靠感觉，得能测。
本工具量三类成本（都按"字符 → ≈token"估算：CJK ≈1 token/字，ASCII ≈1 token/3.6 字符）：

1. **常驻成本**：每个音乐对话必然加载的 `AGENTS.md` + 技能 `SKILL.md`，
   以及技能让 agent "先读"的 `README.md`（整篇读 vs 只读需要的章节）。
2. **单曲成本**：读一遍 `song.json`（按小节分行的规范格式 vs 被写成"一个数字一行"）、
   `notes.md`、`make_song` 一屏输出。
3. **预算守卫**：给上面几项设上限（见 LIMITS），超了就报出来 ——
   文档/数据一旦膨胀，"写歌贵"就会悄悄回来。

用法：`python scripts\token_audit.py [--json]`
"""

import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）

import json_io          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser('~')
DOCS = {
    'AGENTS.md（每个对话常驻）': os.path.join(HOME, '.dsh', 'AGENTS.md'),
    'SKILL.md（音乐任务加载）': os.path.join(HOME, '.dsh', 'skills', 'bgm-studio', 'SKILL.md'),
    'README.md（工具链总索引）': os.path.join(ROOT, 'README.md'),
    'docs/SONG-FORMAT.md（写歌时才读）': os.path.join(ROOT, 'docs', 'SONG-FORMAT.md'),
    'docs/CONVENTION.md（新增内容往哪放）': os.path.join(ROOT, 'docs', 'CONVENTION.md'),
    'CHEATSHEET.md（命令速查，按需查）': os.path.join(ROOT, 'CHEATSHEET.md'),
    'PITFALLS.md（出症状才按编号查）': os.path.join(ROOT, 'PITFALLS.md'),
    'HISTORY.md（开发记录，写代码才看）': os.path.join(ROOT, 'HISTORY.md'),
    'studio/README.md（可视化面板）': os.path.join(ROOT, 'studio', 'README.md'),
}
# 预算（≈token）。超了说明该拆文档/改格式了，不是"无所谓"。
# 注意分两类：AGENTS/SKILL 是**每个音乐任务都要加载**的，README 在常规路径上会被读
# —— 这三个卡紧；PITFALLS / CHEATSHEET 只在"出症状按编号查 / 找命令"时才打开
#（没人整篇读），所以给宽松的膨胀警报而不是紧箍咒。
LIMITS = {
    'AGENTS.md（每个对话常驻）': 900,
    'SKILL.md（音乐任务加载）': 1700,
    'README.md（工具链总索引）': 6440,          # 瘦身后：只留索引/工具/验证状态
    'docs/SONG-FORMAT.md（写歌时才读）': 2000,
    'docs/CONVENTION.md（新增内容往哪放）': 1300,   # 参考类，非热点路径
    'CHEATSHEET.md（命令速查，按需查）': 1600,
    'PITFALLS.md（出症状才按编号查）': 8000,
    'studio/README.md（可视化面板）': 2200,   # 只在使用面板时读；CLI 路径不需要
}
# 宿主级文档：由用户目录（技能/全局约定）提供，**不属于本仓库**。
# 项目内自检不能硬依赖它们 —— 换台机器/换个用户时这些文件本来就不在（实测踩过：
# 硬依赖会让整套自检在别的机器上直接 FileNotFoundError 崩掉，而不是给出可读的提示）。
HOST_LEVEL = ('AGENTS.md（每个对话常驻）', 'SKILL.md（音乐任务加载）')

CJK = re.compile(r'[\u3000-\u9fff\uff00-\uffef]')


def est(s):
    """≈token（粗估：CJK 1 token/字；ASCII 1 token/3.6 字符）"""
    cjk = len(CJK.findall(s))
    return int(cjk * 0.9 + (len(s) - cjk) / 3.6)


def cost(path):
    if not os.path.exists(path):
        return None
    t = open(path, encoding='utf-8').read()
    return {'chars': len(t), 'lines': t.count('\n') + 1, 'tok': est(t)}


def report():
    out = {'docs': {}, 'songs': [], 'notes': []}
    print('== 常驻成本（每次对话 / 每个音乐任务都要付）==')
    total = 0
    for label, p in DOCS.items():
        c = cost(p)
        if not c:
            continue
        out['docs'][label] = c
        lim = LIMITS.get(label)
        flag = ''
        if lim and c['tok'] > lim:
            flag = '  ⚠ 超预算 %d' % lim
        print('  %-34s %6d 字 %5d 行 ≈%5d tok%s'
              % (label, c['chars'], c['lines'], c['tok'], flag))
        total += c['tok']
    print('  %-34s %23s ≈%5d tok' % ('（上面四项合计，实际只会加载前 2~3 项）', '', total))

    print('\n== 单曲成本 ==')
    for p in sorted(glob.glob(os.path.join(ROOT, 'songs', '*', 'song.json'))):
        raw = open(p, encoding='utf-8').read()
        d = json.loads(raw)
        canon = json_io.dumps(d)
        name = p.split(os.sep)[-2]
        n_notes = sum(len(v) for v in d.get('melody', {}).values())
        n_notes += sum(len(s.get('melody_extra') or []) for s in d.get('sections', []))
        row = {'song': name, 'notes': n_notes,
               'now': cost(p), 'canonical_tok': est(canon),
               'canonical_lines': canon.count('\n') + 1}
        out['songs'].append(row)
        print('  %-16s 音符%4d  现在 ≈%5d tok / %5d 行   规范格式 ≈%5d tok / %4d 行'
              % (name, n_notes, row['now']['tok'], row['now']['lines'],
                 row['canonical_tok'], row['canonical_lines']))
    for p in sorted(glob.glob(os.path.join(ROOT, 'songs', '*', 'notes.md'))):
        c = cost(p)
        out['notes'].append({'file': os.path.basename(os.path.dirname(p)), 'tok': c['tok']})
        print('  %-16s notes.md ≈%d tok' % (os.path.basename(os.path.dirname(p)), c['tok']))
    return out


def main():
    out = report()
    print('\n== 一个典型写歌对话的估算 ==')
    sk = out['docs'].get('SKILL.md（音乐任务加载）', {}).get('tok', 0)
    ag = out['docs'].get('AGENTS.md（每个对话常驻）', {}).get('tok', 0)
    rd = out['docs'].get('README.md（工具链总索引）', {}).get('tok', 0)
    song = max([r['canonical_tok'] for r in out['songs']] or [0])
    print('  常驻 %d + 技能 %d + 读 README（整篇 %d / 只读地图上那 1~2 节 ≈1500）'
          % (ag, sk, rd))
    print('  读一遍 song.json（规范格式）≈%d；make_song 一屏 ≈800；notes.md ≈1000' % song)
    lean = ag + sk + 1500 + song + 800 + 1000
    fat = ag + sk + rd + song * 2 + 800 + 1000
    print('  → 精简路线 ≈%d tok；整篇读文档 + 反复读 JSON ≈%d tok（差 %.1f 倍）'
          % (lean, fat, fat / max(1, lean)))
    # 目录成本：出症状时才会读的文件不该计入常规路径
    print('  （`PITFALLS.md` / `HISTORY.md` 只在排查时读，不计入常规路径）')
    if '--json' in sys.argv:
        print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
