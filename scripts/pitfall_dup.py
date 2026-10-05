# -*- coding: utf-8 -*-
"""坑台账重复检测 —— 往 `PITFALLS.md` 加新坑**之前**先跑一次。

## 何时用

台账跨多个对话写、还归档过，很容易"同一件事记两遍"。加新坑前跑一下：
若已有同族条目，应该**并进旧条目或加交叉引用**，而不是再记一遍。

```bash
python scripts/pitfall_dup.py                 # 扫 PITFALLS.md + PITFALLS-ARCHIVE.md
python scripts/pitfall_dup.py <文件...>        # 指定文件
```

## 两种发现要分开读（实测结论）

· **整条重复** —— 标题 Jaccard 高 **且** 共享代码标识符多 → 该合并。
  实测 190 条里**没有**这种情况。
· **同族散落** —— 主题词频高（如"静默"一族 **9 条**）：**不是重复**，
  但只翻到一条 = 看不到全集 = 下次照犯 → 应对策是**主题索引**，不是删条目。
  这正是本工具存在的理由：它报告的后半段（主题词频）比前半段更有用。

判据用**字符 2-gram Jaccard**（对中文比词切分稳）+ **共享代码标识符**
（同一个函数名/文件名出现在两条坑里，多半在讲同一件事 —— 这是最强的重复信号）。

## 索引完整性（**2026-10-06 补**，守卫 `t_pitfall_index` 用）

```bash
python scripts/pitfall_dup.py --check-index    # 校验（守卫调的就是它）· rc=1 = 漂了
python scripts/pitfall_dup.py --index-miss     # 列出"没被任何主题索引收进去"的编号
python scripts/pitfall_dup.py --self-test      # 规则自检（合成数据，不碰真文件）
```

为什么补（`PITFALLS` **328**）：`PITFALLS.md` 第 1 行的区间与"主题索引"都是**手写**的
⇒ 实测漂到：标题写 161–315、正文已到 326、索引里 316–326 一条都没进。
**编号是索引的唯一用途**（先按主题拿编号、再 grep 定位），所以校验只盯编号完整性：
① 标题区间 == 正文实际区间；② 正文每条至少被某个主题行收一次；③ 索引里的编号都在正文里；
④ 编号区间引用（`316–320`）指向存在的条目。主题**归类**质量由人负责（校验管不了）。
"""
import argparse
import io
import itertools
import os
import re
import sys
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT = [os.path.join(ROOT, 'PITFALLS.md'),
           os.path.join(ROOT, 'PITFALLS-ARCHIVE.md')]

IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]{3,}(?:\.(?:py|md|json|mid|ogg|wav))?')
STOP = {'the', 'and', 'for', 'with', 'this', 'that', 'from', 'http', 'https',
        'com', 'www', 'not', 'are', 'was', 'has', 'have', 'none', 'true'}
# 主题词：用来找"同族散落"（同一类问题被记成多条）
THEMES = ['静默', '八度', '跳过作曲', '锚点', '白名单', '频带', '质心',
          '旋律', 'density', 'notes_extra', 'arr_by_role', 'make_song',
          'band_match', 'render', '面板']

# ⚠ 阈值调严过两次：第一版（J>0.30 或共享 ≥3）误报 100+ 对；第二版（J>0.45 或共享 ≥4）
#   仍有 30 对假阳性 —— 真正的问题是**通用标识符**（midi/json/refs/bass…）几乎条条都有。
#   现在先按条目频率剔除通用词（`COMMON`），剩下的独特标识符共享 2 个就很有意义。
J_MIN = 0.45
ID_MIN = 2
COMMON = 4          # 出现在 ≥4 条里的标识符 = 通用词，不参与判重


