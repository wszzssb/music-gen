# 接手 BGM35 整曲还原：**先修识别，再谈自适应音色**（2026-10-02 交接）

> 自足文档：只读这一份就能接手。上一轮的所有读数、路径、踩过的坑都在这里；
> 相关背景（提取精度那条线）在 `docs/HANDOFF-TRANSCRIBE.md`，别再从头摸。
>
> ⚠ **2026-10-06 加注**：本文**已被 `docs/HANDOFF-BGM35-R2.md` 取代** —— 那份顶部写明：
> 本文记的"重复组 137 / 逐层空洞 483 秒 / 一致率 84.9%"与现状对不上，**84.9% 是同源自证的假数**。
> 另外下文引用的 `D:\test\_tmp\lead-chain\` / `reextract\` 等**临时目录已不存在**
> （实测本文 17 处路径全部失效）—— 正文留作过程记录，**接手动作一律照 R2 那份**。

## 0. 用户口径（原话，别改）

- **"我觉得是识别的问题，以前和现在都还原不出来准确的"** ← 本文档要解决的核心
- **"以后可以根据不同音乐切换，其它乐器也要自适应"** ← 音色要按原曲能力自动挑，不是写死
- 更早的两条：**"像不像 / 哪里不像"**（判据口径）· **"不要看之前信息"**（重新提取时要求干净重跑）

## 1. 一句话现状（**用户的判断已被数据证实**）

BGM35 现有两版产物，用 `preflight.py`（无真值、层/事件级体检）量"逐层空洞"：

| 判据 | 旧版 `bgm35_extract` | 新版 `bgm35_reextract` |
|---|---|---|
| ⑧ **逐层空洞**（某层有起音、我们对应轨覆盖不到 1/3 的秒数） | **491 秒** | **483 秒** |
| ↳ `other` 层 | **224** | **224** |
| ↳ `guitar` | 121 | 123 |
| ↳ `piano` | 85 | 81 |
| ↳ `bass` | 60 | 54 |
| ③ 逐秒起音：我们**过少** / **过多**的秒数 | 37 / 23 | 19 / 29 |
| ④ 分轨长音段（≥0.3s）没被同高音盖住 | 663 处（最长 7.11s） | 664 处（最长 6.90s） |
| ② 同轨同音高 ≤60ms 重复组 | 13（正常） | **137** ⚠ 本轮引入 |
| ⑤ 鼓连击（3–8kHz 支撑 <0.6 的串） | 1 | 0 |

**三条结论**：
1. **"识别不准"的主体是 `other` + `guitar` 两层**（占 483 秒的 **72%**）；`bass` 只占 54 秒。
2. **旧版 491 / 新版 483 —— 几乎没变** ⇒ 用户听感正确。前几轮改的是 Bass 音准、音色、编配，
   而那两层**根本没被抓住**，换什么音色都救不回来（这也是"换音色"听着都没解决的原因）。
3. 新版多出一个**本轮引入的回归**：同轨重复组 13 → **137**（新增音 1548 个里 250 个重复，16.1%）。

## 2. 待办（**按推荐顺序**，一条做完再下一条）

### 任务 1（先做，因为它是本轮引入的回归）：修新版"同轨重复组 137"

- 现象：`preflight` ② **FAIL** —— 同轨同音高 ≤60ms 重复组 **137**（骨架 13）；新增音 1548 里 **250 个（16.1%）** 落在重复组。
- 嫌疑：**Bass 并集**（`bass_layer_enhance.py` 的第 ③ 步，两来源音高/时间接近但未完全合并）+ 干净重提取。
  先查：对 `songs/bgm35_reextract/song.json` 逐轨统计"同轨同音高 ≤60ms"的组（按轨定位到底是不是 Bass）。
- 验收：`preflight` ② 由 FAIL 降回 ~13 组；**Bass 转录读数不许掉**（见 §5：一致率 84.9% / 差八度 1.4%）。
- 相关工具：`bass_ensemble.py` 有 `--merge`；若属并集副作用，可考虑提高同格合并容忍或在替换后跑一次同音去重。

### 任务 2：查 `other` / `guitar` 为什么整层没抓到（483 秒里占 72%）

- 工具：**`restore_gap_fill.py`** —— 它就是为"我们 0 音 / 按小节偏少"写的（逐小节判、逐小节挡"原曲静音"）。
  - ⚠ **必须带能量门**：`--stems-audio <demucs 分轨目录> --stem-gap <dB>`（逐分轨能量门）。
    上次不带门跑出"需补 **134/207 小节 · 可补 10942 音**"（≈现有 8461 音翻倍），**不能直接 apply**。
  - 先 `--dry` 看清单；`--prune-quiet` 是对称能力（原曲静音处删我们的音）。
- 素材：分轨转录（各层独立）在 `D:\test\_tmp\reextract\ymt3_allstems\{other,piano,guitar,bass,drums,vocals}.mid`
  （⚠ `other`/`piano`/`vocals` 那次各花了 **10+ 分钟** —— 分轨输入不吐 `<eos>` 的已知症状）。
- 验收：`preflight` ⑧ 的 `other`/`guitar` 秒数明显下降，且 **④覆盖 不恶化**、③密度双向不恶化。
- ⚠ 别照搬 Bass 的"并集"：复音层的分轨转录音符数虚高（other 8960 音 vs 我们 857 音），
  直接并集会翻几倍且掺入大量泛音/重复。

### 任务 3：**自适应音色**（用户明确要的功能）

- 现状（写死的）：`Hook` = GM 24 尼龙吉他，而原曲那把吉他 **质心 1479Hz / 2–6k 29.84%**，
  GM 吉他族没有对应（最近的 30 Distortion 是 63%，过头一倍）；`Melody` = GM 0 钢琴，
  可它的 151 个音来自 YMT3 的 **Synth Lead** 通道。
- 已有材料（可直接用）：
  - **GM 音色能力表**（40 个音色 × 谐波/2–6k/质心/动态/12dB 衰减）：
    脚本 `D:\test\_tmp\lead-chain\gm_capability.py`（一次渲染出全表；**纯 FluidSynth**，去掉混响与中侧加宽）
  - **原曲每层能力表 + 我方每轨能力**：`D:\test\_tmp\lead-chain\乐器能力表.md`
- 做法：按"原曲该层的能力"在**同族内**选 program（Hook→吉他族、Melody→Lead 族、Pad/Strings→垫子族）。
  ⚠ **不许跨族**：跨族的频谱距离会把"钟琴"推给"钢琴"、把"小号"推给"吉他"（我实测过，是陷阱）。
  ⚠ 能力对齐 ≠ 听感像（技能里有实测教训：用户认可的模板自己渲染出来，质心差一个数量级）→ 必须人耳定。
- 落地形态建议：把"选音色"做成一条命令（输入原曲分轨 + 曲目 song.json，输出每层的推荐 program + 依据表），
  再接进 `transcribe_to_song` 的接续链或 `restore_oneshot`。

### 任务 4（可选）：Melody 轨音色 A/B

- 新版 `Melody` = GM 0（钢琴）；候选 `80` 方波（质心 566）/`81` 锯齿（835）。
- ⚠ 顺带记一条**旧版有、新版丢了**的东西：旧版 `bgm35_extract` 的 Melody 带**段级音色**
  （S09/S17→81、S22→27），新版链路没带上。

## 3. 红线（本轮踩过 / 实测过，别再踩）

1. **同源自证**：八度校正是用 pyin 提的参考做的，而 `transcribe_audit` 又拿 pyin 当基准 ——
   用它量出的"差八度 34.6%→1.4%"是自证；独立基频证据只给 **73.1%**。**两个尺子的差就是自证的量**（`PITFALLS.md` 308）。
2. **判八度只能比基频**：比"基频 + 前两次谐波之和"会被污染 —— 低八度候选的 2 次谐波正好落在
   高八度候选的基频上（合成夹具 549.3 vs 549.6，**几乎不可分**；修正后同一份产物 80.6% → **73.1%**）。
3. **pyin 只对单音性强的层有效**（bass 最典型）：复音层（Piano/Strings/Guitar）会得到"方法本身造成的假数"；
   **鼓的音高是鼓件 ID**，要按通道 9 + 按族比（`truth_eval.py` 内置口径），不能拿 pyin 量。
4. **改 `song.json` 前核基准**（记 md5）→ 只改要改的轨 → **读回 `.mid` 验证**（逐轨音符数/program）。
5. **改了 `song.json` 必须重渲染** —— 本轮真犯：恢复了数据却没重渲染，音频停在上一版（stale 类坑）。
6. **`bass_ensemble.py` 单来源会把整条轨静默清空**（权重和为 0 → `Bass → 0 音`，rc 仍是 0；
   `PITFALLS.md` 309）。要单来源就用 `bass_layer_enhance.py`。
7. **别用整曲渲染当探针**：改一版 → 切 A/B 片段（13 秒 ogg）→ 人耳定方向。
8. **A/B 片段给 ogg 不给 wav**：2.29MB 的 WAV 在预览器里只预载约 1.2MB → 显示成"7 秒"（本轮用户报过）。
   实测 ogg 13.00s / 260KB 正常。

## 4. 素材、工具、读数（绝对路径）

| 类别 | 路径 |
|---|---|
| **新版曲目**（干净重提取 + 优化 Bass） | `D:\software\skill\music-gen\songs\bgm35_reextract\`（song.json / .mid / `_sf.ogg` / `_sf.wav`） |
| **旧版曲目**（= `songs\bgm35_extract`，同一个 inode；现被我改成"版 A"） | `D:\software\skill\music-gen\songs_direct\bgm35_extract\` |
| 旧版**基线备份**（用户认可版，要还原就覆盖回去） | `D:\test\_tmp\lead-chain\variants\base\`（song.json md5 `eb16ff3247c0`） |
| 干净重提取全套（转录 / 分轨 / 优化 MIDI / 分段读数） | `D:\test\_tmp\reextract\` |
| ↳ 整混音转录（8625 音） | `D:\test\_tmp\reextract\ymt3_mix\BGM35.mid` |
| ↳ 各层分轨转录 | `D:\test\_tmp\reextract\ymt3_allstems\*.mid` |
| ↳ 参考曲 6 轨分轨（demucs 6s） | `D:\test\_tmp\reextract\stems\htdemucs_6s\BGM35\` |
| ↳ 新版渲染分轨 | `D:\test\_tmp\reextract\stems_new\htdemucs_6s\bgm35_reextract_sf\` |
| ↳ 优化后的 Bass MIDI | `D:\test\_tmp\reextract\bass_fix_thr0.45_oct.mid` |
| ↳ preflight 报告（新旧两版） | `D:\test\_tmp\reextract\pf_new\preflight.md` · `pf_old\preflight.md` |
| **本轮临时脚本与读数**（工具/shell/表） | `D:\test\_tmp\lead-chain\`（`乐器能力表.md` · `gm_capability.py` · `verify_indep.py` · `scan_dissonance.py` · `apply_*.py` · `ab\*.ogg`） |
| Hook 音色 A/B 三版产物 | `D:\test\_tmp\reextract\hook_27\` · `hook_29\`（各含 song.json/.mid/.ogg/.wav） |
| 参考曲 | `D:\test\galgame\ピュアソングガーデン！解包\Bgm\BGM35.ogg` |
| 交付目录（旧版交付物 + notes §6） | `D:\software\skill\music-gen\deliveries\bgm35_extract\` |
| 老一轮的交接（提取精度那条线） | `docs\HANDOFF-TRANSCRIBE.md` · `docs\RESTORE-METHOD.md` §10 防错清单 |

**关键命令**：

```powershell
$ml = "D:\software\skill\music-gen\.venv-ml\Scripts\python.exe"
# 无真值体检（本文档所有读数的来源）
& $ml "D:\test\pop_transcribe_audit_交付\tools\preflight.py" songs\bgm35_reextract\bgm35_reextract.mid `
      --ref D:\test\_tmp\reextract\BGM35.wav --stems D:\test\_tmp\reextract\stems\htdemucs_6s\BGM35 `
      --skeleton D:\test\_tmp\reextract\ymt3_song\bgm35.mid --out D:\test\_tmp\reextract\pf_new
