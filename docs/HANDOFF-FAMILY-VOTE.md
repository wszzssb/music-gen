# 交接：把"族票"接进提取流水线（**下个对话从这里接**）

> 写于 2026-09-30。上两份：`docs/HANDOFF-TRANSCRIBE.md`（这条线的总交接，**§12 = 本轮的实测**）
> 与 `docs/TRANSCRIBE-AUDIT.md`（怎么量准）。
> **本文档只讲一件事：本轮量出来的规则怎么进流水线，以及验收判据。**
> 本轮提交：`25de0b6`（应用层优化）· `cf87c4b`（k 标定 + 无监督开关 S1）—— 都已在 GitHub。

---

## 0. 一句话现状

**规则已经量出来了，但还没进流水线。** 下个对话的第一件事就是**把它接进去**，而不是再找新信号。

| 项 | 状态 |
|---|---|
| 量出"族票"规则（同族多视图算**一票**） | ✅ 实测 +0.0083（10 首 · 9 涨 1 跌 · 收进精度 0.394） |
| 量出无监督开关 `S1`（↔ ΔF1 的 ρ = −0.782，阈值 0.825） | ✅ 见 `HANDOFF-TRANSCRIBE.md` §12.4 |
| 工具原型 `tools/vote_family_rule.py` | ✅ 能跑（真曲自证 ΔF1 +0.0066），**但没自检、没变异用例** |
| **接进 `scripts/vote_apply.py`** | ❌ **没做 —— 这就是本交接的任务** |

---

## 1. 任务：把族票规则接进 `vote_apply.py`

**要改什么**（`tools/vote_family_rule.py` 里已有一份可抄的实现，约 60 行核心逻辑）：

1. `vote_apply.py` 现在按"**视图个数**≥k"收新音（`--k`）。改成按"**族**"：
   `--family-min N`（默认 1）= 每个族**至少 N 个视图**提过这个音才收。
   族名从 `--view` 的 label 前缀推（`ymt3_*` → `ymt3`，`bp_*` → `bp`），或显式写成 `族=路径`。
2. **保留旧行为**：`--k` 还在时走旧逻辑（逐字节不变）—— 参照仓库惯例
   （如 `drum_grid` 逐条指定鼓件是 opt-in：两元素时逐字节等同老行为）。
3. **加 `--s1`**：打印 `S1`（base 被视图覆盖率）并按阈值 **0.825** 给建议
   （只提示，**不自动改行为** —— 阈值是在 5 视图口径上事后选的，见 §3 风险）。

**验收判据（照抄，缺一条都不算完）**：

```bash
# ① 单曲：族票必须比旧规则高，且涨/跌比不劣化
python scripts\vote_apply.py <真值.midi> <base.mid> <out.mid> --family-min 1 --view ymt3=... --view bp=...
#    期望：ΔF1 与 tools\vote_family_rule.py 在同一首歌上**读数一致**（工具间自证）
# ② 10 首留出集：族票 ΔF1 均值 ≥ +0.008（旧规则 +0.0041）、涨/跌 ≥ 9/1
# ③ 旧路径回归：不给 --family-min 时，产物与本题库旧版**逐字节相同**
# ④ 新增检查项 = 必须配变异用例（CONVENTION §4-A）：
#    注入"族票退化成视图票"（把族名当视图名）→ 必须被抓
python scripts\selftest.py --fast && python scripts\mutation_check.py
```

**素材（都在，别重下）**：

| 资产 | 路径 |
|---|---|
| 10 首留出集（真值 + 9 视图 + base） | `D:\test\_tmp\resid\new\`（`mid/*.midi` 真值 · `views/` 9 视图 · `best/*_v0.mid` base） |
| 3 首对照（古典/爵士/电子） | `D:\test\_tmp\resid\g\` |
| 本轮全部脚本与明细 | `D:\test\_tmp\mg-audit\`（`a1_vote_rules.py` · `a2_family_rules.py` · `voteA_rules.json` · `voteA2.json`） |
| 尺子 | `python scripts\truth_eval.py --selftest`（**必须先全 PASS**，14 项） |

---

## 2. 纪律（这一轮踩到的，别重复）

1. **精度只能用尺子量**：`truth_eval.py`（`--selftest` 先过）。同模型多份输出只证明"一致"。
2. **别信"独立"**：TTA（变速当新视图）造出的视图与既有 BP 重合只有 **0.032**（够独立了），
   但收益是**负的**（+0.0083 → +0.0075）。**收益只取决于"收进来的音自己准不准"**（PITFALLS 293）。
3. **口径变了要重标**：`S1` 对视图数敏感 —— 同一首 **5 视图 0.691 / 9 视图 0.823**（差 0.13）。
   阈值 0.825 只在 5 视图口径上成立。
4. **`selftest` 别和 `mutation_check` 并发跑**：`determinism_and_bytes` 会**假 FAIL**（串行即过）。
5. **改文档后跑 `python scripts\doc_map.py`**，否则 `doc_map_fresh` FAIL；
   **往有预算的文档加内容先抬 `token_audit.LIMITS`**（本轮同族第四次"抬窄"翻车，
   HANDOFF 已抬到 12600、压缩目标 11800 —— 那是笔欠账）。
6. **真实录音上 S1 偏低**（塞壬实测 0.174 vs 留出集同口径 0.33–0.42）：它是**风险指标**，
   不是开关真理；成因（素材更难 vs 骨架脏）**没有真值判不了**。

---

## 3. 还没做（按性价比排，都写清了验收判据）

| # | 任务 | 为什么值得做 | 验收判据 |
|---|---|---|---|
| **1** | **族票进 `vote_apply.py` + 自检 + 变异用例** | 规则量出来了却没进流水线 = 等于没做 | §1 的四条 |
| 2 | **留出集扩到 20–30 首**（候选 E′） | 阈值 0.825 与"族票 +0.0083"都是在 10 首上标的 | 新素材上族票仍 ≥ +0.005 且涨/跌不劣化 |
| 3 | **换模型族当投票人**（候选 J′② 的另一半） | 现在只有 2 个族，族票上限受此限制 | 新族加入后 ΔF1 提升且**精度的跌幅 ≤0.05** |
| 4 | 清理 `D:\test\_tmp\mg-audit\` 与本轮中间产物 | 中间产物已沉淀进文档 | 文档里的路径指针全部改指仓库或交付目录 |

⚠ **不建议再做**：TTA 的各种变体（YMT3 上的 TTA / 加噪 / 时移 / 分块）—— 见 §2 第 2 条，
   除非有证据表明"新视图的**单票精度**能到盈亏线以上"（本轮 TTA 单票精度只有 0.32–0.38）。

---

## 4. 环境与入口

```bash
C=D:\software\skill\music-gen
$C\.venv\Scripts\python.exe $C\scripts\truth_eval.py --selftest     # 尺子（先过）
$C\.venv\Scripts\python.exe $C\scripts\selftest.py --fast           # 守卫（约 3 分钟）
$C\.venv\Scripts\python.exe $C\tools\vote_family_rule.py --help     # 规则原型
studio\start.cmd                                                    # 面板 http://127.0.0.1:8765
```

- 主 venv 已装 `librosa`（2026-09-30 起），**全量自检 175/175**；
- GPU 可用（RTX 5060 Laptop / torch 2.11+cu128），YMT3 与 Demucs 都在 `.venv-ml`；
- 面板当前曲库 = `D:\software\skill\music-gen\songs`（35 首，`studio/.libpath`）。
