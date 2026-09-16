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
    'docs/THEME-PACK.md（主题模板包/混音目标）': os.path.join(ROOT, 'docs', 'THEME-PACK.md'),
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
# 2026-09-14 上调（用户明确授权"文档预算实在不够可以加"）。**理由逐条写在行尾** ——
# 不是为了消警告抬数字：能拆的都先拆了（PITFALLS 81–88 归档、主题口径拆成独立文档）。
LIMITS = {
    'AGENTS.md（每个对话常驻）': 900,
    # +100：主题模板包 = 新歌的**唯一合规路径**，"写歌四步"整段改写 + 路由表加一行
    # +350（2026-09-15）：§2 加"第 0 条 首要标准：和谐、不突兀"（用户明确定的**优先级**：
    #   高于 CLAP happy 这类好听度指标，冲突时以和谐为准）+ 两种已量化的突兀形态
    #   + "段间对比不算突兀"的反面校准（`probe_sections_jump.py` 实测中位 31%）。
    #   这条不写在最前面，下次照样会为了推指标把某轨搞突兀（本轮就干了一次）。
    # +200（同日）：指向 `docs/CASE-BGM36.md`（用户指定的最佳案例）+ 它最硬的两个反例
    #   （CLAP tense 0.961、AQA CE 7.115 却最好）—— 防止下次又拿指标判好坏。
    #   压缩时的首选：把 §3 与 §4 的重复条目并掉，别动第 0 条。
    # +50（2026-09-16）：第 9 条"听着乱/控制不住先量三件事"——
    #   用户对还原曲的原话反馈，三条可量化指标 + 三处典型失控。
    # +70（同日）：第 8 条改成"**开工先跑 `audit.py` 拿偏差清单**"（用户要求
    #   "最开始就发现所有要注意的元素"）。详细清单与 8 条"必须人耳"的项拆到
    #   `docs/AUDIT-CHECKLIST.md`（按需读，不占常驻），这里只留入口 + "分数≠像"的警告。
    #   这轮同时把 §3 与 §4 的重复条目并掉、删掉了重复的工具清单。
    'SKILL.md（音乐任务加载）': 2870,
    # +160：工具清单加 theme_pack / new_song（含混音目标）+ 主题包文档指针
    # +100（2026-09-15，用户授权"预算可以增加，最后压缩就行"）：补登记 4 个已存在但漏在
    # 索引外的探针（probe_voicing / probe_tension / probe_aesthetic / probe_aqa）——
    # **索引缺项比超预算更糟**（工具写了没人知道）。压缩时优先合并"编配/口径诊断"那几行。
    # +100（2026-09-16）：登记 `similarity.py` / `band_match.py`；
    # +160（同日）：登记 `audit.py`（全维度体检，用户"最开始就发现所有元素"的要求）
    #   与 `song_density.py` + `inject_density_curve.py`（段间密度曲线）。三者合并成两行。
    'README.md（工具链总索引）': 6960,
    # +100：`theme` / `basis` / `mix_target` 三个字段的口径
    'docs/SONG-FORMAT.md（写歌时才读）': 2100,
    # **新增一类**：主题→模板→包→混音目标→段间曲线的完整口径（判据表/守卫表/A-B 实测/边界）。
    # 拆出来的目的正是让 SKILL 与 README 只留指针（它们才是常驻成本）。
    # 2026-09-14 四次上调到 3800：① 建立时 2600 ② 加"段间曲线 + A/B"到 3000
    # ③ 加"CC7 实测曲线表 + 6 主题标定表"到 3600 ④ 本轮把标定扩到 14 主题 + 多 seed 决策
    #   A/B（含一次被数据推翻的尝试）—— 这些表是这条口径唯一的证据，删了只剩主张
    'docs/THEME-PACK.md（主题模板包/混音目标）': 3800,
    'docs/CONVENTION.md（新增内容往哪放）': 1300,   # 参考类，非热点路径
    # +100：主题包四条命令 + 混音目标说明（README 只留指针，示例集中在这里）
    # +300（2026-09-15，同一次授权）：加"模型侧主观分"一节（CLAP/AQA 全库排序命令 +
    # **"只揪异常、不当验收门"**的口径）。这条口径不落文档，下次极容易被误当成验收门
    # （实测 39 首里 31 首的 CE 挤在 0.3 内，与客观指标 |r| ≤ 0.28）。
    'CHEATSHEET.md（命令速查，按需查）': 2000,
    # 台账按轮次增长（现 101–128 条）。**优先拆归档**（已拆过 81–88、89–92、93–100、101–104、
    # 114–120）；2026-09-14 上调到 9000（121 生成期干跑 / 122 旋律密度 / 123 CC7 口径 /
    # 124 基础声部 / 125 临时目录泄漏），再上调到 9600。**127（旋律形态）与 128（旋律与伴奏
    # 的配合：TR_SHIFT 非八度移调把伴奏移到和弦外）都是按上一行那句注释拆掉旧条目才加进来的**
    # —— 下一次增长照样先拆（候选 121–126），不许再抬。
    'PITFALLS.md（出症状才按编号查）': 9600,
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
