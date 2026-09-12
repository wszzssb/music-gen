# ML 工具链（可选，`.venv-ml`）—— 干主工具链做不到的事

> 主工具链是**纯标准库+numpy**的确定性管线，不依赖任何模型/GPU。
> 这份文档记录"我做不到、但现成工具能做到"的那几件事，以及怎么在这里用起来。
> 环境：**RTX 5060 Laptop (8GB, sm_120)** + **Python 3.13**。

## 为什么要单独的 venv

主 venv 是 **Python 3.14**（PyTorch 没有 cp314 轮子）。所以另建 `.venv-ml`，
**绝不与主 venv 混用**（主 venv 是交付管线的一部分，另一个对话也依赖它）。

```powershell
py -3.13 -m venv .venv-ml
.\.venv-ml\Scripts\python.exe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
.\.venv-ml\Scripts\python.exe -m pip install demucs matchering librosa pyloudnorm
# 验证
.\.venv-ml\Scripts\python.exe -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

## 三个现成工具，实测结论

| 工具 | 作用 | 实测 | 命令 |
|---|---|---|---|
| **Demucs 4.1**（htdemucs） | 音源分离：vocals/drums/bass/other | ✅ **决定性**：旋律扒谱调内率 **66% → 93%**；257 秒音频 **7 秒**（GPU 38× 实时） | `& $ml -m demucs -n htdemucs -o stems "<音频>"` |
| **matchering 2.0** | 按参考曲做母带匹配（频谱/响度/动态） | ✅ 解决"冲击力"那一层：峰值 **0.909→0.993**（例曲 1.042）、峰值因数 **16.6→17.8dB**（例曲 15.9）、占用率差之和 **62%→55%**。**内容问题它解决不了** | 已封成一步：`python scripts\master_match.py <成品.wav> <参考>` |
| **librosa.pyin** | 概率式单音高跟踪 | ❌ 在这个材料上**不如自己写的**（调内率 61.7%，扒到低音区） | `librosa.pyin(y, fmin=..., fmax=...)` |
| **换音源**（MuseScore_General.sf3 / Arachno.sf2） | 更好的音色/瞬态 | ⚠️ **拿不到**（见文末「音源与连续性」：GitHub/archive/HF 全超时，PyPI/npm 上的同名包都不是音源）。管道已就绪：文件丢进 `vendor/`，`find_sf2()` 自动优先用它 | 手动放到 `vendor\` 即可 |

**结论：现成工具要挑对的那一个**。分离（Demucs）是突破口，单音高跟踪用我们自己的
`melody_profile.py`（高通 + 谱白化 + 起音处取音 + 音高稳定性判据）反而更准。

## 标准流程

```powershell
$ml = "D:\software\skill\.venv-ml\Scripts\python.exe"
# ① 分离参考曲（也分离我们自己的成品，做逐声部对比）
& $ml -m demucs -n htdemucs -o stems "D:\refs\BGM33.ogg"
& $ml -m demucs -n htdemucs -o stems "songs\14_d75_pulse\pulse_sf.wav"
# ② 在 other 声部上扒旋律语言（调内率 66%→93%，这才敢用）
& .\.venv\Scripts\python.exe scripts\melody_profile.py stems\BGM33\other.flac BGM33_other --tonic G
# ③ 按画像生成旋律（级进/切分/密度/音域都按画像；强拍仍强制和弦音）
& .\.venv\Scripts\python.exe scripts\melody_gen.py songs\14_d75_pulse\song.json refs\melody\BGM33_other_melody.json --seed 7
# ④ 参考母带匹配（可选，交付前最后一步）
& $ml -c "import matchering as mg; mg.process(target='songs\\14_d75_pulse\\pulse_sf.wav', reference='stems\\_ref.wav', results=[mg.pcm24('songs\\14_d75_pulse\\pulse_matched.wav')])"
```

## 逐声部对比：这才是"像不像"的真正判据

占用率（"有声帧占比"）/ 动态范围（峰值−25 分位），前 40 秒：

```
            40   80  160  315  630 1250 2500 5000     动态(dB)
