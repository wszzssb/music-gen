# -*- coding: utf-8 -*-
"""PITFALLS.md 归档：把较早的条目剪切进 PITFALLS-ARCHIVE.md（**编号不变**）。

依据：文件头自己写着"本文件只留'新近 + 高频'的坑 —— 按 token 预算定期归档"。

⚠ **本脚本第一版不幂等，被重复跑坏过一次**（2026-09-17）：它对头部索引做的是
**追加**式 `re.sub(..., lambda m: m.group(1) + '，%d–%d' % (LO, HI))`，
于是第二次运行时变成
`…、133–143，144–160，**编号不变**，144–160，**编号不变**）。`
（当时是另一个会话又跑了一遍它，把台账头部弄花了。）

现在加两道守卫：
  ① **已归档过就直接退出** —— 编号不在 `PITFALLS.md` 里就说明搬过了，不动任何文件；
  ② 头部索引**整体重写**（不是追加）—— 从归档文件里的
     `<!-- 从 PITFALLS.md 归档（编号不变）：A–B -->` 标记**重新推导**，不拼旧字符串。

用法：`python scripts/pitfalls_archive.py [起始编号] [结束编号]`（缺省 161 180）
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, 'PITFALLS.md')
A = os.path.join(ROOT, 'PITFALLS-ARCHIVE.md')


def nums_of(path):
    out = []
    for ln in open(path, encoding='utf-8'):
        m = re.match(r'^(\d{3})\. ', ln)
        if m:
            out.append(int(m.group(1)))
    return out


def archived_ranges(path):
    txt = open(path, encoding='utf-8').read()
    return [(int(a), int(b)) for a, b in
            re.findall(r'从 PITFALLS\.md 归档（编号不变）：(\d+)–(\d+)', txt)]


def fmt_ranges(rs):
    parts = []
    for i, (a, b) in enumerate(rs):
        s = '%d–%d' % (a, b)
        parts.append('**%s**' % s if i == len(rs) - 1 else s)
    return '、'.join(parts)


def main():
    lo = int(sys.argv[1]) if len(sys.argv) > 1 else 161
    hi = int(sys.argv[2]) if len(sys.argv) > 2 else 180

    have = nums_of(P)
    missing = [n for n in range(lo, hi + 1) if n not in have]
    if not missing:
        print('  ✓ %d–%d 已经归档过（PITFALLS.md 里已无这些条目），跳过 —— 不重复动文件'
              % (lo, hi))
        return 0

    src = open(P, encoding='utf-8').read().splitlines(keepends=True)
    idx = [(i, int(m.group(1))) for i, ln in enumerate(src)
           if (m := re.match(r'^(\d{3})\. ', ln))]
    blocks = []
    for k, (i, num) in enumerate(idx):
        end = idx[k + 1][0] if k + 1 < len(idx) else len(src)
        blocks.append((num, i, end))
    move = [b for b in blocks if lo <= b[0] <= hi]
    keep = [b for b in blocks if not (lo <= b[0] <= hi)]
    if not move:
        print('  ✓ 没有可搬的条目')
        return 0
    print('  搬 %d 条（%d–%d）· 保留 %d 条' % (len(move), lo, hi, len(keep)))

    head_end = blocks[0][1]
    new_src = src[:head_end] + [ln for (_n, i, e) in keep for ln in src[i:e]]
    moved = [ln for (_n, i, e) in move for ln in src[i:e]]

    with open(A, 'a', encoding='utf-8') as f:
        f.write('\n\n<!-- 从 PITFALLS.md 归档（编号不变）：%d–%d -->\n\n' % (lo, hi))
        f.write(''.join(moved).rstrip() + '\n')

    rs = archived_ranges(A)
    head = ''.join(new_src[:head_end])
    head = re.sub(r'^# 坑台账（当前：[^）]*）',
                  '# 坑台账（当前：%d–%d）' % (keep[0][0], keep[-1][0]),
                  head, count=1, flags=re.M)
    head = re.sub(r'> 归档的坑见 `PITFALLS-ARCHIVE\.md`（[^）]*）。',
                  '> 归档的坑见 `PITFALLS-ARCHIVE.md`（%s，**编号不变**）。' % fmt_ranges(rs),
                  head, count=1)
    open(P, 'w', encoding='utf-8').write(head + ''.join(new_src[head_end:]))

    sys.path.insert(0, os.path.join(ROOT, 'scripts'))
    import token_audit as T
    tok = T.est(open(P, encoding='utf-8').read())
    lim = T.LIMITS.get('PITFALLS.md（出症状才按编号查）')
    print('  PITFALLS.md ≈%d tok（上限 %s）%s' % (tok, lim, '  ✓' if tok <= lim else '  ✗ 仍超'))
    print('  头部索引 → %s' % fmt_ranges(rs))
    return 0


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    sys.exit(main())
