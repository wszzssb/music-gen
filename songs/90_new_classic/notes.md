# 90_new_classic（主题模板包：classic / 古典庄重）

| 项目 | 值 |
|---|---|
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/classic.json`） |
| 主题→风格 | classical/baroque/public_domain（引擎预设 gorgeous） |
| 速度·调式 | 110 BPM · G minor（模板中位） |
| 和声 | D7 Dm7 Gm6 Cm7（来源 window4） |
| 曲式 | 5 段 × 8 小节 = 52 小节 |

## 模板清单（来源可溯源；.mid 不进仓库，重建见 `fetch_midi_lib.py`）

| 模板 | 风格 | 速度 | 来源 |
|---|---|---|---|
| `baroque/Handel in the Strand.mid` | baroque | 70 | https://bitmidi.com/uploads/28665.mid |
| `baroque/Variations and Fugue on theme by Handel op24.mid` | baroque | 120 | https://bitmidi.com/uploads/28190.mid |
| `classical/Bach-1.mid` | classical | 102 | https://bitmidi.com/uploads/14984.mid |
| `classical/chopin-sonata11.mid` | classical | 107 | https://bitmidi.com/uploads/23779.mid |
| `classical/Arty & Mat Zo - Mozart.mid` | classical | 132 | https://bitmidi.com/uploads/7830.mid |
| `classical/chopin-ballade1.mid` | classical | 150 | https://bitmidi.com/uploads/23776.mid |
| `public_domain/MentreDormi.mid` | public_domain | 40 | https://www.mutopiaproject.org/ftp/VivaldiA/rv725/MentreDormi/MentreDormi.mid |
| `public_domain/03-gavotte.mid` | public_domain | 92 | https://www.mutopiaproject.org/ftp/HandelGF/Aylesford/03-gavotte/03-gavotte.mid |
| `public_domain/k375g.mid` | public_domain | 110 | https://www.mutopiaproject.org/ftp/MozartWA/Anh41/k375g/k375g.mid |
| `public_domain/LVB_Sonate_02no1_1.mid` | public_domain | 190 | https://www.mutopiaproject.org/ftp/BeethovenLv/O2/LVB_Sonate_02no1_1/LVB_Sonate_02no1_1.mid |

## 主题旋律语言（画像 5284 音）

- 音域 [60, 88] · 4.15 音/小节 · 级进 54% · 正拍 50%

## 频谱对齐画像（**不是模板**，只用于混音对标）

- **classic_mix**：6 份真实录音画像的**逐维度中位数**（多方参考，见坑 132）
  —— 响度 -16.2 dBFS · 宽度 0.523 · 质心 3286Hz

| 成员参考 | 评分 | 质心 | 来源 |
|---|---|---|---|
| `BGM13` | 0.934 | 3068 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM13.ogg） |
| `BGM09` | 0.924 | 2421 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM09.ogg） |
| `fine_BGM09` | 0.759 | 3695 | 仰望夜空的星辰（FINE DAYS） 的 Bgm 目录（真实商业混音）（<参考曲目录/仰望夜空星辰 FINE DAYS>/BGM09.ogg） |
| `t1_BGM34` | 0.711 | 3812 | 仰望夜空的星辰（FINE DAYS） 的 Bgm 目录（真实商业混音）（<参考曲目录/仰望夜空星辰 FINE DAYS>/BGM34.ogg） |
| `BGM04` | 0.700 | 2281 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM04.ogg） |
| `BGM15` | 0.675 | 3286 | 项目素材包 Bgm（同批提取的 galgame 商业 BGM）（BGM15.ogg） |

> 来源结构：`source` 支持 `kind='file'`（项目素材包，带文件名）与
> `kind='web'`（网络权威源，带 `url`/`license`）—— 混音参考**必须可溯源**，
> 守卫 `mix_target_aggregate` 会要求每份成员都有 `source`。

## 复现

```powershell
$py = "<工具链根>/venv/python.exe"
cd <工具链根>
& $py scripts\theme_pack.py classic        # 模板包（模板不足时 --allow-fetch 联网抓）
& $py scripts\new_song.py 90_new_classic --theme classic --seed 103
& $py scripts\make_song.py 90_new_classic --check
```

## 还没验证什么

- 未渲染/未对齐（跑 `make_song.py` 才有成绩单）
