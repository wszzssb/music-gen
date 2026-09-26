# song.json 格式与风格预设（写歌时才需要读）

> **这份只管"数据格式"**，不区分路径 —— 三条路最后都落在同一个 `song.json`：
> **直接作曲**（`docs/THEME-PACK.md`）· **仿写**（`docs/IMITATE-PATH.md`）·
> **提取 MIDI / 还原**（`docs/RESTORE-METHOD.md`）。


> 从 README 拆出来（只有"写/改歌"才用得上）。校验一律 `scripts\check_song.py`；
> 想写更短的 spec（和弦走向+旋律骨架）用 `scripts\build_song.py`，见 `CHEATSHEET.md`。

## `song.json` 结构（新歌要写的全部内容）

```jsonc
{
 "name": "morning", "bpm": 150.0, "desc": "风格说明",
 "chords":  { "D": [38, [57,62,66,69,74]], ... },   // 贝斯音 midi + 和弦音（低→高）
 "melody":  { "a": [[小节,拍,时值,音高], ...], "b": [...], "intro": [...], "outro": [...] },
 "sections":[ {"name":"A","bars":8,"chords":["D","A/C#","Bm7","G"],"melody":"a",
               "melody_extra":[[1,3.5,0.5,76]],       // 可选：变奏加音
               "arr":{"uku":true,"piano":true,"strings":false,"glock":true,
                      "perc":1,"bass":true,"pad":true,"vel":0.96,
                      "mix":{"Strings":80}}}],        // 可选：段落级 CC7（**单整数**）
 "patterns":{"bass_style":"offbeat","perc_style":"light","arpeggio":[0,2,3,4,3,2,4],
             "sub_gain":1.5,"sub_dur":0.55,"melody_dyn":true},
 "programs":{"Melody":[0,0], ...}, "mix":{"Melody":[76,104], ...}   // GM 音色 + CC10 声像/CC7 音量
}
```

> ⚠ **小节号是 0 基**（第一小节 = `0`）。`melody` 用**段内**号、`notes_extra` 用**全局**号
> （段区间累加：第 1 段从 0 起、第 2 段从 `bars1` 起）。引擎就是这么算的 ——
> `song_engine.py` 是 `bar * 4 + beat`、段表从 `_acc = 0` 起；`transcribe_to_song.py` 落盘用
> `int(秒 / 小节长)`。**这条以前一个字没写，同一天坑了两次**（把 0 基当 1 基 → 假报
> "melody 与 Piano 轨零重复"、逐小节密度表整体错位一格）。读/改这两个字段前先按 0 基想一遍。

## 各参数速查

**`theme` / `basis`（模板依据，`new_song.py --theme` 自动写）**：`theme.name` = 主题模板包名，
连同 `pack`/`templates`/`melody_profile`/`mix_target`（混音目标 = 对齐到哪份真实音频画像，
**不是模板依据**）/`energy_curve_db`（段间能量曲线，落在各段 `arr.mix` 上）。
**模板只能是 `refs/midi2/` 或网络权威数据**，且 ≥8 首同主题；
老 `--from` 会写 `basis.kind=copied_song`（不合规，`check_song` 拦）。

**`meter`**：拍号，缺省 `[4,4]`。`[3,4]`（一小节 3 个四分）/ `[6,8]`（6 个八分＝3 个四分）。
  引擎内部的"拍"**一律是四分音符**（`bpm` 也是四分音符速度），拍号只改三件事：
  一小节几个四分、写进 MIDI 的拍号元事件、**强拍位置**（4/4→第 1、3 拍；3/4→只有第 1 拍；
  6/8→第 1 拍与第 4 个八分）。4/4 的输出与加这个字段之前**逐字节相同**。
  ⚠ **分析侧仍只支持 4/4**（参考曲画像 / `melody_gen` 会拒绝非 4/4，不给你算错的结果）。

`bass_style`：`offbeat`（反拍驱动）/ `eighth`（八分）/ `sixteenth`（十六分双踩）/
`simple`（正拍长音，带 sub 层）/ **`pump16`**（每拍 e/a 两个十六分都推、正拍留空）/
**`waltz`**（圆舞曲：根音只踩第 1 拍 + 第 3 拍一个轻五度）

