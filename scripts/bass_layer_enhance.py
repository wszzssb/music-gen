#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""bass_layer_enhance.py —— **低音层增强工序**：分轨 → 两来源转录 → 并集 + 八度校正 → 独立核对。

## 为什么（2026-10-02，BGM35 干净重提取上实测）

单来源（YMT3 整混音里的 Bass 轨）在**独立尺子**（demucs bass 分轨 + pyin）上是：

| 版本 | 一致率 | 差八度 | 漏检 |
|---|---|---|---|
| 单来源基线 | **35.4%** | 34.6% | 26.0% |
| 本工序（并集 + 八度校正） | **84.9%** | **1.4%** | **5.0%** |
| 参照：用户认可版同尺子校准值 | 53.2% | — | — |

**归因**（各改一个维度，互不重叠）：**并集 +37.2 点**（修漏检 26%→4.1%）·
**八度校正 +12.3 点**（修差八度 14.3%→1.4%）。

⚠ **同源自证防护**：八度校正是用 pyin 提的参考做的，所以**不能只用 pyin 量**
（`RESTORE-METHOD.md` §10 第 1 条）。本工具内置**谐波证据**核对 —— 直接问音频
"两个候选八度里哪一个真有能量"：实测 **80.6% 被独立支持 · 19.4% 是过修**。

## 三步（全部调已有工具，不重造轮子）

```text
① transcribe_ymt3.py <bass分轨.wav> --no-song      → 来源 A（GPU / .venv-ml）
② ~~bp_transcribe.py~~                             → 来源 B **已退役**（Basic Pitch 2026-10-06）
   ⇒ 本工具因此**停用**（它的前提是「两来源、票才独立」）；只保留 `band_amp` / `octave_check`
     两个原语给 `octave_judge.py` 复用。
③ bass_ensemble.py --source A --source B --octave-ref <bass分轨.wav> --thr 0.45
```

⚠ **`--thr` 必须 ≤ 0.45**：两来源的归一化支持率是**离散**的
（both=1.0 / 仅 A≈0.53 / 仅 B≈0.47）。实测 0.55 与 0.75 **结果完全相同**（只保留 both
→ 475 音、漏检 47%），那正是技能里已证伪的"高阈值合并 = 高精度低召回"。

## 用法

```bash
python scripts/bass_layer_enhance.py <整混音.mid> <bass分轨.wav> --out <输出.mid>
#   [--work <临时目录>] [--thr 0.45] [--no-check] [--dry]
python scripts/bass_layer_enhance.py --selftest        # 只跑契约自检（不跑模型）
```

输出：`<输出.mid>`（只替换 Bass 轨，其余轨逐字节不动）+ `<work>/report.json`
（三步的来源数、支持率、八度校正统计、谐波证据核对）。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

DEFAULT_THR = 0.45          # ⚠ 见上面那段：>0.45 会掉进"只保留 both"的坑
BASS_PITCH_LO, BASS_PITCH_HI = 24, 60


def _py(venv):
    """venv 名（相对仓库根）**或绝对路径** → 解释器路径。

    ⚠ bp-venv 在仓库**外**（`D:\\test\\bp-venv`）—— 只按"相对仓库根"拼会得到
    `FileNotFoundError: [WinError 2]`（本工具第一次跑就踩了）。`BP_PY` 环境变量优先。
    """
    if venv and os.path.isfile(venv):
        return venv
    base = venv if os.path.isabs(venv) else os.path.join(ROOT, venv)
    for sub in (('Scripts', 'python.exe'), ('bin', 'python')):
        p = os.path.join(base, *sub)
        if os.path.isfile(p):
            return p
    raise SystemExit('找不到解释器：%s（可用 BP_PY 指向 bp-venv 的 python.exe）' % base)


def md5(p):
    return hashlib.md5(open(p, 'rb').read()).hexdigest()


def run(cmd, log_path, tag):
    print('  → %s' % tag)
    with open(log_path, 'w', encoding='utf-8') as f:
        r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=ROOT)
    if r.returncode != 0:
        tail = open(log_path, encoding='utf-8', errors='replace').read()[-800:]
        raise SystemExit('✗ %s 失败（rc=%d）\n%s\n（完整日志 %s）' % (tag, r.returncode, tail, log_path))
    return log_path


