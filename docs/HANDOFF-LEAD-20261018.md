# HANDOFF：主奏音色与听感事故（2026-10-18）

> **上一份**：`docs/HANDOFF-A1-20261017.md`（MIDI → 引擎 / 自动化轨，§6 的 1–7 已在今天全部收口）。
> 这份只讲**今天这一轮**：§6 收口 · 用户听感事故三连（和声 / 跳进 / 主奏音色）· 三条度量口径的修正。
> 先读 §1，再按 §4 挑活。

## 0. 一句话

用户对"直接生成的曲子"只说得出"**高音那个音轨很奇怪 / 奇怪地升高 / 和周围不协调**"。
这一轮把这三句拆成了**可量的原因**：慢起音主奏（418.5ms 撑不住 232ms 的音）· 重复段换了和弦
（同一条旋律在 B2 上只剩 39% 弦内音）· 旋律 74↔84 反复跳（35 处上行大跳里 30 处落 84）。
**慢起音主奏已做成默认拒绝**，重复段和声修了生成器，旋律跳进只出了对照版（用户没采纳）。

## 1. 接手先看（状态）

| 项 | 状态 |
|---|---|
| **曲库** | `songs_direct` 只有 3 首：**`123`**（gorgeous·16 段·120 小节·242s，主奏 = **GM41 中提琴**）· **`BGM29`**（用户 23:56 新提取）· **`BGM36`**（示例/还原曲） |
| **面板** | `http://127.0.0.1:8765` 在跑（**PID 37932**）。改 `web/*.js` → 升 `?v=` + `python scripts\asset_versions.py --write`；改 `server.py` → 重启 |
| **自检** | `selftest.py --fast` = **252/262**（10-18 09:00 复跑）。10 项红**全部**是"曲库只有 3 首 → 夹具太少空转"（`track_balance`/`section_transition`/`melody_distinct`/`melody_lang_diverse`/`accompaniment_harmony`/`melody_voice_leading`/`theme_ref_consistency`/`bass_register`/`sustain_criteria`/`theme_fit`）；原来的真红 `velocity_measured`（`BGM29` Piano）**已清**（见 §4-1） |
| **A/B 素材** | `D:\test\123-ab\`：`123b`（呼吸口）· `123c`（和声对齐）· `123d`（跳进收敛）· `123e`（长笛）· `123f`（小号）· `123g`（中提琴=定稿）· `123h`（弦乐群）· `_backup\123.song.original_violin.json` |
| **临时目录** | `D:\test\_tmp\handoff-a1\`：本轮全部日志/脚本/BGM36 备份（可整目录删，但 `123-ab` 别删） |
| **未跑的事** | 全量 `mutation_check.py`（需要"带 melody 的生成曲"当夹具；现成夹具已备在 `D:\test\_tmp\handoff-a1\fixture\zz_mut_fixture`，**还没跑**） |

## 2. 这一轮做了什么

### 2a. `HANDOFF-A1` §6 的 1/2/4/5/6/7 —— 六项全收口

| 项 | 落点 | 守卫 / 变异 | 读数 |
|---|---|---|---|
| 1 Type 2 / `.kar` | `midi_probe.SUPPORTED_FORMATS`（Type 2 拒绝并说"导成 0/1"）· `LYRIC_META=0x05` 只统计不导入 · `midi_file` 带 `lyrics` | `midi_type2_kar` / 2 | Type 2 拒绝 · `.kar` 4 音全对 · 歌词 3 行"已忽略" |
| 2 automation 面板 | `app.js` 自动化段重写：撤销/重做（Ctrl+Z/Y，只回滚 automation）· Shift+拖框选 · Del 删选中 · 多选整体拖 · 参数 5→11（+CC2/10/71/74/91/93） | `automation_panel_contracts` / 3 | Node 打桩 15 项断言全绿；版本号 `app.js?v=34` |
| 4 `mix_ramp` | `new_song.build_from_theme` 的 patterns 加 `'mix_ramp': 0.5`（**引擎默认仍 0**，老曲字节不变） | `mix_ramp_for_new_songs` / 1 | 新歌 16/16 段带 `arr.mix`；引擎无键仍 2 点 |
| 5 弯音掩蔽 | 新工具 `scripts\bend_masking_probe.py`（同曲两版之差 + 窄带 Δ247，先 solo 自检） | `bend_masking_probe` / 2 | solo Δ247 **+34.0dB**；dense **+0.1dB** 且 pyin 量不到 F0 ⇒ **写在被压住的轨上等于没写** |
| 6 提取→编辑器 | `create.js` 提取 `done` 后 1.5s 跳 `/editor?song=<id>`；`ed.js` 新增该深链接 | `extract_editor_autopen` / 2 | 仅成功才跳；用户实测**已看到自动打开** |
| 7 既有红 | `measure_velocity` 加整轨 RMS 门槛 −60dB（空分轨不再被判"可以校准"）· `harmony_add_contracts` 改自造夹具 · BGM36 Bass 写带数字的 `velocity_exempt` | `measure_velocity_empty_stem` / 1 · `harmony_add_contracts` / 既有 | 两项红都已转绿 |

### 2b. 听感事故三连（用户现场，全部落到数字）

1. **慢起音主奏**（`PITFALLS` **371**）：`123` 的 16 段 `arr.melody_prog` 全是 **40 小提琴**——
   指纹说明是**面板里手工点的**（`programs.Melody` 仍是主题默认 56）。开头第一个音
   （1.39s · F5 · **232ms**）而小提琴**单音到 90% 峰值 418.5ms** ⇒ 音符结束音色还没起来。
   **单音实测表**：40 小提琴 **418.5** · 110 民间提琴 415.7 · 42 大提琴 395.9 · 48 弦乐群 342.1 ·
   45 拨弦 73.9 · 73 长笛 73.9 · **41 中提琴 62.7** · 0 钢琴 46.1（ms）。
   **用户裁定**：听完 8 版 →"**123g 好一点**"（中提琴 41）+ 原话"**GM 音源小提琴太垃圾了标记一下**"。
   ⇒ `new_song.LEAD_BLOCKED = {40, 110}` **默认拒绝**（提示带 418.5ms + 替代 41/48/73），
   覆盖需 `--allow-bad-lead`；守卫 `lead_bad_timbre_blocked` + 变异 1；`123` 已定为 41，
   并写 `patterns.lead_prog_source` 留痕（该键已进 `PAT_KEYS`，否则每次渲染都刷"不认识"）。
2. **重复段换和弦**（`PITFALLS` **370**）：`B/B2/B3` 共用**同一条旋律**，却因
   `prog_index_for_section()` 按**完整段名**哈希拿到 **3 套不同进行** ⇒ 弦内率
   **B 94% / B2 39% / B3 50%**。修法：哈希前取**段基名**（`re.sub(r'\d+$','',name)`）。
   守卫 `theme_progression_repeat_consistent` + 变异 2（退回完整段名 / 塌成一条）。
   对照版 `123c` = 弦内 **78%→90%**、半音摩擦 **24→12**（用户未采纳，素材在 `123-ab`）。
3. **旋律跳进**：上行 ≥7 半音的跳 **35 处，30 处落点 84（C6）**；相邻小节重心跳 ≥5 半音的 30 处。
   对照版 `123d` = 大跳 **35→2**、弦内 90%（用户未采纳）。

### 2c. 度量口径修正（三条，都带证据）

| 口径 | 原来 | 现在 | 依据 |
|---|---|---|---|
| `probe_playable` 两个入口容差 | 3% | **6%** | `123` 实测 **3.57%**（1512 vs 1458）：生成层 `joint_cadence` 删句尾 / `legato_fill` 补空隙**本来就会让源数据与 .mid 有差**；"删一整条轨 = 12.7%"的自证仍在，牙齿没掉 |
| `measure_velocity --self-test` | 只看"有没有音 > −60dB" | 先看**整轨 RMS < −60dB = 没有内容** | BGM36 `bass.wav` **−82.8dB** 却因 6 个串音峰被判"可以校准" |
| `harmony_add_contracts` 夹具 | 从曲库挑 ≥4 段的曲目 | **自造**带生成层的曲子 | 曲库归档后只剩还原曲，还原曲每层被 `notes_extra` 整体覆盖，`harmony_add` 天然无处可加 |

## 3. 关键读数（可复查）

```powershell
$py = 'D:\software\skill\music-gen\.venv\Scripts\python.exe'
cd 'D:\software\skill\music-gen'
& $py scripts\selftest.py --fast                      # 251/262（11 红见 §1）
& $py scripts\bend_masking_probe.py                   # 弯音掩蔽：solo +34.0dB / dense +0.1dB
& $py scripts\probe_melody_health.py 123              # 2.0 音/小节 · 同音 12% · 碎音 0% · 落点 12 格
& $py scripts\melody_profile.py --compare songs_direct\123\song.json refs\themes\gorgeous_melody.json
                                                      # （该 CLI 会崩在 scale_degree_hist，六项读数已打出）
