#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**按风格挑参考曲** —— 别拿风格不同的权威模板比（用户 2026-09-21 明确）。

为什么要它（用户原话："让生成不同风格的音乐和权威模板比较 不要一直拿 BGM35 比较 风格都不一样"）：
  我一直拿 `BGM35 (1).mid` 当唯一权威尺子，可它是 **C 小调 / 120BPM / 有鼓 / 35 音每小节 /
  多乐器**；而我在做的是一首 **A 小调 / 78BPM / 纯钢琴 / 无鼓**曲 —— 风格根本不是一类，
  那个对比从第一步就是错的（频谱、密度、节奏型的差异全来自"编制不同"，不是"好坏"）。

口径（可核）：**先按风格筛出候选，再在候选里挑最接近的**，而不是全局硬比。
  风格特征全部来自**画像**（`refs/*.json`，真实录音实测值，不是猜的）：
    · `bpm`            速度带（慢速抒情通常 ≤ 100）
    · `character`      人声 / 器乐（我们自己生成的是器乐 → 排除 vocal）
    · `rhythm_high/low` 16 格节奏型 → **密集度**（★/◇ 计数）：有鼓的曲密集、纯钢琴曲稀疏
    · `bands['40-80']` / `bands['5000-10000']` 低/高频能量 → 编制厚薄的旁证
    · `centroid`       亮度（纯钢琴通常偏低）
  与"我的曲子"的距离 = 上述特征的加权差，**速度与"有无鼓"权重最高**（这两项最决定风格）。

用法:
  python scripts\\pick_reference.py --list                    # 列出全部画像的风格标签
  python scripts\\pick_reference.py --song <song.json>        # 给我的曲子挑同风格参考（top5）
  python scripts\\pick_reference.py --bpm 78 --drum no --centroid 1016   # 直接给特征
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu          # noqa: E402

_cu.setup()

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REFS = os.path.join(ROOT, 'refs')


def dens(rhythm):
    """16 格节奏型 → 起音密度（★ 重、◇ 轻、· 空；两者都算"这一格有音"）"""
    if not rhythm:
        return 0
    return sum(1 for c in rhythm if c in '★◇')


def drum_evidence(ref):
    """**鼓的证据**（比"密集度≥5"准）：低频节奏型里强拍(★)的数量 + 高频是否成片。

    为什么不用密集度：实测把 `BGM19`（低频只有 1 个 ★、中高频几乎全空 —— 明显是安静曲）
    也判成"有鼓"，因为它的 16 格里有几格轻音(◇)。风格判据错了，挑出来的参考就全错。
    """
    lo, hi = ref.get('rhythm_low') or '', ref.get('rhythm_high') or ''
    strong_lo = lo.count('★')
    dense_hi = sum(1 for c in hi if c in '★◇')
    # 强拍 ≥4 或 高频起音 ≥8 才算"真有鼓组"
    return strong_lo >= 4 or dense_hi >= 8


def style_of(p):
    """给画像打一个**可读的风格标签**（原始特征 → 人话）"""
    bpm = float(p.get('bpm') or 0)
    cent = float(p.get('centroid') or 0)
    drum = drum_evidence(p)
    tags = []
    tags.append('慢速' if bpm <= 100 else ('中速' if bpm <= 135 else '快速'))
    tags.append('有鼓' if drum else '**无鼓/极稀疏**')
    tags.append('偏暗' if cent < 2500 else ('中等' if cent < 3300 else '偏亮'))
    if p.get('character') == 'vocal_forward':
        tags.append('人声')
    return ' · '.join(tags), drum, bpm, cent


def load_refs(exclude_vocal=True):
    out = []
    for f in sorted(glob.glob(os.path.join(REFS, '*.json'))):
        if os.path.basename(f).startswith(('mix_targets', 'themes', 'sections',
                                           'identify', 'melody')):
            continue
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:                                        # noqa: BLE001
            continue
        if 'bpm' not in d or 'bands' not in d:
            continue
        if exclude_vocal and d.get('character') == 'vocal_forward':
            continue
        d['_path'] = f
        out.append(d)
    return out


