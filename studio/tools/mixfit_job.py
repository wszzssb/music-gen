#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mixfit_job.py —— "自动配平"的完整闭环：**解方程 → 真渲染 → 实测 → 更好就留、更差就回滚**。

为什么要闭环：mixfit 的模型是"带功率线性叠加 + 母带链折算"，它预测得不错但不完美
（实测：预测 mad 1.24→0.64，渲染后真值 2.03→1.00 ✓ 好；再迭代一次反而 1.43 ✗ 过冲）。
所以这里以**实测成绩单**为准：只有实测变好才保留，否则回滚并重渲染。

用法（由面板的"🎚 自动配平"按钮调用，也可单独跑）：
  python tools/mixfit_job.py <曲目id> [--max-db 6] [--max-pass 2]
输出：一行 JSON（before/after 的实测平均|差| + 每轨 CC7 改动 + 是否回滚）
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MG = (os.environ.get('BGM_STUDIO_ROOT')
      or os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(
          os.path.abspath(__file__))), '..')))
PY = os.path.join(MG, '.venv', 'Scripts', 'python.exe')
BR = os.path.join(APP, 'bridge.py')


def run(args, timeout=1800):
    env = dict(os.environ, PYTHONIOENCODING='utf-8', BGM_STUDIO_ROOT=MG)
    p = subprocess.run([PY, '-X', 'utf8'] + args, cwd=MG, env=env, capture_output=True,
                       text=True, encoding='utf-8', errors='replace', timeout=timeout)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def last_json(out):
    return json.loads([l for l in out.strip().splitlines() if l.startswith('{')][-1])


def metrics(sid):
    rc, out = run([BR, 'metrics', os.path.join(MG, 'songs', sid, 'song.json')])
    if rc != 0:
        raise SystemExit('metrics 失败：' + out[-300:])
    return last_json(out)


def mad(m, bands):
    d = m.get('diff_rel') or {}
    vals = [abs(d[k]) for k in bands if k in d]
    return (sum(vals) / len(vals) if vals else float('nan')), (max(vals) if vals else 0.0)


HINTS = {
    '20-40': '最下潜：mix.Bass / patterns.sub_gain（GM 音源到不了就别硬追）',
    '40-80': '低频：mix.Bass / patterns.sub_gain',
    '80-160': '低频基音：mix.Bass（最容易"厚"的一段）',
    '160-315': '低中频：mix.Pad / mix.Piano',
    '315-630': '中频：mix.Piano / mix.Strings',
    '630-1250': '中高频：mix.Strings / mix.Arp',
    '1250-2500': '中高频：mix.Strings / mix.Arp / mix.Glock',
    '2500-5000': '明亮度：mix.Glock / mix.Arp（或 render --shelf）',
    '5000-10000': '空气感：mix.Perc（沙锤）/ mix.Glock，或 render --shelf',
    '10000-18000': '顶频：mix.Perc / mix.Glock；连续性是音源天花板（见 PITFALLS 103）',
}


def hints_for(m, bands, tol=3.0, limit_keys=()):
    """还有大缺口 → 给"该改哪一轨"的可执行建议（到限幅的轨优先解释）"""
    out = []
    d = m.get('diff_rel') or {}
    for k in bands:
        v = d.get(k)
        if v is None or abs(v) <= tol:
            continue
        act = HINTS.get(k, '')
        out.append('%s 差 %+.1fdB → %s' % (k, v, act))
    if limit_keys:
        out.append('这些轨已到 ±6dB 限幅：%s —— 说明差距来自**编配**（换音色/加层/改织体），'
                   '不是推子能解决的' % ', '.join(sorted(limit_keys)))
    return out


def aligned_bands(ref_name):
    # 画像路径走 `scorecard.ref_path`（唯一真源）：聚合画像在 `refs/mix_targets/` 下，
    # 只拼 `refs/` 会读成空 → 调用处 `m['ref']['name']` 直接 TypeError（面板按钮点了报错）。
    sys.path.insert(0, os.path.join(MG, 'scripts'))
    import scorecard as _sc
    p = _sc.ref_path(ref_name)
    rp = json.load(open(p, encoding='utf-8')) if os.path.isfile(p) else {}
    return rp.get('align_bands') or [k for k in (rp.get('bands') or {})
                                     if not k.startswith('20-40')]


def render(sid, song):
    rc, out = run(['scripts/make_song.py', sid, '--no-tune'])
    if rc != 0:
        raise SystemExit('渲染失败：' + out[-300:])
    run([BR, 'finalize', song])


ap = argparse.ArgumentParser()
ap.add_argument('sid'); ap.add_argument('--max-db', type=float, default=6.0)
ap.add_argument('--max-pass', type=int, default=2)
a = ap.parse_args()

song = os.path.join(MG, 'songs', a.sid, 'song.json')
bak = song + '.bak_mixfit'
if not os.path.isfile(bak):
    shutil.copy2(song, bak)

m = metrics(a.sid)
bands = aligned_bands(m['ref']['name'])
base, base_max = mad(m, bands)
best, best_song = base, open(song, encoding='utf-8').read()
log = []

for k in range(a.max_pass):
    rc, out = run([BR, 'mixfit', song, '--apply', '--max-db', str(a.max_db)])
    if rc != 0:
        log.append('mixfit 失败：' + out[-200:])
        break
    j = last_json(out)
    limit_keys = {k for k, v in (j.get('gain') or {}).items() if v.get('at_limit')}
    render(a.sid, song)
    m = metrics(a.sid)
    cur, cur_max = mad(m, bands)
    log.append('第%d 遍：模型 %.2f→%.2f dB，实测 %.2f→%.2f dB' % (k + 1, j['mad_before'],
                                                                 j['mad_after'], base, cur))
    if cur < best - 1e-3:
        best, best_song = cur, open(song, encoding='utf-8').read()
    else:
        log.append('这一遍没有变好 → 回滚到最好的一版')
        break

if best_song != open(song, encoding='utf-8').read():
    open(song, 'w', encoding='utf-8').write(best_song)
    render(a.sid, song)
    log.append('已回滚到最优版本并重渲染')

mf = metrics(a.sid)
hints = hints_for(mf, bands, tol=3.0, limit_keys=limit_keys)
for h in hints:
    print('建议：' + h, flush=True)
print(json.dumps({'ok': True, 'sid': a.sid, 'ref': m['ref']['name'],
                  'hints': hints,
                  'mad_before': round(base, 2), 'mad_after': round(best, 2),
                  'max_before': round(base_max, 1), 'max_after': round(mad(metrics(a.sid), bands)[1], 1),
                  'log': log}, ensure_ascii=False))