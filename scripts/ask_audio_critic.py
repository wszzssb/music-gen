#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""ask_audio_critic.py —— 让**本地 HF 音频大模型**逐段听曲子，输出带时间戳的问题清单 / 版本对照。

定位（先说清，免得当成验收门）：**它是"线索生成器"，不是判据**。实测边界见下，
完整数字见 `PITFALLS.md` 232/233。

═══ 四条硬约束（不遵守就白跑，全部实测过）═══
  ① **单次只吃 30 秒**：processor 是 `WhisperFeatureExtractor`（16k / 480000 样本），
     多喂的部分会被**静默截断** —— 模型以为听了整段。本模块的 `segment_bounds()`
     把每段夹在 `MAX_SEC` 内，`selftest` 有断言守着（曾用 `--segments 1` 把 223 秒当一段喂进去）。
  ② **参数名是 `audio=`（单数）**：`transformers 5.17` 的 `audios=` 被**静默忽略**，
     模型没听到音频却照样评价（坑 227）。本模块内置断言。
  ③ **必须 `greedy` 才可复现**：模型的 `generation_config.json` 是
     `do_sample: true / temperature 0.7 / top_p 0.5` —— 什么都不传时**每次答案都不同**
     （同段三次给出三个不同位置，坑 232）。本工具**默认 `do_sample=False`**，
     要多样性才加 `--sample`（那时请多次取多数，别用单次读数）。
  ④ **判定会"恒真"且对微扰敏感**：base 的 8 段**全部**被判"有问题"；改动别处引起的
     0.2dB 增益差就能让答案挪位置（坑 233）。→ **它能回答"哪一段可疑"，
     不能回答"这版比那版好吗"**；后者的判据是样本级音频差异（`diff_audio_ab.py`）+ 用户耳朵。

═══ 环境（重依赖在函数内延迟 import，所以本模块能被无 torch 的自检 import）═══
  主 venv **没有** torch → 用 `.venv-ml`：
    <根>\.venv-ml\Scripts\python.exe scripts\ask_audio_critic.py <音频> --segments 8 --greedy
  CPU 上 decode 是**内存带宽**瓶颈（465ms/token ≈ 33GB/s）：加线程/缩短生成都没用，
  fp32 直接装不下。**要快就上 GPU 4bit**（实测 52.7s → 3.0s/段）：
    pip install --no-deps accelerate bitsandbytes && pip install --no-deps psutil
    ... --load-4bit      # 8GB 卡：4bit LM 进显存，音频编码器留 CPU
  ⚠ 量化会**改变判定** → 做 A/B 时全部版本必须同配置。

用法:
  python scripts\ask_audio_critic.py <音频> --start 55 --dur 28      # 单段
  python scripts\ask_audio_critic.py <音频> --segments 8 --json out.json
  python scripts\ask_audio_critic.py --compare a.ogg b.ogg c.ogg --segments 8 --json cmp.json
      # 同段同问扫描多个版本 → 输出"段 × 版本"矩阵，用于定位"改动落在哪、它有没有反应"
  python scripts\ask_audio_critic.py <音频> --segments 8 --load-4bit  # GPU 4bit
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import cli_utf8 as _cu
    _cu.setup()
except Exception:                                     # noqa: BLE001
    pass

MODEL_ID = 'Qwen/Qwen2-Audio-7B-Instruct'
MAX_SEC = 30.0                     # WhisperFeatureExtractor 的硬上限（多喂 = 静默截断，坑 227）

Q_TMPL = (
    '请非常仔细地听这段音乐（第 {a:.0f} 秒到第 {b:.0f} 秒）。'
    '列出你听到的**所有**问题，越详细越好。每个问题请给三点：'
    '① 问题是什么（例如：音符被不自然地延长 / 和弦过渡生硬 / 某声部被盖住 / '
    '节奏不稳 / 音色发假 / 旋律突然跳）；'
    '② 问题大约出现在这段的第几秒；'
    '③ 涉及哪个声部或乐器（钢琴 / 低音 / 打击乐 / 旋律 / 伴奏 / 其他）。'
    '只列问题，不要评价优点。若这段确实没问题，就回答"没问题"。'
)

