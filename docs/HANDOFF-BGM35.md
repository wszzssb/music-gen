# 交接：用**专门的识别模型**回答"BGM35 3:31–3:41 那一段到底是什么"

> ## ✅ 这份已经**做完**了（2026-09-27 第十二轮）—— 别重做
>
> 三问的答案 + 全部读数 → `D:\test\BGM35_提取\notes.md` **§8**；
> 可复用判据与工序 → `docs/HANDOFF-TRANSCRIBE.md` **§11**；
> 新踩的四个坑 → `PITFALLS.md` **284–287**；探针脚本 → `tools/bgm35-3m31-probes/`。
>
> **一句话结论**：Q1 **不该补打击**（那层比真鼓段低 21dB、谱平坦度低 9 倍 = 分离残留）；
> Q2 **是八度错** —— 原曲那层在 p77/80/82/84（698–1046Hz），高八度那些能量是**它的谐波**，
> 而我们弹的是真的 p89/92/94/96；Q3 那层是**偏暖的合成器层**，GM 里最接近的是 **GM88 warm pad**（不是现用的 GM80 方波，钢片琴最末）。
> 已按此出候选 **`BGM35_r25_主奏降八度.mid`** + A/B（`对照_r25\对照_3m31-3m42\`）。
> **仍未做**：听感验收；"单线还是和弦"这一条没量出来（判据本身无区分度，见 §11.3）。
>
> 下面保留的是**接手时**的原文（背景与素材路径）—— 注意其 §5 的"已证伪清单"里
> 有几条判据**后来被证明是无区分度的**（§11.3），别再拿它们推结论。
> ⚠ §2 里的工作区 `D:\test\_tmp\b35\`（源/分轨/各来源转录）**已随中间产物清理**；
> 要重跑先按 `tools/bgm35-3m31-probes/README.md` 的"跑之前要知道的三件事"重建（约 5 分钟）。

> 接手先读这一份（自足）+ `D:\test\BGM35_提取\notes.md`（逐轮台账）+ `docs\HANDOFF-TRANSCRIBE.md` §9–§10（判据与工序）。
> **本文档的任务不是继续调参，而是换工具**：这一段的三个问题**用现有工具链量不出来**（YMT3 / Basic Pitch / Demucs / GM 渲染），
> 需要**专用模型**去识别"那一层到底是什么乐器、什么节奏、什么音高"。

---

## 0. 一句话现状

BGM35（331.9s 真实游戏 BGM，无真值）已交付到 **r21 / r23 / r24 三个候选**，
体检 ① ② ⑤ 通过、⑥ 仍 WARN；**唯一卡住的是 3:31–3:41 这一段的听感**：
用户三次反馈都是"**不像 / 不脆 / 那串打击挡住主旋律**"，而我的自动判据在这段上**全部失灵或指错方向**。

## 1. 要回答的三个具体问题（这就是"识别告诉问题"的交付物）

| # | 问题 | 已知实测 | 为什么现有工具答不了 |
|---|---|---|---|
| **Q1** | 3:35–3:40（216–220s）原曲那 2–4 下/秒的"打击"到底是**真鼓/镲**，还是 **Demucs 分轨的串音**（把旋律的瞬态漏进了 drums 轨）？ | 我按"鼓分轨有起音"补了 19 记打击，**16 记落在旋律音头 ±50ms 内**；原曲 drums 分轨那几秒只比旋律 **−17~−22dB**（215s/220s 甚至 −49/−59dB） | Demucs 的 6 轨是**软分离**，串音无法自查；需要一个**专门的鼓分离/转写模型**给出"到底有没有鼓、是什么鼓" |
| **Q2** | 3:33–3:41 的旋律线原曲到底是 **p77/p80/p97** 还是 **p89/p92**？（逐音高能量：我们 p89/p92 = 原曲 1.8–1.9×，p77/p80/p97 = 0.2–0.4×） | 换来源（other 分轨转写）只改善了点名的三处、整体轮廓误差没净赚；窄带起音率在真实混音上**虚高**（原曲明明 100–400ms 持续音却报 9.4/s） | 需要**单声部旋律提取**模型（pYIN / CREPE / torchcrepe / Omnizart vocal-melody / basic-pitch 在**去伴奏后的 other 分轨**上跑），逐帧给 F0 曲线 |
| **Q3** | 那层"亮"的合成器（8–12kHz 主要来自 `other` 分轨）是什么音色？GM 能不能近似？ | 四种 GM 亮音色（钢片琴/钟琴/锯齿/尼龙吉他）在这段 8–12k 只从 −8.0 收到 **−7.3dB**；原曲该带能量 4.4e5 | 需要**乐器/音色分类**（如 musicnn / MTG-Jamendo 标签、CLAP 音频-文本嵌入、或 docs\AUDIO-CRITIC.md 的本地 Qwen2-Audio 当"嘴替"）给出"这是什么音色/演奏法" |

**验收标准**：三个问题各给一个**可复核的读数**（不是"我觉得"），并落到"这一段该怎么改"的具体动作上。
例：Q1 → "drums 分轨在 216–219s 的 3–8kHz 能量来自 X 乐器的泄漏（证据：鼓专用模型的转写为空 / 频谱衰减与旋律一致）" ⇒ 结论"不该补打击"。

## 2. 现有资产（绝对路径，全部就绪）

| 资产 | 路径 |
|---|---|
| 交付目录（候选 MIDI/OGG、A/B、台账、体检报告） | `D:\test\BGM35_提取\` |
| **当前最优候选** | `BGM35_r21_补脉冲.mid`（不补打击，用户三选项里的 A）· `BGM35_r24_只补不撞旋律.mid`（B）· `BGM35_r23_补打击_闭镲.mid`（C，用户嫌抢） |
| 待用户拍板的 A/B | `D:\test\BGM35_提取\对照_r24\对照_3m31-3m42\`（原曲 / A / B / C） |
| 台账（**必读**，§7.6–§7.10 是这段的全部实测） | `D:\test\BGM35_提取\notes.md` |
| 工作区（源/分轨/各来源转录） | `D:\test\_tmp\b35\`：`BGM35.flac`（44.1k）· `ymt3\BGM35.mid`（骨架）· `src\BGM35_ymt3_{other,guitar,piano,drums}.mid`（**分轨单独转写**）· `bp\` · `views\` · `stems\htdemucs_6s\BGM35\`（6 轨） |
| 工具（本轮新建，都在 `D:\test\pop_transcribe_audit_交付\tools\`） | `preflight.py`（交付前体检 6 条）· `layer_band_probe.py`（**逐层频带份额诊断：报"哪层×哪频带×哪几秒"塌了**）· `stem_onset_fill.py`（按分轨起音补整层）· `lead_swap.py`（只换主奏轨）· `level_match.py`（动态包络闭环）· `diverge_scan.py`（逐秒背离扫描）· `rhythm_compare.py`（**IOI 节奏对照**）· `coincide.py`（**补充内容与旋律音头的重合检查**）· `deflutter.py` · `pitch_gate.py`（已证伪，留档别用） |
| 环境 | `D:\software\skill\music-gen\.venv-ml\Scripts\python.exe`（demucs · librosa · matchering · **piano_transcription_inference 0.0.6**）· ~~`D:\test\bp-venv`（basic-pitch）~~（2026-10-06 退役，已归档）· GPU 可用 |
| 现成但**没量成**的钢琴模型（可当第三来源） | `D:\test\models\piano\`：`piano_crnn.pth`（172MB，单来源 F1 最高 0.666）· `piano_conformer.pth`（628MB，缺模型定义）· `music_piano-v2.onnx`（84MB，`ConvTranspose` 负 pads 载入失败） |
| "嘴替"路线（本地音频大模型） | `docs\AUDIO-CRITIC.md`（Qwen2-Audio；**只能报"差异描述"，不能做价值判断**） |

## 3. 候选**专用模型**清单（按 Q1/Q2/Q3 排，含"该看什么读数"）

> ⚠ 纪律：**任何新模型先自检**（拿已知答案的片段/合成信号跑；`docs\RESTORE-METHOD.md` §10 第 4 条）；
> **同模型多份输出不算独立证据**（第 1 条）；**单首素材上不下结论**。

| 模型/工具 | 回答 | 装法/入口 | 该看的读数 |
|---|---|---|---|
| **鼓专用分离**（`drumsep` / Demucs 官方 `--two-stems`+鼓模型 / `htdemucs` 的 drums 轨二次分离） | Q1 | pip（`.venv-ml`） | 把 drums 轨再分成 kick/snare/toms/cymbals，看 216–219s 各子轨有没有能量；若"cymbals 子轨在 216–219s 为空、而 snare/kick 也空" ⇒ 原 drums 轨是串音 |
| **鼓转写**（`ADTOF` / `madmom` onsets+分类 / `Omnizart` drum） | Q1 | pip/GitHub | 逐下给出鼓件类别与时刻；与我的 19 记对照"哪几记真有" |
| **单声部旋律**（`torchcrepe` / `pyin`(librosa) / `Omnizart vocal` / `basic-pitch` 跑 other 分轨） | Q2 | `torchcrepe` pip 或 librosa 自带 | 213.5–215.5s 的 F0 曲线 → 折算音高序列，直接回答 p77/p80/p97 vs p89/p92 |
| **乐器/音色识别**（`musicnn` / MTG-Jamendo 标签 / **CLAP** 嵌入 / Qwen2-Audio 描述） | Q3 | pip 或 `docs\AUDIO-CRITIC.md` 流程 | 对 `other` 分轨 213–216s 切片给 top-3 乐器标签 + 与 GM 候选音色的 CLAP 相似度排序 |
| **和弦/调性**（`madmom` chords / `chord-extractor`） | 交叉验证 | pip | 与 our MIDI 的 213–216s 音高集合对照，看"多/少"的是和弦音还是装饰音 |
| **节拍/速度**（`madmom` DBNBeatTracker） | 交叉验证 | pip | 那段是否 165ms 网格（≈ 6 音/秒）；我的 IOI 是 163–174ms |

## 4. 复现现状的最小命令集（照抄）

```bash
PY=/d/software/skill/music-gen/.venv-ml/Scripts/python.exe
T=/d/test/pop_transcribe_audit_交付/tools
B=/d/test/BGM35_提取 ; W=/d/test/_tmp/b35
SD=$W/stems/htdemucs_6s/BGM35

