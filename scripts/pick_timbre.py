#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""pick_timbre.py —— **按"原曲那一层的能力"自动挑 GM 音色**（一条命令）。

## 为什么要有它（用户 2026-10-02 口径："以后可以根据不同音乐切换，其它乐器也要自适应"）

现有链路里音色是**写死**的：`Hook` 恒为 GM 24 尼龙吉他，而原曲那把吉他
**质心 1479Hz / 2–6k 占 29.84%**，GM 吉他族里没有对应（最近的 30 Distortion 是 63%，过头一倍）。
`Melody` 恒为 GM 0 钢琴，可它的 151 个音其实来自 YMT3 的 **Synth Lead** 通道。

本工具把"选音色"变成一条**可复现、带依据表**的命令：
  ① 从**原曲分轨**量该层的能力（谐波% / 2–6k% / 质心 / 动态 / 短音占比）
  ② 从 GM 音色能力表里，在**同一族内**按距离排序
  ③ 打印"推荐 program + 逐项依据 + 与第 2 名的差距"

## 三条硬规矩（都是实测踩出来的）

1. **不许跨族**：跨族的频谱距离会把"钟琴"推给"钢琴"、把"小号"推给"吉他"。
   所以候选池 = 由引擎轨名（或 `--family`）决定的那一族，**只在该族内排**。
2. **能力对齐 ≠ 听感像**：技能里有实测教训 —— 用户认可的模板自己渲染出来，质心差一个数量级。
   所以本工具的输出是**候选 + 依据**，最终要人耳定（`--ab` 会给一条渲染 A/B 的命令）。
3. **短音占比决定族内取舍**：同族里"拨弦型"（衰减快）与"持续型"（能按住）听感完全不同。
   只比频谱会把两者判成等价 —— 所以把原曲该层的**短音占比**（时值 <0.3s 的比例）也当一维。

## 用法

```bash
python scripts/pick_timbre.py --stems <demucs六轨目录> --song <曲名> [--json 出.json] [--ab]
python scripts/pick_timbre.py --selftest          # 合成正负例验方向（不读真音频）
#   GM 能力表：--gmcap <目录>（默认 <根>/_gmcap，由 `gm_capability.py` 生成）
```

⚠ 缺 GM 能力表时**不猜**：直接报"缺表 + 生成命令"（`gm_capability.py`），
   因为"没量过的音色"排出来的名次是噪声。
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

SR = 22050

# 引擎轨 → 该轨该从哪条分轨取"能力目标"（与 `gap_fill_stem`/`restore_gap_fill` 同口径）
STEM_OF = {'Bass': 'bass', 'Drums': 'drums', 'Piano': 'piano', 'Hook': 'guitar',
           'Strings': 'other', 'Pad': 'other', 'Melody': 'vocals', 'Glock': 'other'}
# 引擎轨 → GM 族（**候选池只能在本族内**；见文首规矩 1）
FAMILY_OF = {'Bass': 'bass', 'Piano': 'piano', 'Hook': 'guitar', 'Strings': 'strings',
             'Pad': 'pad', 'Melody': 'lead', 'Glock': 'chroma', 'Drums': 'drum'}
