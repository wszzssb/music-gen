# 交接：八度校正器（**最小版本**）—— 2026-10-18

> **一句话**：低音的"差整八度"是**已量化的系统错**（BGM29 修了 408 个音、占"有参考样本"的 39%），
> 现用 `bass_ensemble --octave-ref` 的 **pyin 硬规则**修；这份交接要把它换成**学习型判别器**，
> 目标**超过 pyin 规则**，并用同一把尺子量化。
> **先读 §4 的红线**（标签与验收同源 = 自证），再看 §2 素材与 §6 命令。

## 0. 为什么值得做（今天的实测，可复查）

| 曲 | Bass 音数 | pyin 修正 | 审计一致率（vs bass 分轨） |
|---|---|---|---|
| BGM29 | 279 → 1176 | **408** / 有参考 1056 | **3.9% → 62.1%**（校准值 53.2%） |
| 已至 | 127 → 328 | **107** / 有参考 243 | **7.6% → 32.2%** |

现有规则的边界很清楚：pyin 在参考分轨上按 **0.1s 网格取中位**，只改 **±12 / ±24**、
置信度 ≥ **0.20** 的音，其余一律不动（`bass_ensemble.py` 的 `--octave-ref`）。
它**不看"这个音的谐波长什么样"** —— 这正是可学的地方。

## 1. 目标与验收（先定，否则训完说不清有没有用）

**任务**：给一个候选音（时间 t、音高 p、所在分轨局部音频），判 `0 / +12 / −12`。

**三组对照**（同一批留出曲目，按曲划分）：

1. `base`：不校正；
2. `pyin`：现有硬规则（复现 `--octave-ref` 的判定）；
3. `learned`：本轮产物。

**判据（缺一不可）**：

- 用 `scripts\transcribe_audit.py` 量**一致率**：`learned` 必须在**至少一档**上 ≥ `pyin`
  （否则**不落地**，保留 pyin）。
- **假修正率**：`pyin` 判"本来就对"的音里，`learned` 改错的比例 ≤ **5%**。
- **按曲划分 train/test**，不许按音随机分（同曲相邻音高度相关 → 泄漏）。

## 2. 素材（现成，不用重跑转录）

| 用途 | 绝对路径 |
|---|---|
| BGM29 bass 分轨（输入兼尺子） | `D:\software\skill\music-gen\_extract\BGM29\stems\htdemucs_6s\source\bass.wav` |
| 已至 bass 分轨 | `D:\software\skill\music-gen\_extract\已至\stems\htdemucs_6s\source\bass.wav`（**含中文，先拷 ASCII**） |
| 低音转录（base） | `_extract\BGM29\ymt3\BGM29_stems\BGM29_Bass.mid` · `_extract\已至\ymt3\已至_stems\已至_Bass.mid` |
| 多来源（BGM29 那 3 份） | `D:\test\llm_direct\projects\bgm29\ymt3\bgm29_bass_ymt3.mid` · `midi\bp_bass4.mid` · `midi\bp_bass6.mid` |
| 已修好的产物（对照） | `D:\test\BGM29-bass-fix\`（`BGM29_bassfix_t55_vel.mid`）· `D:\test\已至-bass-fix\` |
| 其他曲的分轨（扩样本） | `_extract\{BGM01,BGM01zz,BGM35,BGM36}\stems\htdemucs_6s\`（都有 bass.wav） |

**建议的新工具名**：`octave_corrector.py`（**本轮新建**，三步 `--train` / `--apply` / `--audit`）。

## 3. 最小实现（建议，不必照抄）

- **特征**（每个候选音取 3–5 帧）：`E(f0)/E(2f0)`、`E(f0)/E(f0/2)`、低频占比、谱平坦度、
  pyin 的置信度与差分。⚠ 这些量**单独用都试过不行**（PITFALLS **286**：能量份额只做判读、不做门）——
  学习型的关键是**组合**，不是找新单量。
- **模型**：<100k 参数的小 MLP / 1D-CNN，CPU 也能跑。
- **标签**：用 pyin 规则自动生成（三态"修正八度 / 本来就对 / 无参考"），**只用"有参考"的样本**。
- **规模**：两首合计 **~1300** 个有参考样本 —— **偏少**。要提上去就把 `_extract` 下其余曲目
  也跑一遍 `--octave-ref` 扩到 ~5000（代价：每首 10–30 秒 pyin）。

## 4. 红线（**最要紧的一条**）

1. **标签与验收不能同源**：标签来自 pyin、验收也是 pyin ⇒ 只能证明"学会了 pyin"。
   必须有**第二把尺子**：① 能量份额法（`tools/bgm35-3m31-probes/q2c_octave_final.py`，
   口径见 `HANDOFF-TRANSCRIBE.md` §11.4）② 人工抽 100 音看谱 ③ 另一种 F0 估计器（本机没有，要装）。
2. **别把"修得多"当"修得对"**：只有**审计一致率涨**才算；只数"改了几个音"会自我欺骗。
3. **改 MIDI 要能回退**：改前记 SHA256、逐音读回核验（PITFALLS **238** 的纪律）。
4. **本任务只产出 MIDI / 判别器**：装进曲库是另一件事（今天 BGM29 装过一版，
   流程与回滚见 `songs_direct\BGM29\notes.md` 的「低音重做」节）。

## 5. 今天踩过的坑（省时间）

- `.venv` 是 **Python 3.14**、`.venv-ml` 是 **3.13**。`bass_ensemble --octave-ref` 会**硬插**
  `.venv-ml/Lib/site-packages` ⇒ 必须用 `.venv-ml\Scripts\python.exe` 跑（用 `.venv` 会 scipy ABI 崩）。
- `.venv-ml` 的 libsndfile **打不开含中文的路径**（`System error`）⇒ 先拷到 ASCII 路径。
- `measure_velocity --track "名=wav:mid"` 会被 Windows 的 `D:` 切断（`rsplit(':', 1)`）⇒ 用三个位置参数。
- `transcribe_audit` 的 70% 门**只对单音层成立**（校准值：Bass 53.2% / Piano 9.1%）。
- 训练入口 `amt/`（在 `D:\test\models\ymt3repo`）**从没在本机跑通过**（只跑过推理）——
  别把"能推理"当"能训练"；8GB 显存全参数微调不现实。

## 6. 验收命令（照抄）

```powershell
$py = 'D:\software\skill\music-gen\.venv\Scripts\python.exe'
cd 'D:\software\skill\music-gen'
& $py scripts\transcribe_audit.py <bass.wav> <midi> --tracks Bass --fmin 40 --fmax 300
```

三组各跑一次，把**一致率 / 差八度 / 漏检 / 假音**四行贴进结论。

**要复现今天的 pyin 基线**（BGM29，产物在 `D:\test\BGM29-bass-fix\`）：

```powershell
cd 'D:\software\skill\music-gen'
& 'D:\software\skill\music-gen\.venv-ml\Scripts\python.exe' scripts\bass_ensemble.py `
    --base _extract\BGM29\ymt3\BGM29.mid --out <输出>\BGM29_bassfix.mid `
    --source "ymt3b=D:\test\llm_direct\projects\bgm29\ymt3\bgm29_bass_ymt3.mid|24|60" `
    --source "bp4=D:\test\llm_direct\projects\bgm29\midi\bp_bass4.mid|24|60" `
    --source "bp6=D:\test\llm_direct\projects\bgm29\midi\bp_bass6.mid|24|60" `
    --layer Bass --program 38 --sub --min-beats 0.47 --thr 0.55 `
    --octave-ref _extract\BGM29\stems\htdemucs_6s\source\bass.wav
