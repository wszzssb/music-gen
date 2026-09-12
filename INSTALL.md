# 安装与首次运行（clone 之后）

## 一条命令装好（Windows）

**双击 `setup.cmd`** —— 自动建虚拟环境、装依赖、取音源、跑自检，装完告诉你下一步看哪。
**已装过的步骤会自动跳过，重复运行安全。**

手动三步也行（与上面等价）：

```powershell
python -m venv .venv                                                          # ① 环境（Python 3.10+）
.\.venv\Scripts\python.exe -m pip install numpy soundfile imageio-ffmpeg      # ② 依赖
.\.venv\Scripts\python.exe scripts\setup_soundfont.py                         # ③ 音源
```

**一律用 `.venv\Scripts\python.exe`，别用系统 `python`**（后者可能没装 numpy）。

③ 从多个镜像下载 `fluidsynth.exe` 与 `GeneralUser GS v1.471.sf2` 到 `vendor\`；
网络不通时按脚本提示手动放进 `vendor\` 即可。**没装音源也不会一片红** —— 自检会明确报
`找不到 fluidsynth.exe，先跑 scripts/setup_soundfont.py`，其余 73 项照常通过。

## 验证环境可用

```powershell
.\.venv\Scripts\python.exe scripts\selftest.py      # 78 项全绿 = 可用（约 30 秒）
.\.venv\Scripts\python.exe scripts\rehearsal.py     # 彩排：5 套风格端到端（约 4 分钟）
```

## 参考曲：自备（版权原因不随仓库分发）

`refs\` 里只有**画像**（频谱/速度/频段/节奏型统计），没有任何参考曲音频。想扒自己的曲子：

```powershell
.\.venv\Scripts\python.exe scripts\check_audio.py <你的音频>           # 先体检（能不能读 / 速度几层）
.\.venv\Scripts\python.exe scripts\profile_ref.py <你的音频> <画像名>   # 存成 refs\<画像名>.json
```

`rehearsal.py` 的「全新参考曲扒谱」那一步用环境变量 `BGM_REF_DIR` 指定参考曲目录；
**没设置就跳过该步、用 `refs\` 里已有画像继续**（不算失败）—— 所以 clone 下来直接跑彩排也能全绿。

## 仓库带什么、不带什么

| 带 | 不带（`.gitignore` 已排除，可自行生成） |
|---|---|
| 全部脚本、文档、`song.json` / `spec.json` / MIDI / `notes.md` | `.venv\` / `.venv-ml\`（按上面 ①② 自建） |
| `refs\` 里的参考曲画像（41 个 + 子目录） | `vendor\`（音源，按 ③ 获取） |
| 质量分级「很好」的曲目带**成品 ogg**（开箱可听；wav 母版跑 make_song.py 重生成） | 其余曲目的音频（跑 `make_song.py <曲目>` 重生成）；质量差的已归档到 `songs\_archive\`（不进仓库） |

## 五分钟出第一首

**① 写一份 mini spec** —— 只写「和弦走向 + 旋律骨架」，时值、和弦排列、编配全部自动推导。
存成 `my_spec.json`：

```json
{
 "name": "my_first", "bpm": 150, "style": "daily", "ref": "BGM16c",
 "desc": "我的第一首",
 "sections": [
   {"name": "A", "bars": 8, "chords": "D A Bm7 G6 D A G6 A",
    "melody": {"m": [[0,0,74],[0,2,78],[1,0,81],[2,0,83],[3,0,79],
                     [4,0,74],[5,0,81],[6,0,79],[7,0,81]]}}
 ]
}
```

旋律写成 `[小节, 拍, 音高]` 三元组，**时值自动推**到下一个音；和弦用符号写（`Bm7` / `G6` / `Fsus4` / `A/C#`），
排列自动推导。上面这份 8 小节、24 个音的 spec 是**实测可跑**的（强拍零错音）。

**② 两条命令**：

```powershell
.\.venv\Scripts\python.exe scripts\build_song.py my_spec.json --out songs\99_my_first
.\.venv\Scripts\python.exe scripts\make_song.py 99_my_first
```

产物在 `songs\99_my_first\`：**`.mid`**（9 轨 GM，可挂任何更好的音源）+ `_sf.wav` + `_sf.ogg` + 对标成绩单。

只想先听现成的示例：`songs\23_d150_skip_along\d150_skip_along_sf.ogg`。
命令全表见 `CHEATSHEET.md`；曲式与风格预设见 `docs/SONG-FORMAT.md`；
要把参考曲换成你自己的，见上面「参考曲：自备」。
