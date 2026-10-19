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
# 路径**相对化**：面板就在工具链目录下（studio/），工具链根默认取父目录；可用 env 覆盖。
#
# ⚠ 2026-09-16 拆分 `ROOT` 与 `LIB`（踩过）：
#   原来一个 `ROOT` 同时当"工具链根"和"曲库根"用（`ROOT/scripts` 找工具、`ROOT/songs` 找曲目），
#   于是把 `--root` 指到**外置曲库**时，连 `scripts/render_midi.py` 都找不到了：
#       RuntimeError: 渲染失败（rc=2）
#       can't open file 'D:\...\b35_studio\scripts\render_midi.py'
#   现在：
#     · `TOOLCHAIN` = 工具链根（**永远**用来找 scripts/、传给 bridge 子进程）—— 不受 --lib 影响
#     · `LIB`       = 曲库根（`songs/` 与 `export/` 的父目录）—— 可用 `--lib` 指到别的盘/目录
#   这样"曲库外置"与"工具链在原地"两件事互不干扰。
TOOLCHAIN = os.path.abspath(os.path.join(HERE, '..'))
ROOT = TOOLCHAIN                       # 兼容旧环境变量/旧脚本的读法（= 工具链根）
# 曲库根：优先 `--lib`（main 里赋给 LIB）→ env → 默认在工具链下的 songs/
LIB = os.environ.get('BGM_STUDIO_LIB') or TOOLCHAIN
# **曲库根的持久化**（2026-09-16 增，用户："要能更改目录"）：
# 面板里改过曲库后写一行文本，**重启仍生效**（否则每次换库都要改启动参数）。
# 放在 studio/ 下而不是曲库里 —— 它是"这台机器的偏好"，跟着工具链走。
LIB_FILE = os.path.join(HERE, '.libpath')
# 编辑器的解析/操作/读写都在 scripts/ 里（`midi_file` / `midi_ops` / `midi_probe`），
# 服务器进程要能 import 它们 —— 加一次 sys.path（与 `run_py` 子进程的口径一致）。
_SCRIPTS = os.path.join(TOOLCHAIN, 'scripts')
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
# 曲目名口径**只有一处**（`scripts/name_rules.py`）：2026-10-09 放开中文与空格。
# 原来这里/`/api/upload`/`/api/extract`/`create.js` 各写一条 ASCII 白名单，
# 用户问"为什么不能有中文和空格"—— 查下来必须挡的只有**路径元字符**那一半。
from name_rules import check_song_name, SONG_NAME_HINT      # noqa: E402
PY = None                     # 由 main() 设定：music-gen 的 .venv python
EXPORT_DIR = os.path.join(LIB, 'export')        # 交付物跟着**曲库**走
TMP_AUDIO = os.path.join(os.environ.get('TEMP', HERE), 'bgm-studio-audio')
STEM_CACHE = None            # main() 里设为 %TEMP%\bgm-studio-audio\stems
JOBS = {}
JOB_SEQ = [0]
RENDER_TASKS = {}              # 编辑器真音源渲染任务（id → {done, r|err}）
LOCK = threading.Lock()
# **任务账本落盘**（2026-10-14 修用户报障"提取 midi 重名会卡住只能重启"的第二半）。
# 为什么必须有：`JOBS` 是**纯内存**的，面板一重启（或换个 server 实例）它就被清空 ⇒
# 前端还在轮询旧 job id，`/api/job` 返回 404 ⇒ `watchJob` 的 `catch` 只弹一句提示、
# **界面永远停在"运行中"**，⏹ 也停不动（同样 404）—— 用户唯一的出路就是重启面板，
# 重启后又看不到"上一次到底跑完没有"。现在：① 状态一变就写盘；② 启动时把账本里
# 仍写 `running` 的任务标成 `interrupted`（进程已随面板退出，**这是唯一的可能**）；
# ③ `/api/job` 在内存里找不到时**回落到账本**，于是前端总能拿到一个终态。
# ⚠ 文件名**必须以 `.` 开头**：`prune_tmp_audio` 在启动时删 `TMP_AUDIO` 下的直接子项
#    （白名单只有 stems/preview/mixfit + 跳过 `.` 开头的），叫 `jobs.json` 会被当缓存删掉。
JOBS_FILE = os.path.join(TMP_AUDIO, '.jobs.json')
JOBS_KEEP = 40                 # 账本只留最近 N 条（日志只留尾巴 40 行，防无限长大）
#: 账本读回时给"还在 running"的任务用的终态名。**提成常量**是为了能被**属性注入**测到 ——
#: 2026-10-14 实测教训：文本注入改源码对**已经 import 的模块**无效，那条变异用例当场"漏了"。
INTERRUPTED_STATE = 'interrupted'

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
    # `BGM_STUDIO_INNER=1`：告诉子进程"你是面板调起来的" → `studio_guard` 的 A′ 委托
    # 据此**走原生实现**，否则 `new_song.py` 会反过来 POST `/api/new` → 无限递归
    # （面板的 `/api/new`、`kind=render-tune` 背后正是 run_py 起这两个脚本）。
    env = dict(os.environ, PYTHONIOENCODING='utf-8', BGM_STUDIO_ROOT=ROOT,
               BGM_STUDIO_INNER='1',
               PYTHONPATH=pp + (os.pathsep + os.environ['PYTHONPATH'] if os.environ.get('PYTHONPATH') else ''))
    p = subprocess.run([py_exe(), '-X', 'utf8'] + args, cwd=cwd or ROOT, env=env,
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=timeout)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


AUDIO_EXT = ('.ogg', '.wav', '.mp3', '.flac', '.m4a')

# ------------------------------------------------------------------ 提取（扒谱）
# 「创作台」的提取链要**两个解释器**：分轨/转录在 `.venv-ml`（torch + demucs + YourMT3），
# 作曲/渲染在主 `.venv` —— 所以任务命令的第一项允许直接写解释器绝对路径（见 `_argv_of`）。
EXTRACT_WORK = os.path.join(TOOLCHAIN, '_extract')
PY_ML = os.path.join(TOOLCHAIN, '.venv-ml', 'Scripts', 'python.exe')
# 上传的参考音频后缀（比 `AUDIO_EXT` 宽一点：扒谱的素材常见 flac/aiff）
UPLOAD_EXT = AUDIO_EXT + ('.aiff', '.aif', '.opus', '.wma')

# 主题模板包的中文名（**面板展示用**，唯一真源就在这里；词面匹配的那份词表在
# `scripts/ask_parse.py` 里，两者职责不同：这里只负责"给人看叫什么"）。
THEME_CN = {
    'battle': '战斗', 'cheerful': '欢快', 'classic': '古典庄重', 'daily': '日常',
    'folk_tale': '民谣叙事', 'gorgeous': '华丽', 'lounge': '酒馆爵士', 'mystery': '神秘',
    'neon': '霓虹电子', 'night': '夜晚', 'retro': '复古游戏', 'seaside': '海边',
    'sorrow': '悲伤', 'tender': '温柔抒情', 'waltz': '三拍圆舞',
}


def theme_list():
    """可用主题模板包（扫 `refs/themes/*.json`，排除 `_melody` 画像）—— 只报**真的有画像**的，
    没画像的主题让用户选了会在 `new_song` 里失败，不如不出现在选项里。

    ⚠ 2026-10-07 起**连实测参数一起报**（速度中位/四分位 · 引擎预设 · 拍号 · 总小节数）：
    创作台要让「BPM / 风格 / 时长」这几个维度**可见可改**，而它们的默认值必须有依据 ——
    依据就是画像里量出来的值，不是猜的。总小节数还用来把"想要多少秒"换算成建议 BPM。
    """
    d = os.path.join(TOOLCHAIN, 'refs', 'themes')
    try:
        names = sorted(os.listdir(d))
    except OSError:
        names = []
    out = []
    for fn in names:
        if not fn.endswith('.json') or fn.endswith('_melody.json') or fn.startswith('_'):
            continue
        k = fn[:-5]
        prof = {}
        try:
            with open(os.path.join(d, fn), encoding='utf-8') as f:
                prof = json.load(f)
        except (OSError, ValueError):
            prof = {}
        bpm = prof.get('bpm') or {}
        # ⚠ **拍号也要带出去**（2026-10-08）：创作台要用它做"秒数 ↔ BPM"换算（3/4 一小节只有
        #   3 个四分，按 ×4 算会偏 25%），而且用户此前完全看不到拍号（只能从主题名猜）。
        _meter = (prof.get('form') or {}).get('meter') or prof.get('meter') or [4, 4]
        form = prof.get('form') or {}
        out.append({'key': k, 'cn': THEME_CN.get(k, k),
                    'bpm': bpm.get('median'), 'bpm_p25': bpm.get('p25'),
                    'bpm_p75': bpm.get('p75'),
                    'engine_style': prof.get('engine_style'),
                    'meter': _meter,
                    # **默认音色池**（2026-10-19 面板需求）：创作台要**列出每一轨的默认乐器**
                    # 并允许改（以前只给"主奏"，其余轨隐藏）。键 = 池角色，值 = GM 号候选。
                    'prog_pool': (prof.get('arrangement') or {}).get('prog_pool'),
                    'total_bars': form.get('total_bars')})
    return out


def has_audio(d):
    """目录里有音频文件吗？

    ⚠ 为什么曲目判据要认它（用户 2026-09-17 报的）：**A/B 试听目录天然没有 song.json**
    （就是几个版本各一个 ogg 摆在一起），旧判据"必须是含 song.json 的目录"逼得用户
    去伪造一份借来的 song.json —— 那份 json 与目录内容毫无关系，点"数据契约检查"必炸，
    显示的还是"找不到曲目"。试听这类只读用途，判据应当是"这里有能播的东西"。
    """
    try:
        return any(f.lower().endswith(AUDIO_EXT) and os.path.isfile(os.path.join(d, f))
                   for f in os.listdir(d))
    except OSError:
        return False


