#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""ask_music_critic.py —— **音乐理解的"描述器"**（本地 Music Flamingo 7B）。

## 它是干什么的（与 `ask_audio_critic.py` 分工不同）

| 工具 | 模型 | 用途 | 能不能判"哪版更好" |
|---|---|---|---|
| `ask_audio_critic.py` | Qwen2-Audio 7B（通用音频对话） | "哪段听着可疑"的线索清单 | ❌（价值判断，见 `docs/AUDIO-CRITIC.md` §6.1） |
| **本工具** | **Music Flamingo 7B（音乐专用）** | **"像不像 / 哪里不像"**：同段同问，比两版**描述**的差异 | ❌ 同样不判好坏 —— **只判差异** |

用户口径（2026-10-02）："**不是回答好不好听，只是要判断像不像，哪里不像**"。

## 能力标定（2026-10-02 实测，全部落在 `docs/AUDIO-CRITIC.md` §11）

| 维度 | 实测 | 能不能当判据 |
|---|---|---|
| **速度 BPM** | 8 首已知 BPM **8/8 在 5% 内**，中位误差 **0.9 BPM**（130.43/133、150/151、83.33/84…） | ✅ 可以 |
| 调性 | 部分对（`Dsus4/Gm7/A7` → 报 D minor ✓；`Gmaj7/C6/D7` → 报 G major ✓；`Em7/D7/G6` → 报 A minor ✗ 实为 E minor） | ⚠️ 参考 |
| 主奏乐器 | **合成类准**（GM 80 方波 → "Synth lead" ✓）；**管乐类系统性偏**（GM 69 英国管 / 71 单簧管 → 稳定报 "Accordion"；70 巴松 → "Clarinet"） | ⚠️ 只信大类 |
| 真假音源 | ❌ **判错**（把我们 GM 渲染说成"real studio recording"） | ❌ 别用 |
| greedy 可复现 | ✅ 同段三次**逐字一致** | — |
| 采样（`--sample`） | ❌ 5 次 **5 种答案**（比 Qwen 的单次 74% 噪声还差）→ **必须 greedy** | — |
| 单段长度 | ⚠️ `config.max_position_embeddings = 1200`：**>30 秒触发长度告警**（147 秒、60 秒都报过）；README 的"20 分钟"是**总时长**不是单次上下文 | 单段 ≤ 30 秒 |
| 速度/显存 | 8GB 卡 4bit：加载 **12~18 秒**、单段 **1.4~4.7 秒** | — |

## 用法

```powershell
$ml = "<工具链根>\.venv-ml\Scripts\python.exe"     # 主 venv 没有 torch

& $ml scripts\ask_music_critic.py <音频> --ask "问什么"            # 单段问（默认前 30 秒）
& $ml scripts\ask_music_critic.py <音频> --segments 12             # 逐段问
& $ml scripts\ask_music_critic.py --compare 参考.ogg 我的.ogg --segments 12
#   ↑ 同段同问**并排**（"哪里不像"的主用法）
& $ml scripts\ask_music_critic.py <音频> --start 55 --dur 28 --json out.json
```

⚠ 模型权重不在仓库里（15.4GB）。按下面顺序找：`--model` > 环境变量 `MF_MODEL` >
默认本地目录 `D:\test\hf-models\music-flamingo-2601-hf` > HF repo id（需联网）。
本机下载方式（hf-mirror 单连接限 5MB/s，多线程也一样）见 `docs/AUDIO-CRITIC.md` §11。

## 两条实测坑（改这个文件前先看）

1. **音频塔不能丢 CPU**：写成 `device_map={'model.audio_tower':'cpu'}` 时 accelerate 把参数
   留在 **meta device**（警告 "offloaded to the cpu" 是假象）→ `generate` 崩在
   `Tensor.item() cannot be called on meta tensors`，而且音频塔**根本没加载**（等于"没听音频
   就评价"，同 PITFALLS 227）。7B 的 4bit（≈4.5GB）+ 音频塔全上 8GB 卡是够的 → `{'': 0}`。
2. **音频必须真进 processor**：`apply_chat_template(..., tokenize=True, return_dict=True)`
   之后要检查 `input_features` 在不在，并把 `.to(model.dtype)`；`ask()` 里内置断言。
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cli_utf8 as _cu                                                  # noqa: E402
_cu.setup()
import ask_audio_critic as aac                                          # noqa: E402

# ⚠ **单段上限 30 秒**（2026-10-02 实测：60 秒与 147 秒都触发
#   "exceeded the model's predefined maximum length (1200)"）。
#   自检 `t_music_critic_contracts` 用**字面量 30** 守着这个数。
MAX_SEC = 30.0
# ⚠ 音频塔**必须在卡上**（`{'': 0}`）—— 写成 `{'model.audio_tower': 'cpu'}` 会退化成
#   meta device（见文件头"两条实测坑"）。自检断言这里不含 audio_tower。
DEVICE_MAP = {'': 0}
LOCAL_DIR = os.environ.get('MF_MODEL') or r'D:\test\hf-models\music-flamingo-2601-hf'
REPO_ID = 'nvidia/music-flamingo-2601-hf'
Q_TMPL = ('Describe this excerpt concretely and briefly: (1) which instrument carries the '
          'lead melody, (2) what the accompaniment is doing, (3) what the drums are doing. '
          'No quality judgements — just what you hear.')