```

`123` 的逐段弦内率（修前）：`Intro 100 · A 93 · B 94 · A2 93 · B2 39 · A3 93 · C 72 · A4 93 · B3 50 …
B2 39 · A3 93 · C 72 · Outro 75`（%）。
`123` 的逐段音符数：`A 177 · B 245 · B2 345 · C 251 …`，第 13 段（3:05）曾只有 **50**（呼吸口落在重复段上）。

## 4. 没做 / 下一步（按价值）

1. ✅ **`BGM29` 的 `velocity_measured` 已清**（2026-10-18 09:00，自检 251→**252**/262）。
   **两条路都没走那条假的**：`piano.wav` 整轨 **−75.4dB**（空），`other.wav` 虽 −24.3dB 有内容、
   `--self-test` 也判"可以校准"，但**音符时刻的峰 −16.5dB ≈ 随机时刻 −16.0dB**（`Hook` 在
   `guitar.wav` 上同口径 **+4.8dB**）⇒ 那是垫子层，拿它量等于把伴奏起伏当钢琴力度。
   真凶是**记录过期**：一键链 `vel` 阶段的 `vel_from_ref --apply` 把（`Bass` 85 种 28~112 →
   **10 种 102~111** · `Piano` 恒 100 → 12 种 99~110），却没改 `velocity_source`（Piano 还留着
   `FAILED`）。修法：`vel_from_ref` 自己把来源改成链式 `<原来源> → vel_from_ref(<ref>)`（幂等、
   `FAILED` 改写），守卫 `t_restore_oneshot_chain` + 变异 2 条；BGM29 八轨来源已改写、
   **音符一个没动 ⇒ 音频不需要重渲**。全部数字见 `PITFALLS` **372**。
   **重扒复核（09:10–09:15，用户点头"只留结论、不动曲"）**：① 同音频重转，逐通道 96–100% 重合
   （Piano 441→443、重合 **100%**）⇒ 转录稳定，不是噪声；② 去鼓后重分离 `piano.wav` −75.4→**−57.9dB**
   （仍低于 −60 门，音符时刻峰 −57.1 ≈ 随机 −57.4）；③ 去鼓后重转 Piano 441→354 只重合 **24%**，
   而 `Hook` 277→**3276**、`Drums` 2663→**697** ⇒ 去鼓版不是"更干净"；④ 441 音**无一独奏时刻**。
   ⇒ **重扒补不出这层的独立动态**，BGM29 保持原样（记录已写进 `songs_direct\BGM29\notes.md`）。
2. **`123c` / `123d` 未被采纳但已量好**：和声对齐（B2/B3 弦内 39%→94%）· 跳进收敛（35→2）。
   素材在 `D:\test\123-ab\`，用户想听随时可拷回曲库。
3. **全量 `mutation_check.py` 仍未跑通**：需要"带 melody 的生成曲"当夹具（§9-4 记过崩在哪两条）。
   现成夹具：`D:\test\_tmp\handoff-a1\fixture\zz_mut_fixture`（当前生成器现造，不在曲库）。
   跑法：拷进 `songs_direct\`（名字不以 `_` 开头）→ 跑全量 → 移出。**会占 CPU ~10 分钟**。
4. automation 面板还缺"整条曲线缩放/平移 · 吸附到网格"（框选后只能整体拖）。
5. `bend_masking_probe` 只测了 "Strings 长音 vs 4 轨伴奏" 一种编制；阈值曲线要 `--depth` 多档 + 多编制。

## 5. 踩过的坑（这一轮真踩，别再踩）

1. **子串断言会假绿**：守卫写 `"S.auto.rect" in js`，变异把名字改成 `S.auto.rectX` **仍然命中** ⇒
   要断言**赋值那句**（`S.auto.rect = {x0`）。同族 `PITFALLS` 285/349/365。
2. **文本变异对"已 import 的模块"无效**：`_text_mut(new_song.py)` 改磁盘，但 `new_song` 早就在进程里
   ⇒ 注入等于没注入（连"未注入"那条也报 FAIL）。模块级行为要用 `Mut(模块, 函数名, 包装函数)`。
3. **别在混音里量音色**：整曲里量开头那个音得到 **635.8ms**，单音口径只有 **418.5ms** ——
   混音包络被伴奏/限幅拉长，量到的是别人的起音。**判音色必须只渲染那一个音**。
4. **弯音 8192 会被引擎拒**（上限 **8191**）：探针第一版就这么崩的；`--depth 2` 正好顶到上限。
5. **`melody` 的小节号是段内 0 基**（写成全局号 → 音**静默不发声**，自检当场报"≥ 该段小节数"）。
6. **"全塌成一条进行"漏检**：首个版本把 distinct 写到"所有旋律组"上，`A` 组用进行 0 ⇒
   "B/C 都塌成 1" 仍满足 ≥2 种。要**只看非 A 的对比组**（这条注入漏过一次）。
7. **`arr.mix` 挂在空轨上**：`123` 有 **100 处**段级 CC7 写给空轨 `Glock`，被新加的报警抓出来
   （`!! automation 被丢弃：轨 'Glock' 没有音符`）—— 无害但说明新歌在给不存在的层写推子。

## 6. 接手第一件事

```powershell
# 1) 确认状态（曲库应只有 123 / BGM29 / BGM36；面板 8765 在跑）
Get-ChildItem 'D:\software\skill\music-gen\songs_direct' -Name
# 2) 跑一遍基线（251/262 是当前正常值）
& 'D:\software\skill\music-gen\.venv\Scripts\python.exe' `
   'D:\software\skill\music-gen\scripts\selftest.py' --fast
# 3) 从 §4 挑一项开工（推荐 1：那条红是用户自己的曲子）
```

## 7. 文件清单（本轮改过）

| 文件（绝对路径） | 改动 |
|---|---|
| `D:\software\skill\music-gen\scripts\song_engine.py` | `automation_dropped` / `section_mix_points` / 空轨 automation 报警；`PAT_KEYS` 加 `lead_prog_source` |
| `D:\software\skill\music-gen\scripts\new_song.py` | 重复段和弦按段基名（`prog_index_for_section`）· `LEAD_ATTACK_MS` / `LEAD_BLOCKED` + 默认拒绝 + `--allow-bad-lead` · 新歌默认 `mix_ramp=0.5` |
| `D:\software\skill\music-gen\scripts\midi_probe.py` | `SUPPORTED_FORMATS`（Type 2 拒绝）· `LYRIC_META` 歌词记录 |
| `D:\software\skill\music-gen\scripts\midi_file.py` / `midi_to_song.py` | 带 `lyrics`；导入时报"已忽略 N 行" |
| `D:\software\skill\music-gen\scripts\measure_velocity.py` | 整轨 RMS 门槛 `_RMS_FLOOR_DB=-60` |
| `D:\software\skill\music-gen\scripts\bend_masking_probe.py` | **新增**工具（弯音掩蔽探针） |
| `D:\software\skill\music-gen\scripts\selftest.py` | 7 个新检查 + `probe_playable` 容差 6% + `lead_override_contracts` 改用 73 |
| `D:\software\skill\music-gen\scripts\mutation_check.py` | 13 条新变异（全部实测抓得到） |
| `D:\software\skill\music-gen\studio\web\app.js` / `index.html` | automation 面板重写 + 两个按钮 + 6 个新 CC（`?v=34`） |
| `D:\software\skill\music-gen\studio\web\create.js` / `ed.js` | 提取完自动打开编辑器 + `/editor?song=` 深链接（`?v=22` / `?v=16`） |
| `D:\software\skill\music-gen\PITFALLS.md` | **368–371** 四条（空轨 automation / 弯音被埋 / 重复段换和弦 / 慢起音主奏） |
| `D:\software\skill\music-gen\README.md` / `docs\SONG-FORMAT.md` / `docs\DOC-MAP.md` | 工具登记 / 自动化两条边界 / 地图重生成 |
| `D:\software\skill\music-gen\docs\HANDOFF-A1-20261017.md` | §6 全勾 · §9/§10/§11/§11b 记本轮读数 |

预算：`README` 28300→**28800** · `SONG-FORMAT` 3000→**3100** · `PITFALLS` 96000→**97000**（各自 `LIMITS` 行尾都写了压缩目标）。
