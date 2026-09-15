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
  # 全库情绪排序（每首均匀取 4 片 8 秒平均 —— 整曲一段只会量到开头 10 秒）
  .venv-ml\Scripts\python.exe scripts\probe_aesthetic.py --all --prompts mood --json clap.json
"""
import argparse
import glob
import os
import sys

os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')   # 直连不通时的常用镜像

import numpy as np                                          # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_utf8 as _cu; _cu.setup()                          # noqa: E402  控制台编码兜底

SR = 48000                                                  # CLAP 要求的采样率
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SEG_S = 8.0                                                 # 全库模式每片秒数
NSEG = 4                                                    # 全库模式每首取几片

# ⚠ CLAP 只吃 **前 ~10 秒**（processor max_length 480000 @48k，超了直接截断）——
# 所以「整曲一段」量到的其实是开头。全库模式因此**均匀取 NSEG 片 8 秒再平均**。


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
    ap.add_argument('audio', nargs='?', help='音频路径（--all 时省略）')
    ap.add_argument('--segments', type=int, default=6, help='等分几段（默认 6）')
    ap.add_argument('--prompts', default='mood',
                    help='提示词组：mood / quality / style（逗号分隔可组合）')
    ap.add_argument('--all', action='store_true',
                    help='全库：每首均匀取 %d 片 %.0f 秒平均后排序（整曲一段只会量到开头）'
                         % (NSEG, SEG_S))
    ap.add_argument('--sort', default='', help='全库排序键=短标签（如 sad/happy/galgame）')
    ap.add_argument('--glob', default='', help='--all 改用它取文件（例：对照权威 BGM 目录）')
    ap.add_argument('--json', default='', help='把全库结果写成 JSON（便于与 AQA 交叉算相关）')
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

    y, _sr = librosa.load(a.audio, sr=SR, mono=True) if a.audio else (None, None)

    with torch.no_grad():                 # 文本特征只算一次（--all 全库复用）
        tin = proc(text=texts, return_tensors='pt', padding=True)
        tf = _feat(model.get_text_features(**tin))
        tf = tf / tf.norm(dim=-1, keepdim=True)
    scale = float(model.logit_scale_a.exp())

    if a.all:
        files = sorted(glob.glob(a.glob or os.path.join(ROOT, 'songs', '*', '*_sf.ogg')))
        if not files:
            raise SystemExit('没找到 songs/*/*_sf.ogg')
        key = a.sort or labels[0]
        if key not in labels:
            raise SystemExit('--sort 只能是：%s' % '/'.join(labels))
        n = int(SEG_S * SR)
        rows = []
        with torch.no_grad():
            for f in files:
                yy, _r = librosa.load(f, sr=SR, mono=True)
                starts = ([0] if len(yy) <= n
                          else list(np.linspace(0, len(yy) - n, NSEG).astype(int)))
                ps = []
                for s0 in starts:
                    ain = proc(audio=yy[s0:s0 + n], sampling_rate=SR, return_tensors='pt')
                    af = _feat(model.get_audio_features(**ain))
                    af = af / af.norm(dim=-1, keepdim=True)
                    ps.append((scale * af @ tf.T).softmax(dim=-1)[0])
                # --glob 指向别处（如权威 BGM 目录）时父目录名全是同一个 → 改用文件名
                _sid = (os.path.splitext(os.path.basename(f))[0] if a.glob
                        else os.path.basename(os.path.dirname(f)))
                rows.append((_sid, torch.stack(ps).mean(0)))
        rows.sort(key=lambda r: -float(r[1][labels.index(key)]))
        if a.json:
            import json
            json.dump({sid: {lab: round(float(v[i]), 4) for i, lab in enumerate(labels)}
                       for sid, v in rows},
                      open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            print('已写 %s' % a.json)
        print()
        print('%d 首（每首 %d 片 %.0f 秒平均，按 %s 倒序）' % (len(rows), NSEG, SEG_S, key))
        print('%-26s' % '' + ''.join('%-10s' % lab for lab in labels))
        for sid, v in rows:
            print('%-26s' % sid[:26] + ''.join('%-10s' % ('%.3f' % float(p)) for p in v))
        print()
        print('读法：**成组相对**（每行和为 1）——看的是「更像哪一类」，不是绝对好坏。')
        print('      排在两端的曲子值得优先听；配合 probe_aqa.py 的 CE 交叉看。')
        return 0

    if not a.audio:
        raise SystemExit('给一个音频路径，或用 --all')
    dur = len(y) / float(SR)
    n = max(1, a.segments)
    step = len(y) // n
    print('%s  %.1f 秒  →  %d 段' % (os.path.basename(a.audio), dur, n))

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
