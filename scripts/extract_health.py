#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""extract_health.py —— **提取出来的 MIDI 到底有多少音在音频里站得住**（无真值也能量）

## 什么时候用

扒带 / 还原 / 音频转 MIDI 的**交付前体检**：手上有一份 MIDI（模型给的、或自己拼的）和它的源音频，
但要回答"**这份 MIDI 里哪些音是音频里真有的、哪些是模型编的、哪些被别的音盖住量不出来**"。
没有真值（没有"标准答案 MIDI"）时，这三问就是能问的全部。

## 怎么判（不看任何标签，只量音频）

对 MIDI 里每个音，在它自己的时窗内量三件事：

| 读数 | 定义 | 怎么读 |
|---|---|---|
| `fund_db` | 该音**基频带**（±0.4 半音）的能量相对该窗总能量 | 太低 = 音频里根本没有这个音高 |
| `purity` | 基频带能量 ÷ 该音**谐波**（1..6 次）能量 | 低 = 这个音高上的能量主要不在它的谐波上（是别人的泛音/噪声） |
| `margin_db` | 该音起音处相对**音前 150ms 本底**的抬升 | <6dB = 被同时发声的音盖住，**量不出**（不许猜） |

判定三态（与 `tools/preflight.py` 同口径：**测不到就报 UNKNOWN，不静默降级**）：
  · `ok`        —— 基频带能量达标（音频里真有这个音）
  · `masked`    —— 有能量但抬升 <6dB：被盖住，**这一档不许当结论**
  · `no-support`—— 基频带几乎没能量：音频里没有它（模型编的 / 音高错了八度）

## 用法

```powershell
$py = "<工具链>\.venv-ml\Scripts\python.exe"     # 需要 librosa/soundfile
& $py scripts\extract_health.py <音频> <MIDI> [--tol 0.08] [--budget 60] [--json 报告.json]
& $py scripts\extract_health.py <音频> --selftest           # 合成音频自检（不需要 MIDI）
```

输出：可量率 / 三态分布 / **逐段表**（用户 2026-09-25 口径：**先看要不要分段**）/
**复核预算**（按可疑度只列最该人听的 N 个音）。
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SR = 22050
NFFT = 512          # 频率分辨率 43Hz/格：与 `attr_scan` 里验证过的参数一致
HOP = 64            # ≈2.9ms/帧（时间分辨率由 hop 给，不由窗长）
FMIN, NBINS = 27.5, 120
ATT_MAX_MS = 30.0


def db(x):
    import numpy as np
    return 20.0 * np.log10(np.maximum(x, 1e-10))


class Grid:
    """一次 STFT，按音高取基频带 / 谐波带包络（与 `attr_scan.STFTGrid` 同构，去掉 CQT）。"""

    def __init__(self, y, sr=SR, n_fft=NFFT, hop=HOP):
        import librosa
        self.S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop, window='hann'))
        self.fr = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        self.sr = sr
        self.hop_ms = hop / sr * 1000.0
        nfr = self.S.shape[1]
        self.band_energy = np.zeros(nfr)
        for a in range(0, nfr, 4096):
            b = min(nfr, a + 4096)
            self.band_energy[a:b] = (self.S[:, a:b] ** 2).sum(axis=0)
        self.broad = np.sqrt(self.band_energy)
        self._m = {}

    def masks(self, pitch, n_harm=6, half_semi=0.4):
        key = (pitch, n_harm, half_semi)
        if key in self._m:
            return self._m[key]
        f0 = 440.0 * 2.0 ** ((pitch - 69) / 12.0)
        ms = []
        for h in range(1, n_harm + 1):
            f = f0 * h
            if f > self.sr * 0.45:
                break
            lo, hi = f * 2 ** (-half_semi / 12), f * 2 ** (half_semi / 12)
            m = np.where((self.fr >= lo) & (self.fr <= hi))[0]
            if m.size == 0:                     # 低频：带内可能一个频格都没有 → 取最近格
                m = np.array([int(np.argmin(np.abs(self.fr - f)))])
            ms.append(m)
        out = (ms[0], ms)
        self._m[key] = out
        return out

    def band_pow(self, pitch, a, b, half_semi=0.4):
        """[a,b) 帧内、该音高 ±half_semi 半音带的能量。"""
        fm, _ = self.masks(pitch, n_harm=1, half_semi=half_semi)
        return float((self.S[fm, a:b] ** 2).sum())

    def between_masks(self, pitch, n_harm=6, half_semi=0.4):
        """**谐波之间**的地带频格（0.6f0~0.9f0、1.6f0~1.9f0、…）—— 判"是不是成谐波结构的音"。"""
        key = ('btw', pitch, n_harm, half_semi)
        if key in self._m:
            return self._m[key]
        f0 = 440.0 * 2.0 ** ((pitch - 69) / 12.0)
        ms = []
        for h in range(1, n_harm):
            lo, hi = f0 * (h + 0.6), f0 * (h + 0.9)
            if lo > self.sr * 0.45:
                break
            mm = np.where((self.fr >= lo) & (self.fr <= hi))[0]
            if mm.size:
                ms.append(mm)
        self._m[key] = ms
        return ms

    def envs(self, pitch, n_harm=6):
        """→ (基频带幅度包络, 谐波和幅度包络)"""
        import numpy as np
        fm, ms = self.masks(pitch, n_harm)
        e_f = self.S[fm, :].max(axis=0)
        e_h = np.zeros(self.S.shape[1])
        for m in ms:
            e_h += self.S[m, :].max(axis=0)
        return e_f, e_h


