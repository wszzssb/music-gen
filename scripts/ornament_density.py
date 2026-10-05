#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""ornament_density.py —— **从模板库直接量"转音"的真实密度**（`HANDOFF-ORNAMENT` §3-B2）。

## 为什么要有它

生成侧的"写几处转音"原先只看**画像派生量**（`melody_gen.ornament_tendency` = 短时值×0.40 +
级进率×0.35 + 弱格占比×0.25）——那是个**组合出来的量**，交接文档明确警告过
"派生量与模板真实转音密度**差多少没人量过**"。本工具就是这个"量"：**直接从
`refs/midi2/` 的模板量**，逐主题出"每小节几处 / 音占比"。

**实测结论（2026-10-06，15 主题）**：两者**秩相关只有 0.12~0.40**（n=15）⇒ 派生量
**不能**当"转音密度"用；生成侧改读本工具的产物（`refs/ornament_density.json`）。

## 口径（与生成端**同一套**，这是可比的唯一前提）

`theme_pack._melody_notes(midi_probe.parse(p))` → `[(起拍, 止拍, 音高)]`（拍 = 四分音符）。
⇒ 参与统计的模板与主题包 `melody.templates` **逐主题相同**（实测 15/15 一致；
"提取不到旋律轨"的模板两边都不算）。

**"run"（转音）的定义**：从某音起向后取 k=3~5 个音，要求 ① **同向** ② 相邻步长 ≤ `max_step`
半音 ③ **总时长落在 [dur_lo, dur_hi] 秒**；命中就记一处并从 k 之后继续扫（**不重叠**）。
三档都算（口径敏感性 → `ornament_probe` 的纪律）：**严**（≤2 半音 · 0.3~0.8s，即 §4-1 的形态）·
**中**（≤2 半音 · ≤1.2s）· **宽**（≤5 半音 · 0.3~0.8s）。

## 用法

```powershell
$py = "<工具链>\.venv\Scripts\python.exe"
& $py scripts\ornament_density.py                 # 量全部主题并写 refs\ornament_density.json
& $py scripts\ornament_density.py daily sorrow    # 只量这几个（只打印，不写盘）
& $py scripts\ornament_density.py --selftest      # 尺子自检（合成已知答案）
```

⚠ 产物是**派生文件**（不是主题包）：主题包仍由 `theme_pack.py` 管；本文件只加"转音密度"一列，
`melody_gen` 读不到它就退回画像派生量（**两条路都留着**，便于 A/B 与回退）。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
LIB = os.path.join(ROOT, 'refs', 'midi2')
THEMES = os.path.join(ROOT, 'refs', 'themes')
OUT = os.path.join(ROOT, 'refs', 'ornament_density.json')
# ⚠ 产物放 `refs/` 根下、**不放 `refs/themes/`**：那个目录的惯例是"主题包 + `<主题>_melody.json`
#   子画像"，任何"扫 `themes/*.json` 当主题包"的工具都可能把这份派生文件误认成主题。

# (键, 标签, 最大步长(半音), 最少音, 最多音, 时长下限(s), 时长上限(s))
GRIDS = [('strict', '严 ≤2半音·3~5音·0.3~0.8s', 2, 3, 5, 0.30, 0.80),
         ('mid', '中 ≤2半音·3~5音·≤1.2s', 2, 3, 5, 0.00, 1.20),
         ('wide', '宽 ≤5半音·3~5音·0.3~0.8s', 5, 3, 5, 0.30, 0.80)]


def find_runs(mel, bpm, max_step=2, min_notes=3, max_notes=5, dur_lo=0.30, dur_hi=0.80):
    """滑窗（不重叠）找"同向级进短跑"。`mel` = [(起拍, 止拍, 音高)]，拍 = 四分音符。"""
    spb = 60.0 / float(bpm or 120.0)
    hits, i = [], 0
    while i <= len(mel) - min_notes:
        best = None
        for k in range(min_notes, max_notes + 1):
            if i + k > len(mel):
                break
            seq = [m[2] for m in mel[i:i + k]]
            steps = [b - a for a, b in zip(seq, seq[1:])]
            if any(s == 0 or abs(s) > max_step for s in steps):
                break                                   # 重复音/大跳 ⇒ 断
            if len({1 if s > 0 else -1 for s in steps}) > 1:
                break                                   # 不同向 ⇒ 断
            dur = (mel[i + k - 1][1] - mel[i][0]) * spb
            if dur > dur_hi:
                break                                   # k 更大只会更长
            if dur < dur_lo:
                continue
            best = (k, seq, dur)
        if best:
            hits.append(best)
            i += best[0]
        else:
            i += 1
    return hits


