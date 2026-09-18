# 交接：18 首生成曲的听感修复（2026-09-18 止）

> **下一个对话从这里开始，不要重新摸索。** 本文所有数字都是可复现的实测值。
> 用户听感四条 → **段界硬切已修好**；**其余三条已量清根因、一行未改**。

## 0. 用户方针（已进 `SKILL.md` ⑤，不要再问）

**起伏主要做在编配层，音量只是辅助。** 段间厚薄对比靠**加减声部 / 换乐器 / 改音区**，
不靠 CC7 / 电平；边界处的对比用**一个小节爬坡**（段末逐条退场），不在边界一瞬切换。

## 1. 已完成 ✅

| 项 | 结果 | commit |
|---|---|---|
| Hook 音色避开极响吉他（2.5–5kHz > 41dB）+ 按音色标定电平（`HF_LEVEL`/`HF_CC7_K=0.49`） | Hook 从钢弦吉他 → 钢琴/尼龙吉他 | `d9be3fc` |
| `new_song --force`（原名撞目录会**静默拒绝**） | 面板侧撞上会自动补 | `d9be3fc`、`732fbb0` |
| 面板 `/api/new` 补 `force`/`seed` + `--force` 后**自动重渲染** | 走面板生成已跑通 | `732fbb0` |
| `melody_gen` 支持 3/4（`SPB` 按拍号设 + 重绑默认参数） | waltz 实测 `timesig=[3,4]`、68 个落点 0 越界 | `251fc9c` |
| 段界判据取样改"段中部 30%~70%"（**门限一字未动**） | FAIL 4 首 → 3 首 | `f00a7e4` |
| **段末逐条退场**（`song_engine` 的 `_RAMP`+`_ORDER`） | `02_wave_walk` **0/6 → 6/6**，`jump` 仍 10dB | `bffe306`、`13dc4ba` |

## 2. 未完成 ❌（都已量清根因，直接开工）

### 2.1 鼓像"打字机"（最明确、收益最大）

`b35_remake.mid`（仿写）vs `songs/02_wave_walk/wave_walk.mid`：

| 指标 | 仿写 | 我的 | 目标 |
|---|---|---|---|
| Perc 音数 | **3854**（≈18.5/小节） | **309**（≈6.4/小节） | ×3 |
| 力度种类 | **66 种**（85 出现 1877 次，其后 58/56/59…） | **13 种**，且 36/43/31/37 **各 36 个 = 完全均匀** | 多档且不均匀 |
| 16 分格落点 | **16 格全有**，疏密差 2 倍（338 vs 167） | **只 9 格**（0/2/4/6/8/10/12/13/14），基本只踩偶数格 | 铺满 16 格 |

⚠ **修正一条旧结论**：库里写"v5 的鼓力度平（100）却好听、groove 比力度重要"——那是
`v5_source.mid` 的读数；**"更进一步"的 `b35_remake.mid` 是 66 种力度**。**力度层次是要的。**

### 2.2 伴奏撞主奏（用户："12 秒左右出现的重叠问题，其它地方也有"）

跨轨"同起点 + 同音高"的次数：**仿写 498 处，我 24 处** —— 所以**撞音本身不是问题**。
差别在**撞的是谁**：仿写全是**伴奏轨之间**（`Arp×Hook` 140、`Arp×Piano` 104、`Hook×Piano` 83）；
**我的有 `Hook×Melody` 7 处 = 伴奏撞主奏**。
→ 要修的是"**伴奏避让主奏**"（同音高与同音区），**不是消除撞音**。

其它实测：`02_wave_walk` **同轨同音高时间重叠 = 0 处**（`legato_trim` 正常）；
12s 处同时发声峰值 7、全曲峰值 18（在 68.5~79.5s）；**12s = 第 6.5 小节（A 段中间，不是段界）**。

### 2.3 整首太平