# 低音层增强（已固化的工序）
& $ml scripts\bass_layer_enhance.py <整混音.mid> <bass分轨.wav> --out <输出.mid>
# 分段补漏（**必须带能量门**）
& $ml scripts\restore_gap_fill.py bgm35_reextract --stems-midi D:\test\_tmp\reextract\ymt3_allstems `
      --stems-audio D:\test\_tmp\reextract\stems\htdemucs_6s\BGM35 --stem-gap 12 --dry
```

## 5. 已经做完的（别重做）

- **低音层增强工序已固化**：`scripts\bass_layer_enhance.py`（一条命令：两来源转录 → 并集 + 八度校正 → 独立核对）。
  实测：Bass 一致率 **35.4% → 84.9%** · 差八度 34.6% → **1.4%** · 漏检 26% → **5.0%**；
  归因 = 并集 **+37.2 点** / 八度校正 **+12.3 点**；参照用户认可版同尺子校准值 53.2%。
- **干净重提取整套**（不看旧产物）：新曲目 `songs\bgm35_reextract`（8625 音 + 替换为 1553 音的优化 Bass）。
- **守卫**：`selftest.t_bass_layer_enhance_contracts` PASS · `mutation_check` **264/264**（含新增 2 条注入）·
  文档守卫 4/4。新增坑：`PITFALLS.md` **308**（同源自证的两种形态）· **309**（`bass_ensemble` 单来源静默清空）。