# 问题类别 → 正则（用于把回答归成可比对的类别）
CATS = {
    '延长': r'延长|拖长|时值过长|长音',
    '生硬': r'生硬|突兀|不自然.*(过渡|连接)|过渡',
    '盖住': r'盖住|被掩|淹没|遮',
    '跳': r'突然(下降|跳|上升)|大跳|跳进',
    '节奏': r'节奏(不稳|混乱|突变)|忽快忽慢|停止|加速',
    '音色': r'音色(发假|怪异)|听起来假|电子',
    '噪音': r'噪音|杂音|底噪',
    '音量': r'音量(不稳|忽大忽小)|忽大忽小|太响|太轻',
}


def problem_cats(ans):
    """从回答里抽问题类别（返回集合）。"""
    return {k for k, pat in CATS.items() if re.search(pat, ans or '')}


def verdict(ans):
    """这一段算"报了问题"还是"没问题"。

    ⚠ **口径坑（踩过，`selftest` 有断言守着）**：不能用 `'没问题' not in answer` 判 ——
    模型常输出"**有问题**，以下是详细信息：…（末尾）没问题。"，
    于是**报了问题的段会被判成"没问题"**（第一版矩阵整张表因此失真）。
    正确顺序：**先抽类别**，抽到就是报问题；抽不到再看有没有"没问题"。
    """
    c = problem_cats(ans)
    if c:
        return '/'.join(sorted(c))
    return '没问题' if '没问题' in (ans or '') else '其它'


def segment_bounds(total, n, max_sec=None):
    """把 `total` 秒均分成 `n` 段，**每段不超过 `max_sec`**（超出会被静默截断）。

    返回 [(start, dur), ...]。`n` 太小时段长会被夹到 `max_sec`（而不是喂整曲）——
    这正是坑 227 的现场：`--segments 1` 曾把 223 秒整曲喂进去，模型只"听"到前 30 秒。

    ⚠ `max_sec` 默认写 `None`、在函数里取 `MAX_SEC`（**不要写成 `max_sec=MAX_SEC`**）：
    默认参数在定义时绑定，那样改 `MAX_SEC` 对调用方无效 —— 变异测试注入"上限被抬大"时
    会**打不进去**（假通过）。
    """
    max_sec = MAX_SEC if max_sec is None else max_sec
    n = max(1, int(n))
    step = float(total) / n
    out = []
    for i in range(n):
        st = i * step
        du = min(step, float(total) - st, max_sec)
        if du > 1.0:
            out.append((round(st, 3), round(du, 3)))
    return out


def load_audio(path, start=0.0, dur=MAX_SEC, sr_target=16000):
    """读成 16kHz 单声道 float32（重采样优先 librosa，缺失时用线性插值兜底）。"""
    import numpy as np
    import soundfile as sf

    info = sf.info(path)
    y, sr = sf.read(path, dtype='float32', always_2d=True,
                    start=int(start * info.samplerate),
                    stop=int(min(info.duration, start + dur) * info.samplerate))
    y = y.mean(axis=1)
    if sr != sr_target:
        try:
            import librosa
            y = librosa.resample(y, orig_sr=sr, target_sr=sr_target)
        except Exception:                             # noqa: BLE001
            n = int(round(len(y) * sr_target / float(sr)))
            idx = np.linspace(0.0, len(y) - 1.0, n)
            y = np.interp(idx, np.arange(len(y)), y).astype('float32')
    return np.ascontiguousarray(y, dtype='float32')


