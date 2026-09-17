# -*- coding: utf-8 -*-
"""YourMT3+ 多乐器转录（通用版）—— 整段混音直接出 MIDI，无需先分轨。

与官方 `model_helper.transcribe()` 的唯一区别是**推理 batch 可调**（官方硬编码 8）。
本机实测（BGM35，331.9s / 66 段 / 13 解码通道）：

    bsz= 8 → 0.688 s/段      bsz=16 → 0.354 s/段      bsz=32 → 0.297 s/段（2.3 倍）
    整曲：官方版 ≈12 分钟（显存吃满 7.8GB）→ 本脚本 bsz=32 ≈100 秒（显存 1.8GB）
    产物一致性：音符 8667 → 8679（+0.1%），音域/唯一音高/时长完全相同

慢的根因不是硬件：逐段小 batch 时 GPU 利用率虚高（99%）但功耗只有 35W/115W——时间全耗在
kernel 启动开销上。加大 batch 是这里唯一有效的旋钮。

用法：
    python scripts/transcribe_ymt3.py <音频或目录> [-o 输出目录] [--bsz 32|auto]
                                      [--name 名字] [--repo 仓库] [--weights 权重]

模型准备（一次性，见 ML.md）：
    仓库  D:\\test\\models\\ymt3repo          （HF Space mimbres/YourMT3 的 clone）
    权重  <仓库>/amt/logs/2024/<exp>/checkpoints/model.ckpt （516MB，经 hf-mirror 取得）
    依赖  .venv-ml 里的 transformers 4.45.1（放 <仓库>/../ymt3libs，YourMT3 要 4.x）
"""
import argparse
import json
import os
import shutil
import sys
import time
from collections import Counter

EXP = "mc13_256_all_cross_v6_xk5_amp0811_edr005_attend_c_full_plus_2psn_nl26_sb_b26r_800k"

MODEL_ARGS = ["%s@model.ckpt" % EXP, "-p", "2024", "-tk", "mc13_full_plus_256",
              "-dec", "multi-t5", "-nl", "26", "-enc", "perceiver-tf",
              "-ac", "spec", "-hop", "300", "-atc", "1", "-pr", "16"]