def resolve_model(explicit=None):
    """模型位置：`--model` > `$MF_MODEL` > 本地目录 > HF repo id。

    ⚠ 返回 repo id 时**需要联网**（本机 `huggingface.co` 不通，只有 hf-mirror 通）——
    调用方拿到 id 后仍走 `from_pretrained`，离线环境会明确报错，不会静默用错模型。
    """
    if explicit:
        return explicit
    if LOCAL_DIR and os.path.isdir(LOCAL_DIR):
        return LOCAL_DIR
    return REPO_ID


def parse_bpm(ans):
    """从回答里抠出第一个数字（能力标定用；"130.43 BPM" → 130.43）。"""
    m = re.findall(r'\d+(?:\.\d+)?', str(ans))
    return float(m[0]) if m else None


def bounds(total, n=None, start=0.0, dur=None, max_sec=MAX_SEC):
    """切段：给了 `dur` 就单段（`n=None`），否则按 `n` 均分。**每段 ≤ max_sec。**"""
    if dur:
        return aac.single_bounds(total, start, min(float(dur), max_sec), max_sec=max_sec)
    return aac.segment_bounds(total, n or 1, max_sec=max_sec)


def ab_bounds(total_ref, total_mine, n=None, start=0.0, dur=None, max_sec=MAX_SEC):
    """A/B 逐段切分：两版**用同一组区间**（同段同问才有可比性），每段 ≤ `max_sec`。

    ⚠ 抽成独立函数是因为**踩过一次**（2026-10-02）：`--compare` 分支一开始自己算
    `step = min(时长)/n`，**绕过了 `bounds()` 的夹紧** → `--segments 2` 时每段
    **165.9 秒**，模型当场超上下文（同 PITFALLS 227 那类静默截断）。
    现在两条路径共用同一把尺子，自检 `t_music_critic_contracts` 逐条断言。

    ⚠ **2026-10-09 又修一条同族**：本函数原来**没有 `start` 形参**，单段分支写死 `0.0`
    ⇒ `--compare --start 186 --dur 28` **静默跑 0–28s** —— 我据此做"跨窗复核"时，
    两个窗其实是**同一个窗跑两遍**，差一点就写成"两窗一致"的结论。
    **与 `ask_audio_critic.single_bounds` 2026-09-21 修的那条一模一样**
    （"参数收下了但没用"）⇒ 同类错第二次，故三处一起加固：形参 · 调用点 · 守卫断言
    （含**源码断言**：调用点必须把 `start=` 传下来）。
    """
    total = min(float(total_ref), float(total_mine))
    if dur:
        return aac.single_bounds(total, start, min(float(dur), max_sec), max_sec=max_sec)
    return aac.segment_bounds(total, max(1, int(n or 1)), max_sec=max_sec)


class MusicCritic:
    """懒加载（torch/transformers 只在这里 import —— `--help` 不需要它们）。"""

    def __init__(self, model=None, load_4bit=True):
        import torch
        from transformers import AutoProcessor, MusicFlamingoForConditionalGeneration
        self.model_id = resolve_model(model)
        t0 = time.time()
        print('[i] processor …（%s）' % self.model_id, flush=True)
        self.proc = AutoProcessor.from_pretrained(self.model_id)
        kw = {}
        if load_4bit:
            from transformers import BitsAndBytesConfig
            kw['quantization_config'] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type='nf4',
                bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
            kw['device_map'] = dict(DEVICE_MAP)
            print('[i] 4bit 加载中（device_map=%s）…' % kw['device_map'], flush=True)
        else:
            kw['torch_dtype'] = torch.bfloat16
        self.model = MusicFlamingoForConditionalGeneration.from_pretrained(self.model_id, **kw)
        self.model.eval()
        print('[i] 就绪 %.1f 秒' % (time.time() - t0), flush=True)

    def ask(self, wav_path, question, wav=None, max_new_tokens=256, do_sample=False):
        """问一段。**默认 greedy**（采样实测 5 次 5 种答案，全是噪声）。"""
        import torch
        import soundfile as sf
        path = wav_path
        if wav is not None:
            path = os.path.join(os.environ.get('TEMP', HERE), '_music_critic_clip.wav')
            sf.write(path, wav, 16000)
        conv = [{'role': 'user', 'content': [
            {'type': 'text', 'text': question},
            {'type': 'audio', 'path': path}]}]
        inputs = self.proc.apply_chat_template(conv, tokenize=True,
                                               add_generation_prompt=True, return_dict=True)
        keys = list(inputs)
        if not any(('feature' in k) or ('audio' in k) for k in keys):
            raise RuntimeError('音频没进 processor！只有 %s —— 绝不能在没听到音频的情况下'
                               '让它描述音乐。' % keys)
        inputs = {k: (v.to(self.model.device) if hasattr(v, 'to') else v)
                  for k, v in inputs.items()}
        if 'input_features' in inputs:
            inputs['input_features'] = inputs['input_features'].to(self.model.dtype)
        t0 = time.time()
        with torch.no_grad():
            out = self.model.generate(**inputs, max_new_tokens=max_new_tokens,
                                      do_sample=bool(do_sample))
        ans = self.proc.batch_decode(out[:, inputs['input_ids'].shape[1]:],
                                     skip_special_tokens=True)[0]
        return {'answer': ans.strip(), 'seconds': time.time() - t0, 'keys': keys}


