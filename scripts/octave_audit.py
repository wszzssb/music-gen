# -*- coding: utf-8 -*-
"""八度错误率审计 —— 还原/扒带量"识别准不准"时用（PITFALLS 180 的第一道工序）。

## 何时用

扒完参考曲、量识别精度时。**八度错是最常见、也最会骗过频谱指标的系统性错误**：
BGM29 那轮把低音补到 1416 音、带差做到 0.83dB（**看起来更好了**），
而那批音里 **25.3% 弹的是错八度**（一致率只有 48.5%）。

## 读法（重要）

看 **"占全部音符"** 那个数。只看"占未匹配"会被**小分母放大** ——
实测某版未匹配仅 108 音，"占未匹配 63%" 听着吓人，其实只占全曲 **1.7%**。
⚠ **多轨版本不能用这个口径比**：单轨参照（如钢琴改编谱）与多轨转录天然对不上，
那是口径问题不是质量问题。

## 用法

```bash
python scripts/octave_audit.py <被评.mid> --ref <参照.mid> [--grid 0.1]
python scripts/octave_audit.py <参照.mid> --ref <参照.mid>     # 自检：应 P=R=1.000
```

口径与 `eval_transcription.py` 一致：同格 + 同音高一对一计数（`floor(t/grid + eps)`）。
用 `midi_file.import_midi` 解析（**纯标准库**，主 venv 可用，不需要 mido）。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import midi_file                                              # noqa: E402


def load(path, grid):
    """→ {(格, 音高): 个数}。⚠ `notes` 的起始时间是**拍**（四分音符），
    要乘 `60/bpm` 换成秒 —— `midi_file` 的数据模型就是"拍"，别当秒用。"""
    d = midi_file.import_midi(str(path))
    spb = 60.0 / max(1e-9, float(d.get('bpm') or 120.0))
    cells = {}
    for tr in d.get('tracks') or []:
        for it in tr.get('notes') or []:
            if len(it) < 4 or int(it[3]) <= 0:
                continue
            g = int(float(it[0]) * spb / grid + 1e-6)
            key = (g, int(it[2]))
            cells[key] = cells.get(key, 0) + 1
    return cells


def audit(mine, ref, grid=0.1):
    a, b = load(mine, grid), load(ref, grid)
    matched = sum(min(c, b.get(k, 0)) for k, c in a.items())
    tot_mine, tot_ref = sum(a.values()), sum(b.values())
    unmatched = tot_mine - matched
    hits = {}
    for sh in (12, 24):
        got = 0
        for (g, p), c in a.items():
            extra = c - min(c, b.get((g, p), 0))
            for s in (+sh, -sh):
                if extra <= 0:
                    break
                take = min(extra, b.get((g, p + s), 0))
                got += take
                extra -= take
        hits[sh] = got
    return {
        'notes_mine': tot_mine, 'notes_ref': tot_ref, 'matched': matched,
        'unmatched': unmatched,
        'P': matched / max(1, tot_mine), 'R': matched / max(1, tot_ref),
        'oct12': hits[12], 'oct24': hits[24],
        'oct12_pct_all': 100.0 * hits[12] / max(1, tot_mine),
        'oct12_pct_unm': 100.0 * hits[12] / max(1, unmatched),
    }


def main():
    import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
    ap = argparse.ArgumentParser(description='八度错误率审计')
    ap.add_argument('mine', help='被评 MIDI')
    ap.add_argument('--ref', required=True, help='参照 MIDI')
    ap.add_argument('--grid', type=float, default=0.1, help='量化格（秒，默认 0.1）')
    a = ap.parse_args()
    if not os.path.exists(a.mine) or not os.path.exists(a.ref):
        raise SystemExit('文件不存在：%s / %s' % (a.mine, a.ref))

    r = audit(a.mine, a.ref, a.grid)
    print('被评 %s：%d 音' % (os.path.basename(a.mine), r['notes_mine']))
    print('参照 %s：%d 音' % (os.path.basename(a.ref), r['notes_ref']))
    print('匹配 %d 音（P %.3f · R %.3f）' % (r['matched'], r['P'], r['R']))
    print('未匹配 %d 音' % r['unmatched'])
    for sh in (12, 24):
        print('  其中 ±%d 半音处参照有音：%d 音'
              '（占未匹配 %.1f%% · **占全部音符 %.1f%%**）'
              % (sh, r['oct' + str(sh)],
                 100.0 * r['oct' + str(sh)] / max(1, r['unmatched']),
                 100.0 * r['oct' + str(sh)] / max(1, r['notes_mine'])))
    print()
    print('判读（用**占全部音符**那个数）：')
    print('  · 恒等输入自检应 P=R=1.000 —— 先跑一次 <参照> --ref <参照>，尺子不对就别读数')
    print('  · ±12 占全部 >10%% → 系统性八度记错，应做八度对齐')
    print('  · 低 → 是"内容不同/漏音"，不是八度问题')
    print('  · ⚠ 多轨版本不能用这个口径（单轨参照与多轨转录天然对不上）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
