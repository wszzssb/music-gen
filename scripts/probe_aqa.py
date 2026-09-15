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
     本工具的用法是 **A/B 比较**（同一首的不同版本、或我们自己的曲子之间），
     不是拿某个绝对分数线当验收门。**没有对应的自检项，也不该有。**
  ③ 分值区间经验上落在 1~9（越高越好）。

依赖只在 `.venv-ml`（torch + audiobox-aesthetics + soundfile）。**HuggingFace 直连不通 →
脚本默认走镜像** `HF_ENDPOINT=https://hf-mirror.com`（实测可用）。

⚠ **绕开 torchaudio 的 TorchCodec 依赖**：它的 `read_wav` 用 `torchaudio.load`，
而新版 torchaudio 要 torchcodec（本机没装、还要 ffmpeg）。下游 `make_inference_batch`
直接吃 wav 数组 —— 所以这里猴补丁 `read_wav`，改用 soundfile 读。

用法:
  .venv-ml\Scripts\python.exe scripts\probe_aqa.py songs\42_gtr_tender\gtr_tender_sf.ogg
  .venv-ml\Scripts\python.exe scripts\probe_aqa.py <音频A> <音频B> --segments 3   # 分段也支持
"""
import argparse
import os
import sys

os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')   # 直连不通时的常用镜像

import numpy as np                                              # noqa: E402
import torch                                                    # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

AXES = [('CE', '内容享受'), ('PQ', '制作质量'), ('CU', '内容有用'), ('PC', '制作复杂')]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('audio', nargs='+')
    ap.add_argument('--segments', type=int, default=0,
                    help='>1 时把每个文件等分几段分别打分（看走向）')
    a = ap.parse_args()

    import soundfile as sf
    import audiobox_aesthetics.infer as AB

    def _read_wav(meta):
        f = meta['path']
        info = sf.info(f)
        start = float(meta.get('start_time') or 0.0)
        end = float(meta.get('end_time') or (info.duration))
        y, sr = sf.read(f, start=int(start * info.samplerate),
                        stop=int(end * info.samplerate), dtype='float32', always_2d=True)
        wav = torch.from_numpy(np.ascontiguousarray(y.T))
        if wav.shape[0] > 1:
            wav = wav.mean(0, keepdim=True)
        return wav, sr

    AB.read_wav = _read_wav                                     # 猴补丁（绕 TorchCodec）

    print('加载模型 …')
    p = AB.initialize_predictor()

    jobs, labels = [], []
    if a.segments and a.segments > 1:
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

    out = p.forward(jobs)
    print()
    print('%-30s' % '' + ''.join('%10s' % ('%s %s' % (k, v)) for k, v in AXES))
    for lab, o in zip(labels, out):
        if isinstance(o, dict):
            print('%-30s' % lab + ''.join('%10.3f' % (o.get(k) or 0) for k, _v in AXES))
        else:
            print('%-30s %s' % (lab, o))
    print()
    print('读法：越高越好，经验区间 1~9。**绝对值没有本地参照**（拿不到商业曲音频），')
    print('      所以只用于 A/B 比较与曲内走势，别当绝对验收线。')


if __name__ == '__main__':
    sys.exit(main())
