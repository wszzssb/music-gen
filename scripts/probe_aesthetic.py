#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_aesthetic.py —— **让模型"听"一遍**：零样本音频-文本相似度，分段打分。

为什么需要它（2026-09-15 用户："有没有用 ai 听音乐好不好听的方法"）：
`metrics.py` 量的是**客观质量**（响度/质心/宽度/噪声/爆音），`probe_*` 量的是
**形态与结构**（落点/时值/音程/张力/声部）—— 这些全绿之后，剩下的"好不好听"
只能靠耳朵。CLAP（Contrastive Language-Audio Pretraining）能把这一步**部分**自动化：
它把音频与文本编码到同一空间，于是可以问"这段音频更像『悲伤的钢琴曲』还是『欢快的流行歌』"，
**不需要训练、不需要参考音频**（我们拿不到参考曲音频：版权原因不下发）。

⚠ **它的边界（必须说清）**：CLAP 量的是**语义相似度**，不是"好听度"。
问它"这段音乐好不好听"得到的只是"它像不像数据集里被标为『好听』的那类音频"——
这仍然是**分布上的像不像**，与你耳朵的判断不是一回事。
研究界有专门的美学质量评估（AQA）模型，但泛化性差、且同样需要配对数据。
**所以本工具用来查"这段音频的语义像不像我们要的"（情绪/风格/编制），
不能替代你听。** 用它做的是"把主观描述变成可复现的数字"，便于**回归对照**。

依赖：`torch` + `transformers` + `librosa` —— 只在 `.venv-ml` 里（主 venv 没有）。
模型：`laion/clap-htsat-unfused`。**HuggingFace 直连不通时**：
    $env:HF_ENDPOINT='https://hf-mirror.com'   # 实测镜像可用

用法:
  # 分段情绪走向（默认 6 段）：看"前悲后喜"这类设计有没有被听出来
  .venv-ml\Scripts\python.exe scripts\probe_aesthetic.py songs\45_sorrow_to_joy\sorrow_to_joy_sf.ogg
  .venv-ml\Scripts\python.exe scripts\probe_aesthetic.py <音频> --segments 8 --prompts mood
"""
import argparse
import os
import sys

os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')   # 直连不通时的常用镜像

import numpy as np                                          # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()                          # noqa: E402  控制台编码兜底

SR = 48000                                                  # CLAP 要求的采样率


def _feat(x):
    """新版 transformers 的 `get_text_features` / `get_audio_features` 可能返回
    output 对象（`BaseModelOutputWithPooling`）而不是 tensor —— 两种都接住。"""
    for k in ('pooler_output', 'text_embeds', 'audio_embeds'):
        if hasattr(x, k):
            return getattr(x, k)
    return x

# (短标签, 提示词) —— 短标签只用于表头，提示词才是喂给模型的
PROMPT_SETS = {
    # 情绪轴：**必须成组比较**（softmax 是组内相对的）
    'mood': [('sad', 'a sad and melancholic music'),
             ('happy', 'a happy and joyful music'),
             ('calm', 'a calm and peaceful music'),
             ('tense', 'a tense and dramatic music')],
    # 质量轴：**注意上文的边界** —— 这是"像不像制作精良的那类"，不是"好不好听"
    'quality': [('pro', 'a well-produced professional song'),
                ('amateur', 'a poorly produced amateur recording'),
                ('clean', 'a clean and clear mix'),
                ('muddy', 'a muddy and noisy recording')],
    # 风格轴：写歌时自查"像不像我们要的曲风"
    'style': [('galgame', 'a galgame background music'),
              ('ballad', 'a piano ballad'),
              ('upbeat', 'an upbeat pop song'),
              ('cinematic', 'an orchestral cinematic piece')],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('audio')
    ap.add_argument('--segments', type=int, default=6, help='等分几段（默认 6）')
    ap.add_argument('--prompts', default='mood',
                    help='提示词组：mood / quality / style（逗号分隔可组合）')
    a = ap.parse_args()

    import librosa
    import torch
    from transformers import ClapModel, ClapProcessor

    sets = [s.strip() for s in a.prompts.split(',') if s.strip() in PROMPT_SETS]
    if not sets:
        raise SystemExit('提示词组无效；可选：%s' % '/'.join(PROMPT_SETS))
    labels, texts = [], []
    for st in sets:
        for lab, txt in PROMPT_SETS[st]:
            labels.append(lab)
            texts.append(txt)

    print('加载模型 …（首次要下 ~1.6GB）')
    model = ClapModel.from_pretrained('laion/clap-htsat-unfused').eval()
    proc = ClapProcessor.from_pretrained('laion/clap-htsat-unfused')

    y, _sr = librosa.load(a.audio, sr=SR, mono=True)
    dur = len(y) / float(SR)
    n = max(1, a.segments)
    step = len(y) // n
    print('%s  %.1f 秒  →  %d 段' % (os.path.basename(a.audio), dur, n))

    with torch.no_grad():
        tin = proc(text=texts, return_tensors='pt', padding=True)
        tf = _feat(model.get_text_features(**tin))
        tf = tf / tf.norm(dim=-1, keepdim=True)

    scale = float(model.logit_scale_a.exp())
    print()
    print('段   时间          ' + ''.join('%-10s' % lab for lab in labels))
    for i in range(n):
        seg = y[i * step:(i + 1) * step]
        with torch.no_grad():
            ain = proc(audio=seg, sampling_rate=SR, return_tensors='pt')
            af = _feat(model.get_audio_features(**ain))
            af = af / af.norm(dim=-1, keepdim=True)
            prob = (scale * af @ tf.T).softmax(dim=-1)[0]
        t0, t1 = i * step / float(SR), (i + 1) * step / float(SR)
        print('%2d   %5.1f-%5.1fs  ' % (i + 1, t0, t1)
              + ''.join('%-10s' % ('%.3f' % float(p)) for p in prob))
    print()
    print('读法：每行是**该段在所有提示之间的相对归属**（softmax，行内和为 1）——')
    print('      比的是"更像哪一类"，不是绝对质量。跨段看**走向**才有意义。')


if __name__ == '__main__':
    sys.exit(main())
