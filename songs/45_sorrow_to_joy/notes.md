# 45_sorrow_to_joy（前悲后喜 · 主题模板包 sorrow）

| 项目 | 值 |
|---|---|
| 情绪路径 | **C 小调悲伤（引子 + A 段）→ 2 小节转折（Cm7→C6 同主音转换）→ C 大调欢快（B 段 + 尾声）** |
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/sorrow.json`，源 bitmidi×10） |
| 和声依据 | 前段 `D7 Gsus4 Cm7 Cm` = sorrow 包 `progressions` 的 `['II7','Vsus4','i7','i']`；后段 `C6 Am7 F6 G7` = cheerful 池 I–vi–IV–V |
| 速度·调式 | **120 BPM** · C 小调 → C 大调（手写旋律，`mode` 随段和弦走） |
| 曲式 | **5 段 × 30 小节 = 60.0 秒**：Intro(4) A(8) Bridge(2) B(8) Outro(8) |
| 编配 | 引子 perc=0（稀疏进入）；A 段轻鼓；B/Outro 全开（uku + glock + dance 鼓组） |
| 复现 | `python scripts\make_song.py 45_sorrow_to_joy`（song.json 是唯一输入） |

## 客观体检（成绩单）

| 指标 | 本曲 | 参考 sorrow_mix | 差 |
|---|---|---|---|
| 响度 RMS | -16.2 | -16.2 | **0.0** |
| 立体声宽度 | 0.369 | 0.369 | **0.000** |
| 频谱质心 | 2971 | 3068 | -97 |
| 40-80Hz | -4.0 | 0.0 | -4.0 |
| 高频节奏型 | `·◇★◇★◇◇··◇★◇★·★·` | `◇◇★·★★◇◇·★★★★★★·` | 引子 4 小节安静是设计 |

**强拍和弦贴合 100%**（21/21；`melody_health` 的自我要求）、
`probe_melody_health` 有问题 0/1、`check_song` 数据契约通过。

## 没达标的项（如实记）

- **40-80Hz 差 4.0dB**：低频重心比参考高一档（本曲最响段在 80-160）。提 `mix.Bass`
  无效——**响度归一化会吃掉音量差**（实测 60→127 只差 0.23dB，见技能 §3）。仍在
  `alignment_vs_refs` 的 8dB 门内。
- **低频节奏型不一致**（43 号同样有）：`bass_style` 的节奏型与参考画像的差异，属结构性的。
- 参考曲 114.1 BPM 与本曲 120 不同：本曲取 120 是为了兼顾"前悲"的沉稳与"后喜"的推进。

## 模板清单（来源可溯源；.mid 不进仓库，重建见 `fetch_midi_lib.py`）

| 模板 | 风格 | 速度 | 来源 |
|---|---|---|---|
| `ballad/ame ni uta u tanshikyoku - A Ballad Sung to Rain.mid` | ballad | 48 | https://bitmidi.com/uploads/5932.mid |
| `ballad/Animal Crossing - KK Ballad Aircheck.mid` | ballad | 80 | https://bitmidi.com/uploads/6841.mid |
| `ballad/BALLAD-2.MID` | ballad | 89 | https://bitmidi.com/uploads/15417.mid |
| `ballad/A-Very-Special-Love-Song.mid` | ballad | 120 | https://bitmidi.com/uploads/3173.mid |
| `classical/Bach-1.mid` | classical | 102 | https://bitmidi.com/uploads/14984.mid |
| `classical/chopin-pol53.mid` | classical | 130 | https://bitmidi.com/uploads/23778.mid |
| `classical/chopin-ballade1.mid` | classical | 150 | https://bitmidi.com/uploads/23776.mid |
| `romantic/Liszt Bach Prelude Transcription.mid` | romantic | 60 | https://bitmidi.com/uploads/30179.mid |
| `romantic/Tchaikovsky Lake Of The Swans Act 1 1mov.mid` | romantic | 110 | https://bitmidi.com/uploads/31291.mid |
| `romantic/brahms.mid` | romantic | 160 | https://bitmidi.com/uploads/19565.mid |

## 主题旋律语言（画像 6164 音）

- 音域 [48, 86] · 5.65 音/小节 · 级进 52% · 正拍 32%

## 频谱对齐画像（**不是模板**，只用于混音对标）

- **sorrow_mix**：6 份真实录音画像的**逐维度中位数**（多方参考，见坑 132）
  —— 响度 -16.2 dBFS · 宽度 0.369 · 质心 3068Hz

| 成员参考 | 评分 | 质心 | 来源 |
|---|---|---|---|
| `BGM04` | 0.850 | 2281 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM04.ogg） |
| `BGM13` | 0.784 | 3068 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM13.ogg） |
| `BGM09` | 0.774 | 2421 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM09.ogg） |
| `BGM20` | 0.709 | 2232 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM20.ogg） |
| `bgm01c` | 0.662 | 3284 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（bgm01c.ogg） |
| `fine_BGM09` | 0.609 | 3695 | 仰望夜空的星辰（FINE DAYS） 的 Bgm 目录（真实商业混音）（<参考曲目录/仰望夜空星辰 FINE DAYS>/BGM09.ogg） |

> 来源结构：`source` 支持 `kind='file'`（项目素材包，带文件名）与
> `kind='web'`（网络权威源，带 `url`/`license`）—— 混音参考**必须可溯源**，
> 守卫 `mix_target_aggregate` 会要求每份成员都有 `source`。

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py sorrow        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 45_sorrow_to_joy --theme sorrow --seed 7
& $py scripts\make_song.py 45_sorrow_to_joy --check
```

## 还没验证什么

- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）