def measure_one(g, pitch, t0, dur):
    """量一个音 → dict（level_db / harm_ratio_db / purity / margin_db / state / why）

    ⚠ 三态判据的**三次修正**（都是自检抓出来的，别退回旧版）：
      ① 只卡 `fund_db`（该带能量占比）→ **静音**会因除零得 0.0 被判成 `masked`；
      ② 加"绝对电平门"后仍漏：C4 的曲子里量 B5 得 level=+3.7dBFS、纯度 0.998
         —— 带里落进了 A5 高次谐波的裙边。**光看绝对电平分不开"真峰"与"裙边"**；
      ③ 试过"与 ±3/5 半音邻居比"（突出度）→ **当场作废**：位移太小、带与带严重重叠，
         "邻居带"其实就是自己的裙边 → 99% 的真音被判 no-support（已实测证伪）；
      ✓ 现在用 **谐波间能量比**：把"基频 + 2..6 次谐波"这些带里的能量，与**它们之间的
        地带**（0.6f0~0.9f0、1.6f0~1.9f0…）比。真音的能量按谐波结构分布；别的音的裙边、
        宽带噪声则不挑位置 → 这个比值才分得开"这里真有个音"和"这里只是有能量"。
    """
    a = max(0, int((t0 - 0.05) * g.sr / HOP))
    b = max(a + 4, int((t0 + max(dur, 0.30)) * g.sr / HOP))
    b = min(b, g.S.shape[1] - 1)
    if b - a < 4:
        return {'state': 'unknown', 'why': '窗太短', 'level_db': -200.0,
                'harm_ratio_db': -200.0, 'purity': 0.0, 'margin_db': 0.0}
    fm, ms = g.masks(pitch)
    e_f = g.S[fm, :].max(axis=0)
    peak = float(e_f[a:b].max())
    level_db = 20.0 * np.log10(max(peak, 1e-12)) - 6.0     # 近似 dBFS，只作门限
    fund = float((g.S[fm, a:b] ** 2).sum())
    harm = float(sum((g.S[m, a:b] ** 2).sum() for m in ms))
    tot = float(g.band_energy[a:b].sum())
    fund_db = 10.0 * np.log10(max(fund, 1e-18) / max(tot, 1e-18))
    purity = float(min(1.0, fund / max(harm, 1e-18)))
    between = float(sum((g.S[m, a:b] ** 2).sum() for m in g.between_masks(pitch, 6)))
    harm_ratio_db = 10.0 * np.log10(max(harm, 1e-18) / max(between, 1e-18))
    ed = db(np.convolve(e_f, np.ones(3) / 3, mode='same'))
    i0 = int(t0 * 1000.0 / g.hop_ms)
    p0 = max(0, i0 - int(round(150.0 / g.hop_ms)))
    base = float(np.median(ed[p0:i0])) if i0 - p0 >= 3 else float(np.percentile(ed[a:b], 5))
    pk = float(ed[max(0, i0 - 6):min(ed.size, i0 + int(round(150.0 / g.hop_ms)))].max())
    margin = pk - base
    if level_db < -80.0:
        state, why = 'no-support', '该带几乎没有能量（%.0fdBFS，音频里没有这个音）' % level_db
    elif harm_ratio_db < 3.0:
        # ⚠ 这一档对**打击乐**天然不利（鼓不成谐波结构）→ 报数时要按轨分开看，
        #   别把"鼓被判 unsupported"当成缺陷（见输出里的"按轨"表）。
        state, why = 'weak', ('谐波带只比谐波间地带高 %.1fdB（成谐波结构的证据弱；'
                              '可能是打击乐或与被盖住）' % harm_ratio_db)
    elif margin < 6.0:
        state, why = 'masked', '起音抬升仅 %.1fdB（被同时发声的音盖住）' % margin
    else:
        state, why = 'ok', ''
    return {'state': state, 'why': why, 'level_db': round(level_db, 2),
            'fund_db': round(fund_db, 2), 'purity': round(purity, 4),
            'harm_ratio_db': round(harm_ratio_db, 2), 'margin_db': round(margin, 2)}


