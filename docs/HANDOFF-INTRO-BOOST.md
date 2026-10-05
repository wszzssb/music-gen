# HANDOFF —— 生成能力强化（2026-10-05 这一轮）

> **给下一个对话**：这份是自足的。读完它 + `skill/SKILL.md` 就能接着干。
> 一句话现状：**引擎改动已写好并自检通过，但"①+② 伴奏/鼓逐句变化 + ④ 逐段旋律密度"
> 这三项还没在任何曲目上开启、没重生成、没验收** —— 下一步就是把它们跑起来并量。

---

## 0. 这一轮已经落地的东西（都带读数，已完成）

| # | 能力 | 改在哪 | 读数（改前 → 改后） |
|---|---|---|---|
| 1 | **旋律落点词汇** | `melody_gen.py`：装饰位按画像 `onset16_hist` 权重抽（`DECOR_CELLS`）· 装饰密度跟画像（`min(画像弱格, WEAK_CAP=15%)`）· 去掉 `bar % len(variants)` 的 6 小节周期轮换（`_pick_variant`） | 16 格里**恒 0 格 6~7 → 3~4**；落点熵 0.74 → **0.79**；每小节节奏型去重 18% → **20~38%** |
| 2 | **旋律音域** | `melody_gen.py` `persona()` + `MELODY_SPAN = 21` | 旋律跨度 34 → **19~20 半音**（真实 BGM 量级） |
| 3 | **伴奏给旋律让位** | `song_engine.yield_to_melody()`（逐段，p75 判据；**还原曲不让位**） | 全库"旋律落在伴奏最高音之下" 12.0% → **38.5% → 11.3%**（门 15%） |
| 4 | **引子切入手法** | `song_engine.shape_intro()` + `new_song.intro_style_for()`（曲名 CRC32 → 12 档表） | 16 首**全在 0.00 秒齐进** → **5 种手法**：pickup 6 / silence 4 / solo_first 3 / drums_first 2 / default 1 |
| 5 | **主题包首段** | `refs/themes/*.json` 的 `form.plan[0]`（长度 2/4/6/8；5 个主题无 Intro） | 首段 **15/15 都是 Intro 4 小节** → 段名序列 **9 种/15 包** |
| 6 | **候选打分补三条守卫** | `melody_gen.py` `cand_score(..., run_pen, motif_pen)`：最长同音串 · 句末收束 ≥0.25 · 跳后反向 ≥0.60 | `melody_health` 同音串 6/7 → **≤3**；`melody_motif_rules` 由 FAIL → PASS |

**当前自检**：`selftest.py --fast` **201/201 通过**（改动前是 198/201，三条 FAIL 都在本轮修掉）。

---

## 1. ⚠ 未完成：① ② ④（代码就位，**未开启 / 未验证**）

### 已写好的代码（**未提交**，`git status` 里是 `M`）

| 开关 | 位置 | 做什么 |
|---|---|---|
| **① `patterns.comp_vary`**（全局布尔） | `song_engine.vary_comp()`（新增，在 `build_events` 之前）+ 4 个调用点：Bass / uku(Hook) / ep(Hook) / Piano | 每 **4 小节乐句**按 (曲名 seed + 乐句序号) 轮换 4 种"说法"：v0 原样 · v1 句末撤最后一个短音（呼吸）· v2 句末末音推到下一小节前的"a"位（推进）· v3 句首首个反拍音提前 1/4 拍（切分）。**只动短音（时值 ≤0.6 拍）与落点，不改音高** |
| **② `sections[i].arr.drum_fill`**（逐段布尔） | `song_engine.build_events` 的 `drum_grid` 分支（`elif arr.get('perc') and _dg:`）末尾 | 每 **4 小节的末小节**补一个十六分过门（格 12/13/14/15，鼓件 38/45/47/50，力度 66→108 + 曲名哈希抖动） |
| **④ 逐段旋律密度** | `melody_gen.SEC_DENS_GAIN = {0:0.65, 1:0.8, 2:1.0, 3:1.12, 4:1.2}`；`main()` 里 `gen_section(..., dens * _g, ...)` | 旋律密度按**该段** `arr.density`（0~4）缩放；上限守住形态门（2.4×1.2 = **2.88 ≤ 2.9**） |

