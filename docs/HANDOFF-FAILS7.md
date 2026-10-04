# 交接：修这 7 条 FAIL（**全是既有问题，非本轮引入**）

> 交接时间：2026-10-04 夜 · 发起人：用户（"交接，让下一个对话修七条 FAIL"）
> 当前基线：**全量自检 194/201** · **全量变异 291/291**（改完每条都要回到这两个数）
> 本文档**自足**：每条都给了【现象·实测读数·真因·修法·验收命令】。**先读这份，别重新排查。**
>
> ⚠ **关于"素材在哪"**：`songs/` 整个目录**已被 `.gitignore` 排除**（第 85 行 `songs/`），
> 所以本文档提到的 `songs/<曲>/*.json` 与 `D:\test\_tmp\...` 的中间产物**不在仓库里** ——
> 那些路径是**本机现场**。但**每条结论所需的读数都已抄进本文档**（包括两条原始密度曲线），
> 所以离开那台机器也能照着修；要复现素材就按 §附 的链重跑（mp3 源在
> `D:\game\なついろレシピ\...\mp3\`）。

## 0. 先看这条：本轮刚固化的东西（别踩回去）

| 新增/改动 | 作用 | 别动它的理由 |
|---|---|---|
| `scripts/audit_stems.py` | **逐轨精度审计＝提取任务第一道工序**（6 轨分轨当独立尺子） | 它带**两个闸门**：整层缺失档 · 准入 `fixable/unusable`（`--gate` 非零退出） |
| `scripts/strip_drums.py` / `shaker_layer.py` / `fill_pitch_layer.py` | 去鼓 · 补沙锤层 · 按分轨亮音补高音层 | 三者都**按曲内 `name` 兜底解析目录**（改名后写错地方这坑踩过） |
| `bp_primary.py --drum-source` | **默认 `ymt3`**（用户定：BP 的鼓搞不好就别用） | 守卫 `bp_primary_contracts` 会拦改默认值 |
| `transcribe_ymt3.py DECODE_CAP_WARN=200` | 解码撞上限 ⇒ **拒绝接续 song.json** | 新守卫钉着阈值区间 + 判据回放 |
| 文档 | `PITFALLS.md` **316–320** · `SKILL.md` §8 两个闸门 · README/CHEATSHEET 同步 | — |

**跑法**（串行，别并发 —— 并发时 `determinism_and_bytes` 会假 FAIL）：

```powershell
$py = "D:\software\skill\music-gen\.venv\Scripts\python.exe"; cd D:\software\skill\music-gen
& $py scripts\selftest.py          # 应回到 194/201（修完每条 +1）
& $py scripts\mutation_check.py    # 应保持 291/291
```

⚠ 本仓库**只带工具与文档**（`songs/` 被 gitignore）：clone 下来 `selftest.py` 的曲目类判据
会因"没有曲目"而跳过或换夹具。**在发起人那台机器上修**（本文档的读数都来自那台机）。

---

## ① `determinism_and_bytes`：`asa_no_kaori` 已交付 MIDI ≠ 当前引擎输出

**现象**：`songs\asa_no_kaori\asa_no_kaori.mid` 与当前引擎重跑不一致。

**实测读数（已量，别重复）**：
- 已交付 5128 B / md5 `026c4ca766d4f43e037094f0e92a222c`；当前引擎 5167 B / md5 `54d6d209d285ae4074a2be755c6b7be2`
- 逐事件 diff：**只在"当前"里多出** `track_name=Drums` + `control_change(ch9, cc7=80)` + `control_change(ch9, cc10=64)` + `note_on(ch9, note=56, vel=100)`
- 即：**当前引擎多写了一条 `Drums` 轨**（`song.json` 的 `notes_extra.Drums` 里有 1 个音）

**真因**：`song_engine` 在 2026-10-04 加了"第二把鼓椅" `Drums`（`DEFAULT_PROGRAMS`/`CH`），
而这首的 MIDI 是**加鼓椅之前**生成的 ⇒ 内容其实**变好了**（那 1 个转录鼓音以前被静默丢掉）。

**修法**（**别改判据**，这是真需要重跑）：
```powershell
& $py scripts\make_song.py asa_no_kaori --check      # 重出 MIDI + 重渲音频（约 30s）
```
⚠ 这首的 `render.json` 里 `norm` 若为旧口径，自动调参会重新收敛（输出会变响 ≤2.1dB，正常）。
⚠ 重渲会改 `_sf.ogg/_sf.wav` —— 如果这首已经在某处交付/被引用，交付副本要同步更新。

**验收**：`& $py scripts\selftest.py --only determinism_and_bytes` → PASS

---

## ② `density_dynamic_range`：`douzo_np` / `douzo_np2` / `douzo_tool` 密度太平

**现象**：三首都报"逐小节起伏 4.0 倍（min 9 / max 36 音每小节；逐段 2.80 倍）"，门是 **8 倍**。

**实测读数（已量）**：
- 三首 `song.json` **内容完全相同**：22 段 / 70 小节 / 1506 音
  （`Hook 1125 · Bass 191 · Glock 99 · Piano 91 · Drums 0`）—— 只是不同实验的副本。
- 逐段均值：`S01=11.2 · S02=20.5 · S03=24.0 · … · S08=31.0 · S10=31.5 · S21=14.8 · Ending=19.0`
  ⇒ 最静段 11.2、最密段 31.5，**缺"极静小节"**（没有 2~5 音/小节的段）。
- **原曲真实密度（关键证据）**：参考 `drums` 分轨逐小节高频起音 **min 7 / 中位 13 / max 19
  = 2.7 倍**（曲线见本文档 §附）⇒ **原曲本身就没有 8 倍起伏**。

**真因（两条都要查，别只挑一条）**：
1. **判据可能对还原曲不适用** —— 本项目的口径是"**判据服从原曲**"（用户 2026-09-25 定：
   "如果是真的没有音要保留，重要的是符合原曲"，见 `SKILL` §19）。原曲 2.7 倍、我们 4.0 倍
   ⇒ **我们比原曲还起伏大**，被判"太平"是判据的门（8 倍）取错了对象。
2. 但这三首**本身也确实有问题**：`Hook` 独占 1125/1506 = **75%** 的音，
   而 `Piano 91 / Glock 99 / Bass 191` 极稀 —— 这不是"密度"问题，是**归属/编配问题**
   （它们是 `douzo_bp*` 那条 BP 路径的产物，`PITFALLS`/`notes.md` 已记"归属塌成两条轨"）。

**修法（建议顺序）**：
- 先按 `melody_exempt` / `velocity_exempt` 的既有先例，给这三首写
  `patterns.density_exempt`，**理由必须带上面两条实测数字**（原曲 2.7 倍 · 我们 4.0 倍 ·
  参考侧 22 倍是生成曲口径）—— 若判据支持该键则走豁免；**判据是否认这个键要先确认**
  （`grep -n "density_exempt" scripts/selftest.py`）。
- 若判据不认，就去**改判据的适用范围**（还原曲 vs 生成曲分开），**不要改数字凑通过**。
- ⚠ 这三首是**实验版副本**，如果确认不再需要，另一个正当修法是**清理掉它们**
  （`studio_audit.py --clean` 只清链接；曲目目录要手动确认后再删）—— **删之前问用户**。

**验收**：`& $py scripts\selftest.py --only density_dynamic_range`

---

## ③ `console_encoding_safe`：`bp_primary.py` 没做编码兜底

**现象**：`scripts/bp_primary.py` 缺 `cli_utf8.setup()`。

**实测读数**：`tail -5 scripts/bp_primary.py` 显示文件**以 `sys.exit(main())` 结束，
全文没有 `_cu.setup()`**（对照合格写法：`audit_stems.py:44` / `shaker_layer.py:43` /
`strip_drums.py:50` 都是 `import cli_utf8 as _cu; _cu.setup()`）。

**真因**：它是最早那批工具之一，写的时候没加；GBK 控制台下打印 `✓` 会 `UnicodeEncodeError` 崩。

**修法**（一行，放在文件末尾 `if __name__ == '__main__':` **之前**）：
```python
import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印 ✓ 会崩）
```
（`cli_utf8.setup()` 幂等、失败静默，顺带统一 `--help` 出口。）

**验收**：`& $py scripts\selftest.py --only console_encoding_safe`
**变异**：这条判据有静态契约与"真进程"两条路径，改完跑一次全量变异确认没踩坏（应 291/291）。

---

## ④ `host_docs_synced`：宿主文档的仓库备份不同步（`AGENTS.md`）

**现象**：`docs/HOST-DOCS/AGENTS.md`（8762 B，10-02 22:18）落后于宿主
`C:\Users\z\.dsh\AGENTS.md`（11160 B，10-03 19:30）。

**真因**：宿主编过、备份没重跑。**方向只有一个**：宿主 → 仓库
（`sync_host_docs.py` 的 `SRC` 就是 `~/.dsh/...`，**别反向覆盖宿主**）。

**修法**：
```powershell
& $py docs\HOST-DOCS\sync_host_docs.py
```
（顺带会同步 `COT-PERSONA.md` / `SHELL-NOTES.md`；这三个文件顶部会被加一行"这是备份"横幅。）

**验收**：`& $py scripts\selftest.py --only host_docs_synced`

---

## ⑤ `docs_budget_and_skill_intact`：`AGENTS.md` 超预算

**现象**：`AGENTS.md（每个对话常驻） ≈3190 tok，超预算 2500`。

**实测读数**：宿主那份 **6015 字 / 144 行 ≈ 3190 tok**，预算 2500 ⇒ 要砍 **≈700 tok（约 780 字）**。

**真因**：常驻文档只增不减（它是**每个对话**的开销，比任何仓库文档都贵）。

**修法（顺序不能反）**：
1. **改宿主那份**（`C:\Users\z\.dsh\AGENTS.md`）—— 它才是生效副本；仓库那份改了不生效；
2. 砍的内容优先：把**已经落成代码/守卫**的条目压成一行指针（例如本轮的
   "音乐任务先加载 bgm-studio" 这类触发器可以缩成一行 + 指向 `SKILL.md`）；
   ⚠ **别删触发词**（技能靠 description 匹配，删了就等于技能不存在）；
3. 再跑 `sync_host_docs.py` 同步回仓库；
4. 若确实降不下来，抬 `scripts/token_audit.py` 的 **LIMITS['AGENTS.md（每个对话常驻）']**
   并**写清理由 + 压缩目标**（`CONVENTION.md` 文首那个框：抬完补压缩是欠账）。

**验收**：`& $py scripts\token_audit.py`（看 AGENTS 那行不再 `⚠ 超预算`）
+ `& $py scripts\selftest.py --only docs_budget_and_skill_intact`

---

## ⑥ `notes_extra_within_sections`：三种 `douzo_ymt3*` 有音落在末小节之外

**现象**：`douzo_ymt3` / `douzo_ymt3_nodrum` / `douzo_ymt3_nodrum_ab` 报
"轨 Hook 有音落在第 70 小节，而段落共 70 小节（至少丢 1 小节的内容）"。

**实测读数（已量，比判据报的更全）**：每首**越界 4 个音**（小节索引 70 = 第 71 小节）：
```
Hook  [bar 70, beat 0.42, pitch 62]
Piano [bar 70, beat 0.28, pitch 86] · [70, 0.77, 86] · [70, 1.25, 86]
```
**真因**：源转录把最后一小节标成了 70（0 基索引），而 `sections` 只覆盖 0–69
⇒ 引擎 `build_events` **静默丢弃**它们（判据抓的是"会丢内容"，不是"数据非法"）。

**修法（两条路，选一条并记录）**：
- **A（推荐，保内容）**：给 `sections` **末尾补 1 小节**（`Ending.bars: 2 → 3`）——
  但要确认原曲真有小节 71 的内容（用 `audit_stems` 看那 4 个音在分轨上有没有能量）；
- **B（丢弃）**：把这 4 个音从 `notes_extra` 里**删掉**（它们本来就播不出来），
  并在 `notes.md` 记一句"原转录越界 4 音已剔除"。
⚠ **别只改判据门**；⚠ 改完 **`douzo_ymt3_nodrum` 与 `_ab` 要一起改**（三首同病，`_ab` 是 A/B 对照版）。
⚠ 改 `song.json` 后**必须重渲染**（`make_song.py <曲> --check`），否则数据与产物不一致
（这条今天刚踩过：清鼓清了数据、产物没重渲 ⇒ 报告与耳朵对不上）。

**验收**：`& $py scripts\selftest.py --only notes_extra_within_sections`

---

## ⑦ `accompaniment_harmony`：旋律与伴奏音区分离不足

**现象**：`音区分离中位 +5 半音（门 +6；真实模板 +12）—— 旋律被伴奏盖住；旋律有 31% 的音
落在伴奏最高音之下（真实 1%）`。

**真因线索（本轮实测，别重复查）**：这条影响的是 `douzo_ymt3*` 系列 —— 它们的
`Patterns.melody` 是**空的**（`melody_exempt`：主奏只在 0/71 小节有音，"真的没有音要保留"），
而 `Hook`（吉他）占 862/1395 = 62% 的音、音域 40~86 **跨 4 个八度**。
⇒ "旋律 vs 伴奏"的分界在这首里**根本不存在**（没有独立的旋律轨）。

**修法（要先判定判据适不适用）**：
1. 先看这条判据在还原曲上是怎么取"旋律轨"的：
   `grep -n "def t_accompaniment_harmony" -A 40 scripts/selftest.py`；
   若它取的是 `Melody` 轨（空的）或"最高音轨"，那在**无旋律轨的还原曲**上就是**误判**；
2. 若是误判 ⇒ 照 `melody_exempt` / `lead_track` 的先例，让它对"**声明了
   `patterns.lead_track` 的还原曲**"换用主奏轨判定（`PITFALLS` 315 就是同类问题：
   判据覆盖面与名字不符）；
3. 若判据其实取得对 ⇒ 那是**真的编配问题**：`Hook` 音域过宽把主奏盖住了，
   修法是**分段**抬主奏/压伴奏（`sections[].arr.mix`），**不许整曲一刀切**（用户 2026-09-25 定）。

**验收**：`& $py scripts\selftest.py --only accompaniment_harmony`

---

## 附：本轮量到的两条原始曲线（省得再算）

**`douzo` 原曲高频起音 · 逐小节**（`drums` 分轨 4–10kHz，87.5BPM / 70 小节）：
```
[13,12,11,18,11,12,10,15,7,11,11,13,9,10,8,16,13,15,13,19,15,12,13,16,10,12,14,18,13,12,
 14,16,12,13,12,18,10,12,10,15,8,11,10,14,10,10,8,15,14,14,13,17,17,12,14,16,10,12,12,18,
 13,12,14,16,12,14,11,12,0,0]     → min 7 / 中位 13 / max 19
```

**`douzo_np`（三首同数据）我方逐小节音数**：
```
[13,10,13,9,21,20,26,22,25,17,22,17,23,20,28,16,14,12,17,21,25,34,23,36,29,26,36,27,29,34,
 19,26,11,10,12,11,20,21,25,24,25,19,24,15,25,21,26,17,16,14,16,19,25,33,23,33,32,26,36,30,
 24,36,20,23,13,9,12,12,21,17]     → min 9 / 中位 21 / max 36（4.0 倍）
```

## 收尾纪律（每条都适用）

1. **一条一提交**，改完立刻跑 `selftest.py --only <那一条>` + 收尾跑全量 `selftest.py` 与
   `mutation_check.py`（**别并发**）；
2. **改判据必须配变异用例**（`CONVENTION.md` §3：坏不了的检查等于没检查）；
3. **改数据必须重渲染**（数据与产物不一致 = 今天踩过两次的坑）；
4. **改文档要重生成地图**：`& $py scripts\doc_map.py`，并检查预算（`token_audit.py`）；
5. **不许为了让灯变绿而改数字** —— 要么改数据、要么改判据的**适用范围**并写清依据。