# ① 听感问题定位（用户报"不像/不脆/挡旋律"时先跑这两条）
"$PY" "$T/layer_band_probe.py" "$B/BGM35_r21_补脉冲.mid" "$W/BGM35.flac" "$SD" --from 200 --to 230
"$PY" "$T/diverge_scan.py" "$B/BGM35_r21_补脉冲.wav" "$W/BGM35.flac" "$SD" --from 205 --to 232 \
      --mid "$B/BGM35_r21_补脉冲.mid"

# ② 节奏型对照（把"de den de den"这类口头描述变成 IOI）
"$PY" "$W/rhythm_compare.py" "$W/BGM35.flac" "$B/BGM35_r21_补脉冲.mid" "$SD" --from 210 --to 222

# ③ 补充内容是否撞旋律音头（本轮教训：撞了就一定是"挡旋律"）
"$PY" "$W/coincide.py"

# ④ 交付前体检（6 条判据，FAIL 不许交付）
"$PY" "$T/preflight.py" "$B/BGM35_r21_补脉冲.mid" --ref "$W/BGM35.flac" --stems "$SD" \
      --skeleton "$W/ymt3/BGM35.mid" --base "$W/ymt3/BGM35.mid" \
      --mine-wav "$B/BGM35_r21_补脉冲.wav" --out "$B/_preflight_r21"
