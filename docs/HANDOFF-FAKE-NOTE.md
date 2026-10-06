# 接手：**假音 / 漏层治理**（转录质量提分 · A+B）—— 2026-10-06

> 用户原话（2026-10-06）：「**转录质量到底怎么解决，你可以搜一下**」→ 我搜了文献 → 结论指向
> "**把没有音频支撑的音剔掉**" → 用户：「**交接给下一个对话做 ab**」。
> ⇒ 本文就是把 **A（分轨能量剔假音）** 与 **B（onset 证据门）** 这两条交出去，
> 含**现状评分 · 素材绝对路径 · 验收判据 · 红线与已证伪清单**。
>
> 相关：`docs/HANDOFF-TRANSCRIBE.md`（提取精度那条线的总台账）·
> `docs/TRANSCRIBE-AUDIT.md`（尺子口径）· `PITFALLS` **334/335/336**（本轮新入账）·
> `scripts/transplant_window.py`（本轮新增的**分段移植**工具）·
> `scripts/octave_judge.py`（本轮新增的八度**筛查器**，未通过标定）。

---

## 0. 接手状态（**先读这节**）

- ✅ **本轮已提交**：`c4de57a`（26 files changed, +1021 / −955）—— 回滚点 = 它的父提交 `7da25ac`。
  提交内容：**新增** `scripts/octave_judge.py` · `scripts/transplant_window.py` · 本文档；
  **改** `selftest.py` / `mutation_check.py` / `token_audit.py` / `doc_map.py` /
  `transcribe_to_song.py` / `transcribe_ymt3.py` / `song_engine.py` / `README.md` /
  `PITFALLS.md` / `docs/*` / `SKILL.md`；**删** `bp_transcribe.py` / `bp_primary.py`。
  工作区干净（`git status` 无输出）⇒ **接手直接从本文 §4 的 A+B 开工即可。**
- **一句话现状**：当前流程（`transcribe_ymt3 --bsz 24` 默认接续 + `make_song --no-tune`，含
  逐音力度）在 5 首难例上 —— **准入判据 `unusable` 0/5**（逐轨过线中位 **2/8**）；
  **交付门 4/5 无 FAIL**（`hard_bgm23_v2` 有 1 个）。
- **曲库（还原类）**：`b35_clean`（用户认可基准）· `hard_bgm35_v2` · `hard_bgm35_orn`
  （= v2 + 19.0–19.6s 从认可版移植）· `hard_bgm23_v2` · `real_amakute_v2` · `easy_bgm12_v2`。
- **归档（不硬删）**：`D:\test\_deprecated_restore\` —— `bp-venv`（Basic Pitch 环境，458MB）·
  11 首历史还原曲 · 6 首无力度 v1 · `hard_bgm35_tx`（工具验证产物）· `hard_bgm35_r2`（失败实验）。
- **本轮已落地的能力**（别重复做）：Basic Pitch **完整退役**（代码删/环境归档/守卫同步/文档标注）·
  `--stems-dir` **三种布局都认**（裸名 / `h6_` / `h4_`）· 逐音力度**已接**（逐轨 85 种，与认可版一致）·
  **分段移植工具**（`transplant_window.py`，自检 8/8 + 守卫 + 变异用例）。

## 1. 用户口径（原话，别改）

- 2026-10-06：「转录质量到底怎么解决，你可以搜一下」
- 2026-10-06：「**交接给下一个对话做 ab**」
- 2026-10-06（更早同轮）：「**还是没有转音，可能也不叫转音，是丝滑的音符连打**」
- 一贯：**听感是唯一判据** · **改必须分段** · **不许自创依据** · **一次只改一个维度** · **先量再改**

## 2. 现状评分（本轮实测 · 两把仓库既有尺子）

尺子：`scripts/audit_stems.py`（**6 轨分轨当独立尺子**：精度 = 我方每个音在它该在的分轨上、
音高带能量占比 ≥0.25；召回 = 分轨逐小节 RMS > 全混音中位 −30dB 算"在响"）+
`scripts/preflight.py`（六条关系型判据，FAIL/WARN/UNKNOWN）。

| 曲目 | 准入判据 | 过线轨 | 交付门 F/W/U | 逐轨中位 | 整层缺失 |
|---|---|---|---|---|---|
| `hard_bgm35_v2` | **unusable** | 2/8 | 0/5/0 | 54% | vocals 覆盖 7.7% |
| `hard_bgm35_orn` | **unusable** | 2/8 | 0/5/0 | 54% | vocals 覆盖 7.7% |
| `hard_bgm23_v2` | **unusable** | 1/8 | **1**/5/0 | 40% | piano 覆盖 15.2% |
| `real_amakute_v2` | **unusable** | 3/7 | 0/5/0 | 60% | bass 13.8% + vocals 0% |
| `easy_bgm12_v2` | **unusable** | 0/5 | 0/5/0 | **17%** | drums 1.8% / guitar 0% / piano 0% / vocals 0% |

**汇总：`fixable` 0/5 · 无 FAIL 4/5 · 逐轨过线中位 2/8 · 无整层缺失 0/5。**

逐轨明细（精度 / 召回小节；★ = 两条都 ≥50%）：

```
hard_bgm35_v2 / hard_bgm35_orn（同源，只差 19.0–19.6s 那一窗）
   Drums   3835 音  精度 23.6%  召回 96.0%   缺口  8/199
 ★ Piano   2376 音  精度 50.5%  召回 88.9%   缺口 19/171
 ★ Bass     833 音  精度 86.1%  召回 90.4%   缺口 16/167
   Strings  702 音  精度 45.2%  召回 62.1%   缺口 74/195
   Hook     387 音  精度 17.1%  召回 39.6%   缺口 113/187
   Glock    186 音  精度 18.3%  召回 18.1%   缺口 140/171
   Pad      155 音  精度 50.3%  召回  9.7%   缺口 176/195
   Melody   151 音  精度 57.0%  召回  8.2%   缺口 179/195

