# 1_village_road（主题模板包：folk_tale / 民谣叙事）

| 项目 | 值 |
|---|---|
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/folk_tale.json`） |
| 主题→风格 | folk/blues/ballad（引擎预设 ballad） |
| 速度·调式 | 123 BPM · C major（模板中位） |
| 和声 | Cmaj7 F6 Am6 Dm6（来源 window4） |
| 曲式 | 7 段 × 8 小节 = 48 小节 |

## 模板清单（来源可溯源；.mid 不进仓库，重建见 `fetch_midi_lib.py`）

| 模板 | 风格 | 速度 | 来源 |
|---|---|---|---|
| `ballad/ame ni uta u tanshikyoku - A Ballad Sung to Rain.mid` | ballad | 48 | https://bitmidi.com/uploads/5932.mid |
| `ballad/A-Love-Song.mid` | ballad | 87 | https://bitmidi.com/uploads/3122.mid |
| `ballad/A-Very-Special-Love-Song.mid` | ballad | 120 | https://bitmidi.com/uploads/3173.mid |
| `blues/After-Dinner-Blues.mid` | blues | 60 | https://bitmidi.com/uploads/4196.mid |
| `blues/Blues-Breaker-(Blues-Rock-'n-Roll-Style).mid` | blues | 123 | https://bitmidi.com/uploads/18592.mid |
| `blues/A.JACKSON.Mercury blues K.mid` | blues | 174 | https://bitmidi.com/uploads/3236.mid |
| `blues/Boogie-Woogie-Demo.mid` | blues | 180 | https://bitmidi.com/uploads/19184.mid |
| `folk/I-Was-Country-When-Country-Wasn't-Cool.mid` | folk | 103 | https://bitmidi.com/uploads/59404.mid |
| `folk/A.JACKSON.Gone country.mid` | folk | 125 | https://bitmidi.com/uploads/3229.mid |
| `folk/Acid-Folk.mid` | folk | 161 | https://bitmidi.com/uploads/3715.mid |

## 主题旋律语言（画像 4933 音）

- 音域 [52, 83] · 4.83 音/小节 · 级进 49% · 正拍 37%

## 频谱对齐画像（**不是模板**，只用于混音对标）

- **folk_tale_mix**：6 份真实录音画像的**逐维度中位数**（多方参考，见坑 132）
  —— 响度 -16.9 dBFS · 宽度 0.369 · 质心 3284Hz

| 成员参考 | 评分 | 质心 | 来源 |
|---|---|---|---|
| `bgm01c` | 0.855 | 3284 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（bgm01c.ogg） |
| `BGM04` | 0.823 | 2281 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM04.ogg） |
| `fine_BGM09` | 0.791 | 3695 | 仰望夜空的星辰（FINE DAYS） 的 Bgm 目录（真实商业混音）（<参考曲目录/仰望夜空星辰 FINE DAYS>/BGM09.ogg） |
| `BGM06` | 0.679 | 1533 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM06.ogg） |
| `BGM15c` | 0.674 | 3338 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM15c.ogg） |
| `BGM13` | 0.589 | 3068 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM13.ogg） |

> 来源结构：`source` 支持 `kind='file'`（项目素材包，带文件名）与
> `kind='web'`（网络权威源，带 `url`/`license`）—— 混音参考**必须可溯源**，
> 守卫 `mix_target_aggregate` 会要求每份成员都有 `source`。

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py folk_tale        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 1_village_road --theme folk_tale --seed 7
& $py scripts\make_song.py 1_village_road --check
```

## 还没验证什么

- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）
