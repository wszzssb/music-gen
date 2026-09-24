# 安装与首次运行（clone 之后）

## 一条命令装好（Windows）

**双击 `setup.cmd`** —— 自动建虚拟环境、装依赖、取音源、跑自检，装完告诉你下一步看哪。
**已装过的步骤会自动跳过，重复运行安全。**

手动三步也行（与上面等价）：

```powershell
python -m venv .venv                                                          # ① 环境（Python 3.10+）
.\.venv\Scripts\python.exe -m pip install numpy soundfile imageio-ffmpeg      # ② 依赖
.\.venv\Scripts\python.exe scripts\setup_soundfont.py                         # ③ 音源
```

**一律用 `.venv\Scripts\python.exe`，别用系统 `python`**（后者可能没装 numpy）。

③ 从多个镜像下载 `fluidsynth.exe` 与 `GeneralUser GS v1.471.sf2` 到 `vendor\`；
网络不通时按脚本提示手动放进 `vendor\` 即可。**没装音源也不会一片红** —— 自检会明确报
`找不到 fluidsynth.exe，先跑 scripts/setup_soundfont.py`，其余 73 项照常通过。

## 验证环境可用

```powershell
.\.venv\Scripts\python.exe scripts\selftest.py      # 全绿 = 可用（约 30 秒；项数见输出，别照抄数字）
.\.venv\Scripts\python.exe scripts\rehearsal.py     # 彩排：5 套风格端到端（约 4 分钟）
```

> **clone 后第一次自检**：素材类项目（外部 MIDI、参考曲音频、wav 母版）**不随仓库分发**，
> 所以那几条检查会打印"跳过…（素材不随仓库分发）"而不是报错 —— 这是正常的，**不是环境坏了**。
> 自备素材（或跑一次 `make_song.py`）后它们自动生效。

## 写歌技能（可选，推荐）

仓库里带了 **`skill/bgm-studio/SKILL.md`** —— 写歌的**唯一流程**、开工前必定的 **7 件事**、
以及省 token 的纪律都在里面（`setup.cmd` 会自动把它接到 DSH 的技能目录）。手动等价于：

```powershell
mklink /J "%USERPROFILE%\.dsh\skills\bgm-studio" "<仓库路径>\skill\bgm-studio"
```

没装 DSH 也不影响用命令行：`CHEATSHEET.md` + `docs/` 覆盖了同样的命令与数据格式。

## 参考曲：自备（版权原因不随仓库分发）

`refs\` 里只有**画像**（频谱/速度/频段/节奏型统计），没有任何参考曲音频。想扒自己的曲子：

```powershell
.\.venv\Scripts\python.exe scripts\check_audio.py <你的音频>           # 先体检（能不能读 / 速度几层）
.\.venv\Scripts\python.exe scripts\profile_ref.py <你的音频> <画像名>   # 存成 refs\<画像名>.json
```

`rehearsal.py` 的「全新参考曲扒谱」那一步用环境变量 `BGM_REF_DIR` 指定参考曲目录；
**没设置就跳过该步、用 `refs\` 里已有画像继续**（不算失败）—— 所以 clone 下来直接跑彩排也能全绿。

## 想扒 MIDI（把音频转成 MIDI）？

**可选**（只写歌不用看这节）。要 Python 3.13 + 约 6GB 磁盘；有 NVIDIA 卡最快，没卡也能跑（慢一两个数量级）。

**① 装 ML 环境**（一次性，约 5 分钟）

```powershell
py -3.13 -m venv .venv-ml
.\.venv-ml\Scripts\python.exe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
.\.venv-ml\Scripts\python.exe -m pip install demucs matchering librosa pyloudnorm mido "lightning>=2.2.1" deprecated einops wandb python-dotenv mir_eval soundfile
.\.venv-ml\Scripts\python.exe -m pip install --target D:\models\ymt3libs transformers==4.45.1 tokenizers==0.20.3 "huggingface-hub<1.0"
```

- `D:\models` 随便挑个地方放；最后那条 `--target` 是给 YourMT3 专用的 **transformers 4.45.1**，
  **不动** `.venv-ml` 里自带的 5.x（脚本会自己把它 `sys.path` 前置）。
- **为什么单开一个 venv**：主 venv 是 3.14，而 PyTorch 上游还没有 cp314 的轮子。
- 后面那条 `pip install` 清单**照 `ymt3repo\requirements.txt` 抄，但别整体 `-r`** ——
  它钉 `numpy==1.26.4` 且带 `..cu113` 索引，会**重装 4GB 的旧 torch**。

**② 扒一首** —— 模型代码和权重都**自动下**（走 hf-mirror 镜像）

```powershell
$env:DSH_YMT3_LIBS = "D:\models\ymt3libs"
.\.venv-ml\Scripts\python.exe scripts\transcribe_ymt3.py "<你的音频.ogg>" --download
#   → 默认落 <曲库>\_transcribe\<名字>.mid + _report.json（逐通道音符数 —— 能看出哪些声部有内容）
#   ⚠ 默认目录是**面板认得的目录**（工具链或曲库之内）：扒完在面板点「📂 打开」选这个 .mid，
#     就在 MIDI 编辑器里（能听、能改、能导出）。用 `-o` 指到桌面/临时目录的话面板会拒绝打开。
```

第一次跑会下 **4MB 代码 + 516MB 权重**，默认放 `<工具链>\vendor\ymt3repo`（想换地方加 `--repo <路径>`）。
实测（RTX 5060 Laptop 8GB）：**5.5 分钟的曲子 44 秒**出 MIDI（8× 实时，8671 音符）。
**别直接调官方 `transcribe()`** —— 它把 batch 硬编码成 8，同一首要跑 12 分钟。

**找不到东西时**：脚本依次找 `DSH_YMT3_REPO`/`DSH_YMT3_LIBS` → `D:\test\models\ymt3repo`
（`ymt3libs` 认它的兄弟目录）→ `<工具链>\vendor\ymt3repo`；**缺依赖会直接打印三条可复制的
pip 命令**，缺权重会提示加 `--download`。

**出问题再看这里**（都实测过）：
- **别整体装 `ymt3repo\requirements.txt`** —— 它钉 `numpy==1.26.4` 且带 `..cu113` 索引，
  会**重装 4GB 的旧 torch**；按 ④ 那条只装缺的就行。
- **权重别指望 clone 带下来**：Space 把 `amt/logs/` 写进了它自己的 `.gitignore`，`git clone`
  只回 4MB 代码、`git lfs pull` 也没东西 —— 用 `--download`（它从 HF dataset 下并自动摆位）。
- 三样东西的搜索顺序：`DSH_YMT3_REPO`/`DSH_YMT3_LIBS` → `D:\test\models\ymt3repo`
  （`ymt3libs` 认它的兄弟目录）→ `<工具链>\vendor\ymt3repo`；也可 `--repo <路径>`。
- 更细的坑（project 必须是 **`2024`**、`transformers` 必须 **4.45.1**、torchcodec 已打补丁）
  → `docs\原案例实测台账（已按用户要求删除 2026-09-24） 第 33 条。

