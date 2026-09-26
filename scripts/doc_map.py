#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""doc_map.py —— **文档地图**：大目录（主题域）→ 小目录（文档节 + 行号 + 体量）。

═══ 为什么需要它（现场） ═══
README / SKILL 的路由表只到**文件级**（"要什么 → 读哪份"），而
`HISTORY.md` **38k tok** / `PITFALLS.md` **30k** / `README.md` **9.8k** 这种
一次根本读不完 —— 没有节级行号时只有两条路：整篇读（烧上下文），或猜 offset
（读错地方，更慢）。**"查找慢"的根因就在这**。

═══ 产出 ═══
`docs/DOC-MAP.md`（**生成物，别手改**）：
  · **大目录** = 主题域（写新歌 / 模仿 / 还原 / 混音体检 / 面板 / 出症状 / 新增内容 / 开发史 / 宿主级）
  · **小目录** = 该域下每份文档的节（`起–止行` + 标题 + 体量）
  · 体量 ≤ `SMALL_TOK` 的文档只列**文件级一行** —— 免得地图自己长成又一份长文档
  · 主题域归属手写在本文件的 `GROUPS` 里（**代码即真源**，与 CONVENTION §1 一致：
    "能由代码回答的别抄进文档"）；行号/体量全部现扫，不手抄。

═══ 守卫（地图不会腐烂） ═══
`selftest` 的 `doc_map_fresh` 跑 `--check`：**重算一遍与磁盘文件逐字比对**，
行号漂了 / 换了标题 / 加了新文档 → 直接 FAIL。变异用例见 `mutation_check.py`。

用法:
  python scripts\doc_map.py             # 重新生成 docs/DOC-MAP.md
  python scripts\doc_map.py --check     # 只校验是否过期（退出码 1 = 过期）
  python scripts\doc_map.py --stdout    # 只打印，不写文件
  python scripts\doc_map.py --list      # 盘点：所有文档 + 体量 + 一级标题（归类时用）
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import cli_utf8 as _cu
    _cu.setup()
except Exception:                                     # noqa: BLE001
    pass

import token_audit as ta                              # noqa: E402  （复用 est/cost，体量口径唯一）

ROOT = ta.ROOT
HOME = ta.HOME
OUT = os.path.join(ROOT, 'docs', 'DOC-MAP.md')

BIG_TOK = 5000            # > 这个体量才展开**节级行号**
HUGE_TOK = 15000          # > 这个体量算"巨型"：只列 `##` 级（`###` 太碎，列全了地图自己也成了长文档）
MIN_SEC_TOK = 400         # 非巨型文档展开时的小节门槛
HUGE_MINS = 100           # 巨型文档（只列 `##` 级）的门槛 —— 单独一个常量，
                          # 这样"改门槛"能被变异注入到（混用 MIN_SEC_TOK 会有文档收不到）
HEAD = re.compile(r'^(#{1,3})\s+(.+?)\s*$')
# 枚举时跳过的目录：虚拟环境 / 版本库 / 模板库 / 按曲生成的 notes / 第三方仓库
SKIP_DIRS = {'.venv', '.venv-ml', '.git', 'refs', '__pycache__', 'node_modules', 'songs',
             # 2026-09-24：曲库拆成两个独立根（用户要求"直接写的歌和模仿写的歌放不同文件夹"）——
             # `songs` 已改名为 `songs_direct`（`songs/` 保留成指向它的 junction），
             # 所以这两个新名字也要排除，否则每首曲子的 `notes.md` 会被当成"未归类的新文档"
             # （实测：`doc_map_fresh` 直接 FAIL，点名 `songs_direct/01_morning_light/notes.md`）。
             'songs_direct', 'songs_imitate',
             'vendor',
             # `check_song.py` 的沙箱（`lint_dirs` 把根 `*.md`、`docs/`、`refs/`、`studio/`
             # 复制进去跑单曲判据）。**不排除它 → 每次 `check_song` 都假报
             # `doc_map_fresh` FAIL**（沙箱里那份 `CHEATSHEET.md` 等被当成"没归类的新文档"，
             # 实测点名 `_lint_sandbox/CHEATSHEET.md`）。这正是 `lint_dirs` docstring 里
             # 骂的那类"沙箱造成的假报，把真正的数据错误淹掉"（2026-09-24 修）。
             '_lint_sandbox',
             # 2026-09-26：`deliveries/<曲名>/`（**点名要传 GitHub 的那一首**，见 `.gitignore` §⑥）
             # —— 与 `songs*` 同性质：**按曲生成、随交付增删**。不排除则 `deliveries/` 下每份
             # `notes.md`/`HANDOFF-*.md` 都会落进"未归类"→ `doc_map_fresh` FAIL
             # （实测：`deliveries/dear_good_friends/HANDOFF-ROUND18.md` 已经红了一天）。
             'deliveries'}