def measure(path, bpm, timesig=(4, 4)):
    import midi_probe as mp
    import theme_pack as TP
    res = mp.parse(path, quiet=True)
    mel, _tr = TP._melody_notes(res)
    if not mel:
        return None, '提取不到旋律轨'
    num, den = res.get('timesig') or timesig
    bpb = num * 4.0 / den
    bars = max(1.0, max(m[1] for m in mel) / bpb)
    out = {}
    for tag, _lab, mx, mn, xm, lo, hi in GRIDS:
        rs = find_runs(mel, bpm, mx, mn, xm, lo, hi)
        out[tag] = dict(runs=len(rs), runs_per_bar=round(len(rs) / bars, 4),
                        share=round(sum(x[0] for x in rs) / float(len(mel)), 4),
                        n_med=(sorted(x[0] for x in rs)[len(rs) // 2] if rs else 0),
                        dur_med=(round(sorted(x[2] for x in rs)[len(rs) // 2], 3) if rs else 0.0),
                        up=(round(sum(1 for x in rs if x[1][-1] > x[1][0]) / float(len(rs)), 2)
                            if rs else 0.0))
    return dict(notes=len(mel), bars=round(bars, 2), grids=out), ''


def measure_theme(theme, verbose=True):
    import theme_pack as TP
    pack = json.load(open(os.path.join(THEMES, theme + '.json'), encoding='utf-8'))
    per = {tag: [] for tag, *_ in GRIDS}
    ok, failed = 0, []
    for t in (pack.get('templates') or []):
        p = os.path.join(LIB, *str(t['file']).split('/'))
        if not os.path.exists(p):
            failed.append((t['file'], '文件不在'))
            continue
        try:
            r, why = measure(p, t.get('bpm') or 120.0, tuple(t.get('timesig') or (4, 4)))
        except Exception as e:                                   # noqa: BLE001
            failed.append((t['file'], '%s: %s' % (type(e).__name__, e)))
            continue
        if not r:
            failed.append((t['file'], why))
            continue
        ok += 1
        for tag, *_ in GRIDS:
            per[tag].append(r['grids'][tag])
    if not ok:
        return None
    med = {}
    for tag in per:
        xs = sorted(per[tag], key=lambda d: d['share'])
        med[tag] = xs[len(xs) // 2]                              # 主题内取**中位**（抗单首怪例）
    row = dict(templates_ok=ok, templates_failed=len(failed), grids=med)
    if verbose:
        print('%-12s 模板可用 %2d / 失败 %d' % (theme, ok, len(failed)))
        for tag, lab, *_ in GRIDS:
            m = med[tag]
            print('    %-28s 每小节 %.2f 处 · 音占比 %4.1f%% · 音数中位 %d · 时长中位 %.2fs · 上行 %2d%%'
                  % (lab, m['runs_per_bar'], 100 * m['share'], m['n_med'], m['dur_med'],
                     100 * m['up']))
    return row


def selftest():
    """**尺子自检**：合成已知答案（不依赖外部文件）—— 好件必须响、坏件必须不响。"""
    ok = True
    # ① 3 音上行级进、每音 0.5 拍，@120bpm 共 0.75s ⇒ 严档必须命中
    mel1 = [(0.0, 0.5, 60), (0.5, 1.0, 62), (1.0, 1.5, 64), (2.0, 3.0, 60)]
    r1 = find_runs(mel1, 120.0)
    good = len(r1) == 1 and r1[0][1] == [60, 62, 64]
    print('  [%s] 3 音上行级进（0.75s）        期望 1 处  实得 %d 处 %s'
          % ('PASS' if good else 'FAIL', len(r1), [x[1] for x in r1]))
    ok = ok and good
    # ② 上行后下行（换向）⇒ 不许算成一处同向 run
    mel2 = [(0.0, 0.5, 60), (0.5, 1.0, 62), (1.0, 1.5, 64), (1.5, 2.0, 62)]
    r2 = [x for x in find_runs(mel2, 120.0) if x[1] == [60, 62, 64]]
    good = len(r2) == 1 and all(x[1][-1] > x[1][0] for x in r2)
    print('  [%s] 上行后换向（不许算同一处）      期望仍只有那 1 处上行 实得 %s'
          % ('PASS' if good else 'FAIL', [x[1] for x in find_runs(mel2, 120.0)]))
    ok = ok and good
    # ③ 大跳（>2 半音）⇒ 严档不许命中
    mel3 = [(0.0, 0.5, 60), (0.5, 1.0, 67), (1.0, 1.5, 74)]
    good = not find_runs(mel3, 120.0)
    print('  [%s] 大跳（60→67→74）             期望 0 处  实得 %d 处'
          % ('PASS' if good else 'FAIL', len(find_runs(mel3, 120.0))))
    ok = ok and good
    # ④ 太慢（每音 1 拍 = 1.5s）⇒ 严档不许命中（§4-1 要的是"短促"）
    mel4 = [(0.0, 1.0, 60), (1.0, 2.0, 62), (2.0, 3.0, 64)]
    good = not find_runs(mel4, 120.0)
    print('  [%s] 慢速级进（每音 1 拍）          期望 0 处  实得 %d 处'
          % ('PASS' if good else 'FAIL', len(find_runs(mel4, 120.0))))
    ok = ok and good
    # ⑤ 重复音不许算（同音不是"级进"）
    mel5 = [(0.0, 0.5, 60), (0.5, 1.0, 60), (1.0, 1.5, 62)]
    good = not find_runs(mel5, 120.0)
    print('  [%s] 重复音起头                      期望 0 处  实得 %d 处'
          % ('PASS' if good else 'FAIL', len(find_runs(mel5, 120.0))))
    ok = ok and good
    print('  尺子自检 %s' % ('全部通过' if ok else '有失败'))
    return ok


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    if '--selftest' in sys.argv:
        return 0 if selftest() else 1
    themes = args or sorted(
        n[:-5] for n in os.listdir(THEMES)
        if n.endswith('.json') and not n.endswith('_melody.json')
        and n != 'ornament_density.json')
    rows = {}
    for th in themes:
        r = measure_theme(th)
        if r:
            rows[th] = r
    if args:
        return 0                                   # 指定主题 = 只看，不写盘
    import melody_gen as MG
    corr = {}
    a, b = [], []
    for th, r in rows.items():
        try:
            prof = json.load(open(os.path.join(THEMES, th + '_melody.json'), encoding='utf-8'))
        except OSError:
            continue
        r['b1_tend'] = round(MG.ornament_tendency(prof)['tend'], 4)
        a.append(r['b1_tend'])
        b.append(r['grids']['strict']['runs_per_bar'])
    corr['b1_tend_vs_strict_runs_per_bar'] = round(_rho(a, b), 3)
    doc = {'_meta': {'basis': '模板库直接量（refs/midi2）—— **不是**派生量',
                     'script': 'scripts/ornament_density.py',
                     'melody_extract': 'theme_pack._melody_notes（与主题包同一口径）',
                     'run_def': '同向 · 相邻步长 ≤max_step 半音 · 3~5 音 · 总时长在 [lo,hi] 秒 · 不重叠',
                     'grids': {t: {'label': lab, 'max_step': mx, 'notes': [mn, xm],
                                   'dur_sec': [lo, hi]}
                               for t, lab, mx, mn, xm, lo, hi in GRIDS},
                     'main_grid': 'strict',
                     'spearman_b1_vs_strict': corr['b1_tend_vs_strict_runs_per_bar'],
                     'note': '生成侧（melody_gen）优先读本文件；读不到才退回画像派生量 B1'},
           'themes': rows}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, sort_keys=True)
    print('\n→ %s（%d 个主题）' % (OUT, len(rows)))
    print('B1 派生量 vs B2 严档 每小节处数：Spearman rho=%.2f'
          % corr['b1_tend_vs_strict_runs_per_bar'])
    return 0


def _rho(a, b):
    if len(a) < 4:
        return 0.0
    ra, rb = _rank(a), _rank(b)
    n = len(a)
    return 1 - 6.0 * sum((x - y) ** 2 for x, y in zip(ra, rb)) / (n * (n * n - 1))


def _rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    for pos, i in enumerate(order):
        r[i] = float(pos + 1)
    return r


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                          # noqa: BLE001
        pass
    sys.exit(main())
