# music-gen —— 程序化作曲 / 扒谱 / 真音源渲染工具链

BGM 制作与分析的完整管线。**任何新对话（或新的人）从这个文件开始读就能接手全部工作。**

> **这套东西怎么被触发**：说「音乐 / song / music / 歌 / 曲子 / 写歌 / BGM / 扒谱 /
> 复刻某曲 / 还原某曲 / 扒成 MIDI / 渲染 MIDI」等**任一**即可加载 `bgm-studio` 技能
> （**没有自动匹配，全靠模型按 description 判断**）；**完整词表与加载机制** →
> `skill/bgm-studio/README.md`。

> ## ⚠ 别整篇读这个文件（300 行 ≈6k token）—— 按下面这张地图读你需要的 1~2 节
>
> | 你要做的事 | 读哪节 |
> |---|---|
> | **写一首新歌（本工具链内的唯一合规路径）** | **`docs/THEME-PACK.md`**（主题模板包 = 依据；混音目标）+ 下面 §1 的四步 |
> | **照着某首参考曲写（模仿写歌）** | **`docs/IMITATE-PATH.md`** + `scripts\imitate_plan.py`（与"直接作曲 / 还原"的分界见该文首表） |
> | 要写/改 `song.json`（格式与风格预设） | **`docs/SONG-FORMAT.md`** |
> | 只是重出某首歌的成品 | §1 里第 ③ 步那条命令（其余不用看） |
> | 想省 token / 问"为什么写歌变贵" | §1 里「省 token 的三条硬规矩」+ `python scripts\token_audit.py` |
> | 出现症状（有杂音/错音/调参不收敛/工具崩） | **`PITFALLS.md`**，按编号查（本文件只留最常踩的 11 条） |
> | 想知道修过哪些 bug · 要完整命令与开关 | **`HISTORY.md`** · **`CHEATSHEET.md`** |
> | **要新增文档/工具/坑**（往哪写、动哪些守卫） | **`docs/CONVENTION.md`** |
> | 要改引擎/加工具 · 想知道可信到什么程度 | §2 工具清单 + §6 渲染参数 + `selftest.py` · 「验证状态与残余风险」 |
>
> 目录在 §0，产物命名在 §3，参考曲画像在 §5。**排查问题才需要 `PITFALLS.md`，写歌不需要。**

---

## 0. 目录结构与环境

```
music-gen\
├── README.md            本文件（总索引，**按顶部地图按需读**）
├── PITFALLS.md          踩坑台账（出症状时按编号查；旧条目在 PITFALLS-ARCHIVE.md）
├── HISTORY.md           开发经过 + 修过的全部 bug（写代码/排查才看）
├── .venv\               依赖环境（numpy + soundfile + imageio-ffmpeg）
├── vendor\              外部二进制：fluidsynth.exe + GeneralUser GS 音源
├── refs\                参考曲画像缓存（对比时不必再读参考曲）
├── scripts\             引擎 + 分析/体检/渲染 + 自检/预演/变异/成本审计
└── songs\               每首歌一个文件夹（song.json + MIDI + 成品音频 + notes.md）
```

**每曲的 `notes.md` 写了调性/速度/结构/复现命令/实测对比**，接手先读它。
`render.json` 存该曲的渲染参数（`make_song.py` 直接读，不用在命令行重复）。

**clone 后先双击 `setup.cmd`**（自动建环境 + 取音源 + 自检），细节见 `INSTALL.md`；运行时一律用 `.venv\Scripts\python.exe`。

---

## 1. 一分钟上手（**新歌只写一个 JSON，不写代码**）

> **先选路径（三条，依据不同，混用会静默走样）**：
> ① **直接作曲**（"来一首 BGM"）= 本节四步，依据**主题模板包**；
> ② **模仿写歌**（"照着某首做"）= 同样先出骨架，**段落层改用 `scripts\imitate_plan.py`
> 按单首参考曲的实测结构重写** → `docs/IMITATE-PATH.md`；
> ③ **还原扒带**（"扒成 MIDI"）= 抄参考曲的**音符** → `docs/RESTORE-METHOD.md`。
> ⚠ **只改 `--ref` ≠ 模仿**（`--ref` 只是混音目标）；**拿音频当模板依据不合规**（`check_song` 拦）。

**改完任何东西先跑一次自检**（覆盖导入/数据/编码/DSP 方向/语义/端到端渲染/产物/文档）：

