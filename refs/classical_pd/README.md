# `refs/classical_pd` —— **可再分发**的古典 MIDI 模板库

> 与 `refs/midi2/` 的区别：**这一支的每一首都许可明确、可以随仓库分发**。
> `refs/midi2/` 里的 bitmidi/vgmusic 那 199 首是粉丝转录（版权灰色），**不进仓库**。

## 组成（共 424 首 · 6.0 MB）

| 来源 | 首数 | 许可 | 说明 |
|---|---|---|---|
| Mutopia Project | 128 | PD / CC（逐首见来源页） | `mutopia/<作曲家>/*.mid`，钢琴曲 |
| HuggingFace `TiMauzi/imslp-midi-cc0-1.0` | 296 | **逐行 `license == cc0-1.0`** | `hf_cc0/<风格>/*.mid` |

## ⚠ 为什么 HF 只收了 296 首（那个数据集有 1113 行）

数据集 tag 写着 `cc0-1.0`，但**逐行 `license` 只有一部分有值**：
890 行（train）里 **653 行为空**，且空的那批正是 Bach / Vivaldi / Mozart / Scarlatti 等
知名作曲家（来源是 IMSLP 的 `PMLP*` 文件页，**转录许可未标注**）。
按用户口径"**转录许可不明不用**"，这些**一律不收** —— 想收得回 IMSLP 逐首核转录者许可。

## 风格分布（HF 部分）

- Modern                   142
- Romantic                 31
- Baroque                  29
- Early 20th century       28
- Classical                25
- Renaissance              23
- Traditional              6
- Ancient                  5
- Jazz                     4
- Medieval                 2
- Non-western classical    1

## Mutopia 部分（按作曲家）

- ChopinFF         10
- BeethovenLv      10
- SatieE           10
- HandelGF         10
- SchubertF        10
- SchumannR        10
- BrahmsJ          9
- HaydnFJ          9
- BachJS           8
- GriegE           8
- FaureG           8
- MozartWA         7

## 索引

- `_index_hf_cc0.json`：逐首 `file / style / composer / title / era / key / year / license / source / md5`
- `mutopia/_index.json`：逐首 `file / composer / title / source / license / md5`

## 复现

```powershell
$py = "<工具链根>\.venv\Scripts\python.exe"
& $py tools\fetch_classical_pd.py     # HuggingFace CC0（自动跳过许可不明）
& $py tools\fetch_mutopia_pd.py       # Mutopia 钢琴曲（PD/CC）
```
（`pyarrow` 是读 parquet 分片的依赖；HF 直连不通时脚本走 `hf-mirror.com`。）
