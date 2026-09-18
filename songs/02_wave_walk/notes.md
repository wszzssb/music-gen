# 02_wave_walk（主题模板包：seaside / 海边）

| 项目 | 值 |
|---|---|
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/seaside.json`） |
| 主题→风格 | newage/folk/pop（引擎预设 acoustic） |
| 速度·调式 | 130 BPM · A major（模板中位） |
| 和声 | C#m7 A6 D6 Amaj7（来源 window4） |
| 曲式 | 7 段 × 8 小节 = 48 小节 |

## 模板清单（来源可溯源；.mid 不进仓库，重建见 `fetch_midi_lib.py`）

| 模板 | 风格 | 速度 | 来源 |
|---|---|---|---|
| `folk/I-Was-Country-When-Country-Wasn't-Cool.mid` | folk | 103 | https://bitmidi.com/uploads/59404.mid |
| `folk/A.JACKSON.Gone country.mid` | folk | 125 | https://bitmidi.com/uploads/3229.mid |
| `folk/Acid-Folk.mid` | folk | 161 | https://bitmidi.com/uploads/3715.mid |
| `newage/Card Captor Sakura - Tooi Kono Machi De - from the Official Piano Solo Album.mid` | newage | 96 | https://bitmidi.com/uploads/22004.mid |
| `newage/Age of Empires II The Age of Kings - Menu.mid` | newage | 130 | https://bitmidi.com/uploads/4280.mid |
| `newage/Nola, Novelty piano solo.mid` | newage | 130 | https://bitmidi.com/uploads/28310.mid |
| `newage/EarthBound - New Age Retro Hippie.mid` | newage | 180 | https://bitmidi.com/uploads/42465.mid |
| `pop/IGGY POP.Louie Louie.mid` | pop | 120 | https://bitmidi.com/uploads/60059.mid |
| `pop/Disco Citizens - Footprint.mid` | pop | 132 | https://bitmidi.com/uploads/39730.mid |
| `pop/Disco-Fans.mid` | pop | 158 | https://bitmidi.com/uploads/39732.mid |

## 主题旋律语言（画像 3635 音）

- 音域 [57, 79] · 4.03 音/小节 · 级进 53% · 正拍 57%

## 频谱对齐画像（**不是模板**，只用于混音对标）

- BGM01：响度 -17.1 dBFS · 宽度 0.478 · 质心 2810Hz

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py seaside        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 02_wave_walk --theme seaside --seed 7
& $py scripts\make_song.py 02_wave_walk --check
```

## 还没验证什么

- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）
