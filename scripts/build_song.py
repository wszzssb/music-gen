#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_song.py —— 紧凑 spec → 完整 `song.json`（**写歌省 60~70% token**）

为什么需要它（实测教训）：手写 `song.json` 时，一半以上的字是**机器能推的**：
和弦的每个音（排列）、每个旋律音的时值、段落编制、段落混音曲线。上一首 72 小节的歌
`notes.md`+`song.json` 加起来 ≈2100 token，其中真正属于"创作决定"的不到 700。

spec 里**只写决定**，其余全部推导：

```jsonc
{
 "name": "17_x", "bpm": 72.8, "style": "ballad",
 "sections": [                      // 或 "sections_from": "songs/17_b73_slow_evening" 直接复用段落结构
   {"name":"A", "bars":8, "chords":"Fm Cm7 Ab Db Fm Cm7 Abm7 C7",
    "melody":{"m":[[0,0,77],[0,1,75],[0,2,72]]},   // [小节, 拍, 音高] —— **时值自动推**
    // 要显式给时值就写 [小节, 拍, 音高, 时值]（第 4 列），如 [0,0,77,2] = 2 拍长
    "arr":{"strings":true}, "mix":{"Strings":66}}
 ],
 "chords_used": "auto"
}
```

推导规则（都有测试兜着，见 `--selftest`）：
- **和弦排列**：由符号推出（音级精确等于符号、低音放低八度）—— 不用手写 `[41,[53,56,60,65,68]]`
- **旋律时值**：由"到下一个音的间距"推（最后一个音到小节末）；也可显式写 `[小节,拍,时值,音高]`
- **段落编制**：`style` 预设打底 + spec 里显式 `arr` 覆盖
- **段落混音**：spec 的 `mix`（单整数）直通 `arr.mix`
- **和弦表**：只收集实际用到的（`chords_used: auto`）

用法:
  python scripts\\build_song.py <spec.json> [--out songs/<曲名>] [--lo 55] [--dry-run]
  python scripts\\build_song.py --selftest            # 自测推导规则（不动磁盘）
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()

import json_io                                 # noqa: E402
import selftest as st                          # noqa: E402  # 和弦符号解析的唯一口径
import song_engine                             # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# ---------------------------------------------------------------- 推导
def voicing(sym, lo=55, bass_floor=28):
    """和弦符号 → [低音, [排列]]（音级**精确**等于符号；斜杠和弦的 '6/9' 不是斜杠）"""
    base, slash = sym, None
    if '/' in sym and not sym.endswith('6/9'):
        base, sl = sym.split('/', 1)
        slash = st.NOTE_PC.get(sl)
    r, _, want = st.parse_chord(base)
    if want is None or r is None:
        raise SystemExit('和弦符号解析不了: %r' % sym)
    notes, prev = [], lo - 1
    for pc in sorted(want, key=lambda x: (x - r) % 12):
        n = lo + ((pc - lo) % 12)
        while n <= prev:
            n += 12
        notes.append(n)
        prev = n
    bpc = slash if slash is not None else r
    bass = bass_floor + ((bpc - bass_floor) % 12)
    if bass > bass_floor + 12:
        bass -= 12
    return [bass, notes]


def melody_from_spec(events, bars, default_dur=None):
    """`[[小节, 拍, 音高], ...]` → `[[小节, 拍, 时值, 音高], ...]`

    **时值自动推**：到下一个音的间距；小节内最后一个音延到下一个小节首音，末尾音延到小节末。
    这是手写时最容易写错、也最占字的部分。
    要显式指定时值就写**4 元**：`[小节, 拍, 时值, 音高]`（与 song.json 同列序，如 `[0,0,2,77]`）。
    """
    ev = []
    for e in events:
        if len(e) >= 4:                      # 显式：[小节, 拍, 时值, 音高]
            ev.append([int(e[0]), float(e[1]), float(e[2]), int(e[3])])
        else:                                # [小节, 拍, 音高]：时值待推
            ev.append([int(e[0]), float(e[1]), None, int(e[2])])
    ev.sort(key=lambda x: (x[0], x[1]))
    for i, it in enumerate(ev):
        if it[2] is not None:
            continue
        b, beat = it[0], it[1]
        if i + 1 < len(ev):
            nb, nbeat = ev[i + 1][0], ev[i + 1][1]
        else:
            nb, nbeat = bars, 0.0
        dur = (nb - b) * 4.0 + (nbeat - beat)
        it[2] = round(dur if dur > 0 else (default_dur or 1.0), 4)
    return ev


