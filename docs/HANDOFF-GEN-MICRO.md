# HANDOFF —— 微时序（swing / 人性化）与「尺子自检」（2026-10-05）

> **给下一个对话**：这份讲 ⑥「微时序」这一条。结论有点反直觉，**先看 §1 再动手**。
> 一句话：**功能实现了、能精确验收，但标定结论是"本项目参考曲是直拍" → 默认关**；
> 更有价值的是 §3：**怎么发现一把尺子测不了它声称要测的东西**。

---

## 0. 做了什么

| # | 内容 | 位置 |
|---|---|---|
| 1 | **微时序开关**：`patterns.swing`（奇数 16 分格后移，单位拍）+ `patterns.swing_humanize`（逐音确定性抖动） | `song_engine.apply_micro_timing()`，在 `write_midi` 里**排在 `legato_trim` 之前** |
| 2 | **MIDI 域验收尺子**：直接量成品 MIDI 的奇偶格中位差（无检测误差） | `D:\test\_tmp\boost-verify\swing_midi.py` |
| 3 | **音频域尺子 + 自检**：真分轨的 swing/人性化 + "这把尺子准不准" | `swing_real.py` · `humanize_real.py` · `ruler_selfcheck.py` |
| 4 | **守卫**：`selftest.t_micro_timing_swing`（单点反证 **4/4** 全抓到） | `scripts/selftest.py` |

**读数（成品 MIDI，精确值）**

| 状态 | 偶格中位 | 奇格中位 | swing 差 | 贴格(≤1ms) |
|---|---|---|---|---|
| 库里 16 首（未开） | +0.00ms | +0.00ms | **+0.00ms** | **100%** |
| 100 号开 `swing=0.05` @142BPM | +0.00ms | **+21.13ms** | +21.13ms（理论 0.05×60000/142 = 21.13ms，**精确吻合**） | 23% |

`selftest --fast` **203/203**（新增 1 条）。

---

## 1. ⚠ 标定结论：本项目手头的真参考曲**是直拍**，所以默认必须关

