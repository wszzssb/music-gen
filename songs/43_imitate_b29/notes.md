# 43_imitate_b29（主题模板包：daily / 日常）

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

- t2_BGM29：响度 -14.8 dBFS · 宽度 0.521 · 质心 4463Hz

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py daily        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 43_imitate_b29 --theme daily --seed 43
& $py scripts\make_song.py 43_imitate_b29 --check
```

## 还没验证什么

- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）
