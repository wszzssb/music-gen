#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""setup_wizard.py —— 一步步的环境向导（**中/英按系统语言自动切**）

从"刚 clone 完仓库"开始，把整条路走完；每步先报状态，再给选项，可随时跳过：

  第 1 步  主工具链：`.venv` + numpy/soundfile/imageio-ffmpeg
  第 2 步  音源：fluidsynth + GeneralUser GS（约 32MB，多镜像自动重试）
  第 3 步  自检：确认主工具链真的可用
  第 4 步  写第一首：一个 8 小节示例（可选，约 1 分钟）
  第 5 步  ML 环境：`.venv-ml` + torch（**约 5.4GB**，只有"扒 MIDI"才需要）
  第 6 步  模型：YourMT3 代码（4MB）+ 权重（516MB），自动下、走 hf-mirror 镜像
  第 7 步  试扒一首：把音频转成 MIDI（可选）

用法:
  python scripts\\setup_wizard.py            # 交互（每步问一下）
  python scripts\\setup_wizard.py --yes      # 全部照做，不问
  python scripts\\setup_wizard.py --lang en  # 强制英文（缺省按系统语言）
  python scripts\\setup_wizard.py --only 5,6 # 只跑第 5、6 步

为什么要有它（2026-09-19 按新手流程实测出来的）：
  · 自检在 clone 后必然有几条"素材不随仓库分发"的提示，新人不知道是不是坏了；
  · "扒 MIDI"要 4 样东西（venv-ml / 代码 / 权重 / 版本钉死的 transformers），
    老文档里三样只有"在哪"、没有"怎么拿"；
  · 依赖要装两轮（先 torch，再 requirements 里那一串），少一个就崩在半路。
每一步都幂等：装过的会跳过，随时可重跑。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

PY_VENV = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
if not os.path.isfile(PY_VENV):                      # 非 Windows 布局
    PY_VENV = os.path.join(ROOT, ".venv", "bin", "python")


def _cli_opt(name):
    """从 argv 里提前读一个选项 —— 要在模块顶部（定义提示语/路径之前）就能用到。"""
    for i, a in enumerate(sys.argv):
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


# ML 解释器：`--ml-python <路径>` > 环境变量 `DSH_ML_PYTHON` > 本仓库的 `.venv-ml`。
# 为什么要能指别处：**本机已经有装好的 5.4GB 环境时，别让新人再下一份**（用户 2026-09-19
# 明确要求"ML 环境 5.4GB 先调我电脑里有的"）——指向它，第 5 步就会直接判"本来就已完成"。
_ML_EXPLICIT = _cli_opt("--ml-python") or os.environ.get("DSH_ML_PYTHON")
if _ML_EXPLICIT:
    PY_ML = _ML_EXPLICIT
else:
    PY_ML = os.path.join(ROOT, ".venv-ml", "Scripts", "python.exe")
    if not os.path.isfile(PY_ML):
        PY_ML = os.path.join(ROOT, ".venv-ml", "bin", "python")


# ---------------------------------------------------------------- 语言
def is_zh():
    """系统界面语言是中文吗（Windows 用系统 API 更准；其它平台看 LANG）。"""
    try:
        if os.name == "nt":
            import ctypes
            return (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0xFF) == 0x04
    except Exception:                                        # noqa: BLE001
        pass
    return (os.environ.get("LANG") or os.environ.get("LC_ALL") or "").lower().startswith("zh")


def _cli_lang():
    """从 argv 里提前读 `--lang`（要在定义提示语之前知道用哪种语言）。"""
    for i, a in enumerate(sys.argv):
        if a.startswith("--lang="):
            return a.split("=", 1)[1]
        if a == "--lang" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


_PICK = _cli_lang()
ZH = (_PICK == "zh") if _PICK in ("zh", "en") else is_zh()

