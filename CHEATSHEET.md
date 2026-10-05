# 命令速查（CHEATSHEET）

README 有 token 预算（守卫会拦），完整命令示例集中在这里。README 只留一行指针。

## 写一首新歌（四步）

```powershell
cd <工具链根>
$py = ".\.venv\Scripts\python.exe"

# ① 模板包：同主题 ≥8 首 MIDI 模板聚合成画像（新歌的**唯一合规依据**）
& $py scripts\theme_pack.py --list-themes                  # 15 个主题：日常/夜晚/海边/战斗/圆舞…
& $py scripts\theme_pack.py seaside                        # → refs/themes/seaside.json（模板清单+来源 URL）
& $py scripts\theme_pack.py seaside --allow-fetch          # 同主题不足 8 首时联网抓（BitMidi/VGMusic/Mutopia）
& $py scripts\theme_pack.py seaside --calibrate            # 标定段间曲线的阻尼系数（出探针曲→渲染→写回包）
# ② 依据模板包出歌：和声/速度/调式/节奏音型/配器/段落 + 旋律语言画像（自动跑 melody_gen）
& $py scripts\new_song.py 35_x --theme seaside --ref BGM16c   # 省略 --ref = 用包里的**混音目标**
#   BPM：默认在**主题模板的 p25~p75 真实范围**内按 seed 取（旧行为是固定中位数 →
#   同主题每首一样，实测 battle 3 首全 139、daily 4 首全 128）；要钉死加 `--bpm 150`
#   --energy-gain 1.0（默认）= 段间能量曲线的阻尼系数（0=不写曲线，1=照抄目标起伏）
#   混音目标 = 对齐到哪份真实音频画像（BPM/打击感/调式挑），**不是模板依据**
# ③ 只改 songs\35_x\song.json（chords / melody / sections）
# ④ 一条命令：作曲 + 真音源渲染 + **自动调参** + 对标成绩单
& $py scripts\make_song.py 35_x --check                    # 先 2 秒查数据
& $py scripts\make_song.py 35_x
& $py scripts\make_song.py 35_x --no-tune                  # 只渲染一轮不调参
```

模板**只能是** `refs/midi2/` 或网络权威数据；老 `--from` 会写 `basis=copied_song`，
`check_song` 报"依据不合规"。

## 模仿写歌（照着某一首参考曲写 —— 与上面四步分开的一条路径）

**分界**：依据**同样是主题模板包**（合规不变），但段落层要按**单首参考曲**的实测结构重写。
口径 / 七步 / 段名纪律 / 三条路径的边界 → **`docs/IMITATE-PATH.md`**。

```powershell
& $py scripts\profile_ref.py "<素材>/BGM35.ogg" BGM35 --bpm 150   # ① 参考曲画像（BPM 必须显式钉死一层）
& $py scripts\new_song.py 40_imitate_b35 --theme night --ref BGM35 --seed 40   # ② 骨架（依据=模板包）
& $py scripts\imitate_plan.py 40_imitate_b35 --plan plan40.json --dry-run      # ③ 只校验 + 打段表
& $py scripts\imitate_plan.py 40_imitate_b35 --plan plan40.json                #    写盘（会把 melody 清空）
& $py scripts\melody_gen.py songs\40_imitate_b35\song.json refs\themes\night_melody.json --avoid songs
& $py scripts\check_song.py 40_imitate_b35
& $py scripts\make_song.py 40_imitate_b35
```

- 结构表字段与硬校验（段名只能 A–E、同角色 = 同进行 + 同旋律、段数/和弦数）→ `scripts\imitate_plan.py --help`
- ⚠ **只改 `--ref` 不算模仿**（那只是混音目标）；**抄音符是还原**（`docs/RESTORE-METHOD.md`），不是模仿

### 旋律：**一首一份画像**（共用 = 十首一套口音，孪生 5 对）

```powershell
# --avoid 去重（8 条取最不像）；--step-bias 1.0 优先挑级进最高的那条（听感更顺）
& $py scripts\melody_gen.py songs\23_x\song.json refs\melody\psg_BGM16b_melody.json `
      --seed 23 --avoid songs --candidates 8 --step-bias 1.0