def scan(c, audio, bounds_, question, repeat=1, sample=False, max_new_tokens=256):
    rows = []
    for i, (st, du) in enumerate(bounds_, 1):
        # ⚠ `ask_audio_critic.load_audio` 返回**单个** float32 数组（16kHz 单声道），
        #   不是 `(wav, sr)` 二元组（2026-10-02 冒烟测试当场踩到）。
        wav = aac.load_audio(audio, st, du)
        q = question.format(a=st, b=st + du) if '{a}' in question else question
        ans, secs = [], []
        for _k in range(max(1, int(repeat))):
            r = c.ask(audio, q, wav=wav, max_new_tokens=max_new_tokens, do_sample=sample)
            ans.append(r['answer'])
            secs.append(r['seconds'])
        stable = len(set(ans)) == 1
        rows.append({'seg': i, 'start': st, 'dur': du, 'question': q,
                     'answer': ans[0], 'all': ans, 'stable': stable,
                     'seconds': secs})
        print('  段%02d [%5.1f~%5.1f 秒] %4.1fs | %s%s'
              % (i, st, st + du, sum(secs) / len(secs), ' '.join(ans[0].split())[:200],
                 '' if stable or repeat == 1 else '   ⚠ %d 次里 %d 种答案'
                 % (repeat, len(set(ans)))), flush=True)
    return rows


def main():
    ap = argparse.ArgumentParser(description='Music Flamingo 当"像不像"的描述器')
    ap.add_argument('audio', nargs='?', help='音频（wav/ogg/flac/mp3）')
    ap.add_argument('--compare', nargs=2, metavar=('参考', '我的'),
                    help='同段同问并排（"哪里不像"的主用法）')
    ap.add_argument('--ask', default=Q_TMPL, help='问题（`--compare` 时两版同问）')
    ap.add_argument('--segments', type=int, default=1, help='均分段数')
    ap.add_argument('--start', type=float, default=0.0)
    ap.add_argument('--dur', type=float, help='单段秒数（>30 会被夹到 30）')
    ap.add_argument('--repeat', type=int, default=1, help='同段问几次（看稳不稳）')
    ap.add_argument('--sample', action='store_true',
                    help='采样（❌ 实测 5 次 5 种答案；只在"看它会怎么变"时用）')
    ap.add_argument('--max-new-tokens', type=int, default=256)
    ap.add_argument('--model', help='模型目录或 HF id（默认见 resolve_model）')
    ap.add_argument('--bf16', action='store_true', help='不用 4bit（8GB 卡装不下）')
    ap.add_argument('--json', help='结果落盘')
    a = ap.parse_args()
    if not a.audio and not a.compare:
        print(__doc__)
        return 1
    import soundfile as sf
    c = MusicCritic(model=a.model, load_4bit=not a.bf16)
    out = {'model': c.model_id, 'question': a.ask, 'rows': {}}
    if a.compare:
        ref, mine = a.compare
        # ⚠ `start=a.start` **必须传**（2026-10-09）：漏了它 `--start` 就又变成"收下了没用"
        #   —— 守卫 `t_music_critic_contracts` 对**这一行**有源码断言（不只断言函数行为）。
        bnd = ab_bounds(sf.info(ref).duration, sf.info(mine).duration,
                        a.segments, start=a.start, dur=a.dur)
        print('\n=== A/B 同段同问（%d 段，每段 ≤ %.0f 秒）==='
              % (len(bnd), MAX_SEC), flush=True)
        out['rows']['ref'] = scan(c, ref, bnd, a.ask, a.repeat, a.sample, a.max_new_tokens)
        out['rows']['mine'] = scan(c, mine, bnd, a.ask, a.repeat, a.sample, a.max_new_tokens)
        print('\n=== 并排（差异自己读；它不判好坏）===', flush=True)
        for r1, r2 in zip(out['rows']['ref'], out['rows']['mine']):
            print('段%02d' % r1['seg'])
            print('   参考: %s' % ' '.join(r1['answer'].split())[:300])
            print('   我的: %s' % ' '.join(r2['answer'].split())[:300])
    else:
        total = sf.info(a.audio).duration
        bnd = bounds(total, a.segments if not a.dur else None, a.start, a.dur)
        out['rows']['one'] = scan(c, a.audio, bnd, a.ask, a.repeat, a.sample,
                                  a.max_new_tokens)
    if a.json:
        json.dump(out, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('\n[i] 写了 %s' % a.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
