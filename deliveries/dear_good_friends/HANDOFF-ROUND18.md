# HANDOFF-ROUND18 · **dear_good_friends：V1 全量转录定案 + 「提取即 V1 准度」做成默认**

> 接 `HANDOFF-ROUND17.md`。本轮起点 = R17 §1 那个没答的问题（"**midi 还有点问题**"）。
> **自足**：读这一份就能接着干。**下个对话第一件事 = 看 §6（126s 弦乐，唯一没修完的）。**

---

## 0. 一句话现状

- **V1 已定案并落地**：用户原话「**V1_全量转录.mid 非常好**」。
- **交付三件套（2026-09-26）**：

| 文件 | SHA256（前 16） |
|---|---|
| `song.json`（192347 → **171252** 字节） | `675fd2575a9d3ecd…` |
| `dear_good_friends.mid` | `890d59e9c355f4aa…` |
| `dear_good_friends_sf.ogg` | `0e204ea3aab26c19…` |
| `dear_good_friends_sf.wav` | `fb84612d299e091b…` |

- **唯一改动**：`song.json` 的 `patterns` 加一行 **`"notes_extra_full": true`**。
  渲染配置**一字未改**（GeneralUser + `render_midi` 默认参数 + `--hp 15`，无 CC7）。
- **尺子**：`|RMS差| 0.987 → 0.967` · `chroma 0.8703 → 0.8791` · `timbre_audit 0/15 段有音色问题`。
- **R17 旧版仍在**：`song.json a2e8f107…` / `mid ed46dea1…` / `ogg f69a514a…`（备份见 §7）。
- **音源也已定案**：用户「**GeneralUser 好，就用这个，其它删了**」——
  交付音源 = `GeneralUser GS v1.471.sf2`（`vendor\`，SHA `f45b6b4a68b6bf3d…`），**未改动**。
  R18b 试过 **23 个音源**（全部基于 V1）后排除了其余 22 个；试听件已按用户要求删除。
  **决策记录与全量读数表 → `variants\音源选型\README.md`**（含"指标最好那个音色问题段反而最多"这条）。

---

## 1. 根因（一句话）

**引擎默认把转录（扒带）音符按 `arr.density` 逐小节抽样**（上限 `{1:4, 2:10, 3:18}` 音/小节/轨），
`notes_extra_full` 没开 ⇒ **转录被砍掉 46%**：Piano 984→517 · Hook 482→265 · Bass 47→38。

**用户点名的三段，正是砍得最狠的段落**（这是本轮最强的一条证据）：

| 段落 | `arr.density` | 每轨每小节上限 | Piano 转录→输出 | 保留率 | 用户投诉 |
|---|---|---|---|---|---|
| S01–S04 | 1 | **4** | 37→12 · 23→8 · 37→9 · 53→7 | **32 / 35 / 24 / 13 %** | ★ **0–27s** |
| S05–S06 | 2 | 10 | 63→28 · 66→30 | 44 / 45 % | — |
| S07 | 0→*(bug 取 2)* | 10 | 190→86 | 45 % | ★ **63–71s**（其末 3 小节） |
| S08–S12 | 3 | 18 | 100→50 · 52→52 · 100→85 · 68→55 · 97→64 | **50 / 100 / 85 / 81 / 66 %** | — |
| S13 · S14 · Ending | 1 | **4** | 27→9 · 31→8 · 40→14 | **33 / 26 / 35 %** | ★ **126s+** |

⇒ **投诉的 8 段全是 `density=1`（保留 13~35%）；没投诉的 5 段全是 `density=3`（保留 50~100%）。**

**旁证（文档早就不一致）**：`notes.md` 第 37 行一直写着"渲染后 2503 音：Piano **984** · Hook **482** · Bass **47**"
—— 那是**转录全量**的数字，而交付的 MIDI 一直是抽样后的 `517/265/38`。**R18 把这个不一致消掉了。**

---

## 2. 做了什么：把「提取即 V1 准度」变成默认

用户口径：「**我需要每次提取时都能达到 V1 的准度**」。改了三层（**都只改默认值，不改算法**）：

| 文件 | 改动 |
|---|---|
| `scripts/song_engine.py` | `_full` 默认**翻转为 True**（`notes_extra` 为空时**完全无副作用**，生成类曲目不受影响）。要抽样必须**显式**写 `false` |
| `scripts/transcribe_to_song.py` | 默认写 `notes_extra_full: true`；新增 **`--sample`**（写 `false`）；`--full` 保留但已废弃 |
| `scripts/selftest.py` | **新守卫 `t_restore_notes_full`**：扒带曲（`notes_extra` 非空）**必须显式声明**该字段 —— 不许只吃引擎默认（默认一翻、音频就静默变）；写 `false` 还要配 `patterns.notes_extra_sample_reason`（带实测数字） |
| `scripts/mutation_check.py` | 新变异用例：删掉夹具的该字段 → 守卫必须 FAIL |
| `docs/SONG-FORMAT.md` · `CHEATSHEET.md` · `PITFALLS.md` 坑 255 | 同步口径（`token_audit.LIMITS` 三处**先抬后写**）；`docs/DOC-MAP.md` 已重生成 |

⚠ **库内 4 首扒带曲里，3 首（`siren_end` / `siren_end2` / `c15_chain_repro`）本来就是全量**，
只有 `dear_good_friends` 漏了 ⇒ **默认翻转是"让工具追上既有实践"**，不是引入新行为。

**踩到并当场修掉的一个自造 bug**：`--sample` 第一版只"不写字段"—— 而引擎默认已翻成 True，
**"不写"等于全量 ⇒ `--sample` 会变成空转**。现在两个分支都**显式写盘**。

---

## 3. 独立验证（逐字节，不是"命令 ok"）

| 输入 | 产出 MIDI SHA | 判定 |
|---|---|---|
| **删掉 `notes_extra_full` 字段** | `890d59e9c355f4aa…` | **== V1** ✓ 引擎默认已是全量 |
| 显式 `false` | `ed46dea1d655fc34…` | **== 旧交付**（抽样）✓ 反向开关真的有效 |
| 显式 `true` | `890d59e9c355f4aa…` | == V1 ✓ |

- 交付重生成的 MIDI **== 用户在 DAW 里听过的 `V1_全量转录.mid`**（逐字节）。
- 交付重渲染的 wav **== A/B 里测过的那版 V1 渲染**（逐字节）。
- `selftest.py --fast`：**168/173**（余 5 项**已实测对账为既有问题**，见 §8）。
- `mutation_check.py`：**233/233 个故障被抓到**（含本轮新增那条）。
- 守卫单独验证：加数据前 **FAIL**（当场点名 `dear_good_friends` 未声明）→ 补数据后 **PASS**
  （`全量 4 首 ['c15_chain_repro','dear_good_friends','siren_end','siren_end2'] · 抽样 0 首`）。

---

## 4. 三把尺子：改动前后

| 分组 | R17 旧交付 | **R18 交付** |
|---|---|---|
| **投诉的 8 段** chroma | 0.8450 | **0.8668** ↑ |
| 　同上 \|ΔRMS\| | 1.487 | **1.400** ↓ |
| 　同上 \|Δ质心\| | 101 Hz | **81 Hz** ↓ |
| **没投诉的 7 段** chroma | 0.8993 | 0.8931 ↓略 |
| **全 15 段** chroma | 0.8703 | **0.8791** ↑ |
| **全 15 段** \|ΔRMS\| | 0.987 | **0.967** ↓ |
| `timbre_audit` | 0/15 | **0/15** |

单段质心改善最明显的：`S01 45→6 Hz`（原曲 1049）· `S04 136→37 Hz` · `S14 chroma 0.828→0.909` ·
`Ending chroma 0.688→0.725`。

---

## 5. 126s「奇怪的声音」已经定案到轨（**但仍未修**）

**正确方法 = 整混音逐轨静音**（⛔ 单轨渲染不可相加 —— 交接 §6 坑 2；R17 用错方法把两个嫌疑都否掉了）。

| 时刻 | 不静音时 10–18k 超原曲 | **静音 Strings 后** | 静音 Perc 后 |
|---|---|---|---|
| **125.4s** | +16.4 dB | **−2.5 dB** ← 元凶是 **Strings** | +16.7 |
| **126.4s** | +15.6 dB | **−2.9 dB** ← 同上 | +15.9 |
| 128.4s | +4.4 dB | −11.6 dB | +4.7 |
| **127.4s** | +18.4 dB | +18.8（无关） | **+1.4 dB** ← 这一段是 **Perc** |

- **Strings 贡献**：125.0–128.75s 我方 10–18k 是**恒定 −0.0~−4.7 dB 平台**，原曲是 −11~−18 dB；
  1–3kHz 在 126.75s **Strings 一个人占 29 dB**（静音后 43.3 → 14.2 dB，原曲那时是 39.0 dB 的**有起伏**内容）。
- **修正 R17 §5 两行**：上轮"不是 Strings""也不是 Perc"**都是方法错**；用对方法后**两个都对**，
  只是**分处不同的秒**。
- ⛔ **与音色无关**：把 S13/S14/Ending 的 `arr.prog.Strings` 由 51 换回 49（V2），
  这段 10–18k **变化 0.0 dB**。原因：那 4 个音在 **bar 41（S12）**，V0/V2 里**都用 prog 49**，V2 根本没碰到它们。

### 已备好的两个单维度变体（疗效已测，125.0–129.0s，Σ\|偏差\| 越小越好）

| 变体 | 改了什么 | 1–3k Σ | 10–18k Σ | 判断 |
|---|---|---|---|---|
| V0 交付 | — | 43.8 | 116.2 | 基线 |
| **V3** | bar41 那 4 个 Strings 音的力度 `107 → 60`（约 −13dB） | **23.4** | **52.9** | **更均衡，首选** |
| V4 | 删掉 bar41 那 4 个音 | 50.9 | **39.0** | 10–18k 更准，但 1–3k **塌 12–15dB**（过度） |

MIDI 在 `D:\test\_tmp\dgfr18\ab\`（`V3_弦乐降力.mid` / `V4_弦乐删除.mid`）。

### ⛔ 结果：用户听过 V3/V4 之后 —— **「还是 V1 好」**（2026-09-26）

⇒ **V3 / V4 都不采用，不落进交付**；126s 那处弦乐**保持现状**。
交付 MIDI 与 `V1_全量转录.mid` **逐字节相同**（`890d59e9c355f4aa…`），未被污染。

**用户最终决定（原话）**：
> "我觉得 v1，**126s 那里也可以接受**，**v3v4 后面又不一样了**，就这样 v1 吧"

⚠ **这就是本曲的收工结论**（不是"待办"）。
⚠ 下个对话**别再重试 V3（降力度）/ V4（删音）这条路** —— 已被耳朵否掉一次。
⚠ **他那句"后面又不一样了"是个技术信息**：那 4 个音的时值长达 **4.0 / 4.5 秒**
（`[41, 3.49, 5.4, 52]` / `[41, 3.49, 6.07, 55]`，响到 **129.5s**）—— 所以动它们
**不只影响 126s，会一路改到 S13**。这正是"一次只改一个维度"要防的**意外波及**。
（注意：否掉的是**"把那 4 个音变轻/删掉"这个做法**，**不等于** §5 那些 dB 实测数不成立 ——
差异确实存在，只是**用户判定可以接受**。）

---

## 6. ⚠ 下个对话第一件事

**状态：本曲已收工。** V1 定案并落地，用户明确"**就这样 v1 吧**"，126s 那处**他判定可以接受**。
⇒ **交付三件套就是 §0 那三个 SHA，不要再动；126s 不需要继续修。**

如果以后**同一首**又出新问题（用户主动提新的时间点），再按 §7 的流程走
（**别再用单轨渲染**；开工前先问"是变难听了还是听不出区别"，比盲试省一轮）。

**本轮真正要带走的是工具链那条口径**（§2 / §10）：提取默认全量，已在引擎与生成器两侧生效，
并有守卫 `t_restore_notes_full` 兜底 —— **下首扒带曲不用再关心这个开关**。

---

## 7. 纠错台账（**下个对话别再用这些**）

| 说法 | 实际 |
|---|---|
| "V2 换回真实弦乐能修 126s" | **无效**（10–18k 变化 0.0 dB）—— 那 4 个音在 S12、本来就是 49。**我最初判错了靶子** |
| "S13 的高频不是 Strings / 也不是 Perc"（R17 §5 两行） | **方法错**（单轨渲染）。用整混音逐轨静音：**125.4s 是 Strings，127.4s 是 Perc** |
| 原曲 40–200Hz 的**功率积分 dB** 减时域 RMS dB 求"低频占比" | **尺子错**（单位不同），该列作废 |
| 拿**整混音起音数**减 **drums 分轨起音数**求"非鼓起音" | **尺子错**（检测条件不同，出 `nan`），该列作废 |
| "抽样砍音不是主因（我方事件数已多于原曲）" | **判据不当**：那次比较用的是"整混音 onset 检测 vs MIDI 音符数"，两者口径不同。**用户耳朵已定案** |
| "`song.json` 的 CRLF 是我 `edit` 工具引入的" | **不是**：原始 `a2e8f107` **本来就是 CRLF**（21123 处，二进制计数为证）。我的改动**顺手修掉了** CRLF 那一半 |

---

## 8. 仍未解决（如实记账）

| 项 | 现状 | 判断 |
|---|---|---|
| **126s 弦乐那处** | 已定位到轨、变体已备 | ← **下个对话第一件事**（§5/§6） |
| `song_json_canonical` FAIL | `dear_good_friends(21125 行 → 应 3675 行)` | **既有**（改动前同样 FAIL，实测对账 0/5）。⚠ **成因未验证**：怀疑是面板 `POST /api/song` 用 `json.dump(..., indent=1)` 存盘把 `json_io` 的规范格式覆盖了（`server.py` 里确实是 `indent=1`），但**我没验证过这首到底有没有走过面板存盘** —— 要修先按 `scripts/json_io.py` 重排，别照这条猜因去改代码 |
| `melody_health` FAIL | 同音串 11 · 小步 46% | **既有**；Melody 轨本轮**一字未动**（97 音，V0/V1 相同）。属"还原曲本来如此"那类，要写 `patterns.melody_exempt`（带实测数字） |
| `audio_semantics` FAIL | 成品 150.8s vs 谱面 155.2s | **既有**（改动前 149.3s 同样 FAIL）—— Ending 尾 4.4s 是 `ending_fade` 空档 |
| `notes_present` FAIL | 缺 `notes.md`：`c15_chain_repro` | **既有**，与本曲无关（本曲已补） |
| `bass_register` FAIL | `siren_end2` Bass 最低 22 < 24 | **既有**，另一首曲 |
| 40–80Hz 差 · S08–S12 偏暗 · S13/S14 高频 +17dB · 20–40Hz | 均**未动** | 沿用 R17 §9 |

---

## 9. 复现与路径

```bash
C=D:\software\skill\music-gen
# ① 重生成 MIDI（改过 song.json 之后必做 —— 交接 §6 坑 1）
$C\.venv\Scripts\python.exe $C\scripts\song_engine.py $C\songs\dear_good_friends\song.json --brief
#     ⚠ 库内与交付两份 song.json 必须一致（SHA 675fd257…）
# ② 渲染（当前交付配置）
$C\.venv\Scripts\python.exe $C\scripts\render_midi.py $C\songs\dear_good_friends\dear_good_friends.mid <基名> --hp 15
#     ⚠ 传"基名"不要传 .wav —— render_midi 会自己补 .wav/.ogg（传 .wav 会得到 .wav.wav）
# ③ 三把尺子
$C\.venv-ml\Scripts\python.exe $C\scripts\report_sections.py $C\songs\dear_good_friends\song.json \
     --ref D:\test\_tmp\dgf\dgf_src.wav --mine <out>.wav --mid $C\songs\dear_good_friends\dear_good_friends.mid --json <sec.json>