# 预期输出：修正八度 408 · 本来就对 597 · 非八度差异 51 · 无参考 66 · Bass 973 音 + sub 203
```

## 7. 没验证什么（交付时照写）

- 学习型判别器**能不能超过 pyin**：不知道。唯一的反例是"手工能量判别器 AUC 0.64 失败"。
- ~1300 样本够不够：**大概不够**，扩样本方案见 §3。
- 8GB 显存下微调 YMT3：**没试**（上一轮结论：全参数微调不现实）。
- 第二把尺子（能量份额法）**没在这两首上标定过** —— 用之前先拿今天已知的 408/107 个修正当正例跑一遍。

## 8. 本轮实测结论（2026-10-18 · 已落地 `scripts\octave_corrector.py`）

**一句话：学习追不上"换聚合"。** pyin 规则（0.1s 桶中位）与帧级众数在 1325 个有参考音上
只差 **27 个（2.0%）**，pyin 判改的 650 个音里众数 **100% 同意** ⇒ 规则已基本等于帧级最优。
频域第二把尺子（谐波显著性 / 最低谱峰）单独标定**不可信**（与 pyin 一致率 0.46~0.94），
改用 **CREPE**（`torchcrepe` 已装）：两把尺子都判"有声"的帧上 pyin 与 CREPE 音高一致
**93.1~99.7%、差八度 0.000**，可当独立验收。

五臂（帧级一致率 %）· 四首留一曲 = BGM01/BGM29/BGM35/已至 · BGM29 集成 = 1122 音基座：

| 臂 | 留一曲 | 集成 |
|---|---|---|
| base | 17.9 · 3.9 · 33.6 · 7.6 | 48.1 |
| pyin | 22.6 · 16.7 · 65.9 · 24.6 | 70.7 |
| **mode（帧级众数）** | **23.0 · 17.2 · 66.5 · 24.7** | **71.5** |
| learned | 22.4 · 17.0 · 64.8 · 24.6 | 71.2 |
| **hybrid** | 22.6 · 17.0 · 66.1 · 24.6 | 71.2 |

CREPE 尺子同向（集成：pyin 78.7 · mode 79.6 · learned/hybrid 79.3）。
假修正率（pyin 判"本来就对"里被改）：learned 0~2.08%，**hybrid 全 0.00%**（≤5% 通过）。

- **可落地**：`mode`（桶中位 → 帧直方图众数）每首 +0.0~+0.9 点（最差持平）；`hybrid` = 规则优先 +
  模型只补规则弃权的近八度（`dd=±10..14`）、且须与众数一致，**≥ pyin 且不丢修正**。
- **纯 learned 不要落地**：BGM35 64.8 < 65.9，漏掉 13 个 `+12`（全库 8 个 `+12`、6 个在本曲，
  按曲留出训练集只剩 2 例）；用众数当标签训练则逐点复刻 mode（集成 71.5 = mode），无增量。
- 尺子自检：本工具 `audit` 与 `transcribe_audit.py` 同一 MIDI **逐项同数**
  （70.7/2.1/13.3/13.9/11.7）；`--arm pyin` 与原工具都是 **408 改 / 973 音**。
- 新踩的坑：**BGM01zz 的 MIDI 与 BGM01 逐字节相同**（按曲划分必须去掉一份）；
  BGM36 的 bass 分轨几乎没内容（pyin 有效帧 3.4%、0 参考）。
- 残差拆解（校正后仍 13.9%/28.0% 漏检、13.3%/4.6% 非八度错音）与完整表 →
  `D:\test\octave-corrector\REPORT.md`（交付目录，非仓库）。