# ── 大目录（主题域）→ 小目录（文档）。**手写的只有这一处** ────────────────────
# 每项 = (相对路径, 一句话：这份文档回答什么问题)。没归类的文档会落到"未归类"域
# 并在 --check 输出里点名 —— 新文档忘了归类，一眼就能看见。
GROUPS = [
    ('A. 上手 / 安装 / 环境', [
        ('INSTALL.md', 'clone 之后第一件事：装环境 · 五分钟出第一首 · 仓库带什么不带什么'),
        ('README.md', '全貌：目录结构 / 工具清单 / 验证状态 / 产物 / 踩过的坑'),
        ('skill/bgm-studio/README.md', '技能何时被加载 · 触发词怎么维护 · 排错'),
        ('tools/git-push/README.md', 'GitHub 连不上时的推送绕行'),
    ]),
    ('B. 写新歌（直接作曲）', [
        ('docs/THEME-PACK.md', '生成依据：主题模板包（≥8 首聚合）· 混音目标 · 来源白名单'),
        ('docs/SONG-FORMAT.md', 'song.json 结构 / 风格预设 / 段落曲线 / 渲染参数'),
        ('CHEATSHEET.md', '命令与开关速查'),
        # `refs/` 在 SKIP_DIRS 里（它主要是 MIDI 素材），但这份说明**该被索引**：
        # 查"模板库从哪来、许可干不干净"要读它（被跳过 = 地图上不存在）。
        ('refs/classical_pd/README.md', '**可再分发**的古典 MIDI 模板库（424 首 · 许可与来源 · 为什么只收 296 首）'),
    ]),
    ('C. 照着参考曲写（模仿）', [
        ('docs/IMITATE-PATH.md', '七步工序 · 段名纪律 · 与"直接作曲/还原"的分界'),
    ]),
    ('D. 还原 / 扒带 / 转录', [
        ('docs/RESTORE-METHOD.md', '七步工序 · 有效/无效做法 · 度量纪律 · 防错清单'),
        ('ML.md', '扒谱·分轨·多乐器转录·母带匹配（.venv-ml 的重工具）'),
        ('docs/MAKE-IT-SOUND-ALIKE.md', '把"乱"当可测量问题：三个量 · 三处失控根因 · 四条听感修法'),
        ('docs/AUDIT-CHECKLIST.md', '开工前必查清单（四层 + 必须人耳的第 5 层）'),
        ('docs/MIDI-FIDELITY.md', '力度全平的验证与修法（含"为什么分数几乎不动"）'),
    ]),
    ('E. 案例（具体某一首的实测数字）', [
        ('docs/CASE-BGM36.md', '对照案例：指标与听感背离'),
    ]),
    ('F. 改歌 / 听感修复 / 交接', [
        ('docs/HANDOFF.md', '接手"生成曲听感修复"：已完成/未完成 · 流程红线'),
        ('docs/DYNAMICS-VS-RECIPE.md', '起伏该做在哪一层（编配 vs 音量）· 被推翻的假设'),
        ('docs/UNMEASURABLE-SOLUTIONS.md', '测不到的 8 条：网络调研与落地情况'),
    ]),
    ('G. 面板（studio）', [
        ('studio/README.md', '可视化面板：启动 / 功能 ↔ 工具对照 / 边界'),
        ('docs/STUDIO-WORKFLOW.md', '必须开面板的五个时刻 · 症状→真因表 · 省时顺序'),
    ]),
    ('H. 出症状排查 / 台账', [
        ('PITFALLS.md', '坑台账（当前 161–261）：先看 §"主题索引"拿编号，'
                        '再用 `grep -n "^161\\." PITFALLS.md` 定位行号（条目不是 markdown 标题）'),
        ('PITFALLS-ARCHIVE.md', '已归档的旧坑（1–160 的部分），同上用 grep 定位'),
    ]),
    ('I. 音频大模型"嘴替"', [
        ('docs/AUDIO-CRITIC.md', '本地 Qwen2-Audio 听曲子：用法 · 硬约束 · 实测边界 · 交接'),
    ]),
    ('J. 要新增内容（往哪写 / 动哪些守卫）', [
        ('docs/CONVENTION.md', '新增内容往哪放 · 预算纪律 · 守卫清单 · 自查命令'),
    ]),
    ('K. 开发史 / 决策经过', [
        ('HISTORY.md', '开发记录（按批次）· 修过的 bug 全集 · 验证状态'),
    ]),
    # 以 `#` 开头的项 = **纯说明行**（不是文件）：用于登记"按曲/按输入生成、不逐份列"的东西。
    ('M. 曲目笔记（按曲生成，进不了文档库）', [
        ('# 每首 `songs/<曲>/notes.md`（≈0.9k tok）—— 该曲的调性 / 速度 / 结构 / 复现命令 / **没达标项**；'
         '问"这首歌当时怎么做的、哪些没做到"就看它（30 首）', ''),
        ('# `deliveries/<曲名>/`（**点名要传 GitHub 的那一首**，`.gitignore` §⑥）—— '
         '`song.json` + `<曲名>.mid` + `notes.md` + `render.json`（+ 试听 ogg / `variants/`）；'
         '曲库 `songs/` 是 junction、整目录被排除，所以交付物走这个专用目录', ''),
    ]),
]
HOST_DOCS = [                                        # 宿主级（不在仓库里，跟着用户环境走）
    (os.path.join(HOME, '.dsh', 'AGENTS.md'), 'AGENTS.md（每个对话常驻）'),
    (os.path.join(HOME, '.dsh', 'skills', 'bgm-studio', 'SKILL.md'), 'SKILL.md（音乐任务加载）'),
    (os.path.join(ROOT, 'docs', 'HOST-DOCS', 'AGENTS.md'), 'AGENTS.md 的仓库副本（推送用）'),
    (os.path.join(ROOT, 'docs', 'HOST-DOCS', 'SHELL-NOTES.md'), 'shell 踩坑清单（AGENTS 展开版）'),
    (os.path.join(ROOT, 'docs', 'HOST-DOCS', 'COT-PERSONA.md'), '思维链语言改哪（AGENTS 展开版）'),
    (os.path.join(ROOT, 'skill', 'bgm-studio', 'SKILL.md'), 'SKILL.md 的仓库副本（推送用）'),
]