& $py scripts\melody_gen.py <song.json> <画像> --motif off   # 退回"逐音直方图"版（A/B 对照）
#   v2 动机层（默认开）：动机重复 + 大跳反向/回填 + 句末终止式（结构层指标见输出末尾）
& $py scripts\probe_melody_lang.py      # 验收：孪生对（≥85%）必须 0；落点/音程维看全库平均
```

### 紧凑写歌（省字）

```powershell
& $py scripts\build_song.py <spec.json> --out songs/<曲名>   # spec → song.json（自动补时值/排列/脚手架）
& $py scripts\build_song.py --to-spec songs/<曲名>/song.json  # 反向导出 spec（改现成的歌用）
& $py scripts\build_song.py --selftest                        # 推导规则自测
```

### 分段拟合（像不像的关键）

```powershell
& $py scripts\analyze_sections.py <参考曲> <名字>        # 分段目标 → refs/sections/<名字>_sections.json
& $py scripts\analyze_sections.py <参考曲> <名字> --json  # 机器可读（给下游）
& $py scripts\new_song.py 17_x --from <模板> --ref <名字> --from-sections <名字>
#   ↑ 自动把每段的"亮/暗"写成 arr.mix 曲线（段数必须与参考分段数一致）
```

### 编配/口径诊断（"改了没效果"时先跑）

```powershell
& $py scripts\bands_abs.py songs\21_g150_velvet\bgm16c_v2_sf.wav BGM16c_v2  # 绝对 dB + 占用率
& $py scripts\bands_abs.py a.wav b.wav --win 6-20                        # 只看安静段
& $py scripts\probe_timbre.py --programs 8,9,51,95,99                    # 挑"空气层"音色（等响度比）
& $py scripts\probe_timbre.py --solo songs\21_g150_velvet\song.json      # 逐轨量真实电平（弱轨现形）
& $py scripts\probe_peaks.py "<参考曲目录>/BGM16c.ogg" --bpm 150 --bars 1-16  # 谱峰扒谱（速度必须先钉死）
```

**口径**：成绩单的"倍频程"是相对本曲最响频段的 → 编配改动看 `bands_abs`（绝对 dB）；
"像不像"还差**占用率**（墙/点），只有它和 `probe_timbre` 有（见坑 103）。

### 模型侧主观分（可选，需 `.venv-ml`）

`metrics` 全绿后剩下的"好不好听"只能靠耳朵 —— 这两支把**部分**主观判断变成可复现数字：

```powershell
$ml = ".venv-ml\Scripts\python.exe"          # 主 venv 没有 torch
$env:HF_ENDPOINT = 'https://hf-mirror.com'   # HF 直连不通时（镜像实测可用）