def load(path):
    txt = open(path, encoding='utf-8').read()
    out = []
    for chunk in re.split(r'(?m)^(?=\d+\.\s)', txt):
        m = re.match(r'(\d+)\.\s+\*\*(.+?)\*\*', chunk, re.S)
        if not m:
            continue
        title = re.sub(r'\s+', '', m.group(2))
        ids = {x.lower() for x in IDENT.findall(chunk)} - STOP
        out.append((int(m.group(1)), title, ids))
    return out


def bigrams(s):
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


# ── 索引完整性（见文件头 docstring）─────────────────────────────────────────
MAIN = os.path.join(ROOT, 'PITFALLS.md')
ARCHIVE = os.path.join(ROOT, 'PITFALLS-ARCHIVE.md')

#: 主题索引表：每行 = 一格主题、一格编号列
THEME_ROW = re.compile(r'^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$')
#: 编号区间引用（如 `316–320`）——它必须指向**存在**的条目（能发现"引用了已删条目"）
RANGE_REF = re.compile(r'\*{0,2}(\d{3})\s*[–\-—~]\s*(\d{3})\*{0,2}')


def parse_doc(text):
    """把一份台账拆成 `(编号 → 标题, 标题行声明的区间)`。

    编号只认**行首顶格**的 `NNN. **标题**`（与 `load()` 同一口径）——
    正文里的交叉引用（"见 PITFALLS 326"）不在行首、也不带 `**`，不会误判。
    """
    items = {}
    for m in re.finditer(r'(?m)^(\d+)\.\s+\*\*(.*?)\*\*', text, re.S):
        items[int(m.group(1))] = re.sub(r'\s+', '', m.group(2))
    declared = None
    m = re.search(r'当前[：:]\s*(\d+)\s*[–\-—~]\s*(\d+)', text)
    if m:
        declared = (int(m.group(1)), int(m.group(2)))
    return items, declared


def parse_index(text):
    """`主题索引` 表 → `[(主题, 编号列原文)]`。

    ⚠ **只在"主题索引"小节内找表**（2026-10-06 实测踩到）：`PITFALLS.md` 正文里有大量
    **数据表**（"分轨 4.1 拍长音 138/386/676 个"、"质心 3522/1035/3973"…），
    若全文件扫 `| … | … |`，那些**测量数值**会被当成坑号收进来
    （实测报出 0 / 386 / 920 / 2053 / 18651 等 25 个"幽灵编号"，评审成本直接爆掉）。
    判据（**表头锚定**，2026-10-06 第三次修正）：小节标题以"主题索引"开头 →
    在其内部**等着出现表头行**（`| 主题 | 坑号 |`）→ 表头之后**只收表格行**，
    遇到空行继续、遇到第一个非表格行收尾。

    踩过的三个坑（都真报过错数）：
      ① 全文件扫 `| … | … |` ⇒ 正文里的**数据表**被当索引（报出 0/386/920/2053/18651 等
         25 个"幽灵编号"）；
      ② 用"等下一个 `#` 标题"收尾 ⇒ **全文只有第 1 行与第 11 行两个标题**，条件永不触发，
         数据表照样被收；
      ③ 用"遇第一个非表格行就收尾" ⇒ 标题与表格之间夹着**引用块说明**（`> 同一个坑…`），
         于是整节一行都收不到。⇒ 必须**先等到表头**再谈收尾。
    """
    rows = []
    inside = False
    started = False
    for ln in text.split('\n'):
        s = ln.strip()
        if s.startswith('#'):
            # ⚠ 用 `in` 而不是 `startswith`：标题可能写成 ``## `主题索引` 表 →``（含反引号）。
            inside = '主题索引' in s
            started = False
            continue
        if not inside:
            continue
        if not started:
            if s.startswith('|') and '坑号' in s:
                started = True          # 表头到手，从这里开始收
            continue
        if not s:
            continue                    # 表内空行不算结束
        if not s.startswith('|'):
            break                       # 表格块结束（下一行就是正文条目 `NNN. **…**`）
        m = THEME_ROW.match(s)
        if not m:
            continue
        theme, nums = m.group(1), m.group(2)
        if theme in ('主题', '---') or set(nums) <= set('-: '):
            continue
        rows.append((theme, nums))
    return rows


