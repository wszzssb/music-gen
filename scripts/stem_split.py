# -*- coding: utf-8 -*-
"""htdemucs 分轨（4-stem / 6-stem），供转录、混音与对照使用。

为什么自己包一层：torchaudio 2.11 的 `load()` 走 torchcodec（多数机器没装），
所以这里**用 soundfile 解码**、只把重采样交给 torchaudio；模型走 demucs 的 Python API，
不依赖 CLI 与 ffmpeg。

用法：
    python scripts/stem_split.py <音频> [-o 输出目录] [-m htdemucs|htdemucs_6s|both]
    # 输出：<输出目录>/<模型名>/<曲名>/{vocals,drums,bass,other[,piano,guitar]}.wav
"""
import argparse
import os
import time

import numpy as np
import soundfile as sf


def _libs():
    """**惰性**导入重依赖：工具链主 venv（.venv）里没有 torch/demucs，
    而自检的 `import_all` 会 import 本目录每个脚本 —— 顶层 import 会直接判 FAIL
    （实测踩过：`import_all → stem_split: No module named 'torch'`）。
    本脚本必须用 .venv-ml 跑，导入推迟到真正用时即可。"""
    import torch
    import torchaudio
    from demucs.apply import apply_model
    from demucs.pretrained import get_model
    return torch, torchaudio, apply_model, get_model


def load_stereo(path, sr_target):
    torch, torchaudio, _am, _gm = _libs()
    y, s = sf.read(path, dtype="float32", always_2d=True)      # (n, ch)
    wav = torch.from_numpy(np.ascontiguousarray(y.T))          # (ch, n)
    if s != sr_target:
        wav = torchaudio.functional.resample(wav, s, sr_target)
    return wav


def split_one(model_name, audio, outroot):
    torch, _ta, apply_model, get_model = _libs()
    model = get_model(model_name)
    model.eval()
    sr = model.samplerate
    wav = load_stereo(audio, sr)
    ref = wav.mean(0)
    wn = (wav - ref.mean()) / (ref.std() + 1e-8)
    t = time.time()
    with torch.no_grad():
        est = apply_model(model, wn[None], device="cuda" if torch.cuda.is_available() else "cpu",
                          shifts=1, split=True, overlap=0.25)[0]
    est = est * ref.std() + ref.mean()
    name = os.path.splitext(os.path.basename(audio))[0]
    outdir = os.path.join(outroot, model_name, name)
    os.makedirs(outdir, exist_ok=True)
    for i, src in enumerate(model.sources):
        y = est[i].cpu().numpy().T
        sf.write(os.path.join(outdir, "%s.wav" % src), y, sr, subtype="PCM_16")
    print("  %-14s %.1fs · %s → %s（%d 轨）"
          % (model_name, time.time() - t, name, outdir, len(model.sources)), flush=True)
    return outdir


def main():
    ap = argparse.ArgumentParser(description="htdemucs 分轨")
    ap.add_argument("audio", help="输入音频（ogg/wav/flac/mp3）")
    ap.add_argument("-o", "--out", default=None, help="输出根目录（默认 <音频同目录>/stems）")
    ap.add_argument("-m", "--model", default="both",
                    choices=["htdemucs", "htdemucs_6s", "both"], help="分离模型")
    a = ap.parse_args()
    outroot = a.out or os.path.join(os.path.dirname(os.path.abspath(a.audio)), "stems")
    os.makedirs(outroot, exist_ok=True)
    models = ["htdemucs", "htdemucs_6s"] if a.model == "both" else [a.model]
    print("分轨 %s → %s" % (os.path.basename(a.audio), outroot))
    for m in models:
        split_one(m, a.audio, outroot)


if __name__ == "__main__":
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    main()