class Critic:
    """懒加载的音频大模型评委（torch/transformers 都在这里才 import）。

    `load_4bit=True`：bitsandbytes NF4 4bit + 自定义 `device_map`（音频编码器留 CPU、
    LM 主体放卡）。8GB 显存装不下 bf16 的 15.5GB 权重，4bit 约 4.9GB 可以；
    实测 **52.7s → 3.0s/段**。⚠ 量化会改变判定，A/B 必须同配置。
    """

    def __init__(self, device='cpu', dtype_name='bfloat16', load_4bit=False, model_id=MODEL_ID):
        import torch
        from transformers import AutoProcessor, Qwen2AudioForConditionalGeneration

        dtype = {'bfloat16': torch.bfloat16, 'float16': torch.float16,
                 'float32': torch.float32}[dtype_name]
        t0 = time.time()
        print('[i] 加载 processor …')
        self.proc = AutoProcessor.from_pretrained(model_id)
        print('[i] 加载模型（%s%s）…' % ('cuda 4bit' if load_4bit else device,
                                        '' if load_4bit else ' / ' + dtype_name))
        if load_4bit:
            from transformers import BitsAndBytesConfig
            bnb = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type='nf4',
                bnb_4bit_compute_dtype=dtype, bnb_4bit_use_double_quant=True,
                llm_int8_skip_modules=['audio_tower'],
                llm_int8_enable_fp32_cpu_offload=True)
            # 直接 device_map='auto' 会让 accelerate 把部分模块判给 CPU，bnb 随即拒绝；
            # 且 8GB 卡塞不下"4bit LM + 音频编码器"，所以编码器显式留 CPU（decode 才是瓶颈）。
            self.model = Qwen2AudioForConditionalGeneration.from_pretrained(
                model_id, quantization_config=bnb,
                device_map={'audio_tower': 'cpu', 'multi_modal_projector': 0, '': 0})
            self.device = 'cuda'
        else:
            self.model = Qwen2AudioForConditionalGeneration.from_pretrained(
                model_id, torch_dtype=dtype)
            self.model.to(device)
            self.device = device
        self.model.eval()
        print('[i] 就绪，耗时 %.1f 秒' % (time.time() - t0))

    def ask(self, wav, question, max_new_tokens=400, **gen_kwargs):
        """问一段音频。**默认 greedy**（`do_sample=False` 由调用方给，见 `main`）。"""
        import torch

        conversation = [{'role': 'user', 'content': [
            {'type': 'audio', 'audio_url': 'placeholder'},      # 真音频走 audio=（单数！）
            {'type': 'text', 'text': question}]}]
        text = self.proc.apply_chat_template(conversation, add_generation_prompt=True,
                                             tokenize=False)
        inputs = self.proc(text=text, audio=[wav], return_tensors='pt', padding=True)
        keys = [k for k in inputs if hasattr(inputs[k], 'shape')]
        if not any(('feature' in k) or ('audio' in k) for k in keys):
            raise RuntimeError(
                '音频没进 processor！拿到的键只有 %s —— 检查参数名（应为 audio= 单数）。'
                '绝不能在没听到音频的情况下让模型评价。' % keys)
        inputs = {k: (v.to(self.device) if hasattr(v, 'to') else v)
                  for k, v in inputs.items()}
        t0 = time.time()
        with torch.no_grad():
            out = self.model.generate(**inputs, max_new_tokens=max_new_tokens, **gen_kwargs)
        new = out[:, inputs['input_ids'].size(1):]
        ans = self.proc.batch_decode(new, skip_special_tokens=True,
                                     clean_up_tokenization_spaces=False)[0]
        return {'answer': ans.strip(), 'seconds': time.time() - t0}


def _scan_one(c, audio, bounds, greedy, max_new_tokens):
    rows = []
    for i, (st, du) in enumerate(bounds, 1):
        wav = load_audio(audio, st, du)
        q = Q_TMPL.format(a=st, b=st + du)
        try:
            r = c.ask(wav, q, max_new_tokens=max_new_tokens,
                      **({} if not greedy else {'do_sample': False}))
            ans, sec = r['answer'], r['seconds']
        except Exception as e:                                # noqa: BLE001
            ans, sec = '（推理失败：%s）' % str(e)[:150], 0.0
        print('  %-18s 段%d/%d [%.1f~%.1f 秒] %5.1fs | %s'
              % (os.path.basename(audio)[:18], i, len(bounds), st, st + du, sec,
                 ans.replace('\n', ' ')[:110]))
        rows.append({'seg': i, 'start': st, 'end': round(st + du, 3),
                     'answer': ans, 'seconds': sec, 'verdict': verdict(ans)})
    return rows


