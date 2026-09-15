#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_aqa.py —— **美学质量评估（AQA）**：让模型直接给"好听度"打一个分。

模型：[facebook/audiobox-aesthetics](https://huggingface.co/facebook/audiobox-aesthetics)
（[论文](https://ar5iv.labs.arxiv.org/html/2502.05139)）—— 统一的语音/音乐/音效质量评估。
四个轴（**它自己给的名字**）：
  · **CE** Content Enjoyment —— 内容享受度，**最接近"好不好听"**
  · **PQ** Production Quality —— 制作质量（录音/混音的水准）
  · **CU** Content Usefulness —— 内容有用性（"这段能不能用"）
  · **PC** Production Complexity —— 制作复杂度
（`probe_aesthetic.py` 用的 CLAP 是**语义相似度**——"像不像某类音乐"；
 AQA 则是**直接回归到人类主观评分**。两者互补：CLAP 答"像不像"，AQA 答"多好"。）

⚠ **边界（必须知道）**：
  ① **分数是"模型对人类主观分的回归"，不是人类评分本身**。论文自己也强调跨域泛化有限。
  ② **绝对值没有本地参照** —— 我们拿不到商业曲音频（版权），所以**不知道 7.6 算好还是差**。
     本工具的用法是 **A/B 比较或曲库排序**（同曲不同版本、或我们自己的曲子之间），
     不是拿某个绝对分数线当验收门。**没有对应的自检项，也不该有。**
  ③ 分值区间经验上落在 1~9（越高越好）。
  ④ 实测（2026-09-15）**CE 不完全等于「情绪愉悦」**：43 号（前喜后悲）与
     45 号（前悲后喜）的 CE **都随段落下降** —— 它更像"制作/能量/复杂度"的混合。
     看情绪走向请用 `probe_aesthetic.py`（CLAP）。

依赖只在 `.venv-ml`（torch + audiobox-aesthetics + soundfile）。**HuggingFace 直连不通 →
脚本默认走镜像** `HF_ENDPOINT=https://hf-mirror.com`（实测可用）。

⚠ **绕开 torchaudio 的 TorchCodec 依赖**：它的 `read_wav` 用 `torchaudio.load`，
而新版 torchaudio 要 torchcodec（本机没装、还要 ffmpeg）。下游 `make_inference_batch`
直接吃 wav 数组 —— 所以这里猴补丁 `read_wav`，改用 soundfile 读。

用法:
  .venv-ml\Scripts\python.exe scripts\probe_aqa.py songs\42_gtr_tender\gtr_tender_sf.ogg
  .venv-ml\Scripts\python.exe scripts\probe_aqa.py <音频A> <音频B> --segments 3
  .venv-ml\Scripts\python.exe scripts\probe_aqa.py --all            # 全库排序（按 CE）
  .venv-ml\Scripts\python.exe scripts\probe_aqa.py --all --sort pq
（全库模式**必须分块**：一次 forward 会按最长样本 padding，39 首 64 秒要 34 GiB 显存。
  `--chunk` 默认 4，嫌慢可调大，显存不够就调小。）
"""
import argparse
import glob
import os
import sys

os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')   # 直连不通时的常用镜像

import numpy as np                                              # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()                             # noqa: E402  控制台编码兜底

# ⚠ `torch` **只能在函数内 import**：本模块顶层必须能被**主 venv**（没有 torch）import 成功，
#    否则 selftest 的 `import_all` 会红。`probe_aesthetic.py` 是同一约定。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
AXES = [('CE', '内容享受'), ('PQ', '制作质量'), ('CU', '内容有用'), ('PC', '制作复杂')]


def _patch():
    """猴补丁 read_wav：绕开 torchcodec。"""
    import soundfile as sf
    import torch
    import audiobox_aesthetics.infer as AB

    def _read_wav(meta):
        f = meta['path']
        info = sf.info(f)
        start = float(meta.get('start_time') or 0.0)
        end = float(meta.get('end_time') or info.duration)
        y, sr = sf.read(f, start=int(start * info.samplerate),
                        stop=int(end * info.samplerate), dtype='float32', always_2d=True)
        wav = torch.from_numpy(np.ascontiguousarray(y.T))
        if wav.shape[0] > 1:
            wav = wav.mean(0, keepdim=True)
        return wav, sr

    AB.read_wav = _read_wav
    return AB


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('audio', nargs='*')
    ap.add_argument('--all', action='store_true', help='全库排序（默认 songs/*/*_sf.ogg）')
    ap.add_argument('--glob', default='',
                    help='--all 改用它取文件（例：对照权威商业 BGM 目录 '
                         '"<素材包>\\Bgm\\*.ogg"）')
    ap.add_argument('--segments', type=int, default=0,
                    help='>1 时把每个文件等分几段分别打分（看走向）')
    ap.add_argument('--sort', default='CE', choices=[k for k, _v in AXES],
                    help='全库模式的排序键（默认 CE，倒序）')
    ap.add_argument('--chunk', type=int, default=4,
                    help='每批几首（默认 4）——整库一次 forward 会按最长样本 padding，'
                         '39 首 64 秒直接要 34 GiB 显存（实测 OOM）')
    a = ap.parse_args()

    import torch                      # 只在函数内（顶层得让主 venv 也能 import）
    AB = _patch()
    print('加载模型 …')
    p = AB.initialize_predictor()

    def _run(jobs, chunk):
        """分块推理：一次全塞会按最长样本 padding，显存按「首数 × 最长时长」爆掉。"""
        res = []
        for i in range(0, len(jobs), max(1, chunk)):
            res.extend(p.forward(jobs[i:i + max(1, chunk)]))
            torch.cuda.empty_cache()
        return res

    if a.all:
        files = sorted(glob.glob(a.glob or os.path.join(ROOT, 'songs', '*', '*_sf.ogg')))
        if not files:
            raise SystemExit('没找到 songs/*/*_sf.ogg —— 先跑 make_song.py 渲染')
        out = _run([{'path': f} for f in files], a.chunk)
        rows = []
        for f, o in zip(files, out):
            if isinstance(o, dict):
                # --glob 指向别处时父目录名全是同一个 → 改用文件名
                _sid = (os.path.splitext(os.path.basename(f))[0] if a.glob
                        else os.path.basename(os.path.dirname(f)))
                rows.append((_sid, o))
        rows.sort(key=lambda r: -(r[1].get(a.sort) or 0))
        print()
        print('%d 首（按 %s 倒序）' % (len(rows), a.sort))
        print('%-26s' % '' + ''.join('%11s' % ('%s %s' % (k, v)) for k, v in AXES))
        for sid, o in rows:
            print('%-26s' % sid[:26]
                  + ''.join('%11.3f' % (o.get(k) or 0) for k, _v in AXES))
        print()
        vals = [o.get(a.sort) or 0 for _s, o in rows]
        print('   %s：min %.3f / 中位 %.3f / max %.3f' %
              (a.sort, min(vals), sorted(vals)[len(vals) // 2], max(vals)))
        print('   读法：**只做库内比较**（绝对值没有本地参照）——')
        print('        末尾几首值得优先听；配合 `probe_aesthetic.py` 看情绪是否也对。')
        return 0

    if not a.audio:
        raise SystemExit('给至少一个音频路径，或用 --all')
    jobs, labels = [], []
    if a.segments and a.segments > 1:
        import soundfile as sf
        for f in a.audio:
            dur = sf.info(f).duration
            step = dur / a.segments
            for i in range(a.segments):
                jobs.append({'path': f, 'start_time': i * step,
                             'end_time': min(dur, (i + 1) * step)})
                labels.append('%s [%d]' % (os.path.basename(f)[:18], i + 1))
    else:
        for f in a.audio:
            jobs.append({'path': f})
            labels.append(os.path.basename(f)[:26])
    out = _run(jobs, a.chunk)
    print()
    print('%-30s' % '' + ''.join('%11s' % ('%s %s' % (k, v)) for k, v in AXES))
    for lab, o in zip(labels, out):
        if isinstance(o, dict):
            print('%-30s' % lab + ''.join('%11.3f' % (o.get(k) or 0) for k, _v in AXES))
        else:
            print('%-30s %s' % (lab, o))
    print()
    print('读法：越高越好，经验区间 1~9。**绝对值没有本地参照**，只用于 A/B 与曲内走势。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