def repo_docs():
    """枚举项目文档（排除虚拟环境 / 模板库 / 按曲生成的 notes.md / **地图自己**）。

    ⚠ 必须排除 `docs/DOC-MAP.md` 自身：它写出后就会被枚举到、落进"未归类"，
    于是"生成→再校验"永远不一致（实测第一遍 173 行、第二遍 178 行，
    `--check` 恒 FAIL）—— 自指的产物不能进自己的枚举集。
    """
    self_rel = os.path.relpath(OUT, ROOT).replace('\\', '/')
    out = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.lower().endswith('.md'):
                rel = os.path.relpath(os.path.join(base, f), ROOT).replace('\\', '/')
                if rel == self_rel:
                    continue
                out.append(rel)
    return sorted(out)


def sections(path):
    """扫标题 → `[(起行, 止行, 层级, 标题, tok), ...]`（层级 ≤3）。

    ⚠ **必须跳过代码围栏**：文档里的 shell 例子是 `# 注释` 开头，正则会把它们当标题
    （实测 README 里"只改 songs\\08_x\\song.json…"这条注释被列成了节、真正的节反而错位）。
    围栏内一律不当标题。
    """
    if not os.path.exists(path):
        return []
    text = open(path, encoding='utf-8').read()
    lines = text.split('\n')
    heads = []
    fence = False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith('```'):
            fence = not fence
            continue
        if fence:
            continue
        m = HEAD.match(ln)
        if m:
            heads.append((i + 1, len(m.group(1)), m.group(2)))
    out = []
    n = len(heads)
    for k, (ln0, lv, title) in enumerate(heads):
        # ⚠ 终点是**下一个"同级或更高级"标题**的前一行，不是"下一个标题" ——
        #   否则 `## 第十三批`（1256–1293）会被它内部的第一个 `###` 截成 1256–1260
        #   （实测踩过：地图上的区间比实际小一半，按它读会漏掉大半节）。
        ln1 = len(lines)
        for j in range(k + 1, n):
            if heads[j][1] <= lv:
                ln1 = heads[j][0] - 1
                break
        chunk = '\n'.join(lines[ln0 - 1:ln1])
        out.append((ln0, ln1, lv, title, ta.est(chunk)))
    return out