`perc_style`：`light`（沙锤+轻底鼓）/ `dance`（四踩+反拍踩镲）/ `orchestral`（定音鼓+三角铁）/
`none` / **`pump`**（八分踩镲 + 十六分幽灵镲 + 军鼓 2/4 + 底鼓踩"a" + 每 4 小节过门 + 每 8 小节吊镲）/
**`waltz`**（底鼓只踩第 1 拍 + 侧棒点第 2、3 拍 + 八分沙锤）

**奇数拍号（3/4）时钢琴/电钢自动改走华尔兹的 pah-pah**（和弦落在第 2、3 拍）——
配 `bass_style: waltz` + `perc_style: waltz` 才是地道圆舞曲；4/4 的写法与输出**逐字节不变**。

`patterns.voicing_shift`：和弦声部整体移调（+12 可让偏厚的中低频变清亮，**全局参数、不能按段**）
`patterns.sub_gain` / `sub_dur`：sub 层强度与长度（**必须短**，长音会把低频节奏糊成块）
**`patterns.staccato`**：伴奏音长缩放（默认 1.0）；调小 = 在鼓点之间腾出空间
**`patterns.melody_dyn`**（opt-in，默认关）：旋律的**乐句级力度曲线**（句 2/3 处高点、句末收）
**`patterns.piano_stab_vel` / `piano_stab_dur`**（opt-in，缺省 = 老行为**逐字节不变**）：钢琴
  **反拍短音**的力度基准（缺省 58，奇数小节 +6）与时值（缺省 0.28 拍）。用户报"**镫一下**"时
  **只调力度有效**（实测 58→36 → 跳变 +10.3 → +6.7 dB）；**加长时值毫无作用**（读数一字未变）→ 坑 236
**`patterns.hook_stab_vel`**：Hook 轨**反拍切分短音**（`ep_part` · 时值 0.22 拍）的力度基准
  （缺省 54，重音位 +8）。⚠ `20_piano_rain` 实测**它才是全曲最突出的"镫"** —— 比同刻主奏响
  **+20 dB**（比 Piano 轨那批明显得多）；参数化后取 26，"比主奏 ≥+10dB"从 2 处清零。
  定位用 `scripts/probe_sustain.py` 的 ② 榜（`harmony_check.stab_candidates`）
**`sections[i].arr.harmony`**：副旋律/加厚层 —— 给旋律配和弦内的低三度，走 Strings（无则 Hook/Piano）
**`sections[i].arr.mix`**：段落级 CC7 自动化，如 `{"Strings":80,"Perc":46}`
  —— **做"起伏"最直接的手段**，也是"段间对比"（像不像的关键）的实现方式

**`patterns.notes_extra_full`**（扒带/还原专用，**默认已翻转为 true**）：`notes_extra` 里的
  **转录**音符是"逐音照写"还是按 `arr.density` **逐小节抽样**（上限 {1:4, 2:10, 3:18} 音/小节/轨）。
  ⚠ **2026-09-26 默认由"抽样"翻转为"全量"**（用户："**我需要每次提取时都能达到 V1 的准度**"）。
  抽样实测专砍用户最在意的地方：`dear_good_friends` 转录只留 **54%**
  （Piano 984→517 · Hook 482→265 · Bass 47→38），用户点名"不一样"的 0–27s / 63–71s / 126s+
  保留率仅 **13%~35%**（全是 `density=1` 段），而他没投诉的 `density=3` 段保留 **50%~100%**。
  翻成全量后用户原话："**V1_全量转录.mid 非常好**"。
  要旧的抽样行为 → 显式写 `false` **并**在 `patterns.notes_extra_sample_reason` 写理由
  （守卫 `t_restore_notes_full`：扒带曲不许"不声明"只吃默认）。`notes_extra` 为空的曲子**不受影响**。

## 风格预设（`song.json` 里写一行 `"style": "<名字>"`）