def probe_lib(p):
    """探测一个目录**能当曲库根**的哪种布局 → (mode, base)。

    ⚠ 为什么要有这个（用户报的 bug）：旧实现写死 `LIB/songs/<id>/song.json`，
    于是想把曲库指到 `D:\\test\\llm_direct\\b35_remake`（**单曲目录**：里面直接是
    `song.json` / `.mid` / `.ogg`，没有 `songs/` 这一层）时被自己的校验挡住 ——
    **曲库根的判据应当是"这里能找到曲子"，而不是"必须有个叫 songs/ 的子目录"**。
      · nested  `LIB/songs/<id>/song.json`  —— 工具链标准布局
      · flat    `LIB/<id>/song.json`         —— 一层子目录（导出目录、归档目录都长这样）
      · single  `LIB/song.json`              —— 目录本身就是一首曲子
    判据里的"曲子"= **有 song.json 或有音频**（后者见 `has_audio`：纯试听目录也算）。
    """
    p = os.path.abspath(p or '')
    if not os.path.isdir(p):
        return 'missing', p
    if os.path.isdir(os.path.join(p, 'songs')):
        return 'nested', os.path.join(p, 'songs')
    if os.path.isfile(os.path.join(p, 'song.json')) or has_audio(p):
        return 'single', p
    for name in sorted(os.listdir(p)):
        sub = os.path.join(p, name)
        if os.path.isdir(sub) and (os.path.isfile(os.path.join(sub, 'song.json'))
                                   or has_audio(sub)):
            return 'flat', p
    return 'none', p


def lib_base():
    """当前曲库根 → (mode, base)；base 是"里面直接放各曲目目录"的那一层"""
    return probe_lib(LIB)


def songs_base():
    """**曲目目录的父目录**（`songs/<曲名>/song.json` 里那层 `songs`）—— 取不到就用工具链的标准曲库。

    为什么单独一个函数（2026-10-13）：提取链里**装配要读 `songs/<曲名>/song.json` 的 bpm**
    （那是第一步那条链写下的、未经去鼓污染的基准）。把这段路径拼在调用点上，遇到
    "曲库被切成单曲目录 / 一层子目录"两种布局时会拼错，而**拼错的后果是静默钉错速度**
    （时间轴错一倍）—— 所以口径一处、并带兜底。
    """
    try:
        mode, base = lib_base()
        if mode in ('flat', 'single'):
            return base
    except Exception:                                               # noqa: BLE001
        pass
    return os.path.join(TOOLCHAIN, 'songs')


def clean_stale_song_artifacts(sid, verbose=True, d=None):
    """**覆盖重建前**清掉同一目录里**别的曲子**留下的产物，返回清掉的条目数。

    为什么必须做（2026-10-14 实测）：拿 A 的音频去"覆盖重建" B 时，目录里会**同时**留着
    B 的 `<旧名>.mid` / `<旧名>_sf.ogg` / `<旧名>_sf.wav` 与新的 `<新名>.*` ——
    面板试听按 `render.json` 的 `mid/out` 取文件，于是可能把**上一首**放出来；
    排查时也分不清"哪个文件是这一版的"。实测我的复现目录正是这样：
    `songs/zz_dup_probe/` 里既有 `已至.mid` 又有新写入的产物。

    安全边界（**只碰明显属于别的曲子的东西**）：
      · 只删音频/MIDI 产物里**文件名不以本曲名开头**的那些；
      · `render.json` 只有当它的 `mid`/`out` **不指向本曲名**时才删（指向本曲的留着）；
      · **绝不删** `song.json` / `compose.py`（复现依据）/ 目录本身。
    """
    d = d or os.path.join(songs_base(), sid)
    if not os.path.isdir(d):
        return 0
    n = 0
    for fn in list(os.listdir(d)):
        p = os.path.join(d, fn)
        if not os.path.isfile(p):
            continue
        if fn.endswith(('.mid', '.ogg', '.wav')):
            if fn.startswith(sid + '.') or fn.startswith(sid + '_'):
                continue                     # 本曲自己的产物（马上会被重写）
            try:
                os.remove(p)
                n += 1
            except OSError:
                pass
        elif fn == 'render.json':
            try:
                cfg = json.load(open(p, encoding='utf-8')) or {}
            except Exception:                                       # noqa: BLE001
                cfg = {}
            if str(cfg.get('mid') or '').startswith(sid) or str(cfg.get('out') or '') == sid + '_sf':
                continue
            try:
                os.remove(p)
                n += 1
            except OSError:
                pass
    if verbose and n:
        print('  [覆盖重建] 清掉上一版残留产物 %d 个：%s' % (n, d))
    return n


def song_dir(sid, need_json=True):
    """曲目目录。`need_json=False` 时**纯音频目录也放行**（试听/取文件用：
    `/api/files`、`/api/dl`、`/api/audio?kind=mix` 都不需要 song.json）。"""
    mode, base = lib_base()
    if mode == 'missing':
        raise FileNotFoundError('曲库目录不存在：%s' % LIB)
    if mode == 'none':
        raise FileNotFoundError('曲库根里找不到任何曲子（song.json 或音频文件）：%s' % LIB)
    if mode == 'single':
        # ⚠ **single 布局下 sid 必须与"库里那一首"对得上**（id 取目录名，见 `songs_list`）。
        #   原来是无条件 `d = base`：曲库指向一个"本身就是曲子"的目录时，**任何 id 都解析成
        #   它自己** —— 实测把一个曲目 id（44_skip_beat）的 compose 任务跑成了该目录里的
        #   **另一首**曲子，直接覆盖了它的 `.mid`（曲子是 `D:\test\piano_rain_交付`，
        #   而那个交付目录被当成了曲库）。现在对不上就报错说清，而不是静默写错文件。
        want = os.path.basename(os.path.abspath(base))
        if sid and sid != want:
            raise FileNotFoundError(
                '曲库「%s」是**单曲目录**（里面直接有 song.json），它的曲目 id 是 "%s"，'
                '不是你给的 "%s" —— 这个 id 在本曲库下不存在，为避免写错文件已拒绝。\n'
                '  要么用 id "%s"，要么把曲库切到含 songs/<曲目>/song.json（工具链标准）'
                '或含 <曲目>/song.json（一层子目录）的目录。'
                % (base, want, sid, want))
        d = base
    else:
        d = os.path.join(base, sid)
        # ⚠ **防穿越兜底**（2026-10-09）：曲名规则放开了中文与空格，"ASCII 白名单"不再是
        #   唯一防线 ⇒ 这里再按"解析后的路径必须仍在曲库内"验一次（`check_song_name`
        #   已经挡掉 `/ \ ..`，两道一起用；同 `/api/dl` 的 `startswith` 口径）。
        _ab, _ad = os.path.abspath(base), os.path.abspath(d)
        if _ad != _ab and not _ad.startswith(_ab + os.sep):
            raise FileNotFoundError('曲目 id 越出曲库：%r（曲库 %s）' % (sid, base))
    if not os.path.isdir(d):
        raise FileNotFoundError('找不到曲目: %s（曲库 %s）' % (sid, LIB))
    if need_json and not os.path.isfile(os.path.join(d, 'song.json')):
        raise FileNotFoundError(
            '曲目 %s 是**纯音频目录**（里面只有音频、没有 song.json）——'
            '可以试听/下载，但作曲、渲染、数据契约检查这些需要 song.json 的操作不能用。' % sid)
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
    """`refs/*.json` 里第一个 `BGM*` 画像名 —— **只当无主题时的兜底**。"""
    d = os.path.join(ROOT, 'refs')
    names = [f[:-5] for f in sorted(os.listdir(d))] if os.path.isdir(d) else []
    for n in names:
        if n.startswith('BGM'):
            return n
    return names[0] if names else 'BGM16c'


def ref_for_theme(theme):
    """建曲时该对标哪份画像：**该主题自己的聚合混音目标**（`refs/mix_targets/<主题>_mix.json`）。

    为什么不再用 `first_ref()`（2026-10-09 用户实测抓到）：面板建曲时**不带** `ref` 字段，
    于是 `first_ref()` 返回 `refs/` 里字母序第一个 `BGM*` 画像（实测 = `BGM01`），
    而它被**显式** `--ref BGM01` 传给 `new_song.py` ⇒ 覆盖掉 `new_song` 自己
    "按主题包 `mix_target` 选画像"的正确逻辑 ⇒ **每一首面板生成的曲子都照 BGM01 的频谱调参**。
    实证（同一首 `ask_20261009_2029`）：实测九带与 `cheerful_mix` 的 MAE **1.53dB**、
    与 `BGM01` **2.37dB** —— 本该对标前者，而 `render.json` 里写的是 `BGM01`。

    同主题 13 首模板聚合出的中位数，比"随便挑一首别人的曲子"更贴主题；画像文件不存在时
    返回 `''`（= **不传** `--ref`，让 `new_song` 按主题包自己选 —— 别用一个名字把它顶坏）。
    """
    t = str(theme or '').strip()
    if not t:
        return first_ref()
    for stem in (t + '_mix', t):
        p = os.path.join(TOOLCHAIN, 'refs', 'mix_targets', stem + '.json')
        if os.path.isfile(p):
            return stem
    return ''


