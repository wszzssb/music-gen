# -*- coding: utf-8 -*-
"""CLAP 共享库（Q3 音色识别用）

要点：
  · 本地缓存离线加载（HF_HUB_OFFLINE=1），快照目录写死成**完整**的那个
    （79b58ed... 只有 model.safetensors，缺 config/tokenizer → 不能用）
  · CLAP 要求 48kHz 单声道；本库统一 read_mono48() 负责重采样/下混
  · 打分两种口径都出：① 余弦相似度（原始）② softmax(cos * logit_scale_a) 概率
  · 另外给出「限定标签子集」的重归一化概率（自检判据要按子集读）
"""
import json
import os
import sys
import time

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

SR = 48000
SNAP = (r"C:\Users\z\.cache\huggingface\hub"
        r"\models--laion--clap-htsat-unfused"
        r"\snapshots\8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a")

# 必带 7 条（任务指定）+ 与 Q3/Q1 相关的补充候选。全程只用这一套（"同一套打分"）
CORE7 = [
    "a synthesizer playing a melody",
    "a cymbal or hi-hat percussion hit",
    "a drum kit being played",
    "a pure sine tone",
    "an acoustic piano",
    "an acoustic guitar",
    "silence",
]
EXTRA = [
    "a music box playing a melody",
    "a glockenspiel",
    "a celesta",
    "a vibraphone",
    "a xylophone",
    "a church bell",
    "a bell",
    "a marimba",
    "a harp",
    "a plucked string instrument",
    "a synthesizer pad",
    "a sawtooth synthesizer lead",
    "a square wave chiptune lead",
    "8-bit video game music",
    "an electronic dance music track",
    "a string section",
    "an electric guitar",
    "a bass guitar",
    "a snare drum",
    "a kick drum",
    "an electronic drum beat",
    "white noise",
    "a flute",
    "brass instruments",
    "a female vocal singing",
    "a male vocal singing",
]
LABELS = CORE7 + EXTRA
CORE7_IDX = list(range(len(CORE7)))


# ------------------------------------------------------------------ 日志/落盘
class Tee:
    """把 stdout 同时写进日志文件（原始输出不丢）"""

    def __init__(self, path):
        self.f = open(path, "w", encoding="utf-8", buffering=1)
        self.stdout = sys.stdout

    def write(self, s):
        self.stdout.write(s)
        self.f.write(s)

    def flush(self):
        self.stdout.flush()
        self.f.flush()

    def close(self):
        self.flush()
        self.f.close()
        sys.stdout = self.stdout


def start_log(path):
    t = Tee(path)
    sys.stdout = t
    print("# 日志开始 %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("# python %s" % sys.version.replace("\n", " "))
    print("# 日志文件 %s" % path)
    return t


def save_json(path, obj):
    def conv(o):
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        raise TypeError(repr(o))

    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=conv)
    print("# 已写 %s" % path)


# ------------------------------------------------------------------ 模型
def load_model(device=None):
    import torch
    from transformers import ClapModel, ClapProcessor

    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
    t0 = time.time()
    print("# 加载 CLAP：%s" % SNAP)
    model = ClapModel.from_pretrained(SNAP).to(device).eval()
    proc = ClapProcessor.from_pretrained(SNAP)
    ls_a = float(model.logit_scale_a.exp().item())
    ls_t = float(model.logit_scale_t.exp().item())
    print("# 加载完成 %.1fs  device=%s  logit_scale_a=%.4f logit_scale_t=%.4f"
          % (time.time() - t0, device, ls_a, ls_t))
    print("# 采样率=%d  max_length_s=%s  truncation=%s  padding=%s"
          % (proc.feature_extractor.sampling_rate,
             getattr(proc.feature_extractor, "max_length_s", "?"),
             getattr(proc.feature_extractor, "truncation", "?"),
             getattr(proc.feature_extractor, "padding", "?")))
    return model, proc, device, ls_a


# ------------------------------------------------------------------ 音频 IO
def hp_zero_phase(x, sr, fc, order=4):
    """零相位高通（频域巴特沃斯幅度响应）——用于 6kHz 高通版"""
    n = len(x)
    nf = 1 << max(8, (n - 1).bit_length())
    f = np.fft.rfftfreq(nf, 1.0 / sr)
    with np.errstate(divide="ignore", invalid="ignore"):
        g = 1.0 / np.sqrt(1.0 + (fc / np.maximum(f, 1e-9)) ** (2 * order))
    g[f <= 0] = 0.0
    return np.fft.irfft(np.fft.rfft(x, n=nf) * g, n=nf)[:n].astype(np.float64)


