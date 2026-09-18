# 主题模板包（新歌的「依据」层）

> **用户口径**：一次音乐生成要依据**很多不同的相同主题模板**；模板**只能**来自
> `refs/midi2/`（网络多风格 MIDI 模板库，含逐首来源 URL）或网络上的权威数据 ——
> 不许拿"自己生成的曲子 / 某一份音频"当模板。
> 工具：`scripts\theme_pack.py`（建包）+ `scripts\new_song.py --theme`（消费）。
> 命令示例见 `CHEATSHEET.md`；本文件讲**口径与判据**（为什么这么选、怎么复核）。

## 1. 三层结构（别混）

| 层 | 是什么 | 来源限制 | 产物 |
|---|---|---|---|
| **模板** | 同主题的 N 首 MIDI | **只能** `refs/midi2/` 或网络权威数据（带来源 URL） | 包里的 `templates`（逐首 file/md5/来源） |
| **主题模板包** | 这些模板聚合出的画像（和声/节奏/配器/曲式/旋律语言） | 同上（由模板推出） | `refs/themes/<主题>.json` |
| **混音目标** | 对齐到哪份**真实混音**（决定成品混成什么样） | 项目真实音频画像 `refs/*.json` | 包里的 `mix_target` |

**混音目标不是模板依据**：模板管"怎么写"，混音目标管"混成什么样"。
⚠ 不许拿 MIDI 渲染当频谱目标（技能里定死：MIDI 渲染 ≠ 真实录音），所以混音目标只从
真实录音画像里选。

## 2. 主题 → 风格集合 → 模板

体系里 15 个主题（`theme_pack.py --list-themes`），每个 = **主题词 → midi2 风格目录集合 + 引擎风格预设**：

| 主题 | 风格集合 | 引擎预设 |
|---|---|---|
| `daily` 日常 | pop / folk / anime | daily |
| `tender` 温柔抒情 | ballad / romantic / pop | ballad |
| `night` 夜晚 | newage / jazz / electronic | daily |
| `seaside` 海边 | newage / folk / pop | acoustic |
| `battle` 战斗 | rock / game16 / game32 | dance |
| `gorgeous` 华丽 | film / romantic / baroque | gorgeous |
| `waltz` 三拍圆舞 | classical / baroque / romantic / folk / public_domain | gorgeous |

（完整表在代码 `THEMES`；**风格集合必须够宽**才凑得满 8 首 —— 例：3/4 的模板全库只有 26 首，
所以 `waltz` 放了 5 个风格目录。）

**选模板**（确定性，同库同参数 → 同结果）：
1. 候选 = 该主题风格集合内、**拍号与主题一致**、BPM 40–220、≥8 小节、音密度合理的曲子
   （拍号不卡会把 [7,4]/[3,8] 混进 4/4 的统计 → 16 分格对不上，共识节奏型算成 0.1 音/小节）
2. 配额：**每个风格至少 1 首**，余量按可用数轮转分配
3. 风格内按 (bpm, 文件名) 排序后**均匀间隔取**（首尾都取到 → 覆盖速度带）

下限 **≥8 首**（`MIN_TEMPLATES`），默认取 10 首；不足直接报错，**不许凑数**（可 `--allow-fetch` 联网抓）。

## 3. 包里的画像（全部音符层，精确）

来自 MIDI，不受音频扒谱误差影响：**速度/拍号 · 调式（Krumhansl + 低音线加权 3 倍，
否则关系大小调互串）· 和声进行（逐小节和弦 → 罗马级数 → 跨模板 4 小节窗口，按音级序列计数）
· 节奏（每小节 16 分格占用率 + 按轨角色的密度）· 配器（GM 音色族 → 角色 + 音域）
· 曲式（段落长度/总量）· 旋律语言（落点/时值/音程/句长，高于声部单音化后统计）**。

关键字段：
- `harmony.primary` + `primary_source`：主进行（`window4` / `window2` / `chain` 三档降级，**每档都留证据**）
- `arrangement.arr_on / arr_off / arr_maybe`：配器三档（≥50% 模板有 → 开；**0%** → 关；中间交给风格预设）
- `form.plan`：段落计划（段名/小节数/用哪条进行）
- 旋律子画像另存 `refs/themes/<主题>_melody.json`（`melody_gen.py` 直接吃）

## 4. 生成怎么用它（`new_song.py --theme <主题>`）

