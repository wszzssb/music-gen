# -*- coding: utf-8 -*-
"""提取曲目的 `notes.md` —— 把"这份扒谱怎么来的 / 哪些没验证"落成可交接的一页。

## 何时用

`transcribe_ymt3.py` 那条链（转录 → 切轨 → `transcribe_to_song --auto` → `song.json`）
**不写 `notes.md`**，而曲目目录必须齐 4 件（`song.json` / `compose.py` / `notes.md` /
`render.json`）—— 全库守卫 `notes_present` 正是这么判的。少了这一步，**提取出来的每一首**
都会让全量自检变红（2026-10-07 实测：创作台快速版提取完，`notes_present` 当场点名）。
所以它是提取流程的最后一步，不是可选项。

## 用法

```bash
python scripts/extract_notes.py <曲名> --audio <原曲音频> [--mode fast|full]
                               [--seconds 98.8] [--stems-dir <六轨目录>]
python scripts/extract_notes.py --selftest
```

## 写什么（数字全部从 `song.json` / `render.json` **实测读**，不编）

来源音频与档位 · 速度 / 段数 / 小节数 · 逐轨音符数与**力度来源**（哪个分轨量的、哪条没量成）·
复现命令 · **没验证什么**（精度未用独立方法量 · 未做低音专项集成 · fast 档未精修 · 未混音调参）。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: 档位 → 做了什么（写进 notes 的第一段；**别把没做的说成做了**）
MODE_DESC = {
    'fast': '转录 → 切轨 → `transcribe_to_song --auto`（引擎编配）→ 作曲 + 渲染（`--no-tune`）',
    'full': 'fast 的全部 + `restore_oneshot.py` 六阶段（probe / repair / vel / arrange / render / audit）',
}


def _read(p):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def build(name, audio, mode='fast', seconds=None, stems_dir=None):
    """→ notes.md 全文（纯函数，便于自检拿合成夹具直接量）"""
    d = os.path.join(ROOT, 'songs', name)
    song = _read(os.path.join(d, 'song.json')) or {}
    render = _read(os.path.join(d, 'render.json')) or {}
    secs = song.get('sections') or []
    bars = sum(int(s.get('bars') or 0) for s in secs)
    pat = song.get('patterns') or {}
    ne = song.get('notes_extra') or {}
    tracks = [(k, len(v)) for k, v in sorted(ne.items()) if v]

    L = []
    L.append('# %s —— 提取记录（扒谱）' % name)
    L.append('')
    L.append('| 项 | 值 |')
    L.append('|---|---|')
    L.append('| 来源音频 | %s |' % (os.path.basename(audio) if audio else '（未给）'))
    if seconds:
        L.append('| 音频时长 | %.1f 秒 |' % float(seconds))
    L.append('| 提取档 | **%s** —— %s |' % (mode, MODE_DESC.get(mode, mode)))
    L.append('| 转录 | YourMT3+ 单模型（13 解码通道；**未做低音专项集成**） |')
    L.append('| 速度 | %s BPM · %d 段 · %d 小节 |'
             % (song.get('bpm') or '?', len(secs), bars))
    L.append('| 逐轨音符 | %s |'
             % ('、'.join('%s %d' % (k, n) for k, n in tracks) or '（notes_extra 为空）'))
    vs = pat.get('velocity_source')
    if vs:
        L.append('| 力度来源 | %s |' % vs)
    L.append('| 参考画像 | %s |' % (render.get('ref') or '（未记录）'))
    L.append('')
    L.append('## 产物')
    L.append('')
    L.append('`%s.mid` · `%s_sf.ogg` · `song.json` · `compose.py` · `render.json` · 本文件'
             % (render.get('out') or name, render.get('out') or name))
    L.append('')
    L.append('## 复现')
    L.append('')
    L.append('```bash')
    src_name = os.path.splitext(os.path.basename(audio))[0] if audio else '<源名>'
    if stems_dir:
        L.append('python scripts/stem_split.py "%s" -o _extract/%s/stems -m htdemucs_6s' % (audio, name))
    L.append('python scripts/transcribe_ymt3.py "%s" -o _extract/%s/ymt3 --name %s '
             '--song-name %s --stems-dir %s'
             % (audio or '<原曲>', name, name, name,
                stems_dir or ('_extract/%s/stems/htdemucs_6s/%s' % (name, src_name))))
    L.append('python scripts/make_song.py %s --no-tune' % name)
    L.append('```')
    L.append('')
    L.append('## 没验证什么（交付时如实说明，别拿"工具没报错"当"对"）')
    L.append('')
    L.append('- **识别精度没用独立方法量**：单模型转录只证明"前后一致"，不等于"音是对的"。'
             '要量走 `python scripts/transcribe_audit.py <分轨.wav> songs/%s/%s.mid --tracks "Bass"`'
             % (name, name))
    L.append('- **未做低音专项集成**（`bass_ensemble` 那条，属 `imitate_ref.py` 链）—— 低音可能偏薄。')
    if mode == 'fast':
        L.append('- **未做力度写回 / 段级编配**（`restore_oneshot` 的 repair / vel / arrange）——'
                 '某些轨的力度可能仍是转录自带的恒 100（"打字机"）。')
    L.append('- **未做混音调参**（`--no-tune`）：响度 / 宽度 / 平衡都没校准，'
             '产物**不能用于 A/B 结论**，也不该当最终交付物（PITFALLS 348）。')
    L.append('')
    return '\n'.join(L)


def selftest(verbose=True):
    """合成夹具自检：**关键行必须真写出来**（缺任一行的 notes.md 等于没有交接信息）"""
    song = {'bpm': 120.0,
            'sections': [{'bars': 8}, {'bars': 8}],
            'notes_extra': {'Piano': [[0, 0, 60, 1, 100]] * 5, 'Bass': [[0, 0, 36, 1, 90]] * 2},
            'patterns': {'velocity_source': 'Piano<-piano.wav、Bass<-FAILED'}}
    import tempfile
    tmp = tempfile.mkdtemp(prefix='extract_notes_')
    d = os.path.join(tmp, 'songs', 'fixture_song')
    os.makedirs(d)
    with open(os.path.join(d, 'song.json'), 'w', encoding='utf-8') as f:
        json.dump(song, f, ensure_ascii=False)
    with open(os.path.join(d, 'render.json'), 'w', encoding='utf-8') as f:
        json.dump({'out': 'fixture_song_sf', 'ref': 'BGM16c'}, f, ensure_ascii=False)
    global ROOT
    old = ROOT
    try:
        ROOT = tmp
        txt = build('fixture_song', 'ref.ogg', 'fast', seconds=98.8)
    finally:
        ROOT = old
    checks = [
        ('标题', '# fixture_song —— 提取记录' in txt),
        ('来源音频', 'ref.ogg' in txt),
        ('时长', '98.8 秒' in txt),
        ('档位说明', '提取档' in txt and 'fast' in txt),
        ('速度与段数', '120.0 BPM · 2 段 · 16 小节' in txt),
        ('逐轨音符数', 'Bass 2' in txt and 'Piano 5' in txt),
        ('力度来源', 'Piano<-piano.wav' in txt),
        ('复现命令', 'transcribe_ymt3.py' in txt and 'make_song.py' in txt),
        ('没验证什么', '没验证什么' in txt and '--no-tune' in txt),
    ]
    if verbose:
        for nm, ok in checks:
            print('  %-14s %s' % (nm, 'PASS' if ok else 'FAIL'))
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return all(ok for _n, ok in checks)


def main():
    ap = argparse.ArgumentParser(description='提取曲目的 notes.md')
    ap.add_argument('song', nargs='?', help='曲名（songs/<名>）')
    ap.add_argument('--audio', default='', help='原曲音频（写进来源与复现命令）')
    ap.add_argument('--mode', default='fast', choices=('fast', 'full'))
    ap.add_argument('--seconds', type=float, default=None, help='音频时长（秒）')
    ap.add_argument('--stems-dir', default=None, help='六轨分轨目录')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 2
    if not a.song:
        ap.print_help()
        return 1
    d = os.path.join(ROOT, 'songs', a.song)
    if not os.path.isfile(os.path.join(d, 'song.json')):
        raise SystemExit('找不到曲目：%s' % os.path.join(d, 'song.json'))
    txt = build(a.song, a.audio, a.mode, a.seconds, a.stems_dir)
    p = os.path.join(d, 'notes.md')
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.write(txt)
    print('已写 %s（%d 行）' % (p, len(txt.splitlines())))
    return 0


if __name__ == '__main__':
    import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）—— 与其它入口脚本同一写法
    sys.exit(main())