- **能力表**：GM 40 音色（`gm_capability.py`）+ 我方各轨/原曲各层 —— **下表已内联**
  （原表曾在 `D:\test\_tmp\lead-chain\乐器能力表.md`，**2026-10-04 该临时目录已按用户要求删除**，内容搬到这里）。

  > 口径：逐帧 4096/跳 2048，取中位。四个量都是**物理量**，不看音色名 ——
  > **谐波占比** = f0 的 1–5 次谐波窄带能量 / 全帧能量（低 = 宽带噪声）·
  > **2–6k%** = 嘶声（"蚊子叫"）· **质心Hz** = 亮度 · **动态dB** = 帧能量 P90−P10（"忽有忽无"）

  | 我方轨（引擎音色） | 谐波% | 2–6k% | 质心Hz | 动态dB |
  |---|---|---|---|---|
  | Melody（Synth Lead 80） | 18.8 | 2.70 | **1217** | 76.7 |
  | Hook（尼龙吉他 24） | 28.3 | 2.76 | **540** | 72.3 |
  | Piano（钢琴 0） | 24.8 | 0.92 | 487 | 39.2 |
  | Pad（Synth Pad 88） | 30.9 | 2.68 | 962 | 63.7 |
  | Strings（弦乐 48） | 34.0 | **8.27** | 849 | 46.2 |
  | Bass（贝斯 33） | 30.2 | 0.00 | 128 | **26.0** |
  | Glock（钟琴 8） | 23.6 | **20.40** | 1761 | 67.2 |

  | 原曲分轨（demucs，同一把尺子） | 谐波% | 2–6k% | 质心Hz | 动态dB |
  |---|---|---|---|---|
  | bass | 26.3 | 0.00 | 100 | 62.9 |
  | drums | 4.3 | 5.55 | 2274 | 32.5 |
  | guitar | 18.4 | **29.84** | **1479** | 57.6 |
  | other | 32.0 | 7.39 | 1018 | 11.4 |
  | piano | 24.2 | 1.26 | 458 | 51.3 |
  | vocals | 4.5 | 5.56 | **1556** | 7.6 |

  **逐项判定**：Piano / Bass / Strings / Pad **能力匹配**（谐波差 ≤2.0、质心差 ≤169）·
  **Melody 亮度偏低**（1217 vs 原曲主奏 1479）· **Hook 差最远**：尼龙吉他比原曲那把吉他
  谐波 **+9.9**、质心 **−939**（原曲吉他 2–6k 有 **29.84%**，我们只有 2.76%）。
  ⇒ 这一行就是"自适应音色"任务的由来：要按**原曲该层的能力**在同族内选 program。
  ⚠ 另附一条定性：`vocals` 分轨占用率 +97 百分点**不是"我们凭空多了人声"** ——
  两边都是低谐波（≈4.6%）宽带内容，但**频段不同**（原曲 1556Hz / 我方 447Hz），
  是 demucs 把两类宽带内容都丢进 `vocals` 桶；该查的是我们低频里那条低动态宽带（动态仅 4.5dB）。
