# 09_sun_parade（主题模板包：cheerful / 欢快）

| 项目 | 值 |
|---|---|
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/cheerful.json`） |
| 主题→风格 | pop/latin/rock（引擎预设 dance） |
| 速度·调式 | 132 BPM · C major（模板中位） |
| 和声 | Am6 B7 Gmaj7 B7（来源 window2） |
| 曲式 | 6 段 × 8 小节 = 40 小节 |

## 模板清单（来源可溯源；.mid 不进仓库，重建见 `fetch_midi_lib.py`）

| 模板 | 风格 | 速度 | 来源 |
|---|---|---|---|
| `latin/Blue-Tango.mid` | latin | 118 | https://bitmidi.com/uploads/18473.mid |
| `latin/L.CARLTON.Rio Samba.mid` | latin | 129 | https://bitmidi.com/uploads/66951.mid |
| `latin/Schuld-War-Nur-Der-Bossa-Nova-2.mid` | latin | 140 | https://bitmidi.com/uploads/92044.mid |
| `latin/Azure-Bossa-Nova.mid` | latin | 145 | https://bitmidi.com/uploads/8804.mid |
| `pop/IGGY POP.Louie Louie.mid` | pop | 120 | https://bitmidi.com/uploads/60059.mid |
| `pop/Disco Citizens - Footprint.mid` | pop | 132 | https://bitmidi.com/uploads/39730.mid |
| `pop/Disco-Fans.mid` | pop | 158 | https://bitmidi.com/uploads/39732.mid |
| `rock/50's-Rock.mid` | rock | 87 | https://bitmidi.com/uploads/1541.mid |
| `rock/Hard Rock Sofa & Swanky Tunes - Smolengrad.mid` | rock | 130 | https://bitmidi.com/uploads/55047.mid |
| `rock/A-Hard-Day's-Night-4.mid` | rock | 139 | https://bitmidi.com/uploads/3107.mid |

## 主题旋律语言（画像 2725 音）

- 音域 [64, 80] · 2.85 音/小节 · 级进 62% · 正拍 58%

## 频谱对齐画像（**不是模板**，只用于混音对标）

- **cheerful_mix**：6 份真实录音画像的**逐维度中位数**（多方参考，见坑 132）
  —— 响度 -16.9 dBFS · 宽度 0.510 · 质心 3250Hz

| 成员参考 | 评分 | 质心 | 来源 |
|---|---|---|---|
| `BGM15c` | 0.906 | 3338 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM15c.ogg） |
| `bgm01c` | 0.739 | 3284 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（bgm01c.ogg） |
| `BGM06` | 0.665 | 1533 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM06.ogg） |
| `BGM16c` | 0.614 | 3250 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM16c.ogg） |
| `BGM16c_v2` | 0.614 | 3250 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM16c.ogg） |
| `bgm16c_new` | 0.614 | 3250 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM16c.ogg） |

> 来源结构：`source` 支持 `kind='file'`（项目素材包，带文件名）与
> `kind='web'`（网络权威源，带 `url`/`license`）—— 混音参考**必须可溯源**，
> 守卫 `mix_target_aggregate` 会要求每份成员都有 `source`。

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py cheerful        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 09_sun_parade --theme cheerful --seed 7
& $py scripts\make_song.py 09_sun_parade --check
```

## 还没验证什么

- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）