def main():
    ap = argparse.ArgumentParser(description='HF 音频大模型逐段听 → 问题清单 / 版本对照')
    ap.add_argument('audio', nargs='?', help='音频文件（单曲模式）')
    ap.add_argument('--compare', nargs='+', metavar='AUDIO',
                    help='多份音频做**同段同问**对照（段边界按第一份的总时长切）')
    ap.add_argument('--start', type=float, default=0.0)
    ap.add_argument('--dur', type=float, default=MAX_SEC)
    ap.add_argument('--segments', type=int, default=0, help='>1 时均分扫描（每段自动夹到 30 秒内）')
    ap.add_argument('--sample', action='store_true',
                    help='回到采样（**不可复现**）；默认 greedy')
    ap.add_argument('--load-4bit', action='store_true', help='GPU NF4 4bit（快约 17×）')
    ap.add_argument('--dtype', default='bfloat16',
                    choices=['bfloat16', 'float16', 'float32'])
    ap.add_argument('--max-new-tokens', type=int, default=400)
    ap.add_argument('--json', default='')
    a = ap.parse_args()

    files = a.compare or ([a.audio] if a.audio else [])
    if not files:
        ap.error('给一个音频，或用 --compare 列多份')
    for f in files:
        if not os.path.exists(f):
            raise SystemExit('音频不存在: %s' % f)

    import soundfile as sf
    total = sf.info(files[0]).duration
    bounds = (segment_bounds(total, a.segments) if a.segments > 1
              else segment_bounds(total, 1, max_sec=min(a.dur, MAX_SEC)))
    if a.segments > 1 and total / a.segments > MAX_SEC:
        print('!! 每段 %.1f 秒 > 单次上限 %.0f 秒：会被静默截断，请把 --segments 调到 ≥ %d'
              % (total / a.segments, MAX_SEC, int(total / MAX_SEC) + 1))
    print('=' * 84)
    print('%d 份音频 × %d 段（单段上限 %.0f 秒%s）'
          % (len(files), len(bounds), MAX_SEC,
             '' if a.sample else '；greedy 可复现'))
    print('=' * 84)

    c = Critic(dtype_name=a.dtype, load_4bit=a.load_4bit)
    out = {'model': MODEL_ID, 'dtype': a.dtype, 'load_4bit': a.load_4bit,
           'greedy': not a.sample, 'bounds': bounds, 'results': {}}
    for f in files:
        key = os.path.basename(f)
        print('\n### %s' % key)
        out['results'][key] = _scan_one(c, f, bounds, not a.sample, a.max_new_tokens)
        if a.json:                                    # 增量落盘（长扫描禁不起中断）
            with open(a.json, 'w', encoding='utf-8') as fh:
                json.dump(out, fh, ensure_ascii=False, indent=1)

    print('\n' + '=' * 84)
    print('判定矩阵（先抽问题类别，抽不到才看"没问题"）')
    print('=' * 84)
    for key, rows in out['results'].items():
        print('  %-34s %s' % (key, ' '.join('%-10s' % r['verdict'] for r in rows)))
    print('\n⚠ **线索不是判据**：实测它 base 8 段全报问题（恒真）、0.2dB 微扰就能改判定 ——')
    print('  能用它定位"哪段可疑"，**不能**用它判"这版比那版好"（见 PITFALLS 232/233）。')
    if a.json:
        print('JSON: %s' % os.path.abspath(a.json))


if __name__ == '__main__':
    sys.exit(main())