def find_repo(explicit=None):
    """定位 YourMT3 仓库：显式参数 > 环境变量 > 常见路径。"""
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("DSH_YMT3_REPO"):
        candidates.append(os.environ["DSH_YMT3_REPO"])
    candidates += [r"D:\test\models\ymt3repo", r"D:\models\ymt3repo",
                   os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor", "ymt3repo")]
    for path in candidates:
        if path and os.path.isfile(os.path.join(path, "model_helper.py")):
            return os.path.abspath(path)
    return None


def find_weights(repo, explicit=None):
    if explicit and os.path.isfile(explicit):
        return explicit
    root = os.path.join(repo, "amt", "logs", "2024", EXP, "checkpoints")
    for name in ("model.ckpt",):
        path = os.path.join(root, name)
        if os.path.isfile(path):
            return path
    return None


def find_libs(repo):
    """transformers 4.45.1 侧载目录（YourMT3 不兼容 5.x）。"""
    for path in (os.environ.get("DSH_YMT3_LIBS"), os.path.join(os.path.dirname(repo), "ymt3libs")):
        if path and os.path.isdir(path):
            return path
    return None


def pick_bsz(mode, total_mb):
    """auto：按显存总量挑 batch（用总量而非余量——并发任务也会来抢）。"""
    if mode != "auto":
        return int(mode)
    if total_mb >= 20000:
        return 64
    if total_mb >= 12000:
        return 48
    if total_mb >= 7000:
        return 32
    if total_mb >= 5000:
        return 16
    return 8


def main():
    ap = argparse.ArgumentParser(description="YourMT3+ 多乐器转录（加速版）")
    ap.add_argument("audio", help="音频文件或目录（目录则批量处理其下 *.ogg/*.wav/*.flac/*.mp3）")
    ap.add_argument("-o", "--out", default=None, help="MIDI 输出目录（默认 <音频同目录>/ymt3_out）")
    ap.add_argument("--bsz", default="auto", help="推理 batch，默认 auto（按显存自动选）")
    ap.add_argument("--name", default=None, help="单个文件时的输出名")
    ap.add_argument("--repo", default=None, help="YourMT3 仓库目录")
    ap.add_argument("--weights", default=None, help="model.ckpt 路径")
    args = ap.parse_args()

    repo = find_repo(args.repo)
    if repo is None:
        print("✗ 找不到 YourMT3 仓库（需含 model_helper.py）。用 --repo 指定，"
              "或设 DSH_YMT3_REPO。见 ML.md「YourMT3+ 转录」。", flush=True)
        sys.exit(2)
    ckpt = find_weights(repo, args.weights)
    if ckpt is None:
        print("✗ 找不到权重 %s/amt/logs/2024/%s/checkpoints/model.ckpt。"
              "见 ML.md 的下载说明（hf-mirror）。" % (repo, EXP), flush=True)
        sys.exit(2)

    libs = find_libs(repo)
    if libs:
        sys.path.insert(0, libs)          # 4.45.1 必须排在 site-packages 的 5.x 之前
    sys.path.insert(0, os.path.join(repo, "amt", "src"))
    sys.path.insert(0, repo)
    os.chdir(repo)                        # config 里 save_dir='amt/logs' 是相对路径

    import numpy as np                    # noqa: E402
    import soundfile as sf                # noqa: E402
    import torch                          # noqa: E402
    import torchaudio                     # noqa: E402

    def _load_sf(uri, *a, **k):
        y, sr = sf.read(uri, dtype="float32", always_2d=True)
        return torch.from_numpy(np.ascontiguousarray(y.T)), sr

    torchaudio.load = _load_sf            # torchaudio 2.11 走 torchcodec（未装）→ soundfile 顶上

    from model_helper import load_model_checkpoint                                     # noqa: E402
    from utils.audio import slice_padded_array                                         # noqa: E402
    from utils.note2event import mix_notes                                             # noqa: E402
    from utils.event2note import merge_zipped_note_events_and_ties_to_notes             # noqa: E402
    from utils.utils import write_model_output_as_midi                                 # noqa: E402
    import mido                                                                        # noqa: E402

    free_mb, total_mb = (x // 1048576 for x in torch.cuda.mem_get_info())
    bsz = pick_bsz(args.bsz, total_mb)
    print("显存 %d/%d MB · batch=%d%s" % (free_mb, total_mb, bsz,
          "（auto）" if args.bsz == "auto" else ""), flush=True)

    t0 = time.time()
    model = load_model_checkpoint(args=MODEL_ARGS, device="cuda")
    print("① 模型载入 %.1fs" % (time.time() - t0), flush=True)
    sr_target = model.audio_cfg["sample_rate"]
    frames = model.audio_cfg["input_frames"]

    # 输入展开（支持批量）
    exts = (".ogg", ".wav", ".flac", ".mp3", ".m4a")
    if os.path.isdir(args.audio):
        files = sorted(os.path.join(args.audio, f) for f in os.listdir(args.audio)
                       if f.lower().endswith(exts))
        out_dir = args.out or os.path.join(args.audio, "ymt3_out")
    else:
        files = [args.audio]
        out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.audio)), "ymt3_out")
    os.makedirs(out_dir, exist_ok=True)
    if not files:
        print("✗ 没有可处理的音频", flush=True)
        sys.exit(2)

    report = []
    for path in files:
        name = args.name if (args.name and len(files) == 1) else os.path.splitext(os.path.basename(path))[0]
        print("\n--- %s" % name, flush=True)

        t1 = time.time()
        audio, sr = sf.read(path, dtype="float32", always_2d=True)
        audio = torch.from_numpy(np.ascontiguousarray(audio.T))
        audio = torch.mean(audio, dim=0).unsqueeze(0)
        if sr != sr_target:
            audio = torchaudio.functional.resample(audio, sr, sr_target)
        segments = torch.from_numpy(slice_padded_array(audio.numpy(), frames, frames).astype("float32"))
        segments = segments.to("cuda").unsqueeze(1)      # 一次搬上 GPU（官方版每段各搬一次）
        n_seg = segments.shape[0]
        dur = audio.shape[-1] / sr_target
        print("② 音频 %.1fs → %d 段 · 切片 %.1fs" % (dur, n_seg, time.time() - t1), flush=True)

        t2 = time.time()
        with torch.inference_mode():
            pred_token_arr, _ = model.inference_file(bsz=bsz, audio_segments=segments)
        torch.cuda.synchronize()
        dt = time.time() - t2
        print("③ 推理 %.1fs（%.3f s/段，%.1f× 实时）· 显存峰值 %.0f MB"
              % (dt, dt / n_seg, dur / dt, torch.cuda.max_memory_allocated() / 1048576), flush=True)

        t3 = time.time()
        n_ch = model.task_manager.num_decoding_channels
        start_secs = [frames * i / sr_target for i in range(n_seg)]
        per_channel = []
        notes_per_channel = []
        n_err = Counter()
        for ch in range(n_ch):
            arr_ch = [arr[:, ch, :] for arr in pred_token_arr]
            zipped, _events, err_ch = model.task_manager.detokenize_list_batches(
                arr_ch, start_secs, return_events=True)
            notes_ch, err2 = merge_zipped_note_events_and_ties_to_notes(zipped)
            per_channel.append(len(notes_ch))
            notes_per_channel.append(notes_ch)
            for key, value in err_ch.items():
                n_err[key] += value
            for key, value in err2.items():
                n_err[key] += value
        write_model_output_as_midi(mix_notes(notes_per_channel), "./", name,
                                   model.midi_output_inverse_vocab)
        src_mid = os.path.join("./model_output", name + ".mid")
        dst = os.path.join(out_dir, name + ".mid")
        shutil.copy(src_mid, dst)

        mm = mido.MidiFile(dst)
        notes = [(m.note, m.velocity) for tr in mm.tracks for m in tr
                 if m.type == "note_on" and m.velocity > 0]
        pitches = [n[0] for n in notes]
        print("④ 后处理 %.1fs（%d 通道，音符/通道 %s）"
              % (time.time() - t3, n_ch, per_channel), flush=True)
        print("⑤ %s · 音符 %d · 音域 %d-%d · 唯一音高 %d · 总耗时 %.1fs"
              % (dst, len(notes), min(pitches, default=0), max(pitches, default=0),
                 len(set(pitches)), time.time() - t0), flush=True)
        report.append({"name": name, "midi": dst, "seconds": round(dur, 1), "segments": n_seg,
                       "notes": len(notes), "pitch_min": min(pitches, default=0),
                       "pitch_max": max(pitches, default=0),
                       "unique_pitches": len(set(pitches)), "per_channel": per_channel,
                       "infer_sec": round(dt, 1), "bsz": bsz})

    rp = os.path.join(out_dir, "_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({"bsz": bsz, "repo": repo, "weights": ckpt, "items": report}, fh,
                  ensure_ascii=False, indent=1)
    print("\n报告 → %s（%d 首）" % (rp, len(report)), flush=True)


if __name__ == "__main__":
    main()
