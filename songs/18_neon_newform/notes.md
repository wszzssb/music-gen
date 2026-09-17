# 18_neon_newform（主题模板包：neon / 霓虹电子）

| 项目 | 值 |
|---|---|
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/neon.json`） |
| 主题→风格 | electronic/chiptune/game32（引擎预设 dance） |
| 速度·调式 | 140 BPM · G major（模板中位） |
| 和声 | D7 Gm6 D7 Gm6（来源 window2） |
| 曲式 | 6 段 × 8 小节 = 48 小节 |

## 模板清单（来源可溯源；.mid 不进仓库，重建见 `fetch_midi_lib.py`）

| 模板 | 风格 | 速度 | 来源 |
|---|---|---|---|
| `chiptune/1943boss.mid` | chiptune | 70 | https://www.vgmusic.com/music/console/nintendo/nes/1943boss.mid |
| `chiptune/1943-lev3.mid` | chiptune | 126 | https://www.vgmusic.com/music/console/nintendo/nes/1943-lev3.mid |
| `chiptune/1943boss1.mid` | chiptune | 150 | https://www.vgmusic.com/music/console/nintendo/nes/1943boss1.mid |
| `chiptune/1943won.mid` | chiptune | 210 | https://www.vgmusic.com/music/console/nintendo/nes/1943won.mid |
| `electronic/Celine Dion - My Heart Will Go On (Techno Remix).mid` | electronic | 130 | https://bitmidi.com/uploads/22756.mid |
| `electronic/Arrow - Back In The House (U Can See It).mid` | electronic | 140 | https://bitmidi.com/uploads/7787.mid |
| `electronic/Benny Benassi - Techno Cocain.mid` | electronic | 180 | https://bitmidi.com/uploads/17088.mid |
| `game32/ClassicXG.mid` | game32 | 60 | https://www.vgmusic.com/music/console/sony/ps1/ClassicXG.mid |
| `game32/bonusstagecredits.mid` | game32 | 140 | https://www.vgmusic.com/music/console/sony/ps1/bonusstagecredits.mid |
| `game32/T_AVGFighterMAX_YukaThemeGM.mid` | game32 | 165 | https://www.vgmusic.com/music/console/sony/ps1/T_AVGFighterMAX_YukaThemeGM.mid |

## 主题旋律语言（画像 1311 音）

- 音域 [60, 94] · 2.42 音/小节 · 级进 50% · 正拍 52%

## 频谱对齐画像（**不是模板**，只用于混音对标）

- **neon_mix**：6 份真实录音画像的**逐维度中位数**（多方参考，见坑 132）
  —— 响度 -16.9 dBFS · 宽度 0.510 · 质心 3250Hz

| 成员参考 | 评分 | 质心 | 来源 |
|---|---|---|---|
| `BGM15c` | 0.774 | 3338 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM15c.ogg） |
| `BGM16c` | 0.761 | 3250 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM16c.ogg） |
| `BGM16c_v2` | 0.761 | 3250 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM16c.ogg） |
| `bgm16c_new` | 0.761 | 3250 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM16c.ogg） |
| `BGM16b` | 0.696 | 2472 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM16b.ogg） |
| `bgm01c` | 0.592 | 3284 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（bgm01c.ogg） |

> 来源结构：`source` 支持 `kind='file'`（项目素材包，带文件名）与
> `kind='web'`（网络权威源，带 `url`/`license`）—— 混音参考**必须可溯源**，
> 守卫 `mix_target_aggregate` 会要求每份成员都有 `source`。

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py neon        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 18_neon_newform --theme neon --seed 7
& $py scripts\make_song.py 18_neon_newform --check
```

## 还没验证什么

- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）