**当前状态（已实测核对）**：`comp_vary` 已开的曲目 **0 首**、`drum_fill` 已标记的段 **0 段**
→ 也就是说这三项**完全没生效**，库里的曲子还是旧行为。

### 下一步就三条命令

```powershell
$py = "D:\software\skill\music-gen\.venv\Scripts\python.exe"
# ① ② ④ 一起开启 + 重跑 melody_gen（④ 必须重生成才生效）+ make_song
& $py "D:\test\_tmp\rhythm-gap\boost_all.py"      # 已写好、**尚未运行**；日志 D:\test\_tmp-rhythm\boost_all.log
# ② 验收（下面 §3 的尺子）
& $py "D:\test\_tmp\rhythm-gap\compare9.py"       # 逐轨节奏指标（含"每小节型去重"）
& $py "D:\software\skill\music-gen\scripts\selftest.py" --fast
```

`boost_all.py` 做的事：逐首把 `patterns.comp_vary = true`、给 `perc ≥ 1` 的段写 `arr.drum_fill = true`
（用 **`json_io.save`** 写盘，见 §4 坑 1），然后按 `song.json` 里 `melody_gen` 元数据的参数
（seed / candidates / dens / step_bias + **`--tonic`**）重跑 `melody_gen`，最后 `make_song`。

### 还要补的两件

1. **`theme_pack.py` 的 plan 生成器没改** —— 现在只改了 15 个包的数据；**重建主题包会把首段覆盖回
   "Intro 4 小节"**。每个包里已写 `form.intro_note` 留痕提示。要根治得动 `theme_pack.py` 里
   生成 `plan` 那一段（按主题名派生首段长度/有无）。