$C\.venv-ml\Scripts\python.exe $C\scripts\timbre_audit.py $C\songs\dear_good_friends\song.json \
     --ref D:\test\_tmp\dgf\dgf_src.wav --stems D:\test\_tmp\dgf\stems_flat --mine <out>.wav --json <tim.json>
# ④ 自检（⚠ selftest 与 mutation_check **别并发跑**，约定 §5）
$C\.venv-ml\Scripts\python.exe $C\scripts\selftest.py --fast      # 需 .venv-ml（.venv 缺 librosa）
$C\.venv-ml\Scripts\python.exe $C\scripts\mutation_check.py
```

| 名 | 绝对路径 |
|---|---|
| 交付目录 | `D:\test\dear_good_friends_交付\` |
| 曲库（与交付同 SHA） | `D:\software\skill\music-gen\songs\dear_good_friends\` |
| **R17 旧版备份** | `D:\test\_tmp\dgfr18\backup_r18\`（`*.pre_v1.bak`，5 个文件） |
| **A/B 与消融产物** | `D:\test\_tmp\dgfr18\ab\`（`V0/V1/V2/V3/V4*.mid` + `render/` + `mute/` 逐轨静音渲染） |
| 本轮脚本与日志 | `D:\test\_tmp\dgfr18\`（`00_*.sh` … `70_*.sh` 编号） |
| 中间素材 | `D:\test\_tmp\dgf\`（`dgf_src.wav` / `stems_flat/`） |

---

## 10. 口径沉淀（本轮新增，已写进工具链）

> **提取/扒带的默认值取向 = 忠实，不是"服从段落结构"。**
> 用户 2026-09-26："我需要每次提取时都能达到 V1 的准度。"
> 硬形式：`transcribe_to_song` 默认全量 · 引擎兜底默认全量 · 守卫 `t_restore_notes_full`
> 要求每首扒带曲**显式声明**该字段（默认值能被翻转，但**意图必须留痕**）。
