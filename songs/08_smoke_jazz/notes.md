# 8_smoke_jazz（主题模板包：lounge / 酒馆爵士）

| 项目 | 值 |
|---|---|
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/lounge.json`） |
| 主题→风格 | jazz/blues/pop（引擎预设 acoustic） |
| 速度·调式 | 132 BPM · C minor（模板中位） |
| 和声 | C7 Gm7 Fm7 F7（来源 window4） |
| 曲式 | 5 段 × 8 小节 = 32 小节 |

## 模板清单（来源可溯源；.mid 不进仓库，重建见 `fetch_midi_lib.py`）

| 模板 | 风格 | 速度 | 来源 |
|---|---|---|---|
| `blues/After-Dinner-Blues.mid` | blues | 60 | https://bitmidi.com/uploads/4196.mid |
| `blues/Boogie-Woogie.mid` | blues | 130 | https://bitmidi.com/uploads/19185.mid |
| `blues/Boogie-Woogie-Demo.mid` | blues | 180 | https://bitmidi.com/uploads/19184.mid |
| `jazz/Cowboy Bebop - ELM.mid` | jazz | 58 | https://bitmidi.com/uploads/25904.mid |
| `jazz/Cowboy Bebop - Goodnight Julia.mid` | jazz | 120 | https://bitmidi.com/uploads/25905.mid |
| `jazz/Cowboy Bebop - Cat Blues.mid` | jazz | 144 | https://bitmidi.com/uploads/25903.mid |
| `jazz/Alexander's-Ragtime-Band.mid` | jazz | 162 | https://bitmidi.com/uploads/4880.mid |
| `pop/IGGY POP.Louie Louie.mid` | pop | 120 | https://bitmidi.com/uploads/60059.mid |
| `pop/Disco Citizens - Footprint.mid` | pop | 132 | https://bitmidi.com/uploads/39730.mid |
| `pop/Disco-Fans.mid` | pop | 158 | https://bitmidi.com/uploads/39732.mid |

## 主题旋律语言（画像 5185 音）

- 音域 [46, 81] · 6.31 音/小节 · 级进 48% · 正拍 50%

## 频谱对齐画像（**不是模板**，只用于混音对标）

- **lounge_mix**：6 份真实录音画像的**逐维度中位数**（多方参考，见坑 132）
  —— 响度 -16.9 dBFS · 宽度 0.660 · 质心 2472Hz

| 成员参考 | 评分 | 质心 | 来源 |
|---|---|---|---|
| `bgm01c` | 0.874 | 3284 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（bgm01c.ogg） |
| `BGM06` | 0.845 | 1533 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM06.ogg） |
| `BGM15c` | 0.840 | 3338 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM15c.ogg） |
| `BGM18` | 0.726 | 2043 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM18.ogg） |
| `BGM16b` | 0.692 | 2472 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM16b.ogg） |
| `BGM04` | 0.657 | 2281 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM04.ogg） |

> 来源结构：`source` 支持 `kind='file'`（项目素材包，带文件名）与
> `kind='web'`（网络权威源，带 `url`/`license`）—— 混音参考**必须可溯源**，
> 守卫 `mix_target_aggregate` 会要求每份成员都有 `source`。

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py lounge        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 8_smoke_jazz --theme lounge --seed 7
& $py scripts\make_song.py 8_smoke_jazz --check
```

## 还没验证什么

- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）
