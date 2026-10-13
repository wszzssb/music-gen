#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""drumless_mix.py —— 把 Demucs 六轨里**去掉鼓**混成一条可分轨转录的输入，并纠正"半速记谱"。

## 什么时候用

面板提取的 **`partial`（部分分轨）** 档：只有一部分轨（默认 `Hook,Pad`）改从**去鼓**
输入转录 —— 依据是 `D:\test\BGM23_装配提取_交付\notes.md` §5.10 的逐轨独立尺子表。
本工具负责那条链上**唯一新造的两步**，其余全是仓库现有入口：

| 步 | 谁做 |
|---|---|
| ① 去鼓混音 | **本工具**（`mix`） |
| ② 转录去鼓输入 | `transcribe_ymt3.py`（`--no-song`，出逐轨 `*_<轨>.mid`） |
| ③ 纠正半速 BPM | **本工具**（`patch-bpm`） |
| ④ 混合装配 | `transcribe_to_song.py --mid 轨=文件`（**已支持**） |

## 为什么"纠正半速"是必需的一步（不是保险）

实测（`notes.md` §5.2 第 3 条）：**去鼓输入会让 `--auto` 的节拍判定翻倍**
（BGM23：纯单来源 **71.0 BPM / 79 小节** → 去鼓版 **141.6 BPM / 158 小节**）。
而 `transcribe_to_song.py` **按曲目自己的 `bpm` 换算小节/段落/时间轴**，所以记谱速度一错：
段落全错位、逐音时间轴错一倍 —— 上一轮就是这样得出过"独立基音仅 6.1%"的**假读数**
（真值 19.9%，`notes.md` §5.10 开头）。

## 判据（不猜）

`ratio = 本版 bpm ÷ 参考 bpm`（参考 = **纯单来源那版**的 bpm，它是用户认可的口径）：

* `> 1.8`  ⇒ 判**半速记谱**，除以 2（实测比值 1.994）；
* 其余      ⇒ 原样不动（**不硬编码整曲速度** —— 硬编码正是上一轮的坑，见交接 §4.2）；
* 参考缺失或 `> 0.8` 的**慢速对**另有分支：`ratio < 0.55` 判**倍速**（×2），同样只在极端比值上动。

⚠ 这是**启发式**，不是"普遍规律"：它只在 `ratio` 落到两端时动，中间一律不动；
真依据是"去鼓输入的 novelty 会让 `--auto` 把一拍当两拍"，而**不是**"所有曲子的去鼓版都翻倍"。

## 用法

```bash
$py = <仓库>\.venv\Scripts\python.exe

# ① 去鼓混音（默认丢 drums；输出单声道 FLOAT wav，峰值保护 0.95）
& $py scripts\drumless_mix.py mix --stems <六轨目录> -o <工作目录>\nodrums.wav
& $py scripts\drumless_mix.py mix --stems <六轨目录> -o out.wav --drop drums,bass   # 再去贝斯

# ② 转录（现有入口，出逐轨 mid）
& $ml scripts\transcribe_ymt3.py <nodrums.wav> -o <目录> --name nodrums --no-song

# ③ 纠正半速（就地改，先记 SHA256 + 备份）
& $py scripts\drumless_mix.py patch-bpm --song <曲目目录>\song.json --ref-bpm 71.0

# ④ 装配（现有入口）
& $py scripts\transcribe_to_song.py <曲名> --bpm 71.0 --chords-log ... --no-melody --auto \
      --audio <原曲> --stems-dir <六轨目录> --mid "Hook=<目录>\nodrums_Hook.mid" ...

& $py scripts\drumless_mix.py --selftest
```