1. `bpm` = 包里速度中位；`meter`/`style`/`patterns.bass_style/perc_style` 取包
2. 段落 = `form.plan`；和弦 = 主进行循环铺满（非 A 段换起点做变化）
3. 编制 = 引擎预设打底 + `arr_on/arr_off` 覆盖；**`perc` 永远是 1**
   （`perc_style=light` + `perc=1` 是"无鼓组但保住 5–18kHz"的唯一正解）
3.5 **音色** = `arrangement.prog_pool`（由 `extract_theme_timbres.py --inject` 写入），
   逐键覆盖 `STYLES[engine_style].programs` —— 生成**不再只套 5 套预设**：实测模板的实际
   音色远超预设（`classic` 有管钟 14／双簧管 68，`battle` 有排箫 75／钢弦吉他 25，
   `lounge`·`night` 有中音·次中音萨克斯 65/66）。用户判据"**乐器选择还是不像，在 MIDI 里
   也是一样的**"（即不是音源的锅）就卡在这一层。
   · 映射与 `ROLE_TO_ARR` 对齐：`ep→Melody`（lead/reed/pipe 折在这，正是主奏族）·
     `uku→Hook` · `piano/bass/strings/pad/glock` 同名；**`Perc`/`Arp` 不接**
     （`perc` 池里是音高打击乐 114/119，不是鼓组）
   · **取用层**过滤（守卫 `t_theme_timbre_pool` 在输出上判）：主奏禁慢起音
     （11／16-23／40-51／52-55／88-95）、`Hook` 禁弓弦簧管（109-111，ethnic 族被整族
     归进 guitar）—— `t_lead_timbre_attack` 只渲染 `STYLES` 预设、**管不到 song.json
     的实际值**，所以过滤必须写在这里
   · 模板主奏音色要排在段级 `melody_prog` 池**最前**，否则被段级值立刻覆盖
     （实测 `pcs=[71,0,13,8,13,4]` —— 71 只活了一个音）
4. 旋律 = `melody_gen` + 主题旋律画像（密度取 MIDI 精确值，不乘 F0 补偿系数）；
   **生成后当场体检**（`probe_melody_health`），不合格按 1.0→0.75→0.6→0.5 的密度阶梯重试 ——
   密集主题（如 `battle` 的 chiptune/game 模板）实测第一档碎音 37%，降到 1.94 音/小节才过
5. 写 `render.json` 的 `ref` = 包里的**混音目标**（`--ref` 可覆盖）
6. **依据留痕**：`song.json` 的 `theme` 字段写 name/pack/template_count/styles/source_kinds/
   primary_source/mix_target/templates（逐首文件名）+ melody_profile

非 4/4 主题（`waltz`，**3/4**）：**已支持自动生成旋律**（2026-09-18 修 `melody_gen` ——
`SPB`（一小节几拍）改由 `set_meter()` 按拍号设，守卫 `t_meter_spb_fits` 量落点不越界）。
实测：48 小节、MIDI `timesig=[3,4]`、`end_beat=144.0`、68 个旋律音最大 beat **2.75**（<3.0）。
**其余拍号仍拒绝**（强拍位置与句法没在那些拍号上量过，宁可拒绝也不给没验过的答案）。

## 5. 混音目标层（`mix_target`）

**多方聚合**（用户口径："混音要参考权威音源，也要多方参考"）：从 `refs/*.json` 里按下面三个
可量特征挑出**合格的前 N 份（≥3）**，**逐维度取中位数** → `refs/mix_targets/<主题>_mix.json`，
`render.json` 的 ref 指向它（单份的个性不整体带进成品：实测 `tender` 单份 BGM04 让成品中频厚
9.6dB，聚合 6 份后 5.2dB）。

| 判据 | 权重 | 怎么量 |
|---|---|---|
| 速度 | 0.55 | 与主题速度中位的差（差 30BPM 得 0） |
| 打击感 | 0.30 | 主题 `perc_style` ↔ 画像"高频段相对电平 + 高频起音占比" |
| 调式 | 0.15 | 主题大/小调 ↔ 画像安静段音级的 Krumhansl 判定 |

每份成员带 `source`（出处）—— **分级口径与"怎么接网络权威源"见 `theme_pack.tag_ref_sources`
的 docstring**。**人声主导画像直接排除**（`character != 'instrumental'`）。
`validate_pack` 复核"目标存在、有 bands、非人声、速度不差 30BPM、成员 ≥3 且逐份有 source"；
自检 `mix_target_aggregate` 另**逐频段核对"聚合值 = 成员中位数"**。