& $ml scripts\probe_aesthetic.py <音频> --segments 6   # CLAP 情绪走向（看"前悲后喜"有没有被听出来）
& $ml scripts\probe_aesthetic.py --all --prompts mood  # 全库情绪排序（--json 存结果）
& $ml scripts\probe_aqa.py --all                       # 全库美学分排序（CE/PQ/CU/PC，--chunk 控显存）
```

**口径**：CLAP 量"**像哪一类**"，不是好听度；AQA 是模型对人类主观分的回归，**没有本地绝对参照**。
⚠ 实测库内区分度极低（39 首里 31 首的 CE 挤在 0.3 内），与客观指标几乎不相关（|r| ≤ 0.28）
——**只用来揪异常曲目，不能当验收门**（完整边界见两个探针的 docstring）。

### 音频大模型"听"曲子（**线索生成器，不是判据**）

全部内容在 **`docs/AUDIO-CRITIC.md`**（用法 · 硬约束 · 实测数字 · 环境装法 · 交接）。
一句话口径：**判不了"哪版更好"（价值判断），但能用 `--compare` 对比"原版 vs 当前版"的差异**
（同段同问，读它描述的内容；实测见 `docs/AUDIO-CRITIC.md` §6.0）；且**默认 greedy 才可复现**
（模型默认在采样，同段连问三次给三个不同答案）。

```powershell
$ml = "<根>\.venv-ml\Scripts\python.exe"      # ⚠ 主 venv 没有 torch，必须用 .venv-ml
& $ml scripts\ask_audio_critic.py <音频> --segments 8 --load-4bit       # 逐段扫描（GPU 4bit ≈3 秒/段）
& $ml scripts\ask_audio_critic.py <音频> --segments 8 --load-4bit --sample --repeat 5
#   ↑ 同段采样 5 次 → **只留稳定复现的线索**（实测 74 条独立线索里只有 7 条稳定）· --band 桶宽（默认 4 秒）
& $ml scripts\ask_audio_critic.py --compare a.ogg b.ogg --segments 8    # 同段同问对照
#   输出：**整曲时间轴上的指控清单**（可直接跳到问题点）+ 稳定线索表；越界 / 没给时间的单列不硬映射
```

### "像不像 / 哪里不像"（Music Flamingo 7B，音乐专用；2026-10-02）

```powershell
& $ml scripts\ask_music_critic.py <音频> --segments 12                  # 逐段描述（单段 ≤30 秒）
& $ml scripts\ask_music_critic.py --compare 参考.ogg 我的.ogg --segments 12
#   ↑ **主用法**：同段同问、并排读差异（判差异 ≠ 判好坏）
& $ml scripts\ask_music_critic.py <音频> --repeat 3 --json out.json     # 同段问三次（验证稳定性）
# ⚠ 必须 greedy（采样 5 次 5 种答案）；BPM 实测 8/8 在 5% 内，乐器**只信大类**；
#   能力标定 / 两条静默坑 / 模型位置 → `docs/AUDIO-CRITIC.md` §11
```

### 复刻/改曲后"到底动了哪一层" + "这条旋律是谁在弹"（2026-10-02）

```powershell
& $py scripts\midi_diff.py 改前.mid 改后.mid     # 逐轨音符数 / 音域 / program change 时间线（秒）
#   ↑ 改完曲子先跑这条：实测靠它抓到"从备份恢复后只重放了前半段音色改动、后半段静默丢了"

& $py scripts\who_plays_lead.py <demucs分轨目录> <song.json> --track Melody --by-sec 30
& $py scripts\who_plays_lead.py <分轨目录> <song.json> --track Piano --melody-proxy --end 135
#   ↑ "参考曲里这条旋律到底是谁在弹"（**按音高判**，不按响度 —— 按响度会被低音/鼓骗）
#     `--melody-proxy`＝旋律混在伴奏轨里时取"每小节最高音"当代理 · `--by-section` 按段落汇总

& $py scripts\who_plays_lead.py <分轨目录> <song.json> --track Melody --by-section --verdict
#   ↑ **交付前判定**：逐段报"旋律族占比 < 0.50"的段（含义＝这段我们认作旋律的音，
#     在参考曲里不是旋律乐器在弹）＋ 报警段占比。三首实测段级 **27% / 33% / 15%**；
#     ⚠ 落在 5%~30% 才有区分度，别把门调到"全过"。--fail-under 改门 · --selftest 尺子自检