2. **三个交付夹没刷新**（音轨是上一版的）：
   `D:\test\100-108_重做_音轨\` · `D:\test\109-114_新歌_音轨\` · `D:\test\115_three_faces_交付\`
   —— 重跑完照着拷 `.mid` + `_sf.ogg` 即可（脚本：`D:\test\_tmp\rhythm-gap\pack_folder.py` / `pack_new6.py`）。

---

## 2. 验收判据（**改完必须逐条给读数，不许只说"做了"**）

| 项 | 尺子 | 脚本 | 期望方向 |
|---|---|---|---|
| ① 伴奏逐句变化 | 逐轨"每小节节奏型去重"（型数/小节数） | `D:\test\_tmp\rhythm-gap\compare9.py` | `Piano` 现在 **2~5%** → 应显著上升；`Bass` 3~8%、`Hook` 7~19% 同理 |
| ② 鼓段内变化 | 同上（鼓轨）+ 过门可数（每 4 小节末小节的 16 分音数） | 同上 | 鼓轨型去重现在 10~42% → 上升 |
| ④ 逐段密度 | **逐段**音/小节（不是全曲中位） | `compare9.py` 的逐段表 / `probe_melody_health.py` | 副歌段 > 主歌段；**门：形态判据密度 1.8~2.9** |
| 不许变差 | 音区分离"旋律在下" ≤15% | `D:\test\_tmp\rhythm-gap\sep_lib.py` | 现在 **11.3%**，别回到 15% 以上 |
| 不许变差 | 同音串 ≤4 / 句末收束 ≥25% / 跳后反向 ≥60% | `pitch_runs.py` + `selftest --fast` | 201/201 要保住 |

---

## 3. 尺子清单（都在 `D:\test\_tmp\rhythm-gap\`，可直接跑）

| 脚本 | 量什么 |
|---|---|
| `compare9.py` | 基准 vs 现状的**逐轨**节奏指标（音数/正拍/反八/十六/熵/恒 0 格/型去重）—— 基准读 `D:\test\_tmp-rhythm\baseline\<曲目>\` |
| `intro9s.py` | 开头 **9 秒 / 2 秒**的起音位向量两两 Jaccard + 真实模板对照 |
| `intro_fingerprint.py` | 开头指纹（首个起音时刻 / 前 2 秒起音数 / 发声的轨 / 手法）+ 顺手修 song.json 的 CRLF |
| `sep_lib.py` | 全库"旋律落在伴奏最高音之下"% 与音区分离中位 |
| `pitch_runs.py` | 最长同音串 / 同音率（基准 vs 现状） |
| `who_covers.py` | 盖住旋律的最高音来自哪条轨（逐音归因） |
| `regen9.py` / `rerender9.py` | 重生成（melody_gen+make_song）/ 只重渲染 |
| `pack_folder.py` / `pack_new6.py` | 把 `.mid`+`_sf.ogg` 收进交付夹 |

### ⚠ 尺子本身的坑（**踩过，别重踩**）

1. **Jaccard 会被"稀疏开头"骗**：两首各只有一个长音 → 相似度 **100%**。重渲后"前 2 秒中位 15%→17%、
   最高 100%"看着像变差，其实是尺子问题 —— 判断开头要看**指纹表**（首发乐器/首个起音时刻），
   不要只看 Jaccard。
2. **`mido` 迭代 `track` 时 `msg.time` 是 tick，只有迭代 `MidiFile` 才是秒** —— 按 tick 当秒算，
   9 秒窗口只剩 3 个音、Jaccard 全 100%（我踩过一次）。
3. **别把 7~8 条轨混在一起量落点**：伴奏的正拍长音会把十六分位比例稀释（量旋律就只量 `Melody` 轨）。
4. **`--dens` 与画像密度不是一回事**：密度服从曲目本身，画像只提供相对长短/落点/走向。

---

## 4. 硬约束 / 坑（这一轮真踩到的，按重要性排）

1. **写 `song.json` / 主题包一律用 `scripts/json_io.py` 的 `save()`**。
   我用 `json.dump()` 文本模式写过两次，两次都被守卫抓：
   `song_json_canonical`（先报 **CRLF 1107 处**，再报 **1108 行 → 应 484 行**）。
   症状是"文件看着没问题，自检 FAIL 说不是规范格式"。
2. **重跑 `melody_gen` 必须带 `--tonic`（主题包 `key.pc`）**：漏了它会自己 infer_scale，
   实测 `101_neon_drive` 旋律中位 **79 → 64**、跨度 69–84 → 45–93，连带把
   "旋律被伴奏盖住"从 11% 顶到 **23%**。参数要从 `song.json` 的 `melody_gen` 元数据原样取。
3. **段数必须等于主题包 `form.plan` 的段数**，否则 `imitate_path_marked` FAIL
   （除非真的走模仿路径、写了 `basis.structure_source="imitate:<参考曲>"`）。
   我给 15 个包改首段时**特意只改长度/名字、不改段数**，就是为了不连累已生成的曲子。
4. **同一旋律名的段必须等小节数**：4 小节的段复用 8 小节的旋律 → `check_song` 报
   "有 N 个音的小节号 ≥ 该段小节数"（旋律小节号是**段内**的）。
5. **和弦名用升号**（`G#` / `D#` / `A#`）：降号拼写会被解析成 `A`/`E`/`B` → `chord_bass_matches_root` FAIL。
6. **`arr.perc = 0` 是"全曲极简"总闸**（会同时削贝斯/钢琴/琶音），不能用它表达"这段没鼓"——
   要在 `drum_grid` 里给该段一套空型。
7. **`drum_grid` 格式**：`{'kick': [[格, 力度] 或 [格, 力度, GM鼓件]], ...}`；`per_section` 每段一套；
   `per_bar` 每项必须是**按鼓件分键的 dict**（写成扁平 list 会 `.get` 报错，报错信息完全看不出是格式问题）。
8. **`density_dynamic_range` 门 ≥8 倍**：全曲音/小节起伏不够会被判"密度太平"（引子/尾声要真稀疏）。
9. **`probe_playable` 报的 "song.json 与 .mid 音符数不一致"是全库既有现象**（出口的音域夹取移八度），
   不是新问题，别去追。
