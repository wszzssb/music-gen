# 42_imitate_b23（主题模板包：sorrow / 悲伤）

| 项目 | 值 |
|---|---|
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/sorrow.json`） |
| 主题→风格 | ballad/romantic/classical（引擎预设 ballad） |
| 速度·调式 | 110 BPM · G minor（模板中位） |
| 和声 | A7 Dsus4 Gm7 Gm7（来源 window4） |
| 曲式 | 7 段 × 8 小节 = 56 小节 |

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

- t2_BGM23：响度 -11.8 dBFS · 宽度 0.425 · 质心 5202Hz

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py sorrow        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 42_imitate_b23 --theme sorrow --seed 42
& $py scripts\make_song.py 42_imitate_b23 --check
```

## 还没验证什么

- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）
