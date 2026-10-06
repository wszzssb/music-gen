#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""ymt3_moe_transcribe.py —— **换权重转录（能力强化用）**：同一份音频，换检测器。

## 为什么需要它（不是一个新流水线）

`transcribe_ymt3.py` 把官方那套参数（`-enc perceiver-tf -atc 1 -pr 16`，非 MoE）**硬编码**，
所以"换一个更强的权重试试"这件事在现有工具里做不到。本工具只做一件事：
**用指定的实验目录 + 追加的模型参数**跑一遍转录，写出 MIDI（其余流程仍走 `transcribe_to_song`）。

## 换权重前必须先过加载自检（`moe_loadcheck.py`）

`load_state_dict(..., strict=False)` **参数猜错时不报错、只静默丢权重**（子模块退回随机初始化）
⇒ "命令 rc=0、MIDI 照出、读数照有"而模型是半随机的。**匹配率 <0.95 一律不许拿结论**。
（本机实测：现用权重 386/386 = 1.000 · MoE 权重 655/655 = 1.000）

## 用法

    <py-ml> ymt3_moe_transcribe.py <音频.wav> -o <输出目录> --name <名字> \
            --exp-dir <amt/logs/2024 下那一层目录名> [--extra "-ff moe -wf 4 ..."] [--bsz 24]
"""
import argparse
import json
import os
import sys
import time

REPO = r'D:\test\models\ymt3repo'
LIBS = r'D:\test\models\ymt3libs'
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()          # noqa: E402  GBK 控制台下打印 ✓ 会崩

BASE = ["-p", "2024", "-tk", "mc13_full_plus_256",
        "-dec", "multi-t5", "-nl", "26", "-enc", "perceiver-tf",
        "-ac", "spec", "-hop", "300", "-atc", "1", "-pr", "16"]


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('audio')
    ap.add_argument('-o', '--out', required=True)
    ap.add_argument('--name', default=None)
    ap.add_argument('--exp-dir', required=True)
    ap.add_argument('--ckpt-name', default='model.ckpt')
    ap.add_argument('--extra', default='')
    ap.add_argument('--bsz', type=int, default=24)
    a = ap.parse_args()
    name = a.name or os.path.splitext(os.path.basename(a.audio))[0]
    os.makedirs(a.out, exist_ok=True)

    sys.path.insert(0, os.path.join(REPO, 'amt', 'src'))
    sys.path.insert(0, REPO)
    if os.path.isdir(LIBS):
        sys.path.insert(0, LIBS)
    os.chdir(REPO)

    import numpy as np
    import soundfile as sf
    import torch
    import model_helper
    from utils.audio import slice_padded_array
    from utils.note2event import mix_notes
    from utils.event2note import merge_zipped_note_events_and_ties_to_notes
    from utils.utils import write_model_output_as_midi

    args = ["%s@%s" % (a.exp_dir, a.ckpt_name)] + BASE + [t for t in a.extra.split() if t]
    print('① 载入权重 %s（追加参数：%s）' % (a.exp_dir, a.extra or '（无）'), flush=True)
    t0 = time.time()
    model = model_helper.load_model_checkpoint(args=args, device='cuda')
    print('   载入 %.1fs' % (time.time() - t0), flush=True)

    y, sr = sf.read(a.audio, dtype='float32', always_2d=True)
    y = y.mean(axis=1)
    import torchaudio
    wav = torch.from_numpy(y).unsqueeze(0)
    wav = torchaudio.functional.resample(wav, sr, model.audio_cfg['sample_rate'])
    seg = slice_padded_array(wav, model.audio_cfg['input_frames'], model.audio_cfg['input_frames'])
    dev = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    segs = torch.from_numpy(seg.astype('float32')).to(dev).unsqueeze(1)
    dur = len(y) / float(sr)
    print('② 音频 %.1fs → %d 段 · 切片 %.1fs' % (dur, segs.shape[0],
                                              model.audio_cfg['input_frames'] /
                                              model.audio_cfg['sample_rate']), flush=True)
    t0 = time.time()
    pred, _ = model.inference_file(bsz=a.bsz, audio_segments=segs)
    print('③ 推理 %.1fs（%.3f s/段）' % (time.time() - t0, (time.time() - t0) / max(1, segs.shape[0])),
          flush=True)

    num_ch = model.task_manager.num_decoding_channels
    starts = [model.audio_cfg['input_frames'] * i / model.audio_cfg['sample_rate']
              for i in range(segs.shape[0])]
    per_ch = []
    for ch in range(num_ch):
        arr_ch = [arr[:, ch, :] for arr in pred]
        zipped, _lst, _err = model.task_manager.detokenize_list_batches(
            arr_ch, starts, return_events=True)
        notes_ch, _c = merge_zipped_note_events_and_ties_to_notes(zipped)
        per_ch.append(notes_ch)
    notes = mix_notes(per_ch)
    out_mid = os.path.join(a.out, name + '.mid')
    write_model_output_as_midi(notes, a.out, name, model.midi_output_inverse_vocab)
    cand = os.path.join(a.out, 'model_output', name + '.mid')
    if os.path.exists(cand) and not os.path.exists(out_mid):
        import shutil
        shutil.move(cand, out_mid)
    n = sum(len(c) for c in per_ch)
    print('④ 写出 %s · 音符 %d · 逐通道 %s' % (out_mid, n, [len(c) for c in per_ch]), flush=True)
    with open(os.path.join(a.out, '_report.json'), 'w', encoding='utf-8') as fh:
        json.dump({'exp_dir': a.exp_dir, 'extra': a.extra, 'bsz': a.bsz, 'audio': a.audio,
                   'seconds': dur, 'segments': int(segs.shape[0]), 'notes': n,
                   'per_channel': [len(c) for c in per_ch]}, fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