hard_bgm23_v2
   Drums   4175 音  精度 22.3%  召回 99.4%   缺口  1/155
   Piano   1135 音  精度 12.3%  召回 88.0%   缺口  3/25
   Melody   896 音  精度 22.2%  召回 56.9%   缺口 66/153
 ★ Bass     837 音  精度 73.2%  召回 90.5%   缺口 13/137
   Hook     542 音  精度 42.4%  召回 36.6%   缺口 59/93
   Strings  331 音  精度 37.5%  召回 21.6%   缺口 120/153
   Pad      237 音  精度 63.3%  召回 16.3%   缺口 128/153
   Glock      8 音  精度 12.5%  召回  2.2%   缺口 91/93
 ✗ 交付门 ②：新增音里 5/91（5.5%）落在同音高 ≤60ms 重复组

real_amakute_v2
   Drums   1718 音  精度 19.7%  召回 100.0%  缺口  0/67
 ★ Piano    669 音  精度 63.1%  召回 100.0%  缺口  0/33
   Pad      138 音  精度 70.3%  召回  47.8%  缺口 35/67
 ★ Hook     117 音  精度 65.8%  召回  60.0%  缺口 20/50
 ★ Bass      23 音  精度 91.3%  召回  64.3%  缺口  5/14
   Strings   18 音  精度 61.1%  召回  10.4%  缺口 60/67
   Glock     13 音  精度 23.1%  召回   0.0%  缺口 33/33

easy_bgm12_v2
   Bass     209 音  精度 36.4%  召回 57.1%   缺口 12/28
   Pad      169 音  精度 55.6%  召回 47.3%   缺口 29/55
   Piano     67 音  精度  1.5%  召回  0.0%
   Drums     52 音  精度  0.0%  召回 33.3%
   Glock     39 音  精度  2.6%  召回  0.0%
