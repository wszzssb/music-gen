# 36_d132_cheerful（主题模板包：cheerful / 欢快）

| 项目 | 值 |
|---|---|
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/cheerful.json`） |
| 主题→风格 | pop/latin/rock（引擎预设 daily） |
| 速度·调式 | 132 BPM · C major（模板中位） |
| 和声 | Am6 B7 Gmaj7 B7（来源 window4） |
| 曲式 | 8 段 × 8 小节 = 64 小节（A A2 B A3 C A4 B2 A5） |
| 编配 | bass/piano/uku 必开 · glock/pad 关（模板 0%）· ep/perc/strings 由预设定；perc_style=dance，bass_style=sixteenth |
| 段间能量曲线 | k=1.0，各段偏移 `[-2.38, 0.62, 2.62, -0.38, -3.38, 1.62, 0.62, 0.62]` dB（取自混音目标 BGM15c 的 8 小节能量块） |
| 交付物 | `d132_cheerful.mid` · `d132_cheerful_sf.ogg`（本地可听；`songs/**/*.ogg` 默认不入库） |

## 成绩单（vs 混音目标 BGM15c，132.4 BPM）

自动调参 **2 轮达标**（内部闭环，未手工干预）：

| 指标 | 本曲 | 参考 | 差 |
|---|---|---|---|
| 响度 RMS | −19.3 | −19.3 | **0.0** |
| 立体声宽度 | 0.677 | 0.660 | +0.017 |
| 频谱质心 | 3625Hz | 3338Hz | +287 |
| 速度 | 132.0 | 132.4 | −0.4 |
| 最大单频段差 | — | — | **+3.9 dB（2500–5000Hz 偏厚）** |
| 段间起伏 | **5.8 dB** | 6.0 dB | 逐块吻合（8 块内每块 ≤0.3dB） |

质量探针：旋律形态 **0 问题**（2.36 音/小节 · 同音串 ≤2 · 碎音 30% · 强拍贴和弦 **100%**）；
null test 残留 −48.3 dBFS（>45 即认为无伪影）。

**没达标项**：① 2500–5000Hz 偏厚 3.9dB（EQ 已到顶，属于编配层面：可试 `mix.Hook`/`mix.Arp` 降一档）；
② 低频节奏型与参考不一致（参考是四踩+十六分驱动，本曲用主题包的 `bass=sixteenth/perc=dance`，
形状不同属预期，但"像不像"这一层没对齐）。

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

## 主题旋律语言（画像 2808 音）

- 音域 [64, 81] · 3.23 音/小节 · 级进 61% · 正拍 44%

## 频谱对齐画像（**不是模板**，只用于混音对标）

- BGM15c：响度 -19.3 dBFS · 宽度 0.660 · 质心 3338Hz

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py cheerful        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 36_d132_cheerful --theme cheerful --seed 21
& $py scripts\make_song.py 36_d132_cheerful --check
```

## 还没验证什么

- **好不好听只能靠耳朵**：上面的数字只证明"与 BGM15c 的频谱/响度/宽度/段间对比对齐"，
  不证明音乐性（和声/旋律的趣味性、会不会听腻）—— 请你听 `d132_cheerful_sf.ogg` 再决定。
- 模板里 pop/latin/rock 是**跨风格混合**的"欢快"，若你要的是纯日系 pop 或纯 latin，
  该换主题包（`refs/themes/*.json` 的 `styles` 字段可调）。
- 段间对比只对齐了"每 8 小节的响度起伏"，**分段音色（亮度）对比**没碰。