# GM 族 → 允许的 program 列表（GM 号）
FAMILIES = {
    'piano':   [0, 1, 2, 3, 4, 5],
    'chroma':  [8, 9, 10, 11, 12, 13, 14, 15],
    'organ':   [16, 17, 18, 19, 20, 21, 22, 23],
    'guitar':  [24, 25, 26, 27, 28, 29, 30, 31],
    'bass':    [32, 33, 34, 35, 36, 37, 38, 39],
    'strings': [40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55],
    'brass':   [56, 57, 58, 59, 60, 61, 62, 63],
    'reed':    [64, 65, 66, 67, 68, 69, 70, 71],
    'pipe':    [72, 73, 74, 75, 76, 77, 78, 79],
    'lead':    [80, 81, 82, 83, 84, 85, 86, 87],
    'pad':     [88, 89, 90, 91, 92, 93, 94, 95],
    'drum':    [],
}
GM_NAMES = {
    0: 'Acoustic Grand', 1: 'Bright Acoustic', 2: 'Electric Grand', 3: 'Honky-tonk',
    4: 'Electric Piano 1', 5: 'Electric Piano 2', 6: 'Harpsichord', 7: 'Clavinet',
    8: 'Celesta', 9: 'Glockenspiel', 10: 'Music Box', 11: 'Vibraphone', 12: 'Marimba',
    13: 'Xylophone', 14: 'Tubular Bells', 15: 'Dulcimer',
    24: 'Nylon Guitar', 25: 'Steel Guitar', 26: 'Jazz Guitar', 27: 'Clean Guitar',
    28: 'Muted Guitar', 29: 'Overdriven', 30: 'Distortion', 31: 'Guitar Harmonics',
    32: 'Acoustic Bass', 33: 'Electric Bass (finger)', 34: 'Electric Bass (pick)',
    35: 'Fretless Bass', 36: 'Slap Bass 1', 37: 'Slap Bass 2', 38: 'Synth Bass 1',
    39: 'Synth Bass 2', 40: 'Violin', 41: 'Viola', 42: 'Cello', 43: 'Contrabass',
    44: 'Tremolo Strings', 45: 'Pizzicato Strings', 46: 'Orchestral Harp',
    47: 'Timpani', 48: 'String Ensemble 1', 49: 'String Ensemble 2',
    50: 'Synth Strings 1', 51: 'Synth Strings 2', 52: 'Choir Aahs', 53: 'Voice Oohs',
    56: 'Trumpet', 57: 'Trombone', 58: 'Tuba', 59: 'Muted Trumpet', 60: 'French Horn',
    61: 'Brass Section', 62: 'Synth Brass 1', 63: 'Synth Brass 2',
    64: 'Soprano Sax', 65: 'Alto Sax', 66: 'Tenor Sax', 67: 'Baritone Sax',
    68: 'Oboe', 69: 'English Horn', 70: 'Bassoon', 71: 'Clarinet',
    72: 'Piccolo', 73: 'Flute', 74: 'Recorder', 75: 'Pan Flute',
    80: 'Lead 1 (square)', 81: 'Lead 2 (sawtooth)', 82: 'Lead 3 (calliope)',
    83: 'Lead 4 (chiff)', 84: 'Lead 5 (charang)', 85: 'Lead 6 (voice)',
    86: 'Lead 7 (fifths)', 87: 'Lead 8 (bass+lead)',
    88: 'Pad 1 (new age)', 89: 'Pad 2 (warm)', 90: 'Pad 3 (polysynth)',
    91: 'Pad 4 (choir)', 92: 'Pad 5 (bowed)', 93: 'Pad 6 (metallic)',
    94: 'Pad 7 (halo)', 95: 'Pad 8 (sweep)',
}


# ───────────────────────── 度量 ─────────────────────────
def audio_profile(path, max_sec=None):
    """一条分轨的物理能力：谐波% / 2–6k% / 质心Hz / 动态dB（与 `乐器能力表.md` 同口径）"""
    import soundfile as sf
    import librosa
    y, sr = sf.read(path, dtype='float32', always_2d=True)
    y = y.mean(axis=1)
    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    if max_sec:
        y = y[:int(max_sec * SR)]
    n, hop = 4096, 2048
    fr = np.fft.rfftfreq(n, 1.0 / SR)
    win = np.hanning(n)
    b26 = (fr >= 2000) & (fr <= 6000)
    bf0 = (fr >= 60) & (fr <= 1000)
    harms, his, cens, ens = [], [], [], []
    for i in range(0, max(1, len(y) - n), hop * 4):
        sp = np.abs(np.fft.rfft(y[i:i + n] * win))
        p = sp ** 2
        tot = float(p.sum())
        if tot <= 1e-12:
            continue
        ens.append(10 * np.log10(tot))
        his.append(100.0 * float(p[b26].sum()) / tot)
        cens.append(float((fr * p).sum() / tot))
        if bf0.any():
            f0 = float(fr[bf0][int(np.argmax(sp[bf0]))])
            he = 0.0
            for k in range(1, 6):
                f = f0 * k
                if f > SR * 0.45:
                    break
                m = (fr >= f * 0.98) & (fr <= f * 1.02)
                if m.any():
                    he += float(p[m].sum())
            harms.append(100.0 * he / tot)
    if not harms:
        return None
    e = np.asarray(ens)
    return dict(harm=float(np.median(harms)), hi26=float(np.median(his)),
                cent=float(np.median(cens)),
                dyn=float(np.percentile(e, 90) - np.percentile(e, 10)) if len(e) > 3 else 0.0)


