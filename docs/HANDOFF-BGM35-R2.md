# 接手 BGM35 整曲还原（2026-10-02 交接 · 第二轮）
> **本文取代 `HANDOFF-BGM35-RESTORE.md`**（那份写于本轮开工前，里面的读数**已过期**：
> 它记的"重复组 137 / 逐层空洞 483 秒 / 一致率 84.9%" 与现状都对不上了；
> 尤其 **84.9% 那个数是同源自证的假数**，见本文 §6）。
> 自足：只读这一份就能接手。上一轮我按它做了四件事，结论与全部实测数字都在下面。
>
> ⚠ **2026-10-06 加注**：下文引用的 `D:\test\_tmp\...`（`lead-chain` / `reextract` / `lib-rescan`）
> 是**当时的临时工作目录，现已不存在**（实测本文 11 处路径全部失效）。**读数与判据照旧有效**
> —— 它们是定格的实测值；要复现就按 §4 的命令**重新建目录**（命令本身都在，不依赖那些中间件）。

## 0. 用户口径（别改）

- 本轮中途（2026-10-02）："**不要只针对单一音乐，主要是要推广到大部分音乐**"、"**看看之前的对话的问题有没有推广到全部音乐**"
  → 所以除了修 BGM35，还固化了**跨曲扫描**与**写盘护栏**（§5）。
- 更早：**"我觉得是识别的问题，以前和现在都还原不出来准确的"** · **"以后可以根据不同音乐切换，其它乐器也要自适应"** ·
  **"像不像 / 哪里不像"** · **"不要看之前信息"**（重新提取时干净重跑）
- 2026-09-25：**"如果是真的没有音要保留，重要的是符合原曲"** —— 判据服从原曲，**不许为过指标删/补真实音符**。
- 2026-10-02 听完当前版本：**"听一次感觉还行"**（用户已认可当前状态可交接；不是"完美"，是"可以往下走"）。

## 1. 一句话现状

