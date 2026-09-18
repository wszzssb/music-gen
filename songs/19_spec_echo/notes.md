# 16_spec_echo（主题模板包：daily / 日常）

| 项目 | 值 |
|---|---|
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/daily.json`） |
| 主题→风格 | pop/folk/anime（引擎预设 daily） |
| 速度·调式 | 128 BPM · C major（模板中位） |
| 和声 | Em7 G6 G7 C6（来源 window4） |
| 曲式 | 5 段 × 8 小节 = 40 小节 |

## 模板清单（来源可溯源；.mid 不进仓库，重建见 `fetch_midi_lib.py`）

| 模板 | 风格 | 速度 | 来源 |
|---|---|---|---|
| `anime/The-Sound-Of-Music-(From-'The-Sound-Of-Music').mid` | anime | 88 | https://bitmidi.com/uploads/102697.mid |
| `anime/Animal Crossing - Load Game.mid` | anime | 100 | https://bitmidi.com/uploads/6848.mid |
| `anime/Composition - The Anime Medley.mid` | anime | 128 | https://bitmidi.com/uploads/25321.mid |
| `anime/ABBA.Name of the game K.mid` | anime | 156 | https://bitmidi.com/uploads/3439.mid |
| `folk/I-Was-Country-When-Country-Wasn't-Cool.mid` | folk | 103 | https://bitmidi.com/uploads/59404.mid |
| `folk/A.JACKSON.Gone country.mid` | folk | 125 | https://bitmidi.com/uploads/3229.mid |
| `folk/Acid-Folk.mid` | folk | 161 | https://bitmidi.com/uploads/3715.mid |
| `pop/IGGY POP.Louie Louie.mid` | pop | 120 | https://bitmidi.com/uploads/60059.mid |
| `pop/Disco Citizens - Footprint.mid` | pop | 132 | https://bitmidi.com/uploads/39730.mid |
| `pop/Disco-Fans.mid` | pop | 158 | https://bitmidi.com/uploads/39732.mid |

## 主题旋律语言（画像 3685 音）

- 音域 [59, 91] · 3.57 音/小节 · 级进 48% · 正拍 62%

## 频谱对齐画像（**不是模板**，只用于混音对标）

- **daily_mix**：6 份真实录音画像的**逐维度中位数**（多方参考，见坑 132）
  —— 响度 -16.9 dBFS · 宽度 0.369 · 质心 3284Hz

| 成员参考 | 评分 | 质心 | 来源 |
|---|---|---|---|
| `bgm01c` | 0.948 | 3284 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（bgm01c.ogg） |
| `BGM06` | 0.772 | 1533 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM06.ogg） |
| `BGM15c` | 0.766 | 3338 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM15c.ogg） |
| `BGM04` | 0.730 | 2281 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM04.ogg） |
| `fine_BGM09` | 0.701 | 3695 | 仰望夜空的星辰（FINE DAYS） 的 Bgm 目录（真实商业混音）（<参考曲目录/仰望夜空星辰 FINE DAYS>/BGM09.ogg） |
| `BGM18` | 0.652 | 2043 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM18.ogg） |

> 来源结构：`source` 支持 `kind='file'`（项目素材包，带文件名）与
> `kind='web'`（网络权威源，带 `url`/`license`）—— 混音参考**必须可溯源**，
> 守卫 `mix_target_aggregate` 会要求每份成员都有 `source`。

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py daily        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 16_spec_echo --theme daily --seed 7
& $py scripts\make_song.py 16_spec_echo --check
```

## 还没验证什么

- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）