def notes_profile(mid_path):
    """该层转录的"演奏形态"：短音占比（时值 <0.3s）、时值中位、每小节音数"""
    import midi_file
    m = midi_file.import_midi(mid_path)
    spb = 60.0 / float(m.get('bpm') or 120.0)
    durs = []
    for tr in m.get('tracks', []):
        if tr.get('drum') or tr.get('channel') == 9:
            continue
        for (st, du, _p, _v) in tr.get('notes', []):
            durs.append(float(du) * spb)
    if not durs:
        return None
    d = np.asarray(durs)
    return dict(n=len(d), short=float(np.mean(d < 0.3)), med=float(np.median(d)))


def score(prof, note_prof, cap):
    """距离（越小越近）。四维都归一化到"人耳量级"，不是各维方差倒数。

    ⚠ 权重别乱调：`hi26`/`cent` 的分母（5 / 300）来自 `gm_capability.py --targets`
    已经用过的那套；`short` 只有在两边都量到时才算（缺一侧就跳过该维，不塞 0）。
    """
    s = ((cap['harm'] - prof['harm']) / 10.0) ** 2 + \
        ((cap['hi26'] - prof['hi26']) / 5.0) ** 2 + \
        ((cap['cent'] - prof['cent']) / 300.0) ** 2
    n = 3
    cs, ss = cap.get('short'), (note_prof or {}).get('short')
    if cs is not None and ss is not None:
        s += ((cs - ss) / 0.25) ** 2
        n += 1
    return (s / n) ** 0.5


