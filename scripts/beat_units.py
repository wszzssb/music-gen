#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""beat_units.py —— **拍值单位换算**：凡是"把转录/集成产物写进 song.json"的环节都必须过这一关。

## 为什么（2026-10-02，BGM35 实测，会跨曲复现的一类缺陷）

链路里有两套"拍"，它们**默认不相等**：

| 产物 | 它的 beat 是相对谁 | 它的"秒"是不是真秒 |
|---|---|---|
| YourMT3 / Basic Pitch 的转录 MIDI | **它自己 `set_tempo` 声明的 bpm** | ❌ 不是（对真实音频而言） |
| `song.json` | **曲子的 `bpm` 字段** | — |

`transcribe_to_song.read_notes()` 用 `spb = 60/转录声明bpm` 把 beat 换成**秒**，
再用 `bar_sec = 4*60/曲子bpm` 把秒换回 beat —— 这对得上（秒是真秒）。
但 **`bass_ensemble.py` 是直接 `round(t / spb, 6)`**，`spb` 取自**基准 MIDI 声明**的 bpm ——
于是当"转录声明的 bpm ≠ 曲子 bpm"时，整条轨被静默缩放：

```
BGM35：YMT3 输出恒 120BPM，曲子 149.8BPM ⇒ 缩放 120/149.8 = 0.801
      Bass 末音落在 264.1 秒（全曲 331.9）· 266 秒之后的低音一个事件都没有
      · 曲首 −0.6 秒 → 曲末 −7.9 秒（累积漂移）
```

**它伪装成"精度不够"**：帧级一致率读成 10.4%，看着像识别问题；
而 `preflight` 的逐秒密度只报"过少几秒"，也不指方向。

## 契约

```python
ratio = scale_ratio(src_bpm, dst_bpm)        # dst/src：beat_src × ratio = beat_dst
beat_dst = convert_beats(beat_src, src_bpm, dst_bpm)
assert_same_bpm(src_bpm, dst_bpm, what)      # 比值≠1 时**打印/抛出**，不许静默
```

⚠ **比值 ≠1 不是"自动修好"就完事**：它意味着上游有一处口径没对齐，
   `--strict` 下直接拒绝写盘（让调用方显式选换算还是不改），默认打印醒目提示。
