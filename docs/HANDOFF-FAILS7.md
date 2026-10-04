# FAILS7 完成记录（2026-10-05）—— 7 条 FAIL 全绿 ＋ **对原交接的 4 处纠正**

> 本文档由交接文档 `HANDOFF-FAILS7.md` 改写而来：原交接的【现象·读数·修法·验收】仍有效，
> **但其中 4 条的真因/范围被本轮实测推翻或收窄**（见 §纠正）。

## 收尾读数（2026-10-05）

| 项 | 基线（接手时） | 修完 |
|---|---|---|
| `scripts/selftest.py` 全量 | **193/201**（交接写 194） | **201/201** ✅ |
| `scripts/mutation_check.py` | 291/291 | **293/293** ✅（+2 条新用例，见下） |
| `token_audit` AGENTS.md | ≈3190 tok（限 2500，⚠ 超） | **2453 tok**（余量 47）✅ |
| 全库「已交付 `.mid` = 当前引擎输出」 | 22/32 一致、**10 首不一致** | **32/32 一致、0 首不一致** ✅ |

⚠ 基线是 193 而不是交接写的 194：多出的那条是 **`doc_map_fresh`**（交接文档 227 行时未重生成地图）。
⚠ 变异从 291 涨到 293 是本轮**新加的 2 条注入用例**（`CONVENTION.md` §3：改判据必须配变异）：
`同音色旋律没被跳过` · `音区分离豁免没接上代码`。

## 0. 结论表（每条都过了 `--only` 验收）

| # | 判据 | 实际做了什么 | 验收 |
|---|---|---|---|
| ① | `determinism_and_bytes` | 重跑 **10 首**（不只交接写的 1 首）`make_song.py <曲> --check`；`.mid` 与当前引擎逐字节一致 | PASS |
| ② | `density_dynamic_range` | `douzo_np/np2/tool` 写 `patterns.density_exempt.per_bar`（带实测数字）；`201_asa_learn` **改数据**（`Bridge.arr.density 1→0`）＋重渲染 | PASS |
| ③ | `console_encoding_safe` | `bp_primary.py` 末尾 `import cli_utf8 as _cu; _cu.setup()` | PASS |
| ④ | `host_docs_synced` | 跑 `docs/HOST-DOCS/sync_host_docs.py`（宿主 → 仓库，方向没反） | PASS |
| ⑤ | `docs_budget_and_skill_intact` | 压缩宿主 `~/.dsh/AGENTS.md` **3190 → 2453 tok**（限 2500）＋同步回仓库 | PASS |
| ⑥ | `notes_extra_within_sections` | 三首 `douzo_ymt3*` 末段 `Ending.bars 2→3` ＋ **`chords` 补 1 项** | PASS |
| ⑦ | `accompaniment_harmony` | 判据加"**同音色旋律不判音区分离**"；3 首写 `accomp_exempt.sep`（带逐曲读数） | PASS |

⚠ **①实际是 10 首**：判据遇到第一首不一致就断言退出，所以交接只看到 1 首。
全库扫描（口径与判据同：已交付 `.mid` vs 当前引擎 `compose()` 的输出）另有 9 首：
`asa_no_kaori_vel · bgm35_extract · bgm35_reextract · bgm35_v2 · dear_good_friends ·
douzo_bp · douzo_bp_ymtdrum · douzo_np · douzo_np2 · douzo_tool`（用户 2026-10-05 定"全部重跑"）。

---

## 纠正（**原交接说错的四处**，都影响下一步动作）

### 纠正 1（⑥）：那 4 个音**没有被丢弃**，"选 B = 删掉正在响的音"
原交接写"引擎 `build_events` 会**静默丢弃**它们"。实测**不成立**：丢弃只发生在
`notes_extra[轨]` 写成 `{'notes':…, 'target':[…]}` 时（target 分支只遍历段内小节），
而这三首是 **list 简写、没有 target** ⇒ 4 个音**已在 `build_events` 输出里、已在 `.mid` 里、
已经在响**。所以 ⑥ 报的是"小节号越界（数据契约）"，不是"内容会丢"。
⇒ **B 的前提是错的**（会删掉正在响的音），选 A 正确；但 **A 必须连 `chords` 一起改**：
只改 `bars` 会 `IndexError`（`song_engine` 逐小节取 `sec['chords'][i]`）。
原曲侧实测（对齐 +0.020s 后）：bar70 恰好 **4 个起音**（192.203 / 192.308 / 192.540 / 192.871s），
与转录最大误差 **9ms**；整小节 RMS −50.95dB、比 bar69 低 12.8dB —— 是"收尾渐弱里的最后一小节"，
**真有内容**。原曲总时长 193.2829s = **70.47 小节**（bar70 只存在 46.8%，bar71 无样本）。
**产物零变化**（独立证据）：改后重编 MIDI 与仓库现有 `.mid` **SHA256 逐字节相同**
（`douzo_ymt3` / `_ab` = `a0b4a33f…`，`_nodrum` = `adf2abbc…`）。