- **成品级物理对照**：新版谐波 **21.6%** vs 原曲 **21.1%**（旧版 28.5%）· 质心 583 vs 685（旧版 394）。
- Hook 音色 A/B 三版（24 / 27 / 29）已渲染，等用户听感定夺（脚本 `apply_hook_prog.py` + `run_hook_ab.sh`）。

## 6. 已知边界（别越界使用）

- `preflight` ①（轨结构）对**引擎编配版**不适用 —— 引擎按角色自己分配音色，骨架里当然没有那些 `(channel, program)`。
- `preflight` ⑥动态包络 / ⑦频带赤字要 `--mine-wav`（本轮是 UNKNOWN，**不是通过**）。
- 复音层**没有**绝对精度尺子（`transcribe_audit` 的 70% 门只对单音层成立）；复音层只能看
  "漏检/假音的方向" + `preflight` 的层/事件级判据。
- 分轨转录对复音层**音符数虚高**（other 8960 vs 我们 857），不能当"该补多少音"的目标值。

## 7. 待用户决定

- 旧版 `songs_direct\bgm35_extract` 现在停在"版 A"（我加的 S23 低音长音），**保留还是回退到基线备份**？
- Hook 音色留 24 / 27 / 29？（A/B 片段原在 `D:\test\_tmp\lead-chain\ab\hook*_*.ogg`；
  ⚠ **2026-10-04 该临时目录已按用户要求删除** —— 需要再听就重渲那三版）
- 主线走哪一版：旧版 `bgm35_extract` 还是新版 `bgm35_reextract`？
