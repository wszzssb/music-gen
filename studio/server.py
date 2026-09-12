#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""server.py —— BGM Studio 本地服务：静态页面 + JSON API（驱动 music-gen 工具链）。

只依赖 Python 标准库（http.server）+ music-gen 的 .venv；**零构建、零额外依赖**。
音频/分析/渲染全部交给工具链自己的脚本，本文件不实现任何 DSP：
  make_song.py（作曲+渲染+调参+成绩单）/ check_song.py（数据契约）/ song_events.py（逐轨事件）
  / bridge.py（指标 / 分轨 / 导出）

启动:
  python server.py [--port 8765] [--root D:\\software\\skill] [--open]
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
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, 'web')
BRIDGE = os.path.join(HERE, 'bridge.py')
# 路径**相对化**：面板就在工具链目录下（studio/），root 默认取父目录；可用 --root / env 覆盖
ROOT = os.environ.get('BGM_STUDIO_ROOT') or os.path.abspath(os.path.join(HERE, '..'))
PY = None                     # 由 main() 设定：music-gen 的 .venv python
EXPORT_DIR = os.path.join(ROOT, 'export')       # 交付物也留在同一个文件夹里
TMP_AUDIO = os.path.join(os.environ.get('TEMP', HERE), 'bgm-studio-audio')
STEM_CACHE = None            # main() 里设为 %TEMP%\bgm-studio-audio\stems
JOBS = {}
JOB_SEQ = [0]
LOCK = threading.Lock()


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
    try:
        with open(p, encoding='utf-8') as f:
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


def start_job(sid, kind, opts=None):
    opts = opts or {}
    """起后台任务；返回 jobId。kind: compose / render / render-tune / solo:<轨> / export[:stems]"""
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
        self.send_header('cache-control', 'no-store')
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
        self.send_header('cache-control', 'no-store')
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
                tpl = body.get('from') or '05_d135_cheerful'
                ref = body.get('ref') or first_ref()
                style = body.get('style') or 'daily'
                rc, out = run_py(['scripts/new_song.py', nid, '--from', tpl,
                                  '--ref', ref, '--style', style], timeout=300)
                return self._json({'ok': rc == 0, 'rc': rc, 'log': out[-3000:],
                                   'id': nid, 'from': tpl, 'ref': ref, 'style': style})
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
    a = ap.parse_args()
    ROOT, EXPORT_DIR = os.path.abspath(a.root), os.path.abspath(a.export_dir)
    os.makedirs(EXPORT_DIR, exist_ok=True)
    os.makedirs(TMP_AUDIO, exist_ok=True)
    global STEM_CACHE
    STEM_CACHE = os.path.join(TMP_AUDIO, 'stems')
    os.makedirs(STEM_CACHE, exist_ok=True)
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