# 8 条"工具测不到"的项：网络调研结果与落地方案

> 用户 2026-09-16："起音有没有摇摆提前量、和弦音该连的连没连、钢琴延音踏板、
> 演奏法/滑音、音色本身、旋律好不好听、段间过渡顺不顺、整体像不像
> 这些再网络上搜索看看有没有解决方案"。
>
> 调研结论：**8 条里 2 条能被现有工具直接量掉（已落地）、3 条有现成开源方案可接、
> 3 条只能人耳**。下面逐条给"搜到什么 / 能不能落地 / 落地代价"。

## ① 起音有没有摇摆/提前量 —— ✅ **已量掉，结论是"这条不是问题"**

新增 `scripts/groove_probe.py`（谱通量起音 + 局部自适应阈值 + 最近 16 分格偏移）：

实测 BGM35 贝斯轨（1874 个起音，331.9 秒）：

| 指标 | 实测 | 判读 |
|---|---|---|
| 整体偏移中位 | **−4.9 ms** | 格子本身 100 ms → **0.05 格**，可忽略 |
| 各格偏移范围 | −11.6 ~ +3.1 ms | 全在 ±0.12 格内 |
| swing（奇偶格差） | **−0.7 ms** | **直拍，没有 swing** |

**结论：原曲是"贴格子"的直拍**，我之前量化到 0.25 拍**没有损失**。
这条从"未知盲区"变成"已知不是问题"，不用改。

> 反过来说：`groove_probe.py` 现在能对任意参考曲回答这个问题。
> 若某首参考曲真的 swing（差 >15ms），输出末尾直接给**可套到 MIDI 的逐格偏移表**。

## ② 和弦音该连的连没连 —— ✅ **已量掉，而且发现了一个真问题（已修）**

自写时值对比（分轨音频的"实响时长" vs MIDI 时值）+ 同小节同音高重复率：

| | 原曲实响时长中位 | 我的 MIDI 时值中位 |
|---|---|---|
| Piano | **0.32 拍**（点状、快速衰减） | **0.87 拍（2.7×）** |
| Guitar | 0.11~0.50 拍 | 0.81 拍 |
| other | 0.21~0.70 拍 | 0.64 拍 |

**根因**：CQT 转录的时值来自"峰值持续多少帧"，**系统性偏长**；
于是每个音都拖着、和弦音互相重叠 → 听感"糊、乱"。
**这不是音色问题，是音符边界问题**（用户"听着乱"的一部分就是这个）。

修法（`b35_vel.py`）：沿 10ms RMS 包络从起点往后走，能量跌破"该音峰值 −25dB"即算结束
（同一键被再敲时以下一个同轨音符为硬上限）。实测时值中位：
Piano **0.90 → 0.25 拍（28%）**、Hook → 62%、Arp → 78%、Bass 109%（本来就对）。