def build(spec, lo=55):
    """spec（dict）→ song.json（dict）"""
    name = spec.get('name') or 'song'
    sections_in = spec.get('sections') or []
    if not sections_in:
        raise SystemExit('spec 里没有 sections')

    # 段落结构可继承现成曲目（省掉重复写 10 段）
    base_sections = []
    if spec.get('sections_from'):
        src = spec['sections_from']
        p = src if os.path.isabs(src) else os.path.join(ROOT, src, 'song.json')
        with open(p, encoding='utf-8-sig') as f:
            src_d = json.load(f)
        base_sections = src_d.get('sections') or []
    by_name = {s['name']: s for s in base_sections}

    chords = {}
    melody = {}
    out_secs = []
    for i, s in enumerate(sections_in):
        nm = s.get('name') or 'S%d' % (i + 1)
        base = dict(by_name.get(nm, {}))
        bars = int(s.get('bars') or base.get('bars') or 8)
        # chords: "Fm Cm7 ..." 或 list
        cspec = s.get('chords') or base.get('chords')
        if isinstance(cspec, str):
            clist = cspec.split()
        else:
            clist = list(cspec or [])
        if len(clist) < bars:               # 不足则循环铺满
            clist = [clist[j % len(clist)] for j in range(bars)] if clist else clist
        elif len(clist) > bars:
            clist = clist[:bars]
        if not clist:
            raise SystemExit('段落 %s 没有和弦' % nm)
        for c in clist:
            if c not in chords:
                chords[c] = voicing(c, lo=lo)

        # melody: {名: [[小节,拍,音高], ...]}；同名可复用（变奏）
        mname = s.get('melody_name') or ('m%d' % (i + 1))
        mspec = s.get('melody')
        if isinstance(mspec, dict) and mspec:
            mname, ev = next(iter(mspec.items()))
            if len(mspec) > 1:
                raise SystemExit(
                    '段落 %s 的 melody 给了 %d 个键（%s），只认第一个 —— 键名就是旋律名，'
                    '一段写一支；要复用别段已定义的旋律写成 "melody": "%s"'
                    % (nm, len(mspec), '/'.join(list(mspec)[:4]), mname))
            ev2 = melody_from_spec(ev, bars)
            # ⚠ **同名不同内容 = 静默覆盖**（2026-09-19 实测）：写了 5 段、每段都叫 `m`，
            #   结果只剩最后一段生效（154 个音 → 8 个），而**没有任何提示** —— 正是这个
            #   项目最忌讳的"静默失效"。同名本是"复用"的语义（见上一条注释），给不同内容
            #   就是用法冲突，直接报出来。
            if mname in melody and melody[mname] != ev2:
                raise SystemExit(
                    '旋律名 %r 在第 %s 段被重新赋值（前面已有同名、内容不同）—— 同名会覆盖。'
                    '每段要不同旋律请用不同键名（**段名最直观**），'
                    '要复用同一支请写 "melody": %r' % (mname, nm, mname))
            melody[mname] = ev2
        elif isinstance(mspec, str):
            mname = mspec                     # 复用已有旋律名
        elif base.get('melody'):
            mname = base['melody']            # 从基段落继承

        # 编制默认值：**按 style 预设里可用的轨道推导**（踩过的坑：spec 不写 arr 时
        # 引擎会"跳过空轨"→ Bass 根本没生成，低频缺 24~31dB，而成绩单只会说"提高 mix.Bass"）
        arr = {'bass': True, 'perc': 1, 'piano': True, 'uku': True}
        st_name = spec.get('style')
        if st_name:
            import song_engine as _se
            progs = (_se.STYLES.get(st_name) or {}).get('programs') or {}
            for tr, key in (('Bass', 'bass'), ('Perc', 'perc'), ('Piano', 'piano'),
                            ('Hook', 'uku'), ('Arp', 'arp'), ('Pad', 'pad'),
                            ('Strings', 'strings'), ('Glock', 'glock'), ('EP', 'ep')):
                if tr in progs:
                    arr[key] = 1 if key == 'perc' else True
        arr.update(base.get('arr') or {})
        arr.update(s.get('arr') or {})
        if s.get('mix'):
            arr['mix'] = dict(s['mix'])
        arr.setdefault('vel', float(s.get('vel', base.get('arr', {}).get('vel', 1.0))))
        sec = {'name': nm, 'bars': bars, 'chords': clist, 'melody': mname, 'arr': arr}
        if s.get('melody_extra') or base.get('melody_extra'):
            sec['melody_extra'] = s.get('melody_extra') or base.get('melody_extra')
        out_secs.append(sec)

    d = {'name': name, 'bpm': float(spec.get('bpm') or 120.0),
         'desc': spec.get('desc') or '',
         'chords': chords, 'melody': melody, 'sections': out_secs}
    for k in ('style', 'patterns', 'programs', 'mix', 'ref'):
        if spec.get(k) is not None:
            d[k] = spec[k]
    return d


