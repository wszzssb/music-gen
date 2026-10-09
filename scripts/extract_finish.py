#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""extract_finish.py —— 面板提取链的**收尾两步**（2026-10-09 接进 `studio/server.py`）

**为什么有它**：面板两档提取（`fast` / `full`）跑完后有两件事**没人做**，于是每首提取曲都会留红：

  ① **混音对标**：`render.json.ref` 默认落成 `bgm01c`（**另一首曲子**）—— 于是「完整还原」那档做的
     是"照**别人的**频谱调参"，守卫 `t_restore_ref_is_own_song` 必红
     （实测现场：面板提 BGM35 · 我手工跑的 bgm23，两次都撞这条）；
  ② **打击乐声明**：只要原曲有鼓，鼓层音符就会被算进"**渲染后** Perc 占比"
     （我 bgm23 = 4172/7628 = **54.7%**，门 **15%**）⇒ `t_perc_declared_for_restore` 必红。

两件事都有**有据可依**的正解（给本曲建画像 / 按分轨实测数字写 `perc_exempt`），本工具固定成一步。
⚠ 它**不改音乐内容**：只动 `render.json.ref`、`refs/<名>.json` 与 `patterns.perc_exempt`
（后者是守卫留的**声明口**，要求"非空白理由 + 实测数字"）。

用法：
  python scripts\extract_finish.py <曲名|song.json> --audio <原曲> [--stems-dir <6轨目录>]
        [--mode fast|full] [--dry]
  `--dry` 只打印打算做什么（供复核）。退出码：0 = 正常（含"无需处理"）。

口径：**Perc 占比**由 `perc_share()` 给，守卫 `selftest.t_perc_declared_for_restore`
**直接调用它**（口径一处 —— 不许各写一份，那是 PITFALLS 353 的形态）。
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WS = os.path.join('songs')          # 曲库根（相对 ROOT）

#: Perc 占比门（**与守卫同一处**：`t_perc_declared_for_restore` import 这个常量）
PERC_FRAC = 0.15
#: 判"原曲真有鼓"的门：鼓分轨与**最响分轨**之差 ≥ −6dB（我 bgm23 实测：鼓 = 最响，差 0.0dB）
DRUMS_WITHIN_DB = 6.0
PERC_KEYS = ('Perc', 'Drum', 'Kit')


# ─────────────────────────── 口径：Perc 占比（守卫共用）
def perc_share(song_dir):
    """→ `(perc, total, frac, mid_path)` —— **与守卫同一口径**。

    判"是打击乐轨"：`channel == 9` **或** 轨名含 `Perc`/`Drum`/`Kit`。
    读的是**渲染后的 MIDI**（`render.json.mid` 优先，退回目录里第一个 `.mid`）；读不到 → `None`。
    """
    import midi_file
    mid = None
    rj = os.path.join(song_dir, 'render.json')
    if os.path.isfile(rj):
        try:
            c = json.load(open(rj, encoding='utf-8')).get('mid') or ''
            cand = c if os.path.isabs(c) else os.path.join(song_dir, c)
            mid = cand if os.path.isfile(cand) else None
        except Exception:                                          # noqa: BLE001
            mid = None
    if mid is None:
        cs = [os.path.join(song_dir, f) for f in sorted(os.listdir(song_dir))
              if f.endswith('.mid')]
        mid = cs[0] if cs else None
    if not mid:
        return None
    m = midi_file.import_midi(mid)
    tot = perc = 0
    for tr in m.get('tracks') or []:
        n = len(tr.get('notes') or [])
        tot += n
        if tr.get('channel') == 9 or any(k in (tr.get('name') or '') for k in PERC_KEYS):
            perc += n
    return (perc, tot, (perc / tot) if tot else 0.0, mid)


# ─────────────────────────── 证据：分轨电平 → "原曲有没有鼓"
def stem_rms(stems_dir):
    """→ `{分轨名: RMS dBFS}`（读不了的分轨跳过）。"""
    import numpy as np
    import soundfile as sf
    out = {}
    for f in sorted(os.listdir(stems_dir)):
        if not f.lower().endswith(('.wav', '.flac')):
            continue
        try:
            y, _sr = sf.read(os.path.join(stems_dir, f), always_2d=True)
            y = y.astype('float64')
            out[os.path.splitext(f)[0]] = round(
                float(20 * np.log10(float(np.sqrt((y ** 2).mean())) + 1e-12)), 2)
        except Exception:                                          # noqa: BLE001
            continue
    return out


def drums_verdict(ev):
    """→ `(True/False/None, 依据文字)` —— `None` = 判不了（缺分轨）。

    判据：鼓分轨与**最响的那条**相差 ≥ −`DRUMS_WITHIN_DB` ⇒ 判"原曲有打击乐层"。
    依据**全部写进返回值**（要进 `perc_exempt` 的正文，不许空话）。
    """
    if not ev:
        return None, '缺分轨（没给 --stems-dir 或目录为空）⇒ 给不出依据'
    drums = ev.get('drums')
    if drums is None:
        return None, '分轨里没有 drums.wav ⇒ 给不出依据'
    others = {k: v for k, v in ev.items() if k != 'drums'}
    if not others:
        return None, '只有 drums 一条分轨 ⇒ 无从比较'
    top_k = max(others, key=others.get)
    gap = round(drums - others[top_k], 2)
    txt = ('drums.wav RMS %.2fdBFS · 最响的其它分轨 %s RMS %.2fdBFS · 差 %+.2fdB'
           % (drums, top_k, others[top_k], gap))
    return (gap >= -DRUMS_WITHIN_DB), txt


