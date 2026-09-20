# -*- coding: utf-8 -*-
"""和弦音组重排：**保音级完整**地搬进钢琴中音区（修"开放排列丢了音级"）。

为什么必须修（现场证据）：手推"开放排列"时我把和弦音逐个下移 12/24，
结果 **B7 少了三音 D#、Am7 少了 A、F#m7 少了 A**（音级集合不完整）。
后果是旋律里本来合法的和弦音在强拍上被判成"经过音"（`melody_health` FAIL）。

规则（保住音级 + 少改音区）：
  ① 原音组每个音先按 12 的倍数搬进 [40, 64]（钢琴中音区，Piano 轨再整体 −12）
  ② **每个音级（%12）都必须至少留一个音** —— 缺了就补、多了就把最靠近别人的挪开
  ③ 同音级重复但不完全同音高是可接受的（钢琴八度加厚）；**同音高才算错**
"""
import io
import json
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))

P = sys.argv[1] if len(sys.argv) > 1 else r'songs\20_piano_rain\song.json'
NEW = {
    'B7':   [35, [47, 51, 54, 57]],
    'Esus4': [28, [40, 45, 47, 52]],
    'Am7':  [33, [45, 48, 52, 55]],
    'Am':   [33, [45, 48, 52, 57]],
    'Em7':  [28, [40, 43, 47, 50]],
    'A6':   [33, [45, 49, 52, 54]],
    'D6':   [38, [50, 54, 57, 59]],
    'F#m7': [30, [42, 45, 49, 52]],
    'C#m7': [37, [49, 52, 56, 59]],
    'Bm6':  [35, [47, 50, 54, 56]],
}
d = json.load(io.open(P, encoding='utf-8'))
old = d['chords']
# **基准 = 原始生成的和弦定义**（`new_song.py --theme tender` 的输出，音级完整），
# ⚠ 不能拿磁盘上的当前值当基准 —— 当前值就是我手推开放排列时丢过音级的那一版，
#   拿它比对等于循环论证（第一次跑就是这么被判"音级变了"的，两边的音级都不全）。
ORIG = {
    'B7':   [35, [59, 63, 66, 69]],
    'Esus4': [28, [64, 69, 71, 76]],
    'Am7':  [33, [57, 60, 64, 67]],
    'Am':   [33, [57, 60, 64]],
    'Em7':  [28, [64, 67, 71, 74]],
    'A6':   [33, [57, 61, 64, 66]],
    'D6':   [38, [62, 66, 69, 71]],
    'F#m7': [30, [66, 69, 73, 76]],
    'C#m7': [37, [61, 64, 68, 71]],
    'Bm6':  [35, [59, 62, 66, 68]],
}
bad = []
for nm, (b, ts) in NEW.items():
    o = ORIG[nm][1]
    pcs_old = sorted({t % 12 for t in o})
    pcs_new = sorted({t % 12 for t in ts})
    if pcs_old != pcs_new:
        bad.append('%s 音级变了：%s → %s' % (nm, pcs_old, pcs_new))
    if len(set(ts)) != len(ts):
        bad.append('%s 有同音高重复：%s' % (nm, ts))
    if not all(40 <= t <= 66 for t in ts):
        bad.append('%s 越出中音区：%s' % (nm, ts))
if bad:
    print('校验失败：')
    for x in bad:
        print('  ' + x)
    sys.exit(1)
d['chords'] = {nm: [b, ts] for nm, (b, ts) in NEW.items()}
json.dump(d, io.open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('OK：%d 个和弦已重排（音级完整、无同音高重复、落在 40-66）' % len(NEW))
for nm, (b, ts) in NEW.items():
    print('  %-7s 低音 %-3d 音组 %s' % (nm, b, ts))
