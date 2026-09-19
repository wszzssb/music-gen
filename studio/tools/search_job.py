#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""search_job.py —— 🧬 候选搜索：**用指标当适应度**，在编配参数空间里找更贴参考曲的解。

为什么能这么快（关键设计，别改坏了）：
  ① **只渲染切片**：把目标段抽成一个"迷你曲"（默认 8 小节 ≈ 12.8s）再渲染 ——
     整曲一轮 30~120 秒，切片一轮 **1~3 秒**（30~60× 提速）。
  ② **多进程并行**：每个候选一个独立 fluidsynth 进程，默认 4 路。
  ③ **贪心坐标下降 + 预算**：每轮只改一个参数、取最优；`--budget` 限渲染次数、`--time` 限墙钟。
  ④ 打分口径与成绩单一致：绝对倍频程（平均谱带内总功率、相对最响带）在 align_bands 上的平均|差|，
     再叠一个"高频连续性"小惩罚（占用率低于参考要扣分，见坑 103）。

用法（面板「🧬 候选搜索」按钮也调它）:
  python tools/search_job.py <曲目id> [--bars a-b] [--budget 48] [--time 180] [--workers 4] [--apply]
输出：人读的进度 + 机器可读的 `RESULT {...}` 行（面板解析）。
"""
import argparse
import concurrent.futures as cf
import copy
import json
import math
import os
import shutil
import sys
import tempfile
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MG = (os.environ.get('BGM_STUDIO_ROOT')
      or os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(
          os.path.abspath(__file__))), '..')))
sys.path.insert(0, os.path.join(MG, 'scripts'))

import json_io              # noqa: E402
import metrics              # noqa: E402
import render_midi as rm    # noqa: E402
import song_engine          # noqa: E402

BANDS = [(20, 40), (40, 80), (80, 160), (160, 315), (315, 630), (630, 1250),
         (1250, 2500), (2500, 5000), (5000, 10000), (10000, 18000)]
TOP_BANDS = ('5000-10000', '10000-18000')

# 搜索空间（只搜全局 patterns —— 段级开关各有各的音乐意义，不该由打分器决定）
# 织体类（只改纹理，不改音乐性格）→ 可以自动/一键采用
SAFE_KEYS = ('arpeggio', 'staccato')
# 性格类（改律动/调域）→ 必须人工确认（实测整包采用会变差）
STRUCT_KEYS = ('bass_style', 'perc_style', 'voicing_shift', 'sub_gain')

SPACE = {
    'arpeggio': [[0, 2, 3, 4, 3, 2, 4], [0, 1, 2, 3], [0, 2, 4, 2], [0, 3, 2, 3],
                 [4, 3, 2, 1], [0, 2, 3, 2]],
    'bass_style': ['simple', 'eighth', 'offbeat', 'pump16'],
    'perc_style': ['light', 'orchestral', 'pump'],
    'staccato': [1.0, 0.7, 1.3],
    'sub_gain': [1.0, 0.6, 1.4],
    'voicing_shift': [0, 12, -12],
}


def band_power(path):
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


def occ(path, band):
    m, sr, _x = metrics.load(path)
    lo, hi = [int(x) for x in band.split('-')]
    env = metrics._band_env(m, sr, 4096, 1024, float(lo), float(hi))
    return metrics.occupancy(env)


def build_mini(song, sec_idx, max_bars):
    """把第 sec_idx 段抽成迷你曲（段落从段首开始、最长 max_bars 小节）"""
    mini = copy.deepcopy(song)
    sec = copy.deepcopy(song['sections'][sec_idx])
    sec['bars'] = min(sec['bars'], max_bars)
    sec['chords'] = (sec['chords'] or [])[:sec['bars']]
    if len(sec['chords']) < sec['bars']:
        sec['chords'] += [sec['chords'][-1]] * (sec['bars'] - len(sec['chords']))
    # 只留这一段用到的旋律（旋律的 [小节,拍,…] 是本段内的坐标，天然可用）
    key = sec.get('melody')
    mini['melody'] = {key: (song['melody'].get(key) or [])} if key else {}
    mini['sections'] = [sec]
    return mini


def evaluate(job):
    """渲染一个候选并打分（在独立线程里跑独立进程，互不干扰）"""
    tag, mini, params, cfg, workdir, ref_rel, ref_occ, use_occ = job
    mini = copy.deepcopy(mini)
    mini['patterns'].update({k: v for k, v in params.items()})
    d = os.path.join(workdir, tag)
    os.makedirs(d, exist_ok=True)
    sj = os.path.join(d, 'song.json')
    json_io.save(sj, mini)
    mid = os.path.join(d, 'm.mid')
    base = os.path.join(d, 'm')
    t0 = time.time()
    try:
        song_engine.compose(sj, mid)
        rm.render(mid, base, rms_db=cfg.get('rms', -16.9), width=1.0,
                  shelf_db=cfg.get('shelf', 3.0), hp_hz=cfg.get('hp', 28.0),
                  low_db=cfg.get('low', 0.0), drive=cfg.get('drive', 1.5),
                  mid_db=cfg.get('mid_db', 0.0), verbose=False, ogg=False)
        rel = rel_shape(band_power(base + '.wav'))
        keys = list(ref_rel)
        mad = sum(abs(rel[k] - ref_rel[k]) for k in keys) / len(keys)
        pen = 0.0
        if use_occ:
            mo = occ(base + '.wav', '10000-18000')
            pen = 0.01 * max(0.0, ref_occ - mo)
        return {'tag': tag, 'params': params, 'mad': round(mad, 3), 'pen': round(pen, 3),
                'score': round(mad + pen, 3), 'sec': round(time.time() - t0, 2),
                'rel': {k: round(rel[k], 1) for k in keys}}
    except Exception as e:                                   # noqa: BLE001
        return {'tag': tag, 'params': params, 'mad': 99.0, 'score': 99.0, 'err': str(e)[:200],
                'sec': round(time.time() - t0, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sid')
    ap.add_argument('--bars', type=int, default=8, help='切片长度（小节，默认 8 ≈ 12.8s@150BPM）')
    ap.add_argument('--sec', type=int, default=-1, help='搜哪一段（默认：最长的段）')
    ap.add_argument('--budget', type=int, default=48, help='最多渲染多少次（默认 48）')
    ap.add_argument('--time', type=float, default=180.0, help='墙钟上限秒（默认 180）')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--apply', action='store_true',
                    help='把搜索到的**全部**参数写回（含性格类，实测有风险）')
    ap.add_argument('--apply-keys', default=None,
                    help='只写回这些参数（逗号分隔，如 arpeggio,staccato）')
    ap.add_argument('--no-save-best', action='store_true', help='只报告、不写回（默认写回 patterns）')
    a = ap.parse_args()

    folder = os.path.join(MG, 'songs', a.sid)
    song_path = os.path.join(folder, 'song.json')
    song = json.load(open(song_path, encoding='utf-8'))
    cfg = json.load(open(os.path.join(folder, 'render.json'), encoding='utf-8')) \
        if os.path.isfile(os.path.join(folder, 'render.json')) else {}
    ref_name = cfg.get('ref') or 'BGM16c'
    # 画像路径走 `scorecard.ref_path`（唯一真源）：聚合画像在 `refs/mix_targets/` 下，
    # 只拼 `refs/` 会读成空 → 直接抛"参考画像缺 bands"（面板 🧬候选搜索点了就报错）。
    sys.path.insert(0, os.path.join(MG, 'scripts'))
    import scorecard as _sc
    rp_path = _sc.ref_path(ref_name)
    rp = json.load(open(rp_path, encoding='utf-8')) if os.path.isfile(rp_path) else {}
    align = rp.get('align_bands') or [k for k in (rp.get('bands') or {})
                                      if not k.startswith('20-40')]
    if not align:
        raise SystemExit('参考画像缺 bands')

    # ① 参考曲的"同一时间窗"切片：按目标段在小节网格上的位置换算秒数
    secs = [s['bars'] for s in song['sections']]
    idx = a.sec if a.sec >= 0 else max(range(len(secs)), key=lambda i: secs[i])
    bar = 4 * 60.0 / song['bpm']
    t0 = sum(secs[:idx]) * bar
    dur = min(a.bars, secs[idx]) * bar
    # 参考音频一律走 `scorecard.ref_audio`（唯一真源）：聚合画像的 `file` 是占位串
    # （`aggregate(N refs)`），单份画像的 `file` 常常也**只是文件名**（素材不随仓库分发，
    # 靠 `BGM_REF_DIR` / `studio/.refdir` 指过去）。取不到就**不做参考切片**（`use_occ`
    # 天然可降级），而不是拿占位串去 `sf.read`。2026-09-19 实测：面板 🧬候选搜索就是
    # 这么坏的（`LibsndfileError: Error opening 'aggregate(6 refs)'`）。
    sys.path.insert(0, os.path.join(MG, 'scripts'))
    import scorecard as _sc
    ref_file = _sc.ref_audio(rp)
    tmp = os.path.join(tempfile.gettempdir(), 'bgm-studio-audio', 'search_' + a.sid)
    os.makedirs(tmp, exist_ok=True)
    ref_wav = os.path.join(tmp, 'ref_%d_%d.wav' % (int(t0 * 1000), int(dur * 1000)))
    if ref_file and not os.path.isfile(ref_wav):
        import soundfile as sf
        x, sr = sf.read(ref_file, dtype='float32', always_2d=True)
        i0, i1 = int(t0 * sr), int((t0 + dur) * sr)
        if i1 - i0 < sr // 2:
            i0, i1 = 0, min(len(x), sr * 8)
        sf.write(ref_wav, x[i0:i1], sr)
    ref_rel = {k: v for k, v in rp['bands'].items() if k in align}
    ref_occ = None
    # ⚠ 只在**真有参考切片**时才量：`occ()` 找不到文件时抛的是 `SystemExit`
    #   （不是 `Exception`），下面那个 `except` 拦不住它 —— 2026-09-19 实测，
    #   面板 🧬候选搜索就是崩在"找不到音频文件: ...\ref_7272_1454.wav"。
    if ref_file and os.path.isfile(ref_wav):
        try:
            ref_occ = occ(ref_wav, '10000-18000')
        except BaseException:                                # noqa: BLE001
            ref_occ = None
    use_occ = ref_occ is not None
    print('搜索目标：%s 第 %d 段（%d 小节 / %.1fs），参考 %s，对齐带 %d 个，预算 %d 次渲染 / %.0fs，%d 路并行'
          % (a.sid, idx, min(a.bars, secs[idx]), dur, ref_name, len(align), a.budget, a.time,
             a.workers), flush=True)

    mini = build_mini(song, idx, a.bars)
    cur = {k: song['patterns'].get(k) for k in SPACE if k in song.get('patterns', {})}
    for k in SPACE:                                     # 没写过的参数从默认值起
        cur.setdefault(k, SPACE[k][0])

    best = {'score': None, 'params': dict(cur)}
    tried, results = {}, []
    t_start = time.time()
    n_render = 0

    def run_batch(cands):
        """并行渲染一批候选；返回按 score 排序的结果"""
        nonlocal n_render
        jobs = []
        for i, p in enumerate(cands):
            key = json.dumps(p, sort_keys=True)
            if key in tried:
                continue
            tried[key] = True
            n_render += 1
            jobs.append(('%03d' % n_render, mini, p, cfg, tmp, ref_rel, ref_occ, use_occ))
        if not jobs:
            return []
        with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
            out = list(ex.map(evaluate, jobs))
        for r in out:
            results.append(r)
            print('  %-6s mad=%.2f%s  %s' % (r['tag'], r['mad'],
                  ('  占用罚=%.2f' % r['pen']) if r.get('pen') else '',
                  json.dumps({k: (v if not isinstance(v, list) else '…') for k, v in r['params'].items()},
                             ensure_ascii=False)), flush=True)
        return sorted(out, key=lambda r: r['score'])

    # ② 先量基线
    base_res = run_batch([dict(cur)])
    if base_res:
        best = dict(base_res[0])
        print('基线：mad=%.2f  score=%.2f' % (best['mad'], best['score']), flush=True)

    # ③ 贪心坐标下降：逐参数枚举，取最优；一轮无改进就停
    improved = True
    while improved and n_render < a.budget and (time.time() - t_start) < a.time:
        improved = False
        for prm, options in SPACE.items():
            if n_render >= a.budget or (time.time() - t_start) >= a.time:
                break
            cands = []
            for v in options:
                if v == best['params'].get(prm):
                    continue
                p = dict(best['params'])
                p[prm] = v
                cands.append(p)
            got = run_batch(cands)
            if got and got[0]['score'] < best['score'] - 1e-4:
                best = dict(got[0])
                improved = True
                print('★ 采用 %s = %s（mad=%.2f score=%.2f）'
                      % (prm, json.dumps(best['params'][prm], ensure_ascii=False),
                         best['mad'], best['score']), flush=True)

    elapsed = time.time() - t_start
    summary = {'ok': True, 'sid': a.sid, 'sec': idx, 'bars': min(a.bars, secs[idx]),
               'ref': ref_name, 'renders': n_render, 'seconds': round(elapsed, 1),
               'per_render': round(elapsed / max(1, n_render), 2),
               'base': {'mad': base_res[0]['mad'] if base_res else None},
               'best': {'mad': best.get('mad'), 'score': best.get('score'),
                        'params': {k: best['params'][k] for k in SPACE}},
               'changed': {k: v for k, v in best['params'].items()
                           if v != song['patterns'].get(k)}}
    summary['suggest_keys'] = [k for k in summary['changed'] if k in SAFE_KEYS]
    summary['struct_keys'] = [k for k in summary['changed'] if k in STRUCT_KEYS]
    print('RESULT ' + json.dumps(summary, ensure_ascii=False), flush=True)   # 覆盖上面的简版

    keys = [k.strip() for k in (a.apply_keys or '').split(',') if k.strip()]
    if a.apply and not keys:
        keys = list(SPACE)
        if any(k in STRUCT_KEYS for k in keys):
            print('⚠ --apply 会连性格参数一起写回（实测可能变差）；建议 --apply-keys '
                  + ','.join(SAFE_KEYS), flush=True)
    if keys:
        use = {k: best['params'][k] for k in keys if k in best['params']}
        song['patterns'].update(use)
        json_io.save(song_path, song)
        print('已采用：' + json.dumps(use, ensure_ascii=False), flush=True)
    else:
        print('未写回（默认只报告）。面板里勾选后采用，或 --apply-keys '
              + ','.join(SAFE_KEYS), flush=True)


if __name__ == '__main__':
    main()