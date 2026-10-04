# -*- coding: utf-8 -*-
"""转录结果 → `song.json`：**还原/扒带的正道入口**。

## 何时用

扒完一首参考曲（Demucs 分轨 + YMT3 / Basic Pitch 转录）之后，把结果变成一份
**能被引擎编配**的 `song.json` —— 之后交给 `compose.py` / `make_song.py` 就行。

**为什么不手工拼 MIDI**（PITFALLS 185，实测最大的一条教训）：
引擎的编配、音色分配、按 `arr.density` 抽样服从段落结构、混音、调参全是现成的；
手工拼轨 = 自己重造它一半功能还做不全。同一批转录音符，走本工具后
**5000–10000Hz 的缺口从 −12.6dB 收到 −3.8dB**。

## 用法

```bash
python scripts/transcribe_to_song.py <曲名> \
    --bpm 75 --key-mode minor \
    --chords-log <analyze_chords.py --bpm 75 的输出文件> \
    --boundaries 179.2,209.5,221.0,323.5,331.9 \
    --sec-names A,B,C,D,Ending \
    --mid Piano=.../piano.mid --mid Hook=.../guitar.mid \
    --mid Bass=.../bass.mid --mid Strings=.../other.mid --mid Drums=.../drums.mid
```

**逐音力度**（`--stems-dir` / `--stem-audio`，2026-10-02 接进链）：

```bash
python scripts/transcribe_to_song.py <曲名> --bpm 75 ... \
    --stems-dir D:/work/stems/htdemucs_6s/<曲名>      # demucs 六轨目录，自动配对
```

⚠ **为什么必须做**：YourMT3 的输出**每个音的力度恒为 100**（实测六条分轨 + 整混音，
`other.mid` 8960 个音**全是 100**）——不量就是"打字机"。引擎的 `_vel_of` 认
`notes_extra` 的第 5 位，所以力度必须**在 `read_notes` 之前**量好（顺序反了补进来的音还是 100）。
实测 BGM35：逐轨力度种类 **Drums 1→93 · Hook 2→49 · Strings 2→49 · Piano 2→22**。

量失败**不会静默**：`patterns.velocity_source` 会记下 `FAILED`，并在终端打出来。

生成 `songs/<曲名>/song.json`（含 `chords` / `sections` / `melody` / `notes_extra`）。
随后：`cp` 一份 `compose.py`（照 `songs/01_daily_morning/compose.py` 那 14 行）→
`make_song.py <曲名>`。

## 关键约定（都是踩出来的，别改）

1. **小节号两套口径**：`melody` 用**段内**小节号，`notes_extra` 用**全局**小节号。
   写反了前者会静默不发声（引擎现在会拦，见 PITFALLS 182）。
2. **每段的 `chords` 个数必须等于 `bars`** —— 否则 `chords[i+1]` 越界，
   自检里 8 条一起 IndexError。
3. **`sections[].melody` 必须给出**，且一个段一个键；让短段复用长段的旋律键会被
   "小节号超段长"拦下。
4. **轨名只能用引擎认识的**（`Piano/Bass/Hook/Strings/Pad/Arp/Glock/Drums/Melody`）；
   吉他对应 **`Hook`**。鼓的音高是 GM 鼓键，直接进 `Drums`。
5. **提取默认就是"全量"**（写 `patterns.notes_extra_full = true`，**不抽样**）。
   ⚠ 2026-09-26 **默认翻转**：原来默认抽样（为"服从 `arr.density` 的段落结构"设计），
   实测那样会把**用户点名要"像"的那几段砍掉最多** —— `dear_good_friends` 抽样后
   Piano 984→517 / Hook 482→265 / Bass 47→38（**全曲转录只留 54%**），
   而用户投诉的 0–27s / 63–71s / 126s+ 正是 `density=1`（每轨每小节上限 **4 音**）
   的段落，保留率只有 **13%~35%**；未投诉的 `density=3` 段保留 50%~100%。
   翻成默认全量后用户原话："**V1_全量转录.mid 非常好**"。
   想要旧的"服从段落结构"行为 → 显式加 **`--sample`**（它会写 `notes_extra_full: false`；
   ⚠ **不能靠"不写"表达抽样**，引擎默认已是全量，不写等于全量）。
   库内 4 首扒带曲里 3 首（`siren_end`/`siren_end2`/`c15_chain_repro`）本来就是全量，
   只有 `dear_good_friends` 漏了 —— 默认翻转是**让工具追上既有实践**。
   守卫 `t_restore_notes_full` 盯着"扒带曲不许静默走默认"。
6. 段边界按**秒**给（`analyze_sections.py` 的输出），本工具按 `--bpm` 换算成小节。
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import json_io                                                # noqa: E402
import midi_file                                              # noqa: E402
import song_engine as se                                      # noqa: E402

# 引擎认识的轨名（吉他 → Hook）
TRACKS = ('Piano', 'Bass', 'Hook', 'Strings', 'Pad', 'Arp', 'Glock', 'Drums', 'Melody')
# 一个段最多放多少音进 melody（防止把整轨钢琴都塞进旋律）
MEL_MAX_PER_BAR = 2

# demucs 六轨 → 引擎轨名 的默认配对（`--stems-dir` 用）。
# ⚠ 这是**默认猜测，不是判据**。实测（BGM35，2026-10-02）：
#   · `other.wav` 是个"大杂烩"分轨 —— `other.mid` 里 9 条乐器轨**全部**与它对得上
#     （中位 −17 ~ −19 dB），所以 `other` 一条分轨可以喂多条引擎轨；
#   · `bass.wav` 是**单乐器** —— `bass.mid` 里 7 条轨只有 `Bass` 对得上（−12.0 dB），
#     另外 956 个泄漏音在 −63 ~ −70 dB。
#   配错**不会静默**：`measure_velocity` 的逐轨护栏会把对不上的轨跳过并打印出来。
STEM_FILE = {
    'Piano': 'piano.wav', 'Bass': 'bass.wav', 'Drums': 'drums.wav',
    'Hook': 'guitar.wav',                    # 吉他 → Hook（见 TRACKS 的约定 4）
    'Strings': 'other.wav', 'Pad': 'other.wav',
    'Arp': 'other.wav', 'Glock': 'other.wav',
}
# 量力度的固定参数（与 `measure_velocity` 的默认口径一致；它默认 `mode='rank'` 分位映射）
VEL_P50, VEL_K, VEL_MAX_DB = 51.0, 9.0, 18.0


def measure_velocity_for(track, mid_path, wav_path, tmpdir, transcript_bpm=None):
    """用分轨音频给这条转录量逐音力度 → `(可用路径 | None, 说明)`。

    **失败不抛**：调用方要能回退到转录自带力度**并大声报出来** ——
    静默回退正是"打字机"听感里最难查的一种（转录的力度恒 100，从产物上看不出来）。
    """
    import measure_velocity as mv
    out = os.path.join(tmpdir, '%s_vel.mid' % track)
    try:
        n = mv.do_one(wav_path, mid_path, out, VEL_P50, VEL_K, VEL_MAX_DB,
                      report=True, guard=True, transcript_bpm=transcript_bpm)
    except SystemExit as e:                       # 护栏拒写盘（一条轨都没对上）
        return None, str(e)
    except Exception as e:                                            # noqa: BLE001
        return None, '%s: %s' % (type(e).__name__, e)
    return out, '%d 音' % n


def read_notes(path, dst_bpm=None, on_mismatch=None, leak_warn=True):
    """→ [(起始秒, 结束秒, 音高, 力度)]。

    ⚠ **它会把文件里所有轨拍平进同一条引擎轨** —— 而 YMT3 的"逐分轨"输出
      **不是单乐器**（`HANDOFF-BGM35-R2.md` §11.3：`bass.mid` 里 7 条轨，只有 `Bass`
      与 `bass.wav` 对得上；另外 956 个音落在 −63~−70dB）⇒ 泄漏音**会真的进曲子**。
      2026-10-04 实测复现：`Hook=<Hook.mid>` 把泄漏的鼓点铺进 Hook 层，成品在 **12.00s
      突然出现底鼓+军鼓+闭镲**（用户："9 秒突然出现的鼓很奇怪"）。
      ⇒ `leak_warn=True`（默认）时**打印多轨合并且音域重叠的证据**，但不改内容
      （过滤是内容决策，要用户点头 —— 见那份 HANDOFF 的"本轮没动它，要处理请先问用户"）。

    ⚠ **不用 `mido`**：主 venv 里没有它（只有 `.venv-ml` 有），顶层 import 会让
      `selftest` 的 `import_all` 直接 FAIL（PITFALLS 173 同族）。
      改用工具链自带的 `midi_file`（纯标准库）。
    ⚠ `midi_file` 的 `notes` 起始时间是**拍**（四分音符），乘 `60/bpm` 才是秒 ——
      曾当秒用，结果全曲音挤进第 0 格（PITFALLS 184①）。
    ⚠ **力度在 index 3**（`midi_file` 模型 = `[start_beat, dur_beat, pitch, vel]`）。
      它必须一路带到 `song.json` 的 `notes_extra` 第 5 位 —— 丢了力度就是"打字机"
      （实测对照：带力度的版本 17773/17773 全带，不带的 0/23033）。

    ## `dst_bpm`：**拍值口径**（2026-10-02 加，BGM35 实测的最大一条）

    转录产物声明的 bpm **不一定等于曲子 bpm**（YourMT3 的输出恒为 **120 BPM**）。
    本函数按**转录自己声明的 bpm** 把 beat 换成秒，调用方随后按**曲子 bpm** 把秒换回 beat
    —— 只要"秒"是真秒，这条链就是对的，所以这里**不改数据**，只做一件事：
    `dst_bpm` 给了且与转录声明的 bpm 差 >1% 时，通过 `on_mismatch` **报出来**。

    为什么必须报：BGM35 上 `bass_ensemble.py` 走的是另一条路（直接 `t/spb` 写 beat），
    整条 Bass 轨被静默缩放 **0.801×**（末音 264.1 秒 / 全曲 331.9，266 秒之后没有低音），
    而帧级一致率把它读成"10.4%，先修识别"—— **归因完全错方向**。
    """
    d = midi_file.import_midi(str(path))
    src_bpm = float(d.get('bpm') or 120.0)
    if dst_bpm and on_mismatch:
        on_mismatch(src_bpm, float(dst_bpm), os.path.basename(str(path)))
    spb = 60.0 / max(1e-9, src_bpm)
    out = []
    _trs = [tr for tr in (d.get('tracks') or []) if (tr.get('notes') or [])]
    if leak_warn and len(_trs) > 1:
        # 泄漏证据：多轨被合并 + 各轨音域互相重叠（单乐器分轨不该这样）
        _spans = []
        for tr in _trs:
            ps = [int(x[2]) for x in tr['notes']]
            _spans.append((tr.get('name') or '?', len(ps), min(ps), max(ps)))
        _bad = []
        for i, (n1, c1, lo1, hi1) in enumerate(_spans):
            for j, (n2, _c2, lo2, hi2) in enumerate(_spans):
                if i >= j:
                    continue
                ov = min(hi1, hi2) - max(lo1, lo2)
                if ov > 6:                      # 重叠超过半八度 = 可疑
                    _bad.append('%s(%d~%d) ∩ %s(%d~%d) = %d 半音' % (n1, lo1, hi1, n2, lo2, hi2, ov))
        if _bad:
            print('  ! [泄漏] %s 里有 %d 条有声轨会被**拍平进同一条引擎轨**：%s'
                  % (os.path.basename(str(path)), len(_trs),
                     '、'.join('%s %d 音' % (n, c) for n, c, _l, _h in _spans)))
            print('      音域重叠证据：%s' % '；'.join(_bad[:3]))
            print('      ⇒ 混进来的别的乐器**会真的进曲子**（实测：Hook 被塞进鼓点 → '
                  '12.00s 突然出现底鼓）。要过滤得显式选轨/按音域筛，'
                  '见 HANDOFF-BGM35-R2 §11.3')
    for tr in d.get('tracks') or []:
        for it in tr.get('notes') or []:
            if len(it) < 4:
                continue
            st = float(it[0]) * spb
            vel = int(it[3]) if it[3] is not None else 84
            out.append((st, st + float(it[1]) * spb, int(it[2]),
                        max(1, min(127, vel))))
    out.sort()
    return out


# 抽旋律：某小节音符数 ≥ 这个值，才认为"这条轨在这一小节有主奏内容"。
# ⚠ 原值 **4** 太严（PITFALLS 239 实测）：稀疏主奏轨整段抽不出旋律 ——
#   `siren_end`（Piano 612 音 / 143 小节）只抽出 146 音 → melody 密度 **1.02 音/小节**，
#   被 `melody_health` 的下限 1.2 拦下，交付前必须先修数据。
MEL_MIN_PER_BAR = 2
# 取"高音区"的起点（0.70 = 顶部 30%）。
# ⚠ **不要再往下调去"凑密度"**（用户 2026-09-25 口径："如果是真的没有音要保留，
#   重要的是符合原曲"）：把窗口放宽到中音区，等于把**伴奏内声部**当旋律 ——
#   那是为了让 `melody_health` 的密度好看而改内容，方向错了。
#   **原曲稀疏就保留稀疏**；密度不达标走 `patterns.melody_exempt`（带理由 + 数字），
#   而不是补音、也不是改阈值。
MEL_TOP_FRAC = 0.70


def extract_melody(mel_src, secs, bar_sec, max_per_bar=None):
    """从 `--melody-from` 那条轨按**段内小节**抽旋律线。

    → `{段名: [[小节, 拍, 时值, 音高], ...]}`（小节号是**段内**口径，见模块 docstring 契约 1）。

    规则（`selftest.t_transcribe_melody_density` 盯着）：
      · 该小节音符数 ≥ `MEL_MIN_PER_BAR` 才抽；
      · 在**音高最高的 (1 − MEL_TOP_FRAC) 部分**里按时间取最多 `max_per_bar` 个；
      · 该窗口为空时回退到该小节最高音 —— 保证"有内容的小节至少出 1 个音"。
    """
    if max_per_bar is None:
        max_per_bar = MEL_MAX_PER_BAR
    mel, bar0 = {}, 0
    for sec in secs:
        rows_m = []
        for b in range(bar0, bar0 + sec['bars']):
            ns = sorted([(s, p) for (s, _e, p, _v) in mel_src if int(s / bar_sec) == b],
                        key=lambda x: x[1])
            if len(ns) < MEL_MIN_PER_BAR:
                continue
            top = ns[int(len(ns) * MEL_TOP_FRAC):][:max_per_bar] or ns[-1:]
            for s, p in top:
                rows_m.append((b - bar0, (s - b * bar_sec) / (bar_sec / 4), p))
        out = []
        for j, (bb, beat, p) in enumerate(rows_m):
            if j + 1 < len(rows_m) and rows_m[j + 1][0] == bb:
                dur = max(0.5, round(rows_m[j + 1][1] - beat, 2))
            else:
                dur = max(0.5, round(4.0 - beat, 2))
            out.append([bb, round(beat, 2), dur, p])
        mel[sec['name']] = out
        bar0 += sec['bars']
    return mel


NOTE_PC = {'C': 0, 'C#': 1, 'D': 2, 'D#': 3, 'E': 4, 'F': 5, 'F#': 6,
           'G': 7, 'G#': 8, 'A': 9, 'A#': 10, 'B': 11}
# 和弦符号 → 音级（相对根音的半音数）
CHORD_STEPS = {'m7': [0, 3, 7, 10], '7': [0, 4, 7, 10], 'sus4': [0, 5, 7],
               'm': [0, 3, 7], '': [0, 4, 7]}


def chord_tones(name):
    """和弦符号 → `[低音 MIDI, 音级 MIDI 数组]`；认不出来返回 None。

    ⚠ 两条口径（都有实测依据）：
      ① **音级必须落在 C 的整数倍上**（60 = C4）；写成 64 会整体偏 4；
      ② **低音必须跟着根音走**（PITFALLS 239）：这里曾写死 `34`（A#1）——
         于是每个和弦的低音都是 A#1，`check_song` 的 `chord_names_match_notes`
         会对**除 A# 外的每个和弦**报"低音与根音不符"（实测 `siren_end`：20 个
         和弦种全中）。口径与 `check_song._build_voicing` 一致：
         把**根音 pc** 落进 28–45 音区（超出则回退一个八度）。
    """
    m = re.match(r'^([A-G]#?)(.*)$', name)
    if not m:
        return None
    pc = NOTE_PC[m.group(1)]
    steps = CHORD_STEPS.get(m.group(2), [0, 4, 7])
    bass = 28 + ((pc - 28) % 12)
    if bass > 45:
        bass -= 12
    return bass, [60 + pc + s for s in steps]


def gen_layer_on(src_count):
    """**引擎生成层**（`arp / pad / glock / shimmer`）该不该开 —— **只看本段有没有该来源的转录音**。

    ⚠ 别退回"段名规则"（2026-09-25 实测，用户听感"前面有点乱"的根因）：
    旧写法是 `'arp': nm not in ('C', 'Ending')`，而本工具 `--auto` 生成的段名是
    **`S01…S24` + `Ending`** —— 没有任何一段等于 `'C'`，于是这个条件**恒为 True**，
    引擎**凭空生成**的层在**每一段**都开着。实测后果：引子第 1–4 小节转录只有 **0~1 个音**，
    引擎却在那里生成了 **Arp 13 + Pad 4** 个音（开头的主角成了琶音和垫子）。

    正解：**没有来源音的生成层一律不开** —— 还原曲的编制由转录决定，引擎不该补原曲
    根本没有的声部；而且这是**逐段**判断（不是全曲一刀切）。
    抽成模块级函数是为了让 `selftest.t_transcribe_arr_by_source` 能**行为测试**它
    （读源码里的字面串太脆：注释里提一句旧写法就会误判）。
    """
    return int(src_count) > 0


def range_fit(notes, tr):
    """把**越界音**移到最近的合法八度；**合法音一个不动**（还原曲要"符合原曲"）。

    ⚠ 为什么必须在**生成端**做（PITFALLS 253 实测）：引擎的 `TR_RANGE` 保护是**整轨**移位 ——
    本曲 Strings 只有 **11/278（4%）** 越界、Melody **9/32**，引擎却把**整条轨**
    移了 **+12 / +24** 半音，等于把 96% 本来正确的音一起改掉。还原任务里那是硬伤。
    逐音夹取后引擎不再触发整轨移位；怎么移都装不下的音（跨度 > 12）原样放行，交给引擎。
    `notes` = `[(起, 止, 音高, 力度)]`。
    """
    rng = se.TR_RANGE.get(tr)
    if not rng or not notes:
        return notes
    a, b = rng
    out = []
    for st, en, p, vel in notes:
        q = int(p)
        while q < a:
            q += 12
        while q > b:
            q -= 12
        out.append((st, en, q if a <= q <= b else int(p), vel))
    return out


def parse_chords(path):
    """解析 `analyze_chords.py` 的输出 → [(小节号, 和弦名, 起音数)]。
    行格式：`  12 | Fsus4     | Vsus4    |    61  |   -15.3`"""
    rows = []
    pat = re.compile(r'^\s*(\d+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\d+)')
    with open(path, encoding='utf-8') as f:
        for line in f:
            m = pat.match(line)
            if m:
                rows.append((int(m.group(1)), m.group(2), int(m.group(4))))
    return rows


def main():
    import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
    ap = argparse.ArgumentParser(description='转录结果 → song.json')
    ap.add_argument('name', help='曲目名（会成为 songs/<name>/）')
    ap.add_argument('--bpm', type=float, required=True)
    ap.add_argument('--key-mode', default='minor', choices=('major', 'minor'))
    ap.add_argument('--style', default='ballad',
                    help='引擎风格预设（见 new_song.py --list-styles）')
    ap.add_argument('--chords-log', required=True,
                    help='analyze_chords.py 的输出文件（本工具从中解析小节级和弦）')
    ap.add_argument('--boundaries', default='',
                    help='段边界（秒，逗号分隔，含 0 与总时长）；--auto 时可省略')
    ap.add_argument('--sec-names', default='',
                    help='段名（逗号分隔），个数 = 边界数 − 1；--auto 时可省略')
    ap.add_argument('--mid', action='append', default=[],
                    help='轨=文件，可多次；轨名见 --help 的约定 4')
    ap.add_argument('--melody-from', default='Piano', help='从哪条轨抽旋律')
    ap.add_argument('--no-melody', action='store_true',
                    help='**还原模式**：melody 层清空（它是 `--melody-from` 那条轨高音区的'
                         '**副本**，与那条轨重复发声；音留在原轨不丢）；`--full` 时**默认生效**')
    ap.add_argument('--keep-melody', action='store_true',
                    help='`--full` 下仍保留 melody 副本（默认不保留 —— 实测 99%% 的音与主奏轨重复）')
    ap.add_argument('--quota', action='append', default=[],
                    help='轨=目标音数（按时间**均匀抽样**到该数），如 Strings=560；可多次')
    ap.add_argument('--auto', action='store_true',
                    help='**一键还原**：自动跑 analyze_structure 定段落（段长跟随音乐、'
                         '不固定 8 小节）+ extract_drum_grid 定逐小节鼓型；'
                         '此时 --boundaries/--sec-names 可省略')
    ap.add_argument('--audio', default=None, help='--auto 用：参考音频（分析段落）')
    ap.add_argument('--target-segments', type=int, default=25,
                    help='--auto 用：目标段数（默认 25，取 novelty 最强的边界）')
    ap.add_argument('--drums-mid', default=None,
                    help='--auto 用：鼓分轨 MIDI（提取 drum_grid.per_bar）')
    ap.add_argument('--stem-audio', action='append', default=[],
                    help='轨=分轨.wav（可多次）—— 从这条分轨**量逐音力度**再写 notes_extra。'
                         '⚠ 不给就是转录自带的力度，而 YMT3 的转录**恒为 100** ⇒ "打字机"')
    ap.add_argument('--stems-dir', default=None,
                    help='demucs 六轨目录：按 轨名→文件名 自动配对'
                         '（Piano→piano.wav · Bass→bass.wav · Drums→drums.wav · '
                         'Hook→guitar.wav · Strings/Pad/Arp/Glock→other.wav）；'
                         '与 --stem-audio 同时给时后者优先')
    ap.add_argument('--no-measure-velocity', action='store_true',
                    help='即使给了 --stems-dir/--stem-audio 也**不量力度**'
                         '（退回转录自带力度，用于 A/B 对照）')
    ap.add_argument('--sample', action='store_true',
                    help='回到旧的"服从段落结构"抽样（不写 notes_extra_full）；'
                         '默认**全量**，逐音照写')
    ap.add_argument('--full', action='store_true',
                    help='（已废弃，默认即全量；保留是为了旧命令行不报错）')
    ap.add_argument('--out', default=None, help='输出 song.json 路径（默认 songs/<name>/）')
    ap.add_argument('--force', action='store_true',
                    help='目标 song.json 已存在时**直接覆盖**（默认会先备份成 '
                         '<song.json>.bak_before_transcribe 并打印提示）')
    a = ap.parse_args()
    # 面板守卫（硬形式）：没在跑就先拉起来 —— 见 scripts/studio_guard.py 顶部那段。
    try:
        import studio_guard
        studio_guard.ensure_panel()
    except Exception as _e:                                        # noqa: BLE001
        print('  （面板守卫跳过：%s）' % str(_e)[:80])

    bar_sec = 4 * 60.0 / a.bpm
    bounds = [float(x) for x in a.boundaries.split(',') if x.strip()]
    names = [x.strip() for x in a.sec_names.split(',') if x.strip()]

    # —— **一键还原**：段落与鼓型都自动定（见 PITFALLS 182 的教训 —— 写了却不生效；
    #    以及"固定 8 小节"会让曲式同质化，用户口径是「要看情况」）——
    drum_grid = None
    if a.auto:
        import subprocess
        # ⚠ 别在这里再 `import tempfile` —— 那会把模块级的 `tempfile` **遮蔽成局部变量**，
        #   于是同一函数里更早/更晚用到它的地方报 `UnboundLocalError`（本轮实测踩中，
        #   被端到端测试当场抓住）。`tempfile` 已提到文件顶部。
        if not a.audio:
            raise SystemExit('--auto 需要 --audio <参考音频>（用来定段落）')
        tmp = tempfile.mkdtemp(prefix='tts_auto_')
        sj = os.path.join(tmp, 'struct.json')
        print('  [auto] 分析段落结构（目标 %d 段，段长跟随音乐）…' % a.target_segments)
        r = subprocess.run([sys.executable, os.path.join(HERE, 'analyze_structure.py'),
                            a.audio, '--bpm', str(a.bpm),
                            '--target-segments', str(a.target_segments),
                            '--min-bars', '2', '--json', sj],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace')
        if r.returncode != 0:
            raise SystemExit('[auto] analyze_structure 失败：%s' % (r.stderr or '')[-300:])
        st = json.load(open(sj, encoding='utf-8'))
        bounds = list(st['boundaries_sec'])
        names = ['S%02d' % (i + 1) for i in range(len(bounds) - 1)]
        names[-1] = 'Ending'          # 收尾段：role_of_section 只认 Ending/End 为 'E'
        print('  [auto] 段: %d 段 · 段长(小节) %s' % (len(names), st['bars']))
        if a.drums_mid:
            print('  [auto] 提取逐小节鼓型…')
            gj = os.path.join(tmp, 'grid.json')
            r2 = subprocess.run([sys.executable, os.path.join(HERE, 'extract_drum_grid.py'),
                                 a.drums_mid, '--bpm', str(a.bpm),
                                 '--song-bars', str(max(1, st['boundaries_bar'][-1])),
                                 '--out', gj],
                                capture_output=True, text=True, encoding='utf-8',
                                errors='replace')
            if r2.returncode == 0:
                drum_grid = json.load(open(gj, encoding='utf-8'))['per_bar']
                print('  [auto] 鼓型: %d 小节 · %d 点'
                      % (len(drum_grid), sum(len(v) for r in drum_grid for v in r.values())))
            else:
                print('  [auto] ! 鼓型提取失败：%s' % (r2.stderr or '')[-200:])

    if len(names) != len(bounds) - 1:
        raise SystemExit('段名 %d 个 ≠ 边界 %d 个 − 1' % (len(names), len(bounds)))

    # —— 和弦表 ——
    rows = parse_chords(a.chords_log)
    if len(rows) < 8:
        raise SystemExit('从 %s 只解析出 %d 行小节和弦 —— 检查是不是 analyze_chords 的输出'
                         % (a.chords_log, len(rows)))
    chord_names = [r[1] for r in rows]
    onsets = {r[0]: r[2] for r in rows}

    # 和弦排列交给模块级 `chord_tones`（可单测 —— 守卫 `t_chord_bass_matches_root`）
    chords = {}
    for cn in chord_names:
        if cn not in chords:
            t = chord_tones(cn)
            if t:
                chords[cn] = [t[0], t[1]]

    # —— 来源盘点（**必须排在段落之前**）——
    # `arr` 的"引擎生成层"开关要按**本段是否真有该来源的转录音**逐段决定，见下面 `arr` 那段。
    _src_bars = {}
    for spec in a.mid:
        if '=' not in spec:
            continue                      # 格式错留给下面正式读取时报，这里只做盘点
        _tr0, _p0 = spec.split('=', 1)
        if not os.path.exists(_p0):
            continue
        _src_bars.setdefault(_tr0, []).extend(int(st / bar_sec) for (st, _e, _pp, _vv) in read_notes(_p0))

    def _src_in(tr, lo, hi):
        """本段内该来源轨的音符数（0 = 原曲这一段没有这个声部）。"""
        return sum(1 for b in _src_bars.get(tr, ()) if lo <= b < hi)

    # —— 段落 ——
    starts = [int(round(b / bar_sec)) for b in bounds]
    secs, mel = [], {}
    for i, nm in enumerate(names):
        lo, hi = starts[i], starts[i + 1]
        nb = max(1, hi - lo)
        seg = chord_names[lo:hi]
        while len(seg) < nb:                        # 契约 2：和弦数 == 小节数
            seg.append(seg[-1] if seg else 'C')
        seg = seg[:nb]
        dens = sorted(0 if onsets.get(lo + k, 0) <= 6 else
                      1 if onsets.get(lo + k, 0) <= 20 else
                      2 if onsets.get(lo + k, 0) <= 45 else 3
                      for k in range(nb))
        mid_d = dens[len(dens) // 2]
        secs.append({
            'name': nm, 'bars': nb, 'chords': seg,
            'melody': nm,                           # 契约 3：一段一键
            # ⚠ **"引擎生成层"按来源开关**（2026-09-25 修；用户听感"前面有点乱"的根因）：
            #   原来是**段名规则** —— `arp/glock/shimmer = nm not in ('C','Ending')`，
            #   而 `--auto` 生成的段名是 `S01…S24` + `Ending` → **没有任何一段等于 'C'**
            #   → 这些**引擎凭空生成**的层在**每一段**都开着。
            #   实测后果（siren_end2 · 引子）：第 1–4 小节转录只有 **0~1 个音**，
            #   引擎却在那里生成了 **Arp 13 + Pad 4** 个音 —— 开头的主角是琶音与垫子，
            #   原曲那段本来几乎是空的。
            #   正解：**没有来源音的生成层一律不开**（还原曲的编制由转录决定，
            #   引擎不该补原曲根本没有的声部），而且**逐段**判断，不是全曲一刀切。
            #   ⚠ `bass/piano/strings` 必须**恒开**：正式读取后 `notes_extra` 会
            #     `ev[_tr] = …` **整轨覆盖**它们，但 `if _tr not in ev: continue`
            #     意味着**关掉就等于把转录的音整轨丢掉**（不是"不生成"）。
            'arr': {'bass': True, 'piano': True, 'strings': True,
                    'pad': gen_layer_on(_src_in('Pad', lo, hi)),
                    'uku': False,
                    'arp': gen_layer_on(_src_in('Arp', lo, hi)),
                    'glock': gen_layer_on(_src_in('Glock', lo, hi)),
                    'ep': False,
                    'shimmer': gen_layer_on(_src_in('Arp', lo, hi)),
                    'perc': 0 if nm in ('C', 'Ending') else (1 if nm == 'A' else 2),
                    'density': 0 if nm == 'C' else (1 if nm == 'Ending' else mid_d)},
            'mode': a.key_mode,
        })
        if nm == 'Ending':
            secs[-1]['arr']['ending_fade'] = 3      # 实测收尾 3 小节力度趋 0
        mel[nm] = []

    # —— 逐轨音符 ——
    quotas = {}
    for spec in a.quota:
        if '=' not in spec:
            raise SystemExit('--quota 要写成 轨=音数，收到 %r' % spec)
        k, v = spec.split('=', 1)
        quotas[k] = int(v)

    ne, mel_src = {}, None
    # —— 分轨音频配对（量力度用）——
    # ⚠ **量力度必须在 `read_notes` 之前**：力度是跟着转录一起读进来的
    #   （`midi_file` 的 `[start_beat, dur_beat, pitch, vel]`，力度在 index 3），
    #   顺序反了补进来的音还是恒 100 —— 这就是 BGM35 那轮"先量力度、再补音"的由来。
    _stem_map = {}
    if a.stems_dir and not a.no_measure_velocity:
        for _tr, _f in STEM_FILE.items():
            _p = os.path.join(a.stems_dir, _f)
            if os.path.exists(_p):
                _stem_map[_tr] = _p
        print('  [力度] --stems-dir %s → 配到 %d 条轨：%s'
              % (a.stems_dir, len(_stem_map),
                 '、'.join('%s←%s' % (k, os.path.basename(v))
                           for k, v in sorted(_stem_map.items()))))
    for _spec in a.stem_audio:
        if '=' not in _spec:
            raise SystemExit('--stem-audio 要写成 轨=分轨.wav，收到 %r' % _spec)
        _tr, _p = _spec.split('=', 1)
        if _tr not in TRACKS:
            raise SystemExit('--stem-audio 的轨名 %r 不在 %s 里' % (_tr, TRACKS))
        if not os.path.exists(_p):
            raise SystemExit('--stem-audio 找不到 %s' % _p)
        _stem_map[_tr] = _p
    if a.no_measure_velocity and (a.stems_dir or a.stem_audio):
        print('  [力度] --no-measure-velocity：**不量力度**，notes_extra 用转录自带值'
              '（YMT3 恒 100 ⇒ 打字机；这是 A/B 对照模式）')
    _vel_tmp = tempfile.mkdtemp(prefix='tts_vel_') if _stem_map else None
    _vel_rec = {}
    # 拍值口径的差异要**报一次**（每个来源文件一次），别让"转录 120BPM / 曲子 150BPM"
    # 这种组合静默通过 —— 它是 BGM35 那条整轨缩放的温床（见 `read_notes` docstring）。
    def _bpm_note(src_bpm, dst_bpm, fname):
        import beat_units
        beat_units.assert_same_bpm(src_bpm, dst_bpm, '转录 %s' % fname, tol=0.01)
    for spec in a.mid:
        if '=' not in spec:
            raise SystemExit('--mid 要写成 轨=文件，收到 %r' % spec)
        tr, path = spec.split('=', 1)
        if tr not in TRACKS:
            raise SystemExit('轨名 %r 不在引擎认识的 %s 里（吉他请用 Hook）' % (tr, TRACKS))
        if not os.path.exists(path):
            raise SystemExit('找不到 %s' % path)
        # —— 逐音力度：从分轨音频量（转录自己的力度是恒 100）——
        _wav = _stem_map.get(tr)
        if _wav:
            print('  [力度] %s ← %s' % (tr, os.path.basename(_wav)))
            _vp, _why = measure_velocity_for(tr, path, _wav, _vel_tmp)
            if _vp:
                path, _vel_rec[tr] = _vp, os.path.basename(_wav)
            else:
                print('  ! [力度] %s **量不了**（%s）—— 该轨退回转录自带力度'
                      '（YMT3 恒 100，听感会变"打字机"）' % (tr, _why.strip()[:200]))
                _vel_rec[tr] = 'FAILED'
        notes = read_notes(path, dst_bpm=a.bpm, on_mismatch=_bpm_note)
        # 越界音**逐音**夹到最近的合法八度（否则引擎会**整轨**移八度，见 `range_fit`）
        notes = range_fit(notes, tr)
        # ⚠ **带第 5 位力度**（`[小节, 拍, 时值, 音高, 力度]`）：引擎的 `_vel_of` 就认它。
        #   丢了力度 → 引擎套默认值 → "打字机"（实测对照：带 17773/17773，不带 0/23033）。
        ne[tr] = [[int(st / bar_sec),
                   round((st - int(st / bar_sec) * bar_sec) / (bar_sec / 4), 2),
                   max(0.25, round((en - st) / (bar_sec / 4), 2)), p, int(vel)]
                  for (st, en, p, vel) in notes]
        if tr == a.melody_from:
            mel_src = notes
        print('  %-8s %5d 音 · 覆盖 %d 小节 · 力度 %d~%d'
              % (tr, len(notes), len({int(s / bar_sec) for s, _e, _p, _v in notes}),
                 min(v for *_x, v in notes), max(v for *_x, v in notes)))

    # —— **配额抽样**（`--quota 轨=音数`）——
    # ⚠ 为什么必须抽：转录**天然过采样**。实测最痛的一次：把 `other` 轨的 9265 个音
    #   整片铺成 Strings，而最终交付版的 Strings 只有 **563** —— 差 15 倍，听感上就是
    #   "一层弦乐糊在上面"。配额按**目标编配比例**给（交付版实测：Piano 4143 ·
    #   Perc 3776 · Bass 1677 · Strings 563 · Guitar 254），**按时间均匀抽**（不是随机，
    #   随机会在局部留空洞）。
    for tr, q in quotas.items():
        if tr not in ne:
            print('  ! --quota %s=%d 但没给这条轨的 --mid，忽略' % (tr, q))
            continue
        n0 = len(ne[tr])
        if q > 0 and n0 > q:
            step = n0 / float(q)
            ne[tr] = [ne[tr][min(n0 - 1, int(i * step))] for i in range(q)]
            print('  %-8s 配额抽样 %d → %d（%.0f%%）' % (tr, n0, q, 100.0 * q / n0))

    # —— 抽旋律（从 --melody-from 那条轨取每小节的高音区）——
    # ⚠ **还原模式默认不抽**（2026-09-25 · PITFALLS 255 第 ③ 条）：melody 是 `--melody-from`
    #   那条轨高音区的**副本**，而那条轨（`notes_extra`）**照旧整轨发声** → 同一个音
    #   在两条轨上同时响（一条用 Melody 音色、一条用原音色）。实测 `siren_end2`：
    #   Melody **176 音里 174 音（99%）**与 Piano 轨同音高且同时发声 —— 听感就是"糊/乱"。
    #   音**留在原轨、不丢**，所以清空 melody 只是去掉重复，不是删内容。
    _drop_mel = a.no_melody or (a.full and not a.keep_melody)
    if _drop_mel and mel_src:
        print('  melody 层：**清空**（还原模式）—— 它是 %s 轨高音区的副本，与那条轨重复发声；'
              '音留在原轨。要保留加 --keep-melody' % a.melody_from)
    if mel_src and not _drop_mel:
        mel = extract_melody(mel_src, secs, bar_sec)
        # melody 层同样**逐音**夹取（引擎的 Melody 轨也有音域门，同 `range_fit` 的理由）
        _mr = se.TR_RANGE.get('Melody')
        if _mr:
            _ma, _mb = _mr
            for _arr in mel.values():
                for _e in _arr:
                    _q = int(_e[3])
                    while _q < _ma:
                        _q += 12
                    while _q > _mb:
                        _q -= 12
                    if _ma <= _q <= _mb:
                        _e[3] = _q

    # —— 还原曲的旋律密度：**如实保留**，不为达标补音 ——
    # 用户口径（2026-09-25）："**如果是真的没有音要保留，重要的是符合原曲**"。
    # 扒谱天然只覆盖"主奏真的在响"的小节（`siren_end` 实测 89/143），密度常低于
    # `melody_health` 的下限（`probe_melody_health.MIN_DENS`）—— 那是**原曲的事实**，
    # 不是缺陷：靠放宽抽取窗口（把中音区伴奏当旋律）去凑密度 = 改内容迁就指标，方向反了。
    # 处理：写 `patterns.melody_exempt['dens']`（口径同 `align_exempt`：理由 blank = 没写），
    # 理由**必须带实测数字**，便于事后审计，并打印出来让人看见。
    _tot_bars = sum(s['bars'] for s in secs)
    _mel_n = sum(len(v) for v in mel.values())
    _mel_bars = len({(si, e[0]) for si, sec in enumerate(secs)
                     for e in mel.get(sec['melody'], [])})
    _dens = (_mel_n / float(_tot_bars)) if _tot_bars else 0.0
    try:
        import probe_melody_health as _MH
        _min_dens = _MH.MIN_DENS
    except Exception:                                              # noqa: BLE001
        _min_dens = 1.2
    if _tot_bars and _dens < _min_dens:
        _exempt_dens = ('还原曲、原曲稀疏：`--melody-from` 那条轨的转录只在 %d/%d 小节有音，'
                        '取高音区后 melody %d 音 = %.2f 音/小节（生成曲下限 %.1f）。'
                        '按 2026-09-25 口径"真的没有音要保留、符合原曲"**不补音**'
                        % (_mel_bars, _tot_bars, _mel_n, _dens, _min_dens))
        print('  ⚠ melody 密度 %.2f < %.1f：**保留原曲的稀疏**并写 patterns.melody_exempt'
              '（主奏只在 %d/%d 小节有音）' % (_dens, _min_dens, _mel_bars, _tot_bars))
    else:
        _exempt_dens = None

    d = {
        'name': a.name, 'bpm': float(a.bpm), 'meter': [4, 4],
        'desc': '由 transcribe_to_song.py 从转录生成（%g BPM / %d 小节）'
                % (a.bpm, sum(s['bars'] for s in secs)),
        'style': a.style,
        'patterns': {'bass_style': 'simple', 'perc_style': 'light',
                     'melody_dyn': True, 'arr_by_role': False,
                     'section_gap': 3.0, 'seg_fade': True,
                     'range_fix': True, 'legato_trim': True, 'dyn_vel': 8.0},
        'chords': chords, 'sections': secs, 'melody': mel,
        'notes_extra': ne,
    }
    if _exempt_dens:
        d['patterns']['melody_exempt'] = {'dens': _exempt_dens}

    # —— 伴奏轨的"和弦贴合率"：还原曲的读数只有**审计意义** ——
    # 判据 `t_accompaniment_harmony` 的 95% 门是为**引擎生成的编配**设的
    # （它当年抓的是 `TR_SHIFT` 改音级那个 bug）。还原曲的伴奏音是**抄来的真实演奏**，
    # 与独立分析的 `chords` 天然不完全一致（实测 Piano 61% / Strings 56% / Hook 42%）。
    # 要"符合原曲"就**不能改这些音**去凑 95% —— 于是写**带实测数字**的
    # `patterns.accomp_exempt.fit`（口径同 `melody_exempt`：理由空白 = 没写 = 不放行）。
    _gch = [c for _s in secs for c in _s['chords']]
    _fits = {}
    for _tr, _arr in ne.items():
        if _tr not in ('Hook', 'Piano', 'Arp', 'Strings', 'Pad') or len(_arr) < 40:
            continue
        _ok = 0
        for (_b, _bt, _du, _p, _v) in _arr:
            _e = chords.get(_gch[_b]) if _b < len(_gch) else None
            if _e and (_p % 12) in {x % 12 for x in _e[1]}:
                _ok += 1
        _fits[_tr] = _ok / float(len(_arr))
    _lowfit = {k: v for k, v in _fits.items() if v < 0.95}
    if _lowfit:
        _desc = '、'.join('%s %.0f%%' % (k, v * 100) for k, v in sorted(_lowfit.items()))
        d['patterns']['accomp_exempt'] = {
            'fit': ('还原曲：伴奏音来自**转录音符**（不是引擎按和弦生成），与独立分析的 '
                    'chords 不完全一致 —— 实测贴合率 %s（判据门 95%%）。按"符合原曲"'
                    '**不改这些音**（2026-09-25 口径）' % _desc)}
        print('  ⚠ 伴奏和弦贴合率 <95%%：%s —— 写 patterns.accomp_exempt（**不改音**，'
              '如实保留转录结果）' % _desc)
    # ⚠ 2026-09-26 **默认翻转**：不写 `--sample` 就显式写 `notes_extra_full: true`。
    #   **两种都显式写盘**（而不是靠引擎默认）是刻意的：字段留痕 ⇒ 换引擎版本也不会
    #   静默变行为，且 `t_restore_notes_full` 能当场看出"这首扒带曲声明的哪一种"。
    #   ⚠ `--sample` 必须写 `False`、**不能只是"不写"** —— 引擎默认已翻成 True，
    #   "不写"等于全量，那样 `--sample` 就成了空转（本轮真踩：写完第一版才发现）。
    d['patterns']['notes_extra_full'] = not a.sample
    # —— 力度来源留痕 ——
    # ⚠ 与 `notes_extra_full` 同一个理由：**显式写盘**，不靠"没写就是默认"。
    #   换引擎版本 / 换调用方时，`velocity_source` 能当场看出这一首的力度是
    #   "从哪条分轨量的"，还是"根本没量过"（`FAILED`）—— 后者听感就是打字机，
    #   而产物上看不出来（`notes_extra` 的力度字段照样有值，只是恒 100）。
    if _vel_rec:
        d['patterns']['velocity_source'] = dict(_vel_rec)
        _bad = [k for k, v in _vel_rec.items() if v == 'FAILED']
        print('  力度来源：%s%s'
              % ('、'.join('%s←%s' % (k, v) for k, v in sorted(_vel_rec.items())),
                 ('  ⚠ 其中 %s **没量成**（退回恒 100）' % '、'.join(_bad)) if _bad else ''))
    if a.sample:
        print('  ⚠ notes_extra 按 arr.density 抽样（--sample，写 notes_extra_full=false）：'
              '转录会被逐小节砍上限')
    else:
        print('  notes_extra 全量（不抽样，写 notes_extra_full=true）—— 要旧的抽样行为加 --sample')
    if drum_grid:
        # ⚠ 一定要**写进去**才生效：引擎的 Perc 音数主要由它决定，
        #   而"生成了却没写盘"正是这个项目最常见的一类坑（PITFALLS 182）。
        d['patterns']['drum_grid'] = {'per_bar': drum_grid}

    out = a.out or os.path.join(ROOT, 'songs', a.name, 'song.json')
    # —— **覆盖保护**（2026-10-04 加，PITFALLS 196 的同族）——
    # `os.makedirs(..., exist_ok=True)` + 直接 save = **静默覆盖**：回滚点被吃掉，
    # 而"命令 exit=0、日志正常"让人以为只是重跑了一遍（实测本曲第一次重跑时，
    # 我是先手工 `rm -rf` 才拿到干净产物的 —— 那一步本不该由人记得）。
    # 口径同 `new_song`：无 `--force` 时**备份 + 明说**，一个字都不静默丢。
    if os.path.isfile(out) and not a.force:
        bak = out + '.bak_before_transcribe'
        try:
            shutil.copy2(out, bak)
            print('  ! 目标已存在 → 已备份为 %s' % os.path.basename(bak))
            print('    要**整份重写**（不保留旧的回滚点）请加 --force；'
                  '要换名字请加 --out')
        except Exception as _e:                                    # noqa: BLE001
            print('  ! 目标已存在，但备份失败（%s）—— 继续写盘会覆盖它' % str(_e)[:80])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json_io.save(out, d)
    print('写 %s（%d 段 / %d 小节 / %d 和弦种 / notes_extra %d 轨 %d 音）'
          % (out, len(secs), sum(s['bars'] for s in secs), len(chords),
             len(ne), sum(len(v) for v in ne.values())))
    print('下一步：放一份 compose.py 到同目录 → make_song.py %s' % a.name)
    return 0


if __name__ == '__main__':
    sys.exit(main())
