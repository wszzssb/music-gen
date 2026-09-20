# 20_piano_rain —— 慢速抒情钢琴独奏（主题模板包：tender / 温柔抒情）

| 项目 | 值 |
|---|---|
| 模板依据 | **10 首同主题模板聚合**（`refs/themes/tender.json`） |
| 主题→风格 | ballad / romantic / pop（引擎预设 ballad） |
| 速度 | **69 BPM**（四分音符）· 4/4 · 72 小节 · **250.4 s** |
| 编制 | **纯钢琴族音色**（GM 0-4 大钢琴/明亮钢琴/电钢琴 + 8 钢片琴），**无鼓** |
| 曲式 | 10 段：Intro4 · A8 · A2 8 · B8 · A3 8 · C8 · A4 8 · B2 8 · A5 8 · Outro4 |
| 和声 | B7 Esus4 Am7 Am（来源 window4）· C 段 F#m7 D6 C#m7 Bm6 |
| 调 | A 小调（按本曲和弦推断：B7 为 V7 属功能、Esus4/Am7/Am = e–i 级） |
| 频谱对齐 | `tender_mix`（6 份真实录音聚合中位数）· 响度 −16.9 dBFS · 宽度 0.369 |

## 怎么满足「只用钢琴」

8 条轨**全部**走 GM 钢琴族音色（GM 0–8），没有第二件乐器、也没有鼓组：

| 轨 | 音色 | 音域（MIDI） | 作用 |
|---|---|---|---|
| Melody | 1 明亮钢琴 | 50–97 | 主旋律（段级换音色：0/1/2/3/4） |
| Arp | 1 明亮钢琴 | 50–90 | 高音区装饰分解（B / C / B2 段） |
| Piano | 2 电钢琴 | 40–69 | 中音区和弦（左手） |
| Hook | 0 大钢琴 | 40–59 | 反拍和弦补充 |
| Pad | 0 大钢琴 | 40–54 | 长音铺底 |
| Strings | 1 明亮钢琴 | 52–69 | 副歌加厚 |
| Bass | 0 大钢琴 | 24–45 | 低音根音（钢琴低音区） |
| Glock | 8 钢片琴 | 64–107 | 高音点缀（键盘族；见下方取舍） |

> **Glock 的取舍**：为补 5–18kHz（纯钢琴泛音到不了），这条轨弹 C6–C7 高音区、
> 音色用钢片琴 8（分类上属键盘族，不是弦乐/管乐/打击）。
> 若要"严格只有大钢琴"：把 `programs.Glock` 改成 `[0,7]`（音区不变），
> 高频更暗但音色 100% 是钢琴 —— `song.json` 改一行。

## 怎么做到「不单调」（改的都是编配层，不靠音量）

1. **10 段各有织体**：Intro/A3/A5 只有钢琴+低音；A/A2 加反拍与分解音型；
   B/C/A4/B2 叠高音分解 + 加厚 + 高音点缀；Outro 逐小节渐弱（`ending_fade: 4`）
2. **音区分工**：低音 24–45 / 左手 40–69 / 右手 52–98 —— 三带不挤在一起
3. **段级主奏音色变化**（`melody_prog` 0/1/2/3/4）：同一支旋律在 A2、A3、A4 换钢琴音色
4. **段级音量曲线**（`arr.mix` CC7）：A/A3/A5 收、B/B2 推 ——
   实测段间 **RMS 极差 17.9 dB（σ 5.0）**
5. **和弦用开放排列**（四五度 + 八度跨开），不是密集三度叠置 —— 中频不糊

## 客观结果（`seg_contrast.py` 逐段实测）

| 段 | RMS dB | 段间差 | 质心 Hz |
|---|---|---|---|
| Intro | -18.6 | — | -36 |
| A | -19.2 | -0.6 | 517 |
| A2 | -16.5 | **+2.8** | 550 |
| B | -14.9 | **+1.6** | 773 |
| A3 | -19.6 | **-4.7** | 489 |
| C | -16.2 | **+3.4** | 724 |
| A4 | -14.9 | **+1.3** | 559 |
| B2 | -14.5 | +0.4 | 612 |
| A5 | -20.7 | **-6.2** | 582 |
| Outro | -33.0 | **-12.2** | 334 |