`D:\software\skill\music-gen\songs\bgm35_reextract\` 是**当前主线**。
本轮修掉三类缺陷（Bass 整轨时间轴 · 同轨重复 · 力度平坦），补了 `other/guitar/piano` 三层的整层缺口，
音色按"原曲分层的物理能力"重挑，`Melody` 层**查清后保持不变**。
自检 **188/189**（唯一失败是既有的音乐性项，见 §7）。

## 2. 全部关键读数（同一把尺子，修前 → 修后，附旧版参照）

| 判据 | 工具 | 修前 | **修后** | 旧版参照 |
|---|---|---|---|---|
| Bass 末音落点 | `timeline_check.py` | 264.1 秒（全曲 331.9） | **329.6 秒** | 331.8 秒 |
| Bass 到真实 bass 起音中位 | 同上 | 112.0 ms | **59.4 ms** | 41.8 ms |
| Drums / Hook / Piano 落点 | 同上 | 38.0 / 44.9 / 63.0 ms | 36.4 / 44.9 / 63.0 ms | 33.4 / 51.3 / 55.9 |
| 同轨同音高 ≤60ms 重复组 | `preflight` ② | **137** | **55**（均有音频证据） | 13 |
| `preflight` ⑧ 逐层空洞 | `preflight` | 483 秒 | 395 秒（**口径问题，见 §6**） | — |
| 「完全没覆盖的秒」 | `gap_fill_stem.py` | 78 | **9** | — |
| 逐轨力度种类（**渲染后**） | `lib_defect_scan.py` | Drums 1 · Hook 2 · Strings 2 · Piano 2 | **Drums 93 · Hook 49 · Strings 49 · Piano 22** | 旧版**每轨 1** |
| 整曲 2–6k / 质心 | `gm_capability_gen` 同口径 | 3.52% / 528Hz | **5.23% / 638Hz** | 原曲 6.96% / 642Hz |
| 渲染总起音 | `preflight` ③ | 758 | 384（**音色瞬态改变所致，见 §6**） | 原曲 1428 |
| 我们鼓音数 | `preflight` ⑤ | 3626（**多于原曲 2201**） | **2773**（更接近） | — |

## 3. 本轮改了什么（逐项 + 回滚命令）

| # | 改动 | 命令/工具 | 备份（回滚就是把备份拷回 `song.json`） |
|---|---|---|---|
| 1 | 折叠 **72 份**无音频起音的重复份 | `bass_pair_collapse.py`（burst 判据） | `song.json.prepair.bak` |
| 2 | Bass beat 从 120 口径换算到 149.8 口径（×1.248333） | 一次性脚本（临时目录） | `song.json.pre_bassscale.bak` |
| 3 | 补 `Strings` 层（702→1904） | `gap_fill_stem.py --vel-scale 0.7 --place-under-melody` | `song.json.pre_gapfill.bak` |
| 4 | 补 `Hook` / `Piano` 两层（387→1882 · 2376→2656） | 同上（`--track Hook` / `--track Piano`） | `song.json.pre_gpfill.bak` |
| 5 | 音色：Hook 24→**31** · Strings 49→**41** · Pad 89→**91** | `pick_timbre.py --apply --only Hook,Pad,Strings` | ⚠ 见下 |
| 6 | 力度：先用 `measure_velocity.py` 量进分轨转录，**再**重建补音；鼓轨另跑 `extract_drum_grid.py` | 见 §4 命令 | **`song.json.pre_velfix.bak`** |

## 3b. 回滚地图（**2026-10-02 接手时逐份实测的 SHA256 + 内容**，照这张表用）

上一版这张表**有两处标错**，照它回滚会踩空 —— 下面是拿 6 份 `.bak` 逐份读盘核出来的：

| 备份 | mtime | 这一份**实际是什么状态** | 拿它回滚会得到 |
|---|---|---|---|
| `prepair.bak` | 21:38 | Bass **1553** · Hook 387 · Strings 702 · Piano 2376 · **鼓力度恒 100** | ✅ 回到"本轮开工前" |
| `prefix_bak` | 22:24 | Bass 1481（已折叠）· 其余同上 | ⚠ 交接单已标"方向搞反（beat 被缩成 0.64×）"，**只作留痕** |
| `pre_bassscale.bak` | 22:32 | 同 `prefix_bak`（Bass 时间轴**未**修） | ✅ 回滚 Bass 时间轴 |
| `pre_gapfill.bak` | 22:37 | 同上 | ✅ 回滚 Strings 补音 |
| `pre_gpfill.bak` | 22:58 | Strings **1904**（已补）· Hook 387 · Piano 2376 | ✅ 回滚 Hook/Piano 补音（**旧文档漏登记了这一份**） |
| `pre_velfix.bak` | 23:27 | Hook 1882 · Piano 2656 · programs 31/41/91 · **鼓力度恒 100** | ✅ **回滚"力度"改动的唯一正确点** |
| `pre_melody.bak` | 23:35 | **与 `song.json` 逐字节相同**（SHA256 都是 `86112d83…`） | ❌ **无效** —— 回滚等于没动 |
| `song.json` | 23:36 | 现状 | — |

- **`pre_melody.bak` 为什么等于现状**：它是在 Melody 301 音实验**之前**存的，而那次实验
  随后被回滚（见 `songs/bgm35_reextract/notes.md` §5.3）⇒ 文件先改后复原，落回同一份内容。
  **这不是损坏，但它不是回滚点。**
- ⚠ **备份里有**一个坑件**：`song.json.prefix_bak` 是我一次**方向搞反**的中间态（beat 被缩成 0.64×），
  **别用它回滚**，只作留痕。

## 4. 复现命令（绝对路径）

```bash
$py = D:\software\skill\music-gen\.venv\Scripts\python.exe
$ml = D:\software\skill\music-gen\.venv-ml\Scripts\python.exe
$R  = D:\software\skill\music-gen
$S  = D:\test\_tmp\reextract\stems\htdemucs_6s\BGM35          # demucs 六轨
$T  = D:\test\_tmp\reextract\ymt3_allstems                    # 各层转录 MIDI
$V  = D:\test\_tmp\lead-chain\vel_stems                       # 量过力度的转录（本轮产物）
$V2 = D:\test\_tmp\lead-chain\vel_stems                       # 同上