**版权**：转录结果是参考曲的**逐音复制**。本地分析/对照/学习随便用，
**不能上传到网络**（公开发布即侵权）—— 见 `ML.md` 的「版权与边界」。

> ✅ **已在新机器上端到端跑通**（2026-09-19，干净目录从零走一遍）：
> `venv-ml` + torch **95 秒** → clone 代码（4MB）→ 下权重 **1 分 43 秒** → 补依赖 → **转录 44.2 秒**
> 出 MIDI（331.9s 的曲子、8671 音符、8.2× 实时、显存峰值 4963MB），产物与已跑通那台机器
> **逐字节一致**（SHA256 `d92c9ff8…c0c8bf`）—— 同模型同权重，**可复现**。
> 全程**唯一必须走镜像**的是 ②③：`hf-mirror.com` + `HF_ENDPOINT`（官方 huggingface.co 国内下不动）。

## 仓库带什么、不带什么

| 带 | 不带（`.gitignore` 已排除，可自行生成） |
|---|---|
| 全部脚本、文档、`song.json` / `spec.json` / MIDI / `notes.md` | `.venv\`（按上面 ①②③ 自建）· `.venv-ml\`（**只有扒 MIDI 才需要**，见上一节） |
| `refs\` 里的参考曲画像（41 个 + 子目录） | `vendor\`（音源，按 ③ 获取） |
| 质量分级「很好」的曲目带**成品 ogg**（开箱可听；wav 母版跑 make_song.py 重生成） | 其余曲目的音频（跑 `make_song.py <曲目>` 重生成）；质量差的已归档到 `songs\_archive\`（不进仓库） |

## 五分钟出第一首

**① 写一份 mini spec** —— 只写「和弦走向 + 旋律骨架」，时值、和弦排列、编配全部自动推导。
存成 `my_spec.json`：

```json
{
 "name": "my_first", "bpm": 150, "style": "daily", "ref": "BGM16c",
 "desc": "我的第一首",
 "sections": [
   {"name": "A", "bars": 8, "chords": "D A Bm7 G6 D A G6 A",
    "melody": {"m": [[0,0,74],[0,2,78],[1,0,81],[1,2,85],[2,0,83],[2,2,86],
                     [3,0,79],[3,2,83],[4,0,74],[4,2,78],[5,0,81],[5,2,85],
                     [6,0,79],[6,2,86],[7,0,81],[7,2,85]]}}
 ]
}
```

旋律写成 `[小节, 拍, 音高]` 三元组，**时值自动推**到下一个音；和弦用符号写（`Bm7` / `G6` / `Fsus4` / `A/C#`），
排列自动推导。上面这份 8 小节、**16 个音**的 spec 是**实测可跑**的：每小节两个音都落在
**强拍**（4/4 的第 1、3 拍）且都是**和弦音** → 强拍零错音、旋律密度 2.0 音/小节
（自检 `melody_health` 的下限是 1.2；**只写 9 个音的话密度 1.12 会被判"音太少"** —— 这是
"骨架"与"能过判据"之间最容易踩的一格，实测过）。

**② 两条命令**：

```powershell
.\.venv\Scripts\python.exe scripts\build_song.py my_spec.json --out songs\99_my_first
.\.venv\Scripts\python.exe scripts\make_song.py 99_my_first
```

产物在 `songs\99_my_first\`：**`.mid`**（9 轨 GM，可挂任何更好的音源）+ `_sf.wav` + `_sf.ogg` + 对标成绩单。

只想先听现成的示例：`songs\08_smoke_jazz\smoke_jazz_sf.ogg`（**仓库自带这一首的成品 ogg**）。
其余曲目的音频按设计不入库 —— 想看/听哪首就跑一次
`.\.venv\Scripts\python.exe scripts\make_song.py <曲目id>` 重生成（十几秒到一分钟）。
命令全表见 `CHEATSHEET.md`；曲式与风格预设见 `docs/SONG-FORMAT.md`；
要把参考曲换成你自己的，见上面「参考曲：自备」。
