#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""apply_spec.py —— 把**论文/规格驱动的设计表**落到 song.json（`refs/specs/<主题>.json`）。

与"模板统计"的区别：规格里的每个值都是**有出处的判断**（速度、鼓型、配器、动态弧），
不是从模板 MIDI 统计出来的分布；模板只用来事后交叉验证。

做的事（**逐段应用，不做整曲一刀切**）：
  ① `bpm`（规格给了才覆盖）
  ② `programs`：主奏/铺底/节奏吉他（规格里为 null 的层不碰）
  ③ 每段 `arr`：perc / strings / bass / ep / piano / pad / glock
  ④ `patterns.drum_grid.per_bar`：按规格的 16 格鼓型，**逐段变体**（引子无鼓 / 桥段半速 / 尾奏退场）
  ⑤ 打印逐段改动表（红线：不许静默）

用法：
    python scripts\apply_spec.py songs\133_battle_spec\song.json refs\specs\battle.json [--dry-run]
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cli_utf8 as _cu  # noqa: E402
_cu.setup()
import json_io  # noqa: E402

BANDS = ('kick', 'snare', 'hat', 'open')


def role_of(name):
    base = re.sub(r'\d+$', '', str(name or '')).strip().lower()
    return base or 'a'


def drum_variant(drums, role, meter=(4, 4)):
    """段角色 → 该段的鼓型（16 分格）。引子/尾奏空、桥段半速、副歌底鼓加倍。

    ⚠ **格数随拍号变**（2026-10-19）：4/4 一小节 16 格、**3/4 只有 12 格**。
    写死 8/15 会在圆舞曲里落到下一小节（静默错位）。
    """
    if role in ('intro', 'outro'):
        return {}
    bpb = int(meter[0]) * 4 // int(meter[1])       # 一小节 16 分格数
    out = {b: [list(x) for x in (drums.get(b) or [])] for b in BANDS}
    if role == 'c':                        # 桥段：半速，留出空间
        if bpb >= 16:
            out['kick'] = [[0, 118], [8, 118]]
            out['snare'] = [[8, 116]]
        else:                              # 3/4：只留第 1 拍
            out['kick'] = [[0, 112]]
            out['snare'] = [[4, 84]]
        out['open'] = []
    elif role in ('b', 'chorus'):          # 副歌：底鼓加倍（最后半拍补一记）
        out['kick'] = [list(x) for x in out['kick']] + [[max(0, bpb - 1), 92]]
    return out