| 风格 | 音色与编配 | 节奏 |
|---|---|---|
| `acoustic` | 钢弦吉他分解 + 钢琴 + 弦乐 + 沙锤 | simple / light |
| `daily` | 钢琴主奏 + 钢弦吉他 + **电钢琴切分** + 沙锤 | simple / light |
| `gorgeous` | **竖琴分解 + 弦乐 + 人声合唱垫 + 钢片琴** + 定音鼓/三角铁 | simple(带 sub) / orchestral |
| `dance` | **合成主奏 + 电钢琴 + 琶音 + 合成贝斯** + 四踩底鼓 | sixteenth / dance |
| `ballad` | 尼龙吉他 + 钢琴 + 弦乐 | simple / light |

预设里的**音量配比是实际渲染调过的经验值**（如 `gorgeous` 刻意比直觉更瘦更亮）。
`song.json` 里显式写的 `programs/mix/patterns` 覆盖预设。
`python scripts\new_song.py --list-styles`；新歌加 `--style gorgeous` 即可套用。

## 5. 参考曲画像（仿写依据）

### BGM16c.ogg（抒情向）

- **F 大调，150 BPM，4/4，104s（≈65 小节）**，几乎没有打击乐（高频起音仅 0.3 个/秒）
- 主 vamp：`Gm7 → A7 → Bbm7 → C7 → F7` = **ii7–III7–iv7–V7–I7**，低音半音上行 G–A–B♭–C
- 签名手法：**借用 iv7（小四级）、各级属七、三全音代理**（bar 50 `F#7 → F7`）、intro 用 sus4 长音铺底
- 音色画像：40–160Hz 最强；5–10k ≈ −12.6dB、10–18k ≈ −19.6dB；质心 2366–3466Hz；宽度 0.49–0.53

### bgm01c.ogg（舞曲向，drive_pop.py 的蓝本）

- **128 BPM，4/4，155s**；C/D 为中心的小调（安静段音级 A D G A# D# → 开放五度 D-A + Bb/Eb 色彩）
- **节奏型（16 分格）**：低频 `★★◇·★★··★★◇·★★◇·` = 每拍正拍+十六分**双踩底鼓** + 贝斯十六分驱动
  高频 `◇·★◇·◇★◇◇·★◇·◇★◇` = **反拍踩镲**（★只在每拍后半拍）
- 结构：前 8 小节安静（−20dB）→ 之后全程满编（−14dB）
- 音色画像：40-80Hz 最强；质心 **3683Hz**；10-18k ≈ −21.8dB；宽度 0.316；RMS **−14.3dBFS**

**对齐检查**（同口径跑一遍即比：分段看 `section_probe.py`、倍频程看 `analyze_ref2.py`）：
```powershell
& $py section_probe.py <我的文件> 1.6;   & $py analyze_ref2.py <我的文件>
& $py probe_style.py <文件> --bpm 128    # 16 分节奏型对比
```

## 6. 渲染管线的参数（`render_midi.py`）

`FluidSynth(-ni -g1.0 -r44100, reverb room-size .78 / width 1.0 / level .8, chorus on)`
→ 高频搁架 → 低频搁架 → 3 阶高通 → `tanh` 软限幅 → 响度归一 → 中侧加宽 → 峰值上限 0.97 → ffmpeg q=8

```powershell
# 抒情向（夏日曲）：响度 −16.9，宽 ×2.2
& $py render_midi.py summer_seaside.mid summer_seaside_sf --width 2.2 --rms -16.9 --shelf 3.0
# 舞曲向（drive_pop）：响度 −14.3，宽 ×1.0，低调 +3.5，高通 42Hz，限幅更狠
& $py render_midi.py drive_pop.mid drive_pop_sf --width 1.0 --rms -14.3 --shelf 2.5 --hp 42 --low 3.5 --drive 2.0
```

可选参数：`--width`（中侧加宽倍数）`--rms`（目标响度 dBFS）`--shelf`（高频搁架 dB@3kHz）
`--low`（低频搁架 dB@150Hz）`--hp`（高通 Hz，3 阶）`--drive`（软限幅强度）

**注意**：中侧加宽 >2 的代价是单声道回放会损失侧向内容（游戏内立体声播放无影响）。