```

## 5. 这一段的**已证伪清单**（别重复做）

| 做过的 | 结果 |
|---|---|
| 按"原曲逐帧音高集"全局增删（`pitch_gate.py`） | **更差**（RMS 归一化后 p84 1.1×→2.3×）：它删掉了**被掩蔽但真实的低音和弦音** |
| 保守换音（只删"原曲明确没有"的音高 + 从 BP 补） | 只动 6 个音，轮廓误差 4.6dB **不变** |
| 换来源（other 分轨 YMT3）换主奏轨 | 点名的三处（p77/p89/p92）改善，**整体轮廓误差没净赚**（4.1→4.2dB） |
| 换 GM 亮音色修"不脆" | 六种音色 8–12k 只从 −8.0 收到 **−7.3dB**（钢片琴/钟琴/锯齿/尼龙吉他/±八度）⇒ **缺的是内容不是音色** |
| 按"+逐带平衡像原曲"给打击选键选力度 | **错了**：走成 ride→闭镲、能量更小但**更抢**（16/19 记撞在旋律音头）⇒ 目标选错 |
| 用"该层能量占比 > 1.3× 自身中位"当门槛找层空洞 | **正好跳过要抓的秒**（那层在那一刻本来就轻）；改 0.3× 后触发率 68% = 噪声 |
| 用"该层起音数 vs 我们音数"当层空洞判据 | 坏件/好件**都报 476 个**（"我们事件数普遍少于原曲"是基线）⇒ 做不成门 |

## 6. 收尾时要做的两件事

1. **回答 Q1/Q2/Q3**（带读数），并据此给出"3:31–3:41 该怎么改"的**一个**明确动作（或"不改，因为原曲那层本来就极轻"）。
2. 把结论写回：`D:\test\BGM35_提取\notes.md`（新开 §8）+ `docs\HANDOFF-TRANSCRIBE.md`（若产生可复用判据/工序）+ `PITFALLS.md`（新坑）。

## 7. 一条纪律（本轮最贵的）

**"补空洞/补层"之前先查"这个位置和已有内容撞不撞"**：
本轮 19 记打击全部来自"鼓分轨的起音"，而它们**16 记和旋律的音头重合** ⇒ 加一记敲击到旋律的每个音上，
**再轻也会读作"挡住旋律"**。工具 `coincide.py` 就是这条纪律的落地。
