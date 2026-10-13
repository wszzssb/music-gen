#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""extract_paths.py —— 面板提取的**三档内容路径**（命令序列构造，**不执行任何东西**）。

## 为什么单独一份

`studio\server.py` 的 `extract_plan()` 原来把"分几档"和"每一步怎么拼"混在一个函数里，
于是"某档悄悄退回旧做法"（例如 `partial` 少跑了去鼓混音、或 `pure` 被改成按分轨转录）
**在代码上看不出来、在产物上要听很久才发现**。这里把三档拆成纯函数：
入参 → 命令序列，守卫可以对它做**合成断言**（`selftest.t_extract_paths_contracts`），
变异用例可以逐项注入（`mutation_check.py` 第 99 组）。

## ⚠ 两个目录**必须分清**（2026-10-13 实测踩到，一次就废整条链）

| 名字 | 是什么 | 谁用 |
|---|---|---|
| `flat` | **分轨根目录** `<W>/stems`（`stem_split -o` 的实参） | `stem_split.py -o <flat>` |
| `h6` | **六轨目录** `<flat>/htdemucs_6s/<曲名>`（**里面才是 6 个 wav**） | `transcribe_ymt3 --stems-dir` · `drumless_mix --stems` · 装配 `--stems-dir`（量力度）· 收尾两步 |

为什么：`scripts\stem_split.py` 的 `split_one()` 自己会拼 `<outroot>/<模型名>/<音频名>/`
（见那个函数第 55 行）—— 所以 `-o` **只能给根目录**。实测给成"六轨目录"的后果是
**双层嵌套** `<…>/htdemucs_6s/source/htdemucs_6s/source/*.wav`，而 `--stems-dir` 指到的是
那个空的外层 ⇒ **逐音力度一条都没配上**（恒 100 = 打字机，且只在日志里留一行 `!!`）、
`drumless_mix` 直接报「没有可用轨（目录里 0 个 wav）」⇒ 任务在第三步就死。
⚠ 这两个目录名长得像、都在 `<W>/stems` 底下，是这轮最容易混的一处 —— 所以**显式分成两个参数**，
并由守卫逐条核对（`-o` 的实参**不许**落到 `h6` 上）。

## 三档（用户 2026-10-13 点名要；证据见 `docs\HANDOFF-PERC-CLEAN.md`）

| 档 | 转录吃什么 | 依据 | 实测 |
|---|---|---|---|
| `pure`（默认） | **全混音**（单来源） | 用户听感认可的那版 | BGM23 独立尺子 23.9% 有基音峰 · 无峰 54.0% |
| `partial` | 全混音 **+ 指定轨改从"去鼓混音"转录** | `notes.md` §5.10 逐轨独立尺子表 | 只值 2 轨（Hook 19.5%→28.2% · Pad 12.2%→13.6%），代价 +1447 音 |
| `all`（面板上叫「**其它（效果不好）**」） | **逐条分轨各自转录**再装配 | —— | **效果不好**：每项都比 `pure` 差（无峰 56.3% vs 54.0%）· 慢 3~6 倍 |

⚠ **`partial` 的取轨表是"按曲标定"的，不是通用配方**：表里那些百分比来自 BGM23 一首
（n=1）。面板让用户自己填"哪几轨走分轨"，默认 `Hook,Pad` 只是**上一首的实测结果**，
不是"对大部分音乐都成立"的结论。

## 用法

```python
import extract_paths as EP
cmds = EP.plan(name='曲名', path='partial', W=W, flat=flat, h6=h6, audio=src, mode='fast',
               scripts=S, py_exe=py, py_ml=py_ml, parts=['Hook', 'Pad'])
```

