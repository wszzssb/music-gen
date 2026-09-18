# 15_waltz_ballroom（主题模板包：waltz / 三拍圆舞）

| 项目 | 值 |
|---|---|
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/waltz.json`） |
| 主题→风格 | classical/baroque/romantic/folk/public_domain（引擎预设 gorgeous） |
| 速度·调式 | 72 BPM · F major（模板中位） |
| 和声 | C7 Gm7 Fmaj7 Fmaj7（来源 window4） |
| 曲式 | 7 段 × 8 小节 = 48 小节 |

## 模板清单（来源可溯源；.mid 不进仓库，重建见 `fetch_midi_lib.py`）

| 模板 | 风格 | 速度 | 来源 |
|---|---|---|---|
| `baroque/handel-ombra-mai-fu.mid` | baroque | 55 | https://bitmidi.com/uploads/35097.mid |
| `baroque/Bwv0593 Vivaldi Concerto Arrangement RV522.mid` | baroque | 70 | https://bitmidi.com/uploads/27584.mid |
| `classical/Bach-Brandenburg-Menuetto.mid` | classical | 60 | https://bitmidi.com/uploads/14986.mid |
| `classical/Minuet-in-Mozart.mid` | classical | 100 | https://bitmidi.com/uploads/35181.mid |
| `folk/American-Folk-(Variation-1).mid` | folk | 72 | https://bitmidi.com/uploads/6052.mid |
| `folk/caledonia-celtic-thunder-kar_gc9.mid` | folk | 100 | https://bitmidi.com/uploads/54409.mid |
| `public_domain/LVB_Sonate_02no1_2.mid` | public_domain | 46 | https://www.mutopiaproject.org/ftp/BeethovenLv/O2/LVB_Sonate_02no1_2/LVB_Sonate_02no1_2.mid |
| `public_domain/LVB_Sonate_02no1_3.mid` | public_domain | 168 | https://www.mutopiaproject.org/ftp/BeethovenLv/O2/LVB_Sonate_02no1_3/LVB_Sonate_02no1_3.mid |
| `romantic/chubert-Liszt Serenade.mid` | romantic | 60 | https://bitmidi.com/uploads/28798.mid |
| `romantic/Tchaikovsky Lake Of The Swans Act 1 2mov.mid` | romantic | 140 | https://bitmidi.com/uploads/31292.mid |

## 主题旋律语言（画像 5106 音）

- 音域 [64, 86] · 4.62 音/小节 · 级进 74% · 正拍 52%

## 频谱对齐画像（**不是模板**，只用于混音对标）

- **waltz_mix**：6 份真实录音画像的**逐维度中位数**（多方参考，见坑 132）
  —— 响度 -17.1 dBFS · 宽度 0.544 · 质心 3548Hz

| 成员参考 | 评分 | 质心 | 来源 |
|---|---|---|---|
| `amakute` | 0.915 | 3624 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（あまくてとろける.flac） |
| `BGM33` | 0.830 | 3665 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM33.ogg） |
| `BGM16` | 0.721 | 2826 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM16.ogg） |
| `BGM15d` | 0.710 | 3548 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM15d.ogg） |
| `BGM12` | 0.695 | 557 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM12.ogg） |
| `BGM01` | 0.684 | 2810 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM01.ogg） |

> 来源结构：`source` 支持 `kind='file'`（项目素材包，带文件名）与
> `kind='web'`（网络权威源，带 `url`/`license`）—— 混音参考**必须可溯源**，
> 守卫 `mix_target_aggregate` 会要求每份成员都有 `source`。

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py waltz        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 15_waltz_ballroom --theme waltz --seed 7
& $py scripts\make_song.py 15_waltz_ballroom --check
```

## 还没验证什么

- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）