> 搜到的相关方案：[`partitura`](https://github.com/CPJKU/partitura)（符号音乐的声部分离、
> 表情检测）—— 但它面向 MusicXML/MIDI 的**乐谱级**分析，
> "从录音判断该不该连奏"仍要自己写，上面的包络法已经够用。
> 连奏/断奏的**自动分类**也有数据集（[Violin Gesture Dataset](https://zenodo.org/records/7100288)，
> 弦乐演奏法标注），但那是乐器特定的分类任务，不是通用扒谱流程的一环。

## ③ 钢琴延音踏板 —— ⚠ **有模型，但权重 gated，当前拿不到**

搜到两个直接对口的：

- [**ByteDance `piano_transcription`**](https://deepwiki.com/bytedance/piano_transcription/3.2.2-pedal-transcription-model)
  —— 官方实现里有**独立的踏板转录模型**（输出 CC64 曲线）。
- [**High-resolution Piano Transcription with Pedals by Regressing Onset and Offset Times**](https://ar5iv.labs.arxiv.org/html/2010.01815)
  —— 把 pedal 与 onset/offset **一起回归**，是这条线的代表工作。

**实测能不能下**：

```
python -c "from huggingface_hub import list_repo_files; list_repo_files('bytedance/piano_transcription')"
→ 401 Client Error（gated repo，需要账号授权 + HF_TOKEN）
```

与之前 [MuScriptor 权重 gated](https://huggingface.co/MuScriptor/muscriptor-medium) 同一个障碍
（HF 直连不通、镜像也拿不到 gated 权重）。
**替代路径**（未验证）：踏板的效果在**音频层**是可测的 —— 有踏板时钢琴分轨的
能量衰减明显变慢、同音重复会连成一片。可以自写"分轨能量衰减曲线"当代理指标，
但那是**间接量**，不能反推出 CC64 曲线。**这条先记为"拿不到，只能人耳"。**

## ④ 演奏法/滑音/颤音 —— ⚠ **有研究方向，没有可直接用的通用工具**

搜到：

- [Extracting and Re-Targeting Expressive Musical Performing Style from Audio Recording](https://researchportal.hkust.edu.hk/en/publications/extracting-and-re-targeting-expressive-musical-performing-style-f/)
  —— 从录音提取**演奏风格参数**（微时序、力度曲线、发音长短）并重新套到别的 MIDI 上。
  思路与我们要做的"抄演奏"完全一致，但论文没有放通用工具。
- [napanto/jazz-piano-performance-modeling](https://huggingface.co/napanto/jazz-piano-performance-modeling)
  —— 爵士钢琴的**表现力建模**（风格化渲染）。方向对口，但是爵士专用 + 需下载权重。

**可自做的部分**：滑音/颤音在**音高轨迹**上是可测的（`librosa.pyin` 的连续 f0 做二阶差分，
颤音表现为 4~7Hz 的周期调制）。这对弦乐/人声参考曲有意义；
对 BGM35 这种以钢琴/吉他为骨架的曲目**本来就没有滑音**，属于"参考曲没有、我们也不用做"。

## ⑤ 音色本身 —— ⚠ **天花板，已知**（`MIDI.md`）

GM（GeneralUser GS）vs 商业采样库。搜到的方向：

- [Assessing the Alignment of Audio Representations With Timbre Similarity Ratings](https://zenodo.org/records/17811470)
  —— 评估"音频表征与人类音色相似度评分"的对齐程度；
- [Do Joint Language-Audio Embeddings Encode Perceptual Timbre Semantics?](https://browse-export.arxiv.org/pdf/2510.14249)
  —— CLAP 这类联合嵌入**在多大程度上编码了人类感知的音色语义**（结论偏保留）。

**结论**：音色相似度**没有可靠的自动判据**（这也是之前 AQA/CLAP 反复翻车的原因：
AQA CE 给商业曲打 7.1、给我们打 7.5，但人耳结论相反）。
**这条明确交给耳朵**，不要再造指标。

## ⑥ 旋律好不好听 —— ✅ **有现成工具箱，但测的是"像不像人写的"而非"好不好听"**

- [**`mgeval`**](https://github.com/atsukoba/mgeval-python3)（atsukoba/mgeval-python3）
  —— 符号音乐生成的客观评估工具箱：音高/节奏的**分布相似度**、
  pitch class entropy、groove 一致性等。**可以直接 pip 装，纯符号、无需 GPU**。

**能测**："我的旋律的音程/时值分布与参考曲画像的分布有多接近"（我们已有 `probe_melody_lang.py` 在做同一件事）。
**测不到**："好不好听" —— 这一点论文与工具都一致回避。

## ⑦ 段间过渡顺不顺 —— ⚠ **有指标，但与"顺不顺"不是一回事**

搜到 [Long-Form Text-to-Music Generation](https://arxiv-org.ezproxy.obspm.fr/html/2411.03948v3)
里的 **KLD transition smoothness**（用相邻段的分布 KL 散度衡量过渡平滑度）。
我们有更直接的：`t_section_transition`（段界必须"渐弱/渐入 ≥4dB"或有留白）+ 段间质心跳变。
**"平滑度"能测，"顺"是听感**（平滑 ≠ 好听，硬切也未必难听）。

## ⑧ 整体像不像 —— ❌ **只能人耳**（且已被反复证明不能靠指标代替）

`similarity.py` 六轴全是时间平均统计量：补完力度（3038 个音一个值 → 真实动态）
**总分只动 0.1**。调研里也没有"感知整体相似度"的可靠自动指标。

---

## 汇总：能落地的三条

| # | 项 | 状态 | 落在哪 |
|---|---|---|---|
| ① | 起音摇摆/提前量 | ✅ **已量**：原曲 −4.9ms、无 swing → 不是问题 | `scripts/groove_probe.py` |
| ② | 和弦音该连的连没连 | ✅ **已量 + 已修**：时值缩到 28~78% | `b35_vel.py` 的实响时长法 |
| ⑥ | 旋律分布 | ✅ 可接现成工具 `mgeval-python3`（纯符号、可 pip） | 待接（我们已有同类 `probe_melody_lang`） |

**拿不到的**：③ 踏板（权重 gated，401）、④ 演奏法通用工具（只有论文/乐器专用模型）。
**不该造指标的**：⑤ 音色、⑦ 过渡顺不顺、⑧ 整体像不像 —— 交给耳朵。
