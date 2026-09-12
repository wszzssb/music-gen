#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_audio.py —— 音频体检：**能不能读 / 速度几层 / 值不值得拿来做参考**

一条命令回答扒谱前必须知道的四件事（以前要跑 3~4 个脚本才凑得齐）：

  ① **格式**：libsndfile 能读吗？读不了则本机 ffmpeg 兜底（m4a/mp4/aac/wma/ape/视频容器）
  ② **速度层级**：60/75/80 与 120/150/160 是八度关系，**两个层级都成立** ——
     这里把两层的支持度都报出来，并给出该选哪层的依据，而不是只报一个数
  ③ **质量**：时长/采样率/声道/响度/峰值/宽度/质心，外加"是否削波、是否异常短"
  ④ **状态**：该文件是否已有画像（refs/*.json / refs/melody/*.json）

用法:
  python scripts\\check_audio.py "D:\\refs\\BGM19.ogg"      # 单曲体检
  python scripts\\check_audio.py a.ogg b.m4a c.mp4                    # 多曲对照
  python scripts\\check_audio.py "D:\\refs" --formats        # 目录里哪些能读
  python scripts\\check_audio.py song.m4a --bpm 150 --json           # 强制速度 + 机器可读
  python scripts\\check_audio.py x.ogg --deep                         # 加倍频程/节奏型

退出码：0 = 全部可读；1 = 有文件读不了。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()          # 控制台编码兜底（GBK 下打印 ✓ 会崩）

import numpy as np                            # noqa: E402
import metrics                                # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _existing_refs(name):
    """这个文件已经有画像了吗（参考曲画像 / 旋律画像）"""
    out = []
    for sub, tag in ((os.path.join(ROOT, 'refs'), 'refs'),
                     (os.path.join(ROOT, 'refs', 'melody'), 'melody')):
        if os.path.exists(os.path.join(sub, name + '.json')):
            out.append(tag)
    return out


def _clip_count(x, thr=0.999):
    """触顶采样数（真实削波）"""
    a = (np.abs(x) >= thr).any(axis=1) if x.ndim > 1 else (np.abs(x) >= thr)
    return int(a.sum())


def _window_levels(m, sr, win=5.0):
    """每 win 秒的 RMS（dB）→ 看动态起伏是否正常"""
    n = max(1, int(win * sr))
    vals = []
    for i in range(0, len(m) - n // 2, n):
        seg = m[i:i + n]
        vals.append(20 * float(np.log10(max(float(np.sqrt((seg ** 2).mean())), 1e-9))))
    return vals


def check_one(path, bpm_force=None, deep=False):
    """→ (记录 dict, 是否可读)"""
    base = os.path.basename(path)
    name = base.rsplit('.', 1)[0]
    engine, sr, ch, dur, fmt = metrics.probe_format(path)
    rec = {'file': path, 'name': name, 'engine': engine, 'sr': sr, 'channels': ch,
           'duration': dur, 'format': fmt, 'ok': engine != '打不开',
           'refs': _existing_refs(name)}
    if not rec['ok']:
        rec['problems'] = ['读不了：%s' % fmt]
        return rec, False

    rec['needs_ffmpeg'] = (engine == 'ffmpeg')
    try:
        m, sr2, x = metrics.load(path)
    except SystemExit as e:
        rec['ok'] = False
        rec['problems'] = ['读不了：%s' % e]
        return rec, False

    rec['sr'] = sr2
    rec['rms_db'] = round(20 * float(np.log10(max(float(np.sqrt((m ** 2).mean())), 1e-9))), 1)
    rec['peak'] = round(float(np.abs(x).max()), 4)
    rec['width'] = round(float(metrics.width(x)), 3)
    spec, freqs = metrics._avg_spec(m, sr2)
    rec['centroid'] = int(metrics.centroid(m, sr2, spec, freqs))

    # ---- 速度层级
    if bpm_force:
        rec['bpm'] = float(bpm_force)
        rec['bpm_source'] = 'forced(--bpm)'
        rec['levels'] = {'%.1f' % bpm_force: None}
        rec['bpm_note'] = '外部指定，跳过自相关（连奏编配如竖琴/弦乐/合唱必须这样指定）'
    else:
        bpm, score, info = metrics.detect_bpm(m, sr2)
        rec['bpm'] = bpm
        rec['bpm_source'] = 'auto'
        lev = {'%.1f' % info['level_hi']: info['level_hi_score'],
               '%.1f' % info['level_lo']: info['level_lo_score']}
        if info.get('high_level'):
            lev['%.1f' % info['high_level']] = None
        if info.get('window_alt'):           # 窗口外（<60 / >180）同样成立的层级，也要列出来
            lev['%.1f' % info['window_alt']] = info.get('window_alt_score')
        for k, s in (info.get('level_ladder') or {}).items():
            lev.setdefault(k, s)             # 层级阶梯：人工核对时直接挑一个（坑 106）
        rec['levels'] = lev
        rec['bpm_peak'] = score
        rec['bpm_note'] = info.get('level_note') or '无八度歧义（只有一层成立）'
        if info.get('low_level') or info.get('high_level'):
            rec['bpm_alt'] = info.get('low_level') or info.get('high_level')
            rec['bpm_note'] += ('；两个层级都成立（%.1f/%.3f 与 %.1f/%.3f）→ 做参考曲请用'
                                ' --bpm 显式钉死，避免画像与成品各选一层'
                                % (info['level_hi'], info['level_hi_score'],
                                   info['level_lo'], info['level_lo_score']))
        # >180 BPM：折叠窗口的顶就是 180，**快过它的曲子不可能被自动定层**
        # （实测 180BPM→报 60.4、210→70.3，落在 2×/3× 关系上）。自动判不出，
        # 但要把"该看哪一层"指出来，否则人只会看到一个小一半的数。
        hot = sorted((float(k) for k in lev if float(k) > 180.0), reverse=True)
        if hot and rec['bpm'] <= 180.0:
            rec['bpm_note'] += ('；**阶梯里有 >180 的层级（%s）** —— 自动定层窗口上界是 180，'
                                '快曲（战斗/电子）请优先核对这一层，再 `--bpm` 钉死（坑 104/106）'
                                % '/'.join('%.1f' % v for v in hot[:2]))

    # ---- 深度指标（慢，opt-in）
    if deep:
        bar = 4 * 60.0 / rec['bpm']
        rec['bands'] = metrics.octave_bands(m, sr2, spec, freqs)
        rec['rhythm_low'], rec['rhythm_high'] = metrics.rhythm(m, sr2, rec['bpm'])
        rec['quiet_chroma'] = metrics.quiet_chroma(m, sr2, bar)
        lv = _window_levels(m, sr2)
        rec['level_span_db'] = round(max(lv) - min(lv), 1) if lv else 0.0

    # ---- 诊断
    probs = []
    if dur < 8:
        probs.append('只有 %.1fs —— 太短，做不了参考画像（结构/节奏型都测不出来）' % dur)
    nc = _clip_count(x)
    if nc > 0:
        probs.append('有 %d 个采样触顶（≥0.999），可能已削波' % nc)
    if rec['rms_db'] > -9:
        probs.append('响度 %.1fdB 高得反常（母带过载？）' % rec['rms_db'])
    if rec['rms_db'] < -32:
        probs.append('响度 %.1fdB 偏低（可能是素材/未混音）' % rec['rms_db'])
    if ch and ch > 2:
        probs.append('%d 声道 → 分析按平均成单声道处理' % ch)
    if sr and sr < 22050:
        probs.append('采样率 %d 偏低（>16kHz 的空气感测不准）' % sr)
    rec['problems'] = probs
    return rec, True


def _label(path):
    """显示用短名：同名文件（不同目录）要能区分，否则一屏几行看着像重复"""
    name = os.path.basename(path)
    d = os.path.basename(os.path.dirname(os.path.abspath(path)))
    return name if len(name) <= 24 else name[:21] + '...'


def fmt_line(rec):
    """一屏一行的简要输出"""
    if not rec['ok']:
        return '  %-26s ✗ %s' % (_label(rec['file']), rec['format'])
    eng = 'ffmpeg兜底' if rec.get('needs_ffmpeg') else 'libsndfile'
    lv = rec.get('levels') or {}
    lvs = ' '.join(k for k in lv if k)
    refs = ('  已有画像: ' + ','.join(rec['refs'])) if rec['refs'] else ''
    return ('  %-26s %-11s %6.1fs  %5dHz/%dch  %6.1fdB  峰%.3f  宽%.3f  %5.1fBPM[%s]  %s%s'
            % (_label(rec['file']), eng, rec['duration'], rec['sr'] or 0, rec['channels'] or 0,
               rec['rms_db'], rec['peak'], rec['width'], rec['bpm'], lvs, rec['format'], refs))


def list_formats():
    """列出本机支持的扩展名，并说明各自由谁解码"""
    try:
        sf_fmts = sorted(metrics.sf.available_formats())
    except Exception:
        sf_fmts = []
    print('libsndfile 容器（%d 种）: %s' % (len(sf_fmts), ', '.join(sf_fmts)))
    sf_ext = {'.wav', '.flac', '.ogg', '.oga', '.mp3', '.aiff', '.aif', '.aifc',
              '.au', '.snd', '.caf', '.w64', '.rf64', '.mpc'}
    via_ff = [e for e in metrics.AUDIO_EXTS if e not in sf_ext]
    exe = metrics._ffmpeg_exe()
    print('ffmpeg 兜底（%d 种，%s）: %s'
          % (len(via_ff), '已就绪' if exe else '**没有 ffmpeg → 这些读不了**',
             ', '.join(via_ff)))
    if exe:
        print('  ffmpeg: %s' % exe)
    print('=> 合计可读 %d 种扩展名（libsndfile 26 种容器 + ffmpeg 兜底上面这些）' %
          len(metrics.AUDIO_EXTS))
    print('   读不了 ≠ 解不开：真正判据是 `check_audio.py <文件>` 能不能出数')


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    as_json = '--json' in sys.argv
    deep = '--deep' in sys.argv
    formats = '--formats' in sys.argv
    bpm_force = None
    if '--bpm' in sys.argv:
        bpm_force = float(sys.argv[sys.argv.index('--bpm') + 1])

    if formats:
        list_formats()
        return 0
    if not args:
        print(__doc__)
        return 1

    # 目录 → 收集里面所有音频扩展名的文件
    files = []
    for a in args:
        if os.path.isdir(a):
            for f in sorted(os.listdir(a)):
                p = os.path.join(a, f)
                if os.path.isfile(p) and os.path.splitext(f)[1].lower() in metrics.AUDIO_EXTS:
                    files.append(p)
        else:
            files.append(a)
    if not files:
        print('没找到可作为音频的文件（用 --formats 看支持哪些扩展名）')
        return 1

    recs, bad = [], 0
    for f in files:
        rec, ok = check_one(f, bpm_force, deep)
        recs.append(rec)
        bad += 0 if ok else 1
        if not as_json:
            print(fmt_line(rec))
            if rec.get('bpm_note') and (rec.get('bpm_alt') or '起音/拍' in rec.get('bpm_note', '')):
                print('        ⚠ %s' % rec['bpm_note'])
            if deep and rec['ok']:
                print('        倍频程 ' + ' '.join('%s %.1f' % (k, v)
                                                   for k, v in rec['bands'].items()))
                print('        低频型 %s' % rec['rhythm_low'])
                print('        高频型 %s' % rec['rhythm_high'])
                print('        电平跨度 %.1fdB（越大起伏越强）' % rec['level_span_db'])
            for p in rec.get('problems', []):
                print('        ! %s' % p)

    if as_json:
        print(json.dumps(recs, ensure_ascii=False, indent=1))
    else:
        print('\n%d 个文件：%d 个可读、%d 个读不了'
              % (len(recs), len(recs) - bad, bad))
        if any(r.get('bpm_alt') for r in recs):
            print('提示：速度两个层级都成立时，做参考曲请用 `--bpm` 显式钉死一层；'
                  '画像里会记下 level_scores 供复查')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