T = {
    "zh": {
        "title": "music-gen 环境向导",
        "lang_note": "（提示语按系统语言自动选；可用 --lang en / --lang zh 强制）",
        "toolchain": "工具链",
        "step": "第 %d/%d 步",
        "already": "（已装过；重跑会跳过已完成的部分）",
        "ask": "  [Enter] 执行　[s] 跳过　[q] 退出 > ",
        "skipped": "已跳过：%s",
        "quit": "已退出。随时可以重跑（已完成的步骤会自动跳过）。",
        "ok": "✓ 完成",
        "fail": "✗ 失败（看上面的报错）",
        "done_already": "（本来就已完成）",
        "next": "下一步该做什么",
        "all_done": "全部完成 🎉",
    },
    "en": {
        "title": "music-gen Setup Wizard",
        "lang_note": "(messages follow your system language; force with --lang en / --lang zh)",
        "toolchain": "toolchain",
        "step": "Step %d/%d",
        "already": "(already installed; re-running skips finished parts)",
        "ask": "  [Enter] run   [s] skip   [q] quit > ",
        "skipped": "Skipped: %s",
        "quit": "Bye. Re-run anytime (finished steps are skipped).",
        "ok": "✓ done",
        "fail": "✗ failed (see the error above)",
        "done_already": "(was already done)",
        "next": "What to do next",
        "all_done": "All done 🎉",
    },
}[("zh" if ZH else "en")]


def say(msg=""):
    print(msg, flush=True)


def run(args, title=None, cwd=None):
    """跑一条子进程命令，输出直接透到终端。返回退出码。"""
    if title:
        say("  · %s" % title)
    try:
        return subprocess.run(args, cwd=cwd or ROOT).returncode
    except FileNotFoundError as e:
        say("  !! 命令不存在：%s" % e)
        return 127


def quiet_ok(args):
    try:
        return subprocess.run(args, capture_output=True).returncode == 0
    except Exception:                                        # noqa: BLE001
        return False


# ---------------------------------------------------------------- 各步
def step_venv():
    """主工具链：.venv + numpy/soundfile/imageio-ffmpeg。"""
    if os.path.isfile(PY_VENV) and quiet_ok([PY_VENV, "-c", "import numpy, soundfile"]):
        return True, True
    if not os.path.isfile(PY_VENV):
        if run([sys.executable, "-m", "venv", os.path.join(ROOT, ".venv")],
               title="python -m venv .venv") != 0:
            return False, False
    rc = run([PY_VENV, "-m", "pip", "install", "-q",
              "numpy", "soundfile", "imageio-ffmpeg"], title="pip install 依赖 / deps")
    return rc == 0, False


def step_soundfont():
    """音源：fluidsynth.exe + GeneralUser GS（约 32MB）。"""
    v = os.path.join(ROOT, "vendor")
    if os.path.isdir(v) and any(f.lower().endswith(".sf2") for f in os.listdir(v)):
        return True, True
    return run([PY_VENV, os.path.join(HERE, "setup_soundfont.py")]) == 0, False


def step_selftest():
    return run([PY_VENV, os.path.join(HERE, "selftest.py"), "--fast"],
               title="selftest.py --fast") == 0, False


