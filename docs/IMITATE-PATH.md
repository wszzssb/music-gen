# 模仿写歌（照着一首参考曲写新曲）

> **先读这张表再动手** —— 三条路径的**依据不同**，选错就是白干（本轮实测吃过三类静默走样）。

| 路径 | 什么时候用 | **依据**（怎么写） | **蓝本**（像不像） | 入口 |
|---|---|---|---|---|
| **A 直接作曲** | "来一首 / 做一个 BGM" | 主题模板包（同主题 ≥8 首 MIDI） | 无 —— 只对齐**混音目标** | `new_song.py --theme` |
| **B 模仿写歌** | "照着这首做 / 仿照某曲" | **同样是主题模板包**（合规不变） | **单首参考曲**：BPM 层 / 调式 / 每 8 小节块曲线 / 段数 | `new_song.py --theme` → **`imitate_plan.py`** |
| **C 还原扒带** | "还原某曲 / 扒成 MIDI" | 参考曲的**音符层**（转录） | 参考曲本身（逐音照抄） | `docs/RESTORE-METHOD.md` 七步 |

**四条不许混的红线**：
1. **依据只能是模板包**（`refs/midi2/` 或网络权威数据，≥8 首）—— 拿"某一份音频"当模板依据
   `check_song` 会拦（`theme_basis_whitelist`）。
2. **只改 `--ref` 不等于模仿** —— `--ref` 是**混音目标**（对齐到哪份真实录音），不是结构依据。
3. **模仿 ≠ 还原**：模仿写**新旋律**（`melody_gen` 按主题旋律画像生成）；还原才抄音符
   （`transcribe_to_song.py` + `notes_extra`）。
4. **结构被改过就必须留痕**（`basis.structure_source = 'imitate:<参考曲>'`，由
   `imitate_plan.py` 自动写）—— 否则守卫 `t_imitate_path_marked` 报"段数与主题包 `form.plan`
   不符却没走模仿路径"。

## 1. 七步工序（实测走通过的那条）

| # | 做什么 | 工具 / 判据 |
|---|---|---|
| ⓿ | **先识别参考曲**：有哪些声部、各占多少 —— **别靠别人给的分轨、也别靠转录工具的通道名**（实测两次翻车：拿 YourMT3 通道名当"原曲有琶音层"，被用户当场否掉；拿 `probe_timbre --solo` 的三个高频段说"钢琴被埋"，而钢琴能量在 160–1250Hz） | **`identify_ref.py`**（见 §1b） |
| ① | 选参考曲，**先分组**：同曲常有多个版本，按 mid-low 差分判人声版/器乐版（人声版 `character != instrumental` 不能当混音目标） | `profile_ref.py` 出 `character` |
| ② | **钉死 BPM 层**：两层都成立时（60/75/80 ↔ 120/150/160）用"每 8 小节块的阶梯性"选，记进画像 | `check_audio.py` + `profile_ref.py --bpm N` |
| ③ | 量**每 8 小节块的起音数 + RMS** —— 这就是段表的来源（也是 `docs/RECIPE-BGM35.md` 的口径） | 见 §2 |
| ④ | 定调式与和弦池：画像 `quiet_chroma` + 参考曲的罗马级数频率 | 画像 JSON |
| ⑤ | 出骨架：`new_song.py <曲> --theme <主题> --ref <参考画像>`（**依据仍是主题包**） | `new_song.py` |
| ⑥ | 按结构表**重写段落层**：`imitate_plan.py <曲> --plan <结构表.json>` | 本工具（§3 有校验） |
| ⑦ | 旋律 → 校验 → 面板试听 → 渲染 → 体检 | `melody_gen.py` · `check_song.py` · 面板 · `make_song.py` · `audit.py` |

## 2. 段表 = "像不像"的骨架（密度曲线怎么落成参数）

参考曲的块曲线（起音数）→ 每段的 `arr.density`（引擎口径：**每小节音符数上限**）：

| density | 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| 每小节上限 | 1（只留骨架音） | 4 | 10 | 18 | 40 |

- **别把曲线抹平**：实测 BGM35 是 `6.5 → 28.8 → 36.5 … → 0.4`（3 个高潮 + 3 个呼吸口），
  40 倍级的起伏；**参考曲本身平的就照它平**（BGM23 的 9 块只有 1.5 倍起伏）—— 不造假变化。
- 其余三个手段配合：`arr.perc_target`（逐段鼓点目标，格/小节）· 轨开关（`bass/piano/uku/strings/pad/arp/glock/ep`）
  · `arr.melody_prog`（**逐段换主奏音色** = 参考曲"不同部分不同音色"那一层）。
- **起伏做在编配层，音量只是辅助**（`PITFALLS.md` 199）—— 段间 CC7 只做小修（±2~3dB）。

