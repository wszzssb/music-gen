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

### 旋律：**一首一份画像**（共用一份 = 十首一套口音，实测孪生 5 对）

```powershell
# 画像一首一份；--avoid 拿库里已有旋律做去重筛选（生成 8 条取最不像的一条）
& $py scripts\melody_gen.py songs\23_x\song.json refs\melody\psg_BGM16b_melody.json `
      --seed 23 --avoid songs --candidates 8
& $py scripts\melody_gen.py <song.json> <画像> --motif off      # 退回"逐音直方图"版（A/B 对照用）
#   v2 动机层（默认开）：动机重复 + 大跳后反向/回填 + 句末终止式；结构层指标在输出末尾打印
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

**口径**：成绩单的"倍频程"是**相对本曲最响频段**的 → 判断编配改动一律用 `bands_abs`（绝对 dB）。
"像不像"还差一层**占用率**（墙/点），只有 `bands_abs` / `probe_timbre` 有这一列（见坑 103）。

### 逐轨事件（面板卷帘 / 排查用）

```powershell
& $py scripts\song_events.py songs\21_g150_velvet\song.json [--track Bass] [--json]
```
### 可视化面板（写歌时最省时间的一条路）

`powershell
studio\start.cmd                     # 起面板（已在跑则只开浏览器）→ http://127.0.0.1:8765
studio\stop.cmd                      # 停
`

面板里点：**⚡ 试听本段** · **🎚 自动配平** · **🧬 候选搜索**（结果**逐项勾选采用**）· **📦 导出**。
细节见 studio/README.md；口径与 CLI 完全一致（改的都是 song.json）。
### MIDI 参考（音符层精确参考）

```powershell
& $py scripts\midi_ref.py <file.mid>              # 和声进行/低音线/旋律线/节奏型/曲式
& $py scripts\midi_ref.py refs\midi --recursive   # 批量看
& $py scripts\midi_ref.py <file.mid> --json       # 机器可读
& $py scripts\midi_ref.py <file.mid> --bars 5-20  # 只看某几小节
```

**音符层看 MIDI，频谱层看音频画像**：`refs/*.json` 画像的频谱/宽度/响度只能从真实录音拿；
MIDI 精确给的是速度/和声/声部/节奏/曲式 —— 两者**互补，不能互相替代**。
素材自备：公共领域用 Mutopia / IMSLP；游戏动漫 MIDI（VGMusic 等）**版权灰色，只内部学习、不进仓库**。
### 体检与校验

```powershell
& $py scripts\check_audio.py <任意音频>              # 能不能读 / 速度几层（-deep 出倍频程）
& $py scripts\check_audio.py <目录> --formats        # 目录里哪些能读
& $py scripts\check_song.py <曲目>                   # 渲染前查数据契约判据（0.4 秒）
& $py scripts\check_song.py <曲目> --fix             # 自动修和弦音集/强拍
```