def step_first_song():
    """写第一首：8 小节示例（spec → song.json → 渲染 → 成绩单）。"""
    import json
    d = os.path.join(ROOT, "songs", "99_my_first")
    if os.path.isfile(os.path.join(d, "song.json")):
        return True, True
    spec = {"name": "my_first", "bpm": 150, "style": "daily", "ref": "BGM16c",
            "desc": "我的第一首 / my first",
            "sections": [{"name": "A", "bars": 8, "chords": "D A Bm7 G6 D A G6 A",
                          "melody": {"m": [[0, 0, 74], [0, 2, 78], [1, 0, 81], [1, 2, 85],
                                           [2, 0, 83], [2, 2, 86], [3, 0, 79], [3, 2, 83],
                                           [4, 0, 74], [4, 2, 78], [5, 0, 81], [5, 2, 85],
                                           [6, 0, 79], [6, 2, 86], [7, 0, 81], [7, 2, 85]]}}]}
    sp = os.path.join(ROOT, "my_spec.json")
    json.dump(spec, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if run([PY_VENV, os.path.join(HERE, "build_song.py"), sp, "--out", d],
           title="build_song.py") != 0:
        return False, False
    rc = run([PY_VENV, os.path.join(HERE, "make_song.py"), "99_my_first"],
             title="make_song.py（渲染 + 成绩单 / render + scorecard）")
    return rc == 0, False


def step_ml_env():
    """ML 环境：.venv-ml + torch cu128 + 转录要的那一串（约 5.4GB）。"""
    if os.path.isfile(PY_ML) and quiet_ok([PY_ML, "-c",
                                           "import torch, soundfile, lightning, mido"]):
        return True, True
    if not os.path.isfile(PY_ML):
        if not quiet_ok(["py", "-3.13", "-V"]) and not quiet_ok(["python3.13", "-V"]):
            say("  !! 需要 Python 3.13（`py -3.13`）。装好再回来，或用 --only 跳过这步。")
            say("  !! Python 3.13 required (`py -3.13`). Install it and re-run, or --only.")
            return False, False
        maker = ["py", "-3.13"] if quiet_ok(["py", "-3.13", "-V"]) else ["python3.13"]
        if run(maker + ["-m", "venv", os.path.join(ROOT, ".venv-ml")],
               title="py -3.13 -m venv .venv-ml") != 0:
            return False, False
    if run([PY_ML, "-m", "pip", "install", "torch", "torchaudio",
            "--index-url", "https://download.pytorch.org/whl/cu128"],
           title="pip install torch torchaudio（约 4.2GB，慢 / slow）") != 0:
        return False, False
    rc = run([PY_ML, "-m", "pip", "install", "demucs", "matchering", "librosa",
              "pyloudnorm", "mido", "lightning>=2.2.1", "deprecated", "einops",
              "wandb", "python-dotenv", "mir_eval", "soundfile"],
             title="pip install 转录依赖 / transcription deps")
    return rc == 0, False


def step_model():
    """模型代码 + 权重：复用 transcribe_ymt3.py 里那两个函数（不抄第二份）。"""
    try:
        import transcribe_ymt3 as ty
    except Exception as e:                                   # noqa: BLE001
        say("  !! 载入 transcribe_ymt3.py 失败 / cannot load: %s" % e)
        return False, False
    fresh = False
    repo = ty.find_repo(None)
    if repo is None:
        repo = ty.clone_repo(os.environ.get("DSH_YMT3_REPO") or ty.DEFAULT_DEST)
        fresh = True
        if repo is None:
            return False, False
    libs = ty.find_libs(repo)
    if not libs:
        say("  !! 缺 transformers 4.45.1 侧载目录 —— 第 5 步没做完？")
        say("  !! missing side-loaded transformers 4.45.1 -- did step 5 finish?")
        return False, False
    sys.path.insert(0, libs)
    miss = ty.missing_modules()
    if miss:
        say("  !! 还缺依赖 / still missing: %s（重跑第 5 步 / re-run step 5）" % ", ".join(miss))
        return False, False
    if ty.find_weights(repo, None) is None:
        if ty.download_weights(repo) is None:
            return False, False
        fresh = True
    return True, not fresh


def step_try_transcribe():
    """试扒一首：有素材就真跑一遍，没有就给命令。"""
    bgm = os.environ.get("BGM_REF_DIR", "")
    cand = []
    if bgm and os.path.isdir(bgm):
        cand = [os.path.join(bgm, f) for f in sorted(os.listdir(bgm))
                if f.lower().endswith((".ogg", ".wav", ".mp3", ".flac"))][:1]
    if not cand:
        say("  （没找到可试的音频。设 BGM_REF_DIR 指向素材目录，或手动跑 / no sample audio;"
            " set BGM_REF_DIR or run manually:）")
        say("     %s scripts%stranscribe_ymt3.py <音频/audio> -o <out> --download"
            % (PY_ML, os.sep))
        return True, False
    out = os.path.join(ROOT, "transcribe_out")
    rc = run([PY_ML, os.path.join(HERE, "transcribe_ymt3.py"), cand[0],
              "-o", out, "--download"], title="transcribe_ymt3.py")
    if rc == 0:
        say("  → %s" % out)
    return rc == 0, False


STEPS = [
    ("主工具链 .venv + 依赖 / main venv + deps", step_venv,
     lambda: os.path.isfile(PY_VENV)),
    ("音源 fluidsynth + sf2 / soundfont", step_soundfont,
     lambda: os.path.isdir(os.path.join(ROOT, "vendor"))
     and any(f.lower().endswith(".sf2") for f in os.listdir(os.path.join(ROOT, "vendor")))),
    ("自检 / self-check", step_selftest, lambda: False),
    ("写第一首 / write your first song", step_first_song,
     lambda: os.path.isfile(os.path.join(ROOT, "songs", "99_my_first", "song.json"))),
    ("ML 环境 .venv-ml（约 5.4GB）/ ML env", step_ml_env,
     lambda: os.path.isfile(PY_ML)),
    ("模型代码 + 权重 / model code + weights", step_model, lambda: False),
    ("试扒一首 MIDI / try transcribing", step_try_transcribe, lambda: False),
]


def main():
    import argparse
    ap = argparse.ArgumentParser(description="music-gen setup wizard / 环境向导")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="全部照做，不逐步询问 / run everything, no prompts")
    ap.add_argument("--only", default=None,
                    help="只跑这几步，如 5,6 / only these steps, e.g. 5,6")
    ap.add_argument("--lang", default=None, choices=["zh", "en"],
                    help="强制语言 / force language（已在启动时生效）")
    ap.add_argument("--ml-python", default=None, metavar="PATH",
                    help="复用已有的 ML 解释器（指向别处 .venv-ml\\Scripts\\python.exe，"
                         "省掉 5.4GB 重装）/ reuse an existing ML python; "
                         "也可用环境变量 DSH_ML_PYTHON")
    a = ap.parse_args()
    only = ({int(x) for x in a.only.replace(" ", "").split(",") if x} if a.only else None)

    say("=" * 68)
    say("  %s" % T["title"])
    say("  %s" % T["lang_note"])
    say("  %s / %s: %s" % (T["toolchain"], "toolchain", ROOT))
    say("=" * 68)

    total = len(STEPS)
    for i, (name, fn, pre) in enumerate(STEPS, 1):
        if only and i not in only:
            continue
        try:
            done = bool(pre())
        except Exception:                                    # noqa: BLE001
            done = False
        say()
        say("─" * 68)
        say("%s — %s" % (T["step"] % (i, total), name))
        if done:
            say("  " + T["already"])
        if not a.yes:
            say(T["ask"], )
            try:
                ans = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "q"
            if ans in ("q", "quit", "exit"):
                say(T["quit"])
                return 0
            if ans in ("s", "skip"):
                say("  " + T["skipped"] % name)
                continue
        import time as _t
        t0 = _t.time()
        try:
            ok, was_done = fn()
        except Exception as e:                               # noqa: BLE001
            say("  !! %s: %s" % (type(e).__name__, e))
            ok, was_done = False, False
        say("  %s（%.0f s）%s" % (T["ok"] if ok else T["fail"], _t.time() - t0,
                                  " " + T["done_already"] if was_done else ""))

    say()
    say("=" * 68)
    say("  %s" % T["all_done"])
    say("  %s / %s：" % (T["next"], "next"))
    say("    · %s" % ("写歌 / song      : python scripts%sbuild_song.py my_spec.json --out songs%s99_x"
                      % (os.sep, os.sep)))
    say("    · %s" % ("扒 MIDI / midi    : .venv-ml%sScripts%spython scripts%stranscribe_ymt3.py <音频> -o out --download"
                      % (os.sep, os.sep, os.sep)))
    say("    · %s" % ("命令全表 / commands: CHEATSHEET.md"))
    say("=" * 68)
    return 0


if __name__ == "__main__":
    import subprocess                                    # noqa: E402  （run() 用）
    import cli_utf8 as _cu; _cu.setup()                  # 控制台编码兜底（GBK 下打印 ✓ 会崩）
    sys.exit(main())
