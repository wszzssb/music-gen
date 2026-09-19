# 40_imitate_b35（主题模板包：night / 夜晚）

| 项目 | 值 |
|---|---|
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/night.json`） |
| 主题→风格 | newage/jazz/electronic（引擎预设 daily） |
| 速度·调式 | 140 BPM · F minor（模板中位） |
| 和声 | F7 C7 A#7 Fm7（来源 window4） |
| 曲式 | 6 段 × 8 小节 = 40 小节 |

## 模板清单（来源可溯源；.mid 不进仓库，重建见 `fetch_midi_lib.py`）

| 模板 | 风格 | 速度 | 来源 |
|---|---|---|---|
| `electronic/Celine Dion - My Heart Will Go On (Techno Remix).mid` | electronic | 130 | https://bitmidi.com/uploads/22756.mid |
| `electronic/Arrow - Back In The House (U Can See It).mid` | electronic | 140 | https://bitmidi.com/uploads/7787.mid |
| `electronic/Benny Benassi - Techno Cocain.mid` | electronic | 180 | https://bitmidi.com/uploads/17088.mid |
| `jazz/Cowboy Bebop - ELM.mid` | jazz | 58 | https://bitmidi.com/uploads/25904.mid |
| `jazz/Cowboy Bebop - Goodnight Julia.mid` | jazz | 120 | https://bitmidi.com/uploads/25905.mid |
| `jazz/Cowboy Bebop - Cat Blues.mid` | jazz | 144 | https://bitmidi.com/uploads/25903.mid |
| `jazz/Alexander's-Ragtime-Band.mid` | jazz | 162 | https://bitmidi.com/uploads/4880.mid |
| `newage/Card Captor Sakura - Tooi Kono Machi De - from the Official Piano Solo Album.mid` | newage | 96 | https://bitmidi.com/uploads/22004.mid |
| `newage/Accadia - Blind Visions (Accadia Ambient Mix).mid` | newage | 130 | https://bitmidi.com/uploads/3624.mid |
| `newage/EarthBound - New Age Retro Hippie.mid` | newage | 180 | https://bitmidi.com/uploads/42465.mid |

## 主题旋律语言（画像 3174 音）

- 音域 [55, 79] · 4.97 音/小节 · 级进 52% · 正拍 57%

## 频谱对齐画像（**不是模板**，只用于混音对标）

- BGM35：响度 -16.1 dBFS · 宽度 0.473 · 质心 3522Hz

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py night        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 40_imitate_b35 --theme night --seed 40
& $py scripts\make_song.py 40_imitate_b35 --check
```

## 还没验证什么

- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）