```

**怎么读这三类"缺失"（别混）**：
1. **结构性的**：`vocals` —— 引擎轨集合是 Piano/Bass/Drums/Hook/Strings/Pad/Arp/Glock/Melody，
   **本来没有人声轨** ⇒ 分轨 vocals 有内容时必然判"整层缺失"。除非原曲真有人声要还原，
   否则这条应看作**口径**、不是缺陷。
2. **真该修的**（本该有的轨却没抓够）：`real_amakute` 的 **bass 13.8%** ·
   `easy_bgm12` 的 **drums 1.8% / piano 0% / guitar 0%** · `hard_bgm23_v2` 的 **piano 15.2%**。
3. **口径偏保守的**：Drums 那类**宽带瞬态**，"能量占比 ≥0.25"会系统性偏低
   （实测精度 20–24% 而召回 96–100%）—— **这条还没独立验证**，不能全算成假音。

## 3. 为什么问题是"假音 / 漏层"（文献依据，2026-10-06 搜）

**2025 AMT Challenge 官方论文**（[arXiv 2603.27528](https://ar5iv.labs.arxiv.org/html/2603.27528)）：

- 两大失败模式：**instrument leakage**（凭空造出乐器/轨）+ 密集复音里**分不清主旋律**
  （多件乐器音域/音色接近时最严重）；
- 从 1 件到 3 件乐器，F1 掉 **0.28–0.36**（MIROS 0.719→0.437 · YourMT3-YPTF-MoE-M 0.759→0.392），
  统计显著（p<0.05，Cohen's d > 1.3）；
- 顶级模型用**同一批 10 个数据集**、跨数据集增强只带来边际提升 ⇒
  **"performance is fundamentally constrained by data scarcity"**；
- 冠军 MIROS（0.5998）靠**换自监督编码器**（MusicFM，conformer + BEST-RQ）+ 重训，
  相对第 2 名（YourMT3-YPTF-MoE-M，0.5938）只高 0.006 ⇒ **"换模型"这条路的天花板就在 0.59**，
  且 400M 参数 + 重训在本机（RTX 5060 Laptop 8GB）**不可行**。

**Timbre-Adaptive Transcription**（[arXiv 2509.12712](https://ar5iv.labs.arxiv.org/html/2509.12712)，
[代码开源](https://github.com/madderscientist/timbreAMT)）—— 逐个拆了 Basic Pitch 的缺陷，两条对我们直接有用：

- **类权重 0.05/0.95 反而加重不平衡** ⇒ onset **大量假阳性**；改用 Focal Loss，
  正类权重 note **0.2** / onset **0.06**（**稀疏类给更小权重**，反直觉但实测）；
- BP 的大卷积核本该覆盖一个八度，但 **padding 让它实际只覆盖半个八度** ⇒
  用**空洞卷积覆盖上下两个八度**（正对应我们那处八度错）。
- 它还证明：**纯随机合成数据（12.5 分钟）训出的模型能泛化到真实录音** ——
  即"数据稀缺"可以靠**针对性合成**缓解，而不是只能等公开数据集。

**与我们读数的对应**：Drums 精度 20–24% 而召回 96–100%（假音多）· 新增音重复 10.4% FAIL ·
5 首全有整层缺失 —— 与文献点名的两种失败模式**完全对得上**。

## 4. 下一步 = **A + B**（做法 + 验收判据）

### A. 分轨能量剔假音

- **假设**：我方某个音，若在**对应分轨**的该时刻、该音高带上**没有能量**（低于门），那就是假音。
- **已有原语（别重造）**：`audit_stems._ratio(mono, sr, t, f0)`（该时刻该音高带能量占比）·
  `filter_song_by_stem.py`（按分轨能量筛音，**默认只报不删**）· `bass_layer_enhance.band_amp`
  （只比基频的窄带幅度）。
- **做法建议**：① **先只报不删** —— 逐轨量出"假音率"与能量分布（先量准门）；② 在 **1–2 首**上做
  "剔 / 不剔"的 A/B（**分段**、记 SHA256、留备份）。
- **验收判据**：`audit_stems` 的**精度上升**且**召回不显著下降**（建议门槛：精度 +10pt 而召回落幅
  ≤5pt）；最终**听感**由用户判。
- ⚠ **风险**：把"真实但能量弱"的音误删 —— 同族教训 `PITFALLS` **325**（混音级/单轨能量 ≠ 归属）。
  所以**先只读、再小范围改**，别一上来全曲剔。

### B. onset 证据门（专治假阳性密集的轨）

- **假设**：Drums 那类假音在 onset 时刻**没有冲击**（3–8kHz 带内支撑低）。
- **已有原语**：`preflight.py` ⑤（连击串缺 3–8kHz 冲击支撑，本轮实测 BGM35 有 **72 串**缺支撑）·
  `librosa.onset.onset_strength`（照 `PITFALLS` 334 的读法用：hop 10ms + `find_peaks`）。
- **验收判据**：该轨**精度上升** + `preflight` ⑤ 的"缺支撑串数"下降，**召回不显著下降**。

**两条都要求**：**一次只改一个维度** · **分段** · 先量后改。建议起点：
`easy_bgm12_v2`（0/5 过线、Piano 精度 **1.5%**、Drums **0.0%**）与
`hard_bgm35_v2`（Drums 精度 **23.6%**，且它是四首里素材最全的）。

## 5. 素材与命令（**绝对路径**）

| 资产 | 路径 |
|---|---|
| 分轨（**裸名**，给 `audit_stems`/`preflight`） | `D:\test\_tmp\extract-hard\<base>\stems\h6\htdemucs_6s\src\` |
| 分轨（摊平 `h6_*`，给 `restore_oneshot`/`probe_instruments`） | `D:\test\_tmp\extract-hard\<base>\stems_flat\` |
| 原曲 44.1k wav | `D:\test\_tmp\extract-hard\<base>\src.wav` |
| 原始音频 | `D:\test\galgame\ピュアソングガーデン！解包\Bgm\{BGM35,BGM23,BGM12}.ogg` · `D:\test\galgame\audio\(21) [ヤヅチスエタ] あまくてとろける.flac` |
| 曲目目录 | `D:\software\skill\music-gen\songs\<曲名>\` |
| 本轮评分汇总（含逐轨表） | `D:\test\评分_当前流程_v2.md` |
| 评分 JSON | `D:\test\_tmp\extract-hard\scoring\<曲>_{audit,preflight}.json` |
| 本轮新增工具 | `scripts\transplant_window.py`（分段移植）· `scripts\octave_judge.py`（八度筛查器，**未过标定**） |
| 本轮回归对照（A/B 片段） | `D:\test\b35_tx_ab\` · `D:\test\b35_runs_ab\` · `D:\test\b35_orn_ab\` |

`<base>` 取值：`hard_bgm35` · `hard_bgm23` · `real_amakute` · `easy_bgm12`。

评分两条命令（**`--stems` 必须传裸名目录**，见 `PITFALLS` 335）：

```powershell
.venv-ml\Scripts\python.exe scripts\audit_stems.py songs\<曲>\song.json `
      --stems D:\test\_tmp\extract-hard\<base>\stems\h6\htdemucs_6s\src `
      --ref D:\test\_tmp\extract-hard\<base>\src.wav --json <out.json>