drums 例曲  69   57   33   21   15   12   55   84    29 36 40 44 48 49 31 21
drums 本曲  23   20   18   14   18   19   25   43    53 55 55 48 45 48 43 37
bass  例曲  58   62   59   43   29   14    6   22    52 50 48 52 52 49 43 34
bass  本曲  94   98   97   69   36   11    2   14    11 13 18 27 42 51 57 33
other 例曲  13   63   92   93   93   90   85   43    42 29 16 15 16 16 19 34
other 本曲  29   64   90   95   90   86   83   27    34 28 20 17 21 21 22 34
```

- **鼓：太稀、太尖**（占用 23% vs 69%，动态 53dB vs 29dB）→ 要做"密集、均匀、被压过"的鼓组
- **贝斯：被我改成了连续长音**（占用 98% vs 62%，动态 13dB vs 50dB）→ 要回到"有颗粒、有起伏"
- **中频 other：已经对齐** ✓

## 版权与边界（写在最前面）

- 用这些工具**分析**参考曲、仿写"风格/语言"→ 可以。
- **把参考曲的旋律/录音逐音复制出来当交付** → 不做（那是侵权）。
  `melody_gen.py` 生成的是**新旋律**，只是语言（音域/级进率/切分/密度）贴近参考曲。

## 音源与"连续性"：从"换音源"改为"在现有音源里补齐"（2026-09 实测）

### ① 更好的 sf2 拿不到（网络实况）

| 通道 | 结果 |
|---|---|
| GitHub / raw / jsDelivr / ghproxy | ❌ 全部超时 |
| archive.org / huggingface / keymusician(FluidR3 官方) | ❌ 连接超时 |
| **PyPI / npm** | ✅ 通，但**没有音源**：PyPI 的 `arachno` 是协程 DSL、`sf2` 是文件共享工具（**同名无关库**）；npm 的 `generaluser` 就是本机已有的 GeneralUser GS 1.471 |

管道留着：`.sf2`/`.sf3` 丢进 `vendor/`，`find_sf2()` 自动优先（名字带 mscore/arachno/timbres/fluidr3 的更优先）。

### ② 先量清楚现有音源有什么：`sf2_lib.py` / `kick_probe.py`

`python scripts/sf2_lib.py "vendor/GeneralUser GS v1.471.sf2" --drums 35,36,41,43`
（解析 SF2 的 pdta 表，列出每个鼓组每个键的**采样名与秒数**）：

| 键 | 音色 | 采样长度 |
|---|---|---|
| 35 / 36 | 底鼓 | **0.14 ~ 0.18 秒** ← 一拍 0.8 秒，垫不满 |
| 41 / 43 | 低音嗵鼓 | **0.67 / 0.60 秒** |
| 45 / 47 | 中音嗵鼓 | 0.61 / 0.62 秒 |

`kick_probe.py` 再量"40–160Hz 到底能垫多久"（渲染单音 → 带能量包络）：
41 号 673ms、43 号 604ms，而 kit0 的 36 号只有 **128ms**。

### ③ 补齐用的两个垫层：`patterns.perc_layers`（opt-in）

```json
"perc_layers": { "kick": [[41, 66, 0.7], [43, 72, 0.7]], "air": [[46, 40, 0.4]] }
```

- **kick 垫层**：在**底鼓的位置**叠低音嗵鼓 —— 位置由引擎里的 `kicks` 派生，**逐点对齐**（错位就变成两个鼓打架）。
- **air 垫层**：每十六分一个开镲，补 2.5–10kHz 的时间覆盖。
- `air` 支持多个条目**交替占子网格**（n 个条目 → 同音高间隔 n×0.25 拍）。

实测（`stem_compare.py`，整混音 mono，前 40 秒 / 主歌段；占用率 = 帧能量 > 峰值 −25dB 的帧占比）：

| 频带 | 例曲 | 修补前 | 修补后 |
|---|---|---|---|
| 40Hz | 95 / 92 | 86 / 87 | **91 / 93** |
| 80Hz | 95 / 93 | 83 / 87 | 85 / 89 |
| 2500Hz | 87 / 99 | 90 / 93 | **97 / 97** |
| 5000Hz | 88 / 99 | 81 / 78 | **97 / 91** |

**关键教训**：连续性取决于**采样长度 vs 网格间隔**，不是 MIDI 时值写多长 ——
每十六分（200ms）铺开镲能补满，改成每八分（400ms）直接掉到 84%。

### ④ 副作用与修法：末尾 15.7 秒死气

重叠的镲（0.4 拍 > 0.25 格距）让 FluidSynth 在 MIDI 结束后多渲染 **15.7 秒**的
**−72dBFS** 死气（4:42 的歌变成 5:03，且"多出来的时长"会被所有统计当成内容）。
修法：`render_midi.trim_tail()` —— **只在尾巴 >3 秒时才裁**，既有 13 首歌的正常混响尾巴
（2.0~3.7 秒）一个字节都不动。

### ⑤ 仍然没做到的（诚实清单）

- **80Hz 占用率**还差 4~6 点（89 vs 93）："又连续又有颗粒"这一层，GM 的短采样已经到顶。
- **瞬态冲击力**（峰值因数、20ms 电平起伏 σ）仍是音源问题，不是编配问题。