# ── 独立核对：谐波证据（不依赖 pyin）────────────────────────────────
def band_amp(y, sr, t, pitch, win=0.20, nfft=16384):
    """`t` 起 `win` 秒内，`pitch` 的**基频**窄带幅度（**不含谐波**）。

    ⚠ 判八度**只能比基频**：第一版比的是"基频 + 前两次谐波之和"，结果在 110Hz 正弦上
    给音高 45 与 33 **几乎相同的读数**（549.3 vs 549.6）—— 因为低八度候选的 2 次谐波
    正好落在高八度候选的基频上，**两个候选互相污染**，判据因此偏向低八度。
    基频没有这个问题：`p+12` 的谐波是 `2f, 3f…`，**不含** `p` 的基频 `f`。
    """
    i0 = max(0, int(t * sr))
    i1 = min(len(y), i0 + int(win * sr))
    if i1 - i0 < 2048:
        return 0.0
    seg = y[i0:i1] * np.hanning(i1 - i0)
    sp = np.abs(np.fft.rfft(seg, n=nfft))
    fr = np.fft.rfftfreq(nfft, 1.0 / sr)
    f0 = 440.0 * 2 ** ((pitch - 69) / 12.0)
    if f0 > sr * 0.45:
        return 0.0
    m = (fr >= f0 * 0.97) & (fr <= f0 * 1.03)
    return float(sp[m].max()) if m.any() else 0.0


def octave_check(bass_wav, base_mid, out_mid, track='Bass'):
    """被移八度的音：**优化后的八度** vs 基线八度，谁在音频上更强（只比基频，见 `band_amp`）。"""
    import soundfile as sf
    import midi_file as mf
    y, sr = sf.read(bass_wav, dtype='float32', always_2d=True)
    y = y.mean(axis=1)

    def notes(path):
        m = mf.import_midi(path)
        spb = 60.0 / float(m.get('bpm') or 150.0)
        for tr in m['tracks']:
            if tr['name'] == track:
                return sorted((round(x[0] * spb, 3), int(x[2])) for x in tr['notes'])
        return []
    b, o = notes(base_mid), notes(out_mid)
    idx = {}
    for t, p in b:
        idx.setdefault(round(t, 1), []).append(p)
    win_new = win_old = 0
    for t, p in o:
        cand = []
        for k in (round(t, 1), round(t - 0.1, 1), round(t + 0.1, 1)):
            cand += idx.get(k, [])
        if not cand:
            continue
        p0 = min(cand, key=lambda q: abs(q - p))
        if p0 == p or abs(p - p0) not in (12, 24):
            continue
        e_new, e_old = band_amp(y, sr, t, p), band_amp(y, sr, t, p0)
        win_new += e_new > e_old
        win_old += e_old > e_new
    n = win_new + win_old
    return {'pairs': n, 'new_wins': win_new, 'old_wins': win_old,
            'new_ratio': round(win_new / n, 3) if n else None,
            'criterion': 'fundamental-only'}


def selftest(verbose=True):
    """契约自检（不跑模型）：**thr 默认不许回到"只保留 both"档**、谐波证据方向正确。"""
    assert DEFAULT_THR <= 0.45, \
        'DEFAULT_THR=%s 太大 —— 支持率是离散的（both=1.0/仅A≈0.53/仅B≈0.47），>0.45 只保留 both（实测漏检 47%%）' % DEFAULT_THR
    sr = 22050                      # ⚠ 别用 8k：本函数的窗（0.2s）会短于 2048 样本 → 恒返回 0（自检假 FAIL）
    t = np.arange(int(1.0 * sr)) / float(sr)
    a2 = (0.5 * np.sin(2 * np.pi * 110.0 * t)).astype('float32')      # A2 = 音高 45
    e_hi, e_lo = band_amp(a2, sr, 0.1, 45), band_amp(a2, sr, 0.1, 33)
    assert e_hi > e_lo * 3, \
        '八度判据方向错：A2 信号上音高 45 该远强于 33（%.1f vs %.1f）—— 比谐波和会被"低八度的 2 次谐波=高八度基频"污染' \
        % (e_hi, e_lo)
    noise = (0.5 * np.random.RandomState(0).randn(len(t))).astype('float32')
    assert band_amp(noise, sr, 0.1, 45) < e_hi, '白噪声不该比正弦的基频证据更强'
    assert band_amp(a2, sr, 0.1, 45, win=0.0) == 0.0, '窗太短该返回 0（不许崩）'
    if verbose:
        print('bass_layer_enhance 自检 PASS：thr=%.2f ≤ 0.45 · 八度判据**只比基频**（%.1f vs %.1f）· '
              '白噪声不误判 · 短窗安全' % (DEFAULT_THR, e_hi, e_lo))
    return True


