# 命令速查（CHEATSHEET）

README 有 token 预算（守卫会拦），所以**完整命令示例集中在这里**：写歌四条命令、体检、
渲染前校验、诊断类工具的常用开关。README 只留一行指针，需要时再来这找。

## 写一首新歌（四步）

从 README 拆出来的完整调用示例（README 有 token 预算，守卫说"该拆文档了"）。

### 写一首新歌（四步）

```powershell
cd D:\software\skill
$py = ".\.venv\Scripts\python.exe"

# ① 脚手架：复制模板数据 + 按参考曲画像自动填 BPM / 响度 / 宽度 / 搁架
& $py scripts\profile_ref.py <参考曲> <画像名>            # 参考曲只扒一次，存 refs/<画像名>.json
& $py scripts\new_song.py 06_morning --from 05_d135_cheerful --ref BGM16c

# ② 只改 songs\06_morning\song.json（chords / melody / sections）
# ③ 一条命令：作曲 + 真音源渲染 + **自动调参** + 对标成绩单
& $py scripts\make_song.py 06_morning
& $py scripts\make_song.py 06_morning --no-tune           # 只渲染一轮不调参
```

### 成绩单输出长这样

```
  第1轮: 质心3572 宽度0.489  ✓ 达标
  ⚠ EQ 到头了，剩下的差距要靠编配（改 song.json）：
    - 315-1250Hz 差 +2.5dB（EQ 已到顶）→ 钢琴/吉他轨：mix.Piano / mix.Hook 音量
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

### 编配/口径诊断（"改了没效果"时先跑这三条）

```powershell
& $py scripts\bands_abs.py songs\21_g150_velvet\bgm16c_v2_sf.wav BGM16c_v2  # 绝对 dB + 占用率
& $py scripts\bands_abs.py a.wav b.wav --win 6-20                        # 只看安静段（段间对比）
& $py scripts\probe_timbre.py --programs 8,9,51,95,99                    # 挑"空气层"音色（等响度比）
& $py scripts\probe_timbre.py --solo songs\21_g150_velvet\song.json      # 逐轨量真实电平（弱轨现形）
& $py scripts\probe_peaks.py "D:\test\BGM16c.ogg" --bpm 150 --bars 1-16  # 谱峰扒谱（速度必须先钉死）
```

**口径**：成绩单的"倍频程"是**相对本曲最响频段**的 → 判断编配改动一律用 `bands_abs`（绝对 dB）。
"像不像"还差一层**占用率**（墙/点），只有 `bands_abs` / `probe_timbre` 有这一列（见坑 103）。

### 逐轨事件（可视化面板 / 排查用）

```powershell
& $py scripts\song_events.py songs\21_g150_velvet\song.json --json     # 全部轨
& $py scripts\song_events.py songs\21_g150_velvet\song.json --track Bass  # 只看一轨
```

### 可视化面板（写歌时最省时间的一条路）

`powershell
studio\start.cmd                     # 起面板（已在跑则只开浏览器）→ http://127.0.0.1:8765
studio\stop.cmd                      # 停
`

面板里点：**⚡ 试听本段**（1~3 秒循环试听）· **🎚 自动配平**（解方程 + 实测回滚）·
**🧬 候选搜索**（切片 + 并行，结果**逐项采纳**）· **📦 合并导出 / 🎚 分轨导出**。
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