def fit_duration(d, spec):
    """**让时长说了算**（2026-10-19 修）：规格改 BPM 会把时长带跑（night 108BPM×72 小节 = 2:44）。

    做法（顺序固定）：
      ① 先**减/加"带数字后缀的重复段"**（A2/A3/B2/C2… 与 outro 之外的段）把**小节数**凑到目标；
         —— 不动 Intro/A/B/C/Outro 的主干，旋律槽复用，不破坏结构
      ② 残余不足 1 小节时，用 **BPM 微调**精确对齐（并夹到 `bpm_range`）
      ③ 打印实得时长与差值（**不许静默给个"看起来对"的时长**）
    返回改动说明列表；spec 没写 `duration_sec` 就原样返回。
    """
    import copy
    dur = float(spec.get('duration_sec') or 0)
    if not dur:
        return []
    bpm0 = float(spec.get('bpm') or d.get('bpm') or 120.0)
    meter = d.get('meter') or [4, 4]
    bpb = float(meter[0]) * 4.0 / float(meter[1])     # 一小节几个四分音符
    secs = d.get('sections') or []
    total = sum(int(s.get('bars') or 0) for s in secs)
    want = dur * bpm0 / (60.0 * bpb)                  # 目标小节数
    log = []

    def _keep(name):
        return re.sub(r'\d+$', '', str(name)).strip().lower()

    g = 0
    while total > want + 0.5 and g < 24:              # ① 减重复段（从后往前）
        g += 1
        cand = [i for i, s in enumerate(secs) if re.search(r'\d+$', str(s.get('name')))]
        if not cand:
            break
        i = cand[-1]
        if total - int(secs[i].get('bars') or 0) < want - 0.5:
            break                    # 再删就短过头了 → 交给 BPM 微调，别来回删加
        total -= int(secs[i].get('bars') or 0)
        log.append('删段 %s（%d 小节）' % (secs[i].get('name'), secs[i].get('bars')))
        secs.pop(i)
    g = 0
    while total < want - 0.5 and g < 24:              # ② 不够就复制最后一段
        g += 1
        cand = [i for i, s in enumerate(secs) if _keep(s.get('name')) not in ('outro',)]
        if not cand:
            break
        i = cand[-1]
        s = copy.deepcopy(secs[i])
        base = re.sub(r'\d+$', '', str(s.get('name')))
        k = 2
        while any(x.get('name') == '%s%d' % (base, k) for x in secs):
            k += 1
        s['name'] = '%s%d' % (base, k)
        secs.insert(i + 1, s)
        total += int(s.get('bars') or 0)
        log.append('加段 %s（复制 %s）' % (s['name'], base))
    rng = spec.get('bpm_range')

    def _bpm(t):
        b = t * bpb * 60.0 / dur
        if rng:
            b = max(float(rng[0]), min(float(rng[1]), b))
        return b

    bpm1 = _bpm(total)
    # ④ **BPM 不许为了凑时长跑太远**（2026-10-19 实测）：sorrow 48 小节把 84 抬到 96（+14%），
    #    "悲伤"就变快了。偏离 >8% 时再删一段（只删重复段），只要新的 BPM 更贴近规格就采用。
    g = 0
    while bpm0 and abs(bpm1 / bpm0 - 1.0) > 0.08 and g < 4:
        g += 1
        cand = [i for i, s in enumerate(secs) if re.search(r'\d+$', str(s.get('name')))]
        if not cand:
            break
        i = cand[-1]
        t2 = total - int(secs[i].get('bars') or 0)
        if t2 < 16:
            break
        b2 = _bpm(t2)
        if abs(b2 / bpm0 - 1.0) >= abs(bpm1 / bpm0 - 1.0):
            break
        log.append('再删段 %s（让 BPM 更贴近规格 %.1f）' % (secs[i].get('name'), bpm0))
        secs.pop(i)
        total, bpm1 = t2, b2
    got = total * bpb * 60.0 / bpm1
    log.append('时段 %d 小节 · BPM %.1f → %.1f · 实得 %.1fs（目标 %ss，差 %+.1fs）'
               % (total, bpm0, bpm1, got, dur, got - dur))
    d['bpm'] = round(bpm1, 1)
    return log