def songs_list():
    mode, base = lib_base()
    if mode in ('missing', 'none'):
        return []
    if mode == 'single':
        # 目录本身就是一首曲子（用户直接把曲库指向 `...\b35_remake` 这种目录）：
        # id 取**目录名**，这样下拉里显示的是 `b35_remake` 而不是空串。
        pairs = [(os.path.basename(os.path.abspath(base)), base)]
    else:
        pairs = []
        for name in sorted(os.listdir(base)):
            sub = os.path.join(base, name)
            if not os.path.isdir(sub):
                continue
            if os.path.isfile(os.path.join(sub, 'song.json')) or has_audio(sub):
                pairs.append((name, sub))
    out = []
    for name, d in pairs:
        sj = os.path.join(d, 'song.json')
        has_json = os.path.isfile(sj)
        s = (read_json(sj) or {}) if has_json else {}
        r = read_json(os.path.join(d, 'render.json')) or {}
        bars = sum(sec.get('bars', 0) for sec in s.get('sections', []))
        bpm = s.get('bpm') or 120
        # 同目录里的 .mid（面板的"曲目"只认 song.json，但一个工作目录里往往还有一堆
        # 中间产物 MIDI —— 它们该能直接点开进编辑器，否则用户会觉得"怎么只有一个"）
        try:
            _mids = sorted(f for f in os.listdir(d)
                           if f.lower().endswith(('.mid', '.midi')))
        except OSError:
            _mids = []
        # 纯音频目录（A/B 试听用）：没有 song.json，只有几个音频文件 —— 也要列出来
        _aud = []
        if not has_json:
            try:
                _aud = sorted(f for f in os.listdir(d) if f.lower().endswith(AUDIO_EXT))
            except OSError:
                _aud = []
        out.append({'id': name, 'name': s.get('name') or name, 'style': s.get('style', ''),
                    'bpm': bpm, 'bars': bars, 'dir': d, 'mids': _mids,
                    'seconds': round(bars * 4 * 60.0 / bpm, 1) if bars else 0,
                    'ref': r.get('ref', ''), 'tracks': len(s.get('sections', [])),
                    'has_ogg': os.path.isfile(os.path.join(d, (r.get('out') or '') + '.ogg'))
                               or bool(_aud),
                    'audio_only': not has_json, 'n_audio': len(_aud),
                    'mtime': os.path.getmtime(sj) if has_json else os.path.getmtime(d)})
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
    """从服务器本机路径导入 .mid —— **限定在「工具链目录」或「当前曲库」之内**。

    约束的用意是"面板不该读任意文件"，而不是"只能读工具链"：
    ⚠ 用户口径（2026-09-16）："要能更改目录" —— 曲库可以被指到工具链**外面**
    （如 `D:\\test\\llm_direct\\b35_remake`），那里面的 .mid 当然要能导入。
    所以允许范围跟着**当前曲库**一起走，两者取并。
    """
    p = os.path.abspath(path)
    allowed = [os.path.abspath(ROOT), os.path.abspath(LIB)]
    if not any(p == a or p.startswith(a + os.sep) for a in allowed):
        raise ValueError('只允许导入**工具链目录或当前曲库**内的文件\n'
                         '  工具链：%s\n  当前曲库：%s' % (ROOT, LIB))
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
                   'ccs': len(t.get('ccs') or []),
                   'bends': len(t.get('pitch_bends') or []),
                   'range': [_mp.note_name(min(ps)), _mp.note_name(max(ps))] if ps else None,
                   'vel': [min([n[3] for n in ns] or [0]), max([n[3] for n in ns] or [0])]})
    return {'title': model.get('title'), 'format': model.get('format'),
            'division': model.get('division'), 'bpm': model.get('bpm'),
            'timesig': model.get('timesig'), 'stats': st, 'tracks': tr}


def _argv_of(c):
    """把任务命令补成完整 argv。

    约定：第一项是**相对路径的脚本**时前面补主 `.venv` 解释器（历史写法，面板原有的任务
    都这么写）；第一项是**绝对路径且以 .exe 结尾**时原样用 —— 扒谱链的分轨/转录必须跑
    `.venv-ml`（torch/demucs/YourMT3 只装在那里面），拿主 venv 跑会 ModuleNotFoundError。
    """
    first = str(c[0])
    if os.path.isabs(first) and first.lower().endswith('.exe'):
        return [str(x) for x in c]
    return [py_exe(), '-X', 'utf8'] + [str(x) for x in c]


#: **任务墙钟上限**（秒）—— 一条命令跑过它就**杀进程树**、任务判 failed。
#: 为什么必须有（2026-10-14 用户报障原话："提取 midi 重名会卡住只能重启"）：
#: `_new_job` 原来**没有任何超时**，一条命令只要不返回，面板就永远显示 `running` ——
#: 用户既看不到进度也没法恢复，**只能重启面板**。上限按本机实测耗时留足余量。
JOB_DEADLINE = {
    'extract:fast': 1800.0,      # 实测 60~120 秒；并发/慢盘留足
    'extract:full': 3600.0,      # 实测 ~100 秒起，十分钟级精修留 1 小时
    'render': 900.0,
    'render-raw': 900.0,
    'render-tune': 1800.0,
    'render-tune-solo': 1200.0,
    'search': 1800.0,
    'mixfit': 1800.0,
    'export': 900.0,
    'export:stems': 900.0,
    'compose': 600.0,
}
JOB_DEADLINE_DEFAULT = 1800.0


def job_deadline(kind):
    """该任务类型**一条命令**的墙钟上限（秒）。口径唯一真源 = 上面的 `JOB_DEADLINE`。"""
    return float(JOB_DEADLINE.get(kind, JOB_DEADLINE_DEFAULT))


def save_jobs():
    """把任务账本写盘（**状态一变就写**）。失败只提示、不影响任务本身。"""
    try:
        os.makedirs(os.path.dirname(JOBS_FILE), exist_ok=True)
        with LOCK:
            rows = [{k: j.get(k) for k in ('id', 'song', 'kind', 'state', 'rc', 'out',
                                           'started', 'ended')} |
                    {'tail': (j.get('log') or [])[-40:]}
                    for j in list(JOBS.values())[-JOBS_KEEP:]]
        tmp = JOBS_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(rows, f, ensure_ascii=False)
        os.replace(tmp, JOBS_FILE)
    except Exception as e:                                        # noqa: BLE001
        sys.stderr.write('[studio] 任务账本写盘失败：%s\n' % e)


def load_jobs():
    """启动时把账本读回内存；**仍写 `running` 的一律标成 `interrupted`**。

    判据不是猜的：任务进程是面板的子进程，面板退出后它们**不可能还活着**
    （`taskkill /T` 与进程组语义都保证这一点）⇒ "还在 running" = 状态丢了，不是任务还在跑。
    """
    try:
        with open(JOBS_FILE, encoding='utf-8') as f:
            rows = json.load(f)
    except Exception:                                             # noqa: BLE001
        return 0
    n = 0
    with LOCK:
        for r in rows if isinstance(rows, list) else []:
            jid = r.get('id')
            if not jid:
                continue
            if r.get('state') == 'running':
                r['state'] = INTERRUPTED_STATE
                r['rc'] = -3
                r.setdefault('tail', []).append(
                    '⚠ 面板重启过：这条任务的状态随之丢失（进程已随面板退出）——'
                    '产物请点「刷新曲库」后核对')
            r['log'] = list(r.get('tail') or [])
            JOBS.setdefault(jid, r)
            n += 1
            try:
                JOB_SEQ[0] = max(JOB_SEQ[0], int(str(jid).rsplit('-', 1)[-1]))
            except ValueError:
                pass
    if n:
        sys.stderr.write('[studio] 任务账本：读回 %d 条\n' % n)
    return n


def run_job_cmd(argv, env, job, deadline, lock=None):
    """跑一条任务命令：逐行写 `job['log']`，**到点杀进程树**；返回 rc（**超时 = -2**）。

    超时与"命令自己失败"（非零 rc）**分开记**：日志里明说「⏱ 超时」，状态记 `failed`，
    这样用户看到的是"任务超时被杀"，而不是一个永远 `running` 的黑洞。
    """
    lock = lock if lock is not None else LOCK
    p = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                         errors='replace', bufsize=1)
    with lock:
        job['proc'] = p
    timed_out = {'v': False}

    def _kill():
        timed_out['v'] = True
        try:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)], capture_output=True)
        except Exception:                                       # noqa: BLE001
            pass

    t = threading.Timer(max(1.0, float(deadline)), _kill)
    t.daemon = True
    t.start()
    try:
        for line in p.stdout:
            with lock:
                job['log'].append(line.rstrip('\n'))
                if len(job['log']) > 500:
                    del job['log'][:200]
        p.wait()
    finally:
        t.cancel()
    if timed_out['v']:
        with lock:
            job['log'].append('⏱ 超时：这条命令跑过 %.0f 秒仍未结束，已杀进程树'
                              '（保护：不让面板停在 running 只能重启）' % float(deadline))
        return -2
    return p.returncode


def _new_job(sid, kind, cmds, outs):
    """注册并启动一个后台任务（日志尾巴 / 进程句柄 / 状态都挂在 JOB 上，前端 1 秒轮询）。

    ⚠ `BGM_STUDIO_INNER=1` **不能少**：面板起任务时脚本会反过来委托回面板 API，
    没有这个标记就是无限套娃（`selftest.t_panel_is_only_entry` 会数这个字符串的出现次数）。
    """
    with LOCK:
        JOB_SEQ[0] += 1
        jid = '%s-%d' % (sid, JOB_SEQ[0])
        JOB = {'id': jid, 'song': sid, 'kind': kind, 'state': 'running', 'log': [],
               'rc': None, 'out': outs, 'started': time.time(), 'ended': None}
        JOBS[jid] = JOB

    def worker():
        env = dict(os.environ, PYTHONIOENCODING='utf-8', BGM_STUDIO_ROOT=ROOT,
                   BGM_STUDIO_INNER='1')
        try:
            rc = 0
            for i, c in enumerate(cmds):
                argv = _argv_of(c)
                if len(cmds) > 1:
                    with LOCK:
                        JOB['log'].append('=== [%d/%d] %s ==='
                                          % (i + 1, len(cmds), ' '.join(argv[-2:])))
                rc = run_job_cmd(argv, env, JOB, job_deadline(kind))
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
        save_jobs()                       # 状态一变就落盘（面板重启后前端也能拿到终态）

    threading.Thread(target=worker, daemon=True).start()
    save_jobs()                           # 起手就记一条（万一马上重启，账本里已有 running → 会被标 interrupted）
    return jid