# ---------------------------------------------------------------- 自测
def to_spec(song, dest=None):
    """`song.json`（dict 或路径）→ 紧凑 spec（**改歌/复用**用）

    用途：想改一首现成的歌时，不必重打整份 song.json —— 导出 spec、改几个字段、
    `build_song.py` 再生成。也当"格式互转"的回归测试（`build(to_spec(x))` 应与 x 等价）。
    """
    if isinstance(song, str):
        with open(song, encoding='utf-8-sig') as f:
            song = json.load(f)
    used = set()
    secs = []
    mmap = {}                       # 旋律数组 → 统一名，去掉重复定义
    for s in song.get('sections', []):
        for c in s.get('chords', []):
            used.add(c)
        mn = s.get('melody')
        arr = song.get('melody', {}).get(mn)
        mel = {}
        if arr is not None:
            key = json.dumps(arr, sort_keys=True)
            if key in mmap:
                mel = mmap[key]                       # 同一份旋律只写一次
            else:
                mmap[key] = mn
                mel = {mn: arr}
        item = {'name': s['name'], 'bars': s['bars'],
                'chords': ' '.join(s.get('chords', []))}
        if mel:
            item['melody'] = mel
        elif arr is not None:
            item['melody'] = song['melody'].get(mn) and mn or mn
        arr_cfg = dict(s.get('arr') or {})
        if s.get('melody_extra'):
            item['melody_extra'] = s['melody_extra']
        if 'vel' in arr_cfg:
            item['vel'] = arr_cfg.pop('vel')
        if 'mix' in arr_cfg:
            item['mix'] = arr_cfg.pop('mix')
        if arr_cfg:
            item['arr'] = arr_cfg
        secs.append(item)
    spec = {'name': song.get('name'), 'bpm': song.get('bpm'),
            'desc': song.get('desc', ''), 'sections': secs}
    for k2 in ('style', 'ref', 'patterns', 'programs', 'mix'):
        if song.get(k2) is not None:
            spec[k2] = song[k2]
    spec['chords_used'] = sorted(used)
    return spec



