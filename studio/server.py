#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""server.py —— BGM Studio 本地服务：静态页面 + JSON API（驱动 music-gen 工具链）。

只依赖 Python 标准库（http.server）+ music-gen 的 .venv；**零构建、零额外依赖**。
音频/分析/渲染全部交给工具链自己的脚本，本文件不实现任何 DSP：
  make_song.py（作曲+渲染+调参+成绩单）/ check_song.py（数据契约）/ song_events.py（逐轨事件）
  / bridge.py（指标 / 分轨 / 导出）

启动:
  python server.py [--port 8765] [--root <工具链根>] [--open]
API（前缀 /api）:
  GET  /api/songs                     曲目列表（含产物/时长/速度）
  GET  /api/song?id=<曲>               song.json + render.json + 逐轨事件（钢琴卷帘用）
  POST /api/song?id=<曲>               保存（body = song.json；用 json_io 规范写回）
  POST /api/check?id=<曲>              数据契约检查（check_song.py，2 秒）
  POST /api/job?id=<曲>&kind=K         起后台任务（compose/render/render-tune/solo:轨/export[:stems]）
  GET  /api/job?id=<jobId>             任务状态 + 日志尾巴（前端 1 秒轮询）
  GET  /api/audio?id=<曲>&kind=mix|solo|stem&track=<轨>&t=<ts>   音频（支持 Range 拖动播放）
  GET  /api/metrics?id=<曲>            指标（bridge metrics：绝对/相对倍频程 + 占用率）
  GET  /api/files?id=<曲>              产物清单

  —— MIDI 编辑器（对标 miditoolbox：导入任意 .mid → 编辑 → 导出 .mid）——
  GET  /api/ed/list                    编辑会话列表（每个会话是 %TEMP%\bgm-studio-edits\<id>\）
  GET  /api/ed/model?id=<会话>         取会话模型（音符/CC/轨）
  POST /api/ed/import                  导入 MIDI（body={name, data_b64} 或 {path}）→ 新会话
  POST /api/ed/op?id=<会话>            执行编辑操作（body={op, params, model?}）
  POST /api/ed/save?id=<会话>          保存模型
  POST /api/ed/export?id=<会话>        导出 .mid（body={fmt:0|1, title}）→ 文件
  GET  /api/ed/download?id=<会话>&fmt= 下载导出的 .mid
"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, 'web')
BRIDGE = os.path.join(HERE, 'bridge.py')
# 路径**相对化**：面板就在工具链目录下（studio/），root 默认取父目录；可用 --root / env 覆盖
ROOT = os.environ.get('BGM_STUDIO_ROOT') or os.path.abspath(os.path.join(HERE, '..'))
# 编辑器的解析/操作/读写都在 scripts/ 里（`midi_file` / `midi_ops` / `midi_probe`），
# 服务器进程要能 import 它们 —— 加一次 sys.path（与 `run_py` 子进程的口径一致）。
_SCRIPTS = os.path.join(ROOT, 'scripts')
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
PY = None                     # 由 main() 设定：music-gen 的 .venv python
EXPORT_DIR = os.path.join(ROOT, 'export')       # 交付物也留在同一个文件夹里
TMP_AUDIO = os.path.join(os.environ.get('TEMP', HERE), 'bgm-studio-audio')
STEM_CACHE = None            # main() 里设为 %TEMP%\bgm-studio-audio\stems
JOBS = {}
JOB_SEQ = [0]
RENDER_TASKS = {}              # 编辑器真音源渲染任务（id → {done, r|err}）
LOCK = threading.Lock()

# 面板的音频缓存预算（`prune_tmp_audio` 用）：试听/搜索/配平/分轨每次都往里丢文件，
# **以前从不清理** —— 实测 `%TEMP%\bgm-studio-audio` 涨到 0.93GB（含 12 个 search_* job 目录）。
# 超出预算就按"最旧优先"删；超过 `KEEP_D` 天的也直接删（都是中间产物，删了下次重生成）。
KEEP_MB = 512
KEEP_D = 7


def prune_tmp_audio(root=None, keep_mb=None, keep_days=None, verbose=True):
    """把面板缓存压回预算内（**启动时调用**；返回 (删除条目数, 释放 MB)）。

    判据（两道，谁先命中谁生效）：① 单项**最新写入**超过 `keep_days` 天 → 删；
    ② 仍超 `keep_mb` 预算 → 从最旧的开始删到预算内。
    安全边界：只动 `TMP_AUDIO` 下的**直接子项**（job 目录 / 散落文件），不递归进别人的目录；
    `stems/`、`preview/`、`mixfit/` 这些容器目录本身不删（只删它们里面的 job 子项）。
    """
    import shutil
    root = root or TMP_AUDIO
    keep_mb = KEEP_MB if keep_mb is None else keep_mb
    keep_days = KEEP_D if keep_days is None else keep_days
    if not os.path.isdir(root):
        return 0, 0.0
    keep_top = {'stems', 'preview', 'mixfit'}
    items = []                     # (最新写入, 大小, 路径)
    for p in (os.path.join(root, 'stems'), os.path.join(root, 'preview'),
              os.path.join(root, 'mixfit')):
        if os.path.isdir(p):
            for q in os.listdir(p):
                items.append(os.path.join(p, q))
    items += [os.path.join(root, q) for q in os.listdir(root)
              if q not in keep_top and not q.startswith('.')]

    def info(path):
        sz, mx = 0, 0.0
        if os.path.isdir(path):
            for dp, _dn, fns in os.walk(path):
                for f in fns:
                    fp = os.path.join(dp, f)
                    try:
                        sz += os.path.getsize(fp)
                        mx = max(mx, os.path.getmtime(fp))
                    except OSError:
                        pass
        else:
            try:
                sz, mx = os.path.getsize(path), os.path.getmtime(path)
            except OSError:
                pass
        return sz, mx

    def rm(path):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
            return True
        except OSError:
            return False
    rows = [{'path': p, 'size': info(p)[0], 'mtime': info(p)[1]} for p in items]
    total = sum(r['size'] for r in rows) / 1e6
    cutoff = time.time() - keep_days * 86400
    removed, freed = 0, 0.0
    old = sorted(rows, key=lambda r: r['mtime'])
    for r in old:                                  # ① 过期（按最新写入算）
        if r['mtime'] and r['mtime'] < cutoff and rm(r['path']):
            r['gone'] = True
            removed += 1
            freed += r['size'] / 1e6
            total -= r['size'] / 1e6
    for r in old:                                  # ② 仍超预算 → 最旧的先删
        if total <= keep_mb:
            break
        if r.get('gone') or not rm(r['path']):
            continue
        removed += 1
        freed += r['size'] / 1e6
        total -= r['size'] / 1e6
    if removed and verbose:
        print('[studio] 音频缓存清理：删 %d 项 / %.0fMB（剩余 %.0fMB，预算 %dMB / %d 天）'
              % (removed, freed, max(0.0, total), keep_mb, keep_days))
    return removed, round(freed, 1)


