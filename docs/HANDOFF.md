# 交接：19 首生成曲的听感修复（2026-09-18 第二轮止）

> **下一个对话从这里开始，不要重新摸索。** 本文所有数字都是可复现的实测值。
> 用户听感四条 → **鼓像打字机 / 伴奏撞主奏 / 段界硬切 三条已修完并全库验证**；
> **只剩「旋律怪」（+ `theme_timbre_pool`）**，下面给口径、位置与已排除的方向。
> **方法（量法 + 修法，可复用到别的曲子上）→ `docs/MAKE-IT-SOUND-ALIKE.md` §3.2**，本文只留状态与红线。

## 0. 用户方针（已进 `SKILL.md` ⑤，不要再问）

**起伏主要做在编配层，音量只是辅助。** 段间厚薄对比靠**加减声部 / 换乐器 / 改音区**，
不靠 CC7 / 电平；边界处的对比用**一个小节爬坡**（段末逐条退场），不在边界一瞬切换。

## 1. 已完成 ✅

| 项 | 结果 | 位置 |
|---|---|---|
| Hook 音色避开极响吉他（2.5–5kHz > 41dB）+ 按音色标定电平（`HF_LEVEL`/`HF_CC7_K=0.49`） | Hook 从钢弦吉他 → 钢琴/尼龙吉他 | `d9be3fc` |
| `new_song --force`（原名撞目录会**静默拒绝**） | 面板侧撞上会自动补 | `d9be3fc`、`732fbb0` |
| 面板 `/api/new` 补 `force`/`seed` + `--force` 后**自动重渲染** | 走面板生成已跑通 | `732fbb0` |
| `melody_gen` 支持 3/4（`SPB` 按拍号设 + 重绑默认参数） | waltz 实测 `timesig=[3,4]`、68 个落点 0 越界 | `251fc9c` |
| 段界判据取样改"段中部 30%~70%"（**门限一字未动**） | FAIL 4 首 → 3 首 | `f00a7e4` |
| 段末逐条退场（`song_engine` 的 `_RAMP`+`_ORDER`） | `02_wave_walk` **0/6 → 6/6** | `bffe306`、`13dc4ba` |
| 🆕 **鼓四档全部重做**（`perc_part`：三件套 + 十六分落点 + 力度分档 + 哈希抖动 + 曲名 seed） | 19 首 **16 格全满**、力度 **39~76 档**（旧 7~39）、音/小节 10.5~19.9 | 本轮 |
| 🆕 **伴奏避让主奏**（`patterns.avoid_lead`，和声内移动） | 全库撞主奏 **763 → 31 处**（低于仿写的 41） | 本轮 |
| 🆕 **段首极短渐入**（`patterns.seg_in = 0.75` 拍，只给 17/19） | **全库 102 个段界 0 FAIL** | 本轮 |

## 2. 未完成 ❌

### 2.1 旋律怪（唯一剩下的听感条，用户原话："旋律怪一行未动"）

两条守卫仍 FAIL，**都在旋律数据层**（`song.json` 的 `melody`，不是引擎）：

- `melody_matches_profile`：`07_hidden_door` **时值 40%**、`16_morning_two` **时值 31%**（下限 40%）
  —— 量的是"该段旋律的落点/时值直方图与画像的交叠率"（`MELODY_ACCEPT_MIN = 0.40`）。
- `melody_onset_spread`：`05_soft_memory` **段Outro 落点偏离 0.702**（门 0.65）
  —— 落点挤在少数格子里（`melody_gen.onset_tvd` 与**该段画像**比）。

**已排除的方向**：别去调画像（画像来自真实模板）、别用频谱指标判"怪"。
**待办第一步**：照鼓那条的做法 —— **先量仿写**（`b35_remake.mid` 的 Melody 轨，442 音）的时值分布
与落点分布，再决定是改 `melody_gen` 的出口还是重生成这 3 首的旋律。
⚠ `melody` 是**数据**：改它要重生成（`new_song --force`）或按指纹改 `song.json`，**别叠加后处理**。

### 2.2 `theme_timbre_pool`

`Hook` 轨没滤掉 111 唢呐（不是拨弦）—— `arrangement.prog_pool` 的取用层过滤漏了一处。
与本轮三条听感问题**无关**，是独立的一个小口子。

## 3. 当前守卫状态：**121/124**

- 剩下的 3 项就是 §2.1（2 条）+ §2.2（1 条）。
- 本轮修掉的：`determinism_and_bytes`（01 重跑后自然恢复）、`section_transition`（17/19 那 2 个边界）、
  `density_dynamic_range`（鼓变密后的连带效果）。
- ⚠ 另有 `docs_budget_and_skill_intact` 报 `~/.dsh/AGENTS.md` ≈1826 tok 超 1500 预算
  —— **不是本轮改的**（它新加了 win-forensics 那段），但它**每个对话都常驻**，建议拆。

## 4. 流程红线（都是踩出来的）

1. **改引擎 ⇒ 重生成 + 重渲染，两步缺一不可**。只跑一半的症状是"命令 ok、`song.json`
   一字未动、听感没变"（`new_song` 撞同名目录**只打印"已存在"就退出**）。