def song_features(path):
    """从 song.json + 渲染出的 MIDI 估风格特征（速度/有无鼓/质心留空）"""
    d = json.load(open(path, encoding='utf-8'))
    percs = [s.get('arr', {}).get('perc') for s in d.get('sections', [])]
    drum = any((p or 0) > 0 for p in percs)
    return {'bpm': float(d.get('bpm') or 0), 'drum': drum,
            'name': os.path.basename(os.path.dirname(os.path.abspath(path)))}


def score(ref, feat):
    """风格距离：速度(权 1.0/每 BPM) + 有无鼓(硬 40) + 质心(若有，权 0.002/Hz)"""
    _, drum, bpm, cent = style_of(ref)
    s = abs(bpm - feat['bpm']) * 1.0
    s += 0.0 if drum == feat['drum'] else 40.0
    if feat.get('centroid') and cent:
        s += abs(cent - feat['centroid']) * 0.002
    return s


def midi_piano_share(it):
    """MIDI 索引条目里"钢琴类轨"的音符占比（GM 0-7 = 钢琴族）。

    ⚠ **字段名以索引为准：音符数在 `notes`**（不是 `note_count` —— 我第一版读错，
    **静默取到 0**，于是所有模板的"钢琴占比"都显示 0%，一个异常都没抛）。
    所以这里显式 assert：一条轨都没读到音符 = 索引格式变了，当场报错而不是给 0。
    """
    tot = pno = 0
    for t in it.get('tracks') or []:
        n = int(t.get('notes') or 0)          # ← 索引里就是这个键
        if not n:
            continue
        tot += n
        pc = t.get('program_changes') or []
        prog = pc[0][1] if (pc and isinstance(pc[0], (list, tuple)) and len(pc[0]) > 1) \
            else t.get('program')
        try:
            if prog is not None and 0 <= int(prog) <= 7:
                pno += n
        except (TypeError, ValueError):
            pass
    if tot == 0 and (it.get('tracks') or []):
        raise SystemExit('midi2 索引里读不到任何音符数（字段名可能又变了）—— '
                         '先核对 `_index.json` 的 tracks[].notes')
    return (pno / float(tot)) if tot else 0.0