# ① 落点体检（抓"整轨错位"）
& $ml $R\scripts\timeline_check.py $R\songs\bgm35_reextract\bgm35_reextract.mid --stems $S

# ② 力度：量进转录（口径实测为 120，别改成别的）
& $ml $R\scripts\measure_velocity.py $S\other.wav  $T\other.mid  $V\other_vel.mid
& $ml $R\scripts\measure_velocity.py $S\guitar.wav $T\guitar.mid $V\guitar_vel.mid
& $ml $R\scripts\measure_velocity.py $S\piano.wav  $T\piano.mid  $V\piano_vel.mid
& $ml $R\scripts\measure_velocity.py $S\drums.wav  $T\drums.mid  $V\drums_vel.mid
& $ml $R\scripts\measure_velocity.py $S\bass.wav   $T\bass.mid   $V\bass_vel.mid   # ⚠ 这一条会被"对齐抽检"拒（见 §6-5），属已知

# ③ 重建补音（顺序：先量力度、再补）
& $py $R\scripts\gap_fill_stem.py bgm35_reextract --stem-midi $V\other_vel.mid  --track Strings --vel-scale 0.7 --place-under-melody --apply
& $py $R\scripts\gap_fill_stem.py bgm35_reextract --stem-midi $V\guitar_vel.mid --track Hook    --vel-scale 0.7 --place-under-melody --apply
& $py $R\scripts\gap_fill_stem.py bgm35_reextract --stem-midi $V\piano_vel.mid  --track Piano   --vel-scale 0.7 --place-under-melody --apply

# ④ 鼓轨力度（它的力度在 patterns.drum_grid，不在 notes_extra）
& $py $R\scripts\extract_drum_grid.py $V\drums_vel.mid --bpm 149.8 --song $R\songs\bgm35_reextract\song.json --song-bars 207

# ⑤ 音色
& $ml $R\scripts\pick_timbre.py --stems $S --stems-midi $T --song bgm35_reextract \
      --apply --only Hook,Pad,Strings --min-gap 0 --max-cap-dist 3.0

# ⑥ 重渲染（**改过 song.json 必须重渲染**）
& $py $R\scripts\make_song.py bgm35_reextract --no-tune

# ⑦ 交付前体检
& $ml D:\test\pop_transcribe_audit_交付\tools\preflight.py $R\songs\bgm35_reextract\bgm35_reextract.mid \
      --ref D:\test\_tmp\reextract\BGM35.wav --stems $S \
      --skeleton D:\test\_tmp\reextract\ymt3_song\bgm35.mid \
      --mine-wav $R\songs\bgm35_reextract\bgm35_reextract_sf.wav --out D:\test\_tmp\reextract\pf_last
