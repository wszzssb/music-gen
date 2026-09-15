---
name: bgm-studio
description: 写歌 / BGM 制作工具链——作曲、扒谱扒和弦、仿照某首参考曲写、MIDI 转真音源渲染成 ogg/wav、以及客观体检（分段响度/频谱质心/立体声宽度/倍频程平衡/噪声/爆音）。工具在本仓库根目录。用户提到写歌/来一首/BGM/配乐/主题曲/插入曲/仿照某曲/扒谱/渲染 MIDI/音乐太闷太电音，或 song、music 时加载。
---

# BGM Studio —— 作曲 / 扒谱 / 渲染工具链

**根目录 = 本仓库根**（clone 下来的目录）：新歌只写 `song.json`，`make_song.py` 一条命令出成品+成绩单。

## 要什么 → 读哪份（**别整篇读 README**）

| 我要… | 读 | 体量 |
|---|---|---|
| **新歌的"依据"**：主题模板包 / 混音目标 / 模板来源白名单 | `docs/THEME-PACK.md` | ≈1.6k |
| 写/改 `song.json`（结构、风格预设、段落曲线） | `docs/SONG-FORMAT.md` | ≈1.7k |
| 完整命令与开关（含分段拟合流程） | `CHEATSHEET.md` | ≈0.7k |
| 全貌：目录、工具清单、验证状态、产物 | `README.md` | ≈5.5k |
| 出症状（杂音/错音/调参不收敛/占用率对不上） | `PITFALLS.md` 按编号查 | 别整读 |
| **要新增内容**（往哪写、动哪些守卫） | `docs/CONVENTION.md` | ≈1.1k |
| **写歌时开可视化面板** | `studio/README.md`；启动 `studio\start.cmd` → http://127.0.0.1:8765 | ≈1.4k |

## 0. 环境（必须）

`$py = <仓库>\.venv\Scripts\python.exe`（numpy/soundfile/ffmpeg）。系统 python 3.14 无 numpy。
路径全绝对 + 编码兜底 → 任意目录可调。**长曲后台跑**。

## 1. 写一首新歌（唯一路径，别自创）

**模板依据是硬规矩**：一次生成依据**同主题 ≥8 首模板**，模板只能是 `refs/midi2/`（带来源 URL）
或网络权威数据 —— 不许拿"自己做的曲子 / 某份音频"当模板（老 `--from` 会被 `check_song` 拦下）。

```powershell
& $py ...\scripts\theme_pack.py <主题>       # ① --list-themes 看主题；同主题不足 8 首加 --allow-fetch
& $py ...\scripts\new_song.py <NN_名字> --theme <主题> --ref <画像名>   # ② 出 song.json + 旋律
#   → 只改 songs\<NN_名字>\song.json（chords / melody / sections）—— 不写代码
& $py ...\scripts\make_song.py <NN_名字> --check    # ③ 先 2 秒查数据，再作曲+渲染+调参+成绩单
#   数据错会被拦下：可先 `check_song.py <曲> --fix`
```
（`--ref` 省略时取**主题包里的混音目标**——与模板依据是两回事；改现成的歌才用 `--from`。）

## 2. 开工前先定这 7 件事（不定就会返工，每条都是实测踩出来的）

1. **调性**：`melody_profile.py` 音级直方图（看"计数为 0 的音级"）；`analyze_bass` 根音整体
   偏低半音、画像 tonic 只是假设 —— **判错=全盘返工**。
2. **引子**：画像 `structure` 里**引子响度≈A 段就别做稀疏引子**（占用率会掉到 66/61/79，例曲 95+）。
3. **结构化参数一次定齐再渲染**（每多一轮 = 2 分钟 + 一次成绩单）。速查 `docs/SONG-FORMAT.md`。
4. **改过 `song.json` 必须重作曲**（`--no-compose` 下 MIDI 不重生成 → 白跑一轮）。
5. **验"加这层有没有用"先用 `layer_exp.py` 离线叠层测**。
6. **速度是"层级"不是单值**（60/75/80 ↔ 120/150/160）：`check_audio.py` 报两层支持度，定参考曲
   用 `--bpm` 钉死一层记进画像，否则一路假报"速度差 100%"；连奏编配**必须**手给。
7. **像不像看「段间对比」而非整体亮度**：`analyze_sections.py <参考曲>` →
   `new_song.py … --from-sections`（全曲一条直线 = 亮却闷）。

## 3. 会改变行为的这几条（其余按需查文档）

1. **别手开"渲染→测→调"循环**：`make_song` 内部闭环（≤6 轮），改完最多再跑一次；
   **要改的多处先一次列全再渲**（反面教材：一首渲 6 次，4 次是"改一处→渲→看"的试错）。
2. **速度一律取自 MIDI（`--bpm`）**：连奏编配会被 `detect_bpm` 测错（106BPM→154.3），小节指标全错位。

3. 成绩单说**"已到顶 → 靠编配"**时改 `mix`/`programs`，别再推 EQ；**不许把"没达标"说成"达标"**。
4. **人声参考曲**只对齐 160–10kHz；不该追的差距写 `align_exempt` + 理由（空白理由自检会拦），
   **不许改阈值"通过"**。
5. `perc: none` 会让 5–18kHz 塌掉 → 要"无鼓组"用 `perc_style: light` + `perc: 1`。
6. **别拿 4 小节小样判断像不像**（预演只证明管线通）。
7. **全量自检只在两种时候跑**：交付前、或**改过检查项本身**（那时配 `mutation_check.py`，
   **新检查必须配注入用例**）。改一行数据/编配别跑全量 —— 只跑相关那一条：
   `python -c "import sys;sys.path.insert(0,'scripts');import selftest;selftest.t_xxx()"`。
   **动过引擎**才要全库重跑 `make_song` + `rehearsal.py`。**多对话共用** → 只追加。
8. 交付：`<曲目>.mid` + `_sf.ogg` + `notes.md`（调性/速度/结构/复现/**没达标项**）。
9. 汇报只给结论 + 差距表 + 一句"还没验证什么"。
10. **改编配前先量，别拿整曲渲染当探针**：倍频程是"相对最响频段"（低频一厚就整体平移）→
    `bands_abs.py` 看绝对差+占用率、`probe_timbre.py --solo` 看哪轨真响；A/B 用 `--no-tune`；
    面板里都有（搜索默认只报告，逐项勾选采用）。见坑 103。

11. **"不好听"先量形态**：密度 2.0~2.6、末落点≥8 格 ≥65%、节奏重复率 ≤45%（真实 23%）；
    守卫 `melody_form_rules`（坑 127）。

## 4. 工具与查哪里

`make_song` `new_song` `theme_pack` `profile_ref` `scorecard`（`--bpm`）`stem_compare`（占用率，判"像不像"的主尺子）
`layer_exp` `melody_gen/profile` `play_midi --wait`（会出声，**先问**）`analyze_sections`
`selftest` `rehearsal` `mutation_check` —— 清单见 `README.md` §2。

## 5. 已知天花板

GM 音源（GeneralUser GS）不如商业库 —— **MIDI 才是上限最高的交付**。
底鼓采样 0.14~0.18s → 连续性靠 `perc_layers`，瞬态补不了。和弦识别用 `probe_peaks.py`（谱峰法）。
