#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge.py —— BGM Studio（可视化编曲面板）与 music-gen 工具链之间的桥。

面板**不重新实现任何音频逻辑**，这里只补四个出口，全部走工具链自己的模块：
  metrics  读渲染产物 + 参考画像 → 绝对/相对倍频程、占用率、RMS/宽度/质心（复用 metrics.py 口径）
  export   把成品（MIDI/OGG/WAV/notes/song.json）拷到目标目录 —— **多轨合并后的交付**
  stems    逐轨单独渲染成 OGG —— **分轨导出**（合并前的一轨一文件）
  solo     单轨试听（临时 OGG，给面板"听这一轨"按钮）

用法:
  python bridge.py metrics <song.json> [--ref 画像名]
  python bridge.py export  <song.json> --to <目录> [--stems]
  python bridge.py solo    <song.json> --track Melody --out <文件.ogg>
  python bridge.py stems   <song.json> --to <目录>
"""
import argparse
import json
import math
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 路径相对化：bridge 在 studio/ 下，工具链就在父目录；可用 env 覆盖
MG = (os.environ.get('BGM_STUDIO_ROOT')
      or os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')))
sys.path.insert(0, os.path.join(MG, 'scripts'))

import bgm_synth as bs      # noqa: E402
import json_io               # noqa: E402  # 规范写回（mixfit --apply 用）
import metrics              # noqa: E402
import render_midi as rm    # noqa: E402
import song_engine          # noqa: E402
import to_ogg                # noqa: E402

BANDS = [(20, 40), (40, 80), (80, 160), (160, 315), (315, 630), (630, 1250),
         (1250, 2500), (2500, 5000), (5000, 10000), (10000, 18000)]


def slice_audio(path, win, dst):
    """按 a-b 秒切片（用工具链的 soundfile 读、写临时 wav）——"逐段差异"用"""
    import soundfile as sf
    x, sr = sf.read(path, dtype='float32', always_2d=True)
    a, b = int(win[0] * sr), int(win[1] * sr)
    seg = x[max(0, a):min(len(x), b)]
    if len(seg) < sr // 2:
        raise SystemExit('窗口太短或超出音频长度: %s' % (win,))
    sf.write(dst, seg, sr)
    return dst


def band_table(path):
    """绝对 dB / 相对最响带 dB / 占用率 —— 口径与 scripts\bands_abs.py 一致"""
    m, sr, x = metrics.load(path)
    S, f = metrics._avg_spec(m, sr)
    power = {}
    for lo, hi in BANDS:
        k = (f >= lo) & (f < hi)
        power['%d-%d' % (lo, hi)] = float((S[k] ** 2).sum())
    pk = max(max(power.values()), 1e-20)
    out = {'rms': metrics.rms_db(m), 'width': metrics.width(x),
           'centroid': metrics.centroid(m, sr, S, f), 'abs': {}, 'rel': {}, 'occ': {}}
    for lo, hi in BANDS:
        key = '%d-%d' % (lo, hi)
        out['abs'][key] = round(10 * math.log10(max(power[key], 1e-20)), 1)
        out['rel'][key] = round(10 * math.log10(max(power[key], 1e-20) / pk), 1)
        env = metrics._band_env(m, sr, 4096, 1024, float(lo), float(hi))
        out['occ'][key] = metrics.occupancy(env)
    return out


def render_cfg(song_json):
    p = os.path.join(os.path.dirname(os.path.abspath(song_json)), 'render.json')
    return json.load(open(p, encoding='utf-8')) if os.path.isfile(p) else {}


def product_audio(song_json):
    """已渲染的成品（优先 OGG，其次 WAV）；没有 → None"""
    cfg = render_cfg(song_json)
    d = os.path.dirname(os.path.abspath(song_json))
    for ext in ('.ogg', '.wav'):
        p = os.path.join(d, (cfg.get('out') or '') + ext)
        if os.path.isfile(p):
            return p
    return None


def song_tracks(song_json):
    """→ [(轨名, 事件表, 完整 song 数据)]（有音符的轨）"""
    d = song_engine.load(song_json)
    ev, _nb = song_engine.build_events(d)
    return [(tr, notes, d) for tr, notes in ev.items() if notes]


def render_track(d, tr, notes, base, keep_wav=True):
    """按歌曲里真实的力度/CC7/段落自动化，把**单轨**渲染成 OGG"""
    prog, chan = d['programs'][tr]
    pan, vol = d['mix'][tr]
    ccs = [(0.0, 10, pan), (0.0, 7, vol)]
    bar0 = 0
    for sec in d['sections']:
        v = ((sec.get('arr') or {}).get('mix') or {}).get(tr)
        if v is not None:
            ccs.append((bar0 * 4.0, 7, int(v)))
        bar0 += sec['bars']
    bs.BPM = d['bpm']
    bs.write_midi(base + '.mid', [(tr, prog, chan, notes, sorted(ccs))], ppq=480)
    rm.render(base + '.mid', base, rms_db=-16.9, width=1.0, shelf_db=0.0, hp_hz=20.0,
              low_db=0.0, drive=1.0, mid_db=0.0, verbose=False, ogg=True)
    for junk in (['.raw.wav'] if keep_wav else ['.raw.wav', '.wav', '.mid']):
        p = base + junk
        if os.path.isfile(p):
            os.remove(p)
    return base + '.ogg'


def band_power(path):
    """平均谱口径的带内总功率（与 bands_abs / 面板指标完全一致）"""
    m, sr, _x = metrics.load(path)
    S, f = metrics._avg_spec(m, sr)
    out = {}
    for lo, hi in BANDS:
        k = (f >= lo) & (f < hi)
        out['%d-%d' % (lo, hi)] = float((S[k] ** 2).sum())
    return out


def rel_shape(power):
    pk = max(max(power.values()), 1e-20)
    return {k: 10 * math.log10(max(v, 1e-20) / pk) for k, v in power.items()}


def cmd_mixfit(a):
    """按分轨求解每轨 CC7，让整混的绝对倍频程贴住参考曲（--apply 写回 song.json）"""
    import tempfile
    d = song_engine.load(a.song_json)
    cfg = render_cfg(a.song_json)
    ref_name = a.ref or cfg.get('ref') or 'BGM16c'
    # ⚠ 画像查找必须与 `scorecard.ref_path` 同一条路（2026-09-19 修，与下面 `cmd_metrics`
    #   同一处病）：主题聚合画像落在 `refs/mix_targets/<名字>.json`，只拼 `refs/<名字>.json`
    #   会"找不到参考画像" —— 面板 🎚自动配平就是这么坏的。
    import scorecard as _sc
    rp_path = _sc.ref_path(ref_name)
    if not os.path.isfile(rp_path):
        raise SystemExit('找不到参考画像: %s' % rp_path)
    rp = json.load(open(rp_path, encoding='utf-8'))
    target = {k: v for k, v in (rp.get('bands') or {}).items()}
    if not target:
        raise SystemExit('画像里没有 bands')
    aligned = set(rp.get('align_bands') or [k for k in target if not k.startswith('20-40')])
    weight = {k: (1.0 if k in aligned else 0.0) for k in target}

    master = product_audio(a.song_json)
    if not master:
        raise SystemExit('还没有成品（先渲染一次），mixfit 要拿它把母带链折算进去')

    # 分轨缓存必须**跟着 song.json 失效**（踩过：改了 patterns/mix 后仍复用旧分轨 →
    # 解出来的方程是旧版的，实测第 2 遍永远变差）
    import hashlib
    fp = hashlib.md5(open(a.song_json, 'rb').read()).hexdigest()[:10]
    out_dir = a.out or os.path.join(tempfile.gettempdir(), 'bgm-studio-audio', 'mixfit',
                                    os.path.basename(os.path.dirname(os.path.abspath(a.song_json))), fp)
    os.makedirs(out_dir, exist_ok=True)
    P, g0 = {}, {}
    for tr, notes, dd in song_tracks(a.song_json):
        base = os.path.join(out_dir, tr)
        if not os.path.isfile(base + '.ogg'):
            render_track(dd, tr, notes, base, keep_wav=False)
        P[tr] = band_power(base + '.ogg')
        g0[tr] = float((d['mix'].get(tr) or [64, 100])[1]) or 100.0
    M = band_power(master)
    keys = list(target)
    corr = {}
    for k in keys:
        tot = sum(P[tr][k] for tr in P) or 1e-20
        corr[k] = M[k] / tot

    def shape(g):
        pred = {}
        for k in keys:
            v = 1e-20
            for tr in P:
                v += corr[k] * (g[tr] / g0[tr]) ** 2 * P[tr][k]
            pred[k] = v
        return rel_shape(pred)

    def loss(rel):
        return sum(weight[k] * (rel[k] - target[k]) ** 2 for k in keys) / len(keys)

    cur = dict(g0)
    rel0 = shape(cur)
    best = loss(rel0)
    lim = 10 ** (a.max_db / 20.0)                     # ±dB → 增益比
    lo_g = {tr: max(0.0, g0[tr] / lim) for tr in P}
    hi_g = {tr: min(127.0, g0[tr] * lim) for tr in P}
    for st in (24, 12, 6, 3):
        moved = True
        while moved:
            moved = False
            for tr in P:
                cand, cs = cur[tr], best
                for g in (cur[tr] + st, cur[tr] - st):
                    if g < lo_g[tr] or g > hi_g[tr]:
                        continue
                    trial = dict(cur); trial[tr] = g
                    l = loss(shape(trial))
                    if l < cs - 1e-7:
                        cand, cs = g, l
                if cand != cur[tr]:
                    cur[tr] = cand; best = cs; moved = True
    rel1 = shape(cur)

    def mad(rel):
        return sum(weight[k] * abs(rel[k] - target[k]) for k in keys) / len(keys)
    print(json.dumps({'ok': True, 'ref': ref_name, 'stems': out_dir,
                      'before': {k: round(rel0[k], 1) for k in keys},
                      'after': {k: round(rel1[k], 1) for k in keys},
                      'ref_rel': {k: round(target[k], 1) for k in keys},
                      'mad_before': round(mad(rel0), 2), 'mad_after': round(mad(rel1), 2),
                      'max_db': a.max_db,
                      'gain': {tr: {'now': int(round(g0[tr])), 'solve': int(round(cur[tr])),
                                    'at_limit': bool(int(round(cur[tr])) in
                                                     (int(round(lo_g[tr])), int(round(hi_g[tr]))))}
                               for tr in sorted(P)}}, ensure_ascii=False))
    if a.apply:
        for tr, v in cur.items():
            new, old = int(round(v)), int(round(g0[tr]))
            if tr in d['mix']:
                d['mix'][tr] = [d['mix'][tr][0], new]
            if old > 0:
                ratio = new / float(old)
                for sec in d['sections']:
                    mix = (sec.get('arr') or {}).get('mix') or {}
                    if tr in mix:
                        mix[tr] = max(0, min(127, int(round(mix[tr] * ratio))))
        json_io.save(a.song_json, d)
        sys.stderr.write('已写回 %s（重渲染后才听得到）\n' % a.song_json)


def cmd_metrics(a):
    mine_p = product_audio(a.song_json)
    if not mine_p:
        raise SystemExit('还没渲染：先在面板上点"渲染"，或跑 make_song.py')
    win = None
    if a.win:
        lo, hi = a.win.split('-')
        win = (float(lo), float(hi))
        import tempfile
        tmp = os.path.join(tempfile.gettempdir(), 'bgm-studio-audio')
        os.makedirs(tmp, exist_ok=True)
        mine_p = slice_audio(mine_p, win, os.path.join(tmp, 'win_mine.wav'))
    ref_name = a.ref or (render_cfg(a.song_json).get('ref') or 'BGM16c')
    mine = band_table(mine_p)
    ref, diff = None, {}
    # 同一处病（见 `cmd_mixfit` 与 `server.py` 的 `/api/ref-audio`）：面板 🎚自动配平靠
    # 这个 `metrics` 子命令拿参考带；画像读成空 → `m['ref']` 为 None → 按钮直接 TypeError。
    import scorecard as _sc
    rp_path = _sc.ref_path(ref_name)
    if os.path.isfile(rp_path):
        rp = json.load(open(rp_path, encoding='utf-8'))
        ref_file = rp.get('file')
        if win and ref_file and os.path.isfile(ref_file):
            import tempfile
            tmp = os.path.join(tempfile.gettempdir(), 'bgm-studio-audio')
            os.makedirs(tmp, exist_ok=True)
            ref_file = slice_audio(ref_file, win, os.path.join(tmp, 'win_ref.wav'))
        ref = {'name': ref_name, 'file': ref_file, 'rms': rp.get('rms_db'),
               'width': rp.get('width'), 'centroid': rp.get('centroid'),
               'bands': rp.get('bands') or {}, 'occ': None}
        if ref['file'] and os.path.isfile(ref['file']):
            try:
                ref['occ'] = band_table(ref['file'])['occ']
            except Exception:
                ref['occ'] = None
        for k, v in (ref['bands'] or {}).items():
            if k in mine['rel']:
                diff[k] = round(mine['rel'][k] - v, 1)
    print(json.dumps({'ok': True, 'product': mine_p, 'mine': mine, 'ref': ref,
                      'diff_rel': diff}, ensure_ascii=False))


def cmd_finalize(a):
    """收尾：成品 WAV 的立体声宽度**精确校准**到参考画像（= make_song 自动调参的最后一步），
    再重编码 OGG。`--no-tune` 快渲染没有这一步 → 宽度会明显偏宽（实测 0.96 vs 目标 0.51）。"""
    d = os.path.dirname(os.path.abspath(a.song_json))
    cfg = render_cfg(a.song_json)
    wav = os.path.join(d, (cfg.get('out') or '') + '.wav')
    if not os.path.isfile(wav):
        raise SystemExit('没有 WAV 产物，先渲染：%s' % wav)
    ref_name = a.ref or cfg.get('ref') or 'BGM16c'
    target = 0.51
    # ⚠ **画像查找必须与 `scorecard.ref_path` 同一条路**（2026-09-19 修）：
    #   主题聚合画像在 `refs/mix_targets/<名字>.json`，而这里原先只拼 `refs/<名字>.json`
    #   → **主题画像永远找不到** → 每首的宽度目标都退回默认 `0.51`
    #   → 实测 24 首成品宽度**全是 0.511**，而权威素材是 0.391~0.656 的自然分布。
    #   这正是「多样性」差距里最直白的一条，也是 `docs/CONVENTION.md` §1
    #   「抄一份 = 埋一处漂移」的活例子（`scorecard` 早就把两处查找写在一处了）。
    import scorecard as _sc
    rp = _sc.ref_path(ref_name)
    if os.path.isfile(rp):
        target = json.load(open(rp, encoding='utf-8')).get('width') or target
    got = rm.set_width_exact(wav, target)
    ogg = to_ogg.convert(wav)
    print(json.dumps({'ok': True, 'target': target, 'width': got,
                      'wav': wav, 'ogg': ogg}, ensure_ascii=False))


def stems_inner(song_json, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    got = []
    for tr, notes, d in song_tracks(song_json):
        f = render_track(d, tr, notes, os.path.join(out_dir, tr))
        got.append(os.path.basename(f))
    return got


def cmd_export(a):
    d = os.path.dirname(os.path.abspath(a.song_json))
    cfg = render_cfg(a.song_json)
    dst = os.path.join(os.path.abspath(a.to), os.path.basename(d))
    os.makedirs(dst, exist_ok=True)
    got = []
    for fn in (cfg.get('mid'), (cfg.get('out') or '') + '.ogg',
               (cfg.get('out') or '') + '.wav', 'notes.md', 'song.json', 'render.json'):
        if not fn:
            continue
        src = os.path.join(d, fn)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dst, fn))
            got.append(fn)
    stems = stems_inner(a.song_json, os.path.join(dst, 'stems')) if a.stems else []
    print(json.dumps({'ok': True, 'dir': dst, 'files': got, 'stems': stems},
                     ensure_ascii=False))


def cmd_stems(a):
    print(json.dumps({'ok': True, 'dir': os.path.abspath(a.to),
                      'stems': stems_inner(a.song_json, os.path.abspath(a.to))},
                     ensure_ascii=False))


def cmd_solo(a):
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    for tr, notes, d in song_tracks(a.song_json):
        if tr != a.track:
            continue
        f = render_track(d, tr, notes, os.path.splitext(out)[0], keep_wav=False)
        if os.path.abspath(f) != out:
            shutil.move(f, out)
        print(json.dumps({'ok': True, 'track': tr, 'file': out}, ensure_ascii=False))
        return
    raise SystemExit('这一轨没有音符：%s' % a.track)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    m = sub.add_parser('metrics'); m.add_argument('song_json'); m.add_argument('--ref')
    m.add_argument('--win', help='只测 a-b 秒（如 6-20）')
    e = sub.add_parser('export'); e.add_argument('song_json')
    e.add_argument('--to', required=True); e.add_argument('--stems', action='store_true')
    s = sub.add_parser('solo'); s.add_argument('song_json')
    s.add_argument('--track', required=True); s.add_argument('--out', required=True)
    t = sub.add_parser('stems'); t.add_argument('song_json'); t.add_argument('--to', required=True)
    f = sub.add_parser('finalize'); f.add_argument('song_json'); f.add_argument('--ref')
    x = sub.add_parser('mixfit'); x.add_argument('song_json'); x.add_argument('--ref')
    x.add_argument('--apply', action='store_true'); x.add_argument('--out')
    x.add_argument('--max-db', type=float, default=6.0,
                   help='每轨最多改多少 dB（默认 6 ≈ ×2/÷2，防止把编配掐死）')
    a = ap.parse_args()
    {'metrics': cmd_metrics, 'export': cmd_export, 'solo': cmd_solo,
     'stems': cmd_stems, 'finalize': cmd_finalize, 'mixfit': cmd_mixfit}[a.cmd](a)


if __name__ == '__main__':
    main()