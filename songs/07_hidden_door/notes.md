# 7_hidden_door（主题模板包：mystery / 神秘）

| 项目 | 值 |
|---|---|
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/mystery.json`） |
| 主题→风格 | film/newage/classical（引擎预设 gorgeous） |
| 速度·调式 | 120 BPM · D# minor（模板中位） |
| 和声 | G#m7 A#7 D#m6 D#sus4（来源 window4） |
| 曲式 | 7 段 × 8 小节 = 56 小节 |

## 模板清单（来源可溯源；.mid 不进仓库，重建见 `fetch_midi_lib.py`）

| 模板 | 风格 | 速度 | 来源 |
|---|---|---|---|
| `classical/Bach-1.mid` | classical | 102 | https://bitmidi.com/uploads/14984.mid |
| `classical/chopin-pol53.mid` | classical | 130 | https://bitmidi.com/uploads/23778.mid |
| `classical/chopin-ballade1.mid` | classical | 150 | https://bitmidi.com/uploads/23776.mid |
| `film/batman-movie-theme_en.mid` | film | 59 | https://bitmidi.com/uploads/51521.mid |
| `film/John_williams_-_Sabrina(Piano),movie-theme1995(fine-tuning-keys-piano).mid` | film | 63 | https://bitmidi.com/uploads/63322.mid |
| `film/Metal Gear Solid - Enclosure (Soundtrack).mid` | film | 108 | https://bitmidi.com/uploads/73553.mid |
| `film/doctorwho-movie.mid` | film | 120 | https://bitmidi.com/uploads/40396.mid |
| `newage/Card Captor Sakura - Tooi Kono Machi De - from the Official Piano Solo Album.mid` | newage | 96 | https://bitmidi.com/uploads/22004.mid |
| `newage/Accadia - Blind Visions (Accadia Ambient Mix).mid` | newage | 130 | https://bitmidi.com/uploads/3624.mid |
| `newage/EarthBound - New Age Retro Hippie.mid` | newage | 180 | https://bitmidi.com/uploads/42465.mid |

## 主题旋律语言（画像 4803 音）

- 音域 [46, 82] · 5.50 音/小节 · 级进 54% · 正拍 51%

## 频谱对齐画像（**不是模板**，只用于混音对标）

- **mystery_mix**：6 份真实录音画像的**逐维度中位数**（多方参考，见坑 132）
  —— 响度 -16.2 dBFS · 宽度 0.523 · 质心 3284Hz

| 成员参考 | 评分 | 质心 | 来源 |
|---|---|---|---|
| `bgm01c` | 0.846 | 3284 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（bgm01c.ogg） |
| `BGM04` | 0.817 | 2281 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM04.ogg） |
| `fine_BGM09` | 0.793 | 3695 | 仰望夜空的星辰（FINE DAYS） 的 Bgm 目录（真实商业混音）（<参考曲目录/仰望夜空星辰 FINE DAYS>/BGM09.ogg） |
| `BGM15c` | 0.680 | 3338 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM15c.ogg） |
| `BGM13` | 0.601 | 3068 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM13.ogg） |
| `BGM09` | 0.590 | 2421 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM09.ogg） |

> 来源结构：`source` 支持 `kind='file'`（项目素材包，带文件名）与
> `kind='web'`（网络权威源，带 `url`/`license`）—— 混音参考**必须可溯源**，
> 守卫 `mix_target_aggregate` 会要求每份成员都有 `source`。

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py mystery        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 7_hidden_door --theme mystery --seed 7
& $py scripts\make_song.py 7_hidden_door --check
```

## 还没验证什么

- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）
