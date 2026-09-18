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
"""
import argparse
import itertools
import os
import re
import sys

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


def main():
    import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
    ap = argparse.ArgumentParser(description='坑台账重复检测')
    ap.add_argument('files', nargs='*', default=None)
    a = ap.parse_args()
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