用真分轨（`D:\test\llm_direct\projects\bgm3{5,9}\stems\htdemucs_6s\`）量出来的 swing：

| 源 | BPM | swing 差 | 判据（>15ms 才算 swing） |
|---|---|---|---|
| BGM35 drums | 120（工程 MIDI 里写着） | **+9.7ms** | 不算 |
| BGM35 bass | 120 | +4.7ms | 不算 |
| BGM35 piano | 120 | +2.1ms | 不算 |
| BGM29 drums | 150 | −5.5ms | 不算（略抢拍） |
| BGM29 bass | 150 | −0.5ms | 不算 |

⇒ **照抄"swing 24%"之类的预设会让成品更不像参考**。这条功能只在**需要摇摆的曲风**
（爵士/摇摆/蓝调/部分 city pop）上开，靠 `patterns.swing` 逐曲给：
`0.03` 拍 @120BPM ≈ 15.6ms（刚过判据）· `0.06` ≈ 31ms（明显摇摆）。
**别把它设成全库默认**（那等于给所有曲子加了一层参考里没有的东西）。

---

## 2. 微时序与腿音修剪的相互作用（本轮新引入，务必守住）

`apply_micro_timing` 改起音 ⇒ 若它排在 `legato_trim` **之后**，会把"同轨同音高不重叠"
重新顶开（部分 GM 音源会吞掉后一个音）。
实测（100 号，`swing=0.05`）：移位后同音高重叠 **384 处**（最大 3.9 拍）→ 修剪后 **0 处**。
守卫 `t_micro_timing_swing` 用**行为断言**（造夹具跑 `write_midi`、读回成品 MIDI 查重叠），
而不是"查源码里两行的先后"—— 后者对"把调用挪到后面"这种坏法**抓不到**（本轮实测 3/4 漏 1）。

---

## 3. ⚠⚠ 尺子自检：`groove_probe` 测不了 <15ms 的微时序（本轮最有价值的一条）

**怎么发现的**：先信了 `groove_probe` 的读数（真分轨"人性化"|偏离| 中位 25~33ms、
我们只有 12ms），差一点就据此"补人性化"。**先拿已知答案验尺**：

| 对象 | 已知答案 | 尺子读数 |
|---|---|---|
| 我方 100 号（142BPM，**逐音精确落 16 分格**） | 应 100% 贴格、swing=0 | |偏离| 中位 **15.3ms**、≤15ms 仅 **49%**、swing +0.8ms |
| 同上，自相关 tempo | 142 | 找到 **71**（差一个八度） |
| 同上，逐段相位（8 窗） | 应恒定 | `[-4.3, 7.3, 4.2, 5.1, 3.2, 1.6, 6.4, 7.4]` ⇒ **稳定在 ±7ms**（说明曲子确实贴格） |

**结论**：谱通量起音检测的**绝对**时刻有 **±15ms 量级的噪声底**（检测误差 + 混响/泛音引起的
伪起音）。所以：
- ✅ 能用的：**相对量**（同一次检测内奇格 vs 偶格中位差；逐段相位的**稳定性**）；MIDI 域读数（精确）。
- ❌ 不能用的：音频域**绝对**偏移（"我们 12ms vs 参考 30ms"这种比较**无效**）；
  `groove_probe` 自相关找 tempo（会把 142 报成 71）。
- ⇒ ④ 那份交接里 ⑥ 的判据「swing > 15ms」**要加前提**：只能拿**同一把尺子的两个格子**比，
  不能跨源比绝对值；跨源比较请用 MIDI 域（有 MIDI 时）。

**可复用的检查法**（任何"量音频微时序"的工具都该先过一遍）：
① 拿自己的量化成品当"已知答案"量一次，读数应≈0；
② 逐段相位应恒定；
③ 换个 BPM 假设看读数怎么动（BGM35 的 BPM 搜索把所有候选都拉到 187~199，
   σ 全是 21.7ms —— **σ 不随 BPM 变化 = 搜索在拟合噪声**，这条一眼就能看出尺子坏了）。

---

## 4. 没验证什么

- **听感一条没有**（我听不了音频）：开 `swing=0.05` 那一版**没人听过**。
- 参考曲的 swing 只有 **2 首**（BGM35/BGM29），且都来自 Demucs 分轨（分离本身会抹平微时序）
  —— "本项目参考曲是直拍"的**适用范围只有这两首**，不能推广成"真实 BGM 都不 swing"。
- ⑦（演奏法：连奏/踏板/力度曲线）没做；`articulation` 仍无。
- 人性化的**幅度**没法验收（噪声底同量级）⇒ 只能当风格旋钮，不许当"达标/不达标"读数。

---

## 5. 产物（绝对路径）

- 引擎：`D:\software\skill\music-gen\scripts\song_engine.py`（`apply_micro_timing` + `PAT_KEYS`）
- 守卫：`D:\software\skill\music-gen\scripts\selftest.py`（`t_micro_timing_swing`）
- 尺子与读数：`D:\test\_tmp\boost-verify\` 下
  `swing_midi.py` / `swing_midi.txt`（MIDI 域）· `swing_real.py` / `swing_real.txt`（真分轨）
  · `humanize_real.py` / `humanize_real.txt` · `ruler_selfcheck.py` / `ruler_selfcheck.txt`
  · `swing_guard_mutation.py` / `swing_guard_mutation.txt`（单点反证 4/4）
  · `set_swing.py`（给某曲设/清 swing，走 `json_io.save`）
- 真分轨（参考）：`D:\test\llm_direct\projects\bgm35\stems\htdemucs_6s\BGM35\*.wav`
  · `D:\test\llm_direct\projects\bgm29\stems\htdemucs_6s\BGM29\*.wav`