def load_gmcap(d):
    """读 GM 能力表：`gm_capability.py --out-dir <d>` 产出的 `gm_capability.json`。

    → ({program: {harm, hi26, cent, dyn, decay12_ms, short?}}, 表路径)
    ⚠ **缺表就报错，不猜**：没量过的音色排出来的名次是噪声。
    ⚠ 首版只找 `--gmcap` 目录、而 `gm_capability.py` 只打印不落盘 → 永远报"缺表"。
      现在两件事都做：表由那个脚本写 `gm_capability.json`，这里找不到就**自动生成一次**。
    """
    p = os.path.join(d, 'gm_capability.json')
    if not os.path.isfile(p):
        gen = os.path.join(ROOT, 'gm_capability.py')
        if not os.path.isfile(gen):
            raise SystemExit('缺 GM 能力表，且找不到生成器 %s' % gen)
        print('（没有 GM 能力表 → 先跑 %s 生成一次，约 1 分钟）' % os.path.basename(gen))
        import subprocess
        os.makedirs(d, exist_ok=True)
        r = subprocess.run([sys.executable, gen, '--out-dir', d],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if not os.path.isfile(p):
            raise SystemExit('生成失败：%s\n%s' % (p, (r.stderr or '')[-300:]))
    j = json.load(open(p, encoding='utf-8'))
    rows = j if isinstance(j, list) else (j.get('rows') or j.get('programs') or [])
    out = {}
    for r in rows:
        prog = r.get('program', r.get('gm', r.get('prog')))
        if prog is None or r.get('harm') is None:
            continue
        out[int(prog)] = {k: r.get(k) for k in
                          ('harm', 'hi26', 'cent', 'dyn', 'decay12_ms', 'short')}
    if not out:
        raise SystemExit('%s 里没有可用的行（要有 program + harm/hi26/cent）' % p)
    return out, p


def apply_picks(song, picks, dry=False):
    """把推荐写进 `songs/<song>/song.json` 的 `programs`（然后必须重渲染）。

    `picks` = {轨: program}。通道沿用 song.json 里已有的（缺则用引擎的固定通道表）。

    ⚠ 三条实测踩出来的（都不是"顺手就能过"的）：
      1. 值必须是 `[prog, chan]` 两元组 —— `song_engine.load` 会 `tuple(v)`；
      2. **chan 不许是 None** —— `bgm_synth.write_midi` 做 `0xC0 | channel`，None 直接崩；
      3. 写盘走 `json_io.save`（LF + 紧凑格式），并**读回核对**。
    """
    import json_io
    import song_engine
    p = None
    for base in ('songs', 'songs_direct'):
        c = os.path.join(ROOT, base, song, 'song.json')
        if os.path.isfile(c):
            p = c
            break
    if not p:
        raise SystemExit('找不到 %s 的 song.json' % song)
    d = json.load(open(p, encoding='utf-8'))
    pr = d.setdefault('programs', {})
    CH = getattr(song_engine, 'CH', {})
    for tr, prog in picks.items():
        old = pr.get(tr)
        ch = (int(old[1]) if isinstance(old, (list, tuple)) and len(old) > 1
              and old[1] is not None else CH.get(tr))
        if ch is None:
            raise SystemExit('轨 %s 的通道未知（song.json 里没有、引擎固定表里也没有）' % tr)
        print('   %-8s → program %-3d channel %d（原 %s）' % (tr, prog, ch, old))
        pr[tr] = [int(prog), int(ch)]
    if dry:
        print('   （--dry：没有写盘）')
        return p, {}
    json_io.save(p, d)
    back = {k: list(v) for k, v in json.load(open(p, encoding='utf-8'))['programs'].items()}
    print('   已写回 %s\n   读回 programs：%s' % (p, back))
    return p, back


def current_programs(song=None):
    """当前 song.json 里各轨用的 program（→ {轨: GM 号}）。

    ⚠ 三种来源都要看（实测踩过"读不到就当没有"）：`programs` 显式表、`sections[].arr.melody_prog`
    （段级音色）、以及引擎风格预设的默认值。这里只读前两种 —— 预设默认值在**渲染后的 MIDI** 里，
    所以 `--from-mid` 可以给一份渲染产物来读真实值（比读 song.json 准）。
    """
    p = None
    if song:
        for base in ('songs', 'songs_direct'):
            c = os.path.join(ROOT, base, song, 'song.json')
            if os.path.isfile(c):
                p = c
                break
    if not p:
        return {}
    d = json.load(open(p, encoding='utf-8'))
    out = dict(d.get('programs') or {})
    for s in (d.get('sections') or []):
        a = s.get('arr') or {}
        nm = s.get('name')
        if a.get('melody_prog') is not None:
            out.setdefault('%s(melody_prog %s)' % ('Melody', nm), a['melody_prog'])
    return out


def current_from_mid(mid_path):
    """从**渲染后的 MIDI** 读每条轨最后一个 program（最接近"实际听到的音色"）。"""
    import midi_file
    m = midi_file.import_midi(mid_path)
    out = {}
    for tr in m.get('tracks', []):
        nm = 'Drums' if (tr.get('drum') or tr.get('channel') == 9) else tr.get('name')
        pc = tr.get('program_changes') or []
        out[nm] = int(pc[-1][1]) if pc else tr.get('program')
    return out


def recommend(stems_dir, song=None, gmcap=None, track=None, family=None, max_sec=None,
              stems_midi=None):
    """主流程：→ [{track, stem, family, target, ranked:[(prog,name,dist,cap)...], note_prof}]"""
    from collections import OrderedDict
    cap_tbl, cap_path = load_gmcap(gmcap)
    tags = OrderedDict()
    for tr, stem in STEM_OF.items():
        if track and tr != track:
            continue
        p = os.path.join(stems_dir, stem + '.wav')
        if os.path.exists(p):
            tags[tr] = (stem, p)
    out = []
    for tr, (stem, p) in tags.items():
        fam = family or FAMILY_OF.get(tr)
        pool = FAMILIES.get(fam) or []
        if not pool:
            out.append(dict(track=tr, stem=stem, family=fam, target=None, ranked=[],
                            note='该族没有可挑的 GM 音色（鼓轨按键位映射，不在此工具范围）'))
            continue
        prof = audio_profile(p, max_sec)
        if not prof:
            continue
        # ⚠ 演奏形态必须取**该层自己那条分轨转录**（首版按目录里第一个 *.mid 取，
        #   于是 7 条轨的"短音占比/时值中位"读数**一模一样**（都是同一个文件的）——
        #   那条维度的权重等于白给，还让人误以为量过了。来源映射见 STEM_OF。
        tr_mid = None
        if stems_midi:
            cand = os.path.join(stems_midi, stem + '.mid')
            if os.path.isfile(cand):
                tr_mid = cand
        np_msg = notes_profile(tr_mid) if tr_mid else None
        ranked = []
        for prog in pool:
            c = cap_tbl.get(prog)
            if not c or c.get('harm') is None:
                continue
            ranked.append((prog, GM_NAMES.get(prog, '?'), score(prof, np_msg, c), c))
        ranked.sort(key=lambda z: z[2])
        out.append(dict(track=tr, stem=stem, family=fam, target=prof, note_prof=np_msg,
                        ranked=[(p_, n, round(d, 3), c) for (p_, n, d, c) in ranked]))
    return out, cap_path


def report(rows, gmcap_path=None, ab=False, song=None, current=None):
    if gmcap_path:
        print('GM 能力表：%s\n' % gmcap_path)
    for r in rows:
        print('== %s ← 分轨 %s · 族 %s ==' % (r['track'], r['stem'], r['family']))
        if r.get('note'):
            print('   %s' % r['note'])
            continue
        t = r['target']
        print('   原曲该层能力：谐波 %.1f%% · 2–6k %.2f%% · 质心 %.0fHz · 动态 %.1f dB'
              % (t['harm'], t['hi26'], t['cent'], t['dyn']))
        if r.get('note_prof'):
            q = r['note_prof']
            print('   该层演奏形态：%d 音 · 短音(<0.3s)占比 %.0f%% · 时值中位 %.0fms'
                  % (q['n'], 100 * q['short'], q['med'] * 1000))
        cur = (current or {}).get(r['track'])
        for i, (prog, name, dist, c) in enumerate(r['ranked'][:4]):
            mark = '★ 推荐' if i == 0 else ('  备选' if i < 3 else '')
            tag = '   ← 当前用的就是这个' if prog == cur else ''
            print('   %s GM %-3d %-24s 距 %.3f · 谐波 %.1f · 2–6k %.2f · 质心 %.0f%s'
                  % (mark, prog, name, dist, c['harm'], c['hi26'], c['cent'], tag))
        if cur is not None:
            rank = next((i + 1 for i, x in enumerate(r['ranked']) if x[0] == cur), None)
            if rank is None:
                print('   ⚠ 当前 program %s 不在本族候选里（可能是别的族/自定义）' % cur)
            else:
                d0, dc = r['ranked'][0][2], r['ranked'][rank - 1][2]
                print('   **当前 GM %s 排第 %d/%d（距 %.3f；推荐比它近 %.3f）**'
                      % (cur, rank, len(r['ranked']), dc, dc - d0))
        if len(r['ranked']) >= 2:
            gap = r['ranked'][1][2] - r['ranked'][0][2]
            note = '（第 1 名比第 2 名近 %.3f' % gap
            if gap < 0.05:
                note += ' —— **差距 <0.05，这一层不算有结论，必须人耳定**'
            elif r['ranked'][0][2] > 2.0:
                note += ' —— ⚠ 但**最近候选的距离 >2.0**（族内没有真匹配的，多半是该层识别本身可疑）'
            else:
                note += ' —— 差距够大，可以进 A/B'
            print('   %s）' % note)
        if ab and song:
            print('   A/B 复现：改 song.json 的 programs.%s = %d → make_song %s --no-tune'
                  % (r['track'], r['ranked'][0][0], song))
        print()
    print('⚠ 能力对齐 ≠ 听感像（技能里有实测教训：用户认可的模板自己渲染出来，质心差一个数量级）'
          '—— 最终要人耳定。')


def selftest():
    """合成正负例：只验"方向"与"族内限制"，不读真音频。
      · 拨弦型目标（低谐波/高 2–6k/高质心/短音多）在同族内必须把"失真吉他"排到"尼龙吉他"前
      · 持续型目标（高谐波/低 2–6k/中质心/短音少）必须反过来
      · 跨族必须被挡住（吉他族里不许出现钢琴）
    """
    ok = True

    def chk(lab, got, want):
        nonlocal ok
        f = 'PASS' if got == want else 'FAIL'
        ok = ok and got == want
        print('  [%s] %-52s 期望 %-6s 实得 %-6s' % (f, lab, want, got))

    cap = {
        24: dict(harm=28.3, hi26=2.76, cent=540, dyn=72.3, short=0.30),
        27: dict(harm=20.0, hi26=10.0, cent=900, dyn=60.0, short=0.55),
        30: dict(harm=18.0, hi26=29.0, cent=1450, dyn=55.0, short=0.70),
        0:  dict(harm=24.8, hi26=0.92, cent=487, dyn=39.2, short=0.20),
    }
    plucked = dict(harm=18.4, hi26=29.84, cent=1479, dyn=57.6)
    plucked_np = dict(n=100, short=0.70, med=0.15)
    sustained = dict(harm=32.0, hi26=2.0, cent=850, dyn=46.0)
    sustained_np = dict(n=100, short=0.05, med=1.2)
    r1 = sorted([p for p in (24, 27, 30)], key=lambda p: score(plucked, plucked_np, cap[p]))
    chk('拨弦型目标 → 失真吉他(30) 排第一', r1[0], 30)
    r2 = sorted([p for p in (24, 27, 30)], key=lambda p: score(sustained, sustained_np, cap[p]))
    chk('持续型目标 → 尼龙吉他(24) 排第一', r2[0], 24)
    chk('跨族被挡：吉他族候选里没有钢琴(0)', 0 in FAMILIES['guitar'], False)
    chk('族表覆盖所有引擎音高轨', all(FAMILY_OF[k] in FAMILIES for k in
                                       ('Bass', 'Piano', 'Hook', 'Strings', 'Pad', 'Melody', 'Glock')), True)
    print('  selftest %s' % ('全部通过' if ok else '有失败'))
    return ok


def main():
    ap = argparse.ArgumentParser(description='按原曲那一层的能力自动挑 GM 音色（族内）')
    ap.add_argument('--stems', help='demucs 六轨目录')
    ap.add_argument('--stems-midi', default=None,
                    help='各分轨**各自的转录**目录（`ymt3_allstems`）。给了才量"演奏形态"'
                         '（短音占比/时值中位）—— 按分轨名一一对应取 `bass.mid`/`guitar.mid`/…')
    ap.add_argument('--song', default=None, help='曲名（只用于 A/B 命令里的显示）')
    ap.add_argument('--from-mid', default=None,
                    help='渲染后的 MIDI：从它读**真实生效**的 program（比读 song.json 准 —— '
                         '风格预设的默认值只在 MIDI 里）')
    ap.add_argument('--gmcap', default=os.path.join(ROOT, '_gmcap'),
                    help='GM 能力表目录（`gm_capability.py --out-dir` 的产物）')
    ap.add_argument('--track', default=None, help='只算这一条轨')
    ap.add_argument('--family', default=None, help='强制候选池族（默认按轨名推）')
    ap.add_argument('--max-sec', type=float, default=60.0, help='每条分轨最多量这么多秒（默认 60）')
    ap.add_argument('--json', default=None)
    ap.add_argument('--apply', action='store_true',
                    help='把推荐写进 song.json 的 programs（⚠ 写完**必须重渲染**才生效）')
    ap.add_argument('--dry', action='store_true', help='与 --apply 同用时只打印不写盘')
    ap.add_argument('--min-gap', type=float, default=0.05,
                    help='第 1/2 名差距小于它就拒绝 --apply（那一层不算有结论）')
    ap.add_argument('--only', default=None,
                    help='--apply 时只改这些轨（逗号分隔）。⚠ 默认"全改"会把 Bass 推成 '
                         'Synth Bass 1(GM38)、Piano 推成 Electric Grand(GM2) —— '
                         '能力更近但**音色族换了个乐器**，用户明确忌过"太电音"，所以默认只建议、'
                         '要改请显式点名')
    ap.add_argument('--max-cap-dist', type=float, default=2.0,
                    help='最近候选的距离大于它就跳过（族内没有真匹配的 ⇒ 多半是该层识别本身可疑）')
    ap.add_argument('--ab', action='store_true', help='打印改 programs 的复现命令')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not a.stems:
        ap.print_help()
        return 2
    if not os.path.isdir(a.stems):
        raise SystemExit('--stems 不是目录：%s' % a.stems)
    try:
        import pyenv
        pyenv.ensure('librosa', '.venv-ml', '本工具要 librosa/soundfile（装在 .venv-ml）')
    except Exception:                                          # noqa: BLE001
        pass
    rows, cap_path = recommend(a.stems, a.song, a.gmcap, a.track, a.family, a.max_sec,
                               a.stems_midi)
    cur = {}
    if a.from_mid and os.path.isfile(a.from_mid):
        cur = current_from_mid(a.from_mid)
    elif a.song:
        cur = current_programs(a.song)
    report(rows, cap_path, a.ab, a.song, cur)
    if a.apply:
        if not a.song:
            raise SystemExit('--apply 要同时给 --song')
        picks = {}
        only = {x.strip() for x in (a.only or '').split(',') if x.strip()}
        for r in rows:
            rk = r.get('ranked') or []
            if len(rk) < 2:
                continue
            if only and r['track'] not in only:
                print('   跳过 %s：不在 --only 名单里' % r['track'])
                continue
            gap = rk[1][2] - rk[0][2]
            if gap < a.min_gap:
                print('   跳过 %s：第 1/2 名只差 %.3f（<%.2f）—— 不算有结论'
                      % (r['track'], gap, a.min_gap))
                continue
            if rk[0][2] > a.max_cap_dist:
                print('   跳过 %s：最近候选距离 %.3f > %.2f —— 族内没有真匹配的，'
                      '多半是该层识别本身可疑（先修识别，不是换音色）'
                      % (r['track'], rk[0][2], a.max_cap_dist))
                continue
            picks[r['track']] = rk[0][0]
        if not picks:
            print('   没有可应用的推荐（都因差距不足被跳过）')
        else:
            print('\n应用推荐（gap ≥ %.2f）：' % a.min_gap)
            apply_picks(a.song, picks, dry=a.dry)
            print('   ⚠ 写完必须重渲染才算生效：`make_song %s --no-tune`（并读回 .mid 核对 program）'
                  % a.song)
    if a.json:
        json.dump(rows, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1,
                  default=str)
        print('  → %s' % a.json)
    return 0


if __name__ == '__main__':
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:                                          # noqa: BLE001
        pass
    sys.exit(main())