def declaration_text(name, ev, perc, tot, frac, verdict_txt):
    """`perc_exempt` 的正文：**必须带实测数字**（守卫要求理由非空；空话放行不了）。"""
    return ('原曲本身有打击乐层（%s 实测）：渲染后 Perc %d 音 / 全曲 %d 音 = %.1f%%；%s。'
            '⇒ 引擎这一层不是"默认加鼓"。'
            % (name, perc, tot, frac * 100, verdict_txt))


# ─────────────────────────── 两步
def fix_ref(name, song_dir, audio, bpm, dry=False, force=False, log=print):
    """① 让混音对标变成**本曲原曲**（建画像 → 指过去 → 必要时重渲染）。

    ⚠ **判据跟守卫走**（`t_restore_ref_is_own_song` 只在 `tuned=true` **且**
    `ref_bpm_mismatch=true` 时 FAIL），所以默认**只在真有问题时才动**：
      · `render.json.ref_bpm_mismatch is True`；或
      · `render.json` 里没有 ref；或
      · 现 ref 画像的 bpm 与本曲不一致（尺寸未知也算）。
    已经对得上的曲子（例：`amakute_aligned` 的 ref=`amakute` 是它自己的原曲画像）
    **一律不动** —— 2026-10-09 实测：按"ref 必须等于曲名"的字面做会给老曲无谓换对标 + 重渲染。
    要强制按本曲重建（例如你确信现对标不是本曲）加 `--force-ref`。
    """
    refs = os.path.join(ROOT, 'refs', '%s.json' % name)
    rj = os.path.join(song_dir, 'render.json')
    cfg = {}
    if os.path.isfile(rj):
        try:
            cfg = json.load(open(rj, encoding='utf-8')) or {}
        except Exception:                                          # noqa: BLE001
            cfg = {}
    old_ref = cfg.get('ref')
    # 现对标与本曲 bpm 是否一致（拿画像文件比，拿不到就按"未知"处理 → 要修）
    old_bpm = None
    if old_ref:
        p = os.path.join(ROOT, 'refs', '%s.json' % old_ref)
        if os.path.isfile(p):
            try:
                old_bpm = float(json.load(open(p, encoding='utf-8')).get('bpm') or 0) or None
            except Exception:                                      # noqa: BLE001
                old_bpm = None
    same_bpm = (old_bpm is not None and bpm and abs(old_bpm - float(bpm)) <= 0.5)
    mismatch = cfg.get('ref_bpm_mismatch') is True
    if (not force) and old_ref and old_ref != name and same_bpm and not mismatch:
        log('  [对标] 现对标 %s（bpm %.2f）= 与本曲一致 ⇒ **不动**（要强制按本曲重建加 --force-ref）'
            % (old_ref, old_bpm))
        return True
    made = False
    if not os.path.isfile(refs):
        cmd = [sys.executable, os.path.join(HERE, 'profile_ref.py'), audio, name]
        if bpm:
            cmd += ['--bpm', '%.4f' % float(bpm)]
        log('  [对标] 建本曲画像 refs/%s.json（%s）' % (name, ' '.join(cmd[-2:])))
        if not dry:
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
            if r.returncode != 0:
                log('  [对标] !! 建画像失败：%s' % (r.stderr or r.stdout or '')[-160:])
                return False
            made = True
    if old_ref == name and not made:
        log('  [对标] 已经指向本曲（ref=%s）—— 不动' % name)
        return True
    if dry:
        log('  [对标] 会把 render.json.ref: %s → %s' % (old_ref, name))
        return True
    if os.path.isfile(rj):
        cfg['ref'] = name
        cfg.setdefault('song', name)
        with open(rj, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=1)
    log('  [对标] render.json.ref: %s → %s' % (old_ref, name))
    # 已经按**错的**对标调过参 ⇒ 必须重渲染，否则那条红只是被"改字"掩盖
    if cfg.get('tuned') is True:
        log('  [对标] 之前是按 %s 调的参 ⇒ 重跑 make_song（按本曲原曲重新收敛）' % old_ref)
        r = subprocess.run([sys.executable, os.path.join(HERE, 'make_song.py'), name],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        tail = [x for x in (r.stdout or '').splitlines() if x.strip()][-2:]
        for x in tail:
            log('         ' + x.strip()[:120])
        if r.returncode != 0:
            log('  [对标] !! 重渲染失败（rc=%s）：%s' % (r.returncode, (r.stderr or '')[-160:]))
            return False
    return True


def declare_perc(name, song_dir, stems_dir, dry=False, log=print):
    """② 渲染后 Perc 占比超门时：按**分轨实测数字**写 `patterns.perc_exempt`（或明确建议不改）。"""
    import json_io
    sj = os.path.join(song_dir, 'song.json')
    try:
        j = json_io.load(sj)
    except Exception:                                              # noqa: BLE001
        j = json.load(open(sj, encoding='utf-8'))
    ne = j.get('notes_extra') or {}
    if not any((v.get('notes') if isinstance(v, dict) else v) for v in ne.values()):
        log('  [打击乐] 不是提取曲（没有 notes_extra）⇒ 这条门不判它，跳过')
        return True
    pats = j.get('patterns') or {}
    if str(pats.get('perc_exempt') or '').strip():
        log('  [打击乐] 已有 perc_exempt ⇒ 不动')
        return True
    ps = perc_share(song_dir)
    if ps is None:
        log('  [打击乐] 读不到渲染后的 MIDI ⇒ 判不了（先跑一次 make_song）')
        return False
    perc, tot, frac, _mid = ps
    log('  [打击乐] 渲染后 Perc %d 音 / 全曲 %d 音 = %.1f%%（门 %.0f%%）'
        % (perc, tot, frac * 100, PERC_FRAC * 100))
    if frac <= PERC_FRAC:
        log('  [打击乐] 在门内 ⇒ 无需声明')
        return True
    ev = stem_rms(stems_dir) if stems_dir and os.path.isdir(stems_dir) else {}
    has, why = drums_verdict(ev)
    log('  [打击乐] 分轨依据：%s' % why)
    if has is None:
        log('  [打击乐] ⚠ 给不出依据 ⇒ **不写声明**（宁可留红，也不写空话）：'
            '要么补 --stems-dir，要么按实情写 arr.perc: 0')
        return False
    if not has:
        log('  [打击乐] 分轨看不出有鼓 ⇒ **不写声明**；若原曲确实无鼓，写 `arr.perc: 0`'
            '（引擎那层关掉），别拿 perc_exempt 糊过去')
        return False
    txt = declaration_text(name, ev, perc, tot, frac, why)
    if dry:
        log('  [打击乐] 会写 patterns.perc_exempt = %s' % txt[:120] + ' …')
        return True
    pats['perc_exempt'] = txt
    j['patterns'] = pats
    json_io.save(sj, j)
    back = (json_io.load(sj).get('patterns') or {}).get('perc_exempt') or ''
    log('  [打击乐] 已写 perc_exempt（读回 %d 字）：%s' % (len(back), back[:100] + ' …'))
    return bool(back.strip())


def resolve_song(arg):
    """`<曲名|song.json>` → `(名字, 曲目目录)`。"""
    if os.path.isfile(arg) and arg.endswith('.json'):
        d = os.path.dirname(os.path.abspath(arg))
        return os.path.basename(d), d
    for base in ('songs', 'songs_direct'):
        d = os.path.join(ROOT, base, arg)
        if os.path.isdir(d):
            return arg, d
    return arg, os.path.join(ROOT, 'songs', arg)


def main():
    ap = argparse.ArgumentParser(description='面板提取链收尾：混音对标 + 打击乐声明')
    ap.add_argument('song', help='曲名（songs/<名>）或 song.json 路径')
    ap.add_argument('--audio', help='原曲音频（建画像用）')
    ap.add_argument('--stems-dir', default=None, help='6 轨分轨目录（给打击乐依据）')
    ap.add_argument('--mode', default=None, choices=[None, 'fast', 'full'], help='提取档（只影响打印）')
    ap.add_argument('--dry', action='store_true', help='只打印打算做什么')
    ap.add_argument('--force-ref', dest='force_ref', action='store_true',
                    help='不管现对标是否对得上，都按本曲重建画像并指过去')
    a = ap.parse_args()
    name, d = resolve_song(a.song)
    if not os.path.isdir(d):
        print('找不到曲目目录：%s' % d)
        return 2
    print('收尾：%s（%s）· 档=%s%s'
          % (name, d, a.mode or '未给', ' · --dry' if a.dry else ''))
    bpm = None
    try:
        bpm = float(json.load(open(os.path.join(d, 'song.json'), encoding='utf-8')).get('bpm') or 0)
    except Exception:                                              # noqa: BLE001
        bpm = None
    ok_ref = True
    if a.audio and os.path.isfile(a.audio):
        ok_ref = fix_ref(name, d, a.audio, bpm, dry=a.dry, force=a.force_ref)
    else:
        print('  [对标] 没给 --audio ⇒ 跳过（守卫会按 render.json 现状判）')
    ok_perc = declare_perc(name, d, a.stems_dir, dry=a.dry)
    print('完成（档=%s）：对标 %s · 打击乐 %s'
          % (a.mode or '未给', 'ok' if ok_ref else '需人工', 'ok' if ok_perc else '需人工'))
    return 0


import cli_utf8 as _cu      # noqa: E402
_cu.setup()                 # 控制台编码兜底（GBK 下打印 ✓ 会崩 —— 守卫 console_encoding_safe）
if __name__ == '__main__':
    sys.exit(main())
