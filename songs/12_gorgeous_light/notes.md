# 12_gorgeous_light（主题模板包：gorgeous / 华丽）

| 项目 | 值 |
|---|---|
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/gorgeous.json`） |
| 主题→风格 | film/romantic/baroque（引擎预设 gorgeous） |
| 速度·调式 | 108 BPM · A minor（模板中位） |
| 和声 | Bm7 E7 A6 A6（来源 window4） |
| 曲式 | 8 段 × 8 小节 = 64 小节 |

## 模板清单（来源可溯源；.mid 不进仓库，重建见 `fetch_midi_lib.py`）

| 模板 | 风格 | 速度 | 来源 |
|---|---|---|---|
| `baroque/Handel in the Strand.mid` | baroque | 70 | https://bitmidi.com/uploads/28665.mid |
| `baroque/Variations and Fugue on theme by Handel op24.mid` | baroque | 120 | https://bitmidi.com/uploads/28190.mid |
| `film/batman-movie-theme_en.mid` | film | 59 | https://bitmidi.com/uploads/51521.mid |
| `film/John_williams_-_Sabrina(Piano),movie-theme1995(fine-tuning-keys-piano).mid` | film | 63 | https://bitmidi.com/uploads/63322.mid |
| `film/Metal Gear Solid - Enclosure (Soundtrack).mid` | film | 108 | https://bitmidi.com/uploads/73553.mid |
| `film/doctorwho-movie.mid` | film | 120 | https://bitmidi.com/uploads/40396.mid |
| `romantic/Liszt Bach Prelude Transcription.mid` | romantic | 60 | https://bitmidi.com/uploads/30179.mid |
| `romantic/Tchaikovsky Lake Of The Swans Act 1 3mov.mid` | romantic | 70 | https://bitmidi.com/uploads/31293.mid |
| `romantic/Tchaikovsky Lake Of The Swans Act 1 1mov.mid` | romantic | 110 | https://bitmidi.com/uploads/31291.mid |
| `romantic/brahms.mid` | romantic | 160 | https://bitmidi.com/uploads/19565.mid |

## 主题旋律语言（画像 4335 音）

- 音域 [64, 98] · 3.16 音/小节 · 级进 62% · 正拍 47%

## 频谱对齐画像（**不是模板**，只用于混音对标）

- **gorgeous_mix**：6 份真实录音画像的**逐维度中位数**（多方参考，见坑 132）
  —— 响度 -17.1 dBFS · 宽度 0.635 · 质心 3480Hz

| 成员参考 | 评分 | 质心 | 来源 |
|---|---|---|---|
| `BGM09` | 0.960 | 2421 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM09.ogg） |
| `BGM13` | 0.927 | 3068 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM13.ogg） |
| `t1_BGM34` | 0.747 | 3812 | 仰望夜空的星辰（FINE DAYS） 的 Bgm 目录（真实商业混音）（<参考曲目录/仰望夜空星辰 FINE DAYS>/BGM34.ogg） |
| `fine_BGM09` | 0.723 | 3695 | 仰望夜空的星辰（FINE DAYS） 的 Bgm 目录（真实商业混音）（<参考曲目录/仰望夜空星辰 FINE DAYS>/BGM09.ogg） |
| `BGM15` | 0.711 | 3286 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM15.ogg） |
| `BGM15b` | 0.698 | 3480 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM15b.ogg） |

> 来源结构：`source` 支持 `kind='file'`（项目素材包，带文件名）与
> `kind='web'`（网络权威源，带 `url`/`license`）—— 混音参考**必须可溯源**，
> 守卫 `mix_target_aggregate` 会要求每份成员都有 `source`。

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py gorgeous        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 12_gorgeous_light --theme gorgeous --seed 7
& $py scripts\make_song.py 12_gorgeous_light --check
```

## 还没验证什么

- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）
