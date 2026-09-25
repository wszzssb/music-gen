#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""restore_oneshot.py —— **扒带"一键到底"**：`song.json` 之后的那半段，第一次就跑到位

## 它补的是哪一段
现成的 `transcribe_ymt3.py <音频>` 已经能"分轨 → 转录 → 写 `song.json`"（引擎编配＝正路）。
**缺的是 `song.json` 之后**：内容整形、力度写回、段级编配、渲染、体检 —— 这半段在 siren_end2 上
花了 **12 轮**才收敛（`HANDOFF-ROUND7…12`）。本工具把那些**验证过**的判据固化成一条命令。

## 阶段与依据（默认值都有出处；`--from` 可续跑）

| 阶段 | 调什么 | 判据 / 出处 |
|---|---|---|
| `probe` | `probe_instruments.py --json` | **扒带第 ⓿ 步**：逐段编制表（`lead` 分轨 + 起音 + 在场分轨）；后面每一步都用它 |
| `repair` | `arrange_voices.py` + `merge_sustain.py` + 静音段体检 | 并轨读编制表（**Pad 只进不出**）· 持续层按**原曲该音高能量连续性**合片 · 静音段**只报不删**（实测删了会让开头掉 14dB，顺序必须先补低频） |
| `vel` | `vel_from_ref.py` | **本轮唯一把指标推上去的杠杆**：原曲响度→力度写回谱面（力度中位 74→105、亮度比 0.41→0.67、\|RMS差\| 2.56→2.07） |
| `arrange` | `arrange_sections.py`（`--mix`/`--vel` **按 `work/` 里有没有判据文件**自动开，见 `arrange_cli()`） | 段级 CC7（逐段 RMS 差，死区 1.5dB / ±3dB）+ 段级力度（2–6k 比值 >1.5 才降）—— ⚠ **首次跑没有 `sections.json`/`bright.json`（那是 `audit` 的产物）⇒ 第一遍不生效，带 `--from arrange` 跑第二遍才生效**（一次迭代，不是循环） |
| `render` | `make_song.py` + `render_midi.py` | 引擎渲染 + 母带链 |
| `audit` | `timbre_audit` + `report_sections` + **亮度比** | 三把尺子一次出：嘶声/形态 · 逐段 RMS 差 & chroma · 逐段 2–6kHz 比值 |

## ⚠ 三条"别跳"的纪律（都来自实测翻车）
1. **静音段筛选（删"凭空音"）必须先补低频再删** —— `siren_end2` 删掉 S01/S02 那 42 个凭空贝斯后
   S01 的 RMS 差从 −5.9 → **−14.4dB**（原曲那两段的 20–80Hz 来自垫子，删了没有等效物）。
   所以本工具**默认只报**，`--allow-stem-cut` 才真删。
2. **逐段 CC7 在"整曲归一化"的母带链下是零和的**（抬高多数段 = 压低没抬的段）—— 只能压平缺口，不能整体抬响。
3. **段级音色（`arr.prog`）别拿单音乐器弹多音和弦线**：GM40 小提琴在 siren_end2 的 S17–S22 上
   把 2–6kHz 从 3.4–5.4% 顶到 **10.4–29.9%**（负结果）。本工具默认**不开** `--prog/--shift`。

## 用法

```bash
python scripts\restore_oneshot.py <曲名|song.json> --audio <原曲音频> --stems-dir <h6_*.wav目录> \
       [--ymt3-dir <转录目录>] [--render] [--from probe|repair|vel|arrange|render|audit] [--dry]
python scripts\restore_oneshot.py --selftest
```
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
ML = os.path.join(ROOT, ".venv-ml", "Scripts", "python.exe")
STAGES = ["probe", "repair", "vel", "arrange", "render", "audit"]
# 默认值写成模块常量，好让 `--selftest` 与变异测试直接量它们（别只测集成路径）
DEFAULT_ALLOW_STEM_CUT = False      # ⚠ 删静音段之前必须先补低频（见文档头第 1 条）
DEFAULT_PROG = None                 # ⚠ 段级音色默认关（GM40 在密集和弦上是负结果）