def sec_rows(path, huge):
    """给地图用的节行：先按层级过滤；**节本来就少（≤20）的文档全列**，
    节多的才按体量过滤（否则 README/SKILL 这种 10 节的文档会漏掉一半）。"""
    max_lv, mins = (2, HUGE_MINS) if huge else (3, MIN_SEC_TOK)
    secs = [s for s in sections(path) if s[2] <= max_lv]
    if len(secs) > 20:
        secs = [s for s in secs if s[4] >= mins]
    return secs


def _short(s, n=26):
    return s if len(s) <= n else s[:n - 1] + '…'


def build():
    """生成地图全文（**确定性**：同输入必同输出，否则 --check 会假 FAIL）。"""
    L = []
    L.append('# 文档地图 —— 大目录 → 小目录（**生成物，别手改**）')
    L.append('')
    L.append('> 生成 `python scripts/doc_map.py` · 校验 `--check`（自检项 `doc_map_fresh` 守着）'
             ' · 体量 ≈token')
    L.append('> **用法**：按「我要做什么」找到那一节 → 只读标出的**行号区间**，别整篇读。'
             '`## ` 分域，可以直接只读一个域。')
    L.append('> 只有 >%.1fk tok 的长文档才展开节级行号（`##` 级）；短文档打开就整篇读完了。'
             % (BIG_TOK / 1000.0))
    L.append('')
    used = set()
    for gname, docs in GROUPS:
        L.append('## %s' % gname)
        L.append('')
        for rel, what in docs:
            if rel.startswith('#'):            # 纯说明行（按曲/按输入生成的东西，不逐份列）
                L.append('- %s' % rel[1:].strip())
                continue
            p = os.path.join(ROOT, rel)
            c = ta.cost(p)
            used.add(rel)
            if c is None:
                L.append('- `%s` —— **缺失**（%s）' % (rel, what))
                continue
            L.append('- **`%s`**（%.1fk tok / %d 行）—— %s'
                     % (rel, c['tok'] / 1000.0, c['lines'], what))
            if c['tok'] > BIG_TOK:
                for a, b, lv, t, k in sec_rows(p, c['tok'] > HUGE_TOK):
                    L.append('    - `%4d–%-4d`（%4.1fk）%s' % (a, b, k / 1000.0, _short(t, 40)))
        L.append('')
    # 宿主级文档里"在仓库内"的那几份也算已归类（否则会被误报未归类）
    for p, _what in HOST_DOCS:
        if os.path.abspath(p).startswith(os.path.abspath(ROOT) + os.sep):
            used.add(os.path.relpath(p, ROOT).replace('\\', '/'))
    rest = [d for d in repo_docs() if d not in used]
    if rest:
        L.append('## Z. 未归类（**补 GROUPS**）')
        L.append('')
        for rel in rest:
            c = ta.cost(os.path.join(ROOT, rel))
            L.append('- `%s`（%.1fk tok）' % (rel, (c or {}).get('tok', 0) / 1000.0))
        L.append('')
    L.append('## L. 宿主级（不在本仓库，跟着用户环境走）')
    L.append('')
    L.append('> 宿主级文档由用户目录提供（换机器/换用户就不在）—— 这里**只登记存在性**，'
             '不列体量/行号：')
    L.append('> 否则地图会随机器漂，`--check` 在新机器上必然误报（`CONVENTION.md` §6 的同一类坑）。')
    L.append('> 改完宿主文档要同步备份：`python docs/HOST-DOCS/sync_host_docs.py`'
             '（自检 `host_docs_synced` 守着：备份与宿主不一致会 FAIL）。')
    L.append('')
    for p, what in HOST_DOCS:
        shown = p.replace(HOME, '~', 1) if p.startswith(HOME) else p
        L.append('- **%s** —— `%s`（%s）'
                 % (what, shown.replace('\\', '/'), '存在' if os.path.exists(p) else '**读不到**'))
    L.append('')
    return '\n'.join(L).rstrip() + '\n'


