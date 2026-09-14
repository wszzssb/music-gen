# 42_gtr_tender（主题模板包：tender / 温柔抒情）

| 项目 | 值 |
|---|---|
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/tender.json`） |
| 主题→风格 | ballad/romantic/pop（引擎预设 ballad） |
| 速度·调式 | 120 BPM · C major（模板中位） |
| 和声 | D7 Gsus4 Cm7 Cm（来源 window4） |
| 曲式 | 8 段 × 8 小节 = 64 小节 |

## 模板清单（来源可溯源；.mid 不进仓库，重建见 `fetch_midi_lib.py`）

| 模板 | 风格 | 速度 | 来源 |
|---|---|---|---|
| `ballad/ame ni uta u tanshikyoku - A Ballad Sung to Rain.mid` | ballad | 48 | https://bitmidi.com/uploads/5932.mid |
| `ballad/Animal Crossing - KK Ballad Aircheck.mid` | ballad | 80 | https://bitmidi.com/uploads/6841.mid |
| `ballad/BALLAD-2.MID` | ballad | 89 | https://bitmidi.com/uploads/15417.mid |
| `ballad/A-Very-Special-Love-Song.mid` | ballad | 120 | https://bitmidi.com/uploads/3173.mid |
| `pop/IGGY POP.Louie Louie.mid` | pop | 120 | https://bitmidi.com/uploads/60059.mid |
| `pop/Disco Citizens - Footprint.mid` | pop | 132 | https://bitmidi.com/uploads/39730.mid |
| `pop/Disco-Fans.mid` | pop | 158 | https://bitmidi.com/uploads/39732.mid |
| `romantic/Liszt Bach Prelude Transcription.mid` | romantic | 60 | https://bitmidi.com/uploads/30179.mid |
| `romantic/Tchaikovsky Lake Of The Swans Act 1 1mov.mid` | romantic | 110 | https://bitmidi.com/uploads/31291.mid |
| `romantic/brahms.mid` | romantic | 160 | https://bitmidi.com/uploads/19565.mid |

## 主题旋律语言（画像 3978 音）

- 音域 [59, 93] · 4.37 音/小节 · 级进 56% · 正拍 41%

## 频谱对齐画像（**不是模板**，只用于混音对标）

- **tender_mix**：6 份真实录音画像的**逐维度中位数**（多方参考，见坑 132）
  —— 响度 -16.9 dBFS · 宽度 0.369 · 质心 3284Hz

| 成员参考 | 评分 | 质心 | 来源 |
|---|---|---|---|
| `BGM04` | 0.877 | 2281 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM04.ogg） |
| `bgm01c` | 0.801 | 3284 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（bgm01c.ogg） |
| `fine_BGM09` | 0.737 | 3695 | 仰望夜空的星辰（FINE DAYS） 的 Bgm 目录（真实商业混音）（<参考曲目录/仰望夜空星辰 FINE DAYS>/BGM09.ogg） |
| `BGM13` | 0.643 | 3068 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM13.ogg） |
| `BGM06` | 0.625 | 1533 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM06.ogg） |
| `BGM15c` | 0.620 | 3338 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM15c.ogg） |

> 来源结构：`source` 支持 `kind='file'`（项目素材包，带文件名）与
> `kind='web'`（网络权威源，带 `url`/`license`）—— 混音参考**必须可溯源**，
> 守卫 `mix_target_aggregate` 会要求每份成员都有 `source`。

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py tender        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 42_gtr_tender --theme tender --seed 5
& $py scripts\make_song.py 42_gtr_tender --check
```

## 还没验证什么

- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）