```

## 5. 已固化的通用能力（**这部分才是"能推广到大部分音乐"的**）

| 工具 | 解决什么 | 自检 |
|---|---|---|
| `beat_units.py` | **拍值单位换算**：转录的 beat 相对转录自己的 bpm、`song.json` 的 beat 相对曲子 bpm，两者不等时写盘会**整轨静默缩放** | 13 项 |
| `timeline_check.py` | **整轨落点体检**（事件级，抓整轨错位；缩放 1.25× 必被抓出） | 合成夹具 |
| `lib_timeline_audit.py` | **跨曲扫时间轴/时长**（4 条判据 + 已知误报表） | 判据纯函数自检 |
| `lib_defect_scan.py` | **把 BGM35 上踩到的每类缺陷逐类扫全库**（T1/T2/T3/D1/D2/D3/D4/D5） | 比值指纹自检 |
| `measure_velocity.py` | 从分轨**逐音量力度**（新增**排位映射** + **对齐抽检护栏**） | 合成强弱音 |
| `gap_fill_stem.py` | **按秒**补"整层没响"的缺口（去重/夹音域/压在旋律下方） | 7 项 |
| `pick_timbre.py` + `gm_capability_gen.py` | **音色自适应**（族内排序 + 依据表 + `--apply`） | 方向 + 族限制 |
| `who_has_melody.py` | **旋律该从哪条分轨抽**（单音性 + 分轨 RMS） | 合成单音/和弦/静音 |
| `bass_pair_audio.py` + `bass_pair_collapse.py` | 同音高近距离重复对的**音频判据**与折叠 | 4 个已知答案 |

**全库扫描的结论（这条最省后代力气）**：32 首里**真时间轴缺陷只有 BGM35 一首**；
另外 10 首的"时长差 2–3%"是渲染混响尾巴、`dear_good_friends` 的比值差是"各轨自然收尾不同" ——
**两类都是已知误报**，别再当缺陷查。

## 6. 本轮踩到并已写进代码注释的坑（**别重走**）

1. **"整轨错位"会伪装成"精度不够"**：帧级一致率把 Bass 的 0.801× 读成"10.4%，先修识别"。
   → **先量落点（`timeline_check`），再谈精度。**
2. **包络互相关不能判时间轴**：正控（参考×1.03）只有 0.08 相关，对比度不足，结论"k=1.00 没问题"是假的。
3. **"峰值扫描"判拍口径**对**密集层无分辨力**（piano 在 120/149.8 都给 −16.8dB）。
   可靠做法：拿分轨转录去对**曲内对应轨**的落点（drums 9ms / guitar **0ms** / piano 189ms / other 1175ms ⇒ **一律 120 口径**）。
   我曾据此错误地判"bass 要用 149.8"——**那是"它现在在曲子里就是错的"，不是它的口径**。
4. **`preflight` ⑧ 的"映射"会全落到同一条轨**：Strings 音最多 ⇒ 六条分轨全映射到它 ⇒
   "395 秒空洞"其实是"Strings 没有别的层的起音密度"，**不是内容缺口**。真实缺口看"完全没覆盖的秒"。
5. **`measure_velocity` 的对齐护栏会拒 `bass`**（抽检 −61dB）—— 现象记对了，**归因写错了**：
   原文说"因为它现在的曲内位置就是错的"，可本工具量的是**转录 vs 这条分轨音频**，
   **与曲内位置无关**。真因 = 转录里混着别的乐器轨（**全文护栏被泄漏轨拖垮**）→ **§11.3**。
   → 已改成**逐轨护栏**，`bass` 现在量得到（−12.0dB / 829 音），`vocals` 仍正确判空。
6. **判"力度平坦"必须用渲染后的 MIDI**：`notes_extra['Drums']` 是**没被渲染用**的那份（力度恒 100），
   真正发声的是 `patterns.drum_grid.per_bar`（93 种）。读错源会给出"看着很具体"的假数字。
7. **只写顶层 `melody` 字典不生效**：引擎的旋律是**段落级引用**（`sections[].melody` 指向字典键）。
8. **`make_song --no-tune` 曾经每轮 rc=1**（`finalize` 拿不到 `out` 拼出 `.wav`），**而音频其实是新的**——
   已修（该分支现在补 `mid/out/composer` 且写 LF）。**这是最误导的一种状态**。

## 7. 没做 / 待用户决定

1. **`Melody` 层三选一**（当前保持 ①）：
   ① 保持 151 音（现状）· ② 清空（`--no-melody` 的语义，更自洽）· ③ 用从 `Strings` 抽的 301 音版
   （音区对、但与 Strings 重复，会把"旋律↔伴奏音区分离"再往下压）。
   依据：`vocals` 分轨 **RMS −81.1dB（全空）**、六条分轨**全是复音** ⇒ **这曲子没有独奏旋律层**；
   现状 151 音只覆盖 11% 曲长。
2. **把 `measure_velocity` 接进 `transcribe_ymt3` 的还原链**（现在要手跑"先量力度再补音"）。
3. **`mutation_check` 注入用例**没给新检查配（`selftest` 已加 3 个新项）。
4. **`accompaniment_harmony` 仍不达标**（音区分离中位 −4.0 半音、门 +6）：`Melody` 151 音只覆盖 102 拍，
   其余拍的"伴奏最高音"落在没有旋律的段落上 ⇒ 这是**编配层**问题，补弦乐改不了。
5. **`preflight` ①轨结构 FAIL** 是**引擎编配版的既有性质**（引擎按角色自己分配音色，骨架里当然没有那些 `(channel, program)`），不是缺陷。
6. 旧版 `songs_direct\bgm35_extract\` 与 `bgm35_reextract` 的取舍：现在主线是 reextract；旧版**没修力度**（每轨 1 种）。

## 8. 没验证什么（**不许拿分数冒充**）

- **没有真值**：原曲是商业 BGM，全部读数都是无真值判据；`preflight` ②③④⑥⑦⑧ 属 WARN 级，要配人耳 A/B。
- **8 条"测不到"的项**（起音摇摆 / 连奏 / 踏板 / 演奏法 / 音色 / 好听度 / 过渡 / 整体）一条都没验。
- **A/B 片段位置**：`D:\test\_tmp\lead-chain\ab_timbre\`（换音色前后各 13 秒 ogg）·
  `ab_bassfix\`（Bass 修复段）· `ab_melody\`（Melody 变体，⚠ 那次渲染路径有 bug，只有 A 侧可用）。
- **全库的其它还原曲**（`siren_end2`、`dear_good_friends`）**没有分轨素材在库里**，
  所以"给它们补力度"这条推广**还没验证**。

## 9. 素材与产物（绝对路径）

| 类别 | 路径 |
|---|---|
| **当前主线曲目** | `D:\software\skill\music-gen\songs\bgm35_reextract\`（song.json / .mid / `_sf.ogg` / `_sf.wav` / **notes.md**） |
| 本轮读数与坑的原始记录 | `D:\software\skill\music-gen\songs\bgm35_reextract\notes.md`（§0–§6，含逐轮结论） |
| 参考曲 | `D:\test\galgame\ピュアソングガーデン！解包\Bgm\BGM35.ogg`（331.9 秒 · 4/4 · 实测 ≈149.8 BPM） |
| demucs 六轨 / 各层转录 / 优化 Bass | `D:\test\_tmp\reextract\`（`stems\` · `ymt3_allstems\` · `bass_fix_thr0.45_oct.mid`） |
| 量过力度的转录（本轮产物） | `D:\test\_tmp\lead-chain\vel_stems\*.mid` |
| 本轮所有临时脚本与日志 | `D:\test\_tmp\lead-chain\`（⚠ 目录已清理；内含 FINDING-bass-timeline.md、各 `*.log`、`scan_*.json`） |
| 旧版（用户认可过的那一支） | `D:\software\skill\music-gen\songs_direct\bgm35_extract\` |
| 交付目录（旧版交付物） | `D:\software\skill\music-gen\deliveries\bgm35_extract\` |
| 上一轮交接（读数已过期） | `docs\HANDOFF-BGM35-RESTORE.md` |
| 方法论 | `docs\RESTORE-METHOD.md` §10 防错清单 · `docs\TRANSCRIBE-AUDIT.md` |

## 10. 开工前必做（照旧）

1. `scripts\selftest.py --fast`（2026-10-02 接手时复跑：**188/189**，唯一失败是 `accompaniment_harmony`，属既有）。
2. 面板 `http://127.0.0.1:8765`（本轮它在跑；改一版先面板试听本段，定稿才整曲渲染）。
3. 改 `song.json` 前**核基准**（记 md5 / 已有一串 `.bak`），改完**读回**，改完**必须重渲染**。
4. 判"像不像"**只看音符层**（音高/密度/织体/起音/力度）；频谱指标是混音工具，不是还原目标。