def do_list():
    """盘点模式：所有文档 + 体量 + 一/二级标题（写 GROUPS 时用）。"""
    rows = []
    for rel in repo_docs():
        c = ta.cost(os.path.join(ROOT, rel))
        # ⚠ 解包要与 `sections()` 的返回结构一致（5 元组：起/止/层级/标题/tok）——
        #   实测踩过：给 sections 加了"层级"字段后忘了改这里，`--list` 直接 ValueError 崩掉，
        #   而 build/--check 都正常（所以守卫抓不到，只有真去跑那个模式才会发现）。
        heads = [t for (_a, _b, _lv, t, _k) in sections(os.path.join(ROOT, rel))]
        rows.append((c['tok'], rel, c['lines'], heads))
    rows.sort(reverse=True)
    for tok, rel, lines, heads in rows:
        print('\n%7d tok %5d 行  %s' % (tok, lines, rel))
        for t in heads[:14]:
            print('        · %s' % _short(t, 46))
        if len(heads) > 14:
            print('        · …（共 %d 节）' % len(heads))


def main():
    ap = argparse.ArgumentParser(description='文档地图：大目录（主题域）→ 小目录（节 + 行号 + 体量）')
    ap.add_argument('--check', action='store_true', help='只校验 docs/DOC-MAP.md 是否过期')
    ap.add_argument('--stdout', action='store_true', help='只打印，不写文件')
    ap.add_argument('--list', action='store_true', help='盘点所有文档 + 体量 + 标题（归类用）')
    a = ap.parse_args()

    if a.list:
        do_list()
        return 0
    text = build()
    if a.stdout:
        sys.stdout.write(text)
        return 0
    old = open(OUT, encoding='utf-8').read() if os.path.exists(OUT) else None
    if a.check:
        if old is None:
            print('文档地图缺失: %s —— 跑 `python scripts/doc_map.py` 生成' % OUT)
            return 1
        if old != text:
            # 给出**可操作**的差异：先说文件级增删，再说节级漂移
            old_l, new_l = old.split('\n'), text.split('\n')
            only_old = [x for x in old_l if x not in new_l][:4]
            only_new = [x for x in new_l if x not in old_l][:4]
            print('文档地图已过期（%d 行 → %d 行）—— 跑 `python scripts/doc_map.py` 重新生成'
                  % (len(old_l), len(new_l)))
            for x in only_old:
                print('  旧: %s' % _short(x, 96))
            for x in only_new:
                print('  新: %s' % _short(x, 96))
            return 1
        print('文档地图最新（%d 行）' % (text.count('\n')))
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    print('已写出 %s（%d 行 / ≈%d tok）' % (OUT, text.count('\n'), ta.est(text)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
