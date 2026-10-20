#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""llm_generate.py —— MIDI-LLM 生成入口（**没有 LLM 写大纲时的默认路径**）

⚠ 先读 `docs/LLM-PATH.md`：
  · **第一选择**：LLM 写大纲（结构表 / 紧凑 spec）→ 本机引擎按大纲生成；
  · **本脚本**：没有文本 LLM 参与时的兜底 —— MIDI-LLM 生成 35–55 秒片段，
    再交本机引擎扩段 / 补编配 / 渲染。

用法:
  $py scripts\llm_generate.py --prompt "A solo violin piece" [--max-tokens 2046] [--out DIR]

限制:
  --max-tokens 默认 **2046**（训练长度 2048 的安全值）；**最高 3250**（≈55 秒），
  超出直接报错。4096（92.5 秒）只作实验、不接常规流程。

环境:
  MIDI_LLM_REPO    MIDI-LLM 代码仓库（默认 D:\test\_tmp\midi-llm-probe\MIDI-LLM-main）
  MIDI_LLM_MODEL   模型权重目录（默认 D:\test\_tmp\midi-llm-probe\model）
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MAX_TOKENS_CAP = 3250
DEFAULT_MAX_TOKENS = 2046
TRAIN_LEN = 2048


def main():
    ap = argparse.ArgumentParser(description='MIDI-LLM 生成（无 LLM 大纲时的兜底）')
    ap.add_argument('--prompt', required=True)
    ap.add_argument('--max-tokens', type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument('--out', default=None)
    ap.add_argument('--n-outputs', type=int, default=1)
    a = ap.parse_args()

    if a.max_tokens > MAX_TOKENS_CAP:
        print('✗ --max-tokens %d 超过上限 %d（≈55 秒）；再往上质量/稳定性无保证'
              % (a.max_tokens, MAX_TOKENS_CAP))
        return 2
    if a.max_tokens > TRAIN_LEN:
        print('⚠ --max-tokens %d > %d（训练长度）：这是超训练外推，质量无保证'
              % (a.max_tokens, TRAIN_LEN))

    print('提示：**第一选择是 LLM 写大纲**（见 docs/LLM-PATH.md）；'
          '本脚本只在没有文本 LLM 参与时使用。')

    repo = os.environ.get('MIDI_LLM_REPO',
                          r'D:\test\_tmp\midi-llm-probe\MIDI-LLM-main')
    model = os.environ.get('MIDI_LLM_MODEL',
                           r'D:\test\_tmp\midi-llm-probe\model')
    if not os.path.isdir(model):
        print('✗ 找不到模型目录：%s（用 MIDI_LLM_MODEL 指定）' % model)
        return 2
    sys.path.insert(0, repo)

    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from midi_llm.utils import save_generation, AMT_GPT2_BOS_ID, LLAMA_VOCAB_SIZE

    system = ('You are a world-class composer. Please compose some music '
              'according to the following description: ')
    tok = AutoTokenizer.from_pretrained(model, pad_token='<|eot_id|>')
    mdl = AutoModelForCausalLM.from_pretrained(model, dtype=torch.bfloat16,
                                               trust_remote_code=True).to('cuda')
    mdl.eval()

    full = system + a.prompt + ' '
    inp = tok(full, return_tensors='pt')
    input_ids = inp['input_ids']
    bos = torch.tensor([[AMT_GPT2_BOS_ID + LLAMA_VOCAB_SIZE]])
    input_ids = torch.cat([input_ids, bos], dim=1).to('cuda')
    with torch.no_grad():
        out = mdl.generate(input_ids=input_ids, do_sample=True,
                           max_new_tokens=a.max_tokens,
                           temperature=1.0, top_p=0.98,
                           num_return_sequences=a.n_outputs,
                           pad_token_id=tok.pad_token_id)
    gen = out[:, input_ids.shape[1]:] - LLAMA_VOCAB_SIZE
    d = a.out or os.path.join(os.getcwd(), 'llm_out')
    for i, tokens in enumerate(gen.cpu().tolist()):
        save_generation(tokens=tokens, prompt=a.prompt, output_dir=d,
                        generation_idx=i + 1, soundfont_path=None, synthesize=False)
    print('SAVED', d)
    return 0


if __name__ == '__main__':
    import cli_utf8 as _cu
    _cu.setup()
    sys.exit(main())