10. **还原/扒带曲（`notes_extra` 存在、无 `melody_gen`）不许被引擎"美化"**：`yield_to_melody` 已按
    `melody_gen` 标记 gate 掉；新加的 `comp_vary` / `drum_fill` 是 opt-in，**别对那 6 首开**。

---

## 5. 产物与读数（全部绝对路径）

**引擎 / 数据（仓库内，未提交）**
- `D:\software\skill\music-gen\scripts\melody_gen.py`（+181 行：细胞层 · 音域窗口 · 同音串/动机罚 · 逐段密度）
- `D:\software\skill\music-gen\scripts\song_engine.py`（+207 行：`yield_to_melody` · `shape_intro` · `vary_comp` · `drum_fill`）
- `D:\software\skill\music-gen\scripts\new_song.py`（+25 行：`intro_style_for` + 写 `arr.intro_style`）
- `D:\software\skill\music-gen\refs\themes\*.json`（15 个包：`form.plan[0]` + `form.intro_note`）

**曲目**：`D:\software\skill\music-gen\songs_direct\{100..115}_*\`（16 首，已重渲到"引子手法"这一版）
**基准备份**：`D:\test\_tmp-rhythm\baseline\<曲目>\`（100–108 的**最早**成品 mid/ogg/song.json）
**SHA256 台账**：`D:\test\_tmp-rhythm\baseline_100-108.json`
**交付夹（未刷新）**：`D:\test\100-108_重做_音轨\` · `D:\test\109-114_新歌_音轨\` · `D:\test\115_three_faces_交付\`

**读数 / 日志（UTF-8，用 read 工具看）**
- `D:\test\_tmp-rhythm\compare9.txt`（逐轨对照）· `sep_lib.txt` / `sep_check.txt`（音区分离）
- `D:\test\_tmp-rhythm\intro9s.txt` · `intro_fingerprint.txt`（开头）· `intro_fix.log` / `intro_fix2.log`
- `D:\test\_tmp-rhythm\three_faces_check.txt`（115 逐段风格对照）· `three_faces_final.log`
- `D:\test\_tmp-rhythm\newsongs_metrics.txt`（109–114）· `selfcheck_new6.txt`
- `D:\test\_tmp-rhythm\selftest_handoff.log`（本轮最后一次自检）

---

## 6. 环境（**别浪费一轮去试**）

- **`D:\test\_tmp\` 沙箱受限进程不可写**（连建子目录都被拒）→ 本轮产物一律放 `D:\test\_tmp-rhythm\`。
- **Git Bash 在本沙箱起不来**（`couldn't create signal pipe, Win32 error 5`）→ 全程用仓库 venv 的 python
  （`D:\software\skill\music-gen\.venv\Scripts\python.exe`）。
- **控制台是 GBK**：python 打印中文/`✓` 会 `UnicodeEncodeError` 或乱码 → **脚本一律把报告写 UTF-8 文件**，
  再用 read 工具看；需要 print 就 `encode("ascii","replace")`。
- **面板**：`http://127.0.0.1:8765`（当前在跑，22 首）。`new_song` / `make_song` 会经 `studio_guard`
  自动拉起并**默认把生成委托给面板 API** —— 手敲 CLI 就等于在面板里建任务（`BGM_CLI_DIRECT=1` 才是直连）。
- 分钟级任务（重生成 16 首 ≈ 8~10 分钟）→ 用后台 job，别静默等。

---

## 7. 没验证什么（交付时必须照抄给用户）

- **听过的一个都没有**：本轮所有结论都是客观读数，没有一条听感结论（我听不了音频）。
- `drums_first` 那两首（**102_waltz_court 圆舞曲 / 103_sorrow_letter 悲伤抒情**）是"纯鼓开场 2 秒"，
  音色上是否跟曲风搭**必须人耳判断**，这是最该先听的两首。
- **音频级频谱体检没做**（`--fast` 跳过渲染类检查）：伴奏让位 + 引子清空有可能让 5–18kHz 变薄。
- ① ② ④ **代码未启用、未跑、未验收**（见 §1）。
- `theme_pack.py` 的 plan 生成器未改 → 主题包重建会退回旧行为。