`01_morning_light` 生成日志原话："**段间能量曲线：本次未写（目标画像本身平坦或起伏
<0.5dB）**"。按方针 → 起伏该做在**编配层**（拉大 `arr_level`/`density` 的段间差），
不是让画像背锅。验收：`t_density_dynamic_range` ≥ 4 倍（现 `10_marble_hall` **3.9**）。

### 2.4 旋律怪（跑调感 / 总在一个音区 / 节奏碎）

`t_melody_matches_profile` FAIL：`07_hidden_door` 时值 **40%**、`16_morning_two` **31%**（下限 40%）；
`t_melody_onset_spread` FAIL：`05_soft_memory` 落点偏离 **0.702**（门 0.65）。
**照 2.1/2.2 的做法先量仿写**再动手。

## 3. 还剩的守卫 FAIL

- `section_transition`：**2 首**（`17_blade_two`、`19_spec_echo`）—— 它们**还没按新引擎重生成**，
  重生成即可（`new_song --force` + `make_song`，或走面板）。
- `density_dynamic_range` · `melody_matches_profile` · `melody_onset_spread` · `track_balance`。

## 4. 流程红线（都是踩出来的）

1. **改引擎 ⇒ 重生成 + 重渲染，两步缺一不可**。只跑一半的症状是"命令 ok、`song.json`
   一字未动、听感没变"（`new_song` 撞同名目录**只打印"已存在"就退出**）。
2. **生成第一件事走面板**：`POST http://127.0.0.1:8765/api/new`，body
   `{"id":"02_wave_walk","theme":"seaside","seed":7,"force":true}` → 自动重渲染。
   批量才用 CLI 并行（bash 数组 + `&` + `wait`，5 路约 10 分钟跑完 18 首）。
   ⚠ 别用**多行 heredoc + `while read`** 喂清单：会吃掉行首字符（18 首目录名错 17 个）。
3. **先量音频包络 / MIDI，再动手**：段界那条我猜了 4 个方向（加留白 / 压电平 / 调
   `section_gap` / 段首稀疏）**全部无效**，量了逐 0.05s RMS 才看清真相。
4. **别删日志、别用管道判退出码**：`git push | tail` 让退出码变成 `tail` 的；
   多进程并发写同一日志会让计数失真（我据此误报过"6 首失败"）。
5. shell 操作**一律落 `.sh` 文件**再跑（`pwsh` 只允许 `& 'bash.exe' 'x.sh'` 一种形式）。

## 5. 环境与路径

- 工具链 `D:\software\skill`（venv `.venv`）· 面板 `http://127.0.0.1:8765`
  （**若面板没在跑：双击 `studio\start.cmd`** —— 我起的那份挂在会话后台作业里，会被带走）
- 曲库 `songs/`（19 首）↔ 面板库 `D:\test\llm_direct\studio_lib\songs`（**junction，单一数据源**）
- 旧 18 首备份：`D:\test\llm_direct\songs_backup_20260918`
- 仿写参考（**所有"照仿写来"的基准**）：`D:\test\llm_direct\studio_lib\songs\99_b35_remake\`
  （`b35_remake.mid` = "更进一步"版，9 轨；`v5_source.mid` 是较早的 5 轨版，读数不同别混用）
- 主题包 `refs/themes/*.json`（15 个，含 `arrangement.prog_pool` = 模板实际音色池）

## 6. 量测代码（重写成本很低，逻辑就这几行）

```python
# 鼓力度/落点
vels = Counter(int(v) for (s, du, p, v) in perc_notes)
slots = Counter(int(round((s % 4) * 4)) % 16 for (s, du, p, v) in perc_notes)   # 16 分格
# 跨轨撞音（同起点 + 同音高）
onsets[(round(s, 3), p)].append(track_name)      # 长度 > 1 即撞
# 段界包络
env = 20*log10(RMS(mono, hop=sr*0.05))           # 每 0.05s
# 门：fade_out>=4 或 fade_in>=4 或 jump<3（pre/post 取"段中部 30%~70%"）
```
