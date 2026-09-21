# 音频大模型当"嘴替"（本地 Qwen2-Audio）—— 现状 · 用法 · 边界 · **下一步交接**

> **这一块的全部内容都在这份文档里**（用户 2026-09-21 要求"AI 内容分成一个大点分开"）——
> README / CHEATSHEET / SKILL / PITFALLS 里只留一行指针，别在那边补细节。
> **下一个对话请从 §8 接着做**；§2 是现在的状态，§6 是"为什么它还不能当验收门"。

## 1. 要做到什么（用户诉求，原话）

> "我主要是想找一个**嘴替**，因为我不可能说出我听到的所有问题让你修改"

用户**能听出问题、但说不出来**（缺术语/表达）。要的是**翻译器**不是打分器：
判据是"**它说的东西有没有用**"，不是"分准不准"。

## 2. 状态（2026-09-21）

| 项 | 状态 |
|---|---|
| 工具 | ✅ `scripts/ask_audio_critic.py`（单段问 / 逐段扫描 / `--compare` **同段同问**对照） |
| 速度 | ✅ GPU NF4 4bit：**3.0s/段**（CPU bf16 52.7s → **17.5×**；40 段 21.4 分钟 → 2.8 分钟） |
| 可复现 | ✅ 显式 `do_sample=False` 后同段三次**逐字一致**（换进程也一致） |
| 判据质量 | ⚠ **还不能当验收门**：base 8 段**全部**被判"有问题"（恒真）、0.2dB 微扰就改判定（§6） |
| 面板集成 | ❌ 只有 CLI，没接 studio |
| 与耳朵对照 | ❌ 未做（§8 第 1 条） |

## 3. 环境（怎么装起来）

- **模型**：`Qwen/Qwen2-Audio-7B-Instruct`，HF 缓存 **16G** 已在
  `C:\Users\z\.cache\huggingface\hub\models--Qwen--Qwen2-Audio-7B-Instruct`
- **跑它的 python**：`.venv-ml\Scripts\python.exe`（torch 2.11.0+cu128 · transformers 5.17.0）。
  ⚠ 主 venv **没有** torch。
- **GPU 4bit 需要的三个包**（本轮已装在 `.venv-ml`）：
  ```bash
  pip install --no-deps accelerate bitsandbytes      # 39MB wheel，Windows 有
  pip install --no-deps psutil                       # accelerate 的运行期依赖
  ```
  ⚠ 用 `--no-deps` 是为了**不动 torch/numpy**；装前快照在
  `D:\test\_tmp\music-critic\pipfreeze_before_quant.txt`（可逐条回滚）。
- **硬约束（8GB 显存）**：bf16 权重 15.5GB 装不下；NF4 4bit ≈ 4.9GB 可以，
  但要把**音频编码器留在 CPU**（`device_map={'audio_tower':'cpu','multi_modal_projector':0,'':0}`
  + `llm_int8_skip_modules=['audio_tower']`）—— 直接 `device_map='auto'` 会被 bnb 拒绝。

## 4. 用法

```powershell
$ml = ".venv-ml\Scripts\python.exe"
& $ml scripts\ask_audio_critic.py <音频> --start 55 --dur 28                  # 单段问
& $ml scripts\ask_audio_critic.py <音频> --segments 8 --json scan.json       # 逐段扫描
& $ml scripts\ask_audio_critic.py --compare a.ogg b.ogg --segments 8 --json cmp.json  # 同段同问对照
& $ml scripts\ask_audio_critic.py <音频> --segments 8 --load-4bit            # GPU 4bit（快 17×）
```

输出：每段一行 `段N [起~止秒] 耗时 | 回答`，结尾一张"段 × 版本"的判定矩阵 + JSON 落盘
（**增量写**，40 次推理禁不起中断）。

## 5. 四条硬约束（不遵守就白跑，全部实测过）

1. **单段 ≤ 30 秒** —— processor 是 `WhisperFeatureExtractor`（16k / 480000 样本），
   多喂的部分被**静默截断**（模型以为听了整段）。`segment_bounds()` 已自动夹住，
   `selftest` 的 `t_audio_critic_contracts` 用**字面量 30** 守着。
2. **参数名是 `audio=`（单数）** —— `audios=` 被 `transformers 5.17` 静默忽略：
   模型没听到音频却照样评价（坑 227）。`Critic.ask` 内置断言。
3. **必须 `do_sample=False` 才可复现** —— 模型的 `generation_config.json` 是
   `do_sample: true · temperature 0.7 · top_p 0.5 · top_k 20`，什么都不传就是每次采样：
   同段连问三次给出**三个不同位置**（59–63s / 67–70s / 72–75s）。工具**默认 greedy**，
   要多样性才加 `--sample`（那时请**多次取多数**，别用单次读数）。
4. **它只能定位"哪一段可疑"，不能判"这版比那版好"** —— 见 §6。

## 6. 实测数字（都是本轮真跑的，脚本在 §9）

### 6.1 为什么不能当 A/B 尺子
- **恒真**：口径修对后（见 6.3），base 的 **8 段全部**被判"有问题"，
  "和弦过渡生硬"几乎铺满 5 版本 × 8 段的整张表 → **没有区分度**（与坑 225"撞音 32/30 恒真"同类）。