& $py scripts\timbre_audit.py <song.json> --ref <原曲.wav> --stems <分轨目录> --mine <我方.wav>
#   ↑ 已接第⑤层（自动跑）：**候选轨各量一遍、取旋律族占比最高那条**当"我方主奏"，
#     再逐段报"哪几段不像"。⚠ 别手指定轨：同一首 dgf 用 Hook 是 33%、用 Piano 是 93%。
#     `--lead-track` 显式指定 · `--no-lead` 跳过这一层（快跑）
```

### 逐轨事件（面板卷帘 / 排查用）

```powershell
& $py scripts\song_events.py songs\21_g150_velvet\song.json [--track Bass] [--json]
```
### 可视化面板（写歌时最省时间的一条路）

```powershell
studio\start.cmd    # 起面板（已在跑则只开浏览器）→ http://127.0.0.1:8765
studio\stop.cmd     # 停
```

面板里点：**⚡ 试听本段** · **🎚 自动配平** · **🧬 候选搜索**（结果**逐项勾选采用**）· **📦 导出**。
细节见 studio/README.md；口径与 CLI 完全一致（改的都是 song.json）。
### MIDI 参考（音符层精确参考）

```powershell
& $py scripts\midi_ref.py <file.mid>              # 和声进行/低音线/旋律线/节奏型/曲式
& $py scripts\midi_ref.py refs\midi --recursive   # 批量看
& $py scripts\midi_ref.py <file.mid> --json       # 机器可读
& $py scripts\midi_ref.py <file.mid> --bars 5-20  # 只看某几小节
```

**音符层看 MIDI，频谱层看音频画像**：画像的频谱/宽度/响度只能从真实录音拿，MIDI 精确给速度/和声/
声部/节奏/曲式 —— 两者**互补**。素材自备：公共领域用 Mutopia / IMSLP；动漫游戏 MIDI **版权灰色，不进仓库**。
### 查文档 / 改文档（长文档别整篇读）

```powershell
& $py scripts\doc_map.py            # 生成 docs/DOC-MAP.md（大目录=主题域 → 小目录=节 + 行号 + 体量）
& $py scripts\doc_map.py --stdout   # 查一节 = 先在地图上拿行号，再只读那一段（别整篇读 HISTORY 38k）
& $py scripts\doc_map.py --check    # 是否过期（守卫 doc_map_fresh 用的就是它）
& $py scripts\doc_map.py --list     # 盘点所有文档 + 体量 + 标题（新文档归类时用）
#   ⚠ 改过任何文档后要重跑第 1 条 —— 地图是生成物、里面全是行号，不重生成自检会 FAIL
```

### 体检与校验

```powershell
& $py scripts\check_audio.py <任意音频>              # 能不能读 / 速度几层（-deep 出倍频程）
& $py scripts\check_audio.py <目录> --formats        # 目录里哪些能读
& $py scripts\check_song.py <曲目>                   # 渲染前查数据契约判据（0.4 秒）
& $py scripts\check_song.py <曲目> --fix             # 自动修和弦音集/强拍
& $py scripts\probe_bpm_layers.py <音频> --stems <demucs目录>   # **BPM 层级**：自相关/IOI/网格贴合 + 关系提示
& $py scripts\selftest.py --only <名1,名2>           # **只跑指定检查**（0.1 秒 vs 全量 169.9 秒）
& $py scripts\selftest.py --list                     # 列出全部检查名（配 --only 用）
#   ⚠ 全量 `selftest.py --fast` 只在**交付前**与**改过检查项本身**时跑；
#     改一条守卫用 `--only`，别为它等 2.8 分钟（实测 `track_balance` 一项就 58.5s）
```

### 还原（扒带）

```powershell
& $py scripts\imitate_ref.py <原曲.ogg> -o <项目>   # 九段全链；原曲别放进项目目录（成品同名会覆盖它，PITFALLS 208）
& $py scripts\note_dur_stats.py <曲.mid> [参考.mid] # 碎音率/时值中位（听感体检）
& $ml scripts\transcribe_ymt3.py <音频>             # 转录 → **默认接续**出 song.json（引擎编配＝正路）
& $ml scripts\transcribe_ymt3.py <音频> --no-song   # 只要一份纯 MIDI（**丢掉引擎的编配/音色分配/段落密度**）
#   ⚠ **分轨输入比全混音慢 3–4 倍**（0.55~0.60 vs 0.148 s/段）：Demucs 分轨是分布外输入，
#     模型不吐 <eos>、空解码到 256 步上限 —— 现在会自报「解码步数」并在撞上限时告警。
#     纯 restore_oneshot 路径其实只用到它的第①层初筛 → 可少跑（实测省 3m42s）。→ ML.md
#   ↑ 2026-09-20 起「正路是默认」：绕开要**显式** --no-song（PITFALLS 185 / SKILL §8）
#     接续链：切轨 → analyze_chords → transcribe_to_song --auto → songs/<名>/song.json
#     ↑ `transcribe_to_song.py` **默认全量**（写 `notes_extra_full: true`，逐音照写）。
#       要旧的"按 arr.density 抽样"（每轨每小节砍上限 4/10/18 音）→ 加 `--sample`
#       （它写 `false`；**不能靠"不写字段"表达抽样** —— 引擎默认已是全量）。守卫 `t_restore_notes_full`
```

参数：`--dur-floor`（时值下限，只动旋律层，默认 0.55 拍）· `--absorb-into`（YMT3 的合成器通道并进哪条轨，默认 Strings —— 并进 Piano 会用钢琴音色弹它）· `--thr-extra`（Guitar/Strings 是单来源层，套 `--merge-thr` 会把整层砍掉）；改过参数要 `--from bass --force bass` 重跑。
判据 → `docs/RESTORE-METHOD.md` §4b · PITFALLS 206/207。

**第一道工序：逐轨精度审计**（**每个提取/还原任务都跑**，用户 2026-10-04 定）

```powershell
# ① 分轨（**必须 6 轨**：piano/guitar 才拆得开；4 轨下 Pad 55.6%→0%、Hook 65%→81% 是假象）
& $ml -m demucs -n htdemucs_6s -o <分轨输出目录> <原曲44k.wav>     # 199s 音频实测 33 秒
# ② 逐轨量「精度 + 召回 + 缺口小节」——"我发的音对不对"＋"原曲有的我漏没漏"
& $py scripts\audit_stems.py <曲目> --stems "<分轨输出目录>\htdemucs_6s\<曲名>" --ref <原曲44k.wav> [--json 读数.json]
& $py scripts\audit_stems.py --selftest        # 尺子自检（440Hz 合成件 + 判据自证）
& $py scripts\audit_stems.py <曲目> --stems <分轨> --ref <混音> --gate   # 准入：unusable 则非零退出
#   ⚠ **两个闸门别跳**：① 报告里的「整层缺失」= 分轨有内容但我方**没有对应轨**
#      （实测某曲 13 个解码通道只出 1 个，漏掉的 5 层在逐轨表里根本不出现）；
#      有意移除的层用 `--accept-missing drums,vocals` 声明，否则会一直判 unusable。
#   ② 准入 `fixable` 才许进「去鼓/补层」这类修工序 —— 上游错音下游修不动（PITFALLS 320）。
#   ⚠ 转录阶段另有一道：`transcribe_ymt3` 在**中位解码步数 ≥200 或 ≥半数 batch 撞上限**时
#      拒绝接续 song.json（撞上限那首逐音精度只有 4.8%~57%，不撞的 75~87%）。
#   ⚠ 判据：可信轨**精度 ≥70% 才动手改内容**；**召回缺口 = 漏内容**，与"错音"分开修
#   ⚠ 4 轨跑时 Piano/Hook/Glock 会显式列成「测不到」（不许整行消失）
#   ⚠ 实测价值：某曲鼓轨 1250 音精度 **3%**（整层凭空，害人连听四版）→ PITFALLS 316
# ③ 若"原曲没有鼓组"（drums 分轨 RMS 比全混音低 >15dB、几乎无 <120Hz）：去鼓 + 补沙锤层
& $py scripts\strip_drums.py <曲目> --dry                       # 先看处理表（三处一起清）
& $py scripts\shaker_layer.py <曲目> --ref-drums <drums.wav> --dry   # 照实测高频律动建网格（只放 GM 82/70）
#   ⚠ 网格三条静默契约（件名/第三列/静音门）→ PITFALLS 317
```

### 交付前体检：关系型判据（**交付前必跑**）

抓"逐音级读数全对、交付却是错的"那一类（编配被压平 / 同音高写 2–5 份 / 长音被切 / 鼓凭空连击）。

```powershell
& $py scripts\preflight.py <成品.mid> --ref <原曲44k.wav> --stems "<分轨目录>" `
      --skeleton <源转录.mid> [--base <补音前.mid>] [--mine-wav <我们渲染.wav>] [--json 报告.json]
& $py scripts\preflight.py --selftest     # 15 个已知答案用例（坏件必须响 / 好件必须不响）
# 退出码：0 无 FAIL（可交付）· 1 有 FAIL（不许生成成品）· 2 缺关键输入
#   ⚠ **只有 ①② 是门**，③④⑤⑥ 是 WARN —— 它们的"越界"在好件上也出现（是这条线的基线），必须配人耳 A/B
#   ⚠ ①b「疑似丢轨」默认只报线索：引擎编配版按角色自行分配音色，骨架的 (channel, program)
#      本来就留不住（真实 douzo 报 ch1 prog8 / ch2 prog16，那两层其实在）；要当门加 `--strict-tracks`
#   ⚠ ④b 只看 other 会在"other 本来没内容"的曲子上**恒真**（实测仅 4% 的窗有能量）⇒ 报「未量」，
#      判据改成"我方放音而**全部分轨**都极静"才算凭空音 —— PITFALLS 322
#   ⚠ ⑤ 不用 onset 检测器：它的 normalize 会把静音区噪声放大成"有冲击"（坏件假 PASS）
#      ⇒ 直接量每一下的 3–8kHz 抬升 —— PITFALLS 321
```