def synth(kind, pitch=60, sr=SR, dur=1.2):
    """合成已知答案：piano | violin | silence（与 `attr_scan` 同一套，便于互证）。"""
    import numpy as np
    n = int(dur * sr)
    t = np.arange(n) / sr
    if kind == 'silence':
        return np.zeros(n)
    f0 = 440.0 * 2.0 ** ((pitch - 69) / 12.0)
    att = 0.004 if kind == 'piano' else 0.250
    nh, roll = (12, 0.55) if kind == 'piano' else (16, 0.18)
    env = np.ones(n)
    k = max(1, int(att * sr))
    env[:k] = (np.linspace(0, 1, k) if kind == 'piano'
               else 0.5 - 0.5 * np.cos(np.pi * np.arange(k) / max(1, k - 1)))
    env *= (0.72 * np.exp(-t / 0.090) + 0.28 * np.exp(-t / 0.900)) if kind == 'piano' \
        else (1 + 0.35 * np.sin(2 * np.pi * 5.5 * t))
    y = np.zeros(n)
    for h in range(1, nh + 1):
        if f0 * h > sr / 2.2:
            break
        dec = np.exp(-t / max(0.08, 1.6 / h)) if kind == 'piano' else 1.0
        y += (roll ** (h - 1)) * dec * np.sin(2 * np.pi * f0 * h * t + 0.3 * h)
    y *= env
    y /= max(1e-9, np.abs(y).max())
    return y * 0.5


def selftest():
    """合成音频自检：自证这把尺子**坏得起来**（不依赖任何素材）。"""
    ok = True
    PAD = 0.3
    print('=== extract_health 自检（合成音频，%.1fms/帧）===' % (HOP / SR * 1000))
    cases = [('piano', 'ok'), ('violin', 'ok'), ('silence', 'no-support')]
    for kind, want in cases:
        y = np.concatenate([np.zeros(int(PAD * SR)), synth(kind, 60)])
        g = Grid(y)
        r = measure_one(g, 60, PAD + 0.02, 0.9)
        good = (r['state'] == want)
        ok = ok and good
        print('  %-14s → %-11s (期望 %-11s) %s  level=%s fund=%s purity=%s margin=%s'
              % (kind, r['state'], want, 'OK ' if good else 'FAIL',
                 r.get('level_db'), r.get('fund_db'), r.get('purity'), r.get('margin_db')))
    # 反向：MIDI 里有个音、音频里**没有**它 → 必须**不判 ok**（判 weak 或 no-support 都算对：
    #   它确实"带里有能量"（邻近谐波的裙边），判据的职责只是**不许把它当好音**）
    # ⚠ **不测"半音邻居"**（如 C4 曲子里量 C#4）：两个音的频带在 43Hz/格的解析度下
    #   基本重叠，**物理上分不开** —— 写进自检只会变成一条永远 FAIL 的假要求。
    y = np.concatenate([np.zeros(int(PAD * SR)), synth('piano', 60)])
    g = Grid(y)
    r = measure_one(g, 83, PAD + 0.02, 0.9)
    good = (r['state'] != 'ok')
    ok = ok and good
    print('  %-14s → %-11s (期望 非ok      ) %s  level=%s fund=%s harm比=%s'
          % ('八度外音高(B5)', r['state'], 'OK ' if good else 'FAIL',
             r.get('level_db'), r.get('fund_db'), r.get('harm_ratio_db')))
    print('自检结论：', 'PASS' if ok else '**FAIL**')
    return ok


def load_notes(mid_path):
    """用项目自己的 `midi_file` 读（**拍速口径**：`bpm`/`mpqn` 都认，见 `t_import_midi_tempo_exposed`）。"""
    import midi_file as mf
    d = mf.import_midi(mid_path)
    bpm = float(d.get('bpm') or (60e6 / (d.get('mpqn') or 500000)))
    spb = 60.0 / bpm
    out = []
    for tr in d.get('tracks') or []:
        for nt in tr.get('notes') or []:
            out.append({'track': tr.get('name') or '?', 'pitch': int(nt[2]),
                        't0': float(nt[0]) * spb, 'dur': max(0.05, float(nt[1]) * spb)})
    return sorted(out, key=lambda n: n['t0']), bpm


def review_score(r):
    s = 0.0
    if r['state'] == 'no-support':
        s += 1.2
    if r['state'] == 'masked':
        s += 0.7
    if r['state'] == 'weak':
        s += 0.5
    p = r.get('purity')
    if p is not None:
        s += min(0.4, max(0.0, (0.5 - p)) * 0.8)
    return s