### 纠正 2（⑦）：真凶**不是** `douzo_ymt3*`
原交接说 ⑦ 影响 `douzo_ymt3*`（"它的 `Melody` 轨是空的"）。**那一系列恰恰不参与这条判据**：
`Melody` 是空的 ⇒ 判据的汇总里**一个音都没贡献**（实测 `douzo_ymt3*` 的 sep 样本 = 0）。
真正的拖累项是 **7 首"旋律与伴奏同音色"**的曲子，最硬的证据：
`bgm35_extract_solo` 的 Melody **160/160（100%）**都能在同拍伴奏 Piano 轨里找到**同音高**，
`dear_good_friends` **97/97（100%）** —— 也就是说"同拍伴奏最高音"量到的**常常就是旋律自己**
（40% / 41% / 76% 的窗口里最高音恰等于旋律音）。
⇒ 口径改成"**旋律与全部发声伴奏轨同 program ⇒ 跳过音区分离**"（本库 7 首）；
另给 3 首真偏低的写 `accomp_exempt.sep`。改后：**中位 +10 半音（门 +6）· 旋律在下 10%（门 15%）**。

### 纠正 3（②）：`201_asa_learn` 不是"还原曲服从原曲"，是**生成曲的编配缺陷**
它有 `theme`（tender）＋ `melody_gen`（seed 71461），**没有 `notes_extra`** ⇒ 生成曲，
`density_exempt` 的立论（"还原曲的密度服从原曲"）对它**不适用**（写了就是假理由）。
实测机制：`arr.density` 在它的配置下**完全失效**（1≡2≡3≡4、**822 音一字不差**）——
因为 `density≤1` 只置 `thin`，而 `piano_part` 里是 `thin and dense`、`dense=bool(arr.perc)`=False；
且 `new_song.py` 本该给每首挑一个 `density=0` 呼吸口，**201 是 0 个**（被"全段只留 piano"的手改抹掉）。
原曲侧两把尺子都不支持"1.57× 是服从原曲"：转录音符口径 `asa_no_kaori` **13.0×**、
独立音频 onset 尺子 **7.00× / 6.00×**。
⇒ 改数据：**只把 `Bridge` 的 `density 1→0`**（只少 50 音），逐小节起伏 **1.57× → 11.00×**。

### 纠正 4（⑤）：触发词**不是**可以缩的东西，缩的是"重复的操作规程"
压缩只做三件事，**触发词一个没删**（用探针核对 `·` 分隔词条：**丢失 0 个**）：
① 四条技能段**共用一个前言**（原来每段复述一遍"没有自动匹配…"）；
② 每条指针只留「技能体路径 + 一行铁律」，逐条铁律的列举下沉到技能 `SKILL.md`（那里本就更详细）；
③ 删"`dsh --version` 显示 0.1.6-alpha.2"这类**会过期**的例子。

---

## 本轮同时修好的（不在原 7 条内）

- **`doc_map_fresh`**：交接文档 227 行未提交且未重生成地图 ⇒ 跑 `scripts/doc_map.py`。基线里它是第 8 条红。
- **`song_json_canonical` 差点被踩**：改 `201_asa_learn` 时先用 `json.dumps(indent=1)` 写盘
  （12213 → **15021** B，每个音符拆一行）⇒ 当场发现并**从备份还原、改用 `json_io.save`** 重做。
  ⚠ **改任何 `song.json` 一律用 `json_io`**，别用 `json.dump`。

---

## 还没做 / 待用户定

1. **`douzo_ymt3` 的 Drums 轨现在是 0 音**（`notes_extra.Drums=[]`），它的 `.mid` 与
   `douzo_ymt3_nodrum_ab.mid` **逐字节相同** ⇒ "V1 带鼓"这版其实**没有鼓**，
   与它自己 `notes.md` 记的"1250 音进成品"不符（`song.json` mtime 晚于 `notes.md`）。
   **本轮没动它**（超出 7 条范围）—— 要不要恢复鼓，请用户定。
2. **`104_lounge_night` 的 `accomp_exempt.sep` 是本库唯一一条"有争议"的豁免**：
   实测逐曲 242 音、中位 −5、在下 62%，但**同刻高于旋律的伴奏音全部 ≥3 半音**
   （距旋律 ≤1 半音的 **0** 个）、半音冲突 **0%** ⇒ 判据分不开"音区交错"与"真盖住"。
   它是生成曲、已带 `space: true` 与 `avoid_lead: 2`，混音是用户认可的自动调参结果。
   **若用户听出旋律被压，应改按段抬旋律音区，而不是继续豁免。**
3. **`s01_r` / `s02_r` 是自述 `unusable` 的反面证据**（s01_r 的 Piano 逐轨精度 **4.8%**、
   转录撞解码上限）——它们的 `accomp_exempt.sep` 理由写的就是"读数不可信"。
   若以后清理这类反面证据曲，豁免可一并删。

---

## 复现命令

```bash
py="D:/software/skill/music-gen/.venv/Scripts/python.exe"; cd /d/software/skill/music-gen
"$py" scripts/selftest.py --only <判据名>      # 逐条验收（①~⑦ 的判据名见 §0 表）
"$py" scripts/selftest.py                       # 全量（**别与 mutation_check 并发**）
"$py" scripts/mutation_check.py                 # 变异
"$py" scripts/token_audit.py                    # 预算
```