### 单乐器独奏化（"提取 MIDI 之后完全用钢琴 / 只用一件乐器演奏"）

```powershell
& $py scripts\solo_instrument.py dear_good_friends        # → songs\dear_good_friends_solo\（.mid + _sf.ogg + notes.md）
& $py scripts\solo_instrument.py bgm35_extract --instrument strings --drums drop
& $py scripts\solo_instrument.py siren_end --dry          # 只出处理表，不写盘
& $py scripts\solo_instrument.py <曲> --no-render         # 只要 song.json（稍后自己 make_song）
& $py scripts\solo_instrument.py --selftest               # 7 条不变量（合成夹具，不碰曲库）
```

它做四件"只改 `programs` 做不到"的事：**鼓 → 乐器音型**（鼓轨在**通道 10** 上 `program` 无效，
改成钢琴音色也还是鼓声）· **跨轨同刻同音高去重** · **长音裁剪**（`--max-beats`，钢琴靠衰减）·
**低音区整理**（`--low-floor` 默认 33 = A1）。旋律仍走 `melody` 字段（守卫与乐句力度都不丢）。

**可弹化（`--playable`，默认 `off` = 不削、保真优先）**：
⚠ **2026-10-01 用户试听判定："感觉效果不好，算了"** —— 即使按音乐重要性削（和弦内音 +3 / 根音再 +1 ·
和弦外音 −2 · 踩拍点 +1.5 · 长音 +1 · 鼓写音 −1.5，保留率 和弦内/外 59%/37% · 踩拍点/弱拍 69%/53%），
**简化版仍不如保真版**，默认已改回不削。要试就显式加 `--playable easy|normal`
（`easy` = 左手 ≤2 音 · 右手 ≤3 含旋律 · 单手跨度 ≤八度 · 每拍 ≤6 · 同音串 ≤3，门槛取库内
86 首钢琴曲的 p10~p50 偏简单侧）。⚠ 反面教材：第一版按"几何位置"削（左手取最低音、右手取
离旋律最近），实测**和弦内音保留 51% vs 外音 54%（无区分）· 踩拍点 39% vs 弱拍 56%（反向）**，
用户当场听出"没有聚集关键特征 …… 反而留下了一些错误或不重要的音"（PITFALLS 299）。
量任意曲子（含现成 `.mid`）：

