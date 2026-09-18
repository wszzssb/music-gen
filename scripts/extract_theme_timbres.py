# -*- coding: utf-8 -*-
"""从主题包的 8+ 首模板 MIDI 里提取**实际音色与配器**—— 直接写音乐的音色依据。

## 为什么需要

`new_song` 现在只套 `song_engine.STYLES[engine_style]` 里那**一套** `programs`
（5 个风格各一套），而同主题有 **8~10 首真实模板**、每首**自带一套完整配器**。

用户的判据是"**乐器选择**像不像"，所以依据该来自模板 —— 而且**不是"每个声部取一个众数音色"**
（那样会得到一个谁都不像的平均值），而是**整首一套配器**。

## 做法

① 读主题包 `refs/themes/<theme>.json` 的 `templates[].file`
② 每首模板 MIDI（`refs/midi2/<file>`）解析出各轨的**轨名 + program number**
③ 归类声部 —— **轨名优先，音域兜底**，且有两条硬规矩（都是踩出来的）：
   · `drum` 规则必须排在 `bass` 之前，否则 `BassDrum` 被判成贝斯
   · `Bassoon`/`Bass Clarinet` 含 "bass" —— 只给 Bass 规则看"摘掉巴松"的串，
     其它规则看原串（否则巴松既不是贝斯、也认不出是木管）
   · **音域兜底只在"整首曲子没有任何可识别轨名"时启用** —— 否则纯钢琴曲的
     左手低音区会被凭空判成"贝斯声部"，pool 里就混进一堆根本不是贝斯的音色
④ 输出两层：
   · `roles`/`pool`：每个声部的音色分布（`pool` 按频次排序，引擎直接取用）
   · `arrangements`：**每首模板一套配器**（声部 → program）

## 用法

```bash
python scripts/extract_theme_timbres.py daily              # 看这个主题
python scripts/extract_theme_timbres.py --all               # 所有主题（跳过 *_melody）
python scripts/extract_theme_timbres.py --all --write       # 写 refs/timbres/<theme>.json
python scripts/extract_theme_timbres.py daily --arrange     # 只列配器方案
```

⚠ 模板是**别人写的 MIDI**，program 不可全信（GM 下常被随手写），
所以输出的是"分布 + 整套方案"，由调用方决定怎么取。
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import midi_file                                              # noqa: E402

# GM 音色全表（0-based，0-95；96-127 是音效/民族/打击/特效，不算配器）
_GM_NAMES = """钢琴 亮钢琴 电钢琴2 酒吧钢琴 电钢 电钢2 大键琴 击弦古琴 钢片琴 钟琴
八音盒 颤音琴 马林巴 木琴 管钟 扬琴 拉杆风琴 打击风琴 摇滚风琴 教堂管风琴
簧风琴 手风琴 口琴 探戈手风琴 尼龙吉他 钢弦吉他 爵士电吉他 清音电吉他 闷音电吉他 过载吉他
失真吉他 吉他泛音 原声贝斯 指弹电贝斯 拨片电贝斯 无品贝斯 击弦贝斯1 击弦贝斯2 合成贝斯1 合成贝斯2
小提琴 中提琴 大提琴 低音提琴 颤弓弦乐 拨弦弦乐 竖琴 定音鼓 弦乐合奏1 弦乐合奏2
合成弦乐1 合成弦乐2 人声啊 人声哦 合成人声 管弦乐齐奏 小号 长号 大号 弱音小号
圆号 铜管乐组 合成铜管1 合成铜管2 高音萨克斯 中音萨克斯 次中音萨克斯 上低音萨克斯 双簧管 英国管
巴松 单簧管 短笛 长笛 竖笛 排箫 吹瓶 尺八 哨子 陶笛
方波主音 锯齿主音 汽笛风琴主音 竹笛主音 主音吉他 人声主音 五度主音 贝斯主音 新世纪Pad 暖Pad
复合成Pad 合唱Pad 弓弦Pad 金属Pad 光环Pad 扫掠Pad""".split()
assert len(_GM_NAMES) == 96, 'GM 表应是 96 项，实际 %d' % len(_GM_NAMES)
GM = {i: n for i, n in enumerate(_GM_NAMES)}

# 轨名关键词 → 引擎声部（**轨名优先**）。顺序有意义：先匹配到的赢
NAME_RULES = [
    (r'drum|perc|percussion|鼓|打击|kick|snare|hi.?hat|cymbal|taiko|timpani', 'Perc'),
    (r'bass|贝斯|低音', 'Bass'),
    (r'string|弦乐|violin|viola|cello|ensemble|orch', 'Strings'),
    (r'pad|synth.*pad|atmos|氛围', 'Pad'),
    (r'arp|琶音', 'Arp'),
    (r'glock|bell|celesta|music.?box|chime|铃声|钟琴|八音', 'Glock'),
    (r'guitar|吉他|gt\b|gtr', 'Guitar'),
    (r'brass|trumpet|horn|trombone|tuba|sax|铜管|小号|圆号|长号|萨克斯', 'Brass'),
    (r'flute|oboe|clarinet|bassoon|basson|recorder|whistle|管|笛|萧|尺八', 'Woodwind'),
    (r'choir|vocal|voice|aah|ooh|人声|合唱', 'Choir'),
    (r'melod|lead|solo|主旋|主奏|旋律|hook|theme', 'Melody'),
    (r'piano|pno|keys|keyboard|organ|harpsichord|clav|钢琴|键盘', 'Piano'),
]

ROLE_ORDER = ['Melody', 'Piano', 'Hook', 'Strings', 'Guitar', 'Brass', 'Woodwind',
              'Choir', 'Glock', 'Arp', 'Pad', 'Bass', 'Perc']

# 排除词：`Bassoon`（巴松 70）、`Bass Clarinet`（低音单簧管 71）都含 "bass" ——
# 实测不摘掉会把 classic 的贝斯众数判成"巴松"，整个音色依据就错了。
# ⚠ 只给 Bass 规则用清理后的串：全串清理会让 Woodwind 也认不出 bassoon
_EXCL = re.compile(r'bassoon|basson|bass\s*clarinet|basscl|bsn|fagott')


def gname(p):
    return '%d %s' % (p, GM.get(p, '音效%d' % p))


def role_by_name(tr):
    """只按轨名判声部。认不出返回 None。"""
    nm = (tr.get('name') or '').lower()
    if not nm:
        return None
    nm_nb = _EXCL.sub(' ', nm)
    for pat, role in NAME_RULES:
        if re.search(pat, nm_nb if role == 'Bass' else nm):
            return role
    return None


def role_by_range(notes):
    """按音域兜底 —— 只在整首曲子没有任何可识别轨名时才用它。"""
    if not notes:
        return None
    ps = [it[2] for it in notes]
    lo, hi = min(ps), max(ps)
    if hi <= 52:
        return 'Bass'
    if lo >= 64 and len(notes) <= 400:
        return 'Melody'
    if (lo + hi) / 2 < 60:
        return 'Piano'
    return 'Hook'


def prog_of(tr):
    p = tr.get('program')
    if p is not None:
        return int(p)
    pcs = tr.get('program_changes') or []
    return int(pcs[0][1]) if pcs else None


def tpl_file(t):
    """模板条目：兼容 dict 与纯字符串两种写法（*_melody 包是 str）。"""
    if isinstance(t, str):
        return t
    if isinstance(t, dict):
        return t.get('file') or t.get('path') or t.get('midi')
    return None


def scan_pack(theme, verbose=False):
    tf = os.path.join(ROOT, 'refs', 'themes', '%s.json' % theme)
    if not os.path.exists(tf):
        return None
    pack = json.load(open(tf, encoding='utf-8'))
    roles, arrs, used = {}, [], 0
    for t in pack.get('templates') or []:
        f = tpl_file(t)
        if not f:
            continue
        p = os.path.join(ROOT, 'refs', 'midi2', f)
        if not os.path.exists(p):
            continue
        try:
            d = midi_file.import_midi(p)
        except Exception as e:
            print('  ! 读不了 %s: %s' % (f, e))
            continue
        used += 1
        trs = d.get('tracks') or []
        # 第一遍：轨名能认出几个声部？全认不出才允许音域兜底（见模块 docstring ③）
        named = [role_by_name(tr) for tr in trs]
        any_named = any(named)
        inst, nperc, nother = {}, 0, 0
        for tr, nm_role in zip(trs, named):
            notes = tr.get('notes') or []
            if not notes:
                continue
            role = nm_role or (role_by_range(notes) if not any_named else None)
            if not role:
                nother += 1
                continue
            if role == 'Perc':          # 鼓组不吃 program，只数"有几首带鼓"
                nperc += 1
                continue
            pg = prog_of(tr)
            if pg is None:
                pg = 0
            if not (0 <= pg <= 95):     # 96-127 = 音效/民族/打击/特效，非 BGM 配器
                roles.setdefault('FX', Counter())[pg] += 1
                continue
            roles.setdefault(role, Counter())[pg] += 1
            # 同声部多轨时保留音符更多的那条
            if role not in inst or len(notes) > inst[role][1]:
                inst[role] = (pg, len(notes))
        if verbose and nother:
            print('    (%s 有 %d 轨轨名认不出，未计入)' % (os.path.basename(f), nother))
        arrs.append({
            'file': f,
            'style': (t.get('style') if isinstance(t, dict) else None),
            'bpm': (t.get('bpm') if isinstance(t, dict) else None),
            'byname': bool(any_named),
            'perc_tracks': nperc,
            'inst': {r: v[0] for r, v in inst.items()},
        })
    return {'theme': theme, 'label': pack.get('label') or pack.get('theme') or theme,
            'engine_style': pack.get('engine_style'),
            'template_count': pack.get('template_count'), 'scanned': used,
            'roles': roles, 'arrangements': arrs}


def report(r, show_arr=True, verbose=False):
    print('=== %s（%s）· 模板 %s 首 · 读到 %d 首 · 引擎预设 %s'
          % (r['theme'], r['label'], r['template_count'], r['scanned'],
             r['engine_style']))
    nperc = sum(1 for a in r['arrangements'] if a.get('perc_tracks'))
    nby = sum(1 for a in r['arrangements'] if a.get('byname'))
    print('  鼓轨 %d/%d 首有 · 轨名可识别 %d/%d 首'
          % (nperc, len(r['arrangements']), nby, len(r['arrangements'])))
    for role in ROLE_ORDER:
        c = r['roles'].get(role)
        if not c:
            continue
        tot = sum(c.values())
        top = c.most_common(5)
        print('  %-9s 样本 %2d · 众数 %-12s %2.0f%% · 其它 %s'
              % (role, tot, gname(top[0][0]), 100.0 * top[0][1] / tot,
                 ' / '.join(gname(k) for k, _v in top[1:4]) or '—'))
    if verbose:
        print('  详情见 --arrange')


# ---------------------------------------------------------------- 注入包
def inject_pool(theme, dry=False, verbose=False):
    """把"每个声部实际用什么音色"注入 `refs/themes/<theme>.json` 的 `arrangement.prog_pool`

    ⚠ **为什么不重跑 `theme_pack.py`**：`mix_target.energy_gain` 与 `calibration` 是
    **标定流程**写进 pack 的（`theme_pack.py:1437` 明确"不自动写"），重跑会整包重建、
    把标定值冲掉。所以这里**只改 `arrangement.prog_pool` 一个字段**，其余原样写回。

    ⚠ 口径**复用 `theme_pack` 自己的** `role_of_program` + `ROLE_TO_ARR` —— 音色池要跟
    `arr_share` 用同一套角色判据；两套口径一定会漂移。轨名只用于诊断（见 `scan_pack`）。

    每首模板**一票**（取该声部音符最多的那条轨）—— 同 `theme_pack` 的"每首等权"原则，
    免得一首带 5 条弦乐轨的曲子压过其它 9 首。
    """
    import theme_pack as tp
    p = os.path.join(ROOT, 'refs', 'themes', '%s.json' % theme)
    if not os.path.exists(p):
        return None
    pack = json.load(open(p, encoding='utf-8'))
    ct = defaultdict(Counter)
    used = 0
    for t in pack.get('templates') or []:
        f = tpl_file(t)
        if not f:
            continue
        mp = os.path.join(ROOT, 'refs', 'midi2', f)
        if not os.path.exists(mp):
            continue
        try:
            d = midi_file.import_midi(mp)
        except Exception as e:
            print('  ! 读不了 %s: %s' % (f, e)); continue
        used += 1
        per = defaultdict(list)
        for tr in d.get('tracks') or []:
            notes = tr.get('notes') or []
            if not notes or (tr.get('channel') or 0) == 9:
                continue
            arr = tp.ROLE_TO_ARR.get(tp.role_of_program(tr.get('program')))
            if not arr:
                continue
            per[arr].append((int(tr.get('program') or 0), len(notes)))
        for arr, lst in per.items():
            ct[arr][sorted(lst, key=lambda x: -x[1])[0][0]] += 1
    pool = {k: [pr for pr, _n in c.most_common()] for k, c in ct.items()}
    old = (pack.get('arrangement') or {}).get('prog_pool')
    changed = (old != pool)
    print('  %-10s %2d 首模板 → prog_pool %s'
          % (theme, used, '**有变化**' if changed else '无变化'))
    if verbose or changed:
        for k in sorted(pool):
            print('    %-8s %-28s 票数 %s'
                  % (k, ','.join(GM.get(x, str(x)) for x in pool[k][:5]),
                     [ct[k][x] for x in pool[k][:5]]))
    if not dry and changed:
        pack.setdefault('arrangement', {})['prog_pool'] = pool
        # ⚠ `newline='\n'`：Windows 上 json.dump 默认写 CRLF → 每次改写整文件 diff
        with open(p, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(pack, f, ensure_ascii=False, indent=1)
    return pool


def main():
    import cli_utf8 as _cu; _cu.setup()
    ap = argparse.ArgumentParser(description='从主题包模板提取实际音色与配器')
    ap.add_argument('theme', nargs='?', help='主题名（如 daily）')
    ap.add_argument('--all', action='store_true', help='扫全部主题')
    ap.add_argument('--melody', action='store_true',
                    help='连 *_melody 包一起扫（默认跳过：与主包是同一批模板的重复视图）')
    ap.add_argument('--arrange', action='store_true', help='列出每首模板的配器方案')
    ap.add_argument('--verbose', action='store_true', help='含"轨名认不出"的明细')
    ap.add_argument('--inject', action='store_true',
                    help='把 prog_pool 注入 refs/themes/<theme>.json（只改这一个字段）')
    ap.add_argument('--dry', action='store_true', help='--inject 时只打印不落盘')
    a = ap.parse_args()
    themes = []
    if a.all:
        themes = sorted(os.path.basename(p)[:-5] for p in
                        glob.glob(os.path.join(ROOT, 'refs', 'themes', '*.json')))
        if not a.melody:                       # 实测 *_melody 与主包内容完全相同
            themes = [t for t in themes if not t.endswith('_melody')]
    elif a.theme:
        themes = [a.theme]
    else:
        raise SystemExit('给一个主题名，或 --all')
    out = {}
    for th in themes:
        if a.inject:                 # 注入模式不做诊断扫（省一遍全量解析）
            continue
        r = scan_pack(th, verbose=a.verbose)
        if not r:
            print('  缺主题包 %s' % th); continue
        report(r, show_arr=(a.arrange or not a.all), verbose=a.verbose)
        if a.arrange:
            print('  -- 每首模板的配器（这才是"像"的来源）--')
            for ar in r['arrangements']:
                parts = ['%s=%s' % (ro, GM.get(pg, pg)) for ro, pg in
                         sorted(ar['inst'].items(),
                                key=lambda kv: ROLE_ORDER.index(kv[0])
                                if kv[0] in ROLE_ORDER else 99)]
                print('    %-42s [%-12s] %s'
                      % (os.path.basename(ar['file'])[:42], ar['style'] or '-',
                         ' · '.join(parts) or '(轨名认不出，未计入)'))
        out[th] = {
            'theme': th, 'label': r['label'], 'engine_style': r['engine_style'],
            'scanned': r['scanned'],
            'roles': {ro: dict(c) for ro, c in r['roles'].items()},
            # pool：按出现频次排序的候选音色 —— new_song 直接取用的就是它
            'pool': {ro: [p for p, _n in c.most_common()]
                     for ro, c in r['roles'].items() if ro != 'FX'},
            'arrangements': r['arrangements'],
        }
        print()
    if a.inject:
        print('=== 注入 prog_pool（只改 arrangement.prog_pool 一个字段） ===')
        for th in themes:
            inject_pool(th, dry=a.dry, verbose=a.verbose)
    return 0


if __name__ == '__main__':
    sys.exit(main())
