#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""selfcheck.py —— 单曲自检：报**离群量**，不报「问题」。

═══ 为什么是"离群量"而不是"问题"（2026-09-21 全库实测）═══
 阈值型判据在本库基本失效 —— `harmony_check` 的撞音 **32/30 首恒真**、音区 39/30 近恒真、
 长音层 0/30 与 `probe_melody_health` 6 维 **0/31 从不触发**；而且"仿写好曲 `b35_remake`
 撞音 498 处、比我们多"—— **方向与听感相反**。撞音消融 A/B 用户听"差不多"。
 → **报"有问题"必然是噪声**（用户特意要求加的"和谐检查"实际等于没查）。
 → 改为报：**这一项你偏离全库中位多少** + **会听起来像什么**。
   好处：不依赖任何阈值、每个数字都实测、模型编不了；用户不需要知道标准，
   看到"偏低/偏高"就知道方向。依据与全部数字见 `PITFALLS.md` 225 · SKILL §3 第 16 条。

═══ 判据不重复实现（CONVENTION §1：抄一份 = 埋一处漂移）═══
 本工具**只做两件事**：① 调 `probe_melody_health.py`（它量 9 维，含小步率）
 ② 把该曲的 9 个数与**全库分布**比对，报离群项 + 听感映射。
 量法、阈值、口径全部沿用现有工具 —— 本文件不新增任何度量。

用法:
  python scripts\selfcheck.py 20_piano_rain      # 报该曲的离群项
  python scripts\selfcheck.py --all               # 全库按"离群总分"排序
  python scripts\selfcheck.py 20_piano_rain --json
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
sys.path.insert(0, HERE)
try:
    import cli_utf8 as _cu
    _cu.setup()
except Exception:                                     # noqa: BLE001
    pass

# 与 `probe_melody_health.py` 表头同序的列名
COLS = ['音数', '音/小节', '同音率', '最长同音串', '小步率', '碎音率',
        '落点格', '正拍率', '强拍贴合']

# 每个维度：偏低时听起来像 / 偏高时听起来像（空串 = 该方向无已知听感含义）
HEARD = {
    '音/小节': ('音少 → 重复感被放大', '偏满 → 有点挤'),
    '同音率': ('', '同音反复多 → 像念经'),
    '最长同音串': ('', '连续同音 → "d d d d"卡住'),
    '小步率': ('跳进偏多 → **不够连贯**', '级进多 → 更顺'),
    '碎音率': ('', '音符被切碎 → **发抖 / 卡卡的**'),
    '落点格': ('节奏语言单一 → 呆板', '节奏语言丰富'),
    '正拍率': ('变化多', '全踩正拍 → 偏呆板'),
    '强拍贴合': ('强拍不在和弦音上（硬纪律）', ''),
}
# 有意**不给听感映射**的维度（要写下来，不许默默空着）：
# `音数` = 整首音符总数，它偏离全库中位的原因多半是**曲子长短 / 段数**，
# 不是听感属性（同一密度下短曲天然少）—— 报"音符少"会把用户引到错的方向。
# `t_selfcheck_outliers` 会断言 `COLS ⊆ HEARD ∪ NO_HEARD`：新加一维时
# 要么给听感，要么在这里登记为"故意不报"。
NO_HEARD = {'音数'}
# ⚠ 这里原来挂着一个 `HIGHER_IS_BETTER = {'小步率'}`，**全仓库没有一处读它**
# （2026-09-21 清）—— 方向语义本来就写在 `HEARD` 的两侧字符串里，再挂一个没人读的
# 常量只会让人以为"方向已经被处理了"。**判据只留一份**（CONVENTION §1）。


_TABLE = {}          # 进程内缓存（`selftest` / `mutation_check` 会反复调它；磁盘数据同一轮不变）