def selftest():
    """推导规则自测（不动磁盘、不依赖任何歌曲）"""
    ok = 0

    def eq(got, want, label):
        nonlocal ok
        if got != want:
            raise AssertionError('%s: %r != %r' % (label, got, want))
        ok += 1

    v = voicing('Fm')
    eq(sorted(t % 12 for t in v[1]), [0, 5, 8], 'Fm 音级')
    eq(v[0] % 12, 5, 'Fm 低音 = F')
    v = voicing('Eb/G')
    eq(v[0] % 12, 7, 'Eb/G 低音 = G')
    eq(sorted(t % 12 for t in v[1]), [3, 7, 10], 'Eb/G 音级')

    ev = melody_from_spec([[0, 0, 72], [0, 2, 74], [1, 0, 75]], 2)      # 3 元：时值自动推
    eq([e[2] for e in ev], [2.0, 2.0, 4.0], '时值推导（含跨小节）')
    eq([e[3] for e in ev], [72, 74, 75], '音高列')
    ev = melody_from_spec([[0, 0, 72], [0, 1.5, 74], [0, 3, 76]], 1)
    eq([e[2] for e in ev], [1.5, 1.5, 1.0], '时值推导（小节末收尾）')
    ev = melody_from_spec([[0, 0, 2, 77]], 1)                            # 4 元：显式时值
    eq([e[2] for e in ev], [2.0], '显式时值')
    eq([e[3] for e in ev], [77], '显式形式的音高在**第 4 列**（与 song.json 同列序）')

    d = build({'name': 't', 'bpm': 100, 'sections': [
        {'name': 'A', 'bars': 4, 'chords': 'Fm Cm7', 'melody': {'m': [[0, 0, 72]]}},
    ]})
    eq(d['sections'][0]['chords'], ['Fm', 'Cm7', 'Fm', 'Cm7'], '和弦循环铺满')
    eq(sorted(d['chords']), ['Cm7', 'Fm'], '和弦表只收用到的')
    eq(d['sections'][0]['bars'], 4, '小节数')
    print('build_song 自测: %d 项通过' % ok)
    return 0


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--selftest' in sys.argv:
        return selftest()
    if '--to-spec' in sys.argv:
        print(json.dumps(to_spec(sys.argv[sys.argv.index('--to-spec') + 1]),
                         ensure_ascii=False, indent=1))
        return 0
    if not args:
        print(__doc__)
        return 1
    spec_path = args[0]
    if not os.path.exists(spec_path):
        print('找不到 spec: %s' % spec_path)
        return 1
    # utf-8-sig：PowerShell 的 Set-Content -Encoding UTF8 会写 BOM，
    # 用 'utf-8' 读会 json.JSONDecodeError（Unexpected UTF-8 BOM）—— 实测踩过
    with open(spec_path, encoding='utf-8-sig') as f:
        spec = json.load(f)
    lo = int(sys.argv[sys.argv.index('--lo') + 1]) if '--lo' in sys.argv else 55
    d = build(spec, lo=lo)

    if '--dry-run' in sys.argv:
        print(json_io.dumps(d))
        return 0

    out = (sys.argv[sys.argv.index('--out') + 1] if '--out' in sys.argv
           else spec.get('out') or os.path.join(ROOT, 'songs', d['name']))
    if not os.path.isabs(out):
        out = os.path.join(ROOT, out)
    os.makedirs(out, exist_ok=True)
    sp = os.path.join(out, 'song.json')
    old = open(sp, encoding='utf-8').read() if os.path.exists(sp) else None
    json_io.save(sp, d)

    # 干跑一次引擎：编配能不能跑通（写 MIDI 到临时目录，不留垃圾）
    import tempfile
    with tempfile.TemporaryDirectory(prefix='build_') as td:
        try:
            song_engine.compose(sp, out_mid=os.path.join(td, 'x.mid'))
        except Exception as e:                              # noqa: BLE001
            if old is not None:
                open(sp, 'w', encoding='utf-8').write(old)
            print('✗ 生成的 song.json 编配失败（已回滚）: %s: %s' % (type(e).__name__, e))
            return 1
    # 补齐脚手架（compose.py / render.json）：**复用 new_song.py 的现成实现**，
    # 免得两处各写一份"渲染参数怎么推"（那种重复早晚漂移）。
    try:
        import new_song as _ns
        stub = os.path.join(out, 'compose.py')
        if not os.path.exists(stub):
            with open(stub, 'w', encoding='utf-8', newline='') as f:
                f.write(_ns.COMPOSE_STUB % {'name': d['name']})
        cfgp = os.path.join(out, 'render.json')
        if not os.path.exists(cfgp):
            ref = None
            if d.get('ref'):
                rp = os.path.join(ROOT, 'refs', d['ref'] + '.json')
                if os.path.exists(rp):
                    with open(rp, encoding='utf-8') as f:
                        ref = json.load(f)
            cfg = {'composer': 'compose.py', 'mid': d['name'] + '.mid',
                   'out': d['name'] + '_sf', 'ref': d.get('ref') or ''}
            if ref:
                cfg.update(_ns.auto_render_params(ref))
            with open(cfgp, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=1)
        print('  脚手架: compose.py%s' % (' + render.json' if os.path.exists(cfgp) else ''))
    except Exception as e:                                   # noqa: BLE001
        print('  （脚手架补全跳过: %s: %s）' % (type(e).__name__, e))

    # **把 spec 落到曲目目录**（跟着歌走，不散落在项目根）：
    # 踩过的坑：spec 留在根目录、又手工编辑过，结果它与 song.json 悄悄漂移
    # （旋律 +7 时值、和弦整八度差），复现命令全失效。自检 t_song_spec_sync 会验证往返一致。
    spec_path = os.path.join(out, 'spec.json')
    with open(spec_path, 'w', encoding='utf-8') as f:
        json.dump(spec, f, ensure_ascii=False, indent=1)

    # **notes.md**（2026-09-19 补）：spec 路径原来不写 notes，于是照 INSTALL「五分钟出第一首」
    # 做完第一首，自检的 `notes_present` 立刻报红 —— 新手会以为自己做错了。
    # 依据如实写成 **spec.json**（**不是**主题模板包，那是 `new_song.py --theme` 的路径），
    # 免得两条路混用（README §1 顶部"三条路径"的分叉表就是这个口径）。
    try:
        _secs = d.get('sections') or []
        _nl = ['# %s（spec 路径：直接作曲）\n' % os.path.basename(out),
               '| 项目 | 值 |', '|---|---|',
               '| 依据 | **`spec.json`** —— 手写"和弦走向 + 旋律骨架"，'
               '时值/排列/编制由 `build_song.py` 推导 |',
               '| 风格 | %s |' % (d.get('style') or '-'),
               '| 速度·拍号 | %s BPM · %s |' % (d.get('bpm'), d.get('meter') or [4, 4]),
               '| 段落 | %s（共 %d 小节） |'
               % (' / '.join('%s %s小节' % (s.get('name'), s.get('bars')) for s in _secs),
                  sum(s.get('bars') or 0 for s in _secs)),
               '| 首段和弦 | %s |'
               % (' '.join((_secs[0].get('chords') or [])[:8]) if _secs else '-'),
               '| 混音目标 | %s（**只用于混音对标，不是模板依据**） |' % (spec.get('ref') or '-'),
               '', '## 复现', '', '```powershell',
               r'$py = "<工具链根>\.venv\Scripts\python.exe"',
               'cd <工具链根>',
               # 用**曲目目录里那份** spec 复现（spec 跟着歌走，见上面那段注释）
               r'& $py scripts\build_song.py songs\%s\spec.json --out songs\%s'
               % (os.path.basename(out), os.path.basename(out)),
               r'& $py scripts\make_song.py %s' % os.path.basename(out), '```', '',
               '## 还没验证什么', '',
               '- 未渲染/未对齐 —— 跑 `make_song.py` 才有成绩单',
               '- **模板依据**：这条路径**不引用主题模板包**（依据 = 你手写的 spec）。'
               '要走"同主题 ≥8 首模板聚合"的合规路径请用 `new_song.py --theme`，'
               '见 `docs/THEME-PACK.md`']
        with open(os.path.join(out, 'notes.md'), 'w', encoding='utf-8', newline='') as f:
            f.write('\n'.join(_nl) + '\n')
    except Exception as _e:                                  # noqa: BLE001
        print('  （notes.md 未生成: %s: %s）' % (type(_e).__name__, _e))

    spec_tok = len(json.dumps(spec, ensure_ascii=False))
    full_tok = len(json_io.dumps(d))
    print('✓ %s' % sp)
    print('  spec %d 字 → song.json %d 字（省 %.0f%%）' % (
        spec_tok, full_tok, 100 * (1 - spec_tok / max(1, full_tok))))
    print('  下一步: python scripts\\check_song.py %s  →  make_song.py %s'
          % (os.path.basename(out), os.path.basename(out)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
