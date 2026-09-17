# -*- coding: utf-8 -*-
"""PITFALLS.md 归档：把较早的条目剪切进 PITFALLS-ARCHIVE.md（**编号不变**）。

依据：文件头自己写着"本文件只留'新近 + 高频'的坑 —— 按 token 预算定期归档"，
但实测已长到 **15149 tok / 上限 9600**（超 58%），自检 `docs_budget_and_skill_intact` 一直 FAIL。

做法：按**行首编号行**（`^144. `）切条目，把 144–160 的段落整体搬到归档文件末尾。
改完必须复核：① 条目总数不变 ② 两个文件都能被 read ③ 自检是否 PASS。
"""
import os
import re
import sys

ROOT = r'D:\software\skill'
P = os.path.join(ROOT, 'PITFALLS.md')
A = os.path.join(ROOT, 'PITFALLS-ARCHIVE.md')
LO, HI = 144, 160

src = open(P, encoding='utf-8').read().splitlines(keepends=True)
idx = [(i, int(m.group(1))) for i, ln in enumerate(src)
       if (m := re.match(r'^(\d{3})\. ', ln))]
print('PITFALLS.md 现有条目 %d 个：%s … %s'
      % (len(idx), idx[0][1], idx[-1][1]))

# 条目 i 的范围 = [idx[i][0], idx[i+1][0])，最后一条到文件末
blocks = []
for k, (i, num) in enumerate(idx):
    end = idx[k + 1][0] if k + 1 < len(idx) else len(src)
    blocks.append((num, i, end))
move = [b for b in blocks if LO <= b[0] <= HI]
keep = [b for b in blocks if not (LO <= b[0] <= HI)]
print('要归档 %d 条（%d–%d）· 保留 %d 条' % (len(move), LO, HI, len(keep)))

head_end = blocks[0][1]                      # 头部说明 = 第一条之前
new_src = src[:head_end] + [ln for (num, i, e) in keep for ln in src[i:e]]
moved_txt = [ln for (num, i, e) in move for ln in src[i:e]]

open(A, 'a', encoding='utf-8').write(
    '\n\n<!-- 2026-09-17 从 PITFALLS.md 归档（编号不变）：%d–%d -->\n\n' % (LO, HI)
    + ''.join(moved_txt).rstrip() + '\n')

# 头部索引：把归档区间写进去
head = ''.join(new_src[:head_end])
head = re.sub(r'> 归档的坑见 `PITFALLS-ARCHIVE\.md`（([^）]*)）',
              lambda m: '> 归档的坑见 `PITFALLS-ARCHIVE.md`（%s，%d–%d，**编号不变**）'
                        % (m.group(1), LO, HI), head, count=1)
head = re.sub(r'^# 坑台账（当前：[^）]*）', '# 坑台账（当前：%d–%d）' % (LO + 1 if False else keep[0][0], keep[-1][0]),
              head, count=1, flags=re.M)
open(P, 'w', encoding='utf-8').write(head + ''.join(new_src[head_end:]))

sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import token_audit as T  # noqa: E402
for path, label in ((P, 'PITFALLS.md'), (A, 'PITFALLS-ARCHIVE.md')):
    tok = T.est(open(path, encoding='utf-8').read())
    lim = T.LIMITS.get(label)
    print('  %-22s ≈%5d tok（上限 %s）%s' % (label, tok, lim or '-',
                                            '' if not lim else ('  ✓' if tok <= lim else '  ✗ 仍超')))