.venv\Scripts\python.exe scripts\preflight.py songs\<曲>\<曲>.mid `
      --ref D:\test\_tmp\extract-hard\<base>\src.wav --stems <同上> `
      --mine-wav songs\<曲>\<曲>_sf.wav --skeleton <该曲的转录 .mid> --json <out.json>
```

## 6. 纪律与红线

1. **听感赢**（`SKILL` §9c/9d）：指标只是证据，最终由用户判；
2. **改前记 SHA256 + 备份**；**改必须分段**；**一次只改一个维度**；**不许为过门而改音**（写实测理由放行）；
3. **自检与变异测试别并发跑**（`CONVENTION` §5：并发会让 `determinism_and_bytes` 假 FAIL）；
4. **预算欠账**：新文档 `HANDOFF-FAKE-NOTE.md` 的压缩目标 **6500**（见 `token_audit.LIMITS`）；
5. 改文档后**必须重跑** `python scripts\doc_map.py`（地图是生成物，`doc_map_fresh` 会拦）。

## 7. 别重复试的（本轮 + 上级文档已证伪）

- **八度判决**：三种自动判据（逐点最强峰 / 频谱新颖度 / DP 全局轨迹）**全部失败** ——
  那条线从来不是能量主导的（`PITFALLS` **334**）；
- **`--stems-dir` 传摊平布局**：配到 0 条轨 ⇒ 力度恒 100（`PITFALLS` **335**）；
- **能量判据定"该弹哪个音高/八度"**：`PITFALLS` **325** + **334**；
- **"连打/转音"自动提取**：只能"有参照版就移植"（`transplant_window.py`），否则半自动；
- `HANDOFF-TRANSCRIBE` §2 的**已证伪清单**（残差法 · 第三来源 · 多视图全收 · TTA · 高阈值合并…）。

## 8. 没验证什么（诚实清单）

- **Drums 精度的口径偏差未独立验证**（宽带瞬态在"能量占比 ≥0.25"下天然偏低）——
  在动 A 之前，建议先拿一个**已知正确**的鼓谱/合成件标定这条尺子；
- **`vocals` 整层缺失**是结构性还是真漏 —— 5 首都没逐曲确认（BGM35 原曲 69–104s 有吟唱之说，
  见 `HANDOFF-TRANSCRIBE` 的 siren_end2 案例）；
- **A/B 一次都没做过**：本文只给了判据与素材，没有任何"剔音"实验的读数；
- 8 条"测不到"（起音摇摆/连奏/踏板/演奏法/音色/好听度/过渡/整体）一条都没验
  → `docs/UNMEASURABLE-SOLUTIONS.md`。
