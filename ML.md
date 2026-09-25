# ML 工具链（可选，`.venv-ml`）—— 干主工具链做不到的事

> 主工具链是**纯标准库+numpy**的确定性管线，不依赖任何模型/GPU。
> 这份文档记录"我做不到、但现成工具能做到"的那几件事，以及怎么在这里用起来。
> 环境：**RTX 5060 Laptop (8GB, sm_120)** + **Python 3.13**。

## 为什么要单独的 venv

主 venv 是 **Python 3.14**（**截至 2026-09 上游还没发 cp314 轮子** —— 这条理由有时效，
上游一发布就该重新评估）。所以另建 `.venv-ml`，**别与主 venv 混用**
（它是交付管线的一部分，另一个对话也依赖它）。

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
| **YourMT3+**（perceiver-TF + multi-T5，2024 多乐器 SOTA） | **整段混音直接转录成 MIDI**（不用先分轨，多乐器一起出） | ✅ 已封装 `scripts/transcribe_ymt3.py`：BGM35（332s）**≈100 秒**（2026-09-19 加**分组推理**后 ≈38 秒，见下节）、音符 8679、13 个解码通道逐通道统计。⚠ **必须用加速版**：官方 `transcribe()` 硬编码 `bsz=8` → 同样一首 **12 分钟**（详见下节） | `& $ml scripts\transcribe_ymt3.py "<音频或目录>" -o <输出目录>` |
| **换音源**（MuseScore_General.sf3 / Arachno.sf2） | 更好的音色/瞬态 | ⚠️ **拿不到**（见文末「音源与连续性」：GitHub/archive/HF 全超时，PyPI/npm 上的同名包都不是音源）。管道已就绪：文件丢进 `vendor/`，`find_sf2()` 自动优先用它 | 手动放到 `vendor\` 即可 |

**结论：现成工具要挑对的那一个**。分离（Demucs）是突破口，单音高跟踪用我们自己的
`melody_profile.py`（高通 + 谱白化 + 起音处取音 + 音高稳定性判据）反而更准。

## 标准流程

```powershell
$ml = "<工具链根>/.venv-ml/Scripts/python.exe"
# ① 分离参考曲（也分离我们自己的成品，做逐声部对比）
& $ml -m demucs -n htdemucs -o stems "<参考曲目录>/BGM33.ogg"
& $ml -m demucs -n htdemucs -o stems "songs\14_d75_pulse\pulse_sf.wav"
# ② 在 other 声部上扒旋律语言（调内率 66%→93%，这才敢用）
& .\.venv\Scripts\python.exe scripts\melody_profile.py stems\BGM33\other.flac BGM33_other --tonic G
# ③ 按画像生成旋律（级进/切分/密度/音域都按画像；强拍仍强制和弦音）
& .\.venv\Scripts\python.exe scripts\melody_gen.py songs\14_d75_pulse\song.json refs\melody\BGM33_other_melody.json --seed 7
# ④ 参考母带匹配（可选，交付前最后一步）
& $ml -c "import matchering as mg; mg.process(target='songs\\14_d75_pulse\\pulse_sf.wav', reference='stems\\_ref.wav', results=[mg.pcm24('songs\\14_d75_pulse\\pulse_matched.wav')])"
```

## YourMT3+ 多乐器转录：整段混音直接出 MIDI（2026-09-17 接入）

**要扒"混合编制的整段曲子"用它**——不用先分轨，一个模型同时出多乐器音符（perceiver-TF +
multi-T5，MC13 数据集，13 个解码通道）。

```powershell
& $ml scripts\transcribe_ymt3.py "<音频或目录>" -o "<输出目录>"            # bsz 自动
& $ml scripts\transcribe_ymt3.py "<目录>" -o "<输出目录>" --bsz 32        # 指定 batch
```

**⚠ 必须用这个脚本，不要直接调官方 `model_helper.transcribe()`**：官方把 batch 硬编码成
`bsz=8`，同一首 BGM35（331.9s → 66 段）要跑 **≈12 分钟**；本脚本 `bsz=32` 只要 **≈100 秒**
（实测 60s 片段 4.2s 推理 = **14× 实时**）。

| batch | s/段 | 相对 | 显存峰值 |
|---|---|---|---|
| **8（官方）** | 0.688 | 1.00× | 1436 MB |
| 16 | 0.354 | 1.94× | 1777 MB |
| **32（本脚本 auto）** | **0.297** | **2.32×** | 1777 MB（整曲推理含缓存 ~5 GB） |

**慢的根因不是硬件**：逐段小 batch 时 GPU 利用率虚高 99%、功耗却只有 **35W/115W**——时间
全耗在 kernel 启动开销上。加大 batch 是**那次实验里**最有效的旋钮（bsz 8→32：0.688→0.297 s/段）。

### ⚠ 长曲必须**分组**推理（2026-09-19 · 实测 16.6 倍，**别再改回"一次喂"**）

同一台机、同一个 `bsz=32`、同一次会话，只改"怎么喂"：

| 喂法 | s/段 | 总耗时（BGM16 282.3s） |
|---|---|---|
| 整曲 138 段**一次**喂 | 3.92 | **543.8s** |
| 分 5 组（每组 ≤29 段 ≈59s）**逐组搬 GPU** | **0.216** | **32.7s** |
| 60s 独立片段（该机上限参照） | 0.224 | — |

**音符数一字不差**（4820 / 13 通道分布相同）→ 纯提速。
根因是"138 段常驻显存"（约 6GB → 换出/分页），**不是 GPU 降频**
（跑任务时采样到 2842 MHz / 84 W / 87% / P0，满载）。
`transcribe_ymt3.py` 现在按「**可用显存 × 0.75**（留 25% 防 OOM）+ **每组 60s**（速度最优）
+ 整曲段数」三者取小**自动推算**组大小，并把推算依据打印出来；`DSH_YMT3_CHUNK` 可手动覆盖。
守卫 `t_ymt3_grouped_inference` 读源码拦"改回一次喂"。

**`--bsz auto` 现在是两把尺子取小**：显存**总量**（并发任务会来抢）× **可用量 × 0.75**
（实测压到 94% 会被 WDDM 分页到共享显存、反而更慢）—— 8GB 卡上通常是 24。

模型准备（一次性，均已通过 hf-mirror 取得）：

```
仓库  D:\test\models\ymt3repo        # HF Space mimbres/YourMT3 的 clone
权重  <仓库>\amt\logs\2024\mc13_256_all_cross_v6_xk5_amp0811_edr005_attend_c_full_plus_2psn_nl26_sb_b26r_800k\checkpoints\model.ckpt  (516MB)
依赖  D:\test\models\ymt3libs        # transformers 4.45.1（环境里是 5.x，YourMT3 要 4.x）
```

脚本会自动找这三样（也可用 `--repo/--weights` 或环境变量 `DSH_YMT3_REPO`/`DSH_YMT3_LIBS` 覆盖）。

> **这三样怎么获得**（clone URL / pip 命令 / 三个已知坑）→ **`INSTALL.md` 的「想扒 MIDI」那节**。
> 本节只记"它们在哪儿"，装法统一放 INSTALL —— 否则新手翻到这里才发现少三样东西、还不知道去哪拿
> （2026-09-19 按新手流程实测踩到：`transcribe_ymt3.py` 的报错原本也是指回本节找"下载说明"，而本节没有）。
产物：`<输出目录>/<名字>.mid` + `_report.json`（每首的音符数/音域/唯一音高/**13 通道各自音符数**/
推理耗时/bsz），13 通道表能直接看出"哪些声部有内容"。

**已验证**：BGM35 整曲 8667→8679 音符（对官方版 +0.1%）、音域 27–103 与唯一音高 74 完全一致
——**加速无精度损失**（同模型同权重，只改 batch）。

**边界**：转录结果可以逐音还原参考曲，但**不能上传到网络**（公开发布/分发即侵权）；
本地分析、对照、学习不受限（见下「版权与边界」）。

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
- **把参考曲的旋律/录音逐音复制出来当交付** → 可以做，**但不能上传到网络**
  （公开发布/分发即是侵权）。留在本地分析、对照、学习都没问题。
  `melody_gen.py` 生成的是**新旋律**，只是语言（音域/级进率/切分/密度）贴近参考曲。

## 音源与"连续性"：从"换音源"改为"在现有音源里补齐"（2026-09 实测）

### ① 更好的 sf2 **现在拿得到**（2026-09-26 重测，**推翻了本节 2026-09 的旧结论**）

> ⚠ 旧版写的是"GitHub / archive / HF **全部超时**、PyPI/npm 上没有音源" ——
> **现已过期，别再照它放弃这条路。**

| 通道 | 2026-09-26 实测 |
|---|---|
| **`hf-mirror.com`** | ✅ **206 通**，且 **HuggingFace 上有整库音源** ← 首选 |
| **`raw.githubusercontent.com`** | ✅ 通（`setup_soundfont.py` 就是从这里下 GeneralUser 的） |
| `ghproxy.net` / `gh-proxy.com` | ✅ 通（返 404 = 路径错，不是墙） |
| `ghfast.top` / `gh.llkk.cc` / `ghproxy.cc` / `gitee` | ❌ 超时 |
| `ftp.osuosl.org`（MuseScore 官方）、`archive.org` | ❌ 超时 |
| PyPI | ✅ 通，但仍然**没有音源**（`arachno` 是协程 DSL、`sf2` 是文件共享工具，同名无关库） |

**现成的一站：HF 仓库 `NewTab/soundfonts`（56 个 GM 音源）**
```bash
# ⚠ 文件名要 URL 编码（含空格/大小写）
curl -sL "https://hf-mirror.com/NewTab/soundfonts/resolve/main/MuseScore.sf2" -o "vendor/MuseScore.sf2"
```
里面有：`MuseScore.sf2` · `ChoriumRevA.sf2` · `Crisis GM 3.51 Edit.sf2` · `E-mu Proteus GM.SF2` ·
`GalaxyStars GM-GS-88-MT.sf2` · `GraceGM2.sf2` · `Musica Theoria.sf2` · `32MBGM.sf2` ·
`GeneralUser-GS.sf2` · `Gravis Ultrasound*` · `Microsoft GS Wavetable Synth.sf2` …

**实测排名**（`dear_good_friends`，150 读数 = 10 带 × 15 段的平均绝对带差，都是 `render_midi` 默认参数）：

| 音源 | 150均值 | 中位 | 最差 | 20–40 | 40–80 | 5–10k | 10–18k |
|---|---|---|---|---|---|---|---|
| **MuseScore.sf2** | **4.33** | 3.34 | 15.6 | 6.84 | 8.70 | 3.62 | 7.24 |
| ChoriumRevA.sf2 | 4.42 | **3.02** | 17.3 | 11.76 | **2.85** | 4.02 | **5.99** |
| GeneralUser GS v1.471（原用） | 4.91 | 3.79 | 17.9 | **4.63** | 9.65 | 4.55 | 9.49 |
| Musica Theoria / E-mu Proteus / Crisis GM / GraceGM2 / GalaxyStars / 32MBGM | 5.62 ~ 6.92 | | | | | | |

⚠ **`find_sf2()` 按"文件名"排序**（`pref = ('mscore','musescore','arachno','timbres','fluidr3','sgm')`，
不在表里的按**字母序**）⇒ 名字不含关键词的候选**会被 `GeneralUser…` 压过去**：
实测 `GraceGM2.sf2` / `Musica Theoria.sf2` 的读数与 GeneralUser **一字不差**（就是没被选中）。
**要测就加前缀（`AA_`）或把 GeneralUser 挪走**；`setup_soundfont.py` 能把它下回来。

⚠⚠ **换音源之后必须重扫渲染参数**：`render.json` 的 `hp / mid_db / shelf / low / drive` 是
`make_song` autotune **对着主题画像**调的（不是对着原曲）。换音源后它们不再匹配 ——
实测同一份 MIDI 换上 `MuseScore.sf2` 后 `|RMS差|` 从 0.99 掉到 **2.35**、嘶声段从 0 变 1。
按**原曲**重扫（`render_midi.py … --hp/--mid/--shelf`）后 150 读数 **4.91 → 3.74**。
**先扫 `hp`**（对低频段影响最大：38 → 15 让 20–40Hz 的平均差从 10.96 降到 4.63，
而其余各带几乎不动），再扫 `mid`/`shelf`。

⚠ `vendor/` **不入库**（`.gitignore:11`），215MB 的音源要重新下。

管道不变：`.sf2`/`.sf3` 丢进 `vendor/`，`find_sf2()` 自动优先（名字带 mscore/arachno/timbres/fluidr3 的更优先）。

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

## 转录能力三件套（2026-09-17 固化）

从 BGM35 还原项目里提炼出来的可复用工具，**零新增依赖**（用工具链自带的
`midi_file`/`midi_probe` 解析 MIDI，音频只用 `numpy + soundfile`）。

| 脚本 | 干什么 | 关键实测 |
|---|---|---|
| `scripts/eval_transcription.py` | 转录评估：被评 MIDI 对参照 MIDI 的**音符级 F1**，逐段列出 | 自检恒等输入 F1 = **1.000**（尺子天花板）；`--shift` 可判"只是错位"还是"内容不同" |
| `scripts/ensemble_transcribe.py` | **多来源集成转录**：多条转录交叉验证后合成一份 MIDI | 单来源最优 0.333 → 集成 **0.532**；recall 0.439→0.575 |
| `scripts/extract_vocals.py` | 人声提取：差分法（细节真实）+ **只修段间过门杂音** | 段间杂音 −6.9~−8.8 dB，12 处人声/和声位置 **±0.03 dB**（零误伤） |

### 用之前先知道的几条（都是踩出来的）

1. **评估先自检**：`--self-test` 把参照自己当被评跑一遍，F1 应 ≈1.0；报数要说清天花板。
2. **判断一律逐段**，不要看全曲平均——各段节奏本就不同，平均会把差异抹平。
3. **集成里 `other`/arp 类分轨是假音主源**：被过滤掉的音里六成只有它支撑。
4. **"碎片合并"（同音高 ≤40ms 合并）会把 F1 从 0.481 打到 0.283**，那些是真实重复起音。
5. **"补漏"要克制**：把单模型在集成覆盖之外的音全收进来，实测 F1 从 0.417 掉到 0.369。
6. **人声提取的两个硬约束**：① 判据要**先中值滤波**（约 220ms）再比，否则候选帧全是断续的；
   ② 工作采样率**统一到 44.1k**（阈值是在这里校准的，96k 下同一套阈值会失效）。
7. **人声"段间过门残留"与"低音量和声"能量几乎相同**（0.0472 vs 0.0478），
   纯靠能量分不开 —— 要靠**持续时长**（段间过门是成段空缺 ≥1.5s，乐句换气 <1s）＋能量上限来分。
8. **别用"最强模型"替换全套**：htdemucs_ft + Wiener 掩码融合在人声上**输给了**最朴素的门控
   （人声被削 2.4 dB），而"AI 分离"在 3:00–3:30 比差分低 1–2.7 dB、直接丢掉和声。

## 渲染提速：并行多个文件（2026-09-17 实测）

**先搞清楚瓶颈在哪**（否则会优化错地方）：

| 环节 | 耗时（BGM35，5.5 分钟曲子） |
|---|---|
| fluidsynth 纯合成 | **3.6s**（92× 实时） |
| `render_midi.py` 全流程（含 numpy 处理 + ogg） | 20.9s |
| 标准链（`band_match` + 宽度 + RMS + ogg） | 14.9s |
| **单组总计** | **≈36s，全程单线程** |

- **`synth.cpu-cores` 对单实例渲染无效**：实测 16 核 0.94×（该选项是给"多个合成器实例"用的）；
- 瓶颈是**每个阶段都在单线程处理一个文件** → 正解是**多进程并行多个文件**。

`scripts/parallel_render.py`：5 个文件 **113.8s → 50.0s（4.97×）**，
且产物与串行**逐样本一致**（残差 0.000e+00）。用法：

```bash
& $py scripts\parallel_render.py <mid 或目录> -o <输出目录> [--jobs N] [--pipe --ref <参考音频>]
```

`--pipe` 会额外走 `band_match` 对齐参考曲频谱。文件数越多收益越大（上限 = 核数）。

### 单文件内部再省一刀：四个频域滤波合并成一次 FFT（2026-09-17）

`render_midi.py` 的链上有四个滤波（高频搁架 / 中频带提升 / 低频搁架 / 高通），
原来**各自做一遍全曲 `rfft`+`irfft`**。它们全是 LTI 的频域乘法 → **级联 = 响应相乘**，
一次变换就够：`apply_chain_np()`。

| 场景 | 旧（四次） | 新（一次） | 提速 |
|---|---|---|---|
| 默认链（搁架 + 高通） | 8.49s | 5.42s | 1.57× |
| 四项全开 | 18.09s | 7.99s | 2.26× |

**等价性证据**（不是"听起来一样"）：同一条 MIDI 同参数各渲染一遍，
**WAV 逐字节相同**（SHA256 同为 `E1BE6E3C…`，逐样本最大差 0.000e+00）；
直接对 420s 音频做链对比，最大差 5.6e-16（浮点结合律误差）。
⚠ 别拿 **ogg** 比字节：ffmpeg 的 ogg 封装每次写随机 serial，同一 WAV 两次编码 SHA 都不同
（解码 PCM 逐位相同）—— 见 PITFALLS 171。

**验收判据**：`selftest.py --fast` 的 `dsp_fft_equivalent` 必须仍然 PASS。