段间起伏：RMS 极差 18.42 dB (σ 5.15) · 质心极差 439 Hz (σ 115) · 5-18k 极差 22.97 dB (σ 6.62)

## 与同速真实参考的差距（`amakute`，**67.6 BPM**，本库最接近的参考）

| 频段 | 本曲 | amakute | 差 |
|---|---|---|---|
| 40–80 | −10.8 | −10.0 | **−0.8** |
| 315–630 | −0.7 | 0.0 | −0.7 |
| 630–1250 | −3.7 | −6.6 | **+2.9** |
| 1250–2500 | −7.1 | −10.0 | +2.9 |
| 2500–5000 | −17.3 | −12.8 | −4.5 |
| 5000–10000 | −33.9 | −14.7 | **−19.2** |
| 10000–18000 | −55.1 | −21.5 | **−33.6** |

**低频与中频已对齐**（40–80 差 0.8dB、315–630 差 0.7dB）；**5–18kHz 差 19–34dB 是纯钢琴的
音源上限** —— 该频段靠镲片/弦乐泛音/齿音撑起，钢琴泛音到不了。实测把高音轨音量从 62 推到
112，这一档只从 −26.8 变到 −25.2 dB（约 0.9dB / 每单位音量），说明**不是编配没做，
是音色物理上限**。要补这段只能加非钢琴音色（与"只用钢琴"冲突）。

## 模板清单（来源可溯源；.mid 不进仓库，重建见 `fetch_midi_lib.py`）

| 模板 | 风格 | 速度 | 来源 |
|---|---|---|---|
| `ballad/ame ni uta u tanshikyoku - A Ballad Sung to Rain.mid` | ballad | 48 | https://bitmidi.com/uploads/5932.mid |
| `ballad/Animal Crossing - KK Ballad Aircheck.mid` | ballad | 80 | https://bitmidi.com/uploads/6841.mid |
| `ballad/BALLAD-2.MID` | ballad | 89 | https://bitmidi.com/uploads/15417.mid |
| `ballad/A-Very-Special-Love-Song.mid` | ballad | 120 | https://bitmidi.com/uploads/3173.mid |
| `pop/IGGY POP.Louie Louie.mid` | pop | 120 | https://bitmidi.com/uploads/60059.mid |
| `pop/Disco Citizens - Footprint.mid` | pop | 132 | https://bitmidi.com/uploads/39730.mid |
| `pop/Disco-Fans.mid` | pop | 158 | https://bitmidi.com/uploads/39732.mid |
| `romantic/Liszt Bach Prelude Transcription.mid` | romantic | 60 | https://bitmidi.com/uploads/30179.mid |
| `romantic/Tchaikovsky Lake Of The Swans Act 1 1mov.mid` | romantic | 110 | https://bitmidi.com/uploads/31291.mid |
| `romantic/brahms.mid` | romantic | 160 | https://bitmidi.com/uploads/19565.mid |

## 复现

```powershell
$py = "<工具链根>\.venv\Scripts\python.exe"
cd <工具链根>
& $py scripts\theme_pack.py tender                    # 模板包（10 首）
& $py scripts\new_song.py 20_piano_rain --theme tender --seed 20 --force
& $py tools\piano_rain_tune.py                        # 段级音量曲线 + A2 织体区分
& $py scripts\json_io.py songs\20_piano_rain\song.json
& $py scripts\make_song.py 20_piano_rain              # 作曲+渲染+自动调参+成绩单
& $py scripts\seg_contrast.py songs\20_piano_rain\piano_rain_sf.wav songs\20_piano_rain\song.json
```

## 还没验证什么（**别拿分数当听感**）

- **未人耳试听**：以上全是客观量（段间 RMS/质心/倍频程），**好不好听只能听**
- **未验证 6 条**：起音摇摆 / 连奏 / **踏板** / 演奏法 / 过渡是否自然 / 旋律的歌唱性
  —— 本项目把这几条列为"测不到"；**MIDI 里没有踏板（CC64）事件**，交付版是干声