def sh(cmd, tag, quiet=False):
    print("  $ %s" % " ".join(str(c) for c in cmd[1:4] + ["…"]))
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    if not quiet:
        for ln in out.strip().splitlines()[-14:]:
            print("    %s" % ln)
    if r.returncode != 0:
        raise SystemExit("[%s] 失败（exit %d）：\n%s" % (tag, r.returncode, out[-1500:]))
    return out


def resolve(song):
    p = song if song.endswith(".json") else os.path.join(ROOT, "songs", song, "song.json")
    if not os.path.exists(p):
        raise SystemExit("找不到 song.json：%s" % p)
    return p


def brightness_table(mine_wav, ref_wav, song_json, out_json):
    """逐段**整段**口径的 2–6kHz 占比（我方 vs 原曲）→ json。⛔ 不要用 timbre_audit 的读数：
    它每次只量段首 186ms（`y[:8192]`），对"层/力度"这类改动不敏感（实测两版逐段读数一字未变）。"""
    code = (
        "import json,sys,numpy as np\n"
        "sys.path.insert(0, r'%s')\n"
        "import metrics\n"
        "d=json.load(open(r'%s',encoding='utf-8')); bar=4*60.0/float(d['bpm'])\n"
        "segs=[];acc=0.0\n"
        "for s in d['sections']:\n"
        "    segs.append((s['name'],acc*bar,(acc+s['bars'])*bar)); acc+=s['bars']\n"
        "def rd(p):\n"
        "    y,sr=metrics.read_audio(p,mono=True); return np.asarray(y,dtype='float32'),sr\n"
        "def rt(y,sr,t0,t1,lo=2000,hi=6000):\n"
        "    a,b=int(t0*sr),int(min(t1,len(y)/sr)*sr); seg=y[a:b]\n"
        "    if len(seg)<4096: return 0.0\n"
        "    P=np.abs(np.fft.rfft(seg*np.hanning(len(seg))))**2; f=np.fft.rfftfreq(len(seg),1.0/sr)\n"
        "    m=(f>=lo)&(f<=hi); return round(float(100.0*P[m].sum()/(P.sum()+1e-12)),3)\n"
        "ym,sm=rd(r'%s'); yr,sr2=rd(r'%s')\n"
        "json.dump({nm:[rt(ym,sm,t0,t1),rt(yr,sr2,t0,t1)] for (nm,t0,t1) in segs},"
        "open(r'%s','w',encoding='utf-8'),ensure_ascii=False,indent=1)\n"
        "print('亮度表 ->', r'%s')\n" % (HERE, song_json, mine_wav, ref_wav, out_json, out_json))
    sh([ML, "-c", code], "brightness")


def arrange_cli(work, prog=None, shift=None, dry=False):
    """`arrange` 阶段传给 `arrange_sections.py` 的参数（**纯函数，便于自检/变异**）。

    ## 这一步以前是**空转**的（2026-09-26 接线）
    原实现把 `--mix off --vel off` **写死**、且**从不传** `--sec-json`/`--bright-json`
    ⇒ 段级 CC7 与段级力度**永远不会生效**；连它自己打印的那句"先跑一次 audit，
    再带 `--from arrange` 重跑才会生效"也走不通 —— 重进的还是同一个硬编码 `off` 分支。

    **实测代价**（`dear_good_friends`，手工接上同一步）：
    `|RMS差| 2.07 → 1.78` · 150 读数（10 带×15 段的平均绝对带差）`3.80 → 3.41` ·
    `chroma 0.8821 → 0.8867`；最差的三段被拉回来：S13 `+6.00 → +3.20dB` ·
    Ending `+7.20 → +4.50` · S14 `+2.10 → +0.70`。

    ## 判据文件从哪来
    `audit` 阶段写进 `work/` 的：`sections.json`（逐段 RMS 差 → CC7）·
    `bright.json`（逐段 2–6k 比值 → 段级力度）。**没有它们就保持 off** ——
    首次跑本来就没有（所以第一遍仍不生效、第二遍带 `--from arrange` 才生效，
    与文档一致；区别是**现在真的能生效**而不是白跑）。
    """
    args = []
    sec = os.path.join(work, "sections.json")
    br = os.path.join(work, "bright.json")
    if os.path.exists(sec):
        args += ["--sec-json", sec, "--mix", "on"]
    else:
        args += ["--mix", "off"]
    if os.path.exists(br):
        args += ["--bright-json", br, "--vel", "on"]
    else:
        args += ["--vel", "off"]
    if prog:
        args += ["--prog", prog]
    if shift:
        args += ["--shift", shift]
    if dry:
        args += ["--dry"]
    return args