"""
import argparse
import sys


class UnitMismatch(ValueError):
    """来源 bpm 与目标 bpm 不一致（= 写盘会静默缩放整条轨）"""


def scale_ratio(src_bpm, dst_bpm):
    """beat_src → beat_dst 的乘数（= dst_bpm / src_bpm 的倒数关系）。

    `beat × 60/bpm = 秒` ⇒ 同一段真实时间：`beat_src × 60/src = beat_dst × 60/dst`
    ⇒ `beat_dst = beat_src × dst/src`。
    """
    src = float(src_bpm or 120.0)
    dst = float(dst_bpm or 120.0)
    if src <= 0 or dst <= 0:
        raise ValueError('bpm 必须为正：src=%s dst=%s' % (src_bpm, dst_bpm))
    return dst / src


def convert_beats(beat, src_bpm, dst_bpm):
    """单个（或列表）beat 值从 src 口径换到 dst 口径"""
    r = scale_ratio(src_bpm, dst_bpm)
    if isinstance(beat, (list, tuple)):
        return [float(x) * r for x in beat]
    return float(beat) * r


def convert_note_rows(rows, src_bpm, dst_bpm, beat_idx=(0, 1)):
    """`[[小节, 拍内, 时值, 音高, 力度], ...]` → 换算成 dst 口径。

    ⚠ 小节号也带 bpm 口径（小节 = 4 拍），所以**先把"小节×4 + 拍内"当作绝对拍**再一起换算，
       最后拆回（小节, 拍内）。只换"拍内"不换"小节"是最容易犯的错。
    """
    r = scale_ratio(src_bpm, dst_bpm)
    out = []
    for row in rows:
        row = list(row)
        bar, off = float(row[0]), float(row[1])
        dur = float(row[2])
        ab = (bar * 4.0 + off) * r
        nb = int(ab // 4.0)
        row[0] = nb
        row[1] = round(ab - nb * 4.0, 6)
        row[2] = round(dur * r, 6)
        out.append(row)
    return out


def mismatch_msg(src_bpm, dst_bpm, what='轨'):
    r = scale_ratio(src_bpm, dst_bpm)
    return ('%s 的来源 bpm %.4g 与目标 bpm %.4g 不一致（比值 %.5f）—— '
            '直接写盘会让整条轨被缩放 %.1f%%：末音位置与曲长对不上、'
            '偏差随位置线性累积。要么显式换算（`convert_beats`），'
            '要么确认来源本来就是目标口径。' % (what, src_bpm, dst_bpm, r, 100 * abs(1 - r)))


def assert_same_bpm(src_bpm, dst_bpm, what='轨', tol=1e-3, strict=False, quiet=False):
    """比值≠1 时的统一处理：默认**打印醒目提示**；`strict=True` 抛 `UnitMismatch`。"""
    r = scale_ratio(src_bpm, dst_bpm)
    if abs(r - 1.0) <= tol:
        return r
    msg = mismatch_msg(src_bpm, dst_bpm, what)
    if strict:
        raise UnitMismatch(msg)
    if not quiet:
        print('  ! %s' % msg, file=sys.stderr)
    return r


def looks_like(cls, obj, min_items=4):
    """轻量类型判断（给"两种格式都吃"的调用方用）：list 且元素像 note row"""
    return (isinstance(obj, (list, tuple)) and len(obj) >= min_items
            and not isinstance(obj[0], (list, tuple, dict)))


def selftest():
    ok = True

    def chk(lab, got, want, tol=1e-9):
        nonlocal ok
        good = (abs(got - want) <= tol) if isinstance(want, float) else (got == want)
        print('  [%s] %-52s 期望 %-10s 实得 %-10s'
              % ('PASS' if good else 'FAIL', lab, want, got))
        ok = ok and good

    # ① BGM35 的实测现场：120 → 149.8 的**比值是 1.248333**（不是 0.801，方向别搞反）
    #    149.8BPM 的一拍更短 ⇒ 同样的音乐，beat 计数更大。
    #    而"秒数"看起来被压缩 0.801×，是 秒 = beat × 60/bpm 里 **bpm 变大**带来的。
    chk('比值 120→149.8 = 1.248333', scale_ratio(120, 149.8), 149.8 / 120, 1e-12)
    chk('其倒数 = 0.801068（= 秒数被压的倍数）', 1 / scale_ratio(120, 149.8), 120 / 149.8, 1e-12)
    # ② 同一段真实时间往返应回到原值
    b = convert_beats(659.28, 120, 149.8)
    chk('659.28 拍（120BPM）→ 149.8BPM = 823.00 拍', b, 659.28 * 149.8 / 120, 1e-6)
    chk('再换回去等于原值', convert_beats(b, 149.8, 120), 659.28, 1e-6)
    # ③ 秒不变式：beat×60/bpm 在换算前后应相同
    t1 = 659.28 * 60.0 / 120.0
    t2 = b * 60.0 / 149.8
    chk('秒不变式（两边都 329.64 秒）', t2, t1, 1e-6)
    # ④ note rows 换算：小节号必须按**绝对拍**重算（只换拍内是常见错法）
    rows = [[0, 0.8, 2.954, 60, 100]]
    c = convert_note_rows(rows, 120, 149.8)
    chk('rows：绝对拍 0.8 → 0.9987（拍内 0.9987）', c[0][1], 0.8 * 149.8 / 120, 1e-6)
    chk('rows：时值同比缩放', c[0][2], 2.954 * 149.8 / 120, 1e-6)
    chk('rows：音高/力度不动', (c[0][3], c[0][4]), (60, 100))
    rows2 = [[10, 3.0, 1.0, 60, 100]]            # 绝对拍 = 43
    ab = (10 * 4.0 + 3.0) * 149.8 / 120          # = 53.678
    c2 = convert_note_rows(rows2, 120, 149.8)
    chk('rows：小节号按绝对拍重算（43 → 53.678 拍 = 小节 13 + 1.678）',
        (c2[0][0], round(c2[0][1], 4)),
        (int(ab // 4.0), round(ab - int(ab // 4.0) * 4.0, 4)))
    # ⑤ 实测量级核对：BGM35 的 Bass 末音 659.28 拍 在 149.8BPM 下 = 264.06 秒（实测就是这个）
    chk('659.28 拍 @149.8BPM = 264.06 秒（实测值）', 659.28 * 60.0 / 149.8, 264.06, 0.01)
    # ⑤ 比值相同（容差内）不报
    import io
    import contextlib
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        assert_same_bpm(120.0, 120.0005, 'X')
    chk('容差内不报', err.getvalue(), '')
    # ⑥ 比值不同：默认打印、strict 抛
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        assert_same_bpm(120, 149.8, 'Bass')
    chk('比值不同会打印提示', '不一致' in err.getvalue(), True)
    try:
        assert_same_bpm(120, 149.8, 'Bass', strict=True)
        chk('strict 会抛 UnitMismatch', 'no-raise', 'UnitMismatch')
    except UnitMismatch:
        chk('strict 会抛 UnitMismatch', 'UnitMismatch', 'UnitMismatch')
    print('  selftest %s' % ('全部通过' if ok else '有失败'))
    return ok


def main():
    ap = argparse.ArgumentParser(description='拍值单位换算（写 song.json 前必过）')
    ap.add_argument('--src-bpm', type=float)
    ap.add_argument('--dst-bpm', type=float)
    ap.add_argument('--beat', type=float, help='要换算的 beat 值')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if a.src_bpm is None or a.dst_bpm is None or a.beat is None:
        ap.print_help()
        return 2
    print('比值 %.6f · %.6f 拍 → %.6f 拍'
          % (scale_ratio(a.src_bpm, a.dst_bpm), a.beat, convert_beats(a.beat, a.src_bpm, a.dst_bpm)))
    assert_same_bpm(a.src_bpm, a.dst_bpm, '示例')
    return 0


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                          # noqa: BLE001
        pass
    sys.exit(main())