# 把 demucs 的标准分轨目录（`piano.wav` 这种**无前缀**名）摊平成 `h6_*.wav`。
# 为什么必须做：`probe_instruments.load_stems` 按 `h4_`/`h6_` **前缀**过滤（`--stems-dir` 给了
# 标准目录时它一层都不认），于是编制表只剩"①②③a"层、第③b/③c 的**分轨能量证据全丢**，
# 后面的 `arrange` 会退化成"没有证据就不改"（2026-10-07 实测：probe 输出整列 `-`）。
# 用**硬链接**（同盘零成本），失败再退回复制。
_FLAT_STEMS_CODE = (
    "import os,shutil,sys\n"
    "src,dst=sys.argv[1],sys.argv[2]\n"
    "os.makedirs(dst,exist_ok=True)\n"
    "n=0\n"
    "for f in sorted(os.listdir(src)):\n"
    "    if not f.lower().endswith('.wav'):\n"
    "        continue\n"
    "    d=os.path.join(dst,'h6_'+f)\n"
    "    if os.path.exists(d):\n"
    "        continue\n"
    "    try:\n"
    "        os.link(os.path.join(src,f),d)\n"
    "    except OSError:\n"
    "        shutil.copy2(os.path.join(src,f),d)\n"
    "    n+=1\n"
    "print('[平铺] h6_*.wav %d 个 -> %s'%(n,dst))\n"
)


def extract_plan(name, src, mode, seconds=None, path='pure', parts=None):
    """三档提取的命令序列（**不重写任何音频逻辑**，只串仓库现有的入口）。

    | `mode` | 链路 | 产物 | 本机实测耗时 |
    |---|---|---|---|
    | `fast` | 转录（→切轨→`song.json`）→ `make_song --no-tune`（作曲+渲染） | `songs/<名>/song.json` + `.mid` + `.ogg` | 分钟级 |
    | `full` | 同上 + `restore_oneshot` 六阶段（probe/repair/vel/arrange/render/audit） | 再加体检 | 十分钟级 |

    `path` = **内容路径**（用户 2026-10-13 点名要的三档，2026-10-13 落地）：

    | `path` | 转录吃什么 | 实测 |
    |---|---|---|
    | `pure`（默认） | **全混音**；分轨只用来量逐音力度 | 用户听感认可的那版 |
    | `partial` | 全混音 + **指定轨**（`parts`，默认 Hook/Pad）改从**去鼓混音**转录，再混合装配 | 只值 2 轨；≈2× 时间 |
    | `all` | **逐条分轨各自转录**再装配 | **实测更差** · 3~6× 时间 |

    三档的步骤表在 `scripts\\extract_paths.py`（纯函数、可合成检验；守卫
    `t_extract_paths_contracts` 盯着"某档悄悄退回旧做法"）。这里只负责补上两处
    `-c` 占位代码（`_FLAT_STEMS_CODE` / `extract_paths.ASM_CODE`）与 `seconds`。

    ⚠ 两档都**先分轨**：`transcribe_ymt3 --stems-dir` 是逐音力度的唯一来源，
    不分轨的话 YMT3 输出的每个音力度恒 100（"打字机"，PITFALLS 有实测）。
    ⚠ 工作目录在**曲库外**（`<工具链>/_extract/<名>/`）：曲库根混进散装音频会让
    `probe_lib` 把整库判成"一首曲"（`docs/STUDIO-WORKFLOW.md` §2.5）。
    """
    import extract_paths as EP
    W = os.path.join(EXTRACT_WORK, name)
    # ⚠ **两个目录别混**（2026-10-13 实测踩到）：`flat` 是分轨**根目录**（`stem_split -o` 的实参，
    #   它自己会在下面再建 `<模型>/<曲名>/`），`h6` 才是**里面真有 6 个 wav 的那层**
    #   （给 `--stems-dir` / `drumless_mix --stems` / 收尾两步用）。
    #   给反了的后果：双层嵌套 + `--stems-dir` 指到空目录 ⇒ 力度恒 100、`partial` 第三步就死。
    flat = os.path.join(W, 'stems')
    h6 = EP.h6_of(flat, src)
    # ⚠ `path=` / `parts=` 用**关键字**传（不靠位置）：守卫 `t_extract_paths_contracts`
    #   按这两个名字核对"转发真的接上了"，写成位置参数它看不见。
    #   `songs=` 是装配钉死 bpm 的来源（`songs/<曲名>/song.json`，第一步那条链写的）——
    #   **不能**用 `<W>/song.json`：那是去鼓那次转录自己写的那份（bpm 翻倍，实测 68 → 136）。
    cmds = EP.plan(name, path=path, W=W, flat=flat, h6=h6, audio=src, mode=mode,
                   scripts=_SCRIPTS, py_exe=py_exe(), py_ml=PY_ML, parts=parts,
                   songs=songs_base())
    # 三处 `-c` 占位替换成真代码（占位常量名与 `extract_paths` 同名 —— 守卫据此核对）：
    #   `FLAT_STEMS_CODE`（摊平分轨）· `ASM_CODE`（装配）· `GAPFILL_CODE`（装配后回灌丢掉的轨）
    cmds = [[_FLAT_STEMS_CODE if c == EP.FLAT_STEMS_CODE else
             EP.ASM_CODE if c == EP.ASM_CODE else
             EP.GAPFILL_CODE if c == EP.GAPFILL_CODE else c for c in step] for step in cmds]
    # **收尾两步**（2026-10-09 接）：① 混音对标改成**本曲原曲**（默认落的 `bgm01c` 是别的曲子，
    #   `full` 档会因此"照别人的频谱调参" ⇒ 守卫 `restore_ref_is_own_song` 必红）；
    #   ② 渲染后 Perc 占比超门时，按**分轨实测数字**写 `patterns.perc_exempt`（或明确建议
    #   写 `arr.perc: 0`）。⚠ 放在**渲染之后、`extract_notes` 之前**：notes.md 里记的
    #   「参考画像」要是修正后的那个。三档共用，由 `EP._tail` 统一给。
    # **无论哪一档，最后都要写 `notes.md`**：转录那条链自己不写它，而曲目目录必须齐 4 件
    # （`song.json`/`compose.py`/`notes.md`/`render.json`，守卫 `notes_present` 就是这么判的）
    # —— 少了这一步，提取出来的**每一首**都会让全量自检变红（2026-10-07 实测踩到）。
    if seconds:
        for step in cmds:
            if any(str(x).endswith('extract_notes.py') for x in step):
                step += ['--seconds', '%.1f' % float(seconds)]
                break
    return cmds


def _sync_notes_speed(d):
    """把曲目 `notes.md` 的「| 速度 |」行同步成 `song.json` 的 `bpm`（其余内容一个不动）。

    为什么需要（2026-10-07 用户实测踩到）：创作台允许**事后改 BPM**（写回 `song.json`），
    而 `notes.md` 是 `new_song.py` 生成时写的、**不会跟着变** —— 于是出现"song.json 180 BPM、
    notes.md 写 124 BPM"。这是**交付文档写错**：照 notes.md 复现会得到另一首曲子，
    而且全库守卫 `notes_speed_matches` 正是查这一条（`selftest.t_notes_speed_matches`）。

    放在 `/api/song` 保存之后调用：这样**不管从哪条路改的 bpm**（创作台、面板的保存按钮、
    手工 POST）都会同步，不用每个入口各写一遍。
    """
    sj = os.path.join(d, 'song.json')
    nt = os.path.join(d, 'notes.md')
    if not (os.path.isfile(sj) and os.path.isfile(nt)):
        return False
    try:
        with open(sj, encoding='utf-8') as f:
            bpm = float(json.load(f).get('bpm') or 0)
        with open(nt, encoding='utf-8') as f:
            txt = f.read()
    except (OSError, ValueError):
        return False
    if not bpm:
        return False
    # 只替换「**<数字> BPM**」里的数字，保留后面跟着的「· 4/4 · 84 小节」这些
    new = re.sub(r'(\| 速度 \|[^\n]*?\*\*)([0-9]+(?:\.[0-9]+)?)(\s*BPM\*\*)',
                 lambda m: m.group(1) + ('%g' % bpm) + m.group(3), txt, count=1)
    if new == txt:
        return False
    try:
        with open(nt, 'w', encoding='utf-8', newline='\n') as f:
            f.write(new)
    except OSError:
        return False
    return True