### 段间能量曲线（`energy_curve_db`）

目标画像的 `structure` = **每 8 小节一块**的响度（dB）→ 折成"相对均值"的起伏：

- **均值为 0** → 只改段间对比，**不改整体响度**（不会把自动调参的响度目标带跑）
- 生成时按**相对位置**映射到我们的段（段长默认也是 8 小节，段数不同就线性重采样）
- dB → MIDI CC7 走**实测曲线**：`dB(cc, cc₀) ≈ 38·log₁₀(cc/cc₀)`（幅度 ∝ **cc^1.9**）——
  CC7 是 **GM 凹曲线，不是线性幅度**！按线性换算会**过冲一倍**（实测目标 6dB → 成品 12dB）
- 偏移先乘**阻尼系数** `--energy-gain`（默认 0.6，6 个主题实测标定；0=不写曲线，1=照抄目标）
- 夹在 **±4dB**、`|Δ| < 0.5dB` 不写；目标本身平坦（起伏 <1dB）就**整段不写**（不造假变化）。
  ⚠ CC7 上限 127：高电平轨（如 Melody 104）上 +4dB 会被夹到 127（实际约 +3.8dB）
- 实际用的曲线与 k 记进 `song.json` 的 `theme.energy_curve_db` / `theme.energy_gain`，可复核

**实测 CC7 曲线**（同一段内容只改 CC7，同渲染内以 CC7=100 为参考）：

| CC7 | 127 | 116 | 108 | 100 | 92 | 84 | 74 | 64 | 56 | 48 | 40 | 30 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dB | +3.8 | +2.4 | +1.2 | 0 | −1.4 | −2.9 | −4.9 | −7.3 | −9.6 | −12.2 | −15.4 | −20.6 |
| 线性模型 | +2.1 | +1.3 | +0.7 | 0 | −0.7 | −1.5 | −2.6 | −3.9 | −5.0 | −6.4 | −8.0 | −10.5 |

**系数标定（含一次被数据推翻的尝试 —— 留着当证据）**

测量口径：`win_db` = 每 8 小节的**浮点** dB（与画像 `structure` 同口径、不取整 ——
用 `metrics.structure` 的整数 dB 会把 1dB 差异吃掉 20% 分辨率）。渲染一律 `--no-tune`
（两版各自开自动调参会调出不同 EQ，A/B 不干净）。

**第一轮**（14 个主题 × 单 seed=5，k=0 / k=1 两版）：

| 主题 | 目标起伏 | k=0（编配自带） | k=1 | k\* | 形状相关 |
|---|---|---|---|---|---|
| battle / neon / retro / cheerful | 6.0 | 0.39~0.61 | 5.77~6.25 | 0.96~1.04 | 1.00 |
| daily / folk_tale / lounge / mystery / night / seaside | 3.0 | 0.30~1.68 | 2.83~3.84 | 0.61~1.07 | 0.66~0.99 |
| classic / gorgeous / sorrow / tender | 1.0 | 0.55~1.35 | 0.52~1.58 | — | — |（目标太平 → 包里不给曲线）

单看这张表，"编配自带起伏 r0 各主题差 0.3~1.7dB，单值系数压不住" → 于是按主题写 `energy_gain`。

**第二轮（多 seed 决策 A/B，把第一轮的结论推翻了）**：4 个主题 × 2 个 seed × 两种系数，
比 |成品起伏 − 目标起伏|：

| 主题 | seed | 逐主题 gain 误差 | 全局 1.0 误差 |
|---|---|---|---|
| mystery | 5 / 11 | 0.84 / **0.13** | **0.14** / 0.85 |
| daily | 5 / 11 | 0.69 / 0.83 | 0.08 / 0.22 |
| lounge | 5 / 11 | 0.25 / 0.33 | 0.35 / 0.28 |
| battle | 5 / 11 | 0.32 / 0.57 | 0.03 / 0.28 |
| **平均（8 样本）** | | **0.50 dB** | **0.28 dB** |

结论：**逐主题系数是在拟合单次生成的随机性**（mystery 两个 seed 上好坏完全翻转），
**全局 `ENERGY_GAIN = 1.0` 更贴**（0.28 vs 0.50）。所以：
- 包里**不写** `energy_gain`（要用得显式 `--calibrate --set-gain`，且先看 evidence 的跨 seed 一致性）
- `--calibrate` 默认**只写证据**（`mix_target.calibration`：各 seed 的 r0/r1/k\*/相关/对齐变化 + verdict）
- 生成时每首都会打印实际用的 k（`song.json` 的 `theme.energy_gain` 留痕）