每个元素都是 `argv` 列表（第一项是脚本名或绝对路径），由 `server.start_job` 逐条执行。
两处 `-c` 占位常量（`FLAT_STEMS_CODE` / `ASM_CODE`）由 `studio\server.py` 替换成同名常量
——**同一个名字**是刻意的，守卫据此核对"替换真的接上了"。
"""
import json
import os

#: `all` 档要逐条转录的 Demucs 六轨（**顺序即执行顺序**，只影响日志可读性）
ALL_STEMS = ('drums', 'bass', 'piano', 'guitar', 'other', 'vocals')
#: `partial` 的默认取轨表 —— ⚠ 这是 **BGM23 一首的实测结果**，不是通用配方
DEFAULT_PARTS = ('Hook', 'Pad')
#: 面板上"换成去鼓版"那排**按钮**的单一真源：`[(引擎轨名, 按钮文案)]`。
#: `studio\web\create.html` 的 chip 必须与这里**逐个对应**（守卫 `t_extract_paths_contracts`
#: 核对两侧 —— 面板加了按钮而后端不认 / 后端认了而面板没按钮，都是静默失效）。
#: ⚠ 文案带**实测收益**：来自 `D:\test\BGM23_装配提取_交付\notes.md` §5.10 的逐轨独立尺子表
#: （n=1，只有 BGM23 一首）—— 所以按钮上写的是"这首的实测"，不是通用结论。
PART_CHOICES = (
    ('Hook', 'Hook 吉他层（BGM23 实测 19.5%→28.2%）'),
    ('Pad', 'Pad 垫子层（BGM23 实测 12.2%→13.6%）'),
    ('Piano', 'Piano 钢琴'),
    ('Strings', 'Strings 弦乐'),
    ('Glock', 'Glock 钟琴'),
    ('Bass', 'Bass 贝斯'),
    ('Arp', 'Arp 琶音'),
    ('Melody', 'Melody 旋律'),
)
#: demucs 分轨名 → 引擎轨名（与 `transcribe_ymt3._ENGINE_MAP` 口径不同：那条映射的是
#: YourMT3 的乐器名，这条映射的是**分轨文件名**）
STEM_TO_ENGINE = {
    'piano': 'Piano', 'bass': 'Bass', 'drums': 'Drums', 'guitar': 'Hook',
    'other': 'Strings', 'vocals': 'Pad',
}
#: `partial` 档"去鼓混音单独转录"那次的 `--name`。⚠ **必须是模块级常量**：它决定产物名
#: `nodrums_<轨>.mid`，而装配 `--mid` 指的是同一个名字 —— 两处各写一遍就会"静默少一层"
#: （守卫 `t_extract_paths_contracts` 逐字核对这两处；变异用例第 99 组会注入不一致）。
NODRUMS_NAME = 'nodrums'
#: 切轨产物的**子目录后缀**：`_split_by_engine_track` 把逐轨 MIDI 固定写进 `<out>/<name>_stems/`
#: （2026-10-13 实测：装配原来指到 `<out>/` 那一层 ⇒ 三首全长曲目全部 exit 3）。
SPLIT_SUFFIX = '_stems'
#: 去鼓那次的 `--song-name` 后缀。⚠ **必须与主曲名分开**（2026-10-13 实测）：
#: ① 去鼓转录**不能加 `--no-song`** —— 逐轨 MIDI 是在"写 song.json"那条支路里切的
#:    （`transcribe_ymt3._run_song_pipeline` → `_split_by_engine_track`），加了 `--no-song`
#:    就**只有一条合并 MIDI、没有 `nodrums_<轨>.mid`** ⇒ 装配必挂（实测三首全长曲目全挂）。
#: ② 而它会往 `songs/<song-name>/song.json` 写一份自己的（**去鼓版 bpm 是翻倍的**）——
#:    那份**必须**与 `pure` 那版分开，否则会把"bpm 基准"覆盖掉，装配就钉错速度了。
NODRUMS_SUFFIX = '_nodrums'
#: `all` 档"逐条分轨转录"的输出根目录名（产物在 `<W>/<它>/<分轨名>/<分轨名>.mid`）
PER_STEM_DIR = 'per_stem'
#: demucs 模型名（`stem_split -m` 的实参，**也是它自建的那层目录名**）
MODEL = 'htdemucs_6s'
#: `-c` 占位（调用方必须替换成 `studio\server.py` 的 `_FLAT_STEMS_CODE`）
FLAT_STEMS_CODE = 'FLAT_STEMS_CODE_PLACEHOLDER'
#: `-c` 占位（调用方必须替换成下面的 `ASM_CODE`）
ASM_CODE = 'ASM_CODE_PLACEHOLDER'
#: `-c` 占位（调用方必须替换成下面的 `GAPFILL_CODE`）
GAPFILL_CODE = 'GAPFILL_CODE_PLACEHOLDER'
#: 保留副本的文件名后缀（装配**之前**的 `song.json` 会被留成 `<song.json>.pre_asm`）
PRE_ASM_SUFFIX = '.pre_asm'
#: 装配命令里供 `ASM_CODE` 替换的那段（**只出现一次**）
SUB_BPM = '{bpm}'
#: `partial`/`all` 装配用的 `-c` 代码：读 pure 那版的 bpm、拼上各轨 `--mid`、执行装配。
#: argv 口径（位置参数）：`[1]=py_exe [2]=cwd [3]=装配 argv 的 JSON [4]=BPM 来源 song.json
#: [5:]=轨=文件`
#:
#: 为什么塞进 `-c` 而不在构造期读文件：命令序列在**面板接单那一刻**就定下了，而 `pure`
#: 那版的 `song.json` 那时还不存在（要等第 2 步转录完）；而**装配必须钉死 bpm** ——
#: 去鼓输入会让 `--auto` 的节拍判定翻倍（实测 74.1→147.7 / 68→136），不钉就整曲时间轴错一倍
#: （`notes.md` §5.2 第 3 条）。所以这一步只能"运行时"做。
#: ⚠ 这里**只读**那份 `song.json`（真正会覆盖它的是 `transcribe_to_song` 自己），
#:   读到的因此就是 `pure` 那版的 bpm。
ASM_CODE = (
    "import json,os,re,subprocess,sys\n"
    "py,cwd,asmjson,bpmsrc=sys.argv[1:5]\n"
    "step=json.loads(asmjson)\n"
    "mids=sys.argv[5:]\n"
    "# ① bpm **必须**钉死（去鼓输入会让 `--auto` 的节拍判定翻倍）：来源是第一步那条链写下的\n"
    "#    `songs/<曲名>/song.json`；没有它就拒绝装配 —— 宁可不做，也不能出时间轴错一倍的产物。\n"
    "if not os.path.isfile(bpmsrc):\n"
    "    print('[装配] ✗ 找不到 bpm 来源 %s（= `songs/<曲名>/song.json`，由第一步那条链写下）'\n"
    "          '⇒ 拒绝装配（没有它就无法钉死 bpm，去鼓输入会让节拍判定翻倍）。' % bpmsrc,\n"
    "          flush=True)\n"
    "    sys.exit(3)\n"
    "bpm=float(json.load(open(bpmsrc,encoding='utf-8'))['bpm'])\n"
    "print('[bpm 钉死] %.4f（来自 %s）—— 去鼓输入会让节拍判定翻倍，不钉则时间轴错一倍'\n"
    "      %(bpm,bpmsrc),flush=True)\n"
    "# ② 逐轨 MIDI **缺了不算错**：切轨只写「这条轨真有音」的那几条（去鼓版里某轨为空是正常\n"
    "#    现象，如实测 BGM11 的 Hook）。缺的那些由后面 `GAPFILL_CODE` 那步从装配前的副本回灌。\n"
    "#    只有**一条都没产出**时才拒绝（那才说明上游真的挂了）。\n"
    "have=[m for m in mids if os.path.isfile(m.split('=',1)[1])]\n"
    "missed=[m.split('=',1)[0] for m in mids if m not in have]\n"
    "if not have:\n"
    "    print('[装配] ✗ 去鼓那段**一条逐轨 MIDI 都没产出**（%s）—— 上游转录失败了，'\n"
    "          '常见的两种：撞 256 步解码上限（`transcribe_ymt3` 按设计拒绝接续），'\n"
    "          '或该段音频根本不是那条链能处理的输入。' % ','.join(missed), flush=True)\n"
    "    sys.exit(3)\n"
    "if missed:\n"
    "    print('[装配] ⚠ 这些轨在去鼓版里**没有音**（切轨没产出）：%s —— 退回装配前那份的内容。'\n"
    "          % ','.join(missed), flush=True)\n"
    "argv=[re.sub(r'\\{bpm\\}','%.4f'%bpm,str(x)) for x in step]\n"
    "for m in have:\n"
    "    tr,path=m.split('=',1)\n"
    "    argv+=['--mid',m]\n"
    "    print('[装配] %s <- %s'%(tr,path),flush=True)\n"
    "sys.exit(subprocess.run(argv,cwd=cwd).returncode)\n"
)
#: 装配**之后**那步：把"装配把它整片丢了"的轨从装配前的副本**逐音回灌**。
#: argv 口径：`[1]=song.json [2]=副本(装配前那份)`
#:
#: 为什么必须有（2026-10-13 三首全长端到端实测的**静默丢内容**）：装配是拿
#: `transcribe_to_song --mid 轨=文件` **重建**一份 song.json，只喂"存在的逐轨 MIDI" ⇒
#: **合并转录里才有、没有独立逐轨文件的那几层**（实测就是 `Melody` 与 `Glock`）会整片消失，
#: 而任务照样 rc=0。实测三首各丢 **157 / 369 / 557 个音**。
#: ⚠ 回灌只针对"副本里有、装配后没有"的轨 —— **不许**覆盖装配刚写好的内容（那正是本轮
#:   那两轨的意义），条数打进日志（不静默）。
GAPFILL_CODE = (
    "import json,os,sys\n"
    "sj,bak=sys.argv[1:3]\n"
    "if not os.path.isfile(bak):\n"
    "    print('[补轨] （没有装配前的副本 %s ⇒ 跳过）'%bak,flush=True)\n"
    "    sys.exit(0)\n"
    "a=json.load(open(sj,encoding='utf-8'))\n"
    "b=json.load(open(bak,encoding='utf-8'))\n"
    "na,nb=a.get('notes_extra') or {},b.get('notes_extra') or {}\n"
    "gap={k:v for k,v in nb.items() if v and not na.get(k)}\n"
    "empty=[k for k,v in nb.items() if not v]\n"
    "print('[补轨] 装配后 %d 轨 %d 音；装配前 %d 轨 %d 音；副本里的空轨：%s'\n"
    "      %(len([k for k,v in na.items() if v]),sum(len(v) for v in na.values()),\n"
    "        len([k for k,v in nb.items() if v]),sum(len(v) for v in nb.values()),\n"
    "        ','.join(empty) or '无'),flush=True)\n"
    "if not gap:\n"
    "    print('[补轨] 没有需要回灌的轨（装配没丢东西）',flush=True)\n"
    "    sys.exit(0)\n"
    "for k,v in gap.items():\n"
    "    na[k]=v\n"
    "    print('[补轨] %s ← 回灌 %d 音（装配把它整片丢了）'%(k,len(v)),flush=True)\n"
    "a['notes_extra']=na\n"
    "json.dump(a,open(sj,'w',encoding='utf-8',newline='\\n'),ensure_ascii=False,indent=1)\n"
    "print('[补轨] 已写回 %s（现 %d 轨 %d 音）'%(sj,len([k for k,v in na.items() if v]),\n"
    "      sum(len(v) for v in na.values())),flush=True)\n"
)


def h6_of(flat, audio):
    """分轨根目录 `flat` + 音频路径 → **六轨目录**（`stem_split` 会写进去的那一层）。

    口径与 `stem_split.split_one()` 第 55 行一致：`<outroot>/<模型名>/<音频名去扩展名>`。
    **两处都从这里推、别在别处再拼一遍** —— 那正是本轮"双层嵌套"的成因。

    ⚠ 自检两件事（口径坏了就在这里炸，而不是等到转录时"0 条轨配不上"）：
    ① 模型名不能是空串/路径分隔符（那会让"六轨目录"退化成"分轨根目录"）；
    ② 结果必须**严格在** `flat` 下面（否则又回到本轮那个 bug）。
    """
    if not MODEL or MODEL in ('.', '..') or os.sep in MODEL or '/' in MODEL:
        raise ValueError('模型名 %r 不能当目录名（会让 `-o` 与 `--stems-dir` 指到同一处）' % MODEL)
    out = os.path.join(flat, MODEL, os.path.splitext(os.path.basename(audio))[0])
    if os.path.abspath(out) == os.path.abspath(flat):                # pragma: no cover
        raise ValueError('六轨目录与分轨根目录重合：%s' % out)
    return out


def _head(name, W, flat, h6, audio, scripts, py_ml):
    """三档共用的前两步：分轨（`-o` 给**根目录**）→ **全混音**转录（pure 那版，永远是基准）。"""
    ymt = os.path.join(W, 'ymt3')
    return [
        [py_ml, os.path.join(scripts, 'stem_split.py'), audio, '-o', flat, '-m', MODEL],
        [py_ml, os.path.join(scripts, 'transcribe_ymt3.py'), audio, '-o', ymt,
         '--name', name, '--song-name', name, '--stems-dir', h6],
    ], ymt


def _render(name, W, audio, mode, scripts, py_exe, ymt_dir, flat):
    """`fast` 档 = 作曲+渲染（不调参）；`full` 档 = 摊平分轨 + `restore_oneshot` 六阶段。

    ⚠ `full` 档摊平的**源目录是分轨根目录的下一层**（`<flat>/<模型>`）：`_FLAT_STEMS_CODE`
    会把里面的 `*.wav` 逐个硬链接成 `h6_*.wav`，而 `probe_instruments.load_stems` 只认这个
    前缀（PITFALLS / `server.py` 那段注释）。
    """
    if mode != 'full':
        return [[os.path.join(scripts, 'make_song.py'), name, '--no-tune']]
    return [
        [py_exe, '-c', FLAT_STEMS_CODE, os.path.join(flat, MODEL), os.path.join(W, 'stems_h6')],
        [py_exe, os.path.join(scripts, 'restore_oneshot.py'), name,
         '--audio', audio, '--stems-dir', os.path.join(W, 'stems_h6'),
         '--ymt3-dir', ymt_dir, '--render'],
    ]


def _tail(name, W, h6, audio, mode, scripts, py_exe):
    """三档共用的**收尾两步**（顺序有守卫守着：收尾必须在 `extract_notes` 之前）。"""
    return [
        [py_exe, os.path.join(scripts, 'extract_finish.py'), name,
         '--audio', audio, '--stems-dir', h6, '--mode', mode],
        [py_exe, os.path.join(scripts, 'extract_notes.py'), name,
         '--audio', audio, '--mode', mode, '--stems-dir', h6],
    ]


def _asm(py_exe, scripts, name, W, ymt, audio, h6, mids, drums_mid=None, songs=None):
    """混合装配那一步（**一个 `-c` 步骤**：读 bpm → 拼 `--mid`/`--drums-mid` → 跑装配）。

    `mids` = `[(引擎轨名, mid 路径)]`。**缺了不算错**（去鼓版里某轨没有音是正常的，
    那条轨退回纯单来源那版的内容）；只有一条都没产出时才拒绝。
    `bpm 来源` = `songs/<曲名>/song.json`（第一步那条链写下的、**未经去鼓污染**的那份）——
    ⚠ 不能读 `<W>/song.json`：那是**去鼓那次**转录自己写的（bpm 翻倍）。`songs` 给的是
    曲库根目录；不给就退回 `<W>/song.json`（只在纯手工跑的时候才需要）。
    `drums_mid` = 鼓的逐小节网格来源（`transcribe_to_song --drums-mid`）；不给就不拼。
    ⚠ `--audio` 给的是**原曲**、`--stems-dir` 给**六轨目录**（逐音力度从它量）。
    """
    run = [py_exe, os.path.join(scripts, 'transcribe_to_song.py'), name,
           '--chords-log', os.path.join(ymt, name + '_chords.txt'),
           '--no-melody', '--auto', '--audio', audio, '--stems-dir', h6,
           '--bpm', SUB_BPM]
    if drums_mid:
        run += ['--drums-mid', drums_mid]
    bpmsrc = (os.path.join(songs, name, 'song.json') if songs
              else os.path.join(W, 'song.json'))
    argv = [py_exe, '-c', ASM_CODE, py_exe, os.path.dirname(scripts) or '.',
            json.dumps(run), bpmsrc]
    for tr, p in mids:
        argv.append('%s=%s' % (tr, p))
    return argv


def pure(name, W, flat, h6, audio, mode, scripts, py_exe, py_ml, songs=None):
    """**纯单来源**（默认）：转录吃全混音；分轨只用来量逐音力度。"""
    head, ymt = _head(name, W, flat, h6, audio, scripts, py_ml)
    return head + _render(name, W, audio, mode, scripts, py_exe, ymt, flat) \
        + _tail(name, W, h6, audio, mode, scripts, py_exe)


def _pure_track_mids(name, ymt, scripts):
    """`pure` 那次全混音转录切出来的**逐轨** MIDI（`<ymt>/<name>_stems/<name>_<轨>.mid`）。

    ⚠ 装配**必须把它们全都带上**（2026-10-13 实测）：`transcribe_to_song` 写的是
    `songs/<曲名>/song.json`，**只带被替换的那两轨**就会把 `pure` 那版的其余轨
    （Drums/Bass/Piano/...）整片覆盖掉 —— 实测成品从 **2781 音掉到 567 音**、
    而任务照样 rc=0（静默丢内容，最难发现的那一类）。口径与 `transcribe_ymt3._ENGINE_MAP`
    的轨名一致，所以这里直接用 `STEM_TO_ENGINE` 的值。

    ⚠ 合并转录**能出、但切不出独立逐轨**的那几层（实测是 `Melody` / `Glock` / `Arp`）
    不在这里 —— 它们靠后面那步"装配后回灌"补回来（`GAPFILL_CODE`）。
    """
    stems = os.path.join(ymt, name + SPLIT_SUFFIX)
    return {tr: os.path.join(stems, '%s_%s.mid' % (name, tr))
            for tr in sorted(set(STEM_TO_ENGINE.values()))}


def _post_cmds(py_exe, asm_argv, sj):
    """装配那一步 + **装配后回灌**那一步（顺序不能反，缺了第二半就会静默丢内容）。

    ① 先把装配前的 `song.json` 复制成 `<song.json>.pre_asm`（**装配会重写它**，先留底）；
    ② 跑装配（`ASM_CODE`）；
    ③ 跑回灌（`GAPFILL_CODE`）：把"副本里有、装配后没有"的轨逐音补回去。
    """
    bak = sj + PRE_ASM_SUFFIX
    copy = [py_exe, '-c',
            "import shutil,sys\n"
            "s,b=sys.argv[1],sys.argv[2]\n"
            "shutil.copy2(s,b)\n"
            "print('[留底] 装配前的 song.json → %s'%b,flush=True)\n", sj, bak]
    gap = [py_exe, '-c', GAPFILL_CODE, sj, bak]
    return [copy, asm_argv, gap]


def partial(name, W, flat, h6, audio, mode, scripts, py_exe, py_ml, parts=DEFAULT_PARTS,
           songs=None):
    """**部分分轨**：只把 `parts` 里那几轨改成"从去鼓混音转录"，其余仍走全混音。

    链路（比 `pure` 多三步，实测**时间约翻倍**）：
      ① 去鼓混音（`drumless_mix.py mix`，单声道；`--stems` 给**六轨目录**）
      ② 去鼓混音**单独转录** → 逐轨 `nodrums_<轨>.mid`（落在 `<nodr>/nodrums_stems/`）
      ③ 混合装配：**纯单来源那版的 7~8 条逐轨全都带上**，只把 `parts` 里那几轨换成去鼓版
         （bpm 由 `ASM_CODE` 运行时从 `songs/<曲名>/song.json` 钉死）

    ⚠ 装配**必须钉死 `--bpm`**：去鼓输入会让节拍判定翻倍（实测 71.0 → 141.6 / 68 → 136），
    不钉就整曲时间轴错一倍（`notes.md` §5.2 第 3 条）。
    """
    head, ymt = _head(name, W, flat, h6, audio, scripts, py_ml)
    nodr = os.path.join(W, NODRUMS_NAME)
    # ⚠ 逐轨 MIDI 在 `<nodr>/<NODRUMS_NAME>_stems/` 里（`_split_by_engine_track` 的固定拼法），
    #   **不是** `<nodr>/` 那一层 —— 指错了装配就"找不到逐轨 MIDI"（实测三首全长全挂）。
    split = os.path.join(nodr, NODRUMS_NAME + SPLIT_SUFFIX)
    wav = os.path.join(W, 'nodrums.wav')
    # 先把 pure 那版的逐轨全列上，再用去鼓版覆盖 `parts`
    mids = list(_pure_track_mids(name, ymt, scripts).items())
    mids = [(tr, p) for (tr, p) in mids if tr not in parts]
    mids += [(tr, os.path.join(split, '%s_%s.mid' % (NODRUMS_NAME, tr))) for tr in parts]
    body = [
        [py_exe, os.path.join(scripts, 'drumless_mix.py'), 'mix',
         '--stems', h6, '-o', wav, '--drop', 'drums'],
        # ⚠ **不加 `--no-song`**：逐轨 MIDI 只在"写 song.json"那条支路里切（见 `NODRUMS_SUFFIX`
        #   那段说明）。`--song-name` 指到 `<名>_nodrums` ⇒ 它的 song.json 落在**另一首**上，
        #   `pure` 那版（装配的 bpm 基准）不会被覆盖。
        #   ⚠ `transcribe_ymt3` **没有** `--no-compose-file` 那个开关（那是 `transcribe_to_song`
        #   的）—— 写了会 argparse 报错（实测）。
        [py_ml, os.path.join(scripts, 'transcribe_ymt3.py'), wav, '-o', nodr,
         '--name', NODRUMS_NAME, '--song-name', name + NODRUMS_SUFFIX,
         '--stems-dir', h6],
    ]
    sj = (os.path.join(songs, name, 'song.json') if songs
          else os.path.join(W, 'song.json'))
    body += _post_cmds(py_exe,
                       _asm(py_exe, scripts, name, W, ymt, audio, h6, mids, songs=songs), sj)
    return head + body + _render(name, W, audio, mode, scripts, py_exe, ymt, flat) \
        + _tail(name, W, h6, audio, mode, scripts, py_exe)


def all_stems(name, W, flat, h6, audio, mode, scripts, py_exe, py_ml, songs=None):
    """**每轨各自转录**（`all`）：逐条分轨各跑一次 YMT3，再装配。

    ⚠ 转录次数 = `len(ALL_STEMS) + 1`：第 1 次是 `_head` 里那次**全混音**转录
    （`all` 也必须留着它 —— 装配要它的 `_chords.txt`，还要拿它的 `bpm` 当钉死值），
    后面 6 次才是逐条分轨。

    ⚠ **实测更差**（`notes.md` §5.9：独立尺子 无峰 56.3% vs 纯单来源 54.0%），
    且比 `pure` 慢 **3~6 倍**（每条分轨都要过一次模型）。留着是为了可复现地做对照，
    面板上必须写明"实测更差"。
    """
    head, ymt = _head(name, W, flat, h6, audio, scripts, py_ml)
    per = os.path.join(W, PER_STEM_DIR)
    body, mids = [], []
    for s in ALL_STEMS:
        # ⚠ 输入是**六轨目录**里的 wav（`h6`），不是分轨根目录
        body.append([py_ml, os.path.join(scripts, 'transcribe_ymt3.py'),
                     os.path.join(h6, s + '.wav'), '-o', os.path.join(per, s),
                     '--name', s, '--no-song'])
        mids.append((STEM_TO_ENGINE[s], os.path.join(per, s, s + '.mid')))
    sj = (os.path.join(songs, name, 'song.json') if songs
          else os.path.join(W, 'song.json'))
    body += _post_cmds(
        py_exe,
        _asm(py_exe, scripts, name, W, ymt, audio, h6, mids,
             drums_mid=os.path.join(per, 'drums', 'drums.mid'), songs=songs), sj)
    return head + body + _render(name, W, audio, mode, scripts, py_exe, '', flat) \
        + _tail(name, W, h6, audio, mode, scripts, py_exe)


#: 三档的**实测代价表**（面板与文档引用同一份，避免"各写一份判据"）
PATHS = {
    'pure': dict(
        label='① 纯单来源（推荐）',
        note='转录吃整段混音，分轨只用来量逐音力度。BGM23 实测：独立尺子 无峰 54.0%',
        slower='1×'),
    'partial': dict(
        label='② 部分分轨（去鼓混音 + 换指定轨）',
        note='把下面点选的几轨改成"从去鼓混音转录"。⚠ 取轨表**按曲标定**：BGM23 实测只值 '
             'Hook/Pad 两轨（19.5%→28.2% / 12.2%→13.6%），代价 +1447 音；'
             'bpm 由装配钉死（去鼓输入会让节拍判定翻倍）',
        slower='≈2×'),
    'all': dict(
        # ⚠ 效果明确不好的那档 = **统一叫「其它」**（2026-10-13 用户定），并在按钮上写明"效果不好"
        label='③ 其它（效果不好 · 仅对照用）',
        note='⚠ **效果不好，不建议用**：实测**每一项都比 ① 差** —— 独立尺子 无峰 56.3% vs 54.0%'
             '（越高越差）· 每轨各自转录 = 6 次分布外输入，模型对单条分轨最容易失控；'
             '慢 3~6 倍。留在这里只为了可复现地做对照',
        slower='3~6×',
        worse=True),
}


def plan(name, W, flat, h6, audio, mode, scripts, py_exe, py_ml, path='pure', parts=None,
         songs=None):
    """按 `path` 选一档 → 命令序列（未知档 / 空取轨表**抛错**，不许静默退回 `pure`）。

    `parts=None` → 用 `DEFAULT_PARTS`；`parts=[]`（**显式空**）→ **抛错** ——
    空清单会让"指定轨替换"变成一句空话（跑出来跟 `pure` 一样，而面板上说选的是部分分轨）。

    ⚠ 面板侧**必须按关键字**调用（`EP.plan(name, path=…, parts=…, flat=…, h6=…)`）：
    位置参数下"传的到底是用户选的那档、还是写死的 `'pure'`"在源码文本上无从区分，
    而那种退化（dropdown 变摆设）在产物上几乎看不出来 —— 守卫 `t_extract_paths_contracts`
    逐条核对这些关键字。
    """
    if path == 'pure':
        return pure(name, W, flat, h6, audio, mode, scripts, py_exe, py_ml, songs=songs)
    if path == 'partial':
        if parts is None:
            parts = DEFAULT_PARTS
        parts = tuple(parts)
        if not parts:
            raise ValueError('partial 档的 parts 是空的 —— 那跟 pure 没区别，'
                             '请给至少一条轨（如 Hook,Pad）')
        return partial(name, W, flat, h6, audio, mode, scripts, py_exe, py_ml, parts=parts,
                       songs=songs)
    if path == 'all':
        return all_stems(name, W, flat, h6, audio, mode, scripts, py_exe, py_ml, songs=songs)
    raise ValueError('未知的提取路径 %r（只能是 %s）' % (path, '/'.join(sorted(PATHS))))
