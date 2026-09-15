# 49_battle_rock（主题模板包：battle / 战斗）

| 项目 | 值 |
|---|---|
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/battle.json`） |
| 主题→风格 | rock/game16/game32（引擎预设 dance） |
| 速度·调式 | 139 BPM · C major（模板中位） |
| 和声 | Csus4 Gm7 Csus4 Gm7（来源 window2） |
| 曲式 | 10 段 × 8 小节 = 72 小节 |

## 模板清单（来源可溯源；.mid 不进仓库，重建见 `fetch_midi_lib.py`）

| 模板 | 风格 | 速度 | 来源 |
|---|---|---|---|
| `game16/7thsaga_town3-1.mid` | game16 | 100 | https://www.vgmusic.com/music/console/nintendo/snes/7thsaga_town3-1.mid |
| `game16/7thsaga_cave.mid` | game16 | 125 | https://www.vgmusic.com/music/console/nintendo/snes/7thsaga_cave.mid |
| `game16/7thsaga_bt1.mid` | game16 | 140 | https://www.vgmusic.com/music/console/nintendo/snes/7thsaga_bt1.mid |
| `game16/7thsaga_bt2_w.mid` | game16 | 144 | https://www.vgmusic.com/music/console/nintendo/snes/7thsaga_bt2_w.mid |
| `game32/ClassicXG.mid` | game32 | 60 | https://www.vgmusic.com/music/console/sony/ps1/ClassicXG.mid |
| `game32/bonusstagecredits.mid` | game32 | 140 | https://www.vgmusic.com/music/console/sony/ps1/bonusstagecredits.mid |
| `game32/T_AVGFighterMAX_YukaThemeGM.mid` | game32 | 165 | https://www.vgmusic.com/music/console/sony/ps1/T_AVGFighterMAX_YukaThemeGM.mid |
| `rock/50's-Rock.mid` | rock | 87 | https://bitmidi.com/uploads/1541.mid |
| `rock/Hard Rock Sofa & Swanky Tunes - Smolengrad.mid` | rock | 130 | https://bitmidi.com/uploads/55047.mid |
| `rock/A-Hard-Day's-Night-4.mid` | rock | 139 | https://bitmidi.com/uploads/3107.mid |

## 主题旋律语言（画像 1552 音）

- 音域 [64, 93] · 2.58 音/小节 · 级进 72% · 正拍 55%

## 频谱对齐画像（**不是模板**，只用于混音对标）

- **battle_mix**：6 份真实录音画像的**逐维度中位数**（多方参考，见坑 132）
  —— 响度 -16.9 dBFS · 宽度 0.510 · 质心 3250Hz

| 成员参考 | 评分 | 质心 | 来源 |
|---|---|---|---|
| `BGM15c` | 0.792 | 3338 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM15c.ogg） |
| `BGM16c` | 0.743 | 3250 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM16c.ogg） |
| `BGM16c_v2` | 0.743 | 3250 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM16c.ogg） |
| `bgm16c_new` | 0.743 | 3250 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM16c.ogg） |
| `BGM16b` | 0.685 | 2472 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM16b.ogg） |
| `bgm01c` | 0.611 | 3284 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（bgm01c.ogg） |

> 来源结构：`source` 支持 `kind='file'`（项目素材包，带文件名）与
> `kind='web'`（网络权威源，带 `url`/`license`）—— 混音参考**必须可溯源**，
> 守卫 `mix_target_aggregate` 会要求每份成员都有 `source`。

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py battle        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 49_battle_rock --theme battle --seed 49
& $py scripts\make_song.py 49_battle_rock --check
```

## 还没验证什么

- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）
