# 接手：**生成曲"同质化"**（节奏骨架 + 开头引出方式都一样）—— 2026-10-07

> ⚠ **交接原因**（用户 2026-10-07 原话）：先说「主要是我想上传 GitHub」→ 听了生成曲后说
> 「我觉得**全部直接生成的开头都比较像**，我也说不出来」→ 又补「**节奏和开头引出方式都好像，
> 交接给下一个对话解决**」。所以这条线**没解决**，本文档把"量到哪、否掉了什么、下一步动哪一处"写全。
>
> 相关：`docs/HANDOFF-GEN-PLACEMENT.md`（**同一条线上的另一半**：落点偏低；那份 §4b 的消融数据
> 本文直接引用，别重复做）· `SKILL.md` §22（2026-10-01 修过的三处"听着都像"）·
> `PITFALLS` 304（同族纪律）。

## 0. 接手状态（**先读这一节**）

> ⚠ **2026-10-07 第二轮已动过手**：§9/§10 是这一轮的依据与实测，**§5 的"下一步"已被取代**
> （引子那件事做完了；`cap` 与主奏分配都已验证到顶）。**新的第一优先级 = §10.4 的"骨架可变"**。

- **仓库状态**：本轮改了 `scripts/new_song.py`（`CELL_CANDS_CAP` 2→4 + 引子池首）、
  `scripts/selftest.py`（`t_melody_prog_pool_order` 同步）、`scripts/mutation_check.py`（㉘ 用例）、
  `scripts/token_audit.py`（本文档预算）、`skill/bgm-studio/SKILL.md`（路由行）、`docs/DOC-MAP.md`（重生成）。
  17 首主题生成曲已按**各自原 theme+seed** 重生成（`_tmp/music-combo/regen.py`，可复跑）。
- ✅ **本轮做完的**：把"同质化"从"说不出来"落成**可量的读数**，并**否掉 3 个猜测 + 2 个改法**（§3）。
  其中"引子和弦雷同"那条**看着最像根因，实测被否掉**（§3.1）—— 这就是先量再改的价值。
- ✅ **本轮量到的核心数字**：3 首不同主题、同 seed 的**落点指纹两两距离只有 0.120~0.134**（§2.1）。
- ⚠ **下一件事**：§10.4 —— 让 `perc`/`pad` 服从主题包自己的 `arr_share`
  （6 个主题 perc=0.00、8 个主题 pad=0.00，现在被硬钉/预设全开 ⇒ 骨架只有 2 种）。

## 1–7. 第一轮的读数与口径（→ 已搬到 `HISTORY-GEN-SAMENESS.md`）

搬走的是：用户口径原话 · 现状读数（跨主题落点指纹 0.129、17 首横向对照、开头指纹形状、格 0 基线）· **已否掉的 3 个猜测 + 2 个改法** · C 组改动（降尾巴权重）与为什么还原 · 旧的"下一步" · 红线 · 素材路径表。
⚠ **要查"哪些路已经走过且被否"就去看那份**（`docs/HISTORY-GEN-SAMENESS.md`），
别在这里重抄一遍（`CONVENTION.md` §1：抄一份 = 埋一处漂移）。


## 9. 权威依据（"网络音乐理论"）—— 来源 + 它推着动了哪一处

用户 2026-10-07 的要求原话："**我希望他能在网上音乐理论中学习各种乐器的各种组合音增加差异**"。
依据纪律（`SKILL.md` 第 1 条）：文本只能是**网络权威数据（带来源 URL）**或 `refs/midi2/` 真实模板。

**① Rimsky-Korsakov《Principles of Orchestration》**（公有领域全文，Project Gutenberg #33900；
卷一第 1 章末"Comparison of resonance … combination of different tone qualities"）——
原文（译）："**不能否认，成对、成组地不断使用复合音色，会抹掉音色的个性、产生一种黯淡中性的
织体；而使用简单、本原的组合，则给色彩变化带来无限得多的余地。**"
→ **这是"同质化"这个词的理论表述**，也是本轮**不再朝"多加一层/多加固定搭配"走**的依据。
同一章还给了一条可执行的比例口径（供编配平衡参考）：`1 Trumpet = 1 Trombone = 1 Tuba = 2 Horns`；
`1 Horn = 2 Clarinets = 2 Oboes = 2 Flutes = 2 Bassoons`；`Violins I = 2 Flutes = 1 Oboe + 1 Clarinet`（forte）。