```powershell
& $py scripts\probe_playable.py songs\dear_good_friends_solo\song.json      # 体检（档 easy）
& $py scripts\probe_playable.py <x.mid> --level normal --split 62 --json    # 换档/换分界/机器可读
& $py scripts\probe_playable.py --selftest                                  # 尺子自检（正例/负例）
```

**钢琴手法补过渡（`--fills auto`，默认 off）**：原曲的过渡常由**别的乐器**做（鼓 fill /
贝斯推进 / 合成器 riser），全钢琴化之后就"硬切"。`auto` 按**每个段界自己的原曲证据**选手法
（技能 §20：不许一刀切）：段末 2 小节鼓事件 ≥ 全曲均值 ×1.15（= 有过门）→ 段首和弦分解（`arp`）·
后 2 小节音数 > 前 ×1.4（变密）→ 右手级进上行 + 左手八度推进（`run_up`）· 前 > 后 ×1.4（变疏）→
下行渐弱（`run_down`）· 两边都 <6 音 → **留白**（`rest`）。素材只取"段末和弦 ∪ 段首和弦"的音级、
力度 70~88（低于旋律 96）、跑动占段末 `--fill-beats`（默认 2 拍）。实测 `bgm35_extract`：
**23 个段界 → 164 音**（run_up 7 · run_down 6 · arp 2 · rest 8），段界前 2 拍的音数
**171 → 335（中位 7 → 16）**。⚠ 这些音是**新造的**（不是原曲内容）—— 报告与 `notes.md` 里逐段界列明。

