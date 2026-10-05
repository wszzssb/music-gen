#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""placement_survey.py —— **落点位置体检**（量"音的位置奇不奇怪"，只读）。

## 何时用

用户听感说"音的位置有点奇怪 / 句子不从拍点起"时，**先量再猜**。本工具给三张表：

1. **每首生成曲**：格 0（小节第 1 拍）落点占比 `g0`、`last8`（跨过第 2 拍的小节占比）、
   密度、与画像的落点 TVD（全曲 / 最差段）；
2. **每主题的真实模板基线**：用与主题包**同一套** `theme_pack._melody_notes` 逐首提旋律，
   给 `g0` 的 **min / 中位 / 均值 / max**（中位与均值都要看，见下面那条口径）；
3. **口径自检**：画像的 `g0` 与"4/4 模板均值"逐主题对照。

## ⚠ 口径（会用错的那一处，实测踩到）

主题包 `melody.onset16_hist` 的格 0 占比 ≈ **模板的均值**，不是中位
（逐主题吻合到 0.1pt，例：sorrow 画像 13.9% = 均值 13.9%、中位 11.6%）
—— 因为主题包把模板音符**汇总**后算比例 ≈ 按音符数加权。
而 `melody_gen.form_stats.g0` 是**按 sections 展开复用旋律**的逐音符口径（两者可比）。
⇒ **比 g0 时对"画像 / 4/4 模板均值"**；拿"模板中位"当基线会把差距**低估约 4pt**
（全库：均值 19.8% vs 中位 15.2%）。

⚠ `waltz` 是 3/4 拍：`%16` 判小节首拍对它**不成立**（实测该主题 4/4 档为 0%），
比较时把它的"4/4 中位/均值"当无效值看。

## 用法

```powershell
& $py scripts\placement_survey.py                      # 写 UTF-8 报告到 _tmp 并打印路径
& $py scripts\placement_survey.py --stdout             # 直接打印（控制台是 GBK 时中文会崩）
& $py scripts\placement_survey.py --out D:\x.txt       # 指定报告路径
```

模板 MIDI 不在仓库里（版权，见 `.gitignore`）：缺 `refs/midi2/**/*.mid` 时第 2 张表会是空，
这时只读第 1 张表即可（第 1 张表不依赖模板文件）。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import melody_gen as mg      # noqa: E402
import midi_probe as mp      # noqa: E402
import theme_pack as tp      # noqa: E402

SONGS = os.path.join(ROOT, 'songs_direct')
TDIR = os.path.join(ROOT, 'refs', 'themes')
DEFAULT_OUT = os.path.join(os.path.expanduser('~'), 'placement_survey.txt')


def load(p):
    with open(p, encoding='utf-8') as fh:
        return json.load(fh)


def profile_of(name):
    """画像在独立文件 `refs/themes/<name>_melody.json`（顶层带 `onset16_hist`）。"""
    p = os.path.join(TDIR, '%s_melody.json' % name)
    return load(p) if os.path.exists(p) else {}


def g0_of(hist):
    tot = float(sum(hist.values())) or 1.0
    return float(hist.get('0', hist.get(0, 0))) / tot


def find_template_midi(name):
    if not isinstance(name, str):
        return None
    base = os.path.join(ROOT, 'refs', 'midi2')
    direct = os.path.join(base, name.replace('/', os.sep))
    if os.path.exists(direct):
        return direct
    base_fn = os.path.basename(name)
    for dirpath, _dirs, files in os.walk(base):
        if base_fn in files:
            return os.path.join(dirpath, base_fn)
    return None


def quiet_melody(path):
    """提模板旋律，**顺手压掉 `midi_probe.parse` 的逐轨打印**。

    为什么（2026-10-06 实测）：`midi_probe.parse` 每首会打印整张"轨/音域/tick"表，
    跑 15 主题 × 十几个模板 ⇒ **几万行刷屏**，报告本身被淹掉（我在同一轮里踩了两次）。
    """
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        got = tp._melody_notes(mp.parse(path))
    return got[0] if isinstance(got, tuple) else got