def apply_render_cfg(song_p, spec, dry=False):
    """规格说 `mix_target: none` → 改 **render.json**（ref 的真源）。

    为什么必须连调参结果一起清（2026-10-19 实测）：sorrow 的参考是 8 首真实录音
    （质心 4104），自动调参为了追它把 `shelf` 顶到 **10.0（上限）**、`low` 9.0 ——
    而"悲伤=钢琴+弦乐、无亮色层"本该更暗。参考不是主题性格的判据，**它不该反过来改编配**。
    这里把 ref 置 none、tuned 置 False、EQ 参数回中性、清掉对着旧参考量的 `last_bands`。
    """
    if str(spec.get('mix_target') or '').lower() not in ('none', 'off'):
        return []
    rp = os.path.join(os.path.dirname(os.path.abspath(song_p)), 'render.json')
    if not os.path.isfile(rp):
        return []
    with open(rp, encoding='utf-8') as f:
        rc = json.load(f)
    neutral = {'rms': -16.9, 'width': 2.2, 'shelf': 3.0, 'hp': 38.0,
               'low': 0.0, 'drive': 1.6, 'mid_db': 0.0}
    log = []
    if rc.get('ref') != 'none':
        log.append('render.json ref %s → none' % rc.get('ref'))
        rc['ref'] = 'none'
    if rc.get('tuned'):
        rc['tuned'] = False
        log.append('render.json tuned → False（不追参考，无调参）')
    for k, v in neutral.items():
        if k in rc and rc[k] != v:
            log.append('render %s %s → %s' % (k, rc[k], v))
            rc[k] = v
    if 'last_bands' in rc:
        rc.pop('last_bands')
        log.append('清掉 last_bands（它是对着旧参考量的）')
    if not dry and log:
        with open(rp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(rc, f, ensure_ascii=False, indent=1)
    return log


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) < 2:
        print(__doc__)
        return 2
    song_p, spec_p = a[0], a[1]
    dry = '--dry-run' in a
    d = json_io.load(song_p)
    with open(spec_p, encoding='utf-8') as f:
        spec = json.load(f)

    changes = []
    # **时长优先**：规格给了 `duration_sec` 就由它决定"小节数 + BPM"（见 `fit_duration`）；
    # 没给才退回"直接改 BPM"（会让时长跟着变）。
    fit_log = fit_duration(d, spec)
    changes.extend(fit_log)
    changes.extend(apply_render_cfg(song_p, spec, dry=dry))
    if spec.get('bpm') and not spec.get('duration_sec'):
        if float(d.get('bpm') or 0) != float(spec['bpm']):
            changes.append('bpm %s → %s' % (d.get('bpm'), spec['bpm']))
            d['bpm'] = float(spec['bpm'])

    orch = spec.get('orchestration') or {}
    progs = d.setdefault('programs', {})
    for key, slot in (('lead', 'Melody'), ('bed', 'Strings'), ('rhythm_gtr', 'Hook')):
        o = orch.get(key) or {}
        if o.get('program') is None:
            continue
        old = (progs.get(slot) or [None])[0]
        if old != o['program']:
            changes.append('%s %s → %s（%s）' % (slot, old, o['program'], o.get('name')))
            progs[slot] = [o['program'], (progs.get(slot) or [0, 0])[1]]

    arr = spec.get('arrangement') or {}
    drums = spec.get('drums') or {}
    # **引子只留规格点名的 2 层**（2026-10-19 实测踩到）：规格把 intro 也套了整段编制，
    # 于是第 1 拍同时响 5~7 层（battle: bass+strings+Hook+Melody；night: 钢琴+吉他+弦乐+垫子+
    # 贝斯+主奏）—— 听感就是"一上来全响"，和"渐入"相反。引子层由 `intro_layers` 显式点名。
    intro_layers = set(spec.get('intro_layers') or [])
    pat = d.setdefault('patterns', {})
    per_bar, total = [], 0
    rows = []
    for s in d.get('sections') or []:
        role = role_of(s.get('name'))
        sa = s.setdefault('arr', {})
        for k in ('perc', 'strings', 'bass', 'ep', 'piano', 'pad', 'glock'):
            if k not in arr:
                continue
            v = arr[k]
            if role in ('intro', 'outro') and k == 'perc':
                v = 0                    # 引子/尾奏不放鼓（动态弧）
            if role == 'intro' and intro_layers and k not in intro_layers:
                v = False                # 引子只留点名的层（渐入）
            if sa.get(k) != v:
                changes.append('%s: arr.%s %s → %s' % (s.get('name'), k, sa.get(k), v))
                sa[k] = v
        nb = int(s.get('bars') or 0)
        dv = drum_variant(drums, role, meter=d.get('meter') or (4, 4))
        per_bar.extend([dv] * nb)
        pts = sum(len(v) for v in dv.values())
        rows.append((s.get('name'), nb, sa.get('perc'), pts))
        total += nb

    if per_bar:
        pat['drum_grid'] = {'per_bar': per_bar}
        changes.append('drum_grid.per_bar：%d 小节（逐段变体）' % len(per_bar))

    print('规格 → %s' % os.path.basename(song_p))
    print('  %-9s %3s %4s %5s' % ('段', '小节', 'perc', '鼓点/小节'))
    for nm, nb, pc, pts in rows:
        print('  %-9s %3d %4s %5d' % (nm, nb, pc, pts))
    print('  改动 %d 处：' % len(changes))
    for c in changes:
        print('   ·', c)
    if dry:
        print('  （--dry-run：未写盘）')
        return 0
    json_io.save(song_p, d)
    print('  已写盘：%s' % song_p)
    return 0


if __name__ == '__main__':
    sys.exit(main())