- **对微扰敏感**：只改了 B/B2 段和弦排列的版本，**没改动的段 1** 也给出了不同答案
  （问题类型相同、时间点整体挪约 1.6s、多报一条）；而两版段 1 的音频差异只有
  **-32.4dB ≈ 0.2dB 增益差**（整曲响度归一化的副产物，人耳听不出）。
- **"改动段"与"判定变化"没有系统性对应**：只改 1 段的版本有 3 处判定变化、**全落在没改的段**；
  改 4 段的版本只有 1 处变化落在改动段。

### 6.2 速度：瓶颈在 decode，不在音频编码
| | 实测 |
|---|---|
| prefill（音频编码 + 提示，27.9s 段） | **6.8s**（占 11%；对音频长度是**饱和**的：5s→3.8s、27.9s→6.8s） |
| decode（生成，126 字） | **58.5s**（占 89%）→ **465ms/token** ≈ 有效带宽 33GB/s |
| 加线程 16→32（机器 32 逻辑核） | **0.96×**（无效，带宽已满） |
| `max_new_tokens` 400→200→120 | **完全不动**（52.7/52.7/52.8s，回答 91 字自然早停） |
| `float32` | **加载失败**（7B fp32 要 ~30GB 内存） |
| **GPU NF4 4bit** | **3.0s/段（17.5×）** ✓ 且 greedy 下三次逐字一致 |
⚠ 量化**会改变判定**（4bit 多报出一条"杂音"）→ 做 A/B 时**全部版本必须同配置**。

### 6.3 一个判据口径坑（我自己踩的）
判"这段有没有报问题"**不能**写 `'没问题' not in answer` —— 模型常输出
"**有问题**，以下是详细信息：…（末尾）没问题。"，于是**报了问题的段被判成"没问题"**
（第一版矩阵整张表因此失真）。正确顺序：**先正则抽问题类别**，抽不到再看有没有"没问题"
（`verdict()`）。这条有 selftest 断言 + 变异用例守着。

## 7. 边界：能 / 不能

| 能用它做的 | **不能**用它做的 |
|---|---|
| 大致定位"**哪一段听着可疑**"（大尺度） | 判"这版比那版好吗"（微调 A/B） |
| 生成"值得用工具复核"的线索清单 | 当验收门 / 打分 |
| 与其他证据交叉（波形、MIDI、耳朵） | 单独作为"改这里"的依据 |

**微调 A/B 的判据只能是**：样本级音频差异（`scripts/... ` 里的 A/B 差法：解码算样本差，
≤-60dB = 实质无效）+ **用户耳朵**。

## 8. 下一步（**交接：下个对话从这里接**）

按价值排序，每条都写清"要产出什么"：

1. **与耳朵做对照实验（最高价值）**：挑 5–10 段，同一段让"模型说"与"用户说"各出一份清单，
   算重合度。产出：**它到底有没有用**的可判定结论（现在只有"看起来有理"）。⚠ 需要用户配合。
2. **多次采样取多数**：`--sample` 跑 N=5，报"哪条指控在 N 次里出现几次"，
   只有稳定出现的才算线索。产出：一个"稳定线索"过滤器；顺带能量化它自身的不确定性。
3. **把段内时间映射回整曲**：工具现在给的时间是**段内相对秒**（用户听的时候要对整曲时间轴），
   且模型自己不知道段在整曲的位置 —— 需要把"段起点"加回每个指控。产出：可直接跳到问题点的清单。
4. **接进 studio 面板**：现在只有 CLI。面板里有了就能"点一段 → 出它的清单"。
   ⚠ 注意 `studio_guard` 的"面板是唯一入口"口径。
5. **换模型对比**：至少跑一个不同尺寸/家族的音频模型（Qwen2-Audio 7B 之外）看结论是否一致 ——
   若两个模型在同一段上说完全不同的话，那"模型当尺子"这条路本身要重新评估。
6. **判据层的改造（若 1–2 证明它有用）**：把"逐段列问题"改成**只报离群量**
   （与 `selfcheck.py` 同一口径：`0.2dB` 级别的差异不报、只报"这一段相对全曲明显异常"）。

## 9. 产物清单（绝对路径）

| 类别 | 路径 |
|---|---|
| **工具（已入库）** | `D:\software\skill\music-gen\scripts\ask_audio_critic.py` |
| 守卫（selftest / mutation） | `scripts\selftest.py` 的 `t_audio_critic_contracts` · `scripts\mutation_check.py` 的 2 条用例 |
| 加速实测 | `D:\test\_tmp\music-critic\bench_qwen_profile.log`（prefill/decode 分解）· `bench_gpu4bit.log`（17.5×） |
| 可复现实测 | `qwen_determinism.log`（采样 vs greedy） |
| 对照扫描结果 | `qwen_compare_scan_4bit.json`（40 段 / 2.8 分钟）· `analyze_4bit.log`（判定矩阵） |
| 稳定性验证 | `verify_scan_stability.log`（段 1 音频差 0.2dB 却改判定） |
| 旧脚本（未入库，参考） | `ask_qwen_music.py` · `qwen_issue_scan.py` · `qwen_compare_scan.py` · `bench_qwen_*.py` · `test_qwen_determinism.py` |
| 环境快照（回滚用） | `pipfreeze_before_quant.txt` |

> ⚠ `D:\test\_tmp\music-critic\` 是**临时目录**（按工作区约定可清理）——
> 需要长期保留的脚本/结果，先搬进仓库再清。