# ------------------------------------------------------------------ 小工具
def py_exe(root=None):
    r = root or ROOT
    p = os.path.join(r, '.venv', 'Scripts', 'python.exe')
    return p if os.path.isfile(p) else sys.executable


def run_py(args, timeout=900, cwd=None):
    """跑工具链脚本（同步）；返回 (rc, 输出文本)"""
    # 工具链的模块都在 <root>/scripts 下：跑 -c 片段时必须让子进程能找到它们
    # （实测踩过：面板保存 song.json 时 ModuleNotFoundError: json_io → 保存按钮直接失败）
    pp = os.path.join(ROOT, 'scripts')
    env = dict(os.environ, PYTHONIOENCODING='utf-8', BGM_STUDIO_ROOT=ROOT,
               PYTHONPATH=pp + (os.pathsep + os.environ['PYTHONPATH'] if os.environ.get('PYTHONPATH') else ''))
    p = subprocess.run([py_exe(), '-X', 'utf8'] + args, cwd=cwd or ROOT, env=env,
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=timeout)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def song_dir(sid):
    d = os.path.join(ROOT, 'songs', sid)
    if not os.path.isfile(os.path.join(d, 'song.json')):
        raise FileNotFoundError('找不到曲目: %s' % sid)
    return d


def read_json(p, default=None):
    """读 JSON；**容忍 UTF-8 BOM**。

    ⚠ 踩过：用外部工具生成 `render.json`（比如 PowerShell 的
    `Set-Content -Encoding UTF8`，它**默认写 BOM**）时，`json.load` 抛
    `Unexpected UTF-8 BOM`，于是面板里那首歌 `has_ogg=False / ref=''` ——
    看起来像"面板不认我的 ogg"，实际是**文件带 BOM**。
    用 `utf-8-sig` 打开即可（无 BOM 时与 `utf-8` 行为一致）。
    """
    try:
        with open(p, encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception:
        return default


def song_key(sid):
    """歌曲指纹（song.json 的 mtime+size）——分轨缓存按它作废"""
    p = os.path.join(song_dir(sid), 'song.json')
    st = os.stat(p)
    return '%d_%d' % (int(st.st_mtime), st.st_size)


def stem_dir(sid):
    return os.path.join(STEM_CACHE, sid, song_key(sid))


def stem_manifest(sid):
    """已缓存的分轨清单 → {cached, tracks:{轨:url}, master:url, dir}"""
    d = stem_dir(sid)
    tracks = {}
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith('.ogg') and not fn.startswith('_'):
                tracks[fn[:-4]] = '/api/stem?id=%s&track=%s' % (
                    urllib.parse.quote(sid), urllib.parse.quote(fn[:-4]))
    return {'cached': bool(tracks), 'tracks': tracks, 'dir': d,
            'master': '/api/audio?id=%s&kind=mix&t=%s' % (urllib.parse.quote(sid), song_key(sid)),
            'key': song_key(sid)}


def first_ref():
    """refs/*.json 里的第一个画像名（新建曲目时当默认参考）"""
    d = os.path.join(ROOT, 'refs')
    names = [f[:-5] for f in sorted(os.listdir(d))] if os.path.isdir(d) else []
    for n in names:
        if n.startswith('BGM'):
            return n
    return names[0] if names else 'BGM16c'


def songs_list():
    base = os.path.join(ROOT, 'songs')
    out = []
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        d = os.path.join(base, name)
        sj = os.path.join(d, 'song.json')
        if not os.path.isfile(sj):
            continue
        s = read_json(sj) or {}
        r = read_json(os.path.join(d, 'render.json')) or {}
        bars = sum(sec.get('bars', 0) for sec in s.get('sections', []))
        bpm = s.get('bpm') or 120
        out.append({'id': name, 'name': s.get('name') or name, 'style': s.get('style', ''),
                    'bpm': bpm, 'bars': bars,
                    'seconds': round(bars * 4 * 60.0 / bpm, 1) if bars else 0,
                    'ref': r.get('ref', ''), 'tracks': len(s.get('sections', [])),
                    'has_ogg': os.path.isfile(os.path.join(d, (r.get('out') or '') + '.ogg')),
                    'mtime': os.path.getmtime(sj)})
    return out


# ---------------------------------------------------------------------------
# MIDI 编辑器（对标 miditoolbox）：会话 = 一份可编辑的 MIDI 模型
#
# 为什么是"会话"而不是直接改 songs/<曲>/：导入的**外部 .mid 不属于曲库**（没有 song.json
# 契约、也不是我们的引擎产物），编辑它不该污染 songs/。所以会话存在临时目录
# （`%TEMP%\bgm-studio-edits\<id>\`），导出时再落成 .mid；想接引擎（渲染/指标）时
# 另有"送入曲库"的路（后续接）。
EDITS_DIR = os.path.join(os.environ.get('TEMP', HERE), 'bgm-studio-edits')


def edit_dir(eid, create=False):
    """会话目录（**id 白名单校验**：只允许 [A-Za-z0-9_-]，防路径穿越）"""
    if not re.match(r'^[A-Za-z0-9_\-]{1,64}$', eid or ''):
        raise ValueError('非法会话 id')
    d = os.path.join(EDITS_DIR, eid)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def edit_list():
    out = []
    if os.path.isdir(EDITS_DIR):
        for name in sorted(os.listdir(EDITS_DIR)):
            p = os.path.join(EDITS_DIR, name, 'model.json')
            if not os.path.isfile(p):
                continue
            try:
                m = read_json(p) or {}
            except Exception:
                continue
            st = m.get('_stats') or {}
            out.append({'id': name, 'title': m.get('title') or name,
                        'tracks': len(m.get('tracks') or []), 'notes': st.get('notes', 0),
                        'bpm': m.get('bpm'), 'timesig': m.get('timesig'),
                        'format': m.get('format'), 'end_beat': m.get('end_beat'),
                        'source': m.get('source'), 'dirty': m.get('_dirty', False),
                        'mtime': os.path.getmtime(p)})
    return out


def edit_load(eid):
    p = os.path.join(edit_dir(eid), 'model.json')
    if not os.path.isfile(p):
        raise FileNotFoundError('没有这个编辑会话：%s' % eid)
    return read_json(p)


def edit_save(eid, model, dirty=True):
    """落盘会话（带统计，UI 不用自己算）"""
    import midi_ops as _mo
    model = dict(model or {})
    model['_stats'] = _mo.stats(model)
    model['_dirty'] = bool(dirty)
    model['_saved_at'] = time.time()
    d = edit_dir(eid, create=True)
    with open(os.path.join(d, 'model.json'), 'w', encoding='utf-8', newline='') as f:
        json.dump(model, f, ensure_ascii=False)
    return model


def edit_import_bytes(name, data):
    """上传的 .mid 字节 → 新会话（返回会话 id 与摘要）"""
    import midi_file as _mf
    eid = uuid.uuid4().hex[:12]
    d = edit_dir(eid, create=True)
    src = os.path.join(d, 'source.mid')
    with open(src, 'wb') as f:
        f.write(data)
    model = _mf.import_midi(src, title=os.path.splitext(os.path.basename(name or 'imported'))[0])
    edit_save(eid, model, dirty=False)
    return eid, model


def edit_render_task(jid):
    """查渲染任务状态（前端轮询）"""
    with LOCK:
        t = RENDER_TASKS.get(jid)
    if not t:
        raise FileNotFoundError('没有这个渲染任务：%s' % jid)
    return t


def edit_render_start(eid, model=None, force=False):
    """**后台**渲染真音源音频（用户口径"比不上主界面"）。

    为什么必须异步：整曲渲染实测要几十秒（123 秒的歌约 1 分钟，含 FluidSynth + OGG 编码），
    同步做会把面板卡住、还会让前端的请求超时。所以：先返回一个 `task` id，
    前端轮询 `/api/ed/render-status`；期间继续用合成音播放，渲染好了再切过去。
    命中缓存则直接返回结果（改音符才会换指纹 → 重渲）。
    """
    import hashlib
    m = model if model is not None else edit_load(eid)
    tr = m.get('tracks') or []
    fp = hashlib.md5(('%d|%.3f|%.3f|%d|%d' % (
        sum(len(t.get('notes') or []) for t in tr),
        max([n[0] + n[1] for t in tr for n in (t.get('notes') or [])] or [0.0]),
        float(m.get('bpm') or 120), len(tr),
        sum(len(t.get('ccs') or []) for t in tr))).encode()).hexdigest()[:12]
    d = edit_dir(eid, create=True)
    ogg = os.path.join(d, 'render_%s.ogg' % fp)
    meta = os.path.join(d, 'render_%s.json' % fp)
    url = '/api/ed/audio?eid=%s&v=%s' % (urllib.parse.quote(eid), fp)
    if os.path.isfile(ogg) and not force:
        info = read_json(meta) or {}
        return {'r': {'cached': True, 'url': url, 'bytes': os.path.getsize(ogg),
                      'seconds': info.get('seconds'), 'fp': fp}}
    jid = uuid.uuid4().hex[:10]

    def _work():
        try:
            r = edit_render_audio(eid, model=m)
            with LOCK:
                RENDER_TASKS[jid] = {'done': True, 'at': time.time(), 'r': r}
        except Exception as e:                                  # noqa: BLE001
            with LOCK:
                RENDER_TASKS[jid] = {'done': True, 'at': time.time(),
                                     'err': '%s: %s' % (type(e).__name__, e)}
    th = threading.Thread(target=_work, daemon=True)
    with LOCK:
        RENDER_TASKS[jid] = {'done': False, 'at': time.time()}
    th.start()
    return {'r': None, 'task': jid}


def edit_render_task(jid):
    """查渲染任务状态（前端轮询）"""
    with LOCK:
        t = RENDER_TASKS.get(jid)
    if not t:
        raise FileNotFoundError('没有这个渲染任务：%s' % jid)
    return t


def edit_render_audio(eid, model=None, force=False):
    """编辑会话 → **真实音源渲染的音频**（与引擎面板同一条 `render_midi.py` 管线）。

    为什么要有（用户口径："还是比不上主界面"）：引擎面板放的是 GeneralUser GS 渲染出来的
    真音频，编辑器原来只有 WebAudio 合成音 —— 合成得再复杂也比不过采样音源。
    这里把编辑器的当前模型导出成 MIDI、走同一条渲染链路，前端直接 `<audio>` 播放它。
    缓存键 = 模型的"音符指纹"（音数/末拍/轨数/BPM/CC 数），**改了音符才会重渲**。
    """
    import hashlib
    import midi_file as _mf
    m = model if model is not None else edit_load(eid)
    tr = m.get('tracks') or []
    fp = hashlib.md5(('%d|%.3f|%.3f|%d|%d' % (
        sum(len(t.get('notes') or []) for t in tr),
        max([n[0] + n[1] for t in tr for n in (t.get('notes') or [])] or [0.0]),
        float(m.get('bpm') or 120), len(tr),
        sum(len(t.get('ccs') or []) for t in tr))).encode()).hexdigest()[:12]
    d = edit_dir(eid, create=True)
    ogg = os.path.join(d, 'render_%s.ogg' % fp)
    meta = os.path.join(d, 'render_%s.json' % fp)
    url = '/api/ed/audio?eid=%s&v=%s' % (urllib.parse.quote(eid), fp)
    if os.path.isfile(ogg) and not force:
        info = read_json(meta) or {}
        return {'cached': True, 'url': url, 'bytes': os.path.getsize(ogg),
                'seconds': info.get('seconds'), 'fp': fp}
    mid = os.path.join(d, 'render_%s.mid' % fp)
    _mf.export_midi(m, mid, fmt=1)
    # ⚠ 渲染必须在**短路径 + 唯一名**下做：
    #   ① `render_midi.py` 会在输出基名旁写 `<base>.raw.wav` 中间文件，长路径（面板会话目录
    #      带 eid 与指纹）会超限 → FluidSynth 写不出来 → `os.remove(raw)` 抛 FileNotFoundError；
    #   ② **名字必须唯一**：两个渲染任务用同一个短名时会互相删对方的 raw.wav
    #      （实测"手工渲染成功、走面板必失败"，就是并发/重名导致的）。
    short = os.path.join(tempfile.gettempdir(),
                         'edr_%s_%s' % (fp, uuid.uuid4().hex[:6]))
    rc, out = run_py(['scripts/render_midi.py', mid, short], timeout=900)
    if rc != 0 or not os.path.isfile(short + '.ogg'):
        errp = os.path.join(d, 'render_err.txt')
        with open(errp, 'w', encoding='utf-8', newline='') as f:
            f.write('rc=%s\nmid=%s\nshort=%s\n\n%s' % (rc, mid, short, out or '(无输出)'))
        raise RuntimeError('渲染失败（rc=%s）· 详细日志：%s\n%s'
                           % (rc, errp, (out or '')[-1200:]))
    try:
        os.replace(short + '.ogg', ogg)
    except OSError:
        shutil.copyfile(short + '.ogg', ogg)
    for ext in ('.ogg', '.wav', '.raw.wav'):
        try:
            os.remove(short + ext)
        except OSError:
            pass
    secs = None
    try:
        import soundfile as _sf
        secs = round(_sf.info(ogg).duration, 2)
    except Exception:
        pass
    with open(meta, 'w', encoding='utf-8', newline='') as f:
        json.dump({'fp': fp, 'seconds': secs, 'bytes': os.path.getsize(ogg),
                   'at': time.time()}, f, ensure_ascii=False)
    return {'cached': False, 'url': url, 'bytes': os.path.getsize(ogg),
            'seconds': secs, 'fp': fp, 'log': (out or '')[-800:]}


def edit_import_path(path):
    """从服务器本机路径导入（**只能在 ROOT 之内**：面板不该读任意文件）"""
    p = os.path.abspath(path)
    root = os.path.abspath(ROOT)
    if not p.startswith(root):
        raise ValueError('只允许导入工具链目录内的文件')
    if not os.path.isfile(p):
        raise FileNotFoundError(path)
    with open(p, 'rb') as f:
        return edit_import_bytes(os.path.basename(p), f.read())


def edit_apply_op(eid, op, params, model=None):
    """执行一个编辑操作（`scripts/midi_ops.py` 是唯一口径，UI 与自检共用）"""
    import midi_ops as _mo
    m = model if model is not None else edit_load(eid)
    fn = _mo.resolve_op(op)                 # 支持短名别名（velocity/ramp/snap…）
    report = fn(m, **(params or {}))
    edit_save(eid, m)
    return {'report': report, 'stats': _mo.stats(m)}


def edit_chords(model, step=None, create=False, merge=True):
    """和弦检测（`scripts/midi_chords.py` 是唯一口径）；`create=True` 顺带写一条和弦轨"""
    import midi_chords as _mc
    segs = _mc.scan(model, step=step, merge=merge)
    out = [{'from': a, 'to': b, 'chord': nm, 'score': round(s, 3),
            'hit': (d or {}).get('hit'), 'extra': (d or {}).get('extra'),
            'miss': (d or {}).get('miss'), 'notes': (d or {}).get('notes')}
           for (a, b, nm, s, d) in segs]
    r = None
    if create:
        r = _mc.chords_track(model, segs)
    return {'segments': out, 'track': r, 'bar': _mc.beat_bar(model)}


def _editor_summary(model):
    """模型摘要（导入后给 UI 一屏信息：轨名/音数/音域/速度）"""
    import midi_probe as _mp
    import midi_ops as _mo
    st = _mo.stats(model)
    tr = []
    for t in model.get('tracks') or []:
        ns = t.get('notes') or []
        ps = [n[2] for n in ns]
        tr.append({'index': t.get('index'), 'name': t.get('name'),
                   'channel': t.get('channel'), 'program': t.get('program'),
                   'drum': t.get('drum'), 'notes': len(ns),
                   'range': [_mp.note_name(min(ps)), _mp.note_name(max(ps))] if ps else None,
                   'vel': [min([n[3] for n in ns] or [0]), max([n[3] for n in ns] or [0])]})
    return {'title': model.get('title'), 'format': model.get('format'),
            'division': model.get('division'), 'bpm': model.get('bpm'),
            'timesig': model.get('timesig'), 'stats': st, 'tracks': tr}


def start_job(sid, kind, opts=None):
    """起后台任务；返回 jobId。kind: compose / render / render-tune / solo:<轨> / export[:stems]"""
    opts = opts or {}
    song = os.path.join(song_dir(sid), 'song.json')
    jobs_dir = TMP_AUDIO
    os.makedirs(jobs_dir, exist_ok=True)
    if kind == 'compose':
        cmds, outs = [[os.path.join(song_dir(sid), 'compose.py')]], ''
    elif kind == 'render':
        # 快渲染 + 收尾（宽度校准/重编码）——不校宽的话成品宽度会偏宽（0.96 vs 0.51）
        cmds = [['scripts/make_song.py', sid, '--no-tune'],
                [BRIDGE, 'finalize', song]]
        outs = ''
    elif kind == 'render-raw':
        cmds, outs = [['scripts/make_song.py', sid, '--no-tune']], ''
    elif kind == 'preview':
        po = ['--sec', str(int(opts.get('sec', 0))), '--bars', str(int(opts.get('bars', 8)))]
        cmds = [[os.path.join(HERE, 'tools', 'preview_job.py'), sid] + po]
        outs = ''
    elif kind == 'search':
        so = ['--bars', str(int(opts.get('bars', 8))),
              '--budget', str(int(opts.get('budget', 48))),
              '--time', str(float(opts.get('time', 120))),
              '--workers', str(int(opts.get('workers', 4)))]
        if opts.get('sec') is not None and int(opts.get('sec', -1)) >= 0:
            so += ['--sec', str(int(opts['sec']))]
        # 默认**不写回**：搜索结果先给面板审（实测整包采用会把性格参数也写进去 → 变差）
        if opts.get('apply_keys'):
            so += ['--apply-keys', str(opts['apply_keys'])]
        elif opts.get('apply'):
            so.append('--apply')
        cmds = [[os.path.join(HERE, 'tools', 'search_job.py'), sid] + so]
        outs = ''
    elif kind == 'mixfit':
        # 解方程 → 真渲染 → 实测 → 更好才留（tools/mixfit_job.py 里做闭环与回滚）
        cmds = [[os.path.join(HERE, 'tools', 'mixfit_job.py'), sid, '--max-pass', '2']]
        outs = ''
    elif kind == 'render-tune':
        cmds, outs = [['scripts/make_song.py', sid]], ''
    elif kind.startswith('solo:'):
        tr = kind.split(':', 1)[1]
        out = os.path.join(jobs_dir, 'solo_%s_%d.ogg' % (re.sub(r'\W+', '', tr), int(time.time())))
        cmds, outs = [[BRIDGE, 'solo', song, '--track', tr, '--out', out]], out
    elif kind == 'stems-cache':
        cmds, outs = [[BRIDGE, 'stems', song, '--to', stem_dir(sid)]], stem_dir(sid)
    elif kind.startswith('export'):
        out = os.path.join(EXPORT_DIR, sid)
        cmd = [BRIDGE, 'export', song, '--to', EXPORT_DIR]
        if kind.endswith('stems'):
            cmd.append('--stems')
        cmds, outs = [cmd], out
    else:
        raise ValueError('未知任务类型: %s' % kind)
    with LOCK:
        JOB_SEQ[0] += 1
        jid = '%s-%d' % (sid, JOB_SEQ[0])
        JOB = {'id': jid, 'song': sid, 'kind': kind, 'state': 'running', 'log': [],
               'rc': None, 'out': outs, 'started': time.time(), 'ended': None}
        JOBS[jid] = JOB

    def worker():
        env = dict(os.environ, PYTHONIOENCODING='utf-8', BGM_STUDIO_ROOT=ROOT)
        try:
            rc = 0
            for i, c in enumerate(cmds):
                if len(cmds) > 1:
                    with LOCK:
                        JOB['log'].append('=== [%d/%d] %s ===' % (i + 1, len(cmds), ' '.join(c[-2:])))
                p = subprocess.Popen([py_exe(), '-X', 'utf8'] + c, cwd=ROOT, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding='utf-8', errors='replace', bufsize=1)
                with LOCK:
                    JOB['proc'] = p
                for line in p.stdout:
                    with LOCK:
                        JOB['log'].append(line.rstrip('\n'))
                        if len(JOB['log']) > 500:
                            del JOB['log'][:200]
                p.wait()
                rc = p.returncode
                if rc != 0:
                    break
            JOB['rc'] = rc
            if JOB.get('state') != 'stopped':
                JOB['state'] = 'done' if rc == 0 else 'failed'
        except Exception as e:                                  # noqa: BLE001
            JOB['state'] = 'failed'
            JOB['log'].append('启动失败: %s' % e)
            JOB['rc'] = -1
        JOB['ended'] = time.time()

    threading.Thread(target=worker, daemon=True).start()
    return jid


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = 'bgm-studio/0.1'

    def log_message(self, fmt, *args):        # 安静点：只留错误
        if ' 4' in (fmt % args)[:6] or ' 5' in (fmt % args)[:6]:
            sys.stderr.write('[studio] %s\n' % (fmt % args))

    # ---------- 基础
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('content-type', 'application/json; charset=utf-8')
        self.send_header('content-length', str(len(body)))
        self.send_header('cache-control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('pragma', 'no-cache')
        self.send_header('expires', '0')
        self.end_headers()
        self.wfile.write(body)

    def _err(self, msg, code=400):
        self._json({'ok': False, 'error': str(msg)}, code)

    def _stop_job(self, jid):
        """停掉一个后台任务（杀进程树）。GET/POST 都能进（面板用 POST）。"""
        j = JOBS.get(jid)
        if not j:
            return self._err('没有这个任务（服务可能重启过）: %s' % jid, 404)
        p = j.get('proc')
        try:
            if p and p.poll() is None:
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)],
                               capture_output=True)
            j['state'] = 'stopped'
        except Exception as e:                                  # noqa: BLE001
            return self._err('停止失败: %s' % e, 500)
        return self._json({'ok': True, 'job': jid, 'state': 'stopped'})

    def _body(self):
        n = int(self.headers.get('content-length') or 0)
        raw = self.rfile.read(n) if n else b'{}'
        return json.loads(raw.decode('utf-8') or '{}')

    def _file(self, path, ctype=None, download=False):
        if not os.path.isfile(path):
            return self._err('文件不存在: %s' % path, 404)
        size = os.path.getsize(path)
        rng = self.headers.get('range')
        ctype = ctype or {'.ogg': 'audio/ogg', '.wav': 'audio/wav', '.mp3': 'audio/mpeg',
                          '.json': 'application/json; charset=utf-8',
                          '.html': 'text/html; charset=utf-8',
                          '.js': 'text/javascript; charset=utf-8',
                          '.css': 'text/css; charset=utf-8'}.get(
            os.path.splitext(path)[1].lower(), 'application/octet-stream')
        start, end = 0, size - 1
        code = 200
        if rng:
            m = re.match(r'bytes=(\d*)-(\d*)', rng.strip())
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
                code = 206
        self.send_response(code)
        self.send_header('content-type', ctype)
        self.send_header('accept-ranges', 'bytes')
        self.send_header('content-length', str(end - start + 1))
        self.send_header('cache-control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('pragma', 'no-cache')
        self.send_header('expires', '0')
        if download:
            self.send_header('content-disposition',
                             'attachment; filename="%s"' % os.path.basename(path))
        if code == 206:
            self.send_header('content-range', 'bytes %d-%d/%d' % (start, end, size))
        self.end_headers()
        with open(path, 'rb') as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(262144, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    # ---------- 路由
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        sid = (q.get('id') or [''])[0]
        try:
            if u.path == '/' or u.path == '/index.html':
                return self._file(os.path.join(WEB, 'index.html'))
            if u.path == '/editor' or u.path == '/ed.html':
                return self._file(os.path.join(WEB, 'ed.html'))
            if re.match(r'^/[A-Za-z0-9_.\-]+\.(js|css|png|svg|ico|woff2?)$', u.path):
                p = os.path.abspath(os.path.join(WEB, u.path.lstrip('/')))
                if not p.startswith(os.path.abspath(WEB)):
                    return self._err('路径越界', 400)
                return self._file(p)
            if u.path == '/api/songs':
                return self._json({'ok': True, 'root': ROOT, 'songs': songs_list(),
                                   'python': py_exe()})
            if u.path == '/api/song':
                d = song_dir(sid)
                rc, ev = run_py(['scripts/song_events.py', os.path.join(d, 'song.json'), '--json'])
                events = json.loads(ev) if rc == 0 and ev.strip().startswith('{') else None
                return self._json({'ok': True, 'song': read_json(os.path.join(d, 'song.json')),
                                   'render': read_json(os.path.join(d, 'render.json')) or {},
                                   'events': events, 'events_err': None if events else ev[-800:]})
            if u.path == '/api/stop':
                return self._stop_job((q.get('id') or [''])[0])
            if u.path == '/api/job':
                jid = (q.get('id') or [''])[0]
                j = JOBS.get(jid)
                if not j:
                    return self._err('没有这个任务', 404)
                return self._json({'ok': True, 'job': {k: j[k] for k in
                                                       ('id', 'song', 'kind', 'state', 'rc', 'out')},
                                   'log': '\n'.join(j['log'][-200:])})
            if u.path == '/api/audio':
                kind = (q.get('kind') or ['mix'])[0]
                if kind == 'mix':
                    d = song_dir(sid)
                    r = read_json(os.path.join(d, 'render.json')) or {}
                    for ext in ('.ogg', '.wav'):
                        p = os.path.join(d, (r.get('out') or '') + ext)
                        if os.path.isfile(p):
                            return self._file(p)
                    return self._err('还没渲染', 404)
                if kind == 'solo':
                    tr = re.sub(r'\W+', '', (q.get('track') or [''])[0])
                    cand = [f for f in os.listdir(TMP_AUDIO) if f.startswith('solo_%s_' % tr)]
                    if not cand:
                        return self._err('这一轨还没试听渲染', 404)
                    cand.sort(key=lambda f: os.path.getmtime(os.path.join(TMP_AUDIO, f)))
                    return self._file(os.path.join(TMP_AUDIO, cand[-1]))
                if kind == 'preview':
                    sec = re.sub(r'\W+', '', str((q.get('sec') or ['0'])[0]))
                    d = os.path.join(TMP_AUDIO, 'preview', sid)
                    cand = [f for f in os.listdir(d) if f.startswith('sec%s_' % sec)] \
                        if os.path.isdir(d) else []
                    if not cand:
                        return self._err('这一段还没渲染过（点"⚡ 试听本段"）', 404)
                    cand.sort(key=lambda f: os.path.getmtime(os.path.join(d, f)))
                    return self._file(os.path.join(d, cand[-1]))
                if kind == 'stem':
                    tr = (q.get('track') or [''])[0]
                    p = os.path.join(EXPORT_DIR, sid, 'stems', tr + '.ogg')
                    return self._file(p)
                return self._err('未知音频类型', 400)
            if u.path == '/api/stems':
                return self._json({'ok': True, **stem_manifest(sid)})
            if u.path == '/api/stem':
                tr = (q.get('track') or [''])[0]
                p = os.path.join(stem_dir(sid), tr + '.ogg')
                return self._file(p)
            if u.path == '/api/ref-audio':
                r = read_json(os.path.join(song_dir(sid), 'render.json')) or {}
                rp = os.path.join(ROOT, 'refs', '%s.json' % (r.get('ref') or 'BGM16c'))
                ref = read_json(rp) or {}
                f = ref.get('file')
                if not f or not os.path.isfile(f):
                    return self._err('参考曲音频不可用（画像里没有 file）', 404)
                return self._file(f)
            if u.path == '/api/metrics':
                args = [BRIDGE, 'metrics', os.path.join(song_dir(sid), 'song.json')]
                if (q.get('win') or [''])[0]:
                    args += ['--win', (q.get('win') or [''])[0]]
                rc, out = run_py(args)
                try:
                    return self._json(json.loads(out.strip().splitlines()[-1]))
                except Exception:
                    return self._json({'ok': False, 'error': out[-600:]})
            if u.path == '/api/files':
                d = song_dir(sid)
                fs = []
                for fn in sorted(os.listdir(d)):
                    p = os.path.join(d, fn)
                    if os.path.isfile(p):
                        fs.append({'name': fn, 'size': os.path.getsize(p),
                                   'mtime': os.path.getmtime(p),
                                   'url': '/api/dl?id=%s&f=%s' % (sid, urllib.parse.quote(fn))})
                return self._json({'ok': True, 'dir': d, 'files': fs})
            if u.path == '/api/dl':
                fn = (q.get('f') or [''])[0]
                p = os.path.abspath(os.path.join(song_dir(sid), fn))
                if not p.startswith(os.path.abspath(song_dir(sid))):
                    return self._err('路径越界', 400)
                return self._file(p, download=True)
            # ---------------- MIDI 编辑器 ----------------
            if u.path == '/api/ed/list':
                return self._json({'ok': True, 'dir': EDITS_DIR, 'edits': edit_list()})
            if u.path == '/api/ed/model':
                eid = (q.get('eid') or [''])[0]
                return self._json({'ok': True, 'eid': eid, 'model': edit_load(eid)})
            if u.path == '/api/ed/render-status':
                t = edit_render_task((q.get('t') or [''])[0])
                if not t.get('done'):
                    return self._json({'ok': True, 'done': False,
                                       'waited': round(time.time() - t.get('at', time.time()), 1)})
                if t.get('err'):
                    return self._json({'ok': False, 'done': True, 'error': t['err']})
                return self._json(dict({'ok': True, 'done': True}, **t['r']))
            if u.path == '/api/ed/audio':
                eid = (q.get('eid') or [''])[0]
                v = re.sub(r'\W+', '', (q.get('v') or [''])[0])
                p = os.path.join(edit_dir(eid), 'render_%s.ogg' % v)
                if not (v and os.path.isfile(p)):
                    return self._err('这段音频还没渲染（先点「🎧 渲染音频」）', 404)
                return self._file(p)
            if u.path == '/api/ed/download':
                eid = (q.get('eid') or [''])[0]
                d = edit_dir(eid)
                want = 'export_fmt0.mid' if (q.get('fmt') or ['1'])[0] == '0' else 'export.mid'
                p = os.path.join(d, want)
                if not os.path.isfile(p):
                    return self._err('还没导出过（先点导出）', 404)
                return self._file(p, download=True)
            return self._err('未知路由: %s' % u.path, 404)
        except FileNotFoundError as e:
            return self._err(e, 404)
        except Exception as e:                                  # noqa: BLE001
            return self._err('%s: %s' % (type(e).__name__, e), 500)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        sid = (q.get('id') or [''])[0]
        try:
            if u.path == '/api/song':
                body = self._body()
                song = body.get('song') or body
                d = song_dir(sid)
                tmp = os.path.join(d, 'song.json.tmp')
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(song, f, ensure_ascii=False, indent=1)
                rc, out = run_py(['-c',
                                  'import sys,json_io;json_io.normalize(sys.argv[1])', tmp])
                if rc != 0:
                    os.remove(tmp)
                    return self._json({'ok': False, 'error': '规范写回失败: ' + out[-400:]})
                os.replace(tmp, os.path.join(d, 'song.json'))
                return self._json({'ok': True, 'saved': os.path.join(d, 'song.json'),
                                   'log': out[-400:]})
            if u.path == '/api/new':
                body = self._body()
                nid = (body.get('id') or '').strip()
                if not re.match(r'^[0-9A-Za-z_][0-9A-Za-z_-]{0,40}$', nid):
                    return self._err('曲目名只能用字母/数字/下划线（例：24_my_song）')
                ref = body.get('ref') or first_ref()
                # **模板依据走主题模板包**（用户口径：一次生成依据同主题 ≥8 首白名单模板）。
                # 老 `--from <现成曲目>` 仍可用，但它会被 check_song 判为"依据不合规"。
                theme = (body.get('theme') or '').strip()
                src = (body.get('from') or '').strip()
                if theme:
                    args = ['scripts/new_song.py', nid, '--theme', theme]
                else:
                    src = src or '05_d135_cheerful'
                    args = ['scripts/new_song.py', nid, '--from', src,
                            '--style', (body.get('style') or 'daily')]
                args += ['--ref', ref]
                rc, out = run_py(args, timeout=300)
                return self._json({'ok': rc == 0, 'rc': rc, 'log': out[-3000:],
                                   'id': nid, 'theme': theme, 'from': src, 'ref': ref})
            if u.path == '/api/stop':
                return self._stop_job((q.get('id') or [''])[0])
            if u.path == '/api/check':
                rc, out = run_py(['scripts/check_song.py', sid])
                ok = '数据契约没问题' in out
                # 沙箱里那份清单含"交付物/跨曲目"类噪声，只把 **[数据契约]** 的报出来
                fails = re.findall(r'^  - \[数据契约\] (\S+) → (.*)$', out, re.M)
                warns = re.findall(r'^  - \[其它\] (\S+) → (.*)$', out, re.M)
                return self._json({'ok': ok, 'rc': rc, 'log': out[-4000:],
                                   'fails': [{'name': a, 'why': b} for a, b in fails][:20],
                                   'other': len(warns)})
            if u.path == '/api/job':
                kind = (q.get('kind') or ['render'])[0]
                body = self._body() if int(self.headers.get('content-length') or 0) else {}
                opts = (body or {}).get('opts') or {}
                save = body or None
                if save and (save.get('song') or save.get('sections')):
                    song = save.get('song') or save
                    d = song_dir(sid)
                    with open(os.path.join(d, 'song.json'), 'w', encoding='utf-8') as f:
                        json.dump(song, f, ensure_ascii=False, indent=1)
                    run_py(['-c', 'import sys,json_io;json_io.normalize(sys.argv[1])',
                            os.path.join(d, 'song.json')])
                jid = start_job(sid, kind, opts)
                return self._json({'ok': True, 'job': jid})
            # ---------------- MIDI 编辑器 ----------------
            if u.path == '/api/ed/import':
                body = self._body()
                if body.get('path'):
                    eid, model = edit_import_path(body['path'])
                else:
                    raw = base64.b64decode(body.get('data_b64') or '')
                    if not raw:
                        return self._err('没有收到文件内容（data_b64 为空）')
                    if raw[:4] != b'MThd':
                        return self._err('这不是标准 MIDI 文件（缺 MThd 头）')
                    eid, model = edit_import_bytes(body.get('name') or 'imported.mid', raw)
                return self._json({'ok': True, 'eid': eid,
                                   'summary': _editor_summary(model),
                                   'model': model})
            if u.path == '/api/ed/op':
                body = self._body()
                eid = (q.get('eid') or [''])[0]
                op = body.get('op') or ''
                r = edit_apply_op(eid, op, body.get('params') or {}, model=body.get('model'))
                return self._json(dict({'ok': True, 'op': op}, **r))
            if u.path == '/api/ed/chords':
                body = self._body()
                eid = (q.get('eid') or [''])[0]
                model = body.get('model') or edit_load(eid)
                step = body.get('step')
                creating = bool(body.get('create_track'))
                segs = edit_chords(model, step=step, create=creating,
                                   merge=body.get('merge', True))
                if creating:
                    edit_save(eid, model)          # 和弦轨是新轨 → 立刻落盘
                return self._json({'ok': True, 'chords': segs,
                                   'model': model if creating else None})
            if u.path == '/api/ed/render-audio':
                body = self._body()
                eid = (q.get('eid') or [''])[0]
                r = edit_render_start(eid, model=body.get('model'),
                                      force=bool(body.get('force')))
                if r.get('r'):
                    return self._json(dict({'ok': True}, **r['r']))
                return self._json({'ok': True, 'task': r['task'], 'pending': True})
            if u.path == '/api/ed/save':
                body = self._body()
                eid = (q.get('eid') or [''])[0]
                model = body.get('model')
                if not model:
                    return self._err('body 里没有 model')
                m = edit_save(eid, model)
                return self._json({'ok': True, 'stats': m['_stats']})
            if u.path == '/api/ed/export':
                import midi_file as _mf
                body = self._body()
                eid = (q.get('eid') or [''])[0]
                model = body.get('model') or edit_load(eid)
                fmt = int(body.get('fmt') or 1)
                d = edit_dir(eid, create=True)
                name = 'export.mid' if fmt != 0 else 'export_fmt0.mid'
                out = os.path.join(d, name)
                real = _mf.export_midi(model, out, fmt=fmt)
                edit_save(eid, model)
                rt = _mf.roundtrip_report(out, os.path.join(d, 'rt_check.mid'))
                return self._json({'ok': True, 'file': out, 'fmt': real,
                                   'bytes': os.path.getsize(out),
                                   'url': '/api/ed/download?eid=%s&fmt=%d'
                                          % (urllib.parse.quote(eid), real),
                                   'roundtrip': {'ok': rt['ok'], 'exact': rt['exact'],
                                                 'net': rt['net'], 'bad': rt['bad'],
                                                 'notes': rt['notes']}})
            return self._err('未知路由: %s' % u.path, 404)
        except FileNotFoundError as e:
            return self._err(e, 404)
        except Exception as e:                                  # noqa: BLE001
            return self._err('%s: %s' % (type(e).__name__, e), 500)


def main():
    global ROOT, PY, EXPORT_DIR, TMP_AUDIO
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--root', default=ROOT, help='music-gen 目录')
    ap.add_argument('--export-dir', default=EXPORT_DIR)
    ap.add_argument('--open', action='store_true', help='启动后打开浏览器')
    ap.add_argument('--keep-tmp-audio', action='store_true',
                    help='不清理音频缓存（默认启动时按预算清，见 prune_tmp_audio）')
    a = ap.parse_args()
    ROOT, EXPORT_DIR = os.path.abspath(a.root), os.path.abspath(a.export_dir)
    os.makedirs(EXPORT_DIR, exist_ok=True)
    os.makedirs(TMP_AUDIO, exist_ok=True)
    global STEM_CACHE
    STEM_CACHE = os.path.join(TMP_AUDIO, 'stems')
    os.makedirs(STEM_CACHE, exist_ok=True)
    # **启动时把音频缓存压回预算**：试听/搜索/配平/分轨每次都往里丢文件，以前从不清理
    # （实测涨到 0.93GB / 12 个 job 目录）。`--keep-tmp-audio` 可跳过（排查缓存相关问题时用）。
    if not a.keep_tmp_audio:
        prune_tmp_audio()
    PY = py_exe()
    srv = ThreadingHTTPServer(('127.0.0.1', a.port), Handler)
    url = 'http://127.0.0.1:%d/' % a.port
    print('BGM Studio 已启动: %s' % url)
    print('  music-gen: %s' % ROOT)
    print('  python   : %s' % PY)
    print('  导出目录 : %s' % EXPORT_DIR)
    print('  Ctrl+C 退出')
    if a.open:
        try:
            os.startfile(url)                                   # noqa: S606
        except Exception:                                       # noqa: BLE001
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')


if __name__ == '__main__':
    main()