def pick_midi_templates(bpm, piano_only, styles=None, limit=10):
    """从**网上下载的 MIDI 模板库**（`refs/midi2/_index.json`，带来源 URL）按风格挑参考。

    为什么这个库比音频画像更适合"按风格挑"：索引里**有显式风格目录**（ballad/romantic/
    classical/newage…）+ BPM + 逐轨信息（能算"钢琴占比"），而音频画像只有 50 份、
    且判风格要靠频谱猜（我上一版把安静的 BGM19 都判成"有鼓"）。
    """
    idx = json.load(open(os.path.join(REFS, 'midi2', '_index.json'), encoding='utf-8'))
    rows = []
    for it in idx:
        f = it.get('file') or ''
        style = it.get('style') or f.split('/')[0]
        if styles and style not in styles:
            continue
        try:
            b = float(it.get('bpm') or 0)
        except (TypeError, ValueError):
            continue
        if b <= 0:
            continue
        ps = midi_piano_share(it)
        # 风格距离：速度为主（1/BPM）+ 钢琴占比（纯钢琴曲要看钢琴主导的模板）
        s = abs(b - bpm)
        if piano_only:
            s += (1.0 - ps) * 60.0        # 钢琴占比越低越远（最多罚 60）
        rows.append((s, style, f, b, ps, it))
    rows.sort(key=lambda x: x[0])
    return rows[:limit]


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--song')
    ap.add_argument('--bpm', type=float)
    ap.add_argument('--drum', choices=['yes', 'no'])
    ap.add_argument('--centroid', type=float)
    # **网上下载的 MIDI 模板库**（用户 2026-09-21 明确要的那条路）
    ap.add_argument('--midi', action='store_true', help='从 refs/midi2 按风格挑 MIDI 模板')
    ap.add_argument('--styles', help='逗号分隔的风格目录（如 ballad,romantic,classical）')
    ap.add_argument('--piano-only', action='store_true', help='只要钢琴主导的模板')
    a = ap.parse_args()

    if a.midi:
        bpm = a.bpm
        if bpm is None and a.song:
            bpm = json.load(open(a.song, encoding='utf-8')).get('bpm')
        bpm = float(bpm or 78.0)
        styles = [s.strip() for s in a.styles.split(',')] if a.styles else None
        print('=== 网上下载的 MIDI 模板库（refs/midi2，带来源 URL）===')
        print('目标：%.0f BPM%s%s'
              % (bpm, ' · 钢琴主导' if a.piano_only else '',
                 (' · 风格 %s' % ','.join(styles)) if styles else ' · 全部风格'))
        rows = pick_midi_templates(bpm, a.piano_only, styles)
        print('\n  %-14s %-6s %-7s %-8s %s' % ('风格', 'BPM', '钢琴占比', '时长', '文件'))
        for s, st, f, b, ps, it in rows:
            print('  %-14s %-6.0f %-7s %-8s %s'
                  % (st, b, '%.0f%%' % (ps * 100),
                     '%.0fs' % (it.get('seconds') or 0), f))
        print('\n  → 复现/对比用：`midi_ref.py refs\\midi2\\<文件>`；'
              '这批模板**不进仓库**，clone 后跑 `fetch_midi_lib.py` 重建')
        return 0

    refs = load_refs()
    if a.list:
        print('%-10s %-38s %6s %8s' % ('画像', '风格标签', 'BPM', '质心'))
        for r in sorted(refs, key=lambda x: float(x.get('bpm') or 0)):
            tag, _d, bpm, cent = style_of(r)
            print('%-10s %-38s %6.0f %8.0f' % (r.get('name'), tag, bpm, cent))
        print('\n共 %d 份器乐画像（已排除人声主导）' % len(refs))
        return 0

    if a.song:
        feat = song_features(a.song)
    else:
        feat = {'bpm': a.bpm or 78.0, 'drum': a.drum == 'yes',
                'centroid': a.centroid, 'name': 'mine'}
    print('我的曲子：%s · %.0f BPM · %s'
          % (feat['name'], feat['bpm'], '有鼓' if feat['drum'] else '无鼓/极稀疏'))

    ranked = sorted(refs, key=lambda r: score(r, feat))
    print('\n=== ① 混音/频谱对齐 → 用这几份（速度与编制最接近）===')
    print('  %-10s %-30s %6s %8s %8s' % ('画像', '风格标签', 'BPM', '质心', '风格距离'))
    for r in ranked[:5]:
        tag, _d, bpm, cent = style_of(r)
        print('  %-10s %-30s %6.0f %8.0f %8.1f'
              % (r.get('name'), tag, bpm, cent, score(r, feat)))

    # ② 段落/结构写法：找"分段数多、速度可不同"的长曲（这类只借结构，不借频谱）
    print('\n=== ② 段落/收尾写法 → 只借结构（速度可以不同，不借频谱）===')
    long_ones = sorted((r for r in refs if float(r.get('duration') or 0) >= 200),
                       key=lambda r: -len(r.get('structure') or []))[:5]
    for r in long_ones:
        print('  %-10s %.0fs · %d 段 · %.0f BPM'
              % (r.get('name'), r.get('duration') or 0, len(r.get('structure') or []),
                 r.get('bpm') or 0))
    print('  （原案例文档（已删 2026-09-24） 的"克制+交代/逐小节渐弱"属这一类 —— **借结构，别借频谱**）')

    # ③ 和声/调式语言：来自主题模板包（同主题 ≥8 首 MIDI 的聚合），不是单首音频
    print('\n=== ③ 和声/调式/旋律语言 → 用主题模板包（`refs/themes/<主题>.json`）===')
    print('  主题模板包 = 同主题 **≥8 首 MIDI** 的聚合画像，比拿单首音频比更稳；')
    print('  选包看 `python scripts\\theme_pack.py --list-themes`，与本曲的调式/编制对齐。')

    print('\n⚠ 判据：**风格距离 > 60 的别拿来比**（那是另一种编制，差异来自风格不是好坏）。')
    print('   若 ① 里最接近的距离仍 > 40（说明库里**没有同风格参考**）→ 别硬比，')
    print('   改用"绝对指标自检"（响度/宽度/无削波/时值/音区间距/撞音）+ ②③ 分层参考。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