## 11. 【2026-10-02 接手第三轮】做了什么 · 还没做什么

**结论先行：`song.json` 一个字节都没动**（SHA256 仍是 `86112d83…`）—— 这一轮全在**工具与文档**上。

### 11.1 已落地

| 事 | 落到哪 | 独立证据 |
|---|---|---|
| **§7-2 完成**：力度量取接进还原链 | `transcribe_to_song.py` 新增 `--stems-dir` / `--stem-audio` / `--no-measure-velocity`；量力度排在 `read_notes` **之前** | 端到端重跑：`Strings`/`Drums` 与手跑 `vel_stems` **一个音都不差**；`patterns.velocity_source` 已写盘 |
| **§7-3 完成**：给新检查配注入用例 | `mutation_check.py` 新增 5 条 | 见 §11.3 |
| **`measure_velocity` 护栏改逐轨** | `do_one` 的 `per_track_guard`（旧行为用 `--no-per-track-guard` 复现） | `bass` 由"整份被拒"变成量到 **829 音**；`vocals` **仍然拒写盘**（负控）；`piano` 少掉 **427** 个噪声力度 |
| **扫描器的门修对** | `lib_defect_scan.py`（D3 门 + `realpath` 去重 + 死常量 `VEL_COVER_MIN`）、`lib_timeline_audit.py`（同样去重） | 全库 **32 条 → 16 条**；D3 命中 **5 首 → 8 首** |
| **两个扫描工具接进主自检** | 新检查 `lib_scanners_ruler` | 它们此前的 `--selftest` **从没被主自检引用过**（`grep` 零命中） |