**② McAdams / Goodchild 等，音色融合的实证研究**（McGill MPCL；
[Goodchild 2018 Oxford Handbook 预印本](https://www.mcgill.ca/mpcl/files/mpcl/goodchild_2018_oxfordhdbktimbre_preprint.pdf) ·
[McAdams et al. 2025, *Factors Contributing to Instrumental Blends*](https://journals.sagepub.com/doi/10.1177/20592043251326391)）——
结论方向：**融合度取决于音区重叠、起音同步与音色族距离**，"木管×弦乐"是公认的互补对。
→ 本库的可执行形式就是既有的 `role_of_program`（同族才允许挑）+ `SLOW_ATTACK`（起音门）；
本轮**没有**据此新增规则（避免自创依据）。

**③ `refs/midi2/` 222 首真实模板的直接统计**（本次实测，脚本 `_tmp/music-combo/tpl_combo.py`）：
用到 **109 种音色**、130/189 种"角色族→GM"组合；逐风格音色种数 8~48。
⚠ 但同时量到一条**会否掉"照抄模板组合"这个做法**的事实：模板里**独奏钢琴曲占多数**
（`Piano=0` 一种组合就占 **35/189 首**）⇒ 直接按"模板组合"抽样会被独奏钢琴淹没，
**不能当主依据**（本条为负结果，见 §10 负结果 4）。

## 10. 本轮实测（2026-10-07）：根因、改动、读数、4 条负结果

### 10.1 根因：三处，全部有读数（不是猜）

用户点名的两件事——"**节奏和开头引出方式都好像**"——在音色/编配层对应三处：

| # | 位置 | 读数（17 首主题生成曲） |
|---|---|---|
| 1 | `new_song.melody_prog_pool` 的 **`head = [4]`**（恒定 4 电钢） | **第 1 段主奏 17/17 全是 GM4** |
| 2 | `new_song.theme_arr` 里 **`arr['bass']=True` / `arr['perc']=1` 硬钉** | **恒开角色骨架只有 2 种**（13 首 `bass+pad+piano`、4 首 `bass+piano`），**84/136 对一字不差** |
| 3 | `new_song.theme_programs` 的 **`cap = min(len(same), 2)`** | 每轨只剩 1~2 个候选：Bass 只用 3 种、Strings 6 种 |

### 10.2 改了两处（都在 `scripts/new_song.py`，**没有自创依据**）

**① `CELL_CANDS_CAP`：2 → 4**（新常量，在 `SECOND_TIER` 下面；消费方在 `theme_programs`）
离线标定（15 主题 × 20 seed = 300 次分配，`_tmp/music-combo/cap_sweep.py`）：

| cap | 组合种数 | Bass | Strings | Piano |
|---|---|---|---|---|
| 2（原） | 104 | 5 | 10 | 6 |
| 3 | 189（+82%） | 6 | 11 | 7 |
| **4（现）** | **204（+96%）** | **6** | **13** | **7** |
| 5 | 210（+3%，收益递减） | 7 | 13 | 7 |
| 无上限 | 206（噪声回吐） | 7 | 13 | 7 |

取 4 = 拐点。⚠ **但它不是主因**（见负结果 1）。

**② 引子音色：`head=[4]` → 池首 = `leads[0]` = `lead_assign()` 分给该主题的音色**
并且**池首不参与 seed 旋转**（只旋池身）。实测效果：第 1 段主奏
**17/17 全是 GM4 → 13 种、且 17 首两两不同**
（`100→72 · 102→70 · 104→65 · 106→71 · 107→69 · 112→81 · 113→75 · 115→68 …`；
只剩 4 对同色：`GM87`=folk_tale/seaside、`GM80`=neon/tender、`GM71`=classic/lounge、`GM68`=gorgeous/mystery），
跨 15 主题 **13 种**（= 二分图最大匹配上界，见负结果 2）。
守卫 `selftest.t_melody_prog_pool_order` **同步改**（新增"池首跨主题 ≥12 种"断言），
变异用例 `mutation_check` ㉘ 也改成注入"分配恒定"来打这条新断言。
注意：受`SLOW_ATTACK` 机制保护，弦乐/簧管/人声**仍进不了引子**（该断言保留）。
⚠ **代价（实测，要留意）**：`唯一组合指纹` **17/17 → 16/17** —— `111_velvet_hall` 与
`115_three_faces` 现在撞成同一指纹（两者都是 gorgeous，分配音色同为 68）。这是"引子差异"
与"整体指纹唯一性"之间的**此消彼长**，不是 bug；若要两边都要，得回到负结果 2 那条
（先加宽主奏池）。

### 10.2b A/B 素材与"只差了音色"的硬证据（4 首，已渲染）

`D:\test\_tmp\music-combo\AB\old\*_old.mid|ogg`（旧引擎）· `...\AB\new\*_new.mid|ogg`（新引擎）。
做法：临时把两处改动**注入还原**得到旧引擎（`_tmp/music-combo/ab_build4.sh` / `ab_new.sh`），
同一 `theme+seed` 生成 + `make_song --no-tune` 渲染，**取完产物再还原源码与 `song.json` 并核 SHA256**。

| 曲目 | 音符数 旧/新 | MIDI 里变了什么 | 开头质心 旧→新 | MFCC 距离 |
|---|---|---|---|---|
| 100_battle_dawn | 3488 / 3488 | ch0 73→75 · ch2 4→0 · ch5 50→48 | 1691 → 1814 Hz（+123） | 61.9 |
| 102_waltz_court | 1939 / 1939 | ch5 48→40 | 1495 → 1429 Hz（−66） | 22.3 |
| 106_classic_hall | 2905 / 2905 | ch0 71→68 | 1315 → 1462 Hz（+147） | 32.0 |
| 112_midnight_glass | 2458 / 2458 | ch0 开场 **4 → 81** | 1934 → **3317 Hz（+1383）** | 84.1 |

**读数含义**：`音符数四点全部相同` ⇒ 本轮改动**一个音都没动**（只换音色）；
`112` 那条正是"开头从电钢换成亮得多的音色"，质心 +1383Hz 是物理结果。
⚠ **这只是"换了没有"的客观读数，不是"更好听"的证据** —— 好听与否只能人耳判（红线）。

⚠ **量 A/B 时的踩坑（我的脚本 bug，记下来免得下次重踩）**：第一版 event-diff 用
`dict(channel→program)` 收集事件，把**同一 tick 上的两次 program_change 折叠成一次**，
于是 112 报"program 无变化"——**假的**。真相是 Melody 轨 tick 0 有**两次**：
先全局 `programs.Melody`、再段级 `melody_prog`，**后者才是实际听到的**。
量 MIDI 差异要**保留事件顺序**，别用"每通道取一个值"的口径。

### 10.3 4 条负结果（**别重复走**）

1. **`cap` 不是"都好像"的主因**：cap 2→4 后重生成 17 首，**恒开骨架 2 种、84/136 对一字不差
   —— 与改前完全一样**；单轨取值只松了一点（Bass 3→4、Piano 6→7、Strings 6→4）。
   ⚠ 本条**推翻了本轮的开工推断**（我原以为 cap 是共同上游）。
2. **主奏音色分配已到理论上界，别再放宽**：用匈牙利算法算最大二分匹配 —— cap=2 上界 11、
   **cap=3 上界 13**、cap=4 上界 13、**全池上界 13**；当前实现已经拿到 13。
   sea 与 tender 是"做不出独立音色"的两个（`seaside` 全池只有 `[73,87,80]`，
   `folk_tale` 的候选被 battle/daily 占了）。所以 §5 第 4 条"撞色"**在当前池宽下无解**。
3. **"段级主奏序列退化"试过两种改法，都失败并已回滚**：
   · 种子化**置换**（把 `ls[k:]+ls[:k]` 换成 seed-keyed 排序）→ classic 从 **2 种降到 1 种**；
   · 段级索引加 **seed 相位**但删掉环形取模 → gorgeous 的 `68 69 71 73` 循环退化成
     `68 73 69 71 73 69 71…`（池身只走一遍就重复），**是我的新 bug，当场修回环形**。
   根因：`i % len(body)` 本质是**环**，**上界 = 池身长度**（classic 分配走 71 后池身只剩 `[68]`
   ⇒ 序列必然是 `71 68 68 …`）。**这条维度的天花板由池宽决定，不是索引写法能突破的。**
4. **"照抄模板组合"这个做法被否**：见 §9 ③ —— 模板以独奏钢琴为主（35/189），
   按组合抽样会把所有主题拉向独奏钢琴，与"增加差异"的目标相反。

### 10.4 骨架那条的取证（改动**还没做**，留给下一步）

`_tmp/music-combo/skeleton_evidence.py` 量了 15 主题的 `prog_pool` 与 `arr_share`：

| 事实 | 读数 |
|---|---|
| 主题包的 `arr_off` 里含 `bass` 的 | **0 个**（一个都没有） |
| `arr_on` 含 `bass` 的 | 4 个（battle/cheerful/neon/retro） |
| `arr_maybe` 含 `bass` 的 | 9 个 |
| **`perc` 的 `arr_share` = 0.00 的主题** | **6 个**（classic/folk_tale/gorgeous/mystery/sorrow/waltz 等） |
| **`pad` 的 `arr_share` = 0.00 的主题** | **8 个** |

→ 即：**主题包自己的证据是"这 6 个主题根本没有 perc、这 8 个没有 pad"**，
但 `theme_arr` 里 `arr['perc'] = 1` 硬钉、`pad` 又由**风格预设**打开 ⇒ 骨架被抹平成
`bass+pad+piano`。**"骨架可变"的正解是让 `perc`/`pad` 服从 `arr_share`**（而不是关掉 bass：
`bass` 是 40–80Hz 的唯一来源，实测关掉会让低频塌到 −33dB，那条硬钉要保留）。
⚠ 动它要**逐首量 40–80Hz / 5–18kHz 两个频段**再定（低频靠 bass、高频靠 perc），
否则会重演"高频塌 26dB"那条老坑。

## 12. 研究与风格扩展（2026-10-07 第二轮）

用户：「**还有没有其它音乐论文，依照他们继续看看怎么让直接生成音乐更好听更有差异化而且不局限现在几个风格**」
→ 搜到一批，并把最有落点的一条**做了**（§12.3）；另一条**量出是负结果**（§12.2）。

### 12.1 文献（按"能落到哪一处"分组；⚠ 只读了摘要/片段，动手前要读实）

| 组 | 文献 | 落到本仓库哪一处 |
|---|---|---|
| **编配/结构** | [NeurIPS 2025《Unifying Symbolic Music Arrangement: Track-Aware Reconstruction and Structured Tokenization》](https://papernotes.org/NeurIPS2025/audio_speech/unifying_symbolic_music_arrangement_track-aware_reconstruction_and_structured/) · [Lead Sheet Generation and Arrangement by cGAN（arXiv 1807.11161）](https://ar5iv.labs.arxiv.org/html/1807.11161) · [XMusic（arXiv 2501.08809）](https://arxiv-org.ezproxy.obspm.fr/html/2501.08809v1) | 主题→引擎预设那一层（`theme_pack.THEMES[..]['engine']`） |
| **好听度/张力** | [《Building the Anticipation: How Variation in Tension Mediates Emotions in Music》(*Music Perception* 42(3):256)](https://online.ucspress.edu/mp/article-abstract/42/3/256/204039/Building-the-AnticipationHow-Variation-in-Tension) · [《Feature-Based Modelling of Perceived Emotion in Film Music》(CMMR 2025)](https://zenodo.org/records/17488748/files/CMMR2025_P1_2.pdf?download=1) | `new_song.energy_mix` 的段间张力曲线（**下一件事**） |
| **演奏表情** | [微时序/量化对 groove 感知（UVM 论文，偏差 30–40ms≈感知阈）](https://scholarworks.uvm.edu/cgi/viewcontent.cgi?params=/context/hcoltheses/article/1704/&path_info=The_Effects_of_Microtiming_Deviations_and_Quantization_on_the_Perception_of_Musical_Groove___Emilia_Winquist.pdf) · [swing 量化研究（*Communications Physics* 2022）](https://preview-www.nature.com/articles/s42005-022-00995-z.pdf) · [Harrison & Pearce 声部进行认知模型](https://cms.mus.cam.ac.uk/publications/harrison-pearce-voice-leadings/) | 引擎导出前的表情层（**本仓库这条判据完全空白，要先建尺子**） |

### 12.2 负结果 5：**"扩引擎预设"改不了骨架**（本轮最重要的一条否证）

量到：**5 个引擎预设的"角色集合"完全相同**（都是 `Arp/Bass/Glock/Hook/Melody/Pad/Perc/Piano/Strings` 9 个），
而 `new_song.theme_arr` **根本不读预设的 `programs`** —— 它只读主题包的
`arr_on/arr_off/arr_maybe/arr_share`（`FOUNDATION = ('bass','piano')` 还把这两个钉住）。
⇒ **扩预设不会新增角色、也不会改骨架**，它只影响"音色（`programs`）"与"节奏型（`patterns`）"。
所以 §10.4 说的"骨架可变"**要在 `theme_pack` 的 `arr_share` / `FOUNDATION` 上动，不在 `song_engine.STYLES`**。

### 12.3 做到了：**低音节奏型改成"用模板证据选"**（`bass_style` 形状匹配）

**根因**：`theme_pack.py` 的二分规则 `bass_style = 'sixteenth' if bass_dens >= 3.0 else 'simple'`（3/4 一律 waltz）
让引擎 6 种 `bass_style` 里 `eighth`/`offbeat`/`pump16` **一次都没被用过**（15 个主题只有 3 种取值）。
而模板层实测：**`#.#.#.#.#.#.#.#.`（每 8 分一个）= 34 首，是最常见的低音形状**。

**改法**（`theme_pack.bass_style_from_occ` + 外科式只更新 4 个主题的 `rhythm.bass_style`）：

| 主题 | 改前 | 改后 | 模板投票（只算正分） |
|---|---|---|---|
| battle | sixteenth | **eighth** | eighth 13 · sixteenth 8 · simple 1 |
| cheerful | sixteenth | **eighth** | eighth 5 · sixteenth 3 · simple 2 |
| daily | sixteenth | **eighth** | eighth 3 · sixteenth 2 · simple 2 |
| retro | sixteenth | **eighth** | eighth 12 · sixteenth 11 · simple 1 |

⚠ **只改有把握的 4 个**：加了"最小证据门槛"（领先者 ≥2 票且**严格多于**第二名），
其余 9 个主题（`classic`/`gorgeous`/`lounge`/`mystery`/`night`/`seaside`/`tender`/`sorrow`/`waltz`）
**证据不足 → 保留原值**（平票时 `most_common` 是"先遇到的"，不是判据 —— 实测 `lounge` 5:5、`night` 5:5、`tender` 2:2）。

**验收读数**：
- 15 个主题的 `bass_style` 取值 **3 种 → 4 种**（`eighth` 从 **0** 个变 **4** 个）
- 4 首重生成后，**`programs` 与"第 1 段主奏"13 维逐项未变**（只差 bass 这一维 —— 干净的消融）
- MIDI 层实测低音落点：4 首全部 `#...#...#...#...`（每拍）→ **`#.#.#.#.#.#.#.#.`（每 8 分）**，与 `eighth` 期望形状精确一致
- `selftest --fast` **213/215**（两个失败项 `section_transition`/`theme_melody_reuse` 在本轮之前就存在）· 变异 **310/311**

⚠ **还没验证**：改前/改后音频的 A/B（脚本第一次跑因 `trap ... EXIT` 用错备份而产出**假 after**，
已用 git 还原点修正并重生成）· **低频 40–80Hz 只在 1 首上量过（+0.15dB，未变差）** ·
听感完全没判（红线：听感赢）。

## 13. 没验证什么（诚实清单）
- **听感只做过一轮 A/B，用户未认可**（"都好像"）—— 所以本文档里所有"分化 +56%"之类的读数
  **都还只是 MIDI 层的结构性证据**，**不等于"听起来更不像了"**。
- **样本小**：跨主题距离只量了 **3 首**（battle/seaside/night，各 1 首）；2.3 的"开头指纹形状"
  是 17 首，但其中多数主题只有 1 首 ⇒ "逐主题"的结论强度受此限制。
- **格 0 那一维（§5 第 1 条）根本没动过** —— 只验证了"另加奖励项"是错的，没验证
  `1 - L1/2` 那条路可行。
- **"开头引出方式"没量过**：只量了引子的编曲开关组合与和弦，**没量各轨的进入顺序/时间**。
- 落点指纹用的是 **16 分格直方图 L1/2** 这个自造尺子 —— 它没有经过"已知答案"的自检
  （`HANDOFF-GEN-PLACEMENT` §10 第 4 条：新尺子先拿已知答案跑一遍）。**用它下方向性判断前先补这一步。**
- 本轮**没有渲染成音频**（只出 MIDI）⇒ 没听过混音之后的差别。

## 14. ② 张力/情绪曲线：现状复核与缺口（2026-10-07 第二轮）

### 14.1 复核结论：**响度曲线这一层是好的**（别重做）

用**仓库自己的尺子** `theme_pack.win_db`（8 小节整窗、浮点 dB，与 `THEME-PACK.md` 标定同口径）
量 10 首有曲线的成品（17 首里只有 10 首的目标本身不平坦）：

| 读数 | 值 |
|---|---|
| 形状相关（成品 8 小节窗 vs 目标 `energy_curve_db`） | **中位 0.65**（范围 −0.20~0.86） |
| 目标口径（`THEME-PACK.md` 记的标定） | 0.66~1.00 ⇒ **基本落在档内** |
| 成品起伏 vs 目标起伏 | 中位 **2.74dB vs 1.97dB**（略过冲，在 ±4dB 夹取内） |
| 偏低的两首 | `100_battle_dawn` **−0.20** · `106_classic_hall` 0.48（**留待复查，未处理**） |

⚠ **量这条曲线必须用 `win_db`**：我自己按 16 小节窗从头写的第一版把**静音前奏/尾音**算进 RMS，
报出"成品起伏 98dB"这种假数（窗口跨静音 ⇒ RMS≈0）。**先复用仓库的尺子，别再自造。**

### 14.2 缺口 1：**7 个主题根本没有能量曲线**，根因是**上游整数量化**

`daily/folk_tale/lounge/mystery/retro/seaside/tender` 的 `mix_target.energy_curve_db` 为空。
查到的根因链（**不是"目标真的平坦"就完事**）：

1. `metrics.structure`（`metrics.py` L320）对每个 8 小节块 **`round()` 成整数 dB**
2. `theme_pack` 聚合层再 `round(_med(...), 1)`
3. 写曲线的门槛是 `max(dev)-min(dev) >= 1.5` dB

而这 7 个主题的 `structure_db` 极差**全都恰好 1.00**、且值都是整数（`-18 -17 -17 …`）——
**起伏总量只有 1~6dB，整数化先把 0.4dB 以下的真实起伏吃掉**，再撞 1.5 门槛。
⇒ 与 `theme_pack.win_db` 注释里点名的那个教训**同族**（"1dB 的差异就是 20% 分辨率"），
只是**当时只修了"用量尺"，没修"落盘那份"**。

⚠ **本轮修不了**：参考音频是**项目私有素材**（`BGM15c.ogg` 等，不在仓库；画像注记明写
"该文件在画像里只记了文件名 —— 别人没有"）⇒ 无法用浮点重算 `structure`。
**这是既有上游限制**，要修得先让参考音频可获取（或接受量化）。

### 14.3 缺口 2：**"张力"这个维度根本没有**（`energy_curve_db` 是响度、不是张力）

依据 **Nikrang, Sears & Widmer《Automatic estimation of harmonic tension by distributed
representation of chords》**（[arXiv 1707.00972](https://ar5iv.labs.arxiv.org/html/1707.00972)）：
张力 = 当前和弦与**前 n 个和弦**的**加权余弦距离**（预期性越低 → 张力越高），
且该模型在实证听众数据上复现了既定排序：**major < minor < diminished < augmented**、
**三和弦 < 七和弦**、**PAC < HC < DC**。

**前提已验证**（本轮实测）：223 首模板里 **215 首**能取出 ≥8 个和弦（用 `midi_ref.analyze`），
共 **122 种**和弦符号。`?`（识别失败）是最高频符号（1359 次），做模型时要显式跳过。

**这两条现成的实证排序就是"判据自证"的锚点**（新尺子先拿已知答案跑一遍）——
下一步：实现张力模型 → 先过这 3 条已知答案 → 再谈拿它驱动 `arr.density` / `arr.mix`。

## 15. ② 张力接线（2026-10-07 第二轮，**已落地**）

### 15.1 改了哪一维：段级**和声色彩音**（`arr.harmony_add` 0/1/2）

- **引擎**（`song_engine`）：新增 `ARR_KEYS['harmony_add']` + 模块级 `harmony_extend()`
  （档 1 = 加七度 · 档 2 = 再加九度）+ 总开关 `HARMONY_ADD_ENABLED`。
  安全边界：只加根音之上的 **7/9 度**、夹在音集区域内、**不许与已有和弦音相邻半音**、
  **音级不许紧邻全曲和弦池的任一音级**（相邻小节常是不同和弦，否则同一小节会出半音摩擦）。
- **生成端**（`new_song.harmony_tension_levels`）：用**该曲自己的和弦进行**算
  `tension_model.variation_curve` 的逐段均值，按**全局固定阈值**切档
  （`HARMONY_TENSION_P33=0.158` / `P67=0.289`，取自 209 段实测分位）。

⚠ **固定阈值 vs 逐曲分位**：第一版用"每首自己的分位"，结果**每首都必然有 0/1/2 三档**
（差异被归一化掉）；换固定阈值后跨曲差异才出来。

### 15.2 实测读数

| 项 | 读数 |
|---|---|
| 17 首 215 段的档位分布 | 档 0 **106** · 档 1 **67** · 档 2 **42** |
| **几乎不加色彩**（全档 0） | `104_lounge_night` · `109_sunlit_desk` · `110_village_tale` |
| **大量加色彩**（档 2 ≥8 段） | `113_pixel_arcade` 8 · `103/111/115` 各 9 |
| 端到端（`100_battle_dawn` 全段档 2） | 给 4 条轨加出 **8 个新音高**（`Piano 67` · `Hook 54/58/63` · `Arp 66/70` · `Glock 78/87`） |
| **旋律轨** | **一个音都没动**（187 音，档 0 = 档 2） |
| 消融干净性 | 17 首的 `programs` 与第 1 段主奏**逐项未变**（只差 harmony 这一维） |
| 守卫 | `selftest` **216/218**（两个失败项本轮之前就有）· 变异 **314/315** |

### 15.3 三个踩坑（都留证据，别退回）

1. **扩展音的基准音找错两次**：① 用和弦字典的 `root` 参数 → 加出比整个和弦低一个八度的怪音
   （因为发声音高已被 `TR_SHIFT` 挪过）；② 用音集最高音 → 候选与已有音构成八度重复、被去重全拒。
   **正解**：以"根音 pc 在音集区域内的那个八度"为基准，再夹在音集区域内。
2. **`avoid_lead` 被我误判为摩擦来源**：加了"避开同轨其它音"的过滤后**摩擦不减** ⇒ 不是根因，
   **已撤回**（正解是上面的"避开全曲和弦池音级"）。
3. **`t_accompaniment_harmony` 报假 FAIL**（`105_seaside_walk` Hook 94% / Arp 92%）：
   它拿 `data['chords']` 的**原始音集**判"和弦贴合"，而引擎已按**扩展后**的音集发声
   ⇒ 合法扩展音被判成和弦外音。**修法**：把扩展函数提成模块级公开函数，让守卫**共用同一口径**
   （"同一口径只写一处"，同 `CONVENTION.md` §1）。

### 15.4 摩擦的度量口径（换了三次，第三次才对）

判"加色彩音有没有把和声弄脏"这件事，尺子迭代了三版：
① "新增音高是否与同轨其它音相邻半音" → 假阳性（`avoid_lead` 挪音混进差集）；
② "整轨音高集合的半音对数" → 太粗（**跨小节**的 57 与 58 被算成摩擦，而真实音乐里跨小节半音相邻正常）；
③ **正解 = 按小节分组，只数"同一小节内实际同响"的相邻半音对**。

## 16. ③ 微时序/表情：尺子已建（2026-10-07 第二轮）

### 16.1 先做尺子自证，结果**推翻了两条既有说法**

按纪律"新尺子先拿已知答案跑一遍"，用合成音频量 `groove_probe`：

| 合成条件 | 真 swing | 检出 | 误差 |
|---|---|---|---|
| 理想脉冲 | 0/10/20/40/80 ms | 同值 | **0.3~0.6 ms** |
| 软起音 + 混响 + 垫音 + 噪声 | 0/10/20/40 ms | 同值 | **0.4~2.6 ms** |

⚠ **两条都要更正**：
1. **"量化步长（半格 62.5ms）导致测不了 <15ms"是错的** —— 理想/现实合成上都只有毫秒级误差。
2. **"±15ms 噪声底"不是尺子的固有极限，而是"测哪条源"的问题** ——
   `HANDOFF-GEN-MICRO` §3 那次是在**真实分轨/混音**上量的（分轨残留 + 复音互扰）。
   ⇒ 精度必须按**源**声明，不能推广成"这把尺子只能到 15ms"。

### 16.2 新工具：`scripts/micro_timing_ruler.py`（**符号层**，零检测误差）

直接在 MIDI 音符时刻上量：逐轨格内偏移（中位/sd/格内为零比例）+ **swing**（奇偶格中位差）。
**已知答案自证**（守卫 `t_micro_timing_ruler_known_answers` 守着）：

| 判据 | 读数 |
|---|---|
| 真 swing 0/10/20/40ms → 检出 | 0.00 / **10.42** / **19.79** / **39.58**（误差 ≤0.42ms） |
| 纯抖动 ±15ms | swing **0.52ms**（不误报）· sd **8.55**（理论 8.7 = 15/√3） |
| 闭环：`patterns.swing` 0.04 / 0.08 拍 | 逐轨 swing **16.7 / 33.5 ms**（线性精确） |

### 16.3 用它量的结果（**先控制两个前提**）

| 前提 | 模板库 223 首 | 我们 39 首 |
|---|---|---|
| **多 tempo**（格距会变 ⇒ 不下结论） | **75** | 0 |
| **格不自洽**（偏差贴半格 ⇒ 不可信） | **28** | 20 |
| 可信样本里：纯量化 / **带微时序** | 57 / **62** | 18 / **1** |

⇒ 与 `HANDOFF-GEN-MICRO` 的定性结论一致，而且**现在有区分度**：
模板里 **41%** 带真 swing（44~61ms，集中在 **jazz / blues**），我们只有 **1/19**。
⚠ 但适用范围仍是"github 那两首"级别 —— 62 首带微时序说明**不能推广成"真实 BGM 都不 swing"**。

### 16.4 又一条死代码（**留证据，别再加回去**）

我一度在尺子里加了"把偏移折进 ±半格"，理由是"不折会让 swing 超半格时偏移变 0"。
实测（`_tmp/music-micro/fold_probe.py`）：**折叠与不折叠在 0~125ms 全部一致** ——
因为 `gi = round(t/grid)` 取的就是**最近**格，`t − gi·grid` **天然在 ±半格内**，折叠那几行永不生效。
真因是 `ms_per_tick` **漏 ×1000**（所有偏移缩小 1000 倍、swing 恒 0）。
⇒ **死代码已删**，对应的变异注入**已撤回**（它抓不到，因为不改变行为）。
⚠ 明示限制：**超过半格（120BPM/16 分格 = 62.5ms）的 swing 任何实现都测不出** ——
那个音离另一格更近，是**节拍歧义**，不是尺子缺陷。

### 16.5 还没做（③ 的后半）

- **表情层本身没实现**：`patterns.swing` 用的是**全轨同一个值**，真实演奏不会这样
  （鼓/贝斯/旋律各有自己的微时序）。要做得按轨分别给，且**依据模板**（62 首可信样本可量）。
- **没验证"加了更好听"**：听感一律未判（红线）。