def index_numbers(nums):
    """一格编号列里出现的全部编号（`316–320` 展开成区间内的每一个）。

    ⚠ 用 `\\d+` 而不是 `\\d{3}`：本文件正文只有三位数编号（161–328），但判据
    不该把"千位段乱入"（如 `3100`）**当成看不见** —— 那样它就会绕过"被索引"这条检查
    （合成夹具里正是这么暴露的）。
    """
    out = set()
    for a, b in RANGE_REF.findall(nums):
        a, b = int(a), int(b)
        out.update(range(min(a, b), max(a, b) + 1))
    for x in re.findall(r'(?<!\d)(\d+)(?!\d)', RANGE_REF.sub(' ', nums)):
        out.add(int(x))
    return out


def has_number(nums, n):
    """`n` 是否真的出现在编号列里（不把 `1316` 里的 `316` 算命中）。"""
    return n in index_numbers(nums)


def to_lines(items, declared):
    out = ['# 坑台账（当前：%d–%d）' % declared]
    out += ['%d. **%s**' % kv for kv in sorted(items.items())]
    return '\n'.join(out)


def archive_numbers(path=None):
    """`PITFALLS-ARCHIVE.md` 里的编号集合 —— 主题索引合法引用它们的编号。"""
    path = path or ARCHIVE
    try:
        items, _d = parse_doc(open(path, encoding='utf-8').read())
    except OSError:
        return set()
    return set(items)


def check_index(main=None, archive=None):
    """索引完整性校验 —— 全通过返回 `[]`（正确），否则返回问题列表。

    ⚠ 默认值必须在**调用时**读 `MAIN` / `ARCHIVE`（`mutation_check` 用
    `Mut(pd, 'MAIN', <假文件>)` 注入），所以别把它写成默认参数。
    """
    main = main or MAIN
    gone = archive_numbers(archive)   # 归档里确实有的编号 = 合法引用
    text = open(main, encoding='utf-8').read()
    items, declared = parse_doc(text)
    rows = parse_index(text)
    if not items:
        return ['读不到条目（口径 `^NNN. **标题**`）—— 路径或格式变了？']
    errs = []
    lo, hi = min(items), max(items)
    if declared is None:
        errs.append('第 1 行没有声明区间（`当前：%d–%d`）' % (lo, hi))
    elif declared != (lo, hi):
        errs.append('第 1 行声明 %d–%d，正文实际 %d–%d' % (declared[0], declared[1], lo, hi))
    # 合法编号 = 本文件的条目 ∪ 归档文件里的条目（主题索引常年两者混写，
    #   实测 `188 245` `31 62 97 104` 这类都在归档里 ⇒ 不能按"小于最小号"猜）。
    known = set(items) | gone
    idx = set()
    for theme, nums in rows:
        for a, b in RANGE_REF.findall(nums):
            a, b = int(a), int(b)
            for n in range(min(a, b), max(a, b) + 1):
                if n not in known:
                    errs.append('%s 的区间 %d–%d 含**不存在的条目** %d' % (theme, a, b, n))
        idx |= index_numbers(nums)
    stray = sorted(n for n in idx if n not in known)
    if stray:
        errs.append('索引里有**本文件与归档都没有**的编号：%s' % stray)
    missing = sorted(n for n in items
                     if not any(has_number(nums, n) for _t, nums in rows))
    if missing:
        errs.append('正文有 %d 条**没被任何主题行收**：%s%s'
                    % (len(missing), missing[:14], ' …' if len(missing) > 14 else ''))
    return errs