def selftest(verbose=True):
    """链路自检：**阶段顺序** · **默认不删音** · **默认不开段级音色** —— 这三条都是实测换来的默认。
    （删音那条来自"删了 S01 掉 14.4dB"；段级音色那条来自"GM40 把 S18 顶到 55.6%"。）"""
    ok = []
    ok.append(("阶段顺序", STAGES == ["probe", "repair", "vel", "arrange", "render", "audit"]))
    ok.append(("默认不删音", DEFAULT_ALLOW_STEM_CUT is False))
    ok.append(("默认不开段级音色", DEFAULT_PROG is None))
    if verbose:
        for nm, v in ok:
            print("  %-18s %s" % (nm, "PASS" if v else "FAIL"))
        print("restore_oneshot 自检 " + ("PASS" if all(v for _n, v in ok) else "FAIL"))
    return all(v for _n, v in ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song", nargs="?", help="曲名或 song.json")
    ap.add_argument("--audio", help="原曲音频（力度写回 + 体检的参考）")
    ap.add_argument("--stems-dir", help="Demucs 分轨 wav 目录（h6_*.wav）")
    ap.add_argument("--ymt3-dir", help="转录目录（含 mix.mid / h6_*.mid，probe 的第①层用）")
    ap.add_argument("--work", help="中间产物目录（默认 <song.json 所在目录>/_oneshot）")
    ap.add_argument("--prog", default=DEFAULT_PROG, help="段级音色（默认关；负结果见文档）")
    ap.add_argument("--shift", default=None, help="段级移调（默认关）")
    ap.add_argument("--from", dest="frm", default="probe", choices=STAGES)
    ap.add_argument("--to", dest="to", default="audit", choices=STAGES)
    ap.add_argument("--render", action="store_true", help="跑 make_song（默认只到 arrange）")
    ap.add_argument("--allow-stem-cut", dest="allow_stem_cut", action="store_true",
                    default=DEFAULT_ALLOW_STEM_CUT, help="真删静音段的音（⚠ 先补低频）")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 2
    if not a.song:
        ap.print_help(); return 1
    sj = resolve(a.song)
    name = os.path.basename(os.path.dirname(sj))
    work = a.work or os.path.join(os.path.dirname(sj), "_oneshot")
    os.makedirs(work, exist_ok=True)
    print("曲目 %s\nsong.json %s\n中间产物 %s\n阶段 %s → %s%s"
          % (name, sj, work, a.frm, a.to, "（--dry）" if a.dry else ""))
    run = STAGES[STAGES.index(a.frm):STAGES.index(a.to) + 1]
    table = os.path.join(work, "inst_table.json")

    if "probe" in run:
        print("\n=== probe：逐段编制表（第 ⓿ 步）===")
        if not (a.ymt3_dir and a.stems_dir):
            print("  ⚠ 缺 --ymt3-dir / --stems-dir → 跳过编制表（后面的 arrange 会退化成'没有证据就不改'）")
        else:
            sh([PY, os.path.join(HERE, "probe_instruments.py"), "--ymt3-dir", a.ymt3_dir,
                "--stems-dir", a.stems_dir, "--song-json", sj, "--json", table], "probe")

    if "repair" in run:
        print("\n=== repair：内容整形（并轨读表 + 持续层合片）===")
        args = [PY, os.path.join(HERE, "arrange_voices.py"), "--song-json", sj]
        if os.path.exists(table):
            args += ["--table", table]
        if a.dry:
            args += ["--dry"]
        sh(args, "arrange_voices")
        if a.stems_dir and os.path.exists(os.path.join(a.stems_dir, "h6_other.wav")):
            args = [PY, os.path.join(HERE, "merge_sustain.py"), "--song-json", sj,
                    "--ref", os.path.join(a.stems_dir, "h6_other.wav"), "--tracks", "Pad,Strings"]
            args += ["--dry"] if a.dry else ["--apply"]
            sh(args, "merge_sustain")
        else:
            print("  ⚠ 缺 h6_other.wav → 跳过持续层合片（合片判据要原曲能量）")
        if a.stems_dir and os.path.exists(os.path.join(a.stems_dir, "h6_bass.wav")):
            args = [PY, os.path.join(HERE, "filter_song_by_stem.py"), "--song-json", sj,
                    "--stem", os.path.join(a.stems_dir, "h6_bass.wav"), "--track", "Bass"]
            if a.allow_stem_cut:
                args += ["--apply"]
            sh(args, "filter_song_by_stem")

    if "vel" in run:
        print("\n=== vel：原曲响度 → 力度写回谱面（本轮唯一有效的杠杆）===")
        if not a.audio:
            print("  ⚠ 缺 --audio → 跳过")
        else:
            args = [PY, os.path.join(HERE, "vel_from_ref.py"), "--song-json", sj, "--ref", a.audio]
            args += ["--dry"] if a.dry else ["--apply"]
            sh(args, "vel_from_ref")

    if "arrange" in run:
        print("\n=== arrange：段级编配（CC7 + 力度）===")
        args = [PY, os.path.join(HERE, "arrange_sections.py"), "--song-json", sj]
        if os.path.exists(table):
            args += ["--table", table]
        # ⚠ 这一步**以前是空转的**（`--mix off --vel off` 写死 + 从不传判据文件）——
        #   接线口径与实测数字见 `arrange_cli()` 的 docstring。
        args += arrange_cli(work, a.prog, a.shift, a.dry)
        args += ["--dry"] if a.dry else ["--apply"]
        sh(args, "arrange_sections")

    if "render" in run and a.render:
        print("\n=== render：make_song + render_midi ===")
        if a.dry:
            print("  （--dry：不渲染）")
        else:
            sh([PY, os.path.join(HERE, "make_song.py"), name], "make_song")
            mid = os.path.join(os.path.dirname(sj), name + ".mid")
            sh([PY, os.path.join(HERE, "render_midi.py"), mid, os.path.join(work, name)], "render")

    if "audit" in run:
        print("\n=== audit：三把尺子 ===")
        wav = os.path.join(work, name + ".wav")
        if not os.path.exists(wav):
            wav = os.path.join(os.path.dirname(sj), name + "_sf.wav")
        if a.audio and os.path.exists(wav) and a.stems_dir:
            sh([ML, os.path.join(HERE, "timbre_audit.py"), sj, "--ref", a.audio,
                "--stems", a.stems_dir, "--mine", wav, "--json", os.path.join(work, "timbre.json")],
               "timbre_audit")
            sh([ML, os.path.join(HERE, "report_sections.py"), sj, "--ref", a.audio,
                "--mine", wav, "--mid", os.path.join(os.path.dirname(sj), name + ".mid"),
                "--json", os.path.join(work, "sections.json")], "report_sections")
            brightness_table(wav, a.audio, sj, os.path.join(work, "bright.json"))
            print("  ⚠ 逐段 CC7 要拿 `sections.json` 的 RMS 差当判据 → 先跑一次 audit，"
                  "再带 `--from arrange` 重跑 arrange 才会生效（一次迭代，不是循环）")
        else:
            print("  ⚠ 缺 --audio / 缺渲染音频 → 跳过体检")
    print("\n完成。中间产物都在 %s" % work)
    return 0


_SRC_SELF = open(os.path.abspath(__file__), encoding="utf-8").read() if os.path.exists(
    os.path.abspath(__file__)) else ""

if __name__ == "__main__":
    raise SystemExit(main())