退出码：`0` = 正常 · `1` = 用法/输入错 · `2` = `mix` 之后没有可用轨（没配到 wav）。
"""
import argparse
import hashlib
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cli_utf8 as _cu                                          # noqa: E402
_cu.setup()
import json_io                                                  # noqa: E402

#: 默认丢掉的分轨（"去鼓"）。
#: ⚠ `vocals` 与 `piano` 在 Demucs 6s 下**近乎空支路**（实测能量占比 0.8% / 0.6%，
#: `notes.md` §5.9）—— 留着不影响能量、也不影响时间密度，所以默认一起丢；
#: 但**不影响结论**：真正起作用的是去掉 `drums`（占 49.6%）。
DEFAULT_DROP = ('drums',)
#: 判"半速记谱"的比值门（实测去鼓版/纯单来源 = 1.994，取 1.8 留余量）
HALF_RATIO = 1.8
#: 判"倍速记谱"的比值门
DOUBLE_RATIO = 0.55
#: 峰值保护（超过就整体缩，不做削波）
PEAK = 0.95


def sha256(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def fix_bpm(bpm, ref_bpm):
    """把"去鼓版测出来的 bpm"折回参考口径 → `(new_bpm | None, 说明)`。

    `None` = **不动**（比值落在正常区间，或没有参考）。判据只认**极端比值**，
    中间一律不动 —— 这样它不会去"顺手修"一个本来就对的速度。
    """
    try:
        bpm = float(bpm)
    except (TypeError, ValueError):
        return None, 'bpm 读不出来（%r）—— 不动' % (bpm,)
    if not ref_bpm or ref_bpm <= 0 or bpm <= 0:
        return None, '没有参考 bpm（或非正数）—— 不动'
    ratio = bpm / float(ref_bpm)
    if ratio > HALF_RATIO:
        return round(bpm / 2.0, 4), ('半速记谱：%.4f / %.4f = %.3f > %.1f ⇒ ÷2'
                                     % (bpm, ref_bpm, ratio, HALF_RATIO))
    if ratio < DOUBLE_RATIO:
        return round(bpm * 2.0, 4), ('倍速记谱：%.4f / %.4f = %.3f < %.2f ⇒ ×2'
                                     % (bpm, ref_bpm, ratio, DOUBLE_RATIO))
    return None, ('比值 %.3f 落在 [%.2f, %.1f] 内 ⇒ **不动**'
                  % (ratio, DOUBLE_RATIO, HALF_RATIO))


def patch_bpm(song_json, ref_bpm, apply=False):
    """读 `song.json` 的 `bpm` 与参考对账 → `(old, new | None, why)`；`apply` 时就地写。

    写盘走 `json_io.save`（规范格式 + LF）—— 直接 `json.dump(open(p,'w'))` 在 Windows
    上会把 `\n` 翻成 CRLF，`song_json_canonical` 当场拦（坑 341）。
    就地改之前记 SHA256 并备份 `.pre_bpmfix.bak`（与 `perc_clean.py` 同一套纪律）。
    """
    d = json_io.load(song_json)
    old = d.get('bpm')
    new, why = fix_bpm(old, ref_bpm)
    if new is None or abs(float(new) - float(old)) < 1e-9:
        return old, None, why
    if apply:
        bak = song_json + '.pre_bpmfix.bak'
        if not os.path.exists(bak):
            shutil.copy2(song_json, bak)
        d['bpm'] = new
        json_io.save(song_json, d)
    return old, new, why


def mix_stems(stems_dir, out, drop=DEFAULT_DROP, peak=PEAK):
    """把 `stems_dir` 里除 `drop` 之外的 wav 求和成**单声道** → `out`；返回 `(n, sr, used)`。

    ⚠ **单声道**：上一轮的去鼓消融就是这么做的（`13_nodrums.sh`：各轨 `mean(axis=1)`
    再相加）—— 转录用的是同一份预处理，换成立体声就不是"复现那条链"了。
    """
    import numpy as np
    import soundfile as sf
    drop = set(x.strip().lower() for x in drop if x.strip())
    names = sorted(f for f in os.listdir(stems_dir) if f.lower().endswith('.wav'))
    parts, sr, used = [], None, []
    for f in names:
        stem = os.path.splitext(f)[0].lower()
        base = stem.split('_')[-1] if '_' in stem else stem       # `h6_piano.wav` 也认
        if base in drop:
            continue
        y, sr = sf.read(os.path.join(stems_dir, f), dtype='float32', always_2d=True)
        parts.append(y.mean(axis=1))
        used.append(base)
    if not parts:
        raise SystemExit('没有可用轨（drop=%s，目录里 %d 个 wav）—— 检查 --stems/--drop'
                         % (','.join(sorted(drop)), len(names)))
    n = min(len(p) for p in parts)          # 取**最短**（对齐到公共长度，不补零造出静音尾巴）
    m = np.zeros(n, dtype=np.float64)
    for p in parts:
        m += p[:n]
    raw = float(np.max(np.abs(m))) if n else 0.0
    if raw > peak:
        m = m * (peak / raw)
    d = os.path.dirname(os.path.abspath(out))
    if d:
        os.makedirs(d, exist_ok=True)
    sf.write(out, m.astype(np.float32), sr, subtype='FLOAT')
    return n, sr, used


def selftest():
    """判据自证（合成件，不依赖曲库/不依赖 demucs）。

    五条断言：
      ① `mix` 真的**丢掉指定分轨**（丢鼓后输出里没有鼓的拍点）
      ② 输出是**单声道** 且长度 = 公共最短长度（不补零）
      ③ 峰值保护生效（叠加到超过 0.95 时整体缩、不削波）
      ④ `fix_bpm` 三态：半速 ÷2 · 倍速 ×2 · **中间一律不动**（不许硬编码整曲速度）
      ⑤ `patch_bpm` 就地改：bpm 变了、**音符一个不动**、格式仍规范、幂等
    """
    import tempfile
    import numpy as np
    import soundfile as sf
    tmp = tempfile.mkdtemp(prefix='drumless_')
    sr = 16000
    t = np.arange(sr, dtype=np.float32) / sr
    st = os.path.join(tmp, 'stems')
    os.makedirs(st)
    tone = (0.30 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    # 鼓 = 50ms 一次的脉冲串（能量集中在拍点上）
    drum = np.zeros_like(tone)
    for i in range(0, sr, sr // 20):
        drum[i:i + 80] = 0.9
    sf.write(os.path.join(st, 'piano.wav'), tone, sr, subtype='FLOAT')
    sf.write(os.path.join(st, 'drums.wav'), drum, sr, subtype='FLOAT')
    sf.write(os.path.join(st, 'bass.wav'), (0.6 * tone).astype(np.float32), sr, subtype='FLOAT')

    out = os.path.join(tmp, 'nodrums.wav')
    n, sr2, used = mix_stems(st, out, drop=('drums',))
    y, _ = sf.read(out, dtype='float32', always_2d=True)
    assert 'drums' not in used and set(used) == {'piano', 'bass'}, '① 没按 --drop 丢轨：%s' % used
    # ② 单声道 + 长度 = 最短（这里三轨等长，验的是"不补零"这条口径）
    assert y.shape[1] == 1, '② 输出该是单声道，实得 %d 通道' % y.shape[1]
    assert len(y) == n, '② 长度该等于公共最短 %d，实得 %d' % (n, len(y))
    # ① 的实质验证：脉冲串不在输出里（用"相邻样本最大跳变"当鼓的证据）
    jump = float(np.max(np.abs(np.diff(y[:, 0]))))
    jump_tone = float(np.max(np.abs(np.diff(tone))))
    assert jump < 5 * jump_tone, \
        '① 输出里还留着鼓的瞬变（跳变 %.4f vs 纯音的 %.4f）' % (jump, jump_tone)
    # ③ 峰值保护：把两轨都放大到 1.0 再混 → 必须被缩到 peak 以内
    sf.write(os.path.join(st, 'loud1.wav'), np.ones(sr, dtype=np.float32) * 0.8, sr, subtype='FLOAT')
    sf.write(os.path.join(st, 'loud2.wav'), np.ones(sr, dtype=np.float32) * 0.8, sr, subtype='FLOAT')
    out2 = os.path.join(tmp, 'loud.wav')
    mix_stems(st, out2, drop=('drums', 'piano', 'bass'))
    y2, _ = sf.read(out2, dtype='float32')
    assert float(np.max(np.abs(y2))) <= PEAK + 1e-6, \
        '③ 峰值保护失效：%.4f > %.2f' % (float(np.max(np.abs(y2))), PEAK)
    assert float(np.max(np.abs(y2))) > PEAK - 0.05, '③ 缩过头了（该正好贴到 %.2f）' % PEAK

    # ④ fix_bpm 三态
    _n, _w = fix_bpm(141.6, 71.0)
    assert _n is not None and abs(_n - 70.8) < 1e-6, '④ 半速没折半：%s（%s）' % (_n, _w)
    _n2, _w2 = fix_bpm(35.5, 71.0)
    assert _n2 is not None and abs(_n2 - 71.0) < 1e-6, '④ 倍速没翻倍：%s（%s）' % (_n2, _w2)
    _n3, _w3 = fix_bpm(71.4, 71.0)
    assert _n3 is None, '④ 比值 1.006 竟被判成要改（%s ⇒ %s）—— 硬编码速度回来了' % (_n3, _w3)
    _n4, _w4 = fix_bpm(141.6, None)
    assert _n4 is None, '④ 没有参考 bpm 时不该动：%s' % _n4

    # ⑤ patch_bpm：改 bpm、不动音符、格式规范、幂等
    sp = os.path.join(tmp, 'song.json')
    notes = [[0, 0.0, 1.0, 60, 100], [1, 2.0, 1.0, 62, 90]]
    json_io.save(sp, dict(name='t', bpm=141.6, notes_extra=dict(Piano=notes)))
    old, new, why = patch_bpm(sp, 71.0, apply=True)
    assert (old, new) == (141.6, 70.8), '⑤ bpm 该 141.6 → 70.8，实得 %s → %s' % (old, new)
    raw = open(sp, 'rb').read()
    assert b'\r' not in raw, '⑤ patch 后出现 CRLF（该走 json_io.save）'
    d2 = json_io.load(sp)
    assert d2['notes_extra']['Piano'] == notes, '⑤ 音符被动过了：%s' % d2['notes_extra']['Piano']
    assert json_io.dumps(d2) == raw.decode('utf-8'), '⑤ 不是规范格式（json_io.dumps 对不上）'
    assert os.path.exists(sp + '.pre_bpmfix.bak'), '⑤ 就地改没留备份'
    _o2, _n2, _w = patch_bpm(sp, 71.0, apply=True)
    assert _n2 is None, '⑤ 不幂等：第二次又改了（%s → %s）' % (_o2, _n2)
    shutil.rmtree(tmp, ignore_errors=True)
    print('drumless_mix 自检：① 丢鼓（瞬变消失）· ② 单声道/不补零 · ③ 峰值保护 · '
          '④ fix_bpm 半速/倍速/中间不动/无参考不动 · ⑤ patch 改值+音符不动+规范+备份+幂等 '
          '—— 5/5 PASS')
    return True


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('cmd', nargs='?', choices=('mix', 'patch-bpm'))
    ap.add_argument('--stems', help='Demucs 六轨目录（含 drums.wav 等）')
    ap.add_argument('-o', '--out', help='mix：输出 wav')
    ap.add_argument('--drop', default=','.join(DEFAULT_DROP),
                    help='要丢掉的分轨（逗号分隔，默认 %s）' % ','.join(DEFAULT_DROP))
    ap.add_argument('--song', help='patch-bpm：song.json')
    ap.add_argument('--ref-bpm', type=float, default=None,
                    help='patch-bpm：参考 bpm（**纯单来源那版**的 bpm）')
    ap.add_argument('--apply', action='store_true', help='patch-bpm：真写盘（默认只报）')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if a.cmd == 'mix':
        if not (a.stems and a.out):
            print(__doc__)
            return 1
        if not os.path.isdir(a.stems):
            print('找不到分轨目录 %s' % a.stems)
            return 1
        n, sr, used = mix_stems(a.stems, a.out, drop=tuple(a.drop.split(',')))
        print('去鼓混音：用 %d 轨（%s）· %.2fs @%dHz · 单声道 → %s'
              % (len(used), '+'.join(used), n / float(sr or 1), sr, a.out))
        print('丢掉了：%s' % (a.drop or '(无)'))
        print('下一步：`transcribe_ymt3.py %s -o <目录> --name <名> --no-song`' % a.out)
        return 0
    if a.cmd == 'patch-bpm':
        if not (a.song and os.path.isfile(a.song)):
            print('找不到 song.json：%s' % a.song)
            return 1
        print('改前 SHA256 %s' % sha256(a.song))
        old, new, why = patch_bpm(a.song, a.ref_bpm, apply=a.apply)
        print('  bpm %.4f → %s' % (float(old), ('%.4f' % new) if new else '(不动)'))
        print('  依据：%s' % why)
        if new is None:
            return 0
        if not a.apply:
            print('\n（**只报不改** —— 要真写盘加 `--apply`；就地改会先备份 `.pre_bpmfix.bak`）')
            return 0
        print('已写 %s' % a.song)
        print('改后 SHA256 %s' % sha256(a.song))
        print('⚠ 改完**必须重跑装配**（`transcribe_to_song.py` 按 bpm 换算小节/时间轴）')
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main())