def index_miss_report(main=None):
    """列出没进索引的条目（带标题，方便人工归类）。"""
    main = main or MAIN
    text = open(main, encoding='utf-8').read()
    items, _declared = parse_doc(text)
    rows = parse_index(text)
    miss = [(n, t) for n, t in sorted(items.items())
            if not any(has_number(nums, n) for _t, nums in rows)]
    print('共 %d 条 · 未进索引 %d 条' % (len(items), len(miss)))
    for n, t in miss:
        print('  %3d  %s' % (n, t[:88]))
    return 0


def self_test():
    """规则自检（合成数据，不碰真文件）：每种坏法都必须被 `check_index` 抓到。

    ⚠ 合成数据的**格式必须与真文件同构**（表头 + 分隔行 + 编号列直接列编号）。
    第一版图省事用 `to_lines()` 拼正文 ⇒ 多出一条"当前：1–3"的重复行、
    主题行全是 `**甲**`，于是**所有**用例都因为"列不动编号"而"通过"，
    只有"全好"那条露了馅（4/5）——**错误的夹具会让判据看着有效**。
    """
    import tempfile
    T = ('| 主题 | 坑号 |\n|---|---|\n'
         '| **静默失效** | 31 62 |\n'
         '| **每份清单都会漂移** | 188 245 |\n')

    def run(decl, idx_text):
        """造一份迷你台账 + 一份迷你归档（照真文件的形：正文 171–174 + 千位段 3100）。

        ⚠ 归档文件是**必须**的：主题索引常年混写"本文件编号 + 归档编号"
        （真文件里 `31 62 97 104`、`188 245` 都在 `PITFALLS-ARCHIVE.md`），
        第一版按"小于最小号就当归档"猜 ⇒ 夹具里 `188/245` 立刻被误判成违规。
        """
        d = tempfile.mkdtemp(prefix='pidx_')
        arch = os.path.join(d, 'PITFALLS-ARCHIVE.md')
        open(arch, 'w', encoding='utf-8', newline='\n').write(
            '31. **归档条目**\n62. **归档条目**\n188. **归档条目**\n245. **归档条目**\n')
        p = os.path.join(d, 'PITFALLS.md')
        body = ('# 坑台账（当前：%d–%d）\n\n## 主题索引 —— 查这里\n\n%s\n\n'
                '171. **a**\n172. **b**\n173. **c**\n174. **d**\n'
                '3100. **千位段条目**\n' % (decl[0], decl[1], idx_text))
        open(p, 'w', encoding='utf-8', newline='\n').write(body)
        return check_index(p, arch)

    cases = [
        # 正文里放一条 `3100` 是**故意**的：真文件只用到 3 位数，但"千位段乱入"这种坏法
        #   必须被当成正文条目、算进区间（否则 min/max 被它带偏而守卫看不出来）。
        #   所以每条完整的索引列都得把它收进去，缺了它就会**顺带**报"漏索引"。
        ('全好（含归档引用 + 千位段）',
         run((171, 3100), T + '| **甲** | 171–174 31 188 3100 |\n'), False),
        ('标题区间没算上千位段',
         run((171, 174), T + '| **甲** | 171–174 31 188 3100 |\n'), True),
        ('漏索引一条（174）',
         run((171, 3100), T + '| **甲** | 171–173 31 188 3100 |\n'), True),
        ('索引引用了不存在的编号（999）',
         run((171, 3100), T + '| **甲** | 171–174 31 188 3100 999 |\n'), True),
        ('区间引用跨到不存在的条目（171–179）',
         run((171, 3100), T + '| **甲** | 171–179 31 188 3100 |\n'), True),
    ]
    bad = 0
    for label, errs, want in cases:
        ok = (bool(errs) == want)
        bad += 0 if ok else 1
        print('  %-4s %-26s %s' % ('OK' if ok else '**错**', label,
                                   (errs[0][:64] if errs else '（无问题）')))
    print('规则自检：%d/%d' % (len(cases) - bad, len(cases)))
    return 1 if bad else 0