```powershell
& $py scripts\selftest.py          # 全量（含一次极小渲染，约 30 秒）
& $py scripts\selftest.py --fast   # 秒级（跳过渲染）

# 动过引擎/风格/渲染管线之后，再跑一次"新歌预演"：用全新参考曲把 5 套风格 + 边界情况
# 从零走完整流程（作曲→渲染→自动调参→验收），并核对文档里的命令行开关是否真实存在
& $py scripts\rehearsal.py         # 约 4 分钟
```

**以后写歌只有这四步**（别走别的路）：

```powershell
& $py scripts\theme_pack.py <主题>            # ① 模板包：同主题 ≥8 首 MIDI 模板聚合（--list-themes 看主题）
& $py scripts\new_song.py 08_x --theme <主题>  # ② 依据模板包出 song.json + 旋律（自动填 BPM/和声/编制）
#   只改 songs\08_x\song.json（chords / melody / sections）
& $py scripts\make_song.py 08_x --check      # ③ 2 秒查数据 → ④ 作曲+渲染+调参+成绩单
```

**模板依据是硬规矩**：一次生成必须依据**同主题的多首模板**，来源只能是 `refs/midi2/`
（`fetch_midi_lib.py` 从 BitMidi/VGMusic/Mutopia 抓的，带来源 URL）或网络权威数据 ——
不许拿"自己生成的曲子"当模板（老 `--from` 路径会留痕并在 `check_song` 里报不合规）。

**这两步省的是大头**：主题模板包让"和声/速度/节奏/配器"一次到位；`make_song --check`
渲染前 2 秒查完数据判据，可先 `check_song.py <曲> --fix` 自动修（和弦音集/强拍）。

完整的命令示例（含 `--no-tune`、分段拟合流程）见 **`CHEATSHEET.md`**。
**改完编配觉得"没变化"** → 先跑 `bands_abs.py`（绝对口径 + 占用率）与 `probe_timbre.py --solo`（谁在整混里真响），见坑 103。

**自动调参是省 token 的核心**：`make_song.py` 内部闭环「渲染 → 与画像比 → 修 EQ/宽度/响度
→ 再渲染」，≤6 轮、通常 1–2 轮 `✓ 达标`（各频段差 <1.5dB），参数写回 `render.json`，**不占对话轮次**。

**耗时**：单轮渲染 **≈29s**、autotune **≈31s**（313 秒的歌；提速前 101.8s／112.6s）。
长曲（>3 分钟）**走后台跑**——前台 120s 上限会把 autotune 杀在半路（见坑 65；该上限若调整，这条要复核）。

EQ 参数有保守上限（`low ≤9 / mid_db ≤10 / shelf ≤10`）：差距 >4dB 通常是**编配缺能量**
而不是 EQ 不够，硬推只会变刺耳。到顶时它会直接给出该改哪一轨的建议，例如：

```
  第1轮: 质心3572 宽度0.489  ✓ 达标
  ⚠ EQ 到头了，剩下的差距要靠编配（改 song.json）：
    - 315-1250Hz 差 +2.5dB（EQ 已到顶）→ 钢琴/吉他轨：mix.Piano / mix.Hook 音量
```

### `song.json` 的格式与风格预设

**见 `docs/SONG-FORMAT.md`** —— 结构、`bass_style`/`perc_style` 速查、5 套风格预设、
段落级 `arr.mix` 与 `arr.harmony` 的用法都在那里。**写歌时才读它**，所以没放进常驻预算。

## 2. 工具清单（都在 `scripts\`）