⚠ 单 seed 下的 k\* 不确定度约 ±0.15，而生成本身（`--avoid songs` 去重会让候选选择微变）
带来 ±1dB 的段间起伏波动 —— **任何"精确到小数"的系数都在骗自己**。

**A/B 实测**（同 seed，只差曲线；`metrics.structure` 同口径）：

| 主题 / 目标 | 目标起伏 | +曲线 | −曲线 | 整体对齐 |
|---|---|---|---|---|
| `battle` / BGM15c | 6.0 dB | **6.0 dB** | 0.0 dB | 最差频段 3.1 vs 3.2 dB（没变差）、响度 = 参考 |
| `seaside` / bgm01c | 3.0 dB | **3.0 dB** | 0.0 dB | 响度 −14.3 = 参考、宽度 0.316 = 参考 |

即：曲线把"一条直线"拉回目标量级，**整体响度与频段对齐都没变差**（均值为 0 + 每段都写）。

依据：技能里定死 —— **"像不像"主要来自段间对比**，全曲一条直线 = 亮却闷
（参考曲 5–10kHz 段间起伏 9~13dB，平了就不像）。

**实测 A/B**（同一 seed，只差曲线；用同一个 `metrics.structure` 口径量成品）：

见上面"系数标定"与"A/B 实测"两张表 —— 结论：+曲线把起伏拉回目标量级（6.0/6.0、3.0/3.0），
−曲线一律 0.0，且整体响度/频段对齐都没变差。

## 6. 守卫（改完必跑）

| 守卫 | 抓什么 |
|---|---|
| `theme_pack_valid`（自检） | 模板数 ≥8 / 不重复 / 在 `refs/midi2/_index.json` 里且 md5 对得上 / 风格属于该主题 / 来源 URL 在权威白名单 / 画像字段齐全 / 混音目标有效 |
| `theme_basis_whitelist`（自检 + `check_song` 数据契约） | 曲目声明的模板名单与包**逐首一致**、数量一致；`basis.kind != theme_pack`（老 `--from`）→ FAIL |
| `mutation_check` | 6 条注入用例（白名单放宽 / 索引清空 / 主题表清空 / 包路径坏 / 主题名认不出 / 混音目标指向空画像） |

判据**只有一份**（`theme_pack.validate_pack`），生成路径与守卫共用以防各说各话。

## 7. 命令

```powershell
$py = "<工具链根>/venv/python.exe"

& $py scripts\theme_pack.py --list-themes      # 主题表（主题 → 风格集合 → 引擎预设）
& $py scripts\theme_pack.py --selftest         # 聚合规则自测（不读库、不联网）
& $py scripts\theme_pack.py seaside            # 建包 → refs/themes/seaside.json（+ _melody.json）
& $py scripts\theme_pack.py seaside --show     # 建完打印摘要（模板清单/进行/节奏/配器/混音目标）
& $py scripts\theme_pack.py --all              # 15 个主题全建
& $py scripts\theme_pack.py waltz --min 8 --allow-fetch   # 同主题不足 8 首时联网抓
& $py scripts\new_song.py 35_x --theme seaside --seed 7   # 依据模板包出 song.json + 旋律
```

## 8. 加/改一个主题

1. 在 `theme_pack.py` 的 `THEMES` 加一行（主题词 → styles + engine + 可选 meter）
2. 跑 `theme_pack.py <主题> --show`：候选 <8 首就加风格目录或 `--allow-fetch`
3. 跑 `theme_pack.py --selftest` + `selftest.py --fast`（守卫会核对新包）

## 9. 已知边界（别当成没问题）

- **模板库不进仓库**（`.mid` 版权灰色）：clone 后要跑一次 `fetch_midi_lib.py` 重建；
  `midi_lib_index_sync` 会检查索引与磁盘一致
- **混音目标只保证"性格相近"**（速度/打击感/调式），不保证音乐上最好听 —— 可 `--ref` 指定
- **旋律生成只支持 4/4**；主题语言取自模板（模板偏密则旋律偏密，可用 `melody_gen --dens` 压）
- 包是**画像不是作品**：它描述"这个主题通常怎么写"，具体一首歌的旋律/结构仍由生成与人工决定
