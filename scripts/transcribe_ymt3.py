# -*- coding: utf-8 -*-
"""YourMT3+ 多乐器转录（通用版）—— 整段混音直接出 MIDI，无需先分轨。

与官方 `model_helper.transcribe()` 的唯一区别是**推理 batch 可调**（官方硬编码 8）。
本机实测（BGM35，331.9s / 66 段 / 13 解码通道）：

    bsz= 8 → 0.688 s/段      bsz=16 → 0.354 s/段      bsz=32 → 0.297 s/段（2.3 倍）
    整曲：官方版 ≈12 分钟（显存吃满 7.8GB）→ 本脚本 bsz=32 ≈100 秒（显存 1.8GB）
    产物一致性：音符 8667 → 8679（+0.1%），音域/唯一音高/时长完全相同

慢的根因不是硬件：逐段小 batch 时 GPU 利用率虚高（99%）但功耗只有 35W/115W——时间全耗在
kernel 启动开销上。加大 batch 是这里唯一有效的旋钮。

⚠ **长曲必须分组推理**（2026-09-19 · 实测 16.6 倍，**别改回"整曲一次喂"**）：
同一台机、同一个 bsz=32、同一次会话，只改"怎么喂"（BGM16 282.3s / 138 段）：

    整曲一次喂            3.92 s/段 · 总 543.8s      ← 138 段常驻显存 ≈6GB → 换出/分页
    分 5 组（≤29 段/组）  0.216 s/段 · 总 32.7s      ← 逐组搬 GPU、跑完即放
    60s 独立片段          0.224 s/段                 ← 该机上限参照

  · **音符数一字不差**（4820 / 13 通道分布相同）→ 纯提速，无精度损失；
  · **不是 GPU 降频**：跑任务时采样到 2842 MHz / 84 W / 87% / P0（满载）；
  · 组大小由 `auto_chunk()` 按「可用显存 × 0.75（留 25% 防 OOM）+ 每组 60s（速度最优）
    + 整曲段数」三者取小**自动推算**，每次运行打印依据；`DSH_YMT3_CHUNK` 可手动覆盖；
  · `--bsz auto` 也是两把尺子取小：显存总量 × 可用量 × 0.75。
  守卫 `selftest.t_ymt3_grouped_inference` 会拦"改回一次喂"。

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


# ── 显存预算：batch 与分组（2026-09-19 · 实测标定）────────────────────────
# RTX 5060 Laptop 8GB · BGM16 282s · bsz=32：
#   一次性喂 138 段        → 3.92 s/段（0.5× 实时）· 峰值 6453 MB
#   分 5 组（每组 30 段）  → 0.253 s/段（8.1× 实时）· 峰值 6040 MB
#   60s 独立片段（30 段）  → 0.224 s/段（8.9× 实时）← 本机上限
SPEED_CHUNK_SEC = 60.0      # 每组音频时长：实测这个长度最快，再大就退化
SAFETY = 0.75               # 只用**可用**显存的 75%，留 25% 防 OOM
MODEL_RESIDENT_MB = 1600    # 模型常驻（载入后的显存增量）
ACT_MULT = 8.0              # 中间激活相对"输入数据"的经验倍数（用于显存侧限组）


def pick_bsz(mode, total_mb, free_mb=None):
    """auto：按显存挑 batch —— **总量与可用量两把尺子取小**。

    · 用总量：并发任务会来抢，不能只看当前余量；
    · 用可用量 × SAFETY：实测压到 94% 会被 WDDM 分页（PCIe），反而更慢 —— 留余量更快。
    """
    if mode != "auto":
        return int(mode)
    by_total = (64 if total_mb >= 20000 else 48 if total_mb >= 12000 else
                24 if total_mb >= 7000 else 16 if total_mb >= 5000 else 8)
    if free_mb is None:
        return by_total
    budget = free_mb * SAFETY - MODEL_RESIDENT_MB
    by_free = (64 if budget >= 12000 else 32 if budget >= 6000 else
               24 if budget >= 3000 else 16 if budget >= 1500 else 8)
    return min(by_total, by_free)


def _chunk_by_mem(free_mb):
    """可用显存 → 每组段数上限（阶梯表）。

    依据：本机 8GB 卡上"30 段一组（≈60s）"的峰值约 6GB —— 也就是说
    **可用 5GB 以上就放得开 60s 那档**；越紧越往回收，最紧时一组只 4 段，
    保证"输入 + 中间激活"都能落在可用显存里（这就是"留一点防止崩溃"）。
    """
    if free_mb >= 5000:
        return 64
    if free_mb >= 3500:
        return 32
    if free_mb >= 2500:
        return 24
    if free_mb >= 1800:
        return 16
    if free_mb >= 1200:
        return 8
    return 4


def auto_chunk(free_mb, dur, seg_sec, frames, bsz):
    """**按可用显存 + 歌曲时长自动推算每组转录多少段**（并留安全余量）。

    两项依据都是实测出来的：
      · **时长**：一组喂多少音频决定速度 —— 60s 一组 = 0.253 s/段，整曲一次 = 3.92 s/段，
        所以速度档就是 `SPEED_CHUNK_SEC / 每段秒数`；
      · **可用显存**：`free_mb` 是**载入模型之后**的读数（模型那块已经扣掉了，别再扣一次），
        再乘 `SAFETY` 留 25% 余量，查 `_chunk_by_mem` 的阶梯。
    取三者（速度档 / 显存档 / 整曲段数）最小。返回 `(每组段数, 推算明细)`，明细会打印可复核。
    """
    n_total = max(1, int(round(dur / max(1e-6, seg_sec))))
    by_speed = max(1, int(round(SPEED_CHUNK_SEC / max(1e-6, seg_sec))))
    by_mem = _chunk_by_mem(free_mb * SAFETY)
    n = max(1, min(by_speed, by_mem, n_total))
    return n, dict(n_total=n_total, by_speed=by_speed, by_mem=by_mem,
                   per_seg_mb=round(frames * 4 / 1048576.0, 3),
                   budget_mb=round(free_mb * SAFETY), free_mb=free_mb, bsz=bsz)


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
    # ⚠ 2026-09-19 撤掉 `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`：
    #   同一首 BGM16 实测，加它之后 **1.915 s/段（1.1× 实时）、显存峰值 4873 MB**，
    #   而 ML.md 记录的基准（无此变量、bsz=32）是 **0.297 s/段、峰值 1777 MB** —— 慢 6.4 倍。
    #   "官方推荐"不等于在本机这个负载下更快，撤掉（要试可临时 `export`）。
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
    bsz = pick_bsz(args.bsz, total_mb, free_mb)
    print("显存 %d/%d MB · batch=%d%s" % (free_mb, total_mb, bsz,
          "（auto：总量与可用量取小）" if args.bsz == "auto" else ""), flush=True)

    # 面板守卫（硬形式）：没在跑就先拉起来 —— 见 scripts/studio_guard.py 顶部那段。
    # ⚠ 2026-09-19 补：冷启动审计（新开对话只读文档）发现"自动探活"当时只覆盖
    #   new_song/make_song/melody_gen 三个入口，**模仿路径整条都没接** ——
    #   于是新对话走仿写时，"已自动化"并不成立，面板还得靠人记得。
    #   面板是辅助通道：起不来只警告、不中断（这里用 try 兜住，守卫自身出错也不该拖垮生成）。
    try:
        import studio_guard
        studio_guard.ensure_panel()
    except Exception as _e:                                        # noqa: BLE001
        print('  （面板守卫跳过：%s）' % str(_e)[:80])
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
        # ⚠ **不要把全部段一次搬上 GPU**（2026-09-19 修正）：
        #   原来的 `segments.to("cuda")` 让 138 段的整曲**常驻约 6GB 显存** → 触发换出/分页，
        #   于是"分块推理"也救不回来（实测仍 2.146 s/段，而 30 段的 60s 片段只要 0.224 s/段）。
        #   改成**逐组搬**：显存峰值回到"模型 + 一组段"的量级，跑完即释放。
        n_seg = segments.shape[0]
        dur = audio.shape[-1] / sr_target
        print("② 音频 %.1fs → %d 段 · 切片 %.1fs" % (dur, n_seg, time.time() - t1), flush=True)

        t2 = time.time()
        # **长曲分块推理**（2026-09-19 优化 · 实测 13 倍）：
        #   把全部段一次性喂给 `inference_file` 会**超线性变慢** —— 同一台机、同一个 bsz、同一次会话：
        #     60s 片段 → **0.224 s/段**（8.9× 实时，显存峰值 3281MB）
        #     282s 整曲 → **3.92 s/段**（0.5× 实时，峰值 6453MB）—— 差 **17 倍**
        #   而且**不是 GPU 降频**：跑任务时采样到 2842 MHz / 84 W / 87% / P0（满载）。
        #   切成一小组一组分别推理，把每个 batch 的 token 数组按顺序 extend 回去，
        #   后处理（start_secs 用**全局**帧号）与写盘逻辑一字不改。
        seg_sec = frames / sr_target
        if os.environ.get('DSH_YMT3_CHUNK'):                 # 手动覆盖（调试用）
            CHUNK = int(os.environ['DSH_YMT3_CHUNK'])
            _why = {'manual': CHUNK}
        else:
            # 载入模型后**再读一次**可用显存（模型已占了常驻那块）
            _free_now = torch.cuda.mem_get_info()[0] // 1048576
            CHUNK, _why = auto_chunk(_free_now, dur, seg_sec, frames, bsz)
        groups = ([segments[i:i + CHUNK] for i in range(0, n_seg, CHUNK)]
                  if CHUNK > 0 and n_seg > CHUNK else [segments])
        pred_token_arr = []
        with torch.inference_mode():
            for g in groups:
                gg = g.to("cuda", non_blocking=True).unsqueeze(1)   # 只搬这一组
                pta, _ = model.inference_file(bsz=bsz, audio_segments=gg)
                pred_token_arr.extend(pta)
                torch.cuda.synchronize()
                del gg                                              # 立刻放掉，别累积
                torch.cuda.empty_cache()
        dt = time.time() - t2
        print("③ 推理 %.1fs（%.3f s/段，%.1f× 实时）· 显存峰值 %.0f MB · 分 %d 组（每组 ≤%d 段，"
              "约 %.1fs 音频；%s）"
              % (dt, dt / n_seg, dur / dt, torch.cuda.max_memory_allocated() / 1048576,
                 len(groups), CHUNK, CHUNK * seg_sec,
                 "手动指定" if _why.get('manual') else
                 "自动推算：可用 %d MB 预算 %d MB · 速度档 %d 段 · 显存档 %d 段"
                 % (_why.get('free_mb', 0), _why.get('budget_mb', 0),
                    _why.get('by_speed', 0), _why.get('by_mem', 0))), flush=True)

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
    try:
        import cli_utf8 as _cu
        _cu.setup()
    except Exception:
        pass
    main()