渲染用 `make_song --no-tune`：**autotune 会硬把独奏版往原曲混音推**，而编制变了（5–18kHz 塌是物理结果）。
⚠ `--instrument` 用 GM 0（真钢琴）时 `check_song` 会提示"只响 0.几秒"（掉 12dB 只要 0.24s）——
要持续型出对照版用 `--instrument ep`（GM 4 电钢琴，钢琴族内）。全部坑 → `PITFALLS.md` 298。

### 主题符合度检查（"这首像不像它标的主题" —— **不靠音频大模型**）

```powershell
& $py scripts\theme_fit.py 101_neon_drive      # 单曲：逐项对比它自己的主题画像
& $py scripts\theme_fit.py --themes            # 主题间区分度总表（**选主题前先看这个**）
& $py scripts\theme_fit.py --selftest
```

查六项（全部来自主题包画像，可复现）：**BPM 区间 / 拍号 / 主奏音色 / 各声部音色 / 段落 /
旋律形态**。⚠ 实测（2026-10-01）：**15 个主题只有 7 种主奏音色**，**GM 73 长笛独占 8 个**
（daily/folk_tale/neon/retro/seaside/sorrow/tender/waltz）—— 这几首生成出来**听感都像轻音乐**，
与"霓虹电子/海边/悲伤/圆舞曲"的主题名对不上。各主题池里其实有更贴的候选
（neon 池有 81 锯齿/80 方波 · sorrow/waltz 池有 0 钢琴 · 25 钢弦吉他），
只是 `new_song.theme_programs(pick=0)` 取的是**池里第一个**。
**换法**：改 `song.json` 的 `programs.Melody`（段级 `arr.melody_prog` 会覆盖它，要一起看）。

### 多视图投票装配（族票 · 应用层，**不跑模型**）

```powershell
# 一键链（推荐）：音频 → 6 视图 → 族票装配（Demucs 分轨 + YMT3×2 + BP×4）
& $py scripts\vote_views.py <音频> <工作目录>              # 默认 cheap=6 视图（实测 ΔF1 +0.0075）
& $py scripts\vote_views.py <音频> <工作目录> --profile full   # 9 视图（+0.0083，慢 1/3）
#   --dry-run 只看计划 · 产物已在就不重跑（幂等）· 产物：ymt3.mid(base+骨架) · base.mid ·
#   views\*.mid · voted.mid（音色按骨架还原，坑 277）

# 手工装配（已知 base 与视图时）
& $py scripts\vote_apply.py <真值.mid> <base.mid> <out.mid> --family-min 1 --s1 --skeleton <骨架.mid> `
    --view ymt3_nodrums=V1.mid --view bp_on70=V2.mid ...   # 族名 = 标签第一个 '_' 之前的前缀
& $py scripts\vote_apply.py ... --k 3 --view lab=path ...   # 旧视图票路径（逐字节复现旧台账）
#   --s1 = 打印无监督开关（≥0.825 建议别开投票，**只提示**）· --skeleton = 新音按同音高同刻回原轨
#   ⚠ 跨工具对比（tools\vote_family_rule.py）要传 `ymt3=`/`bp=` —— 那个入口把左边**整串**当族名
```

### 推不上去（github.com 时通时不通）

```powershell
bash tools/git-push/push_via_tunnel.sh       # 一键：先试直连 → 不通就挑可达 IP 走本地转发器再推
git rev-list --count origin/main..HEAD       # 推之前先看还有几个提交没推
```

根因**不是 DNS**，是"**某个目标 IP 的 443 被丢**"（详见 `tools/git-push/README.md`）；
零改动的办法是**隔 15 秒重试**（实测第 3 轮就通）· 症状台账 PITFALLS 212。