def read_mono48(path, start=None, end=None, hp_hz=None, peak_norm=None):
    """读 [start,end) 秒 → 48kHz 单声道 float32。返回 (wave, info)"""
    info = sf.info(path)
    sr = info.samplerate
    s = 0 if start is None else int(round(start * sr))
    n = -1 if end is None else int(round((end - start) * sr))
    x, sr2 = sf.read(path, start=s, frames=n, dtype="float64", always_2d=True)
    assert sr2 == sr
    mono = x.mean(axis=1)
    if sr != SR:
        import librosa
        mono = librosa.resample(mono, orig_sr=sr, target_sr=SR, res_type="soxr_hq")
    rms = float(np.sqrt((mono ** 2).mean())) if len(mono) else 0.0
    pk = float(np.abs(mono).max()) if len(mono) else 0.0
    if hp_hz:
        mono = hp_zero_phase(mono, SR, float(hp_hz))
    if peak_norm is not None and len(mono):
        m = float(np.abs(mono).max())
        if m > 0:
            mono = mono * (float(peak_norm) / m)
    db = lambda v: (20 * np.log10(max(v, 1e-12)))  # noqa: E731
    meta = dict(path=str(path), src_sr=int(sr), start=start, end=end,
                dur=round(len(mono) / SR, 4), hp_hz=hp_hz,
                rms_dbfs=round(db(rms), 2), peak_dbfs=round(db(pk), 2))
    return mono.astype(np.float32), meta


# ------------------------------------------------------------------ 嵌入/打分
def embed_audio(model, proc, waves, device, tag=""):
    import torch
    embs = []
    for i in range(0, len(waves), 8):
        chunk = waves[i:i + 8]
        inp = proc(audio=[np.asarray(w, dtype=np.float32) for w in chunk],
                   return_tensors="pt", sampling_rate=SR)
        inp = {k: (v.to(device) if hasattr(v, "to") else v) for k, v in inp.items()}
        with torch.inference_mode():
            out = model.get_audio_features(**inp)
        e = out.pooler_output if hasattr(out, "pooler_output") else out[0]
        embs.append(e.detach().float().cpu().numpy())
    E = np.concatenate(embs, axis=0)
    if tag:
        print("# 音频嵌入 %s: %s  (L2 范数 %.4f~%.4f)"
              % (tag, E.shape, np.linalg.norm(E, axis=1).min(),
                 np.linalg.norm(E, axis=1).max()))
    return E


def embed_text(model, proc, texts, device):
    import torch
    inp = proc(text=list(texts), return_tensors="pt", padding=True)
    inp = {k: (v.to(device) if hasattr(v, "to") else v) for k, v in inp.items()}
    with torch.inference_mode():
        out = model.get_text_features(**inp)
    E = (out.pooler_output if hasattr(out, "pooler_output") else out[0])
    return E.detach().float().cpu().numpy()


def cos_to_labels(a_emb, t_emb):
    """返回 (n_audio, n_text) 余弦（两者都已 L2 归一化）"""
    return np.asarray(a_emb) @ np.asarray(t_emb).T


def softmax(z, axis=-1):
    z = z - z.max(axis=axis, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=axis, keepdims=True)


def rank_row(cos_row, labels, logit_scale, idx=None):
    """给出排序表；idx 限定子集时按子集重归一化概率"""
    cos_row = np.asarray(cos_row)
    if idx is None:
        idx = list(range(len(labels)))
    sub = cos_row[idx]
    probs = softmax(sub * logit_scale)
    order = np.argsort(-sub)
    out = []
    for k in order:
        out.append(dict(label=labels[idx[k]], cosine=round(float(sub[k]), 4),
                        prob=round(float(probs[k]), 4)))
    return out


def print_rank(title, rows, top=None):
    print("\n  [%s]" % title)
    n = len(rows) if top is None else min(top, len(rows))
    for i, r in enumerate(rows[:n], 1):
        print("   %2d. %-42s cos=%+.4f  p=%.4f"
              % (i, r["label"], r["cosine"], r["prob"]))