## 3. 段名纪律（**踩过两次的坑，工具已写成硬校验**）

`song_engine.role_of_section` 的规则是：**取段名里第一个 a–e 字母当角色**（intro/outro 特判）。

- ✅ `A` `A2` `A3` `B` `C12` `Intro` `Outro`（`Intro`/`Outro` 走特判）
- ❌ `Rise` `Peak` `Quiet` `Surge` `Wave` —— 它们**全落进角色 `E`**（第一个 a–e 字母是 `e`），
  于是被要求"同名段落共用一支旋律"，实测报
  `同名段落 E 用了 5 支不同旋律 ['Lone','Peak','Quiet','Rise','Surge']`。
- 同角色的段**必须**：同一条进行 + 同一支旋律（`music` 键 = 去尾数字的名字）。
  否则复用段落的强拍不合弦（`melody_gen` 会报"复用段冲突"，且渲染后强拍贴合率掉到 93~96%）。

`imitate_plan.py` 会在写盘前把 §3 的① ②、§1 的段数/和弦数/参数范围全部校验一遍，不过就退出。

## 4. 命令

```powershell
$py = "<工具链根>/.venv/Scripts/python.exe"; cd <工具链根>

# ② 参考曲画像（--bpm 必须显式钉死一层）
& $py scripts\profile_ref.py "<素材>/BGM35.ogg" BGM35 --bpm 150
# ⑤ 骨架（依据 = 主题模板包；--ref 只是混音目标）
& $py scripts\new_song.py 40_imitate_b35 --theme night --ref BGM35 --seed 40
# ⑥ 按结构表重写段落层（校验不过不写盘；--dry-run 只校验）
& $py scripts\imitate_plan.py 40_imitate_b35 --plan D:\test\plan40.json --dry-run
& $py scripts\imitate_plan.py 40_imitate_b35 --plan D:\test\plan40.json
# ⑦ 旋律 → 数据 → 渲染 → 体检
& $py scripts\melody_gen.py songs\40_imitate_b35\song.json refs\themes\night_melody.json --seed 40 --avoid songs
& $py scripts\check_song.py 40_imitate_b35
& $py scripts\make_song.py 40_imitate_b35
& $py scripts\audit.py "<素材>/BGM35.ogg" songs\40_imitate_b35\imitate_b35_sf.wav --bpm 150 --mine-json songs\40_imitate_b35\song.json --midi songs\40_imitate_b35\imitate_b35.mid
```

## 4b. "好听"的判据（**写之前定、交付前量** —— 2026-09-19 从还原链反推）

仿写的目标是"像参考曲的结构 + 好听"，而"好听"这一半是可量化的
（用户那轮的反馈原话：修参数前"太杂乱、不流畅"，修完"**都挺好听**，还是不像"）：

| 判据 | 目标 | 依据 |
|---|---|---|
| 旋律轨**时值中位** | ≥ **0.25s** | 认可版 v5：Piano 0.266s · Bass 0.232s · Guitar 0.345s |
| 旋律轨**碎音率**（≤0.25 拍占比） | **< 5%** | v5 是 2.7~3.9%；我修前 30.4% / 22.1% / **92.5%** = "杂乱" |
| 打击轨碎音率 | **~100%**（别拉长鼓） | v5 的 Perc 同样 0.010s / 100% |
| 段密度起伏 | 逐小节 ≥ **8 倍** | `density_dynamic_range` 的判据（自检在守）|

- **量**：`python scripts\note_dur_stats.py <曲.mid> [参考.mid] --floor 0.55`
- **为什么仿写也要管这个**：段表（§2）管的是"像不像骨架"，碎音/时值管的是"听不听得下去" ——
  两者独立。骨架再像，音符碎成一地照样是"杂乱"。
- ⚠ **时值下限 = 延长，不是合并**（起点不动）—— 与 `RESTORE-METHOD.md` §2
  那条"碎片合并 → F1 变差"不冲突，详见 `PITFALLS.md` 206。

## 5. 交付判据与已知边界

- **该看的**：`make_song` 成绩单（RMS/宽度/质心/倍频程）+ `audit.py` 偏差清单。
- ⚠ `audit.py` / `similarity.py` 的 `chord`/`grid` 轴是**还原口径** —— 模仿写新旋律、新进行，
  这两轴天然低（实测 40 号 chord 11.1），**别拿它当"不像"的依据**；频谱/演奏层才有意义。
- ⚠ **段数不等于小节数**：参考曲 207 小节不要硬套成 26 段 × 8 —— 先看曲线在哪跳（曲式的边界），
  再定段长；`bars_expected` 只做 ±2 的容差校验。
- **人耳 6 项工具测不到**（起音摇摆/连奏/踏板/演奏法/音色/好不好听）→ 交付时必须逐条说明
  （见 `docs/UNMEASURABLE-SOLUTIONS.md`）。
