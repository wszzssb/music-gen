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
### 体检与校验

```powershell
& $py scripts\check_audio.py <任意音频>              # 能不能读 / 速度几层（-deep 出倍频程）
& $py scripts\check_audio.py <目录> --formats        # 目录里哪些能读
& $py scripts\check_song.py <曲目>                   # 渲染前查数据契约判据（0.4 秒）
& $py scripts\check_song.py <曲目> --fix             # 自动修和弦音集/强拍
```