def main():
    import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
    ap = argparse.ArgumentParser(description='坑台账重复检测 + 索引完整性校验')
    ap.add_argument('files', nargs='*', default=None)
    ap.add_argument('--check-index', action='store_true',
                    help='只校验"标题区间/正文/主题索引"三者一致（守卫 t_pitfall_index 调它）')
    ap.add_argument('--index-miss', action='store_true',
                    help='列出没被任何主题行收进去的编号（人工归类用）')
    ap.add_argument('--self-test', action='store_true',
                    help='规则自检：合成数据上每种坏法都要被抓到')
    a = ap.parse_args()

    if a.self_test:
        return self_test()
    if a.index_miss:
        return index_miss_report()
    if a.check_index:
        errs = check_index()
        if errs:
            print('索引不一致（%d 处）：' % len(errs))
            for e in errs:
                print('  · %s' % e)
            print('  ⇒ 补 `PITFALLS.md` 第 1 行的区间与文首"主题索引"表'
                  '（或跑 `pitfall_dup.py --index-miss` 看待归类的条目）')
            return 1
        items, declared = parse_doc(open(MAIN, encoding='utf-8').read())
        print('索引一致：%d 条 · 区间 %d–%d · 全部条目都被主题索引收'
              % (len(items), declared[0], declared[1]))
        return 0

    files = a.files or DEFAULT

    items = []
    for f in files:
        if os.path.exists(f):
            items += load(f)
    if not items:
        raise SystemExit('没读到任何条目（检查路径：%s）' % files)
    items.sort()

    # ⚠ **只留"独特"标识符**：第一版按"共享 ≥4 个标识符"判，实测在 190 条上给出
    #   **30 对候选、全是 J≈0.00 的假阳性** —— 因为 `midi` / `json` / `refs` / `bass`
    #   这类通用词在几十条里都出现，"共享 4 个"毫无信息量。
    #   修法：按**条目频率**过滤 —— 出现在 ≥COMMON 条里的视为通用词剔除，
    #   剩下的才是"同一个函数名 / 同一个开关名"这种真信号。
    freq = {}
    for _n, _t, ids in items:
        for x in ids:
            freq[x] = freq.get(x, 0) + 1
    items = [(n, t, {x for x in ids if freq[x] < COMMON}) for (n, t, ids) in items]

    nums = [n for n, *_ in items]
    dup = sorted({n for n in nums if nums.count(n) > 1})
    miss = [n for n in range(min(nums), max(nums) + 1) if n not in nums]
    print('共 %d 条 / 编号 %d–%d · 重号 %s · 缺号 %s'
          % (len(items), min(nums), max(nums), dup or '无', miss or '无'))

    print()
    print('=== ① 整条重复候选（标题 J>%.2f 或共享 ≥%d 个代码标识符）===' % (J_MIN, ID_MIN))
    hits = 0
    for (n1, t1, i1), (n2, t2, i2) in itertools.combinations(items, 2):
        if abs(n1 - n2) <= 1:                       # 相邻条目常互相引用，跳过
            continue
        j = len(bigrams(t1) & bigrams(t2)) / max(1, len(bigrams(t1) | bigrams(t2)))
        shared = i1 & i2
        if j > J_MIN or len(shared) >= ID_MIN:
            hits += 1
            print('  %3d ↔ %3d  J=%.2f  共享 %d' % (n1, n2, j, len(shared)))
            print('      %s' % t1[:64])
            print('      %s' % t2[:64])
            if shared:
                print('      共有：%s' % sorted(shared)[:6])
    if not hits:
        print('  （没有 —— 说明没有整条重复；同族散落见 ②）')

    print()
    print('=== ② 同族散落（主题 ≥3 条）—— 对策是加主题索引，不是删条目 ===')
    for k in THEMES:
        hit = [n for n, t, _i in items if k.lower() in t.lower()]
        if len(hit) >= 3:
            print('  %-14s %2d 条：%s' % (k, len(hit), hit[:14]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