def start_job(sid, kind, opts=None):
    """起后台任务；返回 jobId。kind: compose / render / render-tune / solo:<轨> / export[:stems]"""
    opts = opts or {}
    # **同名并发闸**（2026-10-14 修用户报障"提取 midi 重名会卡住只能重启"）。
    # 现场：同名提取被连点两次（或第一次失败后重试 + 上一次其实还在跑）⇒ 两个任务**同时**写
    # 同一份 `songs/<名>/song.json` 与同一个渲染输出，两套 demucs/转录并发互抢 CPU/GPU，
    # 面板上两个任务都长时间停在 running，用户既看不到进度也没法恢复 ⇒ **只能重启**。
    # 实测（本轮复现）：同名两次请求各自跑起一条完整链，`.venv-ml` 同时 4 个进程。
    # 判据：**同一首曲目 + state == running** 就算占用（不同曲目照样并行，不受影响）。
    with LOCK:
        _busy = [j for j in JOBS.values()
                 if j.get('song') == sid and j.get('state') == 'running']
    if _busy:
        raise ValueError('同名任务还在跑：%s（kind=%s，起于 %s）—— 等它结束，或先点 ⏹ 停止'
                         % (_busy[0]['id'], _busy[0]['kind'],
                            time.strftime('%H:%M:%S', time.localtime(_busy[0]['started']))))
    if kind.startswith('extract:'):
        # **提取任务在曲目还不存在时就要能起**（`song_dir` 对不存在的曲目会抛）
        mode = kind.split(':', 1)[1]
        src = os.path.abspath(str(opts.get('audio') or ''))
        if not os.path.isfile(src):
            raise ValueError('参考音频不存在：%s' % src)
        # **覆盖重建**：先把上一版残留清掉（否则目录里混着上一首的 .mid/.ogg，试听会放错文件）
        if opts.get('force'):
            clean_stale_song_artifacts(sid)
        return _new_job(sid, kind,
                        extract_plan(sid, src, mode, opts.get('seconds'),
                                     path=opts.get('path') or 'pure',
                                     parts=opts.get('parts')), '')
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
    elif kind == 'render-tune-solo':
        # 「全用钢琴/弦乐」：**只做独奏化**，不先跑一次原曲渲染。
        # ⚠ 为什么不能串 `make_song.py <曲>` 在前面（第一版就是这么写的，2026-10-07 改）：
        #   ① 它会 autotune 原曲并把 EQ/CC7 决策**写回 song.json**，而独奏版编制已经变了
        #      （鼓变成音型、层被并轨），拿原曲的频谱目标去调是"往错的目标推"；
        #   ② `solo_instrument` 自己出新曲目目录并调 `make_song --no-tune` 渲染（技能 §21 的口径），
        #      串两次渲染等于白多 2~3 分钟，用户却只看得到最后一份产物。
        inst = str(opts.get('instrument') or opts.get('instruments') or 'piano')
        # **单件 vs 多件是两条路**（2026-10-08）：单件 `--instrument` 把全部声部**合并**成
        # 一条轨（原有行为）；多件 `--instruments`（逗号分隔）**保留声部**、每轨换一件并
        # 关掉清单外的编配层。用"有没有逗号"自动判 —— 面板只需传一个字符串。
        _flag = '--instruments' if ',' in inst else '--instrument'
        cmds, outs = [[os.path.join(_SCRIPTS, 'solo_instrument.py'), sid,
                       _flag, inst,
                       '--out', str(opts.get('out') or (sid + '_solo'))]], ''
        # 「只生成数据、不渲染」：独奏化页上的复选框（渲染要 30 秒~2 分钟，先看数据时省掉）
        if opts.get('no_render'):
            cmds[0].append('--no-render')
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
    return _new_job(sid, kind, cmds, outs)


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = 'bgm-studio/0.1'

    def log_message(self, fmt, *args):        # 安静点：只留错误
        if ' 4' in (fmt % args)[:6] or ' 5' in (fmt % args)[:6]:
            sys.stderr.write('[studio] %s\n' % (fmt % args))

    # ---------- 基础
    def _json(self, obj, code=200):
        """写 JSON 响应。

        ⚠ **客户端断连必须吞掉、不能让异常逃出去**（2026-09-17，面板真的被它拖垮过）：
        `/api/metrics` 这类接口要先跑子进程（几秒），用户等不及刷新/关标签页时连接就断了；
        此时 `end_headers()` / `wfile.write()` 抛
        `ConnectionAbortedError: [WinError 10053] 你的主机中的软件中止了一个已建立的连接`。
        原来的写法让异常一路逃到 `do_GET` 的兜底 `except` → 兜底里又调 `self._err()` 再写一次
        → **再抛一次** → 栈里出现三层 "During handling of the above exception"，
        日志被刷满、异常逸出请求处理线程。**客户端已经走了，写不进去是正常的**，
        正确做法就是静默收场。
        """
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        try:
            self.send_response(code)
            self.send_header('content-type', 'application/json; charset=utf-8')
            self.send_header('content-length', str(len(body)))
            self.send_header('cache-control', 'no-store, no-cache, must-revalidate, max-age=0')
            self.send_header('pragma', 'no-cache')
            self.send_header('expires', '0')
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, BrokenPipeError, OSError) as e:
            # WinError 10053/10054 都归 ConnectionError 家族；OSError 兜住 socket 已关
            if isinstance(e, OSError) and not isinstance(e, ConnectionError):
                sys.stderr.write('[studio] 写响应失败（客户端多半已断开）：%s\n' % e)
            self.close_connection = True

    def _err(self, msg, code=400):
        self._json({'ok': False, 'error': str(msg)}, code)

    def _no_content(self, hdr=None, val=None):
        """**204 无内容**。用在"这条资源**预期内**就是没有"的场合（例如参考曲音频不随仓库分发）——
        回 404 会让浏览器控制台多一条"加载失败"，而仓库自带的
        `studio/tools/browser_check_create.js` 把**任何** console/network error 判成 FAIL
        ⇒ 全新 clone 上必然假红（2026-10-09 实测：四页全 200，只因 `/api/ref-audio` 的 404 判 FAIL）。
        前端 `engine.js#loadRef` → `decode()` 失败本来就返回 null，所以 204 对它是"静默无音频"。"""
        self.send_response(204)
        self.send_header('Content-Length', '0')
        if hdr:
            self.send_header(hdr, val or '')
        self.end_headers()

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
            j['ended'] = time.time()
            save_jobs()
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
        # ⚠ 音频/大文件是最容易"客户端中途断开"的路径（拖进度条、切歌、关标签页）：
        #   写 socket 抛 ConnectionAborted / BrokenPipe 是**正常现象**，必须静默收场 ——
        #   让异常逃出去会刷满日志并逸出请求线程（见 `_json` 的注释）。
        try:
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
        except (ConnectionError, BrokenPipeError) as e:
            sys.stderr.write('[studio] %s 传输中断（客户端断开）：%s\n'
                             % (os.path.basename(path), e))
            self.close_connection = True

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
            if u.path == '/create' or u.path == '/create.html':
                return self._file(os.path.join(WEB, 'create.html'))
            # **独奏化**独立成页（2026-10-08 用户口径："可以在创作台下面单独下来"）：
            # 它是"选一首现成的曲子 + 选**一件**乐器"的独立工序，不该塞在"生成新曲"的表单里，
            # 也不该混在引擎面板的工具行里 ⇒ 侧栏里与创作台并列一项。
            if u.path == '/solo' or u.path == '/solo.html':
                return self._file(os.path.join(WEB, 'solo.html'))
            if u.path == '/api/create/themes':
                return self._json({'ok': True, 'themes': theme_list()})
            if re.match(r'^/[A-Za-z0-9_.\-]+\.(js|css|png|svg|ico|woff2?)$', u.path):
                p = os.path.abspath(os.path.join(WEB, u.path.lstrip('/')))
                if not p.startswith(os.path.abspath(WEB)):
                    return self._err('路径越界', 400)
                return self._file(p)
            if u.path == '/api/songs':
                return self._json({'ok': True, 'root': ROOT, 'lib': LIB,
                                   'songs': songs_list(), 'python': py_exe()})
            if u.path == '/api/song':
                d = song_dir(sid)
                rc, ev = run_py(['scripts/song_events.py', os.path.join(d, 'song.json'), '--json'])
                # ⚠ `song_engine.load` 会往 stdout 打日志（"[还原曲] 生成层默认关…"），
                #   原来用 `ev.startswith('{')` 判 JSON ⇒ 所有还原曲的 events 都变成 None
                #   （2026-10-17 查自动化面板时实测：BGM29/auto_panel_test 全中）。
                #   改为**从第一个 `{` 开始**解析。
                _i = (ev or '').find('{')
                events = (json.loads(ev[_i:]) if rc == 0 and _i >= 0 else None)
                return self._json({'ok': True, 'song': read_json(os.path.join(d, 'song.json')),
                                   'render': read_json(os.path.join(d, 'render.json')) or {},
                                   'events': events, 'events_err': None if events else ev[-800:]})
            if u.path == '/api/stop':
                return self._stop_job((q.get('id') or [''])[0])
            if u.path == '/api/job':
                jid = (q.get('id') or [''])[0]
                j = JOBS.get(jid)
                if not j:
                    return self._err('没有这个任务（面板重启过且账本里也没留下它）: %s' % jid, 404)
                # ⚠ **一律 `j.get(k)`**：账本里读回来的条目字段可能比内存里少
                #   （2026-10-14 实测：账本没存 `out`，而这里原来是 `j[k]` 硬索引 ⇒
                #    `/api/job` 直接 500 —— 前端 `watchJob` 于是走 catch 分支，
                #    界面又变成"卡住"）。宽容取值 + 下面给默认值。
                return self._json({'ok': True,
                                   'job': {k: j.get(k) for k in ('id', 'song', 'kind',
                                                                 'state', 'rc', 'out')},
                                   'log': '\n'.join((j.get('log') or [])[-200:])})
            if u.path == '/api/jobs':
                # 最近任务一览（**账本里的也在**）：面板/排查都能看出"上一次到底跑完没有"，
                # 而不是像以前那样——重启一次就什么都不知道了。
                with LOCK:
                    rows = sorted(JOBS.values(), key=lambda x: x.get('started') or 0,
                                  reverse=True)[:JOBS_KEEP]
                    out = [{k: r.get(k) for k in ('id', 'song', 'kind', 'state', 'rc',
                                                  'started', 'ended')} for r in rows]
                return self._json({'ok': True, 'jobs': out})
            if u.path == '/api/audio':
                kind = (q.get('kind') or ['mix'])[0]
                if kind == 'mix':
                    d = song_dir(sid, need_json=False)
                    fn = (q.get('f') or [''])[0]
                    if fn:
                        # 指定文件名：纯音频 A/B 目录里逐个试听用（前端"▶ 试听"按钮）
                        p = os.path.abspath(os.path.join(d, fn))
                        if not p.startswith(os.path.abspath(d)) or not os.path.isfile(p):
                            return self._err('文件不存在：%s' % fn, 404)
                        return self._file(p)
                    r = read_json(os.path.join(d, 'render.json')) or {}
                    for ext in ('.ogg', '.wav'):
                        p = os.path.join(d, (r.get('out') or '') + ext)
                        if os.path.isfile(p):
                            return self._file(p)
                    # 纯音频目录（没有 render.json）：把目录里第一个音频当作"主混音"
                    try:
                        aud = sorted(f for f in os.listdir(d)
                                     if f.lower().endswith(AUDIO_EXT))
                    except OSError:
                        aud = []
                    if aud:
                        return self._file(os.path.join(d, aud[0]))
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
                # 画像路径走 `scorecard.ref_path`（唯一真源）：聚合画像在
                # `refs/mix_targets/` 下，只拼 `refs/` 会让"参考曲 A/B"拿不到音频。
                sys.path.insert(0, os.path.join(TOOLCHAIN, 'scripts'))
                import scorecard as _sc
                # 参考音频一律走 `scorecard.ref_audio`（唯一真源）：画像里的 `file` 常常
                # **只是文件名**（素材因版权不随仓库分发），要按 `BGM_REF_DIR` /
                # `studio/.refdir` 解析；聚合画像还得先落到成员画像上。直接把 `file`
                # 当路径用 → 这个按钮对所有主题曲目恒 404（2026-09-19 实测）。
                f = _sc.ref_audio(r.get('ref') or 'BGM16c')
                if not f:
                    # ⚠ **两种"没有"要分开**（2026-10-09）：
                    #   · 画像**在**、只是参考曲音频没自备（**素材不随仓库分发**，版权原因）
                    #     ⇒ 这是**预期内**的缺失，回 **204**（不是 404）—— 否则浏览器控制台多一条
                    #     "加载失败"，而 `browser_check_create.js` 把任何 console/network error
                    #     判成 FAIL ⇒ 全新 clone 上必然假红（实测：四页全 200，只这一条判 FAIL）。
                    #   · 画像**本身**都找不到 ⇒ 那才是真配置错（ref 名字写错），仍旧 404 并说清怎么办。
                    _ref = r.get('ref') or 'BGM16c'
                    if os.path.isfile(_sc.ref_path(_ref)):
                        return self._no_content('X-BGM-Ref-Audio', 'unavailable')
                    return self._err(
                        '参考曲音频不可用：画像只记了文件名，素材不随仓库分发 —— '
                        '设环境变量 BGM_REF_DIR，或在 studio/.refdir 里写一行素材目录'
                        '（见 INSTALL.md）', 404)
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
                d = song_dir(sid, need_json=False)
                fs = []
                for fn in sorted(os.listdir(d)):
                    p = os.path.join(d, fn)
                    if os.path.isfile(p):
                        fs.append({'name': fn, 'size': os.path.getsize(p),
                                   'mtime': os.path.getmtime(p),
                                   # ⚠ `sid` 也必须 `quote`（2026-10-09）：曲名放开中文/空格后，
                                   #   这里原来是裸 `id=%s` ⇒ 含 `&`/`#`/`%` 的名字会把 URL 切坏
                                   #   （同一文件里别处都 quote 了，只有这一处漏）。
                                   'url': '/api/dl?id=%s&f=%s'
                                          % (urllib.parse.quote(sid), urllib.parse.quote(fn))})
                return self._json({'ok': True, 'dir': d, 'files': fs})
            if u.path == '/api/dl':
                fn = (q.get('f') or [''])[0]
                d = song_dir(sid, need_json=False)
                p = os.path.abspath(os.path.join(d, fn))
                if not p.startswith(os.path.abspath(d)):
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
            if u.path == '/api/lib':
                # **切换曲库根**（用户："要能更改目录"）：校验目录里有 songs/ 再切，并持久化。
                global LIB, EXPORT_DIR, TMP_AUDIO
                body = self._body()
                raw = str(body.get('path') or '').strip().strip('"').strip("'")
                if not raw:
                    return self._err('请给出曲库目录（含 songs/ 的目录）')
                p = os.path.abspath(os.path.expanduser(raw))
                if not os.path.isdir(p):
                    return self._err('目录不存在：%s' % p)
                _m, _b = probe_lib(p)
                if _m in ('missing', 'none'):
                    return self._err(
                        '这个目录里找不到任何 song.json：%s\n'
                        '曲库根可以是下面任一种（面板会自动认）：\n'
                        '  · 含 songs/<曲目>/song.json 的目录  ← 工具链标准\n'
                        '  · 含 <曲目>/song.json 的目录         ← 一层子目录\n'
                        '  · 本身就是一首曲子（目录里直接有 song.json）' % p)
                LIB = p
                EXPORT_DIR = os.path.join(LIB, 'export')
                try:
                    os.makedirs(EXPORT_DIR, exist_ok=True)
                except OSError:
                    pass
                try:
                    with open(LIB_FILE, 'w', encoding='utf-8') as f:
                        f.write(LIB)
                except OSError:
                    pass
                return self._json({'ok': True, 'lib': LIB, 'songs': len(songs_list()),
                                   'saved': LIB_FILE})
            if u.path == '/api/song':
                body = self._body()
                song = body.get('song') or body
                d = song_dir(sid)
                tmp = os.path.join(d, 'song.json.tmp')
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(song, f, ensure_ascii=False, indent=1)
                # 保存前校验 automation（非法当场拒，别写坏了等渲染才炸）
                _chk = ("import sys,json,json_io,song_engine;"
                        "d=json.load(open(sys.argv[1],encoding='utf-8'));"
                        "song_engine.validate_automation(d);"
                        "json_io.normalize(sys.argv[1])")
                rc, out = run_py(['-c', _chk, tmp])
                if rc != 0:
                    os.remove(tmp)
                    return self._json({'ok': False, 'error': '规范写回失败: ' + out[-400:]})
                os.replace(tmp, os.path.join(d, 'song.json'))
                # 保存后顺手把 notes.md 的「速度」行对齐（改了 bpm 不改它 = 交付文档写错速度）
                _fixed = _sync_notes_speed(d)
                return self._json({'ok': True, 'saved': os.path.join(d, 'song.json'),
                                   'notes_speed_synced': _fixed, 'log': out[-400:]})
            if u.path == '/api/new':
                body = self._body()
                nid, _nmerr = check_song_name(body.get('id'))
                if _nmerr:
                    return self._err('%s（%s）' % (_nmerr, SONG_NAME_HINT))
                # **模板依据走主题模板包**（用户口径：一次生成依据同主题 ≥8 首白名单模板）。
                # 老 `--from <现成曲目>` 仍可用，但它会被 check_song 判为"依据不合规"。
                theme = (body.get('theme') or '').strip()
                # 对标画像：**该主题自己的聚合混音目标**优先（2026-10-09 修，见 `ref_for_theme`
                # 的 docstring：`first_ref()` 会让每首面板生成的曲子都照 BGM01 调参）。
                # ⚠ 前端想指定就显式传 `ref`，否则一律按主题解析 —— 别再"随便挑第一首"。
                ref = (body.get('ref') or '').strip() or ref_for_theme(theme)
                src = (body.get('from') or '').strip()
                # **重生成（覆盖）**：`--force`，2026-09-18 补。没有它时 `new_song` 碰到同名
                # 目录只打印一句"已存在"就退出，而面板照样回 `ok:true` —— 用户以为重新生成
                # 成功了，其实 `song.json` 一字未动（引擎改了也不会进这首曲子；实测害我白调
                # 两轮参数）。面板是"改完立刻再生成一版"的主场景，这个开关必须有。
                force = bool(body.get('force') or body.get('overwrite'))
                seed = body.get('seed')
                if theme:
                    args = ['scripts/new_song.py', nid, '--theme', theme]
                else:
                    if not src:
                        # 老默认写死 `05_d135_cheerful`，那首早就不在库里了 → 静默失败
                        cand = [s['id'] for s in songs_list() if s.get('mids')]
                        src = cand[0] if cand else ''
                    if not src:
                        return self._err('没给 theme，也没有能当模板的曲目'
                                         '（新歌请用 theme，见 --list-themes）')
                    args = ['scripts/new_song.py', nid, '--from', src,
                            '--style', (body.get('style') or 'daily')]
                if seed not in (None, ''):
                    try:
                        args += ['--seed', str(int(seed))]
                    except (TypeError, ValueError):
                        return self._err('seed 要是整数（例：7 / 21）')
                # **编配白名单**（`--arr-only`，2026-10-08 用户口径："能不能只用规定的几个
                #   乐器演奏"）。只在**生成时**用这几件乐器；"独奏化"是另一条路径
                #   （`kind=render-tune-solo` → `solo_instrument.py`），两者互不替代。
                arr_only = (body.get('arr_only') or '').strip()
                if arr_only:
                    args += ['--arr-only', arr_only]
                # **指定主奏音色**（`--lead sax` / `--lead sax,trumpet`，2026-10-08）：主奏本来由
                #   主题主奏池 + seed 决定 —— 15 个主题里只有 battle/night 的池含萨克斯，所以
                #   别的主题生成出来没有萨克斯（用户问："为什么直接生成没有萨克斯"）。
                lead = (body.get('lead') or '').strip()
                if lead:
                    args += ['--lead', lead]
                # **逐轨音色覆盖**（`--programs "Melody=73,Strings=49"`，2026-10-19）：
                # 主题默认音色以前只显示"主奏"，其余轨隐藏且改不了；现在面板把每轨列出来，
                # 改过的随生成下发（`new_song.apply_program_overrides` 逐轨打印前后值）。
                prog = (body.get('programs') or '').strip()
                if prog:
                    args += ['--programs', prog]
                # **逐轨关掉**（`--mute "Pad,Glock"`，2026-10-19）：面板「默认乐器」里选
                # 「无（关掉这一轨）」的那些轨 → 逐段把 `arr` 置 False（与改音色是两件事）。
                mute = (body.get('mute') or '').strip()
                if mute:
                    args += ['--mute', mute]
                # **想要的时长（秒）**（`--seconds`，2026-10-08 第二轮④）：与"改 BPM"是同一个量的
                #   两种写法，但 `--seconds` 会**联合解**「段数 + BPM」，尽量让速度留在该主题模板
                #   的实测区间内（见 `new_song.duration_plan`）。给了它，前端就**不要再**走
                #   "生成后回写 bpm"那条路（那条只改速度、不改结构，还会把速度顶到区间外）。
                secs = body.get('seconds')
                if secs not in (None, '', 0, '0', 'null'):
                    try:
                        secs = float(secs)
                    except (TypeError, ValueError):
                        return self._err('seconds（想要的时长）要是数字，单位秒（例：100）')
                    if not 10 <= secs <= 600:
                        return self._err('seconds 要在 10~600 秒之间（给的是 %s）' % secs)
                    args += ['--seconds', '%.1f' % secs]
                # **表现层三件套**（2026-10-13 接；用户听感认可后落到面板）：
                #   `joint_cadence`（句尾让位 + 主奏收束）· `phrase_vary`（0/1/2 伴奏逐句换句型）·
                #   `legato_fill`（内声部补空隙的拍数，0 = 关）。
                # ⚠ **三个都在这里按关键字转发**：缺一个就是"面板上点了、产物没变"（静默失效），
                #   守卫 `t_express_panel_contracts` 逐字核对；未知值**直接报错**，不静默退回默认。
                for _k, _flag, _ok in (('joint_cadence', '--joint-cadence', (0, 1)),
                                       ('phrase_vary', '--phrase-vary', (0, 1, 2))):
                    _v = body.get(_k)
                    if _v in (None, '', 'null'):
                        continue
                    try:
                        _v = int(_v)
                    except (TypeError, ValueError):
                        return self._err('%s 要是 %s 之一（给的是 %r）' % (_k, _ok, body.get(_k)))
                    if _v not in _ok:
                        return self._err('%s 要是 %s 之一（给的是 %r）' % (_k, _ok, _v))
                    args += [_flag, str(_v)]
                _lf = body.get('legato_fill')
                if _lf not in (None, '', 'null'):
                    try:
                        _lf = float(_lf)
                    except (TypeError, ValueError):
                        return self._err('legato_fill 要是 0~2.0 的拍数（给的是 %r）' % body.get('legato_fill'))
                    if not 0.0 <= _lf <= 2.0:
                        return self._err('legato_fill 要在 0~2.0 拍之间（给的是 %s）' % _lf)
                    args += ['--legato-fill', '%.2f' % _lf]
                # 能量：创作台的「提要求」会给一个 0.8~1.3 的建议值（`ask_parse` 推的）
                try:
                    eg = float(body.get('energy_gain') or 0)
                except (TypeError, ValueError):
                    eg = 0
                if eg:
                    if not 0.5 <= eg <= 2.0:
                        return self._err('energy_gain 要在 0.5~2.0 之间（给的是 %s）' % eg)
                    args += ['--energy-gain', '%.3f' % eg]
                if force:
                    args.append('--force')
                # ⚠ **ref 为空就不传**：传一个空串会把 `new_song` 的"按主题包选画像"顶掉，
                #   成绩单随后报"参考画像不存在"（`ref_for_theme` 找不到画像时返回 ''）。
                if ref:
                    args += ['--ref', ref]
                rc, out = run_py(args, timeout=300)
                # 同名目录会让 `new_song` 拒绝（只打印"已存在"）。面板的生成场景几乎总是
                # "改完引擎再来一版"，所以**自动补一次 `--force` 重试**，并把 `force: true`
                # 写回响应 —— 用户才不会以为"什么都没发生"。
                if rc != 0 and '已存在' in (out or '') and not force:
                    force = True
                    args.append('--force')
                    rc, out = run_py(args, timeout=300)
                # **`--force` 之后必须重渲染**：它只重建 `song.json`，`.mid`/`.ogg` 还是旧的
                # （官方口径："改了引擎 ⇒ 重生成 + 重渲染，两步缺一不可"）。面板在这条路径上
                # 默认顺手渲染，省掉"生成完忘了渲染、听到的还是上一版"这个高频坑。
                rrc, rout = None, ''
                do_render = body.get('render')
                if do_render is None:
                    do_render = bool(force)
                if rc == 0 and do_render:
                    rrc, rout = run_py(['scripts/make_song.py', nid], timeout=600)
                return self._json({'ok': rc == 0, 'rc': rc, 'log': out[-3000:],
                                   'id': nid, 'theme': theme, 'from': src, 'ref': ref,
                                   'force': force, 'seed': seed,
                                   'render_rc': rrc, 'render_log': (rout or '')[-1500:]})
            if u.path == '/api/ask':
                # 「提要求」：中文口语 → 生成参数（**纯本地规则**，见 scripts/ask_parse.py）。
                # 解析器只给**建议**，前端会把每一项摊开让人改 —— 读错一个词不会毁掉整首歌。
                body = self._body()
                text = str(body.get('text') or '').strip()
                if not text:
                    return self._err('先写一句要求（例：来一首欢快的钢琴曲，90 秒）')
                rc, out = run_py([os.path.join(_SCRIPTS, 'ask_parse.py'), '--text', text],
                                 timeout=60)
                parsed = None
                for ln in reversed((out or '').strip().splitlines()):
                    s = ln.strip()
                    if s.startswith('{'):
                        try:
                            parsed = json.loads(s)
                            break
                        except ValueError:
                            continue
                if parsed is None:
                    return self._json({'ok': False, 'error': '要求解析失败',
                                       'log': (out or '')[-1500:]})
                return self._json({'ok': True, 'parsed': parsed, 'log': (out or '')[-1500:]})
            if u.path == '/api/upload':
                # 参考音频上传：桌面壳里前端拿不到本机绝对路径，只能把文件读成 base64 发过来
                # ⚠ **这里不许再写一次 `import base64`**（2026-10-09 实测的 bug）：模块顶部已有
                #   `import base64`，在这里再 import 一次会让 `base64` 变成**整个 do_POST 的局部
                #   变量** ⇒ 同一方法里**靠后**的 `/api/ed/import`（MIDI 编辑器导入文件那条路）
                #   直接 `UnboundLocalError: cannot access local variable 'base64'` ⇒ HTTP 500。
                #   症状极隐蔽：**按服务端路径导入正常**（不走 base64），只有"⬆ 导入 MIDI"选文件必炸。
                body = self._body()
                # ⚠ 这里原来是 `re.sub(r'[^0-9A-Za-z_-]+','_')` —— **静默改写**而不是拒绝：
                #   中文名会被整串改成 `_` ⇒ strip 后为空 ⇒ 报"先给这首曲子起个名"，
                #   用户看到的是一句与"我明明起了名"矛盾的错。现在与 `/api/new` 同一口径。
                nid, _uerr = check_song_name(body.get('id'))
                if _uerr:
                    return self._err('%s（%s）' % (_uerr, SONG_NAME_HINT))
                ext = os.path.splitext(str(body.get('name') or ''))[1].lower()
                if ext not in UPLOAD_EXT:
                    return self._err('不认这种音频后缀：%s（支持 %s）'
                                     % (ext or '（没有后缀）', ' '.join(UPLOAD_EXT)))
                try:
                    blob = base64.b64decode(body.get('data_b64') or '')
                except Exception as e:                              # noqa: BLE001
                    return self._err('音频解码失败：%s' % e)
                if not blob:
                    return self._err('音频内容为空')
                d = os.path.join(EXTRACT_WORK, nid)
                os.makedirs(d, exist_ok=True)
                p = os.path.join(d, 'source' + ext)
                with open(p, 'wb') as f:
                    f.write(blob)
                return self._json({'ok': True, 'path': p, 'bytes': len(blob), 'id': nid,
                                   'name': os.path.basename(str(body.get('name') or ''))})
            if u.path == '/api/import-midi':
                # **从 MIDI 新建**（2026-10-17）：现成的 .mid → songs/<id>/song.json。
                # 与 `/api/extract` 的分工：提取是"音频 → 转录 → 数据"（分钟级、要起任务），
                # 这里只是"已有 MIDI → 引擎"（秒级），所以同步跑、不走任务队列。
                body = self._body()
                nid, _eerr = check_song_name(body.get('id'))
                if _eerr:
                    return self._err('%s（%s）' % (_eerr, SONG_NAME_HINT))
                try:
                    bps = int(body.get('bars_per_section') or 8)
                except (TypeError, ValueError):
                    return self._err('每段小节数必须是整数')
                if not (1 <= bps <= 64):
                    return self._err('每段小节数要在 1~64 之间')
                src = None
                if body.get('path'):
                    src = os.path.abspath(str(body['path']))
                    if not os.path.isfile(src):
                        return self._err('MIDI 文件不存在：%s' % src)
                elif body.get('data_b64'):
                    raw = base64.b64decode(body.get('data_b64') or '')
                    if raw[:4] != b'MThd':
                        return self._err('这不是标准 MIDI 文件（缺 MThd 头）')
                    d = os.path.join(TMP_AUDIO, 'import')
                    os.makedirs(d, exist_ok=True)
                    src = os.path.join(d, nid + '.mid')
                    with open(src, 'wb') as f:
                        f.write(raw)
                else:
                    return self._err('没有收到 MIDI（path 或 data_b64 二选一）')
                if any(s.get('id') == nid for s in songs_list()) and not body.get('force'):
                    return self._err('曲库里已经有 %s 了 —— 换个名字，或勾上"同名重建"' % nid)
                cmd = [sys.executable, os.path.join(ROOT, 'scripts', 'midi_to_song.py'),
                       src, '--name', nid, '--bars-per-section', str(bps)]
                if body.get('bpm'):
                    try:
                        cmd += ['--bpm', str(float(body['bpm']))]
                    except (TypeError, ValueError):
                        return self._err('BPM 必须是数字')
                if body.get('force'):
                    cmd += ['--force']
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True,
                                       timeout=180, cwd=ROOT, encoding='utf-8',
                                       errors='replace')
                except subprocess.TimeoutExpired:
                    return self._err('导入超时（>180 秒）')
                out = ((r.stdout or '') + (r.stderr or '')).strip()
                if r.returncode != 0:
                    return self._err('导入失败：\n%s' % out[-1500:])
                m = re.search(r'写出 .*?（(\d+) 轨 / (\d+) 音）', out)
                return self._json({'ok': True, 'id': nid, 'log': out[-3000:],
                                   'tracks': int(m.group(1)) if m else None,
                                   'notes': int(m.group(2)) if m else None})
            if u.path == '/api/extract':
                body = self._body()
                nid, _eerr = check_song_name(body.get('id'))
                if _eerr:
                    return self._err('%s（%s）' % (_eerr, SONG_NAME_HINT))
                mode = (body.get('mode') or 'fast').strip()
                if mode not in ('fast', 'full'):
                    return self._err('mode 只能选 fast（只出 MIDI）或 full（完整还原）')
                # 内容路径三档（2026-10-13 接）：默认 `pure` = 用户听感认可的那版。
                # ⚠ 未知档**报错**、不许静默退回 pure —— "选了 A 实际跑了 B"在产物上
                #   要听很久才发现（口径与 `extract_paths.plan` 一致，那里也会抛）。
                import extract_paths as _EP
                path = (body.get('path') or 'pure').strip()
                if path not in _EP.PATHS:
                    return self._err('path 只能选 %s' % ' / '.join(sorted(_EP.PATHS)))
                raw_parts = body.get('parts')
                if isinstance(raw_parts, str):
                    parts = [x.strip() for x in raw_parts.replace('，', ',').split(',') if x.strip()]
                elif isinstance(raw_parts, (list, tuple)):
                    parts = [str(x).strip() for x in raw_parts if str(x).strip()]
                else:
                    parts = []
                # 轨名必须是引擎认识的那 8 条（写错了装配会静默少一层）
                _bad = [t for t in parts if t not in _EP.STEM_TO_ENGINE.values()]
                if _bad:
                    return self._err('parts 里的轨名不认识：%s（可用 %s）'
                                     % (', '.join(_bad),
                                        ' / '.join(sorted(set(_EP.STEM_TO_ENGINE.values())))))
                if path != 'partial':
                    parts = []          # 只有 partial 用得上取轨表（别的档传了也不理）
                src = os.path.abspath(str(body.get('audio') or ''))
                if not os.path.isfile(src):
                    return self._err('参考音频不存在：%s' % src)
                if any(s.get('id') == nid for s in songs_list()) and not body.get('force'):
                    return self._err('曲库里已经有 %s 了 —— 换个名字，或勾上"同名重建"' % nid)
                # 音频时长（写进 notes.md 的来源信息）——读不出来就不写，不让它拦住任务
                secs = None
                try:
                    import soundfile as _sf
                    _i = _sf.info(src)
                    secs = _i.frames / float(_i.samplerate or 1)
                except Exception:                               # noqa: BLE001
                    secs = None
                try:
                    jid = start_job(nid, 'extract:%s' % mode,
                                    {'audio': src, 'seconds': secs,
                                     # ⚠ **`force` 必须带进来**（2026-10-14 实测的静默失效）：
                                     #   它原来只在"重名检查"里用，没进 opts ⇒ `start_job` 里
                                     #   "覆盖重建前清残留"那段**从来没执行过**（守卫也没盯这条）。
                                     'path': path, 'parts': parts,
                                     'force': bool(body.get('force'))})
                except ValueError as e:
                    return self._err(str(e))
                return self._json({'ok': True, 'job': jid, 'id': nid, 'mode': mode,
                                   'path': path, 'parts': parts or list(_EP.DEFAULT_PARTS),
                                   'audio': src, 'seconds': secs})
            if u.path == '/api/stop':
                return self._stop_job((q.get('id') or [''])[0])
            if u.path == '/api/check':
                # 先自己解析一次：纯音频目录在这里会被说清"没有 song.json，检查不了"，
                # 否则用户点"数据契约检查"只会得到 check_song.py 那句含糊的"找不到曲目"
                # （而且它后面还跟一长串"可选: …"，看起来像曲目丢了）。
                try:
                    song_dir(sid)
                except FileNotFoundError as e:
                    return self._json({'ok': False, 'rc': 1, 'log': str(e),
                                       'fails': [], 'other': 0, 'audio_only': True})
                # ⚠ **必须带 `--fast`**（2026-10-18 实测）：不带时跑全套 263 项，
                #   `check_song.py 123` 实测 **161 秒**（面板上像卡死）；`--fast` 只跑
                #   `DATA_CHECKS`，同一曲 **1.8 秒**。本文件顶部的路由注释本来就写着"2 秒"。
                #   全套在沙箱里还会跑 7 项依赖 `scripts/` 的检查 → 7 条 FileNotFoundError
                #   （指向 `_lint_sandbox_<pid>\scripts\...`），`--fast` 不会有。
                rc, out = run_py(['scripts/check_song.py', sid, '--fast'])
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
                if body.get('song'):
                    # **曲库 → 编辑器**（2026-10-17 A2 回程）：曲目目录里的 .mid 直接开，
                    # 不用先知道文件路径（引擎写出的文件名不一定等于曲目名）。
                    import glob as _glob
                    sid = str(body['song']).strip()
                    cands = sorted(_glob.glob(os.path.join(ROOT, 'songs', sid, '*.mid')))
                    if not cands:
                        return self._err('曲目 %s 里没有 .mid（先渲染 / 导出一次）' % sid)
                    eid, model = edit_import_path(cands[0])
                elif body.get('path'):
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
            if u.path == '/api/ed/to-song':
                # **编辑器 → 引擎**（2026-10-17 A2）：当前编辑会话直接落成
                # `songs/<id>/song.json` —— 不用"先导出 .mid、再去创作台导入"。
                # 内存模型优先（未保存的改动也算数）；没传就用会话落盘的那份。
                body = self._body()
                eid = (q.get('eid') or [''])[0]
                nid, _eerr = check_song_name(body.get('id'))
                if _eerr:
                    return self._err('%s（%s）' % (_eerr, SONG_NAME_HINT))
                model = body.get('model') or edit_load(eid)
                if not model or not (model.get('tracks') or []):
                    return self._err('编辑会话是空的（没有轨）')
                try:
                    bps = int(body.get('bars_per_section') or 8)
                except (TypeError, ValueError):
                    return self._err('每段小节数必须是整数')
                if not (1 <= bps <= 64):
                    return self._err('每段小节数要在 1~64 之间')
                dest = os.path.join(ROOT, 'songs', nid)
                if os.path.exists(os.path.join(dest, 'song.json')) and not body.get('force'):
                    return self._err('曲库里已经有 %s 了 —— 换个名字，或勾上"覆盖重建"' % nid)
                try:
                    import json_io as _jio
                    import midi_to_song as _m2s
                    import song_engine as _se
                    data, info = _m2s.build_from_model(
                        model, name=nid, bpm=body.get('bpm'),
                        bars_per_section=bps, verbose=False)
                    if not data.get('notes_extra'):
                        return self._err('这个 MIDI 里没有可导入的音符（全是空轨？）')
                    os.makedirs(dest, exist_ok=True)
                    out = os.path.join(dest, 'song.json')
                    _jio.save(out, data)
                    _se.compose(out, os.path.join(dest, nid + '.mid'), quiet=True)
                except SystemExit as e:
                    return self._err(str(e))
                except Exception as e:                     # noqa: BLE001
                    return self._err('转换失败：%s' % e)
                return self._json({'ok': True, 'id': nid,
                                   'tracks': len(info['counts']),
                                   'notes': sum(info['counts'].values()),
                                   'cc': info.get('kept_cc', 0),
                                   'bends': info.get('kept_bends', 0),
                                   'songs': [s.get('id') for s in songs_list()]})
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
    global ROOT, LIB, PY, EXPORT_DIR, TMP_AUDIO
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--root', default=ROOT,
                    help='工具链根目录（含 scripts/、studio/）—— 找工具用，通常不用改')
    ap.add_argument('--lib', default=None,
                    help='**曲库根**（含 songs/ 的目录）。想让曲库放到别处就指它 —— '
                         '此时工具链仍在原地，脚本照常能跑（2026-09-16 拆分，见文件头注释）')
    ap.add_argument('--export-dir', default=None)
    ap.add_argument('--open', action='store_true', help='启动后打开浏览器')
    ap.add_argument('--keep-tmp-audio', action='store_true',
                    help='不清理音频缓存（默认启动时按预算清，见 prune_tmp_audio）')
    a = ap.parse_args()
    ROOT = os.path.abspath(a.root)
    # 曲库根优先级：**显式 `--lib` > 环境变量 > 面板里改过的持久化值 > 工具链默认**
    # （持久化值最低不能盖过命令行 —— 否则 `--lib` 会被上一次的面板操作"吃掉"）
    if a.lib:
        LIB = os.path.abspath(a.lib)
    elif os.environ.get('BGM_STUDIO_LIB'):
        LIB = os.path.abspath(os.environ['BGM_STUDIO_LIB'])
    elif os.path.isfile(LIB_FILE):
        try:
            _p = open(LIB_FILE, encoding='utf-8').read().strip()
        except OSError:
            _p = ''
        LIB = os.path.abspath(_p) if _p and os.path.isdir(_p) else ROOT
    else:
        LIB = ROOT
    EXPORT_DIR = os.path.abspath(a.export_dir) if a.export_dir else os.path.join(LIB, 'export')
    if not os.path.isdir(os.path.join(ROOT, 'scripts')):
        raise SystemExit('--root 指向的目录里没有 scripts/：%s\n'
                         '（--root 是**工具链根**；曲库外置请用 --lib）' % ROOT)
    os.makedirs(EXPORT_DIR, exist_ok=True)
    os.makedirs(TMP_AUDIO, exist_ok=True)
    global STEM_CACHE
    STEM_CACHE = os.path.join(TMP_AUDIO, 'stems')
    os.makedirs(STEM_CACHE, exist_ok=True)
    # **启动时把音频缓存压回预算**：试听/搜索/配平/分轨每次都往里丢文件，以前从不清理
    # （实测涨到 0.93GB / 12 个 job 目录）。`--keep-tmp-audio` 可跳过（排查缓存相关问题时用）。
    if not a.keep_tmp_audio:
        prune_tmp_audio()
    # **读回任务账本**（放在 `prune_tmp_audio()` 之后：账本是 `.` 开头、pruner 会跳过它，
    # 但仍按"先清理、再读状态"的顺序，免得读到正要被删的东西）
    load_jobs()
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