2. **生成第一件事走面板**：`POST http://127.0.0.1:8765/api/job?id=<曲>&kind=render`（重渲染）
   或 `/api/new`（重建 `song.json` + 自动重渲染）。批量才用 CLI 并行
   （bash 数组 + `&` + `wait`，5 路；18 首约 10 分钟），**批量完必须回面板核对**。
   ⚠ 别用**多行 heredoc + `while read`** 喂清单（会吃行首字符，18 首目录名错 17 个）。
3. **先量包络 / MIDI，再动手**：段界那条上一轮猜了 4 个方向（加留白 / 压电平 / 调 `section_gap` /
   段首稀疏）**全部无效**，量了逐 0.05s RMS 才看清真相。
4. **别删日志、别用管道判退出码**：`git push | tail` 让退出码变成 `tail` 的；
   多进程并发写同一日志会让计数失真（据此误报过"6 首失败"）。
5. shell 操作**一律落 `.sh` 文件**再跑（`pwsh` 只允许 `& 'bash.exe' 'x.sh'` 一种形式）。
6. 🆕 **"读数一字未变"要当"没生效"查，别当"改动无效"** —— 两者排查方向相反
   （本轮 `avoid_lead` 第一版就是这样：`ch_all` 的键是**和弦标识**不是小节号，查表永远落空）。
   → `PITFALLS.md` 200。
7. 🆕 **改包络类判据前先问"这个窗口的能量是谁发出来的"**（新起音 / 持续音 / 混响尾巴）
   —— 段末压"新起音的力度"在我试的那组参数下没动读数、段首才有效
   （⚠ 别读成"段末原理上无解"，我只试了一种窗口与幅度）。→ `PITFALLS.md` 201。
8. 🆕 **临时脚本别落在 `scripts/`** —— 自检把 `scripts/*.py` 全当入口脚本扫，
   凭空多两条 FAIL（`import_all` / `console_encoding_safe`）。落仓库外，如 `D:\test\`。→ `PITFALLS.md` 202。

## 5. 环境与路径

- 工具链 `D:\software\skill`（venv `.venv`；**`mido` 只在 `.venv-ml` 里**）· 面板 `http://127.0.0.1:8765`
  （**若面板没在跑：双击 `studio\start.cmd`** —— 会话后台作业起的那份会被带走）
- 曲库 `songs/`（19 首）↔ 面板库 `D:\test\llm_direct\studio_lib\songs`（**junction，单一数据源**）
- 旧 18 首备份：`D:\test\llm_direct\songs_backup_20260918\songs\`（⚠ 是**改名前**的目录名：
  `02_seaside_walk` = 现在的 `02_wave_walk`）
- 仿写参考（**所有"照仿写来"的基准**）：`D:\test\llm_direct\studio_lib\songs\99_b35_remake\`
  （`b35_remake.mid` = "更进一步"版，9 轨；`v5_source.mid` 是较早的 5 轨版，**读数不同别混用**）
- 本轮量测/诊断脚本（可复用，都在**仓库外**，避免踩坑 202）：
  `D:\test\_k_audit_drums.py`（逐曲鼓审计，按档判定 + 面板能播检查）· `_k_ovsum.py`（全库撞主奏汇总）·
  `_k_scan_sec.py`（全库段界扫描）· `_k_diag_sec.py`（单曲逐边界四窗口）· `_k_checkmid.py`
  （音域 / 同轨同音高重叠）· `_k_set_pat.py`（给某曲设 `patterns` 键，记 SHA256）·
  `_k_paneljob.py`（走面板起重渲染并轮询）
- 主题包 `refs/themes/*.json`（15 个，含 `arrangement.prog_pool` = 模板实际音色池）
- 本轮新增的三个 `patterns` 键（**都已进 `PAT_KEYS` 白名单**）：`avoid_lead`（允许的音高差；
  2 = 修"伴奏撞主奏"）· `seg_in`（段首渐入长度，单位拍）；`seg_fade` / `section_gap` 是上一轮就有的。

## 6. 量测代码（重写成本很低，逻辑就这几行）

```python
# 鼓力度/落点（⚠ 只认 channel 9；按"音高落在 35..82"猜会把 Hook/Piano 误判成鼓）
perc = [n for n in notes if n.channel == 9]                              # (起始拍,时值,音高,力度)
vels = Counter(int(v) for (s, du, p, v) in perc)                         # 力度档数
slots = Counter(int(round((s % 4) * 4)) % 16 for (s, du, p, v) in perc)  # 16 分格（3/4 按拍号取模）
# 跨轨撞音（同起点 + 同音高）
onsets[(round(s, 3), p)].append(track_name)      # 长度 > 1 即撞
# 伴奏撞主奏（宽口径）：伴奏轨与 Melody **时间重叠**且 |Δpitch| <= 2
# 段界包络
env = 20*log10(RMS(mono, hop=sr*0.05))           # 每 0.05s
# 门：fade_out>=4 或 fade_in>=4 或 jump<3（pre/post 取"段中部 30%~70%"）
```