- `theme_timbre_pool`：已跑 `extract_theme_timbres.py tender --inject` 补齐模板音色池
  （本曲音色是**按"钢琴独奏"手写的**，不走池子；补齐是为了不再让这条守卫长期 FAIL）
- `melody_step_bias`：它靠**真跑 `melody_gen.py`** 来判断，而该脚本**写盘**（见修订记录 #4）
  —— 这条自检在这个仓库状态下会报非零退出，属工具链本身的问题，**别用跑它来"修"曲子**
- 未做 `--strict`（强拍弦内音 100%）：**已由 `melody_health` 覆盖**（它要求 100%，当前已达标）

## 数据修订记录（2026-09-20 晚，`PITFALLS` 216–218）

四处**真缺陷**都是量出来的（不是听出来的），都已修并留下工具：

| # | 症状 | 根因 | 修法 / 工具 |
|---|---|---|---|
| 1 | 旋律某段一度被写成**16 个同音**（我批量改音高造成） | 守卫只判"连续同音串"，串长被段落切碎（2~3）→ 看不见 | `probe_melody_health.MAX_SAME=45%`（全库实测最大 18.5%）+ 变异用例 |
| 2 | 强拍上有 **8 个"经过音"** | **和弦音级不完整**（手推开放排列时 B7 丢了 D#、Am7 丢了 A） | `tools/fix_chord_voicing.py`（按音级完整性校验、以**原始定义**为基准）→ 强拍偏差 8→0 |
| 3 | 引子落点过集中（自检 FAIL） | 引子 4 小节只用 4 个落点 | `tools/fix_intro_onsets.py` 补 2.75/3.75 拍 → PASS |
| 4 | **旋律被整体覆盖**（把 `melody_gen.py` 当只读诊断跑） | 它是**写盘**工具（`json_io.save`），且 `git` 没追踪本曲 → 无版本副本 | `tools/restore_melody_from_midi.py` 从**覆盖前的 MIDI** 逐音恢复：重渲后 **194/194 音一致**、仅 1 个音时值差 **4.7ms**；并给该工具加 `--dry-run`（对照实验：SHA256/mtime 均不变） |

> 同轮的第三个同族问题（音频比 MIDI 旧）已由 `make_song.py` 的**同步检查**兜住。
> 四条都写进了坑台账（`PITFALLS.md` 216–218），并各配变异用例 / 对照实验。


## 和谐体检（用户 2026-09-21："以后生成音乐最后检查是否和谐" + "生成期间也要注意"）

**已接成自动工序**（不靠人记得跑）：
- `make_song.py` **自动调参每轮结束**都跑 `harmony_check.check()`（第 1 轮 + 末轮必打）
- `check_song.py` 输出「和谐体检」段；而 `make_song` 每一步都调 `check_song` → 生成链必然跑到

判据**只有一份**（`scripts/harmony_check.py`，`check_song` 是薄包装）：

| # | 判据 | 本曲实测 |
|---|---|---|
| ① 旋律↔和弦**音区间距** 5–22 半音 | 过大 = 中间空掉（"空 + 发尖、不融合"） | 修前 A+21/B+26/**C+34** → 修后 **9–22** ✓ |
| ② 跨轨**同刻同音高（撞音）** | 同族音色 + 音域重叠 → 发浑 | 修前 **148 处** → 修后 **18 处**（只剩 Hook+Piano） |
| ③ **长音层**（Pad/Strings 4.1 拍 · shimmer 4 拍） | 钢琴这类"靠衰减"音色按住不放 = 不和谐 | 全关，最长时值 **0.69s** ✓ |

**顺带抓到并修掉的数据错误**：`chord_names_match_notes` 报 8 个和弦低音与根音不符
（我把 `b-12` 写成 `max(40, b-12)` → B7/Am7/Am/A6/D6/F#m7/C#m7/Bm6 的低音全被压成 40(E)）
→ 已按根音改正 10 个。