def _run_melody_health(force=False):
    """调现有工具（全库一次），返回 {曲名: [N 个数]}。判据来源唯一。

    **维度以表头为准**：这里按**表头列数**切片，并每次与 `COLS` 对齐。
    原来写死 `parts[1:10]`（9 维）—— `probe_melody_health` 将来加第 10 维时，
    解析照样"成功"，只是**静默少报一维**（连测试都看不出来）。
    漂移必须在这里响（CONVENTION §1：抄一份 = 埋一处漂移）。
    """
    if _TABLE and not force:
        return dict(_TABLE)
    r = subprocess.run([PY, os.path.join(HERE, 'probe_melody_health.py')],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', cwd=ROOT)
    rows, ncol, started = {}, None, False
    for ln in (r.stdout or '').splitlines():
        if ln.startswith('曲目'):
            ncol = len(ln.split())
            assert ncol == len(COLS) + 1, (
                'probe_melody_health 表头 %d 列，而 selfcheck.COLS 声明 %d 维（+「曲目」列）'
                '—— 维度漂移，先对齐 COLS / HEARD' % (ncol, len(COLS)))
            started = True
            continue
        if not started:
            continue
        if ln.startswith('有问题'):
            break
        parts = ln.split()
        if len(parts) < ncol:          # 行尾的「← 问题」列可有可无，只取前 ncol 个
            continue
        vals = []
        for p in parts[1:ncol]:
            try:
                vals.append(float(p.rstrip('%')))
            except ValueError:
                vals.append(None)
        if len(vals) == len(COLS):
            rows[parts[0]] = vals
    _TABLE.clear()
    _TABLE.update(rows)
    return dict(_TABLE)


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _pct_rank(xs, v):
    """v 在全库 xs 里的百分位（0~100，越大越高）。"""
    xs = [x for x in xs if x is not None]
    if not xs or v is None:
        return None
    return 100.0 * sum(1 for x in xs if x <= v) / len(xs)


def rank_all(table):
    """全库按**离群总分**降序：每一维"到中位的百分位距离"取平均（0~50）。

    抽成函数是为了让自检能直接断言它（原来这段逻辑埋在 `main()` 里，只能靠 subprocess
    端到端验 —— 与 `cand_score` 那次"注入打不进去"同类，见 `mutation_check.py`）。
    """
    scored = []
    for n in sorted(table):
        vals = table[n]
        dev, seen = 0.0, 0
        for i in range(len(COLS)):
            cv = [r[i] for r in table.values()]
            pr = _pct_rank(cv, vals[i])
            if pr is not None:
                dev += abs(pr - 50.0)
                seen += 1
        scored.append((n, dev / (seen or 1)))
    scored.sort(key=lambda x: -x[1])
    return scored


def report(table, name, as_json=False):
    if name not in table:
        print('全库里没有这首曲目：%s' % name)
        print('可用：%s' % ', '.join(sorted(table)[:12]))
        return 1
    vals = table[name]
    items = []
    for i, col in enumerate(COLS):
        col_vals = [r[i] for r in table.values()]
        med = _median(col_vals)
        pr = _pct_rank(col_vals, vals[i])
        if med is None or pr is None or vals[i] is None:
            continue
        low, high = HEARD.get(col, ('', ''))
        # 偏离程度：离中位多远（用百分位到 50 的距离）
        dev = abs(pr - 50.0)
        side = '偏低' if pr < 50 else '偏高'
        heard = low if pr < 50 else high
        items.append({'col': col, 'val': vals[i], 'median': med,
                      'pct': round(pr, 1), 'side': side,
                      'dev': round(dev, 1), 'heard': heard})
    items.sort(key=lambda x: -x['dev'])

    if as_json:
        print(json.dumps({'song': name, 'items': items}, ensure_ascii=False, indent=1))
        return 0

    W = 78
    print('=' * W)
    print(' 离群清单：%s       （全库 %d 首，按偏离度排序）' % (name, len(table)))
    print('=' * W)
    print('%-12s %9s %9s %8s %6s  %s' % ('维度', '本曲', '全库中位', '百分位', '方向', '听起来会像'))
    print('-' * W)
    for it in items:
        mark = '  ' if it['dev'] < 25 else ('⚠ ' if it['dev'] < 40 else '⚠⚠')
        print('%s%-10s %9.2f %9.2f %7.0f%% %6s  %s'
              % (mark, it['col'], it['val'], it['median'], it['pct'], it['side'],
                 it['heard'] or '—'))
    print('-' * W)
    print('判读：**百分位离 50 越远越离群**；⚠ 越多越该优先听那一段。')
    print('⚠ 离群 ≠ 有问题 —— 这是"你偏离群体多少"，不是判据；')
    print('   曲风本来就该有别的取值（如舞曲的正拍率天然高）。')
    top = [x for x in items if x['dev'] >= 40]
    if top:
        print('\n最该先听的：%s' % '；'.join(
            '%s %s（%.0f%%）→ %s' % (x['col'], x['side'], x['pct'], x['heard'])
            for x in top[:3] if x['heard']))
    print('=' * W)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('song', nargs='?')
    ap.add_argument('--all', action='store_true', help='全库按离群总分排序')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    table = _run_melody_health()
    if not table:
        raise SystemExit('解析 probe_melody_health 输出失败 —— 先单独跑一次看它能不能出表')

    if a.all:
        scored = rank_all(table)
        print('全库 %d 首，按"离群总分"降序（越高越该听）：' % len(scored))
        for n, s in scored:
            print('  %6.1f  %s' % (s, n))
        return 0

    if not a.song:
        raise SystemExit('给曲目名，或用 --all')
    return report(table, a.song, a.json)


if __name__ == '__main__':
    sys.exit(main())