def main():
    ap = argparse.ArgumentParser(description='低音层增强：两来源转录 + 并集 + 八度校正 + 独立核对')
    ap.add_argument('base', nargs='?', help='整混音 MIDI（保留其所有轨，只替换 Bass）')
    ap.add_argument('bass_wav', nargs='?', help='bass 分轨 wav（demucs 分出来的那条）')
    ap.add_argument('--out', default=None, help='输出 MIDI')
    ap.add_argument('--work', default=None, help='临时目录（默认 <out 同目录>/bass_enhance）')
    ap.add_argument('--thr', type=float, default=DEFAULT_THR)
    ap.add_argument('--no-check', action='store_true', help='跳过谐波证据核对（快跑）')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    # ⚠ 2026-10-06：Basic Pitch 退役 → 本工具的「两来源」前提不成立。
    #   **明确早退**，不静默退化成单来源（单来源时「支持率」判据毫无意义）。
    if not os.path.isfile(os.path.join(HERE, 'bp_transcribe.py')):
        raise SystemExit(
            'Basic Pitch 已于 2026-10-06 退役（bp_transcribe.py 已删除、bp-venv 已归档到 '
            'D:\\test\\_deprecated_restore\\bp-venv）。本工具的核心是「两来源交叉验证」，'
            '单来源时「支持率」判据失效 ⇒ 停用。要用两来源请改用 bass_ensemble.py 自行传两个 --source；'
            '`band_amp` / `octave_check` 仍可被 octave_judge.py 复用。')
    if not (a.base and a.bass_wav and a.out):
        ap.print_help()
        return 1
    if a.thr > 0.45:
        raise SystemExit('⚠ --thr %.2f > 0.45：会掉进"只保留 both"的坑（实测漏检 47%%）。'
                         '确实要试就改 DEFAULT_THR 并重跑自检。' % a.thr)
    for p in (a.base, a.bass_wav):
        if not os.path.isfile(p):
            raise SystemExit('找不到 %s' % p)
    work = a.work or os.path.join(os.path.dirname(os.path.abspath(a.out)), 'bass_enhance')
    os.makedirs(work, exist_ok=True)
    print('基线 %s（md5 %s）' % (a.base, md5(a.base)[:12]))
    if a.dry:
        print('（--dry：只打印计划）\n ① YMT3 分轨转录 ② BP 分轨转录 ③ 并集+八度校正 ④ 谐波核对')
        return 0

    src_a = os.path.join(work, 'src_ymt3.mid')
    src_b = os.path.join(work, 'src_bp.mid')
    run([_py('.venv-ml'), os.path.join(HERE, 'transcribe_ymt3.py'), a.bass_wav,
         '-o', work, '--no-song', '--name', 'bass_src'],
        os.path.join(work, 'log_ymt3.txt'), '① YMT3 对 bass 分轨转录')
    if not os.path.isfile(src_a):
        cand = [os.path.join(work, f) for f in os.listdir(work) if f.endswith('.mid')]
        if not cand:
            raise SystemExit('① 没有产出 MIDI（看 log_ymt3.txt）')
        src_a = cand[0]
    run([_py(os.environ.get('BP_PY') or r'D:\test\bp-venv'),
         os.path.join(HERE, 'bp_transcribe.py'), a.bass_wav, src_b],
        os.path.join(work, 'log_bp.txt'), '② BP 对 bass 分轨转录')
    run([_py('.venv-ml'), os.path.join(HERE, 'bass_ensemble.py'),
         '--base', a.base, '--out', a.out,
         '--source', 'ymt3b=%s|%d|%d' % (src_a, BASS_PITCH_LO, BASS_PITCH_HI),
         '--source', 'bp=%s|%d|%d' % (src_b, BASS_PITCH_LO, BASS_PITCH_HI),
         '--layer', 'Bass', '--octave-ref', a.bass_wav, '--thr', str(a.thr)],
        os.path.join(work, 'log_ensemble.txt'), '③ 并集 + 八度校正')
    if not os.path.isfile(a.out):
        raise SystemExit('③ 没有产出 %s（看 log_ensemble.txt）' % a.out)

    rep = {'base': a.base, 'bass_wav': a.bass_wav, 'out': a.out, 'thr': a.thr,
           'out_md5': md5(a.out)}
    if not a.no_check:
        rep['octave_check'] = octave_check(a.bass_wav, a.base, a.out)
        print(' ④ 谐波证据核对：%s' % rep['octave_check'])
    json.dump(rep, open(os.path.join(work, 'report.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('✓ 写出 %s\n  报告 %s' % (a.out, os.path.join(work, 'report.json')))
    return 0


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                          # noqa: BLE001
        pass
    sys.exit(main())