| 脚本 | 作用 |
|---|---|
| `bgm_synth.py` | 加法合成内核 + WAV/MIDI 写出（被 import；也是 04 曲目生成器） |
| `bgm_acoustic.py` | 原声音色库（钢琴/弦垫/贝斯/钢片琴/沙锤/混响/EQ）+ `master()` |
| `song_engine.py` | **引擎：读 song.json → 展开编配 → MIDI**。4 种贝斯/3 种打击风格 + 各生成器 |
| `build_song.py` | **紧凑 spec → song.json**：只写和弦走向+旋律骨架，时值/排列/编制自动推；`--to-spec` 反向导出 |
| `new_song.py` | 新歌脚手架：`--theme <主题>` 按**主题模板包**出 song.json（含跑 melody_gen、填混音目标）；`--from` 只用于复现/改歌 |
| `theme_pack.py` | **主题模板包**：同主题 ≥8 首 MIDI 模板（来源白名单 + 可溯源）聚合成和声/节奏/配器/曲式/旋律画像 + **混音目标** → `refs/themes/` |
| `imitate_plan.py` | **模仿写歌的段落层入口**（2026-09-19）：按**单首参考曲**的实测结构（`--plan` 结构表：进行 / 每段 density / 编配 / 主奏音色）重写 `songs/<曲>/song.json` 的 sections+chords+patterns。写盘前**硬校验**：段名只能用 A–E（`role_of_section` 取段名里第一个 a–e 字母，`Rise`/`Peak` 会全落 `E`）、同角色 = 同进行 + 同旋律、段数/和弦数/参数范围；和弦没变时**保留旋律**。留痕 `basis.structure_source="imitate:<参考曲>"`（守卫 `t_imitate_path_marked` 照它判"结构改过却没走模仿路径"）→ 口径见 `docs/IMITATE-PATH.md` |
| `identify_ref.py` | **参考曲识别**（2026-09-19，调 `.venv-ml` 的 Demucs + YourMT3）：两路独立来源交叉 —— ① Demucs 6s 分离（GPU ≈40s/首）给逐声部**能量占比/活跃度**；② YourMT3+ 转录（≈75s/首）给 **13 通道音符数**（通道名从转录 MIDI 的轨名读，不猜）。名次差 ≤1 才算"一致"，冲突时**默认信 ymt3**（BGM35 实测：钢琴 demucs 判 7.0%、ymt3 判 27.4%、真值 29.4%）→ `refs/identify/<名字>.json`。**别再用转录通道名或 `probe_timbre --solo` 的高频段推配器** |
| `make_song.py` | **一条命令**：作曲 → 渲染 → 对标成绩单（`--check` 先验数据） |
| `scorecard.py` | 成品 vs 画像 → 一屏差距表 + 调参建议 + 可粘贴重跑命令（`--bpm N` 给真实速度） |
| `rehearsal.py` | **新歌预演**：全新参考曲 × 5 套风格 + 边界情况端到端（对齐 dB 只打印，坑 105） |
| `check_audio.py` | **音频体检**：格式能不能读、**速度的八度层级**与支持度、时长/响度/削波、是否已有画像 |
| `analyze_sections.py` | **分段剖析**：每段倍频程/质心/密度 + 与全曲均值的偏离 → 分段拟合目标（段间对比才是"清脆"来源） |
| `check_song.py` | **渲染前数据校验**（含模板依据白名单）：单曲沙箱跑数据契约判据；`--fix` 自动修；`--all` 查全库 |
| `fix_breathing.py` | **给整库旋律补"换气"**（判据在 `breath.py`）：只在乐句边界收短时值；`--dry` 预览。**改完必须重渲染** |
| `profile_ref.py` | 参考曲剖析 → 缓存成 `refs/<名字>.json`（含倍频程/宽度/质心/16 分节奏型/调式/结构、速度层级与 `level_scores`） |
| `metrics.py` | 共用度量内核（上面两个工具都用它，保证口径一致） |
| `setup_wizard.py` | **一步步的环境向导**（**中/英按系统语言自动切**）：主工具链 → 音源 → 自检 → 写第一首 → ML 环境（约 5.4GB）→ 模型代码+权重 → 试扒一首。`--yes` 全自动 · `--only 5,6` 只跑某几步 · `--lang en` 强制英文；**每步幂等**，随时可重跑 |
| `cli_utf8.py` | 控制台编码兜底（GBK 下打印 `✓` 会崩）——所有入口脚本启动即调用，见坑 58 |
| `studio_guard.py` | **面板守卫 + A′「面板是唯一入口」**：① 生成开工前探活 `127.0.0.1:8765`，不在跑就 **detached** 拉起（`start.cmd` 是阻塞前台，从脚本里调会连生成一起卡）—— 接在 `new_song` / `make_song` / `melody_gen` 的 `main()`；② **默认把生成/渲染委托给面板 API**（`/api/new`、`/api/job?kind=render-tune`）：手敲 CLI 就等于在面板里建任务，GUI 全程可见、产物立刻能听。开关：`BGM_STUDIO_INNER=1` 面板内部走原生（**防递归**）· `BGM_CLI_DIRECT=1` 批量直连 · `BGM_NO_PANEL=1` 整段跳过。判据：自检 `panel_guard_wired` / `panel_is_only_entry` |
| `i18n_check.py` | **面板中英切换的覆盖率守卫**：抓 `studio/web/{index,ed}.html` 里每一条含汉字的文案（文本 + `title`/`placeholder`）逐条比对 `studio/web/i18n.js` 的字典，**漏一条退出码 1**。存在的理由：漏翻**只有英文环境看得见**，中文系统下怎么点都不暴露（详见 `studio/README.md`） |
| `melody_profile.py` | **扒"旋律语言"**（音级/音程/时值/落点）+ **调内率自检**（<80% 就报"别用"）。在 Demucs 的 other 声部上跑：66%→93% |
| `melody_gen.py` | **按画像生成旋律**：句长/落点/时值/音程都从画像的**分布**抽样（句末留休止），强拍强制吸附和弦音；`--avoid`+`--candidates` 与库里已有旋律去重；每首一组个性参数 |
| `probe_melody_lang.py` / `probe_melody_health.py` | **旋律体检**：①语言分布重合度（≥85%=孪生）②形态——密度/同音/最长同音串/碎音/强拍 |
| `stem_compare.py` / `layer_exp.py` | **分轨体检 / 层次实验**：占用率+动态逐声部对比（判"像不像"的主尺子）；离线叠层测"加这层有没有用" |
| `similarity.py` / `band_match.py` | **还原度总分 0–100**（和弦/节奏格/密度/倍频程/音色/段间变化六轴，改前先拿基线）；`band_match` 对齐**成片**的频谱倾斜（孤立一两带差是编配问题，别硬填，见 `docs/MAKE-IT-SOUND-ALIKE.md`） |
| `audit.py` | **全维度体检**（用户 2026-09-16："最开始就发现所有要注意的元素"）：结构/频谱/演奏/编配四层一次跑完，逐项给"达标/偏差/**测不到**"三态；`--midi` 才查力度与时值。参考线与 8 条必须人耳的项见 `docs/AUDIT-CHECKLIST.md`（调研与落地方案见 `docs/UNMEASURABLE-SOLUTIONS.md`） |
| `groove_probe.py` / `section_gain.py` | **微时序探针**（起音偏离最近 16 分格的量 → 逐格偏移表；实测 BGM35 是贴格子的直拍 −4.9ms、无 swing）；**逐段响度对齐**（把每 4/8 小节的响度压到参考曲上；⚠ 实测**全段对齐会压平 `variation`** 84.6→34.9，默认不接渲染链） |
| `song_density.py` / `inject_density_curve.py` | **段间密度曲线**：从同主题多份模板 MIDI 量"每 8 小节音符数"→ `arr.density` 档；`inject_*` 只往现有主题包补这条曲线、**不动其他字段**（直接重建主题包会连旋律画像一起重算 → 旧曲子与新画像不匹配） |
| `bands_abs.py` | **绝对口径对照**：段电平看**绝对 dB**（成绩单那列会被低频厚度平移）+ **占用率**（墙/点）→ 坑 103 |
| `probe_timbre.py` | **音色/单轨探针**：等响度下比候选音色的频段与占用率；`--solo` 逐轨量**真实电平**（揪出"改了没效果"的弱轨） |
| `probe_peaks.py` | **谱峰扒谱**：逐小节低音峰/音级/根音锚定和弦（按 bin 归音级会被低频泄漏带偏） |
| `sf2_lib.py` / `kick_probe.py` | 读音源内部表（`--drums` 列每个鼓组每个键的采样名与秒数）；候选音色渲染单音、量 40–160Hz 尾巴长度 |
| `song_events.py` | **逐轨音符事件出口**（JSON）：面板卷帘 / 排查某轨弹了哪些音 |
| `studio/` | **可视化面板**（`studio\start.cmd` → http://127.0.0.1:8765）：音轨/混音台/卷帘/指标/试听本段/配平/搜索/导出（用法见 `studio/README.md`） |
| `selftest.py` | **全链路自检**（改完东西先跑它） |
| `mutation_check.py` | **变异测试**：注入故障验证自检**真会报警**（改过检查项就跑它；新检查必须配注入用例） |
| `render_midi.py` | **MIDI → 真音源 → WAV → OGG 渲染管线**（搁架/搁低/高通→软限幅→响度归一→中侧加宽→q8 编码） |
| `to_ogg.py` | WAV→OGG（ffmpeg libvorbis q=8；libsndfile 会崩，见坑 5） |
| `analyze_chords.py` | **扒谱**：节拍跟踪 → 逐小节 chroma → 和弦模板匹配 → 罗马数字（`--bpm N` 可强制速度） |
| `analyze_prog.py` | **和弦进行读取**（root-anchored 法）：根音取 80-220Hz 避开底鼓，再定 maj/min/7/sus/6 |
| `analyze_bass.py` | 根音 + 三度倾向 + 音级排序（判断大小调/多利亚色彩） |
| `analyze_ref.py` / `analyze_ref2.py` | 参考曲画像：时长/响度/速度/调性/**倍频程平衡**/立体声宽度/逐 4 秒响度 |
| `probe_style.py` | **风格画像**：安静段干净 chroma + 逐拍 16 分节奏型（底鼓/踩镲）+ 结构 |
| `probe_voicing.py` / `probe_tension.py` | **声部进行 / 张力曲线**：voicing 移动·保持·交叉；逐段调内张力 σ 与头尾走向 |
| `probe_aesthetic.py` / `probe_aqa.py` | **模型侧主观分**（`.venv-ml`，`--all` 全库排序）：CLAP 零样本情绪 / AQA 的 CE·PQ·CU·PC。⚠ 库内区分度极低 → **只揪异常，不当验收门** |
| `section_probe.py` | 分段体检：RMS / 频谱质心 / 6-16k / 低频 / 宽度 / 起音密度（段落地图按曲子硬编码，换曲子要改 `SECS`） |
| `analyze_structure.py` | **按音乐自适应切段**（2026-09-18）：四特征 novelty（响度/亮度/起音/和声）→ 平滑 → 峰值检测 → 边界吸附小节线。`--target-segments N` 控制段数；段长**跟随音乐**，不固定 8 小节（用户口径"古典规整、现代多不等，要看情况"） |
| `transcribe_to_song.py` | **转录 → `song.json`**（还原/扒带的正道入口）：`--auto` 一键串起**段落切分 + 逐小节鼓型 + 逐音力度 + 配额抽样**；五条契约（段内/全局小节号 · 和弦数=小节数 · 一段一键 · 轨名白名单 · `notes_extra` 完整形式） |
| `extract_drum_grid.py` / `measure_velocity.py` | **鼓型提取**（逐小节 `drum_grid.per_bar` —— 引擎 Perc 音数**主要由它决定**，不是 `perc_style`/`arr.perc`）与**逐音力度量取**（分位校准；缺它 = 打字机听感） |
| `extract_theme_timbres.py` | **主题模板的实际音色与配器**（2026-09-18）：扫 8~10 首同主题模板的**轨名 + program**（轨名优先；音域**只在整首都没有可识别轨名时**兜底 —— 否则纯钢琴曲的左手低音会被凭空判成"贝斯声部"）→ `--inject` 把每个声部的音色池写进 `refs/themes/<主题>.json` 的 `arrangement.prog_pool`。⚠ **只改这一个字段**：`mix_target.energy_gain` / `calibration` 是标定流程写的，重跑 `theme_pack.py` 会整包重建、把它们冲掉。`new_song.theme_programs` 靠它把"乐器选择"从 5 套风格预设换成模板真值（classic → 管钟/双簧管；battle → 排箫/钢弦吉他；neon → 方波主音） |
| `transcribe_ymt3.py` / `bp_transcribe.py` | **两个转录模型**：YourMT3+（整段混音直接出多轨 MIDI，`--bsz auto`；⚠ 必须用本脚本，官方 `bsz=8` 慢 2.3×）与 Basic Pitch（Spotify，ONNX 后端，跑在**独立 venv** `D:\test\bp-venv`，不碰 `.venv-ml` 的 torch）。⚠ 二者错误**互不相关**才是价值所在 |
| `eval_transcription.py` / `ensemble_transcribe.py` | **转录评估与集成**：前者是被评 MIDI 对参照的**音符级 F1**（逐段列，`--self-test` 量尺子天花板、`--shift` 判"只是错位"）；后者**多来源交叉验证**后合成一份（实测单来源 0.333 → 集成 0.532）。⚠ 集成修不掉**系统性**错误，要独立方法当尺子 |
| `extract_vocals.py` / `imitate_ref.py` | 人声提取（差分法 + 只修段间过门杂音）与**九段还原链**（第 9 段 = ①识别体检 → ②逐带体检）。⚠ 收尾会**拒绝**"成品路径 == 参考路径"（否则会覆盖参考原曲，见 PITFALLS 208）；`stale()` 是**真比时间戳**的（改了 `song.mid` 会自动重渲染，见 PITFALLS 207） |
| `merge_tracks.py` | **并轨 + 时值下限**（还原链第 5 段末）：把 YMT3 的合成器/键盘通道并进 Piano（`--from "Synth Pad,Organ,…"` → 集成后 9 轨收敛成 5 轨），并给全轨拉时值下限 `--min-beats`（**延长、不删 onset**）。听感"杂乱/不流畅"的两条量化病根见 PITFALLS 206 |
| `bass_ensemble.py` | **多来源集成**：同音高/同 0.1s 格合并 + 按跨来源支持率归一化打分（`--thr`）→ 替换或 `--merge` 合并；`--min-beats` 给时值下限、`--octave-ref` 用 pyin 校八度、`--sub` 补低八度层。⚠ 阈值须**归一化**（`sum(w)/W_TOTAL`）：原写法在三来源权重只有 0.44/0.40/0.07 时，**三源全共识**才 0.908 —— 实测整条 Bass 轨只剩 2 音 |
| `note_dur_stats.py` | **时值体检**：逐轨量音数 / 时值中位（秒）/ **碎音率**（≤0.25 拍占比），可对照参照 MIDI。判读：旋律轨碎音率 <5% 且中位 ≥0.25s；**打击轨 ~100% 是正常的**。听感"杂乱/不流畅"先查它（PITFALLS 206）|
| `arrange_probe.py` | 编配诊断：逐段音高分布、音符密度、亮度指数 |
| `noise_probe.py` | 杂音体检：6-16k 尾巴电平 + 谱平坦度（噪声高、纯音≈0）+ 爆音检测 |
| `midi_probe.py` | MIDI 解析：轨名/音色/速度/音域/音符数/小节数 |
| `fetch_midi_lib.py` | **抓 MIDI 建模板库**（BitMidi/VGMusic/Mutopia）→ 风格目录 + `_index.json`（2 号库 ≈200 首） |
| `play_midi.py` | 不用 DAW 试听 MIDI（Windows 自带 GS Wavetable，`--wait` 等放完，`--stop` 停） |
| `setup_soundfont.py` | 下载并安装 FluidSynth + GeneralUser GS 到 `vendor\` |
| `probe_mirrors.py` / `check_soundfont.py` | GitHub 镜像 / 音源可下载性探测 |

### ML 工具链（可选）：见 `ML.md`

纯 numpy 管线做不到的事（扒旋律、音源分离、母带匹配）用现成工具解决：**Demucs** 分离声部
（扒谱调内率 **66%→93%**，257 秒音频 7 秒）、**matchering** 母带匹配（峰值因数 →**16.3dB**）、
**librosa.pyin** 在这个材料上不如自己写的。环境 `.venv-ml`（Python 3.13 + CUDA），命令见 `ML.md`。

## 验证状态与残余风险（诚实清单）

### 已被证明的

- **自检全绿**；**注入故障全部被抓**（防线不是摆设）：
  和弦错音、通道冲突、风格预设通道冲突、旋律越界、段落和弦数不符、限幅越界、
  MIDI 丢音符、参考画像缺字段、`voicing_shift` 失效、响度契约失效。
  （自检项数随每轮修 bug 增长：现在是 **89 项**，含模板依据白名单、速度来源、编码安全等检查。）
- 端到端：新参考曲扒谱 → 脚手架 → 作曲 → 真音源渲染 → 自动调参 → 对标，5 套风格 + 5 种边界情况全通。
- 交付物：零削波、无 NaN、无直流、尾部无底噪、时长与谱面一致、MIDI 往返无丢音。

### **未被验证的（别假设它们没问题）**

1. **好不好听**：所有指标只证明"与参考曲的频谱/律动/响度对齐"，**不能证明音乐性**。
   最终判据只有耳朵。自动调参能让数字对上，但把和声写错、旋律写难听，它一样报"✓ 达标"。
2. **GM 音源自身的音色问题**：GeneralUser GS 里某些音色本身就偏闷/偏假，指标看不出来。
3. **拍号**：**作曲侧已支持 3/4 与 6/8**（`song.json` 的 `meter`：`[3,4]` → 一小节 3 个四分；
   `[6,8]` → 一小节 6 个八分＝3 个四分。编配/打击乐格数、MIDI 拍号元事件、强拍口径全跟着走，
   3/4 另有地道的圆舞曲写法 `bass_style/perc_style: waltz` + 钢琴自动 pah-pah；
   **4/4 的输出逐字节不变**）；**分析侧仍只支持 4/4** —— 参考曲画像的小节网格、成绩单的
   "节奏型"两行、`melody_gen` 的落点网格都建在 4/4 上，遇到非 4/4 会**显式拒绝**而不是算错。
   其余拍号（5/4、7/8、分母非 4/8）未支持。
4. **极端规模**：**>3 分钟的长曲已交付**（`11_dn75_neon` 307s、`13`/`14` 281s、`12`/`15`/`17`/`22`
   237–256s，一律后台渲染）；**<60 / >200 BPM 仍不算支持** —— 55BPM 实测被自动测速报成 111
   （<60 一侧只报候选层级，靠人工 `--bpm` 钉死）；`>180` 现在**能报就报**（高于窗口上界的层级
   若支持度更高就直接报，210BPM 素材实测报 209.4），报不出来时至少候选在层级阶梯里（坑 104）。
5. **平台**：只在 Windows 验证（`play_midi` 走 winmm、fluidsynth 是 win64 二进制）。
6. **老曲目 01/02**（脚本式作曲期的测试曲，无 `song.json`）只经过"和弦名 vs 音集"校验，
   没有音乐性校验；已标 `legacy: true`（坑 32）**不参与对标**。03/04 等已归档到 `songs\_archive\`。
7. **自检逻辑自身可能有盲点**：第四轮的变异测试就发现 2 条检查项因脚本 bug 而**从未被真正验证**。
   本轮又踩到一次同类：新加的"速度网格"不变量一开始用**音频测速**当判据，结果在连奏编配上
   误报（见坑 56）——**判据本身不可靠时，检查会比没有检查更糟**（会掩盖真问题、制造假问题）。
8. **音频测速（`detect_bpm`）仍不可靠 —— 现在有实测数字**：外部参考曲上与人工钉死值的一致率
   **4%**（23 首：精确 1 / 八度内 3）；本仓库自有曲目上 41%（17 首：精确 7 / 层级 15）。
   所以它只负责"给候选"：`check_audio` 打印**层级阶梯**（本层 + ×2/÷2/×3/÷3 + 支持度），
   `profile_ref.py` 把 `bpm_source: auto(未核对)` 与 `bpm_ladder` 写进画像并提醒复核。
   **速度一律人工 `--bpm` 钉死**（与 `probe_style` 逐拍节奏型对不上就是判错了）；60–180 之外
   还会另报 `window_alt`（坑 104、106）。
9. **你的播放器/DAW**：OGG/MIDI 在你用的软件里是否正常，只有你能确认。
10. **预演的"对齐 dB"已移出判据**：小样只有 4 小节、配器是通用骨架，而参考曲是完整制作，
    实测差距常在 5–20dB（慢速参考曲如 64BPM 的 BGM01/BGM10 更明显；"无打击乐段落"实测
    18.8→15.0dB）—— 拿它当门就是用测不准的尺子判分。现在判据是 **`tune_error`**（`autotune`
    真正优化的**分组**误差之和）"确实变小了，或本来就 ≤3.0"，对齐 dB 只打印并标注"仅供参考"（坑 105）。
11. **旋律"同质化"（已治本）**：根因不是"画像少"而是**生成器把画像差异吃掉了**（换画像只值 42%）。
    已改成真读画像**分布** + 每首一份画像 + 个性参数 + 候选去重：**孪生对 5→0**。
    守卫 `melody_distinct` / `melody_lang_diverse` / `melody_matches_profile`；见坑 109–116。
12. **没有"LLM 直出音乐"这条路（LLM 已在作曲位）**：LLM（或人）只写 `song.json` 的
    `chords`/`melody`/`sections`，编排/织体/CC7/音色由 `song_engine.py` 展开、FluidSynth
    出音频 —— **LLM 不直接产出音频或 MIDI**，也**没接** Suno/Udio/MusicGen 等外部音乐生成
    （模板须可溯源，见 §1）。**没做过的对照**：LLM 直写 MIDI 跳过引擎差多少
    （会丢 9 轨编排与 `check_song` 全部校验）。


## 3. 产物

```
songs\23_d150_skip_along\   d150_skip_along.mid / _sf.wav / _sf.ogg + song.json + spec.json + notes.md
songs\11_dn75_neon\         dn75_neon.mid / _sf.wav / _sf.ogg（5 分钟长曲，走后台渲染）
songs\01_ac150_seaside\     脚本式作曲期遗留（legacy，无 song.json，不参与对标）
songs\_archive\             已归档曲目（03/04/06–09 等，不进交付）
```

**质量分级「很好」的曲目才带成品音频**（开箱可听）；其余只带谱面与文档 —— 跑 `make_song.py <曲目>` 即出音频。

命名约定：`_sf` = 真音源版（FluidSynth + GeneralUser GS）；不带 `_sf` 的是自写合成器试听版。
**MIDI 是上限最高的交付**（可换任何更好的音源），OGG 是能直接听的成品，WAV 可重编码。

## 4. 踩过的坑（完整台账见 `PITFALLS.md`（现行 81–104）+ `PITFALLS-ARCHIVE.md`（1–80），按编号查）

> 这里只留最常踩的 11 条；**其余按症状去 `PITFALLS.md` 查编号**，别整篇读。

2. **母带 `tanh` 必须在归一化之后**。合成后原始混音峰值能到 2.4，直接 `tanh` = 硬削波，
   产生刺耳交调失真（这就是"很多杂音"的真凶之一）。正确顺序：量峰值 → 归一化 → 轻限幅。
4. **噪声层 ≠ 真实感**。曾加过"击弦噪声/弓噪/房间底噪"想模仿实录，结果就是可听的沙沙声。
   要提亮用 EQ 搁架和更多高次分音，**不要用噪声**。沙锤算打击乐（短促离散）不算底噪。
11. **不要用 `Select-Object -First N` 接在生成脚本后面**（尤其 `... | Select-Object -First 2`）：
    PowerShell 拿到 N 条就**终止管道并杀掉上游进程**，会把正在写的 MIDI 截成 0 字节。
    要少看输出就用 `> $null` 或 `Select-Object -Last N`。
12. **别用 PowerShell 的 `Get-Content`/`Set-Content` 改写含中文的 UTF-8 脚本**（会按 GBK 解码再存，直接乱码）。
    改文件用编辑工具，或 Python 读写时显式指定 `encoding='utf-8'`。
20. **`perc_style: none` 会让高频塌掉**：这类小编配里**沙锤是 5–18kHz 的唯一来源**，
    关掉后顶频比参考暗 26dB（已踩两次）。要"无鼓组"用 `perc_style: light` + `perc: 1`（只有沙锤）。
26. **自动调参的 EQ 有上限**：到顶时会打印"该改哪一轨、幅度多少"（`+` 表示本曲偏厚 → 要降）。
    照着改 `song.json` 的 `mix` 比继续推 EQ 有效得多；风格预设里的配比就是这些结论的沉淀。
31. **`write_midi` 对越界数据报错而不是静默回绕**（原来 `note & 0x7F` 会把 128 变成 0，音高悄悄错掉）。
44. **自动调参的"偏厚"判据不能用宽阈值**：原来是 `> TOL+1`，导致 **+1.5~2.5dB 的过厚永远不修**、
    却报"✓ 达标"（和之前的夹紧 bug 同一类：假装收敛）。现在两个方向都用 `TOL`。
48. **夹紧只能有一层**：`tune_step` 先夹一次、autotune 再夹一次，会让"想要 −8.24dB、
    被夹成 0"的信息丢失 → 日志谎报"✓ 达标"而实际还差 10dB。
    **这是"假装收敛"这类 bug 的第三次出现**：现在 `tune_step` 只给原始建议，
    夹紧与"到顶"报告统一在 autotune 一层，并有自检项 `autotune_reports_caps` 守着。
56. **速度要读 MIDI 的 tempo，不能靠音频测速**：`detect_bpm` 对**连奏编配**（竖琴/弦乐/
    合唱，没有明显起音）会误判 —— 实测 gorgeous 编配的 106BPM 样带被读成 **154.3**
    （而它自相关峰值正好落在**一小节**上，音频确实是 106BPM；置信度得分也分不开对错：
    误判 0.289 vs 正确却更低的 0.239，所以设阈值没用）。
    速度判错 → 小节类指标全错位、成绩单还会误报"!! 速度不一致，先改 BPM"。
    现在：`make_song` 从 MIDI 读真实速度（`midi_bpm`）传给 `measure` 与 `scorecard --bpm`，
    只有拿不到速度时才退回测速。
58. **"换个目录/换台机器就崩"是编码问题，不是玄学**：Windows 默认控制台是 GBK，
    脚本打印 `✓`（U+2713，不在 GBK 里）直接 `UnicodeEncodeError` —— **崩在自动调参中途**。
    本地一直没暴露，因为跑之前都设了 `chcp 65001` / `PYTHONIOENCODING=utf-8`；
    一旦在别的对话、别的目录、普通终端里跑就必现（我实测抓到的就是这条）。
    修法：所有入口脚本调用 `cli_utf8.setup()` 把 stdout/stderr 切成 UTF-8 + `errors='replace'`
    （编码不了就降级成 `?`，绝不为一个装饰性字符中断渲染）；捕获子进程输出也统一
    `encoding='utf-8'`（否则父进程按 GBK 解码子进程的 UTF-8 → 乱码，检查项会误判）。
    守卫：自检项 `console_encoding_safe` —— 起真进程（`PYTHONIOENCODING=gbk`）验证救得回来、
    **反向对照**验证不加固确实会崩（防止检查空转）、并静态要求每个入口脚本都调用它。

其余 47 条（渲染/DSP、MIDI、调参、测量、工具流程）在 `PITFALLS.md`。

## 5. 参考曲画像与渲染参数

**见 `docs/SONG-FORMAT.md`**（画像字段含义、`render_midi.py` 的 EQ 参数与推荐值）。