### 11.2 全库复扫的结论（回答"缺陷有没有扩散到别的曲子"）

**曲库其实只有 16 首不是 32 首**：`songs` 是**指向 `songs_direct` 的符号链接**（实测 `ls -ld`
确认、逐曲 inode 相同）⇒ 两个入口扫同一个库，命中数会**虚高一倍**。已修（realpath 去重）。

| 缺陷类 | 修前工具报 | **修后** | 结论 |
|---|---|---|---|
| 整轨时间轴缩放 | 7 首 | 7 首（**复核后 0 首成立**） | ✅ **"真缺陷只有 BGM35"成立** —— 7 首全是"各轨自然收尾"（已知误报） |
| 逐轨力度平坦 | 5 首（**漏报 3 首**） | **8 首** | ❌ **扩散了**：新增 `100_battle_dawn`（Hook 8/550）· `103_sorrow_letter`（Hook 15/1346）· `104_lounge_night`（Hook 12/917） |
| 同轨同音高重复 | 7 首 | 7 首 | ❌ **最严重不是 BGM35**：`siren_end2` **142 组** > `bgm35_reextract` **72 组**（8 首生成曲 0 组） |

⚠ 上表"复核后 0 首成立"来自接手时的全库复核（逐条查了漂移与音频长度），细节与反证在
`D:\test\_tmp\lib-rescan\REPORT.md`；**力度平坦那一列是这次修好 D3 门之后重扫出来的**。

### 11.3 新发现：`ymt3` 的"逐分轨"输出**不是单乐器**（影响所有还原曲）

`ymt3_allstems/bass.mid` 里 **7 条轨**，只有 `Bass`（829 音 · −12.0dB）与 `bass.wav` 对得上，
另外 **956 个音**（Piano/Perc/Organ/Guitar/Strings/Drums）落在 −63~−70dB。
`piano.mid` / `guitar.mid` / `other.mid` 同样混着别的乐器轨。后果两条：

1. **量力度时**：全文护栏被泄漏音拖垮（已修，改逐轨）；不修时 `piano.mid` 的 427 个泄漏音
   还会拿到一串"看着正常"的噪声力度。
2. ⚠ **`read_notes()` 会把文件里所有轨拍平进同一条引擎轨** ⇒ 泄漏音**会真的进曲子**
   （`Bass=…/bass.mid` 就会把 366 个鼓点、350 个钢琴音一起塞进引擎的 Bass 层）。
   **本轮没动它**（改它会改内容，属用户口径范围）；要处理请先问用户。

### 11.4 还没验证什么

- **`measure_velocity` 的音频判据在别的曲子上一次没跑**：`siren_end2` 的 142 组重复、
  `dear_good_friends` 的 4 组，都因为**盘上找不到对应的 demucs 分轨**而**判不了**
  （既不能当缺陷，也不能当通过）。
- **`bass_vel.mid` 那份手跑产物不是同一把尺子**：它的力度是 `1~65`（旧 `linear` 锚点口径），
  而现在默认是 `rank`（`28~112`）⇒ 端到端比对里 Bass 那一列**不可比**，别拿它当回归基线。
- 听感全未验；`timeline_audit` 的判据④仍然空转（ref 解析不到参考音频）。