def main():
    ap = argparse.ArgumentParser(description='提取 MIDI 的逐音体检（无真值）')
    ap.add_argument('audio', nargs='?', help='源音频')
    ap.add_argument('mid', nargs='?', help='要体检的 MIDI')
    ap.add_argument('--selftest', action='store_true', help='合成音频自检')
    ap.add_argument('--budget', type=int, default=60, help='列出最该复核的 N 个音')
    ap.add_argument('--segment', type=float, default=30.0, help='逐段表的段长（秒）')
    ap.add_argument('--json', default=None, help='把逐音读数写成 JSON')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 2
    if not (a.audio and a.mid):
        ap.error('要给 <音频> <MIDI>，或 --selftest')

    import numpy as np
    import soundfile as sf
    y, sr0 = sf.read(a.audio, always_2d=True)
    y = y.mean(axis=1).astype(np.float64)
    if sr0 != SR:
        import librosa
        y = librosa.resample(y, orig_sr=sr0, target_sr=SR)
    dur_audio = len(y) / SR
    notes, bpm = load_notes(a.mid)
    print('音频 %.1fs · MIDI %d 音 · 声明 %.1f BPM' % (dur_audio, len(notes), bpm))
    over = [n for n in notes if n['t0'] > dur_audio]
    if over:
        print('  ⚠ %d 个音的起音**超出音频时长**（最多超 %.1fs）—— 先查拍速口径'
              % (len(over), max(n['t0'] for n in over) - dur_audio))
    g = Grid(y)
    rows = []
    for n in notes:
        r = measure_one(g, n['pitch'], n['t0'], n['dur'])
        rows.append(dict(n, **r))
    from collections import Counter
    cnt = Counter(r['state'] for r in rows)
    print('\n=== 三态分布（不看任何标签，只量音频）===')
    for k in ('ok', 'masked', 'weak', 'no-support', 'unknown'):
        if cnt.get(k):
            print('  %-11s %5d (%.0f%%)' % (k, cnt[k], cnt[k] / len(rows) * 100))
    print('  （`weak` = 有能量但谐波结构证据弱：**打击乐天然落这一档**，也可能是被盖住）')
    print('\n=== 按轨（同一把尺子，打击轨的低分不是缺陷）===')
    print('%-18s %6s %7s %7s %7s %9s' % ('轨', '音数', 'ok', 'masked', 'weak', '无支撑'))
    bytr = {}
    for r in rows:
        bytr.setdefault(r['track'], []).append(r)
    for t in sorted(bytr, key=lambda x: -len(bytr[x])):
        sub = bytr[t]
        c = Counter(r['state'] for r in sub)
        print('%-18s %6d %6.0f%% %6.0f%% %6.0f%% %8.0f%%'
              % (t[:17], len(sub), c.get('ok', 0) / len(sub) * 100,
                 c.get('masked', 0) / len(sub) * 100, c.get('weak', 0) / len(sub) * 100,
                 c.get('no-support', 0) / len(sub) * 100))
    print('\n=== 逐段（每 %.0fs）===' % a.segment)
    print('%-14s %6s %7s %7s %9s %9s' % ('段', '音数', 'ok', 'masked', '无支撑', '纯度中位'))
    nseg = max(1, int(np.ceil(dur_audio / a.segment)))
    for i in range(nseg):
        lo, hi = i * a.segment, (i + 1) * a.segment
        sub = [r for r in rows if lo <= r['t0'] < hi]
        if not sub:
            continue
        c = Counter(r['state'] for r in sub)
        pu = np.median([r['purity'] for r in sub if r.get('purity') is not None])
        print('%-14s %6d %6.0f%% %6.0f%% %8.0f%% %9.2f'
              % ('%.0f–%.0fs' % (lo, hi), len(sub), c.get('ok', 0) / len(sub) * 100,
                 c.get('masked', 0) / len(sub) * 100,
                 c.get('no-support', 0) / len(sub) * 100, pu))
    for r in rows:
        r['review'] = review_score(r)
    need = sorted(rows, key=lambda r: -r['review'])[:a.budget]
    print('\n=== 复核预算：最该人听的 %d 个（按可疑度）===' % a.budget)
    print('%-8s %-16s %-6s %-11s %9s %8s %8s' %
          ('t0', '轨', '音高', '状态', 'fund_dB', '纯度', '抬升dB'))
    for r in need[:25]:
        print('%-8.2f %-16s %-6d %-11s %9.1f %8.2f %8.1f'
              % (r['t0'], r['track'][:15], r['pitch'], r['state'],
                 r['fund_db'] or 0.0, r['purity'] or 0.0, r['margin_db'] or 0.0))
    if a.json:
        json.dump(rows, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('\n→ %s' % a.json)
    return 0


if __name__ == '__main__':
    import cli_utf8 as _cu; _cu.setup()      # 控制台编码兜底（GBK 下打印 ✓ 会崩）
    sys.exit(main())