def survey(out):
    packs = [f[:-5] for f in sorted(os.listdir(TDIR))
             if f.endswith('.json') and not f.endswith('_melody.json')]
    out.write('主题包 %d 个：%s\n\n' % (len(packs), ' '.join(packs)))

    # ① 生成曲
    rows = []
    for name in sorted(os.listdir(SONGS)) if os.path.isdir(SONGS) else []:
        sp = os.path.join(SONGS, name, 'song.json')
        if not os.path.exists(sp):
            continue
        d = load(sp)
        if 'melody_gen' not in d:          # 只看生成曲（还原曲没有 melody_gen 元数据）
            continue
        th = d.get('theme') or {}
        theme = th.get('name') if isinstance(th, dict) else th
        prof = profile_of(theme or '')
        mel, secs = d.get('melody') or {}, d.get('sections') or []
        fs = mg.form_stats(mel, secs) or {}
        try:
            worst = mg.onset_tvd_worst(mel, secs, prof) if prof else float('nan')
        except Exception:
            worst = float('nan')
        rows.append({
            'name': name, 'theme': theme or '?', 'g0': fs.get('g0'),
            'last8': fs.get('last8'), 'dens': fs.get('dens'),
            'tvd': mg.onset_tvd(mel, prof) if prof else float('nan'), 'worst': worst,
            'prof_g0': g0_of((prof or {}).get('onset16_hist') or {}),
        })
    out.write('=== ① 生成曲（%d 首）===\n' % len(rows))
    out.write('%-24s %-11s %7s %7s %7s %8s %8s %8s\n'
              % ('曲目', '主题', 'g0', 'last8', '密度', 'TVD全曲', 'TVD最差', '画像g0'))
    for r in rows:
        out.write('%-24s %-11s %6s%% %6s%% %7s %8s %8s %7s%%\n' % (
            r['name'], r['theme'], _pct(r['g0']), _pct(r['last8']), _num(r['dens']),
            _num(r['tvd']), _num(r['worst']), _pct(r['prof_g0'])))
    g = [r['g0'] for r in rows if r['g0'] is not None]
    if g:
        out.write('\ng0 汇总：%d 首 · 中位 %.1f%% · 最小 %.1f%% · 最大 %.1f%%'
                  '（画像中位 %.1f%%）\n'
                  % (len(g), 100 * _med(g), 100 * min(g), 100 * max(g),
                     100 * _med([r['prof_g0'] for r in rows])))

    # ② 真实模板基线
    out.write('\n=== ② 真实模板 g0 基线（与主题包同一套 `_melody_notes`）===\n')
    out.write('%-12s %5s %8s %8s %8s %8s %8s\n'
              % ('主题', '模板数', 'g0最小', 'g0中位', 'g0均值', 'g0最大', '提到MIDI'))
    all44 = []
    for th in packs:
        pack = load(os.path.join(TDIR, th + '.json'))
        # ⚠ 模板名列表在主题包**顶层** `templates`（元素是 dict）；`melody.templates` 是**计数**。
        want = pack.get('templates') or []
        vals, used = [], 0
        for tinfo in want:
            tname = tinfo.get('file') if isinstance(tinfo, dict) else tinfo
            p = find_template_midi(tname)
            if not p:
                continue
            try:
                notes = quiet_melody(p)
            except Exception:
                continue
            if not notes or len(notes) < 8:
                continue
            used += 1
            h = {}
            for x in notes:
                g = int(round(x[0] * 4)) % 16
                h[g] = h.get(g, 0) + 1
            tot = float(sum(h.values())) or 1.0
            vals.append(h.get(0, 0) / tot)
        all44 += vals
        out.write('%-12s %5d %7s%% %7s%% %7s%% %7s%% %8d\n' % (
            th, len(want), _pct(min(vals) if vals else None),
            _pct(_med(vals) if vals else None), _pct(_avg(vals) if vals else None),
            _pct(max(vals) if vals else None), used))
    out.write('\n基线口径提醒：**画像 ≈ 模板均值**（汇总口径），不是中位'
              '（全库 4/4 模板：均值 %.1f%% / 中位 %.1f%%）。\n'
              % (100 * _avg(all44), 100 * _med(all44)))
    out.write('⚠ `waltz` 是 3/4 拍，`%%16` 判小节首拍对它不成立（该主题数值当无效看）。\n')


def _pct(x):
    return '—' if x is None else '%.1f' % (100 * float(x))


def _num(x):
    try:
        return '—' if x != x else '%.3f' % float(x)
    except Exception:
        return '—'


def _med(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return 0.0
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


def main():
    import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印中文/✓ 会崩）
    ap = argparse.ArgumentParser(description='落点位置体检（只读）')
    ap.add_argument('--out', default=None, help='报告路径（默认写到用户目录）')
    ap.add_argument('--stdout', action='store_true', help='直接打印（控制台非 UTF-8 会崩）')
    a = ap.parse_args()
    if a.stdout:
        survey(sys.stdout)
        return 0
    path = a.out or DEFAULT_OUT
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        survey(fh)
    print('报告已写出（UTF-8）：%s' % path